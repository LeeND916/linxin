"""
状态-事件关联约束系统

让角色的各类状态与事件相互关联，产生符合生物学逻辑和生活常识的行为限制。
所有约束逻辑同时支持 LLM 驱动和降级模拟两种模式。
"""
import json

from backend.models import Character, Friend


# ============================
# 1. 物理状态 -> 活动约束
# ============================

PHYSICAL_CONSTRAINTS = {
    "energy": {
        0:  {"max": 15, "blocked_activities": ["workout", "code_practice", "write_novel", "attend_class", "travel_explore"],
             "forced_activities": ["relax_home"],
             "warning": "你精疲力竭，已经无法站立。必须立即休息或睡觉，任何活动都会损害健康。"},
        15: {"max": 25, "blocked_activities": ["workout", "travel_explore"],
             "warning": "你非常疲惫，不适合健身和远行。建议休息或吃点东西补充能量。"},
        25: {"max": 40, "blocked_activities": ["workout"],
             "warning": "你有些累了，高强度锻炼可能会让你更加疲惫。"},
    },
    # hunger 为高坏属性：数值越高表示越饿（口径 B），与 energy/health 相反。
    "hunger": {
        75: {"max": 100, "blocked_activities": ["workout", "code_practice", "write_novel", "attend_class", "travel_explore"],
             "forced_activities": ["eat"],
             "warning": "你饿得头晕眼花，必须立刻吃东西，否则会晕倒。"},
        50: {"max": 75, "blocked_activities": ["workout", "travel_explore"],
             "warning": "你很饿，无法集中精力。建议先去餐厅或咖啡馆吃点东西。"},
    },
    "health": {
        0:  {"max": 30, "blocked_activities": ["workout", "travel_explore", "attend_class", "code_practice"],
             "warning": "你生病了，身体状况很差。需要去医院或在家休息，强行活动会加重病情。"},
        30: {"max": 50, "blocked_activities": ["workout"],
             "warning": "你身体不太舒服，不适合剧烈运动。"},
    },
    "hygiene": {
        0: {"max": 20, "blocked_activities": ["socialize", "attend_class", "shopping"],
            "warning": "你已经很久没有洗澡了，不好意思出门见人。需要先回宿舍洗漱。"},
    },
    "brain_health": {
        0: {"max": 15, "blocked_activities": ["code_practice", "write_novel", "study_library", "attend_class"],
            "warning": "你用脑过度，头痛欲裂。强行学习或编程可能会损伤大脑。需要休息。"},
    },
    "eye_health": {
        0: {"max": 12, "blocked_activities": ["code_practice", "write_novel", "study_library"],
            "warning": "你的眼睛干涩疼痛，无法长时间盯着屏幕或书本。需要闭眼休息。"},
    },
}


# ============================
# 2. 心理状态 -> 行为约束
# ============================

MENTAL_CONSTRAINTS = {
    "stress": {
        80: {"max": 100, "dialogue_tone": "irritable", "activity_penalty": 0.5,
             "warning": "你压力很大，情绪很不稳定，可能不太想聊天。"},
        90: {"max": 100, "dialogue_tone": "breakdown",
            "blocked_activities": ["code_practice", "study_library", "attend_class"],
            "warning": "你濒临崩溃边缘！无法进行任何脑力活动。你需要放松、倾诉或者一个人静静。"},
        # breakdown 是否真触发 / 是否进「生命安全级别」块并写"濒临崩溃"，由统一函数
        # _is_real_breakdown 决定：只有 stress>=90 且 (mood<=20 或 joy<=15) 才算真崩溃；
        # 单纯高压力只算"烦躁"(warning 级、温和文案)，避免模型一聊就崩、也避免误标生命安全。
        # get_all_active_warnings 与 check_dialogue_constraints 共用该函数，保证提示词侧
        # 与对话语气侧对"崩不崩"的判断完全一致。
    },
    "loneliness": {
        70: {"max": 100, "behavior": "seeks_social", "auto_message_chance_boost": 0.3,
             "warning": "你感到孤独，会主动找人聊天。但你可能情绪低落，需要对方的陪伴。"},
        90: {"max": 100, "dialogue_tone": "desperate",
             "warning": "你极度孤独，心情糟透了。如果对方不理你，你可能会陷入抑郁。"},
    },
    "mood": {
        0:  {"max": 20, "dialogue_tone": "depressed", "refuse_chat_chance": 0.3,
             "warning": "你心情非常低落，可能会不想说话。即使回复也会很简短冷淡。"},
        20: {"max": 40, "dialogue_tone": "sad",
             "warning": "你心情不太好，回复可能会比较消极。"},
    },
    "confidence": {
        0: {"max": 20, "blocked_activities": ["attend_class", "socialize"],
            "warning": "你极度缺乏自信，不敢在公众场合出现，也不想见朋友。"},
    },
    "motivation": {
        0: {"max": 15, "blocked_activities": ["code_practice", "write_novel", "study_library", "attend_class"],
            "warning": "你完全失去动力，什么都不想做。需要对方的鼓励才能重新振作。"},
        15: {"max": 30, "activity_penalty": 0.5,
             "warning": "你缺乏动力，学习或工作的效率会大打折扣。需要对方鼓励一下。"},
    },
    "creativity": {
        0: {"max": 15, "blocked_activities": ["write_novel"],
            "warning": "你灵感枯竭，坐在书桌前一个字也写不出来。换个环境或许能激发灵感。"},
    },
    # ── 新增情感轴（anger/happiness/joy/boredom/disappointment/fulfillment）──
    # 这些值已存在于 Character 模型，但此前未接入约束系统，导致约束对其完全失效。
    "anger": {
        70: {"max": 100, "dialogue_tone": "irritable", "refuse_chat_chance": 0.3,
             "warning": "你现在怒气值很高，说话容易带刺。最好先别让对方惹你，等自己消气。"},
        85: {"max": 100, "dialogue_tone": "irritable", "refuse_chat_chance": 0.5,
             "blocked_activities": ["socialize", "code_practice", "attend_class"],
             "warning": "你气得不想理任何人，更不想社交或学习。让自己待会儿，等火气下去。"},
    },
    "happiness": {
        0:  {"max": 20, "dialogue_tone": "depressed", "refuse_chat_chance": 0.25,
             "warning": "你心里空落落的，提不起精神，可能不太想说话。"},
        20: {"max": 40, "dialogue_tone": "sad",
             "warning": "你幸福感不高，情绪有些低落，回复可能偏消极。"},
    },
    "joy": {
        0:  {"max": 20, "dialogue_tone": "sad",
             "warning": "你最近没什么开心事，情绪淡淡的，提不起劲。"},
        20: {"max": 40, "dialogue_tone": "sad",
             "warning": "你今天不太开心，话可能比较少。"},
    },
    "boredom": {
        70: {"max": 100, "behavior": "seeks_novelty", "auto_message_chance_boost": 0.3,
             "warning": "你无聊得发慌，特别想找点有意思的事做，或者找人聊聊天打发时间。"},
        85: {"max": 100, "auto_message_chance_boost": 0.45,
             "warning": "你简直无聊到长草，迫切想要点新鲜刺激，或是有人陪你说话。"},
    },
    "disappointment": {
        70: {"max": 100, "dialogue_tone": "sad", "refuse_chat_chance": 0.15,
             "warning": "你心里有些失望，话里可能带着失落感。"},
        85: {"max": 100, "dialogue_tone": "depressed", "refuse_chat_chance": 0.3,
             "warning": "你非常失望，整个人都闷闷不乐，不太想搭理人。"},
    },
    "fulfillment": {
        0:  {"max": 20, "dialogue_tone": "sad",
             "warning": "你觉得生活没什么充实感，有点空虚，提不起劲。"},
        20: {"max": 40, "dialogue_tone": "sad",
             "warning": "你最近没什么成就感，状态有点蔫。"},
    },
}


# ============================
# 3. 关系状态 -> 互动约束
# ============================
#
# 【已废弃 / DEPRECATED】RELATIONSHIP_CONSTRAINTS 的语义前提是：
#   「两人关系曾经很好，现在因故下跌」——所以文案才写成
#   「你对他很生气…需要道歉和弥补」「感情出现了裂痕，需要修复」。
# 只有在这种「由好转坏」的场景下才应该启用本表。
#
# 实际运行中它被误用为「关系值低」的通用判定：第 0 天刚开局、
# 关系尚未发展的角色（如叶知秋，baseline 仅 5~6 分）也会被判定为
# 「生气/不信任/疏远」，与【关系阶梯 tier 0 陌生人期】的
# 「你们刚刚认识，完全不熟」直接矛盾；且 refuse_chat_chance 0.7
# 还会与 tier 的 refuse_boost 0.25 叠加成 0.95，导致几乎必然拒绝聊天。
#
# 因此本表不再作为「当前关系的真实状态」来源使用：
#   - 关系的亲疏与形式规矩：以 dialogue.TIER_CONFIG 关系阶梯为准；
#   - 刚认识阶段的温和提示：以本文件 get_all_active_warnings 中
#     [关系状态]（relationship_state，P6 刚认识保护）为准。
# 保留本表仅供后续按「由高值跌落」语义重构时参考，新逻辑请勿再引用。
RELATIONSHIP_CONSTRAINTS = {
    "player_affection": {
        0:  {"max": 20, "dialogue_tone": "angry_cold", "refuse_chat_chance": 0.7,
             "warning": "你对他很生气，不想和他说话。他可能做了什么让你伤心的事。需要道歉和弥补。"},
        20: {"max": 40, "dialogue_tone": "distant", "refuse_chat_chance": 0.3,
             "warning": "你对他有些冷淡，回复会变得敷衍。你们的感情出现了裂痕，需要修复。"},
        40: {"max": 55, "dialogue_tone": "polite_but_guarded",
             "warning": "你对他的态度有所保留，不像以前那么亲密了。"},
    },
    "player_trust": {
        0:  {"max": 25, "dialogue_tone": "suspicious", "refuse_chat_chance": 0.4,
             "warning": "你不信任他了，他说的话你都会怀疑。你不会分享内心的想法。"},
        25: {"max": 45, "dialogue_tone": "cautious",
             "warning": "你对他还存有戒心，不会完全敞开心扉。"},
    },
    "player_intimacy": {
        0: {"max": 30, "dialogue_tone": "distant",
            "warning": "你和他之间的关系变得疏远。你不把他当成亲密的人。"},
    },
}


# 朋友关系约束
FRIEND_CONSTRAINTS = {
    "closeness": {
        0:  {"max": 15, "blocked_activities": ["socialize"],
             "warning": "你和{friend_name}的关系已经破裂，无法一起活动。需要主动修复关系。"},
        15: {"max": 30, "blocked_activities": ["socialize"],
             "warning": "你和{friend_name}关系紧张，勉强聚会会很不自在。建议先改善关系。"},
    },
}


# ============================
# 约束涉及的角色状态字段（集中登记，新增状态量只改这里）
# ============================
_PHYSICAL_ATTRS = ("energy", "hunger", "health", "hygiene", "brain_health", "eye_health")
_MENTAL_ATTRS = ("stress", "mood", "loneliness", "confidence", "motivation", "creativity",
                 "anger", "happiness", "joy", "boredom", "disappointment", "fulfillment")
_RELATIONSHIP_ATTRS = ("player_affection", "player_trust", "player_intimacy")
# 全部约束字段全集
_CONSTRAINT_ATTRS = _PHYSICAL_ATTRS + _MENTAL_ATTRS + _RELATIONSHIP_ATTRS


def _state_values(character, attrs=_CONSTRAINT_ATTRS):
    """从角色对象读取给定状态字段的当前值，返回 {attr: value}。

    统一替代各函数内重复的 attr_map 字面量，避免新增字段时漏改某处。
    """
    return {a: getattr(character, a, 0) for a in attrs}


# ============================
# 约束检查函数
# ============================

def _get_constraint_for_attr(constraint_dict, attr_name, current_value):
    """根据属性值和约束字典，找到匹配的约束"""
    thresholds = constraint_dict.get(attr_name, {})
    for threshold_val, constraint in sorted(thresholds.items(), reverse=True):
        if threshold_val <= current_value <= constraint["max"]:
            return constraint
    return None


def check_activity_constraints(character, activity_id, activity_name=None):
    """
    检查角色是否可以执行某项活动。
    返回 (can_do: bool, warning: str, forced_activities: list, blocked: bool)
    """
    if character is None:
        return True, "", [], False

    attr_map = _state_values(character)

    blocked = False
    all_warnings = []
    all_forced = []

    # 检查物理约束
    for attr_name in _PHYSICAL_ATTRS:
        constraint = _get_constraint_for_attr(PHYSICAL_CONSTRAINTS, attr_name, attr_map[attr_name])
        if constraint:
            if activity_id in constraint.get("blocked_activities", []):
                blocked = True
                all_warnings.append(constraint["warning"].format(name=character.name or "角色"))
            forced = constraint.get("forced_activities", [])
            all_forced.extend(forced)

    # 检查心理约束（仅取"会影响活动执行"的子集：stress/confidence/motivation/creativity/anger）
    for attr_name in ("stress", "confidence", "motivation", "creativity", "anger"):
        constraint = _get_constraint_for_attr(MENTAL_CONSTRAINTS, attr_name, attr_map[attr_name])
        if constraint:
            if activity_id in constraint.get("blocked_activities", []):
                blocked = True
                all_warnings.append(constraint["warning"].format(name=character.name or "角色"))

    # 如果活动被 blocked，不允许执行
    if blocked:
        # 但如果活动在 forced 列表中且是生存必需（eat/relax_home），可以豁免
        if activity_id in all_forced:
            return True, "\n".join(all_warnings), list(set(all_forced)), False
        return False, "\n".join(all_warnings), list(set(all_forced)), True

    can_do = True
    return can_do, "\n".join(all_warnings) if all_warnings else "", list(set(all_forced)), blocked


# 语气优先级：数值越高越优先（心理危机 > 关系紧张 > 一般情绪）。
# 用于多约束同时激活时决议最终语气，避免低优先级约束吞掉高优先级（如关系紧张吞掉崩溃）。
_TONE_PRIORITY = {
    "breakdown": 100,            # 心理崩溃（最紧急）
    "desperate": 90,             # 极度孤独
    "depressed": 80,             # 极度低落
    "angry_cold": 70,            # 关系愤怒冷淡
    "sad": 60,
    "irritable": 55,             # 烦躁
    "suspicious": 50,            # 不信任
    "distant": 45,               # 疏远
    "cautious": 40,              # 戒备
    "polite_but_guarded": 35,    # 客气但有保留
    "normal": 0,
}

# 语气 → 中文详细描述（注入提示词用，不再暴露内部英文枚举如 breakdown）。
# 描述只讲"怎么说话"，活动限制交给代码拦截，不在对话里列清单。
_TONE_DESCRIPTIONS = {
    "normal": "状态正常，语气自然、平和，像平常一样聊天。",
    "irritable": "你压力很大、情绪烦躁，说话可能带刺、简短、没耐心，但底子不坏，别故意找茬。",
    "sad": "你情绪低落，提不起劲，说话慢、少、带点无力感，可以叹气、可以沉默。",
    "depressed": "你情绪极度低落，几乎没有精神，说话很轻、很短，不想多谈，可以回避话题。",
    "desperate": "你感到极度孤独、快要撑不住了，非常渴望有人陪、有人听你说话，说话会带着求助和依赖。",
    "breakdown": "你正处于情绪崩溃边缘，说话会断断续续、声音发抖、字句简短。可以示弱、可以求助、可以沉默，但不要强撑、不要长篇大论、不要幽默或显得精力充沛。",
    "angry_cold": "你对对方带着怨气和冷淡，说话疏离、不爱搭理，回应简短甚至不接话，但别直接骂人。",
    "distant": "你有意保持距离，态度客气但疏远，不想太亲近，聊到深入话题会绕开。",
    "suspicious": "你不信任对方，说话带防备、试探，不愿意轻易交底，会留意对方话里的意图。",
    "cautious": "你有些戒备，说话小心、留有余地，不会一下子全信，先观望再决定敞开心扉。",
    "polite_but_guarded": "你表面上客气有礼，但内心有保留，不轻易交心，礼貌中带着距离感。",
    "professional": "你以专业身份与对方交流，语气温和、耐心、先共情，不因私人关系冷淡或敷衍，保持职业底线。",
}


def _is_real_breakdown(character):
    """判定 stress 是否构成「真崩溃」（值得打"生命安全/崩溃"标签）。

    单纯高压力(stress>=90)不算——必须压力高 且 (心情极差 mood<=20 或 开心极低 joy<=15)
    才算真崩溃。该判定被 get_all_active_warnings(决定提示词是否标 critical/生命安全级)
    与 check_dialogue_constraints(决定对话语气是否 breakdown) 共用，确保两边规则一致，
    消除"提示词喊崩溃、语气却说别崩"的自相矛盾（漏洞5）。
    """
    if character is None:
        return False
    return (getattr(character, "stress", 0) >= 90
            and (getattr(character, "mood", 100) <= 20
                 or getattr(character, "joy", 100) <= 15))


def check_dialogue_constraints(character, user_message=''):
    """
    计算角色此刻"不想聊天的倾向概率"及对话语气。
    返回 (refuse_chance: float, tone: str, warning: str)

    注意：是否真正拒绝聊天的概率判定不在本函数内掷骰子——由调用方
    process_dialogue 在叠加关系阶梯调整(tier_refuse_adj)后统一掷一次，
    以保证概率语义单一、可复现，且阶梯调整能真正生效（避免函数内先掷、
    调用方再叠加两层概率导致语义混杂且结果不可复现）。
    """
    if character is None:
        return 0.0, "normal", ""

    refuse_chance = 0.0
    all_warnings = []
    # 收集所有约束建议的语气，最后按优先级决议；不再用 tone=='normal' 短路，
    # 否则关系约束先赋值后会吞掉心理约束的 breakdown 语气
    candidate_tones = []

    # ── 关系约束：已移除（原遍历 RELATIONSHIP_CONSTRAINTS 取 refuse/tone/warning）──
    # 移除原因：该表语义是「关系曾好、现在下跌」，却被当「关系值低」的通用判定使用，
    # 导致开局未发展的角色（如叶知秋 baseline 5~6 分）被判成「生气/不信任/疏远」，
    # 与关系阶梯 tier0「刚认识不熟」自相矛盾；且 refuse_chat_chance 0.7 会与
    # tier 的 refuse_boost 0.25 叠加成 0.95，使角色几乎必然拒绝聊天。
    # 现由以下两者接管：
    #   1) 拒绝概率 —— 只保留本函数的物理/心理约束，叠加量由 dialogue.get_tier_refuse_adjustment 提供；
    #   2) 关系亲疏与相处规矩 —— 由 dialogue.TIER_CONFIG 关系阶梯（persona_note/字数/分享深度）统一负责。
    # 详见 RELATIONSHIP_CONSTRAINTS 定义处的废弃说明。

    # 检查心理约束（含 anger/happiness/joy/boredom/disappointment/fulfillment 等新情感轴）
    for attr_name in _MENTAL_ATTRS:
        val = getattr(character, attr_name, 0)
        constraint = _get_constraint_for_attr(MENTAL_CONSTRAINTS, attr_name, val)
        if constraint:
            if constraint.get("dialogue_tone"):
                candidate_tones.append(constraint["dialogue_tone"])
            refuse_chance = max(refuse_chance, constraint.get("refuse_chat_chance", 0))
            all_warnings.append(constraint["warning"].format(name=character.name or "角色"))

    # 按优先级决议最终语气（收集所有候选后取最高优先级者）
    tone = "normal"
    if candidate_tones:
        tone = max(candidate_tones, key=lambda t: _TONE_PRIORITY.get(t, 0))

    # 当孤独感很高时，降低拒绝概率（渴望交流）
    if character.loneliness > 70:
        refuse_chance = max(0, refuse_chance - 0.4)

    # 无聊时反而更想找人说话（降低拒绝概率，渴望新鲜刺激/陪伴）
    if getattr(character, "boredom", 0) > 70:
        refuse_chance = max(0, refuse_chance - 0.3)

    # breakdown 判定统一走 _is_real_breakdown：只有"真崩溃"才保留 breakdown 语气，
    # 否则降级为 irritable 并替换为温和文案。与 get_all_active_warnings 共用同一规则（漏洞5）。
    if tone == "breakdown" and not _is_real_breakdown(character):
        tone = "irritable"  # 降级为烦躁，不崩溃
        # 替换掉相关 warning
        all_warnings = [w for w in all_warnings if "崩溃" not in w]
        all_warnings.append("你压力很大，情绪烦躁。但还没到崩溃的程度，温柔些就好。".format(name=character.name or "角色"))

    # 职业服务场景豁免：专业服务型职业 + 处于职业场景 + 玩家是服务接受方时，
    # 将疏远/戒备/怨气/不信任等语气降级为 professional（咨询师不因关系值低就对来访者冷淡）。
    # 紧急约束（真崩溃 breakdown）不豁免。详见职业关系豁免改造方案 P3。
    if tone != "breakdown":
        try:
            from backend.game.profession_rules import is_professional_service
            if is_professional_service(character, user_message):
                if tone in ("distant", "suspicious", "angry_cold", "polite_but_guarded", "cautious"):
                    tone = "professional"
                    # 移除关系类的"疏远/不信任/怨气"警告，替换为职业身份提示
                    all_warnings = [w for w in all_warnings
                                    if not any(k in w for k in ("疏远", "不信任", "戒备", "怨气", "冷淡", "生气"))]
                    all_warnings.append(
                        f"你正以{getattr(character, 'identity_label', '') or '专业'}的身份与对方交流，"
                        f"保持专业、耐心、先共情，不因私人关系冷淡或敷衍。")
        except Exception:
            pass

    # 不在此处掷骰子：拒绝与否的概率判定交给调用方在叠加阶梯调整后才掷一次
    return refuse_chance, tone, "\n".join(all_warnings) if all_warnings else ""


def _rel_profile(character):
    """读取 profile_json 中的关系基线/已建立标记（不新增 Character 列）。

    返回 (established: bool, baseline: dict)。原用于 P6 刚认识保护：关系警告是否判 critical
    取决于"关系是否已建立且负向下跌"，而非单纯看绝对当前值。

    注：关系警告已移除（见 RELATIONSHIP_CONSTRAINTS 废弃说明），本函数当前无调用者，
    保留供后续按「由好转坏」语义重构关系约束时复用。
    """
    pj = getattr(character, 'profile_json', None)
    if not pj:
        return False, {}
    try:
        d = json.loads(pj) if isinstance(pj, str) else pj
    except Exception:
        return False, {}
    return bool(d.get('relationship_established', False)), d.get('relationship_baseline', {}) or {}


def get_all_active_warnings(character):
    """获取当前所有活跃的警告/限制"""
    if character is None:
        return []

    warnings = []
    attr_map = _state_values(character)

    # 物理警告
    for attr_name in _PHYSICAL_ATTRS:
        constraint = _get_constraint_for_attr(PHYSICAL_CONSTRAINTS, attr_name, attr_map[attr_name])
        if constraint:
            # 物理属性默认低=坏，但 hunger 为高坏，critical 阈值也需反转。
            if attr_name == "hunger":
                severity = "critical" if attr_map[attr_name] >= 90 else "warning"
            else:
                severity = "critical" if attr_map[attr_name] <= 15 else "warning"
            warnings.append({
                "type": "physical",
                "attr": attr_name,
                "severity": severity,
                "message": constraint["warning"],
                "forced_activities": constraint.get("forced_activities", []),
                "blocked_activities": constraint.get("blocked_activities", []),
            })

    # 心理警告
    for attr_name in _MENTAL_ATTRS:
        constraint = _get_constraint_for_attr(MENTAL_CONSTRAINTS, attr_name, attr_map[attr_name])
        if constraint:
            # stress 的"崩溃"语义特殊：只有 _is_real_breakdown 为真才算 critical 级、
            # 才在提示词里写"濒临崩溃"（进「生命安全级别」块）；否则降级为 warning 并改用
            # 温和文案，避免对单纯高压力喊崩溃。与对话语气侧规则一致（漏洞5）。
            if attr_name == "stress":
                is_critical = _is_real_breakdown(character)
                if is_critical:
                    msg = constraint["warning"]
                else:
                    msg = "你压力很大，情绪烦躁，但还能撑住。温柔些就好。"
            else:
                is_critical = attr_map[attr_name] >= 90 or attr_map[attr_name] <= 10
                msg = constraint["warning"]
            warnings.append({
                "type": "mental",
                "attr": attr_name,
                "severity": "critical" if is_critical else "warning",
                "message": msg,
                "tone": constraint.get("dialogue_tone", ""),
                "blocked_activities": constraint.get("blocked_activities", []),
            })

    # ── 关系警告：已移除（原遍历 RELATIONSHIP_CONSTRAINTS 生成 relationship / relationship_state）──
    # 移除原因同上：该表只在「关系由好转坏」时才成立，用它解释低关系值会把开局角色
    # 误判为「生气、需要道歉弥补」，与关系阶梯 tier0 的「刚认识不熟」冲突。
    # 接管方：
    #   1) 刚认识/不熟的相处分寸 —— dialogue.TIER_CONFIG[0] persona_note
    #      （「你和玩家刚刚认识，完全不熟…回答简短、不展开」）每轮都会注入，
    #      原 [关系状态]「刚认识，态度客气有分寸」与其语义重复，故一并移除；
    #   2) 恋爱/亲密关系 —— dialogue 的【恋爱关系】段（relationship_status == 'dating'）。
    # 副作用确认：get_llm_constraints_prompt 中针对 relationship / relationship_state 类型
    # 的职业服务豁免分支（is_professional_service 时替换为 professional 提示）现无匹配项，
    # 属预期行为——职业场景下本就不该出现基于关系值的负面文案。

    # 警告文本已统一为第一人称（角色=你，玩家=他/对方），不再含角色名占位符
    name = character.name if character and character.name else "角色"
    for w in warnings:
        w["message"] = w["message"].format(name=name)

    return warnings


def get_llm_constraints_prompt(character, user_message=''):
    """生成 LLM 约束提示词，描述当前状态与对话语气。

    设计要点：
    - 对话只吃「状态描述 + 语气」，不列活动黑名单。活动拦截由代码层
      (perform_activity / check_activity_constraints / resolve_constraint_conflicts)
      硬执行，提示词里列 workout/socialize 等清单对聊天无约束力，纯噪音。
    - 语气用与对话路由一致的降级语气(check_dialogue_constraints)，并渲染成
      中文详细描述，不再暴露内部英文枚举(如 breakdown)。
    """
    if character is None:
        return ""

    name = character.name if character and character.name else "角色"
    warnings = get_all_active_warnings(character)

    # 职业服务场景豁免：专业服务型职业 + 职业场景 + 玩家是服务接受方时，
    # 抑制关系类警告（生气/不信任/疏远），改注入职业身份提示——
    # 咨询师不因关系值低就对来访者冷淡/不信任。详见职业关系豁免改造方案 P3。
    try:
        from backend.game.profession_rules import is_professional_service
        if is_professional_service(character, user_message):
            # 职业场景抑制所有"关系类"提示（含刚认识的[关系状态]），避免"客气有分寸"
            # 与 professional 专业共情语气互相矛盾，统一改注入职业身份提示。
            rel_warnings = [w for w in warnings if w.get("type") in ("relationship", "relationship_state")]
            if rel_warnings:
                warnings = [w for w in warnings if w.get("type") not in ("relationship", "relationship_state")]
                warnings.append({
                    "type": "professional",
                    "attr": "",
                    "severity": "warning",
                    "message": (f"你正以{getattr(character, 'identity_label', '') or '专业'}的身份与对方交流，"
                                f"保持专业、耐心、先共情，不因私人关系冷淡或敷衍。"),
                })
    except Exception:
        pass

    if not warnings:
        return f"【当前状态约束】你目前身心状态良好，没有任何行为限制。你可以自由地进行各种活动，对话也会正常进行。"

    prompt_parts = ["【当前状态约束 - 非常重要！你在对话、事件活动中必须严格遵守以下行为限制】"]

    critical_warnings = [w for w in warnings if w["severity"] == "critical"]
    relstate_warnings = [w for w in warnings if w["severity"] == "relstate"]
    normal_warnings = [w for w in warnings if w["severity"] not in ("critical", "relstate")]

    # 只给状态描述，不列活动黑名单（活动由代码层拦截，对话里列了也没用）
    # [紧急约束 - 生命安全级别] 仅收物理 critical + 真崩溃(breakdown)，关系低值永不进此块（P6）
    if critical_warnings:
        prompt_parts.append("\n[紧急约束 - 生命安全级别]")
        for w in critical_warnings:
            prompt_parts.append(f"- {w['message']}")

    if normal_warnings:
        prompt_parts.append("\n[一般约束]")
        for w in normal_warnings:
            prompt_parts.append(f"- {w['message']}")

    # [关系状态]：刚认识/关系初建时的温和提示，独立于一般约束（P6）
    if relstate_warnings:
        prompt_parts.append("\n[关系状态]")
        for w in relstate_warnings:
            prompt_parts.append(f"- {w['message']}")

    # 对话语气：用与路由一致的降级语气，渲染成中文详细描述（不再暴露英文枚举）
    _, real_tone, _ = check_dialogue_constraints(character, user_message)
    tone_desc = _TONE_DESCRIPTIONS.get(real_tone, _TONE_DESCRIPTIONS["normal"])
    prompt_parts.append(f"\n[对话语气] {tone_desc}")

    prompt_parts.append(f"\n请根据以上约束调整你的对话内容、行为。")
    prompt_parts.append(f"注意：这是游戏的一部分，你需要像真实人类一样表现出被限制的感觉，而不是忽略这些状态。")

    return "\n".join(prompt_parts)


def resolve_constraint_conflicts(character, planned_action):
    """
    当多个约束同时激活时，判断某计划行动是否应被封禁。

    旧实现只取排序后的第一条（最高优先级）约束来判断，会覆盖低优先级但同样
    封禁该行动的约束，导致错误放行（如 hygiene=0 封 socialize，但 energy 优先级
    更高且不封，最终放行 socialize）。

    修正：合并所有激活约束的 blocked_activities，只要任意一条封禁该行动即封禁。
    返回 (combined_warning: str, should_block: bool)
    """
    warnings = get_all_active_warnings(character)
    if not warnings:
        return "", False

    # 任意一条激活约束的 blocked_activities 含该行动，即视为封禁
    block_reasons = [
        w.get("message", "")
        for w in warnings
        if planned_action in w.get("blocked_activities", [])
    ]

    should_block = bool(block_reasons)
    if block_reasons:
        combined = "\n".join(block_reasons)
    else:
        # 未被封禁：返回最高严重度约束的提示，便于调用方了解当前状态
        top = max(warnings, key=lambda w: 1 if w.get("severity") == "critical" else 0)
        combined = top.get("message", "")

    return combined, should_block


def get_blocked_activities(character):
    """获取所有当前被封禁的活动 ID 列表"""
    if character is None:
        return set()

    blocked = set()
    attr_map = _state_values(character)

    for attr_name in attr_map:
        constraint = _get_constraint_for_attr(PHYSICAL_CONSTRAINTS, attr_name, attr_map[attr_name])
        if not constraint:
            constraint = _get_constraint_for_attr(MENTAL_CONSTRAINTS, attr_name, attr_map[attr_name])
        if constraint:
            blocked.update(constraint.get("blocked_activities", []))

    return blocked


def get_forced_activities(character):
    """获取所有当前强制推荐的活动 ID 列表"""
    if character is None:
        return set()

    forced = set()
    attr_map = _state_values(character, _PHYSICAL_ATTRS)

    for attr_name in attr_map:
        constraint = _get_constraint_for_attr(PHYSICAL_CONSTRAINTS, attr_name, attr_map[attr_name])
        if constraint:
            forced.update(constraint.get("forced_activities", []))

    return forced


def get_activity_penalty(character):
    """获取当前活动的效率惩罚系数 (0-1)"""
    if character is None:
        return 1.0

    penalties = []
    attr_map = _state_values(character, ("stress", "motivation"))

    for attr_name in ("stress", "motivation"):
        constraint = _get_constraint_for_attr(MENTAL_CONSTRAINTS, attr_name, attr_map[attr_name])
        if constraint:
            p = constraint.get("activity_penalty")
            if p:
                penalties.append(p)

    if not penalties:
        return 1.0
    return min(penalties)  # 取最严重的惩罚


# ============================
# 4. 状态量 -> 人话行为指令（全量程，不依赖约束阈值）
# ============================
# 把单个 0-100 状态量翻译成一句人话行为指令，覆盖整个量程。
# 与上面 PHYSICAL/MENTAL_CONSTRAINTS 的"极端才提示"互补：这里无论数值高低都给
# 角色一句"现在该怎么表现"，使【当前状态】块本身就能驱动对话——约束系统是否
# 触发都不影响数值对对话的作用。
# 方向以 character.py 的 *_map 为准：
#   energy 高=精神好 / hunger 高=饿（注意与属性名"饱腹"相反！）/ health 高=健康
#   hygiene 高=干净 / mood 高=开心 / stress 高=压力大 / happiness 高=幸福 / loneliness 高=孤独

_STATE_BEHAVIOR = {
    "energy": {  # 高=精神好
        (0, 30): "精疲力竭，浑身没劲，只想躺着休息，连说话都嫌累",
        (30, 50): "有点疲惫，体力不足，容易累",
        (50, 75): "体力正常，能正常活动",
        (75, 101): "精力充沛，活力满满",
    },
    "hunger": {  # 高=饿（与属性名"饱腹"含义相反，务必按值翻译！）
        (0, 30): "刚吃饱，肚子很撑，一点都不饿，甚至吃不下",
        (30, 50): "不饿不撑，状态刚好",
        (50, 75): "有点饿了，想找点东西吃",
        (75, 101): "饿坏了，胃空空的，迫切想吃东西",
    },
    "health": {  # 高=健康
        (0, 30): "身体很差，可能生病了，没力气也没精神，需要休息或就医",
        (30, 50): "身体不太舒服，状态欠佳，不宜剧烈活动",
        (50, 75): "身体良好，基本没问题",
        (75, 101): "身体健康，精力体力都跟得上",
    },
    "hygiene": {  # 高=干净
        (0, 30): "好久没洗漱，身上脏兮兮的，不好意思见人",
        (30, 50): "卫生状况欠佳，有点邋遢",
        (50, 75): "干干净净，状态正常",
        (75, 101): "清清爽爽，很干净",
    },
    "mood": {  # 高=开心
        (0, 20): "心情极度低落，郁郁寡欢，不想说话",
        (20, 40): "心情不太好，有点闷",
        (40, 70): "心情平静",
        (70, 101): "心情不错，轻松愉快",
    },
    "stress": {  # 高=压力大
        (0, 30): "很轻松，没什么压力",
        (30, 50): "略微紧张",
        (50, 70): "压力比较大",
        (70, 101): "压力山大，情绪不稳，濒临崩溃",
    },
    "happiness": {  # 高=幸福
        (0, 30): "很不幸福，内心空落落的",
        (30, 50): "幸福感一般",
        (50, 75): "还算幸福",
        (75, 101): "很幸福，内心满足",
    },
    "loneliness": {  # 高=孤独
        (0, 30): "不觉得孤独，身边有人陪伴",
        (30, 50): "偶尔有点孤单",
        (50, 70): "挺孤独的，想有人陪",
        (70, 101): "非常孤独，迫切需要陪伴和聊天",
    },
}


def get_state_behavior_hint(attr, value):
    """把单个状态量(0-100)翻译成一句人话行为指令。

    覆盖全量程：无论数值高低都返回角色"现在该如何表现"的描述，因此
    【当前状态】块本身就能驱动对话，不依赖约束阈值是否触发。

    Args:
        attr:  状态量内部名，如 'energy'/'hunger'/'health'/'hygiene' 等
        value: 数值(0-100)，可为 None 或非数字（此时返回空串）

    Returns:
        str: 人话行为指令；attr 不受支持或 value 非法时返回空串
    """
    bands = _STATE_BEHAVIOR.get(attr)
    if not bands:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    for (lo, hi), hint in bands.items():
        if lo <= v < hi:
            return hint
    # 越界兜底：>=100 取最高档，<0 取最低档
    return list(bands.values())[-1] if v >= 100 else list(bands.values())[0]
