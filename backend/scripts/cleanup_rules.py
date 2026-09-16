"""清洗 triggered_events.json：修复格式 + 加 location 字段"""
import json, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'game', 'triggered_events.json')

# 为每条规则分配地点
LOCATION_MAP = {
    'morning_jog': '操场',
    'skipped_meal': '食堂',
    'healthy_snack': '宿舍',
    'catch_cold': '校医院',
    'took_shower': '宿舍',
    'sleep_disorder': '宿舍',
    'gym_workout': '健身房',
    'stomach_ache': '食堂',
    'sun_bath': '阳台',
    'sports_injury': '操场',
    'study_session': '图书馆',
    'exam_stress': '图书馆',
    'code_review': '实验室',
    'internship_offer': '教室',
    'project_failure': '实验室',
    'creative_burst': '教室',
    'study_group': '图书馆',
    'deadline_night': '宿舍',
    'skill_workshop': '教室',
    'cheating_temptation': '教室',
    'friend_support': '宿舍',
    'social_party': '商业街',
    'argument': '校园',
    'surprise_gift': '宿舍',
    'neighbor_visit': '宿舍',
    'group_project': '实验室',
    'betrayal': '校园',
    'invitation_dinner': '校外',
    'public_speech': '教室',
    'mentorship': '教室',
    'rainy_day': '宿舍',
    'wind_storm': '校园',
    'power_outage': '宿舍',
    'lost_item': '校园',
    'good_weather': '校园',
    'self_reflection': '宿舍',
    'achievement_unlock': '实验室',
    'coincidence': '商业街',
    'life_lesson': '公园',
    'nature_walk': '公园',
    'learn_new_hobby': '宿舍',
}

# 不存在的属性名 → 替换为有效属性
INVALID_ATTR_MAP = {
    'vitamin_d': 'health',
    'surprise': 'joy',
    'trust': 'happiness',
    'skills.legal_knowledge': 'skills.learning',
    'skills.medical_knowledge': 'skills.learning',
}

# 有效属性名白名单（用于校验）
VALID_ATTRS = {
    'health', 'energy', 'hunger', 'hygiene',
    'mood', 'stress', 'happiness', 'loneliness', 'confidence',
    'motivation', 'creativity', 'joy', 'anger', 'disappointment',
    'boredom', 'fulfillment',
    'skills.writing', 'skills.coding', 'skills.social', 'skills.learning',
    'skills.fitness', 'skills.painting', 'skills.debate',
    'skills.game_design', 'skills.teamwork',
    'goals.writer_progress', 'goals.coder_progress',
}


def fix_conditions(conditions, trigger_id='unknown'):
    """修复条件格式：attribute→attr, operator→op, not_cooldown→标准格式"""
    fixed = []
    for c in conditions:
        # not_cooldown 字段单独处理
        if 'not_cooldown' in c:
            raw = c.pop('not_cooldown', 24)
            # 处理三种情况: 数字、字符串、已存在的对象
            if isinstance(raw, dict):
                hours = raw.get('hours', 24)
            elif isinstance(raw, (int, float)):
                hours = int(raw)
            else:
                hours = raw  # 字符串
            fixed.append({"not_cooldown": trigger_id, "hours": hours if isinstance(hours, (int, float)) else 24})
            continue

        new_c = {}
        # attribute → attr
        attr = c.pop('attribute', c.pop('attr', None))
        if attr:
            # 替换无效属性
            new_c['attr'] = INVALID_ATTR_MAP.get(attr, attr)
        
        # operator → op
        op = c.pop('operator', c.pop('op', None))
        if op:
            new_c['op'] = op
        
        # value / min / max
        for k in ('value', 'min', 'max'):
            if k in c:
                new_c[k] = c[k]
        
        if new_c:
            fixed.append(new_c)
    return fixed


def fix_state_changes(changes):
    """修复 state_changes 中的无效属性名"""
    fixed = []
    for ch in changes:
        attr = ch.get('attribute', '')
        if attr in INVALID_ATTR_MAP:
            ch['attribute'] = INVALID_ATTR_MAP[attr]
        fixed.append(ch)
    return fixed


def main():
    with open(FILE, 'r', encoding='utf-8') as f:
        rules = json.load(f)

    print(f"处理前: {len(rules)} 条规则")

    for rule in rules:
        tid = rule.get('trigger_id', 'unknown')

        # 1. 加 location
        loc = LOCATION_MAP.get(tid, '校园')
        rule['location'] = loc

        # 2. 修复 conditions
        rule['conditions'] = fix_conditions(rule.get('conditions', []), tid)

        # 3. 修复 state_changes 中的无效属性
        rule['state_changes'] = fix_state_changes(rule.get('state_changes', []))

        # 4. 删除 not_cooldown 裸数字（已迁移到 fix_conditions 处理）
        # 5. 移除 cooldown（如果存在且不是标准格式）
        rule.pop('cooldown', None)

    # 写回
    with open(FILE, 'w', encoding='utf-8') as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)

    print(f"处理后: {len(rules)} 条规则")
    print("已添加 location 字段，修复条件/属性格式")
    
    # 打印前3条验证
    for r in rules[:3]:
        print(f"\n  [{r['trigger_id']}] loc={r['location']}")
        print(f"    条件: {r['conditions'][:2]}...")


if __name__ == '__main__':
    main()
