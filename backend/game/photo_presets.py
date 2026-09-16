# -*- coding: utf-8 -*-
"""
聊天生图 / 肖像生成 共用的中文写实风提示词构件（单一事实来源）。

所有构件全中文、写实风，供两条生图链路复用：
  - 聊天生图（media_intent → 七场景 L3 模板）
  - 角色肖像默认生成（comfyui_client._build_character_prompt 现已改用本模块）

设计原则（见《聊天生图方案.md》第 4 章）：
  - L1 身份锚 STYLE_REALISTIC 锁死真实摄影写实，不开放选择
  - L2 状态层由角色属性（mood / 当前穿搭 / 所在地）实时拼装
  - 光线层按游戏时间自动映射（7 段）
  - 负面词含「反动漫」整组，压制 Z-Image 往二次元漂

命名约定（与方案保持一致，避免同义不同名）：
  - *_REALISTIC / *_CN 后缀：本模块导出的常量
  - build_identity_anchor / build_state_layer / lighting_for_game_time：复用名
  - assemble_portrait_prompt(char, scene_compose, extra_neg)：对外统一入口
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── L1 身份锚：写实风固定串（方案 4.3）──
STYLE_REALISTIC = (
    '真实摄影写实风格,(专业相机拍摄的真人照片),'
    '皮肤真实纹理与毛孔细节,自然肤色过渡与血色感,发丝根根分明,'
    '真实物理光照与阴影衰减,浅景深虚化背景,'
    '35mm定焦镜头质感,高动态范围,超高清画质,细节丰富'
)


# ── 中文负面提示词（方案 4.6，所有场景共用）──
NEGATIVE_REALISTIC = (
    '动漫风格,二次元,插画,漫画,美式卡通,3D渲染,CG建模感,游戏截图,'
    '油画笔触,水彩晕染,绘画感,手绘线稿,平涂色块,'
    '塑料质感皮肤,过度磨皮,假面感,蜡像感,'
    '畸形的手,多余的手指,缺失的手指,手指粘连,肢体扭曲,多余的肢体,'
    '面部扭曲,五官错位,眼睛不对称,双瞳,重影,'
    '三人以上,多余人物,人物融合,重复人物,双脸,镜像分身,'
    '低分辨率,模糊,失焦,严重噪点,过度曝光,死黑,偏色,'
    '水印,签名,文字,logo,边框'
)

# ── 构图/景别负面（portrait_lab 专用，压制大头照/半身像/残体）──
# 不放权重：负向词是「禁止清单」，加权重反而不稳定；纯逗号分隔的禁止项最稳。
# 补强：增加 close-up/portrait/headshot/cropped 等英文残体词，以及脚部/腿部缺失、身体被裁切。
NEGATIVE_FRAMING = (
    '大头照,头部特写,半身像,胸像,只有头部,截断,缺手臂,缺腿,'
    '特写,close-up,portrait,headshot,cropped,'
    'missing legs,missing feet,big head,oversized head,'
    '残体,脚部缺失,腿部缺失,身体被裁切'
)


# ── L 光线层（方案 4.7，按游戏时间 7 段）──
# 注：L2 情绪段已移除——聊天生图改由 emotion_engine.get_composite_label 统一注入多维复合情绪标签。
# (起始小时, 结束小时, 光线片段)；22:00-05:00 为跨日兜底，单独处理。
_LIGHTING_TABLE = [
    (5,  8,  '清晨柔光,金色日出光晕,冷调蓝紫色阴影,空气通透'),
    (8,  11, '上午明亮自然光,清澈日光,光线均匀,阴影边缘清晰'),
    (11, 14, '正午强光,高对比度,短促锐利的阴影,画面明亮'),
    (14, 17, '午后暖光,斜射阳光,拉长的柔和阴影,暖黄色调'),
    (17, 19, '黄金时刻,夕阳暖光,橙红色轮廓边缘光,逆光氛围'),
    (19, 22, '夜晚室内灯光,暖色灯泡光源,温馨氛围,光线柔和'),
]


def lighting_for_game_time(game_time) -> str:
    """按游戏时间字符串（'HH:MM' 或 'HH'）返回中文光线片段。

    22:00-次日 05:00 归为深夜段。无法解析时回退正午段。
    """
    hour = None
    try:
        if isinstance(game_time, str) and game_time:
            hour = int(game_time.split(':')[0])
        elif isinstance(game_time, (int, float)):
            hour = int(game_time)
    except (ValueError, AttributeError):
        hour = None

    if hour is None:
        return _LIGHTING_TABLE[2][2]  # 正午兜底
    if hour >= 22 or hour < 5:
        return '深夜,昏暗环境光,窗外透进的月光,低调暗部,冷色调'
    for start, end, text in _LIGHTING_TABLE:
        if start <= hour < end:
            return text
    return _LIGHTING_TABLE[2][2]


# ── 穿搭可见项抽取（与 comfyui_client 原逻辑一致；顺序：上衣→下装→外套→鞋子→配饰→发型）──
_OUTFIT_SLOTS = ['top', 'bottom', 'outer', 'shoes', 'accessory', 'hairstyle']


def _visible_items(outfit) -> list:
    """从 get_current_outfit() 返回的 dict 中抽取可见服饰名列表。"""
    if not outfit or not isinstance(outfit, dict):
        return []
    comps = outfit.get('components', {})
    names = []
    if outfit.get('replacement_mode') == 'underwear_full_replace':
        underwear = comps.get('underwear')
        if isinstance(underwear, dict) and underwear.get('name'):
            names.append(underwear['name'])
        return names
    if outfit.get('replacement_mode') == 'custom_full_replace':
        custom = comps.get('custom')
        if isinstance(custom, dict) and custom.get('description'):
            return [custom['description']]
    for slot in _OUTFIT_SLOTS:
        item = comps.get(slot)
        if not item:
            continue
        if isinstance(item, list):
            for sub in item:
                if isinstance(sub, dict) and sub.get('name'):
                    names.append(sub['name'])
        elif isinstance(item, dict) and item.get('name'):
            names.append(item['name'])
    return names


def _age_to_desc_cn(age, gender: str = '') -> str:
    """数字年龄 → 中文年龄段标签（配合「单人入镜」，带性别词）。

    返回空串表示未设置年龄。
    """
    if not age:
        return ''
    gender_cn = '东亚女性' if gender == 'female' else ('东亚男性' if gender == 'male' else '')
    if age <= 17:
        return f'十来岁的{gender_cn}'
    if age <= 22:
        return f'二十岁出头的年轻{gender_cn}'
    if age <= 27:
        return f'二十多岁的{gender_cn}'
    if age <= 32:
        return f'三十岁出头的{gender_cn}'
    if age <= 39:
        return f'三十多岁的{gender_cn}'
    return f'成熟{gender_cn}'


# ── L1 + L2 拼装（方案 4.3 / 4.4）──

def build_identity_anchor(char, solo: bool = True, scene_summary: str = None) -> str:
    """L1 身份锚：写实风 + [场景定格概括] + [单人] + 外貌 + 年龄 + 身份。

    solo: 是否注入「单人入镜」约束（'#' 占位符，由上层解析）。
          自拍/拍张照/换装展示为 True；场景定格等其它场景为 False。
    scene_summary: 场景定格的 Qwen 概括片段，置于写实风之后、外貌之前。
    """
    parts = [STYLE_REALISTIC]
    if scene_summary:
        parts.append(scene_summary)
    if solo:
        parts.append('#')  # 替代英文 solo
    _appearance = getattr(char, 'appearance', '') or '清秀的年轻东亚女性'
    # 外貌段包权重 (...:1.3)：在 CFG 1.2 下提升脸型/五官/肤色等细节的引导强度，
    # 缓解 z-image 把不同角色收敛成同一张脸、自拍互相撞脸的问题。
    if _appearance:
        _appearance = f'({_appearance}:1.3)'
    parts += [
        _appearance,
        _age_to_desc_cn(getattr(char, 'age', None), getattr(char, 'gender', '') or ''),
        getattr(char, 'identity_label', '') or '',
    ]
    if getattr(char, 'name', '') == '苏晴':
        parts.append('苏晴必须佩戴金属细框眼镜，左腕必须佩戴银色机械表，配饰清晰可见')
    return ','.join([p for p in parts if p])


def build_state_layer(char, outfit_override=None, location_only=False) -> str:
    """L2 状态层：当前穿搭 + 所在地；可用快照覆盖角色表状态。

    location_only=True 时只输出地点、不写穿搭（换装左右分栏场景由 L3 完全控制两侧穿搭）。
    """
    location = getattr(char, 'location', '') or '室内'
    if location_only:
        return f'背景是{location}的真实环境'
    if isinstance(outfit_override, str):
        outfit_desc = outfit_override.strip() or '日常休闲装'
        return f'身着{outfit_desc},背景是{location}的真实环境'
    outfit = outfit_override if outfit_override is not None else _safe_get_outfit(char)
    outfit = outfit or {}
    mode = outfit.get('replacement_mode')
    if mode == 'custom_full_replace':
        custom = (outfit.get('components') or {}).get('custom') or {}
        outfit_desc = custom.get('description') or outfit.get('name') or '自定义穿搭'
    elif mode == 'underwear_full_replace':
        outfit_desc = '、'.join(_visible_items(outfit)) or outfit.get('name') or '角色专属内衣'
    else:
        outfit_desc = '、'.join(_visible_items(outfit)) or '日常休闲装'
    location = getattr(char, 'location', '') or '室内'
    replace_guard = ''
    if mode in ('custom_full_replace', 'underwear_full_replace'):
        replace_guard = '，当前服装为完整替换结果，不叠加普通上衣、下装、外套或裙装'
    return f'身着{outfit_desc}{replace_guard},背景是{location}的真实环境'


def _safe_get_outfit(char):
    try:
        return char.get_current_outfit()
    except Exception:
        return {}


def assemble_portrait_prompt(char, scene_compose: str = None, extra_neg: str = None,
                             solo: bool = True, scene_summary: str = None,
                             outfit_override=None, location_only: bool = False):
    """统一入口：拼出 (正向提示词, 负面提示词)。

    参数:
        char: 角色对象（需有 appearance/age/gender/identity_label/mood/location）
        scene_compose: 场景 L3 模板文本（聊天生图传入；默认肖像留空）
        extra_neg: 额外负面词（可空）
        solo: 是否注入单人入镜约束（聊天生图按场景类型决定；默认 True 保持肖像生成单人为真）
        scene_summary: 场景定格 Qwen 概括片段，置于 L1 写实风之后、外貌之前
    返回:
        (positive: str, negative: str)
    """
    anchor = build_identity_anchor(char, solo=solo, scene_summary=scene_summary)
    state = build_state_layer(char, outfit_override=outfit_override, location_only=location_only)
    # 角色当前时间取自 game_hour/game_minute 整数（Character 模型无 game_time 列），
    # 与 photo_gen._build_sky_desc 共用同一真实来源，避免"正午 vs 凌晨"式时间感冲突。
    _h = int(getattr(char, 'game_hour', 12) or 12)
    _m = int(getattr(char, 'game_minute', 0) or 0)
    lighting = lighting_for_game_time(f"{_h:02d}:{_m:02d}")

    positive_parts = [anchor, state]
    if scene_compose:
        positive_parts.append(scene_compose)
    positive_parts.append(lighting)
    positive = ','.join([p for p in positive_parts if p])

    negative = NEGATIVE_REALISTIC
    if extra_neg:
        negative = f'{negative},{extra_neg}'
    return positive, negative


# ── 写实风词库（后端可选画风/光影等数据源；方案 ①）
# 用途边界（重要）：本 dict 经 /api/photo/vocab 下发，供前端「自定义肖像提示词」面板加载。
#          【画风定调 style】前端已于 2026-08-12 改为使用内置 18 种混合画风（含动漫/彩绘/赛博朋克），
#          不再用本 dict 的写实向 style 覆盖；本条 style 数据当前仅作保留/将来 /api/portrait/presets 备用。
#          【其余分类 lighting 等】前端仍用本 dict 覆盖内置词库。
#          聊天拍照链路【不读】此 dict：其画风由文件头 L1 身份锚 STYLE_REALISTIC 锁死（不开放选择），
#          与本条词库天然隔离，互不干扰。两者已通过"后端锁死常量 vs 前端可选下拉"分层解耦。
# 仅保留写实向条目，供未来 /api/portrait/presets 下发，逐步替代前端 hardcode 的动漫词库。
# 扩充约定：新增画风只改下方 style.core.options（或并列新增 subGroup），切勿改动整体结构、也勿挪作聊天拍照用途，
#          以免误污染聊天拍照的锁死写实画风。
REALISTIC_VOCAB = {
    'style': {
        'label': '1 · 画风定调（写实向）',
        'subGroups': [
            {
                'key': 'core', 'label': '核心风格',
                'options': [
                    {'value': '真实摄影写实', 'prompt': STYLE_REALISTIC},
                    {'value': '古典油画写实', 'prompt': '古典油画写实风格,(伦勃朗式明暗法),学院派严谨造型,细腻堆叠的笔触质感,温润沉稳的暗部处理,厚重颜料表现,古典大师级光影'},
                    {'value': '电影级概念艺术', 'prompt': '电影级概念艺术,(影视级光影渲染),叙事向画面,精细环境与人物刻画'},
                    {'value': '新中式写实', 'prompt': '新中式写实风格,(东方审美构图),淡墨晕染,现代国风,克制雅致的色彩搭配,真实摄影质感'},
                ],
            },
        ],
        'default': {'core': '真实摄影写实'},
    },
    'lighting': {
        'label': '8 · 光影与色调（写实向）',
        'subGroups': [
            {
                'key': 'source', 'label': '光源类型',
                'options': [
                    {'value': '柔和自然光', 'prompt': '柔和自然光,(来自窗户的柔和日光),光线均匀,无刺眼强光'},
                    {'value': '侧逆光', 'prompt': '侧逆光,(光源来自人物侧后方),勾勒人物轮廓金边,面部再做补光'},
                    {'value': '黄昏暖光', 'prompt': '黄昏暖光,(傍晚金色阳光,暖色温光线),氛围感强烈,暖金色漫射'},
                    {'value': '屏幕微光', 'prompt': '屏幕微光,(电脑屏幕散发微弱冷光),光线淡淡映照面部,夜间办公氛围'},
                ],
            },
        ],
        'default': {'source': '柔和自然光'},
    },
}


def get_realistic_vocab() -> dict:
    """返回写实向词库（供前端 /api/portrait/presets 下发）。"""
    return REALISTIC_VOCAB
