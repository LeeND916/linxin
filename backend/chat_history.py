"""聊天记录文件管理器 — 按 4 小时时间段分 MD 文件存储"""
import json
import os
import re
import threading
from datetime import datetime
from backend.config import Config

# 文件写锁：串行化 append_message / update_message_effects / write_normalized_block
# 对同一 md 文件的写操作，避免异步归一写回与对话结束追加新消息相互覆盖。
_write_lock = threading.Lock()

_CHAT_DIR_PREFIX = "你与"
_CHAT_DIR_SUFFIX = "的对话"

# ── MD 特殊字符转义（防止消息内容中的格式标记被误解析）──

_ZWSP = '\u200b'  # 零宽空格 U+200B，用于破坏 MD 格式标记的正则匹配

_ESCAPE_RULES = [
    # 在行首格式标记中插入零宽空格，破坏正则匹配
    # "## " 开头 → "##\u200B "（解析器不再匹配为消息头）
    (re.compile(r'\n## '), '\n##' + _ZWSP + ' '),
    # "[effects:" → "[\u200Beffects:"
    (re.compile(r'\n\[effects:'), '\n[' + _ZWSP + 'effects:'),
    # "[raw_reply_start]" / "[raw_reply_end]"
    (re.compile(r'\n\[raw_reply_start\]'), '\n[' + _ZWSP + 'raw_reply_start]'),
    (re.compile(r'\n\[raw_reply_end\]'), '\n[' + _ZWSP + 'raw_reply_end]'),
    # "[normalized_start]" / "[normalized_end]"（历史格式归一结果块）
    (re.compile(r'\n\[normalized_start\]'), '\n[' + _ZWSP + 'normalized_start]'),
    (re.compile(r'\n\[normalized_end\]'), '\n[' + _ZWSP + 'normalized_end]'),
    # "> 第X天 HH:MM:SS" → "\u200B> 第X天 HH:MM:SS"
    (re.compile(r'\n> 第'), '\n' + _ZWSP + '> 第'),
]

_UNESCAPE_RULES = [
    # 反转义：移除零宽空格
    (re.compile('\n##' + _ZWSP + ' '), r'\n## '),
    (re.compile('\n\\[' + _ZWSP + 'effects:'), r'\n[effects:'),
    (re.compile('\n\\[' + _ZWSP + 'raw_reply_start\\]'), r'\n[raw_reply_start]'),
    (re.compile('\n\\[' + _ZWSP + 'raw_reply_end\\]'), r'\n[raw_reply_end]'),
    (re.compile('\n\\[' + _ZWSP + 'normalized_start\\]'), r'\n[normalized_start]'),
    (re.compile('\n\\[' + _ZWSP + 'normalized_end\\]'), r'\n[normalized_end]'),
    (re.compile('\n' + _ZWSP + '> 第'), r'\n> 第'),
]


def _escape_md_content(text: str) -> str:
    """转义消息内容中可能被误解析为 MD 格式标记的字符。"""
    if not text:
        return text
    for pattern, replacement in _ESCAPE_RULES:
        text = pattern.sub(replacement, text)
    return text


def _unescape_md_content(text: str) -> str:
    """反转义 MD 读取时的格式标记。"""
    if not text:
        return text
    for pattern, replacement in _UNESCAPE_RULES:
        text = pattern.sub(replacement, text)
    return text


def get_current_character_name():
    """从 DB 读取当前活跃 Character 的 name，若不存在返回 config.CHARACTER_NAME 兜底"""
    from backend.models import Character
    char = Character.query.filter_by(is_active=True).first()
    if not char:
        char = Character.query.order_by(Character.id.desc()).first()
    if char and char.name:
        return char.name
    return Config.CHARACTER_NAME


def _get_chat_dir(character_name=None):
    """获取角色对话目录绝对路径（项目根目录下）"""
    if character_name is None:
        character_name = get_current_character_name()
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, f"{_CHAT_DIR_PREFIX}{character_name}{_CHAT_DIR_SUFFIX}")


def _ensure_folder():
    """确保存储目录存在"""
    folder = _get_chat_dir()
    os.makedirs(folder, exist_ok=True)
    return folder


def _get_4hour_filename():
    """返回当前 4 小时时间段的文件名，格式 YYYY-MM-DD_HH.md。

    HH 取该 4 小时段的起始小时（0/4/8/12/16/20）。
    例如 08:00-11:59 → 08，12:00-15:59 → 12。
    """
    now = datetime.now()
    hour_block = (now.hour // 4) * 4
    return f"{now.strftime('%Y-%m-%d')}_{hour_block:02d}.md"


def append_message(speaker, content, timestamp=None, game_time_str=None, reasoning=None, effects=None, raw_reply=None, photo=None, photo_context=False):
    """向当前 4 小时时间段的 MD 文件追加一条消息。

    Args:
        speaker: 'player' 或 'character'
        content: 消息内容（clean_reply）
        timestamp: datetime 对象，默认当前时间
        game_time_str: 游戏时间字符串，如 "第1天 08:00:15"，为 None 则不写入
        reasoning: 角色的推理过程内容，为 None 或空字符串则不写入
        effects: 属性变化 dict，如 {"player_affection": 3, "mood": 1}，为 None 或空则不写入
        raw_reply: 原始 LLM 输出（含结构化格式），用于刷新后重新解析 segments
        photo: 可选 dict，如 {"id": 123}，表示该轮对话关联的照片（聊天生图），
               写入后刷新对话流可还原照片卡片，避免刷新丢失
        photo_context: 本轮是否触发生图；为真时整轮对话均不参与后续场景定格上下文

    Returns:
        文件绝对路径
    """
    folder = _ensure_folder()
    filename = _get_4hour_filename()
    filepath = os.path.join(folder, filename)

    if timestamp is None:
        timestamp = datetime.now()

    ts_str = timestamp.strftime('%Y-%m-%d %H:%M:%S')

    # 如果文件不存在，先写入标题
    if not os.path.exists(filepath):
        # 从文件名提取日期和时段
        parts = filename.replace('.md', '').split('_')
        if len(parts) == 2:
            date_part = parts[0]
            hour_start = int(parts[1])
            hour_end = hour_start + 3
            title = f"# {date_part} {hour_start:02d}:00 - {hour_end:02d}:59 对话记录\n\n"
        else:
            title = f"# 对话记录\n\n"
    else:
        title = ""

    char_name = get_current_character_name()
    speaker_label = '玩家' if speaker == 'player' else char_name
    # 转义消息内容中可能被误解析的 MD 格式标记（## 头、[effects:]、[raw_reply_*]、行首 > 第X天）
    try:
        safe_content = _escape_md_content(content)
    except Exception as e:
        print(f"[ChatHistory] _escape_md_content(content) 失败: {e}")
        raise
    entry = f"## {ts_str} - {speaker_label}\n{safe_content}\n"
    if raw_reply and raw_reply != content:
        # 将 raw_reply 用特殊标记包裹，便于解析时重新提取
        try:
            safe_raw = _escape_md_content(raw_reply)
        except Exception as e:
            print(f"[ChatHistory] _escape_md_content(raw_reply) 失败: {e}")
            raise
        entry += f"\n[raw_reply_start]\n{safe_raw}\n[raw_reply_end]\n"
    if effects:
        try:
            entry += f"\n[effects: {json.dumps(effects, ensure_ascii=False)}]\n"
        except Exception as e:
            print(f"[ChatHistory] json.dumps(effects) 失败: {e}")
            raise
    if game_time_str:
        entry += f"\n> {game_time_str}\n"
    if reasoning:
        entry += f"\n<details>\n<summary>💭 {char_name}的推理过程</summary>\n\n{reasoning}\n\n</details>\n"
    if photo and isinstance(photo, dict) and photo.get('id'):
        # 照片卡片持久化：仅存 photo_id，刷新时回查 PhotoRecord 还原（含图片/状态）
        try:
            entry += f"\n[photo: {json.dumps({'id': int(photo['id'])}, ensure_ascii=False)}]\n"
        except Exception:
            pass
    if photo_context:
        entry += "\n[photo_context]\n"
    entry += "\n"

    try:
        with _write_lock:
            with open(filepath, 'a', encoding='utf-8') as f:
                if title:
                    f.write(title)
                f.write(entry)
    except Exception as e:
        print(f"[ChatHistory] 文件写入失败 filepath={filepath}: {e}")
        raise

    return filepath


def load_all_sessions(character_name=None):
    """读取指定角色的所有 MD 文件（按文件名排序），合并返回消息列表。

    character_name 为 None 时保持读取当前活跃角色，兼容既有调用。

    Returns:
        list[dict]: 每条消息包含 speaker / content / game_day / game_time
    """
    folder = _get_chat_dir(character_name)
    if not os.path.exists(folder):
        return []

    files = sorted([f for f in os.listdir(folder) if f.endswith('.md')])
    if not files:
        return []

    all_messages = []
    for filename in files:
        filepath = os.path.join(folder, filename)
        all_messages.extend(_parse_md_file(filepath))

    # 跨文件（多 MD 分段）按游戏时间从先到后排序，确保照片与文字统一归位时间线
    all_messages.sort(key=lambda m: (
        (m.get('game_day') or 0),
        (m.get('game_time') or '')
    ))
    return all_messages


def _parse_md_file(filepath):
    """解析 MD 文件内容为消息列表，提取 effects / game_time / reasoning"""
    messages = []

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return []

    # 按消息头切分：## YYYY-MM-DD HH:MM:SS - 玩家/角色名
    sections = re.split(r'\n(?=## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - )', content)
    for section in sections:
        header_match = re.match(
            r'## (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - (玩家|.+?)\n',
            section
        )
        if not header_match:
            continue

        timestamp, speaker_label = header_match.groups()
        speaker = 'player' if speaker_label == '玩家' else 'character'
        body = section[header_match.end():].strip()

        # 1. 先提取末尾的 <details> 推理过程块（必须在提取 game_time 之前，否则 $ 锚点无法匹配）
        details_match = re.search(
            r'\n<details>\s*<summary>💭 .+?的推理过程</summary>\s*\n(.*?)\n</details>',
            body, re.DOTALL
        )
        if details_match:
            reasoning = details_match.group(1).strip()
            # 仅删除 details 块本身，保留其后可能跟随的 [photo: {id}] / [photo_context] 标记。
            # 旧写法 body[:details_match.start()] 会截断 details 之后的全部内容，
            # 导致「带推理块的角色照片消息」刷新后照片卡片丢失（photo 标记被一并吃掉）。
            body = (body[:details_match.start()] + body[details_match.end():]).strip()
        else:
            reasoning = ''

        # 2. 本轮对话触发生图：先提取内部标记，保证后续时间/照片解析不受末尾标记影响。
        photo_context = '\n[photo_context]' in body
        if photo_context:
            body = body.replace('\n[photo_context]', '').strip()

        # 3. 提取 blockquote 中的游戏时间：> 第X天 HH:MM:SS（此时 body 末尾无 details，$ 可正常匹配）
        # 提取 blockquote 中的游戏时间（time line 可能在末尾，也可能是照片独占消息的 block 开头；
        # 用 (?:^|\n) 同时兼容“行首”与“换行后”，且精确删除 time line 不影响其后 [photo] 标记）
        gt_match = re.search(r'(?:^|\n)> (第\d+天 \d{2}:\d{2}:\d{2})', body)
        if gt_match:
            game_time_str = gt_match.group(1)
            body = (body[:gt_match.start()] + body[gt_match.end():]).strip()
        else:
            # 兼容只带“第X天”不带时分秒的写法（照片独占消息常见），仍可解析出 game_day
            day_only = re.search(r'(?:^|\n)> (第\d+天)\b', body)
            if day_only:
                game_time_str = day_only.group(1)
                body = (body[:day_only.start()] + body[day_only.end():]).strip()
            else:
                game_time_str = ''

        # 3. 提取照片关联块（照片独占消息可能只有“> 第X天”和[photo]标记）
        photo = None
        photo_match = re.search(r'(?:^|\n)\[photo: (\{.*?\})\]', body)
        if photo_match:
            try:
                _photo_obj = json.loads(photo_match.group(1))
                if _photo_obj.get('id'):
                    photo = {'id': int(_photo_obj['id'])}
            except Exception:
                photo = None
            body = body[:photo_match.start()].strip()

        # 照片独占消息的时间可能只有“> 第X天”，不能当作正文
        if re.fullmatch(r'> 第\d+天', body or ''):
            body = ''

        # 4. 提取属性变化（在游戏时间行之前）
        effects_match = re.search(r'\n\[effects: ({.*?})\]\s*$', body)
        if effects_match:
            try:
                effects = json.loads(effects_match.group(1))
            except (json.JSONDecodeError, KeyError):
                effects = None
            body = body[:effects_match.start()].strip()
        else:
            effects = None

        # 4. 提取 raw_reply 块（用于重新解析 segments）
        raw_reply_match = re.search(r'\n\[raw_reply_start\]\n(.*?)\n\[raw_reply_end\]\s*$', body, re.DOTALL)
        if raw_reply_match:
            raw_reply = _unescape_md_content(raw_reply_match.group(1).strip())
            body = body[:raw_reply_match.start()].strip()
        else:
            raw_reply = None

        # 4.5 提取 normalized 块（历史格式归一结果，回灌时优先替代散文 content）
        # [normalized_start]...[normalized_end] 用独立起止标记，内容可含 "]" 不被截断
        norm_match = re.search(r'\n\[normalized_start\]\n(.*?)\n\[normalized_end\]', body, re.DOTALL)
        if norm_match:
            normalized_content = _unescape_md_content(norm_match.group(1).strip())
            body = body[:norm_match.start()].strip()
        else:
            normalized_content = None

        msg_content = _unescape_md_content(body)

        # 解析 segments（优先用 raw_reply，否则用 clean_reply 兆底）
        segments = []
        if raw_reply:
            from backend.game.dialogue import parse_structured_reply
            segments = parse_structured_reply(raw_reply)
        else:
            # 旧数据没有 raw_reply，尝试从 content 解析
            from backend.game.dialogue import parse_structured_reply
            segments = parse_structured_reply(msg_content)

        # 从 "第X天 HH:MM:SS" 拆出 game_day 与 game_time(HH:MM:SS)
        gt_parse = re.match(r'第(\d+)天 (.+)', game_time_str)
        if gt_parse:
            game_day = int(gt_parse.group(1))
            game_time = gt_parse.group(2)
        elif game_time_str:
            # 仅“第X天”（无时分秒）：仍解析出 game_day，game_time 留空
            day_only = re.match(r'第(\d+)天', game_time_str)
            game_day = int(day_only.group(1)) if day_only else None
            game_time = ''
        else:
            game_day = None
            game_time = ''

        messages.append({
            'speaker': speaker,
            'content': msg_content,
            'raw_reply': raw_reply,
            'normalized_content': normalized_content,
            'photo': photo,
            'photo_context': photo_context,
            'game_day': game_day,
            'game_time': game_time,
            'reasoning': reasoning,
            'effects': effects,
            'segments': segments,
        })

    return messages


def mark_latest_photo_context(character_name=None, message_count=2):
    """给最新一轮聊天补写生图标记，供回复侧触发生图后排除上下文。

    回复侧生图在 append_message 之后执行，因此需要将刚写入的玩家/角色两条消息
    标为 photo_context，避免下一次场景定格把本次生图触发文本当作剧情依据。
    """
    folder = _get_chat_dir(character_name)
    if not os.path.exists(folder):
        return False
    files = sorted(f for f in os.listdir(folder) if f.endswith('.md'))
    if not files:
        return False
    filepath = os.path.join(folder, files[-1])
    try:
        with _write_lock:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            starts = [m.start() for m in re.finditer(r'(?m)^## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - ', content)]
            if not starts:
                return False
            for start in reversed(starts[-message_count:]):
                next_start = next((pos for pos in starts if pos > start), len(content))
                block = content[start:next_start]
                if '[photo_context]' in block:
                    continue
                insertion = next_start
                content = content[:insertion].rstrip() + '\n\n[photo_context]\n\n' + content[insertion:]
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
        return True
    except Exception:
        return False


def list_sessions():
    """列出所有历史会话（按文件名排序）。

    Returns:
        list[str]: 会话 ID 列表
    """
    folder = _get_chat_dir()
    if not os.path.exists(folder):
        return []

    files = sorted([f for f in os.listdir(folder) if f.endswith('.md')])
    return [os.path.splitext(f)[0] for f in files]


def clear_all_files():
    """清空当前角色的所有 MD 对话文件"""
    folder = _get_chat_dir()
    if not os.path.exists(folder):
        return
    for f in os.listdir(folder):
        if f.endswith('.md'):
            try:
                os.remove(os.path.join(folder, f))
            except Exception:
                pass


def _count_messages_in_file(filepath):
    """统计一个 MD 文件中的消息数量（不含块头标题）。"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return 0
    messages = re.findall(r'^## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - (玩家|.+)$', content, re.MULTILINE)
    return len(messages)


def total_message_count():
    """快速统计所有 MD 文件中的消息总数（轻量，只数 ## 头不解析全文）。"""
    folder = _get_chat_dir()
    if not os.path.exists(folder):
        return 0
    files = [f for f in os.listdir(folder) if f.endswith('.md')]
    return sum(_count_messages_in_file(os.path.join(folder, f)) for f in files)


def update_message_effects(message_index: int, new_effects: dict):
    """更新第 message_index（0-based，跨所有会话文件）条消息的 effects 块。

    只处理 character 消息；若目标消息非 character 或索引越界，则静默返回。

    Args:
        message_index: 消息在所有 MD 文件合并后的索引（0-based）
        new_effects: 新的 effects dict

    Returns:
        bool: 是否成功更新
    """
    folder = _get_chat_dir()
    if not os.path.exists(folder):
        return False

    files = sorted([f for f in os.listdir(folder) if f.endswith('.md')])

    # 找到包含目标消息的 MD 文件
    cumulative = 0
    target_file = None
    target_offset = 0  # 目标消息在 target_file 内的序号（0-based）

    for filename in files:
        filepath = os.path.join(folder, filename)
        count = _count_messages_in_file(filepath)
        if cumulative + count > message_index:
            target_file = filepath
            target_offset = message_index - cumulative
            break
        cumulative += count

    if target_file is None:
        return False

    # 读取目标文件并找到目标消息（按 ## 时间戳头切分）
    try:
        with open(target_file, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return False

    sections = re.split(r'\n(?=## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - )', content)
    # 第一个拆分项可能是标题头，跳过非消息块
    msg_sections = [s for s in sections if re.match(r'^## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - ', s)]
    if target_offset >= len(msg_sections):
        return False

    target_section = msg_sections[target_offset]

    # 检查是否为 character 消息（非"玩家"）
    header_match = re.match(r'^## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - (玩家|.+?)\n', target_section)
    if not header_match or header_match.group(1) == '玩家':
        return False

    # 替换 effects 块
    effects_str = json.dumps(new_effects, ensure_ascii=False)
    new_effects_block = f'\n[effects: {effects_str}]'

    # 先移除旧的 [effects: ...] 块
    old_section = target_section
    section_without_effects = re.sub(r'\n\[effects: \{.*?\}\]', '', old_section, count=1)

    # 在第一个 blockquote（游戏时间）前插入新 effects 块
    if '\n> ' in section_without_effects:
        section_with_effects = section_without_effects.replace('\n> ', new_effects_block + '\n> ', 1)
    elif '<details>' in section_without_effects:
        # 没有游戏时间行，插入到 details 块之前
        section_with_effects = section_without_effects.replace('\n<details>', new_effects_block + '\n<details>', 1)
    else:
        section_with_effects = section_without_effects.rstrip() + new_effects_block + '\n'

    # 回写文件
    new_content = content.replace(old_section, section_with_effects, 1)
    try:
        with _write_lock:
            with open(target_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
        return True
    except Exception:
        return False


def update_message_photo(message_index: int, photo_id):
    """把照片 id 持久化到跨所有会话文件合并后第 message_index（0-based）条消息末尾。

    仅处理 character 消息；若目标消息非 character、索引越界或已含 photo 标记，则静默返回。

    用途：玩家侧触发生图时由 append_message 直接写 [photo: {id}] 标记；而「角色回复侧
    触发生图」是在写 MD 之后才执行的逻辑，只补了 [photo_context] 记号、漏写了照片身份证号，
    导致刷新后聊天流无法还原该照片卡片。本函数用于在回复侧触发后，把 [photo: {id}] 标记
    补写回对应轮（角色回复）的 MD 段尾，使其与玩家侧触发行为一致。

    Returns:
        bool: 是否成功写入（已存在视为成功）
    """
    try:
        photo_id = int(photo_id)
    except (TypeError, ValueError):
        return False
    folder = _get_chat_dir()
    if not os.path.exists(folder):
        return False

    files = sorted([f for f in os.listdir(folder) if f.endswith('.md')])

    # 与 update_message_effects 同构：按 ## 头累计定位目标消息所在文件与文件内序号
    cumulative = 0
    target_file = None
    target_offset = 0
    for filename in files:
        filepath = os.path.join(folder, filename)
        count = _count_messages_in_file(filepath)
        if cumulative + count > message_index:
            target_file = filepath
            target_offset = message_index - cumulative
            break
        cumulative += count

    if target_file is None:
        return False

    try:
        with open(target_file, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return False

    sections = re.split(r'\n(?=## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - )', content)
    msg_sections = [s for s in sections if re.match(r'^## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - ', s)]
    if target_offset >= len(msg_sections):
        return False

    target_section = msg_sections[target_offset]

    # 只处理角色（非玩家）消息
    header_match = re.match(r'^## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - (玩家|.+?)\n', target_section)
    if not header_match or header_match.group(1) == '玩家':
        return False

    # 幂等：已含 [photo: ...] 标记则跳过（玩家侧触发已写或重复调用）
    if re.search(r'\n\[photo:\s*\{', target_section):
        return True

    # 标记格式与 append_message 写入的一致：置于 [photo_context] 之前、段尾。
    photo_marker = '\n[photo: {"id": ' + str(photo_id) + '}]'
    if '[photo_context]' in target_section:
        new_section = target_section.replace('\n[photo_context]', photo_marker + '\n[photo_context]', 1)
    else:
        new_section = target_section.rstrip() + photo_marker + '\n'

    new_content = content.replace(target_section, new_section, 1)
    try:
        with _write_lock:
            with open(target_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
        return True
    except Exception:
        return False


def write_normalized_block(global_index: int, normalized_text: str):
    """把历史格式归一结果写回第 global_index（0-based，跨所有会话文件）条消息的
    [normalized_start]...[normalized_end] 块。

    只处理 character 消息；若目标消息非 character、索引越界或写入失败，则静默返回 False。
    normalized_text 应为 (描述)[对话内容] 结构文本。配合 history_normalizer 使用。
    """
    folder = _get_chat_dir()
    if not os.path.exists(folder):
        return False

    files = sorted([f for f in os.listdir(folder) if f.endswith('.md')])

    # 跨文件定位目标消息（与 update_message_effects 同逻辑）
    cumulative = 0
    target_file = None
    target_offset = 0
    for filename in files:
        filepath = os.path.join(folder, filename)
        count = _count_messages_in_file(filepath)
        if cumulative + count > global_index:
            target_file = filepath
            target_offset = global_index - cumulative
            break
        cumulative += count

    if target_file is None:
        return False

    try:
        with _write_lock:
            with open(target_file, 'r', encoding='utf-8') as f:
                content = f.read()
            sections = re.split(r'\n(?=## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - )', content)
            msg_sections = [s for s in sections if re.match(r'^## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - ', s)]
            if target_offset >= len(msg_sections):
                return False
            target_section = msg_sections[target_offset]

            # 只处理角色（非玩家）消息
            header_match = re.match(r'^## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - (玩家|.+?)\n', target_section)
            if not header_match or header_match.group(1) == '玩家':
                return False

            # 幂等：该消息已有归一块则跳过，避免重复写块（多次触发归一时易叠加）
            if '\n[normalized_start]' in target_section:
                return True

            # 转义归一文本中的格式标记，防止其被误解析为 MD 格式
            safe = _escape_md_content(normalized_text)
            block = f"\n[normalized_start]\n{safe}\n[normalized_end]"

            # 插入位置：优先在 [raw_reply_start] 之前（content 之后）；
            # 若无 raw_reply，则在 [effects: 之前；再无则在 > 第 之前；再无则置于段尾
            if '\n[raw_reply_start]' in target_section:
                new_section = target_section.replace('\n[raw_reply_start]', block + '\n[raw_reply_start]', 1)
            elif '\n[effects:' in target_section:
                new_section = target_section.replace('\n[effects:', block + '\n[effects:', 1)
            elif '\n> ' in target_section:
                new_section = target_section.replace('\n> ', block + '\n> ', 1)
            else:
                new_section = target_section.rstrip() + block + '\n'

            new_content = content.replace(target_section, new_section, 1)
            with open(target_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
        return True
    except Exception:
        return False


def remove_photo_marker(photo_id):
    """删除所有角色聊天 MD 文件中指向该照片 id 的 [photo: {"id": X}] 标记。

    相册删除照片（api_photo_delete）时调用，消除聊天流里的 ghost round
    （照片记录已删、聊天 MD 仍残留标记，导致刷新后查无此图、只剩空时间行气泡）。

    - 命中标记的整轮消息若去掉标记后变空（仅剩块头/时间行/effects/推理，无任何可见正文），
      则整轮一并移除；
    - 命中标记但仍有正文/推理等内容，则仅删标记行，保留对话文字。
    Returns:
        int: 被修改并写回的文件数
    """
    target = int(photo_id)
    marker_re = re.compile(r'^\[photo:\s*\{"id":\s*' + str(target) + r'(?:,[^{}]*)?\}\]\s*$')
    changed_files = 0
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in sorted(os.listdir(root)):
        folder = os.path.join(root, name)
        if not (os.path.isdir(folder) and name.startswith(_CHAT_DIR_PREFIX) and name.endswith(_CHAT_DIR_SUFFIX)):
            continue
        for fn in sorted(os.listdir(folder)):
            if not fn.endswith('.md'):
                continue
            fp = os.path.join(folder, fn)
            try:
                with open(fp, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception:
                continue
            new_content = _remove_marker_from_content(content, target, marker_re)
            if new_content != content:
                try:
                    with _write_lock:
                        with open(fp, 'w', encoding='utf-8') as f:
                            f.write(new_content)
                    changed_files += 1
                except Exception as e:
                    print(f"[ChatHistory] remove_photo_marker 写回失败 {fp}: {e}")
    return changed_files


def _remove_marker_from_content(content, target, marker_re):
    """从单个 MD 文件内容中删除目标照片标记，并清理因此变空的整轮消息。

    按消息头(## 时间戳 - 角色)切分为消息块；首块通常是文件标题，不含标记，原样保留。
    """
    parts = re.split(r'(?=\n## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - )', content)
    out = []
    changed = False
    for part in parts:
        if not any(marker_re.match(ln) for ln in part.split('\n')):
            out.append(part)
            continue
        # 去掉目标 marker 行
        lines = [ln for ln in part.split('\n') if not marker_re.match(ln)]
        # 判断去掉 marker 后该块是否为空（无任何用户可见内容）：
        # 移去 时间行 / 块头 / 仅 effects 行 / <details> 推理块 / 空白 后若仍无内容，则整块删除
        body = '\n'.join(lines)
        body = re.sub(r'(?m)^\s*> 第\d+天[^\n]*\n?', '', body)
        body = re.sub(r'(?m)^## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - .*\n?', '', body)
        body = re.sub(r'(?m)^\s*\[effects:[^\]]*\]\s*\n?', '', body)
        body = re.sub(r'<details>.*?</details>\s*', '', body, flags=re.DOTALL)
        if body.strip() == '':
            changed = True
            continue
        new_part = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines))
        out.append(new_part)
        changed = True
    if not changed:
        return content
    result = ''.join(out)
    result = re.sub(r'\n{3,}', '\n\n', result).strip('\n')
    return result + ('\n' if result else '')
