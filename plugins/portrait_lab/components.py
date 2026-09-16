# -*- coding: utf-8 -*-
"""六维度候选读取 + 外貌文本拆解（对 game.db 只读）

六个可变维度（与 outfit_components.type 对应）：
    eyes 眼睛 / eyebrows 眉毛 / mouth 嘴型 / hairstyle 发型 /
    underwear 内衣 / outfit 服饰（走 outfit_presets 成套穿搭）

候选来源：
    - 共享池：outfit_components.character_id = 0（所有女主通用，用户后续新增的行落这里）
    - 专属池：outfit_components.character_id = 该女主 id
    - 服饰：outfit_presets.character_id = 该女主 id（成套预设）

type 命名兼容若干别名，防止手工插入数据时写法不一致导致读不到。
"""

import re

from backend.models import OutfitComponent, OutfitPreset

# 维度 → 中文名 + 可接受的 outfit_components.type 别名（第一个为推荐写法）
DIMENSIONS = {
    'face_shape': {'label': '脸型', 'types': ['face_shape', 'face', '脸型', '脸型与轮廓', '轮廓']},
    'eyes':      {'label': '眼睛', 'types': ['eyes', 'eye', '眼睛', '眼型']},
    'eyebrows':  {'label': '眉毛', 'types': ['eyebrows', 'eyebrow', 'brow', 'brows', '眉毛', '眉型']},
    'nose':      {'label': '鼻子', 'types': ['nose', '鼻子', '鼻型']},
    'mouth':     {'label': '嘴型', 'types': ['mouth', 'lips', 'lip', '嘴型', '嘴唇', '唇型']},
    'hairstyle': {'label': '发型', 'types': ['hairstyle', 'hair', '发型']},
    'underwear': {'label': '内衣', 'types': ['underwear', '内衣']},
    'outfit':    {'label': '服饰', 'types': []},  # 特殊：走 outfit_presets
}

# 展示顺序按「视觉影响力降序」，也是建议的逐维收敛顺序
DIMENSION_ORDER = ['hairstyle', 'face_shape', 'eyes', 'eyebrows',
                   'nose', 'mouth', 'underwear', 'outfit']

# 候选来源：segment = 从 appearance 分段原位替换；component = outfit_components；preset = outfit_presets
DIMENSION_SOURCE = {
    'face_shape': 'segment', 'eyes': 'segment', 'eyebrows': 'segment',
    'nose': 'segment', 'mouth': 'segment',
    'hairstyle': 'component', 'underwear': 'component', 'outfit': 'preset',
}

# 维度 key → appearance_parser 的分段 key（供原位替换时定位要摘除的段）
DIMENSION_SEGMENT = {
    'face_shape': 'face', 'eyes': 'eye', 'eyebrows': 'brow',
    'nose': 'nose', 'mouth': 'mouth', 'hairstyle': 'hair',
}


# ─────────────────────────── 外貌文本拆解 ───────────────────────────
# appearance 列形如：
#   脸型与轮廓：长脸偏鹅蛋型……
#   眉眼：眉毛是细长平眉，颜色偏深灰黑；眼睛是桃花眼，卧蚕浅淡不抢戏。
#   鼻子：……
#   嘴唇：嘴唇偏薄，嘴型是清晰的线形唇……
#   肤色与妆容：……
# 变量实验时需要把"被替换的那一句"从原生外貌里摘掉，否则会和新值互相打架。

# 解析逻辑全部委托 appearance_parser（零依赖模块），这里只做维度 key 的转译。
# 刻意不在本文件重复实现一套切分规则 —— 两套逻辑必然漂移。
try:
    from .appearance_parser import analyze, build_appearance, detect_anomalies
except ImportError:                                   # 脚本方式直接运行时的回退
    from appearance_parser import analyze, build_appearance, detect_anomalies


def extract_feature(appearance: str, dim: str) -> str:
    """从 appearance 里抽出该维度的原生描述（用作"角色原生"候选项）"""
    seg = DIMENSION_SEGMENT.get(dim)
    if not seg:
        return ''
    return (analyze(appearance).get(seg) or {}).get('text', '')


def strip_feature(appearance: str, dim: str) -> str:
    """返回"摘掉该维度描述"后的外貌底稿，供变量覆盖时拼接使用"""
    seg = DIMENSION_SEGMENT.get(dim)
    return build_appearance(appearance, variable_key=seg, variable_text=None)['text']


def compose_feature(appearance: str, dim: str, candidate_text: str) -> dict:
    """把候选值原位替换进外貌底稿，返回含单变量校验信息的完整结果"""
    seg = DIMENSION_SEGMENT.get(dim)
    return build_appearance(appearance, variable_key=seg, variable_text=candidate_text)


def appearance_bundle(appearance: str) -> dict:
    """一次性返回各维度原生描述、摘除后底稿、锁定项与体检结论"""
    info = analyze(appearance)
    native, stripped = {}, {}
    for dim, seg in DIMENSION_SEGMENT.items():
        if seg == 'hair':                              # 发型走组件表，不从外貌取
            continue
        native[dim] = (info.get(seg) or {}).get('text', '')
        stripped[dim] = build_appearance(appearance, variable_key=seg)['text']
    return {
        'full': (appearance or '').strip(),
        'native': native,
        'stripped': stripped,
        'baseline': build_appearance(appearance)['text'],
        'locked': {'eyewear': info['eyewear']['text'],
                   'identity': info['identity']['text']},
        'issues': [{'level': l, 'dim': d, 'msg': m}
                   for l, d, m in detect_anomalies(appearance)],
    }


# ─────────────────────────── 候选读取 ───────────────────────────

def _component_rows(dim: str, character_id: int):
    """按维度别名读取 outfit_components：共享池(character_id=0) + 角色专属"""
    types = DIMENSIONS[dim]['types']
    if not types:
        return []
    rows = OutfitComponent.query.filter(
        OutfitComponent.type.in_(types),
        OutfitComponent.character_id.in_([0, character_id]),
    ).order_by(
        OutfitComponent.character_id,
        OutfitComponent.sort_order,
        OutfitComponent.name,
    ).all()
    return rows


def _preset_to_candidate(preset) -> dict:
    """把成套穿搭预设展开成候选项

    优先用 preset.description（已是成文描述，直接可进提示词）；
    只有 description 为空时才回退去展开组件（复用 wardrobe._assemble_components）。
    """
    desc = (preset.description or '').strip()
    detail = ''
    if not desc:
        from backend.game.wardrobe import _assemble_components
        parts = []
        try:
            comps = _assemble_components(preset)
        except Exception:
            comps = {}
        for slot in ('outer', 'top', 'bottom', 'shoes', 'accessory'):
            item = comps.get(slot)
            if not item:
                continue
            if isinstance(item, list):
                parts.extend([i.get('name', '') for i in item if i.get('name')])
            elif item.get('name'):
                parts.append(item['name'])
        detail = '、'.join([p for p in parts if p])
    return {
        'value': f'preset:{preset.id}',
        'name': preset.name or f'预设{preset.id}',
        'description': desc or detail,
        'detail': detail,
        'shared': False,
        'season': preset.season or '',
        'occasion': preset.occasion or '',
    }


def list_candidates(dim: str, character) -> list:
    """返回某维度的全部候选项 [{value,name,description,shared,...}]"""
    if dim == 'outfit':
        presets = OutfitPreset.query.filter_by(character_id=character.id).order_by(
            OutfitPreset.occasion, OutfitPreset.season, OutfitPreset.name
        ).all()
        return [_preset_to_candidate(p) for p in presets]

    out, seen = [], set()
    for row in _component_rows(dim, character.id):
        key = (row.name or '').strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({
            'value': f'comp:{row.id}',
            'name': key,
            # description 为空（如内衣组件只有 name 无描述）时用候选名兜底，
            # 否则 prompt_builder 会把该候选跳过，导致「没有可生成的候选」
            'description': (row.description or '').strip() or key,
            'detail': (row.style_tags or '').strip(),
            'shared': row.character_id == 0,
            'subtype': row.subtype or '',
        })
    return out


def current_outfit_slot(character, slot: str) -> dict:
    """从角色 current_outfit 取出某个槽位（hairstyle / underwear）的当前值"""
    try:
        comps = (character.get_current_outfit() or {}).get('components', {}) or {}
    except Exception:
        return {}
    item = comps.get(slot)
    if isinstance(item, list):
        item = item[0] if item else None
    if isinstance(item, dict) and item.get('name'):
        return {'name': item.get('name', ''), 'description': item.get('description', '')}
    return {}


def build_dimension_payload(character) -> dict:
    """组装该角色六个维度的 {候选列表 + 当前默认值}，供前端渲染"""
    appearance = appearance_bundle(character.appearance or '')
    payload = {}

    for dim in DIMENSION_ORDER:
        cands = list_candidates(dim, character)
        default_name, default_desc = '', ''

        if dim in ('eyes', 'eyebrows', 'mouth'):
            default_desc = appearance['native'].get(dim, '')
            default_name = '角色原生'
        elif dim in ('hairstyle', 'underwear'):
            cur = current_outfit_slot(character, dim)
            default_name = cur.get('name', '')
            default_desc = cur.get('description', '')
        elif dim == 'outfit':
            try:
                co = character.get_current_outfit() or {}
            except Exception:
                co = {}
            default_name = co.get('name', '') or ''
            default_desc = co.get('description', '') or ''

        payload[dim] = {
            'label': DIMENSIONS[dim]['label'],
            'candidates': cands,
            'default': {'name': default_name, 'description': default_desc},
            'accept_types': DIMENSIONS[dim]['types'],
        }

    payload['_appearance'] = appearance
    return payload
