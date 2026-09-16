# -*- coding: utf-8 -*-
"""数据库迁移：为 character 表添加 zimage_lora 字段

用途：z-image 工作流生图时按角色指定 LoRA。
  - 字段为空 → 使用默认 LoRA（pixel_art_style_z_image_turbo.safetensors）
  - 字段非空 → 在 ComfyUI 可用 LoRA 列表中做「精确匹配 → 包含匹配」，
              例如填 'yanyin' 可命中 yanyin_zimage_turbo_lora_v1_000002500.safetensors

匹配失败时回退默认 LoRA 并打 warning，不阻塞出图。
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'game.db')


def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(character)")
    columns = [row[1] for row in cursor.fetchall()]

    if 'zimage_lora' in columns:
        print("[OK] zimage_lora field already exists, no migration needed")
    else:
        print("-> Adding zimage_lora field...")
        cursor.execute("ALTER TABLE character ADD COLUMN zimage_lora VARCHAR(255)")
        conn.commit()
        print("[OK] zimage_lora field added successfully")

    conn.close()


if __name__ == '__main__':
    migrate()
