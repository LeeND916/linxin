"""LLM 工具函数 — API URL 归一化、SSL 修复、日志"""
import os
import sys
import json
import logging
import time
import requests
import traceback
import threading
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from backend.models import LLMConfig


class LLMNetworkError(Exception):
    """LLM API 网络超时或连接错误"""
    pass


# ========== 日志系统 ==========
logger = logging.getLogger('sim_life.llm')
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(logging.Formatter(
        '[%(asctime)s] [LLM-%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    ))
    logger.addHandler(ch)

logger.propagate = False

# ========== 连接池与 Session 复用 ==========

# 模块级 Session：复用 TCP 连接，避免每次请求重建 TLS 握手
_llm_session = None
_llm_session_lock = threading.Lock()
# 请求节流：两次 LLM 请求之间至少间隔 500ms，防止触发限流
_last_request_time = 0.0
_request_interval = 0.5
_request_interval_lock = threading.Lock()


def _get_llm_session() -> requests.Session:
    """获取或创建模块级 Session（线程安全单例）。"""
    global _llm_session
    if _llm_session is None:
        with _llm_session_lock:
            if _llm_session is None:
                _llm_session = requests.Session()
                adapter = HTTPAdapter(
                    pool_connections=1,
                    pool_maxsize=2,  # 最多 2 个并发连接（dialogue + tick）
                    max_retries=0,   # 重试由 safe_llm_post 手动控制
                    pool_block=False,  # 不阻塞，直接使用新连接
                )
                _llm_session.mount('https://', adapter)
                _llm_session.mount('http://', adapter)
                # 启用 HTTP Keep-Alive
                _llm_session.headers.update({
                    'Connection': 'keep-alive',
                })
    return _llm_session


def _throttle_request():
    """节流控制：确保两次 LLM 请求间隔 >= 500ms。"""
    global _last_request_time
    with _request_interval_lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < _request_interval:
            time.sleep(_request_interval - elapsed)
        _last_request_time = time.time()


# ========== SSL 修复 ==========
def fix_ssl_keylog():
    """彻底清除损坏的 SSLKEYLOGFILE 环境变量。

    某些抓包工具（Fiddler/Charles 等）会设置 SSLKEYLOGFILE 指向临时目录，
    该目录可能已被清理。urllib3 读取该变量后尝试写入 TLS 密钥日志文件，
    若目录不存在则抛出 FileNotFoundError，导致所有 HTTPS 连接失败。
    """
    if 'SSLKEYLOGFILE' in os.environ:
        val = os.environ['SSLKEYLOGFILE']
        del os.environ['SSLKEYLOGFILE']
        logger.warning(f"已清除损坏的 SSLKEYLOGFILE: {val}")


def _log_request_start(url, model, msg_count, max_tokens, temperature, call_type="", character_name=""):
    call_type_tag = f"[{call_type}] " if call_type else ""
    char_tag = f" 女主={character_name}" if character_name else ""
    logger.info(f"API请求 {call_type_tag}→ {url}  模型={model} 消息数={msg_count} max_tokens={max_tokens} temperature={temperature}{char_tag}")


def _log_request_result(url, status_code, elapsed_ms, content_len, usage_info=None):
    if 200 <= status_code < 300:
        token_str = ""
        if usage_info:
            prompt_t = usage_info.get('prompt_tokens', '?')
            completion_t = usage_info.get('completion_tokens', '?')
            total_t = usage_info.get('total_tokens', '?')
            token_str = f"，token 用量 {prompt_t} + {completion_t} = {total_t}"
        else:
            token_str = "，token 用量 未知"
        logger.info(f"API响应 ← {url} 状态={status_code} 耗时={elapsed_ms}ms 长度={content_len}{token_str}")
    else:
        logger.error(f"API响应 ← {url} 状态={status_code} 耗时={elapsed_ms}ms 内容={content_len}")


def _log_request_error(url, error, elapsed_ms=0):
    logger.error(f"API异常 ← {url} 类型={type(error).__name__} 耗时={elapsed_ms}ms")
    logger.debug(f"  错误详情: {error}")
    logger.debug(f"  堆栈:\n{traceback.format_exc()}")


def write_llm_log(call_type, url, model, messages, max_tokens, temperature, response, character_name=""):
    """将 LLM 调用详情写入独立 JSON 日志文件"""
    CALL_TYPE_CN = {
        "dialogue": "对话dialogue",
        "event": "事件event",
        "tick": "心跳tick",
        "constraint": "约束constraint",
        "summary": "摘要summary",
        "reflection": "反思reflection",
        "dream": "梦想dream",
        "weather": "天气weather",
        "relationship": "关系relationship",
        "goal": "目标goal",
        "action": "行动action",
        "planning": "规划planning",
        "tick_update": "心跳tick",
        "event_generate": "事件event",
        "emotional_hints": "情感提示emotional_hints",
        "dialogue_recommend": "对话推荐dialogue_recommend",
        "active_message_decide": "主动消息决策active_message_decide",
        "friend_relations_update": "朋友关系更新friend_relations_update",
        "character_generate": "角色生成character_generate",
        "connection_test": "连接测试connection_test",
        "tts_classify": "TTS情绪分类tts_classify",
        "tts_deepseek_modify": "TTS语气修饰tts_deepseek_modify",
        "tts_voice_design": "TTS语音合成tts_voice_design",
        "tts_tencent_classify": "TTS情绪分类tts_tencent_classify",
        "tts_tencent": "TTS语音合成tts_tencent",
        "emotion_local_qwen": "本地情绪分析local_qwen",
        "scene_freeze_qwen": "场景定格概括scene_freeze_qwen",
        "memory_extract": "记忆提取memory_extract",
    }
    try:
        base_log_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "llm_logs"
        )

        ts = datetime.now()
        date_subdir = ts.strftime("%Y-%m-%d")
        log_dir = os.path.join(base_log_dir, date_subdir)
        os.makedirs(log_dir, exist_ok=True)

        char_name = character_name if character_name else "未知角色"
        call_type_cn = CALL_TYPE_CN.get(call_type, call_type)
        filename = f"{ts.strftime('%H%M%S')}_{char_name}_{call_type_cn}.json"
        filepath = os.path.join(log_dir, filename)

        log_entry = {
            "timestamp": ts.isoformat(),
            "call_type": call_type,
            "request": {
                "url": url,
                "model": model,
                "messages_count": len(messages),
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            "response": response,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(log_entry, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 日志写入失败不影响主流程


def write_comfyui_log(call_type, character_name, request_info, response_info):
    """将 ComfyUI 调用详情写入独立 JSON 日志文件（格式与 write_llm_log 完全一致）

    日志目录: 项目根/llm_logs/YYYY-MM-DD/
    文件名:   {HHMMSS}_{角色名}_{类型中文}.json
    结构:     {timestamp, call_type, request, response}

    与 LLM 日志共用的唯一差异是 request/response 的具体字段：
    - request  改为 ComfyUI 的 url / 提示词 / 尺寸 / seed / steps 等
    - response 改为 prompt_id / 成功标志 / image_url / 耗时 / 错误等

    Args:
        call_type:     调用类型标识，如 "comfyui"
        character_name:角色名（用于文件名，缺失时记为「未知角色」）
        request_info:  dict，请求信息
        response_info: dict，响应信息
    """
    CALL_TYPE_CN = {
        "comfyui": "ComfyUI图像生成comfyui",
        # 聊天拍照生图（photo_gen）按场景类型区分，便于按场景查阅
        "photo_take": "拍照photo_take",
        "selfie": "自拍selfie",
        "activity_shot": "活动特写activity_shot",
        "scene_freeze": "场景定格scene_freeze",
        "outfit_change": "换装outfit_change",
        "gift_photo": "送礼gift_photo",
        "share_new": "主动展示share_new",
    }
    try:
        base_log_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "llm_logs"
        )

        ts = datetime.now()
        date_subdir = ts.strftime("%Y-%m-%d")
        log_dir = os.path.join(base_log_dir, date_subdir)
        os.makedirs(log_dir, exist_ok=True)

        char_name = character_name if character_name else "未知角色"
        call_type_cn = CALL_TYPE_CN.get(call_type, call_type)
        filename = f"{ts.strftime('%H%M%S')}_{char_name}_{call_type_cn}.json"
        filepath = os.path.join(log_dir, filename)

        log_entry = {
            "timestamp": ts.isoformat(),
            "call_type": call_type,
            "request": request_info,
            "response": response_info,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(log_entry, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 日志写入失败不影响主流程


def strip_reasoning_from_response(result: dict) -> None:
    """递归清除 API 响应中的 reasoning_content 字段。

    部分模型/API（如 deepseek-reasoner）会在 choices[].message 中注入
    reasoning_content，导致下游提取 content 时被污染。此函数原地删除。
    """
    if not isinstance(result, dict):
        return
    # 处理 choices 数组中的 message
    for choice in result.get('choices', []) or []:
        if isinstance(choice, dict):
            msg = choice.get('message')
            if isinstance(msg, dict):
                msg.pop('reasoning_content', None)
    # 递归清理嵌套结构（防御性处理）
    for val in list(result.values()):
        if isinstance(val, dict):
            strip_reasoning_from_response(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    strip_reasoning_from_response(item)


# 记录“不支持 thinking 参数”的 (api_url, model)，命中后直接不再发送，避免反复 400
_no_thinking_support: set = set()


def safe_llm_post(api_url, api_key, model, messages,
                  max_tokens=300, temperature=0.85, timeout=(10, 60),
                  call_type="unknown", character_name="",
                  extra_body=None) -> dict | None:
    """安全地调用 LLM API（兼容 OpenAI/DeepSeek 格式），返回响应 JSON 或 None。

    内置 SSLKEYLOGFILE 修复、超时处理、连接失败自动重试、详细日志、调用日志持久化。
    使用模块级 Session 复用 TCP 连接，内置请求节流防止限流。

    timeout 参数：
        - tuple (connect_timeout, read_timeout)：分别控制连接和读取超时，默认 (10, 60)
        - int/float：会被自动转换为 (10, int_value)，保留向后兼容
    """
    fix_ssl_keylog()

    # 请求节流：确保两次调用间隔 >= 500ms
    _throttle_request()

    # 归一化 timeout：单值自动转为 (10, read_timeout)
    if isinstance(timeout, (int, float)):
        timeout = (10, timeout)
    connect_timeout, read_timeout = timeout

    url = normalize_api_url(api_url)
    _log_request_start(url, model, len(messages), max_tokens, temperature, call_type, character_name)

    # 日志记录变量
    log_response = {
        "success": False,
        "status_code": None,
        "content": None,
        "error": None,
        "elapsed_ms": None,
    }
    result = None

    # 使用模块级 Session 复用 TCP 连接
    session = _get_llm_session()

    max_attempts = 3                     # 最多 3 次尝试
    retry_delay = 5.0                    # 重试间隔 5 秒
    backoff_multiplier = 2.0             # 指数退避倍数

    stripped = False
    for attempt in range(max_attempts):
        delay = retry_delay * (backoff_multiplier ** attempt)
        t0 = datetime.now()

        # 组装请求体；stripped=True（上次因 thinking 不受支持被 400）时不再合并 extra_body
        req_body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if extra_body and not stripped and (normalize_api_url(api_url), model) not in _no_thinking_support:
            req_body.update(extra_body)

        try:
            resp = session.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json=req_body,
                timeout=timeout
            )
            elapsed = (datetime.now() - t0).total_seconds() * 1000
            body = resp.text

            log_response["status_code"] = resp.status_code
            log_response["elapsed_ms"] = int(elapsed)

            if resp.status_code == 200:
                result = resp.json()
                # 提取 token 用量（DeepSeek/OpenAI 格式）
                usage_info = None
                if isinstance(result, dict):
                    usage_info = result.get('usage')
                log_response["success"] = True
                log_response["content"] = result
                _log_request_result(url, resp.status_code, int(elapsed), len(body), usage_info)
                break

            elif resp.status_code == 429:
                # 限流：读取 Retry-After header，使用较大值
                retry_after = resp.headers.get('Retry-After', '')
                if retry_after.isdigit():
                    delay = float(retry_after)
                else:
                    delay = max(delay, 10.0)  # 限流最少等 10s
                _log_request_result(url, resp.status_code, int(elapsed), len(body))
                logger.warning(
                    f"  API限流(429)，{delay:.0f}秒后重试 (第{attempt+1}/{max_attempts}次)"
                )
                log_response["error"] = f"HTTP 429 Rate Limited"
                if attempt < max_attempts - 1:
                    time.sleep(delay)
                    continue
                break

            elif resp.status_code >= 500:
                # 服务端错误，可重试
                _log_request_result(url, resp.status_code, int(elapsed), len(body))
                logger.error(
                    f"  API服务端错误(HTTP {resp.status_code}): {body[:300]}"
                )
                log_response["error"] = f"HTTP {resp.status_code}: {body[:200]}"
                if attempt < max_attempts - 1:
                    logger.warning(
                        f"  {delay:.0f}秒后重试 (第{attempt+1}/{max_attempts}次)"
                    )
                    time.sleep(delay)
                    continue
                break

            elif resp.status_code == 400 and extra_body and not stripped and 'thinking' in body.lower():
                # 模型不支持 thinking 参数（如 deepseek-chat / 非推理别名），
                # 去掉后重试一次，并记入负缓存，后续同 (url, model) 直接不发
                _no_thinking_support.add((normalize_api_url(api_url), model))
                stripped = True
                _log_request_result(url, resp.status_code, int(elapsed), len(body))
                logger.warning(
                    f"  thinking 参数不被支持，去掉后重试 (第{attempt+1}/{max_attempts}次)"
                )
                log_response["error"] = f"HTTP 400 (thinking unsupported)"
                if attempt < max_attempts - 1:
                    continue
                break
            else:
                # 4xx 非 429 错误不重试
                _log_request_result(url, resp.status_code, int(elapsed), len(body))
                logger.error(f"  API返回错误: {body[:500]}")
                log_response["error"] = f"HTTP {resp.status_code}: {body[:200]}"
                break

        except requests.exceptions.Timeout as e:
            elapsed = int((datetime.now() - t0).total_seconds() * 1000)
            _log_request_error(url, e, elapsed)
            logger.error(
                f"  请求超时 (connect={connect_timeout}s, read={read_timeout}s)，"
                f"请检查网络或增大超时"
            )
            log_response["error"] = str(e)
            log_response["elapsed_ms"] = elapsed
            if attempt < max_attempts - 1:
                logger.warning(
                    f"  {delay:.0f}秒后重试 (第{attempt+1}/{max_attempts}次)"
                )
                time.sleep(delay)
                continue
            break

        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError) as e:
            # RemoteDisconnected 是 urllib3 异常，被 requests 包装为
            # ConnectionError（ProtocolError 子类）或 ChunkedEncodingError
            elapsed = int((datetime.now() - t0).total_seconds() * 1000)
            _log_request_error(url, e, elapsed)
            log_response["elapsed_ms"] = elapsed
            log_response["error"] = str(e)
            if attempt < max_attempts - 1:
                logger.warning(
                    f"  连接断开 ({type(e).__name__})，"
                    f"{delay:.0f}秒后重试 (第{attempt+1}/{max_attempts}次)"
                )
                time.sleep(delay)
                continue
            logger.error(
                f"  连接失败（已重试{max_attempts}次）— "
                f"请检查 API URL 是否正确、网络是否可达"
            )
            break

        except Exception as e:
            elapsed = int((datetime.now() - t0).total_seconds() * 1000)
            _log_request_error(url, e, elapsed)
            log_response["error"] = str(e)
            log_response["elapsed_ms"] = elapsed
            break

    # 写入调用日志文件
    write_llm_log(call_type, url, model, messages, max_tokens, temperature, log_response, character_name)

    return result


def normalize_api_url(url):
    """自动补全 API URL 路径，确保指向 chat/completions 端点。
    DeepSeek: https://api.deepseek.com → https://api.deepseek.com/v1/chat/completions
    OpenAI:   https://api.openai.com   → https://api.openai.com/v1/chat/completions
    如果 URL 已经包含 /chat/completions 则不做处理。
    """
    url = url.rstrip('/')
    if url.endswith('/chat/completions'):
        return url
    if url.endswith('/v1'):
        return url + '/chat/completions'
    return url + '/v1/chat/completions'


def get_active_llm_config() -> dict | None:
    """从数据库获取当前激活的 LLM 配置"""
    config = LLMConfig.query.filter_by(is_active=True).first()
    if not config or not config.api_key:
        return None
    return config.to_secret_dict()


# ========== 游戏时间 → 时段描述 ==========

TIME_PERIOD_MAP = [
    (6, 8,   '清晨', '刚起床，可能犯困，准备开始新的一天'),
    (8, 12,  '上午', '精力充沛，适合学习/工作'),
    (12, 14, '中午', '刚吃完午饭，可能有点困'),
    (14, 18, '下午', '继续学习/活动'),
    (18, 21, '傍晚/晚上', '放松时间，可能在看剧/散步'),
    (21, 23, '深夜', '准备休息，有点疲惫'),
    (23, 24, '凌晨', '应该已经睡了，如果还醒着说明失眠或有心事'),
    (0, 6,   '凌晨', '应该已经睡了，如果还醒着说明失眠或有心事，不会出门'),
]

# 每个时段对应的「单条行为约束」。
# 改造后只注入「当前时段」的那一条，避免把一整天的互斥规则全塞给模型导致它纠结/串时段。
# key 必须与 TIME_PERIOD_MAP 中的 period_name 完全一致。
TIME_PERIOD_CONSTRAINTS = {
    '清晨':      '刚起床，精力尚未完全恢复，不适合高强度活动',
    '上午':      '精力充沛，适合上课/学习/运动/工作，不要提午休或吃饭约饭',
    '中午':      '刚吃完午饭可能犯困，适合午休/闲聊，语气可轻松慵懒',
    '下午':      '精力回升，适合继续学习/活动/社交',
    '傍晚/晚上': '放松时间，可能在看剧/散步/吃饭，不要太正式',
    '深夜':      '准备休息，有点疲惫，不要约出门或说刚吃完饭',
    '凌晨':      '应该已经睡了，被吵醒才醒，不会主动约外出活动',
}


def get_time_period_info(game_hour: int) -> dict:
    """根据游戏小时返回时段信息。

    Returns:
        {'period_name': '上午', 'period_desc': '精力充沛，适合学习/工作', 'hour': game_hour}
    """
    for start, end, name, desc in TIME_PERIOD_MAP:
        if start <= game_hour < end:
            return {'period_name': name, 'period_desc': desc, 'hour': game_hour}
    # fallback
    return {'period_name': '白天', 'period_desc': '日常时间', 'hour': game_hour}


def get_time_period_prompt(char) -> str:
    """构建游戏时间 + 时段描述的 Prompt 片段。

    用于注入到对话 System Prompt、事件生成 Prompt、tick 更新 Prompt 中。
    """
    game_day = getattr(char, 'game_day', 0)
    game_hour = getattr(char, 'game_hour', 8)
    game_minute = getattr(char, 'game_minute', 0)
    game_second = getattr(char, 'game_second', 0)
    return get_time_period_prompt_for_hour(game_day, game_hour, game_minute, game_second)


def get_time_period_prompt_for_hour(game_day: int, game_hour: int,
                                     game_minute: int = 0,
                                     game_second: int = 0) -> str:
    """根据指定的游戏天数和小时构建时间段 Prompt，不依赖 Character 对象。

    用于事件生成场景：事件时间可能与角色当前时间不同（如批量 tick 时错时生成）。"""
    info = get_time_period_info(game_hour)

    # 防御性转 int（game_hour 可能在 DB 中为 float）
    gh = int(game_hour)
    gm = int(game_minute)

    period = info['period_name']
    rule = TIME_PERIOD_CONSTRAINTS.get(period, info['period_desc'])

    return f"""【当前游戏时间】第{game_day}天 {gh:02d}:{gm:02d}
时段：{period}（{info['period_desc']}）

【时段行为约束】你现在处于「{period}」，所有回复和行为必须与当前时段相符：
- {rule}
- 无论何时，都不要编造或混淆当前日期/时间/天气，所有行为须与上面给定的时段一致，不要参考历史对话里的时间。"""
