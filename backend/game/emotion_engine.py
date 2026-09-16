# -*- coding: utf-8 -*-
"""
情绪引擎（AI伴侣系统升级 — 阶段二）

职责：
  1. apply_inertia()        — 情绪惯性平滑，防止情绪瞬间切换
  2. get_composite_label()  — 计算复合情绪标签，注入 prompt 影响 LLM 语气
  3. recall_by_topic()      — 根据话题召回相关情感时刻
  4. recall_moments()       — 根据心情/时间召回情感时刻（从 emotion_archive 迁移）
  5. record_emotion_snapshot() — 记录当前情绪快照到 emotion_history

数据流：
  compute_effects_python() → apply_inertia() → apply_attr_changes()
                                ↓
                    get_composite_label() → 注入 prompt
                                ↓
                    recall_by_topic() → 注入 prompt
"""

import json
import logging
from typing import Optional

logger = logging.getLogger('sim_life.emotion_engine')

# ============================================================
# 情绪惯性相关常量
# ============================================================

# 情绪属性集合（只有这些属性参与惯性计算）
EMOTION_ATTRS = {
    'mood', 'stress', 'happiness', 'loneliness', 'confidence', 'motivation',
    'creativity', 'joy', 'anger', 'disappointment', 'boredom', 'fulfillment',
}

# 惯性窗口：保留近 N 轮情绪快照
EMOTION_HISTORY_WINDOW = 5

# 惯性衰减系数：连续同方向变化时，每轮压缩比例（0.3 = 保留 30% 增量）
INERTIA_DECAY_RATIO = 0.3

# 反向变化最低保留比例（情绪翻转至少允许 50% 增量通过）
INERTIA_REVERSE_MIN_RATIO = 0.5


# ============================================================
# 1. 情绪快照记录 — 每次 apply_attr_changes 后调用
# ============================================================

def record_emotion_snapshot(char) -> None:
    """记录当前情绪快照到 char.emotion_history。

    只记录 EMOTION_ATTRS 中的属性当前值，保留最近 EMOTION_HISTORY_WINDOW 条。
    """
    try:
        history = _load_history(char)
        snapshot = {attr: getattr(char, attr, 50.0) for attr in EMOTION_ATTRS}
        history.append(snapshot)
        # 只保留最近 N 条
        if len(history) > EMOTION_HISTORY_WINDOW:
            history = history[-EMOTION_HISTORY_WINDOW:]
        char.emotion_history = json.dumps(history, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[EmotionEngine] 记录情绪快照失败: {e}")


def _load_history(char) -> list:
    """从 char.emotion_history 加载历史，异常时返回空列表。"""
    try:
        raw = getattr(char, 'emotion_history', None) or '[]'
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ============================================================
# 2. 情绪惯性平滑 — effects 写入角色前调用
# ============================================================

def apply_inertia(effects: dict, char) -> dict:
    """情绪惯性平滑：连续同方向变化时压缩增量，反向变化时减缓。

    算法：
      - 读取近 N 轮情绪历史，计算每个属性的平均变化方向
      - 如果本轮 effects 方向与趋势一致 → 压缩（避免过度叠加）
      - 如果本轮 effects 方向与趋势相反 → 保留但至少 50%（情绪不会瞬间翻转）

    参数:
        effects: {attr_name: delta} 字典（来自 compute_effects_python 或 LLM）
        char: Character 对象

    返回:
        dict: 惯性调整后的 effects
    """
    if not effects:
        return effects

    history = _load_history(char)
    if len(history) < 2:
        # 历史不足，无法计算趋势，直接返回原始 effects
        return effects

    # 计算每个情绪属性的趋势方向（近 N-1 次的平均变化）
    trends = _compute_trends(history)

    adjusted = {}
    for attr, delta in effects.items():
        # 非情绪属性（技能/物理/关系）不参与惯性计算
        if attr not in EMOTION_ATTRS:
            adjusted[attr] = delta
            continue

        trend = trends.get(attr, 0.0)

        # 判断方向：同号为同向，异号为反向
        same_direction = (delta > 0 and trend > 0) or (delta < 0 and trend < 0)

        if same_direction and abs(trend) > 1.0:
            # 连续同向且趋势明显 → 压缩增量
            ratio = max(INERTIA_DECAY_RATIO, 1.0 - abs(trend) / 100.0)
            adjusted[attr] = round(delta * ratio, 1)
        elif not same_direction and abs(trend) > 2.0:
            # 反向变化但趋势很强 → 至少保留 INERTIA_REVERSE_MIN_RATIO
            adjusted[attr] = round(delta * INERTIA_REVERSE_MIN_RATIO, 1)
        else:
            # 趋势不明显或方向一致但趋势弱 → 直接通过
            adjusted[attr] = delta

    return adjusted


def _compute_trends(history: list) -> dict:
    """从情绪历史计算各属性的平均变化趋势。

    返回: {attr: avg_delta}，avg_delta > 0 表示上升趋势，< 0 表示下降趋势。
    """
    trends = {}
    for attr in EMOTION_ATTRS:
        deltas = []
        for i in range(1, len(history)):
            prev = history[i - 1].get(attr, 50.0)
            curr = history[i].get(attr, 50.0)
            deltas.append(curr - prev)
        if deltas:
            trends[attr] = sum(deltas) / len(deltas)
        else:
            trends[attr] = 0.0
    return trends


# ============================================================
# 3. 复合情绪标签 — 注入 prompt 影响 LLM 语气
# ============================================================

# 复合情绪规则：(条件函数, 标签)
_COMPOSITE_RULES = [
    # 高心情 + 高压力 → 焦虑但开心
    (lambda c: c.mood > 60 and c.stress > 60, "焦虑但心情不错"),
    # 低心情 + 高压力 → 压力很大
    (lambda c: c.mood < 40 and c.stress > 60, "压力很大，有些喘不过气"),
    # 低开心 + 高孤独 → 孤独失落
    (lambda c: c.joy < 30 and c.loneliness > 60, "感到孤独失落"),
    # 高愤怒 + 高失望 → 愤怒又委屈
    (lambda c: c.anger > 60 and c.disappointment > 50, "又生气又委屈"),
    # 高动力 + 高创造力 → 灵感迸发
    (lambda c: c.motivation > 75 and c.creativity > 70, "灵感涌现，干劲十足"),
    # 低动力 + 高无聊 → 提不起劲
    (lambda c: c.motivation < 30 and c.boredom > 60, "提不起精神，有些无聊"),
    # 高幸福 + 低孤独 → 满足平静
    (lambda c: c.happiness > 70 and c.loneliness < 30, "感到幸福满足"),
    # 高自信 + 高动力 → 充满干劲
    (lambda c: c.confidence > 70 and c.motivation > 70, "信心满满，充满干劲"),
    # 低心情 + 低开心 → 情绪低落
    (lambda c: c.mood < 35 and c.joy < 35, "情绪有些低落"),
    # 高充实 + 中压力 → 忙碌但充实
    (lambda c: c.fulfillment > 65 and 40 < c.stress < 70, "忙碌但充实"),
]


def get_composite_label(char) -> str:
    """根据当前多维情绪计算复合标签。

    按规则优先级匹配，返回第一个匹配的标签。
    全部不匹配时返回空字符串（表示无需注入 prompt）。
    """
    for condition, label in _COMPOSITE_RULES:
        try:
            if condition(char):
                return label
        except Exception:
            continue
    return ""


# ============================================================
# 4. 话题触发回忆 — 根据玩家消息召回相关情感时刻
# ============================================================

def recall_by_topic(user_message: str, char, max_results: int = 2) -> str:
    """根据当前话题召回相关情感时刻。

    1. 从 user_message 提取关键词（简单分词 + 停用词过滤）
    2. 在 EmotionalMoment 表中匹配包含关键词的记录
    3. 按情感强度排序，返回可注入 prompt 的文本

    与 recall_moments()（心情/时间触发）互补：
      - recall_moments: 深夜/心情波动 → 自动回忆
      - recall_by_topic: 玩家提到相关话题 → 触发回忆

    参数:
        user_message: 玩家当前消息
        char: Character 对象
        max_results: 最多召回几条

    返回:
        str: 可注入 prompt 的回忆文本，无匹配时返回空字符串
    """
    if not user_message or len(user_message) < 4:
        return ""

    try:
        from backend.models import EmotionalMoment

        # 提取关键词（简单方案：按标点分词 + 过滤短词）
        keywords = _extract_keywords(user_message)
        if not keywords:
            return ""

        # 查询该角色的情感时刻
        moments = EmotionalMoment.query.filter_by(
            character_name=char.name
        ).order_by(EmotionalMoment.emotional_intensity.desc()).limit(50).all()

        if not moments:
            return ""

        # 匹配关键词
        matched = []
        for m in moments:
            text = (m.player_message or '') + (m.character_reply or '') + (m.summary or '')
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                matched.append((m, score))

        if not matched:
            return ""

        # 按匹配度 × 情感强度排序
        matched.sort(key=lambda x: x[1] * x[0].emotional_intensity, reverse=True)
        selected = matched[:max_results]

        # 格式化
        lines = ["【她想起了那些相关的事】"]
        for m, _ in selected:
            type_emoji = {
                'tender': '💕', 'conflict': '💔', 'breakthrough': '✨', 'memory': '🌙'
            }.get(m.moment_type, '📝')
            lines.append(f"- {type_emoji} {m.summary}（第{m.game_day}天）")

        return '\n'.join(lines)

    except Exception as e:
        logger.warning(f"[EmotionEngine] 话题回忆召回失败: {e}")
        return ""


def _extract_keywords(text: str) -> list:
    """从文本提取关键词（简单方案，不依赖外部库）。"""
    # 停用词
    STOP_WORDS = {
        '的', '了', '是', '在', '我', '你', '他', '她', '它', '们',
        '这', '那', '有', '和', '与', '或', '但', '也', '都', '就',
        '不', '没', '很', '好', '啊', '呢', '吧', '吗', '呀', '哦',
        '嗯', '哈', '嘿', '嘛', '啦', '么', '什么', '怎么', '为什么',
    }

    # 简单分词：按标点和空格切分，保留 2 字以上的词
    import re
    tokens = re.split(r'[，。！？、\s,.\?!]+', text)
    keywords = []
    for token in tokens:
        token = token.strip()
        if len(token) >= 2 and token not in STOP_WORDS:
            keywords.append(token)
        # 长 token 再拆分为 2-3 字的子串（增加命中率）
        if len(token) > 3:
            for i in range(len(token) - 1):
                sub = token[i:i + 2]
                if sub not in STOP_WORDS:
                    keywords.append(sub)

    return list(set(keywords))  # 去重


# ============================================================
# 5. 心情/时间触发回忆（从 emotion_archive.py 迁移）
# ============================================================

def recall_moments(char, game_hour: int = 12, max_moments: int = 2) -> str:
    """在特定场景下召回情感时刻，返回可注入 prompt 的文本。

    召回触发条件：
      - 深夜时段（22:00-06:00）：容易回忆
      - 心情低落（mood < 35）：回忆温暖时刻
      - 心情极好（mood > 80）：回忆快乐时刻

    参数:
        char: Character 对象
        game_hour: 当前游戏小时 (0-23)
        max_moments: 最多召回几条

    返回:
        str: 格式化的回忆文本，可注入 prompt。无回忆时返回空字符串。
    """
    char_mood = getattr(char, 'mood', 50.0)

    # 判断是否处于容易回忆的场景
    is_late_night = game_hour >= 22 or game_hour < 6
    is_low_mood = char_mood < 35
    is_high_mood = char_mood > 80

    if not (is_late_night or is_low_mood or is_high_mood):
        return ""

    try:
        from backend.models import EmotionalMoment

        # 查询该角色的情感时刻（按情感强度降序）
        moments = EmotionalMoment.query.filter_by(
            character_name=char.name
        ).order_by(EmotionalMoment.emotional_intensity.desc()).limit(max_moments * 2).all()

        if not moments:
            return ""

        # 根据当前心情筛选合适类型的回忆
        selected = []
        for m in moments:
            if is_low_mood and m.moment_type in ('tender', 'breakthrough'):
                selected.append(m)  # 心情低落时回忆温暖时刻
            elif is_high_mood and m.moment_type in ('tender', 'memory'):
                selected.append(m)  # 心情好时回忆快乐时刻
            elif is_late_night:
                selected.append(m)  # 深夜时任何高价值回忆都合适
            if len(selected) >= max_moments:
                break

        if not selected:
            return ""

        # 格式化
        lines = ["【她想起了那些时刻】"]
        for m in selected:
            type_emoji = {
                'tender': '💕', 'conflict': '💔', 'breakthrough': '✨', 'memory': '🌙'
            }.get(m.moment_type, '📝')
            lines.append(f"- {type_emoji} {m.summary}（第{m.game_day}天）")

        return '\n'.join(lines)

    except Exception as e:
        logger.warning(f"[EmotionEngine] 心情/时间回忆召回失败: {e}")
        return ""


# ============================================================
# 6. 情绪趋势检测 — 供 tick 衰减使用
# ============================================================

def get_emotion_trend(char, attr: str = 'mood', window: int = 3) -> float:
    """获取指定属性近 N 轮的平均变化趋势。

    返回:
        float: 正值表示上升趋势，负值表示下降趋势，0 表示无明显趋势
    """
    history = _load_history(char)
    if len(history) < 2:
        return 0.0

    recent = history[-window:]
    deltas = []
    for i in range(1, len(recent)):
        prev = recent[i - 1].get(attr, 50.0)
        curr = recent[i].get(attr, 50.0)
        deltas.append(curr - prev)

    return sum(deltas) / len(deltas) if deltas else 0.0
