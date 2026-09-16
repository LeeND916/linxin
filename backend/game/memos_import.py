"""全量导入模块

拉取 Memos 实例全部 memo，逐条写入 NerdMemo 表，
计算 embedding（复用 memory._compute_memory_embedding 链路）。
"""

import json
import logging
from datetime import datetime, timezone, timedelta

from flask import Flask

logger = logging.getLogger(__name__)

_BEIJING_TZ = timezone(timedelta(hours=8))


def import_all_memos(app: Flask, batch_size: int = 50) -> dict:
    """全量导入所有 memos

    Args:
        app: Flask 应用实例
        batch_size: 每多少条 commit 一次

    Returns:
        {'total': int, 'new': int, 'updated': int, 'skipped': int, 'embedding_failed': int}
    """
    from backend.models import NerdMemo, db
    from backend.game.memory import _compute_memory_embedding
    from backend.game.memos_client import fetch_all_memos
    from backend.routes.api import get_active_memos_config

    cfg = get_active_memos_config(app)
    base_url = cfg['api_url']
    access_token = cfg['access_token']
    skip_attachments = cfg['skip_attachments']

    stats = {'total': 0, 'new': 0, 'updated': 0, 'skipped': 0, 'embedding_failed': 0}

    logger.info(f"[MemosImport] 开始全量拉取 memos 数据… (skip_attachments={skip_attachments})")
    raw_memos = fetch_all_memos(
        base_url=base_url,
        access_token=access_token,
        skip_attachments=skip_attachments
    )
    stats['total'] = len(raw_memos)
    logger.info(f"[MemosImport] 拉取完成，共 {stats['total']} 条 memo")

    with app.app_context():
        for i, raw in enumerate(raw_memos):
            memo_name = raw.get('name', '')
            if not memo_name:
                stats['skipped'] += 1
                continue

            # 解析时间
            create_time_str = raw.get('createTime', '')
            update_time_str = raw.get('updateTime', '')
            real_created_at = _parse_memo_time(create_time_str)
            real_updated_at = _parse_memo_time(update_time_str)

            # 游戏内时间：年份 -2
            game_created_at = real_created_at.replace(year=real_created_at.year - 2)

            content = raw.get('content', '') or ''
            snippet = raw.get('snippet', '') or content[:200]
            tags = raw.get('tags', []) or []
            tags_json = json.dumps(tags, ensure_ascii=False)

            # 幂等检查
            existing = NerdMemo.query.filter_by(memo_name=memo_name).first()
            if existing:
                # 检查是否需要更新
                if existing.real_updated_at and real_updated_at.astimezone(timezone.utc) <= existing.real_updated_at.replace(tzinfo=_BEIJING_TZ).astimezone(timezone.utc):
                    stats['skipped'] += 1
                else:
                    existing.content = content
                    existing.snippet = snippet
                    existing.tags = tags_json
                    existing.real_updated_at = real_updated_at
                    existing.game_created_at = game_created_at
                    try:
                        existing.embedding = _compute_memory_embedding(content)
                    except Exception as e:
                        logger.warning(f"[MemosImport] embedding 失败 (memo={memo_name}): {e}")
                        stats['embedding_failed'] += 1
                    stats['updated'] += 1
            else:
                nm = NerdMemo(
                    memo_name=memo_name,
                    content=content,
                    snippet=snippet,
                    tags=tags_json,
                    importance=50.0,
                    real_created_at=real_created_at,
                    real_updated_at=real_updated_at,
                    game_created_at=game_created_at,
                )
                try:
                    nm.embedding = _compute_memory_embedding(content)
                except Exception as e:
                    logger.warning(f"[MemosImport] embedding 失败 (memo={memo_name}): {e}")
                    stats['embedding_failed'] += 1
                db.session.add(nm)
                stats['new'] += 1

            # 批量提交
            if (stats['new'] + stats['updated']) % batch_size == 0:
                db.session.commit()
                logger.info(
                    f"[MemosImport] 进度 {i + 1}/{stats['total']} "
                    f"(新 {stats['new']}, 更新 {stats['updated']}, 跳过 {stats['skipped']})"
                )

        # 最终提交
        db.session.commit()

    logger.info(
        f"[MemosImport] 导入完成：总计 {stats['total']}，新增 {stats['new']}，"
        f"更新 {stats['updated']}，跳过 {stats['skipped']}，embedding 失败 {stats['embedding_failed']}"
    )
    return stats


def _parse_memo_time(time_str: str) -> datetime:
    """解析 Memos API 返回的时间字符串 → 北京时间 datetime"""
    if not time_str:
        return datetime.now(_BEIJING_TZ)
    try:
        dt = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_BEIJING_TZ)
    except Exception:
        return datetime.now(_BEIJING_TZ)
