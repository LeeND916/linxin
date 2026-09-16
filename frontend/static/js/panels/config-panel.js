/** 系统设置面板 — 大模型 / TTS / ComfyUI / Embedding */
const ConfigPanel = {
    currentConfig: null,
    editingId: null,
    _cachedConfigs: [],
    _ttsCharacters: [],
    _ttsServices: [],
    _comfyuiConfigs: [],
    _comfyuiWorkflows: [],
    _embeddingConfigs: [],
    _comfyuiEditingId: null,
    _comfyuiWorkflowEditingId: null,
    _comfyuiWorkflowEditingPath: '',
    _comfyuiWorkflowActionPending: false,
    _embeddingEditingId: null,
    _memosConfigs: [],
    _memosEditingId: null,
    _smallModelActive: null,
    _cachedSmallModels: [],
    _smallModelEditingId: null,

    init() {
        // 打开/关闭设置弹窗
        document.getElementById('btn-llm-config').addEventListener('click', () => this.open());
        document.getElementById('btn-close-settings').addEventListener('click', () => this.close());
        document.getElementById('settings-modal-overlay').addEventListener('click', (e) => {
            if (e.target.id === 'settings-modal-overlay') this.close();
        });

        // Tab 切换
        document.querySelectorAll('.settings-tab').forEach(tab => {
            tab.addEventListener('click', () => this._switchTab(tab.dataset.tab));
        });

        // ---- LLM Tab ----
        document.getElementById('btn-cancel-form').addEventListener('click', () => this.resetForm());
        document.getElementById('btn-test-config').addEventListener('click', () => this.testConnection());
        document.getElementById('config-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.saveConfig();
        });

        // ---- 小模型 Tab ----
        document.getElementById('smallmodel-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.saveSmallModel();
        });
        document.getElementById('btn-smallmodel-test').addEventListener('click', () => this.testSmallModelConnection());
        document.getElementById('btn-smallmodel-cancel').addEventListener('click', () => this.resetSmallModelForm());

        // Provider 选择时自动填充 API URL
        document.getElementById('config-provider').addEventListener('change', (e) => {
            this._onProviderChange(e.target.value);
        });

        // ---- TTS Tab ---- (事件绑定在 _loadTtsTab 中动态处理)

        // ---- ComfyUI Tab ----
        document.getElementById('btn-test-comfyui')?.addEventListener('click', () => this._testComfyuiConnection());
        document.getElementById('btn-new-comfyui')?.addEventListener('click', () => this._newComfyuiConfig());
        document.getElementById('btn-save-comfyui')?.addEventListener('click', () => this._saveComfyuiConfig());
        document.getElementById('btn-cancel-comfyui')?.addEventListener('click', () => this._resetComfyuiForm());
        document.getElementById('btn-save-comfyui-workflow')?.addEventListener('click', () => this._saveComfyuiWorkflow());
        document.getElementById('btn-cancel-comfyui-workflow')?.addEventListener('click', () => this._resetComfyuiWorkflowForm());
        document.getElementById('btn-scan-comfyui-workflow')?.addEventListener('click', () => this._scanComfyuiWorkflows());
        document.getElementById('btn-upload-workflow')?.addEventListener('click', () => this._uploadComfyuiWorkflow());

        // ---- Embedding Tab ----
        document.getElementById('embedding-provider')?.addEventListener('change', (e) => {
            this._onEmbeddingProviderChange(e.target.value);
        });
        document.getElementById('btn-test-embedding')?.addEventListener('click', () => this._testEmbeddingConnection());
        document.getElementById('btn-new-embedding')?.addEventListener('click', () => this._newEmbeddingConfig());
        document.getElementById('btn-save-embedding')?.addEventListener('click', () => this._saveEmbeddingConfig());
        document.getElementById('btn-cancel-embedding')?.addEventListener('click', () => this._resetEmbeddingForm());

        // ---- 视觉回看 VLM Tab ----
        document.getElementById('btn-save-vlm')?.addEventListener('click', () => this._saveVLMConfig());
        document.getElementById('btn-test-vlm')?.addEventListener('click', () => this._testVLMConnection());
        document.getElementById('vlm-provider')?.addEventListener('change', (e) => this._onVLMProviderChange(e.target.value));

        // ---- Memos Tab ----
        document.getElementById('btn-test-memos')?.addEventListener('click', () => this._testMemosConnection());
        document.getElementById('btn-save-memos')?.addEventListener('click', () => this._saveMemosConfig());
        document.getElementById('btn-cancel-memos')?.addEventListener('click', () => this._resetMemosForm());
        document.getElementById('btn-sync-memos')?.addEventListener('click', () => this._manualMemosSync());
        document.querySelectorAll('.btn-toggle-password[data-target]').forEach(btn => {
            btn.addEventListener('click', () => {
                const input = document.getElementById(btn.dataset.target);
                const icon = btn.querySelector('.material-symbols-outlined');
                if (!input) return;
                input.type = input.type === 'password' ? 'text' : 'password';
                if (icon) icon.textContent = input.type === 'password' ? 'visibility' : 'visibility_off';
            });
        });

        this.loadConfigs();
    },

    open() {
        document.getElementById('settings-modal-overlay').style.display = 'flex';
        this.loadConfigs();
        this._loadTtsTab();
        this._loadMemosTab();
        NewsPanel.loadTab();
    },

    close() {
        document.getElementById('settings-modal-overlay').style.display = 'none';
    },

    /* ==================== Tab 切换 ==================== */
    _switchTab(tabName) {
        document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.settings-tab-content').forEach(c => c.classList.remove('active'));
        const tab = document.querySelector(`.settings-tab[data-tab="${tabName}"]`);
        if (tab) tab.classList.add('active');
        const content = document.getElementById(`settings-tab-${tabName}`);
        if (content) content.classList.add('active');

        // 切换 Tab 时按需加载数据
        if (tabName === 'comfyui') {
            this._loadComfyuiTab();
        } else if (tabName === 'embedding') {
            this._loadEmbeddingTab();
        } else if (tabName === 'memos') {
            this._loadMemosTab();
        } else if (tabName === 'news') {
            NewsPanel.loadTab();
        } else if (tabName === 'prompts') {
            if (typeof PromptPanel !== 'undefined') PromptPanel.init();
        } else if (tabName === 'vlm') {
            this._loadVLMTab();
        } else if (tabName === 'smallmodel') {
            this._loadSmallModelTab();
        }
    },

    /* ==================== LLM Tab ==================== */
    async loadConfigs() {
        try {
            const [activeResp, allResp] = await Promise.all([
                fetch('/api/config/llm'),
                fetch('/api/config/llm/all')
            ]);
            const activeData = await activeResp.json();
            const allData = await allResp.json();

            this.currentConfig = activeData.success ? activeData.data : null;
            this._cachedConfigs = allData.success ? (allData.data || []) : [];
            this.renderActiveConfig();
            this.renderConfigList(this._cachedConfigs);
        } catch (e) {
            console.error('加载 LLM 配置失败:', e);
        }
    },

    renderActiveConfig() {
        const el = document.getElementById('active-config-info');
        if (!this.currentConfig) {
            el.innerHTML = '<p class="no-config">暂无激活配置（LLM 不可用，使用模拟模式）</p>';
            return;
        }
        const providerNames = {
            'openai': 'OpenAI', 'deepseek': 'DeepSeek', 'zhipu': '智谱 (GLM)',
            'dashscope': '阿里 (DashScope)', 'siliconflow': 'SiliconFlow',
            'llm_studio': 'LLM Studio (本地)', 'ollama': 'Ollama (本地)', 'custom': 'Custom'
        };
        const displayProvider = providerNames[this.currentConfig.provider] || this.currentConfig.provider;
        el.innerHTML = `
            <div class="active-config-card">
                <div class="cfg-row"><span class="cfg-label">平台:</span> <span>${this.escapeHtml(displayProvider)}</span></div>
                <div class="cfg-row"><span class="cfg-label">URL:</span> <span class="cfg-mono">${this.escapeHtml(this.currentConfig.api_url)}</span></div>
                <div class="cfg-row"><span class="cfg-label">Model:</span> <span>${this.escapeHtml(this.currentConfig.model_name)}</span></div>
                <div class="cfg-row"><span class="cfg-label">Temperature:</span> <span>${this.currentConfig.temperature}</span></div>
                <div class="cfg-row"><span class="cfg-label">Max Tokens:</span> <span>${this.currentConfig.max_tokens}</span></div>
            </div>
        `;
    },

    renderConfigList(configs) {
        const el = document.getElementById('config-list');
        if (!configs.length) {
            el.innerHTML = '<p class="no-config">暂无配置组，请新增</p>';
            return;
        }
        el.innerHTML = configs.map(c => `
            <div class="config-item ${c.is_active ? 'active' : ''}" data-id="${c.id}">
                <div class="config-item-info">
                    <strong>${this.escapeHtml(c.model_name || '未命名')}</strong>
                    <small>${this.escapeHtml(c.provider)} · ${this.escapeHtml(c.api_url)}</small>
                    ${c.is_active ? '<span class="badge-active">激活</span>' : ''}
                </div>
                <div class="config-item-actions">
                    ${!c.is_active ? `<button class="btn-sm" data-action="activate" data-id="${c.id}">激活</button>` : ''}
                    <button class="btn-sm" data-action="edit" data-id="${c.id}">编辑</button>
                    ${!c.is_active ? `<button class="btn-sm btn-danger-sm" data-action="delete" data-id="${c.id}">删除</button>` : ''}
                </div>
            </div>
        `).join('');

        el.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const action = btn.dataset.action;
                const id = parseInt(btn.dataset.id);
                if (action === 'activate') this.activateConfig(id);
                else if (action === 'edit') this.editConfig(id);
                else if (action === 'delete') this.deleteConfig(id);
            });
        });
    },

    async activateConfig(id) {
        try {
            const resp = await fetch(`/api/config/llm/${id}/activate`, { method: 'PUT' });
            const data = await resp.json();
            if (data.success) {
                Toast.show('配置已激活', 'success');
                this.loadConfigs();
            } else {
                Toast.show(data.error || '激活失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    editConfig(id) {
        const cfg = this._cachedConfigs.find(c => c.id === id);
        if (!cfg) return;

        this.editingId = id;
        document.getElementById('form-title').textContent = '编辑配置';
        document.getElementById('config-id').value = cfg.id;
        // 自动纠正 provider：如果 URL 域名与 provider 不匹配，用 URL 反推正确的 provider
        const guessed = this._guessProviderFromUrl(cfg.api_url);
        document.getElementById('config-provider').value = (guessed && guessed !== cfg.provider) ? guessed : cfg.provider;
        document.getElementById('config-api-url').value = cfg.api_url;
        document.getElementById('config-api-key').value = '';
        document.getElementById('config-api-key').placeholder = '（留空保持不变）';
        document.getElementById('config-model').value = cfg.model_name;
        document.getElementById('config-temperature').value = cfg.temperature;
        document.getElementById('config-max-tokens').value = cfg.max_tokens;
        document.getElementById('btn-cancel-form').style.display = 'inline-block';

        document.getElementById('config-form').scrollIntoView({ behavior: 'smooth' });
    },

    resetForm() {
        this.editingId = null;
        document.getElementById('form-title').textContent = '新增配置';
        document.getElementById('config-id').value = '';
        document.getElementById('config-form').reset();
        document.getElementById('config-api-key').placeholder = 'sk-...';
        document.getElementById('config-temperature').value = '0.85';
        document.getElementById('config-max-tokens').value = '4000';
        document.getElementById('btn-cancel-form').style.display = 'none';
    },

    async saveConfig() {
        const id = document.getElementById('config-id').value;
        const body = {
            provider: document.getElementById('config-provider').value,
            api_url: document.getElementById('config-api-url').value,
            model_name: document.getElementById('config-model').value,
            temperature: parseFloat(document.getElementById('config-temperature').value),
            max_tokens: parseInt(document.getElementById('config-max-tokens').value),
        };

        const apiKey = document.getElementById('config-api-key').value;
        if (apiKey) {
            body.api_key = apiKey;
        }

        try {
            let resp;
            if (id) {
                resp = await fetch(`/api/config/llm/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
            } else {
                resp = await fetch('/api/config/llm', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
            }

            const data = await resp.json();
            if (data.success) {
                Toast.show(id ? '配置已更新' : '配置已创建', 'success');
                this.resetForm();
                this.loadConfigs();
            } else {
                Toast.show(data.error || '保存失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    async deleteConfig(id) {
        try {
            const resp = await fetch(`/api/config/llm/${id}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.success) {
                Toast.show('配置已删除', 'success');
                this.loadConfigs();
            } else {
                Toast.show(data.error || '删除失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    async testConnection() {
        const apiUrl = document.getElementById('config-api-url').value;
        const apiKey = document.getElementById('config-api-key').value;
        const model = document.getElementById('config-model').value;

        if (!apiUrl || !apiKey) {
            Toast.show('请填写 API URL 和 API Key', 'warning');
            return;
        }

        const btn = document.getElementById('btn-test-config');
        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '测试中...';
        btn.classList.add('btn-loading');

        try {
            const resp = await fetch('/api/config/llm/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_url: apiUrl, api_key: apiKey, model_name: model })
            });
            const data = await resp.json();
            if (data.success) {
                Toast.show(`连接成功！延迟 ${data.latency_ms}ms`, 'success');
            } else {
                Toast.show(data.error || '连接失败', 'danger');
            }
        } catch (e) {
            Toast.show('测试请求失败', 'danger');
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
            btn.classList.remove('btn-loading');
        }
    },

    /** Provider 切换时自动填充 API URL */
    _onProviderChange(provider) {
        const urlMap = {
            'openai': 'https://api.openai.com/v1/chat/completions',
            'deepseek': 'https://api.deepseek.com/v1/chat/completions',
            'zhipu': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
            'dashscope': 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
            'siliconflow': 'https://api.siliconflow.cn/v1/chat/completions',
            'llm_studio': 'http://127.0.0.1:1234/v1/chat/completions',
            'ollama': 'http://127.0.0.1:11434/v1/chat/completions',
            'custom': '',
        };
        const url = urlMap[provider] || '';
        document.getElementById('config-api-url').value = url;
    },

    /* ==================== 小模型 Tab ==================== */
    async _loadSmallModelTab() {
        try {
            const [activeResp, allResp] = await Promise.all([
                fetch('/api/config/smallmodel'),
                fetch('/api/config/smallmodel/all')
            ]);
            const activeData = await activeResp.json();
            const allData = await allResp.json();
            this._smallModelActive = activeData.success ? activeData.data : null;
            this._cachedSmallModels = allData.success ? (allData.data || []) : [];
            this.renderSmallModelActive();
            this.renderSmallModelList(this._cachedSmallModels);
        } catch (e) {
            console.error('加载小模型配置失败:', e);
        }
    },

    renderSmallModelActive() {
        const el = document.getElementById('smallmodel-active-info');
        if (!this._smallModelActive) {
            el.innerHTML = '<p class="no-config">暂无激活配置（相关功能将降级）</p>';
            return;
        }
        const c = this._smallModelActive;
        el.innerHTML = `
            <div class="active-config-card">
                <div class="cfg-row"><span class="cfg-label">名称:</span> <span>${this.escapeHtml(c.name || '未命名')}</span></div>
                <div class="cfg-row"><span class="cfg-label">URL:</span> <span class="cfg-mono">${this.escapeHtml(c.api_url)}</span></div>
                <div class="cfg-row"><span class="cfg-label">Model:</span> <span>${this.escapeHtml(c.model_name)}</span></div>
            </div>`;
    },

    renderSmallModelList(configs) {
        const el = document.getElementById('smallmodel-config-list');
        if (!configs.length) {
            el.innerHTML = '<p class="no-config">暂无配置，请新增</p>';
            return;
        }
        el.innerHTML = configs.map(c => `
            <div class="config-item ${c.is_active ? 'active' : ''}" data-id="${c.id}">
                <div class="config-item-info">
                    <strong>${this.escapeHtml(c.name || '未命名')}</strong>
                    <small>${this.escapeHtml(c.api_url)} · ${this.escapeHtml(c.model_name)}</small>
                    ${c.is_active ? '<span class="badge-active">激活</span>' : ''}
                </div>
                <div class="config-item-actions">
                    ${!c.is_active ? `<button class="btn-sm" data-action="activate" data-id="${c.id}">激活</button>` : ''}
                    <button class="btn-sm" data-action="edit" data-id="${c.id}">编辑</button>
                    ${!c.is_active ? `<button class="btn-sm btn-danger-sm" data-action="delete" data-id="${c.id}">删除</button>` : ''}
                </div>
            </div>`).join('');
        el.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', () => {
                const action = btn.dataset.action;
                const id = parseInt(btn.dataset.id);
                if (action === 'activate') this.activateSmallModel(id);
                else if (action === 'edit') this.editSmallModel(id);
                else if (action === 'delete') this.deleteSmallModel(id);
            });
        });
    },

    editSmallModel(id) {
        const cfg = this._cachedSmallModels.find(c => c.id === id);
        if (!cfg) return;
        this._smallModelEditingId = id;
        document.getElementById('smallmodel-form-title').textContent = '编辑小模型配置';
        document.getElementById('smallmodel-id').value = cfg.id;
        document.getElementById('smallmodel-name').value = cfg.name || '';
        document.getElementById('smallmodel-api-url').value = cfg.api_url;
        document.getElementById('smallmodel-api-key').value = '';
        document.getElementById('smallmodel-api-key').placeholder = '（留空保持不变）';
        document.getElementById('smallmodel-model').value = cfg.model_name;
        document.getElementById('btn-smallmodel-cancel').style.display = 'inline-block';
        document.getElementById('smallmodel-form').scrollIntoView({ behavior: 'smooth' });
    },

    resetSmallModelForm() {
        this._smallModelEditingId = null;
        document.getElementById('smallmodel-form-title').textContent = '新增小模型配置';
        document.getElementById('smallmodel-id').value = '';
        document.getElementById('smallmodel-form').reset();
        document.getElementById('smallmodel-api-key').placeholder = '（可留空）';
        document.getElementById('btn-smallmodel-cancel').style.display = 'none';
    },

    async saveSmallModel() {
        const id = document.getElementById('smallmodel-id').value;
        const body = {
            name: document.getElementById('smallmodel-name').value,
            api_url: document.getElementById('smallmodel-api-url').value,
            model_name: document.getElementById('smallmodel-model').value,
        };
        const apiKey = document.getElementById('smallmodel-api-key').value;
        if (apiKey) body.api_key = apiKey;
        try {
            let resp;
            if (id) {
                resp = await fetch(`/api/config/smallmodel/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
            } else {
                resp = await fetch('/api/config/smallmodel', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
            }
            const data = await resp.json();
            if (data.success) {
                Toast.show(id ? '配置已更新' : '配置已创建', 'success');
                this.resetSmallModelForm();
                this._loadSmallModelTab();
            } else {
                Toast.show(data.error || '保存失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    async activateSmallModel(id) {
        try {
            const resp = await fetch(`/api/config/smallmodel/${id}/activate`, { method: 'PUT' });
            const data = await resp.json();
            if (data.success) {
                Toast.show('配置已激活', 'success');
                this._loadSmallModelTab();
            } else {
                Toast.show(data.error || '激活失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    async deleteSmallModel(id) {
        try {
            const resp = await fetch(`/api/config/smallmodel/${id}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.success) {
                Toast.show('配置已删除', 'success');
                this._loadSmallModelTab();
            } else {
                Toast.show(data.error || '删除失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    async testSmallModelConnection() {
        const apiUrl = document.getElementById('smallmodel-api-url').value;
        const apiKey = document.getElementById('smallmodel-api-key').value;
        const model = document.getElementById('smallmodel-model').value;
        if (!apiUrl || !model) {
            Toast.show('请填写访问地址和模型名称', 'warning');
            return;
        }
        const btn = document.getElementById('btn-smallmodel-test');
        const orig = btn.textContent;
        btn.disabled = true;
        btn.textContent = '测试中...';
        try {
            const resp = await fetch('/api/config/smallmodel/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_url: apiUrl, api_key: apiKey, model_name: model })
            });
            const data = await resp.json();
            if (data.success) Toast.show('连接成功', 'success');
            else Toast.show(data.error || '连接失败', 'danger');
        } catch (e) {
            Toast.show('测试请求失败', 'danger');
        } finally {
            btn.disabled = false;
            btn.textContent = orig;
        }
    },

    /** 根据 API URL 反推 provider（编辑时自动纠正不一致的 provider） */
    _guessProviderFromUrl(url) {
        if (!url) return null;
        const lower = url.toLowerCase();
        if (lower.includes('deepseek')) return 'deepseek';
        if (lower.includes('openai')) return 'openai';
        if (lower.includes('bigmodel.cn')) return 'zhipu';
        if (lower.includes('dashscope') || lower.includes('aliyuncs')) return 'dashscope';
        if (lower.includes('siliconflow')) return 'siliconflow';
        if (lower.includes('127.0.0.1:1234') || lower.includes('localhost:1234')) return 'llm_studio';
        if (lower.includes('11434')) return 'ollama';
        return null;
    },

    /* ==================== TTS Tab ==================== */
    async _loadTtsTab() {
        // 加载服务列表
        await this._loadTtsServices();
        // 先绑定事件
        document.getElementById('btn-new-tts-service')?.addEventListener('click', () => this._newTtsService());
        // 加载角色列表
        await this._loadTtsCharacters();
    },

    /* ----- TTS 服务管理 ----- */
    async _loadTtsServices() {
        try {
            const resp = await fetch('/api/tts/services');
            const data = await resp.json();
            this._ttsServices = data.success ? (data.data || []) : [];
        } catch (e) {
            console.error('加载 TTS 服务列表失败:', e);
            this._ttsServices = [];
        }
        this._renderTtsServices();
    },

    _renderTtsServices() {
        const select = document.getElementById('tts-service-select');
        const cardsEl = document.getElementById('tts-service-cards');
        if (!select || !cardsEl) return;

        // 更新下拉
        select.innerHTML = '<option value="">-- 选择已有服务或新增 --</option>' +
            this._ttsServices.map(s => {
                const name = this._truncate(s.service_name || '', 40);
                const activeMark = s.is_active ? ' [当前]' : '';
                return `<option value="${s.id}">${name}${activeMark}</option>`;
            }).join('');

        select.onchange = () => {
            const id = parseInt(select.value);
            if (id) this._editTtsService(id);
        };

        // 渲染服务卡片
        if (!this._ttsServices.length) {
            cardsEl.innerHTML = '<p class="no-config">暂无服务配置</p>';
            return;
        }

        cardsEl.innerHTML = this._ttsServices.map(s => {
            const isTencent = s.service_type === 'tencent_tts';
            const summary = isTencent
                ? `腾讯云 TTS (SecretId: ${s.tencent_secret_id || '***'})`
                : this.escapeHtml(s.api_url || '');
            return `
            <div class="config-item ${s.is_active ? 'active' : ''}" data-service-id="${s.id}" id="tts-service-card-${s.id}">
                <div class="config-item-info">
                    <strong>${this.escapeHtml(s.service_name || '')}</strong>
                    <small class="cfg-mono">${summary}</small>
                    ${s.is_active ? '<span class="badge-active">激活</span>' : ''}
                </div>
                <div class="config-item-actions">
                    ${!s.is_active ? `<button class="btn-sm btn-activate" data-action="activate" data-id="${s.id}">激活</button>` : ''}
                    <button class="btn-sm" data-action="edit" data-id="${s.id}">编辑</button>
                    <button class="btn-sm" data-action="test" data-id="${s.id}">测试</button>
                    ${!s.is_active ? `<button class="btn-sm btn-danger-sm" data-action="delete" data-id="${s.id}">删除</button>` : ''}
                </div>
            </div>
        `;
        }).join('');

        cardsEl.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const action = btn.dataset.action;
                const id = parseInt(btn.dataset.id);
                if (action === 'activate') this._activateTtsService(id);
                else if (action === 'edit') this._editTtsService(id);
                else if (action === 'test') this._testTtsService(id);
                else if (action === 'delete') this._deleteTtsService(id);
            });
        });
    },

    _newTtsService() {
        document.getElementById('tts-service-select').value = '';
        const cardsEl = document.getElementById('tts-service-cards');
        if (!cardsEl) return;

        const formHtml = `
            <div class="tts-config-form" id="tts-service-edit-form" style="background:#1e2330;border:1px solid #00d4ff;border-radius:8px;padding:16px;margin-bottom:12px;">
                <input type="hidden" id="tts-service-editing-id" value="">
                <label>服务名称</label>
                <input type="text" id="tts-service-name" placeholder="如：腾讯云TTS">
                <label>服务类型</label>
                <select id="tts-service-type">
                    <option value="voice_design">VoiceDesign（文本描述音色）</option>
                    <option value="base">Base（上传音频克隆）</option>
                    <option value="tencent_tts">腾讯云 TTS（云端合成）</option>
                </select>
                <div id="tts-qwen-fields">
                    <label>API 地址</label>
                    <input type="text" id="tts-service-api-url" placeholder="http://127.0.0.1:9802">
                    <label>API Token</label>
                    <input type="password" id="tts-service-api-token" placeholder="留空表示无需认证">
                    <label>模型路径</label>
                    <input type="text" id="tts-service-model-path" placeholder="H:/qwen3-tts-models/...">
                </div>
                <div id="tts-tencent-fields" style="display:none;">
                    <label>SecretId</label>
                    <input type="password" id="tts-tencent-secret-id" placeholder="AKIDxxxxxxxx">
                    <label>SecretKey</label>
                    <input type="password" id="tts-tencent-secret-key" placeholder="xxxxxxxx">
                    <label>AppId（可选）</label>
                    <input type="text" id="tts-tencent-app-id" placeholder="如：1300000000">
                </div>
                <div class="form-actions">
                    <button type="button" class="btn-test" id="btn-test-tts-service">测试连接</button>
                    <button type="button" class="btn-cancel" id="btn-cancel-tts-service">取消</button>
                    <button type="button" class="btn-primary" id="btn-save-tts-service">保存</button>
                </div>
            </div>
        `;

        cardsEl.insertAdjacentHTML('beforeend', formHtml);
        // 服务类型切换时显示/隐藏对应字段
        document.getElementById('tts-service-type').addEventListener('change', (e) => {
            const isTencent = e.target.value === 'tencent_tts';
            document.getElementById('tts-qwen-fields').style.display = isTencent ? 'none' : 'block';
            document.getElementById('tts-tencent-fields').style.display = isTencent ? 'block' : 'none';
        });
        document.getElementById('btn-save-tts-service').addEventListener('click', () => this._saveTtsService());
        document.getElementById('btn-cancel-tts-service').addEventListener('click', () => {
            document.getElementById('tts-service-edit-form')?.remove();
        });
        document.getElementById('btn-test-tts-service').addEventListener('click', () => this._testTtsServiceCurrent());
    },

    _editTtsService(id) {
        const svc = this._ttsServices.find(s => s.id === id);
        if (!svc) return;

        // 先清除已有编辑表单
        document.getElementById('tts-service-edit-form')?.remove();

        const cardsEl = document.getElementById('tts-service-cards');
        if (!cardsEl) return;

        const isTencent = svc.service_type === 'tencent_tts';
        const formHtml = `
            <div class="tts-config-form" id="tts-service-edit-form" style="background:#1e2330;border:1px solid #00d4ff;border-radius:8px;padding:16px;margin-bottom:12px;">
                <input type="hidden" id="tts-service-editing-id" value="${svc.id}">
                <label>服务名称</label>
                <input type="text" id="tts-service-name" value="${this.escapeHtml(svc.service_name || '')}">
                <label>服务类型</label>
                <input type="text" id="tts-service-type-display" value="${isTencent ? '腾讯云 TTS（云端合成）' : (svc.service_type === 'voice_design' ? 'VoiceDesign（文本描述音色）' : 'Base（上传音频克隆）')}" readonly style="opacity:0.6;">
                <div id="tts-qwen-fields" style="display:${isTencent ? 'none' : 'block'};">
                    <label>API 地址</label>
                    <input type="text" id="tts-service-api-url" value="${this.escapeHtml(svc.api_url || '')}">
                    <label>API Token</label>
                    <input type="password" id="tts-service-api-token" placeholder="${svc.api_token ? '•••••••• 令牌已保存，留空保持不变' : '留空表示无需认证'}">
                    <label>模型路径</label>
                    <input type="text" id="tts-service-model-path" value="${this.escapeHtml(svc.model_path || '')}">
                </div>
                <div id="tts-tencent-fields" style="display:${isTencent ? 'block' : 'none'};">
                    <label>SecretId</label>
                    <input type="password" id="tts-tencent-secret-id" value="${this.escapeHtml(svc.tencent_secret_id || '')}" placeholder="已保存，留空保持不变">
                    <label>SecretKey</label>
                    <input type="password" id="tts-tencent-secret-key" placeholder="已保存，留空保持不变">
                    <label>AppId（可选）</label>
                    <input type="text" id="tts-tencent-app-id" value="${this.escapeHtml(svc.tencent_app_id || '')}">
                </div>
                <div class="form-actions">
                    <button type="button" class="btn-test" id="btn-test-tts-service">测试连接</button>
                    <button type="button" class="btn-cancel" id="btn-cancel-tts-service">取消</button>
                    <button type="button" class="btn-primary" id="btn-save-tts-service">保存</button>
                </div>
            </div>
        `;

        cardsEl.insertAdjacentHTML('afterbegin', formHtml);
        document.getElementById('btn-save-tts-service').addEventListener('click', () => this._saveTtsService());
        document.getElementById('btn-cancel-tts-service').addEventListener('click', () => {
            document.getElementById('tts-service-edit-form')?.remove();
        });
        document.getElementById('btn-test-tts-service').addEventListener('click', () => this._testTtsServiceCurrent());
        document.getElementById('tts-service-edit-form').scrollIntoView({ behavior: 'smooth' });
    },

    async _saveTtsService() {
        const editingIdEl = document.getElementById('tts-service-editing-id');
        const id = editingIdEl ? parseInt(editingIdEl.value) || null : null;

        const nameEl = document.getElementById('tts-service-name');
        const apiUrlEl = document.getElementById('tts-service-api-url');
        const apiTokenEl = document.getElementById('tts-service-api-token');
        const modelPathEl = document.getElementById('tts-service-model-path');

        const serviceName = (nameEl?.value || '').trim();
        const apiUrl = (apiUrlEl?.value || '').trim();
        const modelPath = (modelPathEl?.value || '').trim();

        let serviceType = 'voice_design';
        if (id) {
            const svc = this._ttsServices.find(s => s.id === id);
            if (svc) serviceType = svc.service_type;
        } else {
            const typeEl = document.getElementById('tts-service-type');
            serviceType = typeEl?.value || 'voice_design';
        }

        if (!serviceName) { Toast.show('请填写服务名称', 'warning'); return; }

        const body = {
            service_type: serviceType,
            service_name: serviceName,
            api_url: apiUrl,
            model_path: modelPath,
        };

        const tokenEl = document.getElementById('tts-service-api-token');
        const tokenVal = tokenEl?.value || '';
        if (tokenVal && !tokenVal.includes('•••')) {
            body.api_token = tokenVal;
        }

        // 腾讯云 TTS 字段
        if (serviceType === 'tencent_tts') {
            const sidEl = document.getElementById('tts-tencent-secret-id');
            const skeyEl = document.getElementById('tts-tencent-secret-key');
            const appIdEl = document.getElementById('tts-tencent-app-id');
            const sidVal = (sidEl?.value || '').trim();
            const skeyVal = (skeyEl?.value || '').trim();
            const appIdVal = (appIdEl?.value || '').trim();

            if (!id && (!sidVal || !skeyVal)) {
                Toast.show('请填写 SecretId 和 SecretKey', 'warning'); return;
            }
            // 新建时必须填写；编辑时留空表示不修改
            if (sidVal && !sidVal.includes('****')) body.tencent_secret_id = sidVal;
            if (skeyVal) body.tencent_secret_key = skeyVal;
            if (appIdVal) body.tencent_app_id = appIdVal;
        } else {
            if (!apiUrl) { Toast.show('请填写 API 地址', 'warning'); return; }
            if (!modelPath) { Toast.show('请填写模型路径', 'warning'); return; }
        }

        try {
            let resp;
            if (id) {
                resp = await fetch(`/api/tts/services/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
            } else {
                resp = await fetch('/api/tts/services', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });
            }
            const data = await resp.json();
            if (data.success) {
                Toast.show(`已保存：${serviceName}`, 'success');
                document.getElementById('tts-service-edit-form')?.remove();
                await this._loadTtsServices();
            } else {
                Toast.show(data.error || '保存失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    async _activateTtsService(id) {
        try {
            const resp = await fetch(`/api/tts/services/${id}/activate`, { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                Toast.show('服务已激活', 'success');
                await this._loadTtsServices();
            } else {
                Toast.show(data.error || '激活失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    async _testTtsService(id) {
        const svc = this._ttsServices.find(s => s.id === id);
        if (!svc) return;
        if (svc.service_type === 'tencent_tts') {
            await this._doTtsServiceTest(svc.id, null);
        } else {
            this._doTtsServiceTest(svc.id, svc.api_url);
        }
    },

    _testTtsServiceCurrent() {
        const apiUrl = document.getElementById('tts-service-api-url')?.value || '';
        const editingId = document.getElementById('tts-service-editing-id')?.value;
        const id = editingId ? parseInt(editingId) : null;
        // 判断是否是新建的腾讯云服务
        const typeEl = document.getElementById('tts-service-type');
        const isTencent = typeEl && typeEl.value === 'tencent_tts';
        this._doTtsServiceTest(id, isTencent ? null : apiUrl);
    },

    async _doTtsServiceTest(id, apiUrl) {
        const btn = document.getElementById('btn-test-tts-service');
        const origText = btn?.textContent || '';
        if (btn) { btn.disabled = true; btn.textContent = '测试中...'; }
        try {
            // 如果有已保存的 ID，直接请求后端测试接口
            if (id) {
                const resp = await fetch(`/api/tts/services/${id}/test`, { method: 'POST' });
                const data = await resp.json();
                if (data.success) {
                    Toast.show('服务连接正常', 'success');
                } else {
                    Toast.show(data.error || '连接失败', 'danger');
                }
                return;
            }
            if (!apiUrl) { Toast.show('请先保存后再测试', 'warning'); return; }
            const body = { api_url: apiUrl };
            if (id) body.id = id;
            const resp = await fetch(`/api/tts/services/${id}/test`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await resp.json();
            if (data.success) {
                Toast.show('TTS 服务连接成功', 'success');
            } else {
                Toast.show(data.error || '连接失败', 'danger');
            }
        } catch (e) {
            Toast.show('测试请求失败: ' + e.message, 'danger');
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = origText; }
        }
    },

    async _deleteTtsService(id) {
        if (!confirm('确定要删除该 TTS 服务配置吗？')) return;
        try {
            const resp = await fetch(`/api/tts/services/${id}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.success) {
                Toast.show('服务已删除', 'success');
                await this._loadTtsServices();
            } else {
                Toast.show(data.error || '删除失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    /* ----- 角色音色底色锚点编辑 ----- */
    async _loadTtsCharacters() {
        try {
            const resp = await fetch('/api/tts/characters');
            const data = await resp.json();
            this._ttsCharacters = data.success ? (data.data || []) : [];
        } catch (e) {
            console.error('加载角色列表失败:', e);
            this._ttsCharacters = [];
        }
        this._renderTtsCharacters();
    },

    _renderTtsCharacters() {
        const grid = document.getElementById('tts-character-grid');
        if (!grid) return;
        if (!this._ttsCharacters.length) {
            grid.innerHTML = '<p class="no-config">暂无角色数据</p>';
            return;
        }

        grid.innerHTML = this._ttsCharacters.map(char => `
            <div class="tts-char-card" id="tts-char-card-${char.id}">
                <div class="tts-char-card-header">
                    <span class="tts-char-avatar">${this.escapeHtml((char.name || '?')[0])}</span>
                    <strong>${this.escapeHtml(char.name || '')}</strong>
                </div>
                <textarea class="tts-char-base" id="tts-char-base-${char.id}" rows="4"
                    placeholder="角色的音色底色锚点描述...">${this.escapeHtml(char.character_base || '')}</textarea>
                <div class="tts-char-card-actions">
                    <button class="btn-sm" data-action="test-voice" data-id="${char.id}" data-name="${this.escapeHtml(char.name)}">试听</button>
                    <button class="btn-sm btn-primary-sm" data-action="save-base" data-id="${char.id}">保存</button>
                    <button class="btn-sm" data-action="toggle-samples" data-id="${char.id}">语音样本 ▾</button>
                </div>
                <div class="tts-char-samples" id="tts-char-samples-${char.id}" style="display:none;">
                    <div class="samples-list" id="tts-samples-list-${char.id}">
                        <p class="no-config" style="font-size:12px;">加载中...</p>
                    </div>
                    <div class="samples-upload-row" style="margin-top:8px;">
                        <input type="file" accept=".wav,.mp3" id="tts-sample-file-${char.id}" style="display:none;">
                        <input type="text" id="tts-sample-text-${char.id}" placeholder="样本文字" style="flex:1;">
                        <button class="btn-sm" data-action="upload-sample" data-id="${char.id}">上传</button>
                    </div>
                </div>
            </div>
        `).join('');

        // 绑定事件
        grid.querySelectorAll('[data-action]').forEach(btn => {
            const action = btn.dataset.action;
            const id = parseInt(btn.dataset.id);
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (action === 'test-voice') {
                    const name = btn.dataset.name;
                    this._testCharacterVoice(id, name);
                } else if (action === 'save-base') {
                    this._saveCharacterBase(id);
                } else if (action === 'toggle-samples') {
                    this._toggleSampleArea(id);
                } else if (action === 'upload-sample') {
                    this._uploadVoiceSample(id);
                }
            });
        });

        // 隐藏的文件输入触发
        grid.querySelectorAll('input[type="file"]').forEach(fileInput => {
            const charId = parseInt(fileInput.id.replace('tts-sample-file-', ''));
            const uploadBtn = grid.querySelector(`[data-action="upload-sample"][data-id="${charId}"]`);
            if (uploadBtn && !fileInput._bound) {
                fileInput._bound = true;
                uploadBtn.addEventListener('click', () => {
                    const input = document.getElementById(`tts-sample-file-${charId}`);
                    if (input && !input.value) {
                        alert('请先选择音频文件（.wav/.mp3）');
                        return;
                    }
                    this._uploadVoiceSample(charId);
                });
                // 让上传按钮触发文件选择
                fileInput.addEventListener('change', () => {
                    if (fileInput.files.length > 0) {
                        const fileName = fileInput.files[0].name;
                        const textInput = document.getElementById(`tts-sample-text-${charId}`);
                        if (textInput && !textInput.value) {
                            textInput.placeholder = `已选: ${fileName}`;
                        }
                        this._uploadVoiceSample(charId);
                    }
                });
            }
        });
    },

    _toggleSampleArea(charId) {
        const area = document.getElementById(`tts-char-samples-${charId}`);
        if (!area) return;
        const isHidden = area.style.display === 'none';
        area.style.display = isHidden ? 'block' : 'none';
        if (isHidden) this._loadVoiceSamples(charId);
    },

    async _saveCharacterBase(charId) {
        const textarea = document.getElementById(`tts-char-base-${charId}`);
        if (!textarea) return;
        const base = textarea.value.trim();
        try {
            const resp = await fetch(`/api/tts/characters/${charId}/base`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ character_base: base }),
            });
            const data = await resp.json();
            if (data.success) {
                Toast.show('已保存', 'success');
            } else {
                Toast.show(data.error || '保存失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    async _testCharacterVoice(charId, charName) {
        const textarea = document.getElementById(`tts-char-base-${charId}`);
        const base = textarea?.value?.trim() || '';
        const testText = `你好，我是${charName}，很高兴认识你。`;
        Toast.show('正在合成语音...', 'info');
        try {
            const resp = await fetch('/api/tts/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    character_name: charName,
                    text: testText,
                    dialogue_history: [],
                }),
            });
            if (!resp.ok) throw new Error('合成失败');
            const audioBlob = await resp.blob();
            const audio = new Audio(URL.createObjectURL(audioBlob));
            audio.play();
            audio.onended = () => URL.revokeObjectURL(audio.url);
        } catch (err) {
            Toast.show('试听失败: ' + err.message, 'danger');
        }
    },

    /* ----- 语音样本 ----- */
    async _loadVoiceSamples(charId) {
        const listEl = document.getElementById(`tts-samples-list-${charId}`);
        if (!listEl) return;
        try {
            const resp = await fetch(`/api/tts/characters/${charId}/samples`);
            const data = await resp.json();
            const samples = data.success ? (data.data || []) : [];
            if (!samples.length) {
                listEl.innerHTML = '<p class="no-config" style="font-size:12px;">暂无语音样本</p>';
                return;
            }
            listEl.innerHTML = samples.map(s => `
                <div class="sample-item" style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px;">
                    <span style="flex:1;">${this.escapeHtml(s.filename)}</span>
                    <span style="color:#888;">${this._truncate(s.sample_text || '', 20)}</span>
                    <button class="btn-sm btn-danger-sm" data-action="del-sample" data-sid="${s.id}" data-char="${charId}">删除</button>
                </div>
            `).join('');

            listEl.querySelectorAll('[data-action="del-sample"]').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this._deleteVoiceSample(parseInt(btn.dataset.sid), parseInt(btn.dataset.char));
                });
            });
        } catch (e) {
            console.error('加载语音样本失败:', e);
        }
    },

    async _uploadVoiceSample(charId) {
        const fileInput = document.getElementById(`tts-sample-file-${charId}`);
        const textInput = document.getElementById(`tts-sample-text-${charId}`);

        if (!fileInput || !fileInput.files.length) {
            Toast.show('请选择音频文件（.wav/.mp3）', 'warning');
            return;
        }

        const file = fileInput.files[0];
        const sampleText = textInput?.value?.trim() || file.name;

        const formData = new FormData();
        formData.append('file', file);
        formData.append('sample_text', sampleText);

        try {
            const resp = await fetch(`/api/tts/characters/${charId}/samples`, {
                method: 'POST',
                body: formData,
            });
            const data = await resp.json();
            if (data.success) {
                Toast.show('语音样本已上传', 'success');
                fileInput.value = '';
                if (textInput) textInput.value = '';
                await this._loadVoiceSamples(charId);
            } else {
                Toast.show(data.error || '上传失败', 'danger');
            }
        } catch (e) {
            Toast.show('上传失败: ' + e.message, 'danger');
        }
    },

    async _deleteVoiceSample(sampleId, charId) {
        if (!confirm('确定要删除该语音样本吗？')) return;
        try {
            const resp = await fetch(`/api/tts/samples/${sampleId}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.success) {
                Toast.show('样本已删除', 'success');
                await this._loadVoiceSamples(charId);
            } else {
                Toast.show(data.error || '删除失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    /* ==================== ComfyUI Tab ==================== */
    async _loadComfyuiTab() {
        // 不再自动回填表单，表单初始为空+新增按钮
        // 加载工作流列表
        let workflowLoaded = false;
        try {
            const wfResp = await fetch('/api/config/comfyui/workflow');
            const wfData = await wfResp.json();
            if (!wfResp.ok || !wfData.success) {
                throw new Error(wfData.error || `HTTP ${wfResp.status}`);
            }
            this._comfyuiWorkflows = Array.isArray(wfData.data) ? wfData.data : [];
            this.renderComfyuiWorkflowList(this._comfyuiWorkflows);
            workflowLoaded = true;
        } catch (e) {
            console.error('加载 ComfyUI 工作流失败:', e);
            const el = document.getElementById('comfyui-workflow-list');
            if (el) el.innerHTML = '<p class="no-config error-text">工作流列表加载失败，请重试</p>';
            Toast.show(`工作流列表加载失败: ${e.message || '网络错误'}`, 'danger');
        }

        // 加载已保存的配置列表
        await this._loadComfyuiConfigList();
        return workflowLoaded;
    },

    async _loadComfyuiConfigList() {
        try {
            const resp = await fetch('/api/config/comfyui/all');
            const data = await resp.json();
            this._comfyuiConfigs = data.success ? (data.data || []) : [];
            this.renderComfyuiConfigList(this._comfyuiConfigs);
        } catch (e) {
            console.error('加载 ComfyUI 配置列表失败:', e);
        }
    },

    renderComfyuiConfigList(configs) {
        const el = document.getElementById('comfyui-config-list');
        if (!el) return;
        if (!configs.length) {
            el.innerHTML = '<p class="no-config">暂无配置，请新增</p>';
            return;
        }
        el.innerHTML = configs.map(c => {
            const urlSummary = this._truncate(c.api_url || '', 40);
            return `
            <div class="config-item ${c.is_active ? 'active' : ''}" data-id="${c.id}">
                <div class="config-item-info">
                    <strong class="cfg-mono">${this.escapeHtml(urlSummary)}</strong>
                    ${c.is_active ? '<span class="badge-active">激活</span>' : ''}
                </div>
                <div class="config-item-actions">
                    ${!c.is_active ? `<button class="btn-sm btn-activate" data-action="activate" data-id="${c.id}">激活</button>` : ''}
                    <button class="btn-sm" data-action="edit" data-id="${c.id}">编辑</button>
                    ${!c.is_active ? `<button class="btn-sm btn-danger-sm" data-action="delete" data-id="${c.id}">删除</button>` : ''}
                </div>
            </div>
        `;
        }).join('');

        el.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', () => {
                const action = btn.dataset.action;
                const id = parseInt(btn.dataset.id);
                if (action === 'activate') this._activateComfyuiConfig(id);
                else if (action === 'edit') this._editComfyuiConfig(id);
                else if (action === 'delete') this._deleteComfyuiConfig(id);
            });
        });
    },

    _enterComfyuiEditMode(id) {
        this._comfyuiEditingId = id;
        document.getElementById('comfyui-editing-id').value = id || '';
        document.getElementById('btn-new-comfyui').style.display = 'none';
        document.getElementById('btn-save-comfyui').style.display = 'inline-block';
        document.getElementById('btn-cancel-comfyui').style.display = 'inline-block';
    },

    _newComfyuiConfig() {
        this._resetComfyuiForm();
        this._enterComfyuiEditMode(null);
    },

    _editComfyuiConfig(id) {
        const cfg = this._comfyuiConfigs.find(c => c.id === id);
        if (!cfg) {
            console.warn('[ConfigPanel] ComfyUI 配置未找到 id=', id);
            return;
        }
        this._enterComfyuiEditMode(id);
        document.getElementById('comfyui-api-url').value = cfg.api_url || '';
        // 不填充掩码密钥，只显示占位提示
        const keyInput = document.getElementById('comfyui-api-key');
        keyInput.type = 'password';
        keyInput.value = '';
        keyInput.placeholder = cfg.api_key ? '•••••••• 密钥已保存，留空保持不变' : '留空表示无需认证';
        document.querySelector('.comfyui-config-form').scrollIntoView({ behavior: 'smooth' });
    },

    _resetComfyuiForm() {
        this._comfyuiEditingId = null;
        document.getElementById('comfyui-editing-id').value = '';
        document.getElementById('comfyui-api-url').value = '';
        document.getElementById('comfyui-api-key').type = 'password';
        document.getElementById('comfyui-api-key').value = '';
        document.getElementById('comfyui-api-key').placeholder = '留空表示无需认证';
        document.getElementById('btn-new-comfyui').style.display = 'inline-block';
        document.getElementById('btn-save-comfyui').style.display = 'none';
        document.getElementById('btn-cancel-comfyui').style.display = 'none';
    },

    _setComfyuiButtonBusy(btn, busy, busyText = '处理中...') {
        if (!btn) return;
        if (busy) {
            if (!btn.dataset.originalText) btn.dataset.originalText = btn.textContent;
            btn.disabled = true;
            btn.classList.add('is-loading');
            btn.textContent = busyText;
        } else {
            btn.disabled = false;
            btn.classList.remove('is-loading');
            if (btn.dataset.originalText) {
                btn.textContent = btn.dataset.originalText;
                delete btn.dataset.originalText;
            }
        }
    },

    async _activateComfyuiConfig(id) {
        const btn = document.querySelector(`#comfyui-config-list [data-action="activate"][data-id="${id}"]`);
        if (btn?.disabled) return;
        this._setComfyuiButtonBusy(btn, true, '激活中...');
        try {
            const resp = await fetch(`/api/config/comfyui/${id}/activate`, { method: 'PUT' });
            const data = await resp.json();
            if (data.success) {
                Toast.show('ComfyUI 配置已激活', 'success');
                await this._loadComfyuiTab();
            } else {
                Toast.show(data.error || '激活失败', 'danger');
            }
        } catch (e) {
            Toast.show(`激活失败: ${e.message || '网络错误'}`, 'danger');
        } finally {
            this._setComfyuiButtonBusy(btn, false);
        }
    },

    async _deleteComfyuiConfig(id) {
        if (!confirm('确定要删除该 ComfyUI 配置吗？')) return;
        const btn = document.querySelector(`#comfyui-config-list [data-action="delete"][data-id="${id}"]`);
        if (btn?.disabled) return;
        this._setComfyuiButtonBusy(btn, true, '删除中...');
        try {
            const resp = await fetch(`/api/config/comfyui/${id}`, { method: 'DELETE' });
            const data = await resp.json();
            if (!resp.ok || !data.success) {
                throw new Error(data.error || `HTTP ${resp.status}`);
            }
            Toast.show('配置已删除', 'success');
            if (this._comfyuiEditingId === id) this._resetComfyuiForm();
            await this._loadComfyuiTab();
        } catch (e) {
            Toast.show(`配置删除失败: ${e.message || '网络错误'}`, 'danger');
        } finally {
            this._setComfyuiButtonBusy(btn, false);
        }
    },

    renderComfyuiWorkflowList(workflows) {
        const el = document.getElementById('comfyui-workflow-list');
        if (!el) return;
        if (!workflows || !workflows.length) {
            el.innerHTML = '<p class="no-config">暂无工作流，请新增或上传</p>';
            return;
        }
        el.innerHTML = workflows.map(wf => {
            const wfName = wf.name ? this.escapeHtml(wf.name) : '<span class="no-config">未命名</span>';
            const defaultBadge = wf.is_default ? '<span class="badge-active">默认</span>' : '';
            const fileBadge = wf.file_exists
                ? '<span class="badge-file-ok">文件 ✓</span>'
                : '<span class="badge-file-missing">文件 ✗ 缺失</span>';
            const sizeInfo = wf.file_exists ? ` · ${(wf.file_size/1024).toFixed(1)} KB` : '';
            const defaultBtn = wf.is_default
                ? `<button class="btn-sm" data-action="unset-default" data-id="${wf.id}">取消默认</button>`
                : `<button class="btn-sm" data-action="set-default" data-id="${wf.id}">设为默认</button>`;
            return `
            <div class="config-item${wf.is_default ? ' active' : ''}${wf.file_exists ? '' : ' missing-file'}" data-id="${wf.id}">
                <div class="config-item-info">
                    <strong class="cfg-mono">${wfName} ${defaultBadge} ${fileBadge} ${wf.is_active ? '<span class="badge-active">激活配置</span>' : ''}</strong>
                    <small class="cfg-mono">${this.escapeHtml(wf.workflow_path || '')}${sizeInfo}${wf.config_url ? ` · ${this.escapeHtml(this._truncate(wf.config_url, 40))}` : ''}</small>
                </div>
                <div class="config-item-actions">
                    ${defaultBtn}
                    <button class="btn-sm btn-danger-sm" data-action="delete" data-id="${wf.id}">删除</button>
                </div>
            </div>
        `;
        }).join('');

        el.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', () => {
                const action = btn.dataset.action;
                const id = parseInt(btn.dataset.id);
                if (action === 'set-default') this._setDefaultComfyuiWorkflow(id, true);
                else if (action === 'unset-default') this._setDefaultComfyuiWorkflow(id, false);
                else if (action === 'delete') this._deleteComfyuiWorkflow(id);
            });
        });
    },

    _enterComfyuiWorkflowEditMode(id) {
        this._comfyuiWorkflowEditingId = id;
        this._comfyuiWorkflowEditingPath = '';
        if (id) {
            const wf = this._comfyuiWorkflows.find(w => w.id === id);
            this._comfyuiWorkflowEditingPath = wf ? (wf.workflow_path || '') : '';
        }
        document.getElementById('comfyui-workflow-editing-id').value = id || '';
        document.getElementById('btn-save-comfyui-workflow').style.display = 'inline-block';
        document.getElementById('btn-cancel-comfyui-workflow').style.display = 'inline-block';
    },

    _editComfyuiWorkflow(id) {
        const wf = this._comfyuiWorkflows.find(w => w.id === id);
        if (!wf) return;
        this._enterComfyuiWorkflowEditMode(id);
        document.getElementById('comfyui-workflow-name').value = wf.name || '';
        document.getElementById('comfyui-workflow-default').checked = !!wf.is_default;
        document.querySelector('.comfyui-workflow-form').scrollIntoView({ behavior: 'smooth' });
    },

    _resetComfyuiWorkflowForm() {
        this._comfyuiWorkflowEditingId = null;
        this._comfyuiWorkflowEditingPath = '';
        document.getElementById('comfyui-workflow-editing-id').value = '';
        document.getElementById('comfyui-workflow-name').value = '';
        document.getElementById('comfyui-workflow-default').checked = false;
        const fileInput = document.getElementById('comfyui-workflow-file');
        if (fileInput) fileInput.value = '';
        document.getElementById('btn-save-comfyui-workflow').style.display = 'none';
        document.getElementById('btn-cancel-comfyui-workflow').style.display = 'none';
    },

    async _deleteComfyuiWorkflow(id) {
        if (!confirm('确定要删除该工作流配置吗？只会删除数据库记录，磁盘文件不会删除。')) return;
        const btn = document.querySelector(`#comfyui-workflow-list [data-action="delete"][data-id="${id}"]`);
        if (btn?.disabled) return;
        this._setComfyuiButtonBusy(btn, true, '删除中...');
        try {
            const resp = await fetch(`/api/config/comfyui/workflow/${id}`, { method: 'DELETE' });
            const data = await resp.json();
            if (!resp.ok || !data.success) {
                throw new Error(data.error || `HTTP ${resp.status}`);
            }
            Toast.show('工作流配置已删除，磁盘文件保留', 'success');
            if (this._comfyuiWorkflowEditingId === id) this._resetComfyuiWorkflowForm();
            await this._loadComfyuiTab();
        } catch (e) {
            Toast.show(`工作流删除失败: ${e.message || '网络错误'}`, 'danger');
            this._setComfyuiButtonBusy(btn, false);
        }
    },

    async _testComfyuiConnection() {
        const url = document.getElementById('comfyui-api-url').value;
        if (!url) {
            Toast.show('请填写 ComfyUI API URL', 'warning');
            return;
        }
        const btn = document.getElementById('btn-test-comfyui');
        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '测试中...';
        try {
            const resp = await fetch('/api/config/comfyui/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_url: url })
            });
            const data = await resp.json();
            if (data.success) {
                Toast.show(`ComfyUI 连接成功！延迟 ${data.latency_ms}ms`, 'success');
            } else {
                Toast.show(data.error || '连接失败', 'danger');
            }
        } catch (err) {
            Toast.show('测试请求失败: ' + err.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
        }
    },

    async _saveComfyuiConfig() {
        const id = this._comfyuiEditingId;
        const saveBtn = document.getElementById('btn-save-comfyui');
        if (saveBtn?.disabled) return;
        const apiUrl = document.getElementById('comfyui-api-url').value.trim();
        if (!apiUrl) {
            Toast.show('请填写 API URL', 'warning');
            return;
        }

        // 去重检查：查已保存配置中是否有相同 api_url
        try {
            const checkResp = await fetch('/api/config/comfyui/all');
            const checkData = await checkResp.json();
            if (checkData.success && checkData.data) {
                const dup = checkData.data.find(c => c.api_url === apiUrl && c.id !== id);
                if (dup) {
                    Toast.show('该地址已有配置，请直接编辑', 'warning');
                    return;
                }
            }
        } catch (e) {
            console.error('去重检查失败:', e);
        }

        const apiKey = document.getElementById('comfyui-api-key').value;
        const body = { api_url: apiUrl };
        this._setComfyuiButtonBusy(saveBtn, true, '保存中...');
        // 编辑时只在用户输入了新密钥时才发送
        if (apiKey && !apiKey.includes('***')) {
            body.api_key = apiKey;
        } else if (!id) {
            body.api_key = apiKey;
        }
        try {
            let resp;
            if (id) {
                resp = await fetch(`/api/config/comfyui/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
            } else {
                resp = await fetch('/api/config/comfyui', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
            }
            const data = await resp.json();
            if (!resp.ok || !data.success) {
                throw new Error(data.error || `HTTP ${resp.status}`);
            }
            Toast.show(id ? 'ComfyUI 配置已更新' : 'ComfyUI 配置已创建', 'success');
            this._resetComfyuiForm();
            await this._loadComfyuiConfigList();
        } catch (e) {
            Toast.show(`配置保存失败: ${e.message || '网络错误'}`, 'danger');
        } finally {
            this._setComfyuiButtonBusy(saveBtn, false);
        }
    },

    async _saveComfyuiWorkflow() {
        const id = this._comfyuiWorkflowEditingId;
        const saveBtn = document.getElementById('btn-save-comfyui-workflow');
        if (saveBtn?.disabled) return;
        if (!id) {
            // 新增请用上传按钮；此处直接拒绝
            Toast.show('新增工作流请用「上传」按钮', 'warning');
            return;
        }
        const body = {
            workflow_path: this._comfyuiWorkflowEditingPath,
            name: document.getElementById('comfyui-workflow-name').value.trim(),
            is_default: document.getElementById('comfyui-workflow-default').checked,
        };
        this._setComfyuiButtonBusy(saveBtn, true, '保存中...');
        try {
            let resp;
            if (id) {
                resp = await fetch(`/api/config/comfyui/workflow/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
            } else {
                resp = await fetch('/api/config/comfyui/workflow', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
            }
            const data = await resp.json();
            if (!resp.ok || !data.success) {
                throw new Error(data.error || `HTTP ${resp.status}`);
            }
            Toast.show('工作流配置已更新', 'success');
            this._resetComfyuiWorkflowForm();
            await this._loadComfyuiTab();
        } catch (e) {
            Toast.show(`工作流配置保存失败: ${e.message || '网络错误'}`, 'danger');
        } finally {
            this._setComfyuiButtonBusy(saveBtn, false);
        }
    },

    /* ── 工作流上传 + 磁盘扫描 + 设默认/取消默认 ───── */
    async _uploadComfyuiWorkflow() {
        const fileInput = document.getElementById('comfyui-workflow-file');
        const btn = document.getElementById('btn-upload-workflow');
        if (btn?.disabled) return;
        if (!fileInput || !fileInput.files || !fileInput.files.length) {
            Toast.show('请先选择 .json 工作流文件', 'warning');
            return;
        }
        const file = fileInput.files[0];
        if (!file.name.toLowerCase().endsWith('.json')) {
            Toast.show('仅支持 .json 文件', 'warning');
            return;
        }
        this._setComfyuiButtonBusy(btn, true, '上传中...');
        try {
            const fd = new FormData();
            fd.append('file', file);
            const resp = await fetch('/api/config/comfyui/workflow/upload', { method: 'POST', body: fd });
            const data = await resp.json();
            if (data.success) {
                const r = data.data || {};
                Toast.show(`已上传 ${r.filename}（${(r.size/1024).toFixed(1)} KB）${r.is_default ? '，并设为默认' : ''}`, 'success');
                fileInput.value = '';
                await this._loadComfyuiTab();
                await this._scanComfyuiWorkflows();
            } else {
                Toast.show(data.error || '上传失败', 'danger');
            }
        } catch (e) {
            console.error('上传工作流失败:', e);
            Toast.show('上传请求失败: ' + e.message, 'danger');
        } finally {
            this._setComfyuiButtonBusy(btn, false);
        }
    },

    async _setDefaultComfyuiWorkflow(id, makeDefault) {
        const btn = document.querySelector(`#comfyui-workflow-list [data-action="${makeDefault ? 'set-default' : 'unset-default'}"][data-id="${id}"]`);
        if (btn?.disabled || this._comfyuiWorkflowActionPending) return;
        const previous = this._comfyuiWorkflows.map(wf => ({ ...wf }));
        this._comfyuiWorkflowActionPending = true;
        this._setComfyuiButtonBusy(btn, true, makeDefault ? '设置中...' : '取消中...');

        // 先更新本地视图，点击后立即显示状态；后端失败时回滚。
        this._comfyuiWorkflows = this._comfyuiWorkflows.map(wf => ({
            ...wf,
            is_default: makeDefault ? wf.id === id : (wf.id === id ? false : wf.is_default),
        }));
        this.renderComfyuiWorkflowList(this._comfyuiWorkflows);
        try {
            const resp = await fetch(`/api/config/comfyui/workflow/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ is_default: makeDefault })
            });
            const data = await resp.json();
            if (!resp.ok || !data.success) {
                throw new Error(data.error || `HTTP ${resp.status}`);
            }
            Toast.show(makeDefault ? '已设为默认工作流' : '已取消默认', 'success');
            // 以数据库最终结果覆盖本地乐观状态，避免其它默认/排序不一致。
            await this._loadComfyuiTab();
        } catch (e) {
            this._comfyuiWorkflows = previous;
            this.renderComfyuiWorkflowList(this._comfyuiWorkflows);
            Toast.show(`工作流默认状态更新失败: ${e.message || '网络错误'}`, 'danger');
        } finally {
            // 列表可能已重绘；当前节点仍存在时恢复按钮状态。
            this._setComfyuiButtonBusy(btn, false);
            this._comfyuiWorkflowActionPending = false;
        }
    },

    async _scanComfyuiWorkflows() {
        const el = document.getElementById('comfyui-workflow-scan-list');
        if (!el) return;
        const btn = document.getElementById('btn-scan-comfyui-workflow');
        if (btn?.disabled) return;
        this._setComfyuiButtonBusy(btn, true, '扫描中...');
        try {
            const resp = await fetch('/api/config/comfyui/workflow/scan');
            const data = await resp.json();
            if (!data.success) {
                el.innerHTML = `<p class="no-config">扫描失败: ${this.escapeHtml(data.error || '未知错误')}</p>`;
                return;
            }
            const files = data.data || [];
            if (!files.length) {
                el.innerHTML = `<p class="no-config">磁盘目录为空或不存在：${this.escapeHtml(data.workflow_dir || '')}</p>`;
                await this._loadComfyuiTab();
                return;
            }
            el.innerHTML = files.map(f => {
                const badge = f.registered
                    ? `<span class="badge-active">${f.active ? '激活配置' : '已导入'}</span>`
                    : '<button type="button" class="btn-sm" data-scan-import="' + this.escapeHtml(f.filename) + '">导入</button>';
                return `
                <div class="config-item" data-scan-file="${this.escapeHtml(f.filename)}">
                    <div class="config-item-info">
                        <strong class="cfg-mono">${this.escapeHtml(f.filename)}</strong>
                        <small>${(f.size / 1024).toFixed(1)} KB${f.empty ? ' · 未注册' : ''}</small>
                    </div>
                    <div class="config-item-actions">${badge}<button type="button" class="btn-sm btn-danger-sm" data-scan-delete="${this.escapeHtml(f.filename)}">删除</button></div>
                </div>`;
            }).join('');
            el.querySelectorAll('[data-scan-import]').forEach(btnEl => {
                btnEl.addEventListener('click', () => this._importScannedWorkflow(btnEl.dataset.scanImport, btnEl));
            });
            el.querySelectorAll('[data-scan-delete]').forEach(btnEl => {
                btnEl.addEventListener('click', () => this._deleteScannedWorkflow(btnEl.dataset.scanDelete, btnEl));
            });
        } catch (e) {
            console.error('扫描磁盘工作流失败:', e);
            el.innerHTML = '<p class="no-config">扫描失败，请检查后端服务</p>';
        } finally {
            this._setComfyuiButtonBusy(btn, false);
        }
    },

    async _deleteScannedWorkflow(filename, clickedBtn = null) {
        if (!confirm(`确定要删除磁盘工作流「${filename}」及其关联配置吗？`)) return;
        const btn = clickedBtn || document.querySelector(`[data-scan-delete="${CSS.escape(filename)}"]`);
        if (btn?.disabled) return;
        this._setComfyuiButtonBusy(btn, true, '删除中...');
        try {
            const resp = await fetch(`/api/config/comfyui/workflow/disk/${encodeURIComponent(filename)}`, { method: 'DELETE' });
            const data = await resp.json();
            if (!resp.ok || !data.success) {
                throw new Error(data.error || `HTTP ${resp.status}`);
            }
            const dbCount = Array.isArray(data.deleted_db_ids) ? data.deleted_db_ids.length : 0;
            Toast.show(`已删除 ${filename}${dbCount ? `，同步删除 ${dbCount} 条配置` : ''}`, 'success');
            await this._loadComfyuiTab();
            await this._scanComfyuiWorkflows();
        } catch (e) {
            Toast.show(`磁盘工作流删除失败: ${e.message || '网络错误'}`, 'danger');
            this._setComfyuiButtonBusy(btn, false);
        }
    },

    async _importScannedWorkflow(filename, clickedBtn = null) {
        const btn = clickedBtn || document.querySelector(`[data-scan-import="${CSS.escape(filename)}"]`);
        if (btn?.disabled) return;
        this._setComfyuiButtonBusy(btn, true, '导入中...');
        try {
            const resp = await fetch('/api/config/comfyui/workflow', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    workflow_path: filename,
                    name: filename.replace(/\.json$/i, ''),
                    is_default: false,
                })
            });
            const data = await resp.json();
            if (!resp.ok || !data.success) {
                throw new Error(data.error || `HTTP ${resp.status}`);
            }
            Toast.show(`已导入 ${filename}`, 'success');
            await this._loadComfyuiTab();
            await this._scanComfyuiWorkflows();
        } catch (e) {
            Toast.show(`导入失败: ${e.message || '网络错误'}`, 'danger');
        } finally {
            this._setComfyuiButtonBusy(btn, false);
        }
    },

    /* ==================== 视觉回看 VLM Tab ==================== */
    async _loadVLMTab() {
        try {
            const [cfgResp, presetsResp] = await Promise.all([
                fetch('/api/vlm-config'),
                fetch('/api/vlm-config/presets')
            ]);
            const cfgData = await cfgResp.json();
            const presetsData = await presetsResp.json();
            this._vlmPresets = (presetsData.success && presetsData.presets) ? presetsData.presets : {};
            const cfg = (cfgData.success && cfgData.config) ? cfgData.config : null;
            if (cfg) {
                const prov = document.getElementById('vlm-provider');
                if (prov && cfg.provider) prov.value = cfg.provider;
                document.getElementById('vlm-api-url').value = cfg.api_url || '';
                const keyInput = document.getElementById('vlm-api-key');
                keyInput.value = '';
                keyInput.placeholder = (cfg.api_key && cfg.api_key.indexOf('***') >= 0)
                    ? '已保存（留空不修改）' : '留空表示无需认证';
                document.getElementById('vlm-model-name').value = cfg.model_name || '';
                document.getElementById('vlm-max-tokens').value = cfg.max_tokens ?? 200;
                document.getElementById('vlm-temperature').value = cfg.temperature ?? 0.2;
                document.getElementById('vlm-timeout').value = cfg.timeout ?? 60;
            }
        } catch (e) {
            console.error('加载 VLM 配置失败:', e);
        }
    },

    _onVLMProviderChange(provider) {
        const preset = this._vlmPresets ? this._vlmPresets[provider] : null;
        if (!preset) return;
        if (preset.api_url) document.getElementById('vlm-api-url').value = preset.api_url;
        if (preset.model_name) document.getElementById('vlm-model-name').value = preset.model_name;
    },

    _vlmFormBody() {
        const keyVal = document.getElementById('vlm-api-key').value;
        const body = {
            provider: document.getElementById('vlm-provider').value,
            api_url: document.getElementById('vlm-api-url').value.trim(),
            model_name: document.getElementById('vlm-model-name').value.trim(),
            max_tokens: parseInt(document.getElementById('vlm-max-tokens').value) || 200,
            temperature: parseFloat(document.getElementById('vlm-temperature').value) || 0.2,
            timeout: parseInt(document.getElementById('vlm-timeout').value) || 60,
        };
        // 只有用户输入了新密钥才发送（避免覆盖已存密钥）
        if (keyVal && !keyVal.includes('***')) body.api_key = keyVal;
        return body;
    },

    async _saveVLMConfig() {
        const body = this._vlmFormBody();
        if (!body.api_url || !body.model_name) {
            Toast.show('请填写 API URL 与模型名称', 'warning');
            return;
        }
        try {
            const resp = await fetch('/api/vlm-config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await resp.json();
            if (data.success) {
                Toast.show('视觉回看配置已保存', 'success');
                this._loadVLMTab();
            } else {
                Toast.show(data.error || '保存失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    async _testVLMConnection() {
        const resultEl = document.getElementById('vlm-test-result');
        const body = this._vlmFormBody();
        resultEl.style.display = 'none';
        if (!body.api_url || !body.model_name) {
            resultEl.style.display = '';
            resultEl.className = 'vlm-test-result err';
            resultEl.textContent = '请先填写 API URL 与模型名称';
            return;
        }
        const btn = document.getElementById('btn-test-vlm');
        const orig = btn.textContent;
        btn.disabled = true; btn.textContent = '测试中...';
        try {
            const resp = await fetch('/api/vlm-config/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await resp.json();
            resultEl.style.display = '';
            if (data.success) {
                resultEl.className = 'vlm-test-result ok';
                resultEl.textContent = '连接成功 ✓';
            } else {
                resultEl.className = 'vlm-test-result err';
                resultEl.textContent = '连接失败：' + (data.error || '未知错误');
            }
        } catch (e) {
            resultEl.style.display = '';
            resultEl.className = 'vlm-test-result err';
            resultEl.textContent = '测试请求失败：' + e.message;
        } finally {
            btn.disabled = false; btn.textContent = orig;
        }
    },

    /* ==================== Embedding Tab ==================== */
    async _loadEmbeddingTab() {
        // 更新状态显示（只读，不填表单）
        try {
            const resp = await fetch('/api/config/embedding');
            const data = await resp.json();
            if (data.success && data.data) {
                const statusEl = document.getElementById('embedding-status-info');
                if (statusEl) {
                    statusEl.innerHTML = `
                        <div class="active-config-card">
                            <div class="cfg-row"><span class="cfg-label">平台:</span> <span>${this.escapeHtml(data.data.provider)}</span></div>
                            <div class="cfg-row"><span class="cfg-label">URL:</span> <span class="cfg-mono">${this.escapeHtml(data.data.api_url)}</span></div>
                            <div class="cfg-row"><span class="cfg-label">Model:</span> <span>${this.escapeHtml(data.data.model_name)}</span></div>
                            <div class="cfg-row"><span class="cfg-label">维度:</span> <span>${data.data.vector_dimension}</span></div>
                            ${data.data.is_active ? '<span class="badge-active">已激活</span>' : '<span class="no-config">未激活</span>'}
                        </div>
                    `;
                }
            }
        } catch (e) {
            console.error('加载 Embedding 配置失败:', e);
        }

        // 加载已保存的配置列表
        await this._loadEmbeddingConfigList();
    },

    async _loadEmbeddingConfigList() {
        try {
            const resp = await fetch('/api/config/embedding/all');
            const data = await resp.json();
            this._embeddingConfigs = data.success ? (data.data || []) : [];
            this.renderEmbeddingConfigList(this._embeddingConfigs);
        } catch (e) {
            console.error('加载 Embedding 配置列表失败:', e);
        }
    },

    renderEmbeddingConfigList(configs) {
        const el = document.getElementById('embedding-config-list');
        if (!el) return;
        if (!configs.length) {
            el.innerHTML = '<p class="no-config">暂无配置，请新增</p>';
            return;
        }
        el.innerHTML = configs.map(c => {
            const summary = `${c.provider || ''} · ${this._truncate(c.model_name || '', 30)}`;
            return `
            <div class="config-item ${c.is_active ? 'active' : ''}" data-id="${c.id}">
                <div class="config-item-info">
                    <strong>${this.escapeHtml(summary)}</strong>
                    ${c.is_active ? '<span class="badge-active">激活</span>' : ''}
                </div>
                <div class="config-item-actions">
                    ${!c.is_active ? `<button class="btn-sm btn-activate" data-action="activate" data-id="${c.id}">激活</button>` : ''}
                    <button class="btn-sm" data-action="edit" data-id="${c.id}">编辑</button>
                    ${!c.is_active ? `<button class="btn-sm btn-danger-sm" data-action="delete" data-id="${c.id}">删除</button>` : ''}
                </div>
            </div>
        `;
        }).join('');

        el.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', () => {
                const action = btn.dataset.action;
                const id = parseInt(btn.dataset.id);
                if (action === 'activate') this._activateEmbeddingConfig(id);
                else if (action === 'edit') this._editEmbeddingConfig(id);
                else if (action === 'delete') this._deleteEmbeddingConfig(id);
            });
        });
    },

    _enterEmbeddingEditMode(id) {
        this._embeddingEditingId = id;
        document.getElementById('embedding-editing-id').value = id || '';
        document.getElementById('btn-new-embedding').style.display = 'none';
        document.getElementById('btn-save-embedding').style.display = 'inline-block';
        document.getElementById('btn-cancel-embedding').style.display = 'inline-block';
    },

    _newEmbeddingConfig() {
        this._resetEmbeddingForm();
        this._enterEmbeddingEditMode(null);
    },

    _editEmbeddingConfig(id) {
        const cfg = this._embeddingConfigs.find(c => c.id === id);
        if (!cfg) {
            console.warn('[ConfigPanel] Embedding 配置未找到 id=', id);
            return;
        }
        this._enterEmbeddingEditMode(id);
        document.getElementById('embedding-provider').value = cfg.provider || '';
        document.getElementById('embedding-api-url').value = cfg.api_url || '';
        // 不填充掩码密钥，只显示占位提示
        const keyInput = document.getElementById('embedding-api-key');
        keyInput.type = 'password';
        keyInput.value = '';
        keyInput.placeholder = cfg.api_key ? '•••••••• 密钥已保存，留空保持不变' : '留空表示无需认证';
        document.getElementById('embedding-model').value = cfg.model_name || '';
        document.getElementById('embedding-dim').value = cfg.vector_dimension || 512;
        document.querySelector('.embedding-config-form').scrollIntoView({ behavior: 'smooth' });
    },

    _resetEmbeddingForm() {
        this._embeddingEditingId = null;
        document.getElementById('embedding-editing-id').value = '';
        document.getElementById('embedding-api-key').type = 'password';
        document.getElementById('embedding-api-key').value = '';
        document.getElementById('embedding-api-key').placeholder = '留空表示无需认证';
        document.getElementById('embedding-api-url').disabled = false;
        document.getElementById('embedding-api-key').disabled = false;
        document.getElementById('btn-new-embedding').style.display = 'inline-block';
        document.getElementById('btn-save-embedding').style.display = 'none';
        document.getElementById('btn-cancel-embedding').style.display = 'none';
    },

    async _activateEmbeddingConfig(id) {
        try {
            const resp = await fetch(`/api/config/embedding/${id}/activate`, { method: 'PUT' });
            const data = await resp.json();
            if (data.success) {
                Toast.show('Embedding 配置已激活', 'success');
                this._loadEmbeddingTab();
            } else {
                Toast.show(data.error || '激活失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    async _deleteEmbeddingConfig(id) {
        if (!confirm('确定要删除该 Embedding 配置吗？')) return;
        try {
            const resp = await fetch(`/api/config/embedding/${id}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.success) {
                Toast.show('配置已删除', 'success');
                if (this._embeddingEditingId === id) this._resetEmbeddingForm();
                this._loadEmbeddingConfigList();
            } else {
                Toast.show(data.error || '删除失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    _onEmbeddingProviderChange(provider) {
        const urlMap = {
            'local': '',
            'openai': 'https://api.openai.com/v1/embeddings',
            'dashscope': 'https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings',
            'zhipu': 'https://open.bigmodel.cn/api/paas/v4/embeddings',
            'siliconflow': 'https://api.siliconflow.cn/v1/embeddings',
            'llm_studio': 'http://localhost:10039/v1/embeddings',
            'ollama': 'http://127.0.0.1:11434/v1/embeddings',
            'custom': '',
        };
        const modelMap = {
            'local': 'BAAI/bge-small-zh-v1.5',
            'openai': 'text-embedding-3-small',
            'dashscope': 'text-embedding-v3',
            'zhipu': 'embedding-3',
            'siliconflow': 'BAAI/bge-small-zh-v1.5',
            'llm_studio': 'text-embedding-bge-m3',
            'ollama': 'nomic-embed-text',
            'custom': '',
        };
        const dimMap = {
            'local': 512, 'openai': 1536, 'dashscope': 1024, 'zhipu': 2048,
            'siliconflow': 512, 'llm_studio': 1024, 'ollama': 768, 'custom': 512,
        };

        document.getElementById('embedding-api-url').value = urlMap[provider] || '';
        document.getElementById('embedding-model').value = modelMap[provider] || '';
        document.getElementById('embedding-dim').value = dimMap[provider] || 512;

        // 本地模型不需要 API Key 和 URL
        const isLocal = provider === 'local';
        document.getElementById('embedding-api-url').disabled = isLocal;
        document.getElementById('embedding-api-key').disabled = isLocal;
    },

    async _testEmbeddingConnection() {
        const url = document.getElementById('embedding-api-url').value;
        const model = document.getElementById('embedding-model').value;
        const apiKey = document.getElementById('embedding-api-key').value;

        if (!url) {
            Toast.show('请填写 API URL', 'warning');
            return;
        }
        const btn = document.getElementById('btn-test-embedding');
        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '测试中...';
        try {
            const resp = await fetch('/api/config/embedding/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_url: url, model_name: model, api_key: apiKey })
            });
            const data = await resp.json();
            if (data.success) {
                Toast.show(`Embedding 连接成功！延迟 ${data.latency_ms}ms`, 'success');
            } else {
                Toast.show(data.error || '连接失败', 'danger');
            }
        } catch (err) {
            Toast.show('测试请求失败: ' + err.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
        }
    },

    async _saveEmbeddingConfig() {
        const id = this._embeddingEditingId;
        const provider = document.getElementById('embedding-provider').value;
        const apiUrl = document.getElementById('embedding-api-url').value.trim();
        const apiKey = document.getElementById('embedding-api-key').value;
        const modelName = document.getElementById('embedding-model').value;
        const vectorDim = parseInt(document.getElementById('embedding-dim').value);

        // 去重检查：查已保存配置中是否有相同 provider + api_url
        try {
            const checkResp = await fetch('/api/config/embedding/all');
            const checkData = await checkResp.json();
            if (checkData.success && checkData.data) {
                const dup = checkData.data.find(c =>
                    c.provider === provider && c.api_url === apiUrl && c.id !== id
                );
                if (dup) {
                    Toast.show('该平台与地址已有配置，请直接编辑', 'warning');
                    return;
                }
            }
        } catch (e) {
            console.error('去重检查失败:', e);
        }

        const body = {
            provider,
            api_url: apiUrl,
            model_name: modelName,
            vector_dimension: vectorDim,
        };
        // 编辑时只在用户输入了新密钥时才发送
        if (apiKey && !apiKey.includes('***')) {
            body.api_key = apiKey;
        } else if (!id) {
            body.api_key = apiKey;
        }
        try {
            let resp;
            if (id) {
                resp = await fetch(`/api/config/embedding/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
            } else {
                resp = await fetch('/api/config/embedding', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
            }
            const data = await resp.json();
            if (data.success) {
                Toast.show(id ? 'Embedding 配置已更新' : 'Embedding 配置已创建', 'success');
                this._resetEmbeddingForm();
                await this._loadEmbeddingTab();
            } else {
                Toast.show(data.error || '保存失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    /* ==================== 工具 ==================== */
    escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    },

    _truncate(str, maxLen) {
        if (!str) return '';
        return str.length > maxLen ? str.substring(0, maxLen) + '...' : str;
    },

    /* ==================== Memos Tab ==================== */

    _loadMemosTab() {
        this._resetMemosForm();
        this._loadMemosConfigList();
    },

    async _loadMemosConfigList() {
        const el = document.getElementById('memos-config-list');
        try {
            const resp = await fetch('/api/config/memos/all');
            const data = await resp.json();
            this._memosConfigs = data.success ? (data.data || []) : [];
            this.renderMemosConfigList(this._memosConfigs);
        } catch (e) {
            console.error('加载 Memos 配置列表失败:', e);
            if (el) {
                el.innerHTML = `<p class="no-config">加载失败：${this.escapeHtml(String(e.message || e))}</p>`;
            }
        }
    },

    renderMemosConfigList(configs) {
        const el = document.getElementById('memos-config-list');
        if (!el) return;
        if (!configs.length) {
            el.innerHTML = '<p class="no-config">暂无配置，请在上方填写并保存</p>';
            return;
        }
        el.innerHTML = configs.map(c => {
            const urlSummary = this._truncate(c.api_url || '', 40);
            const attachmentLabel = c.skip_attachments ? '过滤附件' : '含附件';
            const statusBadge = c.is_active
                ? '<span class="badge-active">当前激活</span>'
                : '<span class="badge-inactive">未激活</span>';
            return `
            <div class="config-item ${c.is_active ? 'active' : ''}" data-id="${c.id}">
                <div class="config-item-info">
                    <strong class="cfg-mono">${this.escapeHtml(urlSummary)}</strong>
                    <small>${attachmentLabel} · ${statusBadge}</small>
                </div>
                <div class="config-item-actions">
                    ${!c.is_active ? `<button class="btn-sm btn-activate" data-action="activate" data-id="${c.id}">激活</button>` : ''}
                    <button class="btn-sm" data-action="edit" data-id="${c.id}">编辑</button>
                    ${!c.is_active ? `<button class="btn-sm btn-danger-sm" data-action="delete" data-id="${c.id}">删除</button>` : ''}
                </div>
            </div>
        `;
        }).join('');

        el.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', () => {
                const action = btn.dataset.action;
                const id = parseInt(btn.dataset.id);
                if (action === 'activate') this._activateMemosConfig(id);
                else if (action === 'edit') this._editMemosConfig(id);
                else if (action === 'delete') this._deleteMemosConfig(id);
            });
        });
    },

    _enterMemosEditMode(id) {
        this._memosEditingId = id;
        document.getElementById('memos-editing-id').value = id || '';
        document.getElementById('btn-save-memos').textContent = id ? '更新配置' : '保存配置';
        document.getElementById('btn-cancel-memos').style.display = 'inline-block';
    },

    _editMemosConfig(id) {
        const cfg = this._memosConfigs.find(c => c.id === id);
        if (!cfg) {
            console.warn('[ConfigPanel] Memos 配置未找到 id=', id);
            return;
        }
        this._enterMemosEditMode(id);
        document.getElementById('memos-api-url').value = cfg.api_url || '';
        const tokenInput = document.getElementById('memos-access-token');
        tokenInput.type = 'password';
        tokenInput.value = '';
        tokenInput.placeholder = cfg.access_token ? 'Token 已保存，留空保持不变' : '粘贴 Memos Access Token...';
        document.getElementById('memos-skip-attachments').checked = cfg.skip_attachments !== false;
    },

    _resetMemosForm() {
        this._memosEditingId = null;
        document.getElementById('memos-editing-id').value = '';
        document.getElementById('memos-api-url').value = '';
        const tokenInput = document.getElementById('memos-access-token');
        tokenInput.type = 'password';
        tokenInput.value = '';
        tokenInput.placeholder = '粘贴 Memos Access Token...';
        document.getElementById('memos-skip-attachments').checked = true;
        document.getElementById('btn-save-memos').textContent = '保存配置';
        document.getElementById('btn-cancel-memos').style.display = 'none';
    },

    async _activateMemosConfig(id) {
        try {
            const resp = await fetch(`/api/config/memos/${id}/activate`, { method: 'PUT' });
            const data = await resp.json();
            if (data.success) {
                Toast.show('Memos 配置已激活', 'success');
                this._resetMemosForm();
                this._loadMemosConfigList();
            } else {
                Toast.show(data.error || '激活失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    async _deleteMemosConfig(id) {
        if (!confirm('确定要删除该 Memos 配置吗？')) return;
        try {
            const resp = await fetch(`/api/config/memos/${id}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.success) {
                Toast.show('配置已删除', 'success');
                if (this._memosEditingId === id) this._resetMemosForm();
                this._loadMemosConfigList();
            } else {
                Toast.show(data.error || '删除失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        }
    },

    async _testMemosConnection() {
        const url = document.getElementById('memos-api-url').value.trim();
        const token = document.getElementById('memos-access-token').value;
        if (!url) {
            Toast.show('请填写 API 地址', 'warning');
            return;
        }
        const btn = document.getElementById('btn-test-memos');
        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '测试中...';
        try {
            const resp = await fetch('/api/config/memos/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ api_url: url, access_token: token })
            });
            const data = await resp.json();
            if (data.success) {
                Toast.show(`连接成功！延迟 ${data.latency_ms}ms`, 'success');
            } else {
                Toast.show(data.error || '连接失败', 'danger');
            }
        } catch (err) {
            Toast.show('测试请求失败: ' + err.message, 'danger');
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
        }
    },

    async _saveMemosConfig() {
        const id = this._memosEditingId;
        const apiUrl = document.getElementById('memos-api-url').value.trim();
        if (!apiUrl) {
            Toast.show('请填写 API 地址', 'warning');
            return;
        }

        // 去重检查（仅新增时）：复用已加载的配置列表，避免重复请求
        if (!id) {
            const dup = (this._memosConfigs || []).find(c => c.api_url === apiUrl);
            if (dup) {
                Toast.show('该地址已有配置，请在下方列表中编辑', 'warning');
                return;
            }
        }

        const token = document.getElementById('memos-access-token').value;
        const skipAttachments = document.getElementById('memos-skip-attachments').checked;
        const body = { api_url: apiUrl, skip_attachments: skipAttachments };

        // Token 处理（决策 4：留空保持不变）
        // - 编辑时：token 留空或包含 *** → 不发送 access_token 字段，后端保持原值
        // - 新增时：token 可为空，发送 access_token 字段
        if (token && !token.includes('***')) {
            // 用户输入了新的完整 token
            body.access_token = token;
        } else if (!id) {
            // 新增配置，token 可为空
            body.access_token = token;
        }
        // else: 编辑时 token 留空，不发送 access_token 字段，后端保持原值

        const btnSave = document.getElementById('btn-save-memos');
        const origText = btnSave.textContent;
        btnSave.disabled = true;
        btnSave.textContent = '保存中...';

        try {
            let resp;
            if (id) {
                resp = await fetch(`/api/config/memos/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
            } else {
                resp = await fetch('/api/config/memos', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
            }
            const data = await resp.json();
            if (data.success) {
                Toast.show(id ? '配置已更新' : '配置已保存', 'success');
                this._resetMemosForm();
                await this._loadMemosConfigList();
            } else {
                Toast.show(data.error || '保存失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        } finally {
            btnSave.disabled = false;
            btnSave.textContent = origText;
        }
    },

    async _manualMemosSync() {
        const btn = document.getElementById('btn-sync-memos');
        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '拉取中...';
        try {
            const resp = await fetch('/api/config/memos/sync', { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                Toast.show(data.message || '手动导入已触发', 'success');
            } else {
                Toast.show(data.error || '触发失败', 'danger');
            }
        } catch (e) {
            Toast.show('网络错误', 'danger');
        } finally {
            btn.disabled = false;
            btn.textContent = origText;
        }
    },
};

document.addEventListener('DOMContentLoaded', () => {
    ConfigPanel.init();
});
