"""角色穿搭引擎 v4 — 基于 DB 的组件式多因子决策穿搭系统"""
import json
import logging
import random
from datetime import date

from backend.models import db
from backend.models import (
    Character, Appearance, OutfitComponent, OutfitPreset, EventLog,
    Weather,
)

log = logging.getLogger(__name__)

# ============================================================
# Step 1：季节判定
# ============================================================

def get_season(char):
    """
    使用 char.get_game_date() 获取月份映射为季节。
    3-4月=春, 5-9月=夏, 10-11月=秋, 12-2月=冬。

    注意：不使用简化的 (game_day % 365) // 30 公式，
    必须走 char.get_game_date() 返推算月份。
    """
    game_date = char.get_game_date()

    if isinstance(game_date, tuple) and len(game_date) >= 2:
        # get_game_date() 返回 (year, month, day, weekday_name)
        month = game_date[1]
    elif hasattr(game_date, 'month'):
        month = game_date.month
    else:
        # 兜底：按字符串解析月份
        month_str = _month_from_gamedate_str(str(game_date))
        try:
            month = int(month_str)
        except (ValueError, TypeError):
            # 二次兜底：用简化的 game_day 公式
            month = ((char.game_day % 365) // 30) + 1

    if month in (3, 4):
        return "spring"
    elif 5 <= month <= 9:
        return "summer"
    elif month in (10, 11):
        return "autumn"
    else:
        return "winter"


def _month_from_gamedate_str(date_str):
    """从游戏日期字符串中提取月份，如 '3月15日' → 3"""
    for sep in ("月", "-", "/"):
        parts = date_str.replace("年", sep).replace("日", "").split(sep)
        if len(parts) >= 2:
            try:
                return int(parts[0].strip())
            except (ValueError, TypeError):
                continue
    return 6  # 兜底


# ============================================================
# Step 2：场合判定
# ============================================================

_OCCASION_MAP = {
    "dormitory": "home", "home": "home", "apartment": "home",
    "classroom": "daily", "library": "daily", "lab": "daily", "studio": "daily",
    "office": "formal", "law_firm": "formal", "hospital": "formal",
    "gym": "sport", "playground": "sport", "park": "daily",
    "cafe": "social", "restaurant": "social", "bar": "social",
    "court": "formal",
}


def get_occasion(location, has_social=False, meeting_target=None):
    """位置 + 社交状态 → 场合类型"""
    base = _OCCASION_MAP.get(location, "daily")
    if meeting_target and base != "formal":
        base = "social"
    if has_social and base == "daily":
        base = "social"
    return base


# ============================================================
# Step 3：候选池过滤
# ============================================================

def get_candidate_presets(char_id, season, occasion):
    """
    从 outfit_presets 表筛选候选穿搭。
    多级兜底：season+occasion → 仅season → 全量。
    """
    candidates = OutfitPreset.query.filter_by(
        character_id=char_id,
        season=season,
        occasion=occasion,
    ).all()

    if not candidates:
        candidates = OutfitPreset.query.filter_by(
            character_id=char_id,
            season=season,
        ).order_by(
            OutfitPreset.use_count.asc(),
            OutfitPreset.last_used_day.asc(),
        ).limit(20).all()

    if not candidates:
        candidates = OutfitPreset.query.filter_by(
            character_id=char_id,
        ).order_by(
            OutfitPreset.use_count.asc(),
        ).limit(20).all()

    return candidates


# ============================================================
# Step 4：多因子评分
# ============================================================

def _score_preset(preset, mood, energy, intimacy, affection, meeting_target, game_hour, temperature=None):
    """
    八因子评分（0~100），权重分配：
    - 心情因子 10%
    - 精力因子 12%
    - 玩家关系因子 15%
    - 好感度附加 10%
    - 颜色偏好 8%
    - 时间修正 10%
    - 温度感知惩罚（高温/低温）
    - 基础分 50%
    """
    score = 50.0

    # --- 心情因子 ---
    if mood >= 80 and preset.formality_level >= 3:
        score += 10
    elif mood < 30 and preset.formality_level >= 3:
        score -= 8

    # --- 精力因子 ---
    if energy < 30:
        if preset.formality_level <= 2:
            score += 12
        else:
            score -= 10

    # --- 玩家关系因子 ---
    if meeting_target == "player":
        if intimacy >= 70 and preset.formality_level >= 3:
            score += 15
        elif intimacy >= 40 and preset.formality_level >= 2:
            score += 8
        elif intimacy <= 20:
            score -= 5

    # --- 好感度附加 ---
    if meeting_target == "player" and affection >= 60:
        tags = (_derive_preset_style_tags(preset) or "").split(",")
        if any(t in tags for t in ["精致", "约会", "得体"]):
            score += 10

    # --- 颜色偏好 ---
    color_tone = _derive_preset_color_tone(preset)
    if mood >= 75 and color_tone == "warm":
        score += 5
    elif mood <= 30 and color_tone == "cool":
        score += 3

    # --- 时间修正 ---
    if game_hour >= 21 and preset.occasion == "home":
        score += 10

    # --- 温度感知惩罚 ---
    if temperature is not None and preset.warmth_level is not None:
        if temperature >= 28:
            score -= preset.warmth_level * 5
        elif temperature <= 12:
            score -= max(0, (3 - preset.warmth_level)) * 5

    return score


def _score_and_rank(candidates, mood, energy, intimacy, affection, meeting_target, game_hour, temperature=None):
    """批量评分 + 排序（分数降序 → last_used_day 升序）"""
    scored = []
    for p in candidates:
        s = _score_preset(p, mood, energy, intimacy, affection, meeting_target, game_hour, temperature)
        scored.append((p, s))
    scored.sort(key=lambda x: (-x[1], x[0].last_used_day or 0))
    return [p for p, _ in scored]


# ============================================================
# Step 4.5：Preset 属性推导（从组件计算）
# ============================================================

def _derive_preset_color_tone(preset):
    """从 preset 的组件推导主色调，优先 top/outer → neutral"""
    priority = ['top', 'outer', 'bottom', 'dress', 'shoe']
    for type_key in priority:
        ref = (preset.component_ids or {}).get(type_key)
        if ref is None:
            continue
        cid = ref[0] if isinstance(ref, list) else ref
        comp = OutfitComponent.query.get(cid)
        if comp and comp.color_tone:
            return comp.color_tone
    return 'neutral'


def _derive_preset_style_tags(preset):
    """从 preset 的所有组件收集 style_tags，去重排序"""
    tags = set()
    for type_key, ref in (preset.component_ids or {}).items():
        ids = ref if isinstance(ref, list) else [ref]
        for cid in ids:
            comp = OutfitComponent.query.get(cid)
            if comp and comp.style_tags:
                for t in comp.style_tags.split(','):
                    t = t.strip()
                    if t:
                        tags.add(t)
    return ','.join(sorted(tags))


# ============================================================
# Step 5：冷却过滤
# ============================================================

def _apply_cooldown(ranked_presets, game_day, cooldown=3):
    """
    同一 preset 在 cooldown 天内不重复。
    全部冷却→放宽至1天→仍无→选排名最高的。
    """
    available = [p for p in ranked_presets if game_day - (p.last_used_day or 0) >= cooldown]

    if not available:
        available = [p for p in ranked_presets if game_day - (p.last_used_day or 0) >= 1]

    if not available:
        available = [ranked_presets[0]]

    return available[0]


# ============================================================
# Step 6：组件装配
# ============================================================

def _assemble_components(preset):
    """
    根据 preset.component_ids 拼装完整组件 dict。
    处理 accessory 的数组/单值混合格式。
    """
    components = {}
    for type_key, ref in (preset.component_ids or {}).items():
        if isinstance(ref, list):
            # accessory 支持多件
            comps = []
            for cid in ref:
                comp = OutfitComponent.query.get(cid)
                if comp:
                    comps.append(comp.to_dict())
            components[type_key] = comps
        else:
            comp = OutfitComponent.query.get(ref)
            if comp:
                components[type_key] = comp.to_dict()

    # ── 兜底补全：内衣 / 睡衣 ──
    SEASON_MAP = {'spring': 0, 'summer': 1, 'autumn': 2, 'winter': 3}
    season_idx = SEASON_MAP.get(preset.season, -1)

    if 'underwear' not in components or 'sleepwear' not in components:
        all_underwear = OutfitComponent.query.filter_by(
            character_id=preset.character_id, type='underwear'
        ).order_by(OutfitComponent.formality).all()

        if 'underwear' not in components:
            for comp in all_underwear:
                if comp.subtype == 'sleepwear':
                    continue
                if season_idx < 0 or int(comp.season_mask[season_idx]):
                    components['underwear'] = comp.to_dict()
                    break

        if 'sleepwear' not in components:
            for comp in all_underwear:
                if comp.subtype != 'sleepwear':
                    continue
                if season_idx < 0 or int(comp.season_mask[season_idx]):
                    components['sleepwear'] = comp.to_dict()
                    break

    return components


def build_underwear_outfit(char, component_id):
    """（保留兼容）构造仅内衣的穿搭——新流程改用 merge_component_into_outfit 做局部并入。"""
    component = OutfitComponent.query.filter_by(
        id=int(component_id), character_id=char.id, type='underwear'
    ).first()
    if not component:
        return None
    return merge_component_into_outfit(char, component)


def merge_component_into_outfit(char, component, base_outfit=None):
    """把单个部件并入当前穿搭（局部替换）：只替换该部件对应槽位，其余保持不变。

    - 当前穿搭若处于「整段替换/塌缩」状态（只剩内衣或带 replacement_mode），先按规则
      派生一套完整基础穿搭再并入，避免换完只留下一两件。
    - accessory 为数组，按名称去重后追加。
    """
    ctype = (component.type or 'custom').lower()
    if base_outfit is None:
        base_outfit = char.get_current_outfit() or {}
    comps = (base_outfit or {}).get('components') or {}
    # 塌缩/整段替换 → 先恢复完整基础穿搭
    if (base_outfit or {}).get('replacement_mode') or not any(
            k in comps for k in ('top', 'bottom', 'outer', 'shoes')):
        base_outfit = assemble_daily_outfit(char) or _empty_outfit()
    components = dict((base_outfit.get('components') or {}))
    item = component.to_dict()
    if ctype == 'accessory':
        lst = components.get('accessory')
        if isinstance(lst, list):
            lst = [i for i in lst if isinstance(i, dict) and i.get('name') != component.name]
            lst.append(item)
            components['accessory'] = lst
        else:
            components['accessory'] = [item]
    else:
        components[ctype] = item
    return {
        'preset_id': base_outfit.get('preset_id', 0),
        'name': (base_outfit.get('name') or '') + ' · ' + component.name,
        'description': base_outfit.get('description') or component.description or component.name,
        'season': base_outfit.get('season') or get_season(char),
        'occasion': base_outfit.get('occasion') or 'home',
        'style': base_outfit.get('style') or 'home',
        'components': components,
    }


def resolve_outfit_request(char, outfit_name=None, activity_text=''):
    """把明确换装请求解析为真实预设（整套替换）或角色专属部件（局部替换）。

    - 预设名 → 整套替换（_finalize）。
    - 角色专属部件名（内衣/上装/下装/外套/鞋/配饰等）→ 只换对应槽位，保留其余。
    - 未知名称 → 自定义整段替换。
    """
    name = (outfit_name or '').strip()
    if name:
        preset = OutfitPreset.query.filter_by(character_id=char.id, name=name).first()
        if preset:
            return _finalize(preset, char.game_day)
        component = OutfitComponent.query.filter_by(
            character_id=char.id, name=name
        ).first()
        if component:
            return merge_component_into_outfit(char, component)
        return build_custom_replacement_outfit(char, name)
    return select_outfit_for_context(char, activity_text)


def apply_outfit(char, outfit, reason='explicit_action'):
    """把选定穿搭写入人物表、历史和事件日志，并推送前端。"""
    if not outfit:
        return None
    char.current_outfit = json.dumps(outfit, ensure_ascii=False)
    char.outfit_changed_at = char.game_day
    history = list(char.outfit_history or [])
    history.append({'day': char.game_day, 'preset_id': outfit.get('preset_id', 0),
                    'name': outfit.get('name', ''), 'reason': reason})
    char.outfit_history = history[-30:]
    db.session.add(EventLog(
        character_name=char.name, game_day=char.game_day,
        game_time=f'{char.game_hour:02d}:{char.game_minute:02d}',
        event_type='outfit_change', title=f"换装：{outfit.get('name', '未知')}",
        description=f"换装：{outfit.get('name', '未知')} — {outfit.get('description', '')}",
        effects=json.dumps({'outfit': outfit, 'reason': reason}, ensure_ascii=False),
    ))
    db.session.commit()
    try:
        from backend.service.outfit_notifier import notify_outfit_changed
        notify_outfit_changed(char.name, outfit)
    except Exception as exc:
        log.warning(f'[Outfit] notify failed: {exc}')
    return outfit


def build_custom_replacement_outfit(char, outfit_name, description=''):
    """构造照片/实际穿搭使用的完整自定义替换穿搭。"""
    name = (outfit_name or '').strip()
    if not name:
        return None
    return {
        'preset_id': 0,
        'name': name,
        'description': (description or name).strip(),
        'season': get_season(char),
        'occasion': 'home',
        'style': 'home',
        'replacement_mode': 'custom_full_replace',
        'components': {'custom': {'name': name, 'description': (description or name).strip()}},
    }


def _finalize(preset, game_day):
    """组装 + 更新统计 + 返回穿搭 dict"""
    components = _assemble_components(preset)

    preset.use_count = (preset.use_count or 0) + 1
    preset.last_used_day = game_day

    return {
        "preset_id": preset.id,
        "name": preset.name,
        "description": preset.description,
        "season": preset.season,
        "occasion": preset.occasion,
        "style": preset.occasion,       # 兼容旧前端
        "components": components,
    }


# ============================================================
# 主入口：assemble_daily_outfit
# ============================================================

def assemble_daily_outfit(char, bypass_cooldown=False):
    """
    完整决策流程。输入 Character 对象，返回穿搭 dict。

    可用于：
    - 每日 tick 自动换装
    - 切换场所触发换装（bypass_cooldown=True）
    - 玩家主动要求换装
    """
    char_id = char.id
    game_day = char.game_day
    game_hour = char.game_hour
    weather = char.weather or ""
    location = char.location or "dormitory"

    # 获取今日温度
    temperature = None
    yr, mo, dy, _ = char.get_game_date()
    w = Weather.query.filter_by(date=date(yr, mo, dy)).first()
    if w and w.temperature is not None:
        temperature = w.temperature

    mood = char.mood or 50
    energy = char.energy or 50
    intimacy = char.player_intimacy or 55
    affection = char.player_affection or 60

    # 社交检测
    has_social = _detect_social_event(char)
    meeting_target = "player" if _is_meeting_player(char) else None

    season = get_season(char)
    occasion = get_occasion(location, has_social, meeting_target)

    candidates = get_candidate_presets(char_id, season, occasion)

    if not candidates:
        # 最终兜底：全部候选
        candidates = OutfitPreset.query.filter_by(character_id=char_id).limit(20).all()

    if not candidates:
        return _empty_outfit()

    # 天气联动
    candidates = _apply_weather_filters(candidates, weather, occasion) or candidates

    ranked = _score_and_rank(candidates, mood, energy, intimacy, affection, meeting_target, game_hour, temperature)

    if bypass_cooldown:
        chosen = ranked[0] if ranked else None
    else:
        chosen = _apply_cooldown(ranked, game_day)

    return _finalize(chosen, game_day)


def _empty_outfit():
    return {
        "preset_id": 0,
        "name": "默认穿搭",
        "description": "暂无穿搭数据",
        "season": "all",
        "occasion": "daily",
        "style": "daily",
        "components": {},
    }


# ============================================================
# 社交/导师检测
# ============================================================

def _detect_social_event(char):
    """检测当前是否有活跃的社交事件（基于最近 EventLog）"""
    try:
        recent = EventLog.query.filter_by(
            character_name=char.name,
        ).order_by(EventLog.created_at.desc()).first()
        if recent:
            event_type = (recent.event_type or "").lower()
            social_keywords = ["social", "社交", "约会", "见面", "聚会", "派对"]
            return any(kw in event_type for kw in social_keywords)
    except Exception:
        pass
    return False


def _is_meeting_player(char):
    """检测是否即将见导师"""
    try:
        # 当前位置是 formal 场合 → 可能见导师
        if _OCCASION_MAP.get(char.location, "daily") == "formal":
            return True
        # 查最近 EventLog
        recent = EventLog.query.filter_by(
            character_name=char.name,
        ).order_by(EventLog.created_at.desc()).first()
        if recent:
            text = (recent.description or "").lower()
            return any(kw in text for kw in ["导师", "mentor", "指导", "论文"])
    except Exception:
        pass
    return False


# ============================================================
# 天气联动
# ============================================================

_WEATHER_FILTERS = {
    "晴天": {},
    "多云": {},
    "阴天": {"color_tone": "cool"},
    "小雨": {"warmth": (2, 5)},
    "大雨": {"warmth": (3, 5)},
    "大风": {"warmth": (2, 5)},
    "雾霾": {},
    "下雪": {"warmth": (4, 5)},
}

# 天气过滤豁免场合：运动装和居家装不参与保暖度/色调筛选
EXEMPT_OCCASIONS = {"sport", "home"}


def select_outfit_for_context(char, activity_text='', prefer_distinct_from=None):
    """规则选择适合地点、天气、季节、活动和状态的真实角色穿搭。

    prefer_distinct_from: 传入当前穿搭名时，若最佳候选与当前同名则顺延到
    同名以外的最佳预设，避免“场合对了但名字相同”导致界面看起来没换装。
    """
    text = (activity_text or '').strip()
    activity_tags = {
        'sport': ('运动', '健身', '跑步', '慢跑', '瑜伽', '球', '锻炼', '训练', '体能'),
        'social': ('约会', '聚会', '见面', '朋友', '餐厅', '咖啡', '沙龙', '团队', '讨论'),
        'shopping': ('逛街', '购物', '商场', '买东西'),
        'home': ('洗澡', '午休', '睡觉', '休息', '居家', '在家'),
        'formal': ('上班', '工作', '会议', '正式', '演出', '上课', '庭审', '手术', '查房', '客户', '案例', '编程', '代码'),
    }
    tag = next((key for key, words in activity_tags.items() if any(w in text for w in words)), None)
    if tag:
        # 活动自带明确场合：sport/social/shopping/home/formal 直接作为场合，
        # 不再回落 daily（修复“洗澡/休息/居家/逛街”等活动无法换上对应场合穿搭）
        occasion = tag
    else:
        occasion = get_occasion(char.location or 'dormitory')
    season = get_season(char)
    candidates = get_candidate_presets(char.id, season, occasion)
    candidates = _apply_weather_filters(candidates, char.weather or '', occasion) or candidates
    if not candidates:
        return None
    ranked = _score_and_rank(
        candidates, char.mood or 50, char.energy or 50,
        char.player_intimacy or 55, char.player_affection or 60,
        'player' if _is_meeting_player(char) else None,
        char.game_hour or 8, None,
    )
    if not ranked:
        return None
    # 若要求与当前穿搭区分（点击活动触发换装），优先选同名以外的最佳预设，
    # 避免“场合对了但名字相同”导致界面看起来没换装
    if prefer_distinct_from:
        for p in ranked:
            if p.name != prefer_distinct_from:
                return _finalize(p, char.game_day)
    return _finalize(ranked[0], char.game_day)


def _apply_weather_filters(candidates, weather, occasion=None):
    """天气联动：根据天气过滤候选池或加权"""
    if not weather:
        return candidates

    # 豁免场合：运动装/居家装不做天气保暖度与色调过滤
    if occasion in EXEMPT_OCCASIONS:
        return candidates

    weather_key = weather.strip()
    rule = _WEATHER_FILTERS.get(weather_key)
    if not rule:
        return candidates

    # 有筛选规则时做过滤
    if "warmth" in rule:
        lo, hi = rule["warmth"]
        filtered = [c for c in candidates if lo <= (c.warmth_level or 1) <= hi]
        if filtered:
            return filtered
    if "color_tone" in rule:
        filtered = [c for c in candidates if _derive_preset_color_tone(c) == rule["color_tone"]]
        if filtered:
            return filtered

    return candidates


# ============================================================
# Tick 集成接口
# ============================================================

def daily_outfit_cycle(char, game_hour):
    """
    在跨天时调用，判断是否需要换装并执行。

    换装条件：
    - outfit_changed_at != game_day（今天还没换过）

    执行：
    - 调用 assemble_daily_outfit 生成穿搭
    - 写入 Character.current_outfit 和 outfit_changed_at
    - 更新 outfit_history（最多保留30条）
    - 创建 EventLog
    """
    if char.outfit_changed_at == char.game_day:
        return None  # 今天已经换过

    try:
        outfit = assemble_daily_outfit(char)

        char.current_outfit = json.dumps(outfit, ensure_ascii=False)
        char.outfit_changed_at = char.game_day

        # 维护 outfit_history
        history = char.outfit_history or []
        history.append({"day": char.game_day, "preset_id": outfit.get("preset_id")})
        if len(history) > 30:
            history = history[-30:]
        char.outfit_history = history

        # 事件日志
        evt = EventLog(
            character_name=char.name,
            game_day=char.game_day,
            game_time=f"{char.game_hour:02d}:00",
            event_type="outfit_change",
            title=f"换装：{outfit.get('name', '未知')}",
            description=f"换装：{outfit.get('name', '未知')} — {outfit.get('description', '')}",
            effects=json.dumps({'outfit': outfit}, ensure_ascii=False),
        )
        db.session.add(evt)

        log.info(f"[Outfit] {char.name} day={char.game_day} → {outfit.get('name')}")
        return outfit

    except Exception as e:
        log.warning(f"[Outfit] daily_outfit_cycle failed for {char.name}: {e}")
        return None


# ============================================================
# 调试工具
# ============================================================

def debug_outfit(char_id, **overrides):
    """
    调试工具：指定参数覆盖，输出决策过程。

    用法：
        result = debug_outfit(char_id=1, mood=100, occasion="formal")
        print(result)
    """
    char = Character.query.get(char_id)
    if not char:
        return {"error": f"Character {char_id} not found"}

    # 保存原始值
    orig = {k: getattr(char, k, None) for k in overrides}

    # 临时覆盖
    for k, v in overrides.items():
        setattr(char, k, v)

    outfit = assemble_daily_outfit(char)

    # 恢复
    for k, v in orig.items():
        setattr(char, k, v)

    return outfit


# ============================================================
# 对话上下文格式化
# ============================================================

def format_outfit_for_dialogue(outfit_dict, max_desc_len=120):
    """
    将穿搭 dict 转为对话上下文中可用的简短摘要。

    用于注入 LLM 提示词，避免 token 膨胀。
    """
    if not outfit_dict or not outfit_dict.get("components"):
        return "今日穿着日常便装"

    comps = outfit_dict["components"]
    parts = []
    type_labels = {
        "top": "上衣", "bottom": "下装", "outer": "外套", "shoes": "鞋",
        "accessory": "配饰", "hairstyle": "发型",
        "underwear": "内衣", "sleepwear": "睡衣",
    }
    for tk, label in type_labels.items():
        v = comps.get(tk)
        if isinstance(v, dict):
            parts.append(f"{label}:{v.get('name','')}")
        elif isinstance(v, list):
            names = [item.get('name', '') for item in v if isinstance(item, dict)]
            parts.append(f"{label}:{'、'.join(names)}")

    desc = outfit_dict.get("description", "")
    if desc:
        desc = desc[:max_desc_len]

    return f"【今日穿搭】{outfit_dict.get('name', '')}：{'，'.join(parts)}。{desc}"


def get_current_outfit_for_dialogue(char):
    """从 Character 对象获取当前穿搭的对话摘要"""
    try:
        outfit = char.get_current_outfit() if hasattr(char, 'get_current_outfit') else json.loads(char.current_outfit or '{}')
    except Exception:
        return "今日穿着日常便装"
    return format_outfit_for_dialogue(outfit)
