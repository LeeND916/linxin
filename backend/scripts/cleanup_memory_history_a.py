"""A 阶段历史记忆清理 — 纯代码规则（零 LLM 成本）。

规则：
  1. 淡化垃圾（is_faded=True，不删除可恢复）：
     - content 含"在聊天/正在聊天/在对话"且长度<=30（无信息量流水账）
     - content 以"玩家说："开头（对话原文当 content）
     - content 去空白后长度<=4（无信息量碎片："姜茶""大草原"等）
     - 同角色重复 content（保留最小 id，其余淡化）
  2. 人称改写（确定性替换，改写后 embedding 置空待重算）：
     - "角色本名" -> "我"
     - "玩家" -> "你"
     - 开头"她" -> "我"（仅句首，句中"她/他"留给 B 阶段 LLM 精修）
  3. 不动 type / importance（B 阶段 LLM 精修处理）

安全：
  - 默认 dry-run（只报告不改动）；--apply 才执行
  - --apply 前自动全表备份到 data/memory_backup_<时间戳>.json
  - 人称改写只做确定性替换，新引擎产出的第一人称记忆不会被误改（不含角色名/玩家/句首她）

用法：
  python backend/scripts/cleanup_memory_history_a.py            # dry-run
  python backend/scripts/cleanup_memory_history_a.py --apply    # 执行
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

APPLY = "--apply" in sys.argv

from backend.app import create_app

app = create_app()

GARBAGE_CHAT_KWS = ("在聊天", "正在聊天", "在对话")
MIN_LEN = 4


def rewrite_content(character_name: str, content: str) -> str:
    """确定性人称改写：角色名->我、玩家->你、句首她->我。"""
    out = content.replace(character_name, "我").replace("玩家", "你")
    if out.startswith("她"):
        out = "我" + out[1:]
    return out


with app.app_context():
    from backend.models import db, CharacterMemory

    all_ms = CharacterMemory.query.all()
    total = len(all_ms)
    print(f"全表 {total} 条（{'APPLY 模式' if APPLY else 'DRY-RUN 模式'}）")

    # ── 备份 ──
    if APPLY:
        backup_path = os.path.join("data", f"memory_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        os.makedirs("data", exist_ok=True)
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump([m.to_dict() for m in all_ms], f, ensure_ascii=False, indent=1)
        print(f"已备份全表 -> {backup_path}")

    # ── 规则判定 ──
    fade_ids = set()          # 淡化
    rewrite_map = {}          # id -> new_content（改写）
    seen_content = {}         # (character_name, content) -> min_id（重复检测）

    for m in all_ms:
        if m.is_faded:
            continue
        c = (m.content or "").strip()
        # 重复：同角色同 content，保留最小 id
        key = (m.character_name, c)
        if key in seen_content:
            fade_ids.add(m.id)
            continue
        seen_content[key] = m.id
        # 垃圾模式
        if c.startswith("玩家说："):
            fade_ids.add(m.id)
            continue
        if len(c) <= MIN_LEN:
            fade_ids.add(m.id)
            continue
        if any(k in c for k in GARBAGE_CHAT_KWS) and len(c) <= 30:
            fade_ids.add(m.id)
            continue
        # 人称问题改写
        new_c = rewrite_content(m.character_name, c)
        if new_c != c:
            rewrite_map[m.id] = new_c

    # 改写后可能产生新的重复：二次去重（同角色同新 content）
    seen2 = {}
    for m in all_ms:
        if m.id in fade_ids:
            continue
        new_c = rewrite_map.get(m.id, (m.content or "").strip())
        key = (m.character_name, new_c)
        if key in seen2:
            # 保留未改写的原记录（或更小 id），另一条淡化
            if m.id in rewrite_map:
                fade_ids.add(m.id)
                rewrite_map.pop(m.id)
            elif seen2[key] in rewrite_map:
                fade_ids.add(seen2[key])
                rewrite_map.pop(seen2[key])
                seen2[key] = m.id
            else:
                fade_ids.add(m.id)
        else:
            seen2[key] = m.id

    by_char_fade = {}
    for m in all_ms:
        if m.id in fade_ids:
            by_char_fade[m.character_name] = by_char_fade.get(m.character_name, 0) + 1
    by_char_rw = {}
    for m in all_ms:
        if m.id in rewrite_map:
            by_char_rw[m.character_name] = by_char_rw.get(m.character_name, 0) + 1

    print(f"\n将淡化 {len(fade_ids)} 条：")
    for name, n in sorted(by_char_fade.items(), key=lambda x: -x[1]):
        print(f"  {name}: {n}")
    print(f"将改写 {len(rewrite_map)} 条：")
    for name, n in sorted(by_char_rw.items(), key=lambda x: -x[1]):
        print(f"  {name}: {n}")

    print("\n改写样例（前 8 条）:")
    shown = 0
    for m in all_ms:
        if m.id in rewrite_map and shown < 8:
            print(f"  [{m.id}] {m.content[:40]!r}")
            print(f"    -> {rewrite_map[m.id][:40]!r}")
            shown += 1

    if not APPLY:
        print("\nDRY-RUN 结束（未改动任何数据）。加 --apply 执行。")
        sys.exit(0)

    # ── 执行 ──
    n_fade = n_rw = 0
    for m in all_ms:
        if m.id in fade_ids:
            m.is_faded = True
            n_fade += 1
        elif m.id in rewrite_map:
            m.content = rewrite_map[m.id]
            m.embedding = None  # 内容变了，向量待重算
            n_rw += 1
    db.session.commit()
    print(f"\n完成：淡化 {n_fade} 条，改写 {n_rw} 条（其 embedding 已置空，待 LM Studio 补偿重算）。")
