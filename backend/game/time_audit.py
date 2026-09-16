# -*- coding: utf-8 -*-
"""游戏时间变动审计

集中记录所有对 `Character.game_day / game_hour / game_minute` 的修改，
一旦出现「归零(→0)」或「倒退(<旧值)」等异常变动，输出高优先级 ALERT 日志
（统一标记 [GAME_TIME_AUDIT]），并在数据库中写入一条 system_warning 事件，
便于事后通过日志 / 事件面板追溯「游戏时间被重置」的成因。

调用约定：在调用方执行 `char.game_day = ...` 赋值【之前】调用本函数，
此时 char 仍持有旧值，本函数负责记录旧→新并判定异常。
"""
import logging

logger = logging.getLogger('game_time_audit')

# 倒退告警阈值（天）：new_day 比 old_day 低超过该值即视为异常倒退
# 设为 0 表示任何倒退都告警
REGRESSION_ALERT_THRESHOLD = 0


def _safe_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def audit_game_time_change(char, new_day, new_hour, new_minute, source, extra=None):
    """审计一次游戏时间变动。

    Args:
        char: Character 实例（仍持有旧值）
        new_day / new_hour / new_minute: 即将写入的新值
        source: 变动来源标识，如 'batch_advance' / 'calendar_jump' /
                'character_create' / 'reset_runtime_data'
        extra: 附加上下文字符串（如 total_minutes、target 日期等）
    """
    try:
        old_day = _safe_int(getattr(char, 'game_day', None))
        old_hour = _safe_int(getattr(char, 'game_hour', None))
        old_minute = _safe_int(getattr(char, 'game_minute', None))
        name = getattr(char, 'name', '?')

        new_day = _safe_int(new_day)
        new_hour = _safe_int(new_hour)
        new_minute = _safe_int(new_minute)

        delta_day = new_day - old_day

        # 基础留痕（INFO）：正常推进也记录，可按 source 检索
        info_msg = (
            f"[GAME_TIME_AUDIT] source={source} char={name} "
            f"old=D{old_day} {old_hour:02d}:{old_minute:02d} -> "
            f"new=D{new_day} {new_hour:02d}:{new_minute:02d} "
            f"delta_day={delta_day}"
        )
        if extra:
            info_msg += f" | {extra}"
        logger.info(info_msg)

        # 异常判定：归零 或 倒退
        alert = False
        reasons = []
        if new_day == 0 and old_day != 0:
            alert = True
            reasons.append("归零(day→0)")
        if delta_day < -REGRESSION_ALERT_THRESHOLD:
            alert = True
            reasons.append(f"倒退{delta_day}天")

        if alert:
            alert_msg = (
                f"[GAME_TIME_AUDIT][ALERT] 游戏时间异常变动! "
                f"source={source} char={name} "
                f"old=D{old_day} {old_hour:02d}:{old_minute:02d} -> "
                f"new=D{new_day} {new_hour:02d}:{new_minute:02d} "
                f"原因={'/'.join(reasons)}"
            )
            if extra:
                alert_msg += f" | {extra}"
            logger.error(alert_msg)

            # 同步写入一条 system_warning 事件，确保事件面板与数据库层也能看到
            try:
                from backend.models import db, EventLog
                ev = EventLog(
                    event_type='system_warning',
                    event_category='system_warning',
                    title='游戏时间异常变动',
                    description=(
                        f"来源={source} 游戏时间由 D{old_day} {old_hour:02d}:{old_minute:02d} "
                        f"变为 D{new_day} {new_hour:02d}:{new_minute:02d}"
                        f"（{'/'.join(reasons)}）"
                    ),
                    effects='{}',
                    game_day=new_day,
                    game_time=f"{new_hour:02d}:{new_minute:02d}:00",
                    character_name=name,
                    location='',
                )
                db.session.add(ev)
                db.session.commit()
            except Exception as e:
                logger.error(f"[GAME_TIME_AUDIT] 写入 ALERT EventLog 失败: {e}")
    except Exception as e:
        logger.error(f"[GAME_TIME_AUDIT] 审计自身异常: {e}")
