# -*- coding: utf-8 -*-
"""本机私有配置模板 —— 复制为 local_config.py 后按需填写。

    cp local_config.example.py local_config.py

用途：把「只属于你这台机器」的服务地址、可执行文件路径填在这里，
而不是改源码（改源码会在下次 pull 时冲突）。

取值优先级：环境变量 > local_config.py > 源码里的中性默认值
（源码默认值都是 localhost，所以什么都不填也能跑起来）。

注意：只有本模板文件会进仓库，local_config.py 已被 .gitignore 排除。
"""

# ── TTS：Qwen3-TTS 服务地址（自建/远程实例）──
QWEN3_TTS_SERVICE_URL = "http://127.0.0.1:9802"
QWEN3_CLASSIFIER_URL = "http://127.0.0.1:9803"
QWEN3_TTS_MODEL_PATH = ""            # 例："D:/models/qwen3-tts"

# ── Memos 私有服务（也可直接在游戏内的配置页填写）──
MEMOS_BASE_URL = ""

# ── ComfyUI 桌面端可执行文件路径（留空则由配置页指定）──
# 例：Windows r"C:\\Program Files\\ComfyUI\\ComfyUI.exe"
COMFYUI_EXE_PATH = ""

# ── 本地小模型（LM Studio / llama.cpp / Ollama 等 OpenAI 兼容端点）──
SMALL_MODEL_URL = "http://127.0.0.1:10039/v1"
