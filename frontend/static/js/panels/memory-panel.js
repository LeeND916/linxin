/* ============================================================
   回忆面板模块（AI伴侣系统升级 — 阶段一）

   职责：
     1. 展示角色的长期记忆列表（按时间倒序）
     2. 展示高价值情感时刻（回忆集，按时间倒序）
     3. 显示记忆统计（总数/类型分布/淡化数）
     4. 支持按子类筛选

   数据来源：
     - GET /api/memory?type=xxx&limit=30  → 记忆列表 + 统计
     - GET /api/emotional-moments?type=xxx&limit=30 → 情感时刻列表 + 统计

   触发方式：
     - 顶部栏"她的回忆"按钮
   ============================================================ */

const MemoryPanel = {
    /* 面板当前是否展开 */
    isOpen: false,

    /* 当前筛选类型（null = 全部） */
    _memoryTypeFilter: null,
    _momentTypeFilter: null,
    _activeTab: 'memories',  /* 当前激活的 Tab */

    /* 缓存数据 */
    _memories: [],
    _moments: [],
    _stats: null,
    _momentStats: null,

    /** 记忆类型定义 */
    MEMORY_TYPES: {
        'fact':       { icon: '📝', label: '事实' },
        'preference': { icon: '💡', label: '偏好' },
        'event':      { icon: '📅', label: '事件' },
        'emotion':    { icon: '💗', label: '情感' },
        'secret':     { icon: '🤫', label: '秘密' },
    },

    /** 情感时刻类型定义 */
    MOMENT_TYPES: {
        'tender':      { icon: '💕', label: '温柔时刻' },
        'conflict':    { icon: '💔', label: '冲突时刻' },
        'breakthrough':{ icon: '✨', label: '突破时刻' },
        'memory':      { icon: '', label: '难忘时刻' },
    },

    init() {
        const btn = document.getElementById('btn-memory-panel');
        if (btn) {
            btn.addEventListener('click', () => this.toggle());
        }
    },

    async toggle() {
        if (this.isOpen) {
            this.close();
        } else {
            await this.open();
        }
    },

    async open() {
        this.isOpen = true;
        this._memoryTypeFilter = null;
        this._momentTypeFilter = null;
        this._activeTab = 'memories';
        this._renderPlaceholder();
        await this._loadData();
        this._render();
    },

    close() {
        this.isOpen = false;
        const panel = document.getElementById('memory-panel-overlay');
        if (panel) panel.remove();
    },

    /** 并行加载记忆和情感时刻数据（带筛选） */
    async _loadData() {
        try {
            const memType = this._memoryTypeFilter || '';
            const momType = this._momentTypeFilter || '';
            const [memResp, momResp] = await Promise.all([
                fetch(`/api/memory?limit=60&type=${encodeURIComponent(memType)}`),
                fetch(`/api/emotional-moments?limit=60&type=${encodeURIComponent(momType)}`)
            ]);
            const memData = await memResp.json();
            const momData = await momResp.json();

            if (memData.success) {
                this._memories = memData.data || [];
                this._stats = memData.stats || null;
            }
            if (momData.success) {
                this._moments = momData.data || [];
                this._momentStats = momData.stats || null;
            }

            this._refreshContent();
        } catch (err) {
            console.error('[MemoryPanel] 加载数据失败:', err);
            this._renderError('加载失败，请重试');
        }
    },

    /** 就地刷新面板内容（不重建 DOM，避免闪烁） */
    _refreshContent() {
        const overlay = document.getElementById('memory-panel-overlay');
        if (!overlay) return;

        // 更新统计栏
        const statsBar = overlay.querySelector('#memory-stats-bar');
        if (statsBar) statsBar.innerHTML = this._renderStats();

        // 更新记忆碎片 Tab 内容
        const memTab = overlay.querySelector('#memory-tab-memories');
        if (memTab) memTab.innerHTML = this._renderMemoryFilters() + this._renderMemories();

        // 更新情感时刻 Tab 内容
        const momTab = overlay.querySelector('#memory-tab-moments');
        if (momTab) momTab.innerHTML = this._renderMomentFilters() + this._renderMoments();

        // 重新绑定筛选标签事件
        overlay.querySelectorAll('.memory-filter-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                this._filterMemory(chip.dataset.type);
            });
        });
        overlay.querySelectorAll('.moment-filter-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                this._filterMoment(chip.dataset.type);
            });
        });
    },

    /** 切换记忆类型筛选 */
    _filterMemory(type) {
        this._memoryTypeFilter = (this._memoryTypeFilter === type) ? null : type;
        this._loadData();
    },

    /** 切换情感时刻类型筛选 */
    _filterMoment(type) {
        this._momentTypeFilter = (this._momentTypeFilter === type) ? null : type;
        this._loadData();
    },

    /** 渲染面板主界面 */
    _render() {
        // 移除旧面板（避免 DOM 中残留重复 overlay 导致 getElementById 错位）
        const oldOverlay = document.getElementById('memory-panel-overlay');
        if (oldOverlay) oldOverlay.remove();

        const overlay = document.createElement('div');
        overlay.id = 'memory-panel-overlay';
        overlay.className = 'memory-panel-overlay';

        const isMemories = this._activeTab === 'memories';

        overlay.innerHTML = `
            <div class="memory-panel">
                <div class="memory-panel-header">
                    <h3><span class="material-symbols-outlined">auto_awesome_memory</span> 她的回忆</h3>
                    <button class="btn-icon" id="btn-close-memory" title="关闭">
                        <span class="material-symbols-outlined">close</span>
                    </button>
                </div>

                <!-- 统计摘要 -->
                <div class="memory-stats-bar" id="memory-stats-bar">
                    ${this._renderStats()}
                </div>

                <!-- Tab 切换 -->
                <div class="memory-tabs">
                    <button class="memory-tab${isMemories ? ' active' : ''}" data-tab="memories">记忆碎片</button>
                    <button class="memory-tab${isMemories ? '' : ' active'}" data-tab="moments">情感时刻</button>
                </div>

                <!-- 记忆列表（含筛选标签） -->
                <div class="memory-tab-content${isMemories ? ' active' : ''}" id="memory-tab-memories">
                    ${this._renderMemoryFilters()}
                    ${this._renderMemories()}
                </div>

                <!-- 情感时刻列表（含筛选标签） -->
                <div class="memory-tab-content${isMemories ? '' : ' active'}" id="memory-tab-moments">
                    ${this._renderMomentFilters()}
                    ${this._renderMoments()}
                </div>
            </div>
        `;

        document.body.appendChild(overlay);

        // 绑定关闭按钮
        document.getElementById('btn-close-memory')?.addEventListener('click', () => this.close());

        // 绑定 Tab 切换
        overlay.querySelectorAll('.memory-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                this._activeTab = tab.dataset.tab;
                overlay.querySelectorAll('.memory-tab').forEach(t => t.classList.remove('active'));
                overlay.querySelectorAll('.memory-tab-content').forEach(c => c.classList.remove('active'));
                tab.classList.add('active');
                const target = overlay.querySelector(`#memory-tab-${tab.dataset.tab}`);
                if (target) target.classList.add('active');
            });
        });

        // 绑定记忆筛选标签
        overlay.querySelectorAll('.memory-filter-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                this._filterMemory(chip.dataset.type);
            });
        });

        // 绑定情感时刻筛选标签
        overlay.querySelectorAll('.moment-filter-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                this._filterMoment(chip.dataset.type);
            });
        });

        // 点击遮罩关闭
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) this.close();
        });
    },

    /** 渲染统计摘要条 */
    _renderStats() {
        const s = this._stats;
        const ms = this._momentStats;
        if (!s && !ms) return '<span class="memory-stats-empty">暂无记忆数据</span>';

        const parts = [];
        if (s) {
            parts.push(`记忆 <strong>${s.active || 0}</strong> 条`);
            if (s.faded > 0) parts.push(`已淡化 <em>${s.faded}</em>`);
        }
        if (ms) {
            parts.push(`情感时刻 <strong>${ms.total || 0}</strong> 个`);
        }
        return parts.join(' &nbsp;|&nbsp; ');
    },

    /** 渲染记忆类型筛选标签 */
    _renderMemoryFilters() {
        const byType = this._stats?.by_type || {};
        const allCount = Object.values(byType).reduce((a, b) => a + b, 0);
        const isActive = (type) => this._memoryTypeFilter === type ? ' active' : '';

        let html = '<div class="memory-filter-bar">';
        html += `<span class="memory-filter-chip${this._memoryTypeFilter === null ? ' active' : ''}" data-type="">全部 (${allCount})</span>`;
        for (const [type, info] of Object.entries(this.MEMORY_TYPES)) {
            const count = byType[type] || 0;
            if (count > 0) {
                html += `<span class="memory-filter-chip${isActive(type)}" data-type="${type}">${info.icon} ${info.label} (${count})</span>`;
            }
        }
        html += '</div>';
        return html;
    },

    /** 渲染情感时刻类型筛选标签 */
    _renderMomentFilters() {
        const byType = this._momentStats?.by_type || {};
        const allCount = Object.values(byType).reduce((a, b) => a + b, 0);
        const isActive = (type) => this._momentTypeFilter === type ? ' active' : '';

        let html = '<div class="memory-filter-bar">';
        html += `<span class="moment-filter-chip${this._momentTypeFilter === null ? ' active' : ''}" data-type="">全部 (${allCount})</span>`;
        for (const [type, info] of Object.entries(this.MOMENT_TYPES)) {
            const count = byType[type] || 0;
            if (count > 0) {
                html += `<span class="moment-filter-chip${isActive(type)}" data-type="${type}">${info.icon} ${info.label} (${count})</span>`;
            }
        }
        html += '</div>';
        return html;
    },

    /** 渲染记忆列表（已按时间倒序） */
    _renderMemories() {
        if (!this._memories || this._memories.length === 0) {
            return '<div class="memory-empty">还没有任何记忆碎片<br><small>和她多聊聊天吧</small></div>';
        }

        return '<div class="memory-list">' + this._memories.map(mem => {
            const t = this.MEMORY_TYPES[mem.memory_type] || { icon: '', label: '记忆' };
            const fadedClass = mem.is_faded ? ' memory-faded' : '';
            const importanceBar = this._importanceBar(mem.importance);
            return `
                <div class="memory-item${fadedClass}">
                    <span class="memory-type-icon">${t.icon}</span>
                    <div class="memory-item-body">
                        <div class="memory-content">${this._escapeHtml(mem.content)}</div>
                        <div class="memory-meta">
                            <span class="memory-type-label">${t.label}</span>
                            ${mem.context ? `<span class="memory-context">${this._escapeHtml(mem.context)}</span>` : ''}
                            <span class="memory-source">第${mem.source_day || '?'}天</span>
                            ${mem.is_faded ? '<span class="memory-faded-tag">已淡化</span>' : ''}
                        </div>
                        ${importanceBar}
                    </div>
                </div>
            `;
        }).join('') + '</div>';
    },

    /** 渲染情感时刻列表（已按时间倒序） */
    _renderMoments() {
        if (!this._moments || this._moments.length === 0) {
            return '<div class="memory-empty">还没有任何情感时刻<br><small>特别的互动会被记录在这里</small></div>';
        }

        return '<div class="moment-list">' + this._moments.map(m => {
            const t = this.MOMENT_TYPES[m.moment_type] || { icon: '📝', label: '时刻' };
            const intensityPercent = Math.round(m.emotional_intensity || 0);
            return `
                <div class="moment-item">
                    <span class="moment-type-icon">${t.icon}</span>
                    <div class="moment-item-body">
                        <div class="moment-summary">${this._escapeHtml(m.summary || '（无摘要）')}</div>
                        <div class="moment-meta">
                            <span class="moment-type-label">${t.label}</span>
                            <span class="moment-time">第${m.game_day || '?'}天 ${m.game_time || ''}</span>
                            <span class="moment-intensity">情感强度 ${intensityPercent}%</span>
                        </div>
                        <div class="moment-intensity-bar">
                            <div class="moment-intensity-fill" style="width:${intensityPercent}%"></div>
                        </div>
                    </div>
                </div>
            `;
        }).join('') + '</div>';
    },

    _importanceBar(value) {
        const pct = Math.min(100, Math.max(0, Math.round(value || 0)));
        return `
            <div class="memory-importance-bar">
                <div class="memory-importance-fill" style="width:${pct}%"></div>
                <span class="memory-importance-text">${pct}</span>
            </div>
        `;
    },

    _renderPlaceholder() {
        const overlay = document.createElement('div');
        overlay.id = 'memory-panel-overlay';
        overlay.className = 'memory-panel-overlay';
        overlay.innerHTML = `
            <div class="memory-panel">
                <div class="memory-panel-header">
                    <h3><span class="material-symbols-outlined">auto_awesome_memory</span> 她的回忆</h3>
                </div>
                <div class="memory-loading">
                    <div class="loading-dots"></div>
                    <p>正在回忆...</p>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
    },

    _renderError(msg) {
        const panel = document.querySelector('.memory-panel');
        if (!panel) return;
        const body = panel.querySelector('.memory-stats-bar');
        if (body) body.innerHTML = `<span class="memory-error">${msg}</span>`;
    },

    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    }
};
