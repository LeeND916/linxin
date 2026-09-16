# -*- coding: utf-8 -*-
"""
ComfyUI 客户端（AI伴侣系统升级 — 阶段三）

职责：
  1. 与 ComfyUI 服务器通信，提交生成任务
  2. 根据角色状态构建提示词
  3. 轮询等待生成结果并保存图片

ComfyUI 桌面端可执行文件路径：由前端配置页或本机 local_config.py 提供
默认 API 地址：http://127.0.0.1:8188
"""

import json
import logging
import os
import random
import threading
import time
import uuid
from typing import Optional
from urllib import request as urllib_request
from urllib import parse as urllib_parse

from backend.config import USER_SIM_LIFE, Config

logger = logging.getLogger('sim_life.comfyui')

# 项目根目录（comfyui_client.py 位于 backend/game/，向上两级即项目根）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WORKFLOW_DIR = os.path.join(_PROJECT_ROOT, "data", "comfyui", "workflows")

# ComfyUI 兜底地址（DB 无激活配置时使用）；ComfyUI 桌面端默认端口为 8188
_FALLBACK_COMFYUI_URL = "http://127.0.0.1:8188"
# 兜底 exe 路径：来自本机 local_config.py（env COMFYUI_EXE_PATH），默认留空，
# 由前端配置页填写。源码里不含任何个人路径。
_FALLBACK_COMFYUI_EXE = getattr(Config, 'COMFYUI_EXE_PATH', '') or ''
# 肖像输出目录：放在用户目录下的 sim_life\portraits（与项目根分离，
# 与 chat_with_*/voices/tts_cache/world_settings 等用户数据并列，便于跨版本保留）。
OUTPUT_DIR = os.path.join(USER_SIM_LIFE, 'portraits')


def _load_active_comfyui_config():
    """从 DB 读取激活的 ComfyUIConfig，读取失败或无激活配置时返回 None"""
    try:
        from backend.models import ComfyUIConfig
        return ComfyUIConfig.query.filter_by(is_active=True).first()
    except Exception as e:
        logger.warning(f"[ComfyUI] 读取 DB 配置失败，使用默认值: {e}")
        return None


class ComfyUIClient:
    """ComfyUI API 客户端"""
    
    def __init__(self, base_url: str = None):
        config = _load_active_comfyui_config()
        if base_url:
            self.base_url = base_url.rstrip('/')
            self.exe_path = _FALLBACK_COMFYUI_EXE
        else:
            self.base_url = (config.api_url if config and config.api_url else _FALLBACK_COMFYUI_URL).rstrip('/')
            self.exe_path = config.exe_path if config and config.exe_path else _FALLBACK_COMFYUI_EXE
        self.api_key = config.api_key if config and config.api_key else ''
        self.client_id = str(uuid.uuid4())

    def _headers(self, content_type=None):
        headers = {}
        if content_type:
            headers['Content-Type'] = content_type
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers

    def reload_config(self):
        """从 DB 重新读取激活配置（配置变更后调用）"""
        config = _load_active_comfyui_config()
        self.base_url = (config.api_url if config and config.api_url else _FALLBACK_COMFYUI_URL).rstrip('/')
        self.exe_path = config.exe_path if config and config.exe_path else _FALLBACK_COMFYUI_EXE
        self.api_key = config.api_key if config and config.api_key else ''
        logger.info(f"[ComfyUI] 配置已重载: api_url={self.base_url}")
    
    def queue_prompt(self, prompt: dict) -> Optional[str]:
        """提交生成任务到 ComfyUI 队列
        
        参数:
            prompt: ComfyUI workflow 格式的 prompt
            
        返回:
            str: prompt_id，失败时返回 None
        """
        try:
            self.reload_config()
            data = json.dumps({
                "prompt": prompt,
                "client_id": self.client_id
            }).encode('utf-8')
            
            req = urllib_request.Request(
                f"{self.base_url}/prompt",
                data=data,
                headers=self._headers('application/json')
            )
            
            with urllib_request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode('utf-8'))
                prompt_id = result.get('prompt_id')
                logger.info(f"[ComfyUI] 任务已提交: {prompt_id}")
                return prompt_id
                
        except Exception as e:
            logger.error(f"[ComfyUI] 提交任务失败: {e}")
            return None
    
    def get_history(self, prompt_id: str) -> Optional[dict]:
        """获取任务历史记录
        
        参数:
            prompt_id: 任务 ID
            
        返回:
            dict: 历史记录，失败时返回 None
        """
        try:
            url = f"{self.base_url}/history/{prompt_id}"
            req = urllib_request.Request(url, headers=self._headers())
            with urllib_request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            logger.error(f"[ComfyUI] 获取历史失败: {e}")
            return None
    
    def wait_for_result(self, prompt_id: str, timeout: int = 120) -> Optional[dict]:
        """轮询等待生成结果
        
        参数:
            prompt_id: 任务 ID
            timeout: 超时时间（秒）
            
        返回:
            dict: 生成结果，失败时返回 None
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            history = self.get_history(prompt_id)
            if history and prompt_id in history:
                result = history[prompt_id]
                if result.get('status', {}).get('completed', False):
                    logger.info(f"[ComfyUI] 任务完成: {prompt_id}")
                    return result.get('outputs', {})
                elif result.get('status', {}).get('status_str') == 'error':
                    logger.error(f"[ComfyUI] 任务失败: {prompt_id}")
                    return None
            
            time.sleep(2)  # 每 2 秒轮询一次
        
        logger.warning(f"[ComfyUI] 任务超时: {prompt_id}")
        return None
    
    def download_image(self, filename: str, subfolder: str = '', folder_type: str = 'output') -> Optional[bytes]:
        """从 ComfyUI 下载生成的图片
        
        参数:
            filename: 文件名
            subfolder: 子文件夹
            folder_type: 文件夹类型（output/input/temp）
            
        返回:
            bytes: 图片数据，失败时返回 None
        """
        try:
            params = {
                "filename": filename,
                "subfolder": subfolder,
                "type": folder_type
            }
            query_string = urllib_parse.urlencode(params)
            url = f"{self.base_url}/view?{query_string}"
            req = urllib_request.Request(url, headers=self._headers())
            with urllib_request.urlopen(req, timeout=30) as response:
                return response.read()
                
        except Exception as e:
            logger.error(f"[ComfyUI] 下载图片失败: {e}")
            return None
    
    def is_available(self) -> bool:
        """检查 ComfyUI 是否可用"""
        try:
            url = f"{self.base_url}/system_stats"
            req = urllib_request.Request(url, headers=self._headers())
            with urllib_request.urlopen(req, timeout=5) as response:
                return response.status == 200
        except Exception:
            return False






def _build_character_prompt(character):
    """构建角色肖像提示词（L1 身份锚 + L2 状态层 + 光线层，全中文写实风）。

    统一走 photo_presets.assemble_portrait_prompt，保证聊天生图与默认肖像
    使用同一套中文写实构件（见《聊天生图方案.md》第 4 章）。
    仅返回正向提示词（负面词由生图链路按 node_map 注入，当前默认肖像路径未接线）。
    """
    from backend.game.photo_presets import assemble_portrait_prompt
    positive, _ = assemble_portrait_prompt(character)
    return positive


def _load_workflow(filename="image_z_image_turbo.json"):
    """从项目 data/comfyui/workflows/ 目录加载工作流 JSON 模板，并转换为 API 格式

    兼容三种入参：
      - 纯文件名（如 image_z_image_turbo.json）→ 拼接到 _WORKFLOW_DIR
      - 相对路径（如 workflows/portrait_gen.json）→ 相对项目根解析
      - 绝对路径 → 直接使用
    """
    if os.path.isabs(filename):
        path = filename
    elif filename.startswith('workflows') or filename.startswith('data'):
        path = os.path.join(_PROJECT_ROOT, filename)
    else:
        path = os.path.join(_WORKFLOW_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"工作流模板不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # 如果是 API 格式（节点 ID 为 key 的字典），直接返回。
    # 兼容子图展开后节点 id 含冒号（如 '238:227'）等非纯数字 id：
    # 用「无顶层 nodes 键 + 首值为含 class_type 的节点 dict」判定，
    # 避免被误判为 UI 格式而返回空工作流（导致提示词注入全部失效）。
    sample = next(iter(raw.values()), None)
    if isinstance(sample, dict) and "class_type" in sample and "nodes" not in raw:
        return raw

    # 导出格式（nodes 数组 + links 数组）→ API 格式
    nodes = raw.get("nodes", [])
    links = raw.get("links", [])

    # 构建链接映射：target_node_id[target_slot] = [source_node_id, source_slot]
    link_map = {}
    for link in links:
        if len(link) < 6:
            continue
        link_id, src_node, src_slot, tgt_node, tgt_slot, _ = link
        link_map.setdefault(str(tgt_node), {})[tgt_slot] = [str(src_node), src_slot]

    # 非执行节点类型，仅用于工作流注释/显示，需过滤
    NON_EXEC_NODE_TYPES = {"MarkdownNote", "Note", "PrimitiveNode", "Reroute"}

    workflow = {}
    for node in nodes:
        if node.get("type") in NON_EXEC_NODE_TYPES:
            continue
        nid = str(node["id"])
        entry = {"class_type": node["type"], "inputs": {}}

        widget_idx = 0
        for slot_idx, inp in enumerate(node.get("inputs", [])):
            slot_name = inp.get("name", f"slot_{slot_idx}")
            if inp.get("link") is not None:
                # 有链接：从 link_map 查源节点
                src_info = link_map.get(nid, {}).get(slot_idx, [None, 0])
                entry["inputs"][slot_name] = src_info
            else:
                # 无链接：从 widgets_values 取值
                wv = node.get("widgets_values", [])
                val = wv[widget_idx] if widget_idx < len(wv) else ""
                entry["inputs"][slot_name] = val
                widget_idx += 1

        workflow[nid] = entry

    return workflow


def load_workflow(filename="image_z_image_turbo.json"):
    """加载并解析工作流，供其他模块复用。"""
    return _load_workflow(filename)


def auto_detect_node_map(workflow: dict) -> dict:
    """从已解析的工作流图（{node_id: {class_type, inputs}}）自动探测节点映射。

    返回 {positive, negative, seed, output, seed_input} 字典，缺失项省略。
    探测失败时返回空 dict（调用方回退硬编码节点号）。无需 ComfyUI 在线，
    仅依据工作流文件结构推断，解决 node_map 只能手填的问题（P2-E）。
    """
    if not isinstance(workflow, dict):
        return {}
    ksampler_id = None
    save_id = None
    for nid, node in workflow.items():
        ct = (node.get('class_type') or '').lower()
        if ksampler_id is None and ('ksampler' in ct or 'sampler' in ct):
            ksampler_id = nid
        if save_id is None and ('saveimage' in ct or 'previewimage' in ct):
            save_id = nid

    result = {}
    if ksampler_id is not None:
        result['seed'] = ksampler_id
        k_inputs = workflow[ksampler_id].get('inputs', {})
        # seed 输入键：优先 seed，其次 noise_seed；不能把 cfg/steps 等整数误判为 seed。
        if 'seed' in k_inputs:
            result['seed_input'] = 'seed'
        elif 'noise_seed' in k_inputs:
            result['seed_input'] = 'noise_seed'
        else:
            seed_keys = [k for k in k_inputs if 'seed' in str(k).lower()]
            if seed_keys:
                result['seed_input'] = seed_keys[0]
        # positive/negative：KSampler 的输入为链接 [src_node, slot]
        for role, key in (('positive', 'positive'), ('negative', 'negative')):
            link = k_inputs.get(key)
            if isinstance(link, (list, tuple)) and len(link) >= 1:
                result[role] = str(link[0])

    # 链接不可用时，按 class_type 兜底（CLIPTextEncode 类）
    if 'positive' not in result or 'negative' not in result:
        clip_nodes = [nid for nid, n in workflow.items()
                      if 'cliptextencode' in (n.get('class_type') or '').lower()]
        if clip_nodes:
            if 'positive' not in result and clip_nodes:
                result['positive'] = clip_nodes[0]
            if 'negative' not in result and len(clip_nodes) > 1:
                result['negative'] = clip_nodes[-1]
            elif 'negative' not in result and clip_nodes:
                result['negative'] = clip_nodes[0]

    for nid, node in workflow.items():
        inputs = node.get('inputs', {}) or {}
        ct = (node.get('class_type') or '').lower()
        if 'latent' not in result and ('emptylatent' in ct or ('width' in inputs and 'height' in inputs)):
            result['latent'] = nid
        if 'lora' not in result and ('lora' in ct or 'strength' in inputs):
            result['lora'] = nid

    if save_id is not None:
        result['output'] = save_id
    return result


# ── z-image LoRA 解析 ───────────────────────────────────────────────
# 角色 zimage_lora 为空时使用的默认 LoRA
DEFAULT_ZIMAGE_LORA = 'pixel_art_style_z_image_turbo.safetensors'
# 可用 LoRA 列表缓存：object_info 单次响应不大，但生图是高频路径，没必要每次都拉
_LORA_CACHE = {'names': [], 'ts': 0.0}
_LORA_CACHE_TTL = 300.0


def list_available_loras(force_refresh=False):
    """列出 ComfyUI 可用的 LoRA 文件名（来源 /object_info/LoraLoader）。

    返回 list[str]。拉取失败时返回上次缓存或空列表，由 resolve_lora_name
    回退默认 LoRA，不阻塞出图。结果缓存 5 分钟。
    """
    global _LORA_CACHE
    now = time.time()
    if (not force_refresh and _LORA_CACHE['names']
            and (now - _LORA_CACHE['ts']) < _LORA_CACHE_TTL):
        return _LORA_CACHE['names']

    try:
        client = ComfyUIClient()
        req = urllib_request.Request(
            f"{client.base_url}/object_info/LoraLoader",
            headers=client._headers())
        with urllib_request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        node = (data or {}).get('LoraLoader') or {}
        required = ((node.get('input') or {}).get('required') or {})
        raw = (required.get('lora_name') or [None])[0]
        names = [n for n in (raw or []) if isinstance(n, str)]
        _LORA_CACHE['names'] = names
        _LORA_CACHE['ts'] = now
        logger.info(f"[ComfyUI] 已拉取可用 LoRA {len(names)} 个")
        return names
    except Exception as e:
        logger.warning(f"[ComfyUI] 拉取 LoRA 列表失败，将回退默认: {e}")
        return _LORA_CACHE['names'] or []


def resolve_lora_name(keyword, default=DEFAULT_ZIMAGE_LORA):
    """把角色配置的 LoRA 片段解析成 ComfyUI 实际文件名。

    匹配顺序：空值→默认 → 精确匹配 → 包含匹配（大小写不敏感）→ 回退默认 + warning。

    包含匹配让角色只需填片段，例如 'yanyin' 即可命中
    'yanyin_zimage_turbo_lora_v1_000002500.safetensors'；命中多个时取列表首个
    （顺序与 ComfyUI 下拉一致）。
    """
    kw = (keyword or '').strip()
    if not kw:
        return default

    names = list_available_loras()
    if not names:
        logger.warning(f"[ComfyUI] LoRA 列表为空，无法校验 '{kw}'，回退默认 {default}")
        return default

    for n in names:
        if n == kw:
            return n

    low = kw.lower()
    for n in names:
        if low in n.lower():
            logger.info(f"[ComfyUI] LoRA 片段 '{kw}' 包含匹配 → {n}")
            return n

    logger.warning(
        f"[ComfyUI] LoRA '{kw}' 在 ComfyUI 中不存在（当前可用 {len(names)} 个），"
        f"回退默认 {default}。请检查 character.zimage_lora 或 ComfyUI 的 models/loras 目录。")
    return default


def build_zimage_portrait_prompt(character, prompt=None, negative=None, pixel_art=False, width=1024, height=1024,
                                 workflow_row=None, node_map=None, cfg=1.2, return_text=False):
    """
    构建 zimage 肖像生成工作流。

    Args:
        character: 角色对象
        prompt: 用户自定义提示词，为 None 时自动构建
        negative: 用户自定义负面提示词，为 None 时不注入
        pixel_art: 【已废弃】原像素风 LoRA 开关。zimage_lora 落地后由该字段接管「用哪个
            LoRA」，强度恒为 1.0，本参数不再参与任何判断，保留仅为兼容旧调用方。
        width/height: 输出图片尺寸
        workflow_row: ComfyUIWorkflow 行（含 workflow_path），传入时用 DB 注册的工作流，
            未传或加载失败时回退内置 image_z_image_turbo.json
        node_map: 节点映射；未提供时按工作流结构自动探测
        cfg: KSampler 引导系数（CFG），默认 1.0；调高可让正向提示词（含眼型/五官变量）
            更显著地影响成图。其余调用方保持默认 1.0 行为不变。
    """
    workflow = None
    if workflow_row is not None:
        wf_path = getattr(workflow_row, 'workflow_path', '') or ''
        if wf_path:
            try:
                workflow = _load_workflow(wf_path)
                logger.info(f"[ComfyUI] 使用 DB 工作流: {wf_path}")
            except Exception as e:
                logger.warning(f"[ComfyUI] DB 工作流 {wf_path} 加载失败，回退内置: {e}")
                workflow = None
    if workflow is None:
        workflow = _load_workflow("image_z_image_turbo.json")
    
    mapping = auto_detect_node_map(workflow)
    mapping.update(node_map or {})

    def _node(role):
        node_id = mapping.get(role)
        return workflow.get(str(node_id)) if node_id is not None else None

    def _set_input(role, key, value):
        node = _node(role)
        if node is not None:
            node.setdefault('inputs', {})[key] = value

    # 提示词
    text = prompt if prompt else _build_character_prompt(character)
    # 在提示词开头注入角色性格类型，让 ComfyUI 出图带上气质底色；
    # 字段为空时（极少数旧角色无 personality_type）不加，保持原样、不报错。
    ptype = getattr(character, 'personality_type', '') or ''
    if ptype and ptype not in text:
        text = f"{ptype}气质，" + text
    # z-image LoRA 触发词前缀：character.zimage_lora 非空时，在提示词「最前面」
    # 追加 "字段值,"，作为该 LoRA 的触发词；为空则留空（不加任何前缀）。
    # 例：zimage_lora='yanyin' → 正向提示词变为 "yanyin, 高冷气质，……"。
    zlora = getattr(character, 'zimage_lora', '') or ''
    if zlora:
        zlora_prefix = f"{zlora}, "
        # 防重复：若已以该前缀开头则跳过（避免重载/二次渲染叠加）。
        if not text.lower().startswith(zlora_prefix.lower()):
            text = zlora_prefix + text
    _set_input('positive', 'text', text)
    if negative is not None:
        _set_input('negative', 'text', negative)

    # 尺寸
    _set_input('latent', 'width', width)
    _set_input('latent', 'height', height)

    # 固定种子与 KSampler 参数
    # 旧角色可能没有 portrait_seed；首次生成时补一个并持久化，避免每次回退随机种子导致人像漂移。
    char_seed = getattr(character, 'portrait_seed', 0) or 0
    if not char_seed:
        char_seed = random.randint(1, 2**31 - 1)
        try:
            character.portrait_seed = char_seed
            if getattr(character, 'id', None):
                from backend.models import db
                if db.session.object_session(character):
                    db.session.commit()
        except Exception as exc:
            logger.warning('[ComfyUI] 固定 portrait_seed 持久化失败，当前请求仍使用该 seed: %s', exc)
    _set_input('seed', mapping.get('seed_input', 'seed'), char_seed)
    for key, value in {
        'steps': 9, 'cfg': cfg, 'sampler_name': 'euler',
        'scheduler': 'simple', 'denoise': 1.0,
    }.items():
        _set_input('seed', key, value)

    # 文件名前缀与 LoRA 控制
    _set_input('output', 'filename_prefix', f"portrait_{character.name}")
    # LoRA 选择：character.zimage_lora 为空 → 默认 LoRA；非空 → 精确/包含匹配 ComfyUI 可用列表。
    # 强度按决策固定 1.0（不额外字段化）。
    #
    # 键名修正：LoraLoaderModelOnly 的强度键是 strength_model，LoraLoader 另有 strength_clip。
    # 旧代码写入的 'strength' 在这两类节点上都不存在，会被 ComfyUI 静默忽略——
    # 这正是 pixel_art 开关自引入起从未生效的根因。
    lora_node = _node('lora')
    if lora_node is not None:
        lora_inputs = lora_node.setdefault('inputs', {})
        lora_inputs['lora_name'] = resolve_lora_name(
            getattr(character, 'zimage_lora', '') or '')
        lora_inputs['strength_model'] = 1.0
        if 'strength_clip' in lora_inputs:
            lora_inputs['strength_clip'] = 1.0
    if return_text:
        return workflow, text
    return workflow


def build_qwen_portrait_prompt(character, prompt=None, negative=None, pixel_art=False,
                               width=1328, height=1328, workflow_row=None, node_map=None,
                               cfg=None, return_text=False):
    """Qwen-Image 肖像注入（专用入口，独立于 z-image 路径，不破坏已走通代码）。

    当前 Qwen 扁平工作流为 SD3/AuraFlow 风格节点（CLIPTextEncode + KSampler +
    EmptySD3LatentImage），与 z-image 拓扑相同，但此处不复用 build_zimage_portrait_prompt
    的函数体，避免改动 z-image 逻辑。正向提示词注入「性格前缀 + 外貌权重」与 z-image 一致；
    尺寸默认 1328（Qwen-Image 推荐方形分辨率）。

    关键差异：不覆盖 KSampler 的 steps/cfg/sampler/scheduler/denoise，
    保留工作流自带的 Lightning-4steps 加速配置（否则会拖慢甚至破坏出图）。

    所有工作流路径与地址均来自 DB（workflow_row / comfyui_config），不硬编码。
    """
    workflow = None
    if workflow_row is not None:
        wf_path = getattr(workflow_row, 'workflow_path', '') or ''
        if wf_path:
            try:
                workflow = _load_workflow(wf_path)
                logger.info(f"[ComfyUI] 使用 DB 工作流: {wf_path}")
            except Exception as e:
                logger.warning(f"[ComfyUI] DB 工作流 {wf_path} 加载失败，回退内置: {e}")
                workflow = None
    if workflow is None:
        workflow = _load_workflow("image_z_image_turbo.json")

    mapping = auto_detect_node_map(workflow)
    mapping.update(node_map or {})

    def _node(role):
        node_id = mapping.get(role)
        return workflow.get(str(node_id)) if node_id is not None else None

    def _set_input(role, key, value):
        node = _node(role)
        if node is not None:
            node.setdefault('inputs', {})[key] = value

    # 提示词（与 z-image 一致：性格前缀 + 外貌权重）
    text = prompt if prompt else _build_character_prompt(character)
    ptype = getattr(character, 'personality_type', '') or ''
    if ptype and ptype not in text:
        text = f"{ptype}气质，" + text
    _set_input('positive', 'text', text)
    if negative is not None:
        _set_input('negative', 'text', negative)

    # 尺寸
    _set_input('latent', 'width', width)
    _set_input('latent', 'height', height)

    # 固定种子（与 z-image 一致，避免每次回退随机种子导致人像漂移）
    char_seed = getattr(character, 'portrait_seed', 0) or 0
    if not char_seed:
        char_seed = random.randint(1, 2**31 - 1)
        try:
            character.portrait_seed = char_seed
            if getattr(character, 'id', None):
                from backend.models import db
                if db.session.object_session(character):
                    db.session.commit()
        except Exception as exc:
            logger.warning('[ComfyUI] 固定 portrait_seed 持久化失败，当前请求仍使用该 seed: %s', exc)
    _set_input('seed', mapping.get('seed_input', 'seed'), char_seed)

    # 文件名前缀
    _set_input('output', 'filename_prefix', f"portrait_{character.name}")
    # Qwen 工作流的 lora 节点(238:221)即 4-step Lightning 加速 LoRA，强度必须为 1.0，
    # 否则 4-step 加速形同虚设（模型退化成 4 步无引导，出图崩坏）。
    # 键名修正：该节点为 LoraLoaderModelOnly，强度键是 strength_model；
    # 旧代码写的 'strength' 不存在，一直被 ComfyUI 静默忽略（此前是靠工作流文件里
    # 自带的 strength_model=1 侥幸生效）。
    # 注意：此处【不注入】character.zimage_lora —— 该节点是加速 LoRA 而非风格 LoRA，
    # 覆盖会导致 Qwen 出图崩坏。zimage_lora 仅作用于 z-image 工作流。
    qwen_lora = _node('lora')
    if qwen_lora is not None:
        qi = qwen_lora.setdefault('inputs', {})
        qi['strength_model'] = 1.0
        if 'strength_clip' in qi:
            qi['strength_clip'] = 1.0

    # 注意：不覆盖 KSampler 的 steps/cfg/sampler/scheduler/denoise，
    # 保留 Qwen 工作流自带的 Lightning-4steps 加速配置。
    if return_text:
        return workflow, text
    return workflow


def build_portrait_prompt(character, prompt=None, negative=None, pixel_art=False,
                          width=1024, height=1024, workflow_row=None, node_map=None,
                          cfg=None, return_text=False):
    """生图注入 dispatcher：按 node_map['prompt_family'] 分流，不破坏 z-image 已走通路径。

    - node_map 含 prompt_family == 'qwen' → build_qwen_portrait_prompt（尺寸 1328）
    - 其余（含 z-image）→ build_zimage_portrait_prompt（尺寸 1024，行为完全不变）

    node_map 来自 DB（resolve_workflow），不依赖硬编码的工作流文件名判断；
    远程地址由 get_comfyui_client 从 DB 读取，本函数不写死任何地址。
    """
    nm = node_map
    if isinstance(nm, str):
        try:
            nm = json.loads(nm) or {}
        except Exception:
            nm = {}
    if nm and nm.get('prompt_family') == 'qwen':
        return build_qwen_portrait_prompt(
            character, prompt=prompt, negative=negative, pixel_art=pixel_art,
            width=1328, height=1328, workflow_row=workflow_row, node_map=nm,
            cfg=cfg, return_text=return_text)
    # 默认走 z-image 路径（cfg 默认 1.2，行为完全不变）
    return build_zimage_portrait_prompt(
        character, prompt=prompt, negative=negative, pixel_art=pixel_art,
        width=width, height=height, workflow_row=workflow_row, node_map=nm,
        cfg=cfg if cfg is not None else 1.2, return_text=return_text)


def save_portrait_image(character_name: str, image_data: bytes) -> Optional[str]:
    """保存生成的肖像图片
    
    参数:
        character_name: 角色名字
        image_data: 图片数据
        
    返回:
        str: 保存路径，失败时返回 None
    """
    try:
        # 确保输出目录存在
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        # 生成文件名
        timestamp = int(time.time())
        filename = f"{character_name}_{timestamp}.png"
        filepath = os.path.join(OUTPUT_DIR, filename)
        
        # 保存图片
        with open(filepath, 'wb') as f:
            f.write(image_data)
        
        logger.info(f"[ComfyUI] 肖像已保存: {filepath}")
        return filepath
        
    except Exception as e:
        logger.error(f"[ComfyUI] 保存图片失败: {e}")
        return None


# 全局客户端实例
_comfyui_client = None


def get_comfyui_client() -> ComfyUIClient:
    """获取 ComfyUI 客户端单例"""
    global _comfyui_client
    if _comfyui_client is None:
        _comfyui_client = ComfyUIClient()
    return _comfyui_client


def _is_local_ip(url: str) -> bool:
    """判断 URL 中的 IP 是否属于本机网卡地址（含 127.x.x.x、局域网 IP 等）。"""
    import re, socket
    m = re.search(r'://([^:/]+)', url)
    if not m:
        return False
    host = m.group(1)
    try:
        addr = socket.getaddrinfo(host, None, socket.AF_INET)
        ip = addr[0][4][0]
    except Exception:
        return False

    # 127.0.0.0/8 回环
    if ip.startswith('127.'):
        return True

    # 遍历本机所有网卡 IP
    try:
        for if_name, if_addrs in _get_local_ips().items():
            if ip in if_addrs:
                return True
    except Exception:
        pass
    return False


def _get_local_ips() -> dict:
    """返回本机所有网卡的 {网卡名: [IPv4地址列表]}。"""
    import os, sys
    ips = {}
    if sys.platform == 'win32':
        # PowerShell 获取本机 IP
        try:
            import subprocess
            result = subprocess.run(
                ['powershell', '-NoProfile', '-Command',
                 'Get-NetIPAddress -AddressFamily IPv4 | '
                 'Where-Object { $_.IPAddress -ne "127.0.0.1" } | '
                 'Select-Object -ExpandProperty IPAddress'],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line:
                    ips.setdefault('default', []).append(line)
        except Exception:
            pass
    else:
        import netifaces
        for if_name in netifaces.interfaces():
            addrs = netifaces.ifaddresses(if_name)
            if netifaces.AF_INET in addrs:
                ips[if_name] = [a['addr'] for a in addrs[netifaces.AF_INET]]
    return ips


# ComfyUI 标准端口候选（优先标准端口 8188，兼容旧配置 3099）
_COMFYUI_CANDIDATE_PORTS = [8188, 3099]


def _parse_host_port(url: str):
    """从 base_url 解析 (scheme, host, port)。"""
    import re
    m = re.match(r'^(https?)://([^:/]+)(?::(\d+))?', url or '')
    if not m:
        return None, None, None
    return m.group(1), m.group(2), (int(m.group(3)) if m.group(3) else None)


def _probe_comfyui(url: str, timeout: int = 4) -> bool:
    """探测指定 base_url 的 ComfyUI 是否可用（/system_stats）。"""
    try:
        with urllib_request.urlopen(f"{url.rstrip('/')}/system_stats", timeout=timeout):
            return True
    except Exception:
        return False


def resolve_comfyui_base_url(preferred: str) -> str:
    """返回真正可用的 ComfyUI base_url。

    优先使用 preferred；若不可达，则在同主机的候选端口上探测，
    自动适配 ComfyUI 实际监听端口（标准 8188 或旧配置 3099 等）。
    """
    if _probe_comfyui(preferred):
        return preferred
    scheme, host, conf_port = _parse_host_port(preferred)
    if not host:
        return preferred
    ports = [p for p in _COMFYUI_CANDIDATE_PORTS if p != conf_port]
    # 配置端口本身也纳入候选（排在最前），避免遗漏
    if conf_port and conf_port not in ports:
        ports.insert(0, conf_port)
    for port in ports:
        cand = f"{scheme}://{host}:{port}"
        if _probe_comfyui(cand):
            logger.info(f"[ComfyUI] 探测到可用端口 {port}，改用 {cand}")
            return cand
    return preferred


# 独立启动锁：串行化 ComfyUI 自启动，避免多线程重复 Popen（不持业务 gen 锁）
_STARTUP_LOCK = threading.Lock()


def ensure_comfyui_running() -> bool:
    """检测 ComfyUI 是否可用，不可用时自动重启并等待就绪。
    
    返回:
        True: ComfyUI 就绪
        False: 启动失败，需手动处理
    """
    import subprocess
    client = get_comfyui_client()
    
    # 0. 解析真正可用的 base_url（配置端口错误时自动探测标准端口）
    client.base_url = resolve_comfyui_base_url(client.base_url)
    
    # 1. 快速检测：已在运行则直接返回
    if client.is_available():
        return True
    
    logger.warning("[ComfyUI] 不可用，尝试自动重启...")
    
    # 判断 api_url 是否指向本机（localhost / 127.x / ::1 / 本机网卡IP）
    api_url_lower = client.base_url.lower()
    is_local = ('localhost' in api_url_lower or '127.0.0.1' in api_url_lower
                or '::1' in api_url_lower or _is_local_ip(client.base_url))
    if not is_local:
        logger.warning(f"[ComfyUI] api_url={client.base_url} 不是本地地址，跳过自动启动")
        return False
    
    # 独立启动锁：仅串行化「杀进程→Popen→等待就绪」这一段，
    # 不占用业务 gen 锁（_GEN_LOCK），使聊天生图的并发闸在 ComfyUI 宕机等待期间不被阻塞（§3.7）。
    with _STARTUP_LOCK:
        # 另一线程可能已在锁内把 ComfyUI 拉起，重新探测一次
        if client.is_available():
            return True
        # 2. 杀掉残留 ComfyUI 进程
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "ComfyUI.exe"],
                capture_output=True, timeout=10
            )
        except Exception:
            pass
        
        # 3. 启动 ComfyUI（使用 DB 配置的 exe_path，回退到默认路径）
        comfyui_exe = getattr(client, 'exe_path', None) or _FALLBACK_COMFYUI_EXE
        if not comfyui_exe:
            logger.error("[ComfyUI] 未配置可执行文件路径，请在「设置 → ComfyUI」填写 exe 路径，"
                         "或在本机 local_config.py 里设置 COMFYUI_EXE_PATH")
            return False
        if not os.path.exists(comfyui_exe):
            logger.error(f"[ComfyUI] 找不到可执行文件: {comfyui_exe}")
            return False
        
        try:
            subprocess.Popen(
                [comfyui_exe],
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
        except Exception as e:
            logger.error(f"[ComfyUI] 启动失败: {e}")
            return False
        
        # 4. 等待就绪（最多 120 秒）：每次轮询前重新探测端口，兼容启动后实际端口
        logger.info("[ComfyUI] 等待服务就绪（最多 120 秒）...")
        waited = 0
        while waited < 120:
            time.sleep(5)
            waited += 5
            client.base_url = resolve_comfyui_base_url(client.base_url)
            if client.is_available():
                logger.info(f"[ComfyUI] 已就绪（耗时 {waited} 秒，{client.base_url}）")
                return True
    
    logger.error("[ComfyUI] 等待超时，启动失败")
    return False
