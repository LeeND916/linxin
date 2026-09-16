# -*- coding: utf-8 -*-
"""临时端到端测试：在沙箱目录里真实调用 chat_history.remove_photo_marker，验证文件遍历/写回/计数。

做法：import 真实模块后，把模块级 __file__ 指到沙箱里的 backend/chat_history.py，
函数内部 os.path.dirname(dirname(abspath(__file__))) 就会解析到沙箱根，从而只动沙箱文件。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from backend import chat_history  # noqa: E402


# 沙箱放系统临时目录：不污染项目，且环境的 safe-delete 会拦截删除，故不做清理
import tempfile  # noqa: E402

SANDBOX = tempfile.mkdtemp(prefix='rmtest_')
os.makedirs(os.path.join(SANDBOX, 'backend'))

CHAT_A = os.path.join(SANDBOX, '你与测试甲的对话')
CHAT_B = os.path.join(SANDBOX, '你与测试乙的对话')
os.makedirs(CHAT_A)
os.makedirs(CHAT_B)

FILE_A = os.path.join(CHAT_A, '2026-08-10_08.md')
FILE_B = os.path.join(CHAT_B, '2026-08-10_12.md')
FILE_C = os.path.join(CHAT_A, '2026-08-10_16.md')  # 不含目标 id，应保持字节不变

A = '''# 2026-08-10 08:00 - 11:59 对话记录

## 2026-08-10 09:29:14 - 测试甲


> 第1天 

[photo: {"id": 57}]

## 2026-08-10 09:40:00 - 测试甲
今天天气不错。

> 第1天 09:40:00

[photo: {"id": 58}]
'''

B = '''# 2026-08-10 12:00 - 15:59 对话记录

## 2026-08-10 13:00:00 - 测试乙
你看这张。

[effects: {"mood": 1}]
> 第2天 13:00:00

<details>
<summary>推理过程</summary>

想让他看看。

</details>

[photo: {"id": 57}]
'''

C = '''# 2026-08-10 16:00 - 19:59 对话记录

## 2026-08-10 17:00:00 - 测试甲
无关内容。

> 第3天 17:00:00
'''

for p, s in ((FILE_A, A), (FILE_B, B), (FILE_C, C)):
    with open(p, 'w', encoding='utf-8') as f:
        f.write(s)

c_before = open(FILE_C, encoding='utf-8').read()

# 关键：把模块 __file__ 指向沙箱
chat_history.__file__ = os.path.join(SANDBOX, 'backend', 'chat_history.py')

n = chat_history.remove_photo_marker(57)
print('remove_photo_marker(57) 返回被修改文件数 =', n)

ra = open(FILE_A, encoding='utf-8').read()
rb = open(FILE_B, encoding='utf-8').read()
rc = open(FILE_C, encoding='utf-8').read()

FAIL = 0


def check(t, cond, extra=''):
    global FAIL
    if cond:
        print('  [PASS]', t)
    else:
        FAIL += 1
        print('  [FAIL]', t)
        if extra:
            print('         ' + extra.replace('\n', '\n         '))


check('返回值为 2（甲、乙各一个文件）', n == 2)
check('甲：57 标记已删', '"id": 57' not in ra, ra)
check('甲：57 那轮整块删除', '09:29:14' not in ra, ra)
check('甲：58 标记未受影响', '"id": 58' in ra, ra)
check('甲：58 那轮正文保留', '今天天气不错' in ra, ra)
check('乙：57 标记已删', '"id": 57' not in rb, rb)
check('乙：正文保留', '你看这张' in rb, rb)
check('乙：推理块保留', '<details>' in rb, rb)
check('乙：块头保留', '13:00:00 - 测试乙' in rb, rb)
check('无关文件字节未变', rc == c_before)

# 幂等：再删一次应返回 0
n2 = chat_history.remove_photo_marker(57)
check('幂等：二次调用返回 0', n2 == 0, 'got %r' % n2)

print()
print('---- 甲 文件最终内容 ----')
print(ra)
print('---- 乙 文件最终内容 ----')
print(rb)

print('沙箱目录（临时盘，未清理）:', SANDBOX)
print('FAILED:', FAIL)
sys.exit(1 if FAIL else 0)
