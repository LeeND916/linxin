"""角色穿搭数据库 — 15 套风格，每天随机变化

每套穿搭包含：
- name: 名称（如"实验室简约风"）
- description: 描述（用于事件/对话中提及）
- style: 风格标签（daily / sport / social / home）
- accessories: 配饰（眼镜、手表、背包等）
- hairstyle: 发型（马尾、披肩、丸子头等）
- color_tone: 颜色基调（cool / warm / neutral）
- season: 季节适配（spring_autumn / summer / winter / all）
"""

# 15 套穿搭
WARDROBE = [
    {
        "id": "lab_minimal",
        "name": "实验室简约风",
        "description": "白色基础款T恤配深灰针织开衫，牛仔裤，白球鞋，干净利落适合写代码。",
        "style": "daily",
        "accessories": "细框眼镜、黑色双肩电脑包、银色手表",
        "hairstyle": "低马尾",
        "color_tone": "neutral",
        "season": "all",
    },
    {
        "id": "library_quiet",
        "name": "图书馆静谧风",
        "description": "米色高领毛衣配卡其长裙，平底乐福鞋，温柔安静适合长时间阅读。",
        "style": "daily",
        "accessories": "细框眼镜、布艺托特包、素色发夹",
        "hairstyle": "披肩发",
        "color_tone": "warm",
        "season": "spring_autumn",
    },
    {
        "id": "campus_fresh",
        "name": "校园清新风",
        "description": "浅蓝衬衫裙配小白鞋，清爽有朝气，上课逛校园都合适。",
        "style": "daily",
        "accessories": "帆布包、细项链、透明框眼镜",
        "hairstyle": "侧分短发",
        "color_tone": "cool",
        "season": "spring_autumn",
    },
    {
        "id": "code_comfort",
        "name": "码农舒适风",
        "description": "宽松灰色卫衣配运动裤，连帽衫兜里塞着手写笔记，最舒服的写代码状态。",
        "style": "daily",
        "accessories": "无线耳机、双肩包、硅胶手环",
        "hairstyle": "丸子头",
        "color_tone": "neutral",
        "season": "all",
    },
    {
        "id": "morning_run",
        "name": "晨跑活力风",
        "description": "速干运动背心配跑步短裤，透气排汗，绑带跑鞋，活力满满去操场。",
        "style": "sport",
        "accessories": "运动手表、发带、腰包",
        "hairstyle": "高马尾",
        "color_tone": "cool",
        "season": "summer",
    },
    {
        "id": "yoga_soft",
        "name": "瑜伽柔软风",
        "description": "莫代尔瑜伽套装，弹性好不束缚，裸色运动内衣外搭薄罩衫，放松舒展。",
        "style": "sport",
        "accessories": "瑜伽垫包、发圈、小水晶手链",
        "hairstyle": "编发",
        "color_tone": "warm",
        "season": "all",
    },
    {
        "id": "gym_energy",
        "name": "健身房能量风",
        "description": "黑色紧身运动上衣配网眼训练裤，专业有型，举铁跑步都自在。",
        "style": "sport",
        "accessories": "护腕、运动水壶、蓝牙耳机",
        "hairstyle": "高丸子头",
        "color_tone": "neutral",
        "season": "all",
    },
    {
        "id": "coffee_date",
        "name": "咖啡馆约会风",
        "description": "奶白色针织衫配百褶半裙，小皮鞋，精致但不刻意，适合见朋友或约会。",
        "style": "social",
        "accessories": "珍珠耳钉、链条小包、细手表",
        "hairstyle": "微卷披肩",
        "color_tone": "warm",
        "season": "spring_autumn",
    },
    {
        "id": "seminar_neat",
        "name": "组会得体风",
        "description": "浅灰西装外套配白衬衫和直筒西裤，干练专业，组会汇报不怯场。",
        "style": "social",
        "accessories": "简约耳钉、皮质公文包、机械表",
        "hairstyle": "低盘发",
        "color_tone": "neutral",
        "season": "all",
    },
    {
        "id": "party_light",
        "name": "轻社交派对风",
        "description": "酒红色丝绒连衣裙配短靴，有点小设计感但不夸张，聚会中恰到好处。",
        "style": "social",
        "accessories": "耳坠、手拿包、细手链",
        "hairstyle": "半扎发",
        "color_tone": "warm",
        "season": "winter",
    },
    {
        "id": "home_lazy",
        "name": "居家慵懒风",
        "description": "宽松棉质睡衣套装，柔软亲肤，窝在沙发里看书追剧最舒服。",
        "style": "home",
        "accessories": "毛绒拖鞋、发箍、抱枕",
        "hairstyle": "随意散落",
        "color_tone": "warm",
        "season": "all",
    },
    {
        "id": "home_sweater",
        "name": "居家毛衣风",
        "description": "oversize 羊绒毛衣配毛绒家居裤，暖呼呼的，阴天窝家里写东西很安心。",
        "style": "home",
        "accessories": "毛线袜、发圈、热饮杯",
        "hairstyle": "丸子头",
        "color_tone": "warm",
        "season": "winter",
    },
    {
        "id": "summer_breeze",
        "name": "夏日清凉风",
        "description": "碎花雪纺连衣裙配草编凉鞋，轻盈透气，夏天出门也不闷。",
        "style": "daily",
        "accessories": "草帽、藤编包、细手链",
        "hairstyle": "麻花辫",
        "color_tone": "warm",
        "season": "summer",
    },
    {
        "id": "winter_layer",
        "name": "冬日叠穿风",
        "description": "高领打底配格纹大衣和围巾，保暖又有层次，雪天也走得稳。",
        "style": "daily",
        "accessories": "毛线围巾、皮手套、托特包",
        "hairstyle": "低马尾",
        "color_tone": "neutral",
        "season": "winter",
    },
    {
        "id": "rainy_blue",
        "name": "雨天蓝调风",
        "description": "雾蓝风衣配深蓝直筒裤和短靴，沉静内敛，阴雨天也自有节奏。",
        "style": "daily",
        "accessories": "透明伞、帆布包、素圈戒指",
        "hairstyle": "披肩发",
        "color_tone": "cool",
        "season": "spring_autumn",
    },
]

# 索引：id -> 穿搭
WARDROBE_BY_ID = {o["id"]: o for o in WARDROBE}


def get_outfit(outfit_id: str) -> dict | None:
    """根据 id 获取穿搭，不存在返回 None"""
    return WARDROBE_BY_ID.get(outfit_id)


def get_outfits_by_style(style: str) -> list:
    """按风格标签筛选穿搭"""
    return [o for o in WARDROBE if o["style"] == style]


def get_outfits_by_season(season: str) -> list:
    """按季节筛选穿搭（'all' 表示四季通用，始终包含）"""
    return [o for o in WARDROBE if o["season"] == "all" or o["season"] == season]


def pick_random_outfit(seed=None, style=None, season=None):
    """随机选择一套穿搭。

    Args:
        seed: 随机种子（可选，用于可复现）
        style: 限定风格（daily/sport/social/home），None 表示不限
        season: 限定季节，None 表示不限
    Returns:
        dict: 选中的穿搭
    """
    import random
    pool = WARDROBE
    if style:
        pool = [o for o in pool if o["style"] == style]
    if season:
        pool = [o for o in pool if o["season"] == "all" or o["season"] == season]
    if not pool:
        pool = WARDROBE  # 兜底：筛选为空时返回全集
    rng = random.Random(seed) if seed is not None else random
    return rng.choice(pool)


def pick_outfit_by_state(energy, mood, has_social=False, season=None, seed=None):
    """根据角色状态智能选择穿搭。

    规则：
    - 有社交活动 → 社交风格
    - 精力低 → 居家/舒适风格
    - 心情好 → 更亮眼/有设计感（暖色或社交）
    - 否则 → 日常风格随机

    Returns:
        dict: 选中的穿搭
    """
    import random
    rng = random.Random(seed) if seed is not None else random

    if has_social:
        return rng.choice(get_outfits_by_style("social"))

    if energy is not None and energy < 30:
        # 精力低：居家或舒适日常
        home_pool = get_outfits_by_style("home")
        comfort_pool = [o for o in WARDROBE if o["id"] in ("code_comfort", "home_lazy", "home_sweater")]
        pool = home_pool + comfort_pool
        if season:
            pool = [o for o in pool if o["season"] == "all" or o["season"] == season]
        return rng.choice(pool) if pool else rng.choice(WARDROBE)

    if mood is not None and mood >= 75:
        # 心情好：偏暖色或社交/有设计感
        bright_pool = [o for o in WARDROBE if o["color_tone"] == "warm" or o["style"] == "social"]
        if season:
            bright_pool = [o for o in bright_pool if o["season"] == "all" or o["season"] == season]
        return rng.choice(bright_pool) if bright_pool else rng.choice(WARDROBE)

    # 默认：日常风格
    daily_pool = get_outfits_by_style("daily")
    if season:
        daily_pool = [o for o in daily_pool if o["season"] == "all" or o["season"] == season]
    return rng.choice(daily_pool) if daily_pool else rng.choice(WARDROBE)
