"""数据库迁移：为 memos_config 表添加 last_synced_at 字段

用于增量同步，记录上次同步时间
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'game.db')

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 检查字段是否已存在
    cursor.execute("PRAGMA table_info(memos_config)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if 'last_synced_at' in columns:
        print("[OK] last_synced_at field already exists, no migration needed")
    else:
        print("-> Adding last_synced_at field...")
        cursor.execute("""
            ALTER TABLE memos_config 
            ADD COLUMN last_synced_at DATETIME
        """)
        conn.commit()
        print("[OK] last_synced_at field added successfully")
    
    conn.close()

if __name__ == '__main__':
    migrate()
