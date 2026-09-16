# -*- coding: utf-8 -*-
"""外貌分段体检 + 眉眼拆分校验（对 game.db 只读，除非显式 --apply）

用法（系统 Python 3.11）：
    python plugins/portrait_lab/scripts/check_appearance.py            # 全库体检报告
    python plugins/portrait_lab/scripts/check_appearance.py --name 晓月 # 单角色详细拆分轨迹
    python plugins/portrait_lab/scripts/check_appearance.py --apply    # 规范化写库（先自动备份）

默认 dry-run，只读不写。--apply 会：
  1. VACUUM INTO 备份 game.db（WAL 模式下 cp 会漏数据，必须用 VACUUM INTO）
  2. 把可拆分角色的「眉眼」段规范化为「眼镜/眉毛/眼睛」三段
  3. 不可拆分的角色原样跳过，绝不产出错误数据
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from portrait_lab.appearance_parser import (          # noqa: E402
    analyze, detect_anomalies, normalize_appearance, split_brow_eye,
    locate_segment, parse_segments, VARIABLE_SEGMENTS,
)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
DB = os.path.join(ROOT, 'data', 'game.db')

BJ = timezone(timedelta(hours=8))       # 北京时间 UTC+8
LEVEL_MARK = {'block': '[阻断]', 'warn': '[警告]', 'info': '[提示]'}
SEG_LABEL = {'face': '脸型', 'eye': '眼睛', 'brow': '眉毛', 'nose': '鼻子',
             'mouth': '嘴唇', 'hair': '发型', 'skin': '肤色', 'eyewear': '眼镜'}


def fetch_characters(where_name=None):
    con = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    sql = ("SELECT id, name, gender, portrait_seed, appearance FROM character "
           "WHERE appearance IS NOT NULL AND appearance != ''")
    args = []
    if where_name:
        sql += " AND name = ?"
        args.append(where_name)
    sql += " ORDER BY id"
    rows = con.execute(sql, args).fetchall()
    con.close()
    return rows


def report_all():
    rows = fetch_characters()
    print(f'共 {len(rows)} 位角色有 appearance 数据\n')

    header = '  id  角色      ' + ''.join(SEG_LABEL[k].ljust(8) for k in VARIABLE_SEGMENTS) + '眼镜'
    print(header)
    print('  ' + '-' * (len(header) + 8))

    all_issues = []
    for r in rows:
        info = analyze(r['appearance'])
        cells = []
        for k in VARIABLE_SEGMENTS:
            d = info[k]
            if not d['ok']:
                cells.append('缺失'.ljust(6))
            elif d['source'].startswith('split-L'):
                cells.append(('OK ' + d['source'][-2:]).ljust(6))
            elif d['source'] == 'sniff':
                cells.append('嗅探'.ljust(6))
            else:
                cells.append('OK'.ljust(7))
        glass = info['eyewear']['text'][:10] if info['eyewear']['ok'] else '-'
        print(f"  {r['id']:>2}  {r['name']:<8}" + ''.join(cells) + f'  {glass}')
        for lvl, dim, msg in detect_anomalies(r['appearance']):
            all_issues.append((lvl, r['name'], dim, msg))

    print('\n' + '=' * 76)
    print('体检问题清单')
    print('=' * 76)
    for lvl in ('block', 'warn', 'info'):
        items = [x for x in all_issues if x[0] == lvl]
        if not items:
            continue
        print(f'\n{LEVEL_MARK[lvl]} 共 {len(items)} 条')
        for _l, name, dim, msg in items:
            print(f'  {name:<8} [{SEG_LABEL.get(dim, dim)}] {msg}')

    blocks = [x for x in all_issues if x[0] == 'block']
    print('\n' + '=' * 76)
    if blocks:
        names = sorted({x[1] for x in blocks})
        print(f'结论：{len(names)} 位角色存在阻断项，需修复后才能全维度实验 → {"、".join(names)}')
    else:
        print('结论：全部角色通过体检，可直接开跑')


def report_one(name):
    rows = fetch_characters(name)
    if not rows:
        print(f'未找到角色：{name}')
        return
    r = rows[0]
    print(f"===== {r['name']} (id={r['id']}, seed={r['portrait_seed']}) =====\n")
    print('--- 原始 appearance ---')
    print(r['appearance'])

    segments = parse_segments(r['appearance'])
    brow_eye, src = locate_segment(segments, 'brow')
    print(f'\n--- 眉眼段定位方式：{src} ---')
    print(brow_eye or '(无)')

    if brow_eye:
        s = split_brow_eye(brow_eye)
        print(f'\n--- 拆分轨迹（降级层级 L{s["level"]}）---')
        for text, cls, lv in s['trace']:
            print(f'  L{lv} [{cls:<8}] {text}')
        print('\n--- 拆分结果 ---')
        print(f'  眼镜：{s["eyewear"] or "(无)"}')
        print(f'  眉毛：{s["brow"] or "(无)"}')
        print(f'  眼睛：{s["eye"] or "(无)"}')
        print(f'  可用：{"否 —— 需人工规范化" if s["unsplittable"] else "是"}')

    print('\n--- 体检 ---')
    issues = detect_anomalies(r['appearance'])
    if not issues:
        print('  无异常')
    for lvl, dim, msg in issues:
        print(f'  {LEVEL_MARK[lvl]} [{SEG_LABEL.get(dim, dim)}] {msg}')

    print('\n--- 规范化预览（--apply 后库中的样子）---')
    normalized = normalize_appearance(r['appearance'])
    if normalized == r['appearance']:
        print('  (不可拆分或无需改动，原样保留)')
    else:
        print(normalized)


def apply_normalize():
    stamp = datetime.now(BJ).strftime('%Y%m%d_%H%M%S')
    backup = os.path.join(ROOT, 'data', f'game.db.bak_broweye_split_{stamp}')

    con = sqlite3.connect(DB)
    print(f'备份数据库 → {backup}')
    con.execute("VACUUM INTO ?", (backup,))     # WAL 模式必须用 VACUUM INTO
    con.row_factory = sqlite3.Row

    rows = con.execute(
        "SELECT id, name, appearance FROM character "
        "WHERE appearance IS NOT NULL AND appearance != ''").fetchall()

    changed, skipped = [], []
    for r in rows:
        new = normalize_appearance(r['appearance'])
        if new == r['appearance']:
            skipped.append(r['name'])
            continue
        con.execute("UPDATE character SET appearance = ? WHERE id = ?", (new, r['id']))
        changed.append(r['name'])

    con.commit()
    con.close()
    print(f'\n已规范化 {len(changed)} 位：{"、".join(changed) if changed else "无"}')
    print(f'跳过 {len(skipped)} 位：{"、".join(skipped) if skipped else "无"}')
    print(f'\n如需回滚：删除 data/game.db 并把 {os.path.basename(backup)} 改名回 game.db')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', help='只看单个角色的详细拆分轨迹')
    ap.add_argument('--apply', action='store_true', help='规范化写库（默认只读）')
    a = ap.parse_args()

    if a.apply:
        apply_normalize()
    elif a.name:
        report_one(a.name)
    else:
        report_all()
