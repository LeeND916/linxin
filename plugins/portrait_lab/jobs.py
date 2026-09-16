# -*- coding: utf-8 -*-
"""批量生成任务队列（ComfyUI 串行出图 + 进度 + 可中断）

ComfyUI 一次只跑一张，批量矩阵（N 位女主 × M 个候选）必须串行排队，
所以这里用一个后台线程逐条消费，前端轮询进度。

复用主项目：
    backend.game.comfyui_client.ensure_comfyui_running   自动拉起 ComfyUI
    backend.game.comfyui_client.get_comfyui_client       提交/轮询/下载
    backend.game.comfyui_client.build_zimage_portrait_prompt
                                                         套工作流 + 固定 portrait_seed
    backend.game.llm_utils.write_comfyui_log             与游戏同格式的调用日志
"""

import logging
import os
import threading
import time
import uuid
from datetime import datetime

logger = logging.getLogger('portrait_lab.jobs')

_JOBS = {}
_LOCK = threading.Lock()
_GEN_LOCK = threading.Lock()   # 串行化 ComfyUI 消费：避免重生成任务与在跑任务并发交错
_MAX_KEEP = 20          # 内存里最多保留的历史任务数


def _safe_name(text: str, limit: int = 24) -> str:
    """文件名安全化：去掉路径分隔符与常见非法字符"""
    bad = '\\/:*?"<>|\r\n\t'
    out = ''.join('_' if c in bad else c for c in (text or ''))
    return out.strip().replace(' ', '')[:limit] or 'x'


class Job:
    """一次批量生成任务"""

    def __init__(self, items, width=1024, height=1024, pixel_art=False):
        self.id = uuid.uuid4().hex[:12]
        self.items = items                  # [{character_id, character_name, dim, dim_label, value_name, prompt}]
        self.width = width
        self.height = height
        self.pixel_art = pixel_art
        self.status = 'pending'             # pending / running / done / cancelled / error
        self.done = 0
        self.total = len(items)
        self.results = []                   # [{index, character_name, value_name, image_url, seed, error, elapsed_ms}]
        self.error = ''
        self.cancel_flag = False
        self.paused = False                 # 可被 pause/resume 挂起
        self.created_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def snapshot(self) -> dict:
        return {
            'job_id': self.id,
            'status': self.status,
            'done': self.done,
            'total': self.total,
            'error': self.error,
            'paused': self.paused,
            'created_at': self.created_at,
            'results': self.results,
        }


# 烧图维度：仅全身穿搭类把 description 烧进图片右下角（避免盖住脸/发型特写）
BURN_DIMS = ('outfit', 'underwear')


def _burn_description(image_data: bytes, desc: str) -> bytes:
    """把 desc 渲染到图片右下角白色半透明底，返回 PNG 字节。

    逻辑复刻 backend/scripts/_生成发型肖像.py 的 add_description_overlay：
    黑体 22px + 白底 rgba(255,255,255,185) + 框宽 62% 图宽 + 右下 margin 22 +
    中文按字符像素贪婪折行。仅 outfit/underwear 维度调用，文字是 outfit_presets.description。
    """
    from PIL import Image, ImageDraw, ImageFont
    import io
    font_path = "C:/Windows/Fonts/simhei.ttf"
    try:
        font = ImageFont.truetype(font_path, 22)
    except Exception:
        font = ImageFont.load_default()
    img = Image.open(io.BytesIO(image_data)).convert("RGBA")
    W, H = img.size
    pad = 16
    margin = 22
    box_w = int(W * 0.62)
    max_text_w = box_w - 2 * pad
    tmp = ImageDraw.Draw(img)
    lines = []
    for para in (desc or "").split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            test = cur + ch
            if tmp.textlength(test, font=font) <= max_text_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    line_h = 30
    text_h = len(lines) * line_h
    box_h = text_h + 2 * pad
    x1 = W - margin
    y1 = H - margin
    x0 = x1 - box_w
    y0 = y1 - box_h
    if x0 < 0:
        x0 = 0
        box_w = x1 - x0
    if y0 < 0:
        y0 = 0
        box_h = y1 - y0
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle([x0, y0, x1, y1], fill=(255, 255, 255, 185))
    text_x = x0 + pad
    text_y = y0 + pad
    for i, line in enumerate(lines):
        od.text((text_x, text_y + i * line_h), line, font=font, fill=(20, 20, 20, 255))
    combined = Image.alpha_composite(img, overlay)
    out = io.BytesIO()
    combined.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def _save_lab_image(target_dir: str, fname: str, image_data: bytes) -> str:
    """把图片写入指定子目录（按「本次生成会话」分文件夹），文件名由调用方给定。

    返回写入的文件名（不含目录），供上层拼出 /api/lab/image/<folder>/<file> 访问路径。
    """
    os.makedirs(target_dir, exist_ok=True)
    fpath = os.path.join(target_dir, fname)
    with open(fpath, 'wb') as f:
        f.write(image_data)
    return fname


def _build_session_folder(job: 'Job') -> str:
    """为「本次生成」聚合出一个文件夹名：女主所有名字 + 变量维度 + 日期时间。

    单女主单维度：苏望倩_outfit_20260812_185103
    多女主：苏望倩_林小鹿_outfit_...
    混合维度：苏望倩_outfit_underwear_...
    """
    names = sorted({i.get('character_name', '') for i in job.items if i.get('character_name')})
    dims = sorted({i.get('dim', '') for i in job.items if i.get('dim')})
    name_part = '_'.join(_safe_name(n) for n in names) or 'unknown'
    dim_part = '_'.join(_safe_name(d, 12) for d in dims) or 'mixed'
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f"{name_part}_{dim_part}_{ts}"


def _run_job(app, job: Job, out_dir: str):
    """后台线程主体：逐条送进 ComfyUI"""
    from backend.models import Character
    from backend.game.comfyui_client import (
        ensure_comfyui_running, get_comfyui_client, build_zimage_portrait_prompt,
    )
    from backend.game.photo_presets import NEGATIVE_REALISTIC, NEGATIVE_FRAMING
    from backend.game.llm_utils import write_comfyui_log

    with _GEN_LOCK, app.app_context():
        job.status = 'running'
        if not ensure_comfyui_running():
            job.status = 'error'
            job.error = 'ComfyUI 启动失败，请手动打开 ComfyUI 桌面应用（默认 http://127.0.0.1:8188）'
            return

        client = get_comfyui_client()

        # 本次生成会话的专属文件夹：女主名字 + 维度 + 时间
        folder_name = _build_session_folder(job)
        job_out_dir = os.path.join(out_dir, folder_name)
        os.makedirs(job_out_dir, exist_ok=True)

        for idx, item in enumerate(job.items):
            # pause/resume 支持：被暂停时轮询等待，不阻塞 ComfyUI 锁（同一时刻只有本任务在跑）
            while job.paused and not job.cancel_flag:
                time.sleep(0.5)
            if job.cancel_flag:
                job.status = 'cancelled'
                return
            if job.cancel_flag:
                job.status = 'cancelled'
                return

            char_name = item.get('character_name', '')
            row = {
                'index': idx,
                'character_id': item.get('character_id'),
                'character_name': char_name,
                'dim': item.get('dim', ''),
                'dim_label': item.get('dim_label', ''),
                'value_name': item.get('value_name', ''),
                'prompt': item.get('prompt', ''),
                'desc': item.get('desc', ''),
                'image_url': '',
                'seed': None,
                'error': '',
                'elapsed_ms': 0,
            }
            t0 = time.time()
            try:
                char = Character.query.get(item.get('character_id'))
                if not char:
                    raise ValueError(f'角色不存在: id={item.get("character_id")}')

                # 复用主项目工作流构建：内部会用 character.portrait_seed 固定种子
                # 负向词 = 写实护栏(NEGATIVE_REALISTIC) + 景别护栏(NEGATIVE_FRAMING，压制大头照/半身像/残体)
                neg = NEGATIVE_REALISTIC + ',' + NEGATIVE_FRAMING
                workflow = build_zimage_portrait_prompt(
                    char, prompt=item.get('prompt') or None,
                    negative=neg,
                    pixel_art=job.pixel_art, width=job.width, height=job.height,
                    cfg=1.0,
                )
                workflow['9']['inputs']['filename_prefix'] = f'lab_{char.name}'
                row['seed'] = workflow.get('44', {}).get('inputs', {}).get('seed')

                prompt_id = client.queue_prompt(workflow)
                if not prompt_id:
                    raise RuntimeError('提交 ComfyUI 任务失败')

                outputs = client.wait_for_result(prompt_id, timeout=180)
                if not outputs:
                    raise RuntimeError('生成超时或失败')

                saved = ''
                for _node, output in outputs.items():
                    for img in output.get('images', []) or []:
                        data = client.download_image(
                            img.get('filename'), img.get('subfolder', ''), img.get('type', 'output')
                        )
                        if data:
                            # #2 后端烧图：outfit/underwear 维度把 description 烧进右下角白色透明框
                            if item.get('desc') and item.get('dim') in BURN_DIMS:
                                try:
                                    data = _burn_description(data, item.get('desc'))
                                except Exception as _be:
                                    logger.warning(f'[Lab] 烧图失败 {char_name} #{idx}: {_be}')
                            # 文件名 = 女主 + 类型具体名称（outfit_presets/outfit_components 的 name 字段）
                            safe_char = _safe_name(char.name)
                            safe_val = _safe_name(item.get('value_name', '') or 'na', 48)
                            fname = f"{safe_char}_{safe_val}.png"
                            base, ext = os.path.splitext(fname)
                            dup = 1
                            while os.path.exists(os.path.join(job_out_dir, fname)):
                                fname = f"{base}_{dup}{ext}"
                                dup += 1
                            _save_lab_image(job_out_dir, fname, data)
                            saved = f"{folder_name}/{fname}"   # 供 image_url / 删除接口定位子路径
                            break
                    if saved:
                        break
                if not saved:
                    raise RuntimeError('ComfyUI 未返回图片')

                row['image_url'] = f'/api/lab/image/{saved}'

                write_comfyui_log('comfyui', char.name, {
                    'url': f'{client.base_url}/prompt',
                    'source': 'portrait_lab',
                    'character': char.name,
                    'dimension': item.get('dim', ''),
                    'variant': item.get('value_name', ''),
                    'positive_prompt': item.get('prompt', ''),
                    'width': job.width, 'height': job.height,
                    'seed': row['seed'],
                }, {
                    'success': True, 'prompt_id': prompt_id,
                    'image_url': row['image_url'],
                    'elapsed_ms': int((time.time() - t0) * 1000),
                })
            except Exception as e:
                row['error'] = str(e)
                logger.error(f'[Lab] 生成失败 {char_name} #{idx}: {e}')
                try:
                    write_comfyui_log('comfyui', char_name, {
                        'source': 'portrait_lab',
                        'dimension': item.get('dim', ''),
                        'variant': item.get('value_name', ''),
                        'positive_prompt': item.get('prompt', ''),
                    }, {'success': False, 'error': str(e)})
                except Exception:
                    pass

            row['elapsed_ms'] = int((time.time() - t0) * 1000)
            job.results.append(row)
            job.done = idx + 1

        job.status = 'cancelled' if job.cancel_flag else 'done'


def submit(app, items, out_dir, width=1024, height=1024, pixel_art=False) -> Job:
    """创建并启动一个批量任务"""
    job = Job(items, width=width, height=height, pixel_art=pixel_art)
    with _LOCK:
        _JOBS[job.id] = job
        if len(_JOBS) > _MAX_KEEP:
            for k in list(_JOBS.keys())[:-_MAX_KEEP]:
                _JOBS.pop(k, None)
    threading.Thread(target=_run_job, args=(app, job, out_dir), daemon=True).start()
    return job


def get(job_id: str):
    return _JOBS.get(job_id)


def cancel(job_id: str) -> bool:
    """标记中断：当前这张画完后停止，不会硬杀 ComfyUI"""
    job = _JOBS.get(job_id)
    if not job:
        return False
    job.cancel_flag = True
    return True


def pause(job_id: str) -> bool:
    """暂停：当前这张画完后进入等待，可被 resume 恢复"""
    job = _JOBS.get(job_id)
    if not job:
        return False
    job.paused = True
    return True


def resume(job_id: str) -> bool:
    """恢复暂停的任务"""
    job = _JOBS.get(job_id)
    if not job:
        return False
    job.paused = False
    return True
