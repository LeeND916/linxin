"""API 蓝图 — 所有 RESTful API 接口"""
import json
import logging
import math
import os
import time
import traceback
from datetime import datetime
from flask import Blueprint, request, jsonify, Response
from backend.config import beijing_now
from backend.models import db, Character, Friend, Achievement, LLMConfig, EventLog, RelationEvent, CharacterActivityMap, Appearance, OutfitComponent, OutfitPreset, QuickHints, ComfyUIConfig, ComfyUIWorkflow, EmbeddingConfig, MemosConfig, SmallModelConfig
from backend.game.character import get_character, apply_attr_changes, tick_decay, get_status_summary, llm_tick_update, tick_chunk_update, daily_mental_regulation, daily_anger_regression, update_relationship_status, is_sleep_period
from backend.game.relationship import get_all_friends, get_friend, update_relationship
from backend.game.relation_state import update_relation_states
from backend.game.dialogue import process_dialogue, llm_emotional_changes, get_relationship_tier, get_tier_config, scale_effects_by_tier, get_dialogue_extras, reset_dialogue_extras
from backend.chat_history import append_message, load_all_sessions, list_sessions, update_message_effects, total_message_count, get_current_character_name
from backend.game.llm_utils import normalize_api_url, safe_llm_post, fix_ssl_keylog
from backend.game.activity import get_all_locations, get_activities_for_location, move_to_location, perform_activity, create_custom_location
from backend.game.event import trigger_auto_message, check_achievements, get_recent_events, update_friend_relations_llm, get_daily_event_count, get_recent_dialogue, llm_fallback_event
from backend.game.constraints import get_all_active_warnings, resolve_constraint_conflicts
from backend.game import weather
from backend.game.time_audit import audit_game_time_change

api_bp = Blueprint('api', __name__, url_prefix='/api')

logger = logging.getLogger(__name__)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _char_with_relationship_tier(char):
    """将 Character.to_dict() 结果附上当前关系阶梯信息。"""
    data = char.to_dict()
    tier = get_relationship_tier(char)
    tier_cfg = get_tier_config(tier)
    data['relationship_tier'] = {
        'tier': tier,
        'name': tier_cfg['name'],
        'effect_scale': tier_cfg['effect_scale'],
        'composite': round(
            (getattr(char, 'player_trust', 0) or 0) * 0.25 +
            (getattr(char, 'player_affection', 0) or 0) * 0.30 +
            (getattr(char, 'player_respect', 0) or 0) * 0.20 +
            (getattr(char, 'player_intimacy', 0) or 0) * 0.25, 1
        ),
    }
    return data


# ========== 角色状态 ==========

@api_bp.route('/character', methods=['GET'])
def api_get_character():
    char = get_character()
    # 0 角色（如全部删除后）时返回明确失败，避免 _char_with_relationship_tier(None) 触发 500
    if char is None:
        return jsonify({'success': False, 'error': 'no_character', 'data': None})
    return jsonify({'success': True, 'data': _char_with_relationship_tier(char)})


@api_bp.route('/character/attr', methods=['POST'])
def api_update_attr():
    data = request.get_json()
    changes = data.get('changes', {})
    applied = apply_attr_changes(changes)
    char = get_character()
    return jsonify({'success': True, 'applied': applied, 'character': char.to_dict()})


@api_bp.route('/character/update_attrs', methods=['POST'])
def api_update_attrs():
    """GM面板：批量设置角色属性值（绝对数值，非delta）"""
    data = request.get_json()
    attrs = data.get('attrs', {})
    if not attrs:
        return jsonify({'success': False, 'error': 'No attrs provided'}), 400

    # 允许修改的属性白名单（直接 setattr 到同名列的扁平属性）
    allowed_keys = {
        # 玩家关系
        'player_trust', 'player_affection', 'player_respect', 'player_intimacy',
        # 物理状态
        'health', 'energy', 'hunger', 'hygiene',
        # 心理状态
        'mood', 'stress', 'happiness', 'loneliness', 'confidence',
        'motivation', 'creativity', 'joy', 'anger', 'disappointment',
        'boredom', 'fulfillment',
    }
    # 技能属性 → skills JSON 映射（key → JSON 子键）
    skill_key_map = {
        'writing_skill': 'writing',
        'coding_skill': 'coding',
        'social_skill': 'social',
        'learning_skill': 'learning',
        'fitness': 'fitness',
    }
    # 目标进度 → goals JSON 映射
    goal_key_map = {
        'writer_progress': 'writer_progress',
        'coder_progress': 'coder_progress',
    }

    char = get_character()

    # 确保 JSON 字段已从旧列初始化
    if not char.skills:
        char.skills = {
            'writing': char.writing_skill,
            'coding': char.coding_skill,
            'social': char.social_skill,
            'learning': char.learning_skill,
            'fitness': char.fitness,
        }
    if not char.goals:
        char.goals = {
            'writer_progress': char.writer_progress,
            'coder_progress': char.coder_progress,
        }

    # 确保 skills/goals JSON 字段不为 None
    if char.skills is None:
        char.skills = {}
    if char.goals is None:
        char.goals = {}

    applied = {}
    intimacy_changed = False

    for key, value in attrs.items():
        # 跳过不在白名单中的键
        is_allowed = key in allowed_keys
        is_legacy_skill = key in skill_key_map
        is_legacy_goal = key in goal_key_map
        is_dynamic_skill = key.startswith('skill:')
        is_dynamic_goal = key.startswith('goal:')

        if not (is_allowed or is_legacy_skill or is_legacy_goal or is_dynamic_skill or is_dynamic_goal):
            continue

        try:
            val = float(value)
            clamped = max(0.0, min(100.0, val))

            if is_dynamic_skill:
                # 动态技能: key 格式 "skill:diagnosis" → json_key = "diagnosis"
                json_key = key[6:]
                char.skills = {**char.skills, json_key: clamped}
                # 如果该技能有对应的旧列则同步
                if hasattr(char, json_key):
                    setattr(char, json_key, clamped)
                elif hasattr(char, f'{json_key}_skill'):
                    setattr(char, f'{json_key}_skill', clamped)
            elif is_legacy_skill:
                # 旧版技能（向后兼容: writing_skill → writing）
                json_key = skill_key_map[key]
                char.skills = {**char.skills, json_key: clamped}
                old_col = 'fitness' if json_key == 'fitness' else f'{json_key}_skill'
                setattr(char, old_col, clamped)
            elif is_dynamic_goal:
                # 动态目标: key 格式 "goal:novel_publish" → json_key = "novel_publish"
                json_key = key[5:]
                char.goals = {**char.goals, json_key: clamped}
                if hasattr(char, json_key):
                    setattr(char, json_key, clamped)
            elif is_legacy_goal:
                # 旧版目标（向后兼容: writer_progress → writer_progress）
                json_key = goal_key_map[key]
                char.goals = {**char.goals, json_key: clamped}
                setattr(char, key, clamped)
            else:
                setattr(char, key, clamped)

            applied[key] = clamped
            if key == 'player_intimacy':
                intimacy_changed = True
        except (ValueError, TypeError):
            continue

    db.session.commit()
    
    # 显式刷新，确保 to_dict() 读取到最新提交的值
    db.session.refresh(char)

    if intimacy_changed:
        update_relationship_status(char)

    # 触发每日心理调节初始化（但不真正执行衰减）
    daily_mental_regulation()

    return jsonify({'success': True, 'applied': applied, 'character': char.to_dict()})


@api_bp.route('/character/status-summary', methods=['GET'])
def api_status_summary():
    return jsonify({'success': True, 'summary': get_status_summary()})


@api_bp.route('/character/warnings', methods=['GET'])
def api_character_warnings():
    """获取角色当前所有活跃警告"""
    char = get_character()
    return jsonify({
        'success': True,
        'warnings': get_all_active_warnings(char)
    })


# ========== 游戏 Tick ==========

def _process_daily_end(char, day_offset, old_game_day,
                       new_game_day, new_game_hour, new_game_minute,
                       total_minutes, days_passed,
                       skip_random_event, skip_tick_llm, llm_config):
    """处理一个游戏天的收尾逻辑：换装/天气/衰减/事件。
    
    返回 (events_generated: list, outfit_changed: bool)
    后续阶段会在此函数中集成 ConditionMatcher、relation_state_decay 等。
    """
    from backend.game import weather as _weather
    from datetime import date as dt_date

    current_day = old_game_day + day_offset
    char.game_day = current_day
    # 提交挂起改动，避免写事务跨过后续 daily_outfit_cycle / tick LLM 等待（并发安全）
    db.session.commit()
    events = []
    outfit_changed = False

    # 换装：跨越到新的一天时触发
    if day_offset > 0:
        from backend.game.wardrobe import daily_outfit_cycle
        daily_outfit_cycle(char, new_game_hour)
        outfit_changed = True

        # 生成天气
        try:
            yr2, mo2, dy2, _ = char.get_game_date()
            game_date_obj2 = dt_date(yr2, mo2, dy2)
            w = _weather.generate_daily_weather(game_date_obj2, character=char)
            char.weather = w.weather_name
        except Exception as e:
            print(f"[Weather] 生成天气失败: {e}")

    # 剧本时钟：检查今天是否有 narrative_schedule 预定事件
    try:
        from backend.game.world_event_clock import WorldEventClock
        clock = WorldEventClock(char.name)
        clock_events = clock.check_and_fire(char)
        for ce in clock_events:
            if isinstance(ce, dict):
                events.append({'event': ce, 'state_changes': [], 'relation_changes': []})
    except Exception as e:
        print(f"[WorldEventClock] 触发失败（不影响运行）: {e}")

    # 计算当天实际经过的小时数
    if days_passed == 0:
        actual_hours = total_minutes / 60.0
    elif day_offset == 0:
        actual_hours = 24.0 - char.game_hour - char.game_minute / 60.0
    elif day_offset == days_passed:
        actual_hours = new_game_hour + new_game_minute / 60.0
    else:
        actual_hours = 24.0
    actual_hours = min(24.0, max(0.01, actual_hours))

    # 衰减 — 按 4 小时块拆分
    daily_mental_regulation()
    daily_anger_regression()  # 每日把愤怒小幅回归基线（与 daily_mental_regulation 不同：每次结算都跑）
    TICK_CHUNK_HOURS = 4.0
    remaining_hours = actual_hours
    sim_hour = int(char.game_hour)
    llm_hours = 0.0
    sleep_hours = 0.0
    awake_start_hour = None
    awake_start_minute = 0

    while remaining_hours > 0:
        chunk = min(TICK_CHUNK_HOURS, remaining_hours)
        chunk = max(0.25, chunk)
        if is_sleep_period(int(sim_hour)):
            sleep_hours += chunk
        else:
            if awake_start_hour is None:
                awake_start_hour = int(sim_hour)
                awake_start_minute = 0
            llm_hours += chunk
        sim_hour = int((sim_hour + chunk) % 24)
        remaining_hours -= chunk

    # ── 关系状态与意图：必须在 LLM tick 之前执行 ──
    # 先衰减朋友关系状态（孤独感增长等），再据此检测互动意图并生成 relation_initiated 事件，
    # 使随后的 LLM tick 能在 prompt 的【最近事件】中看到"朋友需要陪伴"等意图，从而驱动角色做出回应。
    try:
        update_relation_states(char)
    except Exception as e:
        logger.warning(f"[DailyEnd] 关系衰减失败（不影响推进）: {e}")

    # 关系意图检测 — 按关系对象状态触发互动事件（不随 skip_random_event 跳过）
    try:
        from backend.game.relation_intent_detector import detect_relation_intents
        candidates = detect_relation_intents(char)
        for rel_obj, trigger_id in candidates:
            label = _get_relation_labels(char).get(trigger_id, {})
            evt = EventLog(
                event_type='relation_initiated',
                event_category='relation_initiated',
                title=label.get('title', f'与{rel_obj.name}的互动'),
                description=label.get('desc', '').format(name=char.name, friend_name=rel_obj.name),
                effects='{}',
                game_day=char.game_day,
                game_time=f"{int(char.game_hour):02d}:{int(char.game_minute):02d}:00",
                character_name=char.name,
                location=label.get('location', ''),
            )
            db.session.add(evt)
            db.session.commit()
            events.append({'event': evt.to_dict(), 'state_changes': [], 'relation_changes': []})
    except Exception as e:
        logger.warning(f"关系意图检测失败（不影响运行）: {e}")

    # 睡眠时段走硬编码衰减
    if sleep_hours > 0:
        saved_hour = char.game_hour
        char.game_hour = int(char.game_hour)
        result = None
        # 提交挂起改动，避免写事务跨过 tick LLM 等待（并发安全）
        db.session.commit()
        if not skip_tick_llm and llm_config and llm_config.get('api_key'):
            try:
                result = tick_chunk_update(llm_config, tick_delta_hours=sleep_hours)
            except Exception as e:
                logger.warning(f"[DailyEnd] LLM 睡眠衰减调用失败，回退无LLM衰减: {e}")
                result = None
        if result is None:
            tick_count = max(1, round(sleep_hours))
            for _ in range(tick_count):
                tick_decay()
        char.game_hour = saved_hour
        if result and result.get('event'):
            evt = result['event']
            if isinstance(evt, dict):
                events.append({
                    'event': evt,
                    'state_changes': evt.get('state_changes', []),
                    'relation_changes': evt.get('relation_changes', []),
                })

    # 非睡眠时段合并为一次 LLM 调用（用真实 awake 起点，不再污染 char.game_hour）
    if llm_hours > 0:
        start_h = awake_start_hour if awake_start_hour is not None else int(char.game_hour)
        start_m = awake_start_minute if awake_start_hour is not None else int(char.game_minute)
        result = None
        # 提交挂起改动，避免写事务跨过 tick LLM 等待（并发安全）
        db.session.commit()
        if not skip_tick_llm and llm_config and llm_config.get('api_key'):
            try:
                # 传入真实 awake 起点：tick_chunk_update 据此判断睡眠分支（而非被污染的 char.game_hour），
                # 心跳事件戳=起点+时段、提示词起止与戳一致。
                result = tick_chunk_update(llm_config, tick_delta_hours=llm_hours,
                                           start_hour=start_h, start_minute=start_m)
            except Exception as e:
                logger.warning(f"[DailyEnd] LLM 清醒衰减调用失败，回退无LLM衰减: {e}")
                result = None
        if result is None:
            tick_count = max(1, round(llm_hours))
            for _ in range(tick_count):
                tick_decay()
        if result and result.get('event'):
            evt = result['event']
            if isinstance(evt, dict):
                events.append({
                    'event': evt,
                    'state_changes': evt.get('state_changes', []),
                    'relation_changes': evt.get('relation_changes', []),
                })

    # 设置目标时间
    char.game_hour = int(sim_hour)

    # 事件生成：ConditionMatcher（硬规则）→ 未命中则 LLM 兜底
    if not skip_random_event:
        # 提交挂起改动，避免写事务跨过 LLM 兜底事件生成的等待（并发安全）
        db.session.commit()
        try:
            from backend.game.condition_matcher import ConditionMatcher
            matcher = ConditionMatcher(char.name)
            matched = matcher.match_all(char)
            if matched:
                for tmpl in matched:
                    evt = matcher.fire_event(char, tmpl)
                    if evt:
                        events.append({'event': evt, 'state_changes': [], 'relation_changes': []})
            else:
                # 未命中规则 → LLM 兜底
                try:
                    from backend.game.event import llm_fallback_event
                    evt = llm_fallback_event(
                        llm_config,
                        game_day=current_day,
                        game_time=f"{int(char.game_hour):02d}:{int(char.game_minute):02d}:00",
                        daily_event_count=get_daily_event_count(current_day)
                    )
                    if evt:
                        events.append(evt)
                except Exception as e:
                    logger.warning(f"LLM 兜底事件生成失败: {e}")
        except Exception as e:
            logger.warning(f"[DailyEnd] 条件事件匹配失败（不影响推进）: {e}")

    return events, outfit_changed


# ── 关系事件标签映射（按职业分场景）──
_PROFESSION_SCENES = {
    'default': {
        'common': '办公室', 'work': '工位', 'meeting': '会议室',
        'rest': '家', 'outdoor': '公园', 'social': '咖啡厅',
    },
    'legal': {
        'common': '律所', 'work': '法院', 'meeting': '会议室',
        'rest': '家', 'outdoor': '商业街', 'social': '咖啡厅',
    },
    'medical': {
        'common': '医院', 'work': '诊室', 'meeting': '会议室',
        'rest': '宿舍', 'outdoor': '公园', 'social': '咖啡厅',
    },
    'designer': {
        'common': '工作室', 'work': '设计工坊', 'meeting': '会议室',
        'rest': '公寓', 'outdoor': '艺术区', 'social': '画廊',
    },
    'student': {
        'common': '校园', 'work': '教学楼', 'meeting': '教室',
        'rest': '宿舍', 'outdoor': '校园', 'social': '咖啡厅',
    },
    'tech': {
        'common': '科技园', 'work': '工位', 'meeting': '会议室',
        'rest': '公寓', 'outdoor': '园区', 'social': '咖啡厅',
    },
    'academic': {
        'common': '校园', 'work': '研究室', 'meeting': '会议室',
        'rest': '宿舍', 'outdoor': '图书馆', 'social': '咖啡厅',
    },
    'finance': {
        'common': '写字楼', 'work': '办公室', 'meeting': '会议室',
        'rest': '家', 'outdoor': '金融街', 'social': '高级餐厅',
    },
    'media': {
        'common': '电视台', 'work': '演播室', 'meeting': '会议室',
        'rest': '家', 'outdoor': '外景地', 'social': '咖啡厅',
    },
}


def _get_relation_labels(char):
    """
    根据角色职业返回合适的关系事件标签。

    匹配顺序：major（更稳定）-> identity_label（更具体）-> 兜底 default。
    新增角色只需正确设置 major 字段即可自动匹配场景。
    """
    major = (char.major or '')
    label = (char.identity_label or '')
    scenes = _PROFESSION_SCENES['default']
    if any(k in major for k in ['法律', '法', '律师', '司法']):
        scenes = _PROFESSION_SCENES['legal']
    elif any(k in major for k in ['医', '临床', '药学', '护理']):
        scenes = _PROFESSION_SCENES['medical']
    elif any(k in major for k in ['设计', '艺术', '绘画', '油画', '美术', '游戏']):
        scenes = _PROFESSION_SCENES['designer']
    elif any(k in major for k in ['计算机', '软件', '编程', '科技', '信息', '互联网']):
        scenes = _PROFESSION_SCENES['tech']
    elif any(k in major for k in ['文学', '语言', '新闻', '传播', '心理', '教育', '哲学', '历史']):
        scenes = _PROFESSION_SCENES['academic']
    elif any(k in major for k in ['金融', '经济', '会计', '管理', '商业', '贸易']):
        scenes = _PROFESSION_SCENES['finance']
    elif any(k in major for k in ['学生', '在读', '学习']):
        scenes = _PROFESSION_SCENES['student']
    elif any(k in label for k in ['律师', '法律', '法务', '法官', '检察官']):
        scenes = _PROFESSION_SCENES['legal']
    elif any(k in label for k in ['医生', '医师', '护士']):
        scenes = _PROFESSION_SCENES['medical']
    elif any(k in label for k in ['设计', '艺术', '画家', '画师', '游戏', '制作人']):
        scenes = _PROFESSION_SCENES['designer']
    elif any(k in label for k in ['学生', '研究生', '大学生', '教授', '教师', '导师']):
        scenes = _PROFESSION_SCENES['student']
    elif any(k in label for k in ['记者', '传媒', '主播', '编辑']):
        scenes = _PROFESSION_SCENES['media']
    s = scenes
    return {
        'friend_needs_company': {
            'title': '朋友需要陪伴',
            'desc': '{friend_name} 感到有些孤独，想找{name}聊聊天。',
            'location': s['common'],
        },
        'friend_shares_joy': {
            'title': '朋友分享快乐',
            'desc': '{friend_name} 遇到了开心的事，兴冲冲地跑来告诉{name}。',
            'location': s['common'],
        },
        'rival_encounter': {
            'title': '狭路相逢',
            'desc': '{name} 在' + s['common'] + '遇到了{friend_name}，气氛有些微妙。',
            'location': s['common'],
        },
        'rival_setback': {
            'title': '对手受挫',
            'desc': '{friend_name} 最近表现不佳，看起来有些沮丧。',
            'location': s['work'],
        },
        'mentor_advice': {
            'title': '导师关怀',
            'desc': '{friend_name} 注意到了{name}的压力，主动给予指导和建议。',
            'location': s['meeting'],
        },
        'family_call': {
            'title': '家人来电',
            'desc': '{friend_name} 打电话来关心{name}的近况。',
            'location': s['rest'],
        },
        'colleague_conflict': {
            'title': '同事摩擦',
            'desc': '{name} 和{friend_name}在工作上有了一些分歧。',
            'location': s['work'],
        },
        'ex_boyfriend_memory': {
            'title': '回忆往事',
            'desc': '{name} 不经意间想起了和{friend_name}的过去。',
            'location': s['outdoor'],
        },
        'ex_boyfriend_encounter': {
            'title': '前任偶遇',
            'desc': '{name} 在路上遇到了{friend_name}，两人都有些尴尬。',
            'location': s['outdoor'],
        },
        'enemy_intrigue': {
            'title': '暗流涌动',
            'desc': '{name} 感觉到{friend_name}似乎在暗中策划着什么。',
            'location': s['common'],
        },
        'client_meeting': {
            'title': '客户约谈',
            'desc': '{friend_name} 约{name}见面讨论项目进展。',
            'location': s['social'],
        },
        'protege_success': {
            'title': '徒弟进步',
            'desc': '{name} 指导的{friend_name}取得了不错的进展，令人欣慰。',
            'location': s['work'],
        },
    }

@api_bp.route('/tick/batch', methods=['POST'])
def api_tick_batch():
    """批量推进游戏时间（加速器模式），前端加速结束后一次性提交。
    
    请求体：
    {
        "game_minutes_to_advance": 120,
        "final_game_day": 3,
        "final_game_hour": 14,
        "final_game_minute": 30
    }
    """
    from backend.game import weather as _weather
    from datetime import date as dt_date

    char = get_character()
    if not char:
        return jsonify({'success': False, 'error': 'No character found'}), 404

    data = request.get_json() or {}
    total_minutes = data.get('game_minutes_to_advance', 0)
    total_minutes = max(0, min(total_minutes, 1440 * 30))  # 最多30天

    # 计算当前游戏总分钟和新的目标时间
    current_total = char.game_day * 24 * 60 + char.game_hour * 60 + char.game_minute
    new_total = current_total + total_minutes
    new_game_day = new_total // (24 * 60)
    remaining = new_total % (24 * 60)
    new_game_hour = remaining // 60
    new_game_minute = remaining % 60

    # 不再使用前端 final_game_day 覆盖 —— 多客户端场景下前端传来的
    # 值可能是另一个客户端的陈旧内存值，覆盖会导致竞态（如 A 端设到 day=30，
    # B 端 tick 仍带着 day≈18 的 final_game_day 把 DB 写回 18）。
    # 始终以 DB 当前值 + 推进分钟数为准，前端本地时钟偏差由 3 秒轮询自动修正。
    old_game_day = char.game_day
    days_passed = new_game_day - old_game_day

    # 加速跳过开关
    skip_random_event = data.get('skip_random_event', False)
    skip_tick_llm     = data.get('skip_tick_llm', False)
    skip_auto_message = data.get('skip_auto_message', False)
    skip_friend_update = data.get('skip_friend_update', False)

    all_events = []
    relation_changes = None
    outfit_changed = False

    # 获取 LLM 配置
    llm_config_db = LLMConfig.query.filter_by(is_active=True).first()
    llm_config = llm_config_db.to_secret_dict() if llm_config_db else None

    # 首次初始化穿搭
    if char.current_outfit == '{}' or not char.current_outfit:
        try:
            from backend.game.wardrobe import assemble_daily_outfit
            first_outfit = assemble_daily_outfit(char)
            char.current_outfit = json.dumps(first_outfit, ensure_ascii=False)
            char.outfit_changed_at = char.game_day
            db.session.commit()
        except Exception as e:
            logger.warning(f"[DailyEnd] 首次穿搭初始化失败（不影响推进）: {e}")

    # 按天处理：每天做衰减、换装、天气、事件
    # 快照角色初始状态（用于计算 state_changes）
    _tracked_attrs = [
        'health', 'energy', 'hunger', 'hygiene',
        'mood', 'stress', 'happiness', 'loneliness', 'confidence',
        'motivation', 'creativity', 'joy', 'anger', 'disappointment',
        'boredom', 'fulfillment',
        'writing_skill', 'coding_skill', 'social_skill', 'learning_skill', 'fitness',
        'player_trust', 'player_affection', 'player_respect', 'player_intimacy',
        'writer_progress', 'coder_progress',
    ]
    _snapshot_before = {}
    for attr in _tracked_attrs:
        val = getattr(char, attr, None)
        if val is not None:
            _snapshot_before[attr] = round(val)

    # 时间变动审计：批量推进前记录旧天数 -> 目标天数（此时 char 仍为旧值）
    try:
        audit_game_time_change(
            char, new_game_day, new_game_hour, new_game_minute, 'batch_advance',
            extra=f"total_minutes={total_minutes} days_passed={days_passed} "
                  f"old=D{old_game_day} final_game_day={data.get('final_game_day')}"
        )
    except Exception as e:
        logger.warning(f"[DailyEnd] 时间审计异常（不影响推进）: {e}")

    # 逐天推进
    for day_offset in range(days_passed + 1):
        day_events, day_outfit = _process_daily_end(
            char, day_offset, old_game_day,
            new_game_day, new_game_hour, new_game_minute,
            total_minutes, days_passed,
            skip_random_event, skip_tick_llm, llm_config,
        )
        all_events.extend(day_events)
        if day_outfit:
            outfit_changed = True

    # 更新角色时间
    char.game_day = new_game_day
    char.game_hour = new_game_hour
    char.game_minute = new_game_minute
    db.session.commit()

    # 事件全部生成完毕，从 EventLog 中取最新事件的 location 写入 char
    latest = EventLog.query \
        .filter(EventLog.location != '', EventLog.character_name == char.name) \
        .order_by(EventLog.game_day.desc(), EventLog.game_time.desc(), EventLog.id.desc()) \
        .first()
    if latest:
        char.location = latest.location
        db.session.commit()

    # 任务推进（MissionManager）
    try:
        # 提交挂起改动，避免写事务跨过任务推进 / 新闻注入 / 关系更新 LLM 等待（并发安全）
        db.session.commit()
        from backend.game.mission_manager import MissionManager
        mm = MissionManager(char.name)
        mission_events = mm.advance(char)
        for me in mission_events:
            if me:
                all_events.append({'event': me, 'state_changes': [], 'relation_changes': []})
    except Exception as e:
        logger.warning(f"任务推进失败（不影响运行）: {e}")

    # 新闻资讯注入
    try:
        # 提交挂起改动，避免写事务跨过新闻注入 LLM 等待（并发安全）
        db.session.commit()
        from backend.game.news_service import inject_news_for_character
        inject_news_for_character(char)
    except Exception as e:
        logger.debug(f"新闻注入跳过: {e}")

    # LLM 驱动朋友关系更新
    relation_changes = None
    try:
        # 提交挂起改动，避免写事务跨过朋友关系 LLM 更新等待（并发安全）
        db.session.commit()
        if not skip_friend_update and llm_config and llm_config.get('api_key') and all_events:
            relation_changes = update_friend_relations_llm(char, all_events, llm_config)
    except Exception as e:
        logger.warning(f"[DailyEnd] 朋友关系更新失败（不影响推进）: {e}")

    # 主动消息（仅在跨越天数时生成）
    auto_message = None
    try:
        # 提交挂起改动，避免写事务跨过主动消息 LLM 生成等待（并发安全）
        db.session.commit()
        if not skip_auto_message and days_passed > 0:
            auto_message = trigger_auto_message(
                llm_config,
                game_day=new_game_day,
                game_time=f"{new_game_hour:02d}:{new_game_minute:02d}:00"
            )
    except Exception as e:
        logger.warning(f"[DailyEnd] 主动消息生成失败（不影响推进）: {e}")

    # 成就检查
    try:
        achievements = check_achievements()
    except Exception as e:
        logger.warning(f"[DailyEnd] 成就检查失败（不影响推进）: {e}")
        achievements = []

    # 条件型成就：按角色状态（技能/属性阈值等）轮询解锁
    try:
        from backend.game.achievement_checker import check_condition_achievements
        cond_unlocked = check_condition_achievements(char)
        if cond_unlocked:
            logger.info(f"[DailyEnd] 条件成就解锁: {[a.name for a in cond_unlocked]}")
    except Exception as e:
        logger.warning(f"[DailyEnd] 条件成就检查失败（不影响运行）: {e}")

    # 目标进度：按 goal_rules 推进（LLM 初始化目标的解锁规则）
    try:
        from backend.game.achievement_checker import evaluate_goals
        evaluate_goals(char)
    except Exception as e:
        logger.warning(f"[DailyEnd] 目标评估失败（不影响运行）: {e}")

    # 计算角色状态变化（批量推进前后对比）
    state_changes = []
    for attr in _tracked_attrs:
        new_val = getattr(char, attr, None)
        old_val = _snapshot_before.get(attr)
        if new_val is not None and old_val is not None:
            new_val = round(new_val)
            delta = new_val - old_val
            if delta != 0:
                state_changes.append({
                    'attribute': attr,
                    'old': old_val,
                    'new': new_val,
                    'delta': delta
                })

    # 构建返回
    resp_char = get_character().to_dict()
    resp_char['game_minute'] = int(new_game_minute)

    yr, mo, dy, wd_name = char.get_game_date()
    game_date = dt_date(yr, mo, dy)
    is_weekend_flag = weather.is_weekend(game_date.weekday())

    # 构建 events 输出：批量推进可能跨多天（days_passed>=1），需覆盖 old_game_day..new_game_day 整段区间，
    # 否则中间天的心跳事件虽已落库却不会随响应回传，导致事件面板只显示最后一天（见 issue: 多天批量推进事件截断）
    try:
        events_out = _get_events_from_db(
            game_day_from=old_game_day,
            game_day_to=new_game_day,
            limit=200
        )
    except Exception as e:
        logger.warning(f"[DailyEnd] 事件查询失败（不影响推进）: {e}")
        events_out = []

    # 活跃警告查询（独立保护，避免单点失败导致整批推进失败）
    try:
        _active_warnings = get_all_active_warnings(char)
    except Exception as e:
        logger.warning(f"[DailyEnd] 警告查询失败（不影响推进）: {e}")
        _active_warnings = []

    return jsonify({
        'success': True,
        'character': resp_char,
        'events': events_out,
        'state_changes': state_changes,
        'relation_changes': relation_changes,
        'auto_message': auto_message,
        'new_achievements': [a.to_dict() if hasattr(a, 'to_dict') else a for a in achievements],
        'game_day': new_game_day,
        'game_hour': new_game_hour,
        'game_minute': new_game_minute,
        'days_passed': days_passed,
        'outfit_changed': outfit_changed,
        'is_weekend': is_weekend_flag,
        'weekday': wd_name,
        'game_date_display': char.get_game_date_display(),
        'active_warnings': [w['message'] for w in _active_warnings],
    })


# ========== 朋友关系 ==========

@api_bp.route('/friends', methods=['GET'])
def api_get_friends():
    friends = get_all_friends()
    return jsonify({'success': True, 'data': [f.to_dict() for f in friends]})


@api_bp.route('/friends/<int:friend_id>', methods=['GET'])
def api_get_friend(friend_id):
    f = get_friend(friend_id)
    if not f:
        return jsonify({'success': False, 'error': 'Friend not found'}), 404
    return jsonify({'success': True, 'data': f.to_dict()})


@api_bp.route('/friends/<int:friend_id>/interact', methods=['POST'])
def api_interact_friend(friend_id):
    data = request.get_json() or {}
    result = update_relationship(
        friend_id,
        closeness_delta=data.get('closeness', 0),
        trust_delta=data.get('trust', 0),
        affection_delta=data.get('affection', 0)
    )
    if not result:
        return jsonify({'success': False, 'error': 'Friend not found'}), 404
    return jsonify({'success': True, 'data': result})


# ========== 对话系统 ==========

@api_bp.route('/dialogue', methods=['POST'])
def api_dialogue():
    data = request.get_json()
    # P1 注入防护：剥离玩家消息里 [系统指令]/[指令]/system 等伪系统命令（详见职业关系豁免改造方案 P1）
    from backend.game.dialogue import sanitize_user_message
    user_message = sanitize_user_message(data.get('message', ''))
    # 历史里前端已 push 的玩家消息同样清洗，避免注入残留进 LLM 上下文
    history = [
        ({**m, 'content': sanitize_user_message(m.get('content', ''))}
         if m.get('role') == 'user' else m)
        for m in (data.get('history', []) or [])
    ]
    is_new_session = data.get('is_new_session', False)

    if not user_message.strip():
        return jsonify({'success': False, 'error': 'Message is empty'}), 400

    # ── 聊天生图：玩家侧意图评估（在生成回复前，满足 §6.4 同轮注入 + §1.2 同步记忆两写）──
    _char = get_character()
    _photo = None
    _photo_injection = None
    _photo_reject = None
    if _char:
        _gd = int(data.get('game_day', getattr(_char, 'game_day', 0)) or 0)
        _gm = int(data.get('game_hour', 0) or 0) * 60 + int(data.get('game_minute', 0) or 0)
        _gt = (data.get('game_time') or getattr(_char, 'game_time', '') or '')
        try:
            from backend.game.media_intent import evaluate_media_intent
            from backend.game.photo_gen import trigger_photo_generation, build_photo_injection
            _pdec = evaluate_media_intent(_char, user_message, side='player',
                                          game_day=_gd, game_minute=_gm)
            if _pdec.get('triggered') and _pdec.get('scene_type') != 'share_recall':
                if _pdec.get('scene_type') == 'outfit_change':
                    from backend.game.wardrobe import apply_outfit, resolve_outfit_request
                    from backend.game.photo_gen import _outfit_desc
                    from backend.models import OutfitComponent
                    # 先保存换装前快照；apply_outfit 后角色表只保留新穿搭，
                    # 对比图左侧必须继续使用这个旧状态。
                    _pdec['outfit_old'] = _outfit_desc(_char)
                    _requested_outfit = _pdec.get('outfit_name')
                    _selected_outfit = resolve_outfit_request(_char, _requested_outfit, user_message)
                    if _selected_outfit:
                        # 是否换的是内衣：局部替换且部件类型为 underwear → 右侧露出内衣。
                        _reveal = False
                        _new_uw = ''
                        if _requested_outfit:
                            _comp = OutfitComponent.query.filter_by(
                                character_id=_char.id, name=_requested_outfit).first()
                            if _comp and _comp.type == 'underwear':
                                _reveal = True
                                _new_uw = _comp.name
                        _selected_outfit = apply_outfit(_char, _selected_outfit, reason='chat_explicit_outfit')
                        _pdec['outfit_name'] = _selected_outfit.get('name', '')
                        if _reveal:
                            _pdec['outfit_new'] = f'脱去上衣下装与外套，仅身穿{_new_uw}展示贴身内衣'
                        else:
                            _pdec['outfit_new'] = _outfit_desc(_char)
                        _pdec['reveal'] = _reveal
                    else:
                        _pdec['triggered'] = False
                        _pdec['reject_context'] = '[系统：没有找到适合当前场景的真实角色穿搭]'
                if not _pdec.get('triggered'):
                    _photo_reject = _pdec.get('reject_context')
                else:
                    _photo = trigger_photo_generation(
                        _char, _pdec, user_message=user_message,
                        recent_dialogue=user_message, game_day=_gd, game_time=_gt,
                        game_minute=_gm)
                if _photo:
                    _sm = _photo.get('scene_memo') or ''
                    _photo_injection = build_photo_injection(_pdec.get('scene_type'), _sm)
            elif _pdec.get('reject_context'):
                _photo_reject = _pdec['reject_context']
        except Exception as _pe:
            logger.warning(f"[Dialogue] 聊天生图(玩家侧)触发异常（不影响对话）: {_pe}")

    result = process_dialogue(
        user_message, history,
        is_new_session=is_new_session,
        game_day_override=data.get('game_day'),
        game_hour_override=data.get('game_hour'),
        game_minute_override=data.get('game_minute'),
        game_second_override=data.get('game_second'),
        location_override=data.get('location'),
        photo_injection=_photo_injection,
        photo_reject=_photo_reject,
        thinking_enabled=bool(data.get('thinking_enabled', False)),
    )

    # 对话完成后，将本轮玩家消息 + 角色回复追加到当前 4 小时时段的 MD 文件
    chat_saved = True
    try:
        # 玩家消息的游戏时间：来自请求中的实时游戏时间
        player_game_day = data.get('game_day', 1)
        player_game_hour = int(data.get('game_hour', 8))
        player_game_minute = int(data.get('game_minute', 0))
        player_game_second = int(data.get('game_second', 0))
        player_game_time_str = f"第{player_game_day}天 {player_game_hour:02d}:{player_game_minute:02d}:{player_game_second:02d}"

        # 角色回复的游戏时间：来自对话处理结果
        char_game_day = result.get('game_day', 1)
        char_game_time = result.get('game_time', '08:00:00')
        char_game_time_str = f"第{char_game_day}天 {char_game_time}"

        # 关联照片：聊天生图命中时，把照片 id 持久化到本轮角色回复的消息里，
        # 刷新对话流后可还原照片卡片（P0-1）。
        _photo_payload = {'id': _photo['id']} if _photo and isinstance(_photo, dict) and _photo.get('id') else None

        # 写入前先记录当前消息总数，写完后角色回复的索引 = 写入前总数 + 1
        count_before = total_message_count()
        _photo_context = bool(_photo)
        append_message('player', user_message, game_time_str=player_game_time_str,
                       photo_context=_photo_context)
        append_message('character', result.get('reply', ''), game_time_str=char_game_time_str,
                       reasoning=result.get('reasoning', ''), effects=result.get('effects'),
                       raw_reply=result.get('raw_reply', ''), photo=_photo_payload,
                       photo_context=_photo_context)
        # 角色回复在合并消息列表中的后端索引（用于前端「重新分析情绪」精确定位）
        result['message_index'] = count_before + 1
    except Exception as e:
        import traceback
        print(f"[ChatHistory] 写入 MD 失败: {e}")
        traceback.print_exc()
        chat_saved = False

    response_data = {'success': True, 'chat_saved': chat_saved, **result}

    # ── 聊天生图：女主回复侧（玩家侧已在生成回复前处理；此处仅扫 share_new / share_recall）──
    # 玩家侧命中时跳过；share_recall 仅检索真实照片不生图、不注入（§6.4 注意点2）。
    try:
        from backend.game.media_intent import evaluate_media_intent
        from backend.game.photo_gen import trigger_photo_generation, search_recall_photos
        if _char and not _photo:
            _gd = int(data.get('game_day', getattr(_char, 'game_day', 0)) or 0)
            _gm = int(data.get('game_hour', 0) or 0) * 60 + int(data.get('game_minute', 0) or 0)
            _gt = result.get('game_time', getattr(_char, 'game_time', '')) or ''
            _rdec = evaluate_media_intent(_char, result.get('reply', ''), side='reply',
                                          game_day=_gd, game_minute=_gm)
            if _rdec.get('retrieve_only') and _rdec.get('scene_type') == 'share_recall':
                response_data['recall_photos'] = search_recall_photos(_char.name, limit=6)
            elif _rdec.get('triggered'):
                _photo = trigger_photo_generation(
                    _char, _rdec, user_message=user_message,
                    recent_dialogue=result.get('reply', ''), game_day=_gd, game_time=_gt,
                    game_minute=_gm)
                if _photo:
                    from backend.chat_history import mark_latest_photo_context, update_message_photo
                    mark_latest_photo_context(character_name=_char.name)
                    # P0 修复：回复侧触发生图后，把照片身份证号补写回本轮角色回复的 MD 段，
                    # 否则刷新聊天流时 load_all_sessions 解析不出 photo 字段，照片卡片丢失。
                    update_message_photo(result.get('message_index'), _photo['id'])
        if _photo:
            response_data['photo'] = _photo
        if _photo_reject:
            response_data['photo_reject'] = _photo_reject
    except Exception as _pe:
        logger.warning(f"[Dialogue] 聊天生图(回复侧)触发异常（不影响对话）: {_pe}")

    return jsonify(response_data)


@api_bp.route('/dialogue/extras', methods=['GET'])
def api_dialogue_extras():
    """获取对话后异步操作的结果（快捷建议、记忆提示、情感时刻）。
    返回每项独立 ready 标志：{hints_ready, memory_ready, emotion_ready, ...}。
    传入 ?clear=1 时清除上一轮缓存（页面刷新时调用）。"""
    if request.args.get('clear') == '1':
        reset_dialogue_extras()
    extras = get_dialogue_extras()
    return jsonify({'success': True, **extras})


@api_bp.route('/dialogue/hints', methods=['GET'])
def api_dialogue_hints():
    """从 DB 读取当前角色最新一轮的快捷建议（页面刷新时调用）。"""
    char = get_character()
    if not char:
        return jsonify({'success': True, 'hints': []})
    row = QuickHints.query.filter_by(character_name=char.name).order_by(QuickHints.id.desc()).first()
    if not row:
        return jsonify({'success': True, 'hints': []})
    return jsonify({'success': True, 'hints': row.hints or [], 'game_day': row.game_day, 'game_time': row.game_time})


@api_bp.route('/dialogue/recommend', methods=['POST'])
def api_dialogue_recommend():
    """LLM 驱动的快捷对话推荐：根据女主状态 + 玩家关系规则 → 推荐 5 句话"""
    data = request.get_json()
    relation_type = data.get('relation_type', 'trust')

    # 校验 relation_type
    valid_types = ['trust', 'liking', 'respect', 'intimacy']
    if relation_type not in valid_types:
        return jsonify({'success': False, 'error': f'Invalid relation_type: {relation_type}'}), 400

    # 关系类型中文映射
    relation_cn = {
        'trust': '信任',
        'liking': '好感',
        'respect': '尊重',
        'intimacy': '亲密'
    }

    char = get_character()

    # 读取最近聊天记录
    recent_dialogue = get_recent_dialogue(20)

    # 读取今日事件（最近 5 条）
    recent_events_text = ""
    if char.game_day > 0:
        events = EventLog.query.filter(
            EventLog.game_day == char.game_day
        ).order_by(EventLog.created_at.desc()).limit(5).all()
        if events:
            lines = []
            for e in reversed(events):
                time_str = e.game_time or '--:--'
                time_short = time_str[:5] if len(time_str) >= 5 else time_str
                lines.append(f"{time_short} {e.title}: {e.description}")
            recent_events_text = "\n".join(lines)

    # 玩家关系变化规则（从 dialogue.py 提取）
    _player_identity = getattr(char, 'player_identity', '导师') or '导师'
    _player_nickname = getattr(char, 'player_nickname', _player_identity) or _player_identity
    relation_rules = f"""【{_player_identity}关系变化规则 — 基于人情世故/社交常识判断】
你要像真人一样，基于日常人际交往的社交常识和人情世故来判断与{_player_nickname}（玩家）的关系变化，不要机械套用死板规则：
- 注意对话中的微妙情感变化：语气是温柔还是冷淡、用词是亲密还是疏远、话题是轻松还是沉重。
- {_player_nickname}的关心方式：是温柔耐心的还是敷衍的、是否记得你说过的事、是否在你脆弱时给予支持。
- 双方互动的深度：从日常寒暄到深入交心的转变、分享秘密的程度、是否愿意暴露脆弱面。
- 关系的渐进性：信任是慢慢建立的，好感会因累积的正面互动增长，疏远也会因持续冷淡而加深。
- 冲突后的修复：争吵后的道歉、冷战后破冰，都会影响关系恢复的速度和程度。
- 关系变化参考区间（灵活判断，不机械套用）：
  - {_player_nickname}夸奖你 → player_affection+1~5, player_trust+1~3
  - {_player_nickname}批评你 → player_affection-1~5, player_trust-1~3
  - 你向{_player_nickname}展示学习成果 → player_respect+1~3, player_trust+1~2
  - 分享秘密/心事 → player_intimacy+1~5, player_trust+1~3
  - {_player_nickname}给你布置任务 → player_respect+1~2, motivation+1~5"""

    # 当前时间
    yr, mo, dy, wd = char.get_game_date()

    # ── 使用 PromptManager 统一管理 prompt 模板 ──
    from backend.game.prompt_registry import get_prompt_manager
    pm = get_prompt_manager()
    yr, mo, dy, wd = char.get_game_date()
    prompt = pm.render("dialogue.recommend", char, extra={
        "context.relation_type_cn": relation_cn.get(relation_type, relation_type),
        "context.weekday": wd,
        "context.recent_events": recent_events_text or "（今天还没有特别的事件）",
        "context.recent_dialogue": recent_dialogue or "（还没有对话记录）",
        "context.relation_rules": relation_rules,
        "context.relationship_display": "恋人" if char.relationship_status == "dating" else "普通朋友",
    })

    # 调用 LLM
    llm_config = LLMConfig.query.filter_by(is_active=True).first()
    if not llm_config or not llm_config.api_key:
        return jsonify({'success': False, 'error': 'LLM 未配置，无法生成推荐'}), 503

    result = safe_llm_post(
        api_url=llm_config.api_url,
        api_key=llm_config.api_key,
        model=llm_config.model_name,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
        temperature=0.9,
        timeout=(30, 60),
        call_type="dialogue_recommend",
        character_name=char.name
    )

    if not result:
        return jsonify({'success': False, 'error': 'LLM 调用失败'}), 500

    content = result["choices"][0]["message"]["content"]

    # ── 解析 LLM 返回的推荐结果 ──
    # LLM 可能返回：
    #   (a) 包裹在 Markdown 代码块里的数组：```json\n[...]\n```
    #   (b) 纯 JSON 数组（list[str] 或 list[dict]）
    #   (c) 纯 JSON 数组（list[dict]，每项含 text/reason）
    # 这里统一做健壮解析：去代码块 → 直接 json.loads → 回退正则，
    # 并兼容「字符串数组」与「对象数组」两种格式。
    def _strip_fences(text):
        s = text.strip()
        # 去掉 ```json / ``` 包裹
        if s.startswith('```'):
            s = s.split('\n', 1)[-1] if '\n' in s else s[3:]
            if s.endswith('```'):
                s = s[:-3]
            s = s.strip()
            if s.lower().startswith('json'):
                s = s[4:].strip()
        return s

    def _normalize_item(item):
        """把单条推荐归一化为 {'text':..., 'reason':...}；无法归一化返回 None"""
        if isinstance(item, str):
            t = item.strip()
            return {'text': t, 'reason': ''} if t else None
        if isinstance(item, dict):
            # 优先 text；兼容 content/sentence/msg 等别名
            text = (item.get('text') or item.get('content')
                    or item.get('sentence') or item.get('msg')
                    or item.get('message') or '').strip()
            if not text and len(item) == 1:
                # 形如 {"0": "..."} 的退化结构
                text = str(next(iter(item.values()))).strip()
            if text:
                return {'text': text, 'reason': str(item.get('reason', '') or '').strip()}
        return None

    try:
        import re as _re
        parsed = None
        # 1) 直接解析（去代码块后）
        try:
            parsed = json.loads(_strip_fences(content))
        except Exception:
            parsed = None
        # 2) 回退：正则抽取第一个 [...] 数组
        if not isinstance(parsed, list):
            match = _re.search(r'\[[\s\S]*?\]', content)
            if match:
                try:
                    parsed = json.loads(match.group())
                except Exception:
                    parsed = None

        if isinstance(parsed, list) and len(parsed) >= 1:
            cleaned = []
            for item in parsed[:5]:
                norm = _normalize_item(item)
                if norm:
                    cleaned.append(norm)
            if len(cleaned) >= 1:
                if len(cleaned) < 3:
                    logger.warning(f"对话推荐仅解析到 {len(cleaned)} 条（期望≥3）")
                return jsonify({
                    'success': True,
                    'relation_type': relation_type,
                    'recommendations': cleaned
                })
    except Exception as e:
        logger.warning(f"解析 LLM 推荐结果失败: {e}")

    return jsonify({'success': False, 'error': 'LLM 返回格式异常，请重试'}), 500


# ========== 情绪重新分析 ==========

@api_bp.route('/emotion/analyze', methods=['POST'])
def api_emotion_analyze():
    """手动重新分析某条对话的情绪属性变化。

    请求体: { "message_index": int }
    流程：
      1. 从聊天记录 MD 中读取目标消息（必须是 character 消息）及其 effects_old
      2. 快照当前角色属性 → 精确回滚旧 effects（直接 setattr，绕过 inertia/clamping 失真）
      3. 检查是否存在前一条 player 消息作为 user_message，若存在则传入 LLM
      4. 调用 llm_emotional_changes 生成新 effects
      5. 将新 effects 应用到 Character 模型
      6. 更新聊天记录 MD 中的 effects 字段
      7. 返回新 effects、diff、updated_character
    """
    data = request.get_json()
    if data is None:
        return jsonify({'success': False, 'error': '请求体无效'}), 400

    message_index = data.get('message_index')
    if message_index is None or not isinstance(message_index, int) or message_index < 0:
        return jsonify({'success': False, 'error': '缺少 message_index 或格式无效'}), 400

    # 1. 读取所有消息
    messages = load_all_sessions()
    if message_index >= len(messages):
        return jsonify({'success': False, 'error': f'message_index {message_index} 超出范围（共 {len(messages)} 条）'}), 400

    target_msg = messages[message_index]
    if target_msg.get('speaker') != 'character':
        return jsonify({'success': False, 'error': '目标消息不是角色回复，无法重新分析情绪'}), 400

    effects_old = target_msg.get('effects') or {}
    clean_reply = target_msg.get('content', '')
    snapshot = {}  # 回滚前快照，用于失败时精确恢复

    try:
        # 2. 查找前一条 player 消息作为 user_message
        user_message = ''
        for i in range(message_index - 1, -1, -1):
            if messages[i].get('speaker') == 'player':
                user_message = messages[i].get('content', '')
                break

        # 3. 快照 + 精确回滚旧 effects（绕过 inertia 和平滑，避免负向 apply 失真）
        char = get_character()
        rollback_effects = {}
        for k, v in effects_old.items():
            if isinstance(v, (int, float)):
                current = getattr(char, k, None)
                if current is not None:
                    snapshot[k] = current
                    rollback_effects[k] = v
        # 精确回滚：直接 setattr，绕过 apply_attr_changes 的 inertia + 0-100 钳制失真
        if rollback_effects:
            for k, v in rollback_effects.items():
                current = getattr(char, k, None)
                if current is not None:
                    setattr(char, k, max(0, min(100, current - v)))
            db.session.commit()

        # 4. 获取 LLM 配置和关系阶梯
        llm_config_db = LLMConfig.query.filter_by(is_active=True).first()
        if not llm_config_db or not llm_config_db.api_key:
            # 没有 LLM 配置时，从快照恢复
            if snapshot:
                for k, old_val in snapshot.items():
                    setattr(char, k, old_val)
                db.session.commit()
            return jsonify({'success': False, 'error': 'LLM 未配置，无法重新分析情绪'}), 503

        llm_config = llm_config_db.to_secret_dict()
        tier = get_relationship_tier(char)

        # 5. 调用 LLM 重新分析
        try:
            effects_new = llm_emotional_changes(
                clean_reply=clean_reply,
                user_message=user_message,
                char=char,
                history=None,
                tier=tier,
                skip_intent_shortcut=True,  # 手动重分析强制走小模型：增量表是确定性输出，会与旧值完全相同导致"无变化"
            )
        except Exception as e:
            # 失败时从快照精确恢复
            if snapshot:
                for k, old_val in snapshot.items():
                    setattr(char, k, old_val)
                db.session.commit()
            return jsonify({'success': False, 'error': f'LLM 调用失败: {str(e)}'}), 500

        if not effects_new:
            # 空结果时从快照恢复
            if snapshot:
                for k, old_val in snapshot.items():
                    setattr(char, k, old_val)
                db.session.commit()
            return jsonify({'success': False, 'error': 'LLM 未能生成情绪变化，已恢复旧值'}), 500

        # 5b. 按关系阶梯缩放 effects
        effects_new = scale_effects_by_tier(effects_new, tier)

        # 6. 应用新 effects（走正常 apply_attr_changes，含 inertia + clamping）
        apply_attr_changes(effects_new)
        db.session.commit()

        # 7. 更新聊天记录 MD 文件
        update_message_effects(message_index, effects_new)

        # 8. 计算 diff
        diff = {}
        all_keys = set(list(effects_old.keys()) + list(effects_new.keys()))
        for k in all_keys:
            old_val = effects_old.get(k, 0)
            new_val = effects_new.get(k, 0)
            if old_val != new_val:
                diff[k] = {'old': old_val, 'new': new_val}

        # 9. 获取更新后的角色状态
        updated_char = get_character()
        updated_tier = get_relationship_tier(updated_char)
        updated_tier_cfg = get_tier_config(updated_tier)

        return jsonify({
            'success': True,
            'effects': effects_new,
            'diff': diff,
            'character': updated_char.to_dict(),
            'relationship_tier': {
                'tier': updated_tier,
                'name': updated_tier_cfg['name'],
                'effect_scale': updated_tier_cfg['effect_scale'],
            },
        })

    except Exception as e:
        if rollback_effects:
            try:
                apply_attr_changes(effects_old)
                db.session.commit()
            except Exception:
                pass
        return jsonify({
            'success': False,
            'error': f'重新分析失败: {str(e)}',
        }), 500


# ========== Chat 系统（前端聊天面板） ==========

@api_bp.route('/chat/history', methods=['GET'])
def api_chat_history():
    """读取聊天记录，支持分页。
    
    Query params:
        limit: 加载最近 N 条消息（默认全部）
        offset: 跳过前 N 条消息（用于加载更早记录，默认 0）
    """
    limit = request.args.get('limit', type=int)
    offset = request.args.get('offset', type=int, default=0)
    
    messages = load_all_sessions()
    total = len(messages)
    
    has_more = False
    if limit is not None and limit > 0:
        if offset > 0:
            # 从开头加载 offset 条之前的部分
            end = max(0, total - offset)
            start = max(0, end - limit)
            has_more = start > 0
            messages = messages[start:end]
        else:
            has_more = len(messages) > limit
            messages = messages[-limit:]
    
    print(f"[ChatHistory] 加载历史: total={total}, 返回={len(messages)}, offset={offset}, limit={limit}")
    return jsonify({'success': True, 'data': messages, 'total': total, 'has_more': has_more})


@api_bp.route('/chat/reset', methods=['POST'])
def api_chat_reset():
    """清空聊天记录及相关数据。
    
    请求体可选: { "mode": "light" | "full" }
    - "light"（默认）：仅清空 MD 聊天文件 + QuickHints 快捷建议
    - "full"：同时清空 EventLog 事件日志 + EmotionMoment 情感记忆
    """
    from backend.chat_history import clear_all_files
    data = request.get_json(silent=True) or {}
    mode = data.get('mode', 'light')

    deleted_md = 0
    # 清空 MD 文件
    folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          f"你与{get_current_character_name()}的对话")
    if os.path.exists(folder):
        for f in os.listdir(folder):
            if f.endswith('.md'):
                try:
                    os.remove(os.path.join(folder, f))
                    deleted_md += 1
                except Exception:
                    pass

    # 清除 QuickHints
    char = get_character()
    hints_deleted = 0
    if char and char.name:
        hints_deleted = QuickHints.query.filter_by(character_name=char.name).delete()

    # full 模式：清除 EventLog + EmotionMoment
    events_deleted = 0
    memories_deleted = 0
    if mode == 'full' and char and char.name:
        events_deleted = EventLog.query.filter_by(character_name=char.name).delete()
        try:
            from backend.models import EmotionMoment
            memories_deleted = EmotionMoment.query.filter_by(character_name=char.name).delete()
        except Exception:
            pass

    db.session.commit()
    return jsonify({
        'success': True, 
        'deleted': deleted_md + hints_deleted + events_deleted + memories_deleted,
        'details': {'md_files': deleted_md, 'quick_hints': hints_deleted,
                    'event_logs': events_deleted, 'memories': memories_deleted}
    })


# ========== 活动系统 ==========

@api_bp.route('/locations', methods=['GET'])
def api_get_locations():
    locations = get_all_locations()
    return jsonify({'success': True, 'data': locations})


@api_bp.route('/locations', methods=['POST'])
def api_create_location():
    """玩家自定义地点：仅校验并返回中文名 venue_id（不持久化、不写 character_activity_map）。
    前端地点选择器输入新名称并确认时调用，随后随对话把 venue_id 作为 location_override 发送，
    由 move_to_location 写入 Character.location（自定义地点仅 transient 存于当前所在地，不出现在活动地图）。"""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'name required'}), 400
    if len(name) > 32:
        return jsonify({'success': False, 'error': 'name too long (<=32)'}), 400
    char = get_character()
    if not char:
        return jsonify({'success': False, 'error': 'no active character'}), 400
    vid = create_custom_location(name, char)
    if not vid:
        return jsonify({'success': False, 'error': 'create failed'}), 500
    return jsonify({'success': True, 'venue_id': vid, 'venue_name': name})


@api_bp.route('/apply-context', methods=['POST'])
def api_apply_context():
    """立即应用前端选择器设定的游戏时间 / 地点（不依赖发聊天消息）。

    玩家在输入栏 / 移动端选择器里设定时间或地点后点“完成”，直接落库并刷新顶栏与地图，
    解决“设好地点但没发消息就不生效”的问题。时间写回 Character 并 commit；
    地点走 move_to_location（联动换装）。"""
    data = request.get_json(silent=True) or {}
    char = get_character()
    if not char:
        return jsonify({'success': False, 'error': 'no active character'}), 400

    # 时间：先写 char（不提交）
    if data.get('game_day') is not None:
        try:
            char.game_day = int(data['game_day'])
            char.game_hour = int(data.get('game_hour', char.game_hour or 8))
            char.game_minute = int(data.get('game_minute', char.game_minute or 0))
            char.game_second = int(data.get('game_second', char.game_second or 0))
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'invalid time value'}), 400

    # 地点：走 move_to_location（内部 commit，含上方时间改动）
    loc = (data.get('location') or '').strip()
    if loc:
        _res = move_to_location(loc)
        if not _res:
            return jsonify({'success': False, 'error': f'非法地点: {loc}'}), 400
    elif data.get('game_day') is not None:
        db.session.commit()

    return jsonify({'success': True, 'character': char.to_dict()})


@api_bp.route('/character-locations', methods=['GET'])
def api_character_locations():
    char_name = request.args.get('character_name', '')
    if not char_name:
        return jsonify({'success': False, 'error': 'character_name required'}), 400

    # 地图以 CharacterActivityMap 表为唯一权威数据源（单源）；内置 LOCATIONS 已删除，
    # 不再合并任何代码层全局地点，场所显示名直接取 CAM 行自身 venue_name（CHARACTER_VENUES 仅兜底）
    maps = CharacterActivityMap.query.filter_by(character_name=char_name).all()

    # 场所显示名称直接使用 CharacterActivityMap 中记录的 venue_name（LOCATIONS 已删除）；
    # CHARACTER_VENUES 仅在该行 venue_name 缺失时提供名称兜底。
    from backend.game.activity import CHARACTER_VENUES
    char_specific = CHARACTER_VENUES.get(char_name, {})
    venues = dict(char_specific)

    result = {}

    # 只从 CharacterActivityMap 构建结果，用 venues 元数据装饰显示名称
    for m in maps:
        # 优先使用 activity.py 元数据中的名称，回退到 DB 中记录的 venue_name
        display_name = (venues.get(m.venue_id) or m.venue_name or m.venue_id)[:120]
        result[m.venue_id] = {
            'name': display_name,
            'unlocked': bool(m.unlocked),
            'visit_count': m.visit_count or 0,
        }

    # 为每个地点自动生成坐标（环形布局）
    total = len(result)
    for i, (venue_id, data) in enumerate(result.items()):
        angle = (2 * math.pi * i / total) - math.pi / 2
        data['x'] = 50 + 35 * math.cos(angle)
        data['y'] = 50 + 35 * math.sin(angle)

    return jsonify({'success': True, 'data': result})


@api_bp.route('/time-scene-descriptors', methods=['GET'])
def api_time_scene_descriptors():
    """返回时间面板「更多场景」下拉框所需的三类描述词（自然天色/日常作息/微小时段）。"""
    from backend.game.time_scene_descriptors import get_scenes_for_panel
    return jsonify({'success': True, 'data': get_scenes_for_panel()})


@api_bp.route('/locations/<location_id>/activities', methods=['GET'])
def api_location_activities(location_id):
    char = get_character()
    activities = get_activities_for_location(location_id, character=char)
    return jsonify({'success': True, 'data': activities})


@api_bp.route('/move', methods=['POST'])
def api_move():
    data = request.get_json()
    location_id = data.get('location', '')
    result = move_to_location(location_id)
    if not result:
        return jsonify({'success': False, 'error': 'Invalid location'}), 400
    return jsonify({'success': True, **result})


@api_bp.route('/activity', methods=['POST'])
def api_activity():
    data = request.get_json()
    activity_id = data.get('activity', '')
    force = data.get('force', False)
    # 状态约束预检：resolve_constraint_conflicts 合并所有激活约束的封禁列表，
    # 任意一条封即封（修正旧实现"只看第一名"导致低优先级禁令被覆盖、错误放行）
    if not force:
        char = get_character()
        pre_msg, pre_block = resolve_constraint_conflicts(char, activity_id)
        if pre_block:
            return jsonify({
                'success': False,
                'message': pre_msg or '当前状态无法执行该活动',
                'data': {'blocked': True, 'warning': pre_msg, 'forced_activities': [], 'effects': {}},
            }), 409
    result = perform_activity(activity_id, force=force)
    if not result:
        return jsonify({'success': False, 'error': 'Invalid activity'}), 400
    if result.get('blocked'):
        return jsonify({
            'success': False,
            'message': result.get('warning', '当前状态无法执行该活动'),
            'data': result,
        }), 409
    # 洗澡后换装已同步人物表；前端可立即刷新穿搭和肖像状态。
    return jsonify({'success': True, 'data': result})


# ========== 事件系统 ==========

def _eventlog_to_frontend_format(ev):
    """将 EventLog 记录转换为前端事件格式"""
    effects = json.loads(ev.effects) if ev.effects else {}
    state_changes = effects.get('state_changes', [])
    relation_changes = effects.get('relation_changes', [])
    
    # 从 relation_changes 中提取 friend_name
    friend_name = ''
    if relation_changes and isinstance(relation_changes, list):
        friend_name = relation_changes[0].get('friend_name', '')
    
    return {
        'id': ev.id,
        'day': ev.game_day,
        'time': ev.game_time,
        'location': ev.location or '',
        'title': ev.title or '',
        'content': ev.description or '',
        'type': ev.event_type,
        'friend_name': friend_name,
        'state_changes': state_changes,
        'relation_changes': relation_changes,
        # 新闻事件的现实时间（从 news_cache.fetched_at 来），用于前端展示
        'real_world_time': effects.get('real_world_time', ''),
        # created_at 始终传递，前端 fallback 使用（历史事件无 real_world_time 时回退）
        'created_at': ev.created_at.isoformat() if ev.created_at else '',
    }


def _get_events_from_db(game_day=None, limit=50, game_day_from=None, game_day_to=None):
    """从 EventLog 表查询事件，返回前端格式列表

    支持三种过滤方式（互斥，优先级：单日 > 区间 > 全部）：
    - game_day: 仅查指定某一天
    - game_day_from / game_day_to: 查 [from, to] 闭区间（含两端）
    """
    char = get_character()
    query = EventLog.query.filter_by(character_name=char.name)

    if game_day is not None:
        query = query.filter(EventLog.game_day == game_day)
    elif game_day_from is not None or game_day_to is not None:
        if game_day_from is not None:
            query = query.filter(EventLog.game_day >= game_day_from)
        if game_day_to is not None:
            query = query.filter(EventLog.game_day <= game_day_to)
    
    query = query.order_by(EventLog.game_day.desc(), EventLog.game_time.desc())
    
    if limit:
        query = query.limit(limit)
    
    events = query.all()
    return [_eventlog_to_frontend_format(ev) for ev in events]


@api_bp.route('/events', methods=['GET'])
def api_get_events():
    """加载事件日志（纯 SQLite EventLog 表）"""
    limit = request.args.get('limit', 50, type=int)
    game_day = request.args.get('day', type=int)  # None=全部
    
    events = _get_events_from_db(game_day=game_day, limit=limit)
    
    days_set = sorted(set(e.get('day') for e in events if e.get('day') is not None))
    
    return jsonify({
        'success': True,
        'data': events,
        'total': len(events),
        'days': days_set
    })


# ========== LLM 配置管理 ==========

@api_bp.route('/config/llm', methods=['GET'])
def api_get_llm_config():
    """获取当前激活的 LLM 配置"""
    config = LLMConfig.query.filter_by(is_active=True).first()
    if not config:
        return jsonify({'success': True, 'data': None})
    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/llm/all', methods=['GET'])
def api_get_all_llm_configs():
    """获取所有 LLM 配置"""
    configs = LLMConfig.query.order_by(LLMConfig.created_at.desc()).all()
    return jsonify({'success': True, 'data': [c.to_dict() for c in configs]})


@api_bp.route('/config/llm', methods=['POST'])
def api_create_llm_config():
    """创建新的 LLM 配置"""
    data = request.get_json()
    if not data.get('api_url') or not data.get('api_key'):
        return jsonify({'success': False, 'error': 'api_url and api_key are required'}), 400

    config = LLMConfig(
        provider=data.get('provider', 'openai'),
        api_url=data['api_url'],
        api_key=data['api_key'],
        model_name=data.get('model_name', 'gpt-4o-mini'),
        temperature=data.get('temperature', 0.85),
        max_tokens=data.get('max_tokens', 300),
        is_active=False
    )
    db.session.add(config)
    db.session.commit()
    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/llm/<int:config_id>', methods=['PUT'])
def api_update_llm_config(config_id):
    """更新 LLM 配置"""
    config = LLMConfig.query.get(config_id)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404

    data = request.get_json()
    if 'provider' in data:
        config.provider = data['provider']
    if 'api_url' in data:
        config.api_url = data['api_url']
    if 'api_key' in data and data['api_key']:
        config.api_key = data['api_key']
    if 'model_name' in data:
        config.model_name = data['model_name']
    if 'temperature' in data:
        config.temperature = data['temperature']
    if 'max_tokens' in data:
        config.max_tokens = data['max_tokens']
    config.updated_at = beijing_now()

    db.session.commit()
    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/llm/<int:config_id>/activate', methods=['PUT'])
def api_activate_llm_config(config_id):
    """激活指定的 LLM 配置（激活前先验证模型连接可用）"""
    config = LLMConfig.query.get(config_id)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404

    # 激活前验证：确保模型当前可调用
    fix_ssl_keylog()
    test_result = safe_llm_post(
        api_url=config.api_url,
        api_key=config.api_key,
        model=config.model_name,
        messages=[{"role": "user", "content": "Hi"}],
        max_tokens=5,
        temperature=0.7,
        timeout=8,
        call_type="connection_test",
        character_name="系统测试"
    )
    if not test_result:
        return jsonify({
            'success': False,
            'error': '模型连接测试失败，无法激活。请检查 API URL、Key 或模型名称后重试'
        }), 400

    # 取消所有其他配置的激活（排除当前目标，避免 bulk update 覆盖个体变更）
    LLMConfig.query.filter(LLMConfig.id != config_id).update({'is_active': False})
    config.is_active = True
    config.updated_at = beijing_now()
    db.session.commit()
    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/llm/<int:config_id>', methods=['DELETE'])
def api_delete_llm_config(config_id):
    """删除 LLM 配置（禁止删除当前激活的）"""
    config = LLMConfig.query.get(config_id)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404
    if config.is_active:
        return jsonify({'success': False, 'error': 'Cannot delete the active config. Activate another first.'}), 400

    db.session.delete(config)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Config deleted'})


@api_bp.route('/config/llm/test', methods=['POST'])
def api_test_llm_config():
    """测试 LLM 连接"""
    data = request.get_json()
    fix_ssl_keylog()
    start_time = time.time()

    result = safe_llm_post(
        api_url=data['api_url'],
        api_key=data['api_key'],
        model=data.get('model_name', 'gpt-4o-mini'),
        messages=[{"role": "user", "content": "Hi"}],
        max_tokens=10,
        temperature=0.7,
        timeout=10,
        call_type="connection_test",
        character_name="系统测试"
    )

    latency_ms = int((time.time() - start_time) * 1000)

    if result:
        return jsonify({
            'success': True,
            'latency_ms': latency_ms,
            'message': '连接成功'
        })
    else:
        return jsonify({
            'success': False,
            'latency_ms': latency_ms,
            'error': '网络不通，无法连接'
        }), 502


# ========== 小模型配置管理（对话情绪 / TTS 参数分类等轻量推理）==========
def _test_small_model_connection(api_url, api_key, model_name, timeout=10):
    """探活：向 OpenAI 兼容 /v1/chat/completions 发一条最小请求。"""
    try:
        from backend.game.small_model import normalize_base_url
        import requests
        base = normalize_base_url(api_url)
        resp = requests.post(
            base + '/chat/completions',
            headers={'Authorization': f'Bearer {api_key}'} if api_key else {},
            json={'model': model_name, 'messages': [{'role': 'user', 'content': 'Hi'}],
                  'max_tokens': 2, 'temperature': 0},
            timeout=timeout,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f'[SmallModel] 连接测试失败: {e}')
        return False


@api_bp.route('/config/smallmodel', methods=['GET'])
def api_get_small_model_config():
    """获取当前激活的小模型配置"""
    cfg = SmallModelConfig.query.filter_by(is_active=True).first()
    return jsonify({'success': True, 'data': cfg.to_dict() if cfg else None})


@api_bp.route('/config/smallmodel/all', methods=['GET'])
def api_get_all_small_model_configs():
    """获取所有小模型配置"""
    cfgs = SmallModelConfig.query.order_by(SmallModelConfig.created_at.desc()).all()
    return jsonify({'success': True, 'data': [c.to_dict() for c in cfgs]})


@api_bp.route('/config/smallmodel', methods=['POST'])
def api_create_small_model_config():
    """创建小模型配置"""
    data = request.get_json()
    if not data.get('api_url') or not data.get('model_name'):
        return jsonify({'success': False, 'error': 'api_url 与 model_name 必填'}), 400
    from backend.game.small_model import normalize_base_url
    cfg = SmallModelConfig(
        name=data.get('name', '未命名小模型'),
        api_url=normalize_base_url(data['api_url']),
        api_key=data.get('api_key', ''),
        model_name=data['model_name'],
        is_active=False,
    )
    db.session.add(cfg)
    db.session.commit()
    return jsonify({'success': True, 'data': cfg.to_dict()})


@api_bp.route('/config/smallmodel/<int:config_id>', methods=['PUT'])
def api_update_small_model_config(config_id):
    """更新小模型配置"""
    cfg = SmallModelConfig.query.get(config_id)
    if not cfg:
        return jsonify({'success': False, 'error': 'Config not found'}), 404
    data = request.get_json()
    from backend.game.small_model import normalize_base_url
    if 'name' in data:
        cfg.name = data['name']
    if 'api_url' in data:
        cfg.api_url = normalize_base_url(data['api_url'])
    if 'api_key' in data and data['api_key']:
        cfg.api_key = data['api_key']
    if 'model_name' in data:
        cfg.model_name = data['model_name']
    cfg.updated_at = beijing_now()
    db.session.commit()
    return jsonify({'success': True, 'data': cfg.to_dict()})


@api_bp.route('/config/smallmodel/<int:config_id>/activate', methods=['PUT'])
def api_activate_small_model_config(config_id):
    """激活小模型配置（激活前先验证连通性）"""
    cfg = SmallModelConfig.query.get(config_id)
    if not cfg:
        return jsonify({'success': False, 'error': 'Config not found'}), 404
    if not _test_small_model_connection(cfg.api_url, cfg.api_key, cfg.model_name):
        return jsonify({
            'success': False,
            'error': '小模型连接测试失败，无法激活。请检查访问地址 / 模型名称 / 服务是否启动'
        }), 400
    SmallModelConfig.query.filter(SmallModelConfig.id != config_id).update({'is_active': False})
    cfg.is_active = True
    cfg.updated_at = beijing_now()
    db.session.commit()
    return jsonify({'success': True, 'data': cfg.to_dict()})


@api_bp.route('/config/smallmodel/<int:config_id>', methods=['DELETE'])
def api_delete_small_model_config(config_id):
    """删除小模型配置（禁止删除激活中的）"""
    cfg = SmallModelConfig.query.get(config_id)
    if not cfg:
        return jsonify({'success': False, 'error': 'Config not found'}), 404
    if cfg.is_active:
        return jsonify({'success': False, 'error': 'Cannot delete the active config. Activate another first.'}), 400
    db.session.delete(cfg)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Config deleted'})


@api_bp.route('/config/smallmodel/test', methods=['POST'])
def api_test_small_model_config():
    """测试小模型连接"""
    data = request.get_json()
    ok = _test_small_model_connection(
        data.get('api_url', ''), data.get('api_key', ''), data.get('model_name', ''))
    if ok:
        return jsonify({'success': True, 'message': '连接成功'})
    return jsonify({'success': False, 'error': '网络不通，无法连接'}), 502


# ========== 新闻源 & 资讯配置管理 ==========

@api_bp.route('/news/sources', methods=['GET'])
def api_get_news_sources():
    """获取所有新闻源配置"""
    from backend.models import NewsSourceConfig
    sources = NewsSourceConfig.query.order_by(NewsSourceConfig.sort_order).all()
    return jsonify({'success': True, 'data': [s.to_dict() for s in sources]})


@api_bp.route('/news/sources', methods=['POST'])
def api_create_news_source():
    """新建新闻源"""
    from backend.models import NewsSourceConfig
    data = request.get_json()
    if not data.get('name') or not data.get('source_type'):
        return jsonify({'success': False, 'error': 'name 和 source_type 必填'}), 400
    src = NewsSourceConfig(
        name=data['name'],
        source_type=data['source_type'],
        api_url=data.get('api_url', ''),
        api_key=data.get('api_key', ''),
        category=data.get('category', 'general'),
        enabled=data.get('enabled', True),
        sort_order=data.get('sort_order', 0),
    )
    db.session.add(src)
    db.session.commit()
    return jsonify({'success': True, 'data': src.to_dict()})


@api_bp.route('/news/sources/<int:src_id>', methods=['PUT'])
def api_update_news_source(src_id):
    """更新新闻源"""
    from backend.models import NewsSourceConfig
    src = NewsSourceConfig.query.get(src_id)
    if not src:
        return jsonify({'success': False, 'error': 'Source not found'}), 404
    data = request.get_json()
    if 'name' in data:
        src.name = data['name']
    if 'source_type' in data:
        src.source_type = data['source_type']
    if 'api_url' in data:
        src.api_url = data['api_url']
    if 'api_key' in data and data['api_key']:
        # 空值不更新（前端发来的是 mask 后的 ***）
        src.api_key = data['api_key']
    if 'category' in data:
        src.category = data['category']
    if 'enabled' in data:
        src.enabled = data['enabled']
    if 'sort_order' in data:
        src.sort_order = data['sort_order']
    db.session.commit()
    return jsonify({'success': True, 'data': src.to_dict()})


@api_bp.route('/news/sources/<int:src_id>', methods=['DELETE'])
def api_delete_news_source(src_id):
    """删除新闻源"""
    from backend.models import NewsSourceConfig
    src = NewsSourceConfig.query.get(src_id)
    if not src:
        return jsonify({'success': False, 'error': 'Source not found'}), 404
    db.session.delete(src)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Source deleted'})


@api_bp.route('/news/settings', methods=['GET'])
def api_get_news_settings():
    """获取新闻全局配置"""
    from backend.models import NewsSettings
    st = NewsSettings.query.get(1)
    if not st:
        st = NewsSettings(id=1)
        db.session.add(st)
        db.session.commit()
    return jsonify({'success': True, 'data': st.to_dict()})


@api_bp.route('/news/settings', methods=['PUT'])
def api_update_news_settings():
    """更新新闻全局配置"""
    from backend.models import NewsSettings
    st = NewsSettings.query.get(1)
    if not st:
        st = NewsSettings(id=1)
        db.session.add(st)
    data = request.get_json()
    for field in ['enabled', 'max_items_per_feed', 'max_inject_per_day',
                   'push_interval_days', 'fetch_interval', 'cache_retain_days',
                   'summary_threshold']:
        if field in data:
            setattr(st, field, data[field])
    db.session.commit()
    return jsonify({'success': True, 'data': st.to_dict()})


@api_bp.route('/news/cache', methods=['GET'])
def api_get_news_cache():
    """获取已抓取的新闻缓存列表（供管理面板查看是否抓取成功）"""
    from backend.models import NewsCache
    try:
        rows = NewsCache.query.order_by(NewsCache.id.desc()).limit(200).all()
        items = [{
            'id': r.id,
            'title': r.title,
            'summary': r.summary,
            'major_tag': r.major_tag or '',
            'category': r.category or '',
            # NewsCache 字段为 used / fetched_at（此前误写成 is_used / created_at 导致 AttributeError → 500）
            'is_used': bool(r.used),
            'created_at': r.fetched_at.strftime('%Y-%m-%d %H:%M:%S') if r.fetched_at else '',
        } for r in rows]
        return jsonify({'success': True, 'data': items, 'count': len(items)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api_bp.route('/news/fetch-now', methods=['POST'])
def api_fetch_news_now():
    """手动触发一次新闻抓取"""
    from flask import current_app
    from backend.game.news_service import fetch_and_cache_news
    try:
        added = fetch_and_cache_news(current_app._get_current_object())
        return jsonify({'success': True, 'added': added})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ========== ComfyUI 配置管理 ==========

@api_bp.route('/config/comfyui', methods=['GET'])
def api_get_comfyui_config():
    """获取当前激活的 ComfyUI 配置"""
    config = ComfyUIConfig.query.filter_by(is_active=True).first()
    if not config:
        return jsonify({'success': True, 'data': None})
    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/comfyui/all', methods=['GET'])
def api_get_all_comfyui_configs():
    """获取所有 ComfyUI 配置"""
    configs = ComfyUIConfig.query.order_by(ComfyUIConfig.created_at.desc()).all()
    return jsonify({'success': True, 'data': [c.to_dict() for c in configs]})


@api_bp.route('/config/comfyui', methods=['POST'])
def api_create_comfyui_config():
    """创建新的 ComfyUI 配置"""
    from flask import current_app
    data = request.get_json()
    if not data or not data.get('api_url'):
        return jsonify({'success': False, 'error': 'api_url is required'}), 400

    config = ComfyUIConfig(
        api_url=data['api_url'],
        api_key=data.get('api_key', ''),
        # exe_path 未传时用本机 local_config.py 的值（没有则留空，由用户后续补填）
        exe_path=data.get('exe_path') or current_app.config.get('COMFYUI_EXE_PATH', '') or '',
        is_active=False
    )
    db.session.add(config)
    db.session.commit()
    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/comfyui/<int:config_id>', methods=['PUT'])
def api_update_comfyui_config(config_id):
    """更新 ComfyUI 配置"""
    config = ComfyUIConfig.query.get(config_id)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404

    data = request.get_json()
    if 'api_url' in data:
        config.api_url = data['api_url']
    # 只有前端发送了非空且非掩码的 api_key 才更新
    if 'api_key' in data and data['api_key'] and '***' not in data['api_key']:
        config.api_key = data['api_key']
    if 'exe_path' in data:
        config.exe_path = data['exe_path']
    config.updated_at = beijing_now()

    db.session.commit()

    # 通知 ComfyUI 客户端重载配置
    try:
        from backend.game.comfyui_client import get_comfyui_client
        get_comfyui_client().reload_config()
    except Exception:
        pass

    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/comfyui/<int:config_id>', methods=['DELETE'])
def api_delete_comfyui_config(config_id):
    """删除 ComfyUI 配置（禁止删除当前激活的）"""
    config = ComfyUIConfig.query.get(config_id)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404
    if config.is_active:
        return jsonify({'success': False, 'error': 'Cannot delete the active config. Activate another first.'}), 400

    db.session.delete(config)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Config deleted'})


@api_bp.route('/config/comfyui/<int:config_id>/activate', methods=['PUT'])
def api_activate_comfyui_config(config_id):
    """激活指定的 ComfyUI 配置（激活前先验证 ComfyUI 服务可达）"""
    config = ComfyUIConfig.query.get(config_id)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404

    # 激活前验证：先尝试自动拉起 ComfyUI，再验证服务可达
    try:
        from backend.game.comfyui_client import ensure_comfyui_running
        ensure_comfyui_running()
    except Exception:
        pass

    try:
        import requests as _requests
        api_url = (config.api_url or '').rstrip('/')
        resp = _requests.get(f"{api_url}/system_stats", timeout=8)
        if resp.status_code != 200:
            return jsonify({
                'success': False,
                'error': f'ComfyUI 连接测试失败 (HTTP {resp.status_code})，请确认服务已启动'
            }), 400
        data = resp.json()
        if not isinstance(data, dict):
            return jsonify({
                'success': False,
                'error': 'ComfyUI 返回数据格式异常，请检查 API URL 是否正确'
            }), 400
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'ComfyUI 连接测试失败: {str(e)}'
        }), 400

    # 取消所有其他配置的激活（排除当前目标，避免 bulk update 覆盖个体变更）
    ComfyUIConfig.query.filter(ComfyUIConfig.id != config_id).update({'is_active': False})
    config.is_active = True
    config.updated_at = beijing_now()
    db.session.commit()

    # 通知 ComfyUI 客户端重载配置
    try:
        from backend.game.comfyui_client import get_comfyui_client
        get_comfyui_client().reload_config()
    except Exception:
        pass

    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/comfyui/test', methods=['POST'])
def api_test_comfyui_config():
    """测试 ComfyUI 连接（GET {api_url}/system_stats）"""
    import requests as _requests

    data = request.get_json() or {}
    api_url = (data.get('api_url') or '').rstrip('/')
    if not api_url:
        return jsonify({'success': False, 'error': 'api_url is required'}), 400

    start_time = time.time()
    try:
        headers = {}
        if data.get('api_key'):
            headers['Authorization'] = f"Bearer {data['api_key']}"
        resp = _requests.get(f"{api_url}/system_stats", headers=headers, timeout=10)
        latency_ms = int((time.time() - start_time) * 1000)
        if resp.status_code == 200:
            return jsonify({
                'success': True,
                'latency_ms': latency_ms,
                'message': '连接成功'
            })
        return jsonify({
            'success': False,
            'latency_ms': latency_ms,
            'error': f'ComfyUI 返回状态码 {resp.status_code}'
        }), 502
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        return jsonify({
            'success': False,
            'latency_ms': latency_ms,
            'error': f'网络不通，无法连接: {str(e)}'
        }), 502


# ========== ComfyUI 工作流管理 ==========

@api_bp.route('/config/comfyui/workflow', methods=['GET'])
def api_get_comfyui_workflows():
    """获取全部 ComfyUI 工作流，并附原配置 URL 与激活标记。"""
    from backend.game.comfyui_client import _WORKFLOW_DIR
    active_config = ComfyUIConfig.query.filter_by(is_active=True).first()
    configs = {c.id: c for c in ComfyUIConfig.query.all()}
    workflows = ComfyUIWorkflow.query.order_by(ComfyUIWorkflow.created_at.desc()).all()
    data = []
    for w in workflows:
        d = w.to_dict()
        config = configs.get(w.comfyui_config_id)
        d['config_api_url'] = config.api_url if config else ''
        d['config_url'] = d['config_api_url']
        d['config_is_active'] = bool(config and config.is_active)
        d['is_active'] = d['config_is_active']
        wp = (w.workflow_path or '').strip()
        if wp:
            if os.path.isabs(wp):
                fpath = wp
            elif wp.startswith('workflows') or wp.startswith('data'):
                fpath = os.path.join(_PROJECT_ROOT, wp)
            else:
                fpath = os.path.join(_WORKFLOW_DIR, wp)
        else:
            fpath = ''
        d['file_exists'] = bool(fpath) and os.path.isfile(fpath)
        d['file_size'] = os.path.getsize(fpath) if d['file_exists'] else 0
        data.append(d)
    return jsonify({'success': True, 'data': data})


@api_bp.route('/config/comfyui/workflow/scan', methods=['GET'])
def api_scan_comfyui_workflows():
    """扫描工作流目录，返回 JSON 文件及其 DB 注册、激活配置关联状态。"""
    from backend.game.comfyui_client import _WORKFLOW_DIR
    active_config = ComfyUIConfig.query.filter_by(is_active=True).first()
    files = []
    if os.path.isdir(_WORKFLOW_DIR):
        for fname in sorted(os.listdir(_WORKFLOW_DIR)):
            fpath = os.path.join(_WORKFLOW_DIR, fname)
            if not os.path.isfile(fpath) or not fname.lower().endswith('.json'):
                continue
            registrations = ComfyUIWorkflow.query.filter_by(workflow_path=fname).all()
            registered = bool(registrations)
            files.append({
                'filename': fname,
                'size': os.path.getsize(fpath),
                'registered': registered,
                'empty': not registered,
                'active': bool(active_config and any(
                    w.comfyui_config_id == active_config.id for w in registrations
                )),
                'name': os.path.splitext(fname)[0],
            })
    return jsonify({'success': True, 'data': files,
                    'workflow_dir': _WORKFLOW_DIR,
                    'comfyui_config_id': active_config.id if active_config else None})


@api_bp.route('/config/comfyui/workflow/disk/<filename>', methods=['DELETE'])
def api_delete_comfyui_workflow_disk(filename):
    """删除工作流磁盘文件，文件成功删除后再删除关联数据库行。

    Windows 沙箱/回收站不可用时，磁盘删除会失败；此时不提交 DB 删除，
    保证「文件仍在 → DB 记录仍在」，刷新扫描不会出现重新恢复的孤儿状态。
    """
    from backend.game.comfyui_client import _WORKFLOW_DIR
    if not filename or os.path.basename(filename) != filename or not filename.lower().endswith('.json'):
        return jsonify({'success': False, 'error': 'filename 必须是 basename 且以 .json 结尾'}), 400
    workflow_dir = os.path.realpath(_WORKFLOW_DIR)
    target_path = os.path.realpath(os.path.join(workflow_dir, filename))
    if os.path.dirname(target_path) != workflow_dir:
        return jsonify({'success': False, 'error': '非法工作流路径'}), 400
    if not os.path.isfile(target_path):
        return jsonify({'success': False, 'error': '工作流文件不存在'}), 404

    rows = ComfyUIWorkflow.query.filter_by(workflow_path=filename).all()
    deleted_db_ids = [row.id for row in rows]
    # 先删磁盘。safe-delete/回收站不可用时在这里失败，DB 不发生任何变化。
    try:
        os.remove(target_path)
    except Exception as e:
        db.session.rollback()
        logger.warning(f'[ComfyUI] 磁盘工作流删除失败，保留 DB 记录: {target_path}: {e}')
        return jsonify({
            'success': False,
            'error': f'磁盘删除失败: {e}',
            'deleted_db_ids': [],
            'db_preserved': True,
        }), 500

    # 文件已成功删除，再提交关联 DB 删除。
    try:
        for row in rows:
            db.session.delete(row)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f'[ComfyUI] 文件已删除但 DB 清理失败: {filename}: {e}')
        return jsonify({
            'success': False,
            'error': f'文件已删除，但数据库记录清理失败: {e}',
            'deleted_db_ids': [],
            'db_cleanup_pending': deleted_db_ids,
        }), 500
    return jsonify({'success': True, 'filename': filename, 'deleted_db_ids': deleted_db_ids})


@api_bp.route('/config/comfyui/workflow/upload', methods=['POST'])
def api_upload_comfyui_workflow():
    """上传 ComfyUI 工作流 JSON 文件到磁盘目录，并自动注册到 DB。

    校验：
      - 必传 file 字段（multipart）
      - 后缀 .json
      - JSON 可解析
      - 结构合法：API 格式（数字键 + class_type 字段）或导出格式（含 nodes 数组）
    重名自动追加 _1/_2/... 后缀；保存后默认注册为非默认工作流。
    """
    from backend.game.comfyui_client import _WORKFLOW_DIR

    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '未选择文件'}), 400

    f = request.files['file']
    if not f or not f.filename:
        return jsonify({'success': False, 'error': '文件名为空'}), 400

    raw_name = os.path.basename(f.filename)
    if not raw_name.lower().endswith('.json'):
        return jsonify({'success': False, 'error': '仅支持 .json 文件'}), 400

    raw_bytes = f.read()
    try:
        parsed = json.loads(raw_bytes.decode('utf-8'))
    except Exception as e:
        return jsonify({'success': False, 'error': f'JSON 解析失败: {e}'}), 400

    if not isinstance(parsed, dict):
        return jsonify({'success': False, 'error': '工作流 JSON 必须是对象'}), 400

    is_api_format = any(
        isinstance(k, str) and k.isdigit()
        and isinstance(v, dict) and 'class_type' in v
        for k, v in parsed.items()
    )
    is_export_format = isinstance(parsed.get('nodes'), list) and isinstance(parsed.get('links'), list)
    if not (is_api_format or is_export_format):
        return jsonify({'success': False,
                        'error': '不是合法的 ComfyUI 工作流（既不是 API 格式也无 nodes/links 导出结构）'}), 400

    base, ext = os.path.splitext(raw_name)
    final_name = raw_name
    i = 1
    while os.path.exists(os.path.join(_WORKFLOW_DIR, final_name)):
        final_name = f"{base}_{i}{ext}"
        i += 1

    os.makedirs(_WORKFLOW_DIR, exist_ok=True)
    target_path = os.path.join(_WORKFLOW_DIR, final_name)
    with open(target_path, 'wb') as out:
        out.write(raw_bytes)

    active_config = ComfyUIConfig.query.filter_by(is_active=True).first()
    has_any = ComfyUIWorkflow.query.count() > 0
    workflow = ComfyUIWorkflow(
        comfyui_config_id=active_config.id if active_config else (
            ComfyUIConfig.query.first().id if ComfyUIConfig.query.first() else None
        ),
        workflow_path=final_name,
        name=os.path.splitext(final_name)[0],
        is_default=(not has_any),
    )
    db.session.add(workflow)
    db.session.commit()

    return jsonify({
        'success': True,
        'data': {
            'filename': final_name,
            'size': os.path.getsize(target_path),
            'name': os.path.splitext(final_name)[0],
            'registered': True,
            'workflow_id': workflow.id,
            'is_default': workflow.is_default,
            'workflow_path': final_name,
        }
    })


@api_bp.route('/config/comfyui/workflow', methods=['POST'])
def api_create_comfyui_workflow():
    """创建 ComfyUI 工作流"""
    data = request.get_json()
    if not data or not data.get('workflow_path'):
        return jsonify({'success': False, 'error': 'workflow_path is required'}), 400

    comfyui_config_id = data.get('comfyui_config_id')
    if not comfyui_config_id:
        active_config = ComfyUIConfig.query.filter_by(is_active=True).first()
        if not active_config:
            return jsonify({'success': False, 'error': 'No active ComfyUI config, comfyui_config_id is required'}), 400
        comfyui_config_id = active_config.id
    elif not ComfyUIConfig.query.get(comfyui_config_id):
        return jsonify({'success': False, 'error': 'ComfyUI config not found'}), 404

    llm_model_id = data.get('llm_model_id')
    if llm_model_id and not LLMConfig.query.get(llm_model_id):
        return jsonify({'success': False, 'error': 'LLM config not found'}), 404

    is_default = bool(data.get('is_default', False))
    # 全局唯一默认：工作流跨 ComfyUI 配置共享，任意一条设默认都取消其它默认。
    if is_default:
        ComfyUIWorkflow.query.filter_by(is_default=True).update({'is_default': False})

    workflow = ComfyUIWorkflow(
        comfyui_config_id=comfyui_config_id,
        workflow_path=data['workflow_path'],
        llm_model_id=llm_model_id,
        prompt_template=data.get('prompt_template', ''),
        name=data.get('name', '') or '',
        is_default=is_default
    )
    db.session.add(workflow)
    db.session.commit()
    return jsonify({'success': True, 'data': workflow.to_dict()})


@api_bp.route('/config/comfyui/workflow/<int:workflow_id>', methods=['PUT'])
def api_update_comfyui_workflow(workflow_id):
    """更新 ComfyUI 工作流"""
    workflow = ComfyUIWorkflow.query.get(workflow_id)
    if not workflow:
        return jsonify({'success': False, 'error': 'Workflow not found'}), 404

    data = request.get_json()
    if 'workflow_path' in data:
        workflow.workflow_path = data['workflow_path']
    if 'comfyui_config_id' in data:
        if not ComfyUIConfig.query.get(data['comfyui_config_id']):
            return jsonify({'success': False, 'error': 'ComfyUI config not found'}), 404
        workflow.comfyui_config_id = data['comfyui_config_id']
    if 'llm_model_id' in data:
        if data['llm_model_id'] and not LLMConfig.query.get(data['llm_model_id']):
            return jsonify({'success': False, 'error': 'LLM config not found'}), 404
        workflow.llm_model_id = data['llm_model_id']
    if 'prompt_template' in data:
        workflow.prompt_template = data['prompt_template']
    if 'name' in data:
        workflow.name = data['name'] or workflow.name
    if 'is_default' in data:
        new_default = bool(data['is_default'])
        if new_default:
            # 全局唯一默认：即使当前行本来已是默认，也清理历史残留的其它默认行。
            ComfyUIWorkflow.query.filter(
                ComfyUIWorkflow.is_default.is_(True),
                ComfyUIWorkflow.id != workflow.id,
            ).update({'is_default': False})
        workflow.is_default = new_default
    workflow.updated_at = beijing_now()

    db.session.commit()
    return jsonify({'success': True, 'data': workflow.to_dict()})


@api_bp.route('/config/comfyui/workflow/<int:workflow_id>', methods=['DELETE'])
def api_delete_comfyui_workflow(workflow_id):
    """删除 ComfyUI 工作流"""
    workflow = ComfyUIWorkflow.query.get(workflow_id)
    if not workflow:
        return jsonify({'success': False, 'error': 'Workflow not found'}), 404

    db.session.delete(workflow)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Workflow deleted'})


# ========== Embedding 配置管理 ==========

@api_bp.route('/config/embedding', methods=['GET'])
def api_get_embedding_config():
    """获取当前激活的 Embedding 配置"""
    config = EmbeddingConfig.query.filter_by(is_active=True).first()
    if not config:
        return jsonify({'success': True, 'data': None})
    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/embedding/all', methods=['GET'])
def api_get_all_embedding_configs():
    """获取所有 Embedding 配置"""
    configs = EmbeddingConfig.query.order_by(EmbeddingConfig.created_at.desc()).all()
    return jsonify({'success': True, 'data': [c.to_dict() for c in configs]})


@api_bp.route('/config/embedding', methods=['POST'])
def api_create_embedding_config():
    """创建新的 Embedding 配置"""
    data = request.get_json()
    if not data or not data.get('api_url'):
        return jsonify({'success': False, 'error': 'api_url is required'}), 400

    config = EmbeddingConfig(
        provider=data.get('provider', 'llm_studio'),
        api_url=data['api_url'],
        api_key=data.get('api_key', ''),
        model_name=data.get('model_name', 'text-embedding-bge-m3'),
        vector_dimension=data.get('vector_dimension', 1024),
        is_active=False
    )
    db.session.add(config)
    db.session.commit()
    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/embedding/<int:config_id>', methods=['PUT'])
def api_update_embedding_config(config_id):
    """更新 Embedding 配置"""
    config = EmbeddingConfig.query.get(config_id)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404

    data = request.get_json()
    if 'provider' in data:
        config.provider = data['provider']
    if 'api_url' in data:
        config.api_url = data['api_url']
    # 只有前端发送了非空且非掩码的 api_key 才更新
    if 'api_key' in data and data['api_key'] and '***' not in data['api_key']:
        config.api_key = data['api_key']
    if 'model_name' in data:
        config.model_name = data['model_name']
    if 'vector_dimension' in data:
        config.vector_dimension = data['vector_dimension']
    config.updated_at = beijing_now()

    db.session.commit()
    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/embedding/<int:config_id>', methods=['DELETE'])
def api_delete_embedding_config(config_id):
    """删除 Embedding 配置（禁止删除当前激活的）"""
    config = EmbeddingConfig.query.get(config_id)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404
    if config.is_active:
        return jsonify({'success': False, 'error': 'Cannot delete the active config. Activate another first.'}), 400

    db.session.delete(config)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Config deleted'})


@api_bp.route('/config/embedding/<int:config_id>/activate', methods=['PUT'])
def api_activate_embedding_config(config_id):
    """激活指定的 Embedding 配置（激活前先验证模型连接可用）"""
    config = EmbeddingConfig.query.get(config_id)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404

    # 激活前验证：确保 Embedding 模型可调用
    try:
        import requests as _requests
        headers = {}
        if config.api_key:
            headers['Authorization'] = f'Bearer {config.api_key}'
        resp = _requests.post(
            config.api_url,
            json={"model": config.model_name, "input": "ping"},
            headers=headers,
            timeout=8
        )
        if resp.status_code != 200:
            return jsonify({
                'success': False,
                'error': f'Embedding 连接测试失败 (HTTP {resp.status_code})，请检查 API URL、Key 或模型名称'
            }), 400
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Embedding 连接测试失败: {str(e)}'
        }), 400

    # 取消所有其他配置的激活（排除当前目标，避免 bulk update 覆盖个体变更）
    EmbeddingConfig.query.filter(EmbeddingConfig.id != config_id).update({'is_active': False})
    config.is_active = True
    config.updated_at = beijing_now()
    db.session.commit()
    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/embedding/test', methods=['POST'])
def api_test_embedding_config():
    """测试 Embedding 连接（POST {api_url}，body: {"model": model_name, "input": "ping"}）"""
    import requests as _requests

    data = request.get_json() or {}
    api_url = data.get('api_url', '')
    if not api_url:
        return jsonify({'success': False, 'error': 'api_url is required'}), 400

    model_name = data.get('model_name', 'text-embedding-bge-m3')

    start_time = time.time()
    try:
        headers = {}
        if data.get('api_key'):
            headers['Authorization'] = f"Bearer {data['api_key']}"
        resp = _requests.post(
            api_url,
            json={'model': model_name, 'input': 'ping'},
            headers=headers,
            timeout=10
        )
        latency_ms = int((time.time() - start_time) * 1000)
        if resp.status_code == 200:
            return jsonify({
                'success': True,
                'latency_ms': latency_ms,
                'message': '连接成功'
            })
        return jsonify({
            'success': False,
            'latency_ms': latency_ms,
            'error': f'Embedding 服务返回状态码 {resp.status_code}'
        }), 502
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        return jsonify({
            'success': False,
            'latency_ms': latency_ms,
            'error': f'网络不通，无法连接: {str(e)}'
        }), 502


# ========== 成就系统 ==========

@api_bp.route('/achievements', methods=['GET'])
def api_get_achievements():
    """获取当前角色的成就列表（按 character_name 过滤）"""
    char = get_character()
    if not char:
        return jsonify({'success': False, 'error': '没有角色数据'}), 404
    achievements = Achievement.query.filter_by(character_name=char.name).all()
    if not achievements:
        # 如果没有（旧数据库迁移前），fallback 到全局成就
        achievements = Achievement.query.filter_by(character_name='').all()
    return jsonify({'success': True, 'data': [a.to_dict() for a in achievements]})


@api_bp.route('/achievements/check', methods=['POST'])
def api_check_achievements():
    unlocked = check_achievements()
    return jsonify({'success': True, 'unlocked': unlocked})


# ========== 活动地图系统 ==========

@api_bp.route('/activity-map', methods=['GET'])
def api_get_activity_map():
    """获取指定角色的活动地图（合并 CharacterActivityMap 与 CHARACTER_VENUES，确保探索进度与地图一致）"""
    from backend.game.activity import CHARACTER_VENUES

    # 优先使用查询参数中的 character_name，回退到当前活跃角色
    char_name = request.args.get('character_name', '')
    if not char_name:
        char = get_character()
        if not char:
            return jsonify({'success': False, 'error': '没有角色数据'}), 404
        char_name = char.name

    # 1. 从 CharacterActivityMap 加载已访问过地点的统计数据
    venues = CharacterActivityMap.query.filter_by(character_name=char_name) \
        .order_by(CharacterActivityMap.visit_count.desc()).all()

    # 2. 场所名称定义：仅保留 CHARACTER_VENUES 中名称不同的条目覆盖（LOCATIONS 已删除）
    char_specific = CHARACTER_VENUES.get(char_name, {})
    venue_defs = dict(char_specific)

    result = {}
    for v in venues:
        entry = v.to_dict()
        # 优先使用 CHARACTER_VENUES 中的名称，修复旧数据中错误写入的 LOCATIONS 名称
        entry['venue_name'] = venue_defs.get(v.venue_id, v.venue_name)
        result[v.venue_id] = entry

    # 保持按访问次数降序排列
    data = sorted(result.values(), key=lambda x: x['visit_count'], reverse=True)
    return jsonify({'success': True, 'data': data})


@api_bp.route('/activity-map/<string:venue_id>/visit', methods=['POST'])
def api_visit_venue(venue_id):
    """标记地点已访问"""
    char = get_character()
    if not char:
        return jsonify({'success': False, 'error': '没有角色数据'}), 404
    venue = CharacterActivityMap.query.filter_by(
        character_name=char.name, venue_id=venue_id).first()
    if not venue:
        return jsonify({'success': False, 'error': '地点不存在'}), 404
    venue.last_visited = beijing_now()
    venue.visit_count = (venue.visit_count or 0) + 1
    venue.unlocked = True
    db.session.commit()
    return jsonify({'success': True, 'data': venue.to_dict()})


# ========== 天气系统 ==========

@api_bp.route('/weather', methods=['GET'])
def api_get_weather():
    """获取当前角色的天气（基于游戏日期生成/查询，同步到角色 weather 字段）"""
    char = get_character()
    if not char:
        return jsonify({'success': False, 'error': '没有角色数据'}), 404

    # 基于游戏日期计算天气日期
    yr, mo, dy, wd_name = char.get_game_date()
    from datetime import date as dt_date
    game_date_obj = dt_date(yr, mo, dy)
    wd_index = game_date_obj.weekday()

    # 始终使用游戏日期生成/查询天气（不再依赖真实日期）
    w = weather.generate_daily_weather(game_date_obj, character=char)
    effects = weather.get_weather_effects(w)

    # 同步天气到角色字段，确保持久化
    char.weather = w.weather_name
    db.session.commit()

    return jsonify({
        'success': True,
        'data': {
            'weather': w.to_dict(),
            'effects': effects,
            'is_weekend': weather.is_weekend(wd_index),
            'weekday': wd_name,
            'game_date_display': char.get_game_date_display(),
            'game_date_short': char.get_game_date_short(),
        }
    })


@api_bp.route('/calendar/set-date', methods=['POST'])
def api_calendar_set_date():
    """日历弹窗：直接设定游戏日期，重新生成天气，不触发 tick/事件/衰减"""
    from datetime import date as dt_date

    char = get_character()
    if not char:
        return jsonify({'success': False, 'error': '没有角色数据'}), 404

    data = request.get_json() or {}
    year = data.get('year')
    month = data.get('month')
    day = data.get('day')

    if not all([year, month, day]):
        return jsonify({'success': False, 'error': '需要提供 year, month, day'}), 400

    try:
        target_date = dt_date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': '无效日期'}), 400

    # 游戏起始日期：2024-09-01（与 models.py get_game_date 保持一致）
    start_date = dt_date(2024, 9, 1)
    if target_date < start_date:
        return jsonify({'success': False, 'error': '日期不能早于 2024年9月1日'}), 400

    new_game_day = (target_date - start_date).days

    # 时间变动审计：日历跳转（char 仍为旧值）
    try:
        audit_game_time_change(
            char, new_game_day, char.game_hour, char.game_minute, 'calendar_jump',
            extra=f"target={year}-{month}-{day}"
        )
    except Exception as e:
        logger.warning(f"[Calendar] 时间审计异常（不影响跳转）: {e}")

    char.game_day = new_game_day

    # 重新生成天气
    w = None
    try:
        w = weather.generate_daily_weather(target_date, character=char)
        char.weather = w.weather_name
    except Exception as e:
        print(f"[Calendar] 生成天气失败: {e}")

    db.session.commit()

    yr, mo, dy, wd_name = char.get_game_date()
    game_date_obj = dt_date(yr, mo, dy)
    wd_index = game_date_obj.weekday()

    return jsonify({
        'success': True,
        'game_day': new_game_day,
        'character': char.to_dict(),
        'weather': {
            'weather': w.to_dict() if w else {},
            'is_weekend': weather.is_weekend(wd_index),
            'weekday': wd_name,
            'game_date_display': char.get_game_date_display(),
            'game_date_short': char.get_game_date_short(),
        }
    })


@api_bp.route('/weather/generate', methods=['POST'])
def api_generate_weather():
    """强制为今天重新生成天气（用女主 hometown 城市查真实天气）"""
    from datetime import date
    from backend.models import Weather

    char = get_character()
    today = date.today()
    existing = Weather.query.filter_by(date=today).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()

    w = weather.generate_daily_weather(today, character=char)
    effects = weather.get_weather_effects(w)
    return jsonify({
        'success': True,
        'data': {
            'weather': w.to_dict(),
            'effects': effects
        }
    })


# ========== 角色管理 ==========

@api_bp.route('/character/switch', methods=['POST'])
def api_switch_character():
    """切换当前活跃角色"""
    data = request.get_json()
    character_id = data.get('character_id', 0)
    if not character_id:
        return jsonify({'success': False, 'error': 'character_id required'}), 400

    char = Character.query.get(character_id)
    if not char:
        return jsonify({'success': False, 'error': '角色不存在'}), 404

    # 将所有其他角色标记为非活跃，再激活目标角色（排除当前目标，避免 bulk update 覆盖个体变更）
    Character.query.filter(Character.id != char.id).update({Character.is_active: False})
    char.is_active = True
    db.session.commit()

    char_data = _char_with_relationship_tier(char)
    friends = Friend.query.filter_by(character_name=char.name).order_by(Friend.closeness.desc()).all()
    achievements = Achievement.query.filter_by(character_name=char.name).all()

    # 构建状态摘要（内联，get_status_summary 不接受参数）
    mood_map = {range(0, 20): '非常低落', range(20, 40): '有些难过',
                range(40, 60): '平静', range(60, 80): '开心', range(80, 101): '非常快乐'}
    mood_text = next(v for k, v in mood_map.items() if int(char.mood) in k)
    stress_map = {range(0, 30): '轻松', range(30, 50): '略微紧张',
                  range(50, 70): '压力较大', range(70, 101): '濒临崩溃'}
    stress_text = next(v for k, v in stress_map.items() if int(char.stress) in k)
    status = (f"心情{mood_text} 压力{stress_text} "
              f"开心{char.joy:.0f} 愤怒{char.anger:.0f} 失望{char.disappointment:.0f} "
              f"无聊{char.boredom:.0f} 充实{char.fulfillment:.0f}")

    return jsonify({
        'success': True,
        'data': {
            'character': char_data,
            'status': status,
            'friends': [f.to_dict() for f in friends],
            'achievements': [a.to_dict() for a in achievements],
        }
    })


@api_bp.route('/character/list', methods=['GET'])
def api_list_characters():
    """获取所有已有角色列表"""
    chars = Character.query.all()
    return jsonify({
        'success': True,
        'data': [{
            'id': c.id,
            'name': c.name,
            'preset_id': c.preset_id,
            'identity_label': c.identity_label or '',
            'major': c.major or '',
            'education': c.education or '',
            'game_day': c.game_day,
            'tencent_tts_voice_type': c.tencent_tts_voice_type or '',
        } for c in chars]
    })


@api_bp.route('/character/save-all', methods=['POST'])
def api_save_all():
    """保存当前角色的全部属性到数据库"""
    char = get_character()
    if not char:
        return jsonify({'success': False, 'error': '没有角色数据'}), 404

    # 兼容 fetch (application/json) 和 sendBeacon (text/plain) 两种请求
    data = request.get_json(silent=True)
    if data is None:
        raw = request.get_data(as_text=True)
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return jsonify({'success': False, 'error': '无法解析保存数据'}), 400

    # 保存角色全部属性
    update_fields = [
        'energy', 'health', 'mood', 'money', 'intelligence', 'charm',
        'creativity', 'motivation', 'fulfillment', 'stress', 'confidence',
        'inspiration', 'game_day', 'game_hour', 'game_minute', 'game_second',
        'weather',
        # location 由后端 LLM tick / 随机事件 / 手动移动 独家写入，前端不可覆盖（避免竞态）
    ]
    for field in update_fields:
        if field in data:
            setattr(char, field, data[field])

    # skills 和 goals JSON 字段 — 仅更新传回的 key，缺失的 key 保留原值；
    # 传回空值（null / {}）时不覆盖，避免自动保存把技能/目标清空
    if 'skills' in data:
        incoming = data.get('skills')
        if isinstance(incoming, dict) and incoming:
            allowed_skill_keys = set(char.skill_display.keys()) if char.skill_display else set()
            base = char.skills if isinstance(char.skills, dict) else {}
            updated = dict(base)
            for k, v in incoming.items():
                if not allowed_skill_keys or k in allowed_skill_keys:
                    updated[k] = v
            char.skills = updated
    if 'goals' in data:
        incoming = data.get('goals')
        if isinstance(incoming, dict) and incoming:
            allowed_goal_keys = set(char.goal_display.keys()) if char.goal_display else set()
            base = char.goals if isinstance(char.goals, dict) else {}
            updated = dict(base)
            for k, v in incoming.items():
                if not allowed_goal_keys or k in allowed_goal_keys:
                    updated[k] = v
            char.goals = updated

    db.session.commit()

    return jsonify({
        'success': True,
        'data': {
            'saved_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'character': char.to_dict(),
        }
    })


# ============================================================
# 穿搭系统 API（Phase 4 新增）
# ============================================================

@api_bp.route('/outfit/current', methods=['GET'])
def get_current_outfit():
    """获取当前活跃角色的完整穿搭（含外貌）"""
    char = Character.query.filter_by(is_active=True).first()
    if not char:
        return jsonify({'error': 'No active character'}), 404

    try:
        outfit = char.get_current_outfit()
    except Exception:
        outfit = {}

    return jsonify({
        'character_id': char.id,
        'name': char.name,
        'outfit': outfit,
        'appearance': [a.to_dict() for a in char.appearances],
    })


@api_bp.route('/outfit/debug', methods=['GET'])
def get_outfit_debug():
    """调试路由：查看指定角色的决策过程"""
    char_id = request.args.get('character_id', type=int)
    if not char_id:
        char = Character.query.filter_by(is_active=True).first()
        if not char:
            return jsonify({'error': 'No active character'}), 404
        char_id = char.id

    overrides = {}
    for key in ['mood', 'energy', 'location', 'season', 'occasion']:
        val = request.args.get(key)
        if val is not None:
            try:
                overrides[key] = int(val) if key in ('mood', 'energy') else val
            except ValueError:
                overrides[key] = val

    from backend.game.wardrobe import debug_outfit
    result = debug_outfit(char_id, **overrides)
    return jsonify(result)


@api_bp.route('/outfit/stream', methods=['GET'])
def stream_outfit():
    """SSE 端点：实时推送 location 变更触发的穿搭更新。"""
    from backend.service.outfit_notifier import register_listener, unregister_listener

    def event_stream():
        q = register_listener()
        try:
            while True:
                data = q.get()  # 阻塞等待新穿搭数据
                yield f"data: {data}\n\n"
        except GeneratorExit:
            unregister_listener(q)

    return Response(
        event_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        },
    )


# ==================== AI伴侣系统 — 记忆/情感档案接口 ====================

@api_bp.route('/memory', methods=['GET'])
def api_get_memories():
    """获取角色长期记忆列表（供前端回忆面板展示）"""
    from backend.game.memory import get_memory_summary, get_memory_count
    char = get_character()
    limit = request.args.get('limit', 30, type=int)
    memory_type = request.args.get('type', None)
    memories = get_memory_summary(char.name, limit=limit, memory_type=memory_type)
    stats = get_memory_count(char.name)
    return jsonify({'success': True, 'data': memories, 'stats': stats})


@api_bp.route('/memory/stats', methods=['GET'])
def api_get_memory_stats():
    """获取角色记忆统计信息"""
    from backend.game.memory import get_memory_count
    char = get_character()
    stats = get_memory_count(char.name)
    return jsonify({'success': True, 'data': stats})


@api_bp.route('/memory/backfill', methods=['POST'])
def api_backfill_memory_embeddings():
    """手动触发补齐旧记忆的 embedding（仅 LM Studio，失败保留待补偿）"""
    from backend.game.memory import backfill_memory_embeddings
    char = get_character()
    count = backfill_memory_embeddings(character_name=char.name)
    return jsonify({'success': True, 'backfilled': count})


# ==================== 角色档案面板：外貌编辑 / 聊天设置 ====================

@api_bp.route('/character/appearance', methods=['POST'])
def api_save_character_appearance():
    """保存当前角色外貌描述（角色属性面板 → 基础信息 → 人物外貌）"""
    char = get_character()
    if not char:
        return jsonify({'success': False, 'error': '没有角色数据'}), 404
    data = request.get_json(silent=True) or {}
    appearance = str(data.get('appearance', '') or '').strip()
    char.appearance = appearance
    db.session.commit()
    return jsonify({'success': True, 'appearance': char.appearance})


@api_bp.route('/character/zimage-lora', methods=['POST'])
def api_save_character_zimage_lora():
    """保存当前角色的 z-image 生图 LoRA（角色属性面板 → 基础信息 → LoRA 配置）。

    字段为空字符串表示使用默认 LoRA；非空时由后端在 z-image 工作流中做
    精确/包含匹配，命中 ComfyUI 可用列表中的具体 LoRA 文件后注入节点与提示词前缀。
    """
    char = get_character()
    if not char:
        return jsonify({'success': False, 'error': '没有角色数据'}), 404
    data = request.get_json(silent=True) or {}
    zlora = str(data.get('zimage_lora', '') or '').strip()
    char.zimage_lora = zlora
    db.session.commit()
    return jsonify({'success': True, 'zimage_lora': char.zimage_lora})



@api_bp.route('/settings/chat', methods=['GET'])
def api_get_chat_settings():
    """读取聊天设置（角色属性面板 → 聊天设置 tab）"""
    from backend.game.media_intent import get_reply_side_enabled
    return jsonify({'success': True, 'data': {
        'reply_side_auto_photo': get_reply_side_enabled(),
    }})


@api_bp.route('/settings/chat', methods=['POST'])
def api_save_chat_settings():
    """保存聊天设置（写 game_setting KV 表，立即生效）"""
    data = request.get_json(silent=True) or {}
    if 'reply_side_auto_photo' not in data:
        return jsonify({'success': False, 'error': '缺少 reply_side_auto_photo'}), 400
    from backend.models import GameSetting
    from backend.game.media_intent import invalidate_reply_side_cache
    val = 'true' if bool(data.get('reply_side_auto_photo')) else 'false'
    row = GameSetting.query.get('reply_side_auto_photo')
    if row is None:
        row = GameSetting(key='reply_side_auto_photo', value=val)
        db.session.add(row)
    else:
        row.value = val
    db.session.commit()
    invalidate_reply_side_cache()
    return jsonify({'success': True, 'data': {'reply_side_auto_photo': val == 'true'}})



@api_bp.route('/emotional-moments', methods=['GET'])
def api_get_emotional_moments():
    """获取角色情感时刻列表（供前端回忆面板展示）"""
    from backend.game.emotion_archive import get_moments_summary, get_moment_count
    char = get_character()
    limit = request.args.get('limit', 30, type=int)
    moment_type = request.args.get('type', None)
    moments = get_moments_summary(char.name, limit=limit, moment_type=moment_type)
    stats = get_moment_count(char.name)
    return jsonify({'success': True, 'data': moments, 'stats': stats})


# ========== Memos 配置管理 ==========

@api_bp.route('/config/memos', methods=['GET'])
def api_get_memos_config():
    """获取当前激活的 Memos 配置"""
    config = MemosConfig.query.filter_by(is_active=True).first()
    if not config:
        return jsonify({'success': True, 'data': None})
    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/memos/all', methods=['GET'])
def api_get_all_memos_configs():
    """获取所有 Memos 配置"""
    configs = MemosConfig.query.order_by(MemosConfig.created_at.desc()).all()
    return jsonify({'success': True, 'data': [c.to_dict() for c in configs]})


@api_bp.route('/config/memos', methods=['POST'])
def api_create_memos_config():
    """创建新的 Memos 配置"""
    data = request.get_json()
    if not data or not data.get('api_url'):
        return jsonify({'success': False, 'error': 'api_url is required'}), 400

    config = MemosConfig(
        api_url=data['api_url'].rstrip('/'),
        access_token=data.get('access_token', ''),
        skip_attachments=data.get('skip_attachments', True),
        is_active=False
    )
    db.session.add(config)
    db.session.commit()
    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/memos/<int:config_id>', methods=['PUT'])
def api_update_memos_config(config_id):
    """更新 Memos 配置"""
    config = MemosConfig.query.get(config_id)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404

    data = request.get_json()
    if 'api_url' in data:
        config.api_url = data['api_url'].rstrip('/')
    if 'access_token' in data and data['access_token'] and '***' not in data['access_token']:
        config.access_token = data['access_token']
    if 'skip_attachments' in data:
        config.skip_attachments = bool(data['skip_attachments'])
    config.updated_at = beijing_now()

    db.session.commit()

    # 通知 Memos 同步线程重载配置
    try:
        from backend.game.memos_sync import _get_active_memos_config
        _get_active_memos_config.cache_clear() if hasattr(_get_active_memos_config, 'cache_clear') else None
    except Exception:
        pass

    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/memos/<int:config_id>', methods=['DELETE'])
def api_delete_memos_config(config_id):
    """删除 Memos 配置（禁止删除当前激活的）"""
    config = MemosConfig.query.get(config_id)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404
    if config.is_active:
        return jsonify({'success': False, 'error': 'Cannot delete the active config. Activate another first.'}), 400

    db.session.delete(config)
    db.session.commit()
    return jsonify({'success': True, 'message': 'Config deleted'})


@api_bp.route('/config/memos/<int:config_id>/activate', methods=['PUT'])
def api_activate_memos_config(config_id):
    """激活指定的 Memos 配置（激活前先验证 Memos 服务可达）"""
    config = MemosConfig.query.get(config_id)
    if not config:
        return jsonify({'success': False, 'error': 'Config not found'}), 404

    # 激活前验证：测试 Memos API 可达
    try:
        import requests as _requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        headers = {'Content-Type': 'application/json'}
        if config.access_token:
            headers['Authorization'] = f'Bearer {config.access_token}'
        resp = _requests.get(
            f"{config.api_url}/api/v1/memos",
            headers=headers,
            params={'pageSize': 1},
            timeout=10,
            verify=False
        )
        if resp.status_code != 200:
            return jsonify({
                'success': False,
                'error': f'Memos 连接测试失败 (HTTP {resp.status_code})，请检查 URL 和 Access Token'
            }), 400
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Memos 连接测试失败: {str(e)}'
        }), 400

    MemosConfig.query.filter(MemosConfig.id != config_id).update({'is_active': False})
    config.is_active = True
    config.updated_at = beijing_now()
    db.session.commit()

    # 切换激活配置后，清除后台同步线程的配置缓存，确保使用新配置
    try:
        from backend.game.memos_sync import _get_active_memos_config
        _get_active_memos_config.cache_clear() if hasattr(_get_active_memos_config, 'cache_clear') else None
    except Exception:
        pass

    return jsonify({'success': True, 'data': config.to_dict()})


@api_bp.route('/config/memos/test', methods=['POST'])
def api_test_memos_config():
    """测试 Memos 连接（GET {api_url}/api/v1/memos?pageSize=1）"""
    import requests as _requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    data = request.get_json() or {}
    api_url = (data.get('api_url') or '').rstrip('/')
    if not api_url:
        return jsonify({'success': False, 'error': 'api_url is required'}), 400

    start_time = time.time()
    try:
        headers = {'Content-Type': 'application/json'}
        if data.get('access_token'):
            headers['Authorization'] = f"Bearer {data['access_token']}"
        resp = _requests.get(
            f"{api_url}/api/v1/memos",
            headers=headers,
            params={'pageSize': 1},
            timeout=10,
            verify=False
        )
        latency_ms = int((time.time() - start_time) * 1000)
        if resp.status_code == 200:
            return jsonify({
                'success': True,
                'latency_ms': latency_ms,
                'message': '连接成功'
            })
        return jsonify({
            'success': False,
            'latency_ms': latency_ms,
            'error': f'Memos 服务返回状态码 {resp.status_code}'
        }), 502
    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        return jsonify({
            'success': False,
            'latency_ms': latency_ms,
            'error': f'网络不通，无法连接: {str(e)}'
        }), 502


@api_bp.route('/config/memos/sync', methods=['POST'])
def api_manual_memos_sync():
    """手动触发一次 Memos 全量导入（异步，立即返回）"""
    import threading
    from flask import current_app

    try:
        current_app.logger.info("[MemosSync] 手动触发全量导入")
        t = threading.Thread(target=_run_manual_import, args=(current_app._get_current_object(),), daemon=True)
        t.start()
        return jsonify({'success': True, 'message': '手动导入已触发，请稍后查看 NerdMemo 列表'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _run_manual_import(app):
    """手动导入执行体（运行在独立线程）"""
    with app.app_context():
        try:
            from backend.game.memos_import import import_all_memos
            stats = import_all_memos(app)
            app.logger.info(
                f"[MemosSync] 手动导入完成：总计 {stats['total']}，新增 {stats['new']}，"
                f"更新 {stats['updated']}，跳过 {stats['skipped']}，embedding 失败 {stats['embedding_failed']}"
            )
        except Exception as e:
            app.logger.warning(f"[MemosSync] 手动导入失败: {e}")


def get_active_memos_config(app):
    """从 DB 读取当前激活的 Memos 配置，无激活配置时返回空配置
    
    决策 1：强制使用前端配置，不再 fallback 到环境变量
    """
    try:
        with app.app_context():
            cfg = MemosConfig.query.filter_by(is_active=True).first()
            if cfg:
                secret = cfg.to_secret_dict()
                return {
                    'api_url': secret['api_url'],
                    'access_token': secret['access_token'],
                    'skip_attachments': secret['skip_attachments'],
                }
    except Exception as e:
        logger.warning(f"[MemosConfig] 读取配置失败: {e}")
    
    # 无激活配置，返回空配置
    logger.warning("[MemosConfig] 未找到激活的 Memos 配置，请在前端配置页创建并激活")
    return {
        'api_url': '',
        'access_token': '',
        'skip_attachments': True,
    }

# 注：通用事件模板接口（GET/PUT /event-templates）已于方案 D（2026-07-30）移除。
# 通用静态事件池（triggered_events.json）不再加载；随机事件改由 LLM 兜底生成。
# 角色专属模板接口（/<name>/event-templates*）保留，入口迁移至角色属性面板。


@api_bp.route("/" + "<name>/event-templates", methods=["GET"])
def api_get_character_templates(name):
    """角色专属事件模板 = 文件模板 + 当前任务(pending/running)生成的条件事件（只读，实时反映任务状态）。"""
    from backend.game.triggered_events import load_character_templates
    templates = list(load_character_templates(name))
    try:
        from backend.models import Mission
        missions = Mission.query.filter(
            Mission.character_name == name,
            Mission.status.in_(['pending', 'running']),
        ).all()
        for m in missions:
            for tpl in (m.event_templates or []):
                item = dict(tpl)
                item['_source'] = 'mission'
                item['_mission_id'] = m.id
                item['_mission_name'] = m.mission_name
                templates.append(item)
    except Exception as e:
        logger.warning(f"[EventTemplates] 合并任务条件事件失败: {e}")
    return jsonify({"success": True, "templates": templates})


@api_bp.route("/" + "<name>/event-templates", methods=["PUT"])
def api_save_character_templates(name):
    from backend.game.triggered_events import save_character_templates
    body = request.get_json() or {}
    if "templates" not in body:
        return jsonify({"success": False, "error": "缺少 templates 字段"}), 400
    # 任务生成的条件事件（_source=mission）存在 Mission 表中，不落入角色专属文件，避免双份触发
    file_templates = [t for t in body["templates"] if t.get("_source") != "mission"]
    ok = save_character_templates(name, file_templates)
    if not ok:
        return jsonify({"success": False, "error": "保存失败"}), 500
    return jsonify({"success": True, "saved": len(file_templates)})


@api_bp.route("/" + "<name>/event-templates/generate", methods=["POST"])
def api_generate_character_templates(name):
    from backend.game.triggered_events import save_character_templates
    from backend.models import Character, LLMConfig
    from backend.game.llm_utils import safe_llm_post
    from backend.game.event import extract_json_from_llm_response

    char = Character.query.filter_by(name=name).first()
    if not char:
        return jsonify({"success": False, "error": "角色不存在"}), 404

    config = LLMConfig.query.filter_by(is_active=True).first()
    if not config:
        return jsonify({"success": False, "error": "无活跃LLM配置"}), 400

    llm_config = config.to_secret_dict()

    prompt = f"""你是一个剧情设计AI。请为角色「{char.name}」生成 5-8 条专属事件模板。

角色信息：
姓名：{char.name}
身份：{char.identity_label or ''}
专业/领域：{char.major or ''}
性格：{char.personality_type or ''}

注意：她是{char.identity_label or '角色'}，专属事件须严格贴合其职业与日常生活，地点须符合其职业场景。
【职业硬约束】若身份为职场人士（医生/律师/设计师/游戏制作人等），禁止生成校园上课、考试、社团、同学、选修课等学生专属事件。

每条模板格式：
{{"trigger_id": "唯一ID", "category": "daily", "conditions": [...], "state_changes": [...], "narrative": {{"title": "...", "description": "..."}}, "location": "...", "auto_message_probability": 0.3}}

要求：
1. 事件要符合角色职业与活动范围，location 须是真实职业场景
2. conditions 里至少一条属性条件（如 energy<30 / mood>70 / at_location 等）
3. state_changes 里属性变化幅度不超过 15
4. 只返回 JSON 数组"""

    result = safe_llm_post(
        api_url=llm_config["api_url"], api_key=llm_config["api_key"],
        model=llm_config.get("model_name", "gpt-4o-mini"),
        messages=[{"role": "system", "content": "只返回 JSON 数组"}, {"role": "user", "content": prompt}],
        max_tokens=8000, temperature=0.8, timeout=(60, 180),
        call_type="generate_character_templates", character_name=name,
    )
    if not result:
        return jsonify({"success": False, "error": "LLM 返回为空"}), 500

    content = result["choices"][0]["message"]["content"]
    data, err = extract_json_from_llm_response(content)
    if err:
        return jsonify({"success": False, "error": f"解析失败: {err}"}), 500

    save_character_templates(name, data)
    return jsonify({"success": True, "templates": data})


# ========== Prompt 管理 API ==========

@api_bp.route('/prompts', methods=['GET'])
def api_list_prompts():
    """列出全部 25 个 prompt 模板的摘要。"""
    from backend.game.prompt_registry import get_prompt_manager
    pm = get_prompt_manager()
    return jsonify({"success": True, "data": pm.list_all()})


@api_bp.route('/prompts/<prompt_id>', methods=['GET'])
def api_get_prompt(prompt_id):
    """获取单个 prompt 模板的完整内容。"""
    from backend.game.prompt_registry import get_prompt_manager, REGISTRY, is_known_prompt, DYNAMIC, THINKING_DISABLED_PROMPTS
    pm = get_prompt_manager()
    if not is_known_prompt(prompt_id):
        return jsonify({"success": False, "error": f"未知 prompt_id: {prompt_id}"}), 404
    tmpl = REGISTRY.get(prompt_id) or DYNAMIC_API.get(prompt_id)
    if tmpl is None:
        # 重启后尚未加载到 DYNAMIC：从 DB 行取元信息
        from backend.models import PromptTemplateDB
        row = PromptTemplateDB.query.filter_by(id=prompt_id).first()
        if row:
            tmpl = type('T', (), {
                'category': row.category or 'world',
                'label': row.label or prompt_id,
                'version': row.version,
                'is_system': False,
                'require_test': False,
                'description': '角色世界观任务提示词',
                'variables': [],
            })()
        else:
            tmpl = type('T', (), {
                'category': 'world', 'label': prompt_id, 'version': 1,
                'is_system': False, 'require_test': False,
                'description': '', 'variables': [],
            })()
    return jsonify({"success": True, "data": {
        "id": prompt_id,
        "category": tmpl.category,
        "label": tmpl.label,
        "content": pm.get(prompt_id),
        "version": tmpl.version,
        "is_system": tmpl.is_system,
        "require_test": tmpl.require_test,
        "description": tmpl.description,
        "variables": tmpl.variables,
        "thinking_disabled": prompt_id in THINKING_DISABLED_PROMPTS,
    }})


@api_bp.route('/prompts/<prompt_id>/preview', methods=['GET'])
def api_preview_prompt(prompt_id):
    """用指定角色渲染 prompt 预览。"""
    from backend.game.prompt_registry import get_prompt_manager, REGISTRY, is_known_prompt, DYNAMIC
    if not is_known_prompt(prompt_id):
        return jsonify({"success": False, "error": f"未知 prompt_id: {prompt_id}"}), 404
    char_id = request.args.get('char_id', type=int)
    if not char_id:
        return jsonify({"success": False, "error": "缺少 char_id 参数"}), 400
    pm = get_prompt_manager()
    rendered = pm.preview(prompt_id, char_id)
    return jsonify({"success": True, "data": {"rendered": rendered}})


@api_bp.route('/prompts/<prompt_id>', methods=['PUT'])
def api_save_prompt(prompt_id):
    """保存 prompt 模板的自定义版本。"""
    from backend.game.prompt_registry import get_prompt_manager, REGISTRY, is_known_prompt, DYNAMIC
    import flask
    if not is_known_prompt(prompt_id):
        return jsonify({"success": False, "error": f"未知 prompt_id: {prompt_id}"}), 404
    data = flask.request.get_json(force=True, silent=True) or {}
    content = data.get("content", "")
    if not content:
        return jsonify({"success": False, "error": "content 不能为空"}), 400
    pm = get_prompt_manager()
    ok = pm.save(prompt_id, content)
    return jsonify({"success": ok, "error": "" if ok else "保存失败（可能为只读）"})


@api_bp.route('/prompts/<prompt_id>/reset', methods=['POST'])
def api_reset_prompt(prompt_id):
    """重置 prompt 模板为代码默认值。"""
    from backend.game.prompt_registry import get_prompt_manager, REGISTRY, is_known_prompt, DYNAMIC
    if not is_known_prompt(prompt_id):
        return jsonify({"success": False, "error": f"未知 prompt_id: {prompt_id}"}), 404
    pm = get_prompt_manager()
    ok = pm.reset(prompt_id)
    return jsonify({"success": ok, "error": "" if ok else "重置失败"})


@api_bp.route('/prompts/<prompt_id>/versions', methods=['GET'])
def api_prompt_versions(prompt_id):
    """获取 prompt 模板的版本历史列表。"""
    from backend.game.prompt_registry import REGISTRY, is_known_prompt
    from backend.models import PromptTemplateVersion
    if not is_known_prompt(prompt_id):
        return jsonify({"success": False, "error": f"未知 prompt_id: {prompt_id}"}), 404
    rows = PromptTemplateVersion.query.filter_by(prompt_id=prompt_id)\
        .order_by(PromptTemplateVersion.version.desc()).limit(50).all()
    versions = [{"version": r.version, "updated_at": r.updated_at.isoformat() if r.updated_at else None}
                for r in rows]
    return jsonify({"success": True, "data": versions})


@api_bp.route('/prompts/<prompt_id>/rollback/<int:version>', methods=['POST'])
def api_rollback_prompt(prompt_id, version):
    """回退 prompt 到指定版本。"""
    from backend.game.prompt_registry import get_prompt_manager, REGISTRY, is_known_prompt, DYNAMIC
    from backend.models import PromptTemplateVersion
    if not is_known_prompt(prompt_id):
        return jsonify({"success": False, "error": f"未知 prompt_id: {prompt_id}"}), 404
    row = PromptTemplateVersion.query.filter_by(prompt_id=prompt_id, version=version).first()
    if not row:
        return jsonify({"success": False, "error": f"版本 {version} 不存在"}), 404
    pm = get_prompt_manager()
    ok = pm.save(prompt_id, row.content)
    return jsonify({"success": ok, "error": "" if ok else "回退失败"})


@api_bp.route('/prompts/<prompt_id>/variables', methods=['GET'])
def api_prompt_variables(prompt_id):
    """获取 prompt 的可用变量列表 + 当前活跃角色的值。"""
    from backend.game.prompt_registry import REGISTRY, is_known_prompt
    from backend.game.variable_resolver import get_all_variable_names, resolve_variables
    from backend.game.character import get_character
    if not is_known_prompt(prompt_id):
        return jsonify({"success": False, "error": f"未知 prompt_id: {prompt_id}"}), 404
    char = get_character()
    all_vars = get_all_variable_names()
    values = resolve_variables(char) if char else {}
    var_list = [{"name": v, "value": values.get(v, "")} for v in all_vars]
    return jsonify({"success": True, "data": var_list})


@api_bp.route('/prompts/<prompt_id>/validate', methods=['POST'])
def api_validate_prompt(prompt_id):
    """🧪 测试 prompt 格式：用当前模板发一次轻量 LLM 请求，验证输出是否合规。"""
    from backend.game.prompt_registry import REGISTRY, is_known_prompt, get_prompt_manager
    from backend.models import LLMConfig
    from backend.game.llm_utils import safe_llm_post
    if not is_known_prompt(prompt_id):
        return jsonify({"success": False, "error": f"未知 prompt_id: {prompt_id}"}), 404
    tmpl = REGISTRY.get(prompt_id)
    if not tmpl or not tmpl.require_test:
        return jsonify({"success": True, "info": "此 prompt 无需验证（🟢 自由编辑级）"})

    config = LLMConfig.query.filter_by(is_active=True).first()
    if not config or not config.api_key:
        return jsonify({"success": False, "error": "LLM 未配置"}), 503

    llm_cfg = config.to_secret_dict()
    pm = get_prompt_manager()
    sample_text = (request.get_json() or {}).get("sample", pm.get(prompt_id))

    try:
        result = safe_llm_post(
            api_url=llm_cfg["api_url"], api_key=llm_cfg["api_key"],
            model=llm_cfg.get("model_name", "gpt-4o-mini"),
            messages=[{"role": "user", "content": f"TEST: {sample_text[:200]}"}],
            max_tokens=100, temperature=0, timeout=(10, 20),
            call_type="prompt_validate", character_name="",
        )
        if not result:
            return jsonify({"success": False, "error": "LLM 返回为空"})
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        # 基础检查：非空即通过（实际生产应按 prompt 类型检查 JSON 结构）
        return jsonify({"success": True, "sample_response": content[:200]})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)[:100]}), 500
