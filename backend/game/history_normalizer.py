"""历史对话格式归一器 — 把旧散文样本重排为 (描述)[对话内容] 结构。

背景：旧对话的 raw_reply/content 多为纯散文（没有 (描述)[对话内容] 结构），
回灌给 LLM 时成为"错误格式"的 few-shot 样本，把模型带偏、导致新回复也不守格式。

本模块在对话时按需（异步、后台线程）批量调用一次 LLM，把散文重排为
(描述)[对话内容]，结果持久化到 md 文件的 [normalized_start]...[normalized_end] 块。
之后每次读历史都直接复用归一结果，永不再重复调用 LLM（幂等）。

设计要点：
- 只处理回灌窗口（最后 NORMALIZE_WINDOW 条）内的角色消息，因为更早的历史根本不喂给 LLM；
- 分桶批量调用，把 N 条散文压成 1 次 LLM 请求，控制成本；
- 失败静默跳过，绝不阻塞正常对话；
- 写入经 chat_history._write_lock 串行化，避免与 append_message 互相覆盖。
"""
import re
import json
import logging

logger = logging.getLogger(__name__)

# 回灌窗口（与 dialogue.MAX_HISTORY_MESSAGES 对齐）
NORMALIZE_WINDOW = 20
# 每桶最多条数（控制单次 LLM 输出长度 / token 消耗）
BATCH_SIZE = 5


def _looks_structured(text: str) -> bool:
    """判断文本是否已含 (描述)[对话内容] 结构（至少一个括号+方括号对）。"""
    if not text:
        return False
    return bool(re.search(r'\([^()]*\)\[[^\]]*\]', text))


def _strip_effect_marker(text: str) -> str:
    """剥离 LLM 输出中可能携带的【属性变化：...】标记，避免污染回灌。"""
    return re.sub(r'【属性变化[：:][^】]*】', '', text or '').strip()


def normalize_pending_histories(flask_app=None):
    """筛选回灌窗口内未归一的角色散文，批量调 LLM 归一并写回 md。

    幂等：已有 normalized_content 或 raw_reply 已结构化的条目跳过。
    失败静默跳过（不影响正常对话）。供 call_llm 在后台线程调用。
    flask_app: 调用方（请求线程）传入的 Flask app 引用，用于后台线程内
    重建应用上下文，否则 load_all_sessions / write_normalized_block 会因缺少
    上下文而静默失败。
    """
    try:
        from backend.chat_history import load_all_sessions, write_normalized_block
        from backend.game.llm_utils import safe_llm_post, get_active_llm_config
        # 复用 dialogue 的格式判定，确保归一器与实时格式规则一致：
        # 散文 / 已结构化 / 合法台词 都视为「可接受」，只有格式1（[长描述]说话）需归一。
        from backend.game.dialogue import _reply_is_structured
    except Exception as e:
        logger.warning(f"[history_normalizer] 导入失败: {e}")
        return

    # 后台线程没有 Flask 应用上下文，而 load_all_sessions / write_normalized_block
    # 内部会查 DB 活跃角色（get_current_character_name），必须显式 push 一个
    # app context，否则会静默抛 RuntimeError(Working outside of application
    # context) 导致归一无操作。flask_app 由调用方（请求线程）传入。
    import contextlib
    _ctx = flask_app.app_context() if flask_app is not None else contextlib.nullcontext()
    try:
        with _ctx:
            full = load_all_sessions()
            if not full:
                return

            # 仅处理回灌窗口（最后 NORMALIZE_WINDOW 条）内的消息
            start = max(0, len(full) - NORMALIZE_WINDOW)
            window = full[start:]
            offset = start

            # 收集需要归一的目标：(global_index, 散文文本)
            needs = []
            for i, m in enumerate(window):
                # 跳过非角色消息。注意 load_all_sessions 解析返回的 speaker 是
                # 半角 ASCII 'player'/'character'，不能用全角 '玩家' 比对（永远不匹配）。
                if m.get('speaker') != 'character':
                    continue
                if m.get('normalized_content'):
                    continue  # 已归一过，跳过（幂等）
                content = m.get('content') or ''
                if not content.strip():
                    continue
                # 散文 / 已结构化 / 合法台词 一律放过；只有把叙述误塞进方括号的
                # 格式1（[长描述]说话）才需要归一。判定与 dialogue._reply_is_structured
                # 保持一致，避免归一器与实时格式规则自相矛盾、白白消耗 token。
                if _reply_is_structured(content):
                    continue
                needs.append((offset + i, content))

            if not needs:
                return

            cfg = get_active_llm_config()
            if not cfg or not cfg.get('api_key'):
                logger.warning("[history_normalizer] 无可用 LLM 配置，跳过归一")
                return

            # 分桶批量调用，降低请求次数
            for bucket_start in range(0, len(needs), BATCH_SIZE):
                bucket = needs[bucket_start:bucket_start + BATCH_SIZE]
                try:
                    _normalize_bucket(cfg, bucket)
                except Exception as e:
                    logger.warning(f"[history_normalizer] 桶归一失败: {e}")
    except Exception as e:
        logger.warning(f"[history_normalizer] 执行失败（缺应用上下文？）: {e}")


def _normalize_bucket(cfg: dict, bucket: list):
    """对一桶 (global_index, text) 调一次 LLM，返回后逐条写回 md。"""
    from backend.game.llm_utils import safe_llm_post
    indices = [g for g, _ in bucket]
    texts = [t for _, t in bucket]

    system_prompt = (
        "你是对话格式归一器。下面是一段段旧对话散文（角色视角，动作/神态/心理描写与台词混写）。\n"
        "请逐段重排为 (描述)[对话内容] 结构：\n"
        "  - 圆括号 ( ) 内写动作/神态/心理/场景描写；\n"
        "  - 方括号 [ ] 内写该角色的台词；\n"
        "  - 若描述裸写（未用圆括号）或与台词用换行分层，同样按 (描述)[台词] 重排，不要把换行当作分层；\n"
        "  - 只重构呈现方式，绝不增删语义、绝不修改台词原文、不翻译、不发挥；\n"
        "  - 若某段没有明显台词，方括号内写极简旁白或留空；\n"
        "  - 若某段完全是对话无描写，圆括号内写『无』或最简场景。\n"
        "严格只输出一个 JSON 数组（不要解释、不要 markdown 代码块），数组每个元素是对应段落的重排文本字符串。"
    )
    user_prompt = json.dumps(texts, ensure_ascii=False)

    result = safe_llm_post(
        api_url=cfg['api_url'],
        api_key=cfg['api_key'],
        model=cfg.get('model_name', 'deepseek-chat'),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=10000,
        temperature=0.3,
        call_type="history_normalize",
        extra_body={"thinking": {"type": "disabled"}},
    )
    if not result:
        return
    content = result["choices"][0]["message"]["content"]
    arr = _extract_json_array(content)
    if not arr:
        logger.warning("[history_normalizer] LLM 返回无法解析为 JSON 数组，跳过本桶")
        return
    # 与 bucket 按序对齐（模型可能少返回，按序截断）
    for idx, structured in zip(indices, arr):
        structured = _strip_effect_marker(structured)
        if not structured:
            continue
        try:
            from backend.chat_history import write_normalized_block
            write_normalized_block(idx, structured)
        except Exception as e:
            logger.warning(f"[history_normalizer] 写回 normalized 失败 idx={idx}: {e}")


def _extract_json_array(text: str):
    """从 LLM 返回中提取 JSON 数组（容错：去代码块标记、取首个 [...]）。"""
    if not text:
        return None
    t = text.strip()
    # 去掉 ```json ... ``` 包裹
    if t.startswith('```'):
        t = re.sub(r'^```[a-zA-Z]*\n?', '', t)
        t = re.sub(r'\n?```$', '', t)
        t = t.strip()
    # 取最外层 [...]（JSON 字符串内 ] 已转义，直接取首个 [ 到末尾 ] 即可）
    m = re.search(r'\[.*\]', t, re.DOTALL)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(arr, list):
        return None
    return arr
