# -*- coding: utf-8 -*-
"""人物肖像实验室 —— Flask 蓝图 lab_bp

路由：
  GET  /api/lab/characters           角色列表（含基线体检 status / issues）
  GET  /api/lab/dimensions?cid=      某角色的八维候选池 + 原生外貌底稿
  POST /api/lab/generate             单变量批量生成（笛卡尔积 + 护栏 + 单变量硬断言）
  POST /api/lab/combo               定妆间组合生成（多变量，故意偏离多段）
  GET  /api/lab/job/<id>            任务进度快照
  POST /api/lab/job/<id>/cancel     中断任务
  GET  /api/lab/image/<fname>       读取实验室产物图

设计原则：对 game.db 只读（components 只 SELECT，prompt_builder 不写库），
生成图写到插件 outputs/，绝不污染 data/portraits。
"""

import itertools
import os

from flask import Blueprint, request, jsonify, send_from_directory, current_app
from backend.models import Character

from . import components
from . import jobs
from . import prompt_builder
from .appearance_parser import analyze, detect_anomalies

lab_bp = Blueprint('lab', __name__)

# 角色头像底色（按返回顺序取，纯展示用）
_PALETTE = ['#5b8def', '#f0883e', '#a06be0', '#3aa675', '#e06b9a',
            '#e0574a', '#4f9bb5', '#8a7fd4', '#c98a3d', '#7ec96b',
            '#d96b6b', '#6b9bd9', '#b59b4f', '#5bb0a0']

# 单变量批量生成总张数护栏（超过即拒绝，避免误点全选把 ComfyUI 打爆）
_MAX_ITEMS = 300
# 定妆组合护栏（3 维 × 3 候选 = 27 封顶）
_MAX_COMBO = 27


def _out_dir():
    return current_app.config.get('LAB_OUTPUT_DIR') or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'outputs')


# ─────────────────────────── 角色 / 维度 ───────────────────────────

@lab_bp.route('/api/lab/characters')
def list_characters():
    import json as _json
    rows = Character.query.order_by(Character.id).all()
    out = []
    for idx, c in enumerate(rows):
        appearance = c.appearance or ''
        issues = detect_anomalies(appearance)
        blocks = [m for (lvl, _d, m) in issues if lvl == 'block']
        warns = [m for (lvl, _d, m) in issues if lvl == 'warn']
        if not appearance.strip():
            status = 'new'
        elif blocks:
            status = 'block'        # 实验受阻断，需先修基线
        elif warns:
            status = 'warn'
        else:
            status = 'ok'

        # 按段解析 appearance，供每女主预览面板直读真实字段
        app_segments = {}
        if appearance.strip():
            info = analyze(appearance)
            brow = (info.get('brow') or {}).get('text', '').strip()
            eye  = (info.get('eye')  or {}).get('text', '').strip()
            app_segments['brow'] = brow
            app_segments['eye']  = eye
            if brow and eye:
                app_segments['eyebrows_eyes'] = f'{brow}；{eye}'
            elif brow:
                app_segments['eyebrows_eyes'] = brow
            elif eye:
                app_segments['eyebrows_eyes'] = eye
            for seg, key in (('face', 'face'), ('nose', 'nose'),
                             ('mouth', 'mouth'), ('skin', 'skin_makeup'),
                             ('hair', 'hair')):
                app_segments[key] = (info.get(seg) or {}).get('text', '').strip()

        # 解析当前穿搭（top/bottom/outer/underwear 取 name）
        outfit = {'top': '', 'bottom': '', 'outer': '', 'underwear': ''}
        try:
            co = _json.loads(c.current_outfit) if c.current_outfit else {}
            comps = (co or {}).get('components') or {}
            for slot in ('top', 'bottom', 'outer', 'underwear'):
                v = comps.get(slot)
                if isinstance(v, dict):
                    outfit[slot] = (v.get('name') or '').strip()
        except Exception:
            pass

        out.append({
            'id': c.id,
            'name': c.name,
            'portrait_seed': c.portrait_seed or 0,
            'status': status,
            'color': _PALETTE[idx % len(_PALETTE)],
            'has_appearance': bool(appearance.strip()),
            'issues': [{'level': lvl, 'dim': d, 'msg': m} for (lvl, d, m) in issues],
            'hairstyle': prompt_builder.extract_hairstyle_description(c),
            'app_segments': app_segments,
            'outfit': outfit,
        })
    return jsonify(out)


@lab_bp.route('/api/lab/dimensions')
def list_dimensions():
    cid = request.args.get('cid', type=int) or request.args.get('character_id', type=int)
    c = Character.query.get(cid) if cid else None
    if not c:
        return jsonify({'error': '角色不存在'}), 404
    payload = components.build_dimension_payload(c)
    # 去掉 _appearance 里过重的内容，仅留前端需要的原生/基线
    payload['_appearance'].pop('stripped', None)
    return jsonify(payload)


# ─────────────────────────── 候选解析 ───────────────────────────

def _resolve(char, dim, value, payload):
    """把 (dim, value) 解析成 (展示名, 候选描述文本)

    value 形式：
      'native' / 'base'  → 角色原生（该维度 appearance 原段，缺则用默认）
      'comp:<id>'        → outfit_components 行
      'preset:<id>'      → outfit_presets 行
    """
    if value in ('native', 'base'):
        seg = prompt_builder.SEG.get(dim)
        if seg:
            native = (analyze(char.appearance or '').get(seg) or {}).get('text', '')
            if native:
                return ('角色原生', native)
        d = payload.get(dim, {}).get('default', {})
        return (d.get('name') or '角色原生', d.get('description', ''))
    for cand in payload.get(dim, {}).get('candidates', []):
        if cand['value'] == value:
            return (cand['name'], cand['description'])
    return (value, '')


# ─────────────────────────── 生成 / 预览 ───────────────────────────

@lab_bp.route('/api/lab/preview', methods=['POST'])
def preview():
    """根据当前前端选择，返回将要提交给 ComfyUI 的 positive prompt 列表。

    请求体：{character_ids:[id], plan:{dim:[value,...]}}
    返回：[{character_id, character_name, dim, value_name, prompt, is_baseline}, ...]
    不调用 ComfyUI、不写库、只读 Character。
    """
    data = request.get_json(force=True) or {}
    char_ids = data.get('character_ids') or []
    plan = data.get('plan') or {}
    plan_by_char = data.get('plan_by_char') or {}
    if not char_ids or not (plan or plan_by_char):
        return jsonify({'error': 'character_ids 与 plan/plan_by_char 至少一项必填'}), 400

    chars = Character.query.filter(Character.id.in_(char_ids)).all()
    if not chars:
        return jsonify({'error': '未找到任何角色'}), 404

    previews = []
    for char in chars:
        payload = components.build_dimension_payload(char)
        char_plan = {k: list(v) for k, v in plan.items()}
        pc = plan_by_char.get(char.id) or plan_by_char.get(str(char.id)) or {}
        for dim, values in pc.items():
            char_plan[dim] = list(values)
        for dim, values in char_plan.items():
            if dim not in components.DIMENSIONS:
                continue
            for value in values:
                name, desc = _resolve(char, dim, value, payload)
                if value in ('native', 'base'):
                    prompt = prompt_builder.build_baseline_prompt(char)
                    is_baseline = True
                else:
                    try:
                        prompt = prompt_builder.build_variant_prompt(char, dim, desc)
                    except Exception as e:
                        prompt = f'[构建失败：{e}]'
                    is_baseline = False
                if not prompt:
                    continue
                previews.append({
                    'character_id': char.id,
                    'character_name': char.name,
                    'dim': dim,
                    'dim_label': components.DIMENSIONS[dim]['label'],
                    'value_name': name,
                    'value': value,
                    'prompt': prompt,
                    'is_baseline': is_baseline,
                })
    return jsonify(previews)


@lab_bp.route('/api/lab/generate', methods=['POST'])
def generate():
    data = request.get_json(force=True) or {}
    char_ids = data.get('character_ids') or []
    plan = data.get('plan') or {}            # {dim: [value, ...]}
    plan_by_char = data.get('plan_by_char') or {}
    if not char_ids or not (plan or plan_by_char):
        return jsonify({'error': 'character_ids 与 plan/plan_by_char 至少一项必填'}), 400

    chars = Character.query.filter(Character.id.in_(char_ids)).all()
    if not chars:
        return jsonify({'error': '未找到任何角色'}), 404

    items, total = [], 0
    for char in chars:
        payload = components.build_dimension_payload(char)
        char_plan = {k: list(v) for k, v in plan.items()}
        pc = plan_by_char.get(char.id) or plan_by_char.get(str(char.id)) or {}
        for dim, values in pc.items():
            char_plan[dim] = list(values)
        for dim, values in char_plan.items():
            if dim not in components.DIMENSIONS:
                return jsonify({'error': f'未知维度：{dim}'}), 400
            for value in values:
                name, desc = _resolve(char, dim, value, payload)
                if value in ('native', 'base'):
                    prompt = prompt_builder.build_baseline_prompt(char)
                else:
                    try:
                        prompt = prompt_builder.build_variant_prompt(char, dim, desc)
                    except Exception as e:
                        return jsonify({'error': f'维度「{dim}」违反单变量纪律或解析失败：{e}'}), 400
                if not prompt:
                    continue
                items.append({
                    'character_id': char.id,
                    'character_name': char.name,
                    'dim': dim,
                    'dim_label': components.DIMENSIONS[dim]['label'],
                    'value_name': name,
                    'prompt': prompt,
                    'desc': desc,
                })
                total += 1
                if total > _MAX_ITEMS:
                    return jsonify({'error': f'组合超过上限 {_MAX_ITEMS} 张，请收紧选择（最多 3 维 / 每维 3 候选）'}), 400

    if not items:
        return jsonify({'error': '没有可生成的候选（可能候选描述为空）'}), 400

    width = int(data.get('width', 1024) or 1024)
    height = int(data.get('height', 1024) or 1024)
    job = jobs.submit(current_app._get_current_object(), items, _out_dir(),
                      width=width, height=height)
    return jsonify({'job_id': job.id, 'total': job.total})


@lab_bp.route('/api/lab/combo', methods=['POST'])
def combo():
    data = request.get_json(force=True) or {}
    cid = data.get('character_id')
    selections = data.get('selections') or {}     # {dim: [value, ...]}
    char = Character.query.get(cid) if cid else None
    if not char:
        return jsonify({'error': '角色不存在'}), 404
    if not selections:
        return jsonify({'error': '请至少选择一个维度'}), 400

    payload = components.build_dimension_payload(char)
    resolved = {}
    for dim, values in selections.items():
        if dim not in components.DIMENSIONS:
            return jsonify({'error': f'未知维度：{dim}'}), 400
        resolved[dim] = [_resolve(char, dim, v, payload) for v in values]

    keys = list(selections.keys())
    items = []
    for combo in itertools.product(*[resolved[k] for k in keys]):
        sel = {k: combo[i][1] for i, k in enumerate(keys)}
        sel = {k: v for k, v in sel.items() if v}      # 丢弃空描述
        if not sel:
            continue
        prompt = prompt_builder.build_combo_prompt(char, sel)
        sig = ' / '.join(f'{components.DIMENSIONS[k]["label"]}:{combo[i][0]}'
                         for i, k in enumerate(keys))
        items.append({
            'character_id': char.id,
            'character_name': char.name,
            'dim': 'combo',
            'dim_label': '定妆组合',
            'value_name': sig,
            'prompt': prompt,
        })
        if len(items) > _MAX_COMBO:
            return jsonify({'error': f'组合超过上限 {_MAX_COMBO} 套，请收紧选择'}), 400

    if not items:
        return jsonify({'error': '没有可生成的组合'}), 400

    width = int(data.get('width', 1024) or 1024)
    height = int(data.get('height', 1024) or 1024)
    job = jobs.submit(current_app._get_current_object(), items, _out_dir(),
                      width=width, height=height)
    return jsonify({'job_id': job.id, 'total': job.total})


# ─────────────────────────── 进度 / 取消 / 取图 ───────────────────────────

@lab_bp.route('/api/lab/job/<job_id>')
def job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': '任务不存在'}), 404
    return jsonify(job.snapshot())


@lab_bp.route('/api/lab/job/<job_id>/cancel', methods=['POST'])
def job_cancel(job_id):
    ok = jobs.cancel(job_id)
    return jsonify({'ok': ok})


@lab_bp.route('/api/lab/job/<job_id>/pause', methods=['POST'])
def job_pause(job_id):
    ok = jobs.pause(job_id)
    return jsonify({'ok': ok})


@lab_bp.route('/api/lab/job/<job_id>/resume', methods=['POST'])
def job_resume(job_id):
    ok = jobs.resume(job_id)
    return jsonify({'ok': ok})


@lab_bp.route('/api/lab/regenerate', methods=['POST'])
def regenerate():
    """对指定候选（缺图 / 失败）重新提交生成，已成功的自动跳过。

    请求体：{ items:[{character_id, character_name, dim, dim_label, value_name, prompt}, ...] }
      - items 由前端从「失败/缺失」的格子收集而来，因此成功的格子天然不会被重交。
    返回：{job_id, total, regenerated}
    """
    data = request.get_json(force=True) or {}
    items = data.get('items') or []
    if not items:
        return jsonify({'error': '未提供需要重新生成的候选'}), 400
    width = int(data.get('width', 1024) or 1024)
    height = int(data.get('height', 1024) or 1024)
    job = jobs.submit(current_app._get_current_object(), items, _out_dir(),
                      width=width, height=height)
    return jsonify({'job_id': job.id, 'total': job.total, 'regenerated': len(items)})


@lab_bp.route('/api/lab/image/<path:fname>')
def serve_image(fname):
    return send_from_directory(_out_dir(), fname)


@lab_bp.route('/api/lab/image/<path:fname>', methods=['DELETE'])
def delete_image(fname):
    """删除实验室产物图（仅磁盘文件）。

    实验室结果只存前端内存 LAB_RES、不关联数据库，因此删磁盘文件即完成清理，
    符合「磁盘删除先成功再清关联」原则。校验 fname 归一化后仍位于 outputs 目录内，
    防路径穿越（如 ../../）。
    """
    out_dir = _out_dir()
    target = os.path.normpath(os.path.join(out_dir, fname))
    if target != out_dir and not target.startswith(out_dir + os.sep):
        return jsonify({'error': '非法路径'}), 400
    if not os.path.isfile(target):
        return jsonify({'error': '文件不存在'}), 404
    try:
        os.remove(target)
    except OSError as e:
        return jsonify({'error': f'删除失败：{e}'}), 500
    return jsonify({'ok': True})
