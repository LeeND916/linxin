/* 事件日志面板 — 分类展示事件（全部/新闻/任务/社交/世界观/日常） */
const EventLogPanel = {
    _allEvents: [],
    _currentFilter: 'all',

    async refresh() {
        try {
            const resp = await fetch('/api/events?limit=100');
            const data = await resp.json();
            if (data.success) {
                this._allEvents = data.data || [];
                this._renderFilterBar();
                this._applyFilter();
            }
        } catch (e) {
            console.error('EventLog refresh failed:', e);
        }
    },

    render(events) {
        this._allEvents = events || [];
        this._renderFilterBar();
        this._applyFilter();
    },

    appendEvents(newEvents) {
        if (!newEvents || !newEvents.length) return;
        this._allEvents = [...newEvents, ...this._allEvents];
        const maxItems = 100;
        if (this._allEvents.length > maxItems) this._allEvents.length = maxItems;
        this._applyFilter();
    },

    // 带守卫的增量刷新：仅当事件 id 集合变化时才重渲，供 3 秒轮询调用，
    // 实现跨窗口/后台写入（新闻注入、朋友更新等）的事件实时可见，且不丢滚动位置、不闪烁。
    async refreshIfNeeded() {
        try {
            const resp = await fetch('/api/events?limit=100');
            const data = await resp.json();
            if (!data.success) return;
            const incoming = data.data || [];
            const incomingIds = incoming.map(e => e.id).join(',');
            const currentIds = (this._allEvents || []).map(e => e.id).join(',');
            if (incomingIds === currentIds) return;  // 无变化，不动面板
            this._allEvents = incoming;
            this._renderFilterBar();
            this._applyFilter();
        } catch (e) {
            console.error('EventLog refreshIfNeeded failed:', e);
        }
    },

    // ── 分类体系 ──

    _getCategory(eventType, title) {
        const t = eventType || '';
        const tt = (title || '').toLowerCase();
        if (t === 'news') return 'news';
        if (t === 'case') return 'case';
        if (t === 'mission') return 'mission';
        if (t === 'relation_initiated' || t === 'relation_change') return 'social';
        if (t === 'narrative_scheduled') return 'world';
        // 社交类 structured_event
        if (t === 'structured_event' && ['friend_support', 'argument', 'social_party', 'invitation_dinner', 'neighbor_visit', 'coincidence', 'betrayal', 'group_project', 'public_speech', 'friend_support'].some(k => tt.includes(k))) return 'social';
        if (t === 'narrative_scheduled' || t === 'world_event') return 'world';
        return 'daily';
    },

    CATEGORIES: [
        { key: 'all', label: '全部', icon: 'all_inclusive' },
        { key: 'news', label: '新闻', icon: 'newspaper' },
        { key: 'mission', label: '任务', icon: 'explore' },
        { key: 'social', label: '社交', icon: 'diversity_3' },
        { key: 'world', label: '世界观', icon: 'public' },
        { key: 'daily', label: '日常', icon: 'routine' },
    ],

    _renderFilterBar() {
        const bar = document.getElementById('event-filter-bar');
        if (!bar) return;
        const counts = {};
        this._allEvents.forEach(e => {
            const cat = this._getCategory(e.type || e.event_type || '', e.title || '');
            counts[cat] = (counts[cat] || 0) + 1;
        });
        counts.all = this._allEvents.length;

        bar.innerHTML = this.CATEGORIES.map(c => {
            const cnt = counts[c.key] || 0;
            const active = c.key === this._currentFilter ? ' active' : '';
            return `<span class="event-filter-chip${active}" data-cat="${c.key}">${c.label} (${cnt})</span>`;
        }).join('');

        bar.querySelectorAll('.event-filter-chip').forEach(chip => {
            chip.addEventListener('click', () => {
                this._currentFilter = chip.dataset.cat;
                this._renderFilterBar();
                this._applyFilter();
            });
        });
    },

    _applyFilter() {
        const container = document.getElementById('events-list');
        if (!container) return;

        let filtered = this._allEvents;
        if (this._currentFilter !== 'all') {
            filtered = this._allEvents.filter(e => {
                return this._getCategory(e.type || e.event_type || '', e.title || '') === this._currentFilter;
            });
        }

        if (filtered.length === 0) {
            container.innerHTML = '<div class="event-empty">暂无此类事件记录</div>';
            return;
        }

        container.innerHTML = filtered.map(e => this._renderEventItem(e)).join('');

        requestAnimationFrame(() => {
            container.querySelectorAll('.event-item:first-child').forEach(el => {
                el.classList.add('event-highlight');
                setTimeout(() => el.classList.remove('event-highlight'), 3000);
            });
        });
    },

    // ── 渲染单条事件 ──

    _renderEventItem(e) {
        const eventType = e.type || e.event_type || '';
        const eventDay = e.day ?? e.game_day;
        const eventTime = e.time || e.game_time || '';
        const eventTitle = e.title || '';
        const eventContent = e.content || e.description || '';
        const eventLocation = e.location || '';
        const friendName = e.friend_name || '';

        const typeClass = this._getTypeClass(eventType);
        const typeLabel = this._getTypeLabel(eventType);
        let timeDisplay = eventDay ? `第${eventDay}天 ${eventTime.slice(0, 5)}` : eventTime.slice(0, 5);
        // 新闻事件：追加现实时间（从 effects.real_world_time 读取，缺则回退到 created_at）
        if (eventType === 'news') {
            const raw = (e.real_world_time || e.created_at || '').toString();
            if (raw) {
                const dt = new Date(raw);
                if (!isNaN(dt.getTime())) {
                    const pad = (n) => String(n).padStart(2, '0');
                    const realStr = `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
                    timeDisplay += ` <span class="event-real-time">| 现实 ${realStr}</span>`;
                }
            }
        }

        const stateChanges = e.state_changes || (e.effects && e.effects.state_changes) || [];
        const relationChanges = e.relation_changes || (e.effects && e.effects.relation_changes) || [];

        let changesHtml = '';
        if (stateChanges.length > 0) {
            changesHtml += '<div class="event-changes">';
            stateChanges.forEach(sc => {
                const delta = sc.delta || 0;
                const sign = delta > 0 ? '+' : '';
                const cssClass = delta > 0 ? 'positive' : (delta < 0 ? 'negative' : '');
                const attrLabel = (StatusPanel && StatusPanel.ATTR_CN_MAP)
                    ? (StatusPanel.ATTR_CN_MAP[sc.attribute] || sc.attribute)
                    : sc.attribute;
                changesHtml += `<span class="change-tag state-change ${cssClass}">${attrLabel} ${sign}${delta}</span>`;
            });
            changesHtml += '</div>';
        }

        if (relationChanges.length > 0) {
            changesHtml += '<div class="event-changes">';
            relationChanges.forEach(rc => {
                const name = rc.friend_name || rc.name || friendName || '?';
                const oldR = rc.old || {};
                const newR = rc.new || {};
                if (rc.attribute && rc.delta !== undefined) {
                    const cn = {
                        closeness: '亲密度', trust: '信任', affection: '好感',
                        rivalry: '竞争', hostility: '敌意', fear: '畏惧',
                        player_trust: '玩家信任', player_affection: '玩家好感',
                        player_respect: '玩家敬重', player_intimacy: '玩家亲密度',
                        intimacy: '亲密度',
                    }[rc.attribute] || rc.attribute;
                    const d = rc.delta;
                    const sign = d > 0 ? '+' : '';
                    const cls = d > 0 ? 'positive' : 'negative';
                    changesHtml += `<span class="change-tag relation-change ${cls}">${name} ${cn} ${sign}${d.toFixed(0)}</span>`;
                } else {
                    ['closeness', 'trust', 'affection', 'intimacy'].forEach(attr => {
                        const cn = {closeness:'亲密度', intimacy:'亲密度', trust:'信任', affection:'好感'}[attr];
                        if (oldR[attr] !== undefined && newR[attr] !== undefined) {
                            const d = newR[attr] - oldR[attr];
                            if (d !== 0) {
                                const sign = d > 0 ? '+' : '';
                                const cls = d > 0 ? 'positive' : 'negative';
                                changesHtml += `<span class="change-tag relation-change ${cls}">${name} ${cn} ${sign}${d.toFixed(0)}</span>`;
                            }
                        }
                    });
                }
            });
            changesHtml += '</div>';
        }

        const locationTag = eventLocation
            ? `<span class="event-location-tag">${this._esc(eventLocation)}</span>`
            : '';

        return `
            <div class="event-item ${typeClass}" data-event-type="${eventType}">
                <div class="event-time">${timeDisplay}</div>
                <div class="event-header">
                    <span class="event-type-tag ${typeClass}">${typeLabel}</span>
                    <span class="event-title">${this._esc(eventTitle)}</span>
                    ${locationTag}
                </div>
                <div class="event-desc">${this._esc(eventContent)}</div>
                ${changesHtml}
            </div>
        `;
    },

    _getTypeClass(eventType) {
        const map = {
            'personal_activity': 'event-personal', 'social': 'event-social',
            'physical_state': 'event-physical', 'relation_change': 'relation_change',
            'outfit_change': 'event-outfit', 'outfit': 'event-outfit',
            'academic': 'event-academic', 'environment': 'event-personal',
            'inspiration': 'event-personal', 'achievement': 'event-personal',
            'emotional': 'event-social', 'activity': 'event-personal',
            'llm_tick': 'event-physical', 'llm_tick_event': 'event-physical', 'sleep_decay': 'event-physical',
            'llm_event': 'event-personal',
            'active_message': 'event-social', 'auto_message_llm': 'event-social',
            'news': 'event-news', 'case': 'event-news',
            'mission': 'event-mission',
            'narrative_scheduled': 'event-personal',
            'relation_initiated': 'relation_change',
        };
        return map[eventType] || 'event-personal';
    },

    _getTypeLabel(eventType) {
        const map = {
            'personal_activity': '个人', 'social': '社交',
            'physical_state': '身体', 'relation_change': '关系',
            'outfit_change': '换装', 'outfit': '换装',
            'academic': '学业', 'environment': '环境',
            'inspiration': '灵感', 'achievement': '成就',
            'emotional': '情感', 'activity': '活动',
            'llm_tick': '心跳', 'llm_tick_event': '心跳', 'sleep_decay': '睡眠', 'llm_event': 'AI事件',
            'active_message': '主动消息', 'auto_message_llm': '主动消息',
            'news': '资讯', 'case': '任务', 'mission': '任务',
            'narrative_scheduled': '世界观',
            'relation_initiated': '关系',
        };
        return map[eventType] || '事件';
    },

    _esc(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
};
