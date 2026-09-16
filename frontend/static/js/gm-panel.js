/* GM 属性设置面板 */
const GMPanel = {
    // 静态属性分组定义（玩家/物理/心理保持不变——这些是通用属性）
    STATIC_GROUPS: [
        {
            id: 'player', title: '玩家关系',
            keys: [
                { key: 'player_trust', label: '信任' },
                { key: 'player_affection', label: '好感' },
                { key: 'player_respect', label: '尊重' },
                { key: 'player_intimacy', label: '亲密' },
            ]
        },
        {
            id: 'physical', title: '物理状态',
            keys: [
                { key: 'health', label: '健康' },
                { key: 'energy', label: '精力' },
                { key: 'hunger', label: '饥饿' },
                { key: 'hygiene', label: '卫生' },
            ]
        },
        {
            id: 'mental', title: '心理状态',
            keys: [
                { key: 'mood', label: '情绪' },
                { key: 'stress', label: '压力' },
                { key: 'happiness', label: '幸福度' },
                { key: 'motivation', label: '动力' },
                { key: 'joy', label: '开心' },
                { key: 'anger', label: '愤怒' },
                { key: 'disappointment', label: '失望' },
                { key: 'boredom', label: '无聊' },
                { key: 'fulfillment', label: '充实' },
                { key: 'loneliness', label: '孤独感' },
                { key: 'confidence', label: '自信' },
                { key: 'creativity', label: '创造力' },
            ]
        },
    ],

    // 技能标签兜底映射（静态覆盖 + 可被 skill_display 覆盖）
    SKILL_LABEL_FALLBACK: {
        writing: '写作', coding: '编程', social: '社交', learning: '学习', fitness: '健身',
        legal_knowledge: '法律知识', debate: '辩论', case_analysis: '案例分析', negotiation: '谈判',
        painting: '绘画', art_theory: '艺术理论', observation: '观察',
        medical_knowledge: '医学知识', diagnosis: '诊断能力', surgery: '手术技能',
        empathy: '同理心', stress_resistance: '抗压能力',
        literary_analysis: '文学分析', creativity: '创造力',
        game_design: '游戏设计', project_management: '项目管理', teamwork: '协作',
    },

    init() {
        document.getElementById('btn-gm-panel').addEventListener('click', () => this.open());
        document.getElementById('btn-close-gm').addEventListener('click', () => this.close());
        document.getElementById('gm-modal-overlay').addEventListener('click', (e) => {
            if (e.target.id === 'gm-modal-overlay') this.close();
        });
        document.getElementById('btn-gm-cancel').addEventListener('click', () => this.close());
        document.getElementById('btn-gm-apply').addEventListener('click', () => this.apply());

        // Tab 切换
        document.querySelectorAll('.gm-tab').forEach(tab => {
            tab.addEventListener('click', () => this.switchTab(tab.dataset.tab));
        });

        // 数据管理：输入验证 + 确认重置
        const confirmInput = document.getElementById('gm-confirm-input');
        const resetBtn = document.getElementById('btn-gm-reset');
        confirmInput.addEventListener('input', () => {
            const expected = document.getElementById('gm-confirm-name').textContent;
            resetBtn.disabled = confirmInput.value.trim() !== expected;
        });
        resetBtn.addEventListener('click', () => this.resetRuntimeData());

        // 数据管理：输入验证 + 彻底删除角色
        const deleteInput = document.getElementById('gm-delete-input');
        const deleteBtn = document.getElementById('btn-gm-delete');
        deleteInput.addEventListener('input', () => {
            const expected = document.getElementById('gm-delete-name').textContent;
            deleteBtn.disabled = deleteInput.value.trim() !== expected;
        });
        deleteBtn.addEventListener('click', () => this.deleteCharacter());
    },

    async open() {
        // 从后端实时拉取最新数据，而非使用前端缓存
        try {
            const resp = await fetch('/api/character');
            const data = await resp.json();
            if (data.success && data.data) {
                this.populateSliders(data.data);
                document.getElementById('gm-confirm-name').textContent = data.data.name;
                document.getElementById('gm-delete-name').textContent = data.data.name;
                this.switchTab('settings');
                document.getElementById('gm-modal-overlay').style.display = 'flex';
                App.refreshAll(data.data);
                return;
            }
        } catch (err) {
            console.warn('[GM] 实时拉取角色数据失败，降级使用缓存:', err);
        }
        // 网络异常时降级使用缓存
        if (App.character) {
            this.populateSliders(App.character);
            document.getElementById('gm-confirm-name').textContent = App.character.name;
            document.getElementById('gm-delete-name').textContent = App.character.name;
            this.switchTab('settings');
            document.getElementById('gm-modal-overlay').style.display = 'flex';
        } else {
            alert("没有女主数据");
        }
    },

    close() {
        document.getElementById('gm-modal-overlay').style.display = 'none';
    },

    /** 切换 GM 面板 Tab */
    switchTab(tabName) {
        document.querySelectorAll('.gm-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.tab === tabName);
        });
        document.getElementById('gm-panel-settings').style.display = tabName === 'settings' ? '' : 'none';
        document.getElementById('gm-panel-reset').style.display = tabName === 'reset' ? '' : 'none';
        document.getElementById('gm-panel-delete').style.display = tabName === 'delete' ? '' : 'none';
        // Footer（应用/取消）仅在属性设置 tab 显示
        document.getElementById('gm-modal-footer').style.display = tabName === 'settings' ? '' : 'none';
        // 离开对应 tab 时清空确认输入，防止误触
        if (tabName !== 'reset') {
            document.getElementById('gm-confirm-input').value = '';
            document.getElementById('btn-gm-reset').disabled = true;
        }
        if (tabName !== 'delete') {
            document.getElementById('gm-delete-input').value = '';
            document.getElementById('btn-gm-delete').disabled = true;
        }
    },

    /** 重置角色运行时数据 */
    async resetRuntimeData() {
        // 先停钟：reload 窗口内不再发 tick，防止旧 final_game_day 覆盖重置值
        if (typeof App !== 'undefined') App.stopGameClock();
        const btn = document.getElementById('btn-gm-reset');
        btn.disabled = true;
        btn.textContent = '正在重置...';

        try {
            const resp = await fetch('/api/character/reset-runtime-data', { method: 'DELETE' });
            const data = await resp.json();
            if (data.success) {
                // 重置成功后，阻止 beforeunload / visibilitychange 自动保存覆盖重置值
                if (typeof App !== 'undefined') App._skipNextSave = true;
                const msg = data.message + (data.failed_files && data.failed_files.length
                    ? '\n部分文件删除失败：\n' + data.failed_files.join('\n') : '');
                alert(msg);
                setTimeout(() => location.reload(), 500);
            } else {
                alert('重置失败：' + (data.error || '未知错误'));
                btn.disabled = false;
                btn.textContent = '确认重置';
            }
        } catch (err) {
            alert('请求失败：' + err.message);
            btn.disabled = false;
            btn.textContent = '确认重置';
        }
    },

    /** 彻底删除角色（硬删除，不可恢复） */
    async deleteCharacter() {
        const name = document.getElementById('gm-delete-name').textContent;
        const charId = (App.character && App.character.id) ? App.character.id : null;
        if (!charId) {
            alert('无法获取当前角色 ID，删除中止');
            return;
        }
        if (!confirm(`⚠ 此操作不可恢复！\n将永久删除「${name}」的全部游戏数据（含对话记录、肖像、记忆等）。\n确定继续？`)) {
            return;
        }
        // 先停钟：reload 窗口内不再发 tick，防止旧 final_game_day 覆盖下一个活跃角色
        if (typeof App !== 'undefined') App.stopGameClock();
        const btn = document.getElementById('btn-gm-delete');
        btn.disabled = true;
        btn.textContent = '正在删除...';

        try {
            const resp = await fetch(`/api/character/${charId}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.success) {
                const msg = `角色「${name}」已彻底删除。` + (data.failed_files && data.failed_files.length
                    ? '\n部分文件删除失败：\n' + data.failed_files.join('\n') : '');
                alert(msg);
                setTimeout(() => location.reload(), 500);
            } else {
                alert('删除失败：' + (data.error || '未知错误'));
                btn.disabled = false;
                btn.textContent = '彻底删除';
            }
        } catch (err) {
            alert('请求失败：' + err.message);
            btn.disabled = false;
            btn.textContent = '彻底删除';
        }
    },

    /** 从角色数据构建动态技能/目标滑块列表 */
    _buildDynamicGroups(char) {
        const groups = [...this.STATIC_GROUPS];

        // 技能组：从 char.skills 动态读取
        const skillDisplay = char.skill_display || {};
        const skillKeys = char.skills ? Object.keys(char.skills) : [];
        if (skillKeys.length > 0) {
            groups.push({
                id: 'skill', title: '技能属性',
                keys: skillKeys.map(k => ({
                    key: 'skill:' + k,
                    label: skillDisplay[k] || this.SKILL_LABEL_FALLBACK[k] || k,
                }))
            });
        }

        // 目标组：从 char.goals 动态读取
        const goalDisplay = char.goal_display || {};
        const goalKeys = char.goals ? Object.keys(char.goals) : [];
        if (goalKeys.length > 0) {
            groups.push({
                id: 'goal', title: '目标进度',
                keys: goalKeys.map(k => ({
                    key: 'goal:' + k,
                    label: goalDisplay[k] || k,
                }))
            });
        }

        return groups;
    },

    /** 根据 character 对象填充滑块 */
    populateSliders(char) {
        if (!char) return;
        const groups = this._buildDynamicGroups(char);

        groups.forEach(group => {
            const container = document.getElementById(`gm-${group.id}-sliders`);
            if (!container) return;

            let html = '';
            group.keys.forEach(item => {
                let val = 50;

                if (group.id === 'player') {
                    val = char.player_relation ? (char.player_relation[item.key.replace('player_', '')] || 50) : 50;
                } else if (group.id === 'physical') {
                    val = char.physical ? (char.physical[item.key] || 50) : 50;
                } else if (group.id === 'mental') {
                    val = char.mental ? (char.mental[item.key] || 50) : 50;
                } else if (group.id === 'skill') {
                    // item.key 格式: "skill:diagnosis"
                    const skillKey = item.key.slice(6); // 去掉 "skill:" 前缀
                    val = char.skills ? (char.skills[skillKey] != null ? char.skills[skillKey] : 50) : 50;
                } else if (group.id === 'goal') {
                    // item.key 格式: "goal:novel_publish"
                    const goalKey = item.key.slice(5); // 去掉 "goal:" 前缀
                    val = char.goals ? (char.goals[goalKey] != null ? char.goals[goalKey] : 50) : 50;
                }

                val = Math.round(Number(val));
                html += `<div class="gm-slider-row">
                    <label>${item.label}</label>
                    <input type="range" min="0" max="100" value="${val}"
                        data-gm-key="${item.key}" data-gm-group="${group.id}" />
                    <span class="gm-val">${val}</span>
                </div>`;
            });
            container.innerHTML = html;

            container.querySelectorAll('input[type="range"]').forEach(input => {
                const valSpan = input.parentElement.querySelector('.gm-val');
                input.addEventListener('input', () => {
                    valSpan.textContent = input.value;
                });
            });
        });
    },

    /** 收集所有滑块值，发送请求 */
    async apply() {
        const overlay = document.getElementById('gm-modal-overlay');
        const attrs = {};

        overlay.querySelectorAll('input[type="range"][data-gm-key]').forEach(input => {
            const key = input.dataset.gmKey;
            attrs[key] = parseInt(input.value, 10);
        });

        if (Object.keys(attrs).length === 0) return;

        console.log('[GM] 准备提交属性:', attrs);

        try {
            const resp = await fetch('/api/character/update_attrs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ attrs, character_id: App.character ? App.character.id : null })
            });
            const data = await resp.json();

            if (data.success) {
                Toast.show('GM属性已更新', 'success');
                this.close();

                // 从后端重新拉取最新数据，确保前端面板与后端一致
                try {
                    const freshResp = await fetch('/api/character');
                    const freshData = await freshResp.json();
                    if (freshData.success && freshData.data) {
                        App.refreshAll(freshData.data);
                    }
                } catch (e) {
                    console.warn('[GM] 刷新前端数据失败:', e);
                    // 降级：使用 apply 响应中的数据
                    if (data.character) {
                        App.refreshAll(data.character);
                    }
                }
            } else {
                Toast.show('更新失败: ' + (data.error || '未知错误'), 'error');
            }
        } catch (err) {
            Toast.show('网络错误，请检查连接', 'error');
            console.error('[GM] 错误:', err);
        }
    }
};
