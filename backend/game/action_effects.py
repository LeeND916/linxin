"""统一「属性增量表」（action_type × 关系阶梯 × 性格系数）。

设计目标（对应需求「两层最终都映射到同一张属性增量表」）：
- 文本通道（intent_classifier 的 action_type）与行为通道（event.py 的送礼/约会/肢体接触）
  都只产出 action_type，最终统一查这张 ACTION_DELTAS 表得到属性变化 dict。
- 变化幅度 = 基础增量 × 性格系数 × 关系阶梯缩放。
  * 性格系数：不同性格对同种行为的反应强度不同（敏感的人被骂更受伤）。
  * 关系阶梯缩放：复用 dialogue.TIER_CONFIG 的 effect_scale（陌生人 0.25 → 灵魂伴侣 1.3），
    由调用方通过 scale_effects_by_tier 统一施加，本模块的 compute_action_effects 默认不自带 tier。

属性键说明：
- 关系键（会被 scale_effects_by_tier 阶梯缩放）：player_trust / player_affection /
  player_respect / player_intimacy（与 character 直接列、_RELATION_ATTRS 一致）。
- 心理键（阶梯透传，不缩放）：anger / mood / happiness / stress / confidence / motivation 等。
"""

# ============ 属性增量表：action_type -> {属性: 基础增量} ============
# 基础增量是「满关系（tier=4）量级」，实际落地会被 effect_scale 缩放。
# 仅列有非零变化的意图；中性/日常话题留空 → 交由 LLM 做细微情绪分析，不抢答。
ACTION_DELTAS = {
    # ---- 攻击 / 负向（嘴硬心软也照罚，分类只看玩家文本）----
    'insult_strong': {
        'player_trust': -15, 'player_affection': -18, 'player_intimacy': -12,
        'player_respect': -10, 'anger': 12, 'mood': -10,
    },
    'insult_mild': {
        'player_trust': -6, 'player_affection': -7, 'player_intimacy': -5,
        'anger': 5, 'mood': -4,
    },
    'threat': {
        'player_trust': -12, 'player_affection': -10, 'player_intimacy': -8,
        'anger': 10, 'mood': -8, 'stress': 6,
    },
    'anger_outburst': {
        'player_affection': -6, 'player_trust': -4, 'anger': 6, 'mood': -5, 'stress': 4,
    },
    'argument': {
        'player_trust': -5, 'player_affection': -4, 'mood': -4,
    },
    'blame': {
        'player_affection': -5, 'player_trust': -3, 'mood': -4,
    },
    'dismiss': {
        'player_affection': -3, 'player_intimacy': -2, 'mood': -2,
    },
    'boundary_set': {
        # 健康设界：尊重略升、好感微降（不视为攻击）
        'player_respect': 2, 'player_affection': -1,
    },
    'break_up': {
        'player_affection': -15, 'player_intimacy': -12, 'player_trust': -8,
        'mood': -10, 'anger': 4,
    },

    # ---- 爱意 / 亲密（嘴上原谅心里也暖）----
    'affection_verbal': {
        'player_affection': 6, 'player_intimacy': 4, 'player_trust': 3,
        'mood': 4, 'happiness': 4,
    },
    'confession': {
        'player_affection': 12, 'player_intimacy': 10, 'player_trust': 6, 'happiness': 8,
    },
    'praise': {
        'player_affection': 4, 'player_trust': 5, 'player_respect': 4,
        'confidence': 3, 'mood': 4,
    },
    'gratitude': {
        'player_affection': 4, 'player_trust': 4, 'mood': 3,
    },
    'care_concern': {
        'player_affection': 5, 'player_trust': 4, 'player_intimacy': 3, 'happiness': 3,
    },
    'comfort': {
        'player_affection': 4, 'player_trust': 3, 'mood': 5, 'happiness': 4,
    },
    'encouragement': {
        'player_trust': 4, 'player_affection': 3, 'confidence': 4, 'motivation': 4,
    },
    'apology': {
        'player_affection': 5, 'player_trust': 4, 'player_intimacy': 2,
    },
    'teasing': {
        'player_affection': 2, 'player_intimacy': 2,
    },
    'flirting': {
        'player_affection': 4, 'player_intimacy': 4, 'player_trust': 2,
    },
    'missing': {
        'player_affection': 4, 'player_intimacy': 3,
    },
    'jealousy': {
        'player_affection': 2, 'player_intimacy': 2, 'stress': 2,
    },
    'possessive': {
        'player_affection': 3, 'player_intimacy': 3,
    },
    'confiding': {
        'player_trust': 5, 'player_affection': 3, 'player_intimacy': 4,
    },
    'vulnerable_share': {
        'player_trust': 5, 'player_affection': 4, 'player_intimacy': 5,
    },
    'support_seek': {
        'player_affection': 3, 'player_intimacy': 3,
    },
    'physical_compliment': {
        'player_affection': 3, 'player_intimacy': 2,
    },

    # ---- 行为通道（event.py 送礼 / 约会 / 肢体接触，不进文本分类）----
    'gift': {
        'player_affection': 8, 'player_intimacy': 5, 'player_trust': 4, 'happiness': 6,
    },
    'date': {
        'player_affection': 10, 'player_intimacy': 8, 'player_trust': 5, 'happiness': 8,
    },
    'touch_hand_hold': {
        'player_intimacy': 6, 'player_affection': 4,
    },
    'touch_hug': {
        'player_intimacy': 8, 'player_affection': 5, 'mood': 5,
    },
    'touch_kiss': {
        'player_intimacy': 12, 'player_affection': 8, 'player_trust': 3,
    },
    'touch_caress': {
        'player_intimacy': 10, 'player_affection': 6,
    },
}

# 强负向意图（命中即落地，不因角色温柔回复而抵消）
NEGATIVE_ACTIONS = {
    'insult_strong', 'insult_mild', 'threat', 'anger_outburst',
    'argument', 'blame', 'dismiss', 'break_up',
}

# ============ 性格系数：子串匹配 (personality 文案 -> (负向系数, 正向系数)) ============
# 系数 > 1 表示反应更强；< 1 表示更钝感。仅微调，避免数值失控。
_PERSONALITY_COEF = [
    ('敏感', 1.30, 1.10),
    ('高冷', 1.00, 0.80),
    ('冷漠', 1.00, 0.80),
    ('傲娇', 1.15, 0.85),
    ('随和', 0.80, 1.00),
    ('温和', 0.90, 1.00),
    ('温柔', 0.90, 1.00),
    ('活泼', 0.90, 1.05),
    ('开朗', 0.90, 1.05),
    ('内向', 1.10, 1.00),
    ('害羞', 1.10, 1.00),
    ('倔强', 1.10, 0.95),
    ('要强', 1.10, 0.95),
]


def get_personality_coef(personality: str):
    """根据角色性格文案返回 (负向系数, 正向系数)。未命中返回 (1.0, 1.0)。"""
    if not personality:
        return 1.0, 1.0
    p = personality.lower()
    for key, neg, pos in _PERSONALITY_COEF:
        if key.lower() in p:
            return neg, pos
    return 1.0, 1.0


def is_negative_action_type(action_type: str) -> bool:
    return action_type in NEGATIVE_ACTIONS


def compute_action_effects(action_type: str, tier: int = 2, personality: str = '',
                           apply_tier: bool = True) -> dict:
    """查增量表，返回 {属性: 增量}（已乘性格系数；apply_tier=True 时再乘阶梯缩放）。

    调用方约定：
    - 文本通道（dialogue.llm_emotional_changes）传 apply_tier=False，由 scale_effects_by_tier 统一施加阶梯。
    - 行为通道（apply_behavior_effects）传 apply_tier=True，直接落地。
    """
    base = ACTION_DELTAS.get(action_type)
    if not base:
        return {}
    neg_coef, pos_coef = get_personality_coef(personality)
    out = {}
    for attr, delta in base.items():
        coef = neg_coef if delta < 0 else pos_coef
        out[attr] = round(delta * coef, 1)
    if apply_tier:
        # 延迟导入，避免循环依赖（action_effects 被 dialogue 顶层 import）
        from backend.game.dialogue import get_tier_config
        scale = get_tier_config(tier).get('effect_scale', 1.0)
        out = {k: round(v * scale, 1) for k, v in out.items()}
    return out


def apply_behavior_effects(char, action_type: str, tier: int = None) -> dict:
    """行为通道统一入口：event.py 的送礼/约会/肢体接触等调用。

    直接查表并落地（含阶梯缩放），返回 apply_attr_changes 的应用结果。
    未定义 action_type 时返回空 dict，交由事件原有逻辑处理。
    """
    if action_type not in ACTION_DELTAS:
        return {}
    if tier is None:
        from backend.game.dialogue import get_relationship_tier
        tier = get_relationship_tier(char)
    personality = getattr(char, 'personality', '') or getattr(char, 'personality_type', '') or ''
    effects = compute_action_effects(action_type, tier=tier, personality=personality, apply_tier=True)
    if not effects:
        return {}
    from backend.game.character import apply_attr_changes
    return apply_attr_changes(effects, character=char)
