"""Flask 应用主入口"""
import os
import sys

# === 修复 stdout/stderr 缓冲（Flask debug reloader 子进程可能丢失） ===
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass
try:
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# 清除损坏的 SSL 密钥日志路径（由抓包工具残留导致，会阻断所有 HTTPS 连接）
# 保留清除动作以保 HTTPS 正常，但不再打印警告
if 'SSLKEYLOGFILE' in os.environ:
    os.environ.pop('SSLKEYLOGFILE')

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
import time

# === 全局日志系统：统一输出到 stdout ===
# force=True 覆盖已有配置（如 Flask/Werkzeug 内部设置）
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(name)-24s] %(levelname)-7s %(message)s',
    datefmt='%H:%M:%S',
    stream=sys.stdout,
    force=True,
)
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('backend.game.memos_client').setLevel(logging.ERROR)
logging.getLogger('backend.game.memos_import').setLevel(logging.ERROR)
logging.getLogger('backend.game.memos_sync').setLevel(logging.ERROR)

from flask import Flask, request, jsonify
from backend.config import Config
from backend.models import db


def _migrate_db(app, db):
    """增量迁移：为已有表添加缺失的列（SQLite 兼容）"""
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)

    # character 表迁移
    char_columns = [col['name'] for col in inspector.get_columns('character')]
    char_migrations = {
        'relationship_status': "ALTER TABLE character ADD COLUMN relationship_status VARCHAR(16) DEFAULT 'friends'",
        'game_minute': "ALTER TABLE character ADD COLUMN game_minute INTEGER DEFAULT 0",
        'game_second': "ALTER TABLE character ADD COLUMN game_second INTEGER DEFAULT 0",
        # P0-1 角色模块化扩展字段
        'preset_id': "ALTER TABLE character ADD COLUMN preset_id VARCHAR(32) DEFAULT 'p1'",
        'profile_json': "ALTER TABLE character ADD COLUMN profile_json TEXT DEFAULT ''",
        'major': "ALTER TABLE character ADD COLUMN major VARCHAR(64) DEFAULT ''",
        'identity_label': "ALTER TABLE character ADD COLUMN identity_label VARCHAR(32) DEFAULT '女大学生'",
        'education': "ALTER TABLE character ADD COLUMN education VARCHAR(32) DEFAULT ''",
        'hometown': "ALTER TABLE character ADD COLUMN hometown VARCHAR(64) DEFAULT ''",
        'family': "ALTER TABLE character ADD COLUMN family VARCHAR(32) DEFAULT ''",
        'economic': "ALTER TABLE character ADD COLUMN economic VARCHAR(16) DEFAULT ''",
        'hobbies': "ALTER TABLE character ADD COLUMN hobbies VARCHAR(128) DEFAULT ''",
        'appearance': "ALTER TABLE character ADD COLUMN appearance VARCHAR(128) DEFAULT ''",
        'dream_primary': "ALTER TABLE character ADD COLUMN dream_primary VARCHAR(128) DEFAULT ''",
        'dream_secondary': "ALTER TABLE character ADD COLUMN dream_secondary VARCHAR(128) DEFAULT ''",
        'dream_motivation': "ALTER TABLE character ADD COLUMN dream_motivation VARCHAR(256) DEFAULT ''",
        'personality_type': "ALTER TABLE character ADD COLUMN personality_type VARCHAR(64) DEFAULT '温柔'",
        'personality_tone': "ALTER TABLE character ADD COLUMN personality_tone VARCHAR(64) DEFAULT '自然日常'",
        'social_tendency': "ALTER TABLE character ADD COLUMN social_tendency FLOAT DEFAULT 6.0",
        'emotional_stability': "ALTER TABLE character ADD COLUMN emotional_stability FLOAT DEFAULT 6.0",
        'expression_style': "ALTER TABLE character ADD COLUMN expression_style VARCHAR(32) DEFAULT '自然'",
        'relationship_history': "ALTER TABLE character ADD COLUMN relationship_history VARCHAR(32) DEFAULT ''",
        # ── AI伴侣系统升级 — 阶段一：玩家专属档案字段 ──
        'player_identity': "ALTER TABLE character ADD COLUMN player_identity VARCHAR(64) DEFAULT '导师'",
        'player_nickname': "ALTER TABLE character ADD COLUMN player_nickname VARCHAR(32) DEFAULT ''",
        'interaction_style': "ALTER TABLE character ADD COLUMN interaction_style TEXT DEFAULT ''",
        # ── AI伴侣系统升级 — 阶段二：情绪引擎字段 ──
        'emotion_history': "ALTER TABLE character ADD COLUMN emotion_history TEXT DEFAULT '[]'",
        'mental_initialized': "ALTER TABLE character ADD COLUMN mental_initialized BOOLEAN DEFAULT 0",
        # 目标解锁规则 JSON
        'goal_rules': "ALTER TABLE character ADD COLUMN goal_rules TEXT DEFAULT '{}'",
        # 肖像生成固定种子（替代 outfit_seed 的肖像用途）
        'portrait_seed': "ALTER TABLE character ADD COLUMN portrait_seed INTEGER DEFAULT 0",
        # 聊天头像（由角色肖像导入到 /static/avatars/{id}.png）
        'avatar': "ALTER TABLE character ADD COLUMN avatar VARCHAR(512) DEFAULT ''",
    }
    for col_name, sql in char_migrations.items():
        if col_name not in char_columns:
            with app.app_context():
                db.session.execute(text(sql))
                db.session.commit()
            print(f"[Migration] character 表已添加列: {col_name}")

    # 列重命名：mentor_*（女主×玩家养成数值）→ player_*（仅桶B，不动 relation_type='mentor' 师徒关系）
    mentor_renames = [
        ('mentor_trust', 'player_trust'),
        ('mentor_affection', 'player_affection'),
        ('mentor_respect', 'player_respect'),
        ('mentor_intimacy', 'player_intimacy'),
    ]
    for old, new in mentor_renames:
        if old in char_columns and new not in char_columns:
            with app.app_context():
                db.session.execute(text(f"ALTER TABLE character RENAME COLUMN {old} TO {new}"))
                db.session.commit()
            print(f"[Migration] character 表列重命名: {old} -> {new}")

    # appearances 表迁移：保留正数角色专用记录，同时允许 0/-1 表示全局玩家默认外貌
    try:
        appearance_columns = [col['name'] for col in inspector.get_columns('appearances')]
        appearance_migrations = {
            'player_gender': "ALTER TABLE appearances ADD COLUMN player_gender VARCHAR(16) DEFAULT ''",
            'player_keywords': "ALTER TABLE appearances ADD COLUMN player_keywords VARCHAR(256) DEFAULT ''",
            'player_summary': "ALTER TABLE appearances ADD COLUMN player_summary TEXT DEFAULT ''",
            'player_face_shape': "ALTER TABLE appearances ADD COLUMN player_face_shape TEXT DEFAULT ''",
            'player_eyes': "ALTER TABLE appearances ADD COLUMN player_eyes TEXT DEFAULT ''",
            'player_eyebrows': "ALTER TABLE appearances ADD COLUMN player_eyebrows TEXT DEFAULT ''",
            'player_nose': "ALTER TABLE appearances ADD COLUMN player_nose TEXT DEFAULT ''",
            'player_mouth': "ALTER TABLE appearances ADD COLUMN player_mouth TEXT DEFAULT ''",
            'player_hairstyle': "ALTER TABLE appearances ADD COLUMN player_hairstyle TEXT DEFAULT ''",
            'player_skin': "ALTER TABLE appearances ADD COLUMN player_skin TEXT DEFAULT ''",
        }
        for col_name, sql in appearance_migrations.items():
            if col_name not in appearance_columns:
                with app.app_context():
                    db.session.execute(text(sql))
                    db.session.commit()
                print(f"[Migration] appearances 表已添加列: {col_name}")
        with app.app_context():
            from backend.models import Appearance, Character
            defaults = [
                dict(character_id=0, player_gender='male', player_keywords='成熟学长型,自然亲和,略带可爱感', player_summary='男性玩家的全局默认外貌，成熟学长气质中保留自然亲和与轻微可爱感。', player_face_shape='脸型：轮廓清晰的椭圆脸，下颌线利落但不过分凌厉。', player_eyes='眼睛：眉眼沉静有神，目光温和清醒。', player_eyebrows='眉毛：自然平直，眉峰柔和。', player_nose='鼻子：鼻梁端正，鼻头自然。', player_mouth='嘴巴：唇形清晰，笑起来亲和。', player_hairstyle='发型：整洁自然的短发，略有松弛感。', player_skin='皮肤：自然健康肤色，保留真实纹理。'),
                dict(character_id=-1, player_gender='female', player_keywords='成熟学姐型,自然亲和,略带可爱感', player_summary='女性玩家的全局默认外貌，成熟学姐气质中保留自然亲和与轻微可爱感。', player_face_shape='脸型：柔和的鹅蛋脸轮廓，线条清晰自然。', player_eyes='眼睛：眼神清亮沉静，带有温和亲和力。', player_eyebrows='眉毛：自然平眉，眉形柔和。', player_nose='鼻子：鼻梁端正，鼻型秀气自然。', player_mouth='嘴巴：唇形清晰，笑意自然克制。', player_hairstyle='发型：整洁自然的中短发，带轻松感。', player_skin='皮肤：自然匀净肤色，保留真实纹理。'),
            ]
            for values in defaults:
                if not Appearance.query.filter_by(character_id=values['character_id']).first():
                    db.session.add(Appearance(**values))
            shen = Character.query.filter_by(name='沈念').first()
            if shen and not Appearance.query.filter_by(character_id=shen.id).first():
                db.session.add(Appearance(character_id=shen.id, player_gender='female', player_keywords='沈念专用女性玩家,学姐称呼', player_summary='沈念专用的女性玩家外貌，玩家被沈念称为学姐。', player_face_shape='脸型：成熟柔和的鹅蛋脸，轮廓端庄。', player_eyes='眼睛：清亮温和，带有可靠感。', player_eyebrows='眉毛：自然柔和。', player_nose='鼻子：鼻梁端正自然。', player_mouth='嘴巴：唇形柔和，笑意亲切。', player_hairstyle='发型：自然整洁，略带松弛感。', player_skin='皮肤：自然匀净，保留真实纹理。'))
            db.session.commit()
    except Exception as e:
        print(f"[Migration] appearances 玩家外貌迁移失败（不影响运行）: {e}")

    # friend 表迁移
    friend_columns = [col['name'] for col in inspector.get_columns('friend')]
    friend_migrations = {
        'character_name': "ALTER TABLE friend ADD COLUMN character_name VARCHAR(32) DEFAULT ''",
    }
    for col_name, sql in friend_migrations.items():
        if col_name not in friend_columns:
            with app.app_context():
                db.session.execute(text(sql))
                db.session.commit()
            print(f"[Migration] friend 表已添加列: {col_name}")

    # event_log 表迁移
    el_columns = [col['name'] for col in inspector.get_columns('event_log')]
    el_migrations = {
        'location': "ALTER TABLE event_log ADD COLUMN location VARCHAR(128) DEFAULT ''",
    }
    for col_name, sql in el_migrations.items():
        if col_name not in el_columns:
            with app.app_context():
                db.session.execute(text(sql))
                db.session.commit()
            print(f"[Migration] event_log 表已添加列: {col_name}")

    # ── 事件系统重构：Friend 表加关系类型与负面维度 ──
    try:
        friend_columns = [col['name'] for col in inspector.get_columns('friend')]
        friend_v3_migrations = {
            'relation_type': "ALTER TABLE friend ADD COLUMN relation_type VARCHAR(16) DEFAULT 'friend'",
            'rivalry': "ALTER TABLE friend ADD COLUMN rivalry FLOAT DEFAULT 0.0",
            'hostility': "ALTER TABLE friend ADD COLUMN hostility FLOAT DEFAULT 0.0",
            'fear': "ALTER TABLE friend ADD COLUMN fear FLOAT DEFAULT 0.0",
        }
        for col_name, sql in friend_v3_migrations.items():
            if col_name not in friend_columns:
                with app.app_context():
                    db.session.execute(text(sql))
                    db.session.commit()
                print(f"[Migration] friend 表已添加列: {col_name}")
    except Exception as e:
        print(f"[Migration] friend 表迁移失败（不影响运行）: {e}")

    # ── 事件系统重构 v3.1：Friend 表加关系内部状态字段 ──
    try:
        friend_columns = [col['name'] for col in inspector.get_columns('friend')]
        friend_v3_state_migrations = {
            'loneliness': "ALTER TABLE friend ADD COLUMN loneliness FLOAT DEFAULT 50.0",
            'happiness': "ALTER TABLE friend ADD COLUMN happiness FLOAT DEFAULT 50.0",
            'stress': "ALTER TABLE friend ADD COLUMN stress FLOAT DEFAULT 30.0",
            'mood': "ALTER TABLE friend ADD COLUMN mood FLOAT DEFAULT 60.0",
            'energy': "ALTER TABLE friend ADD COLUMN energy FLOAT DEFAULT 70.0",
            'clarity': "ALTER TABLE friend ADD COLUMN clarity FLOAT DEFAULT 60.0",
        }
        for col_name, sql in friend_v3_state_migrations.items():
            if col_name not in friend_columns:
                with app.app_context():
                    db.session.execute(text(sql))
                    db.session.commit()
                print(f"[Migration] friend 表已添加列: {col_name}")
    except Exception as e:
        print(f"[Migration] friend 表状态字段迁移失败（不影响运行）: {e}")

    # ── 更新现有 Friend 的 relation_type（按 role 关键词推断）──
    try:
        from backend.models import Friend
        _rel_map_migrate = [
            (["导师","教授","学长","助教","老师","师傅","指导"], "mentor"),
            (["闺蜜","室友","好友","闺"], "friend"),
            (["搭档","同事","律所","编程搭档"], "colleague"),
            (["后辈","学妹","徒弟"], "protege"),
            (["前男友","前任"], "ex_boyfriend"),
            (["对手","劲敌","竞争对手"], "rival"),
            (["家人","母亲","妈妈","爸爸","父亲","哥哥","弟弟","姐姐","妹妹","爷爷","奶奶","外公","外婆","表"], "family"),
            (["客户","委托人"], "client"),
            (["敌人","仇人"], "enemy"),
        ]
        migrated = 0
        for f in Friend.query.all():
            role_str = (f.role or "")
            cur = f.relation_type or "friend"
            new_t = "friend"
            for keywords, rtype in _rel_map_migrate:
                if any(k in role_str for k in keywords):
                    new_t = rtype
                    break
            if new_t != cur and (cur == 'friend' or cur == 'neutral'):
                f.relation_type = new_t
                migrated += 1
        if migrated:
            db.session.commit()
            print(f"[Migration] 已更新 {migrated} 条 Friend 的 relation_type")
    except Exception as e:
        print(f"[Migration] relation_type 更新失败（不影响运行）: {e}")

    # ── 事件系统重构：EventLog 加 event_category ──
    try:
        el_columns = [col['name'] for col in inspector.get_columns('event_log')]
        if 'event_category' not in el_columns:
            with app.app_context():
                db.session.execute(text("ALTER TABLE event_log ADD COLUMN event_category VARCHAR(32) DEFAULT ''"))
                db.session.commit()
            print("[Migration] event_log 表已添加列: event_category")
    except Exception as e:
        print(f"[Migration] event_log event_category 迁移失败（不影响运行）: {e}")

    # ── 事件系统重构：新建 EventCooldown 表 ──
    try:
        tables = inspector.get_table_names()
        if 'event_cooldown' not in tables:
            with app.app_context():
                db.session.execute(text("""
                    CREATE TABLE event_cooldown (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        character_name VARCHAR(32) NOT NULL,
                        trigger_id VARCHAR(64) NOT NULL,
                        game_day_expires INTEGER NOT NULL
                    )
                """))
                db.session.commit()
            print("[Migration] 已创建 event_cooldown 表")
    except Exception as e:
        print(f"[Migration] event_cooldown 表创建失败（不影响运行）: {e}")

    # ── 角色活动地图：LLM 生成的专属活动 ──
    try:
        cam_columns = [col['name'] for col in inspector.get_columns('character_activity_map')]
        if 'custom_activities' not in cam_columns:
            with app.app_context():
                db.session.execute(text("ALTER TABLE character_activity_map ADD COLUMN custom_activities TEXT DEFAULT '[]'"))
                db.session.commit()
            print("[Migration] character_activity_map 表已添加列: custom_activities")
    except Exception as e:
        print(f"[Migration] character_activity_map custom_activities 迁移失败（不影响运行）: {e}")

    # ── 成就系统：LLM 初始化解锁规则字段（progress_source / unlock_event）──
    try:
        ach_columns = [col['name'] for col in inspector.get_columns('achievement')]
        ach_migrations = {
            'progress_source': "ALTER TABLE achievement ADD COLUMN progress_source TEXT DEFAULT ''",
            'unlock_event': "ALTER TABLE achievement ADD COLUMN unlock_event VARCHAR(64) DEFAULT ''",
        }
        for col_name, sql in ach_migrations.items():
            if col_name not in ach_columns:
                with app.app_context():
                    db.session.execute(text(sql))
                    db.session.commit()
                print(f"[Migration] achievement 表已添加列: {col_name}")
    except Exception as e:
        print(f"[Migration] achievement 表迁移失败（不影响运行）: {e}")

    # ── 事件→成就绑定表（持久化，替代内存 _DYNAMIC_ACHIEVEMENT_MAP）──
    try:
        tables = inspector.get_table_names()
        with app.app_context():
            from backend.models import EventAchievementBinding
            if 'event_achievement_binding' not in tables:
                EventAchievementBinding.__table__.create(db.engine)
                print("[Migration] 已创建 event_achievement_binding 表")
    except Exception as e:
        print(f"[Migration] event_achievement_binding 表创建失败（不影响运行）: {e}")



    # ── NerdMemo 表迁移 ──
    try:
        tables = inspector.get_table_names()
        if 'nerd_memo' not in tables:
            with app.app_context():
                from backend.models import NerdMemo
                NerdMemo.__table__.create(db.engine)
            print("[Migration] 已创建 nerd_memo 表")
    except Exception as e:
        print(f"[Migration] nerd_memo 表创建失败（不影响运行）: {e}")

    # ── 职业关系规则表（数据驱动的职业变量源，供职业场景豁免判定）──
    try:
        tables = inspector.get_table_names()
        if 'profession_rule' not in tables:
            with app.app_context():
                from backend.models import ProfessionRule
                ProfessionRule.__table__.create(db.engine)
                print("[Migration] 已创建 profession_rule 表")
    except Exception as e:
        print(f"[Migration] profession_rule 表创建失败（不影响运行）: {e}")

    # ── GameSetting KV 设置表 ──
    try:
        tables = inspector.get_table_names()
        if 'game_setting' not in tables:
            with app.app_context():
                from backend.models import GameSetting
                GameSetting.__table__.create(db.engine)
                print("[Migration] 已创建 game_setting 表")
    except Exception as e:
        print(f"[Migration] game_setting 表创建失败（不影响运行）: {e}")

    # ── ComfyUIWorkflow 扩展列（name / node_map / is_default）──
    try:
        cw_columns = [col['name'] for col in inspector.get_columns('comfyui_workflow')]
        cw_migrations = {
            'name': "ALTER TABLE comfyui_workflow ADD COLUMN name VARCHAR(64) DEFAULT ''",
            'node_map': "ALTER TABLE comfyui_workflow ADD COLUMN node_map TEXT DEFAULT ''",
            'is_default': "ALTER TABLE comfyui_workflow ADD COLUMN is_default BOOLEAN DEFAULT 0",
        }
        for col_name, sql in cw_migrations.items():
            if col_name not in cw_columns:
                with app.app_context():
                    db.session.execute(text(sql))
                    db.session.commit()
                print(f"[Migration] comfyui_workflow 表已添加列: {col_name}")
    except Exception as e:
        print(f"[Migration] comfyui_workflow 扩展列迁移失败（不影响运行）: {e}")

    # ── NewsCache 表迁移（近期资讯功能）──
    try:
        tables = inspector.get_table_names()
        if 'news_cache' not in tables:
            with app.app_context():
                from backend.models import NewsCache
                NewsCache.__table__.create(db.engine)
            print("[Migration] 已创建 news_cache 表")
    except Exception as e:
        print(f"[Migration] news_cache 表创建失败（不影响运行）: {e}")

    # ── NewsSourceConfig + NewsSettings 表迁移 ──
    try:
        tables = inspector.get_table_names()
        with app.app_context():
            from backend.models import NewsSourceConfig, NewsSettings
            if 'news_source_config' not in tables:
                NewsSourceConfig.__table__.create(db.engine)
                print("[Migration] 已创建 news_source_config 表")
                # 写入默认源
                _DEFAULT_SOURCES = [
                    {'name': '中新网即时', 'source_type': 'rss', 'api_url': 'https://www.chinanews.com.cn/rss/scroll-news.xml', 'category': 'general', 'sort_order': 1},
                    {'name': '中新网IT', 'source_type': 'rss', 'api_url': 'https://www.chinanews.com.cn/rss/it.xml', 'category': 'tech', 'sort_order': 2},
                    {'name': '中新网教育', 'source_type': 'rss', 'api_url': 'https://www.chinanews.com.cn/rss/edu.xml', 'category': 'edu', 'sort_order': 3},
                    {'name': '人民网-国内', 'source_type': 'rss', 'api_url': 'http://www.people.com.cn/rss/politics.xml', 'category': 'general', 'sort_order': 4},
                    {'name': '人民网-国际', 'source_type': 'rss', 'api_url': 'http://www.people.com.cn/rss/world.xml', 'category': 'world', 'sort_order': 5},
                    {'name': '人民网-文化', 'source_type': 'rss', 'api_url': 'http://www.people.com.cn/rss/culture.xml', 'category': 'culture', 'sort_order': 6},
                    {'name': '人民网-健康', 'source_type': 'rss', 'api_url': 'http://www.people.com.cn/rss/health.xml', 'category': 'health', 'sort_order': 7},
                    {'name': '人民网-教育', 'source_type': 'rss', 'api_url': 'http://www.people.com.cn/rss/edu.xml', 'category': 'edu', 'sort_order': 8},
                    {'name': '60s读懂世界', 'source_type': '60s', 'api_url': 'https://60s.viki.moe/v2/60s', 'category': 'general', 'sort_order': 9},
                ]
                for s in _DEFAULT_SOURCES:
                    db.session.add(NewsSourceConfig(**s))
                db.session.commit()
                print(f"[Migration] 已写入 {len(_DEFAULT_SOURCES)} 个默认新闻源")
            if 'news_settings' not in tables:
                NewsSettings.__table__.create(db.engine)
                db.session.add(NewsSettings(id=1))
                db.session.commit()
                print("[Migration] 已创建 news_settings 表（默认配置）")
    except Exception as e:
        print(f"[Migration] news_source_config/news_settings 表创建失败（不影响运行）: {e}")

    # ── 任务系统改造：Mission 相关表 + Character/Friend/Achievement 扩展 ──
    try:
        tables = inspector.get_table_names()
        with app.app_context():
            if 'mission' not in tables:
                from backend.models import Mission
                Mission.__table__.create(db.engine)
                print("[Migration] 已创建 mission 表")
            if 'mission_archive' not in tables:
                from backend.models import MissionArchive
                MissionArchive.__table__.create(db.engine)
                print("[Migration] 已创建 mission_archive 表")

        # mission_archive 新字段：is_failed（到期未达成而失败结束的标记）
        try:
            ma_columns = [col['name'] for col in inspector.get_columns('mission_archive')]
            if 'is_failed' not in ma_columns:
                with app.app_context():
                    db.session.execute(text("ALTER TABLE mission_archive ADD COLUMN is_failed BOOLEAN DEFAULT 0"))
                    db.session.commit()
                print("[Migration] mission_archive 表已添加列: is_failed")
        except Exception as e:
            print(f"[Migration] mission_archive.is_failed 迁移失败（不影响运行）: {e}")

        # Character 新字段（任务系统扩展；use_mission_system 已移除，统一走 MissionManager）
        char_columns = [col['name'] for col in inspector.get_columns('character')]
        char_mission_migrations = {
            'planned_location': "ALTER TABLE character ADD COLUMN planned_location VARCHAR(64) DEFAULT ''",
            'has_pending_mission': "ALTER TABLE character ADD COLUMN has_pending_mission BOOLEAN DEFAULT 0",
        }
        for col_name, sql in char_mission_migrations.items():
            if col_name not in char_columns:
                with app.app_context():
                    db.session.execute(text(sql))
                    db.session.commit()
                print(f"[Migration] character 表已添加列: {col_name}")

        # 清理旧 active_case 链路（任务系统已完全替代 CaseManager）
        try:
            with app.app_context():
                if 'active_case' in tables:
                    db.session.execute(text("DROP TABLE IF EXISTS active_case"))
                    db.session.commit()
                    print("[Migration] 已删除 active_case 表")
                db.session.execute(text("DELETE FROM event_log WHERE event_type='case' OR event_category='case'"))
                db.session.commit()
                if 'use_mission_system' in char_columns:
                    db.session.execute(text("ALTER TABLE character DROP COLUMN use_mission_system"))
                    db.session.commit()
                    print("[Migration] 已移除 character.use_mission_system 列")
        except Exception as e:
            print(f"[Migration] 清理旧 case 链路失败（不影响运行）: {e}")

        # Friend mission_id
        friend_columns = [col['name'] for col in inspector.get_columns('friend')]
        if 'mission_id' not in friend_columns:
            with app.app_context():
                db.session.execute(text("ALTER TABLE friend ADD COLUMN mission_id INTEGER"))
                db.session.commit()
            print("[Migration] friend 表已添加列: mission_id")

        # Achievement mission_id + step_size
        ach_columns = [col['name'] for col in inspector.get_columns('achievement')]
        ach_mission_migrations = {
            'step_size': "ALTER TABLE achievement ADD COLUMN step_size FLOAT DEFAULT 10.0",
            'mission_id': "ALTER TABLE achievement ADD COLUMN mission_id INTEGER",
            'trigger_conditions': "ALTER TABLE achievement ADD COLUMN trigger_conditions TEXT",
        }
        for col_name, sql in ach_mission_migrations.items():
            if col_name not in ach_columns:
                with app.app_context():
                    db.session.execute(text(sql))
                    db.session.commit()
                print(f"[Migration] achievement 表已添加列: {col_name}")

        # friend.gender 存量数据归一化：中文/杂项 → 英文 male/female（前端展示时映射中文）
        with app.app_context():
            r1 = db.session.execute(text(
                "UPDATE friend SET gender='male' WHERE gender IN ('男','男性','M','m','boy','man')"))
            r2 = db.session.execute(text(
                "UPDATE friend SET gender='female' WHERE gender NOT IN ('male','female') OR gender IS NULL"))
            db.session.commit()
            if (r1.rowcount or 0) + (r2.rowcount or 0) > 0:
                print(f"[Migration] friend.gender 归一化: male={r1.rowcount}, female={r2.rowcount}")

        # ── 五幕改造：Mission 加 fired_events + phase_name；MissionArchive 加 3 字段 ──
        mission_columns = [col['name'] for col in inspector.get_columns('mission')]
        mission_v5_migrations = {
            'fired_events': "ALTER TABLE mission ADD COLUMN fired_events TEXT DEFAULT '[]'",
            'phase_name': "ALTER TABLE mission ADD COLUMN phase_name TEXT DEFAULT ''",
            'rendered_prompt': "ALTER TABLE mission ADD COLUMN rendered_prompt TEXT DEFAULT ''",
        }
        for col_name, sql in mission_v5_migrations.items():
            if col_name not in mission_columns:
                with app.app_context():
                    db.session.execute(text(sql))
                    db.session.commit()
                print(f"[Migration] mission 表已添加列: {col_name}")

        # 旧 world_prompt 值拷到 phase_name（留个记录，语义不同但不浪费）
        try:
            with app.app_context():
                db.session.execute(text(
                    "UPDATE mission SET phase_name = world_prompt "
                    "WHERE phase_name = '' AND world_prompt IS NOT NULL AND world_prompt != ''"))
                db.session.commit()
        except Exception:
            pass

        ma_columns = [col['name'] for col in inspector.get_columns('mission_archive')]
        ma_v5_migrations = {
            'mission_summary': "ALTER TABLE mission_archive ADD COLUMN mission_summary TEXT DEFAULT ''",
            'npc_interactions': "ALTER TABLE mission_archive ADD COLUMN npc_interactions TEXT DEFAULT ''",
            'player_relationship_change': "ALTER TABLE mission_archive ADD COLUMN player_relationship_change TEXT DEFAULT ''",
        }
        for col_name, sql in ma_v5_migrations.items():
            if col_name not in ma_columns:
                with app.app_context():
                    db.session.execute(text(sql))
                    db.session.commit()
                print(f"[Migration] mission_archive 表已添加列: {col_name}")

    except Exception as e:
        print(f"[Migration] 任务系统改造迁移失败（不影响运行）: {e}")


def create_app(config_class=Config):
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend', 'templates'),
        static_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend', 'static'),
        static_url_path='/static'
    )

    app.config.from_object(config_class)
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

    # 确保 data 目录存在
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
    os.makedirs(data_dir, exist_ok=True)

    # 初始化数据库
    db.init_app(app)

    with app.app_context():
        db.create_all()
        # 数据库迁移：为已有的 character 表添加新列
        _migrate_db(app, db)

        # ── 预置默认小模型配置（指向本地 LM Studio 1.5B）──
        # 地址来自 Config.SMALL_MODEL_URL（env → local_config.py → localhost 默认）
        try:
            from backend.models import SmallModelConfig
            if SmallModelConfig.query.count() == 0:
                db.session.add(SmallModelConfig(
                    name='本地 LM Studio 1.5B',
                    api_url=app.config.get('SMALL_MODEL_URL') or 'http://127.0.0.1:10039/v1',
                    api_key='',
                    model_name='qwen2.5-1.5b-instruct',
                    is_active=True,
                ))
                db.session.commit()
                print("[Startup] 已预置默认小模型配置（small_model_config）")
        except Exception as e:
            app.logger.warning(f"[Startup] 预置小模型配置失败（不影响运行）: {e}")

        # ── 自动补齐记忆 embedding：后台执行，仅调用 LM Studio，不阻塞启动/聊天 ──
        try:
            from backend.game.memory import schedule_embedding_backfill
            schedule_embedding_backfill(app)
        except Exception as e:
            app.logger.warning(f"[Startup] 记忆 embedding 补偿任务启动失败（不影响运行）: {e}")

    # ── NerdMemo：启动增量同步线程（全量导入已移除，由增量同步自动处理）──
    try:
        from backend.game.memos_sync import start_memos_sync
        interval = app.config.get('MEMOS_SYNC_INTERVAL', 30)
        start_delay = app.config.get('MEMOS_SYNC_START_DELAY', 60)
        start_memos_sync(app, interval=interval, start_delay=start_delay)
        app.logger.info(f"[Startup] NerdMemo 增量同步线程已启动（间隔 {interval}s，延迟 {start_delay}s）")
    except Exception as e:
        app.logger.warning(f"[Startup] NerdMemo 增量同步启动失败（不影响运行）: {e}")

    # ── 近期资讯：后台每小时抓取新闻写缓存（路线①，独立于游戏 tick）──
    try:
        from backend.game.news_service import start_news_fetcher
        news_interval = app.config.get('NEWS_FETCH_INTERVAL', 3600)
        news_start_delay = app.config.get('NEWS_FETCH_START_DELAY', 120)
        start_news_fetcher(app, interval=news_interval, start_delay=news_start_delay)
        app.logger.info(f"[Startup] 近期资讯抓取线程已启动（间隔 {news_interval}s，延迟 {news_start_delay}s）")
    except Exception as e:
        app.logger.warning(f"[Startup] 近期资讯抓取线程启动失败（不影响运行）: {e}")

    # 注册蓝图
    from backend.routes.api import api_bp
    from backend.routes.pages import pages_bp
    from backend.routes.character_api import character_api_bp
    from backend.routes.tts import tts_bp  # AI伴侣系统升级 — 阶段三：TTS
    from backend.routes.portrait import portrait_bp  # AI伴侣系统升级 — 阶段三：ComfyUI
    from backend.routes.photo import photo_bp, vlm_bp  # 聊天生图 + 视觉回看配置
    app.register_blueprint(api_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(character_api_bp)
    app.register_blueprint(tts_bp)  # TTS 语音合成
    app.register_blueprint(portrait_bp)  # ComfyUI 肖像生成
    app.register_blueprint(photo_bp)  # 聊天生图记录/手动触发
    app.register_blueprint(vlm_bp)  # 视觉回看（VLM）配置

    # === HTTP 请求日志：每个前请求记录方法、路径、状态、耗时 ===
    @app.before_request
    def _log_req_start():
        request._req_t0 = time.time()

    @app.after_request
    def _log_req_end(response):
        t = (time.time() - getattr(request, '_req_t0', 0)) * 1000
        app.logger.debug("%s %s → %s %.0fms", request.method, request.path, response.status_code, t)
        # 禁用所有响应的浏览器缓存（HTML/JS/CSS/JSON），避免前端改完仍加载旧版本
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    @app.errorhandler(500)
    def _handle_500(e):
        # 统一将未捕获异常转为 JSON，避免返回 HTML 导致前端 resp.json() 解析失败
        app.logger.error("未捕获异常 (500): %s", e, exc_info=True)
        return jsonify({'success': False, 'error': f'服务器内部错误: {e}'}), 500

    @app.route('/api/changelog')
    def api_changelog():
        changelog_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'CHANGELOG.md')
        try:
            with open(changelog_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except FileNotFoundError:
            content = '# 暂无更新记录'
        return {"content": content}

    return app


if __name__ == '__main__':
    app = create_app()
    # 自动建表（空数据库时）
    from backend.models import Character, db
    with app.app_context():
        if Character.query.first() is None:
            db.create_all()
    app.logger.info("=" * 60)
    app.logger.info("  邻信游戏服务启动")
    app.logger.info("  地址: http://127.0.0.1:5080")
    app.logger.info("  调试模式: False")
    from backend.models import LLMConfig
    with app.app_context():
        active_llm = LLMConfig.query.filter_by(is_active=True).first()
        llm_model_display = active_llm.model_name if active_llm else app.config.get('LLM_MODEL', 'N/A')
    app.logger.info("  LLM 模型: %s", llm_model_display)
    app.logger.info("  LLM API: %s", app.config.get('LLM_API_URL', 'N/A'))
    app.logger.info("  数据库: %s", app.config['SQLALCHEMY_DATABASE_URI'])
    app.logger.info("=" * 60)
    sys.stdout.flush()

    app.run(debug=False, host='0.0.0.0', port=5080, threaded=True)
