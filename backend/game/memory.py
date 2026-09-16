# -*- coding: utf-8 -*-
"""
角色长期记忆模块（AI伴侣系统升级 — 阶段一）

职责：
  1. extract_memories_from_dialogue() — 每轮对话后，调用 LLM 提取值得记忆的信息
  2. recall_relevant_memories()       — 对话前，根据当前话题召回相关记忆注入 prompt
  3. fade_memories()                  — 每日 tick 时，低重要度记忆逐渐淡化

数据表：character_memory（models.py → CharacterMemory）

设计思路：
  - 记忆存储使用 SQLite，与 EventLog 保持一致
  - 记忆提取使用 LLM（提示词走 PromptManager "memory.extract"，thinking disabled），
    失败宁缺毋滥不降级本地 1.5B（其提取视角/类型质量差）
  - 召回策略：话题相关 + 情感触发，淡化机制模拟人脑遗忘曲线
"""

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# Embedding 只允许通过 LM Studio；禁止 SentenceTransformer/Hugging Face 在运行时联网下载。
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
from backend.config import beijing_now

from backend.models import db, CharacterMemory

logger = logging.getLogger('sim_life.memory')


# ============================================================
# 0. Embedding — 仅 LM Studio + 失败后台补偿
# ============================================================

def _get_embedding_config():
    """从 DB 读取激活的 EmbeddingConfig，无配置时返回 None（触发 BGE 降级）"""
    try:
        from backend.models import EmbeddingConfig
        config = EmbeddingConfig.query.filter_by(is_active=True).first()
        if config and config.api_url and config.model_name:
            return config
    except Exception as e:
        logger.warning(f"[Memory] 读取 Embedding 配置失败: {e}")
    return None


# Embedding 补偿任务：只调用 LM Studio，避免多个线程重复请求。
_embedding_retry_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='embedding-retry')
_embedding_retry_lock = threading.Lock()
_embedding_retry_running = False


def schedule_embedding_backfill(app, character_name=None):
    """安排后台补齐 NULL embedding；只调用 LM Studio，不阻塞聊天请求。"""
    global _embedding_retry_running
    with _embedding_retry_lock:
        if _embedding_retry_running:
            return False
        _embedding_retry_running = True

    def _run():
        global _embedding_retry_running
        try:
            with app.app_context():
                backfill_memory_embeddings(character_name=character_name)
        except Exception as e:
            logger.warning(f"[Memory] 后台 embedding 补偿失败（仅 LM Studio）: {e}")
        finally:
            with _embedding_retry_lock:
                _embedding_retry_running = False

    _embedding_retry_executor.submit(_run)
    logger.info(f"[Memory] 已安排后台 embedding 补偿: character={character_name or 'all'}")
    return True


def _encode_via_lm_studio(text: str):
    """通过 LM Studio 远程 API 获取 embedding 向量（配置从 DB 读取）。

    返回: list[float]，无 DB 配置或调用失败时抛出异常。
    """
    import requests
    config = _get_embedding_config()
    if not config:
        raise RuntimeError('DB 中无激活的 Embedding 配置')
    headers = {}
    if config.api_key:
        headers['Authorization'] = f'Bearer {config.api_key}'
    resp = requests.post(
        config.api_url,
        json={'model': config.model_name, 'input': text},
        headers=headers,
        timeout=10
    )
    resp.raise_for_status()
    data = resp.json()
    return data['data'][0]['embedding']


def get_bge_model():
    """兼容旧调用方：Embedding 统一由 LM Studio 提供，不再加载本地 BGE。"""
    raise RuntimeError('本地 BGE 已禁用，Embedding 仅使用 LM Studio')


def embed_text_vector(text: str):
    """常驻文本向量（L2 归一化）。

    与记忆召回共用同一条 embedding 通道：**仅使用 LM Studio**。
    LM Studio 不可用时抛出异常，由媒体意图/意图分类/召回调用方安全跳过；绝不加载本地 BGE，也不访问 Hugging Face。

    返回 numpy 向量（dtype=float32，已归一化）。
    """
    import numpy as np
    vec = _encode_via_lm_studio(text)  # list[float]，LM Studio 远程 Embedding
    arr = np.array(vec, dtype='float32')
    norm = np.linalg.norm(arr)
    if norm <= 0:
        raise RuntimeError('LM Studio 返回空 embedding')
    return arr / norm


def _is_lm_studio_available() -> bool:
    """检测 LM Studio embedding 服务是否可用（配置从 DB 读取）"""
    try:
        import requests
        config = _get_embedding_config()
        if not config:
            return False
        headers = {}
        if config.api_key:
            headers['Authorization'] = f'Bearer {config.api_key}'
        resp = requests.post(
            config.api_url,
            json={'model': config.model_name, 'input': 'ping'},
            headers=headers,
            timeout=3
        )
        return resp.status_code == 200
    except Exception:
        return False


def _compute_memory_embedding(content: str) -> bytes:
    """计算记忆内容的 embedding 并序列化为 bytes。

    只调用 DB 激活配置对应的 LM Studio 远程 API；失败时抛出异常，由上层保留文本记忆并安排后台补偿。
    不加载本地 BGE，不访问 Hugging Face。
    """
    import numpy as np

    # 优先：从 DB 读取配置调 LM Studio 远程 API
    try:
        vec = _encode_via_lm_studio(content)
        arr = np.array(vec, dtype='float32')
        arr = arr / np.linalg.norm(arr)  # 归一化
        return arr.tobytes()
    except Exception as e:
        logger.warning(f"[Memory] LM Studio embedding 不可用，当前记忆保留 NULL 待补偿: {e}")

    raise RuntimeError('LM Studio embedding 不可用；已跳过本地 BGE 降级')


def _deserialize_embedding(blob: bytes):
    """从 bytes 反序列化向量（归一化保证一致）"""
    import numpy as np
    arr = np.frombuffer(blob, dtype='float32')
    return arr / np.linalg.norm(arr)


def _encode_batch_via_lm_studio(texts: list) -> list:
    """批量通过 LM Studio 获取 embedding 向量。

    参数:
        texts: 文本列表

    返回: list[list[float]]，失败时抛出异常。
    """
    import requests
    config = _get_embedding_config()
    if not config:
        raise RuntimeError('DB 中无激活的 Embedding 配置')
    headers = {}
    if config.api_key:
        headers['Authorization'] = f'Bearer {config.api_key}'
    resp = requests.post(
        config.api_url,
        json={'model': config.model_name, 'input': texts},
        headers=headers,
        timeout=30
    )
    resp.raise_for_status()
    data = resp.json()
    # LM Studio 返回的 data 按 index 排序，确保顺序正确
    sorted_data = sorted(data['data'], key=lambda x: x['index'])
    return [item['embedding'] for item in sorted_data]


def backfill_memory_embeddings(character_name: str = None, batch_size: int = 32) -> int:
    """一次性补齐旧记忆的 embedding（embedding 列为 NULL 的记忆）。

    仅使用 LM Studio 批量接口；不可用时保留 NULL，等待后台补偿，不加载本地 BGE。

    参数:
        character_name: 指定角色名，为 None 时补齐所有角色
        batch_size: LM Studio 批量大小

    返回:
        int: 本次补齐的记忆条数
    """
    import numpy as np

    query = CharacterMemory.query.filter(CharacterMemory.embedding == None)  # noqa: E711
    if character_name:
        query = query.filter(CharacterMemory.character_name == character_name)
    pending = query.all()

    if not pending:
        logger.info("[Memory] 没有需要补齐 embedding 的记忆")
        return 0

    count = 0
    use_lm_studio = _is_lm_studio_available()

    if use_lm_studio:
        logger.info(f"[Memory] 使用 LM Studio 批量补齐 {len(pending)} 条记忆的 embedding")
        # 分批处理
        for i in range(0, len(pending), batch_size):
            batch = pending[i:i + batch_size]
            texts = [mem.content or '' for mem in batch]
            try:
                vectors = _encode_batch_via_lm_studio(texts)
                for mem, vec in zip(batch, vectors):
                    arr = np.array(vec, dtype='float32')
                    arr = arr / np.linalg.norm(arr)
                    mem.embedding = arr.tobytes()
                    count += 1
                db.session.commit()
                logger.info(f"[Memory] LM Studio 已补齐 {count}/{len(pending)}")
            except Exception as e:
                logger.error(f"[Memory] LM Studio 批量失败 (batch {i}): {e}，本批保留待补偿")
                db.session.rollback()
                break
    else:
        logger.info(f"[Memory] LM Studio 不可用，{len(pending)} 条记忆保留待补偿")

    logger.info(f"[Memory] 补齐完成，共 {count} 条")
    return count


# ============================================================
# 1. 记忆提取 — 对话后调用 LLM 从对话中抽取值得记住的信息
# ============================================================

def _extract_memories_llm(character_name: str, dialogue_text: str) -> list:
    """调用 LLM 从一轮对话中提取记忆（返回 JSON 数组）。

    提示词走 PromptManager "memory.extract"（第一人称视角 + 类型判定 + importance 校准）。
    LLM 失败（无配置/超时/解析失败）一律返回 []：宁缺毋滥，不降级本地 1.5B
    （其提取的视角/类型质量差，降级等于继续产垃圾）。

    参数:
        character_name: 女主名字
        dialogue_text: 对话文本（"玩家说：...\n女主回复：..."）

    返回:
        list: 记忆条目 dict 列表，每项含 type/content/context/importance/emotional_weight
    """
    from backend.models import Character
    from backend.config import Config
    from backend.game.llm_utils import safe_llm_post, get_active_llm_config
    from backend.game.prompt_registry import get_prompt_manager

    llm_config = get_active_llm_config()
    if not llm_config:
        logger.warning("[Memory] 无激活 LLM 配置，跳过本轮记忆提取")
        return []

    char = Character.query.filter_by(name=character_name).first()
    if char is None:
        logger.warning(f"[Memory] 找不到角色 {character_name}，跳过记忆提取")
        return []

    try:
        pm = get_prompt_manager()
        prompt = pm.render("memory.extract", char, extra={
            "context.dialogue_text": dialogue_text,
        })

        result = safe_llm_post(
            api_url=llm_config['api_url'],
            api_key=llm_config['api_key'],
            model=llm_config.get('model_name', Config.LLM_MODEL),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.3,
            timeout=20,
            call_type="memory_extract",
            character_name=character_name,
            extra_body={"thinking": {"type": "disabled"}},
        )
        if not result:
            return []

        content = result["choices"][0]["message"].get("content", "") or ""
        # 推理模型可能把 token 全花在 reasoning 上导致 content 为空，兜底取 reasoning_content
        if not content.strip():
            content = result["choices"][0]["message"].get("reasoning_content", "") or ""
        if not content.strip():
            logger.warning("[Memory] LLM 记忆提取返回为空")
            return []

        # 提取顶层 JSON 数组（贪心匹配到最后一 ]，避免 content 内含 ] 提前截断）
        match = re.search(r'\[[\s\S]*\]', content)
        if not match:
            logger.warning("[Memory] LLM 记忆提取未返回 JSON 数组")
            return []
        memories = json.loads(match.group())
        if not isinstance(memories, list):
            logger.warning(f"[Memory] LLM 记忆提取返回非列表: {type(memories)}")
            return []

        logger.info(f"[Memory] LLM 提取到 {len(memories)} 条记忆")
        return memories
    except Exception as e:
        logger.warning(f"[Memory] LLM 记忆提取失败: {e}")
        return []


def extract_memories_from_dialogue(character_name: str, player_message: str,
                                  character_reply: str, game_day: int,
                                  game_time: str) -> list:
    """从一轮对话中提取记忆并写入数据库。

    调用 LLM 分析对话内容，识别出值得长期记住的信息（事实、偏好、秘密、情感时刻等），
    每条记忆写入 character_memory 表。

    参数:
        character_name: 女主名字
        player_message: 玩家发送的消息
        character_reply: 女主的回复
        game_day: 当前游戏天
        game_time: 当前游戏时间

    返回:
        list: 新提取的记忆列表（已持久化），每项为 CharacterMemory.to_dict()
    """
    # 构建对话文本
    dialogue_text = f"玩家说：{player_message}\n{character_name}回复：{character_reply}"

    # 调 LLM 提取记忆（失败返回 []，不降级本地 1.5B）
    memories_data = _extract_memories_llm(character_name, dialogue_text)
    if not memories_data:
        return []

    # 将提取的记忆写入数据库（过滤掉低重要度的记忆）
    new_memories = []
    for mem in memories_data:
        try:
            importance = float(mem.get('importance', 50))
            # 阈值过滤：重要度 < 50 的记忆不写入（减少日常闲聊产生的记忆碎片）
            if importance < 50:
                logger.debug(f"[Memory] 跳过低重要度记忆: {mem.get('content', '')} (importance={importance})")
                continue
            memory = CharacterMemory(
                character_name=character_name,
                memory_type=mem.get('type', 'fact'),
                content=mem.get('content', ''),
                context=mem.get('context', ''),
                importance=importance,
                emotional_weight=float(mem.get('emotional_weight', 0)),
                source_day=game_day,
                source_time=game_time,
            )
            db.session.add(memory)
            new_memories.append(memory)
            # 向量失败不影响文本记忆落库；后台补偿任务稍后只调用 LM Studio 重试。
            try:
                memory.embedding = _compute_memory_embedding(memory.content)
            except Exception as emb_err:
                logger.warning(f"[Memory] embedding 暂不可用，文本记忆先落库待补偿: {emb_err}")
        except Exception as e:
            logger.error(f"[Memory] 写入记忆失败: {e}, 数据: {mem}")

    if new_memories:
        db.session.commit()
        logger.info(f"[Memory] {character_name} 新增 {len(new_memories)} 条记忆")

    return [m.to_dict() for m in new_memories]


# ============================================================
# 2. 记忆召回 — 对话前根据当前话题召回相关记忆
# ============================================================

def recall_relevant_memories(character_name: str, player_message: str,
                            char_mood: float = 50.0, max_memories: int = 10) -> str:
    """基于 BGE-small-zh embedding 的语义记忆召回。

    召回策略：
      1. 语义召回 — 用 embedding 余弦相似度替代原 2-gram 关键词匹配
      2. 情感触发召回 — 当前情绪状态与记忆的情感权重匹配时优先召回
      3. 淡化记忆降权 — is_faded=True 的记忆降低优先级，但不完全排除

    参数:
        character_name: 女主名字
        player_message: 玩家当前发送的消息
        char_mood: 女主当前心情值 (0-100)
        max_memories: 最多召回几条记忆（控制 prompt 长度）

    返回:
        str: 格式化的记忆文本，可注入 System Prompt。无记忆时返回空字符串。
    """
    import numpy as np

    # 1. 编码玩家消息（仅 LM Studio；失败则跳过语义召回）
    try:
        raw_vec = _encode_via_lm_studio(player_message)
        query_vec = np.array(raw_vec, dtype='float32')
        query_vec = query_vec / np.linalg.norm(query_vec)
    except Exception as e:
        logger.warning(f"[Memory] LM Studio 查询编码失败，跳过本轮语义召回: {e}")
        return ""

    # 2. 查询该角色所有有 embedding 且未淡化的记忆
    memories = CharacterMemory.query.filter(
        CharacterMemory.character_name == character_name,
        CharacterMemory.embedding != None,   # noqa: E711
        CharacterMemory.is_faded == False
    ).all()

    if not memories:
        return ""

    # 3. 构建记忆向量矩阵 + 计算余弦相似度（向量已归一化，点积=余弦相似度）
    memory_vecs = np.array([
        _deserialize_embedding(m.embedding) for m in memories
    ])
    base_similarities = np.dot(memory_vecs, query_vec)  # shape: (n,)

    # 4. 多因子综合打分
    scores = []
    for i, mem in enumerate(memories):
        score = base_similarities[i] * 10.0  # 语义相似度基础分（0~10）

        # 重要度加权 (0~10)
        score += (mem.importance or 50) / 100.0 * 10.0

        # 情感触发
        if mem.emotional_weight and mem.emotional_weight > 60:
            if char_mood < 40:
                score += 15.0
            elif char_mood > 70:
                score += 5.0

        # 重复访问降权
        if mem.access_count and mem.access_count > 3:
            score *= 0.7

        scores.append((mem, score))

    # 5. 排序取 Top-N
    scores.sort(key=lambda x: x[1], reverse=True)

    top_memories = scores[:max_memories]
    # 过滤得分过低
    top_memories = [(m, s) for m, s in top_memories if s >= 5.0]

    if not top_memories:
        return ""

    # 更新被召回记忆的 access_count 和 last_accessed
    for mem, _ in top_memories:
        mem.access_count = (mem.access_count or 0) + 1
        mem.last_accessed = beijing_now()
    db.session.commit()

    # 格式化为可注入 prompt 的文本
    type_label_map = {
        'fact': '事实', 'preference': '偏好', 'event': '事件',
        'emotion': '情感', 'secret': '秘密'
    }
    lines = ["【她的回忆】"]
    for mem, _ in top_memories:
        type_label = type_label_map.get(mem.memory_type, mem.memory_type or '记忆')
        # 补上记忆来源天数（source_day），让模型知道该记忆发生在第几天，
        # 避免把"第2天床事件"等记忆误判为"昨天"。source_day 缺失时不强加。
        _day_tag = f"（第{mem.source_day}天）" if mem.source_day is not None else ""
        lines.append(f"- 她想起了：{mem.content}{_day_tag}（{type_label}）")

    return '\n'.join(lines)


# ============================================================
# 3. 记忆淡化 — 每日 tick 时调用，模拟人脑遗忘
# ============================================================

def fade_memories(character_name: str, fade_threshold: float = 40.0):
    """淡化低重要度的记忆。

    在每日 tick 时调用，将重要度低于阈值的记忆标记为已淡化。
    高情感权重的记忆即使重要度低也不易淡化（情感记忆更持久）。

    参数:
        character_name: 女主名字
        fade_threshold: 淡化阈值，重要度低于此值的记忆将被淡化
    """
    # 查询所有未淡化的记忆
    active_memories = CharacterMemory.query.filter_by(
        character_name=character_name,
        is_faded=False
    ).all()

    faded_count = 0
    for mem in active_memories:
        # 淡化判定：重要度 < 阈值，但高情感权重的记忆有抗性
        effective_importance = mem.importance or 50

        # 情感权重越高，越不容易淡化（每 10 点情感权重 = +5 重要度抗性）
        emotional_resistance = (mem.emotional_weight or 0) / 10.0 * 5
        effective_importance += emotional_resistance

        # 被频繁召回的记忆也不易淡化
        access_bonus = min((mem.access_count or 0) * 2, 20)
        effective_importance += access_bonus

        if effective_importance < fade_threshold:
            mem.is_faded = True
            faded_count += 1

    if faded_count > 0:
        db.session.commit()
        logger.info(f"[Memory] {character_name} 淡化了 {faded_count} 条记忆")


# ============================================================
# 3.5. NerdMemo 召回 — 外部 Memos 笔记在游戏中的回忆
# ============================================================

def _nm_nickname_label(player_nickname: str = '') -> str:
    """动态生成 NerdMemo 标签名

    降级链: player_nickname → '你'
    """
    return player_nickname.strip() if player_nickname and player_nickname.strip() else '你'


def recall_nm(player_nickname: str = '', game_date=None, max_memories: int = 3) -> str:
    """对话召回 NerdMemo：语义匹配 + 当年今日日期加权

    调用时机：dialogue.call_llm() 中，Tier 4 角色对话前注入。

    Args:
        player_nickname: 女主对玩家的称呼
        game_date: (year, month, day) 元组，游戏内当天日期
        max_memories: 最多召回数

    Returns:
        格式化后的内容块，无匹配时返回空字符串。
    """
    import numpy as np
    from backend.models import NerdMemo

    label = _nm_nickname_label(player_nickname)

    # 构建 sematic_query：用户笔记的语义锚点
    semantic_query = f"我的笔记 回忆 记录 {label}"
    try:
        raw_vec = _encode_via_lm_studio(semantic_query)
        query_vec = np.array(raw_vec, dtype='float32')
        query_vec = query_vec / np.linalg.norm(query_vec)
    except Exception as e:
        logger.warning(f"[NerdMemo] LM Studio 查询编码失败，跳过本轮备忘录语义召回: {e}")
        return ""

    memos = NerdMemo.query.filter(
        NerdMemo.embedding != None  # noqa: E711
    ).all()

    if not memos:
        return ""

    # embedding 余弦相似度
    memo_vecs = np.array([_deserialize_embedding(m.embedding) for m in memos])
    base_similarities = np.dot(memo_vecs, query_vec)

    scores = []
    for i, memo in enumerate(memos):
        score = base_similarities[i] * 10.0  # 语义基础分 0~10
        score += (memo.importance or 50) / 100.0 * 5.0  # 重要度加权 0~5

        # 当年今日加权：月日匹配时大幅加分
        if game_date and len(game_date) == 3:
            gy, gm, gd = game_date
            my, mm, md = memo.get_game_date()
            if gm == mm and gd == md:
                score += 20.0  # 当年今日强信号

        # 重复访问降权
        if memo.access_count and memo.access_count > 3:
            score *= 0.7

        scores.append((memo, score))

    scores.sort(key=lambda x: x[1], reverse=True)
    top = [(m, s) for m, s in scores[:max_memories] if s >= 2.0]

    if not top:
        return ""

    for memo, _ in top:
        memo.access_count = (memo.access_count or 0) + 1
        memo.last_accessed = beijing_now()
    db.session.commit()

    lines = [
        f"【{label}的真实笔记】",
    ]
    for memo, _ in top:
        lines.append(f"- {memo.snippet or memo.content[:120]}")
    return '\n'.join(lines)


def recall_nm_for_event(player_nickname: str = '', game_date=None, max_memories: int = 3) -> str:
    """Tick 事件召回 NerdMemo：当年今日日期匹配 + 生成指导语

    调用时机：event.llm_generate_event() 中，Tier 4 角色生成每日事件前注入。

    Args:
        player_nickname: 女主对玩家的称呼
        game_date: (year, month, day) 元组，游戏内当天日期
        max_memories: 最多召回数

    Returns:
        格式化后的【当年今日】内容块，无匹配时返回空字符串。
    """
    from backend.models import NerdMemo

    if not game_date or len(game_date) != 3:
        return ""

    gy, gm, gd = game_date
    label = _nm_nickname_label(player_nickname)

    # 仅日期匹配：game_created_at 月日 = 当前游戏日期月日
    all_memos = NerdMemo.query.all()
    matched = []
    for m in all_memos:
        my, mm, md = m.get_game_date()
        if mm == gm and md == gd:
            matched.append(m)

    if not matched:
        return ""

    # 按 real_created_at 排序，取最近 max_memories 条
    matched.sort(key=lambda m: m.real_created_at or beijing_now(), reverse=True)
    top = matched[:max_memories]

    for memo in top:
        memo.access_count = (memo.access_count or 0) + 1
        memo.last_accessed = beijing_now()
    db.session.commit()

    lines = [f"【{label}的笔记·当年今日】"]
    lines.append(f"（以下是{label}在过往年份的今天记下的笔记，请在生成事件时自然融入这些回忆，让角色表现出怀念、感慨或与过往经历呼应的情绪。）")
    for memo in top:
        lines.append(f"- {memo.snippet or memo.content[:150]}")
    return '\n'.join(lines)


# ============================================================
# 4. 辅助函数
# ============================================================

def get_memory_summary(character_name: str, limit: int = 10, memory_type: str = None,
                       include_faded: bool = False) -> list:
    """获取角色的记忆摘要列表（供前端展示）。

    参数:
        character_name: 女主名字
        limit: 最多返回几条
        memory_type: 按类型筛选（fact/preference/event/emotion/secret），None 表示全部
        include_faded: 是否包含已淡化记忆（默认排除——淡化=不再参与召回，面板也不展示）

    返回:
        list: 记忆字典列表，按形成时间倒序（最新在前）
    """
    q = CharacterMemory.query.filter_by(character_name=character_name)
    if not include_faded:
        q = q.filter_by(is_faded=False)
    if memory_type:
        q = q.filter_by(memory_type=memory_type)
    memories = q.order_by(CharacterMemory.source_day.desc(), CharacterMemory.id.desc()).limit(limit).all()
    return [m.to_dict() for m in memories]


def get_memory_count(character_name: str) -> dict:
    """统计角色的记忆数量（按类型分组）。

    返回:
        dict: {'total': 总数, 'active': 未淡化, 'faded': 已淡化,
               'by_type': {'fact': N, 'preference': N, ...}}
    """
    all_memories = CharacterMemory.query.filter_by(character_name=character_name).all()
    total = len(all_memories)
    faded = sum(1 for m in all_memories if m.is_faded)
    by_type = {}
    for m in all_memories:
        t = m.memory_type or 'fact'
        by_type[t] = by_type.get(t, 0) + 1

    return {
        'total': total,
        'active': total - faded,
        'faded': faded,
        'by_type': by_type,
    }
