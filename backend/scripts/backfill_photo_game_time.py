# -*- coding: utf-8 -*-
"""回填老照片的游戏时间到 photo_record 表。

背景：
- PhotoRecord 已有 source_day / source_time 字段（与聊天记录的「游戏时间」同源），
  但存量老照片在字段落地之前生成，这两个字段为默认空值（source_day=0 / source_time=''），
  导致相册 _formatGameTime 走兜底分支显示「游戏时间未知」。
- 运行时 enrich_photo_dict 已能通过 _photo_game_time_from_chat 从聊天 MD 临时回填，
  但依赖聊天 MD 存在；本脚本把结果**持久化**进 DB，让相册时间脱离聊天 MD 独立存活
  （即使后续删除聊天记录，相册时间也不丢），也免去每次拉取重复解析。

为什么不复用 _photo_game_time_from_chat：
- 现有 _parse_md_file 在「时间行 与 [photo] 标记之间夹着 <details> 推理块」时，
  会把推理块连同其后的照片标记一起截断，导致这类照片（回复触发生图，最常见）解析不到时间。
- 故本脚本直接对聊天 MD 做**更鲁棒的原始扫描**：按消息头切分，对每个 [photo: {"id": X}]
  向上在同消息块内取最近一条完整「> 第N天 HH:MM:SS」时间行，不受 <details> 位置影响。

行为：
- 仅回填 source_time 为空的记录；已有值不覆盖。
- 聊天记录仅含「> 第N天」（无时分秒）的照片在聊天中本就无精确时间，跳过保持「未知」。
- 时间归一为 HH:MM:SS（与正常生成路径写入格式一致）。北京时间由聊天 MD 原样保留，SQL 写入不转义中文。

用法（项目根目录）：
  python backend/scripts/backfill_photo_game_time.py
"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app import create_app, db
from backend.models import PhotoRecord

_CHAT_DIR_PREFIX = "你与"
_CHAT_DIR_SUFFIX = "的对话"

# 仅回填 source_time 为空的存量记录
_EMPTY_TIME = (PhotoRecord.source_time == '') | (PhotoRecord.source_time.is_(None))

# 单条消息块内的照片标记
_PHOTO_RE = re.compile(r'\[photo:\s*\{"id":\s*(\d+)(?:,[^{}]*)?\}\]')
# 完整的游戏时间行：> 第N天 HH:MM(:SS)
_TIME_RE = re.compile(r'^> (第\d+天 \d{2}:\d{2}(?::\d{2})?)\s*$', re.MULTILINE)
# 从「第N天 HH:MM:SS」拆出 day / time
_GT_PARSE_RE = re.compile(r'第(\d+)天 (.+)')

_TIME_NORMALIZE_RE = re.compile(r'^(\d{1,2}):(\d{2})(?::(\d{2}))?$')


def _normalize_time(t):
    """把 HH:MM 或 HH:MM:SS 统一规整为 HH:MM:SS。"""
    if not t:
        return ''
    m = _TIME_NORMALIZE_RE.match(str(t).strip())
    if not m:
        return str(t).strip()
    return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}:{int(m.group(3) or 0):02d}"


def _scan_character_times(character_name):
    """扫描某角色聊天 MD，返回 {photo_id: (game_day, game_time)}。

    按消息头(## 时间戳 - 角色)切分为消息块，块内独立解析，时间行与照片标记同块即关联，
    不受 <details> 推理块位置影响。
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    folder = os.path.join(root, f"{_CHAT_DIR_PREFIX}{character_name}{_CHAT_DIR_SUFFIX}")
    result = {}
    if not os.path.isdir(folder):
        return result
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith('.md'):
            continue
        fp = os.path.join(folder, fn)
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception:
            continue
        # 按消息块切分（保留 ## 头在块首）
        sections = re.split(r'(?=\n## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - )', content)
        for sec in sections:
            photo_ids = [int(x) for x in _PHOTO_RE.findall(sec)]
            if not photo_ids:
                continue
            time_matches = _TIME_RE.findall(sec)
            if not time_matches:
                continue
            gt = time_matches[-1]  # 取块内最后一条完整时间行（通常即该轮时间）
            gm = _GT_PARSE_RE.match(gt)
            if not gm:
                continue
            day = int(gm.group(1))
            time_text = _normalize_time(gm.group(2))
            for pid in photo_ids:
                result.setdefault(pid, (day, time_text))
    return result


def main():
    app = create_app()
    with app.app_context():
        rows = PhotoRecord.query.filter(_EMPTY_TIME).all()
        total = len(rows)
        updated = 0
        skipped = 0
        for rec in rows:
            times = _scan_character_times(rec.character_name)
            pair = times.get(rec.id)
            if not pair:
                skipped += 1
                continue
            day, time_text = pair
            if day is None or not time_text:
                skipped += 1
                continue
            rec.source_day = int(day)
            rec.source_time = time_text
            updated += 1
        db.session.commit()
        print(f"需回填记录: {total}")
        print(f"已回填(聊天有精确时间): {updated}")
        print(f"跳过(聊天无精确时间或无标记): {skipped}")


if __name__ == '__main__':
    main()
