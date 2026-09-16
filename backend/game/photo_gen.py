"""聊天生图编排器（P3/P4/P5/P7 后端核心）。

职责：
  - 拼装中文写实提示词（L1+L2+光线 来自 photo_presets，L3 来自 prompt_registry 的 photo.* 模板）
  - 异步调用 ComfyUI 生图（复用 build_zimage_portrait_prompt + get_comfyui_client）
  - LLM 压缩（photo.scene_extract）产出场景描述 + 女主第一人称记忆句（A 主干）
  - VLM 多模态回看（photo.vlm_caption，P5）做 A/B 合流
  - 记忆两写：CharacterMemory（带 embedding）+ PhotoRecord（图片实体），不写 EventLog
  - 全局单一工作流：resolve_workflow 解析 ComfyUIWorkflow 默认行

本模块所有函数都可在请求线程外（后台线程）安全运行，依赖 app.app_context 注入。
"""
import os
import re
import json
import base64
import logging
import threading
import random
from datetime import datetime
from urllib.error import HTTPError

logger = logging.getLogger('backend.game.photo_gen')

# ──────────────────────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────────────────────
SCENE_TYPE_CN = {
    'photo_take': '拍照', 'selfie': '自拍', 'activity_shot': '活动特写',
    'outfit_change': '换装展示', 'scene_freeze': '场景定格',
    'gift_photo': '礼物合影', 'share_new': '女主主动展示', 'share_recall': '旧照回忆',
}

SHOT_WHITELIST = {'面部特写', '半身像', '七分身', '全身像', '中近景'}
ANGLE_WHITELIST = {'平视视角', '微仰视视角', '微俯视视角', '过肩视角'}
BANNED_TOKENS = {'头发', '发色', '瞳色', '眼睛颜色', '身材', '胸', '年龄', '岁',
                 '穿着', '裙子', '衬衫', '制服', '杰作', '最高画质', '8K',
                 '写实风格', '动漫', '插画'}

# 自拍（selfie）可随机注入的拍摄角度池——每次自拍随机挑一个，让出图角度不单调
SELFIE_ANGLE_POOL = [
    '俯拍视角,手机举高向下拍,显脸小',
    '平拍视角,手机与脸平齐,自然亲切',
    '微仰视视角,手机稍低向上拍,显下颌线',
    '侧前方45度俯拍,凸显侧脸轮廓',
    '回眸视角,侧身回头看向镜头',
    '略低头俯拍,眼神抬看镜头,俏皮',
]

# ── 场景定格·硬编码天色描述（按游戏小时直接调用，不依赖 LLM）──
# 每个区间 [start, end) 对应一句描述天色/光线氛围的中文短句，供拼进场景定格提示词。
_SCENE_FREEZE_SKY_MAP = [
    (5, 7,   "天色将亮未亮，东边泛起鱼肚白，薄雾还笼着街区"),
    (7, 9,   "清晨淡蓝的天光，斜射的晨光把窗格投在地面"),
    (9, 11,  "上午明澈的蓝天，阳光明亮而不刺眼"),
    (11, 13, "正午白晃晃的天光，影子缩成脚下一小团"),
    (13, 15, "午后偏暖的蓝天，阳光开始染上琥珀色"),
    (15, 17, "傍晚前暖橘色的天光，云被染成蜜色"),
    (17, 19, "黄昏橙红晚霞铺满西天，轮廓光勾出暖边"),
    (19, 21, "入夜的靛蓝天色，远处霓虹与路灯次第亮起"),
    (21, 23, "深夜墨蓝的天，只剩零星窗灯与冷白路灯光"),
    (23, 24, "凌晨死寂的暗蓝天，城市沉入睡眠"),
    (0, 5,   "凌晨死寂的暗蓝天，城市沉入睡眠"),
]

def _scene_freeze_hour(character):
    """从角色 game_time('HH:MM:SS') 解析小时，解析失败回退 game_hour。"""
    gt = getattr(character, 'game_time', '') or ''
    try:
        return int(str(gt).split(':')[0])
    except Exception:
        return int(getattr(character, 'game_hour', 12) or 12)


def _build_sky_desc(character):
    """硬编码天色描述：按游戏小时返回一句天色/光线氛围。"""
    h = _scene_freeze_hour(character)
    for s, e, desc in _SCENE_FREEZE_SKY_MAP:
        if s <= h < e:
            return desc
    return '天光温和的日常时段'


def _clean_summary(text):
    """清理 Qwen 输出：去 ``` 包裹、去掉开头的【】标签/提示词标题行、剥掉残留冒号破折号、截断到 160 字。"""
    if not text:
        return ''
    t = text.strip()
    if t.startswith('```'):
        t = re.sub(r'^```[a-zA-Z]*\n?', '', t)
        t = re.sub(r'\n?```$', '', t)
    # 去开头的【...】标签
    t = re.sub(r'^【[^】]{0,20}】', '', t)
    # 去标题行（如 "场景定格提示词：" / "提示词："）
    t = re.sub(r'^[^，,。.\n]{0,14}提示词[：:：\s]*', '', t)
    # 去掉开头残留的冒号/破折号/空白
    t = re.sub(r'^[：:：\-–—\s]+', '', t)
    t = t.strip()
    if len(t) > 160:
        t = t[:160]
    return t


def _call_photo_llm_text(prompt_text, max_tokens=200, temperature=0.7, character_name=''):
    """调活跃 LLM 跑自由文本生成（远程兜底），返回纯文本或 None。"""
    try:
        from backend.game.llm_utils import safe_llm_post, get_active_llm_config
        cfg = get_active_llm_config()
        if not cfg:
            return None
        messages = [{'role': 'user', 'content': prompt_text}]
        result = safe_llm_post(
            api_url=cfg['api_url'], api_key=cfg.get('api_key', ''),
            model=cfg['model_name'], messages=messages,
            temperature=temperature, max_tokens=max_tokens,
            timeout=(30, 120), call_type='photo_scene_freeze_narrative',
            character_name=character_name,
            extra_body={"thinking": {"type": "disabled"}},
        )
        if not result:
            return None
        return (result.get('choices', [{}])[0].get('message', {}).get('content', '') or '').strip()
    except Exception as e:
        logger.warning(f"[PhotoGen] LLM 场景概括失败: {e}")
        return None


def build_scene_freeze_desc(character, user_message, recent_dialogue):
    """场景定格描述：复用双人叙事字段与硬规则天色。"""
    narrative = _scene_freeze_narrative(character, user_message, recent_dialogue)
    summary = narrative.get('叙事句', '')
    sky = _build_sky_desc(character)
    return '，'.join(p for p in (summary, sky) if p)

# L3 模板兜底（prompt_registry 不可用时用）
_L3_FALLBACK = {
    'photo_take':     '正面站立,目光直视镜头,自然微笑,轻微侧身显身材线条,{outfit},背景是{location}室内环境（镜头平视或微俯视不夸张,头部不前倾,头颈比例自然）',
    'selfie':         '手臂前伸握手机自拍,{angle},广角畸变,俏皮表情,{outfit},背景是{location}（镜头平视或微俯视不夸张,头部不前倾,头颈比例自然）',
    'activity_shot':  '{pose},{outfit},专注投入的神情,背景是{location}（镜头平视或微俯视不夸张,头部不前倾,头颈比例自然）',
    'outfit_change':  '一张真实摄影写实的照片,画面左右平分为两半,左半边是换装前,全身展示旧穿搭{outfit_old};右半边是换装后,全身展示新穿搭{outfit_new},左右两侧为同一人,脸型五官一致,全身像,姿态自然,背景是{location}的真实环境（镜头平视或微俯视不夸张,头部不前倾,头颈比例自然）',
    'scene_freeze':   '{scene_desc},富有故事感的瞬间定格,电影感构图,环境氛围突出,自然光影（镜头平视或微俯视不夸张,头部不前倾,头颈比例自然）',
    'gift_photo':     '双手捧着{gift_name}递向镜头,温柔笑意,惊喜神情,{outfit},背景是{location}（镜头平视或微俯视不夸张,头部不前倾,头颈比例自然）',
    'share_new':      '随手拍质感,生活化构图,自然不做作,{outfit},背景是{location}（镜头平视或微俯视不夸张,头部不前倾,头颈比例自然）',
}
_NEG_FALLBACK = '低质量,模糊,畸变,多余肢体,五官扭曲,过度磨皮,塑料感,油腻皮肤,反动漫,二次元,插画,美式卡通,3D渲染,CG建模感,水彩,厚涂,线条勾勒,非插图,非漫画分镜,非对比图表,非信息图,非海报,非带文字标签的图,非拼贴,低分辨率,噪点,过曝,死鱼眼,空洞凝视,头部前倾,颈部过长,头颈分离,头部从胸口突出,长颈鹿颈,脖子伸长,foreshorten neck,elongated neck,neck merged into body,distorted body proportions,head detached from torso'

# scene_type → prompt_registry 已注册的 L3 模板键映射。
# media_intent 的 reply 侧 scene_type 是 share_new / share_recall，
# 但模板统一注册为 photo.l3_share_photo，此处做归一。
_L3_TEMPLATE_KEY = {
    'share_new': 'share_photo',
    'share_recall': 'share_photo',
}


# ══════════════════════════════════════════════════════════════
# 提示词拼装
# ══════════════════════════════════════════════════════════════
def _render_photo_template(prompt_id, fallback, **vars):
    """从 prompt_registry 取 photo.* 模板并安全 .format；失败回退 fallback。"""
    try:
        from backend.game.prompt_registry import get_prompt_manager
        pm = get_prompt_manager()
        text = pm.get(prompt_id)
        if text:
            return _safe_format(text, fallback, **vars)
    except Exception as e:
        logger.warning(f"[PhotoGen] 取模板 {prompt_id} 失败，用兜底: {e}")
    return _safe_format(fallback, fallback, **vars)


def _safe_format(template, fallback, **vars):
    try:
        return template.format(**vars)
    except Exception:
        try:
            return fallback.format(**vars)
        except Exception:
            return fallback


def _outfit_desc(character):
    """取当前穿搭的中文描述串（组件名拼接）。"""
    try:
        co = character.get_current_outfit() or {}
        comps = co.get('components', {}) or {}
        names = []
        if co.get('replacement_mode') == 'custom_full_replace':
            custom = comps.get('custom')
            if isinstance(custom, dict) and custom.get('description'):
                return custom['description']
        slots = ('underwear',) if co.get('replacement_mode') == 'underwear_full_replace' else (
            'top', 'bottom', 'outer', 'shoes', 'accessory', 'hairstyle'
        )
        for slot in slots:
            item = comps.get(slot)
            if not item:
                continue
            if isinstance(item, list):
                for sub in item:
                    if isinstance(sub, dict) and sub.get('name'):
                        names.append(sub['name'])
            elif isinstance(item, dict) and item.get('name'):
                names.append(item['name'])
        if names:
            return '、'.join(names)
    except Exception:
        pass
    return '日常休闲穿搭'


def _scene_environment_desc(character):
    """把地点转换为必须可见的环境锚点，避免提示词只写抽象地点名。"""
    location = (getattr(character, 'location', '') or '').strip()
    if '车' in location:
        return '车厢内部，前后排座椅、车门内饰、仪表台或中控台、车窗与安全带清晰可见'
    return f'{location or "室内"}内部环境清晰可见，包含具有地点辨识度的固定设施与物件'


def _current_action_name(character):
    """取当前活动名（用于活动特写姿态）。"""
    try:
        from backend.game.activity import get_activities_for_location
        acts = get_activities_for_location(character.location, character=character)
        if acts:
            return list(acts.values())[0].get('name', '') or ''
    except Exception:
        pass
    return ''


def _build_scene_freeze_prompt(character, scene_summary, scene_desc, outfit=''):
    """按场景定格专用七段格式生成最终正向提示词，并注入当前穿搭与配饰。"""
    name = getattr(character, 'name', '') or '女主'
    gender = '东亚女性' if getattr(character, 'gender', '') == 'female' else '东亚男性'
    identity = getattr(character, 'identity_label', '') or '当前身份'
    location = getattr(character, 'location', '') or '室内'
    outfit = outfit or _outfit_desc(character)
    player_name = getattr(character, 'player_nickname', '') or '玩家'
    player_identity = getattr(character, 'player_identity', '') or '玩家'
    _player_row, _player_gender = _get_player_appearance(character)
    player_gender = '东亚女性' if _player_gender == 'female' else '东亚男性'
    environment = _scene_environment_desc(character)
    narrative = scene_summary or ''
    def _field(label, default=''):
        labels = '叙事句|女主姿态|玩家姿态|动作道具|情绪氛围|场景环境'
        match = re.search(rf'{re.escape(label)}：(.*?)(?=，(?:{labels})：|$)', narrative)
        return (match.group(1).strip(' ，。') if match else default)

    hour = _scene_freeze_hour(character)
    period = '上午' if 6 <= hour < 12 else ('下午' if 12 <= hour < 18 else ('晚上' if 18 <= hour < 23 else '深夜'))
    player_role = f'{player_identity}{player_name}' if player_identity else player_name
    # LLM 已根据最新聊天生成具体姿态；最终发给 ComfyUI 的文本只保留结果，不描述生成过程。
    player_scene = _field('玩家姿态', '玩家自然参与眼前互动')
    heroine_scene = _field('女主姿态', '女主自然参与眼前互动')
    action = _field('动作道具', '两人自然交流，场景中保留相关日常物品')
    mood = _field('情绪氛围', '自然、克制的交流氛围')
    if '场景环境' in narrative:
        environment = _field('场景环境', environment)
    sections = [
        '场景与人物关系设定\n'
        f'双人叙事场景，{location}{period}，{player_gender}{player_role}与{name}共同入镜。',
        '角色A（主导/焦点）设定\n'
        f'{name}：{gender}，{identity}。'
        f'{getattr(character, "appearance", "") or "五官端正舒展，神态自然，气质知性"}'
        f'当前穿搭与配饰：{outfit or "日常休闲穿搭"}。',
        '核心动作与道具\n' + f'{heroine_scene}；{player_scene}；{action}',
        '情绪与氛围定调\n' + mood,
        '画风定调\n真实摄影写实风格，专业相机拍摄的真人照片，皮肤真实纹理与自然光影。',
        '光影与色调\n柔和自然光，低饱和度。',
        '环境与构图\n' + f'{environment}，电影感中景构图，聚焦人物神态表情，无多余杂物，人物面部五官刻画细腻。',
    ]
    return '\n\n'.join(sections)


def build_chat_photo_prompt(character, scene_type, gift=None, outfit_name=None,
                             scene_desc=None, scene_summary=None, current_action=None,
                             outfit_old_override=None, outfit_new_override=None,
                             reveal=False):
    """拼装聊天生图正向提示词（L1+L2+光线 基础 + L3 场景段）。

    返回 (positive, negative)。

    outfit_change 场景：
      - 基础状态层只保留地点（location_only），穿搭由 L3 左右分栏描述完全控制，
        避免「身着旧穿搭」与「左侧旧/右侧新」互相打架。
      - 左侧用换装前快照 outfit_old_override；右侧用换装后快照 outfit_new_override
        （内衣替换时右侧文案为「脱去外衣、仅身穿新内衣」）。
    """
    from backend.game.photo_presets import assemble_portrait_prompt
    from backend.game.emotion_engine import get_composite_label

    # 单人入镜约束：仅自拍/拍张照/换装展示需要；场景定格等其它场景不需要
    _SOLO_SCENES = ('selfie', 'photo_take', 'outfit_change')
    solo = scene_type in _SOLO_SCENES

    # 换装对比：基础状态层只保留地点，穿搭交给 L3 左右分栏描述，避免与分栏冲突。
    base_positive, negative_l2 = assemble_portrait_prompt(
        character, solo=solo, scene_summary=scene_summary,
        outfit_override=None,
        location_only=(scene_type == 'outfit_change'))

    # 所有拍照场景统一取当前女主的实时穿搭；其中 accessory 组件会随 outfit 一并保留。
    outfit = _outfit_desc(character)
    location = getattr(character, 'location', '') or '室内'
    gift_name = gift or '一份心意'

    if scene_type == 'outfit_change':
        # 左侧=换装前快照；右侧=换装后（内衣替换时由入口给出「露出内衣」文案）。
        outfit_old = outfit_old_override or outfit
        outfit_new = outfit_new_override or outfit
    else:
        outfit_old = outfit
        outfit_new = outfit_name or outfit
    act = current_action or _current_action_name(character)

    # 活动特写：先解析姿态
    pose = ''
    if scene_type == 'activity_shot' and act:
        from backend.game.activity_pose import pose_desc_for_action
        pose = pose_desc_for_action(act)

    tmpl_key = _L3_TEMPLATE_KEY.get(scene_type, scene_type)
    # 自拍随机角度：从角度池随机取一个注入 L3 模板（非 selfie 为空，模板无占位符即忽略）
    angle = random.choice(SELFIE_ANGLE_POOL) if scene_type == 'selfie' else ''
    l3 = _render_photo_template(
        f'photo.l3_{tmpl_key}', _L3_FALLBACK.get(scene_type, _L3_FALLBACK['photo_take']),
        outfit=outfit, location=location, gift_name=gift_name,
        outfit_old=outfit_old, outfit_new=outfit_new,
        pose=pose, scene_desc=scene_desc or '',
        angle=angle,
    )

    if scene_type == 'scene_freeze':
        positive = _build_scene_freeze_prompt(
            character, scene_summary, scene_desc, outfit=outfit)
    else:
        positive = f"{base_positive},{l3}"
    # 非场景定格场景：补入女主当前游戏时间（天色/光线氛围）描述，
    # 与场景定格 build_scene_freeze_desc 使用的 _build_sky_desc 同源，保持画面时间感一致。
    if scene_type != 'scene_freeze':
        time_desc = _build_sky_desc(character)
        if time_desc:
            positive = f"{positive},{time_desc}"
    # 所有拍照场景：补入女主当前复合情绪标签（多维，源自 emotion_engine.get_composite_label；
    # 中性态返回空则不追加，保持提示词纯净，与"天色"并列形成「…,天色…,情绪…」统一结构）
    emotion_label = get_composite_label(character)
    if emotion_label:
        positive = f"{positive},{emotion_label}"
    negative = _render_photo_template('photo.negative', _NEG_FALLBACK)
    return positive, negative


# ══════════════════════════════════════════════════════════════
# LLM 场景压缩（A 主干）
# ══════════════════════════════════════════════════════════════
def _call_photo_llm_json(prompt_text, max_tokens=300, temperature=0.3, character_name=''):
    """调活跃 LLM 跑结构化抽取，返回解析后的 dict 或 None。"""
    try:
        from backend.game.llm_utils import safe_llm_post, get_active_llm_config
        cfg = get_active_llm_config()
        if not cfg:
            return None
        messages = [{'role': 'user', 'content': prompt_text}]
        result = safe_llm_post(
            api_url=cfg['api_url'], api_key=cfg.get('api_key', ''),
            model=cfg['model_name'], messages=messages,
            temperature=temperature, max_tokens=max_tokens,
            timeout=(30, 120), call_type='photo_scene',
            character_name=character_name or cfg.get('character_name', ''),
            extra_body={"thinking": {"type": "disabled"}},
        )
        if not result:
            return None
        content = (result.get('choices', [{}])[0].get('message', {}).get('content', '') or '').strip()
        return _parse_json(content)
    except Exception as e:
        logger.warning(f"[PhotoGen] LLM 场景压缩失败: {e}")
        return None


def _parse_json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    # 去 ``` 包裹
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def run_scene_extract(character, scene_type, user_message, recent_dialogue=''):
    """跑 photo.scene_extract，返回校验后的 dict（含记忆句）。"""
    from backend.game.prompt_registry import get_prompt_manager
    pm = get_prompt_manager()
    prompt_tmpl = pm.get('photo.scene_extract') or ''
    ctx = {
        'char_name': character.name,
        'age': getattr(character, 'age', ''),
        'identity_label': getattr(character, 'identity_label', ''),
        'location': getattr(character, 'location', ''),
        'game_day': getattr(character, 'game_day', 0),
        'game_time': getattr(character, 'game_time', ''),
        'mood': int(getattr(character, 'mood', 60) or 60),
        'stress': int(getattr(character, 'stress', 30) or 30),
        'current_action': _current_action_name(character),
        'scene_type_cn': SCENE_TYPE_CN.get(scene_type, scene_type),
        'recent_dialogue': recent_dialogue[-800:] if recent_dialogue else '',
        'user_message': user_message or '',
    }
    try:
        prompt = prompt_tmpl.format(**ctx) if prompt_tmpl else ''
    except Exception:
        prompt = ''
    if not prompt:
        return _scene_extract_fallback(ctx)
    parsed = _call_photo_llm_json(prompt)
    if not parsed or not isinstance(parsed, dict):
        return _scene_extract_fallback(ctx)
    return _validate_scene_extract(parsed, ctx)


def _get_player_appearance(character):
    """按角色专用记录、玩家性别默认记录解析玩家外貌。"""
    try:
        from backend.models import Appearance
        # 玩家性别来源于 appearances，而不是 character 表；沈念等角色支持专用记录。
        row = Appearance.query.filter_by(character_id=character.id).first()
        if row and (row.player_gender or '').lower() in ('female', 'male'):
            return row, row.player_gender.lower()
        gender = (getattr(character, 'player_gender', '') or '').lower()
        if gender not in ('female', 'male'):
            gender = 'female' if '学姐' in (getattr(character, 'player_identity', '') or '') else 'male'
        row = Appearance.query.filter_by(character_id=-1 if gender == 'female' else 0).first()
        if row and (row.player_gender or '').lower() in ('female', 'male'):
            gender = row.player_gender.lower()
        return row, gender
    except Exception:
        return None, 'male'


def _player_prompt_desc(character):
    """构造明确标注为玩家的外貌与身份描述，避免与女主混淆。"""
    row, gender = _get_player_appearance(character)
    gender_cn = '东亚女性' if gender == 'female' else '东亚男性'
    name = getattr(character, 'player_nickname', '') or '玩家'
    identity = getattr(character, 'player_identity', '') or '玩家'
    if not row:
        return f'玩家：{gender_cn}，{name}，身份为{identity}'
    fields = [row.player_summary, row.player_face_shape, row.player_eyes,
              row.player_eyebrows, row.player_nose, row.player_mouth,
              row.player_hairstyle, row.player_skin]
    return f'玩家：{gender_cn}，姓名{name}，身份{identity}，' + '，'.join(x for x in fields if x)


def _recent_scene_dialogue(recent_dialogue, user_message=''):
    """提取最新6轮真实对话，排除照片描述和系统注入内容。"""
    excluded = ('照片描述', '照片画面', '场景描述', '场景定格提示词', 'photo_scene',
                'photo_scene_freeze', 'scene_freeze', '[系统：', '图片已生成', '生图')
    lines = []
    # 历史归一化旧数据可能把换行保存为字面量 "\\n"，先还原后再按轮过滤。
    dialogue_text = str(recent_dialogue or '').replace('\\n', '\n')
    for raw in dialogue_text.splitlines():
        line = raw.strip()
        if not line or any(token.lower() in line.lower() for token in excluded):
            continue
        if line.startswith('>') or line.startswith('【') and ('照片' in line or '场景' in line):
            continue
        lines.append(line)
    if user_message and user_message.strip() and not any(user_message.strip() == line for line in lines):
        lines.append(user_message.strip())
    return '\n'.join(lines[-6:])


def _scene_freeze_narrative(character, user_message, recent_dialogue):
    """一次调用活跃 LLM 生成场景定格双人叙事字段。"""
    from backend.game.prompt_registry import get_prompt_manager
    # 场景定格只能使用持久化的真实聊天历史，绝不直接采用本轮触发生图的文本。
    # 这样自拍、拍张照等照片请求本身不会反向污染下一张场景定格的叙事依据。
    dialogue_source = ''
    try:
        from backend.chat_history import load_all_sessions
        history = load_all_sessions(character_name=character.name)
        # 新版记录以 photo_context 标识整轮；排除该轮角色回复及其紧邻的玩家发言。
        # 旧版仅有照片卡片、没有整轮标记时无法可靠对应到哪一轮真实对话，不能误删无关剧情。
        excluded_indexes = set()
        for index, msg in enumerate(history):
            if msg.get('photo_context'):
                excluded_indexes.add(index)
                if index > 0 and not history[index - 1].get('photo'):
                    excluded_indexes.add(index - 1)
        dialogue_source = '\n'.join(
            f"{'玩家' if msg.get('speaker') == 'player' else character.name}: {msg.get('content', '')}"
            for index, msg in enumerate(history[-20:], start=max(0, len(history) - 20))
            if index not in excluded_indexes
            and (msg.get('content') or '').strip()
            and not msg.get('photo')
            and not msg.get('photo_context')
        )
    except Exception as e:
        logger.warning(f"[PhotoGen] 回读{character.name}最近聊天失败: {e}")
    recent_scene_dialogue = _recent_scene_dialogue(dialogue_source)
    row, player_gender = _get_player_appearance(character)
    player_gender_cn = '东亚女性' if player_gender == 'female' else '东亚男性'
    player_name = getattr(character, 'player_nickname', '') or '玩家'
    player_identity = getattr(character, 'player_identity', '') or '玩家'
    prompt = (get_prompt_manager().get('photo.scene_freeze_narrative') or '').format(
        char_name=character.name, char_gender='东亚女性' if getattr(character, 'gender', '') == 'female' else '东亚男性',
        identity_label=getattr(character, 'identity_label', '') or '角色', player_name=player_name,
        player_gender=player_gender_cn, player_identity=player_identity,
        location=getattr(character, 'location', '') or '室内', game_day=getattr(character, 'game_day', 0),
        game_time=f"{int(getattr(character, 'game_hour', 12) or 12):02d}:{int(getattr(character, 'game_minute', 0) or 0):02d}",
        relationship_status=getattr(character, 'relationship_status', '') or 'friends',
        recent_dialogue=recent_scene_dialogue)
    parsed = _call_photo_llm_json(prompt, max_tokens=240, temperature=0.45,
                                  character_name=character.name)
    if not isinstance(parsed, dict):
        return {'叙事句': '', '女主姿态': '女主自然面对玩家', '玩家姿态': '玩家与女主保持自然交流',
                '动作道具': '桌面摆放与对话相关的日常物品', '情绪氛围': '自然温和的交流氛围'}
    out = {}
    for key, default in [('叙事句', ''), ('女主姿态', '女主自然面对玩家'), ('玩家姿态', '玩家与女主保持自然交流'),
                         ('动作道具', '桌面摆放与对话相关的日常物品'), ('情绪氛围', '自然温和的交流氛围')]:
        value = _clean_scene_punctuation(str(parsed.get(key, '') or '').replace('\\n', '').strip())
        out[key] = value[:20] if key == '叙事句' else value[:40]
    return out


def _clean_scene_punctuation(text):
    """清理叙事字段中的句号逗号相邻组合，保留句号。"""
    text = str(text or '').replace('。，', '。').replace('，。', '。')
    text = re.sub(r'。,+', '。', text)
    return text


def _scene_extract_fallback(ctx):
    from backend.game.activity_pose import pose_desc_for_action
    act = ctx.get('current_action', '')
    pose = pose_desc_for_action(act) if act else '姿态自然放松,神情随性'
    memo = f"（第{ctx.get('game_day',0)}天{ctx.get('game_time','')}）在{ctx.get('location','')}，{ctx.get('scene_type_cn','拍照')}。"
    return {'姿态': pose, '表情': '自然神情', '景别': '半身像', '视角': '平视视角',
            '光线': '自然光影', '画面细节': '', '记忆句': memo}


def _validate_scene_extract(p, ctx):
    # 字段缺失用默认
    p.setdefault('姿态', '姿态自然放松,神情随性')
    p.setdefault('表情', '自然神情')
    p.setdefault('景别', '半身像')
    p.setdefault('视角', '平视视角')
    p.setdefault('光线', '自然光影')
    p.setdefault('画面细节', '')
    p.setdefault('记忆句', '')
    # 含禁用词 → 丢弃该字段
    for k in ('姿态', '表情', '光线', '画面细节', '记忆句'):
        v = p.get(k, '')
        if any(tok in v for tok in BANNED_TOKENS):
            p[k] = '' if k != '记忆句' else _scene_extract_fallback(ctx)['记忆句']
    # 白名单校验
    if p.get('景别') not in SHOT_WHITELIST:
        p['景别'] = '半身像'
    if p.get('视角') not in ANGLE_WHITELIST:
        p['视角'] = '平视视角'
    # 记忆句人称清洗
    memo = p.get('记忆句', '')
    memo = re.sub(r'(玩家|女主本名|她|对方)', lambda m: '你' if m.group(0) in ('玩家', '对方') else '我', memo)
    if not memo:
        memo = _scene_extract_fallback(ctx)['记忆句']
    p['记忆句'] = memo
    return p


# ══════════════════════════════════════════════════════════════
# VLM 多模态回看（B 增强，P5）
# ══════════════════════════════════════════════════════════════
def vlm_caption_image(image_path, context):
    """让视觉模型看图说话，返回女主第一人称中文描述；失败返回空串。"""
    try:
        from backend.models import VLMConfig
        from backend.game.prompt_registry import get_prompt_manager
        cfg = VLMConfig.query.filter_by(is_active=True).first()
        if not cfg:
            return ''
        tmpl = get_prompt_manager().get('photo.vlm_caption') or ''
        if not tmpl:
            return ''
        prompt_text = tmpl.format(**context)
        with open(image_path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        payload = {
            'model': cfg.model_name,
            'temperature': cfg.temperature,
            'max_tokens': cfg.max_tokens,
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': prompt_text},
                    {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}},
                ],
            }],
        }
        headers = {'Authorization': f'Bearer {cfg.api_key}'} if cfg.api_key else {}
        import requests
        r = requests.post(cfg.api_url, json=payload, headers=headers, timeout=cfg.timeout)
        r.raise_for_status()
        return r.json()['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.warning(f"[PhotoGen] VLM 回看失败，保留原 scene_memo: {e}")
        return ''


def cosine_similarity(a, b):
    try:
        import numpy as np
        a = np.frombuffer(a, dtype='float32') if isinstance(a, (bytes, bytearray)) else np.asarray(a, dtype='float32')
        b = np.frombuffer(b, dtype='float32') if isinstance(b, (bytes, bytearray)) else np.asarray(b, dtype='float32')
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(a, b) / (na * nb))
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════
# 记忆两写（P4）：CharacterMemory + PhotoRecord（不写 EventLog）
# ══════════════════════════════════════════════════════════════
def write_photo_memories(character, record, scene_memo, importance=45.0):
    """写入照片长期记忆；Embedding 失败时保留文本，等待 LM Studio 后台补偿。"""
    from backend.models import CharacterMemory, db
    from backend.game.memory import _compute_memory_embedding
    if not scene_memo:
        return False
    mem = CharacterMemory(
        character_name=character.name,
        memory_type='event',
        content=scene_memo,
        context=f"聊天生图 · {SCENE_TYPE_CN.get(record.scene_type, record.scene_type)}",
        importance=importance,
        emotional_weight=0.3,
        source_day=getattr(character, 'game_day', 0),
        source_time=getattr(character, 'game_time', ''),
        embedding=None,
    )
    db.session.add(mem)
    try:
        mem.embedding = _compute_memory_embedding(scene_memo)
    except Exception as e:
        logger.warning(f"[PhotoGen] embedding 暂不可用，照片记忆先落库待补偿: {e}")
    try:
        db.session.commit()
        return True
    except Exception as e:
        logger.warning(f"[PhotoGen] 写记忆失败: {e}")
        db.session.rollback()
        return False


# ══════════════════════════════════════════════════════════════
# 同轮注入文本构造（§6.4：女主当轮就能反应）
# ══════════════════════════════════════════════════════════════
def build_photo_injection(scene_type, scene_memo):
    """构造追加到 user_message 的系统注入文本，使女主当轮回复就能提到照片画面。

    按方案 §6.4：必须带「不要复述这段说明」，且生图失败也照常注入（文字先于图片）。
    share_new / share_recall 不在此注入（§6.4 注意点2）。
    """
    if not scene_memo:
        return ''
    _verb = {
        'photo_take': '玩家刚给你拍了一张照片',
        'selfie': '玩家刚让你自拍了一张',
        'outfit_change': '玩家刚让你换了一身衣服，并拍了下来',
        'gift_photo': '玩家刚送了你一份礼物，并拍下了这一刻',
        'scene_freeze': '玩家刚把这一刻定格成了照片',
        'activity_shot': '玩家刚给你拍了一张活动特写',
    }.get(scene_type, '玩家刚给你拍了一张照片')
    return (f"[系统：{_verb}。画面：{scene_memo}。"
            f"请自然地回应这次拍照，可以提到画面里的细节，不要复述这段说明。]")


# ══════════════════════════════════════════════════════════════
# 全局单一工作流（P7）：resolve_workflow
# ══════════════════════════════════════════════════════════════
def resolve_workflow():
    """返回 (ComfyUIWorkflow 行 或 None, node_map 字典)。

    全局单一工作流：取 is_default 行；无则取第一条；都无返回 (None, {})，
    调用方回落到内置 image_z_image_turbo.json（与现状完全一致）。
    """
    try:
        from backend.models import ComfyUIWorkflow
        row = ComfyUIWorkflow.query.filter_by(is_default=True).first()
        if not row:
            row = ComfyUIWorkflow.query.first()
        if not row:
            return None, {}
        node_map = {}
        if row.node_map:
            try:
                node_map = json.loads(row.node_map) or {}
            except Exception:
                node_map = {}
        # P2-E：node_map 为空时自动探测并持久化（仅在有工作流文件路径时尝试）
        if not node_map and row.workflow_path:
            try:
                from backend.game.comfyui_client import load_workflow, auto_detect_node_map
                wf = load_workflow(row.workflow_path)
                detected = auto_detect_node_map(wf)
                if detected:
                    row.node_map = json.dumps(detected, ensure_ascii=False)
                    db.session.commit()
                    node_map = detected
                    logger.info(f"[PhotoGen] 自动探测 node_map: {detected}")
            except Exception as _e:
                logger.debug(f"[PhotoGen] node_map 自动探测跳过: {_e}")
        return row, node_map
    except Exception as e:
        logger.warning(f"[PhotoGen] resolve_workflow 失败: {e}")
        return None, {}


# ══════════════════════════════════════════════════════════════
# 异步生图（线程目标）
# ══════════════════════════════════════════════════════════════
def _generate_photo_thread(app, record_id, character_name, scene_type, gift,
                           outfit_name, user_message, recent_dialogue,
                           game_day, game_time, game_minute, scene_desc=None,
                           scene_summary=None, outfit_old=None, outfit_new=None,
                           reveal=False):
    """异步生图线程：仅负责 ComfyUI 生图 + VLM 回看。

    scene_memo 生成与记忆两写已在 trigger_photo_generation 同步完成（§1.2 文字先于图片），
    故此处 ComfyUI 宕机也不会丢失叙事记忆。业务 gen 锁仅在实际 queue/wait 期间持有，
    不覆盖 ensure_comfyui_running 的启动等待（§3.7）。
    """
    # 生图计时与汇总日志上下文（供 finally 写拍照事件日志）
    _t0 = datetime.now()
    _prompt_id = None
    _saved_path = None
    _neg = None
    _gen_error = None

    with app.app_context():
        from backend.models import db, Character, PhotoRecord
        from backend.game.media_intent import (
            acquire_gen_lock, release_gen_lock, record_trigger, record_trigger_real)
        from backend.game.comfyui_client import (
            get_comfyui_client, ensure_comfyui_running,
            build_portrait_prompt, save_portrait_image,
        )

        char = Character.query.filter_by(name=character_name).first()
        record = PhotoRecord.query.get(record_id)
        if not char or not record:
            return

        try:
            # 1) 提示词（scene_summary 由 trigger_photo_generation 计算后作为参数传入，此处直接用）
            positive, negative = build_chat_photo_prompt(
                char, scene_type, gift=gift, outfit_name=outfit_name,
                scene_desc=scene_desc, scene_summary=scene_summary,
                outfit_old_override=outfit_old, outfit_new_override=outfit_new,
                reveal=reveal)
            _neg = negative
            record.prompt_used = positive
            db.session.commit()

            # 2) ComfyUI 生图（ensure_comfyui_running 内部用独立启动锁，不持业务 gen 锁）
            if not ensure_comfyui_running():
                _gen_error = 'ComfyUI 不可用（ensure_comfyui_running 失败）'
                record.status = 'failed'
                db.session.commit()
                return

            # 仅在实际生图（queue + wait）期间持同角色 gen 锁，避免阻塞并发闸
            acquire_gen_lock(character_name)
            try:
                client = get_comfyui_client()
                # P7 全局单一工作流：优先用 DB 注册的默认工作流，无则回退内置
                wf_row, _node_map = resolve_workflow()
                # 尺寸：场景定格随机竖图/横图（不 1:1），其余保持 1024 方图
                if scene_type == 'scene_freeze':
                    _w, _h = random.choice([(896, 1152), (832, 1216),
                                            (1152, 896), (1216, 832)])
                else:
                    _w, _h = 1024, 1024
                workflow, final_positive = build_portrait_prompt(
                    char, prompt=positive, negative=negative,
                    workflow_row=wf_row, node_map=_node_map,
                    width=_w, height=_h, return_text=True)
                # 用最终注入 ComfyUI 的正向提示词（含性格前缀）覆盖 prompt_used，
                # 让 llm_logs 的 selfie/拍照日志反映真实出图提示词，便于核查。
                record.prompt_used = final_positive
                _prompt_id = client.queue_prompt(workflow)
                if not _prompt_id:
                    _gen_error = 'ComfyUI 未返回 prompt_id'
                    record.status = 'failed'
                    db.session.commit()
                    return
                outputs = client.wait_for_result(_prompt_id, timeout=120)
                _saved_path = None
                if outputs:
                    for node_id, out in outputs.items():
                        if 'images' in out:
                            for img in out['images']:
                                data = client.download_image(
                                    img.get('filename'), img.get('subfolder', ''), img.get('type', 'output'))
                                if data:
                                    _saved_path = save_portrait_image(char.name, data)
                                    if _saved_path:
                                        break
                        if _saved_path:
                            break

                if not _saved_path:
                    _gen_error = 'ComfyUI 生图无输出图像'
                    record.status = 'failed'
                    db.session.commit()
                    return

                record.image_path = _saved_path
                record.status = 'done'
                # seed 回写：不再硬编码节点 '44'（仅 zimage 有效）；qwen-image-2512 等
                # 其他工作流的 KSampler 节点号不同，此前一律落到默认值 0。
                # 改为 auto_detect_node_map 动态探测 seed 节点与键名，兼容任意 prompt_family。
                from backend.game.comfyui_client import auto_detect_node_map
                _mp = auto_detect_node_map(workflow)
                _seed_node = _mp.get('seed')
                _seed_key = _mp.get('seed_input', 'seed')
                record.seed = (workflow.get(str(_seed_node), {})
                               .get('inputs', {}).get(_seed_key, 0)
                               ) if _seed_node else 0
                db.session.commit()
            finally:
                release_gen_lock(character_name)

            # 3) B 增强：VLM 回看（importance>=60 才调；scene_memo 已在触发时写入）
            # 注意：图片已生成并提交为 done，此处任何异常都只记警告，绝不把状态翻回 failed。
            importance = record.importance or 45.0
            if importance >= 60:
                try:
                    ctx = {
                        'char_name': char.name,
                        'location': getattr(char, 'location', ''),
                        'game_day': game_day,
                        'game_time': game_time,
                    }
                    vlm_caption = vlm_caption_image(_saved_path, ctx)
                    if vlm_caption:
                        record.vlm_caption = vlm_caption
                        try:
                            from backend.game.memory import _compute_memory_embedding
                            sim = cosine_similarity(
                                _compute_memory_embedding(record.scene_memo),
                                _compute_memory_embedding(vlm_caption))
                            record.memo_similarity = round(sim, 4)
                            # A/B 合流：相似度过低 → 长期记忆以 VLM 为准
                            if sim < 0.45:
                                record.status = 'drift'
                                write_photo_memories(char, record, vlm_caption, importance)
                        except Exception as e:
                            logger.warning(f"[PhotoGen] A/B 合流失败: {e}")
                        db.session.commit()
                except Exception as e:
                    logger.warning(f"[PhotoGen] VLM 回看失败(不影响已生成图片，状态保持 done): {e}")

        except HTTPError as e:
            _gen_error = f'ComfyUI HTTPError: HTTP {e.code} {e.reason}'
            logger.error(f"[PhotoGen] 生图 HTTPError: {_gen_error}")
            try:
                record.status = 'failed'
                db.session.commit()
            except Exception:
                pass
        except KeyError as e:
            _gen_error = f'工作流缺少节点或输入: {e}'
            logger.error(f"[PhotoGen] 生图 KeyError: {_gen_error}")
            try:
                record.status = 'failed'
                db.session.commit()
            except Exception:
                pass
        except Exception as e:
            _gen_error = str(e)
            logger.error(f"[PhotoGen] 生图线程异常: {e}")
            try:
                record.status = 'failed'
                db.session.commit()
            except Exception:
                pass
        finally:
            # 拍照事件日志（参照 llm_logs 现有 {timestamp,call_type,request,response} 格式，
            # 文件名按场景类型区分，如 拍照photo_take / 自拍selfie / 场景定格scene_freeze）
            try:
                from backend.game.llm_utils import write_comfyui_log
                _elapsed = int((datetime.now() - _t0).total_seconds() * 1000)
                _final_status = record.status or 'failed'
                _img = _saved_path or record.image_path or ''
                request_info = {
                    "scene_type": scene_type,
                    "scene_type_cn": SCENE_TYPE_CN.get(scene_type, scene_type),
                    "player_message": user_message,
                    "scene_memo": record.scene_memo,
                    "gift_name": record.gift_name,
                    "outfit_name": record.outfit_name,
                    "location": record.location,
                    "source_day": record.source_day,
                    "source_time": record.source_time,
                    "positive_prompt": record.prompt_used or '',
                    "negative_prompt": _neg or '',
                    "seed": record.seed,
                    "record_id": record.id,
                }
                response_info = {
                    "success": _final_status == 'done',
                    "status": _final_status,
                    "prompt_id": _prompt_id,
                    "image_path": _img,
                    "image_url": photo_image_url(_img),
                    "elapsed_ms": _elapsed,
                    "error": _gen_error,
                }
                write_comfyui_log(scene_type, character_name, request_info, response_info)
            except Exception:
                pass
            try:
                record_trigger(character_name, game_day, game_minute)
                record_trigger_real(character_name)
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════
# 入口：触发生图（请求线程调用，立即返回 pending 记录）
# ══════════════════════════════════════════════════════════════
def trigger_photo_generation(character, decision, user_message='', recent_dialogue='',
                             game_day=None, game_time=None, app=None, scene_memo=None,
                             game_minute=None, scene_desc=None):
    """按 media_intent 的判定结果触发生图。

    decision: evaluate_media_intent 返回的 dict（含 scene_type/gift/outfit_name）。
    返回 PhotoRecord.to_dict()（status='pending'）或 None。

    文字先于图片（§1.2）：scene_memo 生成 + 记忆两写在**请求线程同步**完成，
    不依赖 ComfyUI 是否可用；随后仅把"生图 + VLM 回看"交给异步线程。
    """
    from flask import current_app
    from backend.models import db, PhotoRecord

    if not decision or not decision.get('triggered'):
        return None
    scene_type = decision.get('scene_type')
    if not scene_type or scene_type == 'share_recall':
        return None

    if app is None:
        app = current_app._get_current_object()

    game_day = game_day if game_day is not None else getattr(character, 'game_day', 0)
    game_time = game_time if game_time is not None else getattr(character, 'game_time', '')
    if game_minute is None:
        # 调用方未传（如手动生图端点）→ 回退到角色当前游戏时间，避免存入 (day, None) 导致后续崩溃
        game_minute = int(getattr(character, 'game_hour', 0) or 0) * 60 + int(getattr(character, 'game_minute', 0) or 0)

    # 重要性（记忆权重）
    importance = 45.0
    if scene_type == 'gift_photo':
        importance = 75.0
    elif scene_type in ('scene_freeze', 'share_new'):
        importance = 60.0

    # ── 同步：scene_memo（A 主干）+ 记忆两写（即使 ComfyUI 宕机也写）──
    if not scene_memo:
        if scene_type == 'scene_freeze':
            # 场景定格只使用下方的双人叙事调用，避免旧版 photo.scene_extract 重复调用。
            scene_memo = ''
        else:
            extract = run_scene_extract(character, scene_type, user_message, recent_dialogue)
            scene_memo = extract.get('记忆句', '')

    # ── 场景定格：一次活跃 LLM 双人叙事 + Python 硬规则地点/时间/人物 ──
    scene_summary = ''
    if scene_type == 'scene_freeze' and scene_desc is None:
        try:
            narrative = _scene_freeze_narrative(character, user_message, recent_dialogue)
            player_desc = _player_prompt_desc(character)
            scene_memo = narrative.get('叙事句', '') or scene_memo
            scene_summary = '，'.join([
                narrative.get('叙事句', ''),
                f'女主姿态：{narrative.get("女主姿态", "")}',
                f'玩家姿态：{narrative.get("玩家姿态", "")}',
                f'动作道具：{narrative.get("动作道具", "")}',
                f'情绪氛围：{narrative.get("情绪氛围", "")}',
                f'场景环境：{_scene_environment_desc(character)}',
                player_desc,
            ])
            scene_desc = _build_sky_desc(character)
        except Exception as e:
            logger.warning(f"[PhotoGen] 场景定格双人叙事生成失败(不影响生图): {e}")
            scene_summary = _player_prompt_desc(character)
            scene_desc = _build_sky_desc(character)

    record = PhotoRecord(
        character_name=character.name,
        scene_type=scene_type,
        gift_name=decision.get('gift') or '',
        outfit_name=decision.get('outfit_name') or '',
        location=getattr(character, 'location', '') or '',
        source_day=game_day,
        source_time=game_time,
        status='pending',
        scene_memo=scene_memo,
        importance=importance,
    )
    db.session.add(record)
    db.session.commit()
    # 仅高意义场景（定格/礼物/主动分享，importance>=60）写长期记忆；
    # 普通拍照（photo_take/activity_shot/outfit_change/selfie，importance=45）只落 PhotoRecord，
    # 不写 CharacterMemory，避免低边际价值的近重读图污染长期记忆召回。
    if importance >= 60:
        write_photo_memories(character, record, scene_memo, importance)

    rec_dict = enrich_photo_dict(record.to_dict())

    t = threading.Thread(
        target=_generate_photo_thread,
        args=(app, record.id, character.name, scene_type, decision.get('gift'),
              decision.get('outfit_name'), user_message, recent_dialogue,
              game_day, game_time, game_minute, scene_desc, scene_summary,
              decision.get('outfit_old'), decision.get('outfit_new'),
              bool(decision.get('reveal'))),
        daemon=True,
    )
    t.start()
    logger.info(f"[PhotoGen] 已触发生图：{character.name} / {scene_type} / record={record.id}")
    return rec_dict


# ══════════════════════════════════════════════════════════════
# 检索接口（供路由 / 前端 / P8 share_recall）
# ══════════════════════════════════════════════════════════════
def photo_image_url(image_path):
    """由落盘路径（完整路径）换算前端可访问的图片 URL（复用肖像服务路由）。"""
    if not image_path:
        return ''
    return f"/api/portrait/image/{os.path.basename(image_path)}"


_photo_venue_cache = {}  # character_name -> {venue_id: 中文名}


def _resolve_photo_location_name(character_name, venue_id):
    """把照片的 location（venue_id 或中文名）解析成可展示的中文地点名。

    命中角色专属场馆表则回中文名；自定义地点直接存中文名时原样返回。
    """
    if not venue_id:
        return ''
    cache = _photo_venue_cache.get(character_name)
    if cache is None:
        cache = {}
        try:
            from backend.models import Character
            from backend.game.activity import get_character_venues
            c = Character.query.filter_by(name=character_name).first()
            if c:
                cache = get_character_venues(c) or {}
        except Exception:
            cache = {}
        _photo_venue_cache[character_name] = cache
    return cache.get(venue_id, venue_id)


def enrich_photo_dict(d):
    """给 PhotoRecord.to_dict() 补 image_url + scene_type_cn + location_name，供前端直接渲染。"""
    if not d:
        return d
    d['image_url'] = photo_image_url(d.get('image_path', ''))
    d['scene_type_cn'] = SCENE_TYPE_CN.get(d.get('scene_type', ''), d.get('scene_type', ''))
    d['location_name'] = _resolve_photo_location_name(d.get('character_name', ''), d.get('location', ''))
    return d


def get_photo_records(character_name, limit=50, only_done=True):
    from backend.models import PhotoRecord
    q = PhotoRecord.query.filter_by(character_name=character_name)
    if only_done:
        q = q.filter_by(status='done')
    return [enrich_photo_dict(r.to_dict()) for r in q.order_by(PhotoRecord.id.desc()).limit(limit).all()]


def get_photo_record(record_id):
    from backend.models import PhotoRecord
    r = PhotoRecord.query.get(record_id)
    return enrich_photo_dict(r.to_dict()) if r else None


def search_recall_photos(character_name, keyword='', limit=10):
    """share_recall 用：检索该女主真实存在的照片（不幻觉）。"""
    from backend.models import PhotoRecord
    q = PhotoRecord.query.filter_by(character_name=character_name, status='done')
    if keyword:
        q = q.filter(PhotoRecord.scene_memo.contains(keyword) |
                     PhotoRecord.gift_name.contains(keyword) |
                     PhotoRecord.outfit_name.contains(keyword))
    return [enrich_photo_dict(r.to_dict()) for r in q.order_by(PhotoRecord.id.desc()).limit(limit).all()]
