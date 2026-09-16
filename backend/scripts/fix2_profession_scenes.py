"""Rewrite _PROFESSION_SCENES and _get_relation_labels in api.py"""
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
api_path = os.path.join(script_dir, '..', 'routes', 'api.py')
api_path = os.path.normpath(api_path)

with open(api_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the section to replace
idx_start = content.find("_PROFESSION_SCENES = {")
search_from = content.find("def _get_relation_labels", idx_start)
idx_end = content.find("@api_bp.route", search_from)
# Walk back to blank line before the route
idx_end = content.rfind("\n\n", 0, idx_end) + 1

print(f"Replacing from char {idx_start} to {idx_end}")

new_section = u'''_PROFESSION_SCENES = {
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
'''

content = content[:idx_start] + new_section + content[idx_end:]
with open(api_path, 'w', encoding='utf-8') as f:
    f.write(content)
print("写入成功")
