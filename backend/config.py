import os
from datetime import datetime, timezone, timedelta

# 用户 sim_life 数据目录（与项目根目录分离，便于跨版本保留数据）
USER_SIM_LIFE = os.path.join(
    os.environ.get('USERPROFILE') or os.path.expanduser('~'), 'sim_life')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)

# ── 本机私有配置（可选，不随仓库分发）────────────────────────────
# 用来放「只属于你这台机器」的地址与路径，避免硬编码进源码。
# 用法：复制 local_config.example.py 为 local_config.py，按需填写。
try:
    from backend import local_config as _local_cfg          # 包方式导入（app.py 主入口）
except ImportError:                                          # pragma: no cover
    try:
        import local_config as _local_cfg                    # 脚本直接运行时
    except ImportError:
        class _local_cfg:                                    # 没有本地配置时的空壳
            pass


def _resolve(name, default=''):
    """配置优先级：环境变量 → local_config.py → 中性默认值。

    两个目的：
      1. 本机私有地址/路径不写进源码（放 local_config.py 或环境变量）；
      2. 社区用户什么都不配也能跑（落到 localhost 默认值）。
    """
    value = os.environ.get(name)
    if value:
        return value
    value = getattr(_local_cfg, name, None)
    if value:
        return value
    return default

# 北京时间（UTC+8）时区对象
_BEIJING_TZ = timezone(timedelta(hours=8))


def beijing_now():
    """返回北京时间（UTC+8）的 naive datetime 对象（兼容 SQLAlchemy DateTime 列）。

    返回不含 tzinfo 的 datetime，与原先 datetime.utcnow() 的返回类型一致，
    只是值从 UTC 变为北京时间。
    """
    return datetime.now(_BEIJING_TZ).replace(tzinfo=None)

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'sim-life-game-secret-key-dev')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL',
        f'sqlite:///{os.path.join(PROJECT_ROOT, "data", "game.db")}')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # SQLite busy_timeout：写锁占用时其他连接最多等待的秒数（兜底用）。
    # 注：tick 批量推进已在每次 LLM 调用前显式 commit，写事务不再跨过 LLM 等待期，
    # 因此正常情况下前台保存几乎不会被阻塞；此处仅作为短暂重叠的兜底重试窗口。
    SQLALCHEMY_ENGINE_OPTIONS = {
        # timeout: 写锁占用时等待最多 60s，而非立即报 database is locked
        # check_same_thread=False: 允许后台 daemon 线程与请求线程共用连接池
        'connect_args': {'timeout': 60, 'check_same_thread': False},
        'pool_pre_ping': True,
    }

    # LLM API 配置（支持 OpenAI 兼容接口）
    LLM_API_URL = os.environ.get('LLM_API_URL', 'https://api.openai.com/v1/chat/completions')
    LLM_API_KEY = os.environ.get('LLM_API_KEY', '')
    LLM_MODEL = os.environ.get('LLM_MODEL', 'gpt-4o-mini')

    # 游戏参数
    TICK_INTERVAL_SECONDS = 60          # 属性自动衰减间隔
    # 主动消息冷却配置（LLM 驱动模式下使用）
    ACTIVE_MESSAGE_COOLDOWN_DEFAULT = 3     # 默认冷却 tick 数（Tier2-3）
    ACTIVE_MESSAGE_COOLDOWN_TIER4 = 2       # Tier4 灵魂伴侣：冷却更短
    ACTIVE_MESSAGE_COOLDOWN_TIER01 = 10     # Tier0-1：冷却很长，几乎不主动联系
    DECAY_RATE = 0.5                    # 每 tick 属性衰减量

    # ── 外部天气（Open-Meteo，免费无需 Key）──
    # 开启后：生成天气优先用角色 hometown 城市查 Open-Meteo 真实天气；
    # 查不到（无网络 / 游戏日期超出可查窗口）时回退到「季节修正」的硬规则。
    EXTERNAL_WEATHER_ENABLED = os.environ.get('EXTERNAL_WEATHER_ENABLED', 'true').lower() != 'false'
    WEATHER_CITY_DEFAULT = os.environ.get('WEATHER_CITY_DEFAULT', '北京')  # 角色无 hometown 时的兜底城市

    # 角色初始设置
    CHARACTER_NAME = "晓月"  # 保留向后兼容
    CHARACTER_NAMES = ["晓月"]  # 初始只有晓月
    CHARACTER_AGE = 20
    CHARACTER_GENDER = "female"

    # ── Qwen3-TTS 情绪分类器方案配置 ──
    # 私有部署地址请填到 backend/local_config.py（或同名环境变量），不要写在这里
    QWEN3_TTS_SERVICE_URL = _resolve('QWEN3_TTS_SERVICE_URL', 'http://127.0.0.1:9802')
    QWEN3_CLASSIFIER_URL = _resolve('QWEN3_CLASSIFIER_URL', 'http://127.0.0.1:9803')
    QWEN3_TTS_MODEL_PATH = _resolve('QWEN3_TTS_MODEL_PATH', '')
    INSTRUCT_DEBOUNCE_ROUNDS = 2

    # TTS 服务类型定义
    TTS_SERVICE_TYPES = {
        "voice_design": "Qwen3-TTS-12Hz-1.7B-VoiceDesign（文本描述音色）",
        "base": "Qwen3-TTS-12Hz-1.7B-Base（上传音频克隆）",
        "tencent_tts": "腾讯云 TTS（云端合成，低延迟）",
    }

    # ── Memos 外部服务配置 ──
    # 实际生效的是前端配置页保存到 DB 的 MemosConfig；此项仅作兜底
    MEMOS_BASE_URL = _resolve('MEMOS_BASE_URL', '')
    MEMOS_SYNC_INTERVAL = int(os.environ.get('MEMOS_SYNC_INTERVAL', '30'))   # 增量同步间隔（秒）
    MEMOS_SYNC_START_DELAY = int(os.environ.get('MEMOS_SYNC_START_DELAY', '60'))  # 启动后首次同步延迟（秒）

    # ── 本机可执行文件 / 本地模型地址（同样走 local_config，源码里不留个人路径）──
    COMFYUI_EXE_PATH = _resolve('COMFYUI_EXE_PATH', '')
    SMALL_MODEL_URL = _resolve('SMALL_MODEL_URL', 'http://127.0.0.1:1234/v1')
