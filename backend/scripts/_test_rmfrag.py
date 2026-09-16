# -*- coding: utf-8 -*-
"""临时单测：验证 _remove_marker_from_content 的行为（不依赖 flask/DB）。"""
import os
import re
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))

# 构造与 chat_history.py 一致的运行环境，再 exec 片段
ns = {
    're': re,
    'os': os,
    '_write_lock': threading.Lock(),
    '_CHAT_DIR_PREFIX': '你与',
    '_CHAT_DIR_SUFFIX': '的对话',
    '__file__': os.path.join(os.path.dirname(HERE), 'chat_history.py'),
}
with open(os.path.join(HERE, '_rmfrag.py'), 'r', encoding='utf-8') as f:
    exec(compile(f.read(), '_rmfrag.py', 'exec'), ns)

_remove = ns['_remove_marker_from_content']


def run(content, target):
    marker_re = re.compile(r'^\[photo:\s*\{"id":\s*' + str(target) + r'(?:,[^{}]*)?\}\]\s*$')
    return _remove(content, target, marker_re)


FAIL = 0


def check(title, cond, extra=''):
    global FAIL
    if cond:
        print('  [PASS]', title)
    else:
        FAIL += 1
        print('  [FAIL]', title)
        if extra:
            print('         ' + extra.replace('\n', '\n         '))


# ---------------- 用例 A：照片独占整轮（ghost round） ----------------
A = '''# 2026-08-10 08:00 - 11:59 对话记录

## 2026-08-10 09:29:14 - 晓月


> 第1天 

[photo: {"id": 57}]
'''
print('用例 A：照片独占整轮 -> 整块应被删除')
ra = run(A, 57)
check('标记已移除', '[photo:' not in ra)
check('块头已移除', '09:29:14' not in ra, ra)
check('时间行已移除', '第1天' not in ra, ra)
check('文件标题保留', '对话记录' in ra, ra)

# ---------------- 用例 B：带正文+推理的回复轮 ----------------
B = '''# 2026-08-09 16:00 - 19:59 对话记录

## 2026-08-09 18:32:47 - 苏晴
翻页的动作被你抓得刚好，这光线把我拍得比庭审时温柔多了。

[raw_reply_start]
(我把案卷轻轻合上)[翻页的动作被你抓得刚好。]
[raw_reply_end]

[effects: {"player_affection": -19.5, "mood": -10.0}]
> 第9天 18:34:09

<details>
<summary>推理过程</summary>

他刚给我拍了张照片。

</details>

[photo: {"id": 21}]
'''
print('用例 B：带正文的回复轮 -> 仅删标记，正文保留')
rb = run(B, 21)
check('标记已移除', '[photo:' not in rb)
check('正文保留', '翻页的动作被你抓得刚好' in rb, rb)
check('块头保留', '18:32:47' in rb, rb)
check('时间行保留', '第9天 18:34:09' in rb, rb)
check('推理块保留', '<details>' in rb, rb)
check('effects 保留', '[effects:' in rb, rb)

# ---------------- 用例 C：同一 id 在同文件出现两次 ----------------
C = '''# 标题

## 2026-08-09 19:20:00 - 苏晴
这支钢笔我很喜欢。

> 第10天 19:20:00

[photo: {"id": 23}]

## 2026-08-09 19:34:12 - 苏晴


> 第10天 

[photo: {"id": 23}]
'''
print('用例 C：同一 id 出现两次（一轮带正文、一轮独占）')
rc = run(C, 23)
check('两处标记都已移除', '[photo:' not in rc, rc)
check('带正文那轮保留', '这支钢笔我很喜欢' in rc, rc)
check('带正文那轮块头保留', '19:20:00 - 苏晴' in rc, rc)
check('独占那轮整块删除', '19:34:12' not in rc, rc)

# ---------------- 用例 D：非目标 id 不受影响 ----------------
print('用例 D：删除 id=16 时不应动 id=21')
rd = run(B, 16)
check('内容完全未变', rd == B)

# ---------------- 用例 E：id 前缀冲突（删 2 不能命中 21） ----------------
print('用例 E：删除 id=2 不应误伤 id=21')
re_ = run(B, 2)
check('内容完全未变', re_ == B, '差异出现，说明前缀误匹配')

# ---------------- 用例 F：带额外字段的标记 ----------------
F = '''# 标题

## 2026-08-10 10:00:00 - 沈念


> 第3天 10:00:00

[photo: {"id": 69, "path": "photos/a.png"}]
'''
print('用例 F：标记含额外字段')
rf = run(F, 69)
check('标记已移除', '[photo:' not in rf, rf)
check('整块删除', '10:00:00 - 沈念' not in rf, rf)

print()
print('=' * 40)
print('FAILED:', FAIL)
sys.exit(1 if FAIL else 0)
