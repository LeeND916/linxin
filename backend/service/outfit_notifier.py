"""SSE 推送管理：当角色 location 改变触发换装后，实时推送穿搭数据到前端。"""
import json
import logging
import queue

log = logging.getLogger(__name__)

# 活跃的 SSE 客户端队列
_listeners: list[queue.Queue] = []


def register_listener():
    """注册新的 SSE 客户端，返回一个阻塞队列。"""
    q = queue.Queue()
    _listeners.append(q)
    log.debug(f"[SSE] Listener registered, total={len(_listeners)}")
    return q


def unregister_listener(q):
    """移除监听器队列。"""
    if q in _listeners:
        _listeners.remove(q)
        log.debug(f"[SSE] Listener removed, total={len(_listeners)}")


def notify_outfit_changed(character_name, outfit):
    """推送穿搭变更事件到所有 SSE 监听器。"""
    payload = json.dumps({
        'character_name': character_name,
        'outfit': outfit,
    }, ensure_ascii=False)

    dead = []
    for q in _listeners:
        try:
            q.put_nowait(payload)
        except queue.Full:
            dead.append(q)

    for q in dead:
        unregister_listener(q)

    if payload:
        log.debug(f"[SSE] Pushed outfit change for {character_name} to {len(_listeners)} listeners")
