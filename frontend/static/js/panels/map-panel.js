/* 活动地图面板 */
const MapPanel = {
    locations: {},
    activityMap: [],       // 角色专属活动地图数据（解锁、访问次数等）
    currentLocation: 'dormitory',

    async init() {
        // 如果角色数据尚未加载，延迟初始化（由 loadAllData/refreshAll 触发）
        const charName = App.character?.name || '';
        if (!charName) {
            return;
        }
        await this.loadLocationsAndRender();
    },

    async loadLocationsAndRender() {
        try {
            const charName = App.character?.name || '';
            const locResp = await fetch(`/api/character-locations?character_name=${encodeURIComponent(charName)}`);
            const locData = await locResp.json();
            if (locData.success) {
                this.locations = locData.data;
            }
        } catch (err) {
            console.error('加载地图失败:', err);
        }
        await this.loadActivityMap();
        this.renderMap();
    },

    async loadActivityMap() {
        try {
            const charName = App.character?.name || '';
            const resp = await fetch(`/api/activity-map?character_name=${encodeURIComponent(charName)}`);
            const data = await resp.json();
            if (data.success) {
                this.activityMap = data.data || [];
            }
        } catch (err) {
            console.error('加载活动地图数据失败:', err);
        }
    },

    /** 根据 venue_id 查找活动地图条目 */
    getActivityMapEntry(venueId) {
        return this.activityMap.find(m => m.venue_id === venueId) || null;
    },

    renderMap() {
        const svg = document.getElementById('map-svg');
        let html = '';

        this.renderActivityMapSummary();

        const locationEntries = Object.entries(this.locations);
        if (locationEntries.length === 0) return;

        // 动态生成路径：连接所有已解锁的地点（简单全连）
        const unlockedLocs = locationEntries.filter(([_, loc]) => loc.unlocked !== false);
        const unlockedIds = unlockedLocs.map(([id]) => id);
        for (let i = 0; i < unlockedIds.length; i++) {
            for (let j = i + 1; j < unlockedIds.length; j++) {
                const a = this.locations[unlockedIds[i]];
                const b = this.locations[unlockedIds[j]];
                html += `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"
                              stroke="#4a4a6a" stroke-width="1" stroke-dasharray="4,1"/>`;
            }
        }
        // 未解锁的地点用虚线连接
        for (const [id, loc] of locationEntries) {
            if (loc.unlocked === false) {
                // 连接到最近的已解锁地点
                let nearest = unlockedLocs[0];
                if (nearest) {
                    const [nid, nloc] = nearest;
                    html += `<line x1="${loc.x}" y1="${loc.y}" x2="${nloc.x}" y2="${nloc.y}"
                              stroke="#2a2a4a" stroke-width="0.5" stroke-dasharray="2,2"/>`;
                }
            }
        }

        // 动态颜色
        const palette = ['#a78bfa','#60a5fa','#fbbf24','#34d399','#fb923c','#f472b6',
                         '#4ade80','#d4a574','#22d3ee','#e879f9','#f87171','#facc15'];
        const entries = locationEntries;
        for (let i = 0; i < entries.length; i++) {
            const [id, loc] = entries[i];
            const isActive = id === this.currentLocation;
            const fill = palette[i % palette.length];
            const lockedIndicator = loc.unlocked === false ? ' opacity="0.4"' : '';
            const visitCount = loc.visit_count ? `(${loc.visit_count})` : '';

            html += `<g class="map-place ${isActive ? 'active' : ''}" data-location="${id}">
                <circle class="map-place-circle" cx="${loc.x}" cy="${loc.y}" r="5"
                        fill="${fill}"${lockedIndicator} />
                <text x="${loc.x}" y="${loc.y - 7}" text-anchor="middle"
                      fill="#e0e0f0" font-size="3.5" font-family="sans-serif">${loc.name}${visitCount}</text>
            </g>`;
        }

        svg.innerHTML = html;

        svg.querySelectorAll('.map-place').forEach(el => {
            el.addEventListener('click', () => {
                const locId = el.dataset.location;
                this.moveToLocation(locId);
            });
        });
    },

    /** 在地图下方渲染活动地图解锁状态面板 */
    renderActivityMapSummary() {
        const container = document.getElementById('activity-map-summary');
        if (!container) return;

        if (!this.activityMap || this.activityMap.length === 0) {
            container.innerHTML = '<span class="hint-label">活动地图数据加载中…</span>';
            return;
        }

        const totalVenues = this.activityMap.length;
        const unlockedCount = this.activityMap.filter(m => m.unlocked).length;
        const totalVisits = this.activityMap.reduce((sum, m) => sum + (m.visit_count || 0), 0);

        let html = `<span class="hint-label">探索进度：${unlockedCount}/${totalVenues} 已解锁，共访问 ${totalVisits} 次</span>`;
        html += '<div class="activity-map-grid">';
        this.activityMap.forEach(m => {
            const unlockedClass = m.unlocked ? 'unlocked' : 'locked';
            html += `<span class="map-venue-tag ${unlockedClass}" title="${m.venue_name}：${m.unlocked ? '已解锁' : '未解锁'}（访问${m.visit_count || 0}次）">
                ${m.unlocked ? '●' : '○'} ${m.venue_name} (${m.visit_count || 0})
            </span>`;
        });
        html += '</div>';
        container.innerHTML = html;
    },

    async moveToLocation(locationId) {
        try {
            const resp = await fetch('/api/move', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ location: locationId })
            });
            const data = await resp.json();
            if (data.success) {
                this.currentLocation = locationId;
                document.getElementById('current-location').textContent = data.location_name;
                this.renderMap();
                this.renderActivities(data.available_activities);
                Toast.show(`${App.character?.name || '角色'}来到了${data.location_name}`, 'info');
            }
        } catch (err) {
            console.error('移动失败:', err);
        }
    },

    renderActivities(activities) {
        const container = document.getElementById('activities-list');
        container.innerHTML = '<span class="hint-label">可用活动：</span>';

        if (!activities || activities.length === 0) {
            container.innerHTML += '<span style="font-size:11px;color:var(--text-muted)">暂无可用活动</span>';
            return;
        }

        // 支持两种格式：[{id, name}] 或 ["id_string"]
        activities.forEach(item => {
            const id = typeof item === 'string' ? item : item.id;
            const name = typeof item === 'string' ? this.getActivityName(id) : (item.name || this.getActivityName(id));
            const btn = document.createElement('button');
            btn.textContent = name;
            btn.className = 'activity-btn';
            btn.title = id;
            btn.addEventListener('click', () => this.performActivity(id));
            container.appendChild(btn);
        });
    },

    async performActivity(activityId) {
        try {
            const resp = await fetch('/api/activity', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ activity: activityId })
            });
            const data = await resp.json();

            if (data.success) {
                Toast.show(data.data.description, 'success');
                if (data.data.character) {
                    App.refreshAll(data.data.character);
                }
            } else {
                Toast.show(data.error || data.message || '活动执行失败', 'danger');
            }
        } catch (err) {
            console.error('活动执行失败:', err);
        }
    },

    getActivityName(id) {
        const names = {
            // 通用活动
            'study_library': '学习', 'write_novel': '写作', 'code_practice': '编程',
            'workout': '健身', 'eat': '用餐', 'socialize': '聚会',
            'shopping': '购物', 'travel_explore': '旅行', 'attend_class': '上课',
            'relax_home': '休息', 'shower': '洗澡',
            // 苏晴 - 律师
            'attend_trial': '参加庭审', 'case_research': '研究案例',
            'meet_client': '会见客户', 'debate_practice': '辩论练习',
            'cafe_legal_reading': '法律文献阅读', 'gym_training': '体能训练',
            'park_jogging': '晨跑锻炼', 'home_case_review': '整理案卷',
            'travel_research': '出差调研',
            // 林小鹿 - 画家
            'paint_in_studio': '画室创作', 'visit_gallery': '参观画廊',
            'sketch_park': '公园写生', 'attend_art_class': '美院上课',
            'museum_study': '临摹大师', 'cafe_sketch': '咖啡厅速写',
            'home_create': '居家创作', 'travel_inspire': '旅途采风',
            // 叶知秋 - 医生
            'ward_round': '查房问诊', 'surgery': '主刀手术',
            'medical_research': '医学研究', 'attend_conference': '参加医学会议',
            'hospital_er_shift': '急诊轮值', 'cafe_journal': '文献阅读',
            'park_jogging_yzq': '晨间慢跑', 'home_case_organize': '整理病历',
            'gym_fitness_yzq': '体能保持',
            // 沈念 - 作家
            'write_novel_session': '小说创作', 'literary_analysis': '文学分析',
            'bookstore_explore': '逛旧书店', 'salon_share': '文学沙龙分享',
            'cafe_writing': '咖啡馆写作', 'park_walk': '公园散步',
            'travel_inspire_sn': '旅途采风',
            // 顾云溪 - 游戏开发者
            'game_design_work': '游戏设计', 'coding_sprint': '编程冲刺',
            'hackathon_join': '黑客马拉松', 'playtest': '游戏试玩',
            'lab_tech_research': '技术调研', 'cafe_team_discuss': '团队讨论',
            'gym_strength': '力量训练', 'park_running': '户外跑步',
            'home_review': '战术复盘',
        };
        return names[id] || id;
    },

    updateLocation(locationId) {
        this.currentLocation = locationId;
        this.renderMap();
    }
};
