# -*- coding: utf-8 -*-
"""
TTS 语音合成 API（腾讯云 TTS）

  - POST /api/tts/generate          — 生成语音
  - GET  /api/tts/status             — 获取 TTS 引擎状态

  服务管理：
  - GET    /api/tts/services         — 返回所有 TTS 服务配置列表
  - POST   /api/tts/services         — 新增 TTS 服务配置
  - PUT    /api/tts/services/<id>    — 更新指定 TTS 服务配置
  - POST   /api/tts/services/<id>/activate — 激活指定服务
  - POST   /api/tts/services/<id>/test      — 测试服务连接

  角色管理：
  - GET    /api/tts/characters           — 返回所有活跃角色
  - PUT    /api/tts/characters/<name>/tencent-voice — 更新角色腾讯云音色
  - GET    /api/tts/tencent-voices       — 获取腾讯云音色列表
"""

import logging
import os
import re
import sqlite3
from datetime import datetime
from flask import Blueprint, request, Response, jsonify
from backend.config import USER_SIM_LIFE

logger = logging.getLogger('sim_life.tts_api')

tts_bp = Blueprint('tts', __name__, url_prefix='/api/tts')

# 数据库路径（与 config.py 保持一致）
_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data', 'game.db'
)

# 项目根目录（用于构建数据目录路径）
GAME_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _get_db():
    """获取 sqlite3 连接（直接操作，与 tts_manager 风格一致）。"""
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ═══════════════════════════════════════════
#  TTS 语音合成
# ═══════════════════════════════════════════

@tts_bp.route('/generate', methods=['POST'])
def api_tts_generate():
    """TTS 语音生成（腾讯云 TTS）

    请求体:
    {
        "character_name": "角色名字",
        "text": "女主的回复内容（纯文本）",
        "dialogue_history": [
            {"role": "user", "content": "..."},
            {"role": "assistant", "content": "..."}
        ]
    }

    返回:
        audio/wav 音频数据（成功）
        JSON {"success": false, "error": "..."}（失败）
    """
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No JSON data provided'}), 400

    text = data.get('text', '')
    character_name = data.get('character_name', '')
    dialogue_history = data.get('dialogue_history', [])

    if not text:
        return jsonify({'success': False, 'error': 'No text provided'}), 400
    if not character_name:
        return jsonify({'success': False, 'error': 'No character_name provided'}), 400

    try:
        from backend.game.tts_manager import get_tts_manager

        tts_manager = get_tts_manager()

        audio_data = tts_manager.synthesize(
            character_name=character_name,
            text=text,
            dialogue_history=dialogue_history,
        )

        if audio_data is None:
            return jsonify({
                'success': False,
                'error': '语音合成失败，请检查 TTS 服务是否可用'
            }), 500

        return Response(audio_data, mimetype='audio/wav')

    except Exception as e:
        logger.error(f"[TTS API] 语音合成异常: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@tts_bp.route('/status', methods=['GET'])
def api_tts_status():
    """获取 TTS 引擎状态。"""
    try:
        from backend.game.tts_manager import get_tts_manager
        tts_manager = get_tts_manager()
        status = tts_manager.get_status()
        return jsonify({'success': True, 'data': status})
    except Exception as e:
        logger.error(f"[TTS API] 获取状态异常: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════
#  TTS 服务配置管理
# ═══════════════════════════════════════════

@tts_bp.route('/services', methods=['GET'])
def api_tts_list_services():
    """获取所有 TTS 服务配置列表。"""
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT id, service_type, service_name, api_url, api_token, model_path, "
            "tencent_secret_id, tencent_secret_key, tencent_app_id, "
            "is_active, created_at, updated_at "
            "FROM tts_service_config ORDER BY is_active DESC, id ASC"
        ).fetchall()
        conn.close()

        services = []
        for r in rows:
            sid = r['tencent_secret_id'] or ''
            skey = r['tencent_secret_key'] or ''
            services.append({
                'id': r['id'],
                'service_type': r['service_type'],
                'service_name': r['service_name'],
                'api_url': r['api_url'],
                'api_token': (r['api_token'][:8] + '***' if r['api_token'] else ''),
                'model_path': r['model_path'],
                'tencent_secret_id': f"{sid[:4]}****{sid[-4:]}" if len(sid) > 8 else sid,
                'tencent_secret_key_masked': True if skey else False,
                'tencent_app_id': r['tencent_app_id'],
                'is_active': bool(r['is_active']),
                'created_at': r['created_at'],
                'updated_at': r['updated_at'],
            })

        return jsonify({'success': True, 'data': services})
    except Exception as e:
        logger.error(f"[TTS API] 获取服务列表异常: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@tts_bp.route('/services', methods=['POST'])
def api_tts_create_service():
    """新增 TTS 服务配置。"""
    data = request.get_json() or {}
    service_type = data.get('service_type', '')
    service_name = data.get('service_name', '')
    api_url = data.get('api_url', '')
    api_token = data.get('api_token', '')
    model_path = data.get('model_path', '')
    is_active = data.get('is_active', False)
    tencent_secret_id = data.get('tencent_secret_id', '')
    tencent_secret_key = data.get('tencent_secret_key', '')
    tencent_app_id = data.get('tencent_app_id', '')

    if not service_type or not service_name:
        return jsonify({'success': False, 'error': '缺少必填字段 (service_type, service_name)'}), 400

    # 腾讯云 TTS 需要凭据
    if service_type == 'tencent_tts':
        if not tencent_secret_id or not tencent_secret_key:
            return jsonify({'success': False, 'error': '腾讯云 TTS 需要 secret_id 和 secret_key'}), 400
    else:
        if not api_url or not model_path:
            return jsonify({'success': False, 'error': '缺少必填字段 (api_url, model_path)'}), 400

    try:
        conn = _get_db()
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if is_active:
            conn.execute("UPDATE tts_service_config SET is_active = 0")

        cur = conn.execute(
            "INSERT INTO tts_service_config "
            "(service_type, service_name, api_url, api_token, model_path, "
            "tencent_secret_id, tencent_secret_key, tencent_app_id, "
            "is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (service_type, service_name, api_url, api_token, model_path,
             tencent_secret_id, tencent_secret_key, tencent_app_id,
             int(is_active), now, now)
        )
        new_id = cur.lastrowid
        conn.commit()
        conn.close()

        logger.info(f"[TTS API] 新增服务配置 id={new_id}, type={service_type}")
        return jsonify({'success': True, 'data': {'id': new_id}})
    except Exception as e:
        logger.error(f"[TTS API] 新增服务配置异常: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@tts_bp.route('/services/<int:service_id>', methods=['PUT'])
def api_tts_update_service(service_id):
    """更新指定 TTS 服务配置。"""
    data = request.get_json() or {}

    try:
        conn = _get_db()
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 构建动态 UPDATE
        fields = []
        values = []
        for key in ['service_name', 'api_url', 'api_token', 'model_path', 'service_type',
                     'tencent_secret_id', 'tencent_secret_key', 'tencent_app_id']:
            if key in data:
                fields.append(f"{key} = ?")
                values.append(data[key])

        if 'is_active' in data:
            val = int(data['is_active'])
            fields.append("is_active = ?")
            values.append(val)
            # 如果激活，清除所有其他服务的激活（确保只有一个激活）
            if val:
                conn.execute(
                    "UPDATE tts_service_config SET is_active = 0 WHERE id != ?",
                    (service_id,)
                )

        if not fields:
            conn.close()
            return jsonify({'success': False, 'error': '未提供任何更新字段'}), 400

        fields.append("updated_at = ?")
        values.append(now)
        values.append(service_id)

        conn.execute(
            f"UPDATE tts_service_config SET {', '.join(fields)} WHERE id = ?",
            values
        )
        conn.commit()
        conn.close()

        logger.info(f"[TTS API] 更新服务配置 id={service_id}")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"[TTS API] 更新服务配置异常: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@tts_bp.route('/services/<int:service_id>/activate', methods=['POST'])
def api_tts_activate_service(service_id):
    """激活指定 TTS 服务（同时取消其他所有服务的激活状态）。"""
    try:
        conn = _get_db()
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 查询服务是否存在
        svc = conn.execute("SELECT id FROM tts_service_config WHERE id = ?", (service_id,)).fetchone()
        if not svc:
            conn.close()
            return jsonify({'success': False, 'error': '服务不存在'}), 404

        # 取消所有服务的激活状态（确保只有一个服务激活）
        conn.execute("UPDATE tts_service_config SET is_active = 0, updated_at = ?", (now,))
        # 激活当前
        conn.execute("UPDATE tts_service_config SET is_active = 1, updated_at = ? WHERE id = ?", (now, service_id))
        conn.commit()
        conn.close()

        logger.info(f"[TTS API] 激活服务 id={service_id}")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"[TTS API] 激活服务异常: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@tts_bp.route('/services/<int:service_id>', methods=['DELETE'])
def api_tts_delete_service(service_id):
    """删除指定 TTS 服务配置。"""
    try:
        conn = _get_db()
        conn.execute("DELETE FROM tts_service_config WHERE id = ?", (service_id,))
        conn.commit()
        conn.close()
        logger.info(f"[TTS API] 删除服务配置 id={service_id}")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"[TTS API] 删除服务配置异常: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@tts_bp.route('/services/<int:service_id>/test', methods=['POST'])
def api_tts_test_service(service_id):
    """测试 TTS 服务连接。"""
    try:
        import requests as req

        conn = _get_db()
        svc = conn.execute(
            "SELECT service_type, api_url, tencent_secret_id, tencent_secret_key "
            "FROM tts_service_config WHERE id = ?", (service_id,)
        ).fetchone()
        conn.close()

        if not svc:
            return jsonify({'success': False, 'error': '服务不存在'}), 404

        service_type = svc['service_type']

        if service_type == 'tencent_tts':
            # 腾讯云 TTS：尝试用凭据初始化引擎
            try:
                sid = svc['tencent_secret_id'] or ''
                skey = svc['tencent_secret_key'] or ''
                if not sid or not skey:
                    return jsonify({'success': False, 'error': '未配置腾讯云凭据'}), 503
                from backend.game.tencent_tts_engine import TencentTTSEngine
                engine = TencentTTSEngine(sid, skey)
                # 发送一个极短的合成请求来验证连通性
                engine.synthesize("测试", 101001)
                return jsonify({'success': True, 'data': {'status': 'ok', 'message': '腾讯云 TTS 连接正常'}})
            except Exception as e:
                return jsonify({'success': False, 'error': f'腾讯云连接失败: {str(e)}'}), 503

        api_url = svc['api_url'].rstrip('/')
        resp = req.get(f"{api_url}/health", timeout=5)

        if resp.status_code == 200:
            return jsonify({'success': True, 'data': {'status': 'ok', 'message': '服务连接正常'}})
        else:
            return jsonify({'success': False, 'error': f'HTTP {resp.status_code}，请确认服务已启动'}), 503
    except Exception as e:
        return jsonify({'success': False, 'error': f'无法连接服务 ({str(e)})，请确认服务已启动'}), 503


# ═══════════════════════════════════════════
#  角色 character_base 管理
# ═══════════════════════════════════════════

@tts_bp.route('/characters', methods=['GET'])
def api_tts_list_characters():
    """返回所有活跃角色。"""
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT id, name, character_base, tencent_tts_voice_type "
            "FROM character WHERE is_active = 1 ORDER BY id"
        ).fetchall()
        conn.close()

        chars = [{
            'id': r['id'], 'name': r['name'],
            'character_base': r['character_base'],
            'tencent_tts_voice_type': r['tencent_tts_voice_type'],
        } for r in rows]
        return jsonify({'success': True, 'data': chars})
    except Exception as e:
        logger.error(f"[TTS API] 获取角色列表异常: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@tts_bp.route('/characters/<int:char_id>/base', methods=['PUT'])
def api_tts_update_character_base(char_id):
    """更新角色的 character_base（参考语音文本/路径）。"""
    data = request.get_json() or {}
    character_base = data.get('character_base', '')

    try:
        conn = _get_db()
        conn.execute(
            "UPDATE character SET character_base = ?, updated_at = ? WHERE id = ?",
            (character_base, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), char_id)
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"[TTS API] 更新 character_base 异常: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@tts_bp.route('/characters/<string:char_name>/tencent-voice', methods=['PUT'])
def api_tts_update_tencent_voice(char_name):
    """更新指定角色的 tencent_tts_voice_type。"""
    data = request.get_json() or {}
    voice_type = data.get('tencent_tts_voice_type', '')

    try:
        conn = _get_db()
        conn.execute(
            "UPDATE character SET tencent_tts_voice_type = ? WHERE name = ?",
            (voice_type or None, char_name)
        )
        conn.commit()
        conn.close()

        logger.info(f"[TTS API] 更新角色 {char_name} 的 tencent_tts_voice_type = {voice_type}")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"[TTS API] 更新 tencent_tts_voice_type 异常：{e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ═══════════════════════════════════════════
#  角色语音样本管理
# ═══════════════════════════════════════════

_VOICE_SAMPLES_DIR = os.path.join(USER_SIM_LIFE, 'qwen3-tts_voice_samples')


@tts_bp.route('/characters/<int:char_id>/samples', methods=['POST'])
def api_tts_upload_voice_sample(char_id):
    """上传角色的语音样本（Base 模式参考音频）。"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '未上传文件'}), 400

        file = request.files['file']
        if not file.filename:
            return jsonify({'success': False, 'error': '空文件名'}), 400

        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ('.wav', '.mp3', '.flac', '.ogg'):
            return jsonify({'success': False, 'error': f'不支持的文件格式 {ext}，仅支持 wav/mp3/flac/ogg'}), 400

        conn = _get_db()
        char_row = conn.execute("SELECT name FROM character WHERE id = ?", (char_id,)).fetchone()
        if not char_row:
            conn.close()
            return jsonify({'success': False, 'error': '角色不存在'}), 404

        char_name = char_row['name']
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', char_name)
        char_dir = os.path.join(_VOICE_SAMPLES_DIR, safe_name)
        os.makedirs(char_dir, exist_ok=True)

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{ts}{ext}"
        filepath = os.path.join(char_dir, filename)
        file.save(filepath)

        # 存入 DB 时使用相对路径（相对于 USER_SIM_LIFE），便于换电脑迁移
        relative_path = os.path.relpath(filepath, USER_SIM_LIFE)
        sample_text = request.form.get('sample_text', '')
        conn.execute(
            "INSERT INTO character_voice_samples (character_id, filename, sample_text, file_path, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (char_id, filename, sample_text, relative_path, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        conn.commit()
        conn.close()

        return jsonify({'success': True, 'data': {'file_path': filepath}})
    except Exception as e:
        logger.error(f"[TTS API] 上传语音样本异常: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@tts_bp.route('/characters/<int:char_id>/samples', methods=['GET'])
def api_tts_list_voice_samples(char_id):
    """获取角色的所有语音样本。"""
    try:
        conn = _get_db()
        rows = conn.execute(
            "SELECT id, filename, sample_text, file_path, created_at FROM character_voice_samples "
            "WHERE character_id = ? ORDER BY created_at DESC",
            (char_id,)
        ).fetchall()
        conn.close()

        samples = [{
            'id': r['id'],
            'filename': r['filename'],
            'sample_text': r['sample_text'],
            'file_path': os.path.join(USER_SIM_LIFE, r['file_path']) if r['file_path'] else '',
            'created_at': r['created_at'],
        } for r in rows]
        return jsonify({'success': True, 'data': samples})
    except Exception as e:
        logger.error(f"[TTS API] 获取语音样本列表异常: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@tts_bp.route('/samples/<int:sample_id>', methods=['DELETE'])
def api_tts_delete_voice_sample(sample_id):
    """删除指定语音样本。"""
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT file_path FROM character_voice_samples WHERE id = ?",
            (sample_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'error': '样本不存在'}), 404

        file_path = os.path.join(USER_SIM_LIFE, row['file_path'])
        if os.path.exists(file_path):
            os.remove(file_path)

        conn.execute("DELETE FROM character_voice_samples WHERE id = ?", (sample_id,))
        conn.commit()
        conn.close()

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"[TTS API] 删除语音样本异常: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@tts_bp.route('/tencent-voices', methods=['GET'])
def api_tts_tencent_voices():
    """获取腾讯云 TTS 音色列表（动态拉取）
    
    注：腾讯云 TTS API 暂无直接查询音色列表的接口，
    此处返回预定义的完整音色列表（基于官方文档）。
    未来如腾讯云提供音色查询 API，可改为动态拉取。
    """
    # 腾讯云 TTS 完整音色列表（基于官方文档）
    voices = [
        # 精品音色（VoiceType 101xxx）- 性价比首选
        {"id": "101001", "name": "智瑜", "type": "精品", "gender": "女声", "emotion": False},
        {"id": "101002", "name": "智聆", "type": "精品", "gender": "女声", "emotion": False},
        {"id": "101003", "name": "智美", "type": "精品", "gender": "女声", "emotion": False},
        {"id": "101004", "name": "智云", "type": "精品", "gender": "男声", "emotion": False},
        {"id": "101005", "name": "智莉", "type": "精品", "gender": "女声", "emotion": False},
        {"id": "101008", "name": "智琪", "type": "精品", "gender": "女声", "emotion": False},
        {"id": "101009", "name": "智芸", "type": "精品", "gender": "女声", "emotion": False},
        {"id": "101010", "name": "智岚", "type": "精品", "gender": "正太", "emotion": False},
        {"id": "101026", "name": "智希", "type": "精品", "gender": "女声", "emotion": False},
        
        # 大模型音色（VoiceType 501xxx/601xxx）- 高拟真
        {"id": "501002", "name": "智菊", "type": "大模型", "gender": "女声", "emotion": False},
        {"id": "501004", "name": "月华", "type": "大模型", "gender": "女声", "emotion": False},
        
        # 大模型多情感音色（VoiceType 601xxx）- 支持 9 种情感 ⭐
        {"id": "601008", "name": "智瑾", "type": "大模型多情感", "gender": "女声", "emotion": True},
        {"id": "601009", "name": "爱小芊", "type": "大模型多情感", "gender": "女声", "emotion": True},
        {"id": "601010", "name": "爱小娇", "type": "大模型多情感", "gender": "女声", "emotion": True},
        {"id": "601012", "name": "爱小璟", "type": "大模型多情感", "gender": "女声", "emotion": True},
        {"id": "601013", "name": "爱小伊", "type": "大模型多情感", "gender": "女声", "emotion": True},
        
        # 超自然大模型音色（VoiceType 502xxx/602xxx/603xxx）- 极高拟真
        {"id": "502001", "name": "智小柔", "type": "超自然大模型", "gender": "女声", "emotion": False},
        {"id": "502003", "name": "智小敏", "type": "超自然大模型", "gender": "女声", "emotion": False},
        {"id": "603004", "name": "温柔小柠", "type": "超自然大模型", "gender": "女声", "emotion": False},
        {"id": "603007", "name": "邻家女孩", "type": "超自然大模型", "gender": "女声", "emotion": False},
    ]
    
    return jsonify({'success': True, 'data': voices})
