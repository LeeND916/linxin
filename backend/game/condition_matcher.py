# -*- coding: utf-8 -*-
"""
条件求值引擎 — 根据角色当前状态，匹配 JSON 规则模板中的条件。

用法:
    matcher = ConditionMatcher()
    matched = matcher.match_all(char)  # 返回所有匹配的规则
"""
import json
import logging
import random
from typing import Optional
from backend.models import db, EventLog, EventCooldown
from backend.game.triggered_events import load_all_templates
from backend.game.cooldown_manager import CooldownManager

logger = logging.getLogger(__name__)

# 单事件属性变化约束
MAX_SINGLE_DELTA = 25
MAX_TOTAL_DELTA = 50

# 每日 structured_event（角色专属/任务模板）硬上限，防止同日事件刷屏
STRUCTURED_EVENT_DAILY_CAP = 3


class ConditionMatcher:
    """条件求值引擎：加载规则模板 → 逐条检查条件 → 返回匹配项。"""

    def __init__(self, character_name: str = None):
        self.templates = load_all_templates(character_name)
        self.cooldown = CooldownManager()

    def evaluate_condition(self, condition: dict, char) -> bool:
        """求值单条条件。"""
        # 冷却检查
        if 'not_cooldown' in condition:
            trigger_id = condition.get('not_cooldown', '')
            hours = condition.get('hours', 24)
            return not self.cooldown.is_on_cooldown(char.name, trigger_id)

        # at_location: 角色当前位置/计划位置匹配
        if 'at_location' in condition:
            target_loc = condition.get('at_location', '')
            current = getattr(char, 'planned_location', '') or getattr(char, 'location', '')
            return current == target_loc

        # time_between: 游戏时间在指定区间内
        if 'time_between' in condition:
            time_range = condition.get('time_between', '')
            try:
                start_s, end_s = time_range.split('-')
                current_minutes = (char.game_hour or 0) * 60 + (char.game_minute or 0)
                start_minutes = int(start_s.split(':')[0]) * 60 + int(start_s.split(':')[1])
                end_minutes = int(end_s.split(':')[0]) * 60 + int(end_s.split(':')[1])
                return start_minutes <= current_minutes <= end_minutes
            except (ValueError, AttributeError):
                return False

        op = condition.get('op', '')
        attr = condition.get('attr', '')
        # 归一化技能属性：skill_X → skills.X（LLM 按提示词约定输出 skill_ 前缀）
        if attr.startswith('skill_') and '.' not in attr:
            attr = 'skills.' + attr[len('skill_'):]
        target_value = condition.get('value')

        # 从 JSON 路径读取属性（如 skills.coding），并支持派生属性
        attr_value = self._resolve_value(char, attr)
        if attr_value is None:
            return False

        try:
            if op == '<':
                return attr_value < target_value
            elif op == '>':
                return attr_value > target_value
            elif op == '<=':
                return attr_value <= target_value
            elif op == '>=':
                return attr_value >= target_value
            elif op == '==':
                return attr_value == target_value
            elif op == 'between':
                return condition.get('min', 0) <= attr_value <= condition.get('max', 100)
            else:
                return True  # 未知操作符视为通过
        except (TypeError, ValueError):
            return False

    def _get_attr_by_path(self, char, path: str):
        """通过点路径读取属性，如 'skills.coding' → char.skills['coding']。"""
        if '.' in path:
            parts = path.split('.', 1)
            obj = getattr(char, parts[0], None)
            if obj and isinstance(obj, dict):
                return obj.get(parts[1])
            return None
        else:
            return getattr(char, path, None)

    def _resolve_value(self, char, attr: str):
        """解析属性值，支持派生属性（friend_count / library_visit / goal_*）。"""
        # 派生：朋友数量
        if attr == 'friend_count':
            try:
                from backend.models import Friend
                return Friend.query.filter_by(character_name=char.name).count()
            except Exception:
                return None
        # 派生：图书馆访问次数
        if attr == 'library_visit':
            try:
                from backend.models import CharacterActivityMap
                m = CharacterActivityMap.query.filter_by(
                    character_name=char.name, venue_id='library').first()
                return m.visit_count if m else 0
            except Exception:
                return None
        # 派生：目标当前进度（goal_<key>）
        if attr.startswith('goal_'):
            key = attr[5:]
            goals = getattr(char, 'goals', None)
            if isinstance(goals, dict):
                return goals.get(key)
            return None
        # 归一化技能属性：skill_coding → skills.coding
        if attr.startswith('skill_') and '.' not in attr:
            attr = 'skills.' + attr[len('skill_'):]
        return self._get_attr_by_path(char, attr)

    def check_conditions(self, conditions: list, char) -> bool:
        """AND 组合求值：全部条件通过才返回 True。"""
        for cond in conditions:
            if not self.evaluate_condition(cond, char):
                return False
        return True

    def validate_event_deltas(self, state_changes: list) -> tuple:
        """校验属性变化是否在约束范围内。
        
        返回 (is_valid: bool, error_msg: str)
        """
        for c in state_changes:
            delta = abs(c.get('delta', 0))
            if delta > MAX_SINGLE_DELTA:
                attr = c.get('attribute', '?')
                return False, f"{attr} 单属性变化 {delta} 超过限制 {MAX_SINGLE_DELTA}"
        total = sum(abs(c.get('delta', 0)) for c in state_changes)
        if total > MAX_TOTAL_DELTA:
            return False, f"属性变化总和 {total} 超过限制 {MAX_TOTAL_DELTA}"
        return True, None

    def match_all(self, char) -> list:
        """检查所有模板（通用+角色专属），返回所有匹配的规则。"""
        matched = []
        all_templates = list(self.templates)
        for tmpl in all_templates:
            conditions = tmpl.get('conditions', [])
            if not self.check_conditions(conditions, char):
                continue

            # 校验 deltas
            state_changes = tmpl.get('state_changes', [])
            valid, err = self.validate_event_deltas(state_changes)
            if not valid:
                logger.warning(
                    f"[ConditionMatcher] 规则 {tmpl.get('trigger_id')} "
                    f"校验失败: {err}，跳过"
                )
                continue

            matched.append(tmpl)

        if matched:
            logger.info(f"[ConditionMatcher] 命中 {len(matched)} 条规则")

        return matched

    def fire_event(self, char, template: dict) -> dict:
        """执行一条规则事件：写 EventLog，应用属性变化，设冷却。

        返回 EventLog 的 to_dict()。
        """
        trigger_id = template.get('trigger_id', 'unknown')

        # ── 每日硬上限：防止角色专属/任务模板在同一天被批量触发导致事件刷屏 ──
        # （修复此前「23:14 同时刻多事件」的溢出问题）
        try:
            _today_structured = EventLog.query.filter(
                EventLog.game_day == getattr(char, 'game_day', 0),
                EventLog.character_name == (char.name or ''),
                EventLog.event_type == 'structured_event',
            ).count()
            if _today_structured >= STRUCTURED_EVENT_DAILY_CAP:
                logger.info(
                    f"[ConditionMatcher] 今日 structured_event 已达上限({STRUCTURED_EVENT_DAILY_CAP})，跳过 {trigger_id}"
                )
                return None
        except Exception as _ce:
            logger.warning(f"[ConditionMatcher] 每日上限检查失败（不影响触发）: {_ce}")

        # 事件发生的时刻：打散到合理时段，避免全部挤在 tick 运行的深夜时刻
        _hour = random.randint(7, 22)
        _minute = random.choice([0, 15, 30, 45])
        event_time = f"{_hour:02d}:{_minute:02d}:00"

        title = template.get('narrative', {}).get('title', trigger_id)
        description = template.get('narrative', {}).get('description', '')
        # 替换 {name} 占位符
        description = description.replace('{name}', char.name or '')

        # 应用属性变化
        state_changes = template.get('state_changes', [])
        applied = {}
        old_values = {}
        for sc in state_changes:
            attr = sc.get('attribute', '')
            delta = sc.get('delta', 0)
            if not attr or delta == 0:
                continue
            if '.' in attr:
                parts = attr.split('.', 1)
                obj = getattr(char, parts[0], None)
                if isinstance(obj, dict):
                    old = obj.get(parts[1], 0)
                    old_values[attr] = old
                    new_val = max(0, min(100, old + delta))
                    obj[parts[1]] = new_val
                    applied[attr] = delta
            else:
                old = getattr(char, attr, 0)
                if old is None:
                    old = 0
                old_values[attr] = old
                new_val = max(0, min(100, old + delta))
                setattr(char, attr, new_val)
                applied[attr] = delta

        if applied:
            db.session.commit()

        # 构建 state_changes 数组（与 event.py 格式一致）
        state_changes_arr = []
        for key, delta in applied.items():
            old_val = old_values.get(key, 0)
            new_val = getattr(char, key, None)
            if new_val is not None:
                state_changes_arr.append({
                    'attribute': key,
                    'old': round(old_val),
                    'new': round(new_val),
                    'delta': round(delta),
                })

        # 构建 effects（与 event.py 格式一致）
        full_effects = {
            'changes': applied,
            'state_changes': state_changes_arr,
            'relation_changes': [],
        }

        event = EventLog(
            event_type='structured_event',
            event_category=template.get('category', 'daily'),
            title=title,
            description=description,
            effects=json.dumps(full_effects, ensure_ascii=False),
            game_day=char.game_day,
            game_time=event_time,
            character_name=char.name,
            location=template.get('location', ''),
        )
        db.session.add(event)
        db.session.commit()

        # 事件写入向量记忆
        try:
            from backend.game.memory import _compute_memory_embedding
            from backend.models import CharacterMemory

            cat = template.get('category', 'daily')
            imp_map = {'case':70,'narrative':80,'relation_initiated':70,'story_arc':75,'achievement':80,'news':60}
            importance = imp_map.get(cat, 30)
            total_delta = sum(abs(d) for d in applied.values())
            if total_delta > 20:
                importance = min(90, importance + 10)

            memory_text = f"第{char.game_day}天：{description}"
            embedding_blob = _compute_memory_embedding(memory_text)
            mem = CharacterMemory(
                character_name=char.name,
                memory_type='event',
                content=memory_text,
                context=f'规则事件触发: {trigger_id}',
                importance=50.0,
                emotional_weight=abs(sum(sc.get('delta', 0) for sc in state_changes)) * 2,
                source_day=char.game_day,
                source_time=event_time,
                embedding=embedding_blob,
            )
            db.session.add(mem)
            db.session.commit()
        except Exception as e:
            logger.warning(f"[Memory] 事件记忆写入失败（不影响运行）: {e}")

        # 检查成就联动
        try:
            from backend.game.achievement_checker import check_event_achievements
            unlocked = check_event_achievements(char, trigger_id)
            if unlocked:
                logger.info(f"[ConditionMatcher] 事件 {trigger_id} 触发成就解锁: {[a.name for a in unlocked]}")
        except Exception as e:
            logger.warning(f"[ConditionMatcher] 成就检查失败（不影响运行）: {e}")

        # 设冷却
        for cond in template.get('conditions', []):
            if 'not_cooldown' in cond:
                self.cooldown.set_cooldown(
                    char.name,
                    cond.get('not_cooldown', trigger_id),
                    cond.get('hours', 24)
                )
                break

        # 清理该角色已过期的冷却记录，避免僵尸记录堆积
        try:
            self.cooldown.cleanup(char.name, char.game_day)
        except Exception as e:
            logger.warning(f"[ConditionMatcher] 冷却清理失败（不影响运行）: {e}")

        logger.info(f"[ConditionMatcher] 触发事件: {trigger_id} (第{char.game_day}天)")
        return event.to_dict()


def resolve_attr(char, attr):
    """模块级属性解析（供 evaluate_goals / 数值型成就复用派生与技能属性）。"""
    try:
        m = ConditionMatcher(char.name)
        return m._resolve_value(char, attr)
    except Exception:
        return None
