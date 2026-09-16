"""事件日志文件系统管理器 — 以 JSON 文件存储所有事件日志。

目录结构：
    项目根目录/
    └── {角色名}的事件日志/
        ├── events_day_1.json     # 按天拆分的事件文件
        ├── events_day_2.json
        └── ...
JSON 数据结构（events_day_{day}.json）：
[
  {
    "id": 1,
    "day": 3,
    "time": "14:00",
    "location": "实验室",
    "title": "午餐后感到困倦",
    "content": "午饭后晓月回到实验室，但困意来袭，趴在桌上小睡了20分钟。",
    "type": "physical_state",
    "state_changes": [
      {"attribute": "energy", "old": 55, "new": 70, "delta": 15}
    ],
    "relation_changes": []
  }
]
"""

import os
import json
import glob

try:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
except NameError:
    BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')

EVENTS_FILE = "events.json"
DAILY_EVENTS_PATTERN = "events_day_*.json"


def _get_current_character_name():
    """从 DB 读取活跃 Character 的 name，若不存在返回兜底值"""
    from backend.config import Config
    from backend.models import Character
    char = Character.query.filter_by(is_active=True).first()
    if not char:
        char = Character.query.order_by(Character.id.desc()).first()
    if char and char.name:
        return char.name
    return Config.CHARACTER_NAME


def _get_event_dir(character_name=None):
    """获取事件日志目录完整路径"""
    if character_name is None:
        character_name = _get_current_character_name()
    return os.path.join(BASE_DIR, f"{character_name}的事件日志")


def get_events_json_path(character_name=None):
    """获取 events.json 完整路径，自动创建目录"""
    path = _get_event_dir(character_name)
    os.makedirs(path, exist_ok=True)
    return os.path.join(path, EVENTS_FILE)


def _get_daily_events_path(day, character_name=None):
    """获取某一天的事件文件完整路径"""
    path = _get_event_dir(character_name)
    os.makedirs(path, exist_ok=True)
    return os.path.join(path, f"events_day_{day}.json")


def _get_available_days(character_name=None):
    """扫描事件目录，获取已有事件文件的天数列表（降序）。"""
    event_dir = _get_event_dir(character_name)
    if not os.path.exists(event_dir):
        return []
    days = []
    for fname in os.listdir(event_dir):
        if fname.startswith("events_day_") and fname.endswith(".json"):
            try:
                day = int(fname[len("events_day_"):-len(".json")])
                days.append(day)
            except ValueError:
                pass
    days.sort(reverse=True)
    return days


def load_all_events(day=None, limit=None):
    """加载事件，支持按天过滤和分页。优先从每日文件读取以提升性能。

    Args:
        day: 指定游戏天，None=全部
        limit: 最多返回条数，None=不限制

    Returns:
        list[dict]: 事件字典列表，按 ID 降序排列。每个事件包含：
            id, day, time, location, title, content, type,
            state_changes, relation_changes
    """
    if day is not None:
        # 只读指定天数的文件 — 性能优化关键路径
        daily_path = _get_daily_events_path(day)
        if os.path.exists(daily_path):
            try:
                with open(daily_path, 'r', encoding='utf-8') as f:
                    events = json.load(f)
                if not isinstance(events, list):
                    events = []
                events.sort(key=lambda e: e.get('id', 0), reverse=True)
                if limit is not None and limit > 0:
                    events = events[:limit]
                return events
            except (json.JSONDecodeError, IOError):
                pass
        return []

    # 无 day 限制：优先从每日文件读取最近的数据
    available_days = _get_available_days()
    if available_days:
        events = []
        for d in available_days:
            if limit is not None and len(events) >= limit:
                break
            daily_path = _get_daily_events_path(d)
            if os.path.exists(daily_path):
                try:
                    with open(daily_path, 'r', encoding='utf-8') as f:
                        day_events = json.load(f)
                    if isinstance(day_events, list):
                        events.extend(day_events)
                except (json.JSONDecodeError, IOError):
                    pass
        events.sort(key=lambda e: e.get('id', 0), reverse=True)
        if limit is not None and limit > 0:
            events = events[:limit]
        return events

    return []


def _load_from_legacy_file(day=None, limit=None):
    """从旧 events.json 兜底读取（兼容旧数据）。"""
    filepath = get_events_json_path()
    if not os.path.exists(filepath):
        return []

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

    events = data.get('events', [])
    if day is not None:
        events = [e for e in events if e.get('day') == day]
    events.sort(key=lambda e: e.get('id', 0), reverse=True)
    if limit is not None and limit > 0:
        events = events[:limit]
    return events


def append_event(event_data):
    """追加一条事件到对应天的 JSON 文件。

    Args:
        event_data: dict, 必须包含以下字段：
            day (int): 游戏天
            time (str): "HH:MM" 或 "HH:MM:SS"
            location (str): 地点
            title (str): 事件标题
            content (str): 事件详细描述
            type (str): 事件类型
            state_changes (list): [{"attribute": "energy", "old": 55, "new": 70, "delta": 15}]
            relation_changes (list): [{"friend_name": "王教授", "attribute": "intimacy", "delta": 3}]

    Returns:
        dict: 包含 auto_id 的完整事件数据
    """
    # 确保 day 字段存在
    day = event_data.get('day', 0)

    # 从每日文件读取已有事件，分配新 ID
    daily_path = _get_daily_events_path(day)
    daily_events = []
    if os.path.exists(daily_path):
        try:
            with open(daily_path, 'r', encoding='utf-8') as f:
                daily_events = json.load(f)
        except (json.JSONDecodeError, IOError):
            daily_events = []
    if not isinstance(daily_events, list):
        daily_events = []

    if daily_events:
        max_id = max((e.get('id', 0) for e in daily_events), default=0)
        event_data["id"] = max_id + 1
    else:
        event_data["id"] = 1

    # 确保必要字段存在
    event_data.setdefault("location", "")
    event_data.setdefault("state_changes", [])
    event_data.setdefault("relation_changes", [])

    # 写入每日文件
    daily_events.append(event_data)
    with open(daily_path, 'w', encoding='utf-8') as f:
        json.dump(daily_events, f, ensure_ascii=False, indent=2)

    return event_data


def load_events(game_day=None, limit=50):
    """读取事件日志，返回与前端兼容的结构。

    Args:
        game_day: int or None, 指定天或 None=全部
        limit: int, 最多返回条数

    Returns:
        dict: {'events': [...], 'total': int, 'days': list}
    """
    all_events = load_all_events()
    days_set = set()

    if game_day is not None:
        filtered = [e for e in all_events if e.get('day') == game_day]
    else:
        filtered = all_events

    for e in filtered:
        if e.get('day') is not None:
            days_set.add(e['day'])

    filtered = filtered[:limit]

    return {
        'events': filtered,
        'total': len(filtered),
        'days': sorted(days_set)
    }


def load_all_events_list(limit=None):
    """加载所有事件（从最新往前读），返回按时间倒序的事件列表"""
    result = load_events(limit=limit or 1000)
    return result['events']



