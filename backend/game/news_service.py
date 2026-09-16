# -*- coding: utf-8 -*-
"""近期新闻资讯服务

后台 daemon 线程每小时调用 `fetch_and_cache_news(app)`：
1. 从正规 RSS 源抓取最新条目（纯标准库 urllib + xml.etree，零新依赖）
2. 关键词黑名单过滤（合规红线，丢弃政治敏感/暴力/低俗内容）
3. 按职业关键词匹配，选出与女主职业相关的资讯 + 通用热点
4. 用 safe_llm_post 把长描述压成 1-2 句摘要（控制 token、防长文）
5. 写入 NewsCache 表，旧记录自动清理

对话构建时由 dialogue.build_dynamic_context 把未用新闻转成 EventLog(event_type='news') 注入
【今日事件】段；event.py 主动消息决策 prompt 里追加【近期资讯素材】段。
失败一律静默跳过，绝不影响游戏主流程。
"""

import json
import logging
import re
import ssl
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import timedelta

from backend.config import beijing_now

logger = logging.getLogger(__name__)

# 全局 LLM 节流，避免抓取阶段锤爆 API
_SUMMARY_THROTTLE_SEC = 1.0


# ============================================================================
# RSS 源（已验证可访问）
# ============================================================================
RSS_FEEDS = [
    {
        'name': '中新网即时',
        'url': 'https://www.chinanews.com.cn/rss/scroll-news.xml',
        'category': 'general',  # 通用即时新闻，覆盖各领域
    },
    {
        'name': '中新网IT',
        'url': 'https://www.chinanews.com.cn/rss/it.xml',
        'category': 'tech',
    },
    {
        'name': '中新网教育',
        'url': 'https://www.chinanews.com.cn/rss/edu.xml',
        'category': 'edu',
    },
]

# ============================================================================
# 职业 → 关键词映射（用于从通用源里筛出与女主职业相关的资讯）
# ============================================================================
MAJOR_KEYWORD_MAP = {
    '法学': ['法律', '法院', '检察', '律师', '判决', '案件', '诉讼', '司法',
            '法规', '立法', '罪名', '维权', '庭审', '刑诉', '民法典'],
    '计算机科学': ['AI', '人工智能', '算法', '芯片', '半导体', '互联网', '科技',
                 '软件', '编程', '大数据', '云计算', '机器人', '数码', '智能',
                 '5G', '6G', '开源', '大模型', '算力'],
    '油画': ['美术', '画展', '艺术展', '画廊', '拍卖', '画家', '艺术品', '展览',
            '博物馆', '文创', '美院', '雕塑', '油画', '水墨'],
    '心血管内科': ['医疗', '医院', '医生', '患者', '健康', '疾病', '手术', '医学',
                 '疫情', '卫生', '心脏', '血压', '糖尿病', '癌症', '医药', '疫苗',
                 '临床试验', '急救'],
    '中国现当代文学': ['文学', '小说', '作家', '出版', '诗歌', '散文', '书评',
                    '文学奖', '阅读', '图书', '书店', '名著', '茅盾', '鲁迅',
                    '文学评论', '翻译'],
    '游戏设计': ['游戏', '电竞', '手游', '主机', 'Steam', '3A', '独立游戏',
               '游戏展', '开发者', '版号', '游戏公司', '网游', '游戏引擎'],
}

# 通用热点关键词（不限定职业，作为补充素材）
GENERAL_KEYWORDS = ['科技', '社会', '文化', '教育', '体育', '艺术', '环境', '气象',
                   '出版', '研究', '大学', '发明', '发现', '赛事']

# ============================================================================
# 内容审核黑名单（合规红线）
# 命中任一关键词的条目立即丢弃，绝不入库
# ============================================================================
BLACKLIST = [
    # ── 政治敏感：领导人姓名/职务/机构 ──
    '习近平', '李强', '赵乐际', '王沪宁', '蔡奇', '丁薛祥', '李希', '韩正',
    '总书记', '国家主席', '政治局', '党中央', '中共中央', '共产党', '中共',
    '全国人大', '国务院总理', '总理',
    # ── 政治敏感：议题/事件 ──
    '台独', '港独', '疆独', '藏独', '六四', '天安门', '法轮功', '维权运动',
    '颜色革命', '政变', '集会抗议',
    # ── 国际敏感（避免卷入地缘政治）──
    '白宫', '普京', '拜登', '特朗普', '哈里斯', '伊朗核', '朝鲜核', '塔利班',
    '战争', '空袭', '导弹袭击', '核武器', '军事打击',
    # ── 暴力/血腥/犯罪（负面）──
    '恐怖袭击', '恐怖组织', '枪击案', '屠杀', '血腥', '强奸', '猥亵', '凶杀',
    '爆炸案', '灭门', '分尸',
    # ── 低俗/违法 ──
    '色情', '淫秽', '赌博', '毒品', '走私', '诈骗案',
]

# 抓取上限：每个职业每轮最多取几条，避免单次 LLM 调用过多
MAX_PER_MAJOR = 2
MAX_GENERAL = 2
# 每个源最多取几条（前端可配）
MAX_ITEMS_PER_FEED = 8
# 每角色每天注入几条（前端可配）
MAX_INJECT_PER_DAY = 3
# 描述超过此长度才调用 LLM 摘要（短描述直接截断使用，省 token）
SUMMARY_THRESHOLD = 80
# 缓存保留天数，超过自动清理
CACHE_RETAIN_DAYS = 2


def _make_ssl_context():
    """构造一个宽松的 SSL 上下文（部分自签证书源需要）。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _strip_html(text):
    """去掉 HTML 标签与多余空白。"""
    if not text:
        return ''
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _passes_blacklist(text):
    """通过黑名单返回 True（安全），命中返回 False（丢弃）。"""
    if not text:
        return True
    for w in BLACKLIST:
        if w in text:
            return False
    return True


def _matches_any(text, keywords):
    """文本命中任一关键词返回 True。"""
    if not text or not keywords:
        return False
    return any(k.lower() in text.lower() for k in keywords)


def fetch_rss_feed(url, source_name):
    """抓取单个 RSS 源，返回条目列表 [{title, link, description, source}]。

    失败返回空列表（静默）。
    """
    items = []
    try:
        req = urllib.request.Request(
            url, headers={'User-Agent': 'Mozilla/5.0 (SimLifeNewsFetcher/1.0)'}
        )
        with urllib.request.urlopen(req, timeout=12, context=_make_ssl_context()) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        for it in root.findall('.//item'):
            title = (it.findtext('title') or '').strip()
            link = (it.findtext('link') or '').strip()
            desc = _strip_html(it.findtext('description') or '')
            if not title:
                continue
            items.append({
                'title': title,
                'link': link,
                'description': desc,
                'source': source_name,
            })
    except Exception as e:
        logger.warning(f"[News] 抓取 {source_name} 失败（跳过）: {type(e).__name__}: {e}")
    return items


def fetch_60s_api(url, source_name):
    """抓取 60s API（JSON 格式，无 API Key）。

    返回条目列表 [{title, link, description, source}]，与 fetch_rss_feed 格式统一。
    失败返回空列表（静默）。
    """
    items = []
    try:
        req = urllib.request.Request(
            url, headers={'User-Agent': 'Mozilla/5.0 (SimLifeNewsFetcher/1.0)'}
        )
        with urllib.request.urlopen(req, timeout=12, context=_make_ssl_context()) as resp:
            raw = resp.read()
        data = json.loads(raw.decode('utf-8'))
        news_list = data.get('data', {}).get('news', [])
        for line in news_list:
            # 60s 返回的每条是纯文本字符串（标题+摘要合一）
            title = line.strip()[:256]
            if not title:
                continue
            items.append({
                'title': title,
                'link': '',
                'description': '',  # 60s 条目只有一句话，标题即全部
                'source': source_name,
            })
    except Exception as e:
        logger.warning(f"[News] 抓取 {source_name} 失败（跳过）: {type(e).__name__}: {e}")
    return items


def get_news_settings():
    """从数据库读取全局新闻配置，无表或异常时回退到默认常量。"""
    from backend.models import NewsSettings
    try:
        st = NewsSettings.query.get(1)
        if st:
            return {
                'enabled': st.enabled,
                'max_items_per_feed': st.max_items_per_feed or MAX_ITEMS_PER_FEED,
                'max_inject_per_day': st.max_inject_per_day or MAX_INJECT_PER_DAY,
                'push_interval_days': st.push_interval_days or 0,
                'fetch_interval': st.fetch_interval or 21600,
                'cache_retain_days': st.cache_retain_days or CACHE_RETAIN_DAYS,
                'summary_threshold': st.summary_threshold or SUMMARY_THRESHOLD,
            }
    except Exception:
        pass
    # 默认值
    return {
        'enabled': True,
        'max_items_per_feed': MAX_ITEMS_PER_FEED,
        'max_inject_per_day': MAX_INJECT_PER_DAY,
        'push_interval_days': 0,
        'fetch_interval': 21600,
        'cache_retain_days': CACHE_RETAIN_DAYS,
        'summary_threshold': SUMMARY_THRESHOLD,
    }


def get_enabled_sources_configs():
    """从数据库读取已启用的新闻源配置列表，无表时回退到内置默认。"""
    from backend.models import NewsSourceConfig
    try:
        rows = NewsSourceConfig.query.filter_by(enabled=True).order_by(NewsSourceConfig.sort_order).all()
        if rows:
            return rows
    except Exception:
        pass
    # fallback 到内置 RSS_FEEDS（代码层兜底）
    return None


def _summarize(title, description, llm_config, threshold=None):
    """用 LLM 把长描述压成 1-2 句摘要。

    短描述直接截断返回（省 LLM 调用）；LLM 失败则回退到截断描述。
    threshold 超过此长度才调 LLM，None 时用默认常量。
    """
    desc = (description or '').strip()
    _threshold = threshold or SUMMARY_THRESHOLD
    # 短描述：直接用，不调 LLM
    if len(desc) <= _threshold:
        return desc[:120] if desc else title

    if not llm_config or not llm_config.get('api_key'):
        return desc[:120]

    try:
        from backend.game.llm_utils import safe_llm_post
        import time as _time
        _time.sleep(_SUMMARY_THROTTLE_SEC)  # 节流
        from backend.game.variable_resolver import render_template
        from backend.game.prompt_registry import get_prompt_manager
        pm = get_prompt_manager()
        body = desc[:500]
        prompt = render_template(pm.get("news.summary"), {
            "context.news_title": title,
            "context.news_body": body,
        })
        result = safe_llm_post(
            api_url=llm_config['api_url'],
            api_key=llm_config['api_key'],
            model=llm_config.get('model_name', 'gpt-4o-mini'),
            messages=[
                {'role': 'system', 'content': '你是一个新闻摘要助手，只输出摘要本身。'},
                {'role': 'user', 'content': prompt},
            ],
            max_tokens=150,
            temperature=0.3,
            timeout=(10, 30),
            call_type='news_summary',
            character_name='',
        )
        if result and result.get('choices'):
            content = result['choices'][0]['message']['content'].strip()
            # 去掉可能的引号包裹
            content = content.strip('"“”‘’`')
            if content:
                return content[:120]
    except Exception as e:
        logger.warning(f"[News] LLM 摘要失败（回退截断）: {type(e).__name__}: {e}")
    return desc[:120]


def _dedup(items):
    """按标题去重（不同源可能重复）。"""
    seen = set()
    out = []
    for it in items:
        key = it['title']
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def fetch_and_cache_news(app):
    """主入口：抓取 → 过滤 → 匹配职业 → 摘要 → 写缓存。

    在后台线程中调用，需传入 app 以建立 app context。
    从 NewsSettings 表读取全局配置，从 NewsSourceConfig 表读取启用的源。
    返回本轮新入库条数。失败返回 0。
    """
    with app.app_context():
        from backend.models import NewsCache, LLMConfig, db

        # 0. 读取全局配置（无表则用默认常量）
        settings = get_news_settings()
        if not settings.get('enabled', True):
            logger.info('[News] 全局开关已关闭，跳过本轮抓取')
            return 0

        max_per_feed = settings['max_items_per_feed']
        sum_threshold = settings['summary_threshold']
        retain_days = settings['cache_retain_days']

        # 1. 读取启用的源配置（无表则用内置 RSS_FEEDS）
        src_configs = get_enabled_sources_configs()

        all_items = []
        if src_configs:
            # 从数据库配置读取
            for src in src_configs:
                items = []
                if src.source_type == 'rss':
                    items = fetch_rss_feed(src.api_url, src.name)
                elif src.source_type == '60s':
                    items = fetch_60s_api(src.api_url, src.name)
                # 每源限流
                if len(items) > max_per_feed:
                    items = items[:max_per_feed]
                all_items.extend(items)
        else:
            # fallback 到内置 RSS_FEEDS
            for feed in RSS_FEEDS:
                items = fetch_rss_feed(feed['url'], feed['name'])
                if len(items) > max_per_feed:
                    items = items[:max_per_feed]
                all_items.extend(items)

        if not all_items:
            logger.info('[News] 本轮无任何条目（所有源抓取失败），跳过')
            return 0
        all_items = _dedup(all_items)
        logger.info(f"[News] 本轮抓取去重后 {len(all_items)} 条")

        # 2. 黑名单过滤（合规红线）
        safe_items = [it for it in all_items if _passes_blacklist(it['title']) and _passes_blacklist(it['description'])]
        dropped = len(all_items) - len(safe_items)
        if dropped:
            logger.info(f"[News] 黑名单过滤丢弃 {dropped} 条")
        if not safe_items:
            logger.info('[News] 黑名单过滤后无剩余条目，跳过')
            return 0

        # 3. 取 LLM 配置（摘要用，无配置则回退到截断描述）
        cfg_db = LLMConfig.query.filter_by(is_active=True).first()
        llm_config = cfg_db.to_secret_dict() if cfg_db and cfg_db.api_key else None

        # 4. 按职业匹配 + 通用热点，选出候选
        # 已入库标题，避免重复
        existing_titles = set(
            t[0] for t in NewsCache.query.with_entities(NewsCache.title).all()
        )

        candidates = []  # [(item, major_tag, category)]
        for major, keywords in MAJOR_KEYWORD_MAP.items():
            picked = 0
            for it in safe_items:
                if picked >= MAX_PER_MAJOR:
                    break
                if it['title'] in existing_titles:
                    continue
                text = it['title'] + ' ' + it['description']
                if _matches_any(text, keywords):
                    candidates.append((it, major, 'profession'))
                    picked += 1

        # 通用热点补充（排除已选）
        chosen_titles = {c[0]['title'] for c in candidates}
        picked_gen = 0
        for it in safe_items:
            if picked_gen >= MAX_GENERAL:
                break
            if it['title'] in chosen_titles or it['title'] in existing_titles:
                continue
            text = it['title'] + ' ' + it['description']
            if _matches_any(text, GENERAL_KEYWORDS):
                candidates.append((it, '通用', 'general'))
                chosen_titles.add(it['title'])
                picked_gen += 1

        if not candidates:
            logger.info('[News] 本轮无匹配职业/通用热点的条目，跳过')
            return 0

        # 5. 摘要 + 入库
        added = 0
        now = beijing_now()
        for it, major_tag, cat in candidates:
            try:
                summary = _summarize(it['title'], it['description'], llm_config, threshold=sum_threshold)
                row = NewsCache(
                    title=it['title'],
                    summary=summary or it['title'],
                    source=it['source'],
                    source_url=it.get('link', ''),
                    category=cat,
                    major_tag=major_tag,
                    fetched_at=now,
                    used=False,
                )
                db.session.add(row)
                added += 1
            except Exception as e:
                logger.warning(f"[News] 入库失败 ({it['title'][:30]}): {e}")
                continue

        if added:
            try:
                db.session.commit()
                logger.info(f"[News] 本轮入库 {added} 条")
            except Exception as e:
                db.session.rollback()
                logger.error(f"[News] 入库 commit 失败: {e}")
                added = 0

        # 6. 清理旧缓存
        try:
            cutoff = beijing_now() - timedelta(days=retain_days)
            old = NewsCache.query.filter(NewsCache.fetched_at < cutoff).all()
            for o in old:
                db.session.delete(o)
            if old:
                db.session.commit()
                logger.info(f"[News] 清理 {len(old)} 条过期缓存")
        except Exception as e:
            db.session.rollback()
            logger.warning(f"[News] 清理旧缓存失败（不影响）: {e}")

        return added


def get_unused_news(major=None, limit=3):
    """查询未注入的新闻条目。

    major 指定时优先返回该职业 + 通用条目；否则返回全部未用。
    返回 NewsCache 对象列表（按抓取时间倒序）。
    """
    from backend.models import NewsCache
    q = NewsCache.query.filter_by(used=False)
    if major:
        # 优先该职业，再补通用
        own = q.filter(NewsCache.major_tag == major).order_by(
            NewsCache.fetched_at.desc()).limit(limit).all()
        if len(own) < limit:
            extra = NewsCache.query.filter(
                NewsCache.used == False,  # noqa: E712
                NewsCache.major_tag == '通用',
                NewsCache.id.notin_([o.id for o in own]) if own else True,
            ).order_by(NewsCache.fetched_at.desc()).limit(limit - len(own)).all()
            own.extend(extra)
        return own[:limit]
    return q.order_by(NewsCache.fetched_at.desc()).limit(limit).all()


def mark_news_used(news_ids):
    """标记一批新闻为已用。"""
    if not news_ids:
        return
    from backend.models import NewsCache, db
    now = beijing_now()
    rows = NewsCache.query.filter(NewsCache.id.in_(news_ids)).all()
    for r in rows:
        r.used = True
        r.used_at = now
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"[News] 标记已用失败: {e}")


def inject_news_for_character(char, max_items=None):
    """把未用新闻写成 EventLog(event_type='news') 注入当前角色今日事件。

    在 dialogue.build_dynamic_context 构建前调用，写出的 EventLog 行会被
    【今日事件】查询自动引用（按 game_day + character_name 过滤）。
    同一新闻标题对同一角色+同一天只写一次（幂等）。
    max_items 为 None 时从 NewsSettings 配置表读取 max_inject_per_day。
    返回新建 EventLog 行数。
    """
    from backend.models import EventLog, db
    # 从配置表读取全局设置（注入上限 + 推送间隔）
    settings = get_news_settings()
    if max_items is None:
        max_items = settings['max_inject_per_day']
    major = getattr(char, 'major', '') or ''
    char_name = getattr(char, 'name', '') or ''
    game_day = getattr(char, 'game_day', 0) or 0
    # Character 用 game_hour + game_minute 存时间，组合成 HH:MM 字符串
    gh = getattr(char, 'game_hour', None)
    gm = getattr(char, 'game_minute', None)
    if gh is not None:
        game_time = f'{int(gh):02d}:{int(gm or 0):02d}'
    else:
        game_time = '08:00'
    if not char_name or game_day <= 0:
        return 0

    # 推送间隔（天）：>0 时按间隔投放，避免每天刷屏
    push_interval_days = settings.get('push_interval_days') or 0
    if push_interval_days > 0:
        last_day = db.session.query(db.func.max(EventLog.game_day)).filter_by(
            event_type='news', character_name=char_name
        ).scalar()
        if last_day is not None and (game_day - int(last_day)) < push_interval_days:
            return 0

    # 今日已注入的新闻事件数；已满则不再注入（避免每次对话都刷屏）
    existing_news_count = EventLog.query.filter_by(
        event_type='news', game_day=game_day, character_name=char_name
    ).count()
    need = max_items - existing_news_count
    if need <= 0:
        return 0

    candidates = get_unused_news(major=major, limit=need)
    if not candidates:
        return 0

    # 已存在于今日 EventLog 的新闻标题（幂等：避免重复写入）
    existing_titles = set(
        t[0] for t in EventLog.query.with_entities(EventLog.title).filter(
            EventLog.game_day == game_day,
            EventLog.character_name == char_name,
            EventLog.event_type == 'news',
        ).all()
    )

    created = 0
    used_ids = []
    for n in candidates:
        if n.title in existing_titles:
            continue
        # 把新闻的抓取时间写入 effects，前端用于显示现实时间
        news_effects = {}
        if n.fetched_at:
            news_effects['real_world_time'] = n.fetched_at.isoformat()
        ev = EventLog(
            event_type='news',
            title=n.title,
            description=n.summary or n.title,
            effects=json.dumps(news_effects, ensure_ascii=False),
            game_day=game_day,
            game_time=game_time,
            character_name=char_name,
            location='',
        )
        db.session.add(ev)
        used_ids.append(n.id)
        created += 1
        existing_titles.add(n.title)

    if created:
        try:
            db.session.commit()
            mark_news_used(used_ids)
            logger.info(f"[News] 为 {char_name} 注入 {created} 条资讯事件")
        except Exception as e:
            db.session.rollback()
            logger.warning(f"[News] 注入资讯事件失败: {e}")
            return 0
    return created


# ============================================================================
# 后台抓取线程（路线①：独立于游戏 tick，每现实小时抓一次）
# ============================================================================
class NewsFetcherThread(threading.Thread):
    """每小时抓取一次新闻写缓存。daemon 线程，进程退出自动结束。"""

    def __init__(self, app, interval: int = 3600, start_delay: int = 120):
        super().__init__(daemon=True)
        self.app = app
        self.interval = interval
        self.start_delay = start_delay
        self._stop_event = threading.Event()

    def run(self):
        logger.info(f"[NewsFetcher] 线程启动 start_delay={self.start_delay}s interval={self.interval}s")
        # 启动延迟，避开 Flask 启动高峰
        if self._stop_event.wait(self.start_delay):
            return
        while not self._stop_event.is_set():
            try:
                added = fetch_and_cache_news(self.app)
                if added:
                    logger.info(f"[NewsFetcher] 本轮入库 {added} 条新闻")
            except Exception as e:
                logger.error(f"[NewsFetcher] 抓取异常（不影响游戏）: {e}")
            # 等待下一轮，可被 stop 中断；每次从数据库重读间隔，前端修改无需重启
            try:
                interval = get_news_settings().get('fetch_interval') or self.interval
            except Exception:
                interval = self.interval
            self._stop_event.wait(interval)

    def stop(self):
        self._stop_event.set()


def start_news_fetcher(app, interval: int = 3600, start_delay: int = 120):
    """启动后台新闻抓取线程。在 create_app 中调用。"""
    t = NewsFetcherThread(app, interval=interval, start_delay=start_delay)
    t.start()
    return t
