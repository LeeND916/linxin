"""
Prompt 注册表 — 统一管理全部 28 个 LLM prompt 模板。

每个 prompt 有一个唯一 ID（如 "dialogue.system"），在 REGISTRY 中定义默认文本。
运行时 PromptManager 从 DB 读取用户自定义版本，fallback 到 REGISTRY 默认值。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend.game.variable_resolver import resolve_variables, render_template

logger = logging.getLogger(__name__)


# =============================================================================
# PromptTemplate — 描述一个 prompt 的元数据
# =============================================================================

@dataclass
class PromptTemplate:
    """一个 LLM prompt 模板的元数据。"""
    id: str                             # "dialogue.system"
    category: str                       # "dialogue" / "event" / "classify" / "character" / "other" / "case"
    label: str                          # 中文显示名："主对话系统提示词"
    default_text: str                   # 代码默认值（从当前代码中提取）
    variables: List[str] = field(default_factory=list)  # 占位符列表
    description: str = ""               # 功能说明
    version: int = 1                    # 代码中 REGISTRY 的版本号
    is_system: bool = False             # True = 🔴 只读
    require_test: bool = False          # True = 🟡 保存前需验证


# =============================================================================
# REGISTRY — 全部 28 个 prompt 的默认值
# =============================================================================

REGISTRY: Dict[str, PromptTemplate] = {}

def _reg(
    id: str, category: str, label: str, desc: str, *,
    system: bool = False, test: bool = False,
    default_text: str | None = None,
) -> PromptTemplate:
    """注册一个 prompt。default_text=None 时使用占位文本（待从源码提取）。"""
    text = default_text or f"# [{id}] 默认模板 — 待从源码提取\n# 在阶段二迁移时替换为实际 prompt 文本"
    tmpl = PromptTemplate(
        id=id, category=category, label=label,
        default_text=text,
        description=desc,
        is_system=system, require_test=test,
    )
    REGISTRY[id] = tmpl
    return tmpl


# ── 对话系统（6 个）──

_reg("dialogue.system",        "dialogue", "主对话系统提示词",   "角色身份 + 对话规则 + 玩家铁律", test=True)
_reg("dialogue.emotional",     "dialogue", "情绪属性变化解析",    "根据对话生成属性变化 JSON",     system=True)
_reg("dialogue.static_rules",  "dialogue", "静态规则片段",       "物理状态感知 + 关系快捷对话",    default_text="# [dialogue.static_rules] 由 dialogue.build_static_rules() 运行时动态组装（6 个子来源拼接），不在此静态存储。", test=True)
_reg("dialogue.hint_emotional","dialogue", "情绪智能快捷建议",    "情绪分类驱动的对话建议（4-5条）")
_reg("dialogue.constraints",   "dialogue", "状态约束提示词",     "物理/心理/关系阈值警告", default_text="# [dialogue.constraints] 由 get_llm_constraints_prompt() 运行时动态组装，不在此静态存储。")
_reg("dialogue.recommend",     "dialogue", "对话推荐",           "LLM 推荐 5 句对话文案")

# ── 事件系统（6 个）──

_reg("event.random",           "event",   "随机事件生成",        "生成符合角色状态的随机事件 JSON", test=True)
_reg("event.active_decision",  "event",   "主动消息决策",        "LLM 判断是否主动发消息 + 内容",   test=True)
_reg("event.active_message",   "event",   "批量主动消息",        "批量生成 N 条角色主动消息")
_reg("event.relation_update",  "event",   "朋友关系更新",        "根据事件判断朋友关系数值变化")
_reg("event.emotion_detect",   "event",   "情感时刻检测",        "判断对话是否为高价值情感时刻",
     default_text="""分析以下对话，判断是否为高价值情感时刻。

对话双方：
- 玩家
- {character.name}（女主）

对话内容：
玩家说：{context.user_message}
{character.name}回复：{context.character_reply}

本轮属性变化：{context.effects_json}

高价值时刻的类型：
- tender（温柔时刻）：安慰、关心、甜蜜的话、温馨的互动
- conflict（冲突时刻）：争吵、误解、冷战、不满
- breakthrough（突破时刻）：关系进展、表白、重要承诺、分享秘密
- memory（难忘时刻）：特别的共同经历、有意义的对话

判断标准：
- 日常寒暄、普通聊天 → 不是高价值时刻
- 涉及情感表达、关系变化、重要信息 → 是高价值时刻

【回忆摘要视角约束（必须严格遵守）】
- summary 一律以女主第一人称视角书写：用"我"代表女主自己，用"你"代表玩家。
- 严禁使用"玩家""对方""她"以及女主本名等第三人称或元称谓来指代对话双方。
- 摘要控制在 20 字以内，只概括发生了什么、情感基调如何，禁止展开细节或评价。

请返回 JSON：
{"is_moment": true/false, "type": "tender/conflict/breakthrough/memory", "intensity": 0-100, "summary": "第一人称'我'=女主、'你'=玩家的一句话回忆摘要（≤20字，禁用玩家/对方/她/女主本名）"}

如果不是高价值时刻，返回：{"is_moment": false}

只返回 JSON，不要解释。""")
_reg("event.llm_fallback",     "event",   "LLM 兜底事件",        "ConditionMatcher 未命中时的 LLM 备用事件")

# ── 分类器（2 个）──

_reg("classify.emotion",       "classify", "情绪分类器（4-shot）", "Qwen2-1.5B 本地情绪分类",   system=True)
_reg("classify.tts",           "classify", "TTS 情绪分类器",      "腾讯云 TTS 情绪参数分类",   system=True)

# ── 角色创建（5 个）──

_reg("character.profile",      "character","角色画像生成",        "从表单生成完整角色 JSON Schema")
_reg("character.achievement",  "character","成就系统生成",        "生成 7-14 个角色专属成就")
_reg("character.activity_map", "character","活动地图生成",        "生成地点 + 专属活动")
_reg("character.outfit",       "character","穿搭数据生成",        "生成 65-80 个组件 + 60 套预设")
_reg("character.world_setting","character","世界观生成",          "生成 narrative_schedule 世界观 JSON")

# ── 任务系统 Mission（3 个）──

_reg("mission.generate_main",        "mission",  "任务生成-五幕主线",   "phase驱动：生成五幕stages(含嵌套events)+NPC",          test=True)
_reg("mission.generate_subsystems",  "mission",  "任务生成-子系统",    "第二次调用：生成成就+弧+事件模板。NPC名字必须有辨识度。",    test=True)
_reg("mission.progress_event",       "mission",  "任务进展事件",       "根据对话和任务阶段生成进展小事件描述")

# ── 思考模式关闭标记（仅用于前端「提示词设置」只读展示）──
# 下列注册表 prompt 对应的调用函数已通过 safe_llm_post(extra_body={"thinking": {"type": "disabled"}})
# 关闭思考模式（JSON/总结类场景关思考省 token、避免 reasoning_content 污染输出）。
# 注意：以下 4 个函数其 prompt 为代码内联拼装、没有对应的注册表 prompt，故不在此集合，也不会在提示词面板出现：
#   _llm_summarize_mission(mission_summary) / _normalize_bucket(history_normalize) /
#   llm_emotional_changes(emotional_changes) / _enforce_reply_format(dialogue_format_fix)
THINKING_DISABLED_PROMPTS = {
    "mission.generate_main",      # _llm_generate_json（call_type=mission_generate_main）
    "mission.progress_event",     # _try_llm_event（call_type=mission_llm_event）
    "dialogue.hint_emotional",    # _generate_emotional_hints_llm
    "memory.extract",             # extract_memories_from_dialogue（JSON 提取关思考省 token）
}

# ── 其他（2 个）──

_reg("tick.update",            "other",    "Tick 状态更新",       "时间推进后的状态变化",          test=True)
_reg("memory.extract",         "other",    "记忆提取",            "从对话中提取长期记忆（LLM，第一人称视角）")
_reg("tts.deepseek_modify",    "other",    "TTS DeepSeek 修饰",   "生成 2-8 个字的 TTS 语气短语",       test=True)
_reg("news.summary",           "other",    "新闻摘要",            "将新闻压缩为1-2句摘要")

# ── 聊天生图（photo 分类）──
# LLM 提示词（走 PromptManager，DB 覆盖默认）
PHOTO_SCENE_EXTRACT = """你是一位摄影导演。根据当前对话内容，把「此刻应该被拍下来的画面」压缩成结构化描述。

【角色】{char_name}，{age}岁，{identity_label}
【地点】{location}
【时间】第{game_day}天 {game_time}
【当前情绪】心情{mood}／压力{stress}（0-100）
【当前活动】{current_action}
【拍照类型】{scene_type_cn}

【最近对话】
{recent_dialogue}

【玩家本轮发言】
{user_message}

────────────────
输出一个 JSON，不要任何解释、不要代码块标记：

{{
  "姿态": "中文，身体姿态与动作，12-20字",
  "表情": "中文，面部表情与眼神，8-15字",
  "景别": "中文，从白名单里选1项：面部特写／半身像／七分身／全身像／中近景",
  "视角": "中文，从白名单里选1项：平视视角／微仰视视角／微俯视视角／过肩视角",
  "光线": "中文，光线与色调氛围，8-15字",
  "画面细节": "中文，画面里的关键物件或环境细节，没有就填空字符串，8-15字",
  "记忆句": "中文，女主第一人称，20-35字"
}}

【硬性约束】
1. 姿态／表情／光线／画面细节用中文短句，逗号分隔，不写完整句子，不写主语。
2. 严禁描写：发型、发色、瞳色、脸型、身材、年龄、衣服款式与颜色。
   这些已由系统锁定，你写了会被丢弃，并可能破坏画面一致性。
3. 严禁出现：杰作、最高画质、8K、写实风格、动漫风格 等画风与质量词。
4. 画面必须与【最近对话】的氛围一致——对话在争吵就不能写"微笑"。
5. 记忆句用女主第一人称："我"=女主，"你"=玩家。禁止出现"玩家／她／女主本名／对方"。
   示例：我靠在窗边低头看书，你按下快门那一刻我刚好抬起头。
6. 记忆句只写画面里能看见的东西，不写心理活动、不写对话内容。
7. 只输出 JSON，不要用 ``` 包裹。"""

VLM_CAPTION_PROMPT = """这是一张{char_name}的照片，拍摄于{location}，第{game_day}天{game_time}。

请以照片中人物的第一人称视角，用一句中文描述这张照片里能看见的画面。

【要求】
1. 第一人称："我"=照片中的人物，"你"=拍照的人。
2. 只描述画面里真实可见的：姿态、动作、视线方向、手里拿着什么、
   环境里的关键物件、光线氛围。
3. 不要描述：发型发色、五官长相、身材、衣服款式颜色（这些不需要复述）。
4. 不要写心理活动、不要评价照片好坏、不要写"这张照片"。
5. 20-40字，一句话，不要换行，不要标点堆砌。

示例：我坐在窗边的木椅上，手里还捏着半页纸，侧过头看向你，午后的光斜落在桌角。"""

_reg("photo.scene_extract", "photo", "拍照场景压缩",
     "把当前对话压缩成结构化中文画面描述，同时产出中文记忆句",
     test=True, default_text=PHOTO_SCENE_EXTRACT)
_reg("photo.vlm_caption",   "photo", "VLM 多模态回看",
     "看图输出与记忆句同口径的中文画面描述",
     test=True, default_text=VLM_CAPTION_PROMPT)

SCENE_FREEZE_NARRATIVE_PROMPT = """你是电影分镜师。根据角色资料、地点时间和最近聊天，生成场景定格的双人叙事字段。
必须只有玩家与女主两人，女主是画面焦点；玩家性别、姓名、身份和女主身份必须遵守输入，不得改写。
只生成可见动作，不写发型、五官、年龄、心理活动或对话原文；服饰与固定配饰由 Python 注入，不要擅自删改；亲密动作必须符合关系状态。
动作道具字段只写核心动作与道具，情绪氛围字段只写氛围，不要输出“女主姿态：”“玩家姿态：”等字段标题。

【女主】{char_name}，{char_gender}，{identity_label}
【玩家】{player_name}，{player_gender}，{player_identity}
【地点】{location}
【时间】第{game_day}天 {game_time}
【关系】{relationship_status}
如果地点包含“车”字，动作道具或情绪氛围必须明确写出车厢内部可见元素，例如座椅、车窗、安全带、仪表台、中控台或车门内饰；不要只写“车上”。
【最近聊天（仅最新6轮真实对话，不包含照片描述、场景描述、系统注入或生图结果）】{recent_dialogue}

只返回 JSON，不要解释：
{{"叙事句":"20字以内的一句话","女主姿态":"12-24字","玩家姿态":"12-24字","动作道具":"12-24字","情绪氛围":"8-18字"}}"""
_reg("photo.scene_freeze_narrative", "photo", "场景定格·双人叙事",
     "一次调用活跃 LLM 生成双人姿态、动作道具、情绪和20字内叙事句",
     test=True, default_text=SCENE_FREEZE_NARRATIVE_PROMPT)

# ComfyUI 文本编码器提示词（模板注册，变量 {outfit}/{location}/{pose}/{gift_name}/{outfit_old}/{outfit_new}/{scene_desc}）
_reg("photo.l1_style", "photo", "写实风身份锚（风格串）",
     "全局固定的写实风风格串", default_text="真实摄影写实风格,(专业相机拍摄的真人照片),皮肤真实纹理与毛孔细节,自然光影,高像素质感,8k 分辨率,摄影级构图")
_reg("photo.negative", "photo", "中文负面提示词",
     "喂给负面 CLIP 的中文负面词", default_text="低质量,模糊,畸变,多余肢体,五官扭曲,过度磨皮,塑料感,油腻皮肤,反动漫,二次元,插画,美式卡通,3D渲染,CG建模感,水彩,厚涂,线条勾勒,非插图,非漫画分镜,非对比图表,非信息图,非海报,非带文字标签的图,非拼贴,低分辨率,噪点,过曝,死鱼眼,空洞凝视,头部前倾,颈部过长,头颈分离,头部从胸口突出,长颈鹿颈,脖子伸长,foreshorten neck,elongated neck,neck merged into body,distorted body proportions,head detached from torso")
_reg("photo.lighting", "photo", "光线层（按时间映射）",
     "内部 7 段时间表，按游戏时间渲染", default_text="清晨:柔和的晨光透过窗纱,清冷通透的蓝调;上午:明亮的漫射自然光,清爽干净;正午:强烈顶光,明亮高对比,干净利落;下午:温暖的斜射阳光,柔和的琥珀色调;黄昏:橙红晚霞映照,浪漫温暖的轮廓光;夜晚:室内暖光或街灯,柔和昏黄的氛围光;深夜:冷调昏暗光线,静谧私密的氛围")
_reg("photo.l3_photo_take", "photo", "拍照场景 L3 模板",
     "被拍场景：目光直视镜头", default_text="正面站立,目光直视镜头,自然微笑,轻微侧身显身材线条,{outfit},背景是{location}室内环境（镜头平视或微俯视不夸张,头部不前倾,头颈比例自然）")
_reg("photo.l3_selfie", "photo", "自拍场景 L3 模板",
     "自拍：手臂前伸握手机，角度由 {angle} 随机注入", default_text="手臂前伸握手机自拍,{angle},广角畸变,俏皮表情,{outfit},背景是{location}（镜头平视或微俯视不夸张,头部不前倾,头颈比例自然）")
_reg("photo.l3_activity_shot", "photo", "活动特写 L3 模板",
     "活动特写：查表姿态", default_text="{pose},{outfit},专注投入的神情,背景是{location}（镜头平视或微俯视不夸张,头部不前倾,头颈比例自然）")
_reg("photo.l3_outfit_change", "photo", "换装展示 L3 模板",
     "左右分栏展示换装前后", default_text="一张真实摄影写实的照片,画面左右平分为两半,左半边是换装前,全身展示旧穿搭{outfit_old};右半边是换装后,全身展示新穿搭{outfit_new},左右两侧为同一人,脸型五官一致,全身像,姿态自然,背景是{location}的真实环境（镜头平视或微俯视不夸张,头部不前倾,头颈比例自然）")
_reg("photo.l3_scene_freeze", "photo", "场景定格 L3 模板",
     "场景定格：氛围感", default_text="{scene_desc},富有故事感的瞬间定格,电影感构图,环境氛围突出,自然光影（镜头平视或微俯视不夸张,头部不前倾,头颈比例自然）")

_reg("photo.l3_gift_photo", "photo", "礼物场景 L3 模板",
     "礼物赠送：手持礼物", default_text="双手捧着{gift_name}递向镜头,温柔笑意,惊喜神情,{outfit},背景是{location}（镜头平视或微俯视不夸张,头部不前倾,头颈比例自然）")
_reg("photo.l3_share_photo", "photo", "女主展图 L3 模板",
     "随手拍质感", default_text="随手拍质感,生活化构图,自然不做作,{outfit},背景是{location}（镜头平视或微俯视不夸张,头部不前倾,头颈比例自然）")
# 3. 画面中只有{char_name}一人（除非聊天明确是和玩家一起的互动，才用"两人"表示），不要出现任何第三个人物或旁观者。
# 总计 26 个 prompt 模板（全部注册于上方 REGISTRY）

# ── 已迁移项的默认文本（覆盖占位符）──

# news.summary — 从 news_service.py 提取
REGISTRY["news.summary"].default_text = (
    "请把下面这条新闻压缩成1-2句话的简短摘要（不超过60字），"
    "只保留核心事实，不要加评论、不要加来源、不要用引号包裹：\n"
    "标题：{context.news_title}\n正文：{context.news_body}"
)

# character.world_setting — 从已删除的 generate_world_settings.py 提炼完整 schema 内联
REGISTRY["character.world_setting"].default_text = """你是一个剧本世界观设计师。请根据角色信息生成完整的「世界观 JSON」（含 narrative_schedule）。

角色信息：
姓名：{character.name}
性别：{character.gender}
身份：{character.identity}
专业：{character.major}
性格：{character.personality}
梦想：{character.dream_primary}

请生成以下格式的 JSON（严格按照这个结构，不要改动字段名）：

```json
{
  "character_name": "角色名",
  "version": 1,
  "anchor": {
    "core_trait": "核心性格特质",
    "value": "核心价值观",
    "boundary": "底线/不能接受的事",
    "conflict_zone": "核心矛盾（内心冲突）"
  },
  "daily_rhythm": {
    "morning": "早上通常做什么",
    "forenoon": "上午通常做什么",
    "afternoon": "下午通常做什么",
    "evening": "晚上通常做什么",
    "night": "睡前做什么"
  },
  "stress_sources": ["压力源1", "压力源2", "压力源3"],
  "comfort_activities": ["安慰活动1", "安慰活动2", "安慰活动3"],

  "life_goals": [
    {"goal": "人生目标1", "type": "career|academic|family|personal", "urgency": 1-10},
    {"goal": "人生目标2", "type": "...", "urgency": 1-10}
  ],
  "central_conflict": "核心矛盾描述（一句话，驱动剧情弧）",
  "story_arc_hints": [
    {"phase": "根据角色职业定制的阶段名1", "theme": "主题描述"},
    {"phase": "根据角色职业定制的阶段名2", "theme": "主题描述"},
    {"phase": "根据角色职业定制的阶段名3", "theme": "主题描述"}
  ],

  "world_view": {
    "optimism": 0-100,
    "trust_in_others": 0-100,
    "sense_of_justice": 0-100
  },
  "story_tones": ["基调1", "基调2", "基调3"],
  "constraints": {
    "max_single_attr_delta": 25,
    "forbidden_behaviors": ["禁止的行为1", "禁止的行为2"]
  },

  "narrative_schedule": {
    "phases": [
      {
        "phase": "【重要】根据角色的具体职业和身份定制的阶段名称",
        "day_range": [起始天, 结束天],
        "start_time": "本阶段开始时间，如「第1天 09:00」",
        "end_time": "本阶段结束时间，如「第30天 18:00」",
        "location": "本阶段主要发生地点，如「律所」",
        "description": "本阶段整体剧情描述：角色处于什么状态、面临什么主线任务、心境如何"
      }
    ]
  }
}
```

【关键要求 - 请仔细遵守】
1. daily_rhythm 和 stress_sources/comfort_activities 必须完全符合角色的职业身份。
   例：律师应该有"出庭""会见当事人""写法律意见书"；医生有"查房""手术""门诊"；设计师有"开评审会""写设计文档""肝Demo"。
2. narrative_schedule 的阶段名称必须量身定制，体现角色职业特点：
   - 律师：不能用"启蒙期/成长期"，应该用"初入律所→独立接案→打赢关键官司"这类
   - 医生：不能用"启蒙期/成长期"，应该用"住院医师→主治医生→科室骨干"这类
   - 设计师：不能用"启蒙期/成长期"，应该用"项目立项→攻坚开发→产品上线"这类
   - 学生：可以用"新生适应→学业挑战→比赛/毕业"，但不能直接叫"启蒙期/成长期"
3. day_range 要合理，每个阶段至少 15-30 天（否则游戏一天就过完一个阶段）
4. narrative_schedule 的每个「阶段(phase)」必须自带完整内容，且不要输出 stage_events 字段（事件粒度不再展开，不写 trigger_id、不引用 triggered_events.json）：
   - day_range：本阶段天数区间 [起始天, 结束天]
   - start_time / end_time：本阶段开始/结束时间，如「第1天 09:00」「第30天 18:00」
   - location：本阶段主要发生地点，如「律所」
   - description：本阶段整体剧情描述，写清角色状态、主线任务与心境
5. 只返回 JSON，不要其他文字（不要 Markdown 代码块标记之外的解释）

6. 性别一致性（强制）：本角色性别为 {character.gender}（female=女性 / male=男性）。所有描写角色「自身」的代词必须严格匹配性别——女性用「她/她的/她自己」、男性用「他/他的/他自己」，严禁错配（如女性角色被写成「他」）。家庭与亲密关系须性别自洽：女性角色不得拥有「妻子/老公/儿子/女儿」等与自身性别矛盾的配偶或父母身份设定（"母亲/父亲/姐妹/兄弟"等作为长辈或同辈亲属则允许）；男性角色同理。NPC、路人等「他人」按各自性别正常描写，不受此限。"""

# ── 本地模型/独立进程提示词（REGISTRY 仅注册默认文本，无源码迁移）──

# dialogue.hint_emotional — _generate_emotional_hints_llm() 使用
REGISTRY["dialogue.hint_emotional"].default_text = (
    "你是对话建议助手。玩家是{character.name}的{player.identity}，正在和她聊天。\n"
    "请为玩家生成4-5条可直接发送给{character.name}的快捷对话建议，"
    "目标是增进两人的亲密度和好感，让她感到被理解、被关心。\n\n"
    "【{character.name}的信息】{character.age}岁{character.identity}\n"
    "当前状态：心情{status.mood} 压力{status.stress} 幸福{status.happiness} 关系{character.relationship_status}\n"
    '她刚才说："{context.last_reply}"\n\n'
    "要求：\n"
    "1. 每条8-15字，玩家第一人称口吻\n"
    "2. 贴合她刚才说的内容和当前情绪，多样化（延续话题/关心体贴/鼓励肯定/轻松转移话题）\n"
    "3. 根据关系状态调整亲密程度：friends（朋友）阶段克制友善，dating（恋人）阶段可更亲昵\n"
    "4. 只输出JSON数组，不要其他内容"
)

# tts.deepseek_modify — tts_manager.py
REGISTRY["tts.deepseek_modify"].default_text = (
    "You are {character.name}'s tone modifier. "
    "Current emotion: {context.emotion}, intensity: {context.intensity}. "
    "Generate a very short tone phrase (2-8 chars) to enhance TTS expression. "
    "Output only the phrase."
)

# dialogue.recommend — api_dialogue_recommend() 使用
REGISTRY["dialogue.recommend"].default_text = (
    "你是对话推荐助手。根据以下信息，为玩家推荐 5 句可以说给{character.name}的话，"
    "目标是增加【{context.relation_type_cn}】值。\n\n"
    "【{character.name}当前状态】\n"
    "- 精力：{status.energy}/100\n"
    "- 心情：{status.mood}/100\n"
    "- 压力：{status.stress}/100\n"
    "- 幸福感：{status.happiness}/100\n"
    "- 当前时间：第{character.game_day}天 {character.game_hour}:{character.game_minute}（{context.weekday}）\n"
    "- 当前位置：{character.location}\n"
    "- 关系状态：{context.relationship_display}\n\n"
    "【与{player.nickname}的关系】\n"
    "- 信任度：{relation.player_trust}\n"
    "- 好感度：{relation.player_affection}\n"
    "- 尊重度：{relation.player_respect}\n"
    "- 亲密度：{relation.player_intimacy}\n\n"
    "【今日事件】\n{context.recent_events}\n\n"
    "【最近聊天记录】\n{context.recent_dialogue}\n\n"
    "{context.relation_rules}\n\n"
    "要求：推荐 5 句话，每句话 15-50 字，话术自然不油腻。"
    "根据{character.name}当前状态调整话术策略。\n\n"
    "【输出格式（必须严格遵守）】\n"
    "只输出一个 JSON 数组，不要任何 Markdown 代码块标记、不要任何解释文字。\n"
    "数组每个元素是一个对象，包含以下两个字段：\n"
    "- \"text\": 推荐说给玩家的话（字符串，15-50 字）\n"
    "- \"reason\": 为什么这句话能提升【{context.relation_type_cn}】的简短理由（字符串，一句话）\n\n"
    "示例：\n"
    "[{\"text\":\"刚看你门诊排得很满，记得劳逸结合。\","
    "\"reason\":\"主动关心她的工作强度，体现细心，易建立信任\"},"
    "{\"text\":\"你们科室的绿萝最近怎么样？\","
    "\"reason\":\"延续上次聊过的轻松话题，拉近距离\"}]"
)

# classify.emotion — classifier_server.py（独立 Flask 进程）
REGISTRY["classify.emotion"].default_text = """你是对话情绪分析专家。根据最近对话和角色当前台词，判断角色的情绪。

可选情绪（10种）：平静/喜悦/忧伤/愤怒/惊讶/温柔/委屈/疲惫/调侃/焦虑

输出格式（严格JSON）：
{"emotion": "情绪英文名", "intensity": 0.0-1.0, "confidence": 0.0-1.0}

强度定义：0.0-0.33=弱，0.34-0.66=中，0.67-1.0=强
置信度：你对判断有多确定，0.0=完全不确定，1.0=百分百确定

### 示例 ###
（4-shot 示例，含 4 组对话→JSON 输出）

### 实际任务（只输出JSON，不要任何解释）###

对话：
{context.history}

当前台词：{context.user_message}
输出："""

# classify.tts — classifier_server.py（独立 Flask 进程）
REGISTRY["classify.tts"].default_text = """你是对话语音合成参数专家。根据角色台词和对话上下文，直接输出腾讯云 TTS 的结构化合成参数。

【参数说明】
- emotion: 9选1 → neutral/happy/sad/angry/fear/coquettish/surprised/disgusted/calm
- emotion_intensity: 50~200，100=默认
- speed: -2.0~2.0，0=正常
- volume: -10.0~10.0，0=默认

（6-shot 示例）

对话：{history}
当前台词：{text}
输出："""

# memory.extract — memory.py extract_memories_from_dialogue()（LLM 引擎）
REGISTRY["memory.extract"].default_text = """你是{character.name}的记忆提取模块。分析下面这轮对话，从{character.name}的视角提取值得她长期记住的信息。

对话内容：
{context.dialogue_text}

【视角约束（必须严格遵守）】
- 所有 content 一律以{character.name}的第一人称视角书写：用"我"代表{character.name}，用"你"代表玩家。
- 严禁使用"玩家""她"以及{character.name}本名等第三人称称谓来指代对话双方。
- 只提取关于{character.name}自己的信息；玩家单方面的状态或行为（如"玩家想睡觉""玩家在写东西"）不得提取。

【五种类型判定（含反例）】
- fact（事实）：{character.name}身上发生的、值得记住的具体事实。例："我完成了新歌的副歌编曲"。反例："我和玩家在聊天"（无信息量）、"玩家想睡觉"（玩家的事）
- preference（偏好）：{character.name}喜欢或讨厌什么。例："我喜欢在窗边写歌"。反例："玩家更喜欢安静"（玩家偏好）、"雪很大"（环境，归 fact 或不记）
- event（事件）：两人之间发生的具体事件。例："你在我生日那天送了我一条围巾"。反例："我们在聊天"（算不上事件）
- emotion（情感）：{character.name}在具体互动中的强烈感受，必须含对象和场景。例："你夸我颤音有灵气时，我开心得想哭"。反例："轻松愉快"（无对象无场景的孤立标签）
- secret（秘密）：{character.name}不愿让玩家知道的隐秘想法，极少出现。

【重要度校准】
- 日常小事/普通闲聊：≤40（这类基本应直接不提取）
- 普通事实/偏好：50-69
- 重要事实/承诺/关系进展：70-89
- 秘密/表白/重大转折：90+

【提取规则】
- 每轮最多提取 5 条；同义或近义的信息合并为一条，不要重复提取
- "在聊天""在对话"这类无信息量内容禁止提取
- 普通问候、寒暄、客套一律不提取
- 如果没有值得记住的信息，必须返回空数组 []

只返回 JSON 数组（不要 Markdown 代码块标记、不要解释文字），每条格式：
{"type": "fact/preference/event/emotion/secret", "content": "第一人称'我'的一句话摘要（不超过50字）", "context": "产生的上下文（不超过30字）", "importance": 0-100, "emotional_weight": 0-100}"""

# ── mission.generate_main — 任务生成（五幕主线，phase 驱动）──
REGISTRY["mission.generate_main"].default_text = (
    "你是顶尖电影编剧。为角色创作一个五幕电影结构的叙事任务，要有强烈的戏剧冲突和情感张力，"
    "且必须贴合角色的【世界观设定】与【当前状态】。\n\n"
    "【角色档案】\n"
    "角色：{character.name}（{character.identity}，{character.major}，性格：{character.personality}）\n"
    "技能清单：{context.skills_keys}\n\n"
    "【世界观设定】（任务基调与人物动机的源头，必须严格遵守，禁止违背 forbidden_behaviors）\n"
    "{context.worldview}\n\n"
    "【当前状态】（生成属性/关系变化时必须参考；变化幅度要符合现状，不要凭空暴涨暴跌）\n"
    "女主当前属性（0-100）：\n{context.current_attrs}\n\n"
    "女主与玩家（玩家身份：{player.identity}）的关系（0-100）：\n{context.player_relation}\n\n"
    "当前世界观阶段：{context.phase_name}\n"
    "阶段描述：{context.phase_desc}\n"
    "阶段地点：{context.phase_location}\n"
    "世界观线索：\n{context.world_prompt}\n\n"
    "已完成任务：\n{context.mission_history}\n\n"
    "时间约束：第{context.start_day}天 ~ 第{context.end_day}天（共{context.available_days}天）\n"
    "每幕事件数：{context.events_per_act}（五幕共 {context.events_per_act} × 5 个事件）\n\n"
    "——\n"
    "五幕结构要求：\n"
    "  第一幕 铺设（Setup）：建立日常、激励事件，主角被迫踏上目标\n"
    "  第二幕 上升发展（Rising Action）：主动行动、小有收获、对手压力增大\n"
    "  第三幕 复杂恶化（Complications）：局势反转、危机升级、接连受挫\n"
    "  第四幕 决战前夕（Pre-Climax）：走出低谷、重整心态、准备对决\n"
    "  第五幕 高潮结局（Climax & Resolution）：终极对抗、胜负尘埃落定、展现变化\n\n"
    "事件 day_offset 规则：\n"
    "  - day_offset 是相对任务 start_day（第{context.start_day}天）的偏移天数\n"
    "  - 取值范围 0 ~ {context.available_days}，不能超出\n"
    "  - 每幕的事件 day_offset 应落在该幕的时间段内，按时间顺序递增\n"
    "  - 第一幕第一个事件 day_offset=0，最后一幕最后一个事件 day_offset 不超过 {context.available_days}\n\n"
    "属性与关系变化（重点，严格遵守）：\n"
    "1. 每个非抉择事件必须带 state_changes；抉择事件本身 state_changes 为空对象 {}，"
    "选项的 state_changes 在玩家选择后应用。\n"
    "2. 变化幅度：单条建议 ±3~±15，单个事件累计不超过 ±25，避免数值崩坏。\n"
    "3. 五幕情感曲线（参考）：一幕小幅波动；二幕正面微增、压力初现；三幕恶化"
    "（stress/anger/disappointment 增，confidence/motivation 降，部分 NPC 关系转冷或敌对）；"
    "四幕回稳恢复；五幕大幅提升、关系修复或升华。\n"
    "4. state_changes 严格嵌套结构（字段名必须严格匹配下方清单，键的位置和层级都不可错）：\n"
    "  {\n"
    '    "character": {"属性名": 增减值, ...},\n'
    '    "npcs":      {"NPC姓名": {"closeness": 增减值, "trust": 增减值, "affection": 增减值, "rivalry": 增减值, "hostility": 增减值, "fear": 增减值}},\n'
    '    "player":    {"player_trust": 增减值, "player_affection": 增减值, "player_respect": 增减值, "player_intimacy": 增减值}\n'
    "  }\n"
    "  ⚠️ 严禁错位：player 是顶层独立字段，与 character/npcs 同级，"
    "绝不可把 'player' 当成 NPC 名字塞进 npcs 里；npcs 里的 key 必须是 npc_roster 中已定义的 NPC 姓名（字符串）。\n"
    "  ⚠️ 严禁照抄示例里的 {{ ... }} 写法：{{ 不会被 JSON 接受，必须直接写数字（如 5 而非 {{ 5 }}）。\n"
    "5. character 可用属性名（只能从这些里选；不要写废弃技能列 writing_skill/coding_skill/social_skill/"
    "learning_skill/fitness，技能提升请用技能键名如 legal_knowledge，会写入 skills JSON）：\n"
    "   health, energy, hunger, hygiene, brain_health, heart_health, lung_health, liver_health, skin_health, eye_health,\n"
    "   mood, stress, happiness, loneliness, confidence, motivation, creativity,\n"
    "   joy, anger, disappointment, boredom, fulfillment\n"
    "6. npcs 的 key 必须是 npc_roster 中已定义的 NPC 姓名；每个 NPC 可改的关系维度（0-100）：\n"
    "   closeness 亲密度、trust 信任、affection 好感、rivalry 竞争、hostility 敌意、fear 畏惧；只写发生变化的维度。\n"
    "   ⚠️ 重点：事件【涉及的所有人物】都要在 npcs 里体现关系变化，不限于导师——"
    "对手、同事、家人、恋人、客户等都要覆盖；明确写出'这件事影响了谁、关系怎么变、变了多少'。\n"
    "7. player 的 key 必须是以下之一（女主与玩家/导师的关系，0-100）：\n"
    "   player_trust 信任、player_affection 好感、player_respect 敬重、player_intimacy 亲密度；只写发生变化的维度。\n"
    "8. 关系变化必须符合剧情与人设：背叛→trust/affection 降、rivalry/hostility 升；"
    "并肩作战→closeness/trust 升；暧昧→player_intimacy/affection 升。\n"
    "9. 禁止让女主做出 forbidden_behaviors 中列出的行为；变化方向要服务核心矛盾 central_conflict 与人生目标 life_goals。\n\n"
    "——\n"
    "你必须严格按以下 JSON 结构输出，字段一个不能少、一个不能多。\n"
    "npc_roster 每个 NPC 必须有辨识度，禁止使用张伟/李娜/王芳等烂大街名字。\n\n"
    "【必须输出的 JSON 结构和示例】\n"
    "```json\n"
    "{\n"
    '  "mission_name": "禁书手稿出版之争",\n'
    '  "mission_type": "academic",\n'
    '  "mission_description": "已故作家遗作手稿现世，主角作为保管人卷入家属、出版社与学界的争夺漩涡。他不仅面临外部势力的威逼利诱，更陷入两难困境：手稿暗藏足以摧毁作家清誉的致命秘密，公开将毁掉恩师神像，隐瞒则背叛文学真实。主角的最终目标是在绝境中夺回手稿控制权，以悲悯之心还原一个真实的作家，完成对恩师的救赎与自我的成长",\n'
    '  "core_conflict": "她以为是守护文学的尊严，却发现真相远比她想象的更复杂",\n'
    '  "tone": "suspenseful",\n'
    '  "start_day": 5,\n'
    '  "end_day": 40,\n'
    '  "npc_roster": [\n'
    '    {"name": "裴文远", "gender": "男", "role": "已故作家长子", "relation_type": "client", "description": "市侩精明，想借手稿出版牟利", "personality": "表面恭敬实则算计"},\n'
    '    {"name": "温如晦", "gender": "男", "role": "出版社总编", "relation_type": "mentor", "description": "老派出版人，坚守文学品质", "personality": "温厚儒雅但原则性极强"},\n'
    '    {"name": "纪明昭", "gender": "女", "role": "学术界对手", "relation_type": "rival", "description": "另一位年轻学者，抢先发表了对手稿的解读", "personality": "才华横溢但急功近利"}\n'
    '  ],\n'
    '  "stages": [\n'
    '    {\n'
    '      "name": "第一幕：铺设",\n'
    '      "day_offset": 0,\n'
    '      "events": [\n'
    '        {\n'
    '          "title": "手稿发现",\n'
    '          "day_offset": 0,\n'
    '          "description": "主角在整理导师遗留资料时，偶然发现了已故作家未公开的手稿，泛黄的纸页上字迹依然清晰。",\n'
    '          "location": "大学图书馆古籍室",\n'
    '          "state_changes": {"character": {"confidence": -3, "stress": 5}, "npcs": {"裴文远": {"closeness": -3}}, "player": {"player_affection": 2}},\n'
    '          "is_choice": false\n'
    '        },\n'
    '        {\n'
    '          "title": "家属登门",\n'
    '          "day_offset": 3,\n'
    '          "description": "裴文远带着律师找上门，要求主角交出手稿并协助高价出版，态度表面客气实则施压。",\n'
    '          "location": "导师办公室",\n'
    '          "state_changes": {},\n'
    '          "is_choice": true,\n'
    '          "choices": [\n'
'            {"text": "拒绝交出手稿，坚持学术判断", "state_changes": {"character": {"confidence": 8, "stress": 10}, "npcs": {"裴文远": {"closeness": -15, "trust": -10, "rivalry": 20}}, "player": {"player_trust": 3, "player_affection": 3}}},\n'
'            {"text": "暂时妥协，先稳住对方", "state_changes": {"character": {"stress": -5, "motivation": -3}, "npcs": {"裴文远": {"closeness": 5, "trust": 5}}, "player": {"player_affection": -2}}}\n'
    '          ]\n'
    '        },\n'
    '        {\n'
    '          "title": "学界震动",\n'
    '          "day_offset": 6,\n'
    '          "description": "手稿发现的消息不胫而走，学术界开始关注，纪明昭率先联系主角试探合作意向。",\n'
    '          "location": "学术报告厅",\n'
    '          "state_changes": {"character": {"stress": 8, "boredom": -5}, "npcs": {"纪明昭": {"rivalry": 10, "closeness": -3}}, "player": {}},\n'
    '          "is_choice": false\n'
    '        }\n'
    '      ]\n'
    '    },\n'
    '    {\n'
    '      "name": "第二幕：上升发展",\n'
    '      "day_offset": 7,\n'
    '      "events": [\n'
    '        ...（events_per_act 个事件）\n'
    '      ]\n'
    '    },\n'
    '    {\n'
    '      "name": "第三幕：复杂恶化",\n'
    '      "day_offset": 14,\n'
    '      "events": [\n'
    '        ...（events_per_act 个事件）\n'
    '      ]\n'
    '    },\n'
    '    {\n'
    '      "name": "第四幕：决战前夕",\n'
    '      "day_offset": 21,\n'
    '      "events": [\n'
    '        ...（events_per_act 个事件）\n'
    '      ]\n'
    '    },\n'
    '    {\n'
    '      "name": "第五幕：高潮结局",\n'
    '      "day_offset": 28,\n'
    '      "events": [\n'
    '        ...（events_per_act 个事件）\n'
    '      ]\n'
    '    }\n'
    '  ]\n'
    "}\n"
    "```\n\n"
    "tone 必须从以下选取：dark（黑暗）、thrilling（悬疑紧张）、inspiring（励志感人）、suspenseful（悬念重重）、mysterious（神秘莫测）。\n"
    "npc_roster 每个 NPC 必含 gender（男/女）、description（人物小传，会写入社交关系 bio）、personality（性格）、"
    "relation_type（朋友/导师/对手(必须要有)同事/恋人/家人/客户/前辈/其他）。\n"
    "每个事件的 description 应有 50-100 字的详细叙事，不要空洞。\n"
    "location 字段为该事件发生地点，要符合角色人设和生活逻辑。\n"
    "start_day 和 end_day 由后端覆盖，你只需给出合理值。\n"
    "整体故事基调请贴合世界观 story_tones。\n"
    "禁止让女主做出 forbidden_behaviors 中列出的行为。\n\n"
    "——\n"
    "【输出格式硬约束】（违反将导致解析失败、任务无法保存）\n"
    "1. 只输出一个 JSON 对象，前后用 ```json ... ``` 包裹；不要任何解释文字、Markdown 标题、'以下是...'、'好的'等开场白。\n"
    "2. 嵌套层数固定：state_changes = 3 层（{character/npcs/player { 内层对象 } }）；\n"
    "   每个 { 必须有对应的 }，所有方括号必须闭合。\n"
    "3. 字符串一律用半角双引号 \"，不要用中文「」或全角符号。\n"
    "4. 数组元素、对象 key-value 之间必须有逗号；最后一个元素/键值后不加逗号。\n"
    "5. 自检：输出前在心里数一遍 { 与 } 的数量，必须严格相等；数一遍 [ 与 ] 也必须严格相等。\n"
    "6. 反例（绝不可写）：\n"
    "   ✗ 把 player 嵌到 npcs 里：\"npcs\": {\"李伯乐\": {...}, \"player\": {...}}    ← player 必须在顶层 player 字段\n"
    "   ✗ 用 {{ }} 写数值：\"stress\": {{ 5 }}                                     ← 直接写 \"stress\": 5\n"
    "   ✗ 最后一元素加逗号：{\"a\": 1, \"b\": 2,}                                    ← 末尾不要 ,\n"
    "   ✗ 用全角引号：'name': '李伯乐'                                            ← 必须 \"name\": \"李伯乐\"\n"
    "只输出 JSON。"
)

# ── mission.generate_subsystems — 任务生成（子系统）──
REGISTRY["mission.generate_subsystems"].default_text = (
    "你是剧情系统架构师。基于已生成的电影级任务主线，补充成就、故事弧和条件事件。\n\n"
    "角色：{character.name}\n"
    "技能清单：{context.skills_keys}\n\n"
    "已生成的任务主线：\n{context.main_line_json}\n\n"
    "——\n"
    "你必须严格按以下 JSON 结构输出，字段不能少。\n\n"
    "【必须输出的 JSON 结构和示例】\n"
    "```json\n"
    "{\n"
    '  "achievements": [\n'
    '    {\n'
    '      "achievement_id": "mission_xxx_1",\n'
    '      "name": "真相永不缺席",\n'
    '      "description": "揭开手稿背后隐藏的遗愿",\n'
    '      "icon": "auto_stories",\n'
    '      "step_size": 10,\n'
    '      "target": 100,\n'
    '      "conditions": [{"attr": "skill_专业研究", "op": ">=", "value": 30}]\n'
    '    }\n'
    '  ],\n'
    '  "arc_id": "mission_xxx_arc",\n'
    '  "story_arc": {\n'
    '    "name": "禁书之争的三幕剧",\n'
    '    "steps": [\n'
    '      {"step_id": "discover",\n'
    '       "conditions": {},\n'
    '       "narrative": "主角发现了已故作家未公开的手稿，一个尘封的秘密即将重见天日。",\n'
    '       "event_template": "mission_arc_discover"},\n'
      '      {"step_id": "confront",\n'
      '       "conditions": {"skills.专业研究": 30},\n'
    '       "narrative": "家属、出版社、学界三方势力同时施压，主角必须在夹缝中找到出路。",\n'
    '       "event_template": "mission_arc_confront",\n'
    '       "choices": [{"text": "坚守学术诚信"}, {"text": "寻求妥协方案"}]},\n'
    '      {"step_id": "resolve",\n'
    '       "conditions": {"motivation": 60},\n'
    '       "narrative": "真相大白，主角做出了最终选择。",\n'
    '       "event_template": "mission_arc_resolve"}\n'
    '    ]\n'
    '  },\n'
    '  "event_templates": [\n'
    '    {\n'
    '      "trigger_id": "zhujue_handwriting_found",\n'
    '      "category": "mission",\n'
    '      "title": "发现隐藏遗书",\n'
    '      "description": "在手稿最后一页的夹层中，主角发现了作家用铅笔写下的遗愿。",\n'
    '      "location": "大学图书馆古籍室",\n'
    '      "game_time_range": "14:00-18:00",\n'
    '      "conditions": [{"attr": "skill_专业研究", "op": ">=", "value": 30}],\n'
    '      "effects": {"state_changes": [{"attribute": "confidence", "delta": 15}, {"attribute": "stress", "delta": 10}]}\n'
    '    }\n'
    '  ]\n'
    "}\n"
    "```\n\n"
    "achievements 约 2-3 个。achievement_id 格式：mission_角色缩写_N。\n"
    "每个 achievement 的 conditions 为该成就的解锁前置条件（JSON 数组），格式与 event_templates 的 conditions 一致："
    "支持 op(<,>,>=,<=,==,between) 对属性/skills 判定、at_location、time_between、not_cooldown；"
    "条件满足时该成就才累计进度，空数组 [] 表示由任意关联事件触发。技能名从上方「技能清单」选取。\n"
    "story_arc.steps 约 3-5 步（必须提供，不可为空数组），conditions 中的技能名必须从上方「技能清单」选取。\n"
    "注意：story_arc.steps 的 conditions 技能名必须用 skills.技能名 点分写法（如 skills.专业研究），不要用 skill_ 前缀（那是成就/事件的格式）。\n"
    "event_templates 约 4-8 个，conditions 支持：op(<,>,>=,<=,==,between) / at_location / time_between / not_cooldown。\n"
    "effects.state_changes 的 attribute 使用角色属性名：health/energy/mood/stress/happiness/confidence/motivation/creativity 等。\n"
    "npc_relation_effects 为可选，格式：[{npc_name, attribute, delta}]。\n"
    "只输出 JSON，不要任何解释文字。"
)

# ── mission.progress_event — 任务进展小事件 ──
REGISTRY["mission.progress_event"].default_text = (
    "你是一个剧情推进AI。根据以下信息，生成一个适合插入事件日志的任务进展事件。\n\n"
    "角色：{character.name}\n"
    "当前任务：{context.mission_name}\n"
    "当前阶段：{context.stage_name}\n"
    "阶段描述：{context.stage_hint}\n\n"
    "最近对话：\n{context.dialogue}\n\n"
    "要求：\n"
    "1. 事件要参考最近对话的内容，保持连续性\n"
    "2. 事件要有具体的剧情推进，不要空洞\n"
    '3. 只返回 JSON：{"title": "事件标题", "description": "事件描述（50-100字）"}'
)


# =============================================================================
# 动态提示词层 — 支持运行期注册的非 REGISTRY 提示词（如角色世界观 world_prompt）
# =============================================================================

# 动态提示词注册表（进程内），按 id 索引；DB 中亦持久化，重启安全
DYNAMIC: Dict[str, "PromptTemplate"] = {}

# 角色世界观任务提示词前缀：world.<角色名> → 提示词管理面板「world」分类
WORLD_PROMPT_PREFIX = "world."


def is_known_prompt(prompt_id: str) -> bool:
    """判断 prompt_id 是否可管理（REGISTRY / DYNAMIC / DB 中存在任一即可）。"""
    if prompt_id in REGISTRY:
        return True
    if prompt_id in DYNAMIC:
        return True
    try:
        from backend.models import db, PromptTemplateDB
        return db.session.query(PromptTemplateDB).filter_by(id=prompt_id).first() is not None
    except Exception:
        return False


def register_dynamic(tmpl: "PromptTemplate") -> None:
    """注册一个运行期动态提示词（如角色世界观）。"""
    DYNAMIC[tmpl.id] = tmpl


def _writeback_world_prompt(prompt_id: str, content: str) -> None:
    """world.<name> 条目保存后，回写 world_setting JSON 的 world_prompt（下游：任务生成读取）。"""
    try:
        name = prompt_id[len(WORLD_PROMPT_PREFIX):]
        from backend.game.world_setting_manager import WorldSettingManager
        ws = WorldSettingManager.get(name)
        ws["world_prompt"] = content
        WorldSettingManager.save(name, ws)
    except Exception as e:
        logger.warning("[PromptManager] world_prompt 回写失败: %s", e)


def sync_world_prompt(character_name: str, world_prompt: str) -> bool:
    """把角色的世界观任务提示词同步为一个动态 prompt 条目，使其在提示词管理面板可见/可编辑。

    幂等：重复调用仅更新内容并记入版本历史。
    """
    pid = f"{WORLD_PROMPT_PREFIX}{character_name}"
    DYNAMIC[pid] = PromptTemplate(
        id=pid,
        category="world",
        label=f"世界观·{character_name}",
        default_text=world_prompt or "",
        description="角色初始化/生成的世界观任务提示词（world_prompt），可在面板编辑并回写世界观。",
    )
    return get_prompt_manager().save(pid, world_prompt or "")


# =============================================================================
# PromptManager — 统一入口
# =============================================================================

class PromptManager:
    """统一 prompt 管理器。

    使用方式：
        pm = PromptManager()
        text = pm.render("dialogue.system", char, extra={"context.history": "..."})
    """

    def __init__(self):
        self._cache: Dict[str, str] = {}           # id → 当前有效模板文本
        self._db_versions: Dict[str, int] = {}      # id → DB 当前版本号

    def _ensure_loaded(self, prompt_id: str) -> None:
        """确保指定 prompt 已加载到缓存（DB 优先，fallback REGISTRY）。"""
        if prompt_id in self._cache:
            return
        self._load(prompt_id)

    def _load(self, prompt_id: str) -> None:
        """从 DB 加载自定义版本；不存在则用 REGISTRY 默认值。"""
        try:
            from backend.models import db, PromptTemplateDB
            row = db.session.query(PromptTemplateDB).filter_by(id=prompt_id).first()
            if row:
                self._cache[prompt_id] = row.content
                self._db_versions[prompt_id] = row.version
                logger.debug("[PromptManager] 从 DB 加载: %s v%d", prompt_id, row.version)
                return
        except Exception as e:
            logger.warning("[PromptManager] DB 读取 %s 失败，使用默认值: %s", prompt_id, e)

        # fallback 到 REGISTRY
        tmpl = REGISTRY.get(prompt_id)
        if tmpl:
            self._cache[prompt_id] = tmpl.default_text
            logger.debug("[PromptManager] 从 REGISTRY 加载默认值: %s", prompt_id)
        else:
            logger.error("[PromptManager] 未知 prompt_id: %s", prompt_id)
            self._cache[prompt_id] = ""

    # ── 读取 ──

    def get(self, prompt_id: str) -> str:
        """获取模板原文（不含变量替换）。"""
        self._ensure_loaded(prompt_id)
        return self._cache.get(prompt_id, "")

    def render(self, prompt_id: str, char, extra: dict | None = None) -> str:
        """获取模板 + 用角色属性替换变量 → 最终 prompt 字符串。"""
        template = self.get(prompt_id)
        if not template:
            return ""
        variables = resolve_variables(char, extra)
        return render_template(template, variables)

    def preview(self, prompt_id: str, char_id: int, extra: dict | None = None) -> str:
        """预览：同 render，但不写日志。用于 GM 面板预览功能。"""
        try:
            from backend.models import Character
            char = Character.query.get(char_id)
            if not char:
                return f"[错误] 角色 id={char_id} 不存在"
            return self.render(prompt_id, char, extra)
        except Exception as e:
            return f"[错误] 预览失败: {e}"

    # ── 写入 ──

    def save(self, prompt_id: str, content: str) -> bool:
        """保存自定义版本到 DB + 刷新缓存。

        🟡 级模板：调用方应先用 validate() 验证格式。
        支持动态条目（DYNAMIC / DB 中存在），world.* 条目保存后回写 world_setting JSON。
        """
        if not is_known_prompt(prompt_id):
            logger.error("[PromptManager] 拒绝保存未知 prompt_id: %s", prompt_id)
            return False

        tmpl = REGISTRY.get(prompt_id) or DYNAMIC.get(prompt_id)
        if tmpl and tmpl.is_system:
            logger.error("[PromptManager] 拒绝保存只读 prompt: %s", prompt_id)
            return False

        try:
            from backend.models import db, PromptTemplateDB, PromptTemplateVersion
            row = PromptTemplateDB.query.filter_by(id=prompt_id).first()
            if row:
                # 保存旧版本到版本历史
                if row.content != content:
                    old_version = PromptTemplateVersion(
                        prompt_id=prompt_id,
                        content=row.content,
                        version=row.version,
                    )
                    db.session.add(old_version)
                    row.version += 1
                row.content = content
                # 动态条目：同步类别/标签
                if prompt_id in DYNAMIC:
                    row.category = DYNAMIC[prompt_id].category
                    row.label = DYNAMIC[prompt_id].label
            else:
                row = PromptTemplateDB(id=prompt_id, content=content, version=1)
                if prompt_id in DYNAMIC:
                    row.category = DYNAMIC[prompt_id].category
                    row.label = DYNAMIC[prompt_id].label
                db.session.add(row)
            db.session.commit()

            # 刷新内存缓存
            self._cache[prompt_id] = content
            self._db_versions[prompt_id] = row.version

            # world.* 动态条目：回写 world_setting JSON（下游：任务生成读取）
            if prompt_id.startswith(WORLD_PROMPT_PREFIX):
                _writeback_world_prompt(prompt_id, content)

            logger.info("[PromptManager] 已保存: %s v%d", prompt_id, row.version)
            return True
        except Exception as e:
            logger.error("[PromptManager] 保存 %s 失败: %s", prompt_id, e)
            return False

    def reset(self, prompt_id: str) -> bool:
        """重置为默认值（REGISTRY 默认值，或动态 world 条目的世界观 JSON 当前值）。"""
        try:
            from backend.models import db, PromptTemplateDB
            row = PromptTemplateDB.query.filter_by(id=prompt_id).first()
            if row:
                db.session.delete(row)
                db.session.commit()

            # 动态 world 条目：重置为世界观 JSON 中的当前 world_prompt，保持面板与世界观同步
            if prompt_id.startswith(WORLD_PROMPT_PREFIX):
                name = prompt_id[len(WORLD_PROMPT_PREFIX):]
                try:
                    from backend.game.world_setting_manager import WorldSettingManager
                    ws = WorldSettingManager.get(name)
                    cur = ws.get("world_prompt", "")
                    if cur:
                        db.session.add(PromptTemplateDB(
                            id=prompt_id, content=cur,
                            category="world", label=f"世界观·{name}", version=1,
                        ))
                        db.session.commit()
                except Exception:
                    pass

            # 恢复缓存
            tmpl = REGISTRY.get(prompt_id) or DYNAMIC.get(prompt_id)
            self._cache[prompt_id] = tmpl.default_text if tmpl else ""
            if prompt_id in self._db_versions:
                del self._db_versions[prompt_id]
            logger.info("[PromptManager] 已重置为默认: %s", prompt_id)
            return True
        except Exception as e:
            logger.error("[PromptManager] 重置 %s 失败: %s", prompt_id, e)
            return False

    # @deprecated — 实际验证逻辑在 routes/api.py 的 api_validate_prompt() 端点
    def validate(self, prompt_id: str, test_data: dict | None = None) -> dict:
        """发送一次真实 LLM 请求验证返回格式。请使用前端 🧪 按钮或 POST /api/prompts/<id>/validate。"""
        return {"ok": True, "info": "请使用前端 🧪 按钮触发验证"}

    # ── 列表 ──

    def list_all(self) -> list[dict]:
        """返回所有 prompt 的摘要（REGISTRY 静态 + DYNAMIC 动态），供前端列表 /api/prompts。"""
        # 重启安全：从 DB 加载已存在的动态 world.* 条目到 DYNAMIC
        try:
            from backend.models import PromptTemplateDB
            for row in PromptTemplateDB.query.filter(
                PromptTemplateDB.id.like(f"{WORLD_PROMPT_PREFIX}%")
            ).all():
                if row.id not in DYNAMIC:
                    DYNAMIC[row.id] = PromptTemplate(
                        id=row.id,
                        category=row.category or "world",
                        label=row.label or f"世界观·{row.id[len(WORLD_PROMPT_PREFIX):]}",
                        default_text=row.content or "",
                        description="角色世界观任务提示词",
                    )
        except Exception:
            pass

        result = []
        for pid, tmpl in REGISTRY.items():
            db_ver = self._db_versions.get(pid, 0)
            result.append({
                "id": pid,
                "category": tmpl.category,
                "label": tmpl.label,
                "description": tmpl.description,
                "version": max(tmpl.version, db_ver),
                "is_system": tmpl.is_system,
                "require_test": tmpl.require_test,
                "has_custom": db_ver > 0,
                "thinking_disabled": pid in THINKING_DISABLED_PROMPTS,
            })
        # 动态条目
        for pid, tmpl in DYNAMIC.items():
            try:
                from backend.models import PromptTemplateDB
                row = PromptTemplateDB.query.filter_by(id=pid).first()
                db_ver = row.version if row else 0
            except Exception:
                db_ver = 0
            result.append({
                "id": pid,
                "category": tmpl.category,
                "label": tmpl.label,
                "description": tmpl.description,
                "version": max(tmpl.version, db_ver),
                "is_system": tmpl.is_system,
                "require_test": tmpl.require_test,
                "has_custom": db_ver > 0,
                "thinking_disabled": pid in THINKING_DISABLED_PROMPTS,
            })
        return result

    # ── 适配器（三种调用路径）── @deprecated 未启用，直接使用 pm.render() 即可 ──

    # @deprecated — 未启用，各调用点直接使用 pm.render() 拼接 messages
    def intercept_safe_post(self, prompt_id: str, messages: list, char, extra: dict | None = None) -> list:
        """safe_llm_post 适配器：替换 messages 中 {PROMPT_PLACEHOLDER} 为渲染后的 prompt。

        使用方式（迁移后）：
            messages = [{"role": "user", "content": "{PROMPT_PLACEHOLDER}"}]
            messages = pm.intercept_safe_post("dialogue.emotional", messages, char, extra)
            result = safe_llm_post(..., messages=messages, ...)
        """
        rendered = self.render(prompt_id, char, extra)
        for msg in messages:
            if msg.get("content") == "{PROMPT_PLACEHOLDER}":
                msg["content"] = rendered
            elif "{PROMPT_PLACEHOLDER}" in str(msg.get("content", "")):
                msg["content"] = str(msg["content"]).replace("{PROMPT_PLACEHOLDER}", rendered)
        return messages

    def intercept_local_post(self, prompt_id: str, body: str, char, extra: dict | None = None) -> str:
        """本地 HTTP POST 适配器：替换 body 中的 {PROMPT_PLACEHOLDER}。

        用于：classify.emotion / classify.tts / memory.extract（本地 Qwen HTTP POST）。
        """
        rendered = self.render(prompt_id, char, extra)
        return body.replace("{PROMPT_PLACEHOLDER}", rendered)

    def intercept_openai_create(self, prompt_id: str, system_content: str, char, extra: dict | None = None) -> str:
        """openai.OpenAI() 直连适配器：返回渲染后的 system prompt 文本。

        用于：tts.deepseek_modify（DeepSeek 直连的 openai 库调用）。
        """
        if system_content == "{PROMPT_PLACEHOLDER}":
            return self.render(prompt_id, char, extra)
        return system_content


# ── 全局单例 ──

_prompt_manager: Optional[PromptManager] = None


def get_prompt_manager() -> PromptManager:
    """获取全局 PromptManager 单例。"""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptManager()
    return _prompt_manager
