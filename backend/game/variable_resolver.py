"""
变量解析器 — 将统一的点分隔层级变量名（{character.name}）解析为实际值。

所有 23 个 LLM prompt 统一使用此命名空间，替代原来混乱的 {name}/{char_name} 等。
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# ── 变量映射表：统一变量名 → Character 属性路径 ──

VARIABLE_SOURCES: dict[str, str] = {
    # 角色基础
    "character.name":             "char.name",
    "character.age":              "char.age",
    "character.gender":           "char.gender",
    "character.identity":         "char.identity_label",      # 如 "女大学生"
    "character.identity_label":   "char.identity_label",
    "character.major":            "char.major",
    "character.personality":      "char.personality_type",
    "character.personality_type": "char.personality_type",
    "character.personality_tone": "char.personality_tone",
    "character.expression_style": "char.expression_style",
    "character.hometown":         "char.hometown",
    "character.hobbies":          "char.hobbies",
    "character.family":           "char.family",
    "character.appearance":       "char.appearance",
    "character.dream_primary":    "char.dream_primary",
    "character.dream_secondary":  "char.dream_secondary",
    "character.dream_motivation": "char.dream_motivation",
    "character.education":        "char.education",
    "character.economic":         "char.economic",
    "character.location":         "char.location",
    "character.social_tendency":  "char.social_tendency",
    "character.emotional_stability": "char.emotional_stability",
    "character.relationship_status": "char.relationship_status",     # 'friends' / 'dating'
    "character.skills_summary":   "char.skills_summary",     # @property，预格式化
    "character.goals_summary":    "char.goals_summary",      # @property，预格式化
    "character.skills_display":   "char.skills_summary",     # 别名
    "character.goals_display":    "char.goals_summary",      # 别名

    # 玩家
    "player.identity":            "char.player_identity",
    "player.nickname":            "char.player_nickname",
    "player.interaction_style":   "char.interaction_style",
    "player.attitude":            "char.attitude_toward_player",

    # 物理状态
    "status.health":              "char.health",
    "status.energy":              "char.energy",
    "status.hunger":              "char.hunger",
    "status.hygiene":             "char.hygiene",
    "status.brain_health":        "char.brain_health",
    "status.heart_health":        "char.heart_health",
    "status.lung_health":         "char.lung_health",
    "status.liver_health":        "char.liver_health",
    "status.skin_health":         "char.skin_health",
    "status.eye_health":          "char.eye_health",

    # 心理状态
    "status.mood":                "char.mood",
    "status.stress":              "char.stress",
    "status.happiness":           "char.happiness",
    "status.loneliness":          "char.loneliness",
    "status.confidence":          "char.confidence",
    "status.motivation":          "char.motivation",
    "status.creativity":          "char.creativity",
    "status.joy":                 "char.joy",
    "status.anger":               "char.anger",
    "status.disappointment":      "char.disappointment",
    "status.boredom":             "char.boredom",
    "status.fulfillment":         "char.fulfillment",

    # 关系
    "relation.player_trust":      "char.player_trust",
    "relation.player_affection":  "char.player_affection",
    "relation.player_respect":    "char.player_respect",
    "relation.player_intimacy":   "char.player_intimacy",

    # 穿搭
    "outfit.style":               "char.outfit_style",
    "outfit.current":             "char.current_outfit",      # JSON 字符串
    "outfit.description":         "char.outfit_style",        # 别名

    # 关系阶梯（动态上下文注入，不作为静态变量）
    # tier.* 由调用方在 build_* 中直接注入，不走 variable_resolver

    # 时间/环境（动态上下文注入）
    # time.* / weather.* / context.* 由调用方在 render 时传入 extra 字典，不走 char 属性
}


def resolve_variables(char, extra: dict | None = None) -> dict:
    """将 Character 对象解析为 PromptManager.render() 所需的 variables dict。

    Args:
        char: Character ORM 实例
        extra: 额外字典，用于注入动态上下文字段
            - time.game_day, time.game_hour, time.game_minute, time.period_text
            - weather.type, weather.temp, weather.desc, weather.weekday
            - tier.name, tier.desc, tier.effect_scale
            - context.history, context.user_message, context.news_section, etc.

    Returns:
        { "character.name": "晓月", "status.health": 90, ... }
    """
    variables: dict[str, str] = {}
    extra = extra or {}

    for var_name, attr_path in VARIABLE_SOURCES.items():
        try:
            # attr_path 格式：char.xxx — "char" 是占位标记，从 Character 对象直接取属性
            parts = attr_path.split(".")
            value = char
            for part in parts:
                if part == "char":
                    continue       # 跳过占位标记
                value = getattr(value, part, None)
                if value is None:
                    break          # 链上断裂，跳过
            if value is None:
                value = ""
            # 数值类型转字符串（模板中的 {status.health} 需要 "90" 而非 90.0）
            if isinstance(value, float):
                value = f"{value:.1f}".rstrip("0").rstrip(".")
            variables[var_name] = str(value)
        except (AttributeError, TypeError) as e:
            logger.debug("[VariableResolver] 解析 %s → %s 失败: %s", var_name, attr_path, e)
            variables[var_name] = ""

    # 注入 extra 字典（覆盖同名 key）
    if extra:
        variables.update({k: str(v) for k, v in extra.items() if v is not None})

    return variables


def get_all_variable_names() -> list[str]:
    """返回所有可用变量名列表（供前端自动补全使用）。"""
    return sorted(VARIABLE_SOURCES.keys())


# ── 旧变量名 → 统一命名空间 别名映射 ──
# 模板中仍可使用旧名（{char_name}、{mood_val} 等），自动转换为统一名后解析。
# 新模板建议直接用统一名（{character.name}、{status.mood}）。
VARIABLE_ALIASES: dict[str, str] = {
    "char_name":        "character.name",
    "char_age":         "character.age",
    "char_identity":    "character.identity",
    "char_personality": "character.personality",
    "char_name_val":    "character.name",
    "mood_val":         "status.mood",
    "stress_val":       "status.stress",
    "happiness_val":    "status.happiness",
    "health_val":       "status.health",
    "energy_val":       "status.energy",
    "hunger_val":       "status.hunger",
    "hygiene_val":      "status.hygiene",
    "loneliness_val":   "status.loneliness",
    "confidence_val":   "status.confidence",
    "motivation_val":   "status.motivation",
    "creativity_val":   "status.creativity",
    "joy_val":          "status.joy",
    "anger_val":        "status.anger",
    "disappointment_val": "status.disappointment",
    "boredom_val":      "status.boredom",
    "fulfillment_val":  "status.fulfillment",
    "player_trust_val":     "relation.player_trust",
    "player_affection_val": "relation.player_affection",
    "player_respect_val":   "relation.player_respect",
    "player_intimacy_val":  "relation.player_intimacy",
    # 旧系统提示词中的原始占位符（来自 CUSTOM_SYSTEM_PROMPT_TEMPLATE）
    "name":             "character.name",
    "age":              "character.age",
    "identity":         "character.identity",
    "major_desc":       "character.major",
    "personality_type": "character.personality",
    "hometown":         "character.hometown",
    "hobbies_desc":     "character.hobbies",
    "family_desc":      "character.family",
    "personality_tone": "character.personality_tone",
    "expression_style": "character.personality",
    "dream_primary":    "character.dream_primary",
    "dream_secondary":  "character.dream_secondary",
    "player_nickname":  "player.nickname",
}


def render_template(template: str, variables: dict[str, str]) -> str:
    """用 variables dict 替换模板中的 {variable.name} 占位符。

    采用字符串替换方式（非 Python format()），支持点分隔的变量名。
    同时支持旧变量名（如 {char_name}）通过 VARIABLE_ALIASES 自动映射。
    """
    # 第一步：将 dict 中的旧 key 名映射为统一名
    unified: dict[str, str] = {}
    for key, value in variables.items():
        unified[VARIABLE_ALIASES.get(key, key)] = value

    # 第二步：在模板中将旧占位符也替换为统一名（双向兼容）
    result = template
    for alias, standard in VARIABLE_ALIASES.items():
        result = result.replace("{" + alias + "}", "{" + standard + "}")

    # 第三步：用统一变量替换占位符
    for key, value in unified.items():
        placeholder = "{" + key + "}"
        result = result.replace(placeholder, str(value))
    return result
