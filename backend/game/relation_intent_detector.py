# -*- coding: utf-8 -*-
"""
关系对象主动事件检测 — 扫描所有关系对象状态，判断是否需要触发互动事件。

用法:
    from backend.game.relation_intent_detector import detect_relation_intents
    candidates = detect_relation_intents(char)
    # candidates: [(relation_obj, trigger_id), ...]
"""
import random
import logging
from backend.models import Friend
from backend.game.relation_state import RELATION_TYPE_CONFIG

logger = logging.getLogger(__name__)


def detect_relation_intents(char) -> list:
    """扫描所有关系，返回需要触发事件的 (relation, trigger_id) 列表。"""
    relations = Friend.query.filter_by(character_name=char.name).all()
    candidates = []

    for r in relations:
        if r.relation_type == 'friend':
            if r.loneliness > 55:                       # 原 70
                candidates.append((r, 'friend_needs_company'))
            if r.happiness > 65:                         # 原 80
                candidates.append((r, 'friend_shares_joy'))
        elif r.relation_type == 'rival':
            if r.rivalry > 40 and random.random() < 0.4: # 原 60
                candidates.append((r, 'rival_encounter'))
            if r.rivalry > 60:                           # 原 80
                candidates.append((r, 'rival_setback'))
        elif r.relation_type == 'mentor':
            if r.stress > 45 and r.clarity < 55:         # 原 stress>60, clarity<50
                candidates.append((r, 'mentor_advice'))
        elif r.relation_type == 'family':
            if r.loneliness > 55:                        # 原 60
                candidates.append((r, 'family_call'))
        elif r.relation_type == 'colleague':
            if r.stress > 50:                            # 原 70
                candidates.append((r, 'colleague_conflict'))
        elif r.relation_type == 'ex_boyfriend':
            if r.loneliness > 50 and random.random() < 0.3:
                candidates.append((r, 'ex_boyfriend_memory'))
            if r.hostility > 30:
                candidates.append((r, 'ex_boyfriend_encounter'))
        elif r.relation_type == 'enemy':
            if r.hostility > 40:                         # 原 60
                candidates.append((r, 'enemy_intrigue'))
        elif r.relation_type == 'client':
            if r.stress > 50:                            # 原 65
                candidates.append((r, 'client_meeting'))
        elif r.relation_type == 'protege':
            if r.clarity > 55:                           # 原 70
                candidates.append((r, 'protege_success'))

    if candidates:
        logger.info(f"[RelationIntent] 检测到 {len(candidates)} 个关系事件待触发")
    return candidates
