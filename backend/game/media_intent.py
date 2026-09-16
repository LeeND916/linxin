"""聊天生图 · 硬逻辑意图识别（L0 按钮 / L1 正则 / L2 向量 / L3 不触发）。

设计原则：宁可漏拍，不可乱拍。
执行顺序：否定表 → 正则白名单 → 向量兜底（过关键词门）。

本模块零前端依赖，纯函数为主；冷却闸门需要 DB（PhotoRecord）时采用懒加载，
PhotoRecord 未建表时对应闸门自动降级为「不拦截」，待 P3 建表后自动生效。
"""
import re
import threading
import logging

from backend.config import beijing_now

logger = logging.getLogger('backend.game.media_intent')

# ──────────────────────────────────────────────────────────────
# 3.2 玩家侧正则全表（46 条，按优先级取一）
# ──────────────────────────────────────────────────────────────
PLAYER_PATTERNS = {
    # ── 场景 1 · photo_take（拍照）10 条 ──
    'photo_take': [
        r'(给|帮|替)(你|妳|她)?(拍|照)(张|个|一)?(照片|相片|照|美照)?',
        r'我来?(给|帮)(你|妳)?拍',
        r'(拍|照)(张|个|一)(照片|相片|美照)',
        r'咔嚓',
        r'看(下|一下)?镜头',
        r'笑一个',
        r'别动.{0,4}(拍|照)',
        r'(站|坐)好.{0,4}(我)?(拍|照)',
        r'(这个|这)(角度|光线).{0,6}(拍|照|记录)',
        r'留(个|下)影',
    ],
    # ── 场景 2 · selfie（自拍）7 条 ──
    'selfie': [
        r'自拍',
        r'(你|妳)(自己)?(拍|照)(张|个)?(自拍)?(发|给)(我|过来)',
        r'(我们|咱们|咱俩|一起)(来)?(拍|照)?(张|个)?(合影|合照|照片|合个影|合影像)',
        r'合影',
        r'合照',
        r'合(个|一)?影',
        r'来张(自拍|合影)',
        r'举(起)?(手机|相机)(拍|自拍)',
    ],
    # ── 场景 3 · activity_shot（活动特写）7 条 ──
    'activity_shot': [
        r'(拍|记录)(下|一下)(你|妳)?(现在|正在)?(在)?(做|干)',
        r'(这个|你这)(动作|姿势|样子).{0,4}(拍|照|记录|留)',
        r'(专心|认真|投入)(的|地)?(样子|时候).{0,4}(拍|照|记录)',
        r'(工作|学习|画画|写作|训练|做菜)(的)?(样子|状态).{0,4}(拍|照|记录)',
        r'别看(我|镜头).{0,6}(拍|照)',
        r'抓拍',
        r'偷拍(你|妳)?(一张)?',
    ],
    # ── 场景 4 · outfit_change（换装）——必须有明确换衣动作 + 服装目标 ──
    # 单独的“转个圈/展示一下/走秀”不再视为换装，避免普通聊天误触发生图。
    'outfit_change': [
        r'(换|穿)(上|件|身|一身|一套)?(这|那|新)?(套|身|件)?(衣服|裙子|裤子|外套|制服|睡衣|内衣|家居服|运动服)',
        r'(换|穿)(个|身|一身|一套)(不一样|别的|适合)[^，。！？!?]{0,12}(衣服|穿搭|装|裙|裤|外套|睡衣|内衣)',
        r'(换|穿)(个|身|一身|一套)[^，。！？!?]{0,12}(约会|逛街|运动|上班|居家|休息)[^，。！？!?]{0,8}(衣服|穿搭|装|裙|裤|外套|睡衣|内衣)',
        r'(新)(衣服|裙子|外套|穿搭).{0,6}(换|穿|试|展示|拍|看看)',
        r'(试试|穿上)(这|那|新)?(套|身|件)?(衣服|裙子|裤子|外套|制服|睡衣|内衣|家居服|运动服)',
        r'(这身|那身|这套|那套).{0,4}(换|穿|展示|拍|照|看看)',
        r'(给我|让我)?换装(玩玩|看看|一下)?',
    ],
    # ── 场景 5 · scene_freeze（场景定格）7 条 ──
    'scene_freeze': [
        r'(把)?(这|此)(一)?(刻|幕|瞬间|画面|场景)(定格|记录|留)',
        r'(记录|留住|定格)(这|此)(一)?(刻|幕|瞬间)',
        r'(现在|此刻)(这|的)?(样子|画面|氛围).{0,4}(记|留|拍)',
        r'(想|要)(记住|留住)(现在|此刻|这个)',
        r'画面感',
        r'(值得|该)(纪念|记录)',
        r'(这)(气氛|氛围|感觉)(真|太)?(好|棒).{0,6}(拍|记录|留)',
    ],
    # ── 场景 6 · gift_photo（礼物）——必须有送礼动作 + 明确物品 ──
    # “收下吧/惊喜/拆开看看”不再单独触发，避免普通对话误拍。
    'gift_photo': [
        r'(送|给)(你|妳)(一个|一束|一份|一杯|个|束|份|杯)?[^，。！？!?]{0,12}(礼物|花|蛋糕|礼盒|巧克力|奶茶|项链|戒指|书|玩偶|香水)',
        r'(这|这个|这份|这束|这盒|这杯)(是|个|份|束|盒|杯)?(给|送)(你|妳)的[^，。！？!?]{0,8}(花|蛋糕|礼物|礼盒|巧克力|奶茶|项链|戒指|书|玩偶|香水)',
        r'(准备|买)了(个|份|束|杯|盒)?[^，。！？!?]{0,10}(花|蛋糕|礼物|礼盒|巧克力|奶茶|项链|戒指|书|玩偶|香水)[^，。！？!?]{0,8}(给|送)(你|妳)',
        r'(送|给)(你|妳)[^，。！？!?]{0,12}(生日|节日|纪念日)(礼物|花|蛋糕|礼盒)',
    ],
}

# 玩家侧命中多类时的优先级（具体语义优先于泛化语义）
PLAYER_PRIORITY = ['gift_photo', 'outfit_change', 'selfie', 'activity_shot', 'scene_freeze', 'photo_take']

# ──────────────────────────────────────────────────────────────
# 3.3 女主侧正则全表（14 条，场景 7 专用，LLM 回复之后扫描）
# ──────────────────────────────────────────────────────────────
REPLY_PATTERNS = {
    # ── 7a · share_new（她刚拍的）6 条 ──
    'share_new': [
        r'给(你|妳)看(看|下)?.{0,8}(拍|照|的)?(照片|相片|图)',
        r'(我)?(刚|刚刚|刚才).{0,6}(拍|照).{0,4}(照片|相片|图)',
        r'(发|传|给)(你|妳).{0,6}(照片|相片|图)',
        r'(这是|这张).{0,6}(我)?(拍|照)的',
        r'(我).{0,6}(拍|照).{0,4}(你|妳)?(看|瞧)',
        r'(要不要|想不想)看(看|下)?(我拍的)?(照片|图)',
    ],
    # ── 7b · share_recall（她提起旧照）8 条 ──
    'share_recall': [
        r'(上次|之前|那天|那次|以前|前几天)(我们|你|妳)?(在)?.{0,8}(拍|照)(的)?(那)?(张|个)?(照片|相片|图)',
        r'(还)?记得.{0,10}(拍|照)(的)?(那)?(张|个)?(照片|相片)',
        r'(翻|找)(到|出)(了)?(以前|之前|上次)(的)?(照片|相片)',
        r'(我)(相册|手机)里(还)?(有|存着)',
        r'(那)(张|个)(照片|相片).{0,8}(我)?(还)?(留|存|收)着',
        r'(想起|想到)(上次|那天|之前).{0,8}(拍|照)',
        r'(给你看看)(上次|之前|那天)',
        r'(旧|老)(照片|相片)',
    ],
}

# 女主侧子类型判定顺序：先 share_recall（复用旧照最安全），再 share_new
REPLY_PRIORITY = ['share_recall', 'share_new']


# ──────────────────────────────────────────────────────────────
# 3.4 否定表（18 条，最先跑，一票否决）
# ──────────────────────────────────────────────────────────────
NEGATIVE_PATTERNS = [
    # ── 指代已有照片（在评论，不是在要）5 条 ──
    r'(上次|之前|那天|昨天|刚才|刚刚|以前)(的|那)?(照片|相片|图)',
    r'(这|那)(张|个)(照片|相片|图)(拍得|真|好|很|不错|挺)',
    r'(照片|相片)(拍|照)得(真|好|不错|挺)',
    r'(删|删除|扔)(掉|了)?(那|这)?(张|个)?(照片|相片)',
    r'(存|保存|收藏)(好|着|起来)(了)?(那|这)?(张|个)?(照片|相片)',
    # ── 疑问 / 假设 / 未来时 5 条 ──
    r'(会|能|可以|要是|如果|假如|想不想|要不要|愿不愿意).{0,6}(拍|照)',
    r'(拍|照)(照片|相)?(吗|么|嘛|\?|？)',
    r'(以后|下次|改天|有空|将来).{0,6}(拍|照)',
    r'(喜欢|讨厌|害怕|擅长)(拍|照|被拍)',
    r'(为什么|怎么).{0,4}(拍|照)',
    # ── 否定 / 拒绝 4 条 ──
    r'(不|别|甭|勿)(想|要|用|准|许)?(拍|照)',
    r'(不想|不要|不用|别再)(拍|照)',
    r'(拍|照)(什么|啥)(呀|啊|呢)?',
    r'(算|得)了.{0,4}(不|别)(拍|照)',
    # ── 元讨论（在聊摄影这件事本身）4 条 ──
    r'(摄影|相机|镜头|构图|单反|微单|快门|光圈|曝光)(技术|参数|设备|课程)?',
    r'(手机|相机)(拍|照)(得|的)(怎么样|如何|好不好)',
    r'(修图|美颜|滤镜|P图)',
    r'(摄影师|拍照的人|影楼)',
]


# ──────────────────────────────────────────────────────────────
# 3.5 参数抽取正则
# ──────────────────────────────────────────────────────────────
GIFT_EXTRACT = [
    r'(送|给)(你|妳|给|的|了)*(一)?[个只束份杯支盒瓶]?(?P<gift>[^，,。！!？?\s]{1,12}?)(吧|哦|呢|啊|。|，|！|$)',
    r'(?P<gift>[^，,。！!？?\s]{1,12}?)(送|给)(你|妳)',
    r'(买|准备)(了)?(?P<gift>[^，,。！!？?\s]{1,12}?)(给|送)(你|妳)',
]
# 抽到的礼物名可能带前导动词/量词，统一剥掉
GIFT_STRIP = re.compile(r'^(给|你|妳|的|了|买|准备|送|一|个|只|束|份|杯|支|盒|瓶)+')
GIFT_STOPWORDS = {'礼物', '东西', '一个', '这个', '那个', '什么'}
GIFT_FALLBACK = '一个包装精美的礼物盒'

# ──────────────────────────────────────────────────────────────
# 3.6 L2 向量兜底（独立原型库，复用项目常驻 BGE 通道）
# ──────────────────────────────────────────────────────────────
MEDIA_PROTOTYPES = {
    'photo_take':    ["我给你拍张照片", "站好我拍一张", "来，看镜头",
                      "这个角度真好看我拍下来", "别动我给你照一张", "留个影吧"],
    'selfie':        ["我们一起拍张合影吧", "来张自拍", "咱俩合个影",
                      "你自己拍一张发我", "举起手机拍一张"],
    'activity_shot': ["把你现在做事的样子拍下来", "你专心的样子真好看我记录一下",
                      "别看镜头我抓拍一张", "拍一下你工作的状态"],
    'outfit_change': ["换上那件新衣服给我看看", "试试这身", "转个圈让我看看",
                      "展示一下你的新穿搭"],
    'scene_freeze':  ["把这一刻定格下来", "这个氛围真好想记录下来",
                      "我想留住现在这个画面", "此刻值得纪念"],
    'gift_photo':    ["这是送给你的礼物", "我给你买了束花", "收下吧",
                      "准备了个惊喜给你"],
}

# 关键词门：向量命中后原文必须含其一，否则降级不触发
KEYWORD_GATE = set('拍照摄影自拍合影换穿试送礼物定格记录留住')

# BGE 查询前缀（与 intent_classifier 一致）
_BGE_QUERY_PREFIX = "为这个句子分类："

# 向量阈值（高于 intent_classifier 的 0.40，双保险）
VECTOR_THRESHOLD = 0.55

# ── 编译正则（统一 IGNORECASE）──
_NEG_RE = [re.compile(p, re.IGNORECASE) for p in NEGATIVE_PATTERNS]
_PLAYER_RE = {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in PLAYER_PATTERNS.items()}
_REPLY_RE = {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in REPLY_PATTERNS.items()}
_GIFT_RE = [re.compile(p, re.IGNORECASE) for p in GIFT_EXTRACT]

# 向量原型缓存
_proto_cache = {}
_proto_loaded = False
_proto_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════
# 基础判定
# ══════════════════════════════════════════════════════════════
def hit_negative(text: str) -> bool:
    """否定表一票否决（最先跑）。"""
    if not text:
        return False
    return any(rx.search(text) for rx in _NEG_RE)


def hit_regex(text: str, side: str = 'player'):
    """正则白名单。返回 scene_type 或 None，按优先级取一。"""
    if not text:
        return None
    if side == 'player':
        table, order = _PLAYER_RE, PLAYER_PRIORITY
    else:
        table, order = _REPLY_RE, REPLY_PRIORITY
    for scene in order:
        for rx in table.get(scene, []):
            if rx.search(text):
                return scene
    return None


def _ensure_prototypes():
    """懒加载并编码媒体意图原型（进程内只做一次）。"""
    global _proto_loaded
    if _proto_loaded:
        return
    with _proto_lock:
        if _proto_loaded:
            return
        import numpy as np
        from backend.game.memory import embed_text_vector
        all_phrases, mapping = [], []
        for at, phrases in MEDIA_PROTOTYPES.items():
            for p in phrases:
                all_phrases.append(p)
                mapping.append(at)
        if not all_phrases:
            _proto_loaded = True
            return
        vecs = [embed_text_vector(p) for p in all_phrases]
        buckets = {}
        for vec, at in zip(vecs, mapping):
            buckets.setdefault(at, []).append(np.asarray(vec, dtype='float32'))
        for at, lst in buckets.items():
            centroid = np.stack(lst, axis=0).mean(axis=0)
            n = np.linalg.norm(centroid)
            if n > 0:
                centroid = centroid / n
            _proto_cache[at] = centroid
        _proto_loaded = True
        logger.info(f"[MediaIntent] 原型库已加载：{len(_proto_cache)} 类")


def keyword_gate(text: str) -> bool:
    """向量命中后的关键词门：原文必须含拍照相关字。"""
    return any(ch in text for ch in KEYWORD_GATE)


def hit_vector(text: str, side: str = 'player') -> tuple:
    """向量兜底。返回 (scene_type, score)。"""
    if not text:
        return (None, 0.0)
    try:
        _ensure_prototypes()
    except Exception as e:
        logger.warning(f"[MediaIntent] 原型加载失败，跳过向量: {e}")
        return (None, 0.0)
    if not _proto_cache:
        return (None, 0.0)
    import numpy as np
    from backend.game.memory import embed_text_vector
    try:
        qvec = np.asarray(embed_text_vector(_BGE_QUERY_PREFIX + text), dtype='float32')
        n = np.linalg.norm(qvec)
        if n > 0:
            qvec = qvec / n
    except Exception as e:
        logger.warning(f"[MediaIntent] 编码失败，跳过向量: {e}")
        return (None, 0.0)
    best_at, best_sim = None, -1.0
    for at, pvec in _proto_cache.items():
        sim = float(np.dot(qvec, pvec))
        if sim > best_sim:
            best_sim, best_at = sim, at
    return (best_at, round(best_sim, 4))


# ══════════════════════════════════════════════════════════════
# 四级漏斗主入口
# ══════════════════════════════════════════════════════════════
def detect_media_intent(text: str, side: str = 'player'):
    """返回 (scene_type, confidence) 或 None。

    L0（显式按钮）由调用方直接给定 scene_type 跳过本函数。
    执行顺序：否定表 → 正则 → 向量（过关键词门）。
    """
    if not text or not text.strip():
        return None
    if hit_negative(text):
        return None                                  # ① 一票否决
    st = hit_regex(text, side)                       # ② 正则白名单
    if st:
        return (st, 1.0)
    st, score = hit_vector(text, side)               # ③ 向量兜底
    if st and score >= VECTOR_THRESHOLD and keyword_gate(text):
        return (st, score)
    return None


# ══════════════════════════════════════════════════════════════
# 3.5 参数抽取
# ══════════════════════════════════════════════════════════════
def extract_gift(text: str):
    """从礼物场景原文抽礼物名；抽不到返回兜底描述。"""
    if not text:
        return GIFT_FALLBACK
    for rx in _GIFT_RE:
        m = rx.search(text)
        if m and m.group('gift'):
            g = GIFT_STRIP.sub('', m.group('gift')).strip()
            if g and g not in GIFT_STOPWORDS:
                return g
    return GIFT_FALLBACK


def extract_outfit_name(text: str, character=None):
    """从换装场景原文抽穿搭名。

    优先匹配当前穿搭组件名（精确子串）；不做模糊匹配——猜错衣服比不换更糟。
    都找不到返回 None（等价于普通拍照，用当前穿搭）。
    """
    if not text or character is None:
        return None
    try:
        outfit = character.get_current_outfit()
        comps = (outfit or {}).get('components', {})
        names = []
        for slot, item in comps.items():
            if isinstance(item, list):
                for sub in item:
                    if sub.get('name'):
                        names.append(sub['name'])
            elif isinstance(item, dict) and item.get('name'):
                names.append(item['name'])
        for name in names:
            if name and name in text:
                return name
    except Exception as e:
        logger.warning(f"[MediaIntent] 穿搭名抽取失败: {e}")
    return None


# ══════════════════════════════════════════════════════════════
# 3.7 冷却与门槛（四道闸）
# ══════════════════════════════════════════════════════════════
# 并发锁：同角色同时只允许 1 个生图任务
_GEN_LOCK = set()
_LOCK_LOCK = threading.Lock()

# 轮次冷却：同角色最近一次触发的 (game_day, game_minute)
_LAST_TRIGGER = {}
_TRIGGER_LOCK = threading.Lock()

# 真实时钟：同角色最近一次触发生成的真实时间（用于真实间隔节流）
_LAST_TRIGGER_REAL = {}
_TRIGGER_REAL_LOCK = threading.Lock()

# 旧：DAILY_CAP=6 按游戏日上限（早期版本，已被 GAME_DAILY_CAP 取代）
DAILY_CAP = 6
SHARE_PHOTO_CAP = 2
ROUND_COOLDOWN_MINUTES = 3   # 同一 game_minute 内禁止连发（近似轮次冷却）

# 限流常量
REAL_COOLDOWN_SECONDS = 30   # 同角色两次生图最小真实间隔（防「再拍一张」连发刷图）
GAME_DAILY_CAP = 8           # 每游戏日（角色 game_day）生成上限：跳过/推进一天即重置

# 状态门槛
REJECT_MOOD_LOW = 20
REJECT_ENERGY_LOW = 15

# 女主侧自主拍照总开关（side='reply' 的 share_new / share_recall）：
# False = 女主回复不再自动触发生图/翻旧照（玩家侧检测与前端拍照按钮不受影响）。
# 此为代码默认值；实际生效值优先读 DB game_setting(key='reply_side_auto_photo')，
# 前端「角色属性 → 聊天设置」面板可改（写 DB 后立即生效）。
REPLY_SIDE_AUTO_PHOTO_ENABLED = False

# 开关 DB 读取缓存（TTL 秒）——避免每轮对话都查一次设置表
_REPLY_SIDE_CACHE = {'value': None, 'ts': 0.0}
_REPLY_SIDE_TTL = 10.0


def get_reply_side_enabled() -> bool:
    """女主侧自主拍照开关的生效值：DB 覆盖 > 代码默认值（带 TTL 缓存）。"""
    import time as _time
    now = _time.time()
    if _REPLY_SIDE_CACHE['ts'] and now - _REPLY_SIDE_CACHE['ts'] < _REPLY_SIDE_TTL \
            and _REPLY_SIDE_CACHE['value'] is not None:
        return _REPLY_SIDE_CACHE['value']
    val = REPLY_SIDE_AUTO_PHOTO_ENABLED
    try:
        from backend.models import GameSetting
        row = GameSetting.query.get('reply_side_auto_photo')
        if row is not None:
            val = str(row.value).strip().lower() in ('true', '1', 'on', 'yes')
    except Exception:
        pass  # DB 不可用时退回代码默认值
    _REPLY_SIDE_CACHE['value'] = val
    _REPLY_SIDE_CACHE['ts'] = now
    return val


def invalidate_reply_side_cache():
    """外部（设置接口）写 DB 后调用，立即使缓存失效。"""
    _REPLY_SIDE_CACHE['value'] = None
    _REPLY_SIDE_CACHE['ts'] = 0.0


def _status_gate(character):
    """状态门槛：心情/体力过低拒绝，返回拒绝语境或 None。"""
    mood = getattr(character, 'mood', 75) or 75
    energy = getattr(character, 'energy', 80) or 80
    if mood < REJECT_MOOD_LOW:
        return '[系统：她心情不好，不太想拍照]'
    if energy < REJECT_ENERGY_LOW:
        return '[系统：她太累了，不想拍照]'
    return None


def _concurrency_gate(name: str):
    """并发锁：已有任务在跑则拒绝。"""
    with _LOCK_LOCK:
        return name in _GEN_LOCK


def _round_cooldown_ok(name: str, game_day: int, game_minute: int):
    """轮次冷却（近似）：同一游戏分钟内禁止重复触发。"""
    with _TRIGGER_LOCK:
        last = _LAST_TRIGGER.get(name)
        if last is None:
            return True
        last_day, last_min = last
        if last_min is None:
            # 历史记录残留 None（旧调用未传 game_minute），不再参与冷却比较
            return True
        if last_day == game_day and abs(game_minute - last_min) < ROUND_COOLDOWN_MINUTES:
            return False
        return True


def _real_cooldown_ok(name: str):
    """真实时钟节流：距上次生图不足 REAL_COOLDOWN_SECONDS 秒则拒绝（防「再拍一张」连发刷图）。"""
    with _TRIGGER_REAL_LOCK:
        last = _LAST_TRIGGER_REAL.get(name)
        if last is None:
            return True
        elapsed = (beijing_now() - last).total_seconds()
        return elapsed >= REAL_COOLDOWN_SECONDS


def _game_daily_cap_ok(name: str, game_day: int):
    """每日上限：按**游戏日（角色 game_day）**统计。

    玩家推进/跳过一天后 game_day 变化，上限即重置——符合「跳一天就能再拍」的预期。
    game_day 为 0（拿不到游戏日）时降级放行。PhotoRecord 未建表时降级放行。
    """
    if not game_day:
        return True
    try:
        from backend.models import PhotoRecord
        cnt = PhotoRecord.query.filter(
            PhotoRecord.character_name == name,
            PhotoRecord.source_day == game_day,
        ).count()
        return cnt < GAME_DAILY_CAP
    except Exception:
        # PhotoRecord 尚未建表（P3 之前）→ 不拦截，交给后续阶段
        return True


def acquire_gen_lock(name: str):
    with _LOCK_LOCK:
        _GEN_LOCK.add(name)


def release_gen_lock(name: str):
    with _LOCK_LOCK:
        _GEN_LOCK.discard(name)


def record_trigger(name: str, game_day: int, game_minute: int):
    with _TRIGGER_LOCK:
        _LAST_TRIGGER[name] = (game_day, game_minute)


def record_trigger_real(name: str):
    """记录本次生发的真实时间（供 _real_cooldown_ok 节流）。"""
    with _TRIGGER_REAL_LOCK:
        _LAST_TRIGGER_REAL[name] = beijing_now()


def evaluate_media_intent(character, text: str, side: str = 'player',
                          scene_override: str = None, game_day: int = 0,
                          game_minute: int = 0):
    """综合判定：是否触发生图 + 抽取参数 + 过四道闸。

    返回 dict：
        triggered      bool       是否触发生图
        scene_type     str|None   场景类型
        retrieve_only  bool       是否仅检索旧照（share_recall，不生图）
        confidence     float      置信度（L0=1.0，L1=1.0，L2=cos）
        gift           str|None   礼物名（gift_photo）
        outfit_name    str|None   穿搭名（outfit_change）
        reason         str|None   未触发原因
        reject_context str|None   注入女主回复的拒绝语境（状态门槛）
    """
    # 统一游戏时间口径：调用方未传时回退到角色当前 game_day/game_minute
    game_day = game_day or getattr(character, 'game_day', 0)
    game_minute = game_minute or getattr(character, 'game_minute', 0)

    # 女主侧自主拍照总开关：关闭时 reply 侧直接不触发（share_new/share_recall 全部失效）
    if side == 'reply' and not get_reply_side_enabled():
        return {'triggered': False, 'scene_type': None, 'retrieve_only': False,
                'confidence': 0.0, 'gift': None, 'outfit_name': None,
                'reason': 'reply_side_disabled', 'reject_context': None}

    # 1) 确定场景
    if scene_override:
        scene_type, confidence = scene_override, 1.0
    else:
        res = detect_media_intent(text, side)
        if res is None:
            return {'triggered': False, 'scene_type': None, 'retrieve_only': False,
                    'confidence': 0.0, 'gift': None, 'outfit_name': None,
                    'reason': 'no_intent', 'reject_context': None}
        scene_type, confidence = res

    # 2) share_recall：仅检索旧照，不生图
    if side == 'reply' and scene_type == 'share_recall':
        return {'triggered': False, 'scene_type': 'share_recall', 'retrieve_only': True,
                'confidence': confidence, 'gift': None, 'outfit_name': None,
                'reason': None, 'reject_context': None}

    name = getattr(character, 'name', None)
    if not name:
        return {'triggered': False, 'scene_type': scene_type, 'retrieve_only': False,
                'confidence': confidence, 'gift': None, 'outfit_name': None,
                'reason': 'no_character', 'reject_context': None}

    # 3) 四道闸
    reject = _status_gate(character)
    if reject:
        return {'triggered': False, 'scene_type': scene_type, 'retrieve_only': False,
                'confidence': confidence, 'gift': None, 'outfit_name': None,
                'reason': 'status_low', 'reject_context': reject}

    # 场景定格（scene_freeze）放开并发锁：氛围记录不受生图任务占用限制
    if scene_type != 'scene_freeze' and _concurrency_gate(name):
        return {'triggered': False, 'scene_type': scene_type, 'retrieve_only': False,
                'confidence': confidence, 'gift': None, 'outfit_name': None,
                'reason': 'busy',
                'reject_context': '[系统：她正在忙别的事，让你稍等一下]'}

    # 场景定格（scene_freeze）放开轮次冷却：连续定格不受 3 分钟游戏分限制
    if scene_type != 'scene_freeze' and not _round_cooldown_ok(name, game_day, game_minute):
        return {'triggered': False, 'scene_type': scene_type, 'retrieve_only': False,
                'confidence': confidence, 'gift': None, 'outfit_name': None,
                'reason': 'round_cooldown',
                'reject_context': '[系统：她刚才已经拍过一张，嫌你催得太急，笑着说等会儿再拍]'}

    if not _real_cooldown_ok(name):
        return {'triggered': False, 'scene_type': scene_type, 'retrieve_only': False,
                'confidence': confidence, 'gift': None, 'outfit_name': None,
                'reason': 'real_cooldown',
                'reject_context': '[系统：她刚放下手机，让你别急着连拍]'}

    # 场景定格（scene_freeze）放开每日上限：氛围记录不占每日 8 张额度
    if scene_type != 'scene_freeze' and not _game_daily_cap_ok(name, game_day):
        return {'triggered': False, 'scene_type': scene_type, 'retrieve_only': False,
                'confidence': confidence, 'gift': None, 'outfit_name': None,
                'reason': 'real_daily_cap',
                'reject_context': '[系统：她今天照片已经拍够了，说改天再拍]'}

    # 4) 参数抽取
    gift = extract_gift(text) if scene_type == 'gift_photo' else None
    outfit_name = extract_outfit_name(text, character) if scene_type == 'outfit_change' else None

    return {'triggered': True, 'scene_type': scene_type, 'retrieve_only': False,
            'confidence': confidence, 'gift': gift, 'outfit_name': outfit_name,
            'reason': None, 'reject_context': None}
