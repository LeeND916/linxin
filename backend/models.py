from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event, inspect, JSON as SAJSON
from sqlalchemy.orm import validates
import json
from backend.config import beijing_now

db = SQLAlchemy()

# SQLite 开启 WAL 模式：允许读写并发，缓解多后台线程(同步/新闻)与请求线程争用导致的
# "database is locked" 以及随之而来的 PendingRollbackError 级联失败。
import sqlite3
from sqlalchemy.engine import Engine

@event.listens_for(Engine, "connect")
def _enable_sqlite_wal(dbapi_connection, connection_record):
    """每个新连接建立时，对 SQLite 执行 WAL 相关 PRAGMA。"""
    if isinstance(dbapi_connection, sqlite3.Connection):
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.close()
        except Exception:
            pass


class ChineseJSON(SAJSON):
    r"""JSON column type that stores Chinese characters as-is, without \uXXXX escaping."""
    def process_bind_param(self, value, dialect):
        if value is not None:
            return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        return value


class LLMConfig(db.Model):
    """LLM API 配置持久化"""
    __tablename__ = 'llm_config'

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(32), default='openai')
    api_url = db.Column(db.String(512), nullable=False, default='https://api.openai.com/v1/chat/completions')
    api_key = db.Column(db.String(512), default='')
    model_name = db.Column(db.String(128), default='gpt-4o-mini')
    temperature = db.Column(db.Float, default=0.85)
    max_tokens = db.Column(db.Integer, default=393200)
    is_active = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=beijing_now)
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)

    @validates('is_active')
    def validate_is_active(self, key, value):
        if value:
            # 确保同时只有一个激活的配置
            existing = LLMConfig.query.filter_by(is_active=True).first()
            if existing and existing.id != self.id:
                existing.is_active = False
        return value

    def to_dict(self):
        return {
            'id': self.id,
            'provider': self.provider,
            'api_url': self.api_url,
            'api_key': self.api_key[:8] + '***' if self.api_key else '',
            'model_name': self.model_name,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def to_secret_dict(self):
        """返回包含完整 api_key 的字典（仅后端使用）"""
        d = self.to_dict()
        d['api_key'] = self.api_key
        return d


class ComfyUIConfig(db.Model):
    """ComfyUI 配置持久化"""
    __tablename__ = 'comfyui_config'

    id = db.Column(db.Integer, primary_key=True)
    api_url = db.Column(db.String(512), default='http://127.0.0.1:8188')
    api_key = db.Column(db.String(512), default='')
    # ComfyUI 桌面端 exe 路径：留空由前端配置页填写（或本机 local_config.py 提供）
    exe_path = db.Column(db.String(512), default='')
    is_active = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=beijing_now)
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)

    @validates('is_active')
    def validate_is_active(self, key, value):
        if value:
            existing = ComfyUIConfig.query.filter_by(is_active=True).first()
            if existing and existing.id != self.id:
                existing.is_active = False
        return value

    def to_dict(self):
        return {
            'id': self.id,
            'api_url': self.api_url,
            'api_key': self.api_key[:8] + '***' if self.api_key else '',
            'exe_path': self.exe_path,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def to_secret_dict(self):
        d = self.to_dict()
        d['api_key'] = self.api_key
        return d


class ComfyUIWorkflow(db.Model):
    """ComfyUI 工作流配置（全局单一工作流：角色画像/肖像/七场景聊天生图共用一个默认）"""
    __tablename__ = 'comfyui_workflow'

    id = db.Column(db.Integer, primary_key=True)
    comfyui_config_id = db.Column(db.Integer, db.ForeignKey('comfyui_config.id'), nullable=False)
    name = db.Column(db.String(64), default='')                 # 显示名（下拉用）
    workflow_path = db.Column(db.String(512), default='')
    llm_model_id = db.Column(db.Integer, db.ForeignKey('llm_config.id'), nullable=True)
    prompt_template = db.Column(db.Text, default='')
    node_map = db.Column(db.Text, default='')                   # JSON：节点号映射（解硬编码）
    is_default = db.Column(db.Boolean, default=False)           # 每配置仅一个默认
    created_at = db.Column(db.DateTime, default=beijing_now)
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)

    comfyui_config = db.relationship('ComfyUIConfig', backref=db.backref('workflows', lazy=True))
    llm_model = db.relationship('LLMConfig', backref=db.backref('comfyui_workflows', lazy=True))

    def to_dict(self):
        return {
            'id': self.id,
            'comfyui_config_id': self.comfyui_config_id,
            'name': self.name,
            'workflow_path': self.workflow_path,
            'llm_model_id': self.llm_model_id,
            'prompt_template': self.prompt_template,
            'node_map': self.node_map,
            'is_default': self.is_default,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


class EmbeddingConfig(db.Model):
    """Embedding 配置持久化（LM Studio / local）"""
    __tablename__ = 'embedding_config'

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(32), default='llm_studio')
    api_url = db.Column(db.String(512), default='http://localhost:10039/v1/embeddings')
    api_key = db.Column(db.String(512), default='')
    model_name = db.Column(db.String(128), default='text-embedding-bge-m3')
    vector_dimension = db.Column(db.Integer, default=1024)
    is_active = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=beijing_now)
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)

    @validates('is_active')
    def validate_is_active(self, key, value):
        if value:
            existing = EmbeddingConfig.query.filter_by(is_active=True).first()
            if existing and existing.id != self.id:
                existing.is_active = False
        return value

    def to_dict(self):
        return {
            'id': self.id,
            'provider': self.provider,
            'api_url': self.api_url,
            'api_key': self.api_key[:8] + '***' if self.api_key else '',
            'model_name': self.model_name,
            'vector_dimension': self.vector_dimension,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def to_secret_dict(self):
        d = self.to_dict()
        d['api_key'] = self.api_key
        return d


class MemosConfig(db.Model):
    """Memos 外部服务配置持久化（自建 Memos 实例）"""
    __tablename__ = 'memos_config'

    id = db.Column(db.Integer, primary_key=True)
    api_url = db.Column(db.String(512), default='')
    access_token = db.Column(db.String(512), default='')
    skip_attachments = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=False)
    last_synced_at = db.Column(db.DateTime, nullable=True)  # 增量同步：上次同步时间
    created_at = db.Column(db.DateTime, default=beijing_now)
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)

    @validates('is_active')
    def validate_is_active(self, key, value):
        if value:
            existing = MemosConfig.query.filter_by(is_active=True).first()
            if existing and existing.id != self.id:
                existing.is_active = False
        return value

    def to_dict(self):
        return {
            'id': self.id,
            'api_url': self.api_url,
            'access_token': self.access_token[:8] + '***' if self.access_token else '',
            'skip_attachments': self.skip_attachments,
            'is_active': self.is_active,
            'last_synced_at': self.last_synced_at.isoformat() if self.last_synced_at else None,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def to_secret_dict(self):
        d = self.to_dict()
        d['access_token'] = self.access_token
        return d


class Character(db.Model):
    """角色核心状态"""
    __tablename__ = 'character'

    id = db.Column(db.Integer, primary_key=True)
    is_active = db.Column(db.Boolean, default=False)  # 当前活跃角色，同一时间只有一个
    name = db.Column(db.String(64), default='晓月')
    age = db.Column(db.Integer, default=20)
    gender = db.Column(db.String(16), default='female')

    # 物理状态 (0-100)
    health = db.Column(db.Float, default=90.0)
    energy = db.Column(db.Float, default=80.0)
    hunger = db.Column(db.Float, default=30.0)
    hygiene = db.Column(db.Float, default=85.0)
    brain_health = db.Column(db.Float, default=95.0)
    heart_health = db.Column(db.Float, default=98.0)
    lung_health = db.Column(db.Float, default=96.0)
    liver_health = db.Column(db.Float, default=94.0)
    skin_health = db.Column(db.Float, default=92.0)
    eye_health = db.Column(db.Float, default=90.0)

    # 心理状态 (0-100)
    mood = db.Column(db.Float, default=75.0)
    stress = db.Column(db.Float, default=30.0)
    happiness = db.Column(db.Float, default=70.0)
    loneliness = db.Column(db.Float, default=25.0)
    confidence = db.Column(db.Float, default=65.0)
    motivation = db.Column(db.Float, default=80.0)
    creativity = db.Column(db.Float, default=75.0)
    joy = db.Column(db.Float, default=70.0)              # 开心
    anger = db.Column(db.Float, default=10.0)             # 愤怒
    disappointment = db.Column(db.Float, default=15.0)    # 失望
    boredom = db.Column(db.Float, default=20.0)           # 无聊
    fulfillment = db.Column(db.Float, default=65.0)       # 充实

    # 能力属性 (0-100) — @deprecated 已迁移到 skills JSON 列，保留以兼容旧数据
    writing_skill = db.Column(db.Float, default=35.0)     # @deprecated
    coding_skill = db.Column(db.Float, default=30.0)      # @deprecated
    social_skill = db.Column(db.Float, default=55.0)      # @deprecated
    learning_skill = db.Column(db.Float, default=60.0)    # @deprecated
    fitness = db.Column(db.Float, default=50.0)           # @deprecated

    # 动态技能 JSON 字段（新）
    skills = db.Column(ChineseJSON, default=dict, nullable=True)
    skill_display = db.Column(ChineseJSON, default=dict, nullable=True)

    # 与玩家的关系 (0-100) — 默认值设为 5（陌生人级别，Tier 0）
    player_trust = db.Column(db.Float, default=5.0)
    player_affection = db.Column(db.Float, default=5.0)
    player_respect = db.Column(db.Float, default=5.0)
    player_intimacy = db.Column(db.Float, default=5.0)

    # 恋爱关系状态
    relationship_status = db.Column(db.String(16), default='friends')  # 'friends' / 'dating'

    # 穿搭风格
    outfit_style = db.Column(db.String(32), default='casual')
    # 当前穿搭（JSON 字符串，存储完整穿搭字典）
    current_outfit = db.Column(db.Text, default='{}')
    # 上次换装时间（游戏天），用于判断每天是否已换装
    outfit_changed_at = db.Column(db.Integer, default=-1)
    # 穿搭历史 [{"day": 15, "preset_id": 42}, ...]，最近 30 天
    outfit_history = db.Column(db.JSON, default=list, nullable=True)
    # 穿搭随机种子（已弃用，保留列仅作历史兼容，实际无人读取）
    outfit_seed = db.Column(db.Integer, default=0)
    # 肖像生成固定种子：非 0 锁定脸，0 则随机（向后兼容）。替代 outfit_seed 的肖像用途
    portrait_seed = db.Column(db.Integer, default=0)
    # z-image 工作流专用 LoRA 名（可填片段，如 'yanyin'）：
    # 为空 → 默认 LoRA；非空 → 在 ComfyUI 可用列表中精确匹配→包含匹配，失败回退默认。
    zimage_lora = db.Column(db.String(255), default='', nullable=True)
    # 聊天头像 URL（由角色肖像导入到 /static/avatars/{id}.png 后写入；空串表示用默认占位）
    avatar = db.Column(db.String(512), default='', nullable=True)

    # 当前位置
    location = db.Column(db.String(64), default='dormitory')

    @validates('location')
    def _on_location_changed(self, key, value):
        """location 变更时仅更新地点；换装由明确动作入口统一处理。"""
        # 仅当角色已持久化且有 id 时才触发，避免创建/加载时误触发
        if not self.id or not db.session.object_session(self):
            return value

        old = getattr(self, '_location_cached', self.location)
        import logging
        _log = logging.getLogger('game')
        _log.info(f"[Outfit Debug] location validator triggered: name={self.name!r}, old={old!r}, new={value!r}, id={self.id}")
        if old and old != value:
            _log.info(f"[Outfit] location changed for {self.name!r}; waiting for explicit outfit action")
        else:
            _log.info(f"[Outfit Debug] location not changed (old==new or empty), skipped outfit update")
        return value

    # 目标进度 — @deprecated 已迁移到 goals JSON 列，保留以兼容旧数据
    writer_progress = db.Column(db.Float, default=5.0)   # @deprecated
    coder_progress = db.Column(db.Float, default=5.0)    # @deprecated

    # 动态目标 JSON 字段（新）
    goals = db.Column(ChineseJSON, default=dict, nullable=True)
    goal_display = db.Column(ChineseJSON, default=dict, nullable=True)
    # 目标解锁规则：{key:{type:'condition'|'numeric', conditions:[...], progress_source:{...}, target:100, label:'...'}}
    goal_rules = db.Column(ChineseJSON, default=dict, nullable=True)

    # 时间戳
    created_at = db.Column(db.DateTime, default=beijing_now)
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)

    # 游戏内部日历系统（与现实时间无关）
    game_day = db.Column(db.Integer, default=0)     # 游戏经过的天数（从0开始，0=9月1日）
    game_hour = db.Column(db.Integer, default=9)    # 游戏当前小时（0-23），新角色默认早上9:00
    game_minute = db.Column(db.Integer, default=0)  # 游戏当前分钟（0-59）
    game_second = db.Column(db.Integer, default=0)  # 游戏当前秒（0-59）

    # 天气快照（游戏内天气，独立于现实日期，每个角色可不同）
    weather = db.Column(db.String(16), default='')  # 天气名称：晴/多云/小雨/阴天/大风/雾

    # 主动消息冷却控制
    last_active_message_tick = db.Column(db.Integer, default=0)  # 上次尝试发主动消息的 tick 序号
    active_message_cooldown_ticks = db.Column(db.Integer, default=3)  # 冷却 tick 数

    # 角色模块化扩展字段 (P0-1)
    preset_id = db.Column(db.String(32), default='p1')
    profile_json = db.Column(db.Text, default='')
    major = db.Column(db.String(64), default='')
    identity_label = db.Column(db.String(32), default='女大学生')
    education = db.Column(db.String(32), default='')
    hometown = db.Column(db.String(64), default='')
    family = db.Column(db.String(32), default='')
    economic = db.Column(db.String(16), default='')
    hobbies = db.Column(db.String(128), default='')
    appearance = db.Column(db.Text, default='')
    dream_primary = db.Column(db.String(128), default='')
    dream_secondary = db.Column(db.String(128), default='')
    dream_motivation = db.Column(db.String(256), default='')
    personality_type = db.Column(db.String(64), default='温柔')
    personality_tone = db.Column(db.String(64), default='自然日常')
    social_tendency = db.Column(db.Float, default=6.0)
    emotional_stability = db.Column(db.Float, default=6.0)
    expression_style = db.Column(db.String(32), default='自然')
    relationship_history = db.Column(db.String(32), default='')

    # ── 玩家专属档案（AI伴侣系统升级 — 阶段一）──
    # 玩家在该女主心中的身份（如：同班同学、竞争对手、上司、导师等）
    player_identity = db.Column(db.String(64), default='导师')
    # 与该玩家的专属互动风格描述
    interaction_style = db.Column(db.Text, default='')
    # 对该玩家的专属称呼
    player_nickname = db.Column(db.String(32), default='')
    # 角色日常话题列表（逗号分隔，LLM 根据角色身份/爱好生成）
    daily_topics = db.Column(db.Text, default='')
    # 角色对玩家的初始态度描述（LLM 根据角色性格+玩家身份生成）
    attitude_toward_player = db.Column(db.Text, default='')
    # 心理属性是否已初始化（daily_mental_regulation 仅首次执行，避免覆盖 GM 手动设置）
    mental_initialized = db.Column(db.Boolean, default=False, nullable=False)

    # ── 情绪引擎（AI伴侣系统升级 — 阶段二）──
    # 近 N 轮情绪快照 JSON: [{"mood": 75, "joy": 60, ...}, ...]，用于情绪惯性计算
    emotion_history = db.Column(db.Text, default='[]')

    # ── 对话摘要系统 ──
    # 累计对话轮次（每20轮生成一次阶段摘要，生成后重置为0）
    dialogue_rounds = db.Column(db.Integer, default=0)

    # ── 任务系统（Mission System）──
    planned_location = db.Column(db.String(64), default='')      # 当日计划前往地点（条件匹配用，非事后 location）
    has_pending_mission = db.Column(db.Boolean, default=False)   # auto_next 失败后置 True，前端显示 regenerate 按钮

    # ── TTS 情绪分类器 — 音色底色锚点（AI伴侣系统升级 — 阶段三）──
    # 固定文本描述角色的音色气质底色，如 "温柔知性的邻家大姐姐音色，语气轻柔、温暖且富有亲和力"
    character_base = db.Column(db.Text, default='')

    # ── 腾讯云 TTS 音色类型（AI伴侣系统升级 — 阶段四）──
    # 如 '101001'（智瑜精品）, '601009'（爱小芊大模型多情感）等
    tencent_tts_voice_type = db.Column(db.String(16), default='')

    def _resolve_location_name(self):
        """把 location 字段（venue_id，如 'home' 或 'custom_xxx'）解析成中文显示名。

        来源优先级：get_character_venues 的 id→名称映射（来自 CharacterActivityMap 专属场馆
        与 CHARACTER_VENUES 名称覆盖；内置 LOCATIONS 已删除），查不到则回退原始 id。
        """
        loc = self.location
        if not loc:
            return ''
        try:
            from backend.game.activity import get_character_venues
            venues = get_character_venues(self)
            return venues.get(loc, loc)
        except Exception:
            return loc

    def get_game_date(self):
        """返回 (year, month, day, weekday_name) 元组，基于 game_day 从 2024-09-01 计算"""
        from datetime import date, timedelta
        start_date = date(2024, 9, 1)  # 开学日期
        target = start_date + timedelta(days=self.game_day)
        weekday_names = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
        return (target.year, target.month, target.day, weekday_names[target.weekday()])

    def advance_time(self):
        """推进 1 游戏小时，满 24 → 1 天，分钟归零"""
        self.game_hour += 1
        self.game_minute = 0
        if self.game_hour >= 24:
            self.game_hour = 0
            self.game_day += 1

    def advance_time_hours(self, hours: int):
        """推进指定游戏小时数，满 24 → 1 天，分钟归零。"""
        total_hours = self.game_hour + hours
        self.game_day += total_hours // 24
        self.game_hour = total_hours % 24
        self.game_minute = 0

    def get_game_date_display(self):
        """返回如 '2024年9月15日 星期日' 的字符串"""
        year, month, day, weekday = self.get_game_date()
        return f"{year}年{month}月{day}日 {weekday}"

    def get_game_date_short(self):
        """返回简短的日期格式，如 '9月1日'"""
        year, month, day, weekday = self.get_game_date()
        return f"{month}月{day}日"

    @property
    def goals_summary(self) -> str:
        """返回目标进度的简要文本摘要，用于对话上下文"""
        if not self.goals or not isinstance(self.goals, dict):
            return "暂无目标"
        display = self.goal_display if isinstance(self.goal_display, dict) else {}
        parts = []
        for key, val in self.goals.items():
            name = display.get(key, key)
            parts.append(f"{name}:{val}")
        return "、".join(parts) if parts else "暂无目标"

    @property
    def skills_summary(self) -> str:
        """返回技能数值的简要文本摘要（中文名），用于对话上下文"""
        if not self.skills or not isinstance(self.skills, dict):
            return "暂无技能数据"
        display = self.skill_display if isinstance(self.skill_display, dict) else {}
        parts = []
        for key, val in self.skills.items():
            name = display.get(key, key)
            parts.append(f"{name}:{val:.0f}")
        return "、".join(parts) if parts else "暂无技能数据"

    def get_current_outfit(self) -> dict:
        """解析 current_outfit 字段，返回穿搭字典（解析失败返回空字典）"""
        if not self.current_outfit:
            return {}
        try:
            return json.loads(self.current_outfit)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _get_current_outfit_compat(self) -> dict:
        """返回 current_outfit，为旧前端注入 style 别名"""
        outfit = self.get_current_outfit()
        if outfit and 'style' not in outfit:
            # 新格式只有 occasion，注入 style 别名兼容旧前端
            outfit['style'] = outfit.get('occasion', 'casual')
        return outfit

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'age': self.age, 'gender': self.gender,
            'physical': {
                'health': self.health, 'energy': self.energy,
                'hunger': self.hunger, 'hygiene': self.hygiene,
                'organs': {
                    'brain': self.brain_health, 'heart': self.heart_health,
                    'lung': self.lung_health, 'liver': self.liver_health,
                    'skin': self.skin_health, 'eye': self.eye_health
                }
            },
            'mental': {
                'mood': self.mood, 'stress': self.stress,
                'happiness': self.happiness, 'loneliness': self.loneliness,
                'confidence': self.confidence, 'motivation': self.motivation,
                'creativity': self.creativity,
                'joy': self.joy, 'anger': self.anger,
                'disappointment': self.disappointment, 'boredom': self.boredom,
                'fulfillment': self.fulfillment
            },
            'skills': self.skills if self.skills else {},
            # 不要用硬编码的 p1 通用标签兜底——它们与角色专属 skills 的 key 不匹配。
            # 当 skill_display 为 None 时返回空 dict，前端 renderSkills 会用 key 本身作为回退标签。
            'skill_display': self.skill_display if self.skill_display else {},
            'player_relation': {
                'trust': self.player_trust, 'affection': self.player_affection,
                'respect': self.player_respect, 'intimacy': self.player_intimacy
            },
            'relationship_status': self.relationship_status,
            'outfit_style': self.outfit_style,
            'current_outfit': self._get_current_outfit_compat(),
            'outfit_changed_at': self.outfit_changed_at,
            'location': self.location,
            'avatar': self.avatar or '',
            'location_name': self._resolve_location_name(),
            'game_day': self.game_day,
            'game_hour': self.game_hour,
            'game_minute': self.game_minute,
            'game_second': self.game_second,
            'game_date_display': self.get_game_date_display(),
            'game_date_short': self.get_game_date_short(),
            'weather': self.weather or '',
            'last_active_message_tick': self.last_active_message_tick,
            'active_message_cooldown_ticks': self.active_message_cooldown_ticks,
            'goals': self.goals if self.goals else {},
            'goal_display': self.goal_display if self.goal_display else {},
            'goal_rules': self.goal_rules if self.goal_rules else {},
            'preset_id': self.preset_id,
            'profile_json': self.profile_json,
            'major': self.major,
            'identity_label': self.identity_label,
            'education': self.education,
            'hometown': self.hometown,
            'family': self.family,
            'economic': self.economic,
            'hobbies': self.hobbies,
            'appearance': self.appearance,
            'dream_primary': self.dream_primary,
            'dream_secondary': self.dream_secondary,
            'dream_motivation': self.dream_motivation,
            'personality_type': self.personality_type,
            'personality_tone': self.personality_tone,
            'social_tendency': self.social_tendency,
            'emotional_stability': self.emotional_stability,
            'expression_style': self.expression_style,
            'relationship_history': self.relationship_history,
            # ── 玩家专属档案（AI伴侣系统升级 — 阶段一）──
            'player_identity': getattr(self, 'player_identity', '导师') or '导师',
            'player_nickname': getattr(self, 'player_nickname', '') or '',
            'interaction_style': getattr(self, 'interaction_style', '') or '',
            'daily_topics': getattr(self, 'daily_topics', '') or '',
            'attitude_toward_player': getattr(self, 'attitude_toward_player', '') or '',
            # ── TTS 情绪分类器 — 音色底色（AI伴侣系统升级 — 阶段三）──
            'character_base': self.character_base or '',
            # ── z-image 生图 LoRA（按角色指定，含片段匹配）──
            'zimage_lora': self.zimage_lora or '',
            # ── 任务系统（Mission System）──
            'planned_location': self.planned_location or '',
            'has_pending_mission': self.has_pending_mission,

        }


class Appearance(db.Model):
    """外貌锚点表：character_id=0/-1 为全局玩家默认，正数可为角色专用玩家外貌。"""
    __tablename__ = 'appearances'

    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, nullable=False)
    keyword = db.Column(db.String(256), default='')
    shorthand = db.Column(db.Text, default='')
    deep_description = db.Column(db.Text, default='')
    player_gender = db.Column(db.String(16), default='')
    player_keywords = db.Column(db.String(256), default='')
    player_summary = db.Column(db.Text, default='')
    player_face_shape = db.Column(db.Text, default='')
    player_eyes = db.Column(db.Text, default='')
    player_eyebrows = db.Column(db.Text, default='')
    player_nose = db.Column(db.Text, default='')
    player_mouth = db.Column(db.Text, default='')
    player_hairstyle = db.Column(db.Text, default='')
    player_skin = db.Column(db.Text, default='')
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=beijing_now)

    def to_dict(self):
        return {
            'id': self.id,
            'character_id': self.character_id,
            'keyword': self.keyword,
            'shorthand': self.shorthand,
            'deep_description': self.deep_description,
            'player_gender': self.player_gender,
            'player_keywords': self.player_keywords,
            'player_summary': self.player_summary,
            'player_face_shape': self.player_face_shape,
            'player_eyes': self.player_eyes,
            'player_eyebrows': self.player_eyebrows,
            'player_nose': self.player_nose,
            'player_mouth': self.player_mouth,
            'player_hairstyle': self.player_hairstyle,
            'player_skin': self.player_skin,
        }


class OutfitComponent(db.Model):
    """穿搭组件表 — 单件衣物/配饰，每角色 50~60 件"""
    __tablename__ = 'outfit_components'

    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)

    type = db.Column(db.String(16), default='')
    subtype = db.Column(db.String(32), default='')
    name = db.Column(db.String(64), default='')
    description = db.Column(db.Text, default='')
    color = db.Column(db.String(32), default='')
    color_tone = db.Column(db.String(16), default='neutral')
    formality = db.Column(db.Integer, default=2)
    warmth = db.Column(db.Integer, default=1)
    season_mask = db.Column(db.String(8), default='1111')
    occasion_mask = db.Column(db.String(64), default='daily')
    style_tags = db.Column(db.String(128), default='')
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=beijing_now)

    character = db.relationship('Character', backref=db.backref('outfit_components', lazy=True))

    def to_dict(self):
        return {
            'id': self.id,
            'type': self.type,
            'subtype': self.subtype,
            'name': self.name,
            'description': self.description,
            'color': self.color,
            'color_tone': self.color_tone,
            'formality': self.formality,
            'warmth': self.warmth,
            'season_mask': self.season_mask,
            'style_tags': self.style_tags,
        }

    def seasons_list(self):
        """season_mask '1111' → ['spring','summer','autumn','winter']"""
        seasons = ['spring', 'summer', 'autumn', 'winter']
        return [s for s, ch in zip(seasons, self.season_mask) if ch == '1']


class OutfitPreset(db.Model):
    """穿搭预设表 — 已组装的成套穿搭，每角色 56 套"""
    __tablename__ = 'outfit_presets'

    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False)

    name = db.Column(db.String(64), default='')
    description = db.Column(db.Text, default='')
    season = db.Column(db.String(16), default='')
    occasion = db.Column(db.String(16), default='daily')
    category = db.Column(db.String(16), default='template')
    base_template_id = db.Column(db.Integer, db.ForeignKey('outfit_presets.id'), nullable=True)
    component_ids = db.Column(db.JSON, default=dict)
    formality_level = db.Column(db.Integer, default=2)
    warmth_level = db.Column(db.Integer, default=2)
    mood_min = db.Column(db.Integer, default=0)
    relationship_min = db.Column(db.Integer, default=0)
    use_count = db.Column(db.Integer, default=0)
    last_used_day = db.Column(db.Integer, default=-1)
    created_at = db.Column(db.DateTime, default=beijing_now)

    character = db.relationship('Character', backref=db.backref('outfit_presets', lazy=True))
    base_template = db.relationship('OutfitPreset', remote_side=[id])

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'season': self.season,
            'occasion': self.occasion,
            'category': self.category,
            'component_ids': self.component_ids,
            'formality_level': self.formality_level,
            'warmth_level': self.warmth_level,
        }


class Friend(db.Model):
    """朋友关系（逐步迁移至 CharacterRelation）"""
    __tablename__ = 'friend'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    gender = db.Column(db.String(16), default='female')
    personality = db.Column(db.String(128), default='')
    role = db.Column(db.String(64), default='同学')

    # ── 关系类型（事件重构 v3.1 新增）──
    relation_type = db.Column(db.String(16), default='friend')

    # ── 正面维度 ──
    closeness = db.Column(db.Float, default=50.0)
    trust = db.Column(db.Float, default=50.0)
    affection = db.Column(db.Float, default=50.0)

    # ── 负面维度（事件重构 v3.1 新增）──
    rivalry = db.Column(db.Float, default=0.0)
    hostility = db.Column(db.Float, default=0.0)
    fear = db.Column(db.Float, default=0.0)

    # ── 关系内部状态字段（事件重构 v3.1 新增）──
    loneliness = db.Column(db.Float, default=50.0)
    happiness = db.Column(db.Float, default=50.0)
    stress = db.Column(db.Float, default=30.0)
    mood = db.Column(db.Float, default=60.0)
    energy = db.Column(db.Float, default=70.0)
    clarity = db.Column(db.Float, default=60.0)

    last_interaction = db.Column(db.DateTime, default=beijing_now)
    bio = db.Column(db.Text, default='')
    character_name = db.Column(db.String(32), default='')
    mission_id = db.Column(db.Integer, nullable=True, default=None)  # 归属任务 ID（三元组唯一：name+character_name+mission_id）
    last_interaction_time = db.Column(db.Text, default='')
    last_interaction_content = db.Column(db.Text, default='')

    def to_dict(self):
        return {
            'id': self.id, 'name': self.name, 'gender': self.gender,
            'personality': self.personality, 'role': self.role,
            'relation_type': self.relation_type,
            'closeness': self.closeness, 'trust': self.trust,
            'affection': self.affection,
            'rivalry': self.rivalry, 'hostility': self.hostility, 'fear': self.fear,
            'loneliness': self.loneliness, 'happiness': self.happiness,
            'stress': self.stress, 'mood': self.mood,
            'energy': self.energy, 'clarity': self.clarity,
            'last_interaction': self.last_interaction.isoformat(),
            'bio': self.bio,
            'character_name': self.character_name,
            'mission_id': self.mission_id,
            'last_interaction_time': self.last_interaction_time or '',
            'last_interaction_content': self.last_interaction_content or '',
        }


class EventLog(db.Model):
    """事件日志"""
    __tablename__ = 'event_log'

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(32), nullable=False)
    event_category = db.Column(db.String(32), default='')  # '' / narrative_scheduled / structured_event / relation_initiated / world_event / daily / story_arc / llm_fallback
    title = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text, default='')
    effects = db.Column(db.Text, default='{}')
    created_at = db.Column(db.DateTime, default=beijing_now)
    game_day = db.Column(db.Integer, default=0)
    game_time = db.Column(db.String(12), default='08:00')
    character_name = db.Column(db.String(32), default='', index=True)
    location = db.Column(db.String(128), default='')

    def to_dict(self):
        return {
            'id': self.id, 'event_type': self.event_type,
            'event_category': self.event_category or '',
            'title': self.title, 'description': self.description,
            'effects': json.loads(self.effects) if self.effects else {},
            'created_at': self.created_at.isoformat(),
            'game_day': self.game_day,
            'game_time': self.game_time,
            'character_name': self.character_name,
            'location': self.location,
        }


class EventCooldown(db.Model):
    """事件冷却管理"""
    __tablename__ = 'event_cooldown'

    id = db.Column(db.Integer, primary_key=True)
    character_name = db.Column(db.String(32), nullable=False, index=True)
    trigger_id = db.Column(db.String(64), nullable=False)
    game_day_expires = db.Column(db.Integer, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'character_name': self.character_name,
            'trigger_id': self.trigger_id,
            'game_day_expires': self.game_day_expires,
        }


class NewsCache(db.Model):
    """近期新闻资讯缓存（后台线程每小时抓取，注入对话与主动消息）"""
    __tablename__ = 'news_cache'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(256), nullable=False)
    summary = db.Column(db.Text, default='')          # LLM 压缩后的 1-2 句摘要
    source = db.Column(db.String(64), default='')    # 来源名，如"中新网"
    source_url = db.Column(db.String(512), default='')
    category = db.Column(db.String(32), default='')  # general / law / tech / medical / culture / game
    major_tag = db.Column(db.String(64), default='') # 关联的职业，如"法学"
    fetched_at = db.Column(db.DateTime, default=beijing_now)  # 现实抓取时间
    used = db.Column(db.Boolean, default=False)       # 是否已注入对话/事件
    used_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'summary': self.summary,
            'source': self.source,
            'source_url': self.source_url,
            'category': self.category,
            'major_tag': self.major_tag,
            'fetched_at': self.fetched_at.isoformat() if self.fetched_at else None,
            'used': self.used,
            'used_at': self.used_at.isoformat() if self.used_at else None,
        }


class NewsSourceConfig(db.Model):
    """新闻源配置（前端面板可增删改/启停）"""
    __tablename__ = 'news_source_config'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)          # 源名称，如"中新网即时"
    source_type = db.Column(db.String(16), nullable=False, default='rss')  # rss / 60s / juhe / volcano
    api_url = db.Column(db.String(512), default='')         # RSS URL 或 API endpoint
    api_key = db.Column(db.String(512), default='')          # 付费源用，RSS/60s 空
    category = db.Column(db.String(32), default='general')  # general/tech/edu/culture/health/world
    enabled = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=beijing_now)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'source_type': self.source_type,
            'api_url': self.api_url,
            'api_key': self.api_key[:8] + '***' if self.api_key else '',
            'category': self.category,
            'enabled': self.enabled,
            'sort_order': self.sort_order,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class NewsSettings(db.Model):
    """新闻资讯全局配置（单行表，id 恒为 1）"""
    __tablename__ = 'news_settings'

    id = db.Column(db.Integer, primary_key=True, autoincrement=False)  # 恒为 1
    enabled = db.Column(db.Boolean, default=True)                      # 总开关
    max_items_per_feed = db.Column(db.Integer, default=8)             # 每源最多抓几条
    max_inject_per_day = db.Column(db.Integer, default=3)              # 每角色每天注入几条
    push_interval_days = db.Column(db.Integer, default=0)            # 推送间隔天数(0=不限制)
    fetch_interval = db.Column(db.Integer, default=3600)              # 抓取间隔(秒)
    cache_retain_days = db.Column(db.Integer, default=2)               # 缓存保留天数
    summary_threshold = db.Column(db.Integer, default=80)             # 超过此长度才调LLM摘要
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)

    def to_dict(self):
        return {
            'enabled': self.enabled,
            'max_items_per_feed': self.max_items_per_feed,
            'max_inject_per_day': self.max_inject_per_day,
            'push_interval_days': self.push_interval_days,
            'fetch_interval': self.fetch_interval,
            'cache_retain_days': self.cache_retain_days,
            'summary_threshold': self.summary_threshold,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Achievement(db.Model):
    """成就系统（按角色隔离）"""
    __tablename__ = 'achievement'

    id = db.Column(db.Integer, primary_key=True)
    character_name = db.Column(db.String(32), default='', index=True)  # 所属角色名
    achievement_id = db.Column(db.String(64), default='', index=True)  # 成就唯一标识（同角色内唯一）
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, default='')
    icon = db.Column(db.String(32), default='star')
    hint = db.Column(db.String(128), default='')  # 解锁提示/条件说明
    unlocked = db.Column(db.Boolean, default=False)
    progress = db.Column(db.Float, default=0.0)
    target = db.Column(db.Float, default=100.0)
    unlocked_at = db.Column(db.DateTime, nullable=True)
    step_size = db.Column(db.Float, default=10.0)                # 每次步进量（LLM 可自定义，默认 10%）
    mission_id = db.Column(db.Integer, nullable=True, default=None)  # 归属任务 ID
    trigger_conditions = db.Column(db.Text, default='[]')          # 成就解锁条件（JSON 数组，格式同条件事件；空数组=任意关联事件触发）
    progress_source = db.Column(db.Text, default='')               # 数值型来源：JSON {"attr":"skill_coding","scale":1}，progress 直接等于该属性值
    unlock_event = db.Column(db.String(64), default='')            # 事件型：引用已知规则事件 trigger_id，每次命中 +step_size

    def to_dict(self):
        return {
            'id': self.id,
            'character_name': self.character_name,
            'achievement_id': self.achievement_id,
            'name': self.name, 'description': self.description,
            'icon': self.icon, 'hint': self.hint, 'unlocked': self.unlocked,
            'progress': self.progress, 'target': self.target,
            'step_size': self.step_size,
            'mission_id': self.mission_id,
            'trigger_conditions': self.trigger_conditions,
            'progress_source': self.progress_source,
            'unlock_event': self.unlock_event,
            'unlocked_at': self.unlocked_at.isoformat() if self.unlocked_at else None
        }


class EventAchievementBinding(db.Model):
    """事件→成就绑定表（持久化，替代内存 _DYNAMIC_ACHIEVEMENT_MAP，重启安全）。

    每行表示：当规则事件 trigger_id 命中时，给属于 character_name 的 achievement_id 累加进度。
    is_mission=True 表示来自任务（任务归档时随 mission_id 删除），False 表示角色创建时 LLM 初始化。
    """
    __tablename__ = 'event_achievement_binding'

    id = db.Column(db.Integer, primary_key=True)
    character_name = db.Column(db.String(32), default='', index=True)
    trigger_id = db.Column(db.String(64), default='', index=True)
    achievement_id = db.Column(db.String(64), default='', index=True)
    mission_id = db.Column(db.Integer, nullable=True, default=None)
    is_mission = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {
            'id': self.id, 'character_name': self.character_name,
            'trigger_id': self.trigger_id, 'achievement_id': self.achievement_id,
            'mission_id': self.mission_id, 'is_mission': self.is_mission,
        }


class CharacterActivityMap(db.Model):
    """角色专属活动地图（按角色隔离，记录各地点解锁/访问情况）"""
    __tablename__ = 'character_activity_map'

    id = db.Column(db.Integer, primary_key=True)
    character_name = db.Column(db.String(32), default='', index=True)  # 所属角色名
    venue_id = db.Column(db.String(64), nullable=False)   # 地点标识（与 locations 表对应）
    venue_name = db.Column(db.String(64), nullable=False)  # 地点显示名
    unlocked = db.Column(db.Boolean, default=False)        # 是否已解锁（可前往）
    discovered_at = db.Column(db.DateTime, nullable=True)  # 首次解锁时间
    last_visited = db.Column(db.DateTime, nullable=True)   # 最近一次访问时间
    visit_count = db.Column(db.Integer, default=0)         # 累计访问次数
    custom_activities = db.Column(db.Text, default='[]')    # LLM 专属活动 JSON

    def to_dict(self):
        import json as _json
        acts = []
        try:
            acts = _json.loads(self.custom_activities) if self.custom_activities else []
        except (ValueError, TypeError):
            acts = []
        return {
            'id': self.id,
            'character_name': self.character_name,
            'venue_id': self.venue_id,
            'venue_name': self.venue_name,
            'unlocked': self.unlocked,
            'discovered_at': self.discovered_at.isoformat() if self.discovered_at else None,
            'last_visited': self.last_visited.isoformat() if self.last_visited else None,
            'visit_count': self.visit_count,
            'custom_activities': acts,
        }


class Weather(db.Model):
    """每日天气"""
    __tablename__ = 'game_weather'

    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, unique=True, index=True)
    weather_type = db.Column(db.String(32), nullable=False)
    weather_name = db.Column(db.String(16), nullable=False)
    temperature = db.Column(db.Float, default=22.0)
    description = db.Column(db.String(128), default='')
    icon = db.Column(db.String(16), default='sun')

    def to_dict(self):
        return {
            'id': self.id,
            'date': self.date.isoformat(),
            'weather_type': self.weather_type,
            'weather_name': self.weather_name,
            'temperature': self.temperature,
            'description': self.description,
            'icon': self.icon
        }


class RelationEvent(db.Model):
    """朋友关系事件（SQLite 独立存储）"""
    __tablename__ = 'relation_events'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    character_name = db.Column(db.String(32), default='', index=True)
    event_day = db.Column(db.Integer, nullable=False)
    event_time = db.Column(db.String(10), nullable=False)
    friend_name = db.Column(db.String(100), nullable=False)
    event_title = db.Column(db.String(200))
    event_content = db.Column(db.Text)
    intimacy_old = db.Column(db.Float, default=0)
    intimacy_new = db.Column(db.Float, default=0)
    trust_old = db.Column(db.Float, default=0)
    trust_new = db.Column(db.Float, default=0)
    affection_old = db.Column(db.Float, default=0)
    affection_new = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=beijing_now)

    def to_dict(self):
        return {
            'id': self.id,
            'character_name': self.character_name,
            'event_day': self.event_day,
            'event_time': self.event_time,
            'friend_name': self.friend_name,
            'event_title': self.event_title,
            'event_content': self.event_content,
            'intimacy_old': self.intimacy_old,
            'intimacy_new': self.intimacy_new,
            'trust_old': self.trust_old,
            'trust_new': self.trust_new,
            'affection_old': self.affection_old,
            'affection_new': self.affection_new,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class ActiveMessageQueue(db.Model):
    """LLM 驱动的主动消息队列"""
    __tablename__ = 'active_message_queue'

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    effects = db.Column(db.Text, default='{}')  # JSON 格式的建议属性变化
    used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=beijing_now)

    def to_dict(self):
        return {
            'id': self.id,
            'content': self.content,
            'effects': json.loads(self.effects) if self.effects else {},
            'used': self.used,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


class Mission(db.Model):
    """任务模型 — 替代 ActiveCase，承载完整电影级叙事任务

    任务包包含：阶段叙事 + NPC关系网 + 专属成就 + 任务目标 + 故事弧 + 条件事件。
    生命周期：pending → running → completed → archived
    """
    __tablename__ = 'mission'

    id = db.Column(db.Integer, primary_key=True)
    character_name = db.Column(db.String(32), index=True, nullable=False)
    mission_name = db.Column(db.String(128), nullable=False)
    mission_type = db.Column(db.String(32), default='legal')
    mission_description = db.Column(db.Text, default='')
    core_conflict = db.Column(db.String(256), default='')
    tone = db.Column(db.String(32), default='thrilling')

    # 阶段管理（JSON）
    stages = db.Column(db.Text, default='[]')          # [{name, day_offset, narrative_prompt, description, conditions, choices, npc_intros}]
    # 初值 -1：advance 循环用 `i <= current_stage_index` 跳过已推进阶段，
    # 若初值 0 会导致第 0 阶段（day_offset=0 的开场戏）永远被跳过 → 任务错乱。
    current_stage_index = db.Column(db.Integer, default=-1)

    # 时间范围
    start_day = db.Column(db.Integer, default=0)
    end_day = db.Column(db.Integer, default=0)

    # 状态机
    status = db.Column(db.String(16), default='pending')  # pending / running / completed / archived

    # NPC 名册（JSON）
    npc_roster = db.Column(db.Text, default='[]')      # [{name, role, relation_type, description, stage_index}]

    # 成就 ID 列表（JSON）
    achievement_ids = db.Column(db.Text, default='[]') # ["mission_xxx_1", "mission_xxx_2"]

    # 目标（单个任务目标）
    goal_key = db.Column(db.String(64), default='')           # "mission_suqing_1"
    goal_label = db.Column(db.String(128), default='')         # "连环杀人案无罪辩护成功"
    goal_target = db.Column(db.Float, default=100.0)

    # 故事弧 ID
    arc_id = db.Column(db.String(64), default='')

    # 条件事件模板（JSON 列，唯一存储，不走文件）
    event_templates_json = db.Column(db.Text, default='[]')

    # 世界观提示词（LLM 生成用）— A5 决策：改名为 phase_name，存 phase 名称用于归档记录
    world_prompt = db.Column(db.Text, default='')
    phase_name = db.Column(db.Text, default='')

    # 实际投喂给 LLM 的完整 prompt（前端展示用，生成时由 _rendered_prompt 写入）
    rendered_prompt = db.Column(db.Text, default='')

    # 已触发事件 key 集合（JSON 列）— 五幕结构：["0_0", "0_1", "1_0", ...]
    fired_events = db.Column(db.Text, default='[]')

    created_at = db.Column(db.DateTime, default=beijing_now)
    completed_at = db.Column(db.DateTime, nullable=True)

    # ── 标准 property 访问器（@property + @setter） ──

    @property
    def stages_list(self) -> list:
        """读取 stages JSON"""
        try:
            return json.loads(self.stages) if self.stages else []
        except (json.JSONDecodeError, TypeError):
            return []

    @stages_list.setter
    def stages_list(self, stages: list):
        """设置 stages — 支持 mission.stages_list = [...] 赋值语法"""
        self.stages = json.dumps(stages, ensure_ascii=False)

    @property
    def npc_list(self) -> list:
        try:
            return json.loads(self.npc_roster) if self.npc_roster else []
        except (json.JSONDecodeError, TypeError):
            return []

    @npc_list.setter
    def npc_list(self, npcs: list):
        self.npc_roster = json.dumps(npcs, ensure_ascii=False)

    @property
    def achievement_id_list(self) -> list:
        try:
            return json.loads(self.achievement_ids) if self.achievement_ids else []
        except (json.JSONDecodeError, TypeError):
            return []

    @achievement_id_list.setter
    def achievement_id_list(self, ids: list):
        self.achievement_ids = json.dumps(ids, ensure_ascii=False)

    @property
    def event_templates(self) -> list:
        try:
            return json.loads(self.event_templates_json) if self.event_templates_json else []
        except (json.JSONDecodeError, TypeError):
            return []

    @event_templates.setter
    def event_templates(self, templates: list):
        self.event_templates_json = json.dumps(templates, ensure_ascii=False)

    @property
    def fired_events_list(self) -> list:
        """读取已触发事件 key 列表"""
        try:
            return json.loads(self.fired_events) if self.fired_events else []
        except (json.JSONDecodeError, TypeError):
            return []

    @fired_events_list.setter
    def fired_events_list(self, keys: list):
        """设置已触发事件 key 列表 — 必须整体重赋值才能触发 setter"""
        self.fired_events = json.dumps(keys, ensure_ascii=False)

    @property
    def current_stage(self) -> dict | None:
        """返回当前阶段的字典"""
        stages = self.stages_list
        if 0 <= self.current_stage_index < len(stages):
            return stages[self.current_stage_index]
        return None

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'character_name': self.character_name,
            'mission_name': self.mission_name,
            'mission_type': self.mission_type,
            'mission_description': self.mission_description,
            'core_conflict': self.core_conflict,
            'tone': self.tone,
            'stages': self.stages_list,
            'current_stage_index': self.current_stage_index,
            'start_day': self.start_day,
            'end_day': self.end_day,
            'status': self.status,
            'npc_roster': self.npc_list,
            'achievement_ids': self.achievement_id_list,
            'goal_key': self.goal_key,
            'goal_label': self.goal_label,
            'goal_target': self.goal_target,
            'arc_id': self.arc_id,
            'event_templates': self.event_templates,
            'world_prompt': self.world_prompt,
            'phase_name': self.phase_name,
            'rendered_prompt': self.rendered_prompt or '',
            'fired_events': self.fired_events_list,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }


class MissionArchive(db.Model):
    """任务归档模型 — 完成后所有相关数据快照存储"""
    __tablename__ = 'mission_archive'

    id = db.Column(db.Integer, primary_key=True)
    mission_id = db.Column(db.Integer, nullable=False, index=True)
    character_name = db.Column(db.String(32), index=True, nullable=False)

    # 全量快照（JSON）
    mission_snapshot = db.Column(db.Text, default='{}')
    achievements_snapshot = db.Column(db.Text, default='[]')
    goal_result = db.Column(db.Text, default='{}')
    relationships_snapshot = db.Column(db.Text, default='[]')
    arc_snapshot = db.Column(db.Text, default='{}')
    events_summary = db.Column(db.Text, default='{}')

    # 五幕改造新增：LLM 总结（Q6 决策：失败任务也生成）
    mission_summary = db.Column(db.Text, default='')
    npc_interactions = db.Column(db.Text, default='')
    player_relationship_change = db.Column(db.Text, default='')

    # 聊天记录（MVP 仅存路径引用）
    chat_log_path = db.Column(db.String(256), default='')
    chat_message_count = db.Column(db.Integer, default=0)

    # 统计
    total_game_days = db.Column(db.Integer, default=0)
    stages_completed = db.Column(db.Integer, default=0)
    is_failed = db.Column(db.Boolean, default=False)   # 是否因超过 end_day 期限而失败结束

    archived_at = db.Column(db.DateTime, default=beijing_now)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'mission_id': self.mission_id,
            'character_name': self.character_name,
            'mission_snapshot': json.loads(self.mission_snapshot) if self.mission_snapshot else {},
            'achievements_snapshot': json.loads(self.achievements_snapshot) if self.achievements_snapshot else [],
            'goal_result': json.loads(self.goal_result) if self.goal_result else {},
            'relationships_snapshot': json.loads(self.relationships_snapshot) if self.relationships_snapshot else [],
            'arc_snapshot': json.loads(self.arc_snapshot) if self.arc_snapshot else {},
            'events_summary': json.loads(self.events_summary) if self.events_summary else {},
            'mission_summary': self.mission_summary or '',
            'npc_interactions': self.npc_interactions or '',
            'player_relationship_change': self.player_relationship_change or '',
            'chat_log_path': self.chat_log_path,
            'chat_message_count': self.chat_message_count,
            'total_game_days': self.total_game_days,
            'stages_completed': self.stages_completed,
            'is_failed': self.is_failed,
            'archived_at': self.archived_at.isoformat() if self.archived_at else None,
        }


class CharacterMemory(db.Model):
    """角色长期记忆（AI伴侣系统升级 — 阶段一）

    存储女主在对话中形成的持久记忆，包括事实、偏好、事件、情感、秘密等。
    每轮对话后由 LLM/本地模型提取，对话前召回注入 prompt，每日 tick 淡化。
    """
    __tablename__ = 'character_memory'

    id = db.Column(db.Integer, primary_key=True)
    character_name = db.Column(db.String(32), index=True, nullable=False)  # 所属角色名
    memory_type = db.Column(db.String(32), nullable=False)  # 记忆类型: fact/preference/event/emotion/secret
    content = db.Column(db.Text, nullable=False)            # 记忆内容摘要
    context = db.Column(db.Text, default='')                # 记忆产生的上下文（对话片段）
    importance = db.Column(db.Float, default=50.0)          # 重要度 0-100（LLM 判定）
    emotional_weight = db.Column(db.Float, default=0.0)     # 情感权重（影响回忆倾向）
    source_day = db.Column(db.Integer, default=0)           # 记忆形成的游戏天
    source_time = db.Column(db.String(12), default='')      # 记忆形成的游戏时间
    access_count = db.Column(db.Integer, default=0)         # 被回忆召回的次数
    last_accessed = db.Column(db.DateTime, nullable=True)   # 最近一次被召回的时间
    created_at = db.Column(db.DateTime, default=beijing_now)
    is_faded = db.Column(db.Boolean, default=False)         # 是否已淡化（低重要度记忆随时间淡化）
    embedding = db.Column(db.LargeBinary, nullable=True)   # 384维 float32 向量（BGE-small-zh 编码）

    def to_dict(self):
        return {
            'id': self.id,
            'character_name': self.character_name,
            'memory_type': self.memory_type,
            'content': self.content,
            'context': self.context,
            'importance': self.importance,
            'emotional_weight': self.emotional_weight,
            'source_day': self.source_day,
            'source_time': self.source_time,
            'access_count': self.access_count,
            'last_accessed': self.last_accessed.isoformat() if self.last_accessed else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_faded': self.is_faded,
        }


class EmotionalMoment(db.Model):
    """高价值情感互动记录（AI伴侣系统升级 — 阶段一）

    记录对话中情感强度较高的时刻，如表白、安慰、冲突、突破等，
    形成"我们的回忆"，可在特定场景下被召回注入 prompt。
    """
    __tablename__ = 'emotional_moment'

    id = db.Column(db.Integer, primary_key=True)
    character_name = db.Column(db.String(32), index=True, nullable=False)  # 所属角色名
    moment_type = db.Column(db.String(32), nullable=False)  # 时刻类型: tender/conflict/breakthrough/memory
    player_message = db.Column(db.Text, default='')         # 玩家发送的消息
    character_reply = db.Column(db.Text, default='')        # 女主的回复
    emotional_intensity = db.Column(db.Float, default=0.0)  # 情感强度 0-100
    summary = db.Column(db.String(256), default='')         # LLM 生成的简短回忆摘要
    game_day = db.Column(db.Integer, default=0)             # 游戏天
    game_time = db.Column(db.String(12), default='')        # 游戏时间
    created_at = db.Column(db.DateTime, default=beijing_now)

    def to_dict(self):
        return {
            'id': self.id,
            'character_name': self.character_name,
            'moment_type': self.moment_type,
            'player_message': self.player_message,
            'character_reply': self.character_reply,
            'emotional_intensity': self.emotional_intensity,
            'summary': self.summary,
            'game_day': self.game_day,
            'game_time': self.game_time,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class QuickHints(db.Model):
    """对话快捷建议持久化（每次对话异步生成后写入）"""
    __tablename__ = 'quick_hints'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    character_name = db.Column(db.String(64), nullable=False, index=True)
    hints = db.Column(ChineseJSON, nullable=False, default=list)  # JSON 数组，如 ["建议1", "建议2", ...]
    game_day = db.Column(db.Integer)
    game_time = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=beijing_now)

    def to_dict(self):
        return {
            'id': self.id,
            'character_name': self.character_name,
            'hints': self.hints or [],
            'game_day': self.game_day,
            'game_time': self.game_time,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class NerdMemo(db.Model):
    """外部 Memos 服务同步的游戏内笔记（玩家日记/备忘录）

    每条记录对应 Memos 实例上的一条 memo。
    游戏内时间 = real_created_at 年份 -2。
    """
    __tablename__ = 'nerd_memo'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    memo_name = db.Column(db.String(64), unique=True, nullable=False, index=True)
    content = db.Column(db.Text, default='')
    snippet = db.Column(db.Text, default='')
    tags = db.Column(db.Text, default='[]')
    importance = db.Column(db.Float, default=50.0)
    embedding = db.Column(db.LargeBinary, nullable=True)
    real_created_at = db.Column(db.DateTime, nullable=False, index=True)
    real_updated_at = db.Column(db.DateTime, nullable=False)
    game_created_at = db.Column(db.DateTime, nullable=False)

    access_count = db.Column(db.Integer, default=0)
    last_accessed = db.Column(db.DateTime, nullable=True)
    is_faded = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=beijing_now)

    def get_game_date(self):
        """返回 (year, month, day) 元组 — 游戏内日期（年份 -2）"""
        dt = self.real_created_at
        return (dt.year - 2, dt.month, dt.day)

    def to_dict(self):
        return {
            'id': self.id,
            'memo_name': self.memo_name,
            'content': self.content,
            'snippet': self.snippet,
            'tags': json.loads(self.tags) if self.tags else [],
            'importance': self.importance,
            'real_created_at': self.real_created_at.isoformat() if self.real_created_at else None,
            'game_created_at': self.game_created_at.isoformat() if self.game_created_at else None,
            'access_count': self.access_count,
        }


class TTSServiceConfig(db.Model):
    """TTS 服务配置（与 tts.py 原生 sqlite 表结构保持一致，供 db.create_all 自动建表）"""
    __tablename__ = 'tts_service_config'

    id = db.Column(db.Integer, primary_key=True)
    service_type = db.Column(db.String(32), default='')
    service_name = db.Column(db.String(128), default='')
    api_url = db.Column(db.String(512), default='')
    api_token = db.Column(db.String(512), default='')
    model_path = db.Column(db.String(512), default='')
    tencent_secret_id = db.Column(db.String(256), default='')
    tencent_secret_key = db.Column(db.String(512), default='')
    tencent_app_id = db.Column(db.String(128), default='')
    is_active = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=beijing_now)
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)


class CharacterVoiceSample(db.Model):
    """角色语音样本（与 tts.py 原生 sqlite 表结构保持一致，供 db.create_all 自动建表）"""
    __tablename__ = 'character_voice_samples'

    id = db.Column(db.Integer, primary_key=True)
    character_id = db.Column(db.Integer, default=0)
    filename = db.Column(db.String(256), default='')
    sample_text = db.Column(db.Text, default='')
    file_path = db.Column(db.String(512), default='')
    created_at = db.Column(db.DateTime, default=beijing_now)


class PromptTemplateDB(db.Model):
    """Prompt 模板持久化 — 用户自定义版本（DB 优先，fallback REGISTRY 默认值）。"""
    __tablename__ = 'prompt_templates'

    id = db.Column(db.String(64), primary_key=True)   # "dialogue.system"
    category = db.Column(db.String(32), default='')
    label = db.Column(db.String(64), default='')
    content = db.Column(db.Text, nullable=False, default='')
    version = db.Column(db.Integer, default=1)
    is_system = db.Column(db.Boolean, default=False)   # True = 🔴 只读
    require_test = db.Column(db.Boolean, default=False) # True = 🟡 需验证
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)


class PromptTemplateVersion(db.Model):
    """Prompt 模板版本历史 — 每次保存自动存档，可回退。"""
    __tablename__ = 'prompt_template_versions'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    prompt_id = db.Column(db.String(64), nullable=False)
    content = db.Column(db.Text, nullable=False, default='')
    version = db.Column(db.Integer, nullable=False)
    updated_at = db.Column(db.DateTime, default=beijing_now)


class PhotoRecord(db.Model):
    """聊天生图产出的照片实体（与 CharacterMemory 长期记忆配对，两写同源）。

    - 不写 EventLog(event_type='photo')，避免稀释【今日事件】。
    - source_day / source_time 与 CharacterMemory 完全同名（同一概念）。
    - scene_memo 由 LLM 压缩（A 主干），vlm_caption 由 VLM 回看（B 增强），
      memo_similarity 为二者余弦一致度，用于 A/B 合流判断。
    """
    __tablename__ = 'photo_record'

    id = db.Column(db.Integer, primary_key=True)
    character_name = db.Column(db.String(32), index=True, nullable=False)
    scene_type = db.Column(db.String(32), default='photo_take')  # 七场景之一 / share_recall
    # 图片与生成
    image_path = db.Column(db.String(512), default='')           # 落盘相对路径
    workflow_id = db.Column(db.Integer, db.ForeignKey('comfyui_workflow.id'), nullable=True)
    seed = db.Column(db.BigInteger, default=0)                   # 锁脸 seed
    prompt_used = db.Column(db.Text, default='')                 # 实际下发 ComfyUI 的提示词
    status = db.Column(db.String(16), default='pending')         # pending/done/failed/drift
    # 文本记忆（A/B 两路）
    scene_memo = db.Column(db.Text, default='')                  # LLM 压缩（女主第一人称记忆句）
    vlm_caption = db.Column(db.Text, default='')                 # VLM 回看描述
    memo_similarity = db.Column(db.Float, default=0.0)           # 余弦一致度
    # 检索与元信息
    gift_name = db.Column(db.String(128), default='')            # 礼物场景的礼物名
    outfit_name = db.Column(db.String(128), default='')          # 换装场景的穿搭名
    location = db.Column(db.String(128), default='')
    source_day = db.Column(db.Integer, default=0)                # 与 CharacterMemory 同名
    source_time = db.Column(db.String(12), default='')
    importance = db.Column(db.Float, default=45.0)
    is_favorite = db.Column(db.Boolean, default=False)           # 收藏则不参与淘汰
    created_at = db.Column(db.DateTime, default=beijing_now)

    def to_dict(self):
        return {
            'id': self.id,
            'character_name': self.character_name,
            'scene_type': self.scene_type,
            'image_path': self.image_path,
            'workflow_id': self.workflow_id,
            'seed': self.seed,
            'prompt_used': self.prompt_used,
            'status': self.status,
            'scene_memo': self.scene_memo,
            'vlm_caption': self.vlm_caption,
            'memo_similarity': self.memo_similarity,
            'gift_name': self.gift_name,
            'outfit_name': self.outfit_name,
            'location': self.location,
            'source_day': self.source_day,
            'source_time': self.source_time,
            'importance': self.importance,
            'is_favorite': self.is_favorite,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class VLMConfig(db.Model):
    """视觉回看（VLM 多模态回看）配置 — provider 可切换，默认 LM Studio。

    与 EmbeddingConfig 同构：is_active 单选互斥（@validates）。
    调用统一走 OpenAI 兼容视觉消息格式，切换 provider 只改 api_url 默认值。
    """
    __tablename__ = 'vlm_config'

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(32), default='lm_studio')
    api_url = db.Column(db.String(512), default='http://localhost:10039/v1/chat/completions')
    api_key = db.Column(db.String(512), default='')
    model_name = db.Column(db.String(128), default='nsfwvision-qwen3-vl-8b-v3')
    max_tokens = db.Column(db.Integer, default=200)
    temperature = db.Column(db.Float, default=0.2)
    timeout = db.Column(db.Integer, default=60)
    is_active = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=beijing_now)
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)

    @validates('is_active')
    def validate_is_active(self, key, value):
        if value:
            existing = VLMConfig.query.filter_by(is_active=True).first()
            if existing and existing.id != self.id:
                existing.is_active = False
        return value

    def to_dict(self, mask_key=True):
        return {
            'id': self.id,
            'provider': self.provider,
            'api_url': self.api_url,
            'api_key': (self.api_key[:8] + '***' if (self.api_key and mask_key) else ''),
            'model_name': self.model_name,
            'max_tokens': self.max_tokens,
            'temperature': self.temperature,
            'timeout': self.timeout,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class ProfessionRule(db.Model):
    """职业关系规则表（数据驱动的职业变量源）。

    所有角色的职业豁免判定都查此表，不写死任何角色名。
    - match_keywords：从 Character.identity_label + major 推断职业的命中词（JSON 列表）
    - scenes：职业场景地点关键词，前半为 venue_id（英文），后半为中文 venue_name 片段（JSON 列表）
    - service_identities：player_identity 中算"服务接受方"的值（JSON 列表）
    """
    __tablename__ = 'profession_rule'

    profession_type = db.Column(db.String(32), primary_key=True)   # 归一化 key，如 doctor
    label = db.Column(db.String(32), default='')                    # 中文名，如 医生
    service_type = db.Column(db.Boolean, default=False)             # 是否专业服务型（决定能否豁免冷淡）
    match_keywords = db.Column(db.Text, default='[]')               # JSON 列表：推断命中词
    scenes = db.Column(db.Text, default='[]')                       # JSON 列表：职业场景地点关键词（venue_id + 中文名）
    relation_label = db.Column(db.String(32), default='')           # 服务接受方称谓，如 病人/客户/乘客
    service_identities = db.Column(db.Text, default='[]')           # JSON 列表：服务接受方身份

    def to_dict(self):
        return {
            'profession_type': self.profession_type,
            'label': self.label,
            'service_type': self.service_type,
            'match_keywords': json.loads(self.match_keywords) if self.match_keywords else [],
            'scenes': json.loads(self.scenes) if self.scenes else [],
            'relation_label': self.relation_label,
            'service_identities': json.loads(self.service_identities) if self.service_identities else [],
        }




class GameSetting(db.Model):
    """全局键值设置（游戏级开关/配置，前端可视可改）

    独立于各 XConfig 表的轻量 KV 存储，用于聊天设置等布尔/字符串开关。
    value 以字符串存取，读取方自行解析（布尔用 'true'/'false'）。
    """
    __tablename__ = 'game_setting'

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, default='')
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)

    def to_dict(self):
        return {'key': self.key, 'value': self.value,
                'updated_at': self.updated_at.isoformat() if self.updated_at else None}


class SmallModelConfig(db.Model):
    """小模型（本地 LM Studio 等 OpenAI 兼容服务）配置持久化。

    与 LLMConfig 同构：is_active 单选互斥（@validates）。
    承接对话情绪属性分析、TTS 语音参数分类等轻量本地推理任务的地址/模型配置。
    """
    __tablename__ = 'small_model_config'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), default='未命名小模型')
    api_url = db.Column(db.String(512), nullable=False, default='http://127.0.0.1:10039/v1')
    api_key = db.Column(db.String(512), default='')
    model_name = db.Column(db.String(128), default='qwen2.5-1.5b-instruct')
    is_active = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=beijing_now)
    updated_at = db.Column(db.DateTime, default=beijing_now, onupdate=beijing_now)

    @validates('is_active')
    def validate_is_active(self, key, value):
        if value:
            # 确保同时只有一个激活的配置
            existing = SmallModelConfig.query.filter_by(is_active=True).first()
            if existing and existing.id != self.id:
                existing.is_active = False
        return value

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'api_url': self.api_url,
            'api_key': self.api_key[:8] + '***' if self.api_key else '',
            'model_name': self.model_name,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_secret_dict(self):
        """返回包含完整 api_key 的字典（仅后端使用）"""
        d = self.to_dict()
        d['api_key'] = self.api_key
        return d
