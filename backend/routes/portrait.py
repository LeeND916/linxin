# -*- coding: utf-8 -*-
"""
ComfyUI 肖像生成 API（AI伴侣系统升级 — 阶段三）

提供角色肖像生成接口，调用 ComfyUI 生成角色当前状态的肖像。
"""

import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, send_from_directory
from backend.models import db
import os

logger = logging.getLogger('sim_life.portrait_api')

portrait_bp = Blueprint('portrait', __name__, url_prefix='/api/portrait')

# ── 发型示例预览图目录（data/character_gen/hair_examples，命名 {女主名}_{发型name}_*.png）──
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HAIR_EXAMPLE_DIR = os.path.join(_BASE_DIR, 'data', 'character_gen', 'hair_examples')


def _hair_example_url(char_name, hair_name):
    """按 角色名+发型名 在发型示例目录匹配预览图，返回 /api/portrait/hair-example/<file> 或空串"""
    if not os.path.isdir(HAIR_EXAMPLE_DIR):
        return ''
    try:
        pngs = [f for f in os.listdir(HAIR_EXAMPLE_DIR) if f.lower().endswith('.png')]
    except OSError:
        return ''
    for f in pngs:                                   # 优先：当前角色专属图
        if f.startswith(f"{char_name}_{hair_name}_"):
            return f"/api/portrait/hair-example/{f}"
    for f in pngs:                                   # 回退：任意含该发型名的图
        if f"_{hair_name}_" in f or f.endswith(f"_{hair_name}.png"):
            return f"/api/portrait/hair-example/{f}"
    return ''


@portrait_bp.route('/generate', methods=['POST'])
def api_portrait_generate():
    """调用 ComfyUI 生成角色肖像
    
    请求体:
    {
        "character_name": "角色名字"（可选，默认使用当前角色）
    }
    
    返回:
    {
        "success": true,
        "data": {
            "image_url": "/api/portrait/image/filename.png",
            "prompt_used": "使用的提示词"
        }
    }
    """
    # ---- ComfyUI 调用日志采集（与 LLM 日志同目录同格式）----
    char_name = ""
    request_info = {}
    response_info = {}
    http_status = 200
    result_json = None

    try:
        from backend.game.character import get_character
        from backend.game.comfyui_client import get_comfyui_client, build_zimage_portrait_prompt, save_portrait_image, ensure_comfyui_running
        from backend.game.llm_utils import write_comfyui_log

        # 获取当前角色
        char = get_character()
        if not char:
            response_info = {'success': False, 'error': 'No character selected'}
            http_status = 400
        else:
            char_name = char.name
            # 强制刷新，确保 current_outfit 等字段为 DB 最新值
            db.session.refresh(char)

            # 确保 ComfyUI 运行（不可用时自动重启）
            if not ensure_comfyui_running():
                response_info = {
                    'success': False,
                    'error': 'ComfyUI 启动失败，请手动打开 ComfyUI 桌面应用',
                    'hint': '默认地址: http://127.0.0.1:8188'
                }
                http_status = 503
            else:
                # 获取 ComfyUI 客户端
                client = get_comfyui_client()

                # 读取请求参数
                # 注：style/pixel_art 已废弃——LoRA 由 character.zimage_lora 决定，
                # 强度恒 1.0，前端仍传该字段也不会生效（保留读取仅为兼容旧前端）。
                data = request.get_json(silent=True) or {}
                prompt = data.get('prompt') or None

                # 构建提示词：prompt 为空时由 _build_character_prompt 自动从 DB 穿搭生成
                workflow = build_zimage_portrait_prompt(char, prompt=prompt)

                # 采集请求信息（按当前工作流自动探测节点，不能硬编码 44/45/41）
                from backend.game.comfyui_client import auto_detect_node_map
                _node_map = auto_detect_node_map(workflow)
                _seed_node = workflow.get(str(_node_map.get('seed')), {})
                _positive_node = workflow.get(str(_node_map.get('positive')), {})
                _latent_node = workflow.get(str(_node_map.get('latent')), {})
                _output_node = workflow.get(str(_node_map.get('output')), {})
                _seed_inputs = _seed_node.get('inputs', {}) or {}
                _latent_inputs = _latent_node.get('inputs', {}) or {}
                _output_inputs = _output_node.get('inputs', {}) or {}
                _lora_inputs = workflow.get(str(_node_map.get('lora')), {}).get('inputs', {}) or {}
                request_info = {
                    'url': f"{client.base_url}/prompt",
                    'character': char.name,
                    'style': _lora_inputs.get('lora_name'),
                    'lora_name': _lora_inputs.get('lora_name'),
                    'positive_prompt': _positive_node.get('inputs', {}).get('text', ''),
                    'width': _latent_inputs.get('width'),
                    'height': _latent_inputs.get('height'),
                    'seed': _seed_inputs.get(_node_map.get('seed_input', 'seed')),
                    'steps': _seed_inputs.get('steps'),
                    'cfg': _seed_inputs.get('cfg'),
                    'sampler_name': _seed_inputs.get('sampler_name'),
                    'scheduler': _seed_inputs.get('scheduler'),
                    # 键名修正：strength -> strength_model（旧键在 LoraLoaderModelOnly 上不存在，
                    # 此前该回显恒为 None）。保留旧键名以兼容前端，新增 lora_* 语义化键。
                    'pixel_art_lora_strength': _lora_inputs.get('strength_model'),
                    'lora_strength': _lora_inputs.get('strength_model'),
                    'filename_prefix': _output_inputs.get('filename_prefix'),
                }

                # 提交生成任务
                t0 = datetime.now()
                prompt_id = client.queue_prompt(workflow)
                if not prompt_id:
                    response_info = {
                        'success': False,
                        'error': '提交任务失败',
                        'prompt_id': prompt_id,
                        'elapsed_ms': int((datetime.now() - t0).total_seconds() * 1000),
                    }
                    http_status = 500
                else:
                    # 等待生成结果
                    outputs = client.wait_for_result(prompt_id, timeout=120)
                    if not outputs:
                        response_info = {
                            'success': False,
                            'error': '生成超时或失败',
                            'prompt_id': prompt_id,
                            'elapsed_ms': int((datetime.now() - t0).total_seconds() * 1000),
                        }
                        http_status = 500
                    else:
                        # 获取生成的图片
                        image_saved = False
                        image_url = None
                        saved_path = None

                        for node_id, output in outputs.items():
                            if 'images' in output:
                                for img_info in output['images']:
                                    filename = img_info.get('filename')
                                    subfolder = img_info.get('subfolder', '')
                                    img_type = img_info.get('type', 'output')

                                    # 下载图片
                                    image_data = client.download_image(filename, subfolder, img_type)
                                    if image_data:
                                        # 保存到本地
                                        saved_path = save_portrait_image(char.name, image_data)
                                        if saved_path:
                                            image_url = f"/api/portrait/image/{os.path.basename(saved_path)}"
                                            image_saved = True
                                            break

                            if image_saved:
                                break

                        if not image_saved:
                            response_info = {
                                'success': False,
                                'error': '未找到生成的图片',
                                'prompt_id': prompt_id,
                                'elapsed_ms': int((datetime.now() - t0).total_seconds() * 1000),
                            }
                            http_status = 500
                        else:
                            response_info = {
                                'success': True,
                                'prompt_id': prompt_id,
                                'image_url': image_url,
                                'saved_path': saved_path,
                                'output_nodes': list(outputs.keys()),
                                'elapsed_ms': int((datetime.now() - t0).total_seconds() * 1000),
                            }
                            result_json = {
                                'success': True,
                                'data': {
                                    'image_url': image_url,
                                    'prompt_used': request_info['positive_prompt'],
                                }
                            }

    except Exception as e:
        logger.error(f"[Portrait API] 生成异常: {e}")
        response_info = {'success': False, 'error': str(e), 'exception': True}
        http_status = 500

    # 统一写出 ComfyUI 调用日志（与 LLM 日志同目录、同文件名格式、同 JSON 结构）
    try:
        from backend.game.llm_utils import write_comfyui_log
        write_comfyui_log('comfyui', char_name, request_info, response_info)
    except Exception:
        pass

    if result_json is not None:
        return jsonify(result_json)
    return jsonify({'success': False, 'error': response_info.get('error', 'unknown error')}), http_status


# ── 角色个性化字段派生（供自定义肖像面板按女主自动带入，解决千人一面）──
def _safe_num(char, attr, default=0.0):
    """安全读取角色数值字段，缺失或非数字时返回 default"""
    try:
        v = getattr(char, attr, None)
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _derive_mood_phrase(char):
    """由角色情绪数值派生中文情绪短语（肖像面板反复诉求的'情绪'落地点）"""
    joy = _safe_num(char, 'joy')
    anger = _safe_num(char, 'anger')
    stress = _safe_num(char, 'stress')
    mood = _safe_num(char, 'mood')  # 主情绪值（Float），仅作派生参考
    if anger >= 60 or stress >= 70:
        return '紧绷焦灼，眉间微蹙，神情中带着防备与紧绷'
    if joy >= 60:
        return '明媚欢脱，眼含笑意，嘴角自然上扬'
    if stress >= 50:
        return '局促不安，指尖微蜷，目光略显游离'
    if mood < 40:
        return '平静柔和，神情略显低沉，带一丝疏离'
    return '平静柔和，神情自然松弛'


# personality_tone / outfit_style 关键词 → 招牌画风（命中前端 PORTRAIT_VOCAB.style.core 的 value）
_SIGNATURE_STYLE_MAP = [
    (('元气', '活泼', '阳光', '甜美', '可爱', '少女', '活力'), '吉卜力手绘（宫崎骏）'),
    (('高冷', '冷艳', '干练', '理性', '精英', '职场'), 'CG游戏角色渲染'),
    (('文静', '内向', '敏感', '温柔', '文艺', '书卷', '知性'), '浮世绘'),
    (('酷', '个性', '前卫', '叛逆'), '赛博朋克'),
    (('优雅', '气质'), '新中式插画'),
    (('清冷', '疏离'), '古典油画写实'),
]


def _derive_signature_style(char):
    """由角色气质基调推导招牌画风；命中失败返回空（不强改画风默认）"""
    tone = (getattr(char, 'personality_tone', '') or '').strip()
    ostyle = (getattr(char, 'outfit_style', '') or '').strip()
    text = f'{tone} {ostyle}'
    for keys, style in _SIGNATURE_STYLE_MAP:
        if any(k in text for k in keys):
            return style
    return ''


def _derive_light_mood(char):
    """由情绪/天气/时刻派生光影建议文本（不强改 chip 默认，作特质卡+自定义框建议）"""
    joy = _safe_num(char, 'joy')
    anger = _safe_num(char, 'anger')
    stress = _safe_num(char, 'stress')
    if anger >= 60 or stress >= 70:
        return '冷调克制、柔和阴影，神情内敛，低对比沉静氛围'
    if joy >= 60:
        return '暖调明亮、窗边自然光，情绪上扬，通透轻盈'
    return '中性柔光、低饱和度，平和自然的光影'


@portrait_bp.route('/options', methods=['GET'])
def api_portrait_options():
    """返回自定义肖像提示词面板所需的全部选项（固定段 + 穿搭/地点/活动列表 + 静态预设）

    返回:
    {
        "success": true,
        "data": {
            "fixed_segments": {"identity_anchor": str, "appearance": str, "temperament": str},
            "outfits": [{"id", "name", "description"}],
            "locations": [{"id": venue_id, "name": venue_name,
                           "activities": [{"id", "name"}]}],
            "presets": {"style": [...], "composition": [...], "lighting": [...], "subtraction": [...]},
            "defaults": {"style", "composition", "outfit", "lighting", "subtraction", "location", "activity"}
        }
    }
    """
    try:
        from backend.game.character import get_character
        from backend.models import OutfitPreset, CharacterActivityMap, OutfitComponent
        from backend.game.activity import get_activities_for_location, get_character_venues
        from backend.game.activity_pose import pose_desc_for_action

        char = get_character()
        if not char:
            return jsonify({'success': False, 'error': 'No character selected'}), 400
        # 强制刷新，确保 current_outfit / location 等字段为 DB 最新值
        db.session.refresh(char)

        # ── 固定段（只读，由角色字段拼装）──
        gender = char.gender or ''
        age = char.age
        age_label = f'{age}岁' if age else ''
        identity_anchor = '·'.join(
            [p for p in [f'{gender}{age_label}', char.identity_label or '', char.major or ''] if p]
        )
        fixed_segments = {
            'identity_anchor': identity_anchor,
            'appearance': char.appearance or '',
            'temperament': char.personality_type or '',
        }

        # ── 穿搭列表（取当前角色全部预设）──
        outfits = []
        current_outfit_name = ''
        try:
            co = char.get_current_outfit()
            current_outfit_name = co.get('name', '') if isinstance(co, dict) else ''
        except Exception:
            pass
        outfit_rows = OutfitPreset.query.filter_by(character_id=char.id).all()
        outfits = [
            {'id': o.id, 'name': o.name, 'description': o.description or ''}
            for o in outfit_rows
        ]

        # ── 内衣 / 配饰组件（取当前角色，供面板独立选择）──
        def _comp_list(t):
            rows = OutfitComponent.query.filter_by(
                character_id=char.id, type=t
            ).order_by(OutfitComponent.sort_order, OutfitComponent.name).all()
            return [{'name': c.name, 'description': c.description or ''} for c in rows]

        underwear = _comp_list('underwear')
        accessory = _comp_list('accessory')

        # ── 发型组件（取共享池 character_id=0，所有人可用）──
        hair_rows = OutfitComponent.query.filter_by(
            character_id=0, type='hairstyle'
        ).order_by(OutfitComponent.sort_order, OutfitComponent.name).all()
        hairstyles = [
            {'name': c.name, 'description': c.description or '',
             'style_tags': c.style_tags or '',
             'image_url': _hair_example_url(char.name, c.name)}
            for c in hair_rows
        ]

        # ── 当前穿搭的内衣 / 配饰默认选中项 ──
        default_underwear = ''
        default_accessory = ''
        try:
            _co = char.get_current_outfit() or {}
            _comps = _co.get('components', {}) or {}
            _uw = _comps.get('underwear')
            if isinstance(_uw, dict):
                default_underwear = _uw.get('name', '') or ''
            _acc = _comps.get('accessory')
            if isinstance(_acc, list) and _acc:
                default_accessory = _acc[0].get('name', '') or ''
            elif isinstance(_acc, dict):
                default_accessory = _acc.get('name', '') or ''
        except Exception:
            pass

        # ── 当前地点 / 活动原始名（供前端按词库匹配默认背景 / 动作）──
        cam_rows = CharacterActivityMap.query.filter_by(character_name=char.name).all()
        current_location = char.location or ''
        current_location_name = ''
        for r in cam_rows:
            if r.venue_id == current_location:
                current_location_name = r.venue_name
                break

        current_activity = ''
        try:
            cur_acts = get_activities_for_location(current_location, character=char)
            if cur_acts:
                current_activity = list(cur_acts.values())[0].get('name', '')
        except Exception:
            pass

        # ── 女主专属活动地点 + 活动（供面板地点/动作段按女主渲染，千人千面）──
        # 取女主可去地点名映射（含角色专属场所覆盖 + LLM 专属地点）
        venues_map = get_character_venues(char)
        # 相关地点集合：以该女主自己的 CharacterActivityMap 地点为准（已全部入库，含完整地图）+ 当前所在地
        relevant = {r.venue_id for r in cam_rows if r.venue_id}
        if char.location:
            relevant.add(char.location)
        locations = []
        for _vid in sorted(relevant):
            _name = venues_map.get(_vid, _vid)
            _acts = get_activities_for_location(_vid, character=char)
            _acts_list = [
                {
                    'id': _aid,
                    'name': (_a.get('name') or _aid),
                    'comfyui_pose': (_a.get('comfyui_pose')
                                    or pose_desc_for_action(_a.get('name') or _aid)),
                }
                for _aid, _a in _acts.items()
            ]
            locations.append({'id': _vid, 'name': _name, 'activities': _acts_list})

        # 注：画风/构图/光影/减法/背景/动作的词库与选项现已移到前端
        #     (frontend/static/js/panels/portrait-presets.js)，后端不再下发预设。
        defaults = {
            'outfit': current_outfit_name or (outfits[0]['name'] if outfits else ''),
            'underwear': default_underwear,
            'accessory': default_accessory,
            'current_location': current_location_name,
            'current_location_id': char.location or '',
            'current_activity': current_activity,
            # 新增：角色个性化字段（供面板顶部特质卡 + 按女主自动带入）
            'personality_tone': (getattr(char, 'personality_tone', '') or ''),
            'signature_style': _derive_signature_style(char),
            'mood_phrase': _derive_mood_phrase(char),
            'light_mood': _derive_light_mood(char),
            'emotion_scores': {
                'mood': _safe_num(char, 'mood'),
                'joy': _safe_num(char, 'joy'),
                'anger': _safe_num(char, 'anger'),
                'happiness': _safe_num(char, 'happiness'),
                'stress': _safe_num(char, 'stress'),
            },
        }

        return jsonify({
            'success': True,
            'data': {
                'fixed_segments': fixed_segments,
                'outfits': outfits,
                'underwear': underwear,
                'accessory': accessory,
                'hairstyles': hairstyles,
                'locations': locations,
                'defaults': defaults,
            }
        })
    except Exception as e:
        logger.error(f"[Portrait API] 获取选项异常: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@portrait_bp.route('/image/<filename>', methods=['GET'])
def api_portrait_image(filename):
    """提供肖像图片访问
    
    参数:
        filename: 图片文件名
        
    返回:
        图片文件
    """
    from backend.game.comfyui_client import OUTPUT_DIR
    
    if not os.path.exists(os.path.join(OUTPUT_DIR, filename)):
        return jsonify({'success': False, 'error': 'Image not found'}), 404
    
    return send_from_directory(OUTPUT_DIR, filename)


@portrait_bp.route('/hair-example/<path:filename>', methods=['GET'])
def api_hair_example(filename):
    """发型示例预览图（来自 data/character_gen/hair_examples，供肖像面板悬停预览）

    参数:
        filename: 图片文件名

    返回:
        图片文件
    """
    if not os.path.exists(os.path.join(HAIR_EXAMPLE_DIR, filename)):
        return jsonify({'success': False, 'error': 'Image not found'}), 404

    return send_from_directory(HAIR_EXAMPLE_DIR, filename)


@portrait_bp.route('/status', methods=['GET'])
def api_portrait_status():
    """获取 ComfyUI 状态
    
    返回:
    {
        "success": true,
        "data": {
            "available": true/false
        }
    }
    """
    try:
        from backend.game.comfyui_client import get_comfyui_client
        
        client = get_comfyui_client()
        available = client.is_available()
        
        return jsonify({
            'success': True,
            'data': {
                'available': available
            }
        })
        
    except Exception as e:
        logger.error(f"[Portrait API] 获取状态异常: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@portrait_bp.route('/latest', methods=['GET'])
def api_portrait_latest():
    """获取当前角色最近一次生成的肖像
    
    返回:
    {
        "success": true,
        "data": {
            "image_url": "/api/portrait/image/xxx.png" 或 null（没有历史肖像时）
        }
    }
    """
    try:
        from backend.game.character import get_character
        from backend.game.comfyui_client import OUTPUT_DIR
        
        char = get_character()
        if not char:
            return jsonify({'success': False, 'error': 'No character selected'}), 400
        
        # 扫描 OUTPUT_DIR 下以角色名开头的图片，找修改时间最新的
        latest_path = None
        latest_mtime = 0
        prefix = f"{char.name}_"
        if os.path.isdir(OUTPUT_DIR):
            for fname in os.listdir(OUTPUT_DIR):
                if fname.startswith(prefix) and fname.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    fpath = os.path.join(OUTPUT_DIR, fname)
                    mtime = os.path.getmtime(fpath)
                    if mtime > latest_mtime:
                        latest_mtime = mtime
                        latest_path = fname
        
        image_url = f"/api/portrait/image/{latest_path}" if latest_path else None
        
        return jsonify({
            'success': True,
            'data': {'image_url': image_url}
        })
        
    except Exception as e:
        logger.error(f"[Portrait API] 获取最新肖像异常: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


