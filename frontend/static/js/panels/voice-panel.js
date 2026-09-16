/* 音色管理面板 (腾讯云 TTS) */

const VoicePanel = {
    _characters: [],
    _tencentVoices: [],
    _activeServiceType: null,

    async open() {
        const overlay = document.getElementById('voice-modal-overlay');
        if (overlay) overlay.style.display = 'flex';

        await this._loadData();
        this._render();
    },

    close() {
        const overlay = document.getElementById('voice-modal-overlay');
        if (overlay) overlay.style.display = 'none';
    },

    async _loadData() {
        try {
            const svcResp = await fetch('/api/tts/services');
            const svcData = await svcResp.json();
            if (svcData.success && svcData.data) {
                const active = svcData.data.find(s => s.is_active);
                this._activeServiceType = active ? active.service_type : null;
            }
        } catch (e) {
            console.warn('[VoicePanel] 获取 TTS 服务列表失败:', e);
            this._activeServiceType = null;
        }

        try {
            const charResp = await fetch('/api/character/list?_t=' + Date.now(), { cache: 'no-store' });
            const charData = await charResp.json();
            if (charData.success) {
                this._characters = charData.data || [];
            }
        } catch (e) {
            console.warn('[VoicePanel] 加载角色列表失败:', e);
            this._characters = [];
        }

        if (this._activeServiceType === 'tencent_tts') {
            try {
                const tencentResp = await fetch('/api/tts/tencent-voices');
                const tencentData = await tencentResp.json();
                if (tencentData.success) {
                    this._tencentVoices = [
                        { id: '', name: '不指定（跟随服务默认）', type: '', gender: '', emotion: false },
                        ...tencentData.data
                    ];
                }
            } catch (e) {
                console.warn('[VoicePanel] 加载腾讯云音色列表失败:', e);
            }
        }
    },

    _render() {
        const container = document.getElementById('voice-character-list');
        if (!container) return;

        if (this._characters.length === 0) {
            container.innerHTML = '<div class="voice-empty">暂无角色</div>';
            return;
        }

        if (this._activeServiceType !== 'tencent_tts') {
            container.innerHTML = '<div class="voice-empty">当前 TTS 服务未激活腾讯云，无法选择音色</div>';
            return;
        }

        container.innerHTML = this._characters.map(char => {
            const currentVoice = String(char.tencent_tts_voice_type || '');

            return `
                <div class="voice-character-card" data-character="${char.name}">
                    <div class="voice-card-header">
                        <h4>${char.name}</h4>
                    </div>
                    <div class="voice-card-body">
                        <div class="voice-tencent-select">
                            <label>腾讯云 TTS 音色：</label>
                            <select class="voice-select" onchange="VoicePanel._onTencentVoiceChange('${char.name}', this.value)">
                                ${this._tencentVoices.map(v => {
                                    const emotionTag = v.emotion ? ' ⭐' : '';
                                    const label = `${v.name}（${v.type} - ${v.gender}${emotionTag}）`;
                                    return `<option value="${v.id}" ${currentVoice === String(v.id) ? 'selected' : ''}>${label}</option>`;
                                }).join('')}
                            </select>
                            <button class="btn-small btn-test-voice" onclick="VoicePanel._testTencentVoice('${char.name}')">🔊 试听</button>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    },

    _onTencentVoiceChange(characterName, voiceType) {
        const char = this._characters.find(c => c.name === characterName);
        if (char) {
            char.tencent_tts_voice_type = voiceType || null;
        }
        this._saveTencentVoice(characterName, voiceType);
    },

    async _saveTencentVoice(characterName, voiceType) {
        try {
            const resp = await fetch(`/api/tts/characters/${encodeURIComponent(characterName)}/tencent-voice`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tencent_tts_voice_type: voiceType || null })
            });
            const data = await resp.json();
            if (data.success) {
                Toast.show('音色已保存', 'success');
            } else {
                Toast.show('保存失败：' + (data.error || '未知错误'), 'danger');
            }
        } catch (err) {
            console.error('[VoicePanel] 保存腾讯云音色失败:', err);
            Toast.show('保存失败：网络错误', 'danger');
        }
    },

    async _testTencentVoice(characterName) {
        const testText = '你好，我是' + characterName + '，很高兴认识你。今天天气真不错，我们一起出去走走吧。';

        Toast.show('正在合成语音...', 'info');

        try {
            const resp = await fetch('/api/tts/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: testText,
                    character_name: characterName
                })
            });

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.error || '合成失败');
            }

            const audioBlob = await resp.blob();
            const audioUrl = URL.createObjectURL(audioBlob);
            const audio = new Audio(audioUrl);
            audio.play();
            audio.onended = () => URL.revokeObjectURL(audioUrl);
            Toast.show('播放中...', 'success');
        } catch (err) {
            console.error('[VoicePanel] 试听失败:', err);
            Toast.show('试听失败：' + err.message, 'danger');
        }
    },

    init() {
        const openBtn = document.getElementById('btn-voice-panel');
        if (openBtn) {
            openBtn.addEventListener('click', () => this.open());
        }

        const closeBtn = document.getElementById('btn-close-voice');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => this.close());
        }

        const overlay = document.getElementById('voice-modal-overlay');
        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) this.close();
            });
        }
    }
};
