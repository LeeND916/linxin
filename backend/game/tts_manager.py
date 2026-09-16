# -*- coding: utf-8 -*-
"""
TTS voice synthesis manager (Qwen3-TTS + Tencent Cloud TTS)

Responsibilities:
  1. Emotion classifier call
  2. Rule engine fallback
  3. Instruct generation
  4. DeepSeek tone modification (optional)
  5. Qwen3-TTS VoiceDesign/Base synthesis
  6. Tencent Cloud TTS synthesis
  7. Audio file saving
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime
from typing import Optional, List, Dict

import requests

from backend.game.llm_utils import write_llm_log
from backend.game.qwen3_tts_engine import Qwen3TTSEngine
from backend.game.tencent_tts_engine import TencentTTSEngine
from backend.game.classifier_server import TENCENT_CLASSIFY_PROMPT
from backend.config import USER_SIM_LIFE

logger = logging.getLogger(__name__)

GAME_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

REQUEST_TIMEOUT = 30

DEFAULT_CLASSIFIER_URL = "http://127.0.0.1:9803"
_DB_PATH = os.path.join(GAME_ROOT, 'data', 'game.db')
CLASSIFIER_SCRIPT = os.path.join(
    os.path.dirname(__file__), "classifier_server.py"
)
CUDA_PYTHON = r"H:\qwen3-tts-models\venv\Scripts\python.exe"
SERVICE_START_TIMEOUT = 120
SERVICE_POLL_INTERVAL = 3

DEFAULT_TTS_SERVICE_URL = "http://127.0.0.1:9802"
TTS_SCRIPT = r"H:\qwen3-tts-models\server.py"
TTS_BASE_SCRIPT = r"H:\qwen3-tts-models\app.py"

# TTS 试听缓存目录
_TTS_CACHE_DIR = os.path.join(USER_SIM_LIFE, 'tts_cache')

# -- rule engine fallback --

_RULE_KEYWORDS = {
    "happy":     ["haha", "happy", "great", "wonderful", "love", "glad", "hehe", "nice"],
    "sad":       ["sad", "upset", "cry", "miserable", "sorrow", "tears", "lost", "sigh"],
    "angry":     ["angry", "furious", "hate", "get out", "annoying", "damn", "bastard", "rage"],
    "surprised": ["oh my", "actually", "what", "wow", "no way", "surprising", "ouch"],
    "tender":    ["it's ok", "don't be afraid", "i'm here", "good boy", "hug", "gentle", "heartache", "love you"],
    "wronged":   ["unfair", "clearly", "why", "unjust"],
    "tired":     ["tired", "sleepy", "rest", "exhausted", "no energy"],
    "playful":   ["teasing", "tricked", "kidding", "playful"],
    "anxious":   ["worried", "what to do", "nervous", "anxious", "too late", "done for"],
    "neutral":   [],
}


def _rule_classify(text):
    text_lower = text.lower()
    emotion_hits = {}
    for emotion, keywords in _RULE_KEYWORDS.items():
        score = 0
        for kw in keywords:
            if kw in text or kw in text_lower:
                score += 1
        if score > 0:
            emotion_hits[emotion] = score

    if not emotion_hits:
        return {"emotion": "neutral", "intensity": 0.3, "confidence": 0.5}

    best_emotion = max(emotion_hits, key=emotion_hits.get)
    hit_count = emotion_hits[best_emotion]
    intensity = min(0.99, 0.3 + hit_count * 0.15)
    confidence = min(0.75, 0.4 + hit_count * 0.1)

    logger.info(
        f"[TTS RuleEngine] text_len={len(text)}, "
        f"emotion={best_emotion}, intensity={intensity:.2f}, "
        f"confidence={confidence:.2f}, hits={hit_count}"
    )
    return {"emotion": best_emotion, "intensity": intensity, "confidence": confidence}


# -- TTS Manager --

class TTSManager:
    """TTS voice synthesis manager (Qwen3-TTS + Tencent Cloud TTS)"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        self._classifier_url = os.environ.get(
            'QWEN3_CLASSIFIER_URL', DEFAULT_CLASSIFIER_URL
        )
        self._tts_service_url = os.environ.get(
            'QWEN3_TTS_SERVICE_URL', DEFAULT_TTS_SERVICE_URL
        )
        self._available = False
        self._classifier_launched = False
        self._last_audio_path = None

    def _get_active_tts_service(self) -> Optional[dict]:
        try:
            conn = sqlite3.connect(_DB_PATH)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, service_type, service_name, api_url, model_path "
                "FROM tts_service_config WHERE is_active = 1 LIMIT 1"
            ).fetchone()
            conn.close()
            if row:
                return dict(row)
            return None
        except Exception as e:
            logger.warning(f"[TTS] Failed to read service config from DB: {e}")
            return None

    def _get_character_voice_sample(self, character_name: str, emotion: str = None) -> Optional[dict]:
        """返回角色的参考音频与文字稿（base 模式 ICL 克隆用）。

        返回 {'file_path': str, 'ref_text': Optional[str]} 或 None。
        ref_text 优先级：character_voice_samples.sample_text > 同目录 reference.txt。

        emotion 维度（仅 base 模式使用）：当传入非 neutral 的情绪且存在对应
        子目录 <角色名>/<emotion>/reference.wav 时优先采用，否则回落默认
        <角色名>/reference.wav。这样可让 base 模型按情绪做不同语气的 ICL，
        而无需 instruct（base 模型不支持 instruct）。
        """
        try:
            conn = sqlite3.connect(_DB_PATH)
            conn.row_factory = sqlite3.Row
            char_row = conn.execute(
                "SELECT id FROM character WHERE name = ?",
                (character_name,)
            ).fetchone()
            if not char_row:
                logger.warning(f"[TTS] Character '{character_name}' not found")
                conn.close()
                return None

            char_id = char_row['id']
            sample_row = conn.execute(
                "SELECT file_path, sample_text FROM character_voice_samples "
                "WHERE character_id = ? ORDER BY created_at DESC LIMIT 1",
                (char_id,)
            ).fetchone()
            conn.close()

            file_path = None
            ref_text = None

            if sample_row and sample_row['file_path']:
                fp = sample_row['file_path']
                if os.path.exists(fp):
                    file_path = fp
                    if sample_row['sample_text']:
                        ref_text = sample_row['sample_text']
                else:
                    logger.warning(f"[TTS] Sample file not found: {fp}")

            if not file_path:
                # base 模式：按情绪选子目录，回落默认 reference.wav
                base_dir = os.path.join(USER_SIM_LIFE, 'qwen3-tts_voices', character_name)
                if emotion and emotion != 'neutral':
                    emo_path = os.path.join(base_dir, emotion, 'reference.wav')
                    if os.path.exists(emo_path):
                        logger.info(f"[TTS] Using emotion reference audio ({emotion}): {emo_path}")
                        file_path = emo_path
                if not file_path:
                    default_path = os.path.join(base_dir, 'reference.wav')
                    if os.path.exists(default_path):
                        logger.info(f"[TTS] Using default reference audio: {default_path}")
                        file_path = default_path

            if file_path and not ref_text:
                # 尝试读取同目录的 reference.txt 文字稿
                txt_path = os.path.splitext(file_path)[0] + '.txt'
                if os.path.exists(txt_path):
                    try:
                        with open(txt_path, encoding='utf-8') as tf:
                            ref_text = tf.read().strip()
                        logger.info(f"[TTS] Loaded reference transcript: {txt_path}")
                    except Exception as e:
                        logger.warning(f"[TTS] Failed to read reference transcript {txt_path}: {e}")

            if not file_path:
                return None
            return {'file_path': file_path, 'ref_text': ref_text}
        except Exception as e:
            logger.warning(f"[TTS] Failed to get character voice sample: {e}")
            return None

    # -- service auto-start --

    def _ensure_service_running(self, label, health_url, script_path,
                               port=None, use_uvicorn=False):
        try:
            resp = requests.get(health_url, timeout=3)
            if resp.status_code == 200:
                return True
        except Exception:
            pass

        logger.info(f"[TTS AutoStart] {label} not running, auto-launching: {script_path}")

        try:
            python_exe = CUDA_PYTHON if os.path.exists(CUDA_PYTHON) else sys.executable
            work_dir = os.path.dirname(os.path.abspath(script_path))

            if use_uvicorn:
                cmd = [python_exe, "-m", "uvicorn", "server:app", "--host", "127.0.0.1"]
                if port is not None:
                    cmd.extend(["--port", str(port)])
            else:
                cmd = [python_exe, script_path]
                if port is not None:
                    cmd.append(str(port))

            subprocess.Popen(
                cmd,
                cwd=work_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
            )

            waited = 0
            while waited < SERVICE_START_TIMEOUT:
                time.sleep(SERVICE_POLL_INTERVAL)
                waited += SERVICE_POLL_INTERVAL
                try:
                    resp = requests.get(health_url, timeout=3)
                    if resp.status_code == 200:
                        logger.info(f"[TTS AutoStart] {label} started successfully in {waited}s")
                        return True
                except Exception:
                    pass

            logger.error(f"[TTS AutoStart] {label} startup timed out after {waited}s")
            return False
        except Exception as e:
            logger.error(f"[TTS AutoStart] Failed to launch {label}: {e}")
            return False

    # -- classifier call (Qwen3 emotion) --

    def _call_classifier(self, text, dialogue_history=None, character_name=""):
        if not self._classifier_launched:
            self._ensure_service_running(
                "Classifier",
                f"{self._classifier_url}/health",
                CLASSIFIER_SCRIPT,
            )
            self._classifier_launched = True

        try:
            payload = {"text": text, "history": dialogue_history or []}
            t0 = datetime.now()
            response = requests.post(
                f"{self._classifier_url}/classify",
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
            if response.status_code == 200:
                result = response.json()
                logger.info(
                    f"[TTS Classifier] emotion={result.get('emotion')}, "
                    f"intensity={result.get('intensity')}, "
                    f"confidence={result.get('confidence')}"
                )
                log_response = {
                    "success": True,
                    "status_code": 200,
                    "content": result,
                    "error": None,
                    "elapsed_ms": elapsed_ms,
                }
                write_llm_log(
                    call_type="tts_classify",
                    url=f"{self._classifier_url}/classify",
                    model="Qwen2-1.5B-Instruct",
                    messages=[{"role": "system", "content": TENCENT_CLASSIFY_PROMPT}, {"role": "user", "content": text}],
                    max_tokens=0,
                    temperature=0,
                    response=log_response,
                    character_name=character_name,
                )
                return result
            else:
                logger.warning(
                    f"[TTS Classifier] HTTP {response.status_code}: {response.text[:100]}"
                )
        except requests.exceptions.ConnectionError:
            logger.warning("[TTS Classifier] Cannot connect to classifier service")
        except Exception as e:
            logger.warning(f"[TTS Classifier] Exception: {e}")
        return None

    # -- DeepSeek slow path --

    def _call_deepseek_modify(self, character_name, text, dialogue_history, classify_result):
        try:
            from backend.config import Config
            deepseek_model = getattr(Config, 'DEEPSEEK_MODEL', 'deepseek-chat')
            deepseek_api_key = getattr(Config, 'DEEPSEEK_API_KEY', '')
            if not deepseek_api_key:
                logger.warning("[TTS DeepSeek] DEEPSEEK_API_KEY not configured")
                return ""

            import openai
            client = openai.OpenAI(api_key=deepseek_api_key, base_url="https://api.deepseek.com")

            emotion = classify_result.get('emotion', 'neutral')
            intensity = classify_result.get('intensity', 0.5)
            from backend.game.prompt_registry import get_prompt_manager
            from backend.game.variable_resolver import render_template
            pm = get_prompt_manager()
            system_prompt = render_template(pm.get("tts.deepseek_modify"), {
                "character.name": character_name,
                "context.emotion": emotion,
                "context.intensity": f"{intensity:.2f}",
            })

            messages = [{"role": "system", "content": system_prompt}]
            if dialogue_history:
                for msg in dialogue_history[-4:]:
                    role = 'user' if msg.get('role') == 'user' else 'assistant'
                    content_text = msg.get('content', '')
                    if content_text:
                        messages.append({"role": role, "content": content_text[:200]})
            messages.append({"role": "user", "content": f"Line: {text[:100]}"})

            t0 = datetime.now()
            response = client.chat.completions.create(
                model=deepseek_model,
                messages=messages,
                max_tokens=20,
                temperature=0.7,
            )
            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
            phrase = response.choices[0].message.content.strip() if response.choices else ""

            log_response = {
                "success": True,
                "status_code": 200,
                "content": phrase,
                "error": None,
                "elapsed_ms": elapsed_ms,
            }
            write_llm_log(
                call_type="tts_deepseek_modify",
                url="https://api.deepseek.com",
                model=deepseek_model,
                messages=messages,
                max_tokens=20,
                temperature=0.7,
                response=log_response,
                character_name=character_name,
            )
            return phrase
        except Exception as e:
            logger.warning(f"[TTS DeepSeek] Modification failed: {e}")
            return ""

    # -- Tencent Cloud TTS classifier --

    def _call_tencent_classifier(self, text, dialogue_history=None, character_name=""):
        self._ensure_service_running(
            "Classifier",
            f"{self._classifier_url}/health",
            CLASSIFIER_SCRIPT,
        )

        try:
            payload = {"text": text, "history": dialogue_history or []}
            t0 = datetime.now()
            response = requests.post(
                f"{self._classifier_url}/classify/tencent",
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
            if response.status_code == 200:
                result = response.json()
                logger.info(
                    f"[TTS TencentClassify] emotion={result.get('emotion')}, "
                    f"intensity={result.get('emotion_intensity')}, "
                    f"speed={result.get('speed')}, volume={result.get('volume')}"
                )
                log_response = {
                    "success": True,
                    "status_code": 200,
                    "content": result,
                    "error": None,
                    "elapsed_ms": elapsed_ms,
                }
                write_llm_log(
                    call_type="tts_tencent_classify",
                    url=f"{self._classifier_url}/classify/tencent",
                    model="Qwen2-1.5B-Instruct",
                    messages=[{"role": "system", "content": TENCENT_CLASSIFY_PROMPT}, {"role": "user", "content": text}],
                    max_tokens=0,
                    temperature=0,
                    response=log_response,
                    character_name=character_name,
                )
                return result
            else:
                logger.warning(
                    f"[TTS TencentClassify] HTTP {response.status_code}: {response.text[:100]}"
                )
                write_llm_log(
                    call_type="tts_tencent_classify",
                    url=f"{self._classifier_url}/classify/tencent",
                    model="Qwen2-1.5B-Instruct",
                    messages=[{"role": "system", "content": TENCENT_CLASSIFY_PROMPT}, {"role": "user", "content": text}],
                    max_tokens=0,
                    temperature=0,
                    response={
                        "success": False,
                        "status_code": response.status_code,
                        "content": response.text[:200],
                        "error": f"HTTP {response.status_code}",
                        "elapsed_ms": elapsed_ms,
                    },
                    character_name=character_name,
                )
        except requests.exceptions.ConnectionError:
            logger.warning("[TTS TencentClassify] Cannot connect to classifier service")
            write_llm_log(
                call_type="tts_tencent_classify",
                url=f"{self._classifier_url}/classify/tencent",
                model="Qwen2-1.5B-Instruct",
                messages=[{"role": "system", "content": TENCENT_CLASSIFY_PROMPT}, {"role": "user", "content": text}],
                max_tokens=0,
                temperature=0,
                response={
                    "success": False,
                    "status_code": None,
                    "content": None,
                    "error": "ConnectionError: Cannot connect to classifier service",
                    "elapsed_ms": 0,
                },
                character_name=character_name,
            )
        except Exception as e:
            logger.warning(f"[TTS TencentClassify] Exception: {e}")
            write_llm_log(
                call_type="tts_tencent_classify",
                url=f"{self._classifier_url}/classify/tencent",
                model="Qwen2-1.5B-Instruct",
                messages=[{"role": "system", "content": TENCENT_CLASSIFY_PROMPT}, {"role": "user", "content": text}],
                max_tokens=0,
                temperature=0,
                response={
                    "success": False,
                    "status_code": None,
                    "content": None,
                    "error": str(e),
                    "elapsed_ms": 0,
                },
                character_name=character_name,
            )
        return None

    # -- Tencent Cloud TTS synthesis --

    def _synthesize_tencent(self, character_name, text, dialogue_history, active_service):
        try:
            conn = sqlite3.connect(_DB_PATH)
            conn.row_factory = sqlite3.Row

            svc = conn.execute(
                "SELECT service_type, tencent_secret_id, tencent_secret_key, "
                "tencent_app_id, service_name "
                "FROM tts_service_config WHERE is_active = 1 LIMIT 1"
            ).fetchone()

            if not svc or not svc['tencent_secret_id'] or not svc['tencent_secret_key']:
                logger.error("[TTS Tencent] Tencent Cloud credentials not configured")
                conn.close()
                return None

            secret_id = svc['tencent_secret_id']
            secret_key = svc['tencent_secret_key']
            service_name = svc['service_name'] or 'TencentTTS'
            logger.info(f"[TTS] Using service: {service_name} (type=tencent_tts)")

            char_row = conn.execute(
                "SELECT tencent_tts_voice_type FROM character WHERE name = ?",
                (character_name,)
            ).fetchone()
            conn.close()

            if not char_row:
                logger.error(f"[TTS Tencent] Character '{character_name}' not found")
                return None

            voice_type = char_row['tencent_tts_voice_type'] if char_row else None
            if not voice_type:
                logger.error(
                    f"[TTS Tencent] Character '{character_name}' has no tencent_tts_voice_type"
                )
                return None

            # ── 试听缓存检查（含服务隔离）：命中则直接返回 ──
            # 缓存 key 包含 service_type，确保不同服务的缓存互不覆盖
            cache_key = hashlib.md5(
                f"{character_name}|tencent_tts|{text}".encode('utf-8')
            ).hexdigest()[:12]
            safe_char = re.sub(r'[\\/:*?"<>|]', '_', character_name)
            cache_filename = f"{safe_char}_{cache_key}.wav"
            cache_path = os.path.join(_TTS_CACHE_DIR, cache_filename)
            meta_path = os.path.join(_TTS_CACHE_DIR, f"{safe_char}_{cache_key}.meta")

            if os.path.exists(cache_path) and os.path.exists(meta_path):
                try:
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                    # 校验服务类型和音色是否匹配
                    if (meta.get('service_type') == 'tencent_tts'
                            and meta.get('voice_type') == voice_type):
                        with open(cache_path, 'rb') as f:
                            cached_audio = f.read()
                        logger.info(
                            f"[TTS Tencent] Cache hit: {cache_filename} "
                            f"({len(cached_audio)} bytes, voice={voice_type})"
                        )
                        return cached_audio
                    else:
                        logger.info(
                            f"[TTS Tencent] Cache stale: meta={meta.get('service_type')}/"
                            f"{meta.get('voice_type')}, current=tencent_tts/{voice_type}"
                        )
                except Exception as e:
                    logger.warning(f"[TTS Tencent] Cache meta read error: {e}")

            logger.info(f"[TTS Tencent] Cache miss, generating: {cache_filename}")

            classify_result = self._call_tencent_classifier(
                text, dialogue_history, character_name
            )
            if classify_result is None:
                classify_result = {
                    "emotion": "neutral",
                    "emotion_intensity": 100,
                    "speed": 0.0,
                    "volume": 0.0,
                }
                logger.info("[TTS Tencent] Classifier unavailable, using default params")

            engine = TencentTTSEngine(secret_id, secret_key)
            t0 = datetime.now()
            audio_data = engine.synthesize(
                text=text,
                voice_type=voice_type,
                speed=classify_result.get('speed', 0.0),
                volume=classify_result.get('volume', 0.0),
                emotion_category=classify_result.get('emotion'),
                emotion_intensity=classify_result.get('emotion_intensity', 100),
            )
            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)

            logger.info(f"[TTS Tencent] Synthesis done, size={len(audio_data)} bytes")

            log_response = {
                "success": True,
                "status_code": 200,
                "content": f"WAV {len(audio_data)} bytes",
                "error": None,
                "elapsed_ms": elapsed_ms,
            }
            write_llm_log(
                call_type="tts_tencent",
                url="https://tts.cloud.tencent.com",
                model="TencentTTS",
                messages=[{"text": text, "voice_type": voice_type, "params": classify_result}],
                max_tokens=0,
                temperature=0,
                response=log_response,
                character_name=character_name,
            )

            chat_dir = os.path.join(USER_SIM_LIFE, f"chat_with_{character_name}", "audio")
            os.makedirs(chat_dir, exist_ok=True)
            now = datetime.now()
            ts = now.strftime("%Y%m%d_%H%M%S")
            safe = re.sub(r'[\\/:*?"<>|]', '_', text[:20].strip())
            filename = f"{ts}_{safe}.wav"
            filepath = os.path.join(chat_dir, filename)
            with open(filepath, "wb") as f:
                f.write(audio_data)
            self._last_audio_path = filepath
            logger.info(f"[TTS] Audio saved: {filepath}")

            # ── 写入试听缓存 + 元数据 ──
            os.makedirs(_TTS_CACHE_DIR, exist_ok=True)
            with open(cache_path, "wb") as f:
                f.write(audio_data)
            meta = {
                'service_type': 'tencent_tts',
                'voice_type': voice_type,
                'character_name': character_name,
                'text': text[:100],
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            }
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            logger.info(f"[TTS Tencent] Cache saved: {cache_filename} (voice={voice_type})")

            return audio_data
        except Exception as e:
            logger.error(f"[TTS Tencent] Synthesis failed: {e}")
            logger.error(traceback.format_exc())
            write_llm_log(
                call_type="tts_tencent",
                url="https://tts.cloud.tencent.com",
                model="TencentTTS",
                messages=[{
                    "text": text,
                    "voice_type": locals().get("voice_type", "unknown"),
                    "params": locals().get("classify_result", {}),
                }],
                max_tokens=0,
                temperature=0,
                response={
                    "success": False,
                    "status_code": None,
                    "content": None,
                    "error": str(e),
                    "elapsed_ms": 0,
                },
                character_name=character_name,
            )
            return None

    # -- main synthesize method --

    def synthesize(self, character_name, text, dialogue_history=None):
        if not text or not text.strip():
            logger.warning("[TTS] Empty text, skipping")
            return None

        # Tencent Cloud TTS branch
        active_service = self._get_active_tts_service()
        if active_service and active_service.get('service_type') == 'tencent_tts':
            return self._synthesize_tencent(character_name, text, dialogue_history, active_service)

        # Qwen3-TTS branch
        # ── 试听缓存检查（含服务隔离）：命中则直接返回 ──
        # 先确定 service_type 用于缓存 key
        if active_service:
            qwen3_service_type = active_service.get('service_type', 'voice_design')
        else:
            qwen3_service_type = 'voice_design'

        cache_key = hashlib.md5(
            f"{character_name}|{qwen3_service_type}|{text}".encode('utf-8')
        ).hexdigest()[:12]
        safe_char = re.sub(r'[\\/:*?"<>|]', '_', character_name)
        cache_filename = f"{safe_char}_{cache_key}.wav"
        cache_path = os.path.join(_TTS_CACHE_DIR, cache_filename)
        meta_path = os.path.join(_TTS_CACHE_DIR, f"{safe_char}_{cache_key}.meta")

        if os.path.exists(cache_path) and os.path.exists(meta_path):
            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                if meta.get('service_type') == qwen3_service_type:
                    with open(cache_path, 'rb') as f:
                        cached_audio = f.read()
                    logger.info(
                        f"[TTS Qwen3] Cache hit: {cache_filename} "
                        f"({len(cached_audio)} bytes, service={qwen3_service_type})"
                    )
                    return cached_audio
                else:
                    logger.info(
                        f"[TTS Qwen3] Cache stale: meta={meta.get('service_type')}, "
                        f"current={qwen3_service_type}"
                    )
            except Exception as e:
                logger.warning(f"[TTS Qwen3] Cache meta read error: {e}")

        logger.info(f"[TTS Qwen3] Cache miss, generating: {cache_filename}")

        # Step 1: emotion classification
        result = self._call_classifier(text, dialogue_history, character_name)
        if result is None:
            logger.info("[TTS] Classifier unavailable, using rule engine fallback")
            result = _rule_classify(text)

        # Step 2: generate instruct
        from backend.game.instruct_generator import safe_build_instruct, modify_instruct
        base_instruct = safe_build_instruct(character_name, result)

        # Step 3: DeepSeek 慢路径（仅高情绪强度时触发，未配置 API key 时静默跳过）
        deepseek_phrase = ""
        if result.get('intensity', 0) >= 0.67:
            deepseek_phrase = self._call_deepseek_modify(
                character_name, text, dialogue_history, result
            )
            if deepseek_phrase:
                logger.info(f"[TTS DeepSeek] Phrase: {deepseek_phrase}")

        instruct = modify_instruct(base_instruct, deepseek_phrase)

        # Step 4: TTS synthesis
        if active_service:
            tts_service_url = active_service['api_url'].rstrip('/')
            service_type = active_service.get('service_type', 'voice_design')
            service_name = active_service.get('service_name', '')
            logger.info(f"[TTS] Using service: {service_name} (type={service_type}, url={tts_service_url})")
        else:
            tts_service_url = self._tts_service_url
            service_type = 'voice_design'
            logger.info(f"[TTS] No active service, using default URL: {tts_service_url}")

        from urllib.parse import urlparse
        parsed = urlparse(tts_service_url)
        tts_port = parsed.port
        is_remote = parsed.hostname not in ('127.0.0.1', 'localhost', '::1')

        if is_remote:
            # 远程服务：只做健康检查，不尝试本地启动
            logger.info(f"[TTS] Remote service detected, checking health: {tts_service_url}/health")
            try:
                resp = requests.get(f"{tts_service_url}/health", timeout=5)
                if resp.status_code != 200:
                    logger.error(
                        f"[TTS] Remote service health check failed: "
                        f"HTTP {resp.status_code}, detail={resp.text[:200]}"
                    )
                    return None
                logger.info(f"[TTS] Remote service healthy: {resp.json()}")
            except requests.exceptions.ConnectionError:
                logger.error(f"[TTS] Cannot connect to remote service: {tts_service_url}")
                return None
            except requests.exceptions.Timeout:
                logger.error(f"[TTS] Remote service timeout: {tts_service_url}")
                return None
        else:
            # 本地服务：自动启动
            if service_type == 'base':
                tts_script = TTS_BASE_SCRIPT
                use_uv = False
            else:
                tts_script = TTS_SCRIPT
                use_uv = True

            self._ensure_service_running(
                "TTS engine",
                f"{tts_service_url}/health",
                tts_script,
                port=tts_port,
                use_uvicorn=use_uv,
            )

        engine = Qwen3TTSEngine(service_url=tts_service_url)
        tts_url_base = f"{tts_service_url}/tts"
        tts_messages = [{"text": text, "instruct": instruct}]
        t0 = datetime.now()

        try:
            if service_type == 'base':
                emotion = result.get('emotion', 'neutral')
                sample = self._get_character_voice_sample(character_name, emotion)
                if not sample:
                    logger.error(
                        f"[TTS] Base mode requires reference audio, "
                        f"but character '{character_name}' has no voice sample"
                    )
                    return None
                ref_audio_path = sample['file_path']
                ref_text = sample.get('ref_text')
                audio_data = engine.generate_voice_clone(text, ref_audio_path, ref_text=ref_text)
                tts_url_base = f"{tts_service_url}/tts/clone"
                tts_messages = [{"text": text, "ref_audio": ref_audio_path, "ref_text": ref_text, "emotion": emotion}]
            else:
                audio_data = engine.generate_voice_design_auto(text, instruct)

            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
            logger.info(
                f"[TTS] Synthesis done, size={len(audio_data)} bytes, "
                f"emotion={result.get('emotion')}, instruct_len={len(instruct)}"
            )

            log_response = {
                "success": True,
                "status_code": 200,
                "content": f"WAV {len(audio_data)} bytes",
                "error": None,
                "elapsed_ms": elapsed_ms,
            }
            write_llm_log(
                call_type="tts_voice_design",
                url=tts_url_base,
                model="Qwen3-TTS",
                messages=tts_messages,
                max_tokens=0,
                temperature=0,
                response=log_response,
                character_name=character_name,
            )

            chat_dir = os.path.join(USER_SIM_LIFE, f"chat_with_{character_name}", "audio")
            os.makedirs(chat_dir, exist_ok=True)
            now = datetime.now()
            ts = now.strftime("%Y%m%d_%H%M%S")
            safe = re.sub(r'[\\/:*?"<>|]', '_', text[:20].strip())
            filename = f"{ts}_{safe}.wav"
            filepath = os.path.join(chat_dir, filename)
            with open(filepath, "wb") as f:
                f.write(audio_data)
            self._last_audio_path = filepath
            logger.info(f"[TTS] Audio saved: {filepath}")

            # ── 写入试听缓存 + 元数据 ──
            os.makedirs(_TTS_CACHE_DIR, exist_ok=True)
            with open(cache_path, "wb") as f:
                f.write(audio_data)
            meta = {
                'service_type': qwen3_service_type,
                'character_name': character_name,
                'text': text[:100],
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            }
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            logger.info(f"[TTS Qwen3] Cache saved: {cache_filename} (service={qwen3_service_type})")

            return audio_data

        except requests.exceptions.ConnectionError:
            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
            logger.error(f"[TTS] Cannot connect to Qwen3-TTS service: {tts_service_url}")
            log_response = {
                "success": False,
                "status_code": None,
                "content": None,
                "error": "ConnectionError: Cannot connect to TTS service",
                "elapsed_ms": elapsed_ms,
            }
            write_llm_log(
                call_type="tts_voice_design",
                url=tts_url_base,
                model="Qwen3-TTS",
                messages=tts_messages,
                max_tokens=0,
                temperature=0,
                response=log_response,
                character_name=character_name,
            )
            return None
        except requests.exceptions.Timeout:
            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
            logger.error("[TTS] Qwen3-TTS request timeout")
            return None
        except RuntimeError as e:
            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
            logger.error(f"[TTS] Qwen3-TTS service error: {e}")
            return None
        except Exception as e:
            elapsed_ms = int((datetime.now() - t0).total_seconds() * 1000)
            logger.error(f"[TTS] Qwen3-TTS synthesis failed: {e}")
            logger.error(traceback.format_exc())
            return None

    # -- utility methods --

    def check_service(self) -> bool:
        try:
            response = requests.get(
                f"{self._tts_service_url}/health", timeout=5
            )
            if response.status_code == 200:
                self._available = True
                return True
            self._available = False
            return False
        except Exception:
            self._available = False
            return False

    def init_engine(self) -> bool:
        return self.check_service()

    def is_available(self) -> bool:
        return self._available

    def get_status(self) -> dict:
        active_service = self._get_active_tts_service()
        service_type = active_service.get('service_type') if active_service else 'voice_design'
        service_name = active_service.get('service_name') if active_service else None
        service_ok = self.check_service()
        return {
            'available': service_ok,
            'engine_loaded': service_ok,
            'service_url': self._tts_service_url,
            'classifier_url': self._classifier_url,
            'mode': 'qwen3_tts',
            'service_type': service_type,
            'service_name': service_name,
        }


# -- global singleton --

_tts_manager = None


def get_tts_manager() -> TTSManager:
    global _tts_manager
    if _tts_manager is None:
        _tts_manager = TTSManager()
    return _tts_manager
