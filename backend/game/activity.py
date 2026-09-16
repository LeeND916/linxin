"""活动系统 — 位置移动与活动效果"""
import logging
from backend.models import db, Character
from backend.game.character import apply_attr_changes, get_character
from backend.game.constraints import (
    get_activity_penalty, check_activity_constraints
)

logger = logging.getLogger(__name__)

# 夜间禁止户外活动的时段
NIGHT_START = 23  # 23:00
NIGHT_END = 6     # 06:00（不包含）

# 地点定义 — 作为 CharacterActivityMap 表所有 venue_id 的元数据（name/icon/坐标）来源
# API 只读 CharacterActivityMap 表构建地图，再用本字典装饰显示名称
# LOCATIONS（内置通用地点）已于 2026-08-07 删除：活动地图改为只展示各角色专属场馆
# （character_activity_map.custom_activities），不再有全局共享的通用地点。相关装饰名改由
# CAM 行自身的 venue_name 提供，角色专属名称差异由 CHARACTER_VENUES 覆盖。

# 活动定义及其效果
ACTIVITIES = {
    'study_library': {
        'name': '在图书馆学习',
        'effects': {'learning': 3, 'writing': 2, 'energy': -8, 'stress': 2},
        'locations': ['library'],
        'description': '沉浸于知识的海洋，学习效率倍增。'
    },
    'write_novel': {
        'name': '写作创作',
        'effects': {'writing': 5, 'creativity': 3, 'writer_progress': 2, 'energy': -6, 'happiness': 3},  # @deprecated: writer_progress 旧字段，新角色用 goals JSON
        'locations': ['library', 'cafe', 'dormitory', 'home'],
        'description': '灵感涌现，笔下生花。'
    },
    'code_practice': {
        'name': '编程练习',
        'effects': {'coding': 5, 'coder_progress': 2, 'energy': -7, 'stress': 3, 'motivation': 2},  # @deprecated: coder_progress 旧字段
        'locations': ['lab', 'library', 'dormitory'],
        'description': '一行行代码，构筑未来的基石。'
    },
    'workout': {
        'name': '健身锻炼',
        'effects': {'fitness': 4, 'health': 2, 'energy': -10, 'stress': -5, 'mood': 3},
        'locations': ['gym', 'park'],
        'description': '挥洒汗水，身心舒畅。',
        'is_outdoor': True,
    },
    'eat': {
        'name': '用餐',
        'effects': {'hunger': -20, 'energy': 5, 'mood': 3},
        'locations': ['canteen', 'cafe', 'mall'],
        'description': '美食是最好的充电方式。'
    },
    'socialize': {
        'name': '朋友聚会',
        'effects': {'social': 3, 'happiness': 5, 'loneliness': -8, 'energy': -3},
        'locations': ['mall', 'cafe', 'park', 'theater'],
        'description': '和朋友在一起的时光总是美好的。'
    },
    'shopping': {
        'name': '逛街购物',
        'effects': {'happiness': 4, 'mood': 3, 'energy': -4, 'stress': -3},
        'locations': ['mall'],
        'description': '换一套穿搭，换个好心情。',
        'is_outdoor': True,
    },
    'travel_explore': {
        'name': '旅行探索',
        'effects': {'happiness': 8, 'creativity': 4, 'stress': -6, 'energy': -8, 'loneliness': -5},
        'locations': ['travel'],
        'description': '世界那么大，我想去看看。',
        'is_outdoor': True,
    },
    'attend_class': {
        'name': '上课',
        'effects': {'learning': 4, 'social': 1, 'energy': -5, 'mood': 1},
        'locations': ['classroom'],
        'description': '知识的传递，思维的碰撞。'
    },
    'relax_home': {
        'name': '在家休息',
        'effects': {'energy': 12, 'stress': -4, 'health': 2, 'mood': 1},
        'locations': ['home', 'dormitory'],
        'description': '好好休息，才能走更远的路。'
    },
    'shower': {
        'name': '洗澡',
        'effects': {'hygiene': 30, 'energy': 5, 'stress': -3, 'mood': 3},
        'locations': ['dormitory', 'home'],
        'description': '洗去一身疲惫，清爽舒适。'
    }
}


def get_all_locations():
    """返回全局已知地点（来自所有角色的 CharacterActivityMap），替代已删除的 LOCATIONS。

    前端 GET /api/locations 用于地点选择器等场景；现在不再有代码层全局地点，
    改为汇总各角色专属场馆的 venue_id + venue_name。
    """
    try:
        from backend.models import CharacterActivityMap
        rows = CharacterActivityMap.query.with_entities(
            CharacterActivityMap.venue_id, CharacterActivityMap.venue_name
        ).distinct().all()
        return {vid: {'name': vname or vid} for vid, vname in rows if vid}
    except Exception:
        return {}


def _increment_venue_visit_count(char, activity: dict) -> None:
    """为活动关联的场所增加访问计数（仅当该场所已在 CharacterActivityMap 中存在行）。

    注意：LOCATIONS 已删除，不再为任何"全局通用地点"自动新建 CAM 行——只有角色专属场馆
    （本就存在于 CAM）的访问计数会被累加；活动若落到已删除的通用地点，则静默跳过。
    """
    try:
        from backend.models import CharacterActivityMap
        char_name = char.name or ''
        if not char_name:
            return
        # 取活动的第一个关联地点作为主场馆
        locations = activity.get('locations', [])
        if not locations:
            return
        venue_id = locations[0]
        # 仅对该场所已在 CAM 中存在的行累加计数
        existing = CharacterActivityMap.query.filter_by(
            character_name=char_name, venue_id=venue_id
        ).first()
        if existing:
            existing.visit_count = (existing.visit_count or 0) + 1
            db.session.commit()
    except Exception as e:
        logger.warning(f"[ActivityMap] 访问计数更新失败: {e}")
        db.session.rollback()


def get_activities_for_location(location_id, character=None):
    """获取指定地点可用活动：DB 专属（custom_activities） > 通用（ACTIVITIES）。

    若该地点在 CharacterActivityMap 中有专属活动（custom_activities），则全部返回
    （含个人活动 + 已折入的共性活动）；无专属活动时回退到通用活动，保证兼容。
    """
    # 第0层：CharacterActivityMap 专属活动（最高优先级）
    if character:
        char_name = character.name if hasattr(character, 'name') else character
        try:
            from backend.models import CharacterActivityMap
            import json as _json
            venue_map = CharacterActivityMap.query.filter_by(
                character_name=char_name, venue_id=location_id
            ).first()
            if venue_map and venue_map.custom_activities:
                custom_acts = _json.loads(venue_map.custom_activities)
                custom_activities = {}
                for act in custom_acts:  # 全部返回（迁移后含个人+共性活动）
                    aid = act.get('id') or act.get('name')  # 允许用 name 兜底 id
                    if aid and aid not in custom_activities:
                        custom_activities[aid] = {
                            'name': act.get('name', aid),
                            'effects': act.get('effects', {}),
                            'energy_cost': act.get('energy_cost', 0),
                            'duration_minutes': act.get('duration_minutes', 30),
                            'locations': [location_id],
                            'description': act.get('name', ''),
                            'source': 'custom',
                        }
                if custom_activities:
                    # 有专属活动：直接返回专属活动列表
                    return custom_activities
        except Exception:
            pass

    # 无 DB 专属活动：回退到通用活动（ACTIVITIES），保证兼容
    activities = {}
    for k, v in ACTIVITIES.items():
        if location_id in v.get('locations', []):
            if k not in activities:
                activities[k] = dict(v)

    return activities


def move_to_location(location_id):
    """移动角色到指定地点（支持角色专属场所与玩家自定义地点）。

    内置/专属/LLM 地点需在 get_character_venues 集合中；玩家自定义地点（自由文本，不在已知
    场馆集合内）也允许直接设为当前所在地——自定义地点仅 transient 存于 Character.location，
    不写入 character_activity_map，故不出现在活动地图；地点变化本身不负责换装。
    """
    char = get_character()
    venues = get_character_venues(char)
    if location_id in venues:
        location_name = venues[location_id]
    else:
        # 自定义地点：允许直接设为当前所在地（不校验场馆集合）
        if not location_id or not str(location_id).strip():
            return None
        location_name = str(location_id).strip()
    char.location = location_id
    db.session.commit()
    acts = get_activities_for_location(location_id, character=char)
    return {
        'location': location_id,
        'location_name': location_name,
        'available_activities': [{'id': aid, 'name': a.get('name', aid)} for aid, a in acts.items()],
    }


def create_custom_location(name, character=None):
    """校验玩家自定义地点名并返回 venue_id（= 中文名）。

    用于「玩家在对话输入栏自定义地点」（如「杭埠」「巴黎圣母院」）：玩家在地点选择器里
    输入新名称并确认，前端调用 POST /api/locations 落库，随后随对话把 venue_id 作为
    location_override 发送，后端 move_to_location 应用。

    ⚠️ 不写入 character_activity_map，也不新增存储列：自定义地点仅作为角色当前所在地
    transient 存于 Character.location（由后续 move_to_location 写入），不出现在活动地图
    （活动地图以 character_activity_map 为唯一权威数据源）。因此本函数只做校验并返回
    中文名作为 venue_id，不做任何持久化。
    """
    if not name or not str(name).strip():
        return None
    name = str(name).strip()
    if len(name) > 32:
        return None
    # 不做持久化：自定义地点通过 move_to_location 写入 Character.location 即可
    return name


def perform_activity(activity_id, force: bool = False):
    """执行活动并应用效果。查找优先级：LLM专属 > 通用 > 角色预设。"""
    char = get_character()
    char_name = char.name if char else None

    activity = None

    # 第1层：查找 LLM 生成的专属活动
    try:
        from backend.models import CharacterActivityMap
        import json as _json
        venue_maps = CharacterActivityMap.query.filter_by(
            character_name=char_name
        ).all()
        for vm in venue_maps:
            if vm.custom_activities:
                try:
                    acts = _json.loads(vm.custom_activities)
                    for act in acts:
                        if act.get('id') == activity_id:
                            activity = {
                                'name': act.get('name', activity_id),
                                'effects': act.get('effects', {}),
                                'locations': [vm.venue_id],
                                'energy_cost': act.get('energy_cost', 0),
                                'description': act.get('name', ''),
                                'is_outdoor': False,
                            }
                            break
                except (ValueError, TypeError):
                    pass
            if activity:
                break
    except Exception:
        pass

    # 第2层：通用活动
    if not activity:
        activity = ACTIVITIES.get(activity_id)

    # 第3层：仍未匹配（角色专属/通用均无此活动）
    if not activity:
        return None

    # 夜间户外活动检查
    if activity.get('is_outdoor'):
        game_hour = int(getattr(char, 'game_hour', 8))
        if game_hour >= NIGHT_START or game_hour < NIGHT_END:
            return {
                'activity': activity['name'],
                'description': activity['description'],
                'blocked': True,
                'warning': f'现在已经是深夜了，{char.name}不会在这个时候出门的。等天亮再说吧。（当前时间：第{char.game_day}天 {char.game_hour}:00）',
                'forced_activities': [],
                'effects': {},
                'character': char.to_dict(),
            }

    # 状态约束拦截：体力/饥饿/健康/卫生/心理等低于阈值时，禁止对应活动
    # check_activity_constraints 返回 (can_do, warning, forced_activities, blocked)
    # 已内置"生存必需活动豁免"（如饿时 eat、累时 relax_home 仍可做）
    _can_do, _cwarn, _forced, _blocked = check_activity_constraints(
        char, activity_id, activity.get('name')
    )
    if _blocked and not force:
        return {
            'activity': activity.get('name', activity_id),
            'description': activity.get('description', ''),
            'blocked': True,
            'warning': _cwarn or f'{char.name}现在的状态不适合做这个活动。',
            'forced_activities': _forced,
            'effects': {},
            'character': char.to_dict(),
        }

    # 应用效果（带效率惩罚）
    penalty = get_activity_penalty(char)
    adjusted_effects = {}
    for key, delta in activity['effects'].items():
        adjusted_effects[key] = delta * penalty

    effects = apply_attr_changes(adjusted_effects)

    # 跟踪活动场所访问次数
    _increment_venue_visit_count(char, activity)

    result = {
        'activity': activity['name'],
        'description': activity['description'],
        'effects': effects,
        'character': char.to_dict(),
    }

    # 点击活动代表角色实际开始/完成该行为：按地点 + 活动名称从真实衣柜选装。
    # 地点移动本身不换装，但活动动作需要让穿搭进入运动/工作/社交等场景。
    try:
        from backend.game.wardrobe import apply_outfit, select_outfit_for_context
        current = char.get_current_outfit() or {}
        current_name = current.get('name', '') if isinstance(current, dict) else ''
        activity_context = f"{activity.get('name', activity_id)} {char.location or ''}"
        outfit = select_outfit_for_context(char, activity_context, prefer_distinct_from=current_name)
        if outfit and outfit.get('name') != current_name:
            result['outfit'] = apply_outfit(char, outfit, reason=f'after_activity:{activity_id}')
            result['character'] = char.to_dict()
    except Exception as exc:
        logger.warning(f'[Activity] activity outfit update failed: {exc}')

    return result


# ============================================================================
# 角色专属地图配置
# ============================================================================
CHARACTER_VENUES = {
    # 晓月：无专属名称覆盖（LOCATIONS 已删除，其原通用场馆已全部移除）
    "晓月": {},
    # 苏晴：law_firm 为专属场馆（非 builtin），提供中文名
    "苏晴": {
        "law_firm": "律师事务所",
    },
    # 林小鹿：studio/gallery 为专属场馆；原 park(写生公园) 属通用地点已删除，不再覆盖
    "林小鹿": {
        "studio": "画室",
        "gallery": "画廊",
    },
    # 叶知秋：operating_room 为专属场馆；原 lab(医学实验室) 属通用地点已删除，不再覆盖
    "叶知秋": {
        "operating_room": "手术室",
    },
    # 沈念：bookstore 为专属场馆
    "沈念": {
        "bookstore": "旧书店",
    },
    # 顾云溪：game_company 为专属场馆
    "顾云溪": {
        "game_company": "游戏公司",
    },
}


# ============================================================================


def get_character_venues(character):
    """根据角色名获取可用地图（仅来自角色专属 CharacterActivityMap，不再合并任何代码层全局地点）。

    LOCATIONS 已删除，因此可移动/可玩地点集合 = 该角色在 character_activity_map 中拥有的
    专属场馆；CHARACTER_VENUES 仅在该行 venue_name 缺失时提供名称兜底。
    """
    char_name = character.name if character else None
    venues = {}
    # 纳入角色专属地点（来自 CharacterActivityMap），保证 move 校验通过、可点击进入
    if char_name:
        try:
            from backend.models import CharacterActivityMap
            for m in CharacterActivityMap.query.filter_by(character_name=char_name).all():
                vid = m.venue_id
                if vid and vid not in venues:
                    venues[vid] = m.venue_name or vid
        except Exception:
            pass
    # 角色专属名称覆盖（CAM 行 venue_name 缺失时的兜底）
    venues.update(CHARACTER_VENUES.get(char_name, {}))
    return venues

