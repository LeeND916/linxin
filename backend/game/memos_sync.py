"""增量同步模块

后台 daemon 线程，定期拉取 Memos API 的增量更新，
对比本地 nerd_memo 表做 upsert。
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_BEIJING_TZ = timezone(timedelta(hours=8))


class MemosSyncThread(threading.Thread):
    """Memos 增量同步后台线程"""

    def __init__(self, app, interval: int = 30, start_delay: int = 60):
        super().__init__(daemon=True)
        self.app = app
        self.interval = interval
        self.start_delay = start_delay
        self._stop_event = threading.Event()

    def run(self):
        logger.info(f"[MemosSync] 线程启动，start_delay={self.start_delay}s, interval={self.interval}s")
        time.sleep(self.start_delay)

        while not self._stop_event.is_set():
            try:
                self._sync_once()
            except Exception as e:
                logger.error(f"[MemosSync] 同步异常: {e}")
            self._stop_event.wait(self.interval)

    def stop(self):
        self._stop_event.set()

    def _sync_once(self):
        from backend.models import NerdMemo, MemosConfig, db
        from backend.game.memory import _compute_memory_embedding
        from backend.game.memos_client import fetch_updated_memos
        from backend.routes.api import get_active_memos_config

        cfg = get_active_memos_config(self.app)
        base_url = cfg['api_url']
        access_token = cfg['access_token']
        skip_attachments = cfg['skip_attachments']
        
        if not base_url or not access_token:
            logger.warning("[MemosSync] 配置不完整（api_url 或 access_token 为空），跳过同步")
            return

        # 获取上次同步时间
        with self.app.app_context():
            memos_cfg = MemosConfig.query.filter_by(is_active=True).first()
            last_synced = memos_cfg.last_synced_at if memos_cfg else None
            if last_synced and last_synced.tzinfo is None:
                last_synced = last_synced.replace(tzinfo=_BEIJING_TZ)
        
        # 增量拉取：只获取 last_synced 之后有更新的 memo
        raw_memos = fetch_updated_memos(
            base_url=base_url,
            access_token=access_token,
            since=last_synced,
            skip_attachments=skip_attachments
        )
        if not raw_memos:
            logger.debug("[MemosSync] 无新增或更新的 memo")
            return

        new_count = updated_count = skip_count = 0
        sync_time = datetime.now(_BEIJING_TZ)

        with self.app.app_context():
            for raw in raw_memos:
                memo_name = raw.get('name', '')
                if not memo_name:
                    skip_count += 1
                    continue

                create_time_str = raw.get('createTime', '')
                update_time_str = raw.get('updateTime', '')
                real_created_at = _parse_memo_time(create_time_str)
                real_updated_at = _parse_memo_time(update_time_str)
                game_created_at = real_created_at.replace(year=real_created_at.year - 2)

                content = raw.get('content', '') or ''
                snippet = raw.get('snippet', '') or content[:200]
                tags = raw.get('tags', []) or []
                tags_json = json.dumps(tags, ensure_ascii=False)

                existing = NerdMemo.query.filter_by(memo_name=memo_name).first()
                if existing:
                    if existing.real_updated_at and real_updated_at.astimezone(timezone.utc) <= existing.real_updated_at.replace(tzinfo=_BEIJING_TZ).astimezone(timezone.utc):
                        skip_count += 1
                        continue
                    existing.content = content
                    existing.snippet = snippet
                    existing.tags = tags_json
                    existing.real_updated_at = real_updated_at
                    existing.game_created_at = game_created_at
                    try:
                        existing.embedding = _compute_memory_embedding(content)
                    except Exception as e:
                        logger.warning(f"[MemosSync] embedding 更新失败 (memo={memo_name}): {e}")
                    updated_count += 1
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
                        logger.warning(f"[MemosSync] embedding 失败 (memo={memo_name}): {e}")
                    db.session.add(nm)
                    new_count += 1

            # 更新 last_synced_at
            # 首次同步（last_synced_at 为 NULL）或有任何变更时都更新时间戳
            if memos_cfg:
                should_update = (new_count or updated_count) or (memos_cfg.last_synced_at is None)
                if should_update:
                    memos_cfg.last_synced_at = sync_time
            
            db.session.commit()

        if new_count or updated_count:
            logger.info(
                f"[MemosSync] 增量同步完成：新增 {new_count}，更新 {updated_count}，跳过 {skip_count}，"
                f"last_synced_at={sync_time.strftime('%Y-%m-%d %H:%M:%S')}"
            )


def start_memos_sync(app, interval: int = 30, start_delay: int = 60) -> MemosSyncThread:
    """启动增量同步线程

    Args:
        app: Flask 应用实例
        interval: 同步间隔（秒）
        start_delay: 启动后首次同步延迟（秒）

    Returns:
        MemosSyncThread 实例
    """
    thread = MemosSyncThread(app, interval=interval, start_delay=start_delay)
    thread.start()
    return thread


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
