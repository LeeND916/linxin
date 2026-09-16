# -*- coding: utf-8 -*-
"""单变量 / 多变量肖像提示词构建

核心纪律（来自《肖像实验室详细方案》）：
  - 单变量实验：相对角色原生外貌**只允许偏离一个段**（deviation 必须 == 1），
    否则多个维度同时变化，归因失效。外观五维（脸型/眼/眉/鼻/嘴）走
    appearance_parser.build_appearance 做**原位替换**并硬断言；
    发型/内衣/服饰不在 appearance 文本段内，作为追加段处理（同样记 1 偏离）。
  - 定妆间组合：故意同时变多个维度（最多 3），用来验证协调度，故不走单变量断言。

所有函数只吃 Character 对象 + 候选描述文本，吐出最终正向提示词字符串。

关于「默认发型」：角色发型只存在于 character.current_outfit.components.hairstyle 的
JSON 快照里，appearance 字段本身不含发型段（苏晴/林小鹿实测均如此）。为让任何维度的
基线都呈现角色「默认发型」而非模型随机长发，这里把当前发型注回 appearance 文本
（_appearance_with_hair），并对 build_appearance 传 drop_hair=False 保留该段。
发型是常驻段，单变量断言只数「被替换的段」，故不破坏 deviation==1 纪律。
"""

import json

from .appearance_parser import analyze, build_appearance, assert_single_variable

# 维度 key → appearance_parser 分段 key（供 build_appearance 定位原位替换段）
SEG = {
    'face_shape': 'face',
    'eyes': 'eye',
    'eyebrows': 'brow',
    'nose': 'nose',
    'mouth': 'mouth',
    'hairstyle': 'hair',
}

# 追加段维度：不在 appearance 文本段内，用「标题：描述」形式追加到基线后
APPEND_TITLE = {
    'hairstyle': '发型',
    'underwear': '内衣',
    'outfit': '服饰',
}

# 固定写实风格尾缀：所有变体共用，不引入额外偏离（偏离计数恒为 0）
STYLE_TAIL = '，写实摄影风格，自然光，高清晰度，8k，细节丰富'

# 构图/景别指令：强约束让模型出全身（放在最前面，CLIP 对靠前 token 注意力更高）
# 写实场景（专业摄影棚/纯色背景）+ 强姿态 + 双脚/地板细节，引导全身构图；
# 全身约束段用 (...:1.5) 加权，对抗大段脸部描述把注意力拉走（实测本地 Qwen/Z-Image
# 工作流对单层括号权重有效；若某工作流出现括号解析异常，移除 :1.5 仅保留文字约束即可）。
FRAMING_PREFIX = {
    'underwear': '专业摄影棚全身照，纯色背景，镜头平视，人物居中直立，正面面对镜头，脊柱自然挺直，肩膀放松放平，头颈躯干保持垂直，不弯腰、不含胸、不塌腰、不扭胯、不侧身。双脚赤脚站在摄影棚地板上。(从头到脚完整入镜，头发、肩膀、腰部、腿部、脚踝和双脚清晰可见，画面不裁切人物，正常人体比例，头部大小适中，避免大头照、半身照、胸像和特写:1.5)。专业摄影棚柔光从侧面打光，柔和阴影。',
    'outfit':     '专业摄影棚全身照，纯色背景，镜头平视，人物居中直立，正面面对镜头，脊柱自然挺直，肩膀放松放平，头颈躯干保持垂直，不弯腰、不含胸、不塌腰、不扭胯、不侧身。双脚赤脚站在摄影棚地板上。(从头到脚完整入镜，头发、肩膀、腰部、腿部、脚踝和双脚清晰可见，画面不裁切人物，正常人体比例，头部大小适中，避免大头照、半身照、胸像和特写:1.5)。专业摄影棚柔光从侧面打光，柔和阴影。',
    'shoes':     '专业摄影棚全身照，纯色背景，镜头平视，人物居中直立，正面面对镜头，脊柱自然挺直，肩膀放松放平，头颈躯干保持垂直，不弯腰、不含胸、不塌腰、不扭胯、不侧身。双脚赤脚站在摄影棚地板上。(从头到脚完整入镜，头发、肩膀、腰部、腿部、脚踝和双脚清晰可见，画面不裁切人物，正常人体比例，头部大小适中，避免大头照、半身照、胸像和特写:1.5)。专业摄影棚柔光从侧面打光，柔和阴影。',
    'bottom':    '专业摄影棚全身照，纯色背景，镜头平视，人物居中直立，正面面对镜头，脊柱自然挺直，肩膀放松放平，头颈躯干保持垂直，不弯腰、不含胸、不塌腰、不扭胯、不侧身。双脚赤脚站在摄影棚地板上。(从头到脚完整入镜，头发、肩膀、腰部、腿部、脚踝和双脚清晰可见，画面不裁切人物，正常人体比例，头部大小适中，避免大头照、半身照、胸像和特写:1.5)。专业摄影棚柔光从侧面打光，柔和阴影。',
    'top':       '专业摄影棚全身照，纯色背景，镜头平视，人物居中直立，正面面对镜头，脊柱自然挺直，肩膀放松放平，头颈躯干保持垂直，不弯腰、不含胸、不塌腰、不扭胯、不侧身。双脚赤脚站在摄影棚地板上。(从头到脚完整入镜，头发、肩膀、腰部、腿部、脚踝和双脚清晰可见，画面不裁切人物，正常人体比例，头部大小适中，避免大头照、半身照、胸像和特写:1.5)。专业摄影棚柔光从侧面打光，柔和阴影。',
}
# 兼容旧名：脚本若引用 FRAMING 仍可用
FRAMING = FRAMING_PREFIX


def extract_hairstyle_description(character) -> str:
    """读取角色默认发型描述。

    优先从 character.appearance 的「发型」段读取（发型已作为外貌正式组成写入，
    由数据回填脚本写入，新角色也由 PROFILE_PROMPT 六段式生成）。这样发型是角色
    固有属性，不随换装漂移，且单变量实验时发型段可被原位替换。

    回退：若 appearance 无发型段（旧角色 / 未回填），则从 current_outfit.components
    .hairstyle 的 JSON 快照读取（优先 description，缺失回退 name）。
    """
    ap = (character.appearance or '').strip()
    if ap:
        try:
            info = analyze(ap)
            hair = info.get('hair') or {}
            if hair.get('ok') and (hair.get('text') or '').strip():
                return hair['text'].strip()
        except Exception:
            pass

    co = getattr(character, 'current_outfit', None)
    if not co:
        return ''
    try:
        obj = json.loads(co) if isinstance(co, str) else co
    except Exception:
        return ''
    if not isinstance(obj, dict):
        return ''
    comps = obj.get('components') or {}
    hair = comps.get('hairstyle')
    if not hair:
        return ''
    if isinstance(hair, list):
        hair = hair[0] if hair else None
    if not isinstance(hair, dict):
        return ''
    desc = (hair.get('description') or '').strip()
    if desc:
        return desc
    # 快照里的 description 可能为空（快照早于「补写发型描述」动作），
    # 用快照自带的组件 id 反查 outfit_components 拿完整描述，更贴合补写的数据。
    cid = hair.get('id')
    if cid:
        try:
            from backend.models import OutfitComponent
            row = OutfitComponent.query.get(cid)
            if row and (row.description or '').strip():
                return row.description.strip()
        except Exception:
            pass
    return (hair.get('name') or '').strip()


def _gender_anchor(character) -> str:
    """按角色性别返回人种锚点（东亚女性 / 东亚男性）。

    用于约束 SD/ComfyUI 生图人种，避免女主等角色照片偏欧美脸；
    空性别返回空串，不改写提示词。
    """
    g = (getattr(character, 'gender', '') or '').lower()
    if g == 'female':
        return '东亚女性'
    if g == 'male':
        return '东亚男性'
    return ''


def _with_gender_anchor(character, text: str) -> str:
    """把人种锚点前置到提示词；若文本已含该锚点则不重复添加。"""
    anchor = _gender_anchor(character)
    if not anchor or anchor in text:
        return text
    return f'{anchor}，{text}'


def _with_personality(character, text: str) -> str:
    """把角色性格类型前置到提示词，让出图带上气质底色（与换装系统同源）。

    字段为空时不加；文本已含该性格词时跳过，避免 build_zimage_portrait_prompt /
    build_qwen_portrait_prompt 二次注入导致重复。
    """
    ptype = (getattr(character, 'personality_type', '') or '').strip()
    if not ptype or ptype in text:
        return text
    return f'{ptype}气质，{text}'


def _with_identity_label(character, text: str) -> str:
    """把角色职业身份（identity_label）前置到提示词，让出图带上身份底色。

    字段为空时不加；文本已含该身份词时跳过，避免重复注入。
    与 _with_personality 同源，仅作用于 portrait_lab 拼装层。
    """
    label = (getattr(character, 'identity_label', '') or '').strip()
    if not label or label in text:
        return text
    return f'{label}，{text}'


def _appearance_with_hair(character) -> str:
    """把角色当前发型注入 appearance 文本，使所有维度的基线都带「默认发型」。

    appearance 字段本身不含发型段（实测苏晴/林小鹿均如此），这里补一段
    「发型：<当前发型描述>」。已含发型词则跳过，避免重复。
    """
    ap = (character.appearance or '').strip()
    if not ap:
        return ap
    if '发型' in ap or '头发' in ap:
        return ap
    hair = extract_hairstyle_description(character)
    if not hair:
        return ap
    return f'{ap}，发型：{hair}'


def build_baseline_prompt(character) -> str:
    """角色原生外貌底稿（零变量偏离），含默认发型"""
    ap = _appearance_with_hair(character)
    text = build_appearance(ap, drop_hair=False)['text']
    return _with_personality(character, _with_identity_label(character, _with_gender_anchor(character, text) + STYLE_TAIL))


def build_variant_prompt(character, dim: str, desc: str):
    """构建单变量变体提示词

    dim  : 维度 key（face_shape/eyes/eyebrows/nose/mouth/hairstyle/underwear/outfit）
    desc : 该候选的核心描述文本（由 components 解析得到）

    返回拼接好的正向提示词；desc 为空或 '' 时返回 None（该候选跳过）。
    """
    desc = (desc or '').strip()
    if not desc:
        return None

    ap = _appearance_with_hair(character)

    if dim in SEG:
        built = build_appearance(ap, variable_key=SEG[dim], variable_text=desc,
                                 drop_hair=False)
        assert_single_variable(built)            # 硬断言：只允许偏离 1 段
        text = built['text']
    else:
        title = APPEND_TITLE.get(dim, dim)
        text = build_appearance(ap, drop_hair=False)['text']
        text = (text + f'，{title}：{desc}').strip('，')

    # 服装/鞋类维度：把全身约束放在最前面，避免被风格尾缀稀释
    text = _with_gender_anchor(character, text)
    framing = FRAMING_PREFIX.get(dim)
    if framing:
        text = (framing + '，' + text).strip('，')
    text = _with_personality(character, _with_identity_label(character, text))
    return text + STYLE_TAIL


def build_combo_prompt(character, selections: dict):
    """构建定妆间组合提示词（多变量，故意偏离多段，不做单变量断言）

    selections: {维度key: 候选描述文本, ...}（1~3 个维度）
    串行替换：每次对上一步结果再原位替换一个段，发型/服饰类走追加。
    """
    ap = _appearance_with_hair(character)
    text = ap
    full_body_dims = []
    for dim, desc in selections.items():
        desc = (desc or '').strip()
        if not desc:
            continue
        if dim in SEG:
            # 链式：build_appearance 会重新解析上一步产物并原位替换（drop_hair=False 保留发型）
            text = build_appearance(text, variable_key=SEG[dim], variable_text=desc,
                                    drop_hair=False)['text']
        else:
            title = APPEND_TITLE.get(dim, dim)
            text = (text + f'，{title}：{desc}').strip('，')
        if dim in FRAMING_PREFIX:
            full_body_dims.append(dim)
    # 服装/鞋类维度：全身约束前置（组合时可能多个，去重后只保留一次全身前缀）
    text = _with_gender_anchor(character, text)
    if full_body_dims:
        text = (FRAMING_PREFIX[full_body_dims[0]] + '，' + text).strip('，')
    text = _with_personality(character, _with_identity_label(character, text))
    return text + STYLE_TAIL
