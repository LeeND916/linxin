# -*- coding: utf-8 -*-
"""
情感档案模块（AI伴侣系统升级 — 阶段一）

职责：
  1. detect_emotional_moment()  — 对话后检测是否为高价值情感时刻，写入 EmotionalMoment 表
  2. recall_moments()           — 在特定场景下召回情感时刻，注入 prompt
  3. get_moments_summary()      — 供前端"回忆"面板查询

数据表：emotional_moment（models.py → EmotionalMoment）

设计思路：
  - 高价值时刻包括：表白、安慰、冲突、突破、秘密分享等
  - 由 LLM 判断对话是否为高价值时刻，同时生成简短回忆摘要
  - 召回时机：深夜、心情波动、纪念日等特殊场景
"""

import json
import logging
from datetime import datetime

from backend.models import db, EmotionalMoment
from backend.game.llm_utils import safe_llm_post, get_active_llm_config

logger = logging.getLogger('sim_life.emotion_archive')


# ============================================================
# 1. 情感时刻检测 — 对话后调用 LLM 判断是否为高价值时刻
# ============================================================

def detect_emotional_moment(character_name: str, player_message: str,
                           character_reply: str, effects: dict,
                           game_day: int, game_time: str) -> dict | None:
    """检测当前对话是否为高价值情感时刻。

    通过 LLM 分析对话的情感强度，判断是否值得记录为"回忆"。
    同时参考 effects 中的属性变化幅度作为辅助判断。

    参数:
        character_name: 女主名字
        player_message: 玩家消息
        character_reply: 女主回复
        effects: 本轮对话的属性变化效果（来自 llm_emotional_changes）
        game_day: 游戏天
        game_time: 游戏时间

    返回:
        dict | None: 如果是高价值时刻，返回 EmotionalMoment.to_dict()；否则返回 None
    """
    llm_config = get_active_llm_config()
    if not llm_config:
        return None

    # ── 快速预判：属性变化幅度大时更可能是高价值时刻 ──
    has_large_change = False
    relation_changes = effects.get('relation_changes', [])
    for rc in relation_changes:
        delta = abs(rc.get('delta', 0))
        if delta >= 5:
            has_large_change = True
            break

    # 如果没有大幅属性变化，且对话很短，大概率不是高价值时刻（跳过 LLM 调用节省 token）
    if not has_large_change and len(player_message) < 10 and len(character_reply) < 20:
        return None

    # ── 使用 PromptManager 统一管理 prompt 模板 ──
    from backend.game.prompt_registry import get_prompt_manager
    from backend.game.variable_resolver import render_template
    pm = get_prompt_manager()
    template = pm.get("event.emotion_detect")
    detect_prompt = render_template(template, {
        "character.name": character_name,
        "context.user_message": player_message,
        "context.character_reply": character_reply,
        "context.effects_json": json.dumps(effects, ensure_ascii=False),
    })

    try:
        result = safe_llm_post(
            api_url=llm_config['api_url'],
            api_key=llm_config['api_key'],
            model=llm_config.get('model_name', 'gpt-4o-mini'),
            messages=[
                {"role": "system", "content": "你是情感分析模块，只返回JSON，不要解释。"},
                {"role": "user", "content": detect_prompt}
            ],
            max_tokens=2000,
            temperature=0.3,
            timeout=(15, 30),
            call_type="emotion_detect",
            character_name=character_name
        )

        if not result or 'choices' not in result:
            return None

        content = result['choices'][0]['message']['content'].strip()
        # 处理 markdown 代码块包裹
        if content.startswith('```'):
            lines = content.split('\n')
            content = '\n'.join(lines[1:-1]) if len(lines) > 2 else content

        data = json.loads(content)

        if not data.get('is_moment', False):
            return None

        # 写入 EmotionalMoment 表
        moment = EmotionalMoment(
            character_name=character_name,
            moment_type=data.get('type', 'memory'),
            player_message=player_message,
            character_reply=character_reply,
            emotional_intensity=float(data.get('intensity', 50)),
            summary=data.get('summary', ''),
            game_day=game_day,
            game_time=game_time,
        )
        db.session.add(moment)
        db.session.commit()

        logger.info(f"[EmotionArchive] {character_name} 记录高价值时刻: "
                    f"type={data.get('type')}, intensity={data.get('intensity')}, "
                    f"summary={data.get('summary')}")

        return moment.to_dict()

    except (json.JSONDecodeError, KeyError, Exception) as e:
        logger.error(f"[EmotionArchive] 情感时刻检测失败: {e}")
        return None


# ============================================================
# 2. 情感时刻召回 — 在特定场景下注入回忆
# ============================================================

def recall_moments(character_name: str, char_mood: float = 50.0,
                  game_hour: int = 12, max_moments: int = 2) -> str:
    """在特定场景下召回情感时刻，返回可注入 prompt 的文本。

    召回触发条件：
      - 深夜时段（22:00-06:00）：容易回忆
      - 心情低落（mood < 35）：回忆温暖时刻
      - 心情极好（mood > 80）：回忆快乐时刻

    参数:
        character_name: 女主名字
        char_mood: 女主当前心情值 (0-100)
        game_hour: 当前游戏小时 (0-23)
        max_moments: 最多召回几条

    返回:
        str: 格式化的回忆文本，可注入 prompt。无回忆时返回空字符串。
    """
    # 判断是否处于容易回忆的场景
    is_late_night = game_hour >= 22 or game_hour < 6
    is_low_mood = char_mood < 35
    is_high_mood = char_mood > 80

    if not (is_late_night or is_low_mood or is_high_mood):
        return ""

    # 查询该角色的情感时刻（按情感强度降序）
    moments = EmotionalMoment.query.filter_by(
        character_name=character_name
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


# ============================================================
# 3. 辅助函数 — 供前端查询
# ============================================================

def get_moments_summary(character_name: str, limit: int = 20, moment_type: str = None) -> list:
    """获取角色的情感时刻列表（供前端“回忆”面板展示）。

    参数:
        character_name: 女主名字
        limit: 最多返回几条
        moment_type: 按类型筛选（tender/conflict/breakthrough/memory），None 表示全部

    返回:
        list: 情感时刻字典列表，按游戏时间倒序（最新在前）
    """
    q = EmotionalMoment.query.filter_by(character_name=character_name)
    if moment_type:
        q = q.filter_by(moment_type=moment_type)
    moments = q.order_by(EmotionalMoment.game_day.desc(), EmotionalMoment.game_time.desc()).limit(limit).all()
    return [m.to_dict() for m in moments]


def get_moment_count(character_name: str) -> dict:
    """统计角色的情感时刻数量（按类型分组）。

    返回:
        dict: {'total': 总数, 'by_type': {'tender': N, 'conflict': N, ...}}
    """
    moments = EmotionalMoment.query.filter_by(character_name=character_name).all()
    by_type = {}
    for m in moments:
        t = m.moment_type or 'memory'
        by_type[t] = by_type.get(t, 0) + 1
    return {'total': len(moments), 'by_type': by_type}
