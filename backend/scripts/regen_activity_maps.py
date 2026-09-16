# -*- coding: utf-8 -*-
"""按角色属性重新生成 character_activity_map（活动地图）。

设计原则：
- 每位女主 10-12 个主题地点，并统一追加一个「家里」地点（共 11-13 个），
  家里含 休息/洗澡/吃饭/和家人聊天 4 个居家活动。
- custom_activities 严格遵循指定 JSON 格式：
  {"duration_minutes", "effects", "energy_cost", "id", "name", "comfyui_pose"}
- effects 使用角色自身 skill_display 的专业技能键 + 通用心理/物理属性键
  （mood/stress/health/energy/creativity/confidence/motivation/happiness 等），
  确保 perform_activity -> apply_attr_changes 真正落地属性变化。
- effects 内必须含 "energy"（真实体力增量，这是游戏机制实际读取的字段）；
  energy_cost 为描述性字段（负=消耗强度），与用户给定示例格式一致。
- 删除每位女主原有 CAM 行后整体重建。

用法（在项目根目录）：
  python backend/scripts/regen_activity_maps.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app import create_app, db
from backend.models import CharacterActivityMap, Character
from datetime import datetime, timezone, timedelta

def bj_now():
    # 北京时间 UTC+8
    return datetime.now(timezone(timedelta(hours=8)))


def A(aid, name, effects, energy_cost, dur, pose):
    """构造单个活动字典（严格遵循指定格式）。"""
    # 保证 effects 含 energy 键，机制才生效
    eff = dict(effects)
    return {
        "id": aid,
        "name": name,
        "effects": eff,
        "energy_cost": energy_cost,
        "duration_minutes": dur,
        "comfyui_pose": pose,
    }


# ============================== 角色活动地图数据 ==============================
# 每位女主：[(venue_id, venue_name, [活动...]), ...]
ACTIVITY_MAPS = {

    # ---------------- 苏晴：刑事律师（法学） ----------------
    "苏晴": [
        ("apartment", "江景公寓", [
            A("morning_brief", "晨间梳案", {"legal_knowledge": 2, "motivation": 3, "energy": -4, "mood": 2}, -5, 30,
              "倚靠落地窗边的小桌翻阅卷宗，晨光落在侧脸，神情清醒专注"),
            A("balcony_alone", "阳台独处", {"stress": -4, "mood": 3, "energy": 8}, -3, 25,
              "双手轻搭栏杆远眺江面，肩背松弛，神情宁静疏离"),
        ]),
        ("law_firm", "律师事务所", [
            A("case_file_study", "卷宗精读", {"legal_knowledge": 4, "case_analysis": 3, "stress": 2, "energy": -8}, -10, 60,
              "伏案于成堆案卷前，指尖划过页脚批注，神情锐利沉着"),
            A("case_discuss", "案情研讨", {"case_analysis": 3, "debate": 2, "confidence": 2, "energy": -6}, -8, 45,
              "立于白板前圈画时间线，眉峰微挑，语气冷静笃定"),
            A("contract_review", "合同把关", {"legal_knowledge": 3, "negotiation": 2, "motivation": 2, "energy": -7}, -9, 50,
              "执笔逐条修订条款，目光如炬，神情一丝不苟"),
        ]),
        ("client_room", "客户接待室", [
            A("first_interview", "初次面谈", {"negotiation": 3, "social": 2, "confidence": 2, "energy": -5}, -7, 40,
              "端坐沙发对面倾身倾听，双手交叠，神情专业而克制"),
            A("comfort_client", "安抚当事人", {"social": 3, "stress": -2, "mood": 1, "energy": -4}, -4, 30,
              "递过温水轻声安抚，眼神温润，姿态柔和下来"),
        ]),
        ("court", "法院", [
            A("trial_defense", "庭审辩护", {"debate": 4, "case_analysis": 3, "confidence": 3, "stress": 3, "energy": -10}, -13, 75,
              "立于法庭中央字句铿锵，昂首直视法官，气场凌厉"),
            A("evidence_sort", "证据梳理", {"case_analysis": 3, "legal_knowledge": 2, "motivation": 2, "energy": -6}, -8, 45,
              "俯身核对物证编号，指尖轻点材料，神情缜密"),
        ]),
        ("legal_library", "法律图书馆", [
            A("precedent_search", "判例检索", {"legal_knowledge": 3, "case_analysis": 2, "creativity": 1, "energy": -6}, -8, 50,
              "侧坐书架间敲键检索，偶尔抬眼凝思，神情沉静"),
            A("treatise_read", "专著研读", {"legal_knowledge": 4, "motivation": 2, "mood": 1, "energy": -5}, -7, 55,
              "执书抵于膝上细读，指尖摩挲书页，神情投入"),
        ]),
        ("debate_club", "辩论俱乐部", [
            A("moot_court", "模拟法庭辩论", {"debate": 4, "confidence": 3, "stress": 2, "energy": -8}, -10, 60,
              "立于模拟席挥臂陈词，语速凌厉，目光灼灼"),
            A("improv_rebut", "即兴反驳", {"debate": 3, "creativity": 2, "motivation": 2, "energy": -6}, -8, 35,
              "微微前倾捕捉破绽，唇角勾起，神情机敏"),
        ]),
        ("gym", "律所健身房", [
            A("strength_train", "力量训练", {"health": 3, "motivation": 2, "stress": -3, "energy": -10}, -12, 45,
              "双手握杠稳稳推举，肌肉绷紧，神情坚毅"),
            A("relief_run", "减压慢跑", {"health": 2, "stress": -5, "mood": 3, "energy": -8}, -10, 40,
              "于跑步机上匀速奔跑，发丝微湿，神情舒展"),
        ]),
        ("riverside", "江边步道", [
            A("night_run", "夜跑解压", {"health": 2, "stress": -6, "energy": -9}, -11, 40,
              "沿江慢跑衣角轻扬，夜风拂面，神情畅快"),
            A("riverside_walk", "江风散步", {"mood": 3, "stress": -4, "energy": 6}, -3, 30,
              "双手插兜缓步而行，任江风扬发，神情松弛"),
        ]),
        ("wine_bar", "红酒吧", [
            A("wine_tasting", "品鉴小酌", {"mood": 3, "stress": -3, "social": 1, "energy": 5}, -4, 35,
              "执杯轻晃凝视酒色，唇角微扬，姿态从容"),
            A("peer_chat", "同行闲聊", {"social": 3, "negotiation": 1, "happiness": 2, "energy": -3}, -4, 30,
              "靠坐高脚凳低声交谈，举杯轻碰，神情松弛"),
        ]),
        ("cafe", "静谧咖啡馆", [
            A("brief_writing", "文案撰写", {"legal_knowledge": 2, "motivation": 3, "creativity": 2, "energy": -6}, -8, 50,
              "就着笔记本敲击措辞，咖啡氤氲，神情专注"),
            A("mind_sort", "思路整理", {"creativity": 3, "motivation": 2, "mood": 2, "energy": -4}, -5, 30,
              "闭眼以笔尖点唇凝神，姿态放松，若有所思"),
        ]),
        ("rooftop", "律所天台", [
            A("deep_breath", "深呼吸放空", {"stress": -5, "mood": 3, "energy": 7}, -2, 20,
              "双臂舒展仰面深呼吸，天光落肩，神情释然"),
            A("gaze_think", "远眺沉思", {"creativity": 2, "motivation": 2, "stress": -2, "energy": -2}, -3, 25,
              "手扶栏远眺楼宇，微风撩发，神色沉静"),
        ]),
        ("seminar_hall", "法学讲座厅", [
            A("front_lecture", "前沿讲座", {"legal_knowledge": 3, "motivation": 2, "creativity": 1, "energy": -5}, -7, 60,
              "端坐前排执笔速记，目光追随讲台，神情求索"),
            A("academic_exchange", "学术交流", {"social": 2, "debate": 2, "confidence": 2, "energy": -4}, -5, 35,
              "与同行低声探讨，手势轻点，神情热络"),
        ]),
    ],

    # ---------------- 林小鹿：美术学院学生（油画） ----------------
    "林小鹿": [
        ("dormitory", "宿舍", [
            A("sketch_diary", "随手速写", {"painting": 2, "creativity": 3, "mood": 2, "energy": -4}, -5, 30,
              "盘腿坐地毯上涂画速写本，笔尖飞舞，神情雀跃"),
            A("cozy_rest", "窝床听歌", {"mood": 3, "stress": -4, "energy": 8}, -3, 25,
              "裹着毯子戴耳机晃腿，哼着小调，神情慵懒甜美"),
        ]),
        ("studio", "画室", [
            A("oil_paint", "油画创作", {"painting": 4, "creativity": 3, "motivation": 3, "energy": -8}, -10, 75,
              "立于画架前大片铺色，颜料沾颊，眼神发亮"),
            A("still_life", "静物写生", {"painting": 3, "observation": 3, "creativity": 2, "energy": -6}, -8, 55,
              "侧身凝视静物细细描摹，呼吸放轻，神情沉醉"),
            A("palette_mix", "调色实验", {"art_theory": 3, "creativity": 2, "mood": 2, "energy": -5}, -6, 35,
              "俯身于调色盘试色，指尖沾染斑斓，神情好奇"),
        ]),
        ("gallery", "画廊", [
            A("exhibit_prep", "布展准备", {"art_theory": 3, "creativity": 2, "confidence": 2, "energy": -7}, -9, 50,
              "踮脚挂画校准高度，哼着歌比划，神情兴奋"),
            A("curate_view", "观展揣摩", {"observation": 3, "creativity": 3, "motivation": 2, "energy": -4}, -5, 40,
              "驻足名画前歪头端详，指尖虚点画面，神情入迷"),
        ]),
        ("art_school", "美术学院", [
            A("life_drawing", "人体素描", {"painting": 3, "observation": 3, "creativity": 2, "energy": -6}, -8, 60,
              "执炭笔快速捕捉轮廓，目光专注，神情认真"),
            A("critique_class", "作品互评", {"art_theory": 2, "social": 2, "confidence": 2, "energy": -4}, -5, 40,
              "举画向大家解释构思，眉飞色舞，手势活泼"),
        ]),
        ("dessert_shop", "甜品店", [
            A("sweet_sketch", "甜品速写", {"painting": 2, "creativity": 3, "mood": 3, "energy": -3}, -4, 25,
              "托腮对着蛋糕画小稿，嘴角沾奶油，神情甜笑"),
            A("treat_self", "犒劳自己", {"mood": 4, "happiness": 3, "stress": -3, "energy": 6}, -2, 20,
              "舀起一勺甜品眯眼品尝，双腿轻晃，神情满足"),
        ]),
        ("photo_corner", "街角摄影", [
            A("street_photo", "街头抓拍", {"observation": 3, "creativity": 3, "mood": 2, "energy": -5}, -7, 35,
              "举相机蹲低抓拍光影，屏息凝神，神情灵动"),
            A("self_shoot", "随手自拍", {"mood": 3, "social": 2, "happiness": 2, "energy": -2}, -3, 15,
              "手机举高歪头比耶，阳光落脸，笑得灿烂"),
        ]),
        ("journal_studio", "手账工作室", [
            A("journal_make", "手账拼贴", {"creativity": 3, "observation": 2, "mood": 3, "energy": -4}, -5, 40,
              "伏桌剪贴贴纸胶带，碎发垂落，神情专注可爱"),
            A("sticker_plan", "规划排版", {"art_theory": 2, "creativity": 2, "motivation": 2, "energy": -3}, -4, 30,
              "以尺比划版面轻笑，笔尖点唇，神情雀跃"),
        ]),
        ("roof_art", "天台写生台", [
            A("roof_paint", "天台写生", {"painting": 3, "creativity": 3, "mood": 3, "energy": -6}, -8, 55,
              "迎风立画架描绘远山，裙角轻扬，神情畅快"),
            A("cloud_gaze", "看云发呆", {"mood": 3, "stress": -4, "energy": 7}, -2, 20,
              "趴栏托腮望云，双腿轻荡，神情放空甜软"),
        ]),
        ("lawn", "校园草坪", [
            A("plein_air", "草地写生", {"painting": 3, "creativity": 2, "mood": 3, "energy": -5}, -7, 45,
              "坐野餐垫上画花草，阳光洒肩，神情惬意"),
            A("frisbee_fun", "飞盘嬉戏", {"health": 2, "social": 3, "happiness": 3, "energy": -8}, -10, 30,
              "奔跑接飞盘笑出声，马尾飞扬，活力四射"),
        ]),
        ("flea_market", "旧物市集", [
            A("curio_hunt", "淘旧玩意", {"observation": 3, "creativity": 2, "mood": 2, "energy": -4}, -6, 35,
              "蹲地翻找老物件眼睛发亮，轻声惊呼，神情兴奋"),
            A("bargain_chat", "摊主闲聊", {"social": 3, "happiness": 2, "stress": -2, "energy": -3}, -4, 25,
              "和摊主比划砍价笑闹，手舞足蹈，气氛热络"),
        ]),
        ("music_fest", "音乐节现场", [
            A("fest_dance", "随乐蹦跳", {"happiness": 4, "mood": 4, "stress": -5, "energy": -9}, -11, 40,
              "随鼓点高举双手蹦跳，发丝汗水飞扬，笑得灿烂"),
            A("live_sketch", "现场速写", {"painting": 2, "creativity": 3, "observation": 2, "energy": -5}, -7, 35,
              "就着舞台光画剪影，神情沉醉，笔走轻快"),
        ]),
        ("bestie_flat", "闺蜜公寓", [
            A("paint_together", "闺蜜共画", {"painting": 2, "social": 3, "creativity": 2, "energy": -4}, -5, 40,
              "并肩趴地画同一幅画，碰肩嬉笑，神情亲昵"),
            A("pillow_talk", "深夜卧谈", {"social": 3, "happiness": 3, "stress": -4, "energy": 6}, -2, 30,
              "裹被窝咬耳私语，眼睛弯成月牙，神情甜蜜"),
        ]),
    ],

    # ---------------- 晓月：女大学生（计算机科学 / 写作） ----------------
    "晓月": [
        ("dormitory", "宿舍", [
            A("code_night", "夜里敲码", {"coding": 3, "learning": 2, "creativity": 2, "energy": -7}, -9, 55,
              "盘腿对笔记本改 bug，屏幕映脸，神情俏皮专注"),
            A("cat_cuddle", "撸猫充电", {"mood": 4, "stress": -4, "happiness": 3, "energy": 8}, -2, 20,
              "抱猫蹭脸眯眼傻笑，猫尾轻扫，神情软糯"),
        ]),
        ("library", "图书馆", [
            A("textbook_study", "啃专业书", {"learning": 4, "coding": 2, "motivation": 2, "energy": -7}, -9, 60,
              "伏案推演算法，笔尖飞快，神情认真"),
            A("quiet_read", "闲读小说", {"writing": 2, "creativity": 3, "mood": 3, "energy": -4}, -5, 40,
              "托腮翻书页轻笑，眼神柔软，沉浸故事"),
        ]),
        ("cs_lab", "计算机实验室", [
            A("lab_experiment", "实验调试", {"coding": 4, "learning": 3, "motivation": 2, "energy": -8}, -10, 65,
              "紧盯终端跑程序，手指翻飞，神情专注"),
            A("pair_program", "结对编程", {"coding": 3, "social": 2, "creativity": 2, "energy": -6}, -8, 45,
              "与同伴凑屏讨论比划，相视一笑，气氛轻快"),
        ]),
        ("anime_club", "动漫社活动室", [
            A("cos_make", "道具制作", {"creativity": 3, "coding": 1, "mood": 3, "energy": -6}, -8, 50,
              "低头粘道具哼着 OP，指尖灵巧，神情雀跃"),
            A("voice_dub", "即兴配音", {"creativity": 2, "social": 3, "happiness": 2, "energy": -4}, -5, 30,
              "举脚本夸张念台词，表情生动，笑得前仰"),
        ]),
        ("guitar_corner", "天台吉他角", [
            A("guitar_practice", "练吉他", {"creativity": 3, "mood": 3, "stress": -3, "energy": -5}, -7, 40,
              "抱吉他轻拨和弦低唱，晚风撩发，神情温柔"),
            A("song_write", "写歌消遣", {"writing": 2, "creativity": 3, "mood": 2, "energy": -4}, -5, 35,
              "边弹边在谱上写词，唇角微勾，若有所思"),
        ]),
        ("cat_cafe", "猫咖", [
            A("cafe_code", "带猫写码", {"coding": 2, "mood": 3, "stress": -3, "energy": -5}, -7, 45,
              "猫卧键盘旁敲代码，伸手挠猫，神情惬意"),
            A("cat_play", "逗猫放松", {"mood": 4, "happiness": 3, "stress": -4, "energy": 6}, -2, 20,
              "拿逗棒追猫转圈，笑出小虎牙，活力满满"),
        ]),
        ("campus_cafe", "校园咖啡馆", [
            A("essay_write", "灵感写作", {"writing": 4, "creativity": 3, "motivation": 2, "energy": -6}, -8, 55,
              "就着拿铁敲小说段落，眼神发亮，唇角微扬"),
            A("idea_brain", "脑暴大纲", {"writing": 3, "creativity": 3, "learning": 1, "energy": -5}, -6, 35,
              "在餐巾上画故事线，笔尖点唇，神情机灵"),
        ]),
        ("lit_club", "文学社", [
            A("reading_share", "读书分享", {"writing": 2, "social": 3, "confidence": 2, "energy": -4}, -5, 40,
              "站前分享书评手势轻扬，语调生动，眼神清亮"),
            A("story_workshop", "故事工坊", {"writing": 3, "creativity": 3, "motivation": 2, "energy": -5}, -6, 45,
              "围坐改彼此稿子笑闹点评，神情投入"),
        ]),
        ("track", "操场跑道", [
            A("jog_lap", "夜跑几圈", {"fitness": 3, "health": 2, "stress": -5, "energy": -8}, -10, 35,
              "匀速绕场奔跑，马尾轻扬，呼吸均匀神情畅快"),
            A("stretch_relax", "拉伸放松", {"health": 2, "mood": 3, "stress": -3, "energy": 7}, -3, 20,
              "坐地压腿舒展双臂，仰头望星，神情松弛"),
        ]),
        ("study_room", "考研自习室", [
            A("focus_review", "专注复习", {"learning": 4, "motivation": 3, "stress": 2, "energy": -7}, -9, 60,
              "埋头刷题库标重点，神情坚毅，偶尔蹙眉"),
            A("peer_encourage", "同伴打气", {"social": 2, "confidence": 2, "motivation": 3, "energy": -3}, -4, 20,
              "与邻座比加油手势相视一笑，神情明媚"),
        ]),
        ("lake_side", "校园湖畔", [
            A("lakeside_think", "湖畔沉思", {"creativity": 3, "writing": 2, "mood": 3, "energy": -3}, -4, 30,
              "坐石阶望湖出神，笔尖点唇，神情恬静"),
            A("feed_koi", "喂锦鲤发呆", {"mood": 3, "stress": -4, "happiness": 2, "energy": 6}, -2, 15,
              "蹲岸撒饵看鱼挤闹，托腮偷笑，神情软萌"),
        ]),
        ("online_space", "线上写作间", [
            A("serial_post", "连载更新", {"writing": 4, "motivation": 3, "creativity": 2, "energy": -6}, -8, 50,
              "敲字追更读者留言偷乐，眼神发亮，神情得意"),
            A("fan_reply", "回复读者", {"social": 3, "happiness": 3, "confidence": 2, "energy": -3}, -4, 25,
              "笑着敲回复比心，腮帮微红，神情甜软"),
        ]),
    ],

    # ---------------- 叶知秋：医生（心血管内科） ----------------
    "叶知秋": [
        ("apartment", "温馨公寓", [
            A("evening_read", "夜读期刊", {"medical_knowledge": 3, "motivation": 2, "mood": 2, "energy": -5}, -7, 45,
              "靠沙发展读论文，暖灯落肩，神情沉静"),
            A("tea_unwind", "泡茶解乏", {"stress": -4, "mood": 3, "energy": 7}, -3, 20,
              "执壶沏茶轻啜，蒸汽袅袅，神情舒展温和"),
        ]),
        ("operating_room", "手术室", [
            A("surgery_lead", "主刀手术", {"surgery": 5, "diagnosis": 3, "stress_resistance": 3, "stress": 3, "energy": -12}, -15, 90,
              "凝立无影灯下稳持器械，额沁薄汗，神情冷峻专注"),
            A("assist_surgery", "台上配合", {"surgery": 3, "diagnosis": 2, "stress_resistance": 2, "energy": -9}, -12, 70,
              "利落递器械目光紧随术野，呼吸平稳，神情缜密"),
        ]),
        ("clinic", "门诊室", [
            A("outpatient_diag", "门诊问诊", {"diagnosis": 4, "empathy": 3, "medical_knowledge": 2, "energy": -7}, -9, 55,
              "倾身听诊细问病史，语气温和，目光关切"),
            A("report_explain", "解读报告", {"diagnosis": 3, "medical_knowledge": 2, "confidence": 2, "energy": -5}, -7, 40,
              "指着片子和家属耐心讲解，手势轻缓，神情稳妥"),
        ]),
        ("hospital", "医院", [
            A("rounds_visit", "查房巡视", {"diagnosis": 3, "empathy": 2, "medical_knowledge": 2, "energy": -6}, -8, 45,
              "推车查房俯身问询，轻拍患者肩，神情可靠"),
            A("emergency_resp", "急诊响应", {"stress_resistance": 4, "diagnosis": 3, "surgery": 1, "stress": 2, "energy": -10}, -13, 50,
              "快步推车进抢救室，口令清晰，神情紧绷高效"),
        ]),
        ("med_library", "医学图书馆", [
            A("case_review", "病例复盘", {"medical_knowledge": 4, "diagnosis": 3, "motivation": 2, "energy": -6}, -8, 55,
              "伏案对照影像细究，指尖点页，神情求索"),
            A("guideline_study", "指南研读", {"medical_knowledge": 3, "diagnosis": 2, "creativity": 1, "energy": -5}, -7, 50,
              "执最新指南勾画重点，神情沉稳专注"),
        ]),
        ("doc_gym", "医院健身房", [
            A("doctor_run", "值班后慢跑", {"health": 3, "stress": -5, "energy": -8}, -10, 40,
              "匀速奔跑汗落额角，神情舒朗，步伐稳健"),
            A("stretch_relax", "拉伸舒缓", {"health": 2, "stress": -4, "mood": 2, "energy": 6}, -3, 20,
              "靠墙压腿舒展肩颈，闭眼吐纳，神情松弛"),
        ]),
        ("tea_room", "茶室", [
            A("tea_brew", "煮茶静心", {"stress": -5, "mood": 3, "energy": 7}, -3, 25,
              "执壶慢沏看叶舒展，神态安宁，肩背松垂"),
            A("silent_rest", "闭目养神", {"stress": -4, "mood": 2, "energy": 8}, -2, 20,
              "靠椅闭目指尖轻搭膝，呼吸匀长，神情淡然"),
        ]),
        ("greenway", "江边绿道", [
            A("green_jog", "绿道慢跑", {"health": 2, "stress": -6, "energy": -8}, -10, 40,
              "沿树荫慢跑衣角轻扬，神情畅快，步履轻快"),
            A("riverside_walk", "江畔散步", {"mood": 3, "stress": -4, "energy": 6}, -3, 30,
              "双手插兜缓行听风，侧脸平和，神情舒展"),
        ]),
        ("study_office", "医生办公室", [
            A("paper_write", "撰写论文", {"medical_knowledge": 3, "creativity": 2, "motivation": 2, "energy": -6}, -8, 50,
              "就电脑整理数据作图，神情专注，偶尔推镜"),
            A("mentor_talk", "带教后辈", {"empathy": 3, "confidence": 2, "energy": -4}, -5, 35,
              "执片向后辈讲解手势温和，眼含笑意，神情可靠"),
        ]),
        ("duty_room", "值班室", [
            A("night_chart", "夜班病历", {"diagnosis": 3, "medical_knowledge": 2, "stress_resistance": 2, "energy": -7}, -9, 50,
              "灯下补写病历揉松肩颈，神情沉静，目光平稳"),
            A("quiet_vigil", "静守病房", {"empathy": 3, "stress_resistance": 2, "mood": 1, "energy": -4}, -6, 40,
              "隔窗轻看监护仪踱步，神情温柔，步态轻缓"),
        ]),
        ("heal_garden", "康复花园", [
            A("patient_walk", "陪患散步", {"empathy": 3, "confidence": 2, "happiness": 2, "energy": -4}, -5, 35,
              "搀扶患者缓行花径，轻声叮咛，神情暖煦"),
            A("garden_breath", "花园吐纳", {"stress": -5, "mood": 3, "energy": 7}, -3, 20,
              "立于花丛间深呼吸展臂，神色安宁，肩背舒展"),
        ]),
        ("academic_hall", "学术会议室", [
            A("case_conference", "病例讨论", {"diagnosis": 3, "medical_knowledge": 3, "confidence": 2, "energy": -5}, -7, 45,
              "站投影前陈述方案手势沉稳，语速适中，神情笃定"),
            A("lecture_share", "学术分享", {"medical_knowledge": 3, "confidence": 2, "motivation": 2, "energy": -4}, -6, 40,
              "执麦讲最新进展，偶尔冷幽默，神情从容"),
        ]),
    ],

    # ---------------- 沈念：文学系研究生（中国现当代文学） ----------------
    "沈念": [
        ("dormitory", "宿舍", [
            A("night_write", "深夜写作", {"writing": 4, "creativity": 3, "motivation": 2, "energy": -6}, -8, 55,
              "伏桌就台灯敲字，神情恬静，指尖轻颤"),
            A("cat_nap", "撸猫小憩", {"mood": 3, "stress": -4, "happiness": 2, "energy": 7}, -2, 20,
              "抱猫埋脸轻蹭，猫呼噜作响，神情软糯安宁"),
        ]),
        ("bookstore", "旧书店", [
            A("rare_hunt", "淘绝版书", {"literary_analysis": 3, "observation": 2, "creativity": 2, "energy": -4}, -6, 40,
              "踮脚翻上层旧书眼亮，轻抚书脊，神情珍重"),
            A("slow_read", "驻足品读", {"writing": 2, "creativity": 3, "mood": 2, "energy": -3}, -4, 35,
              "倚架翻几页低声吟，唇角微弯，神情沉醉"),
        ]),
        ("lit_salon", "文学沙龙", [
            A("reading_aloud", "朗读分享", {"writing": 2, "confidence": 2, "social": 2, "energy": -4}, -5, 30,
              "捧书轻声诵读，耳尖微红，神情羞怯认真"),
            A("idea_exchange", "观点交换", {"literary_analysis": 3, "social": 2, "creativity": 2, "energy": -4}, -5, 40,
              "低头绞指轻声接话，目光闪烁，语气轻柔"),
        ]),
        ("archive_lib", "图书馆古籍部", [
            A("manuscript_study", "手稿研读", {"literary_analysis": 4, "writing": 2, "motivation": 2, "energy": -6}, -8, 55,
              "戴手套翻脆黄纸页，神情肃然，呼吸放轻"),
            A("note_card", "卡片摘抄", {"literary_analysis": 3, "observation": 2, "creativity": 2, "energy": -5}, -6, 45,
              "伏案抄录金句眉目专注，笔尖轻停，若有所思"),
        ]),
        ("folk_house", "民谣Livehouse", [
            A("folk_listen", "听民谣落泪", {"mood": 3, "creativity": 2, "stress": -3, "energy": -3}, -4, 35,
              "抱膝坐暗处听歌，眼眸微湿，神情柔软"),
            A("soft_hum", "轻声跟唱", {"creativity": 2, "happiness": 2, "mood": 2, "energy": -3}, -4, 25,
              "随旋律轻晃哼唱，指尖点膝，神情恬淡"),
        ]),
        ("campus_cafe", "校园咖啡馆", [
            A("chapter_draft", "章节起稿", {"writing": 4, "creativity": 3, "motivation": 2, "energy": -6}, -8, 55,
              "就咖啡敲段落蹙眉又展颜，神情投入"),
            A("mind_wander", "发呆放空", {"mood": 3, "stress": -4, "energy": 6}, -2, 20,
              "托腮望窗外出神，热气绕杯，神情安宁"),
        ]),
        ("bench", "校园长椅", [
            A("bench_observe", "坐看行人", {"observation": 3, "creativity": 2, "mood": 2, "energy": -2}, -3, 25,
              "抱膝坐椅看人来人往，目光清浅，神情闲静"),
            A("snack_rest", "啃面包小憩", {"mood": 3, "happiness": 2, "stress": -3, "energy": 6}, -2, 15,
              "小口咬面包眯眼晒暖，腮帮微动，神情慵懒"),
        ]),
        ("cat_cafe", "猫咪咖啡馆", [
            A("cat_write", "伴猫写作", {"writing": 3, "creativity": 2, "mood": 3, "energy": -5}, -7, 50,
              "猫卧稿边敲字偷笑，神情甜软，肩背松垂"),
            A("cat_cuddle", "抱猫解压", {"mood": 4, "stress": -5, "happiness": 3, "energy": 7}, -2, 20,
              "把脸埋进猫毛里蹭，眯眼笑，神情治愈"),
        ]),
        ("writing_nook", "写作角", [
            A("prose_craft", "散文雕琢", {"writing": 4, "creativity": 3, "literary_analysis": 2, "energy": -6}, -8, 55,
              "逐句推敲换词轻声念，神情专注，唇角微动"),
            A("diary_pour", "随笔倾吐", {"writing": 3, "creativity": 2, "mood": 3, "stress": -3, "energy": -4}, -5, 35,
              "就本子飞快写心事，笔迹渐乱，神情松释"),
        ]),
        ("book_fair", "旧书市集", [
            A("stall_browse", "逛摊寻宝", {"observation": 3, "literary_analysis": 2, "mood": 2, "energy": -4}, -5, 35,
              "蹲摊翻旧书眼睛发亮，轻声惊呼，神情雀跃"),
            A("trade_chat", "与摊主聊书", {"social": 2, "literary_analysis": 2, "happiness": 2, "energy": -3}, -4, 25,
              "和摊主低声聊版本，手比划，神情热络"),
        ]),
        ("poem_tea", "诗词茶座", [
            A("poem_recite", "品诗吟哦", {"literary_analysis": 3, "creativity": 2, "mood": 3, "energy": -3}, -4, 30,
              "执杯轻诵诗句，眼眸微垂，神情沉醉"),
            A("tea_sip", "煮茶静思", {"stress": -4, "mood": 3, "energy": 6}, -2, 20,
              "看茶叶沉浮轻啜，神态安宁，肩背松弛"),
        ]),
        ("lake_bench", "湖边长椅", [
            A("lake_observe", "临湖凝思", {"observation": 3, "creativity": 3, "writing": 2, "energy": -3}, -4, 30,
              "坐椅望水出神，笔尖点唇，神情恬静"),
            A("feed_fish", "喂鱼发呆", {"mood": 3, "stress": -4, "happiness": 2, "energy": 6}, -2, 15,
              "撒饵看鱼挤闹托腮偷笑，神情软萌"),
        ]),
    ],

    # ---------------- 顾云溪：游戏制作人（游戏设计） ----------------
    "顾云溪": [
        ("apartment", "公寓", [
            A("game_review", "深夜评测", {"game_design": 3, "coding": 2, "creativity": 2, "energy": -7}, -9, 55,
              "瘫沙发边玩边记槽点，神情犀利，偶尔翻白眼"),
            A("chill_rest", "瘫着回血", {"mood": 3, "stress": -4, "energy": 8}, -3, 25,
              "抱枕堆里刷手机咧嘴笑，神情松弛率真"),
        ]),
        ("game_company", "游戏公司", [
            A("design_doc", "撰写设计案", {"game_design": 4, "creativity": 3, "motivation": 2, "energy": -7}, -9, 60,
              "立于白板画系统图手指点划，神情亢奋笃定"),
            A("prototype_build", "搭建原型", {"coding": 4, "game_design": 2, "creativity": 2, "energy": -8}, -10, 65,
              "就双屏快速敲原型，眼神发亮，语速加快"),
            A("team_sync", "站会同步", {"project_management": 3, "teamwork": 3, "confidence": 2, "energy": -5}, -6, 30,
              "抱臂站圈布置任务直来直去，手势利落，神情利落"),
        ]),
        ("hackathon", "黑客松会场", [
            A("hack_sprint", "通宵冲刺", {"coding": 4, "creativity": 3, "motivation": 3, "energy": -11}, -14, 80,
              "红牛在手猛敲键盘，眼底血丝，神情亢奋"),
            A("pitch_demo", "登台演示", {"project_management": 3, "confidence": 3, "teamwork": 2, "energy": -6}, -8, 40,
              "执麦讲 Demo 手势夸张，毒舌自嘲，眼里有光"),
        ]),
        ("coworking", "联合办公空间", [
            A("focus_build", "专注开发", {"coding": 3, "game_design": 2, "creativity": 2, "energy": -7}, -9, 60,
              "戴降噪耳机敲代码微点头，神情沉浸"),
            A("peer_review", "互审代码", {"coding": 3, "teamwork": 2, "creativity": 1, "energy": -5}, -7, 40,
              "并屏指bug直言不讳，挑眉吐槽，气氛热络"),
        ]),
        ("review_room", "游戏评测室", [
            A("playtest", "试玩抓 bug", {"game_design": 3, "creativity": 3, "motivation": 2, "energy": -6}, -8, 50,
              "执手柄逐帧试玩蹙眉记问题，神情较真"),
            A("balance_tune", "数值调优", {"game_design": 4, "creativity": 2, "motivation": 2, "energy": -6}, -8, 45,
              "拉表调参数嘀咕权衡，神情专注，偶尔咧嘴"),
        ]),
        ("graffiti_studio", "涂鸦墙工作室", [
            A("concept_draw", "概念涂鸦", {"creativity": 4, "game_design": 2, "mood": 3, "energy": -6}, -8, 50,
              "持笔在墙喷绘草图，发丝沾漆，神情肆意"),
            A("mood_board", "情绪板拼贴", {"creativity": 3, "game_design": 2, "mood": 2, "energy": -4}, -5, 35,
              "剪贴参考图比划审美，挑眉点评，神情活泼"),
        ]),
        ("esports_arena", "电竞馆", [
            A("rank_climb", "冲分上分", {"coding": 1, "creativity": 2, "mood": 3, "energy": -8}, -10, 45,
              "猛敲键盘喊着走位，神情激昂，胜负分明"),
            A("watch_match", "观赛学习", {"game_design": 3, "creativity": 4, "energy": -4}, -5, 40,
              "抱臂看大屏分析镜头，频频点头，神情专注"),
        ]),
        ("rock_live", "摇滚Livehouse", [
            A("headbang", "甩头狂欢", {"happiness": 4, "mood": 4, "stress": -5, "energy": -9}, -11, 40,
              "随鼓点甩头蹦跳嘶吼，发乱汗飞，笑得畅快"),
            A("band_watch", "看乐队排练", {"creativity": 2, "mood": 3, "energy": -4}, -5, 35,
              "靠墙跟拍子点头，唇角带笑，神情松弛"),
        ]),
        ("roof_camp", "天台露营", [
            A("roof_chill", "天台躺平", {"mood": 3, "stress": -5, "energy": 8}, -3, 25,
              "躺折叠椅看星举罐轻晃，神情慵懒惬意"),
            A("star_talk", "和朋友闲扯", {"teamwork": 3, "happiness": 2, "creativity": 1, "energy": -3}, -4, 30,
              "并肩吹牛比划吐槽，咧嘴大笑，气氛热络"),
        ]),
        ("cafe", "咖啡馆", [
            A("doc_write", "写策划文档", {"game_design": 3, "creativity": 3, "energy": -6}, -8, 50,
              "就笔记本敲方案蹙眉又松展，神情投入"),
            A("brain_dump", "灵感速记", {"creativity": 4, "game_design": 2, "mood": 2, "energy": -4}, -5, 30,
              "在餐巾狂写点子笔走龙蛇，眼亮，神情兴奋"),
        ]),
        ("board_game", "桌游吧", [
            A("game_test", "测试自研桌游", {"game_design": 4, "teamwork": 2, "creativity": 2, "energy": -6}, -8, 55,
              "摆棋讲解规则手势利落，挑眉等反馈，神情期待"),
            A("fun_play", "和朋友开黑", {"teamwork": 3, "happiness": 3, "stress": -4, "energy": -5}, -6, 40,
              "拍桌大笑吐槽运气，神情率真，气氛热烈"),
        ]),
        ("dev_conf", "开发者大会", [
            A("tech_talk", "听技术分享", {"coding": 3, "game_design": 2, "creativity": 2, "energy": -5}, -7, 50,
              "坐前排速记架构图，频频点头，神情求索"),
            A("booth_chat", "展位交流", {"teamwork": 4, "creativity": 1, "energy": -4}, -5, 35,
              "和同行聊引擎比手势，毒舌点评，眼里有光"),
        ]),
    ],

    # ---------------- 沈疏筠：初中语文老师（汉语言文学教育） ----------------
    "沈疏筠": [
        ("teachers_office", "教师办公室", [
            A("lesson_plan", "备课批卷", {"teaching": 4, "literature": 2, "motivation": 2, "energy": -6}, -8, 50,
              "伏案红笔批作文眉间微凝，神情安静可靠"),
            A("peer_discuss", "同事研课", {"teaching": 3, "social": 2, "literature": 1, "energy": -4}, -5, 35,
              "轻声与同事聊教法手势温和，眼含笑意，神情熨帖"),
        ]),
        ("classroom", "教室", [
            A("literature_class", "语文讲课", {"teaching": 4, "literature": 3, "confidence": 2, "energy": -7}, -9, 45,
              "立于讲台朗声吟诵，目光扫过学生，神情温润从容"),
            A("blackboard_write", "板书示范", {"teaching": 3, "writing": 2, "literature": 2, "energy": -5}, -7, 35,
              "背身工整板书字迹清秀，肩背舒展，神情沉静"),
        ]),
        ("playground", "操场", [
            A("recess_watch", "课间看护", {"teaching": 2, "social": 2, "happiness": 2, "energy": -4}, -5, 30,
              "立操场边看学生嬉闹，唇角微弯，神情淡然温柔"),
            A("slow_walk", "慢步散心", {"mood": 3, "stress": -4, "energy": 6}, -3, 25,
              "双手交叠缓行看天，微风拂发，神情疏淡安宁"),
        ]),
        ("west_lake", "西湖畔", [
            A("lake_walk", "湖畔独步", {"mood": 3, "stress": -5, "creativity": 2, "energy": 6}, -3, 30,
              "沿堤缓行看荷听风，神情疏离恬静，步履轻"),
            A("verse_murmur", "临水吟诗", {"literature": 3, "creativity": 2, "mood": 2, "energy": -3}, -4, 25,
              "低声诵句望水出神，唇角微扬，神情沉醉"),
        ]),
        ("tea_house", "龙井茶馆", [
            A("tea_savor", "品龙井静心", {"stress": -5, "mood": 3, "energy": 7}, -3, 25,
              "执杯看茶叶沉浮轻啜，神态安宁，肩背松垂"),
            A("quiet_read", "茶馆闲读", {"literature": 3, "creativity": 2, "mood": 2, "energy": -4}, -5, 40,
              "就窗边翻书页低声吟，目光柔软，神情沉浸"),
        ]),
        ("bookstore_xf", "晓风书屋", [
            A("shelf_browse", "书架巡览", {"literature": 3, "learning": 2, "creativity": 2, "energy": -4}, -5, 35,
              "指尖拂过书脊轻取一本，眼亮，神情珍重"),
            A("passage_mark", "佳句摘抄", {"writing": 3, "literature": 2, "creativity": 2, "energy": -4}, -5, 40,
              "伏案抄录金句眉目专注，笔尖轻停，若有所思"),
        ]),
        ("museum_hz", "杭州博物馆", [
            A("relic_observe", "端详文物", {"learning": 3, "literature": 2, "creativity": 1, "energy": -4}, -5, 40,
              "近柜凝视展品微微颔首，神情沉静敬惜"),
            A("note_learn", "展签研读", {"learning": 3, "literature": 2, "motivation": 2, "energy": -4}, -5, 35,
              "就说明牌细读轻记，神情求索，步履轻缓"),
        ]),
        ("calligraphy_studio", "书法工作室", [
            A("brush_practice", "临帖习字", {"writing": 4, "creativity": 2, "stress": -3, "energy": -5}, -6, 45,
              "执毫悬腕写楷书，呼吸匀长，神情专注安宁"),
            A("ink_appreciate", "赏帖品韵", {"literature": 3, "creativity": 2, "mood": 2, "energy": -3}, -4, 30,
              "展卷端详笔意轻叹，眼含欣赏，神情恬淡"),
        ]),
        ("garden_balcony", "园艺阳台", [
            A("plant_tend", "侍弄花草", {"mood": 3, "stress": -4, "happiness": 2, "energy": -4}, -5, 30,
              "蹲身给绿植浇水轻笑，指尖沾泥，神情柔软"),
            A("morning_breathe", "晨间吐纳", {"stress": -4, "mood": 3, "energy": 7}, -2, 20,
              "立阳台展臂深呼吸，晨光落肩，神情疏淡舒展"),
        ]),
        ("tea_studio", "茶艺室", [
            A("tea_ceremony", "习茶修心", {"stress": -5, "mood": 3, "energy": 6}, -3, 30,
              "循礼温杯注汤动作舒缓，神态安宁，气韵沉静"),
            A("quiet_sit", "静坐品茗", {"mood": 3, "stress": -4, "energy": 6}, -2, 20,
              "捧盏闭目轻啜，呼吸匀长，神情淡然"),
        ]),
        ("lesson_prep", "语文备课室", [
            A("text_deep", "文本细读", {"literature": 4, "teaching": 2, "creativity": 2, "energy": -5}, -7, 45,
              "就课本圈画批注低声揣摩，神情沉静投入"),
            A("course_design", "课件设计", {"teaching": 3, "creativity": 2, "motivation": 2, "energy": -5}, -6, 40,
              "于电脑排课件比划构思，眼亮，神情温和"),
        ]),
        ("home_visit", "家访小路", [
            A("home_visit_talk", "家访倾谈", {"social": 3, "confidence": 2, "energy": -5}, -6, 40,
              "与家长轻声调侃学情手势温和，眼含关切，神情熨帖"),
            A("neighborhood_walk", "巷弄漫步", {"mood": 3, "creativity": 2, "stress": -3, "energy": 6}, -3, 25,
              "穿巷看市井缓行，神情疏淡，步履轻闲"),
        ]),
    ],

    # ---------------- 刘星苒：市委办综合处处长（公共管理） ----------------
    "刘星苒": [
        ("apartment", "江畔书房", [
            A("night_read", "夜读沉思", {"learning": 3, "policy_analysis": 2, "mood": 2, "energy": -5}, -7, 45,
              "靠书椅展读文件暖灯落肩，神情通透沉静"),
            A("tea_unwind", "煮茶松弛", {"stress": -4, "mood": 3, "energy": 7}, -3, 20,
              "执壶沏茶轻啜望江，蒸汽袅袅，神情从容"),
        ]),
        ("coffee_shop", "老外滩咖啡馆", [
            A("doc_draft", "起草文稿", {"document_writing": 4, "policy_analysis": 2, "creativity": 2, "energy": -6}, -8, 55,
              "就笔记本推敲措辞眉目清朗，神情专注"),
            A("think_stroll", "临窗凝思", {"creativity": 2, "mood": 3, "stress": -3, "energy": -3}, -4, 25,
              "托腮望江出神笔尖点唇，神情通透恬淡"),
        ]),
        ("east_park", "东部新城公园", [
            A("park_walk", "新城漫步", {"mood": 3, "stress": -5, "energy": 6}, -3, 30,
              "双手交叠缓行看楼影，神情从容舒展"),
            A("morning_taiji", "晨练太极", {"health": 3, "stress": -4, "energy": 5}, -4, 30,
              "于草坪起势慢推手，呼吸匀长，神情安宁"),
        ]),
        ("night_school", "宁波干部夜校", [
            A("policy_study", "夜校进修", {"policy_analysis": 4, "learning": 3, "motivation": 2, "energy": -6}, -8, 55,
              "端坐前排执笔速记，目光追随讲台，神情求索"),
            A("group_discuss", "小组研讨", {"coordination": 3, "social": 2, "public_speaking": 2, "energy": -5}, -6, 40,
              "引导大家发言手势温和，语调清晰，神情通透"),
        ]),
        ("municipal_office", "市委办公室", [
            A("doc_review", "审签文稿", {"document_writing": 4, "policy_analysis": 2, "confidence": 2, "energy": -6}, -8, 50,
              "执笔逐句修订批示沉稳，神情从容笃定"),
            A("task_coord", "统筹分办", {"coordination": 4, "learning": 2, "social": 2, "energy": -6}, -8, 45,
              "于白板排任务条理清晰，手势利落，神情明彻"),
        ]),
        ("policy_lab", "政策研究室", [
            A("data_analysis", "数据研判", {"policy_analysis": 4, "learning": 2, "creativity": 2, "energy": -7}, -9, 55,
              "对屏拉报表析趋势蹙眉又舒展，神情缜密"),
            A("report_write", "撰写专报", {"document_writing": 4, "policy_analysis": 3, "motivation": 2, "energy": -6}, -8, 60,
              "就电脑成文推敲措辞，神情专注，偶尔推镜"),
        ]),
        ("city_walk", "城市漫步道", [
            A("city_observe", "街巷体察", {"learning": 3, "policy_analysis": 2, "creativity": 1, "energy": -4}, -5, 35,
              "缓行看市井民生目光清透，神情关切温和"),
            A("bench_rest", "路椅小憩", {"mood": 3, "stress": -4, "energy": 6}, -3, 20,
              "坐椅望人流出神，微风拂发，神情松弛"),
        ]),
        ("tea_art_room", "茶艺室", [
            A("tea_practice", "习茶静心", {"stress": -5, "mood": 3, "energy": 6}, -3, 30,
              "循礼温杯注汤动作舒缓，气韵沉静，神态安宁"),
            A("verse_sip", "品茗吟句", {"learning": 2, "creativity": 2, "mood": 2, "energy": -3}, -4, 25,
              "捧盏轻吟偶有文艺慢板，神情恬淡从容"),
        ]),
        ("study_room", "家中书房", [
            A("classic_read", "读史明智", {"learning": 3, "document_writing": 2, "creativity": 1, "energy": -5}, -7, 50,
              "执史册细读轻叹，神情沉静通透，目光悠远"),
            A("essay_write", "随笔抒怀", {"document_writing": 3, "creativity": 3, "mood": 2, "energy": -5}, -6, 45,
              "就纸笔写生活感悟笔触轻缓，神情温润"),
        ]),
        ("conference_center", "会议中心", [
            A("meeting_host", "主持会议", {"public_speaking": 4, "coordination": 3, "confidence": 2, "energy": -7}, -9, 50,
              "执麦控场语速清晰手势沉稳，神情从容通透"),
            A("speech_deliver", "政策宣讲", {"public_speaking": 4, "policy_analysis": 2, "confidence": 2, "energy": -6}, -8, 40,
              "立于台前娓娓道来，眼含温度，神情恳切"),
        ]),
        ("community_survey", "社区调研点", [
            A("resident_talk", "走访群众", {"social": 3, "policy_analysis": 2, "confidence": 2, "energy": -5}, -6, 40,
              "蹲身与居民拉家常手势温和，眼含关切，神情熨帖"),
            A("field_note", "实地记录", {"policy_analysis": 3, "document_writing": 2, "learning": 1, "energy": -4}, -5, 35,
              "就本子速记民情皱眉思忖，神情认真"),
        ]),
        ("calligraphy_acad", "书画院", [
            A("brush_write", "临帖写字", {"document_writing": 3, "creativity": 2, "stress": -3, "energy": -4}, -5, 40,
              "执毫悬腕写行书呼吸匀长，神情专注安宁"),
            A("art_appreciate", "赏画品韵", {"learning": 2, "creativity": 2, "mood": 2, "energy": -3}, -4, 30,
              "展卷端详笔意轻叹，眼含欣赏，神情恬淡"),
        ]),
    ],
}

# 每个女主统一追加的「家里」地点：休息 / 洗澡 / 吃饭 / 和家人聊天。
# effects 全部使用通用列键（DIRECT_COLS），且每个活动必含 energy，确保
# perform_activity -> apply_attr_changes 真正落地属性变化。
HOME_VENUE = ("home", "家里", [
    A("home_rest", "休息", {"energy": 12, "stress": -5, "mood": 3, "health": 2}, -2, 40,
      "窝在沙发里盖着薄毯小憩，四肢舒展，神情松弛安恬"),
    A("home_bath", "洗澡", {"hygiene": 30, "energy": 5, "stress": -4, "mood": 3}, -4, 30,
      "发梢滴着水倚门擦肩，面颊微红，神情清爽松弛"),
    A("home_meal", "吃饭", {"hunger": -20, "energy": 6, "mood": 3, "happiness": 2}, -3, 35,
      "端碗坐在桌边慢慢扒饭，腮帮微鼓，神情满足惬意"),
    A("home_chat_family", "和家人聊天", {"happiness": 4, "stress": -3, "mood": 3, "energy": 2, "confidence": 2}, -2, 30,
      "和家人围坐说笑，眉眼弯弯，神情温暖亲近"),
])


def main():
    app = create_app()
    with app.app_context():
        total_chars = 0
        total_venues = 0
        total_acts = 0
        for name, venues in ACTIVITY_MAPS.items():
            # 校验该角色存在
            char = Character.query.filter_by(name=name).first()
            if not char:
                print(f"[跳过] 未找到角色：{name}")
                continue
            # 删除原有地点（含孤儿自定义地点残留，如旧版『家里/游览观光』）
            deleted = CharacterActivityMap.query.filter_by(character_name=name).delete()
            now = bj_now()
            # 防御性：再删一次 home/家里 行，确保任何旧版游览观光变体都无法残留
            CharacterActivityMap.query.filter_by(character_name=name, venue_id="home").delete()
            CharacterActivityMap.query.filter_by(character_name=name, venue_id="家里").delete()
            db.session.flush()
            all_venues = list(venues) + [HOME_VENUE]
            for vid, vname, acts in all_venues:
                row = CharacterActivityMap(
                    character_name=name,
                    venue_id=vid,
                    venue_name=vname,
                    unlocked=True,
                    discovered_at=now,
                    last_visited=None,
                    visit_count=0,
                    custom_activities=json.dumps(acts, ensure_ascii=False),
                )
                db.session.add(row)
                total_venues += 1
                total_acts += len(acts)
            db.session.commit()
            total_chars += 1
            n_loc = len(venues)
            n_act = sum(len(a) for _, _, a in venues)
            print(f"[完成] {name}：旧 {deleted} 行已删，新建 {n_loc} 地点 / {n_act} 活动")
        print(f"\n总计：{total_chars} 位角色，{total_venues} 地点，{total_acts} 活动")


if __name__ == "__main__":
    main()
