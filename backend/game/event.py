"""事件系统 — 环境事件、随机事件与角色主动联系"""
import random
import json
import re
import logging
from datetime import datetime
from backend.config import beijing_now
from backend.models import db, EventLog, Achievement, Character, ActiveMessageQueue, Friend
from backend.game.character import apply_attr_changes, get_character
from backend.chat_history import append_message as append_chat_message
from backend.game.dialogue import get_relationship_tier, get_tier_config, TIER_CONFIG
from backend.game.llm_utils import normalize_api_url, safe_llm_post, strip_reasoning_from_response, LLMNetworkError, logger, get_time_period_prompt, get_time_period_prompt_for_hour
from backend.game.constraints import (
    get_all_active_warnings,
    get_llm_constraints_prompt,
)


# ==================== 角色快照辅助函数 ====================

def build_character_snapshot(char):
    """构建事件生成 prompt 中的角色快照段。"""
    lines = [
        f"- 姓名：{char.name or '角色'}",
        f"- 年龄：{char.age or 20}岁",
        f"- 身份：{char.identity_label or '女大学生'}",
    ]
    if char.major:
        lines.append(f"- 专业/领域：{char.major}")
    if char.education:
        lines.append(f"- 教育背景：{char.education}")
    lines.append(f"- 性格：{char.personality_type or '温柔'}，{char.personality_tone or ''}")
    lines.append(f"- 表达风格：{char.expression_style or '自然'}")
    lines.append(f"- 社交倾向：{char.social_tendency or 6}/10")
    lines.append(f"- 情绪稳定性：{char.emotional_stability or 6}/10")
    if char.dream_primary:
        dream_str = f"- 主梦想：{char.dream_primary}"
        if char.dream_motivation:
            dream_str += f"（{char.dream_motivation}）"
        lines.append(dream_str)
    if char.dream_secondary:
        lines.append(f"- 次梦想：{char.dream_secondary}")
    if char.hobbies:
        lines.append(f"- 爱好：{char.hobbies}")
    if char.hometown:
        lines.append(f"- 家乡：{char.hometown}")
    activity_scope = _infer_activity_scope(char)
    lines.append(f"- 日常活动范围：{activity_scope}")
    return '\n'.join(lines)


def _infer_activity_scope(char):
    """根据身份标签推断角色的典型活动范围。"""
    label = (char.identity_label or '').lower()
    if '学生' in label or '大学生' in label or '研究生' in label:
        return '教室、实验室、图书馆、食堂、社团活动室、宿舍'
    elif '医生' in label or '医师' in label or '护士' in label:
        return '诊室、病房、手术室、医生办公室、医院食堂、值班室'
    elif '律师' in label or '法务' in label or '法律' in label:
        return '律所办公室、法院、客户会议室、法律图书馆、咖啡厅'
    elif '艺术' in label or '画家' in label or '美术' in label:
        return '画室、美术馆、户外写生地、咖啡厅、艺术用品店'
    elif '音乐' in label or '钢琴' in label or '演奏' in label:
        return '琴房、音乐厅后台、排练室、音乐教室、录音棚'
    elif '游戏' in label or '制作人' in label or '开发' in label:
        return '办公室、会议室、游戏展会、咖啡厅、家里书房'
    else:
        return '办公室/学习场所、公共空间、家中、社交场所'


def build_character_profile_block(char):
    """构建主动消息/决策 prompt 中的角色画像段（§8.5 辅助函数）。"""
    profile_lines = [
        f"姓名：{char.name or '角色'}",
        f"年龄：{char.age or 20}岁",
        f"身份：{char.identity_label or '女大学生'}",
    ]
    if char.personality_type:
        profile_lines.append(f"性格：{char.personality_type}")
    if char.personality_tone:
        profile_lines.append(f"语气：{char.personality_tone}")
    if char.expression_style:
        profile_lines.append(f"表达风格：{char.expression_style}")
    if char.dream_primary:
        profile_lines.append(f"梦想：{char.dream_primary}")
    return '\n'.join(profile_lines)


def build_personality_summary(char):
    """构建角色性格摘要文本（§8.5 辅助函数）。"""
    parts = []
    if char.personality_type:
        parts.append(char.personality_type)
    if char.personality_tone:
        parts.append(char.personality_tone)
    return '、'.join(parts) if parts else '温柔、聪明、偶尔俏皮'


# ===========================================================


def extract_json_from_llm_response(response_text):
    """从 LLM 回复中提取 JSON 对象或数组。容错处理多层 fallback。

    Args:
        response_text: LLM 返回的原始文本

    Returns:
        (parsed_data, None) 成功时； (None, error_message) 失败时
    """
    if not response_text or not response_text.strip():
        return None, "LLM 返回空内容"

    text = response_text.strip()

    # ⚠️ 历史 Bug（2026-09-10 修复）：旧实现在解析之前无条件执行
    #     text = text.replace('{{', '{').replace('}}', '}')
    # 而合法 JSON 的嵌套对象闭合**必然**产生 }} / ]}（如
    #     "state_changes": {"character": {...}, "npcs": {"某人": {...}}}
    # ），全局替换会把结构直接改坏 → 凡是带嵌套 state_changes 的
    # mission_generate_main 等 JSON 100% 解析失败，表现为「重试 N 次仍解析失败」，
    # 且报错信息里 text[:500] 被截断，看起来像 LLM 输出被掐断（其实 finish_reason=stop）。
    #
    # 现在改为「先忠实原文，再按需归一化」，并对归一化版本做优先级裁决。

    # 情况 A：原文本身就是合法 JSON（或其 ```json 代码块内合法）——
    # 直接采用原样结果，不做任何归一化改写。这既能修复「嵌套 }} 被改坏」，
    # 也保证字符串内容里若出现 {{ }} 不会被篡改。
    raw, raw_err = _extract_json_candidates(text)
    if raw is not None and _is_clean_json(text):
        return raw, None

    # 情况 B：原文不是合法 JSON（典型：模型照抄 prompt 示例里的 {{...}} 占位符）
    # → 生成归一化候选，与原样结果一起按优先级挑选。
    normalized = []
    collapsed = text.replace('{{', '{').replace('}}', '}')
    if collapsed != text:
        normalized.append(collapsed)
    # 数值占位符 {{ 5 }} / {{5}} → 5
    # （prompt 里已明确警告「✗ 用 {{ }} 写数值」，但模型偶发照抄）
    numeric_fixed = re.sub(r'\{\{\s*(-?\d+(?:\.\d+)?)\s*\}\}', r'\1', text)
    if numeric_fixed != text:
        if numeric_fixed not in normalized:
            normalized.append(numeric_fixed)
        nf_collapsed = numeric_fixed.replace('{{', '{').replace('}}', '}')
        if nf_collapsed not in normalized:
            normalized.append(nf_collapsed)

    # 优先级：原文含 {{ 占位符 → 归一化版才反映真实结构，优先；
    #           否则 → 原样版忠实于原文（嵌套 }} 本就是合法 JSON），优先。
    candidates = normalized + [text] if '{{' in text else [text] + normalized

    degenerate = None       # 备选：方案7「截断修复」可能把内容裁成 {} / []
    first_err = raw_err     # 那是假成功，不能盖掉后面更好的结果
    for cand in candidates:
        if cand is text:
            data, err = raw, raw_err
        else:
            data, err = _extract_json_candidates(cand)
        if data is None:
            if first_err is None:
                first_err = err
            continue
        if isinstance(data, (dict, list)) and not data:
            degenerate = degenerate or (data, None)
            continue
        return data, None
    if degenerate is not None:
        return degenerate
    return None, first_err


def _is_clean_json(text):
    """判断文本本身（或其 ```json 代码块）是否已是合法 JSON。

    用于区分「原文就是好 JSON，无需任何归一化」与「原文是模型照抄的
    {{...}} 模板占位符，需要归一化」两种情况。
    """
    try:
        json.loads(text)
        return True
    except (ValueError, TypeError):
        pass
    m = re.search(r'```(?:json)?\s*([\[{].*[\]}])\s*```', text, re.DOTALL)
    if m:
        try:
            json.loads(m.group(1))
            return True
        except (ValueError, TypeError):
            pass
    return False


def _extract_json_candidates(text):
    """对给定文本依次尝试多级容错提取 JSON（方案1~7）。

    Returns:
        (parsed_data, None) 成功时； (None, error_message) 全部失败时。
    """

    def _strip_reasoning(data):
        """递归删除 parsed data 中的 reasoning_content 字段。"""
        if isinstance(data, dict):
            data.pop('reasoning_content', None)
            for v in data.values():
                _strip_reasoning(v)
        elif isinstance(data, list):
            for item in data:
                _strip_reasoning(item)

    data = None  # 显式初始化：后续方案分支都要访问，未赋值会 UnboundLocalError

    # 方案1：直接解析整个文本
    try:
        data = json.loads(text)
        _strip_reasoning(data)
        return data, None
    except json.JSONDecodeError:
        pass

    # 方案2：提取 ```json ... ``` 代码块（对象或数组）
    m = re.search(r'```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            _strip_reasoning(data)
            return data, None
        except json.JSONDecodeError:
            pass

    # 方案3：提取首个 JSON 对象 { ... }
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            _strip_reasoning(data)
            return data, None
        except json.JSONDecodeError:
            pass

    # 方案4：提取首个 JSON 数组 [ ... ]
    m = re.search(r'\[.*\]', text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            _strip_reasoning(data)
            return data, None
        except json.JSONDecodeError:
            pass

    # 方案5：尝试用更宽松的首个完整 JSON 片段（找平衡括号/方括号）
    # 关键修复：候选片段必须到达文末（允许 ≤50 字符尾部 markdown/空白），
    # 否则只是某个嵌套子对象的闭合，不能当成顶层 JSON 返回。
    if data is None:
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start_idx = text.find(start_char)
            if start_idx == -1:
                continue
            depth = 0
            in_string = False
            escape = False
            last_close = -1
            for i in range(start_idx, len(text)):
                ch = text[i]
                if escape:
                    escape = False
                    continue
                if ch == '\\':
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == start_char:
                    depth += 1
                elif ch == end_char:
                    depth -= 1
                    if depth == 0:
                        last_close = i + 1
                        # 不 break，继续扫——只在文末才接受
            # 接受条件：depth 确实回 0，且候选后面紧跟 ≤50 字符尾部 junk
            if last_close > 0 and (len(text) - last_close) <= 50:
                candidate = text[start_idx:last_close]
                try:
                    data = json.loads(candidate)
                    _strip_reasoning(data)
                    return data, None
                except json.JSONDecodeError:
                    pass  # 该候选不可解析，继续下一个 start_char

    # 方案6：JSON 被截断，尝试补全缺失的括号
    # 如果找到了 { 或 [ 但没有匹配的闭合，尝试补上缺失的括号
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start_idx = text.find(start_char)
        if start_idx == -1:
            continue
        count_start = text.count(start_char)
        count_end = text.count(end_char)
        if count_start > count_end:
            missing = count_start - count_end
            candidate = text + end_char * missing
            try:
                data = json.loads(candidate)
                _strip_reasoning(data)
                return data, None
            except json.JSONDecodeError:
                pass

    # 方案7：修复「模型输出被掐断」的截断 JSON（未闭合字符串 + 容器未闭合）
    repaired = _repair_truncated_json(text)
    if repaired:
        try:
            data = json.loads(repaired)
            _strip_reasoning(data)
            return data, None
        except json.JSONDecodeError:
            pass

    return None, f"无法从 LLM 回复中提取 JSON: {text[:500]}"


def _repair_truncated_json(text):
    """尽力修复被截断/不完整的 JSON 文本，返回修复后的文本或 None。

    模型偶发会在 JSON 写到一半时停止输出（未闭合字符串 + 容器未闭合），
    典型如：{ "a": "xxx", "b": "yyy   （字符串值与容器都没闭合）。
    本函数通过「定位 JSON 起点 → 关闭未闭合字符串 → 补齐未闭合容器 →
    兜底迭代修剪尾部不完整片段」尝试还原可解析 JSON。
    """
    if not text or not text.strip():
        return None

    # 1) 定位 JSON 起点（跳过如 ```json、说明文字等前缀）
    start = len(text)
    for ch in ('{', '['):
        i = text.find(ch)
        if i != -1 and i < start:
            start = i
    if start == len(text):
        return None
    s = text[start:].strip()
    if not s:
        return None

    def _scan(t):
        """返回 (是否处于未闭合字符串内, 未闭合容器栈)。"""
        in_str = False
        esc = False
        stack = []
        for ch in t:
            if esc:
                esc = False
                continue
            if ch == '\\' and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch in '[{':
                stack.append(ch)
            elif ch in ']}':
                if stack:
                    stack.pop()
        return in_str, stack

    close_map = {'{': '}', '[': ']'}

    def _close(t):
        """补齐 t 中未闭合的容器（LIFO：最后打开的最先闭合）。"""
        _, stack = _scan(t)
        for opener in reversed(stack):
            t += close_map[opener]
        return t

    # 2) 若结尾处于未闭合字符串内，截到该字符串起点之前（连同可能的 "key": 前缀）
    in_str, stack = _scan(s)
    if in_str:
        idx = s.rfind('"')
        if idx == -1:
            return None
        s = s[:idx].rstrip().rstrip(',:').rstrip()

    # 3) 迭代：每轮闭合容器后尝试解析；失败则从"未闭合"的 s 尾部裁剪，逐步逼近可解析
    for _ in range(2000):
        candidate = _close(s)
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass
        st = s.rstrip()
        if not st:
            return None
        if st.endswith(',') or st.endswith(':'):
            s = st[:-1].rstrip()
            continue
        if st.endswith('"'):
            # 不完整字符串：删除整段（含可能的 "key": 前缀）
            idx = st.rfind('"')
            prefix = st[:idx]
            k = prefix.rfind('"')
            if k != -1 and prefix[k + 1:].lstrip().startswith(':'):
                s = prefix[:k].rstrip().rstrip(',:').rstrip()
            else:
                s = prefix.rstrip().rstrip(',:').rstrip()
            continue
        if st[-1] in ']}':
            s = st[:-1].rstrip()
            continue
        s = st[:-1]  # 兜底：删最后字符

    return None


def get_recent_dialogue(limit=20):
    """获取最近 N 条对话记录，返回格式化的文本字符串。

    从 chat_history.py 的 load_all_sessions() 读取文件系统中的所有 MD 文件，
    提取最后 N 条消息，格式化为 LLM 可直接使用的文本。
    如果聊天记录为空（玩家未与角色对话），返回空字符串。

    Returns:
        str: 格式化的聊天记录文本，如：
            【最近聊天记录】
            玩家 (第3天 14:22:10): 你今天看上去很开心
            角色 (第3天 14:23:01): 是啊，论文终于通过了！
            ...
        如果没有聊天记录则返回空字符串 ""。
    """
    from backend.chat_history import load_all_sessions

    all_msgs = load_all_sessions()
    if not all_msgs:
        return ""

    # 取最后 limit 条
    recent = all_msgs[-limit:]

    char_name = getattr(get_character(), 'name', None) or '角色'
    # 玩家专属称呼（AI伴侣系统升级 — 阶段一）
    _char_obj = get_character()
    _player_nickname = getattr(_char_obj, 'player_nickname', '') or getattr(_char_obj, 'player_identity', '导师') or '导师'
    lines = ["【最近聊天记录】"]
    for msg in recent:
        speaker_label = _player_nickname if msg['speaker'] == 'player' else char_name
        time_label = ""
        if msg.get('game_day') and msg.get('game_time'):
            time_label = f"第{msg['game_day']}天 {msg['game_time']}"
        elif msg.get('game_time'):
            time_label = msg['game_time']

        if time_label:
            lines.append(f"{speaker_label} ({time_label}): {msg['content']}")
        else:
            lines.append(f"{speaker_label}: {msg['content']}")

    return "\n".join(lines)


# 硬编码事件池和消息池已移除 — 全部由 LLM 驱动


def get_daily_event_count(game_day: int) -> int:
    """查询指定游戏天内已生成的事件数量（排除换装事件）。

    Args:
        game_day: 游戏天数

    Returns:
        int: 该天内的事件总数（不含 outfit_change）
    """
    if game_day <= 0:
        return 0
    char = get_character()
    query = EventLog.query.filter(
        EventLog.game_day == game_day,
        EventLog.event_type != 'outfit_change',
        EventLog.event_type != 'sleep_decay',
        EventLog.event_type != 'auto_message_llm',
        EventLog.event_type != 'relation_change',
        EventLog.event_type != 'relation_initiated',
        EventLog.event_type != 'llm_tick',
        EventLog.event_type != 'llm_tick_event'
    )
    if char and char.name:
        query = query.filter_by(character_name=char.name)
    result_count = query.count()
    
    # 诊断日志：按 event_type 分组统计当天事件
    _diag = logging.getLogger(__name__)
    type_query = EventLog.query.filter(EventLog.game_day == game_day)
    if char and char.name:
        type_query = type_query.filter_by(character_name=char.name)
    all_today = type_query.all()
    from collections import Counter
    type_counts = Counter(e.event_type for e in all_today)
    _diag.info(
        "[DIAG] get_daily_event_count: game_day=%d total_events=%d (excl_sleep_decay+outfit=%d) by_type=%s",
        game_day, len(all_today), result_count, dict(type_counts)
    )
    
    return result_count


# ==================== 成就/目标 → 事件提示词（第一优先级）====================

_ATTR_LABELS = {
    # 物理
    'health': '健康', 'energy': '精力', 'hunger': '饥饿', 'hygiene': '卫生',
    # 心理
    'mood': '心情', 'stress': '压力', 'happiness': '幸福', 'loneliness': '孤独',
    'confidence': '自信', 'motivation': '动力', 'creativity': '创造力',
    'joy': '开心', 'anger': '愤怒', 'disappointment': '失望', 'boredom': '无聊', 'fulfillment': '充实',
    # 关系
    'player_trust': '信任', 'player_affection': '好感', 'player_respect': '尊重', 'player_intimacy': '亲密',
    'friend_count': '朋友数', 'library_visit': '图书馆到访',
}


def _attr_label(attr):
    """属性名 → 中文标签；skill_coding / skills.coding → 编程技能。"""
    if not attr:
        return attr
    a = attr
    if a.startswith('skill_') and '.' not in a:
        a = 'skills.' + a[len('skill_'):]
    if a.startswith('skills.'):
        key = a.split('.', 1)[1]
        return f"{key}技能"
    if a.startswith('goal_'):
        return f"目标({a[5:]})"
    return _ATTR_LABELS.get(a, a)


def _format_condition(cond):
    """单条条件字典 → 中文。"""
    if not isinstance(cond, dict):
        return ''
    if 'at_location' in cond:
        return f"位于「{cond['at_location']}」"
    if 'time_between' in cond:
        return f"时段在 {cond['time_between']}"
    if 'not_cooldown' in cond:
        return f"未处于冷却（{cond.get('not_cooldown', '')}）"
    op = cond.get('op', '')
    attr = _attr_label(cond.get('attr', ''))
    if op == 'between':
        return f"{attr} 在 {cond.get('min')}~{cond.get('max')} 之间"
    op_map = {'>=': '≥', '<=': '≤', '>': '>', '<': '<', '==': '='}
    return f"{attr} {op_map.get(op, op)} {cond.get('value')}"


def _format_achievement_rule(a):
    """单个成就解锁规则 → 中文（供 LLM 事件提示词）。"""
    parts = []
    tc = a.trigger_conditions
    if isinstance(tc, str):
        try:
            tc = json.loads(tc)
        except Exception:
            tc = None
    if isinstance(tc, list) and any(isinstance(c, dict) for c in tc):
        conds = [_format_condition(c) for c in tc if isinstance(c, dict)]
        if conds:
            parts.append("条件：" + " 且 ".join(conds))
    ps = a.progress_source
    if isinstance(ps, str):
        try:
            ps = json.loads(ps)
        except Exception:
            ps = None
    if isinstance(ps, dict) and ps.get('attr'):
        parts.append(f"数值：{_attr_label(ps['attr'])} 达到 {a.target}（当前 {round(a.progress)}/{a.target}）")
    if a.unlock_event:
        parts.append(f"事件：触发「{a.unlock_event}」事件（当前 {round(a.progress)}/{a.target}）")
    if not parts and a.hint:
        parts.append(f"提示：{a.hint}")
    return "；".join(parts) if parts else "（无明确规则）"


def build_achievements_section(char):
    """构建成就段（第一优先级），聚焦未解锁成就。"""
    try:
        achievements = Achievement.query.filter_by(character_name=char.name).all()
    except Exception:
        return ""
    if not achievements:
        return ""
    # 未解锁排前，已解锁排后；同状态按进度降序
    achievements.sort(key=lambda x: (x.unlocked, -x.progress))
    lines = []
    for a in achievements:
        status = "已解锁" if a.unlocked else f"未解锁({round(a.progress)}/{a.target})"
        rule = _format_achievement_rule(a)
        lines.append(f"- 「{a.name}」[{status}] {rule}")
    return "\n".join(lines)


def build_goals_section(char):
    """构建成长目标段（第一优先级）。"""
    gr = char.goal_rules
    if isinstance(gr, str):
        try:
            gr = json.loads(gr)
        except Exception:
            gr = None
    if not gr or not isinstance(gr, dict):
        return ""
    goals = char.goals if isinstance(char.goals, dict) else {}
    display = char.goal_display if isinstance(char.goal_display, dict) else {}
    lines = []
    for key, rule in gr.items():
        if isinstance(rule, str):
            try:
                rule = json.loads(rule)
            except Exception:
                rule = {}
        if not isinstance(rule, dict):
            rule = {}
        label = display.get(key, key)
        cur = goals.get(key, 0)
        try:
            cur_s = f"{cur:.0f}"
        except Exception:
            cur_s = str(cur)
        if rule.get('conditions'):
            conds = [_format_condition(c) for c in rule['conditions'] if isinstance(c, dict)]
            rule_txt = "条件：" + " 且 ".join(conds) if conds else "进行中"
        elif rule.get('progress_source'):
            ps = rule['progress_source']
            attr = ps.get('attr', '') if isinstance(ps, dict) else ''
            rule_txt = f"数值：{_attr_label(attr)} 达到 {rule.get('target', 100)}（当前 {cur_s}）"
        else:
            rule_txt = f"当前 {cur_s}"
        lines.append(f"- 「{label}」{rule_txt}")
    return "\n".join(lines)


def build_achievements_goals_section(char):
    """组合成就 + 目标段，作为事件生成提示词的第一优先级内容。"""
    blocks = []
    goals = build_goals_section(char)
    if goals:
        blocks.append("【成长目标】\n" + goals)
    ach = build_achievements_section(char)
    if ach:
        blocks.append("【成就（未解锁优先）】\n" + ach)
    return "\n\n".join(blocks)


# 事件型成就触发词：trigger_id -> 中文语义（供 LLM 判断何时回填 trigger_id）
_TRIGGER_ID_SEMANTICS = {
    'friend_support': '获得朋友/他人的帮助或支持',
    'invitation_dinner': '被邀请吃饭/聚餐',
    'argument': '与人发生争执/吵架',
    'exam_stress': '面临考试压力/备考',
    'study_session': '自主学习/去图书馆或自习',
    'study_group': '参加学习小组/集体备考',
    'deadline_night': '赶截止期限/熬夜赶工',
    'social_party': '参加聚会/社交派对',
    'neighbor_visit': '邻居/室友来访',
    'coincidence': '偶遇/巧合事件',
    'self_reflection': '自我反思/内省',
    'life_lesson': '领悟人生道理/成长顿悟',
}


def _build_event_trigger_section(char):
    """构建事件型成就触发词段 + 白名单集合（供 LLM 回填 trigger_id 并回调检查）。

    覆盖两类角色：
    1) LLM 创建角色：trigger_id 来自 EventAchievementBinding（is_mission=False）
    2) 预设角色：trigger_id 来自静态 EVENT_ACHIEVEMENT_MAP 中映射到本角色成就的项
    返回 (section_text, whitelist_set)；无事件型成就时返回 ("", set())。
    """
    if not char or not char.name:
        return "", set()
    trigger_to_ach = {}  # trigger_id -> set(achievement_id)
    try:
        from backend.models import EventAchievementBinding, Achievement
        # 1) DB 绑定的事件型成就（LLM 创建角色）
        bindings = EventAchievementBinding.query.filter_by(
            character_name=char.name, is_mission=False).all()
        for b in bindings:
            if b.trigger_id:
                trigger_to_ach.setdefault(b.trigger_id, set()).add(b.achievement_id)
        # 2) 静态 MAP 中映射到本角色成就的 trigger（预设角色）
        try:
            from backend.game.achievement_checker import EVENT_ACHIEVEMENT_MAP
        except Exception:
            EVENT_ACHIEVEMENT_MAP = {}
        ach_ids = {a.achievement_id for a in Achievement.query.filter_by(
            character_name=char.name).all()}
        for tid, aids in (EVENT_ACHIEVEMENT_MAP or {}).items():
            matched = [aid for aid in aids if aid in ach_ids]
            if matched:
                trigger_to_ach.setdefault(tid, set()).update(matched)
    except Exception as e:
        logging.getLogger(__name__).warning(f"[Event] 构建事件触发词失败: {e}")
        return "", set()

    if not trigger_to_ach:
        return "", set()

    # 取成就名（优先 DB 成就名）
    try:
        from backend.models import Achievement
        ach_name = {a.achievement_id: a.name for a in Achievement.query.filter_by(
            character_name=char.name).all()}
    except Exception:
        ach_name = {}

    lines = ["（若上方某成就标注「事件：触发『xxx』事件」，而你生成的事件真实属于该类型，"
             "请在返回 JSON 中加 \"trigger_id\":\"xxx\" 以推进该成就；触发词含义如下：）"]
    for tid, aids in trigger_to_ach.items():
        sem = _TRIGGER_ID_SEMANTICS.get(tid, tid)
        names = "、".join(ach_name.get(aid, aid) for aid in aids)
        lines.append(f"- {tid}（{sem}）→ 推进成就「{names}」")
    lines.append("若无匹配则不要填写 trigger_id 字段。")
    return "\n".join(lines), set(trigger_to_ach.keys())


def llm_fallback_event(llm_config: dict = None, game_day: int = 0,
                      game_time: str = '08:00', daily_event_count: int = 0) -> dict | None:
    """使用 LLM 生成随机事件，失败返回 None"""
    if not llm_config or not llm_config.get('api_key'):
        logging.getLogger(__name__).info("[DIAG] llm_generate_event: no LLM config, returning None")
        return None

    char = get_character()
    _diag = logging.getLogger(__name__)
    _diag.info("[DIAG] llm_generate_event called, game_day=%d, char=%s, daily_event_count=%d",
               game_day, char.name if char else 'unknown', daily_event_count)

    # ── 每日硬上限：每角色每天最多 1 个 LLM 兜底事件（方案 D：cap=1，无事件则跳过）──
    if game_day > 0 and char and char.name:
        try:
            _today_llm = EventLog.query.filter(
                EventLog.game_day == game_day,
                EventLog.event_type == 'llm_event',
                EventLog.character_name == char.name,
            ).count()
            if _today_llm >= 1:
                _diag.info("[DIAG] llm_generate_event: 今日 LLM 事件已达上限(1)，跳过")
                return None
        except Exception as _ce:
            logger.warning(f"[Event] LLM 每日上限检查失败（不影响生成）: {_ce}")

    # 获取最近事件历史
    recent = EventLog.query.filter_by(character_name=char.name if char and char.name else '').order_by(EventLog.created_at.desc()).limit(5).all()
    history_text = "\n".join(f"- [{e.event_type}] {e.title}: {e.description}" for e in recent)

    # 获取最近聊天内容（最近 20 条）
    recent_dialogue_text = get_recent_dialogue(limit=20)

    # 构建聊天上下文段落
    dialogue_section = ""
    if recent_dialogue_text:
        char_display_name = char.name or '角色'
        dialogue_section = f"""
{recent_dialogue_text}

（以上为{char_display_name}与{getattr(get_character(), 'player_nickname', '') or getattr(get_character(), 'player_identity', '导师') or '导师'}的最近对话记录，如果其中有重要话题，请优先生成与之相关的事件）"""

    # ===== 场景匹配器：注入预设场景规则（已迁移到 build_tick_update_prompt）=====

    # 构建每日事件要求段落
    daily_section = ""
    if game_day > 0:
        daily_section = f"""
【每日事件要求】
今天（第{game_day}天）已生成 {daily_event_count} 个事件，每天最多 1 个。
请根据{char.name or '角色'}当前状态，生成 1 个事件，优先覆盖尚未出现的维度（不要重复同一维度）：
- 自身活动（学习、写作、运动、娱乐等）
- 好友关系变化（与玩家/朋友的互动、关系变化）
- 身体状态变化（精力、健康、心情波动）

事件必须发生在早上 6:00 到晚上 24:00 之间，请根据角色当前所处时段选取合理的事件场景。"""

    # 构建角色信息快照
    char_snapshot = build_character_snapshot(char)
    char_identity = char.identity_label or '女大学生'
    char_name = char.name or '角色'

    # 从 JSON goals 构建目标摘要
    goals_summary = ''
    if char.goals and isinstance(char.goals, dict):
        display = char.goal_display if isinstance(char.goal_display, dict) else {}
        parts = []
        for key, val in char.goals.items():
            label = display.get(key, key)
            parts.append(f"{label}:{val:.0f}")
        goals_summary = ', '.join(parts)

    # 构建成就 + 目标段（事件生成的【第一优先级】上下文）
    achievements_goals_section = build_achievements_goals_section(char)

    # 构建事件型成就触发词段 + 白名单（供 LLM 回填 trigger_id 并回调检查）
    event_trigger_section, event_trigger_whitelist = _build_event_trigger_section(char)

    constraints_section = get_llm_constraints_prompt(char)

    # ── NerdMemo 当年今日注入（Tier 4 灵魂伴侣专属）──
    nm_section = ""
    if get_relationship_tier(char) >= 4:
        try:
            from backend.game.memory import recall_nm_for_event
            game_date = char.get_game_date()[:3]  # (year, month, day)
            nm_section = recall_nm_for_event(
                player_nickname=getattr(char, 'player_nickname', '') or '',
                game_date=game_date,
                max_memories=3,
            )
        except Exception as e:
            nm_section = ""
            logging.getLogger(__name__).warning(f"NerdMemo Tick 召回失败: {e}")

    from backend.game.prompt_registry import get_prompt_manager
    pm = get_prompt_manager()
    event_prompt = pm.render("event.llm_fallback", char, extra={
        "context.time_period": get_time_period_prompt(char),
        "context.history_text": history_text or "（无历史事件）",
        "context.dialogue_section": dialogue_section,
        "context.daily_section": daily_section,
        "context.nm_section": nm_section,
        "context.goals_summary": goals_summary,
        "context.achievements_goals_section": achievements_goals_section,
        "context.event_achievement_triggers": event_trigger_section,
        "context.activity_scope": _infer_activity_scope(char),
        "context.constraints_section": constraints_section,
    })


    try:
        result = safe_llm_post(
            api_url=llm_config['api_url'],
            api_key=llm_config['api_key'],
            model=llm_config.get('model_name', 'gpt-4o-mini'),
            messages=[
                {"role": "system", "content": "你是一个游戏引擎事件生成模块。只返回JSON，不要任何解释。"},
                {"role": "user", "content": event_prompt}
            ],
            max_tokens=800,
            temperature=0.9,
            timeout=(30, 60),
            call_type="event_generate",
            character_name=char_name
        )

        if not result:
            logger.warning("LLM事件生成失败，跳过本次事件生成（无降级）")
            _diag.info("[DIAG] llm_generate_event: LLM call returned None/empty, game_day=%d", game_day)
            return None

        # 仅事件生成需要清除 reasoning_content，避免推理链污染 JSON 解析
        strip_reasoning_from_response(result)

        content = result["choices"][0]["message"]["content"]
        _diag.info("[DIAG] llm_generate_event: LLM response received, content_len=%d, preview=%.80s",
                   len(content) if content else 0, content[:80] if content else '(empty)')
        data, parse_error = extract_json_from_llm_response(content)
        if parse_error:
            logger.error(f"[LLM-ERROR] llm_generate_event 解析失败: {parse_error}")
            return None

        # 如果解析结果是 list（而非期望的 dict），无法处理
        if not isinstance(data, dict):
            logger.error(
                f"[LLM-ERROR] llm_generate_event 期望JSON对象，实际收到: {type(data).__name__}"
            )
            return None

        # LLM 主动跳过：当前状态/时段不适合生成有意义事件时返回 {"skip":true}
        if data.get('skip') is True or data.get('skip') == 'true':
            logger.info("[Event] LLM 返回 skip=true，本次不生成事件")
            _diag.info("[DIAG] llm_generate_event: LLM skip=true，无事件")
            return None

        # 兼容新旧 effects 格式：优先使用 state_changes 数组，回退到 effects 字典
        raw_effects = {}
        llm_state_changes = data.get('state_changes', [])
        if llm_state_changes and isinstance(llm_state_changes, list):
            for sc in llm_state_changes:
                if isinstance(sc, dict) and 'attribute' in sc and 'delta' in sc:
                    raw_effects[sc['attribute']] = sc['delta']
        if not raw_effects:
            raw_effects = data.get('effects', {})

        # 行为通道：若事件声明了 action_type（送礼/约会/肢体接触等），统一走
        # 属性增量表，与文本通道共用同一套规则（不进文本分类）。事件模板在返回的
        # JSON 里加 {"action_type": "gift"} 即可触发，未声明则沿用 LLM 原有效果。
        _baction = data.get('action_type')
        if _baction:
            try:
                from backend.game.action_effects import compute_action_effects
                from backend.game.dialogue import get_relationship_tier
                _btier = get_relationship_tier(char)
                _bpers = getattr(char, 'personality', '') or getattr(char, 'personality_type', '') or ''
                _beff = compute_action_effects(_baction, tier=_btier, personality=_bpers, apply_tier=True)
                if _beff:
                    logger.info(f"[Event] 行为通道 action_type={_baction} → 走增量表: {_beff}")
                    raw_effects = _beff
            except Exception as _be:
                logger.warning(f"[Event] 行为通道增量表失败，沿用 LLM 效果: {_be}")

        # 快照旧值（用于计算 state_changes）
        old_values = {}
        for key in raw_effects:
            val = getattr(char, key, None)
            if val is not None:
                old_values[key] = round(val)

        effects = apply_attr_changes(raw_effects, character=char)

        # 计算 state_changes（旧→新→delta）
        state_changes = []
        for key, delta in effects.items():
            new_val = getattr(char, key, None)
            if new_val is not None:
                state_changes.append({
                    'attribute': key,
                    'old': old_values.get(key, round(new_val - delta)),
                    'new': round(new_val),
                    'delta': round(delta)
                })

        # 职业硬约束：职场人士禁止生成学生专属事件（校园/上课/考试/社团/同学）
        event_title = data.get('title', '随机事件')
        event_content = data.get('content', data.get('description', ''))
        _student_kw = ['上课', '考试', '期中考试', '期末考试', '社团', '同学', '校园', '选修', '补课', '值日']
        _identity = (char.identity_label or '')
        _is_pro = not any(k in _identity for k in ['学生', '大学生', '研究生'])
        _text = f"{event_title} {event_content}"
        if _is_pro and any(k in _text for k in _student_kw):
            logger.info(f"[Event] 职业硬约束命中，跳过学生专属事件: {event_title}")
            _diag.info("[DIAG] llm_generate_event: 职业硬约束跳过学生事件")
            return None

        event_type = 'llm_event'
        event_category = data.get('type', 'daily')
        event_location = data.get('location', char.location if hasattr(char, 'location') else '')
        relation_changes = data.get('relation_changes', []) if isinstance(data.get('relation_changes'), list) else []

        # 构建完整的 effects（包含 state_changes 和 relation_changes）
        full_effects = {
            'changes': effects,
            'state_changes': state_changes,
            'relation_changes': relation_changes
        }

        # 解析 LLM 返回的 time 字段（HH:MM，6:00-24:00），否则随机白天时段
        event_time = game_time
        _raw_time = data.get('time')
        if isinstance(_raw_time, str) and re.match(r'^\d{1,2}:\d{2}$', _raw_time):
            _h, _m = _raw_time.split(':')
            if 6 <= int(_h) <= 23 and 0 <= int(_m) <= 59:
                event_time = f"{int(_h):02d}:{int(_m):02d}:00"
        if event_time == game_time:
            event_time = f"{random.randint(8, 22):02d}:{random.choice([0, 15, 30, 45]):02d}:00"

        event = EventLog(
            event_type=event_type,
            event_category=event_category,
            title=event_title,
            description=event_content,
            effects=json.dumps(full_effects, ensure_ascii=False),
            game_day=game_day,
            game_time=event_time,
            location=event_location,
            character_name=char.name or ''
        )
        db.session.add(event)
        db.session.commit()

        # 事件型成就：若 LLM 回填了白名单内的 trigger_id，则回调检查（推进进度）
        _tid = (data.get('trigger_id') or '').strip() if isinstance(data, dict) else ''
        if _tid and _tid in event_trigger_whitelist:
            try:
                from backend.game.achievement_checker import check_event_achievements
                check_event_achievements(char, _tid)
                logger.info(f"[Event] 事件型成就触发: {char.name} trigger_id={_tid}")
            except Exception as _ce:
                logger.warning(f"[Event] 事件型成就检查失败(不影响事件入库): {_ce}")

        return {
            'event': event.to_dict(),
            'effects': effects,
            'state_changes': state_changes
        }

    except Exception as e:
        logger.error(f"LLM事件解析失败: {e}")
        return None


def build_active_message_context(char):
    """构建 LLM 决策所需的完整上下文"""
    from backend.game.dialogue import get_relationship_tier, get_tier_config

    tier = get_relationship_tier(char)
    tier_cfg = get_tier_config(tier)

    # 1. 角色全量状态
    character_state = {
        'physical': {
            'health': round(char.health, 0), 'energy': round(char.energy, 0),
            'hunger': round(char.hunger, 0), 'hygiene': round(char.hygiene, 0)
        },
        'mental': {
            'mood': round(char.mood, 0), 'stress': round(char.stress, 0),
            'happiness': round(char.happiness, 0), 'loneliness': round(char.loneliness, 0),
            'confidence': round(char.confidence, 0), 'motivation': round(char.motivation, 0),
            'creativity': round(char.creativity, 0), 'joy': round(char.joy, 0),
            'anger': round(char.anger, 0), 'disappointment': round(char.disappointment, 0),
            'boredom': round(char.boredom, 0), 'fulfillment': round(char.fulfillment, 0)
        },

    }

    # 2. 关系阶梯
    relationship_tier = {
        'tier': tier,
        'name': tier_cfg['name'],
        'tone': tier_cfg['tone_cn'],
        'effect_scale': tier_cfg['effect_scale'],
        'max_reply_len': tier_cfg['max_reply_len'],
        'values': {
            'trust': round(char.player_trust, 0),
            'affection': round(char.player_affection, 0),
            'respect': round(char.player_respect, 0),
            'intimacy': round(char.player_intimacy, 0)
        },
        'relationship_status': char.relationship_status
    }

    # 3. 最近事件（5条）
    recent_events = []
    events = EventLog.query.filter_by(character_name=char.name if char.name else '').order_by(EventLog.created_at.desc()).limit(5).all()
    for e in reversed(events):
        recent_events.append({
            'type': e.event_type,
            'title': e.title,
            'description': e.description
        })

    # 4. 最近对话 —— 从 MD 文件读取（含游戏时间戳）
    from backend.chat_history import load_all_sessions
    all_dialogues = load_all_sessions()
    recent_dialogue = []
    last_game_day = 0
    last_game_hour = 0
    last_game_minute = 0
    last_speaker = ''
    for msg in reversed(all_dialogues):
        if msg['game_day'] is not None:
            recent_dialogue.append({
                'speaker': msg['speaker'],
                'content': msg['content'],
                'game_day': msg['game_day'],
                'game_time': msg['game_time'],
            })
            # 记录最后一条有效消息的游戏时间
            if not last_speaker:
                last_speaker = msg['speaker']
                last_game_day = msg['game_day']
                if msg['game_time']:
                    parts = msg['game_time'].split(':')
                    last_game_hour = int(parts[0])
                    last_game_minute = int(parts[1]) if len(parts) > 1 else 0
        if len(recent_dialogue) >= 8:
            break
    recent_dialogue.reverse()

    # 5. 时间上下文 — 基于游戏时间计算
    game_day = getattr(char, 'game_day', 1)
    game_hour = getattr(char, 'game_hour', 0)
    time_since_last = ''
    if last_speaker:
        day_diff = game_day - last_game_day
        hour_diff = game_hour - last_game_hour
        total_hours = day_diff * 24 + hour_diff
        if total_hours < 1:
            time_since_last = '刚刚'
        elif total_hours < 24:
            time_since_last = f'{total_hours}小时前'
        else:
            time_since_last = f'{day_diff}天前'

    time_context = {
        'time_since_last_dialogue': time_since_last,
        'last_speaker': (char.name or '角色') if last_speaker == 'character' else ((getattr(char, 'player_nickname', '') or getattr(char, 'player_identity', '导师') or '导师') if last_speaker == 'player' else ''),
        'game_hour': game_hour,
        'game_day': game_day
    }

    return {
        'character_state': character_state,
        'relationship_tier': relationship_tier,
        'recent_events': recent_events,
        'recent_dialogue': recent_dialogue,
        'time_context': time_context
    }


def llm_decide_and_generate_message(llm_config: dict, game_day: int = 0,
                                    game_time: str = '08:00') -> dict | None:
    """LLM 综合分析后决定是否主动发消息及消息内容"""
    char = get_character()
    if not llm_config or not llm_config.get('api_key'):
        return None

    context = build_active_message_context(char)
    cs = context['character_state']
    rt = context['relationship_tier']
    tc = context['time_context']

    # 构建事件文本
    events_text = ''
    if context['recent_events']:
        events_text = '\n'.join(
            f"[{e['type']}] {e['title']}：{e['description']}"
            for e in context['recent_events']
        )
    else:
        events_text = '（暂无事件记录）'

    # 构建对话文本
    dialogue_text = ''
    char_name = char.name or '角色'
    if context['recent_dialogue']:
        lines = []
        for d in context['recent_dialogue']:
            speaker_label = char_name if d['speaker'] == 'character' else (getattr(char, 'player_nickname', '') or getattr(char, 'player_identity', '导师') or '导师')
            lines.append(f"{speaker_label}：{d['content']}")
        dialogue_text = '\n'.join(lines)
    else:
        dialogue_text = '（暂无对话记录）'

    # 构建近期资讯素材（仅 Tier≥3 时才允许主动聊起）
    news_text = ''
    try:
        from backend.game.news_service import get_unused_news
        major = getattr(char, 'major', '') or ''
        news_items = get_unused_news(major=major, limit=2)
        if news_items:
            news_lines = [f"- {n.title}：{n.summary}" for n in news_items]
            news_text = '\n'.join(news_lines)
    except Exception as _e:
        logger.debug(f"资讯素材获取跳过: {_e}")

    news_section = ''
    if news_text:
        news_section = f"""
【近期资讯素材】（你最近看到了这些新闻）
{news_text}

资讯使用规则：
- 仅当关系阶梯达到 Tier≥3（知心好友及以上）时，才允许在主动消息里聊起这些资讯。
- 聊起时要自然（如"今天看到一条关于AI的新闻，挺有意思的……"），不要像播报新闻。
- 严格禁止编造新闻里没有的来源、数据或细节。
- 资讯只是可选素材，不必每条主动消息都用，也不要强塞。"""

    tier_desc_map = {
        0: '陌生人——几乎不会主动联系，除非有极端需要',
        1: '泛泛之交——很少主动联系，除非状态异常',
        2: '信任的人——有事情会想到找他聊聊',
        3: '知心好友——会主动分享日常和心情',
        4: '灵魂伴侣——频繁地想和他说话，随时分享一切'
    }

    char_identity = char.identity_label or '女大学生'
    char_age = char.age or 20
    char_personality = build_personality_summary(char)

    constraints_section = get_llm_constraints_prompt(char)

    from backend.game.prompt_registry import get_prompt_manager
    pm = get_prompt_manager()
    decision_prompt = pm.render("event.active_decision", char, extra={
        "context.time_period": get_time_period_prompt(char),
        "context.tier_name": rt['name'],
        "context.tier": str(rt['tier']),
        "context.tier_desc": tier_desc_map.get(rt['tier'], ''),
        "context.tier_trust": str(rt['values']['trust']),
        "context.tier_affection": str(rt['values']['affection']),
        "context.tier_respect": str(rt['values']['respect']),
        "context.tier_intimacy": str(rt['values']['intimacy']),
        "context.events_text": events_text,
        "context.dialogue_text": dialogue_text,
        "context.news_section": news_section,
        "context.time_since_last_dialogue": tc['time_since_last_dialogue'] or '从未',
        "context.last_speaker": tc['last_speaker'] or '无',
        "context.constraints_section": constraints_section,
        "player.nickname": getattr(char, 'player_nickname', '') or getattr(char, 'player_identity', '导师') or '导师',
    })

    try:
        result = safe_llm_post(
            api_url=llm_config['api_url'],
            api_key=llm_config['api_key'],
            model=llm_config.get('model_name', 'gpt-4o-mini'),
            messages=[
                {"role": "system", "content": "你是一个角色行为决策模块。只返回JSON，不要任何解释。"},
                {"role": "user", "content": decision_prompt}
            ],
            max_tokens=3500,
            temperature=0.8,
            timeout=(30, 60),
            call_type="active_message_decide",
            character_name=char_name
        )

        if not result:
            raise LLMNetworkError("safe_llm_post 返回 None（网络超时或连接错误）")

        content = result["choices"][0]["message"]["content"]
        data, parse_error = extract_json_from_llm_response(content)
        if parse_error:
            logger.error(f"[LLM-ERROR] llm_decide_and_generate_message 解析失败: {parse_error}")
            return None

        if not data.get('should_send'):
            return None  # LLM 决定不发送

        message_text = data.get('message', '')
        effects = data.get('effects', {})

        if not message_text:
            return None

        # 应用 effects
        if effects:
            apply_attr_changes(effects, character=char)

        # 记录事件到 DB
        event = EventLog(
            event_type='auto_message_llm',
            title=f'{char_name}主动发来消息',
            description=message_text,
            effects=json.dumps(effects, ensure_ascii=False),
            game_day=game_day,
            game_time=game_time,
            character_name=char.name or ''
        )
        db.session.add(event)
        db.session.commit()

        # 持久化到聊天 MD 文件（此前主动消息只显示在 DOM，刷新即丢失）
        try:
            game_time_str = f"第{game_day}天 {game_time}"
            append_chat_message('character', message_text,
                                game_time_str=game_time_str,
                                effects=effects if effects else None)
        except Exception as _e:
            logger.warning(f"主动消息落盘失败: {_e}")

        return {
            'message': message_text,
            'effects': effects,
            'event': event.to_dict(),
            'reason': data.get('reason', ''),
            'game_day': game_day,
            'game_time': game_time
        }

    except LLMNetworkError:
        raise
    except Exception as e:
        logger.error(f"LLM主动消息决策/生成失败: {e}")
        return None


def llm_generate_active_messages(llm_config: dict, count=5):
    """调用 LLM 生成候选主动消息，存入 ActiveMessageQueue"""
    char = get_character()
    if not llm_config or not llm_config.get('api_key'):
        return None

    char_name = char.name or '角色'
    char_personality = build_personality_summary(char)

    # 构建全量角色状态
    status_str = (
        f"角色「{char_name}」当前状态：\n"
        f"物理：健康{char.health:.0f} 精力{char.energy:.0f} 饥饿{char.hunger:.0f} 卫生{char.hygiene:.0f}\n"
        f"心理：心情{char.mood:.0f} 压力{char.stress:.0f} 幸福{char.happiness:.0f} 孤独{char.loneliness:.0f} "
        f"自信{char.confidence:.0f} 动力{char.motivation:.0f} 创造力{char.creativity:.0f} "
        f"开心{char.joy:.0f} 愤怒{char.anger:.0f} 失望{char.disappointment:.0f} 无聊{char.boredom:.0f} 充实{char.fulfillment:.0f}\n"
        f"技能：{char.skills_summary}\n"
        f"关系：信任{char.player_trust:.0f} 好感{char.player_affection:.0f} 尊重{char.player_respect:.0f} 亲密{char.player_intimacy:.0f}\n"
        f"目标：{char.goals_summary}  # @deprecated: 旧格式，建议改为从 char.goals JSON 遍历\n"
        f"位置：{char.location} 穿搭：{char.outfit_style}"
    )

    from backend.game.prompt_registry import get_prompt_manager
    pm = get_prompt_manager()
    prompt = pm.render("event.active_message", char, extra={
        "context.status_str": status_str,
        "context.char_personality": char_personality,
        "context.count": str(count),
        "context.char_pronoun": '她' if (char.gender or 'female') == 'female' else '他',
    })

    try:
        result = safe_llm_post(
            api_url=llm_config['api_url'],
            api_key=llm_config['api_key'],
            model=llm_config.get('model_name', 'gpt-4o-mini'),
            messages=[
                {"role": "system", "content": "你是一个角色对话生成模块。只返回JSON数组，不要任何解释。"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=llm_config.get('max_tokens', 800),
            temperature=0.9,
            timeout=(30, 120),
            call_type="active_message_batch",
            character_name=char.name
        )

        if not result:
            logger.warning("LLM主动消息生成失败")
            return None

        content = result["choices"][0]["message"]["content"]
        data, parse_error = extract_json_from_llm_response(content)
        if parse_error:
            logger.error(f"[LLM-ERROR] llm_generate_active_messages 解析失败: {parse_error}")
            return None

        if not isinstance(data, list):
            return None

        saved = 0
        for item in data:
            msg = ActiveMessageQueue(
                content=item.get('content', ''),
                effects=json.dumps(item.get('effects', {}), ensure_ascii=False)
            )
            db.session.add(msg)
            saved += 1
        db.session.commit()
        logger.info(f"LLM生成并存入 {saved} 条主动消息")
        return saved

    except Exception as e:
        logger.error(f"LLM主动消息生成/解析失败: {e}")
        return None


def trigger_auto_message(llm_config=None, game_day: int = 0,
                         game_time: str = '08:00') -> dict | None:
    """根据角色状态触发主动消息（先硬条件检测理由，再 LLM 生成内容）"""
    char = get_character()
    if not char:
        return None

    # ===== 第0步：MessageReasonDetector 硬条件检测 =====
    try:
        from backend.game.message_reason_detector import MessageReasonDetector
        reasons = MessageReasonDetector.detect(char)
        if not reasons:
            return None  # 今天没有发消息的理由
    except Exception:
        reasons = []

    # ===== 第1步：冷却检查 =====
    current_tick = getattr(char, 'game_tick', 0)
    if current_tick > 0 and char.last_active_message_tick > 0:
        ticks_since_last = current_tick - char.last_active_message_tick
        if ticks_since_last < char.active_message_cooldown_ticks:
            return None  # 冷却期中，跳过

    # 更新上次尝试 tick
    char.last_active_message_tick = current_tick
    db.session.commit()

    # ===== 第1步：LLM 综合分析决策 =====
    if llm_config and llm_config.get('api_key'):
        # 根据关系阶梯动态调整冷却间隔
        from backend.config import Config
        tier = get_relationship_tier(char)
        if tier >= 4:
            char.active_message_cooldown_ticks = Config.ACTIVE_MESSAGE_COOLDOWN_TIER4
        elif tier <= 1:
            char.active_message_cooldown_ticks = Config.ACTIVE_MESSAGE_COOLDOWN_TIER01
        else:
            char.active_message_cooldown_ticks = Config.ACTIVE_MESSAGE_COOLDOWN_DEFAULT
        db.session.commit()

        try:
            result = llm_decide_and_generate_message(llm_config, game_day, game_time)
        except LLMNetworkError:
            # 网络错误：翻倍冷却防止连续失败锤爆 API（上限 60 tick）
            new_cooldown = min(char.active_message_cooldown_ticks * 2, 60)
            logger.error(
                f"LLM主动消息网络错误，冷却翻倍: "
                f"{char.active_message_cooldown_ticks} → {new_cooldown} ticks"
            )
            char.active_message_cooldown_ticks = new_cooldown
            db.session.commit()
            return None

        if result:
            return result
        # LLM 决定不发送或失败，不继续降级——尊重 LLM 判断
        return None

    # LLM 不可用时不再降级到预生成队列或硬编码池，返回 None
    return None


def check_achievements():
    """检查并解锁成就（按角色过滤，支持 achievement_id 驱动）"""
    char = get_character()
    if not char:
        return []

    # 成就ID→技能键映射表（优先从 char.skills JSON 读取，再回退到直接列属性）
    # 注意：键名必须与 Character.skills / skill_display 的实际 key 对齐，
    # 例如预设角色用的是 legal_knowledge / coding / painting / game_design /
    # teamwork / debate 等，而不是废弃的旧列名 *_skill。
    achievement_skill_map = {
        'coding_master': ['coding'],
        'writer_dream': ['writing'],
        'social_butterfly': ['social'],
        'network_builder': ['social'],
        'debate_champ': ['debate'],
        'color_master': ['painting'],
        'sketch_100': ['painting'],
        'creative_genius': ['creativity'],
        'chief_resident': ['medical_knowledge'],
        'stress_master': ['stress_resistance'],
        'partner_track': ['legal_knowledge'],
        'law_reciter': ['legal_knowledge'],
        'design_guru': ['game_design'],
        'team_leader': ['teamwork'],
        'player_bond': ['player_affection'],
        'quiet_confidence': ['confidence'],
        'bookworm': ['reading_count'],
    }

    unlocked = []
    achievements = Achievement.query.filter_by(character_name=char.name, unlocked=False).all()
    if not achievements:
        achievements = Achievement.query.filter_by(character_name='', unlocked=False).all()

    for a in achievements:
        new_progress = None

        # 优先：行内 progress_source（LLM 数值型成就，DB 持久化，重启安全）
        if a.progress_source:
            try:
                ps = a.progress_source
                if isinstance(ps, str):
                    ps = json.loads(ps)
                if isinstance(ps, dict) and ps.get('attr'):
                    from backend.game.condition_matcher import resolve_attr
                    val = resolve_attr(char, ps['attr'])
                    if val is not None:
                        scale = ps.get('scale', 1) or 1
                        new_progress = val * scale
            except Exception:
                pass

        # 其次：通过 achievement_id 映射技能（优先 skills JSON，再回退直接列属性；兼容旧角色）
        if new_progress is None and a.achievement_id in achievement_skill_map:
            candidate_keys = achievement_skill_map[a.achievement_id]
            skills_dict = char.skills if isinstance(char.skills, dict) else {}
            found = None
            for k in candidate_keys:
                if k in skills_dict:
                    found = skills_dict[k]
                    break
            if found is None:
                # 回退到直接列属性（心理/关系属性）；无对应属性则保持原 progress 不覆盖
                found = getattr(char, candidate_keys[0], None)
            if found is not None:
                new_progress = found
        elif a.achievement_id == 'library_regular':
            # 探索访问图书馆次数
            from backend.models import CharacterActivityMap
            lib = CharacterActivityMap.query.filter_by(
                character_name=char.name, venue_id='library').first()
            new_progress = (lib.visit_count or 0) * 5 if lib else 0
        elif a.achievement_id == 'best_roommate':
            # 朋友互动次数 → progress
            friend_count = Friend.query.filter_by(character_name=char.name).count()
            new_progress = min(a.target, (friend_count or 0) * 3)
        elif a.achievement_id == 'night_shift_veteran':
            from backend.models import CharacterActivityMap
            ward = CharacterActivityMap.query.filter_by(
                character_name=char.name, venue_id='ward').first()
            new_progress = (ward.visit_count or 0) * 3 if ward else 0

        if new_progress is not None:
            a.progress = min(a.target, max(0, new_progress))

        if a.progress >= a.target:
            a.unlocked = True
            a.unlocked_at = beijing_now()
            unlocked.append(a.to_dict())

    if unlocked:
        db.session.commit()
    return unlocked


def get_recent_events(limit=20):
    char = get_character()
    query = EventLog.query
    if char and char.name:
        query = query.filter_by(character_name=char.name)
    return query.order_by(EventLog.created_at.desc()).limit(limit).all()


def update_friend_relations_llm(char, events: list, llm_config: dict) -> dict | None:
    """LLM 驱动动态朋友关系更新。

    根据角色当前状态和最近事件，让 LLM 判断朋友关系数值的变化。

    Args:
        char: Character 对象
        events: 最近生成的事件列表（可为空）
        llm_config: LLM 配置字典

    Returns:
        dict: {'changes': [...], 'event_log': dict} 或 None（LLM 不可用时降级为随机微调）
    """
    from backend.models import Friend

    friends = Friend.query.filter_by(character_name=char.name).all()
    # 提取玩家（男主）名称，用于后续注入 prompt 和结果匹配
    player_name = (getattr(char, 'player_nickname', '') or '').strip()
    if not player_name:
        player_name = (getattr(char, 'player_identity', '') or '').strip() or '玩家'
    if not friends and not player_name:
        return None

    # 构建朋友关系当前值（按关系类型分组展示）
    friend_lines = []
    from backend.game.relation_state import RELATION_TYPE_CONFIG
    type_order = ['friend', 'family', 'mentor', 'colleague', 'rival', 'enemy', 'ex_boyfriend', 'client', 'acquaintance', 'protege', 'other']
    type_label = {
        'friend': '朋友', 'family': '家人', 'mentor': '导师', 'colleague': '同事',
        'rival': '对手', 'enemy': '敌人', 'ex_boyfriend': '前男友', 'client': '客户',
        'acquaintance': '普通关系', 'protege': '后辈', 'other': '其他',
    }
    for t in type_order:
        type_friends = [f for f in friends if f.relation_type == t]
        if not type_friends:
            continue
        label = type_label.get(t, t)
        friend_lines.append(f"[{label}]")
        for f in type_friends:
            parts = [f"{f.name}（{f.role or '朋友'}）"]
            config = RELATION_TYPE_CONFIG.get(t, RELATION_TYPE_CONFIG['friend'])
            for dim in config['active_dims']:
                val = getattr(f, dim, 0)
                parts.append(f"{dim}{val:.0f}")
            for dim in config['negative_dims']:
                val = getattr(f, dim, 0)
                parts.append(f"{dim}{val:.0f}")
            friend_lines.append("  " + " ".join(parts))
    friend_status = "\n".join(friend_lines)

    # 注入玩家（男主）关系到 friend_status 最前面
    is_dating = getattr(char, 'relationship_status', 'friends') == 'dating'
    player_label = '男友' if is_dating else '玩家'
    player_dim_label = '男友' if is_dating else '玩家'
    player_status_line = (
        f"[{player_label}]\n"
        f"  {player_name}（{player_dim_label}）"
        f"closeness{char.player_intimacy:.0f} "
        f"trust{char.player_trust:.0f} "
        f"affection{char.player_affection:.0f}"
    )
    if friend_status:
        friend_status = player_status_line + "\n" + friend_status
    else:
        friend_status = player_status_line

    # 构建角色当前状态
    char_status = (
        f"心情{char.mood:.0f}/压力{char.stress:.0f}/幸福感{char.happiness:.0f}/"
        f"精力{char.energy:.0f}/动力{char.motivation:.0f}"
    )

    char_name = char.name or '角色'

    # 最近事件
    if events:
        event_texts = [f"- {e['event']['title']}: {e['event']['description']}" for e in events]
        events_text = "\n".join(event_texts)
    else:
        events_text = "（无特殊事件）"

    from backend.game.prompt_registry import get_prompt_manager
    from backend.game.variable_resolver import render_template
    _pm = get_prompt_manager()
    prompt = render_template(_pm.get("event.relation_update"), {
        "char_name": char_name,
        "char_pronoun": '她' if (getattr(char, 'gender', None) or 'female') == 'female' else '他',
        "char_status": char_status,
        "friend_status": friend_status,
        "events_text": events_text,
    })

    max_retries = 2
    for attempt in range(max_retries + 1):
        result = safe_llm_post(
            api_url=llm_config['api_url'],
            api_key=llm_config['api_key'],
            model=llm_config.get('model_name', 'gpt-4o-mini'),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0.7,
            timeout=(30, 60),
            call_type="friend_relations_update",
            character_name=char_name
        )

        if not result:
            if attempt < max_retries:
                logger.warning(f"[LLM-WARN] update_friend_relations_llm 第{attempt+1}次返回None（网络/API故障），重试...")
                continue
            return None

        content = result["choices"][0]["message"]["content"] or ''
        if content.strip():
            break
        # 空内容：不重复请求，直接让 extract_json_from_llm_response 处理
        # （LLM 已返回但无内容，重试同一请求无意义——应尝试 JSON 修复）
        logger.warning(
            f"[LLM-WARN] update_friend_relations_llm 第{attempt+1}次返回空内容，"
            f"不再重试（重试相同请求无法改善空内容问题）"
        )
        break
    else:
        return None

    data, parse_error = extract_json_from_llm_response(content)
    if parse_error:
        logger.error(f"[LLM-ERROR] update_friend_relations_llm 解析失败: {parse_error}")
        return None
    relations = data.get('relations', []) if isinstance(data, dict) else []
    if not relations:
        return None

    # 应用关系变化（先记录旧值，再应用新值）
    changes = []
    for rel in relations:
        name = rel.get('name', '')

        # 检查是否是玩家（男主），特殊处理：更新 Character.player_* 字段
        if name == player_name:
            closeness_delta = max(-5, min(5, int(rel.get('closeness', 0))))
            trust_delta = max(-5, min(5, int(rel.get('trust', 0))))
            affection_delta = max(-5, min(5, int(rel.get('affection', 0))))
            if closeness_delta == 0 and trust_delta == 0 and affection_delta == 0:
                continue
            old_intimacy = char.player_intimacy
            old_trust = char.player_trust
            old_affection = char.player_affection
            char.player_intimacy = max(0, min(100, char.player_intimacy + closeness_delta))
            char.player_trust = max(0, min(100, char.player_trust + trust_delta))
            char.player_affection = max(0, min(100, char.player_affection + affection_delta))
            changes.append({
                'name': name,
                'closeness': closeness_delta,
                'trust': trust_delta,
                'affection': affection_delta,
                'rivalry': 0,
                'hostility': 0,
                'fear': 0,
                'reason': rel.get('reason', '与玩家的关系变化'),
                'old': {'closeness': old_intimacy, 'trust': old_trust, 'affection': old_affection,
                        'rivalry': 0, 'hostility': 0, 'fear': 0},
                'new': {'closeness': char.player_intimacy, 'trust': char.player_trust, 'affection': char.player_affection,
                        'rivalry': 0, 'hostility': 0, 'fear': 0},
            })
            continue

        friend = Friend.query.filter_by(name=name).first()
        if not friend:
            continue

        closeness_delta = max(-5, min(5, int(rel.get('closeness', 0))))
        trust_delta = max(-5, min(5, int(rel.get('trust', 0))))
        affection_delta = max(-5, min(5, int(rel.get('affection', 0))))
        rivalry_delta = max(-5, min(5, int(rel.get('rivalry', 0))))
        hostility_delta = max(-5, min(5, int(rel.get('hostility', 0))))
        fear_delta = max(-5, min(5, int(rel.get('fear', 0))))

        if (closeness_delta == 0 and trust_delta == 0 and affection_delta == 0
                and rivalry_delta == 0 and hostility_delta == 0 and fear_delta == 0):
            continue

        # 记录旧值
        old_closeness = friend.closeness
        old_trust = friend.trust
        old_affection = friend.affection
        old_rivalry = friend.rivalry
        old_hostility = friend.hostility
        old_fear = friend.fear

        friend.closeness = max(0, min(100, friend.closeness + closeness_delta))
        friend.trust = max(0, min(100, friend.trust + trust_delta))
        friend.affection = max(0, min(100, friend.affection + affection_delta))
        friend.rivalry = max(0, min(100, friend.rivalry + rivalry_delta))
        friend.hostility = max(0, min(100, friend.hostility + hostility_delta))
        friend.fear = max(0, min(100, friend.fear + fear_delta))
        friend.last_interaction = beijing_now()

        changes.append({
            'name': name,
            'closeness': closeness_delta,
            'trust': trust_delta,
            'affection': affection_delta,
            'rivalry': rivalry_delta,
            'hostility': hostility_delta,
            'fear': fear_delta,
            'reason': rel.get('reason', f'关系变化'),
            'old': {'closeness': old_closeness, 'trust': old_trust, 'affection': old_affection,
                    'rivalry': old_rivalry, 'hostility': old_hostility, 'fear': old_fear},
            'new': {'closeness': friend.closeness, 'trust': friend.trust, 'affection': friend.affection,
                    'rivalry': friend.rivalry, 'hostility': friend.hostility, 'fear': friend.fear},
        })

    if changes:
        db.session.commit()

        # 生成事件日志（含变化前后数值）
        for c in changes:
            desc_parts = []
            if c['closeness'] != 0:
                desc_parts.append(f"亲密度{c['old']['closeness']:.0f}→{c['new']['closeness']:.0f}（{c['closeness']:+d}）")
            if c['trust'] != 0:
                desc_parts.append(f"信任{c['old']['trust']:.0f}→{c['new']['trust']:.0f}（{c['trust']:+d}）")
            if c['affection'] != 0:
                desc_parts.append(f"好感{c['old']['affection']:.0f}→{c['new']['affection']:.0f}（{c['affection']:+d}）")
            if c['rivalry'] != 0:
                desc_parts.append(f"竞争度{c['old']['rivalry']:.0f}→{c['new']['rivalry']:.0f}（{c['rivalry']:+d}）")
            if c['hostility'] != 0:
                desc_parts.append(f"敌意{c['old']['hostility']:.0f}→{c['new']['hostility']:.0f}（{c['hostility']:+d}）")
            if c['fear'] != 0:
                desc_parts.append(f"恐惧{c['old']['fear']:.0f}→{c['new']['fear']:.0f}（{c['fear']:+d}）")

            desc = f"💞 与{c['name']}的关系变化：{'，'.join(desc_parts)}（{c['reason']}）"

            event_log = EventLog(
                event_type='relation_change',
                title=f"与{c['name']}的关系变化",
                description=desc,
                effects=json.dumps({
                    'friend_name': c['name'],
                    'delta': {
                        'closeness': c['closeness'],
                        'trust': c['trust'],
                        'affection': c['affection'],
                        'rivalry': c['rivalry'],
                        'hostility': c['hostility'],
                        'fear': c['fear'],
                    },
                    'old': c['old'],
                    'new': c['new']
                }, ensure_ascii=False),
                game_day=char.game_day,
                game_time=beijing_now().strftime('%H:%M:%S'),
                character_name=char.name or '',
            )
            db.session.add(event_log)

        db.session.commit()

        return {
            'changes': changes,
            'event_log': event_log.to_dict(),
        }

    return None


# ── 注册 event 模块各 prompt 的默认文本到 REGISTRY ──
from backend.game.prompt_registry import REGISTRY as _pr
import os as _os
_e_dir = _os.path.dirname(_os.path.abspath(__file__))
# event.random / event.active_decision — 已提取到独立文件
_pr["event.random"].default_text = f"# 此 prompt 由 event.random 对应函数内的 f-string 生成。"
_pr["event.active_decision"].default_text = open(_os.path.join(_e_dir, "_active_decision_prompt.txt"), encoding="utf-8").read()
# event.llm_fallback / event.relation_update — 原文存于独立文件
_pr["event.llm_fallback"].default_text = open(_os.path.join(_e_dir, "_llm_fallback_prompt.txt"), encoding="utf-8").read()
_pr["event.relation_update"].default_text = open(_os.path.join(_e_dir, "_relation_update_prompt.txt"), encoding="utf-8").read()
# event.active_message — 已迁移到 pm.render()
_pr["event.active_message"].default_text = (
    "{context.status_str}\n\n"
    "请基于{character.name}当前状态和游戏时间，生成 {context.count} 条{context.char_pronoun}会主动发给玩家的消息。\n"
    "每条消息要自然、符合{context.char_pronoun}性格——{context.char_personality}的{character.identity}。\n"
    '返回 JSON 数组，每条含 content 和 effects。'
)
