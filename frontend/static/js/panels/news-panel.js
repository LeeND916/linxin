/** 新闻资讯设置面板 — 源管理 + 全局参数 */
const NewsPanel = {
    _editingId: null,

    init() {
        // 保存全局配置
        document.getElementById('btn-save-news-settings')?.addEventListener('click', () => this._saveSettings());
        // 立即抓取
        document.getElementById('btn-fetch-news-now')?.addEventListener('click', () => this._fetchNow());
        // 新增源
        document.getElementById('btn-new-news-source')?.addEventListener('click', () => this._showForm());
        document.getElementById('btn-save-news-source')?.addEventListener('click', () => this._saveSource());
        document.getElementById('btn-cancel-news-source')?.addEventListener('click', () => this._hideForm());
        // 查看已抓新闻缓存
        document.getElementById('btn-view-news-cache')?.addEventListener('click', () => this._viewCache());
        document.getElementById('btn-close-news-cache')?.addEventListener('click', () => {
            document.getElementById('news-cache-modal').style.display = 'none';
        });
    },

    async _viewCache() {
        const modal = document.getElementById('news-cache-modal');
        const list = document.getElementById('news-cache-list');
        modal.style.display = 'flex';
        list.innerHTML = '<p class="no-config">加载中...</p>';
        try {
            const resp = await fetch('/api/news/cache');
            const data = await resp.json();
            // 接口报错时明确提示，避免与「真的没数据」混淆
            if (!data.success) {
                list.innerHTML = `<p class="no-config">加载失败：${data.error || '未知错误'}</p>`;
                return;
            }
            if (data.data && data.data.length > 0) {
                list.innerHTML = data.data.map(n => `
                    <div class="config-list-item">
                        <div class="config-list-info">
                            <div class="config-list-name">${n.title}</div>
                            <div class="config-list-meta">
                                <span class="tag-badge">${n.major_tag || '通用'}</span>
                                <span class="tag-badge">${n.category || ''}</span>
                                ${n.is_used ? '<span class="tag-badge tag-key">已注入</span>' : '<span class="tag-badge">未注入</span>'}
                                <span class="tag-badge">${n.created_at || ''}</span>
                            </div>
                            ${n.summary ? `<div class="config-list-name" style="font-weight:normal;color:var(--text-secondary,#666);font-size:12px;margin-top:4px">${n.summary}</div>` : ''}
                        </div>
                    </div>`).join('');
            } else {
                list.innerHTML = '<p class="no-config">暂无已抓新闻，点击"立即抓取一次"试试</p>';
            }
        } catch (e) {
            list.innerHTML = '<p class="no-config">加载失败</p>';
        }
    },

    /** 由 ConfigPanel.open() 调用，加载当前 Tab 数据 */
    async loadTab() {
        await this._loadSettings();
        await this._loadSources();
    },

    async _loadSettings() {
        try {
            const resp = await fetch('/api/news/settings');
            const data = await resp.json();
            if (data.success && data.data) {
                const s = data.data;
                document.getElementById('news-enabled-toggle').checked = s.enabled;
                document.getElementById('news-max-feed').value = s.max_items_per_feed;
                document.getElementById('news-max-inject').value = s.max_inject_per_day;
                document.getElementById('news-push-interval').value = s.push_interval_days;
                document.getElementById('news-fetch-interval').value = s.fetch_interval;
                document.getElementById('news-cache-days').value = s.cache_retain_days;
                document.getElementById('news-sum-threshold').value = s.summary_threshold;
            }
        } catch (e) {
            console.error('News settings load error:', e);
        }
    },

    async _saveSettings() {
        const payload = {
            enabled: document.getElementById('news-enabled-toggle').checked,
            max_items_per_feed: parseInt(document.getElementById('news-max-feed').value) || 8,
            max_inject_per_day: parseInt(document.getElementById('news-max-inject').value) || 3,
            push_interval_days: parseInt(document.getElementById('news-push-interval').value) || 0,
            fetch_interval: parseInt(document.getElementById('news-fetch-interval').value) || 3600,
            cache_retain_days: parseInt(document.getElementById('news-cache-days').value) || 2,
            summary_threshold: parseInt(document.getElementById('news-sum-threshold').value) || 80,
        };
        try {
            const resp = await fetch('/api/news/settings', {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            const data = await resp.json();
            if (data.success) {
                Toast.show('资讯配置已保存', 'success');
            } else {
                Toast.show('保存失败: ' + (data.error || ''), 'error');
            }
        } catch (e) {
            Toast.show('保存失败: ' + e.message, 'error');
        }
    },

    async _fetchNow() {
        Toast.show('正在抓取新闻...', 'info');
        try {
            const resp = await fetch('/api/news/fetch-now', {method: 'POST'});
            const data = await resp.json();
            if (data.success) {
                Toast.show(`抓取完成，新增 ${data.added} 条`, 'success');
                await this._loadSources();
                // 若缓存弹窗已打开，抓取后就地刷新，避免「点了没反应」
                const modal = document.getElementById('news-cache-modal');
                if (modal && modal.style.display === 'flex') {
                    await this._viewCache();
                }
            } else {
                Toast.show('抓取失败: ' + (data.error || ''), 'error');
            }
        } catch (e) {
            Toast.show('抓取失败: ' + e.message, 'error');
        }
    },

    async _loadSources() {
        const list = document.getElementById('news-source-list');
        if (!list) return;
        list.innerHTML = '<p class="no-config">加载中...</p>';
        try {
            const resp = await fetch('/api/news/sources');
            const data = await resp.json();
            if (data.success && data.data.length > 0) {
                list.innerHTML = data.data.map(s => this._renderSourceRow(s)).join('');
                this._bindRowEvents();
            } else {
                list.innerHTML = '<p class="no-config">暂无新闻源，点击"新增源"添加</p>';
            }
        } catch (e) {
            list.innerHTML = '<p class="no-config">加载失败</p>';
        }
    },

    _renderSourceRow(s) {
        const typeLabel = {rss:'RSS', '60s':'60s API', juhe:'聚合数据', volcano:'火山引擎'}[s.source_type] || s.source_type;
        const catLabel = {general:'通用', tech:'科技', edu:'教育', culture:'文化', health:'健康', world:'国际'}[s.category] || s.category;
        return `
        <div class="config-list-item" data-id="${s.id}">
            <div class="config-list-info">
                <div class="config-list-name">${s.name}</div>
                <div class="config-list-meta">
                    <span class="tag-badge">${typeLabel}</span>
                    <span class="tag-badge">${catLabel}</span>
                    ${s.api_key ? '<span class="tag-badge tag-key">有Key</span>' : ''}
                </div>
            </div>
            <div class="config-list-actions">
                <label class="switch-mini">
                    <input type="checkbox" class="src-toggle" data-id="${s.id}" ${s.enabled ? 'checked' : ''}>
                    <span class="switch-slider"></span>
                </label>
                <button class="btn-icon-sm src-edit" data-id="${s.id}" title="编辑">
                    <span class="material-symbols-outlined" style="font-size:18px">edit</span>
                </button>
                <button class="btn-icon-sm src-delete" data-id="${s.id}" title="删除">
                    <span class="material-symbols-outlined" style="font-size:18px">delete</span>
                </button>
            </div>
        </div>`;
    },

    _bindRowEvents() {
        document.querySelectorAll('.src-toggle').forEach(el => {
            el.addEventListener('change', (e) => this._toggleSource(parseInt(e.target.dataset.id), e.target.checked));
        });
        document.querySelectorAll('.src-edit').forEach(el => {
            el.addEventListener('click', (e) => this._editSource(parseInt(e.currentTarget.dataset.id)));
        });
        document.querySelectorAll('.src-delete').forEach(el => {
            el.addEventListener('click', (e) => this._deleteSource(parseInt(e.currentTarget.dataset.id)));
        });
    },

    async _toggleSource(id, enabled) {
        try {
            await fetch(`/api/news/sources/${id}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({enabled}),
            });
        } catch (e) {
            Toast.show('操作失败', 'error');
            await this._loadSources();
        }
    },

    async _editSource(id) {
        try {
            const resp = await fetch('/api/news/sources');
            const data = await resp.json();
            const src = data.data.find(s => s.id === id);
            if (!src) return;
            this._showForm();
            document.getElementById('news-src-editing-id').value = src.id;
            document.getElementById('news-src-name').value = src.name;
            document.getElementById('news-src-type').value = src.source_type;
            document.getElementById('news-src-category').value = src.category;
            document.getElementById('news-src-url').value = src.api_url;
            document.getElementById('news-src-key').value = '';
            document.getElementById('news-src-key').placeholder = src.api_key ? '已设置(留空不改)' : '付费源才需要';
        } catch (e) {
            Toast.show('加载失败', 'error');
        }
    },

    async _deleteSource(id) {
        if (!confirm('确定删除这个新闻源？')) return;
        try {
            const resp = await fetch(`/api/news/sources/${id}`, {method: 'DELETE'});
            const data = await resp.json();
            if (data.success) {
                Toast.show('已删除', 'success');
                await this._loadSources();
            } else {
                Toast.show('删除失败: ' + (data.error || ''), 'error');
            }
        } catch (e) {
            Toast.show('删除失败', 'error');
        }
    },

    _showForm() {
        document.getElementById('news-source-form').style.display = 'block';
        document.getElementById('news-src-editing-id').value = '';
        document.getElementById('news-src-name').value = '';
        document.getElementById('news-src-type').value = 'rss';
        document.getElementById('news-src-category').value = 'general';
        document.getElementById('news-src-url').value = '';
        document.getElementById('news-src-key').value = '';
        document.getElementById('news-src-key').placeholder = '付费源才需要';
    },

    _hideForm() {
        document.getElementById('news-source-form').style.display = 'none';
    },

    async _saveSource() {
        const editingId = document.getElementById('news-src-editing-id').value;
        const payload = {
            name: document.getElementById('news-src-name').value.trim(),
            source_type: document.getElementById('news-src-type').value,
            category: document.getElementById('news-src-category').value,
            api_url: document.getElementById('news-src-url').value.trim(),
            api_key: document.getElementById('news-src-key').value.trim(),
            enabled: true,
        };
        if (!payload.name) {
            Toast.show('请填写源名称', 'error');
            return;
        }
        try {
            let resp;
            if (editingId) {
                resp = await fetch(`/api/news/sources/${editingId}`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload),
                });
            } else {
                resp = await fetch('/api/news/sources', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload),
                });
            }
            const data = await resp.json();
            if (data.success) {
                Toast.show(editingId ? '已更新' : '已新增', 'success');
                this._hideForm();
                await this._loadSources();
            } else {
                Toast.show('保存失败: ' + (data.error || ''), 'error');
            }
        } catch (e) {
            Toast.show('保存失败: ' + e.message, 'error');
        }
    },
};
