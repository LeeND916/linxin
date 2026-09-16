# -*- coding: utf-8 -*-
"""
用户数据目录 — arcs / world_settings 等角色数据 JSON 的统一存放位置。

存放在 Windows 当前登录用户的主目录下（跟随登录用户动态变化）：
    %USERPROFILE%\sim_life\arcs\{角色名}_arcs.json
    %USERPROFILE%\sim_life\world_settings\world_setting_{角色名}.json
    %USERPROFILE%\sim_life\world_settings\triggered_events_character\{角色名}.json

可通过环境变量 SIM_LIFE_USER_DATA 覆盖根目录（便于测试/迁移）。

首次导入时自动把旧仓库目录（backend/game/arcs、backend/game/world_settings、
backend/game/triggered_events_character）中尚未迁移的文件复制到新位置（不覆盖已存在文件），
保证平滑升级。
"""
import os
import shutil
import logging

logger = logging.getLogger(__name__)

# 根目录：默认 ~/sim_life，可用环境变量覆盖
USER_DATA_ROOT = os.environ.get(
    'SIM_LIFE_USER_DATA',
    os.path.join(os.path.expanduser('~'), 'sim_life')
)

ARCS_DIR = os.path.join(USER_DATA_ROOT, 'arcs')
WORLD_SETTINGS_DIR = os.path.join(USER_DATA_ROOT, 'world_settings')
# 角色专属事件模板目录（事件模板面板），存放于 world_settings 下
TRIGGERED_EVENTS_CHAR_DIR = os.path.join(WORLD_SETTINGS_DIR, 'triggered_events_character')

# 旧位置（仓库内），用于一次性自动迁移
_GAME_DIR = os.path.dirname(os.path.abspath(__file__))
_LEGACY_ARCS_DIR = os.path.join(_GAME_DIR, 'arcs')
_LEGACY_WORLD_SETTINGS_DIR = os.path.join(_GAME_DIR, 'world_settings')
_LEGACY_TRIGGERED_EVENTS_CHAR_DIR = os.path.join(_GAME_DIR, 'triggered_events_character')


def _migrate_legacy(old_dir: str, new_dir: str):
    """把旧目录中尚未存在于新目录的 JSON 复制过去（不覆盖、不删除旧文件）。"""
    if not os.path.isdir(old_dir):
        return
    try:
        os.makedirs(new_dir, exist_ok=True)
        for fname in os.listdir(old_dir):
            if not fname.endswith('.json'):
                continue
            dst = os.path.join(new_dir, fname)
            if not os.path.exists(dst):
                shutil.copy2(os.path.join(old_dir, fname), dst)
                logger.info(f"[UserData] 已迁移 {fname} -> {new_dir}")
    except OSError as e:
        logger.warning(f"[UserData] 迁移 {old_dir} 失败: {e}")


def ensure_dirs():
    """确保目录存在并完成一次性旧数据迁移。"""
    os.makedirs(ARCS_DIR, exist_ok=True)
    os.makedirs(WORLD_SETTINGS_DIR, exist_ok=True)
    os.makedirs(TRIGGERED_EVENTS_CHAR_DIR, exist_ok=True)
    _migrate_legacy(_LEGACY_ARCS_DIR, ARCS_DIR)
    _migrate_legacy(_LEGACY_WORLD_SETTINGS_DIR, WORLD_SETTINGS_DIR)
    _migrate_legacy(_LEGACY_TRIGGERED_EVENTS_CHAR_DIR, TRIGGERED_EVENTS_CHAR_DIR)


# 导入即初始化
ensure_dirs()
