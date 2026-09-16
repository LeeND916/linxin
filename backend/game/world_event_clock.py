# -*- coding: utf-8 -*-
"""
剧本时钟 — 从世界观 JSON 的 narrative_schedule 中读取预定事件，按 game_day 触发。

用法:
    clock = WorldEventClock(character_name)
    fired_events = clock.check_and_fire(char)
    
每个 daily_end 调用一次，检查当前游戏天数是否匹配任何阶段事件。
"""
import json
import logging
import os
from backend.models import db, EventLog

logger = logging.getLogger(__name__)

# 世界观 JSON 存放目录（用户主目录 ~/sim_life/world_settings）
from backend.game.user_data_paths import WORLD_SETTINGS_DIR


class WorldEventClock:
    """剧本时钟：按游戏天数触发 narrative_schedule 中的预定事件。"""

    def __init__(self, character_name: str):
        self.character_name = character_name
        self.schedule = self._load_schedule(character_name)

    def _load_schedule(self, character_name: str) -> dict:
        """从世界观 JSON 加载 narrative_schedule。"""
        file_path = os.path.join(
            WORLD_SETTINGS_DIR,
            f'world_setting_{character_name}.json'
        )
        if not os.path.exists(file_path):
            logger.info(
                f"[WorldEventClock] 世界观文件不存在: {file_path}，跳过"
            )
            return {'phases': []}

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('narrative_schedule', {'phases': []})
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"[WorldEventClock] 加载世界观失败: {e}")
            return {'phases': []}

    def check_and_fire(self, char) -> list:
        """检查今天是否有安排事件，有则触发并写入 EventLog。

        返回触发的 EventLog 字典列表。
        """
        today = char.game_day
        fired = []

        for phase in self.schedule.get('phases', []):
            day_range = phase.get('day_range', [None, None])
            start = day_range[0] if isinstance(day_range, (list, tuple)) else None

            # 阶段整体事件：角色进入本阶段（今天 >= 阶段起始天）时写入一条事件；
            # 仅在 phase 自带内容（start_time/end_time/location/description 任一）时触发，
            # 避免对旧格式（仅有 stage_events）角色写入空壳阶段事件。
            has_phase_detail = any(
                k in phase for k in ('start_time', 'end_time', 'location', 'description')
            )
            if isinstance(start, int) and today >= start and has_phase_detail:
                event_log = self._fire_phase_event(char, phase)
                if event_log:
                    fired.append(event_log)

            # 兼容旧格式：stage_events 仍按天精确触发（老角色沿用）
            for evt in phase.get('stage_events', []):
                day = evt.get('day')
                should_fire = False
                if isinstance(day, list):
                    should_fire = today in day
                elif isinstance(day, int):
                    should_fire = day == today

                if should_fire:
                    event_log = self._fire_event(char, evt)
                    if event_log:
                        fired.append(event_log)

        return fired

    def _fire_event(self, char, evt: dict) -> dict | None:
        """执行单个预定事件：写入 EventLog。"""
        trigger_id = evt.get('trigger_id') or evt.get('title') or 'unknown'
        try:
            event = EventLog(
                event_type='narrative_scheduled',
                event_category='narrative_scheduled',
                title=evt.get('title') or evt.get('trigger_id') or '未命名事件',
                description=evt.get('description', ''),
                effects='{}',
                game_day=char.game_day,
                game_time=evt.get('start_time') or f"{char.game_hour:02d}:{char.game_minute:02d}:00",
                character_name=char.name,
                location=evt.get('location', getattr(char, 'location', '')),
            )
            db.session.add(event)
            db.session.commit()
            logger.info(
                f"[WorldEventClock] 触发预定事件: {trigger_id} "
                f"(第{char.game_day}天)"
            )
            return event.to_dict()
        except Exception as e:
            logger.error(
                f"[WorldEventClock] 触发事件 {trigger_id} 失败: {e}"
            )
            return None

    def _fire_phase_event(self, char, phase: dict) -> dict | None:
        """执行阶段整体事件：写入一条 EventLog（与 stage_event 同为 narrative_scheduled，
        以便被【今日事件】注入与主动发消息逻辑引用）。"""
        phase_name = phase.get('phase') or '未命名阶段'
        # 去重：同一角色、同一阶段名已记录则跳过（避免跨天重复写入）
        existing = EventLog.query.filter_by(
            character_name=char.name,
            event_type='narrative_scheduled',
            title=phase_name,
        ).first()
        if existing:
            return existing.to_dict()

        try:
            start_time = phase.get('start_time') or f"{char.game_hour:02d}:{char.game_minute:02d}:00"
            event = EventLog(
                event_type='narrative_scheduled',
                event_category='narrative_scheduled',
                title=phase_name,
                description=phase.get('description', ''),
                effects='{}',
                game_day=char.game_day,
                game_time=start_time,
                character_name=char.name,
                location=phase.get('location', getattr(char, 'location', '')),
            )
            db.session.add(event)
            db.session.commit()
            logger.info(
                f"[WorldEventClock] 触发阶段事件: {phase_name} (第{char.game_day}天)"
            )
            return event.to_dict()
        except Exception as e:
            logger.error(
                f"[WorldEventClock] 触发阶段事件 {phase_name} 失败: {e}"
            )
            return None
