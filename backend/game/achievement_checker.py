# -*- coding: utf-8 -*-
"""
事件→成就映射表 + 成就混合模式检查器。

用法:
    from backend.game.achievement_checker import check_event_achievements
    check_event_achievements(char, trigger_id)  # 在 ConditionMatcher.fire_event 后调用

    from backend.game.achievement_checker import check_condition_achievements
    check_condition_achievements(char)  # 在 daily_end / 确认任务后轮询（按条件触发）

    from backend.game.achievement_checker import check_numeric_achievements
    check_numeric_achievements()  # 在 daily_end 轮询
"""
import json
import logging
from datetime import datetime
from backend.models import db, Achievement

logger = logging.getLogger(__name__)

# 事件→成就映射表：触发事件 ID → 关联的成就 ID 列表（通用/静态部分）
EVENT_ACHIEVEMENT_MAP = {
    # 通用事件 → 通用成就
    'friend_support':     ['best_roommate'],
    'invitation_dinner':  ['best_roommate'],
    'argument':           ['best_roommate'],

    # 学业类事件 → 学业成就
    'exam_stress':        ['library_regular'],
    'study_session':      ['library_regular'],
    'study_group':        ['library_regular'],
    'deadline_night':     ['night_shift_veteran'],

    # 社交类事件 → 社交成就
    'social_party':       ['environment_explorer'],
    'neighbor_visit':     ['environment_explorer'],
    'coincidence':        ['environment_explorer'],

    # 自我成长类
    'self_reflection':    ['quiet_confidence'],
    'life_lesson':        ['quiet_confidence'],
}


def register_mission_achievements(mission_id: int, trigger_to_ach: dict):
    """Mission 启动时调用：把 trigger→achievement 映射持久化到 event_achievement_binding 表。

    改为 DB 驱动后，服务重启无需重建内存映射，成就进度不会因重启而卡死。
    """
    from backend.models import EventAchievementBinding
    count = 0
    for trigger_id, ach_ids in (trigger_to_ach or {}).items():
        if not trigger_id:
            continue
        for aid in (ach_ids or []):
            aid = aid if isinstance(aid, str) else (aid.get('achievement_id') if isinstance(aid, dict) else '')
            if not aid:
                continue
            exists = EventAchievementBinding.query.filter_by(
                trigger_id=trigger_id, achievement_id=aid, mission_id=mission_id).first()
            if exists:
                continue
            db.session.add(EventAchievementBinding(
                character_name='',
                trigger_id=trigger_id,
                achievement_id=aid,
                mission_id=mission_id,
                is_mission=True,
            ))
            count += 1
    if count:
        db.session.commit()
        logger.info(f"[Achievement] 持久化 mission {mission_id} 的 {count} 条成就绑定")


def unregister_mission_achievements(trigger_ids: list = None, mission_id: int = None):
    """Mission 归档时调用：清理持久化绑定（按 trigger_ids 或 mission_id）。"""
    from backend.models import EventAchievementBinding
    if mission_id is not None:
        q = EventAchievementBinding.query.filter_by(is_mission=True, mission_id=mission_id)
    elif trigger_ids:
        q = EventAchievementBinding.query.filter_by(is_mission=True).filter(
            EventAchievementBinding.trigger_id.in_(trigger_ids))
    else:
        return
    deleted = q.delete()
    if deleted:
        db.session.commit()
        logger.info(f"[Achievement] 清理 {deleted} 条任务成就绑定")


def _related_achievement_ids(trigger_id: str):
    """汇总某 trigger_id 关联的成就 ID：静态 MAP + 持久化绑定表（base + mission）。"""
    related = list(EVENT_ACHIEVEMENT_MAP.get(trigger_id, []))
    try:
        from backend.models import EventAchievementBinding
        bindings = EventAchievementBinding.query.filter_by(trigger_id=trigger_id).all()
        for b in bindings:
            if b.achievement_id and b.achievement_id not in related:
                related.append(b.achievement_id)
    except Exception:
        pass
    return related


def check_event_achievements(char, trigger_id: str):
    """规则事件命中后立即检查关联成就（统一入口：静态 MAP + 持久化绑定表）。

    成就若配置了 trigger_conditions，则仅在条件满足时才累计进度；
    条件为空（默认 []）时保持旧行为：任意关联事件均推进。
    """
    related_ids = _related_achievement_ids(trigger_id)
    if not related_ids:
        return []

    # 条件求值器（复用 ConditionMatcher 的 evaluate/check）
    matcher = None
    try:
        from backend.game.condition_matcher import ConditionMatcher
        matcher = ConditionMatcher(char.name)
    except Exception:
        matcher = None

    unlocked = []
    for aid in related_ids:
        ach = Achievement.query.filter_by(
            character_name=char.name,
            achievement_id=aid,
            unlocked=False
        ).first()
        if not ach:
            continue

        # 成就触发条件：未满足则本次不累计进度（空条件保持旧行为）
        if matcher is not None and ach.trigger_conditions:
            try:
                conds = ach.trigger_conditions
                if isinstance(conds, str):
                    conds = json.loads(conds)
                conds = conds or []
            except Exception:
                conds = []
            if conds and not matcher.check_conditions(conds, char):
                continue

        # 事件型成就每次推进（使用 step_size，默认 10%）
        increment = getattr(ach, 'step_size', 10) or 10
        ach.progress = min(100, ach.progress + increment)
        if ach.progress >= ach.target:
            ach.unlocked = True
            ach.unlocked_at = datetime.utcnow()
            unlocked.append(ach)
            logger.info(f"[成就] {char.name} 解锁成就: {ach.name}")

    if unlocked:
        db.session.commit()
    return unlocked


def check_condition_achievements(char) -> list:
    """条件型成就轮询：遍历所有未解锁且有 trigger_conditions 的成就，
    条件满足即解锁（进度置满）。由 daily_end 与确认任务后调用，
    使成就能按角色状态（如技能>=阈值）触发，而非依赖特定事件。"""
    try:
        from backend.game.condition_matcher import ConditionMatcher
        matcher = ConditionMatcher(char.name)
    except Exception:
        return []

    unlocked = []
    achievements = Achievement.query.filter_by(
        character_name=char.name, unlocked=False
    ).all()
    for ach in achievements:
        if not ach.trigger_conditions:
            continue
        try:
            conds = ach.trigger_conditions
            if isinstance(conds, str):
                conds = json.loads(conds)
            conds = conds or []
        except Exception:
            conds = []
        if not conds:
            continue
        if matcher.check_conditions(conds, char):
            ach.progress = ach.target
            ach.unlocked = True
            ach.unlocked_at = datetime.utcnow()
            unlocked.append(ach)
            logger.info(f"[成就] 条件达成解锁: {ach.name}")

    if unlocked:
        db.session.commit()
    return unlocked


def check_numeric_achievements() -> list:
    """数值型成就轮询：检查技能型、访问次数型等（保持现有 check_achievements 行为）。"""
    from backend.game.character import get_character
    from backend.game.event import check_achievements as _old_check

    # 复用已有的 check_achievements 逻辑
    result = _old_check()
    return result


def evaluate_goals(char) -> dict:
    """每日轮询：按 goal_rules 推进 char.goals（LLM 初始化目标的解锁规则）。

    - condition 型：trigger_conditions 满足 → 置满 target
    - numeric 型：progress_source.attr 当前值 → 作为 progress（target=100 时直接等于属性值）
    返回更新后的 goals 字典。
    """
    rules = char.goal_rules
    if not isinstance(rules, dict) or not rules:
        return char.goals if isinstance(char.goals, dict) else {}
    try:
        from backend.game.condition_matcher import ConditionMatcher, resolve_attr
        matcher = ConditionMatcher(char.name)
    except Exception:
        return char.goals if isinstance(char.goals, dict) else {}

    goals = dict(char.goals) if isinstance(char.goals, dict) else {}
    changed = False
    for key, rule in rules.items():
        if not isinstance(rule, dict):
            continue
        target = rule.get('target', 100) or 100
        if rule.get('type') == 'numeric':
            ps = rule.get('progress_source')
            if isinstance(ps, dict) and ps.get('attr'):
                val = resolve_attr(char, ps['attr'])
                if val is not None:
                    new_val = int(round(val))
                    if goals.get(key) != new_val:
                        goals[key] = new_val
                        changed = True
        else:  # condition 型（默认）
            conds = rule.get('conditions')
            if isinstance(conds, list) and conds:
                if matcher.check_conditions(conds, char):
                    if goals.get(key) != target:
                        goals[key] = target
                        changed = True
    if changed:
        # 关键：goals 已是独立拷贝（非 char.goals 跟踪对象），直接赋值即可触发 UPDATE
        char.goals = goals
        db.session.commit()
        logger.info(f"[Goal] {char.name} 目标进度更新: {goals}")
    return goals
