# -*- coding: utf-8 -*-
"""
关系对象状态衰减系统 — 每种关系类型有独立的衰减速率与活跃维度。

用法:
    from backend.game.relation_state import update_relation_states
    update_relation_states(char)
"""
import random
import logging
from backend.models import db, Friend

logger = logging.getLogger(__name__)

# 基准基线值
_BASELINE = {
    'loneliness': 50.0,
    'happiness': 50.0,
    'stress': 30.0,
    'mood': 60.0,
    'energy': 70.0,
    'clarity': 60.0,
    'fulfillment': 50.0,
}

RELATION_TYPE_CONFIG = {
    'friend': {
        'active_dims': ['closeness', 'trust', 'affection'],
        'negative_dims': [],
        'decay_rate': 0.5,
        'state_active': ['loneliness', 'happiness', 'mood'],
        'event_triggers': ['friend_needs_company', 'friend_shares_joy', 'friend_friction'],
    },
    'mentor': {
        'active_dims': ['trust', 'affection', 'closeness'],
        'negative_dims': [],
        'decay_rate': 0.3,
        'state_active': ['stress', 'clarity'],
        'event_triggers': ['mentor_advice', 'mentor_praise'],
    },
    'family': {
        'active_dims': ['closeness', 'affection', 'trust'],
        'negative_dims': ['hostility'],
        'decay_rate': 0.1,
        'state_active': ['happiness', 'loneliness', 'mood'],
        'event_triggers': ['family_call', 'family_visit', 'family_conflict'],
    },
    'rival': {
        'active_dims': [],
        'negative_dims': ['rivalry', 'hostility'],
        'decay_rate': 1.0,
        'state_active': ['stress', 'mood', 'energy'],
        'event_triggers': ['rival_encounter', 'rival_defeat', 'rival_setback'],
    },
    'enemy': {
        'active_dims': [],
        'negative_dims': ['hostility', 'fear'],
        'decay_rate': 0.2,
        'state_active': ['stress', 'mood'],
        'event_triggers': ['enemy_confront', 'enemy_intrigue'],
    },
    'colleague': {
        'active_dims': ['closeness', 'trust'],
        'negative_dims': ['rivalry'],
        'decay_rate': 0.8,
        'state_active': ['stress', 'energy'],
        'event_triggers': ['colleague_collab', 'colleague_conflict'],
    },
    'ex_boyfriend': {
        'active_dims': ['closeness', 'affection'],
        'negative_dims': ['hostility'],
        'decay_rate': 0.3,
        'state_active': ['happiness', 'loneliness', 'mood', 'stress'],
        'event_triggers': ['ex_boyfriend_encounter', 'ex_boyfriend_memory'],
    },
    'client': {
        'active_dims': ['trust', 'closeness'],
        'negative_dims': [],
        'decay_rate': 0.6,
        'state_active': ['stress'],
        'event_triggers': ['client_meeting', 'client_dispute'],
    },
    'acquaintance': {
        'active_dims': ['closeness'],
        'negative_dims': [],
        'decay_rate': 1.5,
        'state_active': [],
        'event_triggers': [],
    },
    'protege': {
        'active_dims': ['closeness', 'trust', 'affection'],
        'negative_dims': [],
        'decay_rate': 0.4,
        'state_active': ['clarity', 'fulfillment'],
        'event_triggers': ['protege_guidance', 'protege_success'],
    },
}


def _clamp(value: float, min_v: float = 0.0, max_v: float = 100.0) -> float:
    return max(min_v, min(max_v, value))


def get_last_interact_day(relation, char) -> int:
    """从关系对象获取最后一次互动的 game_day。"""
    if not relation.last_interaction:
        return 0
    try:
        from backend.config import beijing_now
        from datetime import timedelta
        delta = beijing_now() - relation.last_interaction
        return char.game_day - max(0, delta.days)
    except Exception:
        return char.game_day - 3


def update_relation_states(char):
    """每游戏日更新该角色所有关系对象的状态。"""
    relations = Friend.query.filter_by(character_name=char.name).all()
    for r in relations:
        config = RELATION_TYPE_CONFIG.get(r.relation_type, RELATION_TYPE_CONFIG['friend'])

        # 只对活跃状态字段做基线回归
        for state_field in config['state_active']:
            current = getattr(r, state_field, None)
            if current is None:
                continue
            baseline = _BASELINE.get(state_field, 50.0)
            gap = baseline - current
            step = round(gap * random.uniform(0.02, 0.08), 1)  # 每天回归 2~8%
            setattr(r, state_field, _clamp(current + step))

        # 正面维度衰减（按类型速率）
        if r.relation_type in ('friend', 'mentor', 'family', 'colleague', 'ex_boyfriend', 'client', 'protege'):
            decay = random.uniform(0, config['decay_rate'])
            r.closeness = _clamp(r.closeness - decay)

        # 负面维度衰减
        if r.relation_type in ('rival', 'enemy'):
            decay_r = random.uniform(0, config['decay_rate'] * 0.5)
            decay_h = random.uniform(0, config['decay_rate'] * 0.3)
            r.rivalry = _clamp(r.rivalry - decay_r)
            r.hostility = _clamp(r.hostility - decay_h)

        # 长时间不联系 → 疏远
        days_since = char.game_day - get_last_interact_day(r, char)
        if days_since > 3:
            r.closeness = _clamp(r.closeness - 0.5 * config['decay_rate'])
            r.loneliness = _clamp(r.loneliness + 2, max_v=100)

    db.session.commit()
    logger.info(f"[RelationState] 已更新 {len(relations)} 个关系对象的状态")
