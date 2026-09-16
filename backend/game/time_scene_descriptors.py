# -*- coding: utf-8 -*-
"""时间场景描述词库 TIME_SCENE_DESCRIPTORS。
合并：原列表(94条) + 新增建议补充 = 149条。
用于前端时间面板「更多场景」下拉选择器与 LLM 叙事文案生成。
"""

TIME_SCENE_DESCRIPTORS = [
    # ===================== natural 自然现象与天色变化 =====================
    {"id": "natural_dawn", "phrase": "黎明", "aliases": ["拂晓", "破晓"], "category": "natural", "mode": "range", "start": "04:00", "end": "06:00", "delta_minutes": 0, "span_text": "", "note": "天将亮未亮，夜转昼", "is_new": False},
    {"id": "natural_firstlight", "phrase": "晨曦初露", "aliases": [], "category": "natural", "mode": "range", "start": "06:00", "end": "07:30", "delta_minutes": 0, "span_text": "", "note": "天边泛白，光线柔和", "is_new": False},
    {"id": "natural_highsun", "phrase": "日上三竿", "aliases": [], "category": "natural", "mode": "range", "start": "09:00", "end": "11:00", "delta_minutes": 0, "span_text": "", "note": "太阳升高，上午过半", "is_new": False},
    {"id": "natural_noon", "phrase": "正午", "aliases": ["晌午"], "category": "natural", "mode": "range", "start": "11:00", "end": "14:00", "delta_minutes": 0, "span_text": "", "note": "日头最烈，午间", "is_new": False},
    {"id": "natural_sunset1", "phrase": "日薄西山", "aliases": ["夕阳西下"], "category": "natural", "mode": "range", "start": "17:00", "end": "19:00", "delta_minutes": 0, "span_text": "", "note": "太阳快落山", "is_new": False},
    {"id": "natural_dusk", "phrase": "黄昏", "aliases": ["暮色四合"], "category": "natural", "mode": "range", "start": "18:00", "end": "20:00", "delta_minutes": 0, "span_text": "", "note": "天色转暗", "is_new": False},
    {"id": "natural_lamps", "phrase": "华灯初上", "aliases": [], "category": "natural", "mode": "range", "start": "19:00", "end": "20:30", "delta_minutes": 0, "span_text": "", "note": "路灯亮起，城市夜幕", "is_new": False},
    {"id": "natural_deepnight", "phrase": "夜深人静", "aliases": ["午夜梦回"], "category": "natural", "mode": "range", "start": "23:00", "end": "03:00", "delta_minutes": 0, "span_text": "", "note": "跨午夜 23:00-次日03:00，极安静", "is_new": False},
    {"id": "natural_eastwhite", "phrase": "东方既白", "aliases": [], "category": "natural", "mode": "range", "start": "05:00", "end": "06:30", "delta_minutes": 0, "span_text": "", "note": "天边发白，将亮", "is_new": False},
    {"id": "natural_weiling", "phrase": "凌晨", "aliases": [], "category": "natural", "mode": "range", "start": "00:00", "end": "04:00", "delta_minutes": 0, "span_text": "", "note": "后半夜到天亮前", "is_new": False},
    {"id": "natural_earlymorning", "phrase": "大清早", "aliases": [], "category": "natural", "mode": "range", "start": "05:00", "end": "07:00", "delta_minutes": 0, "span_text": "", "note": "起床前后的早晨", "is_new": False},
    {"id": "natural_morning", "phrase": "早上", "aliases": [], "category": "natural", "mode": "range", "start": "07:00", "end": "09:00", "delta_minutes": 0, "span_text": "", "note": "上午开端", "is_new": False},
    {"id": "natural_forenoon", "phrase": "上午", "aliases": [], "category": "natural", "mode": "range", "start": "08:00", "end": "12:00", "delta_minutes": 0, "span_text": "", "note": "上午整段", "is_new": False},
    {"id": "natural_afternoon1", "phrase": "午后", "aliases": [], "category": "natural", "mode": "range", "start": "13:00", "end": "15:00", "delta_minutes": 0, "span_text": "", "note": "中午过后", "is_new": False},
    {"id": "natural_afternoon", "phrase": "下午", "aliases": [], "category": "natural", "mode": "range", "start": "14:00", "end": "18:00", "delta_minutes": 0, "span_text": "", "note": "下午整段", "is_new": False},
    {"id": "natural_evening", "phrase": "傍晚", "aliases": [], "category": "natural", "mode": "range", "start": "17:30", "end": "19:00", "delta_minutes": 0, "span_text": "", "note": "临近晚饭", "is_new": False},
    {"id": "natural_firstnight", "phrase": "初夜", "aliases": [], "category": "natural", "mode": "range", "start": "20:00", "end": "22:00", "delta_minutes": 0, "span_text": "", "note": "刚入夜", "is_new": False},
    {"id": "natural_late", "phrase": "深夜", "aliases": [], "category": "natural", "mode": "range", "start": "22:00", "end": "00:00", "delta_minutes": 0, "span_text": "", "note": "22:00-次日00:00（跨午夜）", "is_new": False},
    {"id": "natural_deepmid", "phrase": "大半夜", "aliases": [], "category": "natural", "mode": "range", "start": "00:00", "end": "02:00", "delta_minutes": 0, "span_text": "", "note": "午夜最深", "is_new": False},
    {"id": "natural_dimlight", "phrase": "天蒙蒙亮", "aliases": [], "category": "natural", "mode": "range", "start": "05:00", "end": "06:30", "delta_minutes": 0, "span_text": "", "note": "比大清更早的天色微亮", "is_new": True},
    {"id": "natural_bignoon", "phrase": "大中午", "aliases": [], "category": "natural", "mode": "range", "start": "12:00", "end": "14:00", "delta_minutes": 0, "span_text": "", "note": "口语，带热/晒感", "is_new": True},
    {"id": "natural_sleepy_pm", "phrase": "下午最困的时候", "aliases": [], "category": "natural", "mode": "range", "start": "14:00", "end": "16:00", "delta_minutes": 0, "span_text": "", "note": "午后犯困时段", "is_new": True},
    {"id": "natural_darken", "phrase": "天擦黑", "aliases": [], "category": "natural", "mode": "range", "start": "19:00", "end": "20:00", "delta_minutes": 0, "span_text": "", "note": "北方口语，将黑未黑", "is_new": True},
    {"id": "natural_dark", "phrase": "天黑了", "aliases": [], "category": "natural", "mode": "range", "start": "19:30", "end": "21:00", "delta_minutes": 0, "span_text": "", "note": "最直白说法", "is_new": True},
    {"id": "natural_postmid", "phrase": "后半夜", "aliases": [], "category": "natural", "mode": "range", "start": "02:00", "end": "05:00", "delta_minutes": 0, "span_text": "", "note": "过了午夜到天亮前", "is_new": True},
    {"id": "natural_3to5", "phrase": "凌晨三四点", "aliases": [], "category": "natural", "mode": "range", "start": "03:00", "end": "05:00", "delta_minutes": 0, "span_text": "", "note": "拖时间说法", "is_new": True},
    {"id": "natural_daytime", "phrase": "大白天", "aliases": [], "category": "natural", "mode": "range", "start": "08:00", "end": "11:00", "delta_minutes": 0, "span_text": "", "note": "白天感最强时", "is_new": True},

    # ===================== routine 日常生活与作息 =====================
    {"id": "routine_mealtime", "phrase": "饭点", "aliases": ["开饭时"], "category": "routine", "mode": "range", "start": "12:00", "end": "13:00", "delta_minutes": 0, "span_text": "", "note": "午市窗口；晚市窗口 18:00-19:00 可复用", "is_new": False},
    {"id": "routine_aftertea", "phrase": "茶余饭后", "aliases": [], "category": "routine", "mode": "delta", "start": "", "end": "", "delta_minutes": 30, "span_text": "", "note": "饭后+30分钟", "is_new": False},
    {"id": "routine_lamp", "phrase": "掌灯时分", "aliases": [], "category": "routine", "mode": "range", "start": "18:30", "end": "20:00", "delta_minutes": 0, "span_text": "", "note": "点灯到入夜", "is_new": False},
    {"id": "routine_rooster", "phrase": "鸡鸣", "aliases": ["晨练时"], "category": "routine", "mode": "range", "start": "04:30", "end": "06:30", "delta_minutes": 0, "span_text": "", "note": "清晨锻炼", "is_new": False},
    {"id": "routine_closing", "phrase": "打烊前", "aliases": ["散场后"], "category": "routine", "mode": "range", "start": "21:30", "end": "23:00", "delta_minutes": 0, "span_text": "", "note": "店铺关门 / 活动散场", "is_new": False},
    {"id": "routine_schoolbell", "phrase": "下课铃响", "aliases": ["放学路上"], "category": "routine", "mode": "delta", "start": "", "end": "", "delta_minutes": 45, "span_text": "", "note": "放学后+45分钟", "is_new": False},
    {"id": "routine_breakfast", "phrase": "早饭时间", "aliases": [], "category": "routine", "mode": "range", "start": "07:00", "end": "08:30", "delta_minutes": 0, "span_text": "", "note": "吃早饭时段", "is_new": False},
    {"id": "routine_noonbreak", "phrase": "午休时间", "aliases": [], "category": "routine", "mode": "range", "start": "12:30", "end": "14:00", "delta_minutes": 0, "span_text": "", "note": "午睡/休息", "is_new": False},
    {"id": "routine_offwork", "phrase": "下班时刻", "aliases": [], "category": "routine", "mode": "range", "start": "17:30", "end": "18:30", "delta_minutes": 0, "span_text": "", "note": "下班点", "is_new": False},
    {"id": "routine_dinner", "phrase": "晚饭时间", "aliases": [], "category": "routine", "mode": "range", "start": "18:00", "end": "19:30", "delta_minutes": 0, "span_text": "", "note": "吃晚饭", "is_new": False},
    {"id": "routine_bedtime", "phrase": "睡前", "aliases": [], "category": "routine", "mode": "range", "start": "21:30", "end": "22:30", "delta_minutes": 0, "span_text": "", "note": "准备睡", "is_new": False},
    {"id": "routine_afterwake", "phrase": "起床之后", "aliases": [], "category": "routine", "mode": "delta", "start": "", "end": "", "delta_minutes": 15, "span_text": "", "note": "起床+15分钟", "is_new": False},
    {"id": "routine_afteroffwork", "phrase": "下班之后", "aliases": [], "category": "routine", "mode": "delta", "start": "", "end": "", "delta_minutes": 20, "span_text": "", "note": "下班+20分钟", "is_new": False},
    {"id": "routine_noonbreakend", "phrase": "午休结束", "aliases": [], "category": "routine", "mode": "delta", "start": "", "end": "", "delta_minutes": 10, "span_text": "", "note": "午休完+10分钟", "is_new": False},
    {"id": "routine_aftermeeting", "phrase": "散会之后", "aliases": [], "category": "routine", "mode": "delta", "start": "", "end": "", "delta_minutes": 15, "span_text": "", "note": "会议结束+15分钟", "is_new": False},
    {"id": "routine_commute_am", "phrase": "上班路上", "aliases": ["早高峰"], "category": "routine", "mode": "range", "start": "07:30", "end": "09:00", "delta_minutes": 0, "span_text": "", "note": "早通勤", "is_new": True},
    {"id": "routine_afterlunch", "phrase": "午饭后", "aliases": [], "category": "routine", "mode": "range", "start": "13:00", "end": "14:00", "delta_minutes": 0, "span_text": "", "note": "精确到午饭后", "is_new": True},
    {"id": "routine_tea_time", "phrase": "下午茶时间", "aliases": [], "category": "routine", "mode": "range", "start": "15:00", "end": "16:00", "delta_minutes": 0, "span_text": "", "note": "三四点加餐休息", "is_new": True},
    {"id": "routine_rush_pm", "phrase": "下班高峰期", "aliases": [], "category": "routine", "mode": "range", "start": "17:30", "end": "19:00", "delta_minutes": 0, "span_text": "", "note": "晚高峰人多车多", "is_new": True},
    {"id": "routine_overtime", "phrase": "加班时间", "aliases": [], "category": "routine", "mode": "range", "start": "18:30", "end": "22:00", "delta_minutes": 0, "span_text": "", "note": "还在公司/实验室", "is_new": True},
    {"id": "routine_latenight_snack", "phrase": "夜宵时间", "aliases": [], "category": "routine", "mode": "range", "start": "21:30", "end": "23:30", "delta_minutes": 0, "span_text": "", "note": "饿了想吃", "is_new": True},
    {"id": "routine_stayup", "phrase": "熬夜时", "aliases": [], "category": "routine", "mode": "range", "start": "00:00", "end": "03:00", "delta_minutes": 0, "span_text": "", "note": "醒着没睡（区别于大半夜）", "is_new": True},
    {"id": "routine_weekend_liein", "phrase": "周末懒觉时间", "aliases": [], "category": "routine", "mode": "range", "start": "08:00", "end": "10:00", "delta_minutes": 0, "span_text": "", "note": "仅周末", "is_new": True},
    {"id": "routine_wait_transit", "phrase": "等车", "aliases": ["等电梯"], "category": "routine", "mode": "delta", "start": "", "end": "", "delta_minutes": 5, "span_text": "", "note": "碎片等待，3~8分钟取中", "is_new": True},

    # ===================== short 动作与状态的微小变化 =====================
    {"id": "short_instant1", "phrase": "转眼间", "aliases": ["眨眼间", "霎那间"], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 5, "span_text": "", "note": "极短", "is_new": False},
    {"id": "short_snap", "phrase": "弹指一挥间", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 3, "span_text": "", "note": "极短", "is_new": False},
    {"id": "short_teatime", "phrase": "一盏茶的功夫", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 10, "span_text": "", "note": "约10分钟", "is_new": False},
    {"id": "short_incense", "phrase": "一炷香的时间", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 30, "span_text": "", "note": "约30分钟", "is_new": False},
    {"id": "short_breath", "phrase": "呼吸之间", "aliases": ["瞬息"], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 1, "span_text": "", "note": "瞬间", "is_new": False},
    {"id": "short_strike", "phrase": "趁热打铁", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 0, "span_text": "", "note": "立即衔接，不推进时间", "is_new": False},
    {"id": "short_fleet", "phrase": "稍纵即逝", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 2, "span_text": "", "note": "极短", "is_new": False},
    {"id": "short_years3", "phrase": "经年累月", "aliases": [], "category": "short", "mode": "span", "start": "", "end": "", "delta_minutes": 0, "span_text": "3年", "note": "长跨度，极少触发", "is_new": False},
    {"id": "short_quarter", "phrase": "一刻钟", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 15, "span_text": "", "note": "15分钟", "is_new": False},
    {"id": "short_halfquarter", "phrase": "半刻钟", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 8, "span_text": "", "note": "约8分钟", "is_new": False},
    {"id": "short_day1", "phrase": "旦夕之间", "aliases": [], "category": "short", "mode": "span", "start": "", "end": "", "delta_minutes": 0, "span_text": "1天", "note": "一天内", "is_new": False},
    {"id": "short_years2", "phrase": "数载", "aliases": [], "category": "short", "mode": "span", "start": "", "end": "", "delta_minutes": 0, "span_text": "2年", "note": "长跨度，极少触发", "is_new": False},
    {"id": "short_years5", "phrase": "旷日持久", "aliases": [], "category": "short", "mode": "span", "start": "", "end": "", "delta_minutes": 0, "span_text": "5年", "note": "长跨度，极少触发", "is_new": False},
    {"id": "short_wait", "phrase": "等一下", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 3, "span_text": "", "note": "短暂等待", "is_new": False},
    {"id": "short_awhile", "phrase": "一会儿", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 15, "span_text": "", "note": "约15分钟", "is_new": False},
    {"id": "short_while1", "phrase": "一阵子", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 60, "span_text": "", "note": "约1小时", "is_new": False},
    {"id": "short_soon", "phrase": "没多久", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 8, "span_text": "", "note": "约8分钟", "is_new": False},
    {"id": "short_flash", "phrase": "一晃", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 2, "span_text": "", "note": "极短", "is_new": False},
    {"id": "short_instant2", "phrase": "一转眼", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 5, "span_text": "", "note": "极短", "is_new": False},
    {"id": "short_while2", "phrase": "一阵子功夫", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 60, "span_text": "", "note": "约1小时", "is_new": False},
    {"id": "short_longwhile", "phrase": "好一阵子", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 120, "span_text": "", "note": "约2小时", "is_new": False},
    {"id": "short_days5", "phrase": "短短几天", "aliases": [], "category": "short", "mode": "span", "start": "", "end": "", "delta_minutes": 0, "span_text": "5天", "note": "数日", "is_new": False},
    {"id": "short_days30", "phrase": "好些日子", "aliases": [], "category": "short", "mode": "span", "start": "", "end": "", "delta_minutes": 0, "span_text": "30天", "note": "约一月", "is_new": False},
    {"id": "short_years8", "phrase": "好几年", "aliases": [], "category": "short", "mode": "span", "start": "", "end": "", "delta_minutes": 0, "span_text": "8年", "note": "长跨度，极少触发", "is_new": False},
    {"id": "short_soon2", "phrase": "不一会儿", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 8, "span_text": "", "note": "5~10分钟取中", "is_new": True},
    {"id": "short_soon3", "phrase": "没多大会儿", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 10, "span_text": "", "note": "5~15分钟取中", "is_new": True},
    {"id": "short_hour1", "phrase": "个把小时", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 60, "span_text": "", "note": "约1小时", "is_new": True},
    {"id": "short_halfday", "phrase": "大半天", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 480, "span_text": "", "note": "6~10小时取中（480分）", "is_new": True},
    {"id": "short_wholeday", "phrase": "一整天", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 840, "span_text": "", "note": "12~16小时取中（840分）", "is_new": True},
    {"id": "short_allnight", "phrase": "熬了一夜", "aliases": ["通宵"], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 540, "span_text": "", "note": "8~10小时取中（540分）", "is_new": True},
    {"id": "short_fast", "phrase": "说快也快", "aliases": [], "category": "short", "mode": "delta", "start": "", "end": "", "delta_minutes": 45, "span_text": "", "note": "30~60分钟取中", "is_new": True},
]


def get_scenes_for_panel():
    """返回前端时间面板「更多场景」下拉框所需的三类数据。

    Returns:
        dict: {
            "natural": 自然天色列表 (mode=range),
            "routine": 日常作息列表 (mode=range + delta),
            "short": 微小时段列表 (mode=delta, 不含 span),
        }
    """
    natural = []
    routine = []
    short_list = []

    for item in TIME_SCENE_DESCRIPTORS:
        cat = item.get("category", "")
        mode = item.get("mode", "")
        base = {
            "id": item["id"],
            "phrase": item["phrase"],
            "mode": mode,
            "start": item.get("start", ""),
            "end": item.get("end", ""),
            "delta_minutes": item.get("delta_minutes", 0),
        }
        if cat == "natural" and mode == "range":
            natural.append(base)
        elif cat == "routine":
            routine.append(base)
        elif cat == "short" and mode == "delta":
            short_list.append(base)

    return {
        "natural": natural,
        "routine": routine,
        "short": short_list,
    }
