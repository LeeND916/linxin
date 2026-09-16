# -*- coding: utf-8 -*-
"""人物肖像实验室 — 独立 Flask 应用（默认端口 5090）

设计原则：
  1. 不修改主游戏任何源码，独立进程、独立端口运行；
  2. 数据库、ComfyUI 客户端、提示词派生函数全部 import 主项目 backend/ 复用；
  3. 对 game.db 只读（只 SELECT，不 INSERT/UPDATE），生成的图片写到插件自己的
     outputs/ 目录，不污染 data/portraits（主游戏"最近肖像"扫描目录）。

启动：
    python -m plugins.portrait_lab.app        # 在 H:\\sim_life_game 下执行
或   plugins\\portrait_lab\\run.bat
"""

import os
import sys

# ── 保证可以 import 主项目的 backend 包（插件位于 <root>/plugins/portrait_lab/）──
_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_PLUGIN_DIR))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from flask import Flask, render_template, send_from_directory  # noqa: E402

from backend.config import Config  # noqa: E402  复用主项目 DB URI / 引擎参数
from backend.models import db      # noqa: E402  复用主项目 SQLAlchemy 实例与全部模型

# 主游戏前端静态目录（复用 portrait-presets.js 里的 PORTRAIT_VOCAB 词库，零拷贝）
GAME_STATIC_DIR = os.path.join(_PROJECT_ROOT, 'frontend', 'static')

# 实验室图片输出目录（与主游戏 data/portraits 分离）
LAB_OUTPUT_DIR = os.path.join(_PLUGIN_DIR, 'outputs')

DEFAULT_PORT = 5090


def create_lab_app(config_class=Config):
    """创建肖像实验室 Flask 应用（独立于主游戏 create_app）"""
    app = Flask(
        __name__,
        template_folder=os.path.join(_PLUGIN_DIR, 'templates'),
        static_folder=os.path.join(_PLUGIN_DIR, 'static'),
        static_url_path='/static',
    )
    app.config.from_object(config_class)
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    app.config['JSON_AS_ASCII'] = False
    app.config['LAB_OUTPUT_DIR'] = LAB_OUTPUT_DIR

    # 静态目录（模板内联 CSS/JS，本目录主要供将来扩展；缺失会让 Flask 报警，故确保存在）
    os.makedirs(app.static_folder, exist_ok=True)
    os.makedirs(LAB_OUTPUT_DIR, exist_ok=True)

    # 复用主项目的 db 实例（同一份 models，同一个 game.db）
    db.init_app(app)

    from plugins.portrait_lab.api import lab_bp
    app.register_blueprint(lab_bp)

    @app.route('/')
    def index():
        return render_template('portrait_lab.html')

    @app.route('/game-static/<path:filename>')
    def game_static(filename):
        """把主游戏 frontend/static 挂进来，直接复用 PORTRAIT_VOCAB 等前端资源"""
        return send_from_directory(GAME_STATIC_DIR, filename)

    @app.after_request
    def _no_cache(resp):
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp

    return app


if __name__ == '__main__':
    port = int(os.environ.get('PORTRAIT_LAB_PORT', DEFAULT_PORT))
    application = create_lab_app()
    print('=' * 60)
    print('  人物肖像实验室 (portrait_lab)')
    print(f'  地址: http://127.0.0.1:{port}')
    print(f'  数据库: {application.config["SQLALCHEMY_DATABASE_URI"]}（只读）')
    print(f'  出图目录: {LAB_OUTPUT_DIR}')
    print('=' * 60)
    sys.stdout.flush()
    application.run(debug=False, host='0.0.0.0', port=port, threaded=True)
