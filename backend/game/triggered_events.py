# -*- coding: utf-8 -*-
"""
事件模板加载器 — 从 triggered_events.json 加载通用规则。

用法:
    from backend.game.triggered_events import load_event_templates
    templates = load_event_templates()  # 通用模板列表
"""
import json
import os
import logging

logger = logging.getLogger(__name__)

# 通用规则文件路径
_EVENTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'triggered_events.json')
# 角色专属规则目录：随用户数据迁移至 %USERPROFILE%/sim_life/world_settings/triggered_events_character
from backend.game.user_data_paths import TRIGGERED_EVENTS_CHAR_DIR
_CHARACTER_EVENTS_DIR = TRIGGERED_EVENTS_CHAR_DIR


def load_event_templates() -> list:
    """加载通用事件模板列表。

    注意：通用静态事件池已于方案 D（2026-07-30）移除。所有随机事件改由
    LLM 兜底生成（见 event.llm_fallback_event）。此处固定返回空列表，
    triggered_events.json 仅作为历史备份保留，不再加载、不再参与运行。
    """
    return []


def load_character_templates(character_name: str) -> list:
    """加载角色专属事件模板列表。"""
    file_path = os.path.join(_CHARACTER_EVENTS_DIR, f'{character_name}.json')
    if not os.path.exists(file_path):
        return []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"[TriggeredEvents] 加载角色专属规则失败: {e}")
        return []


def load_all_templates(character_name: str = None) -> list:
    """加载全部模板（通用 + 角色专属）。"""
    templates = load_event_templates()
    if character_name:
        char_templates = load_character_templates(character_name)
        templates.extend(char_templates)
    return templates


def save_character_templates(character_name: str, templates: list) -> bool:
    """保存角色专属事件模板。"""
    file_path = os.path.join(_CHARACTER_EVENTS_DIR, f'{character_name}.json')
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(templates, f, ensure_ascii=False, indent=2)
        logger.info(f"[TriggeredEvents] 已保存 {character_name} 的 {len(templates)} 条专属规则")
        return True
    except IOError as e:
        logger.error(f"[TriggeredEvents] 保存角色专属规则失败: {e}")
        return False


def get_character_template_ids(character_name: str) -> list:
    """获取角色专属模板的 ID 列表。"""
    templates = load_character_templates(character_name)
    return [t.get('trigger_id', '') for t in templates if t.get('trigger_id')]
