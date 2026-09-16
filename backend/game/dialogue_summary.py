# -*- coding: utf-8 -*-
"""
对话摘要模块 — 每20轮对话自动生成阶段摘要，存入 character_memory

设计：
  - trigger_summary_if_needed()  — 在 process_dialogue() 结束时被调用
  - generate_dialogue_summary()    — 用主 LLM API 生成第一人称阶段摘要
  - store_summary_as_memory()      — 写入 character_memory 表
"""

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from backend.config import beijing_now
from backend.models import db, CharacterMemory

logger = logging.getLogger('sim_life.dialogue_summary')

# 主 LLM 调用入口
_llm_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='summary-llm')


def trigger_summary_if_needed(app, character_name: str, game_day: int,
                               game_time: str):
    """检查是否需要触发摘要生成。

    在每轮对话结束后调用（异步线程中执行，不阻塞对话）。

    触发条件：
      1. 当前角色的 dialogue_rounds >= 20
      2. 生成后重置为 0

    Args:
        app: Flask 应用实例（用于后台线程的 app_context）
        character_name: 女主名字
        game_day: 当前游戏天
        game_time: 当前游戏时间
    """
    def _do():
        try:
            with app.app_context():
                from backend.models import Character
                char = Character.query.filter_by(name=character_name).first()
                if not char:
                    logger.warning(f"[Summary] 角色不存在: {character_name}")
                    return

                rounds = char.dialogue_rounds or 0
                if rounds < 20:
                    return

                logger.info(f"[Summary] 触发摘要生成，当前 dialogue_rounds={rounds}")

                # 取最近 40 条对话历史作为摘要上下文
                from backend.chat_history import load_all_sessions
                all_msgs = load_all_sessions()
                # 只取该角色的对话（player + character），按时间正序
                recent = all_msgs[-(rounds * 2):] if len(all_msgs) >= rounds * 2 else all_msgs

                # 生成摘要（包含时间跨度信息）
                summary, covered_range = generate_dialogue_summary(recent, char.name)
                if not summary:
                    logger.warning(f"[Summary] 摘要生成失败，跳过写入")
                    return

                # 写入记忆库
                store_summary_as_memory(
                    character_name=char.name,
                    summary_text=summary,
                    game_day=game_day,
                    game_time=game_time,
                    day_range=covered_range,
                )

                # 重置 dialogue_rounds
                char.dialogue_rounds = 0
                db.session.commit()
                logger.info(f"[Summary] 摘要写入完成，dialogue_rounds 已重置")

        except Exception as e:
            logger.error(f"[Summary] 摘要生成异常: {e}", exc_info=True)

    _llm_executor.submit(_do)


def generate_dialogue_summary(history_messages: list, character_name: str) -> tuple:
    """用主 LLM API 生成对话阶段摘要（第一人称角色视角）。

    Args:
        history_messages: 最近 N 轮对话消息列表，每条含 content/speaker/game_day/game_time
        character_name: 女主名字（用于 system prompt 中的角色名代入）

    Returns:
        tuple: (摘要文本, 时间跨度字符串)，约 150-300 字，第一人称"我"来叙述
    """
    # 构建摘要用的 prompt
    if not history_messages:
        return "", ""

    # 历史消息整理成易读的对话文本
    lines = []
    days = set()
    for m in history_messages:
        speaker = "我" if m.get('speaker') == 'character' else "他"
        day = m.get('game_day', '?')
        if day != '?':
            days.add(int(day))
        gt = m.get('game_time', '?:?:?')
        if gt and len(gt.split(':')) == 2:
            gt = gt + ':00'
        content = (m.get('normalized_content') or
                   m.get('raw_reply') or
                   m.get('content', ''))
        # 清理 MD 残留格式
        content = re.sub(r'>\s*', '', content).strip()
        if content:
            lines.append(f"（第{day}天 {gt}）【{speaker}】：{content[:200]}")

    dialogue_text = '\n'.join(lines[-40:])  # 最多用最近40条

    # 时间跨度（供 prompt 使用，也写入 context 字段）
    if days:
        sorted_days = sorted(days)
        day_range = f"第{sorted_days[0]}天至第{sorted_days[-1]}天"
    else:
        day_range = "（时间不明）"

    system_prompt = f"""你是{character_name}，一个有着真实情感和记忆的人。

【你的任务】
阅读以下最近一段对话，用第一人称"我"写一段 150-300 字的阶段回顾。

【时间跨度】
这段对话发生在 {day_range}。

【要求】
1. 用"我"的第一人称叙述，像是自己回忆这段经历
2. 如果某天发生了重要事件，用"第X天""那天""那几天""之后"等自然方式标注时间
3. 聚焦：两人之间发生的重要事件、情感变化、相互了解的过程
4. 不要提及"玩家"或"对方"，用"他"或直接用具体称呼
5. 自然流畅，不要列点，像真正的回忆片段
6. 只写这一段回顾文字，不要加标题或说明

【对话记录】
{dialogue_text}

【你的回顾】"""

    user_prompt = f"请根据以上对话，写一段这段时期的回顾，用第一人称，注意标注重要事件发生的日期（第X天）。"

    try:
        from backend.game.llm_utils import normalize_api_url, get_active_llm_config, safe_llm_post
        config = get_active_llm_config()
        if not config:
            logger.warning("[Summary] 无激活的 LLM 配置")
            return ""

        base_url = normalize_api_url(config['api_url'])

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        resp = safe_llm_post(
            api_url=base_url,
            api_key=config['api_key'],
            model=config['model_name'],
            messages=messages,
            max_tokens=600,
            temperature=0.5,
            timeout=60,
            call_type="dialogue_summary",
            character_name=character_name,
        )
        if not resp or resp.get("error"):
            logger.warning(f"[Summary] LLM 调用失败: {resp}")
            return "", ""

        choices = resp.get("choices", [])
        if not choices:
            return "", ""

        content = choices[0].get("message", {}).get("content", "").strip()
        # 去掉可能的引号包裹
        if content.startswith('"') and content.endswith('"'):
            content = content[1:-1].strip()
        return content, day_range

    except Exception as e:
        logger.error(f"[Summary] LLM 调用异常: {e}", exc_info=True)
        return "", ""


def store_summary_as_memory(character_name: str, summary_text: str,
                             game_day: int, game_time: str,
                             day_range: str = "") -> CharacterMemory:
    """将阶段摘要写入 character_memory 表。

    Args:
        character_name: 女主名字
        summary_text: 摘要正文
        game_day: 摘要写入时的游戏天
        game_time: 摘要写入时的游戏时间
        day_range: 这段对话覆盖的时间跨度（如"第3天至第5天"）
    """
    mem = CharacterMemory(
        character_name=character_name,
        memory_type='period_summary',
        content=summary_text,
        context=f"覆盖时间：{day_range}" if day_range else "",
        importance=80.0,
        emotional_weight=50.0,
        source_day=game_day,
        source_time=game_time,
    )
    db.session.add(mem)
    db.session.commit()
    logger.info(f"[Summary] 阶段摘要写入 character_memory id={mem.id}，覆盖：{day_range}")
    return mem


def get_recent_summaries(character_name: str, limit: int = 3) -> list:
    """获取该角色最近的阶段摘要（供 build_dynamic_context 注入用）。

    Args:
        character_name: 女主名字
        limit: 最多返回几条

    Returns:
        list[CharacterMemory]，按 source_day 倒序
    """
    mems = (CharacterMemory.query
            .filter_by(character_name=character_name, memory_type='period_summary')
            .order_by(CharacterMemory.source_day.desc())
            .limit(limit)
            .all())
    return mems
