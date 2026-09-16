# -*- coding: utf-8 -*-
"""
事件冷却管理器 — 防止同一事件在短时间内重复触发。

用法:
    cooldown = CooldownManager()
    if cooldown.is_on_cooldown(char_name, trigger_id):
        return  # 冷却中，跳过
    cooldown.set_cooldown(char_name, trigger_id, game_hours=48)
"""
import logging
from backend.models import db, EventCooldown

logger = logging.getLogger(__name__)

# 默认冷却时间（游戏小时），按 trigger_id 可单独覆盖
DEFAULT_COOLDOWN_HOURS = {
    'exam_stress': 48,
    'friend_needs_company': 24,
    'friend_shares_joy': 48,
    'friend_friction': 72,
    'family_call': 72,
    'rival_encounter': 48,
    'rival_setback': 72,
    'enemy_intrigue': 96,
    'enemy_confront': 96,
    'colleague_conflict': 72,
    'ex_boyfriend_encounter': 72,
    'ex_boyfriend_memory': 96,
    'client_meeting': 48,
    'protege_success': 72,
}


class CooldownManager:
    """事件冷却管理，存储在 DB。"""

    def is_on_cooldown(self, character_name: str, trigger_id: str) -> bool:
        """检查 trigger_id 是否仍在冷却中（按游戏天数判断）。

        冷却中 = 当前游戏天数 < 过期日；过期或记录不存在则视为可触发。
        """
        record = EventCooldown.query.filter_by(
            character_name=character_name,
            trigger_id=trigger_id,
        ).first()
        if not record:
            return False
        from backend.game.character import get_character
        char = get_character()
        if not char:
            # 无法获取角色时保守视为冷却中，避免重复触发
            return True
        return char.game_day < record.game_day_expires

    def is_expired(self, character_name: str, trigger_id: str,
                   current_game_day: int) -> bool:
        """检查冷却是否已过期（按 game_day 比较）。"""
        record = EventCooldown.query.filter_by(
            character_name=character_name,
            trigger_id=trigger_id,
        ).first()
        if not record:
            return True  # 无纪录 = 已过期
        return current_game_day >= record.game_day_expires

    def set_cooldown(self, character_name: str, trigger_id: str,
                     game_hours: int = None):
        """设置冷却，game_hours 小时后过期。
        
        不指定 game_hours 时使用 DEFAULT_COOLDOWN_HOURS 中的值。
        """
        if game_hours is None:
            game_hours = DEFAULT_COOLDOWN_HOURS.get(trigger_id, 24)
        game_days = max(1, game_hours // 24)

        # 获取当前角色的 game_day
        from backend.game.character import get_character
        char = get_character()
        if not char:
            logger.warning("[Cooldown] 无法获取角色，冷却设置失败")
            return

        expires = char.game_day + game_days

        record = EventCooldown.query.filter_by(
            character_name=character_name,
            trigger_id=trigger_id,
        ).first()
        if record:
            record.game_day_expires = expires
        else:
            record = EventCooldown(
                character_name=character_name,
                trigger_id=trigger_id,
                game_day_expires=expires,
            )
            db.session.add(record)
        db.session.commit()
        logger.info(
            f"[Cooldown] {character_name} 的 {trigger_id} "
            f"冷却至第{expires}天 ({game_days}天)"
        )

    def cleanup(self, character_name: str, current_game_day: int):
        """清理已过期的冷却记录。"""
        deleted = EventCooldown.query.filter(
            EventCooldown.character_name == character_name,
            EventCooldown.game_day_expires <= current_game_day,
        ).delete()
        if deleted > 0:
            db.session.commit()
            logger.info(f"[Cooldown] 已清理 {deleted} 条过期冷却记录")
