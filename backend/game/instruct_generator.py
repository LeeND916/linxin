# -*- coding: utf-8 -*-
"""
Instruct 文本生成器 — 拼接 character_base + 情绪槽位，生成 TTS Voice Design 指令。

依赖：
  - backend.game.emotion_slots (EMOTION_SLOTS, intensity_to_bucket)
  - 数据库 character 表的 character_base 列

不包含 CHARACTER_BASE_FALLBACK 字典兜底——数据库查不到直接报错。
"""

import sqlite3
import os
import logging

from backend.game.emotion_slots import EMOTION_SLOTS, intensity_to_bucket

logger = logging.getLogger('sim_life.tts_instruct')

# 数据库路径（与 config.py 中的 DATABASE_URL 一致）
_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "game.db"
)


def _get_character_base(character_name: str) -> str:
    """从数据库查询角色的 character_base 锚点文本。

    查不到时直接抛出 ValueError（不使用 fallback 字典）。
    """
    conn = sqlite3.connect(_DB_PATH)
    try:
        cursor = conn.execute(
            "SELECT character_base FROM character WHERE name = ?",
            (character_name,)
        )
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]
        raise ValueError(
            f"角色 '{character_name}' 的 character_base 未在数据库中配置。"
            f"请执行 ALTER TABLE + UPDATE 语句或检查角色名称是否正确。"
        )
    finally:
        conn.close()


def _get_character_gender(character_name: str) -> str:
    """从数据库查询角色性别，返回 'female' / 'male' / None。"""
    conn = sqlite3.connect(_DB_PATH)
    try:
        cursor = conn.execute(
            "SELECT gender FROM character WHERE name = ?",
            (character_name,)
        )
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]
        return None
    finally:
        conn.close()


_GENDER_LABEL = {
    "female": "女",
    "male": "男",
}


def build_instruct(character_name: str, emotion: str, intensity_bucket: str) -> str:
    """快路径：纯模板拼接，不涉及 DeepSeek。

    参数:
        character_name: 角色名称
        emotion: 10 种情绪之一
        intensity_bucket: 'intensity_low' / 'intensity_mid' / 'intensity_high'

    返回:
        完整的 instruct 文本，格式：
        "性别X，<character_base>此刻<emotion_slot>。"
    """
    emotion_text = EMOTION_SLOTS[emotion][intensity_bucket]
    base = _get_character_base(character_name)

    gender = _get_character_gender(character_name)
    prefix = ""
    if gender and gender in _GENDER_LABEL:
        prefix = f"性别{_GENDER_LABEL[gender]}，"

    return f"{prefix}{base}此刻{emotion_text}。"


def safe_build_instruct(character_name: str, result: dict) -> str:
    """含 fallback 的 instruct 生成入口。

    参数:
        character_name: 角色名称
        result: 分类器输出字典 {"emotion": str, "intensity": float, "confidence": float}

    Fallback 规则:
        - confidence < 0.6 → 强制 neutral / intensity_mid
        - 分类器返回非 10 候选词 → 强制 neutral / intensity_mid

    返回:
        完整的 instruct 文本
    """
    # 检查置信度
    if result.get("confidence", 0.0) < 0.6:
        logger.info(
            f"[Instruct] confidence={result.get('confidence')} < 0.6, "
            f"fallback to neutral/intensity_mid"
        )
        return build_instruct(character_name, "neutral", "intensity_mid")

    # 检查情绪是否合法
    emotion = result.get("emotion", "")
    if emotion not in EMOTION_SLOTS:
        logger.info(
            f"[Instruct] unknown emotion='{emotion}', "
            f"fallback to neutral/intensity_mid"
        )
        return build_instruct(character_name, "neutral", "intensity_mid")

    bucket = intensity_to_bucket(result.get("intensity", 0.3))
    return build_instruct(character_name, emotion, bucket)


def modify_instruct(base_instruct: str, deepseek_phrase: str) -> str:
    """逗号拼接 DeepSeek 语气修饰短语，空字符串不阻塞。

    参数:
        base_instruct: safe_build_instruct 的输出
        deepseek_phrase: DeepSeek 生成的 ≤20 字语气修饰短语

    返回:
        base_instruct + "，" + deepseek_phrase（如果短语非空）
        否则直接返回 base_instruct
    """
    if not deepseek_phrase or not deepseek_phrase.strip():
        return base_instruct
    return base_instruct + "，" + deepseek_phrase.strip()
