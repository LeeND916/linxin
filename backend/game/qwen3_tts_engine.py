# -*- coding: utf-8 -*-
"""
Qwen3-TTS 远程引擎客户端。

封装对远程 Qwen3-TTS 服务的 HTTP 调用，提供 Voice Design 接口。
服务地址从 config 读取（QWEN3_TTS_SERVICE_URL），默认 http://127.0.0.1:8871。
"""

import io
import logging
import os
import re
import tempfile
import wave

import requests

logger = logging.getLogger('sim_life.tts_engine')

# 默认配置（可被 config 覆盖）
DEFAULT_SERVICE_URL = "http://127.0.0.1:9802"
DEFAULT_TIMEOUT = 600  # 秒

# ── 智能分句配置 ──
SPLIT_THRESHOLD = 150   # 超过此字符数才拆分
SPLIT_BATCH = 3         # 拆分后每批最多几句
_SENT_RE = re.compile(r'[^。！？；!?;]+[。！？；!?;]?')


def _split_sentences(text: str):
    """按中文标点分句，保留标点符号。"""
    parts = _SENT_RE.findall(text)
    return [p.strip() for p in parts if p.strip()]


class Qwen3TTSEngine:
    """Qwen3-TTS 远程引擎客户端。

    对外接口:
        generate_voice_design(text, instruct) → WAV bytes
    """

    def __init__(self, service_url: str = None, timeout: int = None):
        """初始化引擎。

        参数:
            service_url: Qwen3-TTS 服务地址，默认从 config 读取
            timeout: HTTP 请求超时秒数
        """
        if service_url is None:
            try:
                from backend.config import Config
                service_url = getattr(Config, 'QWEN3_TTS_SERVICE_URL', DEFAULT_SERVICE_URL)
            except Exception:
                service_url = DEFAULT_SERVICE_URL

        self._service_url = service_url.rstrip('/')
        self._timeout = timeout or DEFAULT_TIMEOUT

    def generate_voice_design(self, text: str, instruct: str) -> bytes:
        """调用 Qwen3-TTS 生成语音。

        参数:
            text: 待合成的文本
            instruct: Voice Design 指令文本（已拼接完成的完整 instruct）

        返回:
            WAV 格式的音频字节数据

        异常:
            requests.exceptions.Timeout: 请求超时
            requests.exceptions.ConnectionError: 无法连接服务
            RuntimeError: 服务返回非 200 状态
        """
        url = f"{self._service_url}/tts"
        payload = {
            "text": text,
            "instruct": instruct,
        }

        logger.info(
            f"[Qwen3-TTS] POST {url} text_len={len(text)} instruct_len={len(instruct)}"
        )

        try:
            response = requests.post(
                url,
                json=payload,
                timeout=self._timeout,
            )
        except requests.exceptions.Timeout:
            logger.error(f"[Qwen3-TTS] 请求超时 (>{self._timeout}s)")
            raise
        except requests.exceptions.ConnectionError:
            logger.error(f"[Qwen3-TTS] 无法连接服务: {self._service_url}")
            raise

        if response.status_code != 200:
            error_detail = response.text[:200]
            logger.error(
                f"[Qwen3-TTS] 服务返回 {response.status_code}: {error_detail}"
            )
            raise RuntimeError(
                f"Qwen3-TTS 服务返回 {response.status_code}: {error_detail}"
            )

        # 服务返回 JSON: {"filename": "xxx.wav", "url": "/audio/xxx.wav"}
        try:
            result = response.json()
            audio_url = result.get('url')
            if not audio_url:
                raise RuntimeError(f"响应中缺少 url 字段: {result}")
            # 下载音频文件
            if audio_url.startswith('/'):
                audio_url = f"{self._service_url}{audio_url}"
            audio_response = requests.get(audio_url, timeout=self._timeout)
            if audio_response.status_code != 200:
                raise RuntimeError(f"下载音频失败: {audio_response.status_code}")
            audio_data = audio_response.content
        except (ValueError, KeyError) as e:
            # 如果不是 JSON，可能直接返回音频字节
            audio_data = response.content

        logger.info(f"[Qwen3-TTS] 合成成功, {len(audio_data)} bytes")
        return audio_data

    def generate_voice_design_auto(self, text: str, instruct: str) -> bytes:
        """智能分句合成语音。

        - ≤150 字：单次 API 调用
        - >150 字：按中文标点分句，每 3 句一批调用 API，拼接为单个 WAV

        参数:
            text: 待合成的文本
            instruct: Voice Design 指令文本

        返回:
            拼接后的 WAV 音频字节数据

        异常:
            requests.exceptions.Timeout / ConnectionError / RuntimeError
        """
        # 短文本：直接单次调用
        if len(text) <= SPLIT_THRESHOLD:
            return self.generate_voice_design(text, instruct)

        # 长文本：分句
        sentences = _split_sentences(text)
        if len(sentences) <= 1:
            # 无法拆分（无标点），单次调用
            return self.generate_voice_design(text, instruct)

        logger.info(
            f"[Qwen3-TTS Auto] 长文本分句: {len(text)}字 → "
            f"{len(sentences)}句, 每{SPLIT_BATCH}句一批"
        )

        all_wav_paths = []
        sample_rate = None
        total_batches = (len(sentences) + SPLIT_BATCH - 1) // SPLIT_BATCH

        for batch_idx in range(0, len(sentences), SPLIT_BATCH):
            batch = sentences[batch_idx:batch_idx + SPLIT_BATCH]
            batch_text = "".join(batch)
            batch_num = batch_idx // SPLIT_BATCH + 1

            logger.info(
                f"[Qwen3-TTS Auto] 批次 {batch_num}/{total_batches}: "
                f"{len(batch)}句 {len(batch_text)}字"
            )

            wav_bytes = self.generate_voice_design(batch_text, instruct)

            # 写入临时文件供 wave 读取
            tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
            tmp.write(wav_bytes)
            tmp.flush()
            all_wav_paths.append(tmp.name)

            with wave.open(tmp.name, 'rb') as wf:
                if sample_rate is None:
                    sample_rate = wf.getframerate()

        # ── 拼接所有 WAV ──
        if not all_wav_paths:
            raise RuntimeError("分句合成未产生任何音频")

        output = io.BytesIO()
        with wave.open(output, 'wb') as out_wav:
            # 取首段 WAV 参数
            with wave.open(all_wav_paths[0], 'rb') as first:
                out_wav.setnchannels(first.getnchannels())
                out_wav.setsampwidth(first.getsampwidth())
                out_wav.setframerate(first.getframerate())

            for path in all_wav_paths:
                with wave.open(path, 'rb') as wf:
                    out_wav.writeframes(wf.readframes(wf.getnframes()))

        # 清理临时文件
        for path in all_wav_paths:
            try:
                os.unlink(path)
            except OSError:
                pass

        combined = output.getvalue()
        total_duration = (len(combined) - 44) / (sample_rate * 2) if sample_rate else 0
        logger.info(
            f"[Qwen3-TTS Auto] 拼接完成: {len(sentences)}句, "
            f"{len(combined)} bytes, 约{total_duration:.1f}s"
        )
        return combined

    def generate_voice_clone(self, text: str, ref_audio_path: str, ref_text: str = None) -> bytes:
        """调用 Qwen3-TTS Base 服务进行音色克隆。

        参数:
            text: 待合成的文本
            ref_audio_path: 参考音频文件路径（5-10秒 WAV/MP3）
            ref_text: 参考音频的文字稿（ICL 模式必需；缺省则走 x_vector_only 声纹克隆）

        返回:
            WAV 格式的音频字节数据

        异常:
            requests.exceptions.Timeout / ConnectionError / RuntimeError
        """
        url = f"{self._service_url}/tts/clone"

        logger.info(
            f"[Qwen3-TTS Clone] POST {url} text_len={len(text)} "
            f"ref={ref_audio_path} ref_text={'有' if ref_text else '无'}"
        )

        try:
            with open(ref_audio_path, 'rb') as f:
                files = {'ref_audio': (os.path.basename(ref_audio_path), f, 'audio/wav')}
                data = {'text': text, 'language': 'Chinese'}
                if ref_text:
                    data['ref_text'] = ref_text
                response = requests.post(
                    url,
                    files=files,
                    data=data,
                    timeout=self._timeout,
                )
        except requests.exceptions.Timeout:
            logger.error(f"[Qwen3-TTS Clone] 请求超时 (>{self._timeout}s)")
            raise
        except requests.exceptions.ConnectionError:
            logger.error(f"[Qwen3-TTS Clone] 无法连接服务: {self._service_url}")
            raise

        if response.status_code != 200:
            error_detail = response.text[:200]
            logger.error(
                f"[Qwen3-TTS Clone] 服务返回 {response.status_code}: {error_detail}"
            )
            raise RuntimeError(
                f"Qwen3-TTS Clone 服务返回 {response.status_code}: {error_detail}"
            )

        # Base 服务直接返回 WAV 字节（非 JSON）
        audio_data = response.content
        logger.info(f"[Qwen3-TTS Clone] 合成成功, {len(audio_data)} bytes")
        return audio_data

    @property
    def service_url(self) -> str:
        return self._service_url
