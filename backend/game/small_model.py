# -*- coding: utf-8 -*-
"""远程小模型客户端（LM Studio / 任意 OpenAI 兼容接口）。

替代原「本地 Qwen2-1.5B-Instruct」方案（models/Qwen2-1.5B-Instruct + transformers 本地加载）。
承接两类原先依赖本地小模型的调用：
  1. 对话情绪属性分析（dialogue.py -> analyze_emotion）
  2. 分类任务（classifier_server 的 /classify、/classify/tencent）

配置来源：SmallModelConfig 表（前端「系统设置 -> 小模型」可增删改、单选激活）。
  5080 主进程通过 ORM 读取；classifier_server(9803) 是独立进程，ORM 不可用，
  自动回退为直接 sqlite3 读取同一张 small_model_config 表（见 _read_active_from_sqlite）。

设计约定：
  - 不做兜底：调用失败/解析失败一律返回空结果，由调用方决定如何表现；
    不再回退到本地模型、远程大模型或 Python 规则引擎。
"""

import json
import logging
import os
import re
import sqlite3
import threading
import time

import requests

logger = logging.getLogger('sim_life.small_model')

# 默认配置（首次启动时 app.py 会预置一条指向本地 LM Studio 的激活配置；
# 表为空/未激活时此处作为最后兜底，与预置值保持一致）
# 实际地址由 Config.SMALL_MODEL_URL 解析（env → local_config.py → localhost 默认），
# 源码里不留任何内网/个人地址。
def _default_small_model_url() -> str:
    try:
        from backend.config import Config
        return getattr(Config, 'SMALL_MODEL_URL', '') or 'http://127.0.0.1:10039/v1'
    except Exception:                     # 独立进程（如分类器）导入失败时的兜底
        return 'http://127.0.0.1:10039/v1'


DEFAULT_SMALL_MODEL_URL = _default_small_model_url()
DEFAULT_SMALL_MODEL_NAME = 'qwen2.5-1.5b-instruct'
DEFAULT_SMALL_MODEL_API_KEY = ''

# SQLite 直读路径（供独立进程 9803 使用）
_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data', 'game.db',
)

# 配置缓存（避免每次调用都读库；TTL 内复用）
_CACHE = {'cfg': None, 'ts': 0}
_CACHE_TTL = 30  # 秒
_cache_lock = threading.Lock()


def normalize_base_url(url: str) -> str:
    """把用户填的地址规整成 OpenAI 兼容 base（结尾不带斜杠，且以 /v1 结尾）。

    允许用户填 http://host:port 或 http://host:port/v1 或带 /chat/completions，
    统一收敛为 base，避免拼接出 /v1/v1 或 /chat/completions/chat/completions。
    """
    u = (url or '').strip().rstrip('/')
    if not u:
        return DEFAULT_SMALL_MODEL_URL
    # 用户若把完整端点填进来，截断到 base
    u = re.sub(r'/chat/completions$', '', u).rstrip('/')
    if not re.search(r'/v\d+$', u):
        u = u + '/v1'
    return u


def _read_active_from_sqlite() -> dict | None:
    """供独立进程（如 9803 classifier_server）直读 small_model_config 表。

    不依赖 app context / ORM，直接打开同一份 data/game.db 文件读激活行。
    """
    try:
        if not os.path.exists(_DB_PATH):
            return None
        conn = sqlite3.connect(_DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, api_url, api_key, model_name "
            "FROM small_model_config WHERE is_active=1 LIMIT 1"
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return {
            'id': row['id'],
            'name': row['name'],
            'api_url': row['api_url'],
            'api_key': row['api_key'] or '',
            'model_name': row['model_name'],
        }
    except Exception as e:
        logger.debug(f'[SmallModel] sqlite 直读失败（将用默认兜底）: {e}')
        return None


def get_active_small_model() -> dict:
    """读取当前激活的小模型配置。

    读取优先级：
      1. 进程内缓存（TTL 内）
      2. 5080 主进程走 ORM（SmallModelConfig）
      3. 独立进程回退为 sqlite3 直读 small_model_config 表
      4. 以上皆空 -> 使用默认常量兜底
    返回的 api_url 已规整为 base（以 /v1 结尾）。
    """
    now = time.time()
    with _cache_lock:
        if _CACHE['cfg'] and (now - _CACHE['ts']) < _CACHE_TTL:
            return _CACHE['cfg']

    cfg = None
    # 优先走 ORM（仅 5080 主进程可用）
    try:
        from backend.models import SmallModelConfig
        row = SmallModelConfig.query.filter_by(is_active=True).first()
        if row:
            cfg = {
                'id': row.id,
                'name': row.name,
                'api_url': row.api_url,
                'api_key': row.api_key or '',
                'model_name': row.model_name,
            }
    except Exception as e:
        logger.debug(f'[SmallModel] ORM 读取失败，回退 sqlite: {e}')

    # 独立进程 / ORM 不可用时回退 sqlite3 直读
    if cfg is None:
        cfg = _read_active_from_sqlite()

    # 兜底默认
    if cfg is None:
        cfg = {
            'id': None,
            'name': '默认小模型',
            'api_url': DEFAULT_SMALL_MODEL_URL,
            'api_key': DEFAULT_SMALL_MODEL_API_KEY,
            'model_name': DEFAULT_SMALL_MODEL_NAME,
        }

    cfg['api_url'] = normalize_base_url(cfg['api_url'])
    with _cache_lock:
        _CACHE['cfg'] = cfg
        _CACHE['ts'] = now
    return cfg


def _headers(cfg: dict) -> dict:
    h = {'Content-Type': 'application/json'}
    if cfg.get('api_key'):
        h['Authorization'] = f'Bearer {cfg["api_key"]}'
    return h


def chat(messages: list, max_tokens: int = 200, temperature: float = 0.0,
         character_name: str = '', call_type: str = 'small_model',
         timeout=(5, 120), extra_body: dict = None) -> str:
    """调用远程小模型，返回正文文本；失败抛异常（由调用方决定是否吞掉）。

    Args:
        messages: OpenAI 格式消息列表
        max_tokens/temperature: 采样参数（情绪/分类任务默认贪心）
        call_type/character_name: 仅用于写入 llm_logs
    """
    cfg = get_active_small_model()
    url = f"{cfg['api_url']}/chat/completions"
    payload = {
        'model': cfg['model_name'],
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': temperature,
        'stream': False,
    }
    if extra_body:
        payload.update(extra_body)

    from backend.game.llm_utils import write_llm_log

    t0 = time.time()
    status_code = -1
    content = ''
    error = None
    try:
        resp = requests.post(url, headers=_headers(cfg), json=payload, timeout=timeout)
        status_code = resp.status_code
        resp.raise_for_status()
        data = resp.json()
        content = (data.get('choices') or [{}])[0].get('message', {}).get('content', '') or ''
    except Exception as e:
        error = str(e)
        logger.warning(f'[SmallModel] 调用失败 ({url}, model={cfg["model_name"]}): {e}')
    finally:
        elapsed_ms = int((time.time() - t0) * 1000)
        try:
            write_llm_log(
                call_type=call_type,
                url=url,
                model=cfg['model_name'],
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                response={
                    'success': error is None,
                    'status_code': status_code,
                    'content': content,
                    'error': error,
                    'elapsed_ms': elapsed_ms,
                },
                character_name=character_name,
            )
        except Exception:
            pass

    if error is not None:
        raise RuntimeError(error)
    return content


def _parse_json_dict(text: str) -> dict:
    """从模型输出中解析出 JSON 对象；失败返回 {}。"""
    if not text:
        return {}
    s = text.strip()
    # 去掉 ```json 围栏
    s = re.sub(r'^```(?:json)?\s*|\s*```$', '', s, flags=re.I | re.M).strip()
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    m = re.search(r'\{[\s\S]*\}', s)
    if m:
        try:
            obj = json.loads(m.group())
            return obj if isinstance(obj, dict) else {}
        except Exception:
            pass
    return {}


def analyze_emotion(prompt: str, max_tokens: int = 200, character_name: str = '') -> dict:
    """用远程小模型分析情绪属性变化，返回 {属性名: delta}；失败返回 {}。

    接口与原 local_model.analyze_emotion_with_qwen 保持一致，便于调用方平滑迁移。
    """
    messages = [
        {'role': 'system', 'content': '你是游戏引擎的情绪属性解析模块。只返回JSON，不要任何解释。'},
        {'role': 'user', 'content': prompt},
    ]
    try:
        content = chat(messages, max_tokens=max_tokens, temperature=0.0,
                       character_name=character_name, call_type='emotion_small_model')
    except Exception as e:
        logger.warning(f'[SmallModel] 情绪分析失败（无兜底，返回空）: {e}')
        return {}

    data = _parse_json_dict(content)
    if not data:
        logger.warning(f'[SmallModel] 情绪分析 JSON 解析失败（无兜底）: {(content or "")[:120]}')
        return {}
    # 兼容 {"changes": {...}} 与直接 {...}
    changes = data.get('changes', data) if isinstance(data.get('changes', data), dict) else data
    out = {}
    for k, v in (changes or {}).items():
        if isinstance(v, (int, float)):
            out[k] = float(v)
    return out


def is_available(timeout=5) -> bool:
    """探活：请求 /models 判断服务与模型是否可访问（供设置页「测试连接」与状态展示）。"""
    cfg = get_active_small_model()
    try:
        resp = requests.get(f"{cfg['api_url']}/models", headers=_headers(cfg), timeout=timeout)
        if resp.status_code != 200:
            return False
        models = [m.get('id', '') for m in (resp.json().get('data') or [])]
        return cfg['model_name'] in models if models else True
    except Exception as e:
        logger.debug(f'[SmallModel] 探活失败: {e}')
        return False
