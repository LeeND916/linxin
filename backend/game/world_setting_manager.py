# -*- coding: utf-8 -*-
"""
世界观管理器 — 按角色加载/缓存世界观 JSON。

用法:
    from backend.game.world_setting_manager import WorldSettingManager
    ws = WorldSettingManager.get(char.name)
    print(ws.get('daily_rhythm'))
"""
import json
import os
import logging

logger = logging.getLogger(__name__)

# 世界观 JSON 存放目录（用户主目录 ~/sim_life/world_settings）
from backend.game.user_data_paths import WORLD_SETTINGS_DIR as _SETTINGS_DIR


class WorldSettingManager:
    """世界观管理器，按角色名加载缓存。"""

    _cache = {}  # character_name → dict

    @classmethod
    def _file_path(cls, character_name: str) -> str:
        return os.path.join(_SETTINGS_DIR, f'world_setting_{character_name}.json')

    @classmethod
    def get(cls, character_name: str) -> dict:
        """获取指定角色的世界观（带缓存）。"""
        if character_name in cls._cache:
            return cls._cache[character_name]

        file_path = cls._file_path(character_name)
        if not os.path.exists(file_path):
            logger.warning(f"[WorldSetting] 世界观文件不存在: {file_path}")
            return {}

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            cls._cache[character_name] = data
            return data
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"[WorldSetting] 加载失败: {e}")
            return {}

    @classmethod
    def save(cls, character_name: str, data: dict) -> bool:
        """保存世界观 JSON。"""
        file_path = cls._file_path(character_name)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            cls._cache[character_name] = data
            logger.info(f"[WorldSetting] 已保存 {character_name} 的世界观")
            return True
        except IOError as e:
            logger.error(f"[WorldSetting] 保存失败: {e}")
            return False

    @classmethod
    def clear_cache(cls):
        """清空缓存（用于热重载）。"""
        cls._cache.clear()
