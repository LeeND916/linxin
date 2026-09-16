# -*- coding: utf-8 -*-
"""
主动消息理由检测器 — 判断女主今天是否有理由主动发消息给玩家。

用法:
    from backend.game.message_reason_detector import MessageReasonDetector
    reasons = MessageReasonDetector.detect(char)
    if reasons: ...  # 有理由发消息
"""
import logging
from backend.models import db, EventLog, Friend

logger = logging.getLogger(__name__)


class MessageReasonDetector:
    """检测女主主动发消息的理由，按权重排序返回。"""

    @staticmethod
    def detect(char) -> list:
        """扫描角色状态，返回发消息的理由列表（已按 weight 降序）。"""
        reasons = []

        # 1. 情绪触发（女主自己的心情波动）
        if char.loneliness > 65:
            reasons.append({"type": "lonely", "weight": 0.7})
        if char.stress > 55:
            reasons.append({"type": "stressed", "weight": 0.6})
        if char.happiness > 80:
            reasons.append({"type": "happy", "weight": 0.5})
        if char.anger > 60:
            reasons.append({"type": "angry", "weight": 0.6})
        if char.disappointment > 65:
            reasons.append({"type": "disappointed", "weight": 0.5})

        # 2. 今天有重要规则事件触发（relation_initiated / narrative_scheduled / story_arc）
        try:
            today_important = EventLog.query.filter(
                EventLog.game_day == char.game_day,
                EventLog.event_category.in_(
                    ['relation_initiated', 'narrative_scheduled', 'story_arc']
                ),
                EventLog.character_name == char.name
            ).all()
            for e in today_important:
                reasons.append({
                    "type": "event_followup",
                    "event_id": e.id,
                    "title": e.title,
                    "weight": 0.9,
                })
        except Exception:
            pass

        # 3. 玩家关系触发
        try:
            from backend.game.dialogue import get_relationship_tier
            tier = get_relationship_tier(char)
            if tier >= 3 and hasattr(char, 'player_intimacy') and char.player_intimacy > 75:
                reasons.append({"type": "close_feeling", "weight": 0.4})
        except Exception:
            pass

        # 4. CharacterRelation 驱动触发
        try:
            relations = Friend.query.filter_by(character_name=char.name).all()
            for r in relations:
                if r.relation_type == 'ex_boyfriend':
                    if getattr(r, 'loneliness', 0) > 60:
                        reasons.append({
                            "type": "ex_memory", "target": r.name, "weight": 0.7
                        })
                    if getattr(r, 'hostility', 0) > 40:
                        reasons.append({
                            "type": "ex_upset", "target": r.name, "weight": 0.6
                        })
                elif r.relation_type == 'rival' and getattr(r, 'rivalry', 0) > 65:
                    reasons.append({
                        "type": "rival_stress", "target": r.name, "weight": 0.5
                    })
                elif r.relation_type == 'enemy' and getattr(r, 'hostility', 0) > 60:
                    reasons.append({
                        "type": "enemy_angst", "target": r.name, "weight": 0.6
                    })
                elif r.relation_type == 'family' and getattr(r, 'loneliness', 0) > 60:
                    reasons.append({
                        "type": "homesick", "weight": 0.5
                    })
        except Exception:
            pass

        # 5. 记忆情感强度检测
        try:
            from backend.models import CharacterMemory
            strong_memories = CharacterMemory.query.filter(
                CharacterMemory.character_name == char.name,
                CharacterMemory.is_faded == False,
                CharacterMemory.emotional_weight >= 30.0,  # 情感权重 ≥30
            ).order_by(CharacterMemory.emotional_weight.desc()).limit(3).all()
            for m in strong_memories:
                reasons.append({
                    "type": "memory_recall",
                    "memory_id": m.id,
                    "content": m.content[:40],
                    "weight": 0.3 + m.emotional_weight / 200.0,  # 权重 0.3~0.8
                })
        except Exception:
            pass

        # 按 weight 降序排序
        reasons.sort(key=lambda x: -x['weight'])
        return reasons
