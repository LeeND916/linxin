# -*- coding: utf-8 -*-
"""appearance 外貌文本解析器（零依赖，纯文本进出）

刻意不 import backend / flask / sqlalchemy —— 这样校验脚本可以脱离 Flask 上下文
直接跑，也方便单测。所有函数只吃字符串、吐字符串或 dict。

要解决的三个真实问题（均来自 game.db 实测）：

1. **眉眼段是合并段**：库里没有独立的「眉毛」「眼睛」段，两者挤在「眉眼：」一行。
   7/8 位女主能按 `；` 干净切开，晓月不能（眉、眼混在同一分号句里、用句号分隔）。
   → `split_brow_eye()` 三级降级拆分。

2. **眼镜混在眉眼段里**：晓月的「眉眼」首句是「深棕细框方形圆边眼镜」。
   它既不是眉也不是眼，而是**常驻配饰**——做眼睛实验时如果跟着眼句一起被摘除，
   晓月就会摘掉眼镜，那已经不是同一个人，对照实验直接失效。
   → 单独提取为 `eyewear`，任何实验中都作为锁定段原样保留。

3. **脸型段可能缺失或重复**：苏晴、顾云溪无「脸型与轮廓」段；晓月该段整句重复两遍。
   脸型已升级为变量维度，缺基线 = 对照底板不受控。
   → `detect_anomalies()` 体检时报出来。
"""

import re

# ─────────────────────────── 分段规格 ───────────────────────────
# canonical 为库中标准标题；aliases 用于容错；sniff 为无标题时的关键词嗅探。

SEGMENT_SPEC = {
    'face':   {'canonical': '脸型与轮廓', 'aliases': ['脸型', '轮廓', '面部轮廓', '脸廓', '脸'],
               'sniff': ['鹅蛋', '瓜子', '下颌', '颧骨', '圆脸', '方脸', '心形脸'],
               'variable': True},
    'brow':   {'canonical': '眉眼', 'aliases': ['眉毛', '眉形', '五官'],
               'sniff': ['眉'], 'variable': True, 'split': 'brow'},
    'eye':    {'canonical': '眉眼', 'aliases': ['眼睛', '眼型'],
               'sniff': ['眼'], 'variable': True, 'split': 'eye'},
    'nose':   {'canonical': '鼻子', 'aliases': ['鼻型', '鼻'],
               'sniff': ['鼻'], 'variable': True},
    'mouth':  {'canonical': '嘴唇', 'aliases': ['唇形', '嘴型', '嘴'],
               'sniff': ['唇'], 'variable': True},
    'skin':   {'canonical': '肤色与妆容', 'aliases': ['肤色', '妆容', '皮肤'],
               'sniff': ['肤色', '妆容'], 'variable': False},
    'hair':   {'canonical': '发型', 'aliases': ['头发', '发式', '发色'],
               'sniff': ['发型', '长发', '短发', '马尾'],
               'variable': True, 'strip_always': True},
    'eyewear': {'canonical': '', 'aliases': [], 'sniff': [],
                'variable': False, 'derived': True},   # 从眉眼段派生，锁定不可变
}

# 变量维度的展示顺序（前端 DIMS 与之对齐）
VARIABLE_SEGMENTS = ['face', 'eye', 'brow', 'nose', 'mouth', 'hair']

# ─────────────────────────── 关键词权重表 ───────────────────────────
# 眼镜必须最先剥离：「眼镜」二字含「眼」，不先摘走会被误判成眼睛描述。
_EYEWEAR_KW = ('眼镜', '镜框', '镜片', '框镜', '平光镜', '细框', '半框', '无框')

_BROW_KW = ('眉毛', '眉形', '眉峰', '眉尾', '眉头', '平眉', '弯眉', '剑眉',
            '柳叶眉', '远山眉', '野生眉', '细眉', '粗眉', '毛流', '眉')

_EYE_KW = ('眼睛', '眼型', '杏眼', '丹凤眼', '桃花眼', '狐狸眼', '猫眼', '小狗眼',
           '眼裂', '眼尾', '眼头', '眼白', '双眼皮', '内双', '外双', '卧蚕',
           '瞳', '睫毛', '眼神', '目光', '眼波', '眼')

_LINE_SEP = re.compile(r'[\r\n]+')
_CLAUSE_SEP = re.compile(r'[；;]')
_SENTENCE_SEP = re.compile(r'[。!！?？]')

# 额外的已知标题（不构成变量维度，但必须能被识别，否则会吃掉后面的段）
_EXTRA_TITLES = ('身材', '气质', '身高', '体型', '眼镜', '配饰', '眉毛', '眼睛')


def _build_title_pattern():
    """收集全部已知标题，构造「标题：」匹配式（长标题优先，避免「脸」抢在「脸型与轮廓」前）"""
    titles = set(_EXTRA_TITLES)
    for spec in SEGMENT_SPEC.values():
        if spec.get('canonical'):
            titles.add(spec['canonical'])
        titles.update(spec.get('aliases', []))
    ordered = sorted(titles, key=len, reverse=True)
    return re.compile('(' + '|'.join(re.escape(t) for t in ordered) + r')[：:]')


_TITLE_RE = _build_title_pattern()


def _score(text: str, keywords) -> int:
    """关键词命中计分：命中越多、越具体，得分越高"""
    return sum(1 for k in keywords if k in text)


def _is_eyewear(clause: str) -> bool:
    """是否为眼镜等常驻配饰描述"""
    if not any(k in clause for k in _EYEWEAR_KW):
        return False
    # 「细框」「无框」等词过泛，必须同时出现「镜」字才算眼镜
    if not any(k in clause for k in ('眼镜', '镜框', '镜片', '框镜', '平光镜')):
        return '镜' in clause
    return True


# ─────────────────────────── 分段切分 ───────────────────────────

def parse_segments(appearance: str) -> dict:
    """把 appearance 切成 {标题: 正文}，保留原始行序

    返回 {'脸型与轮廓': '...', '眉眼': '...', '_preamble': '...', ...}

    关键：支持**行内嵌标题**。库里实测存在这种脏数据 ——

        苏晴   带金丝圆框眼镜，脸型与轮廓：长脸偏鹅蛋型……
        顾云溪 东亚女性，脸型与轮廓：方圆脸下颌分明……

    标题前粘了额外前缀。若按「第一个冒号前若不超过 8 字才算标题」的朴素规则，
    整行会被判成无标题，导致误判为「缺脸型段」。这里改为扫描行内所有已知标题的
    位置来切段，标题前的残留归入 `_preamble`（常驻身份/配饰描述，全程锁定）。
    """
    out = {}
    preambles = []

    def _put(label, body):
        body = body.strip()
        if not body:
            return
        if label in out:
            out[label] += '\n' + body       # 同名标题追加，不覆盖
        else:
            out[label] = body

    for line in _LINE_SEP.split(appearance or ''):
        line = line.strip()
        if not line:
            continue

        matches = list(_TITLE_RE.finditer(line))
        if not matches:
            _put('', line)                  # 完全无标题，留给关键词嗅探
            continue

        head = line[:matches[0].start()].strip().strip('，,、；;')
        if head:
            preambles.append(head)

        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(line)
            _put(m.group(1), line[m.end():end])

    if preambles:
        out['_preamble'] = '；'.join(preambles)
    return out


def locate_segment(segments: dict, key: str) -> tuple:
    """三级降级定位一个分段 → (正文, 命中方式)

    命中方式：exact 精确标题 / alias 别名 / sniff 关键词嗅探 / missing 缺失
    """
    spec = SEGMENT_SPEC.get(key, {})
    canonical = spec.get('canonical', '')

    if canonical and canonical in segments:
        return segments[canonical], 'exact'

    for alias in spec.get('aliases', []):
        for label, body in segments.items():
            if label and not label.startswith('_') and alias in label:
                return body, 'alias'

    for kw in spec.get('sniff', []):
        for label, body in segments.items():
            if label.startswith('_'):       # _preamble 等内部键不参与嗅探
                continue
            if kw in body:
                return body, 'sniff'

    return '', 'missing'


def split_preamble(preamble: str) -> dict:
    """把标题前残留的前缀拆成 眼镜 / 其它身份描述

    苏晴「带金丝圆框眼镜」→ eyewear；顾云溪「东亚女性」→ identity。
    两者都是全程锁定项，任何维度实验中都原样保留。
    """
    glasses, identity = [], []
    for part in [p.strip() for p in re.split(r'[；;，,]', preamble or '') if p.strip()]:
        (glasses if _is_eyewear(part) else identity).append(part)
    return {'eyewear': '；'.join(glasses), 'identity': '；'.join(identity)}


# ─────────────────────────── 眉眼拆分（核心） ───────────────────────────

def split_brow_eye(text: str) -> dict:
    """把「眉眼」合并段拆成 眉 / 眼 / 眼镜 三部分

    返回 {
        'brow': str, 'eye': str, 'eyewear': str,
        'level': 1|2|3,        # 拆分所用的降级层级
        'unsplittable': bool,  # True 表示眉眼无法分离，两维都不可做变量
        'trace': [...]         # 每个小句的归属，供体检表展示
    }

    降级链：
      level 1 — 按 `；` 切分即可分离（覆盖 7/8 位女主）
      level 2 — 存在眉眼混写的分号句，按 `。` 再切一层（晓月走这条）
      level 3 — 切到句子级仍混写 → unsplittable，该角色眉、眼两维灰显
    """
    result = {'brow': '', 'eye': '', 'eyewear': '',
              'level': 1, 'unsplittable': False, 'trace': []}
    if not (text or '').strip():
        result['unsplittable'] = True
        return result

    brow_parts, eye_parts, glass_parts = [], [], []
    level = 1
    mixed_leftover = []

    for clause in [c.strip() for c in _CLAUSE_SEP.split(text) if c.strip()]:
        # 第一步：剥离眼镜（必须最先，「眼镜」含「眼」会污染眼句判定）
        if _is_eyewear(clause):
            glass_parts.append(clause.strip('。'))
            result['trace'].append((clause, 'eyewear', 1))
            continue

        b, e = _score(clause, _BROW_KW), _score(clause, _EYE_KW)

        if b and not e:
            brow_parts.append(clause.strip('。'))
            result['trace'].append((clause, 'brow', 1))
        elif e and not b:
            eye_parts.append(clause.strip('。'))
            result['trace'].append((clause, 'eye', 1))
        elif b and e:
            # level 2：眉眼混写，按句号再切一层
            level = max(level, 2)
            for sent in [s.strip() for s in _SENTENCE_SEP.split(clause) if s.strip()]:
                if _is_eyewear(sent):
                    glass_parts.append(sent)
                    result['trace'].append((sent, 'eyewear', 2))
                    continue
                sb, se = _score(sent, _BROW_KW), _score(sent, _EYE_KW)
                if sb > se:
                    brow_parts.append(sent)
                    result['trace'].append((sent, 'brow', 2))
                elif se > sb:
                    eye_parts.append(sent)
                    result['trace'].append((sent, 'eye', 2))
                else:
                    # level 3：句子级仍分不开
                    level = 3
                    mixed_leftover.append(sent)
                    result['trace'].append((sent, 'mixed', 3))
        else:
            # 既不含眉也不含眼（例如纯气质描述）→ 归眼侧，宁可多带不可丢
            eye_parts.append(clause.strip('。'))
            result['trace'].append((clause, 'eye?', 1))

    result['level'] = level
    result['brow'] = '；'.join(brow_parts)
    result['eye'] = '；'.join(eye_parts)
    result['eyewear'] = '；'.join(glass_parts)

    # 混写残留：附加到两侧，并标记不可拆（该角色眉/眼维度需人工规范化）
    if mixed_leftover:
        tail = '；'.join(mixed_leftover)
        result['brow'] = (result['brow'] + '；' + tail).strip('；')
        result['eye'] = (result['eye'] + '；' + tail).strip('；')
        result['unsplittable'] = True

    # 只有一侧有内容也算不可用（无法做该维度的对照）
    if not result['brow'] or not result['eye']:
        result['unsplittable'] = True

    return result


# ─────────────────────────── 统一入口 ───────────────────────────

def analyze(appearance: str) -> dict:
    """一次性解析全部分段，返回每维的 {text, source, ok}

    source: exact / alias / sniff / split / missing
    """
    segments = parse_segments(appearance)
    out = {'_segments': segments, '_raw': (appearance or '').strip()}

    brow_eye_text, be_src = locate_segment(segments, 'brow')
    split = split_brow_eye(brow_eye_text) if brow_eye_text else None

    for key in ('face', 'nose', 'mouth', 'skin', 'hair'):
        text, src = locate_segment(segments, key)
        out[key] = {'text': text, 'source': src, 'ok': bool(text)}

    if split:
        out['brow'] = {'text': split['brow'], 'source': f'split-L{split["level"]}',
                       'ok': bool(split['brow']) and not split['unsplittable']}
        out['eye'] = {'text': split['eye'], 'source': f'split-L{split["level"]}',
                      'ok': bool(split['eye']) and not split['unsplittable']}
        out['_split'] = split
    else:
        for key in ('brow', 'eye'):
            out[key] = {'text': '', 'source': 'missing', 'ok': False}
        out['_split'] = None

    # 常驻锁定项：眼镜可能出现在眉眼段内（晓月）或标题前缀里（苏晴），合并两处
    pre = split_preamble(segments.get('_preamble', ''))
    glasses = [t for t in ((split or {}).get('eyewear', ''), pre['eyewear']) if t]
    out['eyewear'] = {'text': '；'.join(glasses), 'source': 'derived',
                      'ok': bool(glasses)}
    out['identity'] = {'text': pre['identity'], 'source': 'preamble',
                       'ok': bool(pre['identity'])}

    return out


# ─────────────────────────── 提示词底稿组装 ───────────────────────────
# 段序固定，不随内容长短改变。变量段在**原位**被替换，绝不追加到末尾 ——
# 段位一动，后面所有 token 位置全变，同 seed 下的对照就不成立了。

APPEARANCE_ORDER = [
    ('identity', ''),           # 东亚女性等身份前缀，无标题直接前置
    ('face', '脸型'),
    ('eyewear', ''),            # 眼镜：常驻配饰，任何实验都锁定
    ('brow', '眉毛'),
    ('eye', '眼睛'),
    ('nose', '鼻子'),
    ('mouth', '嘴唇'),
    ('skin', '肤色'),
]


def build_appearance(appearance: str, variable_key: str = None,
                     variable_text: str = None, drop_hair: bool = True) -> dict:
    """组装进提示词的外貌底稿

    variable_key  要替换的分段（face/eye/brow/nose/mouth），None 表示纯基线
    variable_text 替换进去的候选描述
    drop_hair     无条件摘除发型段（发型由独立的组件维度负责，避免双重描述）

    返回 {'text': 组装结果, 'deviation': 偏离基线的段数, 'used': [...], 'fallback': [...]}

    `deviation` 是单变量纪律的硬校验值：**必须恒等于 1**（纯基线时为 0）。
    大于 1 说明提示词里同时动了多段，该任务应被拒绝入队。
    """
    info = analyze(appearance)
    parts, used, fallback = [], [], []
    deviation = 0

    for key, title in APPEARANCE_ORDER:
        seg = info.get(key) or {}
        # 运行时即去重：晓月的脸型段整串重复两遍，不去重会在提示词里被加权
        text = dedup_text(seg.get('text') or '').strip()

        if key == variable_key:
            if variable_text:
                text = variable_text.strip()
                deviation += 1
                used.append(key)
            # 变量段没给值 → 保持基线，不留空（空段会让模型自由发挥）
        elif not text:
            if key in ('identity', 'eyewear'):
                continue                    # 这两段本就多数角色没有，属正常
            fallback.append(key)            # 其余缺段需要回落，调用方应告警
            continue

        if not text:
            continue
        parts.append(f'{title}：{text}' if title else text)

    if not drop_hair and info['hair']['ok']:
        parts.append(f'发型：{info["hair"]["text"]}')

    return {'text': '，'.join(p.rstrip('。') for p in parts),
            'deviation': deviation, 'used': used, 'fallback': fallback,
            'locked': {'eyewear': info['eyewear']['text'],
                       'identity': info['identity']['text']}}


def assert_single_variable(built: dict):
    """单变量纪律硬断言：提示词相对基线只允许偏离一段

    在任务入队前调用。这条纪律一旦破了，整批实验的归因就失效了，
    所以宁可拒绝入队也不能放行。
    """
    if built['deviation'] > 1:
        raise ValueError(
            f'违反单变量纪律：本次提示词偏离基线 {built["deviation"]} 段'
            f'（{built["used"]}），只允许 1 段')
    return True


# ─────────────────────────── 体检 ───────────────────────────

def detect_anomalies(appearance: str) -> list:
    """返回该角色 appearance 的异常清单 [(等级, 维度, 说明)]

    等级：block 阻断实验 / warn 建议修复 / info 仅提示
    """
    issues = []
    info = analyze(appearance)

    if not (appearance or '').strip():
        return [('block', '*', 'appearance 为空，无法参与任何实验')]

    # 1. 脸型缺失 —— 脸型已是变量维度，缺基线则其余维度实验的底板不受控
    if not info['face']['ok']:
        issues.append(('block', 'face',
                       '缺「脸型与轮廓」段。脸型现为变量维度，缺基线会让其余维度实验的底板漂移'))

    # 2. 眉眼不可拆
    split = info.get('_split')
    if split is None:
        issues.append(('block', 'brow/eye', '缺「眉眼」段，眉、眼两维不可用'))
    elif split['unsplittable']:
        issues.append(('block', 'brow/eye', '眉眼混写且句子级仍无法分离，需人工规范化'))
    elif split['level'] >= 2:
        issues.append(('warn', 'brow/eye',
                       f'眉眼需降级到 L{split["level"]} 才拆开，建议规范化为「眉…；眼…」'))

    # 3. 发型段：发型已作为外貌正式组成部分写入 appearance（数据回填 / 新角色六段式生成），
    #    存在即正常，无需告警；仅当 appearance 非空却缺发型段时才提醒（发型现为变量维度，
    #    缺基线会让发型实验的底板受控度下降）。
    if not info['hair']['ok']:
        issues.append(('warn', 'hair',
                       '缺「发型」段。发型现为变量维度，建议补充角色固有发型描述，'
                       '否则发型实验基线不受控'))

    # 4. 重复文本 —— 同一描述出现两次会在提示词里被加权
    for key in ('face', 'nose', 'mouth', 'skin'):
        text = info[key]['text']
        if not text or len(text) < 12:
            continue
        half = len(text) // 2
        head = text[:half].strip('，。；')
        if head and len(head) >= 8 and text.count(head) > 1:
            issues.append(('warn', key, f'段内文本重复：「{head[:20]}…」出现多次，会在提示词里被加权'))

    # 5. 缺失的其余段
    for key, label in (('nose', '鼻子'), ('mouth', '嘴唇'), ('skin', '肤色与妆容')):
        if not info[key]['ok']:
            lvl = 'block' if key in ('nose', 'mouth') else 'warn'
            issues.append((lvl, key, f'缺「{label}」段'))

    # 6. 常驻锁定项：眼镜 / 身份前缀
    if info['eyewear']['ok']:
        issues.append(('info', 'eyewear',
                       f'检测到常驻配饰「{info["eyewear"]["text"]}」，全程锁定不参与变量'))
    if info['identity']['ok']:
        issues.append(('info', 'identity',
                       f'检测到标题前缀「{info["identity"]["text"]}」，已提为锁定段'))

    return issues


def dedup_text(text: str) -> str:
    """去掉段内重复内容，两级去重

    晓月的脸型段是「流畅窄鹅蛋小脸，下颌线条柔和纤细，无生硬棱角」整串**用逗号**
    重复了两遍，只切分号句号是抓不住的，必须下沉到逗号级。
    """
    out_parts, seen = [], set()
    for part in [p.strip() for p in re.split(r'[；;。]', text or '') if p.strip()]:
        subs, sub_seen = [], set()
        for s in [x.strip() for x in re.split(r'[，,]', part) if x.strip()]:
            if s not in sub_seen:
                sub_seen.add(s)
                subs.append(s)
        merged = '，'.join(subs)
        if merged and merged not in seen:
            seen.add(merged)
            out_parts.append(merged)
    return '；'.join(out_parts)


# 规范化后的输出顺序（未知段追加在末尾，不丢数据）
_NORMALIZED_ORDER = [
    ('identity', '身份特征'), ('face', '脸型与轮廓'), ('eyewear', '眼镜'),
    ('brow', '眉毛'), ('eye', '眼睛'), ('nose', '鼻子'),
    ('mouth', '嘴唇'), ('skin', '肤色与妆容'),
]
_CONSUMED_TITLES = {'脸型与轮廓', '眉眼', '鼻子', '嘴唇', '肤色与妆容',
                    '眉毛', '眼睛', '眼镜', '_preamble', ''}


def normalize_appearance(appearance: str) -> str:
    """把 appearance 重组为规范的一段一行格式

    做四件事：拆开眉眼、把眼镜/身份前缀提成独立段、段内去重、未知段原样保留。
    仅供 scripts/check_appearance.py --apply 使用；运行时解析不需要改库。
    不可拆的角色原样返回，绝不产出错误数据。
    """
    info = analyze(appearance)
    split = info.get('_split')
    if split is None or split['unsplittable']:
        return appearance

    lines = []
    for key, title in _NORMALIZED_ORDER:
        d = info.get(key) or {}
        if not d.get('ok'):
            continue
        body = dedup_text(d['text'])
        if body:
            lines.append(f'{title}：{body}。')

    for label, body in info['_segments'].items():   # 身材/气质等未知段不丢
        if label in _CONSUMED_TITLES or label.startswith('_'):
            continue
        lines.append(f'{label}：{body}')

    return '\n'.join(lines)


# 旧名保留，避免外部引用断裂
normalize_brow_eye = normalize_appearance
