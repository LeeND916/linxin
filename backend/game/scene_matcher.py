# -*- coding: utf-8 -*-
"""
场景匹配引擎 —— 根据角色当前状态动态筛选 Top 5 场景，并以极简格式注入 LLM Prompt。

使用方式:
    matcher = SceneMatcher()
    scenes = matcher.match(char, weather, today_types, recent_ids, game_hour, top_k=5)
    lines = matcher.format_for_prompt(scenes)  # -> "1.晨跑→E-5,F+2,H+1,M+3|morning/操场/health>=40"
"""

try:
    from .daily_activity_suggestions import SCENE_RULES
except ImportError:
    from daily_activity_suggestions import SCENE_RULES

# 属性速记映射 —— 用于极简注入
ATTR_ABBREV = {
    "energy": "E",
    "health": "H",
    "hunger": "HU",
    "hygiene": "HG",
    "mood": "M",
    "stress": "S",
    "happiness": "HP",
    "loneliness": "L",
    "confidence": "C",
    "motivation": "MO",
    "creativity": "CR",
    "joy": "J",
    "anger": "A",
    "disappointment": "D",
    "boredom": "B",
    "fulfillment": "F",
    "writing": "W",
    "coding": "CS",
    "social": "SS",
    "learning": "LS",
    "fitness": "FT",
    "player_trust": "MT",
    "player_affection": "MA",
    "player_respect": "MR",
    "player_intimacy": "MI",
}

# 废弃技能列名 → skills JSON 真实键（兼容仍按旧名下发的数据/LLM 输出）
_SKILL_ALIAS = {
    "writing_skill": "writing",
    "coding_skill": "coding",
    "social_skill": "social",
    "learning_skill": "learning",
    "debate_skill": "debate",
    "painting_skill": "painting",
    "game_design_skill": "game_design",
    "collaboration_skill": "teamwork",
}

# 时段映射
PERIODS = [
    (0, 6, "night"),
    (6, 8, "morning"),
    (8, 12, "forenoon"),
    (12, 14, "noon"),
    (14, 18, "afternoon"),
    (18, 21, "evening"),
    (21, 24, "night"),
]


class SceneMatcher:
    """场景匹配器，根据角色状态筛选最相关的预设场景。"""

    def get_period(self, game_hour: int) -> str:
        """根据小时获取时段名。"""
        for start, end, name in PERIODS:
            if start <= game_hour < end:
                return name
        return "night"

    def _period_match(self, scene_period: str, current_period: str) -> bool:
        """判断场景时段是否匹配当前时段。any 永远匹配，管道分隔的多时段命中其一即可。"""
        if scene_period == "any":
            return True
        allowed = [p.strip() for p in scene_period.split("|")]
        return current_period in allowed

    def _resolve_skill_key(self, key: str) -> str:
        """将废弃技能列名翻译为 skills JSON 真实键。"""
        return _SKILL_ALIAS.get(key, key)

    def _get_attr_value(self, char, key: str):
        """读取角色某属性当前值：优先 skills JSON，回退直接列属性。

        技能类（writing/coding/social/learning…）现已存入 char.skills，
        不再写废弃列；直接 getattr 会永远读到默认值 0，导致门槛条件失效。
        """
        key = self._resolve_skill_key(key)
        skills = char.skills if isinstance(char.skills, dict) else {}
        if key in skills:
            return skills[key]
        return getattr(char, key, 0)

    def _eval_condition(self, cond: dict, char, weather: str = "") -> bool:
        """评估单个条件。支持属性阈值和天气判断。"""
        # 天气条件
        if cond["attr"] == "weather":
            return cond.get("val", "") == weather

        # 属性阈值条件
        val = self._get_attr_value(char, cond["attr"])
        op = cond["op"]
        target = cond["val"]

        if op == ">=":
            return val >= target
        if op == "<=":
            return val <= target
        if op == "==":
            return val == target
        if op == "!=":
            return val != target
        return True

    def match(
        self,
        char,
        weather: str = "",
        today_types: list = None,
        recent_ids: list = None,
        game_hour: int = 12,
        top_k: int = 5,
        recent_dialogue: str = "",
    ) -> list:
        """
        核心匹配方法。

        参数:
            char         : Character 对象（含所有属性值）
            weather      : 当前天气（晴/阴/小雨/大雨/酷暑/严寒）
            today_types  : 今天已生成的事件类型列表
            recent_ids   : 最近生成过的场景 ID 列表（去重用）
            game_hour    : 当前小时 (0-23)
            top_k        : 返回场景数量
            recent_dialogue: 最近与导师的聊天内容

        返回:
            list[dict]   : Top K 匹配场景
        """
        if today_types is None:
            today_types = []
        if recent_ids is None:
            recent_ids = []

        period = self.get_period(game_hour)
        scored = []

        for scene in SCENE_RULES:
            # 1. 时段硬过滤 —— 不匹配直接跳过
            if not self._period_match(scene["period"], period):
                continue

            score = 0

            # 2. 属性条件匹配得分
            for cond in scene.get("conditions", []):
                if self._eval_condition(cond, char, weather):
                    score += 1

            # 3. 事件类型奖励：优先今天未覆盖的类型
            if scene["type"] not in today_types:
                score += 3

            # 4. 危机状态奖励
            if scene["type"] == "physical_state" and char.health <= 40:
                score += 2
            if scene["type"] == "social" and char.loneliness >= 70:
                score += 2
            if scene["location"] == "食堂" and char.hunger >= 80:
                score += 3
            if scene["type"] == "academic" and char.motivation <= 30:
                score += 1  # 轻微奖励，不要过度干预

            # 5. 聊天内容感知 —— 导师相关事件加分
            if recent_dialogue:
                loc = scene.get("location", "")
                if loc in ("实验室", "咖啡馆") and any(
                    kw in recent_dialogue for kw in ("导师", "论文", "学术", "项目", "研究", "会议")
                ):
                    score += 2
                if scene["type"] == "academic" and any(
                    kw in recent_dialogue for kw in ("论文", "考试", "编程", "项目", "竞赛")
                ):
                    score += 2

            # 6. 历史去重：最近已生成过的场景降权
            if scene["id"] in recent_ids:
                score -= 5

            scored.append((score, scene))

        # 按得分降序排序，取 top_k
        scored.sort(key=lambda x: -x[0])
        return [s for score, s in scored[:top_k] if score > 0]

    def format_for_prompt(self, scenes: list) -> str:
        """
        将场景列表格式化为极简注入文本（用于 LLM Prompt）。

        示例输出:
            1.晨跑→E-5,F+2,H+1,M+3|morning/操场/health>=40
            2.食堂早餐→HU-12,M+2,E+2|morning/食堂/hunger>=50
        """
        lines = []
        for i, scene in enumerate(scenes, 1):
            # 属性变化串
            delta_parts = []
            for ch in scene["changes"]:
                ab = ATTR_ABBREV.get(ch["attribute"], ch["attribute"])
                delta_parts.append(f"{ab}{ch['delta']:+d}")
            delta_str = ",".join(delta_parts)

            # 条件串
            conds = scene.get("conditions", [])
            if conds:
                cond_str = ",".join(
                    f"{c['attr']}{c['op']}{c['val']}" for c in conds
                )
            else:
                cond_str = "-"

            lines.append(
                f"{i}.{scene['title']}→{delta_str}|{scene['period']}/{scene['location']}/{cond_str}"
            )

        return "\n".join(lines)

    def build_rules_section(
        self,
        char,
        weather: str = "",
        today_types: list = None,
        recent_ids: list = None,
        game_hour: int = 12,
        recent_dialogue: str = "",
        top_k: int = 5,
    ) -> str:
        """
        构建完整的 Prompt 规则注入段落（含通用规则 + 匹配场景）。

        返回可直接拼入 User Prompt 的文本，约 250-300 tokens。
        """
        period = self.get_period(game_hour)

        # --- 状态约束预警（按需注入） ---
        alerts = []
        if char.energy <= 30:
            alerts.append("E<=30禁剧烈运动")
        if char.health <= 30:
            alerts.append("H<=30禁户外,应休息")
        if char.hunger >= 80:
            alerts.append("HU>=80优先进食")
        if char.stress >= 80:
            alerts.append("S>=80应减压")
        if char.mood <= 20:
            alerts.append("M<=20事件偏安静")
        status_alert_str = "|".join(alerts) if alerts else "正常"

        # --- 时段约束 ---
        period_rules = {
            "night": "0-6默认睡觉(E+15~20,H+2);仅S>=70或M<=20可失眠",
            "morning": "6-8起床洗漱/晨跑/早餐;禁聚餐逛街",
            "forenoon": "8-12精力充沛适合学习工作",
            "noon": "12-14午休吃饭",
            "afternoon": "14-18学习活动社交",
            "evening": "18-21放松吃饭散步;禁高强度脑力",
        }
        period_rule = period_rules.get(period, "自由活动")
        if 21 <= game_hour < 24:
            period_rule = "21-24洗漱日记看书;禁运动聚餐上课"

        # --- 类型覆盖 ---
        ALL_TYPES = ["personal_activity", "social", "physical_state", "academic", "emotional"]
        uncovered = [t for t in ALL_TYPES if t not in today_types]
        type_hint = uncovered[0] if uncovered else "任意"

        # --- 通用规则 ---
        rules = []
        rules.append(
            "delta:琐事±1~2/日常±2~4/有意义±4~7/重大±7~10;进食HU-10~-15;每次1~5属性;"
            ">=85正面减半<=15负面减半;技能>=90增幅减半<=+3;目标每次+1~5;"
            f"时段:{period_rule};状态:{status_alert_str};"
            f"已覆盖:{','.join(today_types) if today_types else '无'}优先:{type_hint}"
        )

        # --- 匹配场景 ---
        matched = self.match(char, weather, today_types, recent_ids, game_hour, top_k, recent_dialogue)
        if matched:
            scenes_lines = self.format_for_prompt(matched)
            rules.append(
                f"可触发场景(参考,可自由发挥):\n{scenes_lines}"
                "\n(E=energy,H=health,HU=hunger,HG=hygiene,M=mood,S=stress,HP=happiness,"
                "L=loneliness,C=confidence,MO=motivation,CR=creativity,J=joy,A=anger,"
                "D=disappointment,B=boredom,F=fulfillment,W=writing,CS=coding,"
                "SS=social,LS=learning,FT=fitness,MT=trust,MA=affection,MR=respect,MI=intimacy,"
                "WP=writer%,CP=coder%)"
            )
        else:
            rules.append("(无匹配预设场景,自由构思)")

        return "\n".join(rules)


# 便捷函数 —— 可直接在 event.py 中导入使用
def get_rules_section(char, weather="", today_types=None, recent_ids=None,
                      game_hour=12, recent_dialogue="", top_k=5) -> str:
    """一行调用获取完整规则注入文本。"""
    matcher = SceneMatcher()
    return matcher.build_rules_section(
        char, weather, today_types, recent_ids, game_hour, recent_dialogue, top_k
    )
