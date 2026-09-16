#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一次性迁移脚本：统一 emotional_moment 表的 summary 人称视角为女主第一人称。

背景：
    早期 event.emotion_detect 提示词无人称约束，各女主回忆摘要混用
    玩家/对方/她/本名/他 等多种指代（详见提交 f60d9b5 的修复）。本脚本把
    历史脏摘要归一化为女主第一人称视角：
        女主本名 / 她  -> 我   （女主自己）
        玩家 / 对方 / 他 -> 你 （玩家）

为什么用占位符法而不是朴素 replace：
    最初的朴素写法（本名->我、玩家/对方->你、她->我）在『女主本名/她 + 玩家视角的我』
    同句出现时会撞车，例如『沈疏筠见我吃得香』会变成『我见我吃得香』。
    占位符法先把女主标记与玩家标记分开（\ue000=女主, \ue001=玩家），最后一次性
    替换，从而正确得到『我见你吃得香』。

已知需人工判定的特例（写入 EXCEPTIONS，避免规则误伤）：
    - id=148：『说我也想你』里的『我』是女主自己引语，不能翻成『你』。
    - id=202：旧误 apply 遗留的『我夸我』（原值已因备份缺口丢失），修正为合理表述。

用法：
    python clean_emotion_summaries.py            # 前15条 dry-run 预览
    python clean_emotion_summaries.py --limit 0  # 全量 dry-run
    python clean_emotion_summaries.py --apply    # 全量重写并写入（apply 前自动 VACUUM 备份）

注意：
    - 中文以原始 UTF-8 写入（sqlite3 参数化），符合项目 SQL 规范。
    - 不改 created_at / game_day 等时间字段。
    - 幂等：已为第一人称（无本名/她/玩家/对方/他）的条目不会被改动。
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
from datetime import datetime

# 把项目根（backend/ 的上两级）加入 sys.path，使 backend 包可导入（备用）
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))

DB_PATH = "data/game.db"
BACKUP_DIR = "backups"
FEM, PL = "\ue000", "\ue001"  # 女主占位 / 玩家占位
TOKENS = ["玩家", "对方", "她", "他", "你", "我"]

# 规则无法覆盖、需人工给定正确结果的特例（key=id, value=正确摘要）
EXCEPTIONS = {
    148: "清晨床边我掖好被角，轻点额头说我也想你",
    202: "我为妈妈煮了早饭，夸火候刚好，还说自己手酸正好歇歇，那一刻很温馨。",
}


def normalize(summary: str, name: str) -> str:
    """把一条 summary 归一化为女主第一人称视角（我=女主，你=玩家）。"""
    if not summary:
        return summary
    female_ref = (name in summary) or ("她" in summary)
    if not female_ref:
        # 无女主指代：仅把元称谓玩家/对方规范化（多为玩家视角残留）
        return summary.replace("玩家", "你").replace("对方", "你")
    # 女主已用本名/她标识 -> 其余我/他/玩家/对方 全是玩家
    s = summary.replace(name, FEM)
    s = s.replace("她", FEM)
    s = s.replace("他", PL)
    s = s.replace("我", PL)
    s = s.replace("玩家", PL)
    s = s.replace("对方", PL)
    s = s.replace(FEM, "我")   # 女主 -> 我
    s = s.replace(PL, "你")    # 玩家 -> 你
    return s


def count_tokens(s: str) -> dict:
    return {t: s.count(t) for t in TOKENS}


def backup(db_path: str) -> str:
    """用 SQLite 在线备份（VACUUM INTO）捕获 WAL，避免 cp 漏记未 checkpoint 的记录。"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(BACKUP_DIR, f"game_before_clean_v2_{stamp}.db")
    src = sqlite3.connect(db_path)
    src.execute(f"VACUUM INTO '{bak}'")
    src.close()
    return bak


def main() -> None:
    ap = argparse.ArgumentParser(description="统一 emotional_moment.summary 人称")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--apply", action="store_true", help="实际写入（否则仅预览）")
    ap.add_argument("--limit", type=int, default=15,
                    help="dry-run 仅处理前 N 条（0=全部）；--apply 时忽略")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, character_name, summary FROM emotional_moment"
    ).fetchall()
    if not args.apply and args.limit:
        rows = rows[: args.limit]

    before = {t: 0 for t in TOKENS}
    after = {t: 0 for t in TOKENS}
    changed = 0
    samples = []
    exceptions_applied = []
    for r in rows:
        rid = r["id"]
        b = r["summary"] or ""
        if rid in EXCEPTIONS:
            a = EXCEPTIONS[rid]
            if a != b:
                exceptions_applied.append((rid, b, a))
        else:
            a = normalize(b, r["character_name"])
        for t in TOKENS:
            before[t] += count_tokens(b)[t]
            after[t] += count_tokens(a)[t]
        if a != b:
            changed += 1
            if len(samples) < 30:
                samples.append((rid, r["character_name"], b, a))

    print(f"待处理 {len(rows)} 条，将变更 {changed} 条")
    print("清洗前各指代出现次数:", before)
    print("清洗后各指代出现次数:", after)
    if exceptions_applied:
        print("\n--- 人工特例覆盖 ---")
        for rid, b, a in exceptions_applied:
            print(f"  [id={rid}] 前:{b}")
            print(f"           后:{a}")
    print("\n--- 变更样例（前30条）---")
    for rid, nm, b, a in samples:
        print(f"  [id={rid} {nm}]")
        print(f"    前: {b}")
        print(f"    后: {a}")

    if not args.apply:
        print("\n[DRY-RUN] 未写入。加 --apply 执行实际 UPDATE（apply 前会自动备份）。")
        con.close()
        return

    bak = backup(args.db)
    print(f"\n[BACKUP] 已在线备份至 {bak}")

    cur = con.cursor()
    n = 0
    for r in rows:
        rid = r["id"]
        b = r["summary"] or ""
        a = EXCEPTIONS[rid] if rid in EXCEPTIONS else normalize(b, r["character_name"])
        if a != b:
            cur.execute(
                "UPDATE emotional_moment SET summary=? WHERE id=?",
                (a, rid),
            )
            n += 1
    con.commit()
    con.close()
    print(f"[APPLY] 已 UPDATE {n} 条。")


if __name__ == "__main__":
    main()
