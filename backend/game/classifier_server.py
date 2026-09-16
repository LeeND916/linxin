# -*- coding: utf-8 -*-
r"""
小模型分类服务（HTTP，端口 9803）

启动方式：
  python backend/game/classifier_server.py

监听端口：9803
端点：
  POST /classify        → {emotion, intensity, confidence}   （对话情绪分类）
  POST /classify/tencent→ 腾讯云 TTS 结构化参数              （语气/语速/音量）
  GET  /health          → {"status":"ok"}

说明：
  本服务是独立进程（tts_manager 通过 HTTP 调用，链路不变）。
  内部推理已从「本地 Qwen2-1.5B-Instruct」切换为「系统设置中配置的小模型」
  （见 backend/game/small_model.py：读取 SmallModelConfig 激活行，
   本进程通过 sqlite3 直读同一张表）。地址/模型名在「系统设置 → 小模型」中可改。
  不做兜底：小模型调用失败时返回安全默认值。
"""

import json
import logging
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")

from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)-7s %(message)s")
logger = logging.getLogger("classifier_server")

# 项目根目录（classifier_server.py 位于 backend/game/，上溯三级即到项目根）
# 让本独立进程也能 import backend.game.small_model
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

app = Flask(__name__)

# ── 情绪定义 ──
EMOTIONS = [
    "neutral", "happy", "sad", "angry", "surprised",
    "tender", "wronged", "tired", "playful", "anxious",
]

EMOTION_LABELS_CN = {
    "neutral": "平静", "happy": "喜悦", "sad": "忧伤",
    "angry": "愤怒", "surprised": "惊讶", "tender": "温柔",
    "wronged": "委屈", "tired": "疲惫", "playful": "调侃",
    "anxious": "焦虑",
}

# ── 分类 Prompt（4-shot 示例） ──
CLASSIFY_PROMPT = """你是对话情绪分析专家。根据最近对话和角色当前台词，判断角色的情绪。

可选情绪（10种）：平静/喜悦/忧伤/愤怒/惊讶/温柔/委屈/疲惫/调侃/焦虑

输出格式（严格JSON）：
{"emotion": "情绪英文名", "intensity": 0.0-1.0, "confidence": 0.0-1.0}

强度定义：0.0-0.33=弱，0.34-0.66=中，0.67-1.0=强
置信度：你对判断有多确定，0.0=完全不确定，1.0=百分百确定

### 示例 ###

对话：
玩家：你今天怎么不说话？
晓月：没什么，就是有点累了。

当前台词：没什么，就是有点累了。
输出：{"emotion": "tired", "intensity": 0.45, "confidence": 0.82}

---

对话：
玩家：生日快乐！给你买了蛋糕！
晓月：天哪！你居然记得！

当前台词：天哪！你居然记得！
输出：{"emotion": "surprised", "intensity": 0.72, "confidence": 0.90}

---

对话：
玩家：对不起，昨天是我不好。
晓月：哼，现在知道错了？

当前台词：哼，现在知道错了？
输出：{"emotion": "playful", "intensity": 0.35, "confidence": 0.78}

---

对话：
玩家：那我们明天见。
晓月：好的，路上小心。

当前台词：好的，路上小心。
输出：{"emotion": "neutral", "intensity": 0.25, "confidence": 0.85}

---

### 实际任务（只输出JSON，不要任何解释）###

对话：
{context.history}

当前台词：{context.user_message}
输出："""


def _parse_classifier_output(raw: str) -> dict:
    """从小模型原始输出中提取 JSON。"""
    from backend.game.small_model import _parse_json_dict
    result = _parse_json_dict(raw)
    if not result:
        return {"emotion": "neutral", "intensity": 0.3, "confidence": 0.3}

    emotion = result.get("emotion", "")
    if emotion not in EMOTIONS:
        reverse_map = {v: k for k, v in EMOTION_LABELS_CN.items()}
        emotion = reverse_map.get(emotion, "neutral")
    try:
        intensity = float(result.get("intensity", 0.3))
    except (ValueError, TypeError):
        intensity = 0.3
    try:
        confidence = float(result.get("confidence", 0.5))
    except (ValueError, TypeError):
        confidence = 0.5
    return {
        "emotion": emotion,
        "intensity": max(0.0, min(1.0, intensity)),
        "confidence": max(0.0, min(1.0, confidence)),
    }


def _classify(text: str, history: list = None) -> dict:
    """对话情绪分类（远程小模型）。"""
    from backend.game import small_model

    history_text = ""
    if history:
        for turn in history[-6:]:
            role_label = "玩家" if turn.get("role") == "user" else "角色"
            history_text += f"{role_label}：{turn.get('content', '')}\n"

    prompt = CLASSIFY_PROMPT.replace("{context.history}", history_text or "（无历史）").replace(
        "{context.user_message}", text
    )
    messages = [{"role": "user", "content": prompt}]

    try:
        content = small_model.chat(
            messages, max_tokens=64, temperature=0.1, call_type="classify_emotion"
        )
        return _parse_classifier_output(content)
    except Exception as e:
        logger.error(f"小模型情绪分类失败: {e}")
        return {"emotion": "neutral", "intensity": 0.3, "confidence": 0.3}


# ════════════════════════════════════════════════════════════
# 腾讯云 TTS 专属分类器（复用同一小模型，不同 prompt）
# ════════════════════════════════════════════════════════════

TENCENT_EMOTIONS = [
    "neutral", "happy", "sad", "angry", "fear",
    "coquettish", "surprised", "disgusted", "calm",
]

TENCENT_CLASSIFY_PROMPT = """你是对话语音合成参数专家。根据角色台词和对话上下文，
直接输出腾讯云 TTS 的结构化合成参数。

【参数说明】
- emotion: 情感类别，9选1 → neutral(中性) / happy(高兴) / sad(悲伤) / 
            angry(生气) / fear(恐惧) / coquettish(撒娇) / 
            surprised(震惊) / disgusted(厌恶) / calm(平静)
- emotion_intensity: 情感强度 50~200，100=默认
- speed: 语速 -2.0~2.0，0=正常，正值加快，负值减慢
- volume: 音量 -10.0~10.0，0=默认，正值增大，负值减小

【语速参考】
悲伤/疲惫/温柔：-1.0~-0.3
平静/中性：-0.2~0.2
高兴/调侃：0.2~0.8
愤怒/震惊/焦虑：0.5~1.5

【输出格式】严格的JSON，不要任何解释
{"emotion":"...","emotion_intensity":...,"speed":...,"volume":...}

### 示例 ###

对话：
[user] 今天工作还顺利吗？
[assistant] 唉，别提了，方案又被否了。

当前台词：我已经连续加班一周了，真的撑不住了。

输出：
{"emotion":"sad","emotion_intensity":155,"speed":-0.8,"volume":-2.0}

---
对话：
[user] 你猜我今天在街上看到谁了？
[assistant] 谁呀？别卖关子了！

当前台词：是咱们大学时的班主任！太巧了吧！

输出：
{"emotion":"surprised","emotion_intensity":145,"speed":0.7,"volume":2.0}

---
对话：
[user] 这件事明明不是我的错，他们凭什么把责任推给我？
[assistant] 你先别急，慢慢说。

当前台词：我辛辛苦苦做了两周，他们一句话就全否了，还说是我的问题！

输出：
{"emotion":"angry","emotion_intensity":170,"speed":1.2,"volume":4.0}

---
对话：
[user] 下雨了，你带伞了吗？
[assistant] 没事的，我淋点雨没关系。

当前台词：你先把外套披上，别着凉了，我送你回家。

输出：
{"emotion":"coquettish","emotion_intensity":110,"speed":-0.3,"volume":-1.0}

---
对话：
[user] 那个项目下周就要交了，来得及吗？
[assistant] 我尽量吧，已经熬了好几个晚上了。

当前台词：希望别再出什么岔子，我真的不想再改了。

输出：
{"emotion":"fear","emotion_intensity":120,"speed":0.3,"volume":1.0}

---
对话：
[user] 咖啡还是茶？
[assistant] 都行，你决定。

当前台词：那就咖啡吧，今天天气不错，心情也挺好的。

输出：
{"emotion":"happy","emotion_intensity":80,"speed":0.3,"volume":0.0}

### 实际任务（只输出JSON，不要任何解释）###
对话：
{context.history}

当前台词：{context.user_message}
输出："""


def _classify_tencent(text: str, history: list) -> dict:
    """腾讯云 TTS 专属分类：小模型 → 结构化参数。"""
    from backend.game import small_model

    history_text = ""
    if history:
        lines = []
        for msg in history[-6:]:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                lines.append(f"[user] {content}")
            else:
                lines.append(f"[assistant] {content}")
        history_text = "\n".join(lines)

    prompt = TENCENT_CLASSIFY_PROMPT
    prompt = prompt.replace("{context.history}", history_text)
    prompt = prompt.replace("{context.user_message}", text)
    messages = [{"role": "user", "content": prompt}]

    try:
        content = small_model.chat(
            messages, max_tokens=64, temperature=0.1, call_type="classify_tencent"
        )
        from backend.game.small_model import _parse_json_dict
        result = _parse_json_dict(content)
        if not result:
            raise ValueError("未解析到 JSON")
        for key in ("emotion", "emotion_intensity", "speed", "volume"):
            if key not in result:
                raise ValueError(f"缺少字段: {key}")
        if result["emotion"] not in TENCENT_EMOTIONS:
            result["emotion"] = "neutral"
        result["emotion_intensity"] = max(50, min(200, int(result["emotion_intensity"])))
        result["speed"] = max(-2.0, min(2.0, float(result["speed"])))
        result["volume"] = max(-10.0, min(10.0, float(result["volume"])))
        logger.info(
            f"腾讯分类: emotion={result['emotion']}, "
            f"intensity={result['emotion_intensity']}, "
            f"speed={result['speed']:.1f}, volume={result['volume']:.1f}"
        )
        return result
    except Exception as e:
        logger.warning(f"腾讯分类失败: {e}")
        return {
            "emotion": "neutral",
            "emotion_intensity": 100,
            "speed": 0.0,
            "volume": 0.0,
        }


# ── HTTP 端点 ──

@app.route("/health")
def health():
    return {"status": "ok"}


@app.route("/classify", methods=["POST"])
def classify():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"emotion": "neutral", "intensity": 0.3, "confidence": 0.3})

    history = data.get("history") or []
    result = _classify(text, history)
    return jsonify(result)


@app.route("/classify/tencent", methods=["POST"])
def classify_tencent():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({
            "emotion": "neutral", "emotion_intensity": 100,
            "speed": 0.0, "volume": 0.0,
        })

    history = data.get("history") or []
    result = _classify_tencent(text, history)
    return jsonify(result)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9803
    logger.info(f"分类器服务启动, port={port}（小模型：系统设置->小模型 配置）")
    app.run(host="127.0.0.1", port=port, debug=False)
