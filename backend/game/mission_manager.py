# -*- coding: utf-8 -*-
"""
任务引擎 — 五幕电影结构，从世界观 phase 驱动生成叙事任务 → 按天触发事件 → 总结归档。

任务包包含：五幕 stages（每幕含嵌套 events）+ NPC 关系网。
生命周期：pending → running → completed → archived

事件驱动：stages[].events[].day_offset 命中 → _fire_mission_event()
抉择事件：is_choice=true 的事件触发后标记已触发，玩家通过 POST /api/mission/choice 选择后应用 state_changes。

用法:
    from backend.game.mission_manager import MissionManager
    mgr = MissionManager(char.name)
    mgr.advance(char)  # 每天调用一次
"""
import json
import logging
import random
from datetime import datetime
from backend.models import db, EventLog, Friend, Achievement, Character, Mission

logger = logging.getLogger(__name__)

# 角色可直接改写的物理/心理属性列（0-100 量表）。
# 注意：writing_skill/coding_skill/social_skill/learning_skill/fitness 为 @deprecated 技能列，
# 已迁移到 skills JSON；技能提升请用技能键名，apply_attr_changes 会自动写入 skills JSON。
_CHAR_ATTR_FIELDS = (
    'health', 'energy', 'hunger', 'hygiene',
    'brain_health', 'heart_health', 'lung_health', 'liver_health', 'skin_health', 'eye_health',
    'mood', 'stress', 'happiness', 'loneliness',
    'confidence', 'motivation', 'creativity',
    'joy', 'anger', 'disappointment', 'boredom', 'fulfillment',
)
# 女主与玩家（导师）的关系属性列（0-100 量表）
_PLAYER_FIELDS = ('player_trust', 'player_affection', 'player_respect', 'player_intimacy')
# Friend 表中「女主与其他人」的关系维度（0-100 量表）
_FRIEND_REL_FIELDS = ('closeness', 'trust', 'affection', 'rivalry', 'hostility', 'fear')


def normalize_gender(value) -> str:
    """性别归一化：任意中英文输入 → 'male' / 'female'（friend 表统一存英文，前端展示时映射中文）"""
    g = str(value or '').strip().lower()
    if g in ('male', 'm', '男', '男性', 'boy', 'man'):
        return 'male'
    return 'female'


class MissionManager:
    """任务管理器：五幕电影结构，phase 驱动生成，事件驱动推进。"""

    def __init__(self, character_name: str):
        self.character_name = character_name

    # ═══════════════════════════════════════════════════════════════
    # 查询方法
    # ═══════════════════════════════════════════════════════════════

    def get_active(self) -> Mission | None:
        """获取当前 running 状态的任务"""
        return Mission.query.filter_by(
            character_name=self.character_name,
            status='running',
        ).first()

    def get_completed(self) -> list:
        """获取所有 completed 状态的任务（按结束天降序）"""
        return Mission.query.filter_by(
            character_name=self.character_name,
            status='completed',
        ).order_by(Mission.end_day.desc()).all()

    def get_pending(self) -> Mission | None:
        """获取最早的 pending 状态的任务"""
        return Mission.query.filter_by(
            character_name=self.character_name,
            status='pending',
        ).order_by(Mission.start_day.asc()).first()

    def get_all(self) -> list:
        """获取所有非归档任务"""
        return Mission.query.filter(
            Mission.character_name == self.character_name,
            Mission.status != 'archived',
        ).order_by(Mission.start_day.asc()).all()

    def get_archived(self) -> list:
        """获取所有已归档任务"""
        from backend.models import MissionArchive
        return MissionArchive.query.filter_by(
            character_name=self.character_name,
        ).order_by(MissionArchive.archived_at.desc()).all()

    # ═══════════════════════════════════════════════════════════════
    # 核心推进逻辑（每天调用一次）
    # ═══════════════════════════════════════════════════════════════

    def advance(self, char) -> list:
        """
        每日 tick 调用一次。五幕结构事件驱动流程：
        1. pending → running（到达 start_day）：创建 NPC
        2. 遍历 stages → events，按 day_offset 触发未触发的事件
        3. 完成检查：全部事件触发 或 到 end_day
        4. LLM 驱动小事件（30%/15% 概率）
        """
        events_fired = []
        mission = self.get_active()

        # ── 1. 检查 pending → running ──
        if not mission:
            pending = self.get_pending()
            if pending and char.game_day >= pending.start_day:
                pending.status = 'running'
                db.session.commit()
                evt = self._fire_mission_start(pending, char)
                if evt:
                    events_fired.append(evt)
                self._insert_mission_npcs(pending)
                mission = pending
            else:
                return events_fired

        if not mission or mission.status != 'running':
            return events_fired

        # ── 2. 遍历 stages → events，按 day_offset 触发 ──
        fired_set = set(mission.fired_events_list)
        stages = mission.stages_list

        for stage_idx, stage in enumerate(stages):
            for event_idx, event in enumerate(stage.get('events', [])):
                key = f"{stage_idx}_{event_idx}"
                if key in fired_set:
                    continue
                target_day = mission.start_day + event.get('day_offset', 0)
                if char.game_day >= target_day:
                    try:
                        evt = self._fire_mission_event(mission, stage, event, char)
                        if evt:
                            events_fired.append(evt)
                            fired_set.add(key)
                            # Q5 决策：抉择事件不在触发时应用 state_changes，
                            # 等玩家通过 POST /api/mission/choice 选择后再应用
                            if not event.get('is_choice', False):
                                self._apply_state_changes(event.get('state_changes', {}), char)
                            # 更新幕游标
                            if stage_idx > mission.current_stage_index:
                                mission.current_stage_index = stage_idx
                        else:
                            fired_set.add(key)
                            logger.warning(f"[Mission] 事件 {key} 触发返回空值，已跳过")
                    except Exception as e:
                        logger.error(f"[Mission] 事件 {key} 触发异常: {e}", exc_info=True)
                        fired_set.add(key)

        mission.fired_events_list = list(fired_set)
        db.session.commit()

        # ── 3. 完成检查：全部事件触发 或 到 end_day ──
        total_events = sum(len(s.get('events', [])) for s in stages)
        # 防御：旧任务 stages 无嵌套 events → total_events=0 → 跳过完成判定
        if total_events > 0:
            all_fired = len(fired_set) >= total_events
            time_up = bool(mission.end_day) and char.game_day >= mission.end_day

            if all_fired or time_up:
                failed = (time_up and not all_fired)
                mission.status = 'completed'
                mission.completed_at = datetime.utcnow()
                db.session.commit()
                evt = self._fire_mission_end(mission, char, failed=failed)
                if evt:
                    events_fired.append(evt)
                self._summarize_and_archive(mission, char, failed)
                self._try_auto_next(char, mission)
                return events_fired

        # ── 4. LLM 驱动小事件（Q2 决策：保留 30%/15% 概率）──
        if events_fired:
            if random.random() < 0.15:
                llm_evt = self._try_llm_event(mission, char)
                if llm_evt:
                    events_fired.append(llm_evt)
        else:
            if random.random() < 0.30:
                llm_evt = self._try_llm_event(mission, char)
                if llm_evt:
                    events_fired.append(llm_evt)

        return events_fired

    # ═══════════════════════════════════════════════════════════════
    # Phase 驱动（新增）
    # ═══════════════════════════════════════════════════════════════

    def _resolve_current_phase(self, char) -> dict | None:
        """从世界观 JSON 中定位当前游戏天数对应的 phase。

        返回 phase dict（含 phase/day_range/location/description）或 None（超出所有 phase）。
        B0-b 兜底：如果 phase 没有 description，回退到 world_setting.world_prompt。
        """
        from backend.game.world_setting_manager import WorldSettingManager
        WorldSettingManager.clear_cache()
        ws = WorldSettingManager.get(char.name)
        if not ws:
            return None

        phases = ws.get('narrative_schedule', {}).get('phases', [])
        if not phases:
            return None

        today = char.game_day
        for phase in phases:
            day_range = phase.get('day_range', [None, None])
            start = day_range[0] if isinstance(day_range, (list, tuple)) and len(day_range) > 0 else None
            end = day_range[1] if isinstance(day_range, (list, tuple)) and len(day_range) > 1 else None
            if isinstance(start, int) and isinstance(end, int) and start <= today <= end:
                # B0-b 兜底：缺 description 时用 world_prompt 补
                if not phase.get('description'):
                    phase = dict(phase)
                    phase['description'] = ws.get('world_prompt', '')
                return phase

        return None

    # ═══════════════════════════════════════════════════════════════
    # 事件触发
    # ═══════════════════════════════════════════════════════════════

    def _fire_mission_start(self, mission: Mission, char) -> dict | None:
        """任务启动事件"""
        event = EventLog(
            event_type='mission',
            event_category='mission',
            title=f'🎬 新任务：{mission.mission_name}',
            description=(
                f'第{char.game_day}天，{char.name}迎来了一段新的征程：'
                f'{mission.mission_name}'
            ),
            effects=json.dumps({'mission_id': mission.id}),
            game_day=char.game_day,
            game_time=f"{char.game_hour:02d}:{char.game_minute:02d}:00",
            character_name=char.name,
        )
        db.session.add(event)
        db.session.commit()
        logger.info(f"[Mission] 任务开始: {mission.mission_name}")
        return event.to_dict()

    def _fire_mission_event(self, mission: Mission, stage: dict, event: dict, char) -> dict | None:
        """写 EventLog：标题 = 幕名 · 事件名"""
        stage_name = stage.get('name', '')
        event_title = event.get('title', '')
        display = self._flatten_effects_for_display(event.get('state_changes', {}), char)
        event_log = EventLog(
            event_type='mission',
            event_category='mission',
            title=f'{stage_name} · {event_title}',
            description=event.get('description', ''),
            game_day=char.game_day,
            game_time=f"{char.game_hour:02d}:{char.game_minute:02d}:00",
            character_name=char.name,
            location=event.get('location', ''),
            effects=json.dumps({
                'mission_id': mission.id,
                'stage_name': stage_name,
                'event_title': event_title,
                'is_choice': event.get('is_choice', False),
                'choices': event.get('choices', []),
                # 嵌套原样保留（供重应用/参考）
                'state_changes_raw': event.get('state_changes', {}),
                # 面板渲染用的扁平结构
                'state_changes': display['state_changes'],
                'relation_changes': display['relation_changes'],
            }, ensure_ascii=False),
        )
        db.session.add(event_log)
        db.session.commit()
        result = event_log.to_dict()
        if event.get('is_choice'):
            result['choices'] = event.get('choices', [])
        return result

    def _fire_mission_end(self, mission: Mission, char, failed: bool = False) -> dict | None:
        """任务完结（或到期失败）事件"""
        if failed:
            title = f'💥 任务失败：{mission.mission_name}'
            desc = (
                f'第{char.game_day}天，{mission.mission_name} 已超过期限'
                f'（第{mission.end_day}天截止）仍未完成，最终以失败告终。'
                f'{char.name} 的这段征程画上了遗憾的句号。'
            )
        else:
            title = f'🏁 任务完结：{mission.mission_name}'
            desc = (
                f'第{char.game_day}天，{mission.mission_name} 圆满完成。'
                f'{char.name}在这段旅程中收获了许多。'
            )
        event = EventLog(
            event_type='mission',
            event_category='mission',
            title=title,
            description=desc,
            effects=json.dumps({'mission_id': mission.id, 'completed': True, 'failed': failed}),
            game_day=char.game_day,
            game_time=f"{char.game_hour:02d}:{char.game_minute:02d}:00",
            character_name=char.name,
        )
        db.session.add(event)
        db.session.commit()
        logger.info(f"[Mission] 任务完结: {mission.mission_name} (failed={failed})")
        return event.to_dict()

    def _apply_state_changes(self, state_changes: dict, char):
        """应用事件属性变化到角色和 NPC。

        支持的嵌套结构（与 prompt 严格对齐）：
        {
          "character": {"属性名": 增减值, ...},          # 走 apply_attr_changes（惯性/钳制/情绪快照/恋爱状态）
          "npcs": {"NPC姓名": {"closeness": d, "trust": d, "affection": d,
                                "rivalry": d, "hostility": d, "fear": d}},
          "player": {"player_trust": d, "player_affection": d,
                     "player_respect": d, "player_intimacy": d}
        }
        兼容旧格式：npcs 单值 → closeness；player 单值 → player_affection。
        """
        if not isinstance(state_changes, dict):
            return

        # ── 角色属性：统一走 apply_attr_changes，保证与其他事件一致（惯性/钳制/情绪快照/恋爱状态）──
        char_changes = state_changes.get('character') or {}
        if isinstance(char_changes, dict) and char_changes:
            try:
                from backend.game.character import apply_attr_changes
                apply_attr_changes(char_changes, character=char)
            except Exception as e:
                logger.warning(f"[Mission] apply_attr_changes 失败: {e}")

        # ── NPC 关系：写入 Friend 表对应维度 ──
        npcs = state_changes.get('npcs') or {}
        if isinstance(npcs, dict):
            for npc_name, rel_delta in npcs.items():
                if not isinstance(rel_delta, dict):
                    # 旧格式兼容：单值当作 closeness 变化
                    if isinstance(rel_delta, (int, float)):
                        rel_delta = {'closeness': rel_delta}
                    else:
                        continue
                friend = Friend.query.filter_by(
                    character_name=self.character_name, name=npc_name
                ).first()
                if not friend:
                    logger.debug(f"[Mission] NPC {npc_name} 不在 Friend 表，跳过关系变化")
                    continue
                for field, delta in rel_delta.items():
                    if field not in _FRIEND_REL_FIELDS:
                        continue
                    if not isinstance(delta, (int, float)):
                        continue
                    old = getattr(friend, field, 0) or 0
                    setattr(friend, field, max(0, min(100, old + delta)))
            db.session.commit()

        # ── 玩家关系：写入 player_* 字段 ──
        player = state_changes.get('player') or {}
        if isinstance(player, (int, float)):
            player = {'player_affection': player}  # 旧格式兼容
        if isinstance(player, dict) and player:
            intimacy_changed = False
            for field, delta in player.items():
                if field not in _PLAYER_FIELDS:
                    continue
                if not isinstance(delta, (int, float)):
                    continue
                old = getattr(char, field, 0) or 0
                setattr(char, field, max(0, min(100, old + delta)))
                if field == 'player_intimacy':
                    intimacy_changed = True
            if intimacy_changed:
                try:
                    from backend.game.character import update_relationship_status
                    update_relationship_status(char)  # 内部已 commit
                except Exception:
                    pass
            db.session.commit()

    def _flatten_effects_for_display(self, state_changes: dict, char) -> dict:
        """把嵌套 state_changes 转成 event-log 面板能渲染的扁平结构。

        返回 {"state_changes": [{attribute, delta}], "relation_changes": [{name, attribute, delta}]}
        """
        flat_state, flat_relation = [], []
        if isinstance(state_changes, dict):
            char_changes = state_changes.get('character') or {}
            if isinstance(char_changes, dict):
                for attr, delta in char_changes.items():
                    if isinstance(delta, (int, float)):
                        flat_state.append({'attribute': attr, 'delta': round(float(delta), 1)})
            npcs = state_changes.get('npcs') or {}
            if isinstance(npcs, dict):
                for npc_name, rel in npcs.items():
                    if isinstance(rel, (int, float)):
                        rel = {'closeness': rel}
                    if isinstance(rel, dict):
                        for field, delta in rel.items():
                            if isinstance(delta, (int, float)):
                                flat_relation.append({
                                    'name': npc_name, 'attribute': field,
                                    'delta': round(float(delta), 1),
                                })
            player = state_changes.get('player') or {}
            if isinstance(player, (int, float)):
                player = {'player_affection': player}
            if isinstance(player, dict):
                player_name = getattr(char, 'player_identity', '') or '玩家'
                for field, delta in player.items():
                    if isinstance(delta, (int, float)):
                        flat_relation.append({
                            'name': player_name, 'attribute': field,
                            'delta': round(float(delta), 1),
                        })
        return {'state_changes': flat_state, 'relation_changes': flat_relation}

    # ═══════════════════════════════════════════════════════════════
    # NPC 创建（Q7 决策：保留 npc_roster）
    # ═══════════════════════════════════════════════════════════════

    def _insert_mission_npcs(self, mission: Mission):
        """启动时：拆解 npc_list → 写入 Friend 表（三元组唯一）。"""
        for npc in mission.npc_list:
            self._upsert_npc(npc, mission)

    def _upsert_npc(self, npc: dict, mission: Mission):
        """插入/更新 NPC（name+character_name+mission_id 三元组唯一）。"""
        name = npc.get('name', '')
        if not name:
            return

        existing = Friend.query.filter_by(
            character_name=self.character_name,
            name=name,
            mission_id=mission.id,
        ).first()
        if existing:
            return existing

        cross_mission = Friend.query.filter_by(
            character_name=self.character_name,
            name=name,
        ).first()
        if cross_mission and cross_mission.mission_id != mission.id:
            role_suffix = npc.get('role', '')
            new_name = f"{name}({role_suffix})" if role_suffix else f"{name}_v2"
            logger.info(f"[Mission NPC] 跨任务重名处理: {name} → {new_name}")
            name = new_name

        relation_type = npc.get('relation_type', 'acquaintance')
        RELATION_INIT = {
            'mentor':        {'closeness': 60, 'trust': 70, 'affection': 60},
            'rival':         {'closeness': 20, 'trust': 20, 'affection': 10, 'rivalry': 60},
            'friend':        {'closeness': 55, 'trust': 55, 'affection': 55},
            'enemy':         {'closeness': 10, 'trust': 5,  'affection': 5,  'hostility': 70},
            'colleague':     {'closeness': 40, 'trust': 40, 'affection': 40, 'rivalry': 20},
            'family':        {'closeness': 70, 'trust': 65, 'affection': 75},
            'ex_boyfriend':  {'closeness': 30, 'trust': 20, 'affection': 25, 'hostility': 30},
            'client':        {'closeness': 40, 'trust': 60, 'affection': 30},
            'acquaintance':  {'closeness': 25, 'trust': 25, 'affection': 20},
            'protege':       {'closeness': 55, 'trust': 50, 'affection': 60},
        }
        init_vals = RELATION_INIT.get(relation_type, {'closeness': 25, 'trust': 25, 'affection': 20})
        friend = Friend(
            character_name=self.character_name,
            name=name,
            gender=normalize_gender(npc.get('gender')),
            personality=npc.get('personality', ''),
            bio=npc.get('description', ''),
            role=npc.get('role', ''),
            relation_type=relation_type,
            mission_id=mission.id,
            **init_vals,
            loneliness=50, happiness=50, stress=30, mood=60, energy=70, clarity=60,
        )
        db.session.add(friend)
        db.session.commit()
        logger.info(f"[Mission NPC] 新增 {name} ({relation_type}) ← {mission.mission_name}")

    # ═══════════════════════════════════════════════════════════════
    # LLM 生成
    # ═══════════════════════════════════════════════════════════════

    def generate_mission(self, char, world_prompt: str = '', custom_prompt: str = None) -> dict:
        """
        phase 驱动生成五幕结构任务——仅生成预览数据，不写入 DB。
        写入由 confirm_mission(action) 控制。
        custom_prompt 非空时使用用户编辑后的 prompt 直接调 LLM（不重新渲染）。
        """
        # 前置判断：定位当前 phase
        phase = self._resolve_current_phase(char)

        if phase:
            phase_name = phase.get('phase', '')
            phase_desc = phase.get('description', '')
            phase_location = phase.get('location', '')
            day_range = phase.get('day_range', [None, None])
            phase_end = day_range[1] if isinstance(day_range, (list, tuple)) and len(day_range) > 1 else None
            start_day = char.game_day + 1
            if isinstance(phase_end, int) and phase_end >= start_day:
                end_day = phase_end
            else:
                end_day = start_day + 30
        else:
            phase_name = ''
            phase_desc = ''
            phase_location = ''
            start_day = char.game_day + 1
            end_day = start_day + 35

        # A1 决策：end_day 不超出 phase 上限 + 5 天；超了缩减事件数
        max_end = phase_end + 5 if (phase and isinstance(phase_end, int)) else end_day
        if end_day > max_end:
            end_day = max_end
        available_days = end_day - start_day
        # 每幕事件数 = max(1, 可用天数 // 10)，五幕共 5 * events_per_act 个事件
        events_per_act = max(1, available_days // 10)

        main, rendered_prompt = self._generate_main_line(char, world_prompt, phase_name, phase_desc,
                                        phase_location, start_day, end_day, events_per_act,
                                        custom_prompt=custom_prompt)
        if 'error' in main:
            return main

        # 注入时间约束（覆盖 LLM 可能返回的错误 start_day/end_day）
        main['start_day'] = start_day
        main['end_day'] = end_day
        main['_phase_name'] = phase_name
        return {'main': main, 'subsystems': None, '_rendered_prompt': rendered_prompt}

    def render_preview_prompt(self, char, world_prompt: str = '') -> str:
        """预渲染：仅渲染完整 prompt 字符串，不调用 LLM。供前端「生成前预览可编辑」使用。"""
        phase = self._resolve_current_phase(char)
        if phase:
            phase_name = phase.get('phase', '')
            phase_desc = phase.get('description', '')
            phase_location = phase.get('location', '')
            day_range = phase.get('day_range', [None, None])
            phase_end = day_range[1] if isinstance(day_range, (list, tuple)) and len(day_range) > 1 else None
            start_day = char.game_day + 1
            if isinstance(phase_end, int) and phase_end >= start_day:
                end_day = phase_end
            else:
                end_day = start_day + 30
        else:
            phase_name = ''
            phase_desc = ''
            phase_location = ''
            start_day = char.game_day + 1
            end_day = start_day + 35
        max_end = phase_end + 5 if (phase and isinstance(phase_end, int)) else end_day
        if end_day > max_end:
            end_day = max_end
        available_days = end_day - start_day
        events_per_act = max(1, available_days // 10)
        return self._render_main_prompt(
            char, world_prompt, phase_name, phase_desc, phase_location,
            start_day, end_day, events_per_act,
        )

    def confirm_mission(self, char, preview_data: dict, action: str) -> dict:
        """
        确认并写入 DB。
        action:
          - "overwrite": 覆盖上一个 pending/running mission
          - "create_new": 新增一个 pending mission
        """
        main = preview_data.get('main', {})

        if not main:
            raise ValueError("主线数据为空")
        mission_name = main.get('mission_name', '').strip()
        stages = main.get('stages', [])
        npcs = main.get('npc_roster', [])
        if not mission_name or mission_name == '未命名任务':
            raise ValueError("任务名称无效或缺失")
        if not stages:
            raise ValueError("阶段数据为空，无法创建无剧情的任务")
        if not npcs:
            raise ValueError("NPC 列表为空，无法创建无人物的任务")

        # 覆写模式：归档当前所有非 archived 任务
        if action == 'overwrite':
            active_missions = Mission.query.filter(
                Mission.character_name == self.character_name,
                Mission.status.in_(['pending', 'running', 'completed']),
            ).all()
            for m in active_missions:
                logger.info(f"[Mission] 覆写模式：归档旧任务 {m.mission_name} (id={m.id})")
                if m.status == 'running':
                    m.status = 'completed'
                    m.completed_at = datetime.utcnow()
                m.status = 'archived'
            db.session.commit()

        start_day = main.get('start_day', char.game_day + 1)
        end_day = main.get('end_day', char.game_day + 30)

        mission = Mission(
            character_name=char.name,
            mission_name=mission_name,
            mission_type=main.get('mission_type', 'legal'),
            mission_description=main.get('mission_description', ''),
            core_conflict=main.get('core_conflict', ''),
            tone=main.get('tone', 'thrilling'),
            start_day=start_day,
            end_day=end_day,
            status='pending',
            current_stage_index=-1,
            fired_events_list=[],
            npc_list=npcs,
            phase_name=main.get('_phase_name', ''),
            rendered_prompt=preview_data.get('_rendered_prompt', ''),
        )

        # 阶段按 day_offset 升序重排
        stages = sorted(stages, key=lambda s: s.get('day_offset', 0))
        mission.stages_list = stages

        # 时间交叉校验：end_day 必须覆盖所有事件的最大 day_offset
        max_off = 0
        for s in stages:
            for e in s.get('events', []):
                max_off = max(max_off, e.get('day_offset', 0))
            max_off = max(max_off, s.get('day_offset', 0))
        min_end = start_day + max_off
        if not mission.end_day or mission.end_day < min_end:
            mission.end_day = min_end
            logger.warning(f"[Mission] end_day 不足，已延展至 {mission.end_day}")
        if mission.end_day <= mission.start_day:
            mission.end_day = mission.start_day + 1

        db.session.add(mission)
        db.session.commit()
        logger.info(f"[Mission] 写入成功: {mission.mission_name} (id={mission.id}, action={action})")

        # 立即落库 NPC→Friend（幂等）
        try:
            self._insert_mission_npcs(mission)
        except Exception as e:
            logger.warning(f"[Mission] 确认时初始化 NPC 失败（不影响任务创建）: {e}")

        return mission.to_dict()

    def _normalize_main_data(self, data):
        """规整主线 LLM 返回的数据类型。"""
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            if not data:
                return {'error': '主线生成返回空数组'}
            if isinstance(data[0], dict):
                return data[0]
            return {'error': '主线生成返回非对象的数组'}
        return {'error': f'主线生成返回了无法识别的类型: {type(data).__name__}'}

    def _llm_generate_json(self, char, prompt: str, call_type: str,
                           system_message: str = "你是一个剧情设计师。只返回合法 JSON。",
                           max_tokens: int = 8000, max_retries: int = 2) -> dict | list | None:
        """调用 LLM 并解析 JSON；解析失败自动重试最多 max_retries 次。"""
        from backend.models import LLMConfig
        from backend.game.llm_utils import safe_llm_post
        from backend.game.event import extract_json_from_llm_response

        config = LLMConfig.query.filter_by(is_active=True).first()
        if not config:
            return {'error': '无活跃LLM配置'}
        llm_config = config.to_secret_dict()

        last_err = None
        for attempt in range(1, max_retries + 1):
            result = safe_llm_post(
                api_url=llm_config['api_url'], api_key=llm_config['api_key'],
                model=llm_config.get('model_name', 'gpt-4o-mini'),
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens, temperature=0.8,
                timeout=(60, 180), call_type=call_type,
                character_name=char.name,
                extra_body={"thinking": {"type": "disabled"}},
            )
            if not result:
                last_err = 'LLM 返回为空'
                logger.warning(f"[Mission] {call_type} 第{attempt}次调用返回空（将重试）")
                continue
            content = result["choices"][0]["message"]["content"]
            data, err = extract_json_from_llm_response(content)
            if err:
                last_err = err
                logger.warning(f"[Mission] {call_type} 第{attempt}次解析失败（将重试）: {err[:120]}")
                continue
            return data
        return {'error': f'{call_type} 重试 {max_retries} 次仍解析失败: {last_err}'}

    def _render_main_prompt(self, char, world_prompt: str,
                             phase_name: str = '', phase_desc: str = '',
                             phase_location: str = '',
                             start_day: int = 0, end_day: int = 0,
                             events_per_act: int = 3) -> str:
        """纯渲染：根据当前角色状态 + 世界观 + phase，生成实际投喂 LLM 的完整 prompt（不调用 LLM）。"""
        completed = self.get_completed()
        history = "\n".join([
            f"- {m.mission_name}（第{m.start_day}-{m.end_day}天，已完结）"
            for m in completed
        ]) or "无"

        skills_keys_display = get_skill_keys_display(char)
        available_days = end_day - start_day

        # ── 世界观字段（2a/2b）：从 WorldSetting 提取，注入 prompt ──
        ws = {}
        try:
            from backend.game.world_setting_manager import WorldSettingManager
            WorldSettingManager.clear_cache()
            ws = WorldSettingManager.get(char.name) or {}
        except Exception:
            ws = {}
        anchor = ws.get('anchor', {}) or {}
        constraints = ws.get('constraints', {}) or {}
        life_goals = ws.get('life_goals', []) or []
        if isinstance(life_goals, list):
            life_goals_display = "\n".join(
                f"  - {g.get('goal', '')}（类型：{g.get('type', '')}，紧急度：{g.get('urgency', '')}）"
                for g in life_goals if isinstance(g, dict)
            ) or "（无）"
        else:
            life_goals_display = "（无）"
        central_conflict = ws.get('central_conflict', '') or ''
        core_trait = anchor.get('core_trait', '') or ''
        value = anchor.get('value', '') or ''
        boundary = anchor.get('boundary', '') or ''
        conflict_zone = anchor.get('conflict_zone', '') or ''
        stress_sources = ws.get('stress_sources', []) or []
        comfort_activities = ws.get('comfort_activities', []) or []
        forbidden_behaviors = constraints.get('forbidden_behaviors', []) or []
        story_tones = ws.get('story_tones', []) or []
        worldview_display = (
            f"人生目标 life_goals：\n{life_goals_display}\n"
            f"核心矛盾 central_conflict：{central_conflict}\n"
            f"核心性格 core_trait：{core_trait}\n"
            f"核心价值观 value：{value}\n"
            f"底线 boundary：{boundary}\n"
            f"内心冲突区 conflict_zone：{conflict_zone}\n"
            f"压力源 stress_sources：{'、'.join(stress_sources) if stress_sources else '（无）'}\n"
            f"安慰活动 comfort_activities：{'、'.join(comfort_activities) if comfort_activities else '（无）'}\n"
            f"禁止行为 forbidden_behaviors：{'、'.join(forbidden_behaviors) if forbidden_behaviors else '（无）'}\n"
            f"整体故事基调 story_tones：{'、'.join(story_tones) if story_tones else '（无）'}"
        )

        # ── 当前属性值：供 LLM 生成「符合现状」的变化 ──
        current_attrs_display = "\n".join(
            f"  {k}: {round(getattr(char, k, 0) or 0, 1)}" for k in _CHAR_ATTR_FIELDS
        )
        player_relation_display = "\n".join(
            f"  {k}: {round(getattr(char, k, 0) or 0, 1)}" for k in _PLAYER_FIELDS
        )

        try:
            from backend.game.prompt_registry import get_prompt_manager
            pm = get_prompt_manager()
            prompt = pm.render("mission.generate_main", char, extra={
                "context.world_prompt": world_prompt,
                "context.mission_history": history,
                "context.skills_keys": skills_keys_display,
                "context.phase_name": phase_name,
                "context.phase_desc": phase_desc,
                "context.phase_location": phase_location,
                "context.start_day": str(start_day),
                "context.end_day": str(end_day),
                "context.available_days": str(available_days),
                "context.events_per_act": str(events_per_act),
                "context.worldview": worldview_display,
                "context.current_attrs": current_attrs_display,
                "context.player_relation": player_relation_display,
            })
        except Exception:
            prompt = (
                f"为角色 {char.name}（{char.major}，{char.personality_type}）"
                f"生成一个五幕电影结构的叙事任务。\n"
                f"世界观阶段：{phase_name}\n阶段描述：{phase_desc}\n阶段地点：{phase_location}\n"
                f"时间范围：第{start_day}天 ~ 第{end_day}天（共{available_days}天）\n"
                f"每幕事件数：{events_per_act}\n"
                f"已归档任务：{history}\n技能清单：{skills_keys_display}\n"
                f"生成五幕 stages（每幕含 {events_per_act} 个嵌套 events）+ NPC(3-6个)。\n"
                f"每个 event 含 title/day_offset/description/state_changes/is_choice。\n"
                f"NPC 名字必须有辨识度。\n只返回 JSON。"
            )
        return prompt

    def _generate_main_line(self, char, world_prompt: str,
                            phase_name: str = '', phase_desc: str = '',
                            phase_location: str = '',
                            start_day: int = 0, end_day: int = 0,
                            events_per_act: int = 3,
                            custom_prompt: str = None) -> dict:
        """LLM 调用：生成五幕结构 stages + NPC（phase 驱动）。custom_prompt 非空时直接用它调 LLM（用户编辑后的 prompt）。"""
        if custom_prompt and custom_prompt.strip():
            prompt = custom_prompt
        else:
            prompt = self._render_main_prompt(
                char, world_prompt, phase_name, phase_desc, phase_location,
                start_day, end_day, events_per_act,
            )

        data = self._llm_generate_json(
            char, prompt, "mission_generate_main",
            system_message="你是一个剧情设计师。只返回合法 JSON。",
            max_tokens=32000,
        )
        if isinstance(data, dict) and 'error' in data:
            return data, prompt
        return self._normalize_main_data(data), prompt

    # ═══════════════════════════════════════════════════════════════
    # 总结 + 归档（Q6 决策：失败任务也生成总结并向量化）
    # ═══════════════════════════════════════════════════════════════

    def _summarize_and_archive(self, mission: Mission, char, failed: bool):
        """任务结束：LLM 总结 → 归档 → 向量化存入 CharacterMemory"""
        summary = self._llm_summarize_mission(mission, char, failed)
        self._archive_mission(mission, char, failed=failed, summary=summary)
        # Q6 决策：失败任务也向量化
        if summary:
            self._store_mission_memory(mission, char, summary)

    def _llm_summarize_mission(self, mission: Mission, char, failed: bool) -> dict:
        """调 LLM 生成任务总结"""
        from backend.models import LLMConfig
        from backend.game.llm_utils import safe_llm_post
        from backend.game.event import extract_json_from_llm_response

        events_summary = self._get_events_summary(mission)
        events_text = json.dumps(events_summary, ensure_ascii=False, indent=2)[:2000]

        prompt = f"""请总结以下任务经历：

任务名：{mission.mission_name}
任务描述：{mission.mission_description}
是否失败：{failed}
经历天数：第{mission.start_day}天 ~ 第{char.game_day}天
事件日志摘要：{events_text}

请以 JSON 返回：
{{
  "mission_intro": "任务介绍（2-3句话概括任务内容）",
  "growth_reflection": "女主的收获与人生感悟（100-200字，第一人称视角）",
  "npc_interactions": [{{"name": "NPC名", "summary": "互动摘要"}}],
  "player_relationship_change": "女主与玩家关系的变化描述（如信任加深/产生隔阂等）"
}}
"""
        config = LLMConfig.query.filter_by(is_active=True).first()
        if not config:
            return {}
        llm_config = config.to_secret_dict()
        try:
            result = safe_llm_post(
                api_url=llm_config['api_url'], api_key=llm_config['api_key'],
                model=llm_config.get('model_name', 'gpt-4o-mini'),
                messages=[
                    {"role": "system", "content": "你是一个叙事总结AI。只返回 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4000, temperature=0.7,
                timeout=(30, 90), call_type="mission_summary",
                character_name=char.name,
                extra_body={"thinking": {"type": "disabled"}},
            )
            if result:
                content = result["choices"][0]["message"]["content"]
                data, err = extract_json_from_llm_response(content)
                if not err and isinstance(data, dict):
                    return data
        except Exception as e:
            logger.warning(f"[Mission] LLM 总结生成失败: {e}")
        return {}

    def _store_mission_memory(self, mission: Mission, char, summary: dict):
        """将任务总结向量化存入 CharacterMemory"""
        try:
            from backend.models import CharacterMemory
            from backend.game.memory import _compute_memory_embedding

            text = summary.get('growth_reflection', '') or summary.get('mission_intro', '')
            if not text:
                return

            npc_text = ''
            npc_list = summary.get('npc_interactions', [])
            if isinstance(npc_list, list):
                npc_text = '; '.join(
                    f"{n.get('name','')}: {n.get('summary','')}" for n in npc_list if isinstance(n, dict)
                )

            memory = CharacterMemory(
                character_name=char.name,
                memory_type='mission',
                content=text,
                context=json.dumps({
                    'mission_name': mission.mission_name,
                    'npc_interactions': npc_text,
                    'player_relationship_change': summary.get('player_relationship_change', ''),
                    'failed': mission.status == 'failed',
                }, ensure_ascii=False),
                importance=8,
                emotional_weight=7,
                source_day=char.game_day,
            )
            db.session.add(memory)
            memory.embedding = _compute_memory_embedding(text)
            db.session.commit()
            logger.info(f"[Mission] 任务记忆已存入 CharacterMemory: {mission.mission_name}")
        except Exception as e:
            logger.warning(f"[Mission] 任务记忆存储失败: {e}")

    def _archive_mission(self, mission: Mission, char, failed: bool = False, summary: dict = None):
        """归档：创建 MissionArchive 全量快照 + 清理运行时数据"""
        from backend.models import MissionArchive
        logger.info(f"[Mission] 归档: {mission.mission_name} (failed={failed})")

        summary = summary or {}
        npc_interactions_str = ''
        npc_list = summary.get('npc_interactions', [])
        if isinstance(npc_list, list):
            npc_interactions_str = json.dumps(npc_list, ensure_ascii=False)

        archive = MissionArchive(
            mission_id=mission.id,
            character_name=self.character_name,
            mission_snapshot=json.dumps(mission.to_dict(), ensure_ascii=False),
            achievements_snapshot=json.dumps(self._get_mission_achievements_snapshot(mission), ensure_ascii=False),
            goal_result=json.dumps(self._get_mission_goal_result(mission, char), ensure_ascii=False),
            relationships_snapshot=json.dumps(self._get_npc_relation_states(mission), ensure_ascii=False),
            arc_snapshot='{}',
            events_summary=json.dumps(self._get_events_summary(mission), ensure_ascii=False),
            mission_summary=summary.get('mission_intro', '') + '\n' + summary.get('growth_reflection', ''),
            npc_interactions=npc_interactions_str,
            player_relationship_change=summary.get('player_relationship_change', ''),
            chat_log_path=f"你与{char.name}的对话/",
            chat_message_count=0,
            total_game_days=max(0, mission.end_day - mission.start_day),
            stages_completed=mission.current_stage_index + 1,
            is_failed=failed,
        )
        db.session.add(archive)
        db.session.flush()

        self._cleanup_mission_data(mission, char)
        mission.status = 'archived'
        db.session.commit()

    def _get_mission_achievements_snapshot(self, mission: Mission) -> list:
        """获取任务专属成就的最终状态"""
        ach_ids = mission.achievement_id_list
        if not ach_ids:
            return []
        achievements = Achievement.query.filter(
            Achievement.character_name == self.character_name,
            Achievement.mission_id == mission.id,
        ).all()
        return [a.to_dict() for a in achievements] if achievements else []

    def _get_mission_goal_result(self, mission: Mission, char) -> dict:
        """获取目标达成结果"""
        if not mission.goal_key:
            return {}
        goals = char.goals or {}
        return {
            'key': mission.goal_key,
            'label': mission.goal_label,
            'target': mission.goal_target,
            'actual': goals.get(mission.goal_key, 0),
            'achieved': goals.get(mission.goal_key, 0) >= mission.goal_target,
        }

    def _get_npc_relation_states(self, mission: Mission) -> list:
        """获取任务 NPC 的最终关系状态"""
        friends = Friend.query.filter_by(
            character_name=self.character_name,
            mission_id=mission.id,
        ).all()
        return [f.to_dict() for f in friends] if friends else []

    def _get_events_summary(self, mission: Mission) -> dict:
        """获取任务事件日志摘要"""
        events = EventLog.query.filter_by(
            character_name=self.character_name,
            event_category='mission',
        ).filter(
            EventLog.game_day >= mission.start_day,
            EventLog.game_day <= mission.end_day,
        ).order_by(EventLog.game_day.asc()).all()
        return {
            'total': len(events),
            'titles': [e.title for e in events[:20]],
        }

    def _cleanup_mission_data(self, mission: Mission, char):
        """清理运行时数据（简化版：只清 NPC mission_id + 旧成就绑定）"""
        try:
            # 清理成就动态映射（旧任务可能存在）
            from backend.game.achievement_checker import unregister_mission_achievements
            unregister_mission_achievements(mission_id=mission.id)

            # NPC mission_id 清空
            Friend.query.filter_by(
                character_name=self.character_name,
                mission_id=mission.id,
            ).update({'mission_id': None})

            # 成就 mission_id 清空（保留记录）
            Achievement.query.filter_by(
                character_name=self.character_name,
                mission_id=mission.id,
            ).update({'mission_id': None})

            # 清理 goal（旧任务可能存在 goal_key）
            if mission.goal_key:
                goals = char.goals or {}
                goals.pop(mission.goal_key, None)
                char.goals = goals
        except Exception as e:
            logger.warning(f"[Mission] 清理失败: {e}")
        db.session.commit()

    # ═══════════════════════════════════════════════════════════════
    # LLM 小事件 + auto_next
    # ═══════════════════════════════════════════════════════════════

    def _try_llm_event(self, mission: Mission, char) -> dict | None:
        """LLM 驱动的小事件（由 advance 控制 30%/15% 概率，这里只做生成）"""
        from backend.models import LLMConfig
        from backend.game.llm_utils import safe_llm_post
        from backend.game.event import extract_json_from_llm_response

        config = LLMConfig.query.filter_by(is_active=True).first()
        if not config:
            return None

        stage = mission.current_stage
        if not stage:
            return None

        dialogue = ''
        try:
            from backend.game.event import get_recent_dialogue
            dialogue = get_recent_dialogue(limit=5)
        except Exception:
            pass

        if not dialogue:
            title = f'💬 {mission.mission_name} · 新进展'
            desc = f'{char.name}在{stage.get("name","当前阶段")}有了新的进展。'
        else:
            llm_config = config.to_secret_dict()
            try:
                from backend.game.prompt_registry import get_prompt_manager
                pm = get_prompt_manager()
                prompt = pm.render("mission.progress_event", char, extra={
                    "context.mission_name": mission.mission_name,
                    "context.stage_name": stage.get('name', '?'),
                    "context.stage_hint": stage.get('narrative_prompt', ''),
                    "context.dialogue": dialogue,
                })
            except Exception:
                prompt = (
                    f"基于以下对话和任务阶段，生成一段任务进展事件描述：\n"
                    f"任务：{mission.mission_name}\n"
                    f"阶段：{stage.get('name', '?')}\n"
                    f"对话：{dialogue}\n"
                    f"只返回 JSON: {{title, description}}"
                )

            result = safe_llm_post(
                api_url=llm_config['api_url'], api_key=llm_config['api_key'],
                model=llm_config.get('model_name', 'gpt-4o-mini'),
                messages=[
                    {"role": "system", "content": "你是一个剧情推进AI。只返回 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=32000, temperature=0.9,
                timeout=(30, 60), call_type="mission_llm_event",
                character_name=char.name,
                extra_body={"thinking": {"type": "disabled"}},
            )
            if result:
                content = result["choices"][0]["message"]["content"]
                data, err = extract_json_from_llm_response(content)
                if not err:
                    title = f'💬 {mission.mission_name} · {data.get("title", "新进展")}'
                    desc = data.get('description', '')
                else:
                    title = f'💬 {mission.mission_name} · 新进展'
                    desc = f'{char.name}在{stage.get("name","当前阶段")}有了新的进展。'
            else:
                title = f'💬 {mission.mission_name} · 新进展'
                desc = f'{char.name}在{stage.get("name","当前阶段")}有了新的进展。'

        event = EventLog(
            event_type='mission', event_category='mission',
            title=title, description=desc,
            effects=json.dumps({'mission_id': mission.id, 'llm_generated': True}),
            game_day=char.game_day,
            game_time=f"{char.game_hour:02d}:{char.game_minute:02d}:00",
            character_name=char.name,
        )
        db.session.add(event)
        db.session.commit()
        return event.to_dict()

    def _try_auto_next(self, char, completed_mission: Mission):
        """完成后自动生成下一个任务（重试 3 次，全部失败后标记 has_pending_mission）"""
        pending = self.get_pending()
        if pending and char.game_day >= pending.start_day:
            pending.status = 'running'
            db.session.commit()
            self._fire_mission_start(pending, char)
            self._insert_mission_npcs(pending)
            logger.info(f"[Mission Auto] 启动 pending 任务: {pending.mission_name}")
            return

        if pending:
            return

        # LLM 生成 — 不再依赖 world_prompt，改为 phase 驱动
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                result = self.generate_mission(char)
                if 'error' not in result:
                    try:
                        self.confirm_mission(char, result, 'create_new')
                        logger.info(
                            f"[Mission Auto] 生成并落库成功（第{attempt}次尝试）: "
                            f"{result.get('main', {}).get('mission_name', '')}"
                        )
                        return
                    except Exception as ce:
                        logger.warning(f"[Mission Auto] 预览生成成功但落库失败: {ce}")
                logger.warning(
                    f"[Mission Auto] 第{attempt}次生成失败: {result.get('error')}"
                )
            except Exception as e:
                logger.warning(f"[Mission Auto] 第{attempt}次生成异常: {e}")

        logger.error(f"[Mission Auto] 3次重试全部失败，标记 has_pending_mission")
        char.has_pending_mission = True
        db.session.commit()

    # ═══════════════════════════════════════════════════════════════
    # 对话上下文
    # ═══════════════════════════════════════════════════════════════

    def get_context(self, char) -> str:
        """生成用于注入对话 prompt 的任务上下文（五幕进度版）"""
        mission = self.get_active()
        if not mission:
            pending = self.get_pending()
            if pending:
                return (
                    f"【即将开始的任务】{pending.mission_name}"
                    f"（第{pending.start_day}天启动）"
                )
            return ""

        stages = mission.stages_list
        total_events = sum(len(s.get('events', [])) for s in stages)
        fired_count = len(mission.fired_events_list)
        current_stage = mission.current_stage
        stage_name = current_stage.get('name', '当前阶段') if current_stage else '未知阶段'

        progress = f"{fired_count}/{total_events}" if total_events > 0 else f"阶段{mission.current_stage_index + 1}/{len(stages)}"

        return (
            f"【当前任务】{mission.mission_name}\n"
            f"  进度：{stage_name}（{progress} 事件已触发）\n"
            f"  核心冲突：{mission.core_conflict}\n"
            f"  {char.name}最近的心思都在这上面，聊天时总会不自觉地提到。"
        )

    # ═══════════════════════════════════════════════════════════════
    # 抉择处理（Q5 决策：不阻塞，玩家通过 API 选择后应用 state_changes）
    # ═══════════════════════════════════════════════════════════════

    def apply_choice(self, event_id: int, choice_index: int, char) -> dict | None:
        """处理抉择事件的选择：找到 EventLog → 取 choices[choice_index] → 应用 state_changes"""
        event = EventLog.query.filter_by(
            id=event_id,
            character_name=self.character_name,
        ).first()
        if not event:
            return None

        try:
            effects = json.loads(event.effects) if event.effects else {}
        except (json.JSONDecodeError, TypeError):
            return None

        if not effects.get('is_choice'):
            return None

        choices = effects.get('choices', [])
        if choice_index < 0 or choice_index >= len(choices):
            return None

        selected = choices[choice_index]
        state_changes = selected.get('state_changes', {})
        if state_changes:
            self._apply_state_changes(state_changes, char)
            # 回写扁平关系/属性变化，使事件日志面板能展示选择后的影响
            display = self._flatten_effects_for_display(state_changes, char)
            effects['state_changes'] = display['state_changes']
            effects['relation_changes'] = display['relation_changes']

        # 标记事件已选择
        effects['choice_made'] = choice_index
        event.effects = json.dumps(effects, ensure_ascii=False)
        db.session.commit()

        logger.info(f"[Mission] 抉择已处理: event={event_id}, choice={choice_index}")
        return {
            'event_id': event_id,
            'choice_index': choice_index,
            'chosen_text': selected.get('text', ''),
            'applied': True,
        }


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def get_skill_keys(char) -> list:
    """从 char.skills dict 提取所有技能名"""
    skills = getattr(char, 'skills', None)
    if not skills or not isinstance(skills, dict):
        legacy = ['writing_skill', 'coding_skill', 'social_skill', 'learning_skill', 'fitness']
        return [k for k in legacy if getattr(char, k, 0) > 0]
    return list(skills.keys())


def get_skill_keys_display(char) -> str:
    """返回逗号分隔的技能名列表，用于 prompt 注入"""
    keys = get_skill_keys(char)
    return "、".join(keys) if keys else "暂无"
