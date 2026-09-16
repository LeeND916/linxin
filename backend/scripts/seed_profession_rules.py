"""职业规则种子数据：把常见职业写入 profession_rule 表（DB 驱动，不写死角色名）。

用法：
    cd <项目根目录>
    python backend/scripts/seed_profession_rules.py
"""
import json
import os
import sys

# 自引导：把项目根加入 sys.path（backend 为命名空间包，无 __init__.py）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app import create_app
from backend.models import ProfessionRule, db

# service_type=True 的专业服务型职业（职业场景内豁免冷淡）
SERVICE_PROFESSIONS = [
    ('psychologist', '心理咨询师', ['心理', '咨询', '医师', '咨询师', '疏导'],
     ['clinic', 'counseling_office', 'hospital', 'study_office', '门诊室', '咨询室', '医院', '心理科'],
     '病人', ['病人', '咨询者', '来访者']),
    ('doctor', '医生', ['医生', '医师', '外科', '内科', '临床', '科主任', '主治'],
     ['hospital', 'clinic', 'operating_room', 'study_office', '医院', '门诊', '手术室', '病房'],
     '病人', ['病人', '患者']),
    ('nurse', '护士', ['护士', '护理', '护师'],
     ['hospital', 'clinic', '病房', '护士站', '医院', '病房', '护士站'],
     '病人', ['病人', '患者']),
    ('lawyer', '律师', ['律师', '法务', '法学', '司法', '法官', '检察官', '法律顾问'],
     ['law_firm', 'court', 'client_room', '律所', '法院', '客户接待室'],
     '客户', ['客户', '委托人']),
    ('teacher', '老师', ['老师', '教师', '教授', '导师', '讲师', '教员'],
     ['classroom', 'teachers_office', 'lesson_prep', 'lecture_hall', '教室', '教师办公室', '备课室', '报告厅'],
     '学生', ['学生', '学员']),
    ('flight_attendant', '空乘', ['空姐', '空少', '乘务员', '乘务', '航空', '航班', '客舱'],
     ['cabin', 'airport', 'lounge', '机舱', '候机厅', '值机台'],
     '乘客', ['乘客', '旅客']),
    ('accountant', '会计', ['会计', '财务', '审计', '出纳', '税务'],
     ['finance_office', 'accounting_firm', '财务室', '事务所', '办税厅'],
     '客户', ['客户', '委托人']),
    ('tour_guide', '导游', ['导游', '领队', '地陪', '研学'],
     ['scenic', 'tour_bus', 'visitor_center', '景区', '旅游大巴', '游客中心'],
     '游客', ['游客', '旅客']),
    ('front_desk', '前台接待', ['前台', '接待', '迎宾', '礼宾', '大堂'],
     ['lobby', 'front_desk', 'reception', '前台', '大堂', '接待处'],
     '访客', ['访客', '客人']),
    ('customer_service', '客服', ['客服', '售后', '热线', '坐席', '工单'],
     ['service_center', 'workstation', '客服中心', '工作台'],
     '用户', ['用户', '客户']),
    ('bank_teller', '银行柜员', ['银行', '柜员', '理财经理', '信贷', '大堂经理'],
     ['bank', 'counter', 'hall', '银行', '柜台', '营业厅'],
     '客户', ['客户']),
    ('hairdresser', '理发/造型', ['理发', '发型', '美发', '造型师', '梳化'],
     ['salon', 'barber_shop', '理发店', '美发沙龙', '化妆间'],
     '顾客', ['顾客']),
    ('fitness_trainer', '健身教练', ['健身', '教练', '私教', '体能', '普拉提'],
     ['gym', 'training_room', '健身房', '操房', '训练区'],
     '会员', ['会员', '学员']),
    ('realtor', '房产中介', ['中介', '置业', '房产', '经纪', '销冠'],
     ['sales_office', 'store', '售楼处', '门店', '样板间'],
     '客户', ['客户']),
    ('vet', '兽医', ['兽医', '宠物医生', '宠物医师'],
     ['pet_hospital', 'clinic', '宠物医院', '诊所'],
     '宠主', ['宠主', '主人']),
    ('makeup_artist', '化妆师', ['化妆师', '妆造', '跟妆', '造型'],
     ['makeup_room', 'studio', '化妆间', '片场', '工作室'],
     '客户', ['客户']),
    ('police', '民警/警察', ['警察', '民警', '辅警', '警官', '片警'],
     ['police_station', 'patrol', '派出所', '警务室', '巡逻'],
     '市民', ['市民', '群众']),
    ('masseur', '按摩/推拿', ['按摩', '推拿', '理疗', '养生', '采耳'],
     ['massage_shop', 'spa', '按摩店', '养生馆'],
     '顾客', ['顾客']),
]

# service_type=False 的非服务型职业（私人关系，不豁免）
NON_SERVICE_PROFESSIONS = [
    ('painter', '画家', ['画家', '美术', '画师', '油画', '插画'], '朋友'),
    ('writer', '作家', ['作家', '文学', '写作', '文创', '诗人'], '朋友'),
    ('designer', '游戏设计师', ['游戏', '设计', '程序', '制作人', '策划'], '朋友'),
    ('ai', 'AI', ['AI', '人工智能', '智能体', '机器人'], '伙伴'),
    ('student', '学生', ['学生', '大学', '研究生', '在读', '学霸'], '朋友'),
    ('singer', '歌手', ['歌手', '演唱', '唱作', '声优'], '朋友'),
    ('actor', '演员/艺人', ['演员', '艺人', '明星', '爱豆', '模特'], '朋友'),
    ('host', '主播/主持', ['主播', '主持', 'up主', '博主'], '朋友'),
    ('civil_servant', '公务员/体制', ['公务员', '处长', '科员', '体制', '干部', '书记'], '朋友'),
    ('businessman', '商人/老板', ['老板', '总裁', '总监', '商人', 'CEO', '创始人'], '朋友'),
    ('journalist', '记者', ['记者', '新闻', '采编', '编导'], '朋友'),
    ('photographer', '摄影师', ['摄影', '摄像', '影像', '后期'], '朋友'),
    ('chef', '厨师', ['厨师', '主厨', '料理', '烘焙'], '朋友'),
    ('other', '其他', [], '朋友'),
]


def seed():
    count = 0
    for ptype, label, kws, scenes, rel, svc in SERVICE_PROFESSIONS:
        existing = db.session.get(ProfessionRule, ptype)
        if existing:
            existing.label = label
            existing.service_type = True
            existing.match_keywords = json.dumps(kws, ensure_ascii=False)
            existing.scenes = json.dumps(scenes, ensure_ascii=False)
            existing.relation_label = rel
            existing.service_identities = json.dumps(svc, ensure_ascii=False)
        else:
            db.session.add(ProfessionRule(
                profession_type=ptype, label=label, service_type=True,
                match_keywords=json.dumps(kws, ensure_ascii=False),
                scenes=json.dumps(scenes, ensure_ascii=False),
                relation_label=rel,
                service_identities=json.dumps(svc, ensure_ascii=False),
            ))
        count += 1
    for ptype, label, kws, rel in NON_SERVICE_PROFESSIONS:
        existing = db.session.get(ProfessionRule, ptype)
        if existing:
            existing.label = label
            existing.service_type = False
            existing.match_keywords = json.dumps(kws, ensure_ascii=False)
            existing.scenes = json.dumps([], ensure_ascii=False)
            existing.relation_label = rel
            existing.service_identities = json.dumps([], ensure_ascii=False)
        else:
            db.session.add(ProfessionRule(
                profession_type=ptype, label=label, service_type=False,
                match_keywords=json.dumps(kws, ensure_ascii=False),
                scenes=json.dumps([], ensure_ascii=False),
                relation_label=rel,
                service_identities=json.dumps([], ensure_ascii=False),
            ))
        count += 1
    db.session.commit()
    print(f"[Seed] profession_rule 已写入/更新 {count} 行")


if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        seed()
