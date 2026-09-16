/* 目标追踪 & 成就面板 */
const GoalTracker = {
    update(character) {
        this.updateGoals(character.goals, character.goal_display, character.goal_rules);
        this.updateAchievements();
    },

    updateGoals(goals, goalDisplay, goalRules) {
        if (!goals) return;
        const container = document.getElementById('goals-container');
        if (!container) return;

        const rules = goalRules || {};
        const allKeys = Object.keys(goals);

        // 动态渲染：有新增/删除的 goal 时重建 DOM
        const existingKeys = Array.from(container.querySelectorAll('.goal-item'))
            .map(el => el.getAttribute('data-goal-key'));
        const keysChanged = allKeys.length !== existingKeys.length
            || !allKeys.every(k => existingKeys.includes(k));

        if (keysChanged) {
            container.innerHTML = allKeys.map(key => {
                const val = goals[key] || 0;
                const label = (goalDisplay && goalDisplay[key]) || key;
                const isMission = !['writer_progress', 'coder_progress'].includes(key);
                const tag = isMission ? '<span class="goal-mission-tag">目标</span>' : '';
                const cond = rules[key] ? GoalTracker.formatGoalRule(rules[key]) : '';
                const condHtml = cond ? `<span class="goal-cond">${cond}</span>` : '';
                return `<div class="goal-item" data-goal-key="${key}">
                    <span class="goal-label">${tag}${label}</span>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width:${val}%"></div>
                    </div>
                    <span class="goal-pct">${val}%</span>
                    ${condHtml}
                </div>`;
            }).join('');
        }

        // 更新进度值 + 条件文本
        container.querySelectorAll('.goal-item').forEach(item => {
            const key = item.getAttribute('data-goal-key');
            if (!key || !(key in goals)) return;
            const val = goals[key] || 0;
            const bar = item.querySelector('.progress-fill');
            const pct = item.querySelector('.goal-pct');
            if (bar) bar.style.width = val + '%';
            if (pct) pct.textContent = val + '%';
            const condEl = item.querySelector('.goal-cond');
            if (condEl && rules[key]) condEl.textContent = GoalTracker.formatGoalRule(rules[key]);
        });
    },

    async updateAchievements() {
        try {
            const resp = await fetch('/api/achievements');
            const data = await resp.json();
            if (data.success) {
                this.renderAchievements(data.data);
            }
        } catch (err) {
            console.error('加载成就失败:', err);
        }
    },

    renderAchievements(achievements) {
        const container = document.getElementById('achievement-list');
        if (!container) return;
        const iconMap = {
            'edit': '&#9998;', 'code': '&#9000;', 'people': '&#128101;',
            'fitness': '&#127947;', 'book': '&#128218;', 'computer': '&#128187;',
            'favorite': '&#10084;', 'auto_stories': '&#128214;',
            'terminal': '&#9000;', 'emoji_events': '&#127942;', 'rocket': '&#128640;',
            'star': '&#11088;'
        };

        container.innerHTML = (achievements || []).map(a => {
            const icon = iconMap[a.icon] || '&#11088;';
            const cls = a.unlocked ? 'unlocked' : 'locked';
            const status = a.unlocked ? '已解锁' : `${Math.round(a.progress || 0)}%`;
            const rule = GoalTracker.formatAchievementRule(a);
            const ruleHtml = rule ? `<span class="achievement-cond">${rule}</span>` : '';
            const hintHtml = a.hint ? `<span class="achievement-hint">${a.hint}</span>` : '';
            return `<div class="achievement-item ${cls}">
                <span class="achievement-icon">${icon}</span>
                <div class="achievement-info">
                    <span class="achievement-name">${a.name || ''}</span>
                    <span class="achievement-desc">${a.description || ''} (${status})</span>
                    ${ruleHtml}
                    ${hintHtml}
                </div>
            </div>`;
        }).join('');
    },

    // ── 解锁规则 → 中文文案 ──
    formatCondition(cond) {
        if (!cond || typeof cond !== 'object') return '';
        if (cond.at_location) return `在「${cond.at_location}」`;
        if (cond.time_between) return `时间 ${cond.time_between}`;
        if (cond.not_cooldown) return `冷却结束(${cond.hours || 24}h)`;
        if (cond.attr) {
            const attrName = GoalTracker._attrLabel(cond.attr);
            const opMap = { '>=': '≥', '<=': '≤', '>': '>', '<': '<', '==': '=', 'between': `在 ${cond.min}~${cond.max}` };
            const op = opMap[cond.op] || cond.op || '';
            if (cond.op === 'between') return `${attrName} ${op}`;
            return `${attrName} ${op} ${cond.value}`;
        }
        return '';
    },

    _attrLabel(attr) {
        const map = {
            'mood': '心情', 'energy': '精力', 'happiness': '幸福', 'confidence': '自信',
            'creativity': '创意', 'stress': '压力', 'motivation': '动力', 'fulfillment': '充实',
            'friend_count': '朋友数', 'library_visit': '图书馆访问',
        };
        if (map[attr]) return map[attr];
        if (attr.startsWith('skill_')) return `${attr.slice(6)}技能`;
        if (attr.startsWith('skills.')) return `${attr.slice(7)}技能`;
        if (attr.startsWith('goal_')) return `目标(${attr.slice(5)})`;
        return attr;
    },

    formatAchievementRule(a) {
        if (!a) return '';
        let conds = a.trigger_conditions;
        if (typeof conds === 'string') { try { conds = JSON.parse(conds); } catch (e) { conds = []; } }
        if (Array.isArray(conds) && conds.length) {
            return '条件达成：' + conds.map(c => GoalTracker.formatCondition(c)).filter(Boolean).join(' 且 ');
        }
        let ps = a.progress_source;
        if (typeof ps === 'string') { try { ps = JSON.parse(ps); } catch (e) { ps = null; } }
        if (ps && ps.attr) {
            return '属性达到：' + GoalTracker._attrLabel(ps.attr);
        }
        if (a.unlock_event) {
            const step = a.step_size || 10;
            return `参与事件「${a.unlock_event}」(每次 +${step}%)`;
        }
        return a.hint || '';
    },

    formatGoalRule(rule) {
        if (!rule || typeof rule !== 'object') return '';
        if (Array.isArray(rule.conditions) && rule.conditions.length) {
            return '达成：' + rule.conditions.map(c => GoalTracker.formatCondition(c)).filter(Boolean).join(' 且 ');
        }
        if (rule.progress_source && rule.progress_source.attr) {
            return '进度 = ' + GoalTracker._attrLabel(rule.progress_source.attr);
        }
        return '';
    }
};

// 供 app.js 等其它模块复用
window.GoalFormat = {
    formatAchievementRule: (a) => GoalTracker.formatAchievementRule(a),
    formatGoalRule: (r) => GoalTracker.formatGoalRule(r),
};
