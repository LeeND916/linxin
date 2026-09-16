"""页面蓝图 — 渲染前端页面"""
from flask import Blueprint, render_template

pages_bp = Blueprint('pages', __name__)


@pages_bp.route('/')
def index():
    return render_template('index.html')


@pages_bp.route('/voice-preview')
def voice_preview():
    """TTS 音色试听页面"""
    return render_template('voice_preview.html')
