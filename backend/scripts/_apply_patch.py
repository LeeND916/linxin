# -*- coding: utf-8 -*-
"""幂等补丁器：基于 .orig 备份生成 A-2 补丁后的文件，同时落到备份区 .patched 与正式路径。

可反复运行；若正式文件被外部进程回滚，重跑本脚本即可恢复。
"""
import os

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BK = os.path.join(BASE, '.workbuddy', 'backup_20260810')
FRAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_rmfrag.py')

CH_LIVE = os.path.join(BASE, 'backend', 'chat_history.py')
PH_LIVE = os.path.join(BASE, 'backend', 'routes', 'photo.py')
CH_ORIG = os.path.join(BK, 'chat_history.py.orig')
PH_ORIG = os.path.join(BK, 'photo.py.orig')
CH_PATCHED = os.path.join(BK, 'chat_history.py.patched')
PH_PATCHED = os.path.join(BK, 'photo.py.patched')


def read(p):
    with open(p, 'r', encoding='utf-8') as f:
        return f.read()


def write(p, s):
    with open(p, 'w', encoding='utf-8') as f:
        f.write(s)


frag = read(FRAG)

# ---- chat_history.py：以 .orig 为基线追加函数（幂等） ----
ch = read(CH_ORIG)
assert 'def remove_photo_marker' not in ch, '.orig 备份已被污染，请检查'
ch_new = ch.rstrip() + '\n' + frag

# ---- routes/photo.py：以 .orig 为基线替换 api_photo_delete ----
ph = read(PH_ORIG)
OLD = '''@photo_bp.route('/record/<int:rid>', methods=['DELETE'])
def api_photo_delete(rid):
    """删除一条照片记录（不删磁盘文件，避免误删）。"""
    from backend.models import PhotoRecord
    rec = PhotoRecord.query.get(rid)
    if not rec:
        return jsonify({'success': False, 'error': 'not_found'}), 404
    db.session.delete(rec)
    db.session.commit()
    return jsonify({'success': True})'''

NEW = '''@photo_bp.route('/record/<int:rid>', methods=['DELETE'])
def api_photo_delete(rid):
    """删除一条照片记录（不删磁盘文件，避免误删），并清理聊天记录中的照片标记。"""
    from backend.models import PhotoRecord
    from backend import chat_history
    rec = PhotoRecord.query.get(rid)
    if not rec:
        return jsonify({'success': False, 'error': 'not_found'}), 404
    db.session.delete(rec)
    db.session.commit()
    # 同步清理聊天 MD 中指向该照片的 [photo: {"id": X}] 标记，消除 ghost round
    try:
        removed = chat_history.remove_photo_marker(rid)
        if removed:
            logger.info('删除照片 %s 后清理了 %s 个聊天文件中的照片标记', rid, removed)
    except Exception as e:
        logger.warning('清理聊天照片标记失败 rid=%s: %s', rid, e)
    return jsonify({'success': True})'''

assert OLD in ph, 'api_photo_delete 旧块未匹配'
ph_new = ph.replace(OLD, NEW)

# 先落备份区（外部回滚也不会动这里），再落正式路径
write(CH_PATCHED, ch_new)
write(PH_PATCHED, ph_new)
write(CH_LIVE, ch_new)
write(PH_LIVE, ph_new)

print('patched chat_history.py -> %d 行' % ch_new.count('\n'))
print('patched routes/photo.py -> %d 行' % ph_new.count('\n'))
print('备份副本：')
print('  ', CH_PATCHED)
print('  ', PH_PATCHED)
