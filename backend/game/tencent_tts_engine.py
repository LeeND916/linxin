# -*- coding: utf-8 -*-
"""
腾讯云 TTS 语音合成引擎

基于腾讯云 TextToVoice API，支持：
  - HMAC-SHA256 签名认证
  - 按标点自动分句（单次 ≤150 字）
  - 并发合成（默认 5 路并发）
  - WAV 按序拼接

依赖：pip install tencentcloud-sdk-python
"""

import logging
import re
import io
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed

from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.tts.v20190823 import tts_client, models

logger = logging.getLogger('sim_life.tencent_tts')

# ─ 分句正则：按中文标点切分 ──
# 智能分句：优先按句号/问号/叹号切分，超过 150 字才按逗号切分
SENTENCE_END_PATTERN = re.compile(r'[。！？\n]+')
COMMA_PATTERN = re.compile(r'[；，…]+')
# 单次 API 最大字符数（中文）
MAX_CHARS_PER_CALL = 150
# 并发数
MAX_CONCURRENT = 5


class TencentTTSEngine:
    """腾讯云 TTS 引擎"""

    def __init__(self, secret_id: str, secret_key: str):
        """
        Args:
            secret_id: 腾讯云 SecretId
            secret_key: 腾讯云 SecretKey
        """
        self.secret_id = secret_id
        self.secret_key = secret_key
        self._client = None

    @property
    def client(self):
        """懒初始化 TTS 客户端"""
        if self._client is None:
            cred = credential.Credential(self.secret_id, self.secret_key)
            self._client = tts_client.TtsClient(cred, "ap-guangzhou")
        return self._client

    # ── 公开接口 ──

    def synthesize(
        self,
        text: str,
        voice_type: int,
        speed: float = 0.0,
        volume: float = 0.0,
        emotion_category: str = None,
        emotion_intensity: int = 100,
    ) -> bytes:
        """
        合成语音，返回 WAV bytes。

        文本 ≤150 字：单次调用
        文本 >150 字：按标点分句 → 并发调用 → 按序拼接 WAV

        Args:
            text: 待合成文本
            voice_type: 音色 ID（如 101001 智瑜）
            speed: 语速 -2.0 ~ 2.0
            volume: 音量 -10.0 ~ 10.0
            emotion_category: 情感类别（仅多情感音色有效）
            emotion_intensity: 情感强度 50~200

        Returns:
            WAV 音频 bytes
        """
        if not text or not text.strip():
            raise ValueError("合成文本为空")

        text = text.strip()

        if len(text) <= MAX_CHARS_PER_CALL:
            return self._call_api(
                text, voice_type, speed, volume,
                emotion_category, emotion_intensity
            )

        # 长文本：分句 + 并发 + 拼接
        sentences = self._split_text(text)
        logger.info(
            f"长文本合成: {len(text)} 字 → {len(sentences)} 句, "
            f"并发 {MAX_CONCURRENT} 路"
        )

        # 并发调用
        results = [None] * len(sentences)

        def _task(idx, sentence):
            try:
                return idx, self._call_api(
                    sentence, voice_type, speed, volume,
                    emotion_category, emotion_intensity
                )
            except Exception as e:
                logger.error(f"第 {idx} 句合成失败: {e}")
                raise

        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT) as executor:
            futures = {
                executor.submit(_task, i, s): i
                for i, s in enumerate(sentences)
            }
            for future in as_completed(futures):
                idx, wav_bytes = future.result()
                results[idx] = wav_bytes

        # 拼接 WAV
        return self._concat_wav(results)

    # ── 内部方法 ──

    def _call_api(
        self, text, voice_type, speed, volume,
        emotion_category, emotion_intensity
    ) -> bytes:
        """单次调用腾讯云 TextToVoice API"""
        req = models.TextToVoiceRequest()
        req.Text = text
        req.VoiceType = voice_type
        req.SessionId = "sim_life_game"
        req.Speed = speed
        req.Volume = volume
        req.Codec = "wav"
        req.SampleRate = 16000
        req.PrimaryLanguage = 1  # 中文

        # 多情感参数（仅多情感音色有效）
        if emotion_category:
            req.EmotionCategory = emotion_category
            req.EmotionIntensity = emotion_intensity

        try:
            resp = self.client.TextToVoice(req)
            # resp.Audio 是 base64 编码的音频数据
            import base64
            audio_bytes = base64.b64decode(resp.Audio)
            logger.debug(
                f"合成成功: {len(text)} 字 → {len(audio_bytes)} bytes, "
                f"voice={voice_type}, speed={speed}"
            )
            return audio_bytes
        except TencentCloudSDKException as e:
            logger.error(f"腾讯云 TTS API 调用失败: {e}")
            raise RuntimeError(f"腾讯云 TTS 合成失败: {e}")

    def _split_text(self, text: str) -> list:
        """智能分句：优先按句号切分，超过 150 字才按逗号切分"""
        sentences = []
        
        # 第一步：按句号/问号/叹号切分
        parts = SENTENCE_END_PATTERN.split(text)
        
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            # 如果单句不超过 150 字，直接加入
            if len(part) <= MAX_CHARS_PER_CALL:
                sentences.append(part)
            else:
                # 超过 150 字，按逗号进一步切分
                sub_parts = COMMA_PATTERN.split(part)
                current = ""
                
                for sub in sub_parts:
                    sub = sub.strip()
                    if not sub:
                        continue
                    
                    # 如果当前累积 + 新片段不超过 150 字，继续累积
                    if len(current) + len(sub) <= MAX_CHARS_PER_CALL:
                        current = current + sub if current else sub
                    else:
                        # 当前累积已满，先保存
                        if current:
                            sentences.append(current)
                        # 如果新片段本身就超过 150 字，强制切分
                        while len(sub) > MAX_CHARS_PER_CALL:
                            sentences.append(sub[:MAX_CHARS_PER_CALL])
                            sub = sub[MAX_CHARS_PER_CALL:]
                        current = sub
                
                # 保存最后一段
                if current:
                    sentences.append(current)
        
        return sentences

    def _concat_wav(self, wav_list: list) -> bytes:
        """拼接多个 WAV 音频（保持采样率和声道一致）"""
        if len(wav_list) == 1:
            return wav_list[0]

        # 读取第一段 WAV 的头部信息
        first = io.BytesIO(wav_list[0])
        with wave.open(first, 'rb') as wf:
            params = wf.getparams()

        # 拼接 PCM 数据
        pcm_data = b''
        for wav_bytes in wav_list:
            buf = io.BytesIO(wav_bytes)
            with wave.open(buf, 'rb') as wf:
                pcm_data += wf.readframes(wf.getnframes())

        # 写入新 WAV
        output = io.BytesIO()
        with wave.open(output, 'wb') as wf:
            wf.setparams(params)
            wf.setnframes(len(pcm_data) // (params.sampwidth * params.nchannels))
            wf.writeframes(pcm_data)

        return output.getvalue()
