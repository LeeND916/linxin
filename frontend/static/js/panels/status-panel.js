/* 角色状态面板 */
const StatusPanel = {
    // 记录最近变化的属性及时间戳和delta值 { key: { timestamp, delta } }
    lastEffects: {},

    // 属性名中英文映射表（包含物理/心理 + 全部技能标签）
    ATTR_CN_MAP: {
        // 物理 & 心理
        boredom: '无聊', disappointment: '失望',
        energy: '精力', fulfillment: '充实', joy: '开心',
        player_respect: '玩家尊重', mood: '情绪', motivation: '动力',
        stress: '压力', health: '健康', hunger: '饥饿', hygiene: '卫生',
        happiness: '幸福度', loneliness: '孤独感', confidence: '自信',
        creativity: '创造力', anger: '愤怒',
        player_trust: '玩家信任', player_affection: '玩家好感',
        player_intimacy: '亲密',
        // 技能属性（p1 晓月）
        writing: '写作', coding: '编程', social: '社交', learning: '学习', fitness: '健身',
        // p2 苏晴
        legal_knowledge: '法律知识', debate: '辩论', case_analysis: '案例分析', negotiation: '谈判',
        // p3 林小鹿
        painting: '绘画', art_theory: '艺术理论', observation: '观察',
        // p4 叶知秋
        medical_knowledge: '医学知识', diagnosis: '诊断能力', surgery: '手术技能', empathy: '同理心', stress_resistance: '抗压能力',
        // p5 沈念
        literary_analysis: '文学分析',
        // p6 顾云溪
        game_design: '游戏设计', project_management: '项目管理', teamwork: '协作',
    },

    /**
     * 动态构建属性中文标签：将 character.skill_display 中 ATTR_CN_MAP 尚未覆盖的新技能注入 ATTR_CN_MAP。
     */
    _injectSkillLabels(character) {
        if (!character || !character.skills) return;
        const labels = character.skill_display || {};
        for (const key of Object.keys(character.skills)) {
            if (!this.ATTR_CN_MAP[key]) {
                this.ATTR_CN_MAP[key] = labels[key] || key;
            }
        }
    },

    /**
     * 记录对话产生的属性变化（供扇形图高亮和delta标注用）
     * @param {Object} effects - { mood: +5, stress: -3, ... }
     */
    recordEffects(effects) {
        if (!effects) return;
        const now = Date.now();
        for (const key of Object.keys(effects)) {
            this.lastEffects[key] = { timestamp: now, delta: effects[key] };
        }
    },

    /**
     * 批量应用状态变化（从加速器返回），触发闪烁动画
     * @param {Array} stateChanges - [{attribute: "energy", old: 80, new: 90, delta: 10}, ...]
     */
    applyStateChanges(stateChanges) {
        if (!stateChanges || stateChanges.length === 0) return;
        const effects = {};
        stateChanges.forEach(sc => {
            if (sc.attribute && sc.delta) {
                effects[sc.attribute] = sc.delta;
            }
        });
        this.recordEffects(effects);

        setTimeout(() => {
            if (window.App && window.App.character) {
                this.update(window.App.character);
            }
        }, 100);
    },

    /**
     * 检查属性是否在最近 5 秒内变化过，返回 delta 值或 null
     */
    _isRecentlyChanged(key) {
        const e = this.lastEffects[key];
        if (!e || (Date.now() - e.timestamp) > 5000) return null;
        return e.delta;
    },

    update(character) {
        if (!character || !character.physical || !character.mental) {
            console.error('[StatusPanel] Invalid character data:', character);
            return;
        }
        this._injectSkillLabels(character);
        this.renderPhysical(character.physical);
        this.renderMental(character.mental);
        this.renderSkills(character.skills, character.skill_display);
        this.renderPlayerRelation(character.player_relation);
        this.renderPlayerIdentity(character);
        this.loadWarnings();
    },

    async loadWarnings() {
        try {
            const resp = await fetch('/api/character/warnings');
            const data = await resp.json();
            if (data.success) {
                this.announceWarnings(data.warnings || []);
            }
        } catch (e) {
            // 静默失败
        }
    },

    /**
     * 关系/情绪约束通知统一进小喇叭（不再渲染状态面板红色横幅 #warning-banner）。
     * 用 change-key 去重：同一组 warning 只在“发生变化”时才播报，避免每次刷新状态面板都刷屏。
     */
    announceWarnings(warnings) {
        if (!warnings || !warnings.length) return;
        const key = warnings.map(w => w.message).join('|');
        if (this._lastWarnedKey === key) return;
        this._lastWarnedKey = key;
        if (typeof App !== 'undefined' && App.announce) {
            warnings.forEach(w => App.announce(w.message, '⚠️'));
        }
    },

    /**
     * 生成新进度条 HTML（统一格式：标签+数值在头部，进度条在下方）
     * @param {Object} item - { key, label, val, barColor?, color?, threshold?, highBad?, lowThreshold? }
     */
    _statBar(item) {
        const delta = this._isRecentlyChanged(item.key);
        let deltaHtml = '';
        let flashClass = '';
        if (delta !== null) {
            const sign = delta > 0 ? '+' : '';
            const deltaCls = delta > 0 ? 'delta-up' : 'delta-down';
            deltaHtml = `<span class="delta-tag ${deltaCls}">${sign}${delta.toFixed(0)}</span>`;
            flashClass = ' stat-flash';
        }
        // 低值判断
        let low = false;
        if (item.threshold != null) {
            low = item.highBad ? (item.val >= item.threshold) : (item.val <= item.threshold);
        } else if (item.lowThreshold != null) {
            low = item.val <= item.lowThreshold;
        }
        const lowClass = low ? ' stat-low' : '';

        // 心理状态有独立颜色，物理/技能/导师用 data-color 属性
        const colorAttr = item.barColor || '';
        const fillStyle = item.color
            ? `width:${item.val}%; background:${item.color};`
            : `width:${item.val}%;`;

        return `<div class="stat-bar${lowClass}${flashClass}" data-color="${colorAttr}">
            <div class="stat-bar-head">
                <span class="stat-bar-label">${item.label}</span>
                <span class="stat-bar-val">${item.val.toFixed(0)}${deltaHtml}</span>
            </div>
            <div class="stat-bar-track">
                <div class="stat-bar-fill" style="${fillStyle}"></div>
            </div>
        </div>`;
    },

    renderPhysical(phys) {
        const container = document.getElementById('physical-status');
        if (!container) return;
        const items = [
            { key: 'health',  label: '健康', val: phys.health,  barColor: 'health',  threshold: 30, highBad: false },
            { key: 'energy',  label: '精力', val: phys.energy,  barColor: 'energy',  threshold: 25, highBad: false },
            { key: 'hunger',  label: '饥饿', val: phys.hunger,  barColor: 'hunger',  threshold: 75, highBad: true },
            { key: 'hygiene', label: '卫生', val: phys.hygiene, barColor: 'hygiene', threshold: 20, highBad: false },
        ];
        container.innerHTML = items.map(item => this._statBar(item)).join('');
    },

    renderMental(mental) {
        const posContainer = document.getElementById('mental-positive');
        const negContainer = document.getElementById('mental-negative');
        if (!posContainer || !negContainer) return;

        // 正面情绪（7项）
        const positive = [
            { key: 'mood',        label: '情绪',   val: mental.mood,        color: '#f472b6' },
            { key: 'happiness',   label: '幸福度', val: mental.happiness,   color: '#fbbf24' },
            { key: 'confidence',  label: '自信',   val: mental.confidence,  color: '#60a5fa' },
            { key: 'motivation',  label: '动力',   val: mental.motivation,  color: '#4ade80' },
            { key: 'creativity',  label: '创造力', val: mental.creativity,  color: '#fb923c' },
            { key: 'joy',         label: '开心',   val: mental.joy,         color: '#fbbf24' },
            { key: 'fulfillment', label: '充实',   val: mental.fulfillment, color: '#2dd4bf' },
        ];

        // 负面情绪（5项）
        const negative = [
            { key: 'stress',         label: '压力',   val: mental.stress,         color: '#f87171' },
            { key: 'loneliness',     label: '孤独感', val: mental.loneliness,     color: '#94a3b8' },
            { key: 'anger',          label: '愤怒',   val: mental.anger,          color: '#ef4444' },
            { key: 'disappointment', label: '失望',   val: mental.disappointment, color: '#94a3b8' },
            { key: 'boredom',        label: '无聊',   val: mental.boredom,        color: '#a78bfa' },
        ];

        posContainer.innerHTML = positive.map(item => this._statBar(item)).join('');
        negContainer.innerHTML = negative.map(item => this._statBar(item)).join('');
    },

    renderSkills(skillsObj, skillDisplay) {
        const container = document.getElementById('skill-status');
        if (!container) return;
        if (!skillsObj || Object.keys(skillsObj).length === 0) {
            container.innerHTML = '';
            return;
        }
        const labels = skillDisplay || {};
        const items = Object.entries(skillsObj).map(([key, val]) => ({
            key, label: labels[key] || this.ATTR_CN_MAP[key] || key, val, barColor: 'skill', lowThreshold: 30
        }));
        container.innerHTML = items.map(item => this._statBar(item)).join('');
    },

    renderPlayerRelation(rel) {
        const container = document.getElementById('player-status');
        if (!container) return;
        const items = [
            { key: 'player_trust',     label: '信任', val: rel.trust,     barColor: 'mood', threshold: 25, highBad: false },
            { key: 'player_affection', label: '好感', val: rel.affection, barColor: 'mood', threshold: 25, highBad: false },
            { key: 'player_respect',   label: '尊重', val: rel.respect,   barColor: 'mood', threshold: 25, highBad: false },
            { key: 'player_intimacy',  label: '亲密', val: rel.intimacy,  barColor: 'mood', threshold: 25, highBad: false },
        ];
        container.innerHTML = items.map(item => this._statBar(item)).join('');
    },

    /**
     * 渲染玩家专属档案（AI伴侣系统升级 — 阶段一）
     * 在关系区域标题旁显示玩家身份、称呼、互动风格
     */
    renderPlayerIdentity(character) {
        const identity = character.player_identity || '导师';
        const nickname = character.player_nickname || identity;
        const style = character.interaction_style || '';

        // 更新关系区域标题（保留图标，PC/移动端通用）
        const playerHeader = document.querySelector('.status-group.player h4');
        if (playerHeader && character.player_identity) {
            playerHeader.innerHTML = `<span class="material-symbols-outlined">favorite</span> 与${character.player_identity}（你）的关系`;
        }

        // PC：单行紧凑展示在顶栏下方的玩家档案条（你的身份 / 她叫你 / 相处模式）
        const strip = document.getElementById('player-identity-strip');
        if (strip && getComputedStyle(strip).display !== 'none') {
            strip.innerHTML = `
                <span class="pi-tag"><span class="pi-label">你的身份</span><span class="pi-value">${identity}</span></span>
                <span class="pi-tag"><span class="pi-label">她叫你</span><span class="pi-value">${nickname}</span></span>
                ${style ? `<span class="pi-tag"><span class="pi-label">相处模式</span><span class="pi-value">${style}</span></span>` : ''}
            `;
            return; // 移动端才走下方状态面板注入，避免重复
        }

        // 移动端（或条被隐藏时）：保持原状，注入关系区域
        let profileEl = document.getElementById('player-identity-profile');
        if (!profileEl) {
            const playerGroup = document.querySelector('.status-group.player');
            if (!playerGroup) return;
            profileEl = document.createElement('div');
            profileEl.id = 'player-identity-profile';
            profileEl.className = 'player-identity-profile';
            playerGroup.insertBefore(profileEl, playerGroup.firstChild);
        }

        profileEl.innerHTML = `
            <div class="player-id-row">
                <span class="player-id-tag">👤 你的身份：${identity}</span>
                <span class="player-id-tag">💬 她叫你：${nickname}</span>
            </div>
            ${style ? `<div class="player-id-style">🎯 ${style}</div>` : ''}
        `;
    }
};
