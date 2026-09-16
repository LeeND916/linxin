"""
游戏天气系统 - 每日天气生成、属性影响、对话描述
"""
import os
import random
import logging
from datetime import date, timedelta
from backend.models import Weather, db, Character
from backend.config import Config
import requests

logger = logging.getLogger(__name__)

# 天气类型定义
WEATHER_POOL = [
    {
        'type': 'sunny',
        'name': '晴天',
        'icon': 'sun',
        'temp_range': (22, 35),
        'effects': {'mood': 5, 'energy': 3, 'motivation': 3, 'stress': -2},
        'descriptions': [
            '阳光明媚，万里无云',
            '晴空万里，适合出门走走',
            '金色阳光洒满校园',
        ],
        'weight': 30,
    },
    {
        'type': 'cloudy',
        'name': '多云',
        'icon': 'cloud',
        'temp_range': (15, 28),
        'effects': {'mood': 0, 'energy': 0, 'motivation': 0, 'stress': 0},
        'descriptions': [
            '多云，天空灰蒙蒙的',
            '云层遮蔽了阳光，但还算舒适',
            '阴天，空气里有潮湿的味道',
        ],
        'weight': 25,
    },
    {
        'type': 'light_rain',
        'name': '小雨',
        'icon': 'rain',
        'temp_range': (12, 22),
        'effects': {'mood': -3, 'energy': -2, 'motivation': -1, 'loneliness': 2, 'creativity': 3},
        'descriptions': [
            '细雨绵绵，窗外沙沙作响',
            '小雨淅淅沥沥，空气湿润清新',
            '窗外飘着细雨，适合窝在室内',
        ],
        'weight': 20,
    },
    {
        'type': 'heavy_rain',
        'name': '大雨',
        'icon': 'storm',
        'temp_range': (8, 18),
        'effects': {'mood': -8, 'energy': -5, 'motivation': -5, 'stress': 5, 'loneliness': 5},
        'descriptions': [
            '大雨滂沱，雷声滚滚',
            '暴雨如注，窗外一片模糊',
            '雨势很大，根本出不了门',
        ],
        'weight': 8,
    },
    {
        'type': 'windy',
        'name': '大风',
        'icon': 'wind',
        'temp_range': (10, 25),
        'effects': {'mood': -2, 'energy': -1, 'motivation': -2, 'stress': 3},
        'descriptions': [
            '大风呼啸，树枝摇晃',
            '风很大，吹得窗户哐哐响',
            '狂风卷起落叶，不宜外出',
        ],
        'weight': 10,
    },
    {
        'type': 'foggy',
        'name': '雾霾',
        'icon': 'fog',
        'temp_range': (5, 15),
        'effects': {'mood': -4, 'energy': -3, 'motivation': -3, 'health': -1},
        'descriptions': [
            '大雾弥漫，能见度很低',
            '雾蒙蒙的，什么都看不清',
            '空气里有雾霾的味道，注意防护',
        ],
        'weight': 5,
    },
    {
        'type': 'snow',
        'name': '下雪',
        'icon': 'snow',
        'temp_range': (-5, 3),
        'effects': {'mood': 8, 'energy': 5, 'happiness': 10, 'creativity': 5},
        'descriptions': [
            '雪花静静飘落，世界银装素裹',
            '初雪！校园里一片白茫茫',
            '雪下得很大，天地间只剩下纯白',
        ],
        'weight': 2,
    },
]


# ========== 外部真实天气（Open-Meteo，免费无需 Key）==========
# 开启开关（config.EXTERNAL_WEATHER_ENABLED，环境变量 EXTERNAL_WEATHER_ENABLED=false 可关）
_EXTERNAL_ENABLED = getattr(Config, 'EXTERNAL_WEATHER_ENABLED', True)
_DEFAULT_CITY = getattr(Config, 'WEATHER_CITY_DEFAULT', '北京')

_GEOCODE_CACHE = {}      # city -> (lat, lon)
_WEATHER_CACHE = {}      # (city, date_iso) -> dict | None

# WMO 天气代码 → 本项目天气类型
_WMO_MAP = {
    0: 'sunny',
    1: 'cloudy', 2: 'cloudy', 3: 'cloudy',
    45: 'foggy', 48: 'foggy',
    51: 'light_rain', 53: 'light_rain', 55: 'light_rain',
    56: 'light_rain', 57: 'light_rain',
    61: 'light_rain', 63: 'light_rain', 66: 'heavy_rain', 67: 'heavy_rain',
    71: 'snow', 73: 'snow', 75: 'snow', 77: 'snow',
    80: 'light_rain', 81: 'light_rain', 82: 'heavy_rain',
    85: 'snow', 86: 'snow',
    95: 'heavy_rain', 96: 'heavy_rain', 99: 'heavy_rain',
}

# 月份 → 温度季节偏移（相对基础 temp_range 的整体平移，单位 °C）
# 解决“12 月仍抽到 30°C 晴天”的硬规则缺陷；仅作为外部天气查不到时的兜底。
_SEASONAL_OFFSET = {
    1: -14, 2: -12, 3: -6, 4: -2, 5: 2, 6: 6,
    7: 9, 8: 9, 9: 5, 10: 0, 11: -6, 12: -14,
}


def _seasonal_offset(month):
    """返回某月的季节温度偏移量（°C）。"""
    return _SEASONAL_OFFSET.get(month, 0)


def _geocode(city):
    """城市名 → (lat, lon)；失败返回 None（带内存缓存避免重复请求）。"""
    if city in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[city]
    try:
        r = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "zh"},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results")
        if results:
            loc = results[0]
            coord = (loc["latitude"], loc["longitude"])
            _GEOCODE_CACHE[city] = coord
            return coord
    except Exception as e:
        logger.warning(f"[Weather] 地理编码失败 {city!r}: {e}")
    _GEOCODE_CACHE[city] = None
    return None


def _fetch_real_weather(city, target_date):
    """查角色所在城市的真实天气。

    返回 dict（含 weather_type / temperature / city / code）或 None：
      - 游戏日期在「过去（含今天）」→ archive-api.open-meteo.com（回溯到 1940）；
      - 游戏日期在「未来 16 天窗口内」→ api.open-meteo.com 预报；
      - 更远（或城市解析失败 / 无网络）→ None，由调用方回退到硬规则。
    """
    key = (city, target_date.isoformat())
    if key in _WEATHER_CACHE:
        return _WEATHER_CACHE[key]
    if not _EXTERNAL_ENABLED:
        _WEATHER_CACHE[key] = None
        return None

    coord = _geocode(city)
    if not coord:
        _WEATHER_CACHE[key] = None
        return None
    lat, lon = coord

    today = date.today()
    if target_date <= today:
        base = "https://archive-api.open-meteo.com/v1/archive"
    elif target_date <= today + timedelta(days=16):
        base = "https://api.open-meteo.com/v1/forecast"
    else:
        # 超出可查窗口：回退硬规则
        _WEATHER_CACHE[key] = None
        return None

    try:
        r = requests.get(
            base,
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
                "start_date": target_date.isoformat(),
                "end_date": target_date.isoformat(),
                "timezone": "Asia/Shanghai",
            },
            timeout=10,
        )
        r.raise_for_status()
        daily = r.json().get("daily", {})
        if not daily.get("time"):
            _WEATHER_CACHE[key] = None
            return None
        tmax = daily["temperature_2m_max"][0]
        tmin = daily["temperature_2m_min"][0]
        code = daily["weather_code"][0]
        wtype = _WMO_MAP.get(code, 'cloudy')
        temp = round((tmax + tmin) / 2, 1)
        result = {
            "weather_type": wtype,
            "temperature": temp,
            "city": city,
            "code": code,
        }
        _WEATHER_CACHE[key] = result
        return result
    except Exception as e:
        logger.warning(f"[Weather] 真实天气获取失败 {city} {target_date}: {e}")
        _WEATHER_CACHE[key] = None
        return None


def generate_daily_weather(target_date=None, character=None):
    """为目标日期生成天气，已存在的日期不会覆盖。

    优先用角色 hometown 城市查 Open-Meteo 真实天气；查不到（无网络 / 游戏日期
    超出可查窗口）时回退到「做了季节温度修正」的硬规则，避免冬天仍抽到 30°C 晴天。
    """
    if target_date is None:
        target_date = date.today()

    existing = Weather.query.filter_by(date=target_date).first()
    if existing:
        return existing

    month = target_date.month

    # 1) 城市：角色 hometown 优先，其次活跃角色，最后兜底默认城市
    city = None
    if character and getattr(character, 'hometown', ''):
        city = character.hometown
    if not city:
        try:
            from backend.game.character import get_character
            c = get_character()
            if c and getattr(c, 'hometown', ''):
                city = c.hometown
        except Exception:
            pass
    if not city:
        city = _DEFAULT_CITY

    # 2) 尝试外部真实天气
    real = _fetch_real_weather(city, target_date)
    if real:
        wtype = real['weather_type']
        selected = next((w for w in WEATHER_POOL if w['type'] == wtype), WEATHER_POOL[0])
        desc = random.choice(selected['descriptions'])
        weather = Weather(
            date=target_date,
            weather_type=wtype,
            weather_name=selected['name'],
            temperature=real['temperature'],
            description=f"{desc}（{city}实况）",
            icon=selected['icon'],
        )
        db.session.add(weather)
        db.session.commit()
        return weather

    # 3) 兜底：季节修正硬规则
    pool = [w.copy() for w in WEATHER_POOL]
    for w in pool:
        if w['type'] == 'snow' and month in (12, 1, 2):
            w['weight'] = 15  # 冬天下雪概率提高
        elif w['type'] == 'snow' and month not in (12, 1, 2):
            w['weight'] = 0   # 非冬季几乎不下雪
        elif w['type'] == 'sunny' and month in (6, 7, 8):
            w['weight'] = 40
        elif w['type'] == 'heavy_rain' and month in (6, 7, 8):
            w['weight'] = 15
        elif w['type'] == 'heavy_rain' and month in (12, 1, 2):
            w['weight'] = 3
        elif w['type'] == 'foggy' and month in (11, 12, 1):
            w['weight'] = 12

    # 加权随机选择
    total_weight = sum(w['weight'] for w in pool if w['weight'] > 0)
    rand = random.uniform(0, total_weight)
    cumulative = 0
    selected = None
    for w in pool:
        if w['weight'] <= 0:
            continue
        cumulative += w['weight']
        if rand <= cumulative:
            selected = w
            break

    if selected is None:
        selected = pool[0]

    # 季节温度修正：按月份整体平移 temp_range，避免冬天仍抽到 30°C 晴天
    off = _seasonal_offset(month)
    tmin = max(-30, selected['temp_range'][0] + off)
    tmax = min(45, selected['temp_range'][1] + off)
    temp = round(random.uniform(tmin, tmax), 1)
    desc = random.choice(selected['descriptions'])

    weather = Weather(
        date=target_date,
        weather_type=selected['type'],
        weather_name=selected['name'],
        temperature=temp,
        description=desc,
        icon=selected['icon'],
    )
    db.session.add(weather)
    db.session.commit()
    return weather


def get_today_weather():
    """获取今天的天气，不存在则生成"""
    today = date.today()
    weather = Weather.query.filter_by(date=today).first()
    if not weather:
        weather = generate_daily_weather(today)
    return weather


def get_weather_effects(weather):
    """根据天气类型获取属性影响"""
    for w in WEATHER_POOL:
        if w['type'] == weather.weather_type:
            return w['effects']
    return {}


def get_weather_dialogue_prompt(weather):
    """生成天气相关的对话提示文本"""
    prompt_parts = [
        f"【当前天气】{weather.weather_name}，气温{weather.temperature}°C，{weather.description}",
    ]
    # 天气对活动和心情的影响提示
    effects = get_weather_effects(weather)
    if weather.weather_type in ('heavy_rain', 'snow'):
        prompt_parts.append("由于天气原因，户外活动受限，你只能在室内活动。")
    elif weather.weather_type in ('light_rain', 'foggy'):
        prompt_parts.append("天气不太好，你可能不太想出门，更倾向室内活动。")
    elif weather.weather_type == 'sunny':
        prompt_parts.append("天气很好，你心情愉悦，愿意出门走走或参加户外活动。")

    return '\n'.join(prompt_parts)


def apply_daily_weather_effects(character):
    """应用当日天气对角色属性的持续性影响（每天一次）"""
    weather = get_today_weather()
    effects = get_weather_effects(weather)
    from backend.game.character import apply_attr_changes
    apply_attr_changes(character, effects)
    return weather


def is_weekend(target_date=None):
    """判断指定日期是否为周末（周六或周日）。
    也可传入 game_weekday 整数（0=周一...6=周日）。"""
    if target_date is None:
        target_date = date.today()
    if isinstance(target_date, int):
        return target_date >= 5  # 5=周六, 6=周日
    # Python: Monday=0, Sunday=6 → 周六=5, 周日=6
    return target_date.weekday() >= 5


def get_weekday_name(target_date=None):
    """获取中文星期名称。
    也可传入 game_weekday 整数（0=周一...6=周日）。"""
    names = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
    if target_date is None:
        target_date = date.today()
    if isinstance(target_date, int):
        return names[target_date] if 0 <= target_date <= 6 else '未知'
    return names[target_date.weekday()]


def get_weekend_prompt(game_weekday=None):
    """如果今天是周末，返回周末提示文本；否则返回空字符串。
    game_weekday: 0=周一...6=周日。不传则用现实日期。"""
    if is_weekend(game_weekday):
        return (
            "【周末提示】今天是周末，你不用去上课，可以睡个懒觉、做自己喜欢的事。"
            "心情更加放松，不需要推进学业和工作相关的活动。"
        )
    return ""


def get_game_date_display(character):
    """返回如 '2024年9月15日 星期日' 的字符串"""
    year, month, day, weekday = character.get_game_date()
    return f"{year}年{month}月{day}日 {weekday}"


def get_weather_effects_by_name(weather_name):
    """根据中文天气名返回属性影响字典（用于角色存储天气场景）"""
    # 短名→全名映射
    alias = {
        '晴': '晴天', '多云': '多云', '小雨': '小雨',
        '阴天': '阴天', '大风': '大风', '雾': '大雾',
    }
    target = alias.get(weather_name, weather_name)
    for w in WEATHER_POOL:
        if w['name'] == target:
            return w.get('effects', {})
    return {}
