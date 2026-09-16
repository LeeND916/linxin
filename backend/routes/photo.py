# -*- coding: utf-8 -*-
"""聊天生图 / 视觉回看 路由层（P3 收口 + P5 VLM 配置）。

两个蓝图：
  photo_bp  (/api/photo)      — 照片记录检索、加号手动触发、收藏/删除
  vlm_bp    (/api/vlm-config)  — 视觉回看（VLM）配置 CRUD + 连通性测试 + provider 预设

玩家侧 / 女主回复侧的自动意图触发不在此处——它挂在 routes/api.py 的 /dialogue 端点，
本文件只暴露检索与手动触发能力。
"""
import logging
from flask import Blueprint, request, jsonify

from backend.models import db, VLMConfig
from backend.game.character import get_character
from backend.game import photo_gen

logger = logging.getLogger('sim_life.photo_api')

photo_bp = Blueprint('photo', __name__, url_prefix='/api/photo')
vlm_bp = Blueprint('vlm_config', __name__, url_prefix='/api/vlm-config')


# ══════════════════════════════════════════════════════════════
# provider 预设（前端切换 provider 自动回填 URL/模型）
# ══════════════════════════════════════════════════════════════
VLM_PROVIDER_PRESETS = {
    'lm_studio': {'label': 'LM Studio', 'api_url': 'http://localhost:10039/v1/chat/completions',
                  'model_name': 'nsfwvision-qwen3-vl-8b-v3'},
    'ollama':    {'label': 'Ollama',    'api_url': 'http://localhost:11434/v1/chat/completions',
                  'model_name': 'qwen2.5-vl'},
    'vllm':      {'label': 'vLLM',      'api_url': 'http://localhost:8000/v1/chat/completions',
                  'model_name': 'Qwen2.5-VL-7B-Instruct'},
    'openai':    {'label': 'OpenAI 兼容', 'api_url': 'https://api.openai.com/v1/chat/completions',
                  'model_name': 'gpt-4o'},
    'custom':    {'label': '自定义',     'api_url': '', 'model_name': ''},
}

# 手动触发允许的场景（加号菜单）
_MANUAL_SCENES = {'photo_take', 'selfie', 'activity_shot', 'outfit_change',
                  'scene_freeze', 'gift_photo'}


# ══════════════════════════════════════════════════════════════
# 照片记录
# ══════════════════════════════════════════════════════════════
@photo_bp.route('/records', methods=['GET'])
def api_photo_records():
    """列出某女主的照片（默认当前角色，只列已完成）。"""
    name = request.args.get('character') or ''
    if not name:
        char = get_character()
        name = char.name if char else ''
    if not name:
        return jsonify({'success': True, 'records': []})
    limit = int(request.args.get('limit', 50) or 50)
    only_done = request.args.get('all') != '1'
    records = photo_gen.get_photo_records(name, limit=limit, only_done=only_done)
    return jsonify({'success': True, 'records': records})


@photo_bp.route('/outfits', methods=['GET'])
def api_photo_outfits():
    """返回当前角色全部穿搭预设（outfit_presets），供「换装展示」下拉选择。

    每条 {id, name, description, season, occasion}，与角色肖像「自定义穿搭」同源。
    """
    char = get_character()
    if not char:
        return jsonify({'success': True, 'outfits': []})
    from backend.models import OutfitPreset, OutfitComponent
    rows = OutfitPreset.query.filter_by(character_id=char.id).order_by(
        OutfitPreset.occasion, OutfitPreset.season, OutfitPreset.name
    ).all()
    outfits = [{
        'id': o.id, 'name': o.name, 'description': o.description or '',
        'season': o.season or '', 'occasion': o.occasion or '', 'kind': 'preset',
    } for o in rows]
    underwear = OutfitComponent.query.filter_by(
        character_id=char.id, type='underwear'
    ).order_by(OutfitComponent.sort_order, OutfitComponent.name).all()
    outfits.extend({
        'id': o.id, 'name': o.name, 'description': o.description or o.name,
        'season': '', 'occasion': 'home', 'kind': 'underwear',
    } for o in underwear)
    return jsonify({'success': True, 'outfits': outfits})


@photo_bp.route('/record/<int:rid>', methods=['GET'])
def api_photo_record(rid):
    """单条照片详情（前端轮询 pending→done 用）。"""
    rec = photo_gen.get_photo_record(rid)
    char = get_character()
    if not rec or not char or rec.get('character_name') != char.name:
        return jsonify({'success': False, 'error': 'not_found'}), 404
    return jsonify({'success': True, 'record': rec})


@photo_bp.route('/generate', methods=['POST'])
def api_photo_generate():
    """加号菜单手动触发：body {scene_type, gift?, outfit_name?}。

    仍走 evaluate_media_intent 的四道闸（状态/并发/冷却/每日上限），
    scene_type 直接指定（scene_override），gift/outfit 从 body 覆盖。
    """
    data = request.get_json(silent=True) or {}
    scene_type = (data.get('scene_type') or '').strip()
    if scene_type not in _MANUAL_SCENES:
        return jsonify({'success': False, 'error': f'invalid scene_type: {scene_type}'}), 400

    char = get_character()
    if not char:
        return jsonify({'success': False, 'error': 'no_character'}), 400

    from backend.game.media_intent import evaluate_media_intent
    game_day = int(data.get('game_day', getattr(char, 'game_day', 0)) or 0)
    game_minute = int(data.get('game_hour', 0) or 0) * 60 + int(data.get('game_minute', 0) or 0)

    decision = evaluate_media_intent(char, text='', side='player',
                                     scene_override=scene_type,
                                     game_day=game_day, game_minute=game_minute)
    if not decision.get('triggered'):
        return jsonify({'success': False, 'error': decision.get('reason') or 'not_triggered',
                        'reject_context': decision.get('reject_context')}), 200

    # 手动指定的参数覆盖
    if data.get('gift'):
        decision['gift'] = data['gift']
    if data.get('outfit_name'):
        decision['outfit_name'] = data['outfit_name']

    # 明确换装：按真实角色衣柜解析；普通穿搭整套替换，内衣/部件局部替换（只换对应槽位）。
    if scene_type == 'outfit_change':
        from backend.game.wardrobe import apply_outfit, resolve_outfit_request
        from backend.game.photo_gen import _outfit_desc
        from backend.models import OutfitComponent
        # apply_outfit 会立刻覆盖角色当前穿搭；先保留旧穿搭供对比图左侧使用。
        decision['outfit_old'] = _outfit_desc(char)
        outfit_name = decision.get('outfit_name')
        outfit = resolve_outfit_request(char, outfit_name, data.get('activity_text', ''))
        if not outfit:
            return jsonify({'success': False, 'error': 'no_outfit_candidate',
                            'reject_context': '当前没有找到适合这次换装的角色专属穿搭'}), 200
        # 是否换的是内衣：局部替换且部件类型为 underwear → 右侧露出内衣。
        reveal = False
        new_underwear_name = ''
        if outfit_name:
            comp = OutfitComponent.query.filter_by(
                character_id=char.id, name=outfit_name).first()
            if comp and comp.type == 'underwear':
                reveal = True
                new_underwear_name = comp.name
        outfit = apply_outfit(char, outfit, reason='photo_outfit_change')
        decision['outfit_name'] = outfit.get('name', '')
        decision['outfit_data'] = outfit
        # 换装后文案：内衣替换 → 右侧脱去外衣、仅身穿新内衣；其它 → 用当前新穿搭。
        if reveal:
            decision['outfit_new'] = f'脱去上衣下装与外套，仅身穿{new_underwear_name}展示贴身内衣'
        else:
            decision['outfit_new'] = _outfit_desc(char)

    # 前端手动拍照只上报 game_day/game_hour/game_minute；character 表无 game_time 列，
    # getattr(char, 'game_time', '') 恒为 ''，会导致照片记录与聊天时间行缺时分秒。
    # 由已算好的 game_minute 派生 'HH:MM:SS'，保证相册与聊天同源、可按秒归位。
    game_time = data.get('game_time') or ('%02d:%02d:00' % (game_minute // 60, game_minute % 60))
    rec = photo_gen.trigger_photo_generation(
        char, decision,
        user_message=data.get('user_message', ''),
        recent_dialogue='',
        game_day=game_day,
        game_time=game_time,
        game_minute=game_minute,
    )
    if not rec:
        return jsonify({'success': False, 'error': 'trigger_failed'}), 200

    # 手动拍照也持久化到聊天记录：刷新后对话流仍能还原照片卡片
    try:
        from backend import chat_history
        chat_history.append_message(
            'character', '',
            photo={'id': rec.get('id')},
            game_time_str=f"第{game_day}天 {game_time}"
        )
    except Exception as e:
        logger.warning('手动拍照聊天记录写入失败: %s', e)

    return jsonify({'success': True, 'record': rec})


@photo_bp.route('/record/<int:rid>/favorite', methods=['POST'])
def api_photo_favorite(rid):
    """收藏/取消收藏（收藏则不参与淘汰）。"""
    from backend.models import PhotoRecord
    rec = PhotoRecord.query.get(rid)
    if not rec:
        return jsonify({'success': False, 'error': 'not_found'}), 404
    rec.is_favorite = not rec.is_favorite
    db.session.commit()
    return jsonify({'success': True, 'is_favorite': rec.is_favorite})


@photo_bp.route('/record/<int:rid>', methods=['DELETE'])
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
    return jsonify({'success': True})


@photo_bp.route('/vocab', methods=['GET'])
def api_photo_vocab():
    """暴露后端写实风词库（get_realistic_vocab），供前端替代 hardcode 动漫词库。"""
    from backend.game.photo_presets import get_realistic_vocab
    return jsonify({'success': True, 'vocab': get_realistic_vocab()})


@photo_bp.route('/auto-node-map', methods=['POST'])
def api_photo_auto_node_map():
    """自动探测默认工作流的节点映射并持久化到 ComfyUIWorkflow.node_map（P2-E）。"""
    from backend.models import ComfyUIWorkflow, db
    from backend.game.comfyui_client import load_workflow, auto_detect_node_map
    row = ComfyUIWorkflow.query.filter_by(is_default=True).first() or ComfyUIWorkflow.query.first()
    if not row:
        return jsonify({'success': False, 'error': 'no_workflow'}), 404
    if not row.workflow_path:
        return jsonify({'success': False, 'error': 'no_workflow_path'}), 200
    try:
        wf = load_workflow(row.workflow_path)
    except Exception as e:
        return jsonify({'success': False, 'error': f'load_failed: {str(e)[:120]}'}), 200
    detected = auto_detect_node_map(wf)
    if not detected:
        return jsonify({'success': False, 'error': 'detect_failed'}), 200
    row.node_map = json.dumps(detected, ensure_ascii=False)
    db.session.commit()
    return jsonify({'success': True, 'node_map': detected})


@photo_bp.route('/record/<int:rid>/recaption', methods=['POST'])
def api_photo_recaption(rid):
    """手动 VLM 重新回看：重算 vlm_caption（不新增记忆，避免重复）。"""
    from backend.models import PhotoRecord, Character
    from backend.game.photo_gen import (
        vlm_caption_image, get_photo_record, cosine_similarity,
    )
    from backend.game.memory import _compute_memory_embedding
    rec = PhotoRecord.query.get(rid)
    if not rec:
        return jsonify({'success': False, 'error': 'not_found'}), 404
    if rec.status != 'done' or not rec.image_path:
        return jsonify({'success': False, 'error': 'not_done'}), 200
    char = Character.query.filter_by(name=rec.character_name).first()
    ctx = {
        'char_name': rec.character_name,
        'location': getattr(char, 'location', '') or '',
        'game_day': rec.source_day,
        'game_time': rec.source_time,
    }
    try:
        caption = vlm_caption_image(rec.image_path, ctx)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:200]}), 200
    if not caption:
        return jsonify({'success': False, 'error': 'vlm_failed'}), 200
    rec.vlm_caption = caption
    try:
        sim = cosine_similarity(
            _compute_memory_embedding(rec.scene_memo or ''),
            _compute_memory_embedding(caption))
        rec.memo_similarity = round(sim, 4)
    except Exception:
        pass
    db.session.commit()
    return jsonify({'success': True, 'record': get_photo_record(rid)})




# ══════════════════════════════════════════════════════════════
# VLM 视觉回看配置
# ══════════════════════════════════════════════════════════════
@vlm_bp.route('/presets', methods=['GET'])
def api_vlm_presets():
    return jsonify({'success': True, 'presets': VLM_PROVIDER_PRESETS})


@vlm_bp.route('', methods=['GET'])
@vlm_bp.route('/', methods=['GET'])
def api_vlm_get():
    """返回当前 VLM 配置（单行，脱敏 api_key）。无则返回默认占位。"""
    cfg = VLMConfig.query.filter_by(is_active=True).first() or VLMConfig.query.first()
    if not cfg:
        preset = VLM_PROVIDER_PRESETS['lm_studio']
        return jsonify({'success': True, 'config': {
            'id': None, 'provider': 'lm_studio', 'api_url': preset['api_url'],
            'api_key': '', 'model_name': preset['model_name'], 'max_tokens': 200,
            'temperature': 0.2, 'timeout': 60, 'is_active': False,
        }})
    return jsonify({'success': True, 'config': cfg.to_dict(mask_key=True)})


@vlm_bp.route('', methods=['POST'])
@vlm_bp.route('/', methods=['POST'])
def api_vlm_save():
    """新建/更新 VLM 配置（单行 upsert）。空 api_key 不覆盖原值。"""
    data = request.get_json(silent=True) or {}
    cfg = VLMConfig.query.filter_by(is_active=True).first() or VLMConfig.query.first()
    if not cfg:
        cfg = VLMConfig()
        db.session.add(cfg)

    if 'provider' in data:
        cfg.provider = data['provider'] or 'lm_studio'
    if 'api_url' in data:
        cfg.api_url = data['api_url'] or cfg.api_url
    if data.get('api_key'):  # 空则保留原 key（前端回显是脱敏值）
        cfg.api_key = data['api_key']
    if 'model_name' in data:
        cfg.model_name = data['model_name'] or cfg.model_name
    if 'max_tokens' in data:
        cfg.max_tokens = int(data['max_tokens'] or 200)
    if 'temperature' in data:
        cfg.temperature = float(data['temperature'] if data['temperature'] is not None else 0.2)
    if 'timeout' in data:
        cfg.timeout = int(data['timeout'] or 60)
    cfg.is_active = bool(data.get('is_active', True))
    db.session.commit()
    return jsonify({'success': True, 'config': cfg.to_dict(mask_key=True)})


@vlm_bp.route('/test', methods=['POST'])
def api_vlm_test():
    """连通性测试：向 VLM 端点发一条极小文本对话，验证可达 + 模型可用。

    body 可带临时参数（api_url/api_key/model_name），否则用已存配置。
    """
    data = request.get_json(silent=True) or {}
    cfg = VLMConfig.query.filter_by(is_active=True).first() or VLMConfig.query.first()
    api_url = data.get('api_url') or (cfg.api_url if cfg else '')
    model = data.get('model_name') or (cfg.model_name if cfg else '')
    # 测试用 key：body 明文优先，否则用库里真实 key（非脱敏）
    api_key = data.get('api_key') or (cfg.api_key if cfg else '')
    timeout = int(data.get('timeout') or (cfg.timeout if cfg else 30))
    if not api_url or not model:
        return jsonify({'success': False, 'error': 'api_url / model_name 不能为空'}), 200

    try:
        import requests
        headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
        payload = {
            'model': model,
            'max_tokens': 8,
            'temperature': 0,
            'messages': [{'role': 'user', 'content': '你好，请只回复：ok'}],
        }
        r = requests.post(api_url, json=payload, headers=headers, timeout=min(timeout, 30))
        r.raise_for_status()
        content = r.json().get('choices', [{}])[0].get('message', {}).get('content', '')
        return jsonify({'success': True, 'reply': (content or '').strip()[:80],
                        'model': model})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)[:200]}), 200
