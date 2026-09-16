# -*- coding: utf-8 -*-
"""角色日常肖像批量生成 - 发型篇（character_id=0 的全部 hairstyle，每女主固定 seed）
基于 _生成内衣肖像.py 改造：
  - 服装固定为：白色宫崎骏风卡通图案T恤 + 浅蓝牛仔短裤 + 白色帆布鞋
  - 发型从 outfit_components 中 character_id=0 的 type='hairstyle' 选取（40 种，每人全量生成）
  - 每个女主 seed 固定为 character.outfit_seed（保证同一人、同款衣服、仅发型不同，便于对比）
  - 图片输出到 data/character_gen/发型 目录，文件名 = {女主名}_{发型name}_{style_tags}.png
  - 发型提示词用 outfit_components 的 name 字段
  - description 字段渲染到图片右下角白色半透明底纹上
  - appearance 字段已移除发型描述，避免与生成图前后不一致
用法：python _生成发型肖像.py
依赖：ComfyUI 须在运行中（默认 http://127.0.0.1:3099 或改 COMFYUI_URL）
"""
import json, os, time, random, urllib.request, urllib.parse, sqlite3, re, io
from PIL import Image, ImageDraw, ImageFont

# ════════════════════ 配置 ════════════════════
COMFYUI_URL = "http://127.0.0.1:3099"
WORKFLOW_NAME = "image_z_image_turbo.json"  # 工作流模板文件名
OUTFIT_DESC = "白色宫崎骏风卡通图案T恤、浅蓝牛仔短裤、白色帆布鞋"  # 固定服装
# ══════════════════════════════════════════════

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "character_gen", "发型")  # data/character_gen/发型
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)  # 注：此处得到 backend/，非项目根（原有行为，待确认）
DB_PATH = os.path.join(PROJECT_ROOT, "data", "game.db")
WF_PATH = os.path.join(PROJECT_ROOT, "data", "comfyui", "workflows", WORKFLOW_NAME)

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── 加载工作流模板 ──
with open(WF_PATH, "r", encoding="utf-8") as f:
    raw = json.load(f)
nodes = raw.get("nodes", [])
links = raw.get("links", [])
link_map = {}
for link in links:
    if len(link) < 6:
        continue
    _, sn, ss, tn, ts, _ = link
    link_map.setdefault(str(tn), {})[ts] = [str(sn), ss]
NON_EXEC = {"MarkdownNote", "Note", "PrimitiveNode", "Reroute"}
workflow = {}
for n in nodes:
    if n.get("type") in NON_EXEC:
        continue
    nid = str(n["id"])
    entry = {"class_type": n["type"], "inputs": {}}
    wi = 0
    for si, inp in enumerate(n.get("inputs", [])):
        sn = inp.get("name", f"slot_{si}")
        if inp.get("link") is not None:
            entry["inputs"][sn] = link_map.get(nid, {}).get(si, [None, 0])
        else:
            wv = n.get("widgets_values", [])
            entry["inputs"][sn] = wv[wi] if wi < len(wv) else ""
            wi += 1
    workflow[nid] = entry

# ── ComfyUI API 封装 ──
def queue_prompt(prompt_dict):
    data = json.dumps({"prompt": prompt_dict, "client_id": "batch_hairstyle"}).encode("utf-8")
    req = urllib.request.Request(f"{COMFYUI_URL}/prompt", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8")).get("prompt_id")

def wait_for_result(prompt_id, timeout=180):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10) as resp:
                history = json.loads(resp.read().decode("utf-8"))
            if history and prompt_id in history:
                r = history[prompt_id]
                status = r.get("status", {})
                if status.get("completed"):
                    return r.get("outputs", {})
                if status.get("status_str") == "error":
                    return None
        except Exception:
            pass
        time.sleep(2)
    return None

def download_image(filename, subfolder="", folder_type="output"):
    params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
    qs = urllib.parse.urlencode(params)
    with urllib.request.urlopen(f"{COMFYUI_URL}/view?{qs}", timeout=30) as resp:
        return resp.read()

# ── 在图片右下角叠加 description 白色半透明底纹 ──
FONT_PATH = "C:/Windows/Fonts/simhei.ttf"

def _load_font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()

def wrap_text(draw, text, font, max_width):
    """按像素宽度贪婪折行（中文按字符断行）。"""
    lines = []
    for para in (text or "").split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            test = cur + ch
            if draw.textlength(test, font=font) <= max_width:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines

def add_description_overlay(image_bytes, description, margin=22):
    """把 description 渲染到图片右下角，白色半透明底 + 深色文字，返回 PNG 字节。"""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    W, H = img.size
    font = _load_font(22)
    pad = 16
    box_w = int(W * 0.62)
    max_text_w = box_w - 2 * pad
    tmp = ImageDraw.Draw(img)
    lines = wrap_text(tmp, description, font, max_text_w)
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
    od.rectangle([x0, y0, x1, y1], fill=(255, 255, 255, 185))  # 白色半透明底
    text_x = x0 + pad
    text_y = y0 + pad
    for i, line in enumerate(lines):
        od.text((text_x, text_y + i * line_h), line, font=font, fill=(20, 20, 20, 255))
    combined = Image.alpha_composite(img, overlay)
    out = io.BytesIO()
    combined.convert("RGB").save(out, format="PNG")
    return out.getvalue()

# ── 构建角色提示词 ──
def build_identity(age, identity_label):
    age_label = f"{age}岁" if age else ""
    parts = [p for p in [f"女{age_label}", identity_label] if p]
    return "·".join(parts)

def safe_filename(name):
    for ch in ["/", "\\", ":", "｜", "|", "*", "?", '"', "<", ">", " "]:
        name = name.replace(ch, "_")
    return name[:60]

# ── 主流程 ──
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

chars = conn.execute(
    "SELECT id, name, age, identity_label, appearance, personality_type, outfit_seed "
    "FROM character WHERE gender='female' AND id != 8 ORDER BY id"
).fetchall()

# character_id=0 的全部发型（每人都生成这 40 种）
hairstyles = conn.execute(
    "SELECT id, name, description, color, style_tags FROM outfit_components "
    "WHERE character_id=0 AND type='hairstyle' ORDER BY sort_order"
).fetchall()

# SMOKE=1 时只跑 1 女主 × 2 发型，用于验证管线
import os as _os
if _os.environ.get("SMOKE") == "1":
    chars = chars[:1]
    hairstyles = hairstyles[:2]
print(f"可用发型数（character_id=0）: {len(hairstyles)}  待处理女主: {len(chars)}")

total = success = 0
fail_list = []

for c in chars:
    # 每女主固定 seed，保证同一人不同发型可对比
    seed = c["outfit_seed"] if c["outfit_seed"] is not None else random.randint(0, 2**63 - 1)
    identity = build_identity(c["age"], c["identity_label"])
    print(f"\n── {c['name']}（{c['identity_label']}）seed={seed} 共 {len(hairstyles)} 种发型 ──")

    for comp in hairstyles:
        safe = safe_filename(comp["name"])
        tag = safe_filename(comp["style_tags"] or "default")
        output_path = os.path.join(OUTPUT_DIR, f"{c['name']}_{safe}_{tag}.png")

        if os.path.exists(output_path):
            print(f"  ⏭ 跳过: {safe}")
            total += 1; success += 1
            continue

        prompt = (
            "cinematic lighting, masterpiece, best quality, ultra detailed, 8k, "
            "电影级艺术画风，大师级构图，8K超高清，精美细节，"
            "治愈系氛围，温暖治愈，宁静安心，"
            "全身照，全身站立，人物居中，"
            "书房背景，书架与书桌，自然窗光，"
            "面带温柔微笑，双手自然垂落身前，"
            f"一位{identity}，{c['appearance'] or ''}，{c['personality_type'] or ''}气质，"
            f"发型是{comp['name']}，"                       # 关键：发型名
            f"穿着{OUTFIT_DESC}，"                          # 关键：固定服装
            f"{comp['description'][:60]}，"                # 发型细节补充（截断避免超长）
            "完整日常穿搭，清新休闲，"
            "柔和自然光线，低饱和度色调，温暖色调，柔焦效果，电影感调色，"
            "摄影级真实感，高级感，艺术写真"
        )

        w = json.loads(json.dumps(workflow))
        w["45"]["inputs"]["text"] = prompt
        w["41"]["inputs"]["width"] = 768
        w["41"]["inputs"]["height"] = 1344
        w["44"]["inputs"]["seed"] = seed                    # 每女主固定 seed
        w["44"]["inputs"]["steps"] = 9
        w["44"]["inputs"]["cfg"] = 1.0
        w["44"]["inputs"]["sampler_name"] = "euler"
        w["44"]["inputs"]["scheduler"] = "simple"
        w["44"]["inputs"]["denoise"] = 1.0
        w["9"]["inputs"]["filename_prefix"] = f"hair_{c['name']}_{safe[:20]}"
        w["48"]["inputs"]["strength"] = 0.0

        total += 1
        try:
            pid = queue_prompt(w)
            if not pid:
                print(f"  ✗ 提交失败: {safe}")
                fail_list.append(f"{c['name']}_{safe}")
                continue
            outputs = wait_for_result(pid)
            if not outputs:
                print(f"  ✗ 超时: {safe}")
                fail_list.append(f"{c['name']}_{safe}")
                continue
            saved = False
            for nd, out in outputs.items():
                if "images" in out:
                    for img in out["images"]:
                        data = download_image(
                            img["filename"], img.get("subfolder", ""), img.get("type", "output"))
                        if data:
                            # 把 description 叠加到图片右下角白色半透明底
                            try:
                                data = add_description_overlay(data, comp["description"] or "")
                            except Exception as e:
                                print(f"  ⚠ 叠加描述失败(保留原图): {e}")
                            with open(output_path, "wb") as f:
                                f.write(data)
                            saved = True; break
                if saved: break
            if saved:
                print(f"  ✓ {safe}")
                success += 1
            else:
                print(f"  ✗ 下载失败: {safe}")
                fail_list.append(f"{c['name']}_{safe}")
        except Exception as e:
            print(f"  ✗ 异常: {e}")
            fail_list.append(f"{c['name']}_{safe}")

conn.close()
print(f"\n{'='*60}\n发型生成完成！总计 {total}  成功 {success}  失败 {len(fail_list)}")
print(f"输出: {OUTPUT_DIR}")
if fail_list:
    print(f"失败列表: {fail_list}")
print(f"{'='*60}")
