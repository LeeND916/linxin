"""B 阶段历史记忆精修 — LLM 批量处理（接 A 阶段 cleanup_memory_history_a.py 之后跑）。

选取范围（A 之后仍有问题的存活记忆）：
  - content 含角色本名 / "玩家" / "她" / "他"（句中人称歧义，A 的确定性替换覆盖不了）
  - 类型可疑：preference 但不含 喜欢/讨厌/偏好/爱/倾向 等词；emotion 且长度<=8（孤立标签）
  - content 以 "我回复：/回复：" 开头（对话原文当 content 的残留）

LLM 每批 20 条，输出逐条裁决：keep（可改写）/ fade（垃圾）；同步修 type 与 importance。
改写一律第一人称：我=角色本人、你=玩家；"他/她"按语境处理（指玩家→你，指第三方→保留并明确化）。

安全：
  - 默认 dry-run（只选取与统计，不调 LLM 不改库）；--apply 执行
  - --apply 启动时备份将处理记录到 data/memory_backup_b_<时间戳>.json
  - 状态文件 data/memory_refine_b_state.json 记录已处理 id，中断后重跑自动续跑
  - 改写内容的 embedding 置空，待 LM Studio 补偿重算

用法：
  python backend/scripts/refine_memory_history_b.py --limit 20      # 试跑一批
  python backend/scripts/refine_memory_history_b.py --apply         # 全量
  python backend/scripts/refine_memory_history_b.py --apply --batch-size 20
"""
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app import create_app

app = create_app()

APPLY = "--apply" in sys.argv
BATCH_SIZE = 20
LIMIT = None
for _a in sys.argv:
    if _a.startswith("--limit"):
        try:
            LIMIT = int(_a.split("=", 1)[1])
        except (IndexError, ValueError):
            LIMIT = None
_state_path = os.path.join("data", "memory_refine_b_state.json")


def _load_state() -> set:
    if os.path.exists(_state_path):
        try:
            with open(_state_path, encoding="utf-8") as f:
                return set(json.load(f).get("processed_ids", []))
        except Exception:
            return set()
    return set()


def _save_state(done: set):
    os.makedirs("data", exist_ok=True)
    with open(_state_path, "w", encoding="utf-8") as f:
        json.dump({"processed_ids": sorted(done)}, f)


def _parse_json_array(text: str):
    if not text.strip():
        text2 = ""
    m = re.search(r'\[[\s\S]*\]', text)
    if not m:
        return None
    try:
        data = json.loads(m.group())
        return data if isinstance(data, list) else None
    except Exception:
        return None


REFINE_PROMPT = """你是记忆整理模块。下面是一个角色的{n}条历史记忆（JSON 数组，每条含 id/type/content）。
请逐条裁决并返回 JSON 数组，每条格式：
{{"id": <原id>, "action": "keep" 或 "fade", "type": "fact/preference/event/emotion/secret/period_summary", "content": "<整理后的内容>", "importance": <0-100>}}

【整理规则】
1. 人称（最重要）：content 必须是角色第一人称视角——"我"=角色本人，"你"=玩家。
   - content 中的角色本名 → "我"；"玩家" → "你"。
   - "他/她"按语境：指玩家 → "你"；指第三方他人 → 保留（必要时补一个身份词让指代明确，如"他（经纪人）"）。
2. 垃圾裁决（action=fade）：无信息量流水账（如"我们在聊天"）、与角色无关的纯玩家状态、孤立无场景的情绪标签（如"轻松愉快"）、纯属对话原文没有记忆价值。
3. 类型修正：fact=角色的具体事实；preference=角色喜欢/讨厌什么；event=两人之间的具体事件；emotion=有对象有场景的感受；secret=隐秘想法；period_summary=阶段总结（这类保持不动）。
4. importance 校准：日常小事 30-49；普通事实/偏好 50-69；重要事实/承诺/关系进展 70-89；秘密/表白/重大转折 90+。
5. content 控制在 60 字内，保留原有具体信息（人名外的细节、数字、歌名等不要丢）。
6. action=keep 时也必须返回 type/content/importance（content 若无需改写就原样返回）。
7. 只返回 JSON 数组，不要解释文字。

历史记忆：
{records_json}"""

SELECT_HINT_PREF = ("喜欢", "讨厌", "偏好", "偏爱", "爱", "倾向", "更想", "钟意", " favorite")


def _needs_refine(m, name: str) -> bool:
    c = m.content or ""
    if m.is_faded:
        return False
    if name in c or "玩家" in c or "她" in c or "他" in c:
        return True
    if c.strip().startswith(("我回复：", "回复：")):
        return True
    if m.memory_type == "preference" and not any(k in c for k in SELECT_HINT_PREF):
        return True
    if m.memory_type == "emotion" and len(c.strip()) <= 8:
        return True
    return False


with app.app_context():
    from backend.models import db, CharacterMemory
    from backend.game.llm_utils import safe_llm_post, get_active_llm_config
    from backend.config import Config

    all_ms = CharacterMemory.query.all()
    name_cache = {m.character_name for m in all_ms}
    todo = [m for m in all_ms if _needs_refine(m, m.character_name)]
    done = _load_state()
    todo = [m for m in todo if m.id not in done]
    if LIMIT:
        todo = todo[:LIMIT]

    print(f"全表 {len(all_ms)} 条 | 需精修 {len(todo)} 条 | 已处理(断点) {len(done)} 条 | 模式: {'APPLY' if APPLY else 'DRY-RUN'}")
    by_char = {}
    for m in todo:
        by_char[m.character_name] = by_char.get(m.character_name, 0) + 1
    for name, n in sorted(by_char.items(), key=lambda x: -x[1]):
        print(f"  {name}: {n}")

    if not APPLY:
        print("\nDRY-RUN 结束。--apply 执行（每批 20 条调一次 LLM）。")
        sys.exit(0)

    llm_config = get_active_llm_config()
    if not llm_config:
        print("无激活 LLM 配置，退出")
        sys.exit(1)

    # 备份将处理的记录
    backup_path = os.path.join("data", f"memory_backup_b_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump([m.to_dict() for m in todo], f, ensure_ascii=False, indent=1)
    print(f"已备份待处理记录 -> {backup_path}")

    id_map = {m.id: m for m in todo}
    ids = [m.id for m in todo]
    n_fade = n_rw = n_keep = n_batch = 0
    total_batches = (len(ids) + BATCH_SIZE - 1) // BATCH_SIZE

    for bi in range(0, len(ids), BATCH_SIZE):
        batch_ids = ids[bi:bi + BATCH_SIZE]
        records = [{"id": m.id, "type": m.memory_type, "content": m.content}
                   for m in (id_map[i] for i in batch_ids)]
        prompt = REFINE_PROMPT.replace("{n}", str(len(records))).replace(
            "{records_json}", json.dumps(records, ensure_ascii=False, indent=1))
        result = safe_llm_post(
            api_url=llm_config['api_url'],
            api_key=llm_config['api_key'],
            model=llm_config.get('model_name', Config.LLM_MODEL),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=3000,
            temperature=0.1,
            timeout=180,
            call_type="memory_refine",
            extra_body={"thinking": {"type": "disabled"}},
        )
        n_batch += 1
        out = None
        if result:
            content = result["choices"][0]["message"].get("content", "") or ""
            if not content.strip():
                content = result["choices"][0]["message"].get("reasoning_content", "") or ""
            out = _parse_json_array(content)
        if not out:
            print(f"[batch {n_batch}/{total_batches}] LLM 返回无效，跳过该批（ids={batch_ids[:3]}...）")
            continue

        applied = 0
        for item in out:
            try:
                mid = int(item.get("id"))
                m = id_map.get(mid)
                if m is None:
                    continue
                if item.get("action") == "fade":
                    m.is_faded = True
                    n_fade += 1
                    applied += 1
                else:
                    new_c = str(item.get("content", "") or "").strip()
                    new_t = str(item.get("type", m.memory_type) or m.memory_type).strip()
                    try:
                        new_i = float(item.get("importance", m.importance))
                    except (TypeError, ValueError):
                        new_i = m.importance
                    if new_c and new_c != m.content:
                        m.content = new_c[:200]
                        m.embedding = None  # 内容变了，向量待重算
                        n_rw += 1
                        applied += 1
                    if new_t in ("fact", "preference", "event", "emotion", "secret", "period_summary"):
                        m.memory_type = new_t
                    m.importance = max(0.0, min(100.0, new_i))
            except Exception as e:
                print(f"  单条应用失败: {e} item={item}")
        db.session.commit()
        done.update(batch_ids)
        _save_state(done)
        print(f"[batch {n_batch}/{total_batches}] 应用 {applied}/{len(batch_ids)} 条（累计 fade={n_fade} rw={n_rw}）")

    print(f"\n完成：fade={n_fade} 改写={n_rw} 保留微调={n_keep} | 状态已存 {_state_path}")
    print("提示：被改写记录的 embedding 已置空，启动 LM Studio(10039) 后由后台补偿重算。")
