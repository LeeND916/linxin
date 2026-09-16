"""角色状态管理模块 — 属性的读取、修改、衰减与增长"""
from backend.models import db, Character
import random
import json
import math
import logging
from datetime import datetime, date
from backend.game.llm_utils import normalize_api_url, safe_llm_post, logger

# 心理状态基线值
MENTAL_BASELINE = {
    'mood': 75.0,
    'stress': 30.0,
    'happiness': 70.0,
    'joy': 70.0,
    'anger': 10.0,
    'disappointment': 15.0,
    'boredom': 20.0,
    'fulfillment': 65.0,
    'motivation': 80.0,
    'loneliness': 25.0,
    'confidence': 65.0,
    'creativity': 75.0,
}

# 废弃技能列名 → skills JSON 实际键 的别名映射。
# activity.py / dialogue.py / scene_rules.py 等仍按旧列名下发技能增益，
# 这里统一翻译为 skill_display 的真实键，避免写入已废弃的列（#26）。
SKILL_ALIAS = {
    'writing_skill': 'writing',
    'coding_skill': 'coding',
    'social_skill': 'social',
    'learning_skill': 'learning',
    'fitness': 'fitness',
}


def get_character():
    """获取当前活跃角色；若无活跃角色，取最新创建的并自动标记为活跃"""
    char = Character.query.filter_by(is_active=True).first()
    if not char:
        char = Character.query.order_by(Character.id.desc()).first()
        if char:
            char.is_active = True
            db.session.commit()
        else:
            char = Character()
            char.is_active = True
            db.session.add(char)
            db.session.commit()
    return char


def apply_attr_changes(changes: dict, character=None, skip_inertia: bool = False):
    """应用属性变化，所有值钳制在 0-100。
    支持三种属性类型：
    1. 直接列属性（物理/心理/旧版技能/关系/旧版目标）
    2. skills JSON 动态技能（如 legal_knowledge、painting、surgery 等）
    3. goals JSON 动态目标

    Args:
        changes: {attr_name: delta} 字典
        character: 可选，调用方已持有的角色对象。传入可避免 LLM 阻塞期间
                   角色被切换导致的竞态。不传则回退到 get_character()。
        skip_inertia: 是否跳过情绪惯性平滑（tick 衰减等系统自动变化应跳过）
    """
    char = character if character is not None else get_character()

    # ── 情绪引擎：惯性平滑（仅对话产生的效果参与，tick 衰减跳过）──
    if not skip_inertia and changes:
        try:
            from backend.game.emotion_engine import apply_inertia
            changes = apply_inertia(changes, char)
        except Exception as e:
            import logging
            logging.getLogger('sim_life.emotion_engine').warning(f"情绪惯性平滑失败，使用原始效果: {e}")

    # 物理状态
    # phys_keys = {'health', 'energy', 'hunger', 'hygiene',
    #              'brain_health', 'heart_health', 'lung_health',
    #              'liver_health', 'skin_health', 'eye_health'}
    # 心理状态
    # mental_keys = {'mood', 'stress', 'happiness', 'loneliness',
    #                'confidence', 'motivation', 'creativity',
    #                'joy', 'anger', 'disappointment', 'boredom', 'fulfillment'}
    # 技能（旧版直接列）
    # skill_keys = {'writing_skill', 'coding_skill', 'social_skill',
    #               'learning_skill', 'fitness'}
    # 关系
    # relation_keys = {'player_trust', 'player_affection',
    #                  'player_respect', 'player_intimacy'}
    # 目标（旧版直接列）
    # goal_keys = {'writer_progress', 'coder_progress'}

    applied = {}
    intimacy_changed = False
    skills_modified = False
    goals_modified = False

    # 确保 JSON 列为 dict
    if not char.skills or not isinstance(char.skills, dict):
        char.skills = {}
    if not char.goals or not isinstance(char.goals, dict):
        char.goals = {}

    skills_dict = char.skills if isinstance(char.skills, dict) else {}
    goals_dict = char.goals if isinstance(char.goals, dict) else {}
    # 角色专属的合法技能/目标键（来自 skill_display / goal_display）
    valid_skill_keys = char.skill_display if isinstance(char.skill_display, dict) else {}
    valid_goal_keys = char.goal_display if isinstance(char.goal_display, dict) else {}

    for raw_key, delta in changes.items():
        # 把废弃技能列名翻译为 skills JSON 真实键，避免写入已废弃列（#26）
        key = SKILL_ALIAS.get(raw_key, raw_key)
        try:
            # 1. 优先：动态技能（skill_display 中的键）— 写入 skills JSON
            #    即便该技能尚未出现在 skills 中也创建，确保游戏内技能提升「存得进、看得见」
            #    放在直接列属性之前，避免 creativity 这类既是列又是技能键的属性被误写进旧列
            if key in valid_skill_keys or key in skills_dict:
                base = skills_dict.get(key, 0) or 0
                new_val = max(0.0, min(100.0, base + delta))
                char.skills = {**skills_dict, key: new_val}
                applied[key] = round(delta, 1)
                skills_modified = True
                continue

            # 2. 直接列属性（物理/心理/关系等）
            current = getattr(char, key, None)
            if current is not None:
                new_val = max(0.0, min(100.0, current + delta))
                setattr(char, key, new_val)
                applied[key] = round(delta, 1)
                if key == 'player_intimacy':
                    intimacy_changed = True
                continue

            # 3. 动态目标（goal_display 中的键）— 写入 goals JSON
            if key in valid_goal_keys or key in goals_dict:
                base = goals_dict.get(key, 0) or 0
                new_val = max(0.0, min(100.0, base + delta))
                char.goals = {**goals_dict, key: new_val}
                applied[key] = round(delta, 1)
                goals_modified = True
                continue

            # 4. 未知 key：不在已知技能/目标集合且非直接列属性，则丢弃，避免污染数据
        except Exception:
            continue

    # 标记 JSON 列已修改，确保 SQLAlchemy 检测到变更
    if skills_modified:
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(char, 'skills')
    if goals_modified:
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(char, 'goals')

    db.session.commit()

    # ── 情绪引擎：记录当前情绪快照（仅对话效果参与，tick 衰减跳过）──
    if not skip_inertia and applied:
        try:
            from backend.game.emotion_engine import record_emotion_snapshot
            record_emotion_snapshot(char)
            db.session.commit()
        except Exception as e:
            import logging
            logging.getLogger('sim_life.emotion_engine').warning(f"情绪快照记录失败: {e}")

    # 亲密值变化后检查并更新恋爱关系状态
    if intimacy_changed:
        update_relationship_status(char)

    # 职业关系豁免 P6：首次正向互动检测（grace 窗口关闭信号）
    # 任一项关系值（信任/好感/尊重/亲密）本轮上涨 → 标记关系已建立并快照基线。
    # 此前的低关系值只显示温和的[关系状态]提示，绝不误判为"关系破裂/有负面历史"。
    # 仅对话/事件产生的正向变化参与（tick 衰减 skip_inertia 不参与）。
    if not skip_inertia:
        try:
            _REL_KEYS = ('player_trust', 'player_affection', 'player_respect', 'player_intimacy')
            if any(applied.get(k, 0) > 0 for k in _REL_KEYS):
                pj = char.profile_json
                pj_dict = json.loads(pj) if isinstance(pj, str) else (pj or {})
                if not pj_dict.get('relationship_established'):
                    pj_dict['relationship_established'] = True
                    pj_dict['relationship_baseline'] = {
                        'player_trust': char.player_trust,
                        'player_affection': char.player_affection,
                        'player_respect': char.player_respect,
                        'player_intimacy': char.player_intimacy,
                    }
                    char.profile_json = json.dumps(pj_dict, ensure_ascii=False)
                    from sqlalchemy.orm.attributes import flag_modified
                    flag_modified(char, 'profile_json')
                    db.session.commit()
        except Exception:
            pass

    return applied


def update_relationship_status(char):
    """根据 player_intimacy 更新恋爱关系状态"""
    old_status = char.relationship_status
    if char.player_intimacy >= 80:
        char.relationship_status = 'dating'
    else:
        char.relationship_status = 'friends'
    if char.relationship_status != old_status:
        db.session.commit()


# ==================== Tick Update Prompt 模板 ====================

TICK_UPDATE_PROMPT_TEMPLATE = """你是游戏引擎的状态更新模块。根据角色「{character.name}」的当前状态，生成一次时间推进（{time.time_delta_desc}）后的合理状态变化。

【时间推进范围】
开始于：第{time.start_day}天 {time.start_hour}:{time.start_minute}，{context.start_location}
结束于：第{time.end_day}天 {time.end_hour}:{time.end_minute}，结束的地点请你依据结束的活动信息自由生成
你的所有行为必须与这段时间的推进保持一致。

角色当前状态：
- 姓名：{character.name}，{character.age}岁，性别：{character.gender}，{character.identity_label}，{character.major}专业
- 当前位置：{character.location}
- 穿搭风格：{outfit.style}

物理状态：
  健康{status.health} 精力{status.energy} 饥饿{status.hunger} 卫生{status.hygiene}
器官健康：大脑{status.brain_health} 心脏{status.heart_health} 肺{status.lung_health} 肝{status.liver_health} 皮肤{status.skin_health} 眼睛{status.eye_health}

心理状态：
  心情{status.mood} 压力{status.stress} 幸福度{status.happiness} 孤独感{status.loneliness}
  自信{status.confidence} 动力{status.motivation} 创造力{status.creativity}
  开心{status.joy} 愤怒{status.anger} 失望{status.disappointment} 无聊{status.boredom} 充实{status.fulfillment}

技能水平：
{character.skills_display}

与{player.nickname}关系：信任{relation.player_trust} 好感{relation.player_affection} 尊重{relation.player_respect} 亲密{relation.player_intimacy}

目标进度：
{character.goals_display}

当前时间：第{time.game_day}天 {time.game_hour}:{time.game_minute}

{context.recent_events}

{context.recent_dialogue}

{context.scene_suggestions}

{context.world_view}

【性别与代词铁律】角色性别为{character.gender}（female=女性 / male=男性）。若角色为女性，描写角色自身时必须使用「她/她的/她自己」，绝对禁止用「他/他的」指代角色自己，且不得出现与女性矛盾的配偶关系（如「妻子」）；若角色为男性则反之。玩家（{player.nickname}）为男性，提及或描写玩家时必须用「他/他的」，角色与玩家的代词绝不能混淆。

请根据以上最近事件和聊天内容，结合角色的性格、身份、当前位置和当前时段，生成一个合理的随机事件。要求：
- 类型可以是：遇到某人、收到消息、内心感悟、突发小状况、灵感闪现、外部环境变化等
- 应与角色近期经历自然关联；若最近聊天中有未解决的情绪或话题，可以此为线索
- 事件应日常、真实、不过度戏剧化，符合角色身份和校园生活场景
- 若无可参考信息，可自由生成一个中性事件；平淡无事也是合理的

请根据角色{character.identity_label}身份和日常作息，以JSON格式返回状态变化：
{{"changes": {{"energy": -2.0, "hunger": 1.5, "mood": 0.5, ...}}, "summary": "一句话描述这段时间内角色的主要的活动轨迹及状态变化", "event": "事件描述，50字以内，无合适事件则为空字符串","location": "最后活动的地点，必须和当前活动关联"}}

可用属性名：health, energy, hunger, hygiene, brain_health, heart_health, lung_health, liver_health, skin_health, eye_health, mood, stress, happiness, loneliness, confidence, motivation, creativity, joy, anger, disappointment, boredom, fulfillment, {context.available_skills}, player_trust, player_affection, player_respect, player_intimacy, {context.available_goals}

要求：
1. 只返回JSON，不要其他内容
2. 变化幅度必须与时间跨度成正比（参考基准：1小时 ≈ ±0.5~±3.0；超过4小时可放大至 ±5.0~±8.0；几分钟级别则缩小至 ±0.1~±1.0）
3. 变化必须符合真实生理和心理规律，不同属性有不同的变化曲线：
   - 饥饿（hunger 为饥饿度，0=刚吃饱/不饿，100=饿极了）：清醒时每小时上升 0.3~1.0（越来越饿），睡眠时减半；进食时段（早中晚）附近如有进食行为则应大幅下降（-3~-8，吃饱后饥饿度降低）
   - 精力：清醒时每小时下降 0.5~2.0（脑力/体力劳动更快），睡眠时每小时恢复 1.0~3.0；熬夜时加速下降
   - 卫生：日常活动缓慢下降（0~0.5/h），运动/户外活动后加速下降（-1~-3）
   - 器官健康：通常不变，仅在极端状态（熬夜>4h、运动过量、生病）时才做 ±0.2~±1.0 微调
   - 心情/压力/幸福/孤独等心理属性：单次变化不超过 ±2.0/h；受活动性质和社交互动影响；长时间独处孤独感上升，长时间社交压力可能上升
   - 技能：仅在长时间（>2h）专注某活动时小幅提升 ±0.3~±1.0，短时间不变化
   - 关系属性：无互动事件时不变化
   - 目标进度：仅在主动推进相关活动时小幅增加（±0.1~±0.5%），通常不变
4. 增强多样性，避免模式化：
   - 不要每次生成相同或相近的数值组合，应根据角色的性格、心情、当天已发生的事件产生合理波动
   - 同一时段、同一场景下多次推进，summary应反映不同细节（如先看书、再画画、后休息），不要重复
   - 允许偶尔出现"什么都没发生"的平淡时段，也允许偶尔出现效率特别高或特别低的波动
5. summary必须简洁自然，描述这{time.time_delta_desc}内角色的核心活动或状态变化，融入角色身份、当前位置和时段特征，不要机械罗列属性变化。"""


def _format_time_delta(hours: float) -> str:
    """将小时数转为中文时间描述。"""
    minutes = hours * 60
    if minutes < 5:
        return "几分钟"
    elif minutes <= 30:
        return "约半小时"
    elif minutes < 60:
        return "不到一小时"
    elif hours <= 1.0:
        return "约1小时"
    else:
        h = int(hours)
        m = int((hours - h) * 60)
        if m > 0:
            return f"约{h}小时{m}分钟"
        return f"约{h}小时"


def _build_skills_display(char) -> str:
    """从 char.skills JSON 构建技能展示文本。"""
    if not char.skills or not isinstance(char.skills, dict) or not char.skills:
        return "暂无技能数据"
    display = char.skill_display if isinstance(char.skill_display, dict) else {}
    parts = []
    for key, val in char.skills.items():
        name = display.get(key, key)
        parts.append(f"{name}:{val:.0f}")
    return "  ".join(parts)


def _build_goals_display(char) -> str:
    """从 char.goals JSON 构建目标展示文本。"""
    if not char.goals or not isinstance(char.goals, dict) or not char.goals:
        return "暂无目标"
    display = char.goal_display if isinstance(char.goal_display, dict) else {}
    parts = []
    for key, val in char.goals.items():
        name = display.get(key, key)
        parts.append(f"{name}:{val}%")
    return "  ".join(parts)


def _build_available_skills(char) -> str:
    """从 char.skill_display 提取所有合法技能 key，供 LLM 作为有效的 JSON 属性名使用。
    
    注意：以 skill_display 为准，而非 skills。skills 可能被污染包含不相关 key，
    但 LLM 只应看到 skill_display 中明确定义的技能。"""
    if not char.skill_display or not isinstance(char.skill_display, dict):
        return ""
    return ", ".join(char.skill_display.keys())


def _build_available_goals(char) -> str:
    """从 char.goal_display 提取所有合法目标 key，供 LLM 作为有效的 JSON 属性名使用。
    
    注意：以 goal_display 为准，而非 goals。"""
    if not char.goal_display or not isinstance(char.goal_display, dict):
        return ""
    return ", ".join(char.goal_display.keys())


def _format_recent_events(events, character_name=None) -> str:
    """将最近事件列表格式化为 LLM 可读文本。"""
    if not events:
        return "【最近事件】\n暂无近期事件记录"
    lines = ["【最近事件】"]
    for e in events:
        game_day = getattr(e, 'game_day', None)
        game_time = getattr(e, 'game_time', None)
        desc = getattr(e, 'description', '') or getattr(e, 'title', '') or ''
        if game_day and game_time:
            lines.append(f"第{game_day}天 {game_time} | {desc.strip()}")
        else:
            lines.append(desc.strip())
    return "\n".join(lines)


# ==================== 睡眠时段硬编码衰减 ====================

SLEEP_START = 23  # 23:00
SLEEP_END = 6     # 06:00（不包含边界）
# 睡眠消气时间常数（小时）：越小消气越快。7~8 小时睡眠可把高愤怒压到接近平静。
SLEEP_ANGER_TAU_HOURS = 3.0


def is_sleep_period(game_hour: int) -> bool:
    """判断当前游戏小时是否在睡眠时段（23:00-06:00）。"""
    return game_hour >= SLEEP_START or game_hour < SLEEP_END


def compute_sleep_decay(hours: float, char=None) -> dict:
    """硬编码计算睡眠时段的属性衰减（每小时基准，按 hours 缩放）。

    变更：
    - 精力恢复：每小时 +1.0~3.0
    - 饥饿上升：每小时 +0.15~0.5（睡眠半速，睡醒会饿）
    - 卫生轻微下降：每小时 -0.1~0.2
    - 睡眠消气：愤怒按「当前值 × 时间衰减」指数下降，睡得越久越接近平静
      （修复根因：原本睡眠不碰 anger，导致“睡一觉气还在”）
    """
    import random
    h = max(0.0, hours)
    changes = {
        'energy': round(h * random.uniform(1.0, 3.0), 1),
        'hunger': round(h * random.uniform(0.15, 0.5), 1),
        'hygiene': round(-h * random.uniform(0.1, 0.2), 1),
    }
    # 睡眠消气：以时间常数 SLEEP_ANGER_TAU_HOURS 做指数衰减，随当前愤怒升高而降得更多，
    # 但不会把本来就很平静（接近基线）的角色强行压到 0。
    if char is not None:
        cur = getattr(char, 'anger', None)
        if cur is not None and cur > 0:
            factor = 1.0 - math.exp(-h / SLEEP_ANGER_TAU_HOURS)
            drop = round(cur * factor, 1)
            if drop > 0:
                changes['anger'] = -drop
    return changes


def _apply_awake_anger_decay(char, hours: float):
    """清醒时段强制小幅消气：保证愤怒随时间缓慢降低，不依赖 LLM 是否主动降。

    每小时约降 1.5~3 点；当前愤怒越高，单次消得略多（越气越容易随清醒时间慢慢平复）。
    系统自动变化，跳过情绪惯性；只动 anger。
    """
    if char is None:
        return
    h = max(0.0, hours)
    if h <= 0:
        return
    cur = getattr(char, 'anger', 0) or 0
    per_hour = random.uniform(1.5, 3.0)
    # 愤怒越高，单位时间多消一点（封顶额外 +2/小时），使高愤怒也能在清醒时段持续回落
    if cur > 85:
        per_hour += random.uniform(1.0, 2.0)
    elif cur > 60:
        per_hour += random.uniform(0.3, 1.0)
    drop = round(h * per_hour, 1)
    if drop > 0:
        apply_attr_changes({'anger': -drop}, character=char, skip_inertia=True)


def build_tick_update_prompt(char, tick_delta_hours: float = 1.0, start_hour: int = None, start_minute: int = None):
    """从 char 实例构建 tick_update 的 prompt，动态填充角色信息。

    start_hour/start_minute 为可选的真实时段起点（由调用方在合并多小时心跳时传入），
    用于让提示词的“起止时段”与事件戳一致；缺省则回落到 char.game_hour/minute。
    """
    import math
    # 获取最近事件和聊天记录（懒加载避免循环依赖）
    from backend.game.event import get_recent_events, get_recent_dialogue
    recent_events_text = _format_recent_events(get_recent_events(limit=5))
    recent_dialogue_text = get_recent_dialogue(limit=10)

    # 场景建议：注入日常活动建议（来自旧的 100 条规则）
    scene_suggestions_text = ""
    try:
        from backend.game.scene_matcher import get_rules_section
        from backend.game.daily_activity_suggestions import SCENE_RULES
        from backend.game import weather as _weather
        from datetime import date as _dt_date

        # 获取天气
        yr, mo, dy, _ = char.get_game_date()
        game_date_obj = _dt_date(yr, mo, dy)
        today_weather = _weather.generate_daily_weather(game_date_obj, character=char)
        weather_type = today_weather.weather_type if today_weather else ""

        # 获取今天已生成事件类型
        from backend.models import EventLog
        today_events = EventLog.query.filter_by(game_day=char.game_day).all()
        today_types = list(set(e.event_type for e in today_events))

        # 最近事件标题去重匹配场景 ID
        recent_evts = get_recent_events(limit=5)
        recent_titles = [e.title for e in reversed(recent_evts)]
        recent_ids = [s["id"] for s in SCENE_RULES if s["title"] in recent_titles]

        scene_suggestions_text = get_rules_section(
            char=char,
            weather=weather_type,
            today_types=today_types,
            recent_ids=recent_ids,
            game_hour=int(char.game_hour),
            recent_dialogue=recent_dialogue_text,
            top_k=5,
        )
        if scene_suggestions_text:
            scene_suggestions_text = "【场景建议】\n" + scene_suggestions_text
    except Exception as e:
        logger.warning(f"生成场景建议失败（不影响运行）: {e}")

    # 世界观注入
    world_view_text = ""
    try:
        from backend.game.world_setting_manager import WorldSettingManager
        ws = WorldSettingManager.get(char.name)
        if ws:
            parts = []
            dr = ws.get('daily_rhythm', {})
            if dr:
                parts.append("【角色日常节奏】")
                for k, v in dr.items():
                    parts.append(f"  {k}: {v}")
            sc = ws.get('stress_sources', [])
            if sc:
                parts.append(f"压力源: {'、'.join(sc)}")
            cc = ws.get('comfort_activities', [])
            if cc:
                parts.append(f"安慰活动: {'、'.join(cc)}")
            tones = ws.get('story_tones', [])
            if tones:
                parts.append(f"故事基调: {'、'.join(tones)}")
            conflict = ws.get('central_conflict', '')
            if conflict:
                parts.append(f"核心矛盾: {conflict}")
            if parts:
                world_view_text = "\n".join(parts)
    except Exception as e:
        logger.warning(f"加载世界观失败（不影响运行）: {e}")

    # 计算推进起止时间（start 可由调用方传入真实时段起点，避免被污染的 char.game_hour 影响戳与提示词）
    if start_hour is None:
        start_hour = int(char.game_hour)
    if start_minute is None:
        start_minute = int(char.game_minute)
    start_day = char.game_day
    total_start_minutes = start_hour * 60 + start_minute
    total_end_minutes = total_start_minutes + tick_delta_hours * 60
    end_day = start_day + int(total_end_minutes // (24 * 60))
    end_hour = int((total_end_minutes % (24 * 60)) // 60)
    end_minute = int(total_end_minutes % 60)

    from backend.game.prompt_registry import get_prompt_manager
    pm = get_prompt_manager()
    return pm.render("tick.update", char, extra={
        "time.game_day": str(char.game_day),
        "time.game_hour": f"{int(char.game_hour):02d}",
        "time.game_minute": f"{int(char.game_minute):02d}",
        "time.start_day": str(start_day),
        "time.start_hour": f"{start_hour:02d}",
        "time.start_minute": f"{start_minute:02d}",
        "context.start_location": char.location or "未知",
        "time.end_day": str(end_day),
        "time.end_hour": f"{end_hour:02d}",
        "time.end_minute": f"{end_minute:02d}",
        "time.time_delta_desc": _format_time_delta(tick_delta_hours),
        "context.recent_events": recent_events_text,
        "context.recent_dialogue": recent_dialogue_text,
        "context.scene_suggestions": scene_suggestions_text,
        "context.world_view": world_view_text,
        "context.available_skills": _build_available_skills(char),
        "context.available_goals": _build_available_goals(char),
        "player.nickname": getattr(char, 'player_nickname', '') or getattr(char, 'player_identity', '导师') or '导师',
    })


def tick_chunk_update(llm_config: dict = None, tick_delta_hours: float = 4.0, start_hour: int = None, start_minute: int = None):
    """执行一次 tick 块更新（默认 4 小时）。

    若角色当前时间在睡眠时段（23:00-06:00），跳过 LLM 调用，
    使用硬编码物理衰减公式；否则走 LLM 驱动更新。

    start_hour/start_minute 为调用方传入的真实时段起点，用于判断睡眠分支与事件戳，
    避免 char.game_hour 在合并多小时心跳时被污染（见 _process_daily_end）。

    Returns:
        dict 或 None（失败时）
    """
    char = get_character()
    game_hour = int(start_hour) if start_hour is not None else int(char.game_hour)

    if is_sleep_period(game_hour):
        # 睡眠时段：硬编码衰减
        changes = compute_sleep_decay(tick_delta_hours, char=char)
        applied = apply_attr_changes(changes)
        summary = f"{char.name or '角色'}正在睡觉。（{tick_delta_hours:.0f}h 硬编码衰减）"

        # 写入事件日志
        from backend.models import EventLog
        _diag = logging.getLogger(__name__)
        _diag.info("[DIAG] tick_chunk_update: writing sleep_decay EventLog, game_day=%d, game_time=%s",
                   getattr(char, 'game_day', 0), getattr(char, 'game_time', '08:00'))
        event = EventLog(
            event_type='sleep_decay',
            title='睡眠衰减',
            description=summary,
            effects=json.dumps(applied, ensure_ascii=False),
            game_day=getattr(char, 'game_day', 0),
            game_time=getattr(char, 'game_time', '08:00'),
            character_name=char.name or '',
            location=getattr(char, 'location', ''),
        )
        db.session.add(event)
        db.session.commit()

        return {
            'applied': applied,
            'summary': summary,
            'event': event.to_dict(),
            'source': 'sleep_hardcoded',
        }
    else:
        # 非睡眠时段：LLM 驱动（把真实起点透传，使心跳事件戳=时段结束、提示词起点=真实起点）
        result = llm_tick_update(llm_config, tick_delta_hours, start_hour=game_hour, start_minute=start_minute)
        # 强制小幅消气：LLM 成功时才追加（失败会回退到 tick_decay，那里已有愤怒偏置，避免双重衰减）。
        # 这保证清醒时段愤怒一定缓慢消解，不依赖 LLM 是否主动降（修复根因：原本只有 LLM 失败兜底才衰减）。
        if result is not None:
            _apply_awake_anger_decay(char, tick_delta_hours)
        return result


def llm_tick_update(llm_config: dict = None, tick_delta_hours: float = 1.0, start_hour: int = None, start_minute: int = None):
    """使用 LLM 驱动角色状态自动更新，失败则返回 None（由调用方降级到 tick_decay）

    start_hour/start_minute 为真实时段起点；事件戳据此算成“时段结束时刻”，
    使心跳事件的时间与“覆盖一整段时段”的内容描述一致。
    """
    from backend.models import EventLog
    from backend.game.event import extract_json_from_llm_response

    char = get_character()
    if not llm_config or not llm_config.get('api_key'):
        return None

    # 构建角色状态 prompt（动态读取角色信息）
    status_prompt = build_tick_update_prompt(char, tick_delta_hours, start_hour=start_hour, start_minute=start_minute)

    try:
        result = safe_llm_post(
            api_url=llm_config['api_url'],
            api_key=llm_config['api_key'],
            model=llm_config.get('model_name', 'gpt-4o-mini'),
            messages=[
                {"role": "system", "content": "你是一个游戏引擎状态更新模块。只返回JSON，不要任何解释。"},
                {"role": "user", "content": status_prompt}
            ],
            max_tokens=4000,
            temperature=llm_config.get('temperature', 0.85),
            timeout=(30, 60),
            call_type="tick_update",
            character_name=char.name
        )

        if not result:
            logger.warning("LLM tick更新失败，降级到内置衰减")
            return None

        content = result["choices"][0]["message"]["content"]
        data, parse_error = extract_json_from_llm_response(content)
        if parse_error:
            logger.error(f"[LLM-ERROR] llm_tick_update 解析失败: {parse_error}")
            return None

        if not isinstance(data, dict):
            logger.error(
                f"[LLM-ERROR] llm_tick_update 期望JSON对象，实际收到: {type(data).__name__}"
            )
            return None

        changes = data.get('changes', {})
        summary = data.get('summary', '时间推进了一次。')
        event_desc = data.get('event', '')
        tick_location = data.get('location', '')          # 新增

        if not changes:
            return None

        # 快照变更前的属性值
        old_values = {}
        for key in changes:
            val = getattr(char, key, None)
            if val is not None:
                old_values[key] = round(val, 1)

        applied = apply_attr_changes(changes, character=char)

        # 构建 state_changes（含 old/new/delta）
        state_changes = []
        db.session.refresh(char)
        for key, delta in applied.items():
            old_val = old_values.get(key)
            new_val = getattr(char, key, None)
            if old_val is not None and new_val is not None:
                state_changes.append({
                    'attribute': key,
                    'old': old_val,
                    'new': round(new_val, 1),
                    'delta': delta
                })

        # 计算游戏时间字符串（事件戳）= 时段结束时刻（真实起点 + tick_delta_hours 进位），
        # 不再用被污染的 char.game_hour，使“08:18”这类戳与“从上午到深夜”的内容一致。
        if start_hour is not None:
            _sh = int(start_hour)
            _sm = int(start_minute) if start_minute is not None else 0
        else:
            _sh = int(char.game_hour)
            _sm = int(char.game_minute)
        _total_end = _sh * 60 + _sm + tick_delta_hours * 60
        _end_hour = int((_total_end % (24 * 60)) // 60)
        _end_minute = int(_total_end % 60)
        game_time = f"{_end_hour:02d}:{_end_minute:02d}"

        # 记录事件到 DB
        _diag = logging.getLogger(__name__)
        _diag.info("[DIAG] llm_tick_update: writing llm_tick EventLog, game_day=%d, game_time=%s",
                   char.game_day, game_time)
        event = EventLog(
            event_type='llm_tick',
            title=f'LLM驱动的状态更新',
            description=summary,
            effects=json.dumps(applied, ensure_ascii=False),
            game_day=char.game_day,
            game_time=game_time,
            location=tick_location,
            character_name=char.name,
        )
        db.session.add(event)
        db.session.commit()

        # 若有 LLM 生成的事件描述，单独写入 EventLog
        if event_desc:
            _diag.info("[DIAG] llm_tick_update: writing llm_tick_event EventLog, game_day=%d, title=%.40s",
                       char.game_day, event_desc[:40])
            llm_event = EventLog(
                event_type='llm_tick_event',
                title=event_desc[:60],
                description=event_desc,
                effects='{}',
                game_day=char.game_day,
                game_time=game_time,
                location=tick_location,
                character_name=char.name,
            )
            db.session.add(llm_event)
            db.session.commit()

        return {
            'applied': applied,
            'summary': summary,
            'event': event.to_dict(),
            'llm_event': event_desc
        }

    except Exception as e:
        logger.error(f"LLM tick解析失败: {e}")
        return None


def daily_mental_regulation():
    """首次心理状态初始化：仅对每个角色执行一次，朝基线值回归（15% 步长）。

    用 Character.mental_initialized 标记保证「只做首次初始化」，之后即便 GM 在后台
    手动改了心理属性也不会被每日调节悄悄覆盖回去。
    """
    char = get_character()
    if char is None:
        return None
    # 已初始化过则直接跳过，绝不覆盖 GM 手动设置的心理值
    if getattr(char, 'mental_initialized', False):
        return None

    changes = {}
    for attr, baseline in MENTAL_BASELINE.items():
        current = getattr(char, attr, None)
        if current is None:
            continue
        gap = baseline - current
        if abs(gap) < 0.5:
            continue
        # 以 15% 差距向基线移动
        step = round(gap * 0.15, 1)
        if abs(step) >= 0.3:
            changes[attr] = step

    if changes:
        apply_attr_changes(changes)

    # 标记已完成首次初始化，后续不再执行
    char.mental_initialized = True
    db.session.commit()
    return changes if changes else {}


def daily_anger_regression():
    """每日把愤怒朝基线小幅回归（区别于 daily_mental_regulation 只跑一次）。

    在 _process_daily_end 每日结算时调用：保证愤怒不会长期漂移偏高，
    隔夜自然平复一部分，与睡眠消气、清醒强制消气共同构成「愤怒消解三通道」。
    只动 anger，不动其他属性，也不覆盖 GM 手动设置（回归是向基线靠，幅度温和）。
    """
    char = get_character()
    if char is None:
        return None
    baseline = MENTAL_BASELINE.get('anger', 10.0)
    cur = getattr(char, 'anger', None)
    if cur is None:
        return None
    gap = baseline - cur
    if abs(gap) < 0.5:
        return {}
    # 每天回归 20% 的差距（至少 0.3），温和地把愤怒拉回基线带
    step = round(gap * 0.20, 1)
    if abs(step) < 0.3:
        step = 0.3 if gap > 0 else -0.3
    apply_attr_changes({'anger': step}, skip_inertia=True)
    return {'anger': step}


def tick_decay():
    """每 tick 自动衰减：饥饿上升、精力下降、情绪自然衰减等"""
    char = get_character()

    # 周末判断：周六/周日放松，mood/joy 不衰减反而缓慢恢复
    from backend.game import weather
    is_weekend = weather.is_weekend()

    if is_weekend:
        # 周末：物理正常衰减，心理更放松（mood/joy 缓慢回升，stress 下降）
        changes = {
            'energy': max(-3.0, -random.uniform(0.5, 3.0)),
            'hunger': random.uniform(1.0, 3.0),
            'hygiene': -random.uniform(0.5, 2.0),
            'mood': random.uniform(0.0, 1.5),       # 周末心情回升
            'stress': random.uniform(-1.5, 0.3),    # 压力下降
            'loneliness': random.uniform(0.3, 1.5),
            'motivation': random.uniform(-1.0, 0.3),
            'joy': random.uniform(0.0, 1.5),        # 开心回升
            'anger': random.uniform(-2.0, 0.3),
            'disappointment': random.uniform(-1.5, 0.3),
            'boredom': random.uniform(0.5, 2.0),
            'fulfillment': random.uniform(-1.0, 0.3),
        }
    else:
        changes = {
            'energy': max(-3.0, -random.uniform(0.5, 3.0)),
            'hunger': random.uniform(1.0, 3.0),
            'hygiene': -random.uniform(0.5, 2.0),
            'mood': random.uniform(-1.5, 0.5),
            'stress': random.uniform(-0.5, 1.5),
            'loneliness': random.uniform(0.5, 2.0),
            'motivation': random.uniform(-1.5, 0.5),
            'joy': random.uniform(-1.5, 0.5),
            'anger': random.uniform(-2.0, 0.5),
            'disappointment': random.uniform(-1.5, 0.5),
            'boredom': random.uniform(0.5, 2.0),
            'fulfillment': random.uniform(-1.5, 0.5),
        }

    # ── 情绪引擎：tick 衰减考虑情绪趋势 ──
    # 持续低落时加速恢复（避免角色长时间陷入低谷）
    try:
        from backend.game.emotion_engine import get_emotion_trend
        mood_trend = get_emotion_trend(char, 'mood', window=3)
        if mood_trend < -3.0 and char.mood < 35:
            # 心情持续下降且已低于 35 → 加速恢复
            changes['mood'] += random.uniform(1.0, 3.0)
            changes['motivation'] += random.uniform(0.5, 1.5)
        elif mood_trend > 3.0 and char.mood > 75:
            # 心情持续上升且已很高 → 减缓回升（避免溢出）
            changes['mood'] *= 0.5
    except Exception:
        pass  # 情绪趋势检测失败不影响正常 tick

    # ── 愤怒：高愤怒加速消散（参照 mood 的加速恢复机制）──
    # 愤怒极高时强制加速消散，避免长期卡在高位；持续上升且已较高时适度抑制继续上涨。
    try:
        from backend.game.emotion_engine import get_emotion_trend
        anger_trend = get_emotion_trend(char, 'anger', window=3)
        if char.anger > 85:
            # 愤怒极高 → 强制加速消散
            changes['anger'] -= random.uniform(2.0, 5.0)
        elif anger_trend > 3.0 and char.anger > 60:
            # 愤怒持续上升且已较高 → 抑制继续上涨
            changes['anger'] -= random.uniform(0.5, 2.0)
    except Exception:
        pass  # 情绪趋势检测失败不影响正常 tick

    # tick 衰减是系统自动变化，跳过情绪惯性
    applied = apply_attr_changes(changes, skip_inertia=True)

    # 触发低属性事件
    if char.hunger > 80:
        apply_attr_changes({'mood': -5, 'energy': -5})
    if char.energy < 15:
        apply_attr_changes({'health': -3, 'mood': -3})
    if char.loneliness > 80:
        apply_attr_changes({'mood': -5, 'motivation': -5})

    return applied


def get_status_summary():
    char = get_character()
    mood_map = {range(0, 20): '非常低落', range(20, 40): '有些难过',
                range(40, 60): '平静', range(60, 80): '开心', range(80, 101): '非常快乐'}
    mood_text = next(v for k, v in mood_map.items() if int(char.mood) in k)
    stress_map = {range(0, 30): '轻松', range(30, 50): '略微紧张',
                  range(50, 70): '压力较大', range(70, 101): '濒临崩溃'}
    stress_text = next(v for k, v in stress_map.items() if int(char.stress) in k)
    health_map = {range(0, 30): '很差', range(30, 50): '欠佳',
                  range(50, 75): '良好', range(75, 101): '健康'}
    health_text = next(v for k, v in health_map.items() if int(char.health) in k)
    energy_map = {range(0, 30): '疲惫', range(30, 50): '不足',
                  range(50, 75): '正常', range(75, 101): '充沛'}
    energy_text = next(v for k, v in energy_map.items() if int(char.energy) in k)
    hunger_map = {range(0, 30): '饱腹', range(30, 50): '正常',
                  range(50, 75): '饥饿', range(75, 101): '极度饥饿'}
    hunger_text = next(v for k, v in hunger_map.items() if int(char.hunger) in k)
    hygiene_map = {range(0, 30): '脏乱', range(30, 50): '欠佳',
                   range(50, 75): '干净', range(75, 101): '洁净'}
    hygiene_text = next(v for k, v in hygiene_map.items() if int(char.hygiene) in k)
    return (f"心情{mood_text} 压力{stress_text} "
            f"健康{health_text} 精力{energy_text} 饥饿{hunger_text} 卫生{hygiene_text} "
            f"开心{char.joy:.0f} 愤怒{char.anger:.0f} 失望{char.disappointment:.0f} "
            f"无聊{char.boredom:.0f} 充实{char.fulfillment:.0f}")


# ── 注册 tick.update 默认文本到 REGISTRY ──
from backend.game.prompt_registry import REGISTRY as _pr
_pr["tick.update"].default_text = TICK_UPDATE_PROMPT_TEMPLATE
