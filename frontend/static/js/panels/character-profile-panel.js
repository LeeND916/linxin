/** 角色属性编辑面板 — 基础信息 / 社交关系 / 世界观 / 任务 / 事件模板 */
const CharacterProfilePanel = {
    _characterName: '',

    init() {
        document.getElementById('btn-character-profile')?.addEventListener('click', () => this.open());
        document.getElementById('btn-close-character-profile')?.addEventListener('click', () => this.close());
        document.getElementById('character-profile-overlay')?.addEventListener('click', (e) => {
            if (e.target.id === 'character-profile-overlay') this.close();
        });
        // Tab 切换
        document.querySelectorAll('.profile-tab').forEach(tab => {
            tab.addEventListener('click', () => this._switchTab(tab.dataset.tab));
        });
        // 世界观生成按钮
        document.getElementById('btn-generate-worldview')?.addEventListener('click', () => this._generateWorldView());
        document.getElementById('btn-save-world-prompt')?.addEventListener('click', () => this._saveWorldPrompt());
    },

    open() {
        // 从全局 App 或 API 获取角色名
        this._characterName = App?.characterName || '';
        if (!this._characterName) {
            // 如果 App 未就绪，直接从 API 获取
            fetch('/api/character').then(r => r.json()).then(d => {
                if (d.success) {
                    this._characterName = d.data.name;
                    this._doOpen();
                }
            }).catch(() => {});
            return;
        }
        this._doOpen();
    },

    _doOpen() {
        document.getElementById('character-profile-overlay').style.display = 'flex';
        this._switchTab('basic');
        this._loadAll();
    },

    close() {
        document.getElementById('character-profile-overlay').style.display = 'none';
    },

    _switchTab(tab) {
        document.querySelectorAll('.profile-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
        document.querySelectorAll('.profile-tab-content').forEach(c => c.style.display = c.dataset.tab === tab ? '' : 'none');
    },

    async _loadAll() {
        if (!this._characterName) return;
        await Promise.all([
            this._loadBasicInfo(),
            this._loadWorldView(),
            this._loadRelations(),
            this._loadCases(),
            this._loadEventTemplates(),
            this._loadChatSettings(),
        ]);
    },

    _escapeHtml(s) {
        return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },

    async _loadBasicInfo() {
        try {
            const r = await fetch('/api/character');
            const data = await r.json();
            if (!data.success) return;
            const c = data.data;
            const html = `
                <div class="profile-field"><label>姓名</label><span>${c.name || '-'}</span></div>
                <div class="profile-field"><label>身份</label><span>${c.identity_label || '-'}</span></div>
                <div class="profile-field"><label>专业</label><span>${c.major || '-'}</span></div>
                <div class="profile-field"><label>性格</label><span>${c.personality_type || '-'}</span></div>
                <div class="profile-field"><label>主梦想</label><span>${c.dream_primary || '-'}</span></div>
                <div class="profile-field"><label>次梦想</label><span>${c.dream_secondary || '-'}</span></div>
                <div class="profile-field"><label>人生目标</label><span>${c.goals ? Object.entries(c.goals).map(([k,v])=>k+':'+v+'%').join(', ') : '-'}</span></div>
                <div class="profile-field"><label>对玩家称呼</label><span>${c.player_nickname || '-'}</span></div>
            `;
            document.getElementById('profile-basic-info').innerHTML = html
                + this._renderAppearanceEditor(c.appearance || '')
                + this._renderLoraEditor(c.zimage_lora || '');
            this._bindAppearanceEvents();
            this._bindLoraEvents();
        } catch(e) {
            document.getElementById('profile-basic-info').innerHTML = '<div class="profile-error">加载失败</div>';
        }
    },

    _renderAppearanceEditor(appearance) {
        return `
            <div class="profile-appearance-editor">
                <div class="profile-appearance-header">
                    <span class="material-symbols-outlined">face_retouching_natural</span>
                    <strong>人物外貌</strong>
                    <small>可直接编辑，保存后写入数据库并参与提示词渲染</small>
                </div>
                <textarea id="profile-appearance-text" class="profile-appearance-textarea"
                    placeholder="外貌描述（脸型与轮廓 / 眉眼 / 鼻子 / 嘴唇 / 发型 / 肤色与妆容…）">${this._escapeHtml(appearance)}</textarea>
                <div class="profile-appearance-actions">
                    <button class="btn btn-primary profile-save-btn" id="btn-save-appearance">
                        <span class="material-symbols-outlined">save</span> 保存外貌
                    </button>
                    <span class="profile-save-tip" id="appearance-save-tip"></span>
                </div>
            </div>`;
    },

    _bindAppearanceEvents() {
        document.getElementById('btn-save-appearance')?.addEventListener('click', () => this._saveAppearance());
    },

    _renderLoraEditor(zimageLora) {
        return `
            <div class="profile-lora-editor">
                <div class="profile-lora-header">
                    <span class="material-symbols-outlined">auto_awesome</span>
                    <strong>LoRA 配置</strong>
                    <small>z-image 生图专用，留空则用默认 LoRA</small>
                </div>
                <div class="profile-lora-hint">
                    填写 ComfyUI 中 LoRA 的<strong>片段/全名</strong>（如 <code>yanyin</code> 命中
                    <code>yanyin_zimage_turbo_lora_v1_000002500.safetensors</code>）。生图时会在提示词最前加
                    <code>"字段值,"</code> 作为触发词，并注入对应 LoRA 节点。
                </div>
                <div class="profile-lora-input-row">
                    <span class="profile-lora-prefix">LoRA</span>
                    <input type="text" id="profile-lora-input" class="profile-lora-input"
                        placeholder="例如 yanyin（留空 = 默认像素风 LoRA）"
                        value="${this._escapeHtml(zimageLora)}" autocomplete="off" spellcheck="false">
                </div>
                <div class="profile-appearance-actions">
                    <button class="btn btn-primary profile-save-btn" id="btn-save-lora">
                        <span class="material-symbols-outlined">save</span> 保存 LoRA
                    </button>
                    <span class="profile-save-tip" id="lora-save-tip"></span>
                </div>
            </div>`;
    },

    _bindLoraEvents() {
        document.getElementById('btn-save-lora')?.addEventListener('click', () => this._saveLora());
    },

    async _saveLora() {
        const btn = document.getElementById('btn-save-lora');
        const tip = document.getElementById('lora-save-tip');
        const input = document.getElementById('profile-lora-input');
        const val = (input?.value || '').trim();
        if (btn) { btn.disabled = true; }
        try {
            const r = await fetch('/api/character/zimage-lora', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ zimage_lora: val })
            });
            const data = await r.json();
            if (!data.success) throw new Error(data.error || '保存失败');
            if (input) input.value = data.zimage_lora;
            if (tip) { tip.textContent = data.zimage_lora ? `✓ 已保存：${data.zimage_lora}` : '✓ 已保存（使用默认 LoRA）'; tip.className = 'profile-save-tip ok'; }
        } catch(e) {
            if (tip) { tip.textContent = '保存失败: ' + e.message; tip.className = 'profile-save-tip err'; }
        } finally {
            if (btn) { btn.disabled = false; }
        }
    },

    async _saveAppearance() {
        const btn = document.getElementById('btn-save-appearance');
        const tip = document.getElementById('appearance-save-tip');
        const text = document.getElementById('profile-appearance-text')?.value || '';
        if (btn) { btn.disabled = true; }
        try {
            const r = await fetch('/api/character/appearance', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ appearance: text })
            });
            const data = await r.json();
            if (!data.success) throw new Error(data.error || '保存失败');
            if (tip) { tip.textContent = '✓ 已保存'; tip.className = 'profile-save-tip ok'; }
        } catch(e) {
            if (tip) { tip.textContent = '保存失败: ' + e.message; tip.className = 'profile-save-tip err'; }
        } finally {
            if (btn) { btn.disabled = false; }
        }
    },

    async _loadChatSettings() {
        const box = document.getElementById('profile-chat-settings');
        if (!box) return;
        box.innerHTML = '<div class="profile-loading">加载中...</div>';
        try {
            const r = await fetch('/api/settings/chat');
            const data = await r.json();
            if (!data.success) throw new Error('load failed');
            const on = !!data.data.reply_side_auto_photo;
            box.innerHTML = `
                <div class="chat-setting-card">
                    <div class="chat-setting-row">
                        <div class="chat-setting-info">
                            <div class="chat-setting-title"><span class="material-symbols-outlined">photo_camera</span> 女主自主拍照</div>
                            <div class="chat-setting-desc">开启后，女主回复时可能自动拍照 / 翻旧照发给你（仍受状态、冷却与每日上限约束）。<br>关闭后，只有玩家侧（发消息提到拍照或点拍照按钮）才会触发拍照。</div>
                        </div>
                        <label class="chat-toggle" title="女主自主拍照开关">
                            <input type="checkbox" id="toggle-reply-photo" ${on ? 'checked' : ''}>
                            <span class="chat-toggle-slider"></span>
                        </label>
                    </div>
                    <div class="chat-save-tip" id="chat-setting-tip"></div>
                </div>`;
            document.getElementById('toggle-reply-photo')?.addEventListener('change', (e) => this._saveChatSettings(e.target.checked));
        } catch(e) {
            box.innerHTML = '<div class="profile-error">加载失败</div>';
        }
    },

    async _saveChatSettings(enabled) {
        const tip = document.getElementById('chat-setting-tip');
        try {
            const r = await fetch('/api/settings/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reply_side_auto_photo: !!enabled })
            });
            const data = await r.json();
            if (!data.success) throw new Error(data.error || '保存失败');
            if (tip) {
                tip.textContent = enabled ? '✓ 已开启：女主可能会自主拍照' : '✓ 已关闭：女主不再自主拍照';
                tip.className = 'chat-save-tip ok';
            }
        } catch(e) {
            if (tip) { tip.textContent = '保存失败: ' + e.message; tip.className = 'chat-save-tip err'; }
            // 保存失败回滚开关显示
            const t = document.getElementById('toggle-reply-photo');
            if (t) t.checked = !enabled;
        }
    },

    async _loadWorldView() {
        const container = document.getElementById('profile-worldview');
        const contentArea = document.getElementById('profile-worldview-content');
        contentArea.innerHTML = '<div class="profile-loading">加载中...</div>';
        // 生成前预览：调用 preview-prompt 接口渲染当前真实 prompt，填到文本框供编辑
        try {
            const pr = await fetch(`/api/character/${this._characterName}/world-setting/preview-prompt`);
            const prData = await pr.json();
            const seedEl = document.getElementById('profile-world-seed');
            if (seedEl && prData.success && prData.data?.prompt) {
                seedEl.value = prData.data.prompt;
            }
        } catch(e) { /* preview 失败不阻塞面板渲染 */ }

        try {
            const r = await fetch(`/api/character/${this._characterName}/world-setting`);
            if (!r.ok) { contentArea.innerHTML = '<div class="profile-empty">尚未生成世界观，点击上方按钮生成</div>'; return; }
            const data = await r.json();
            if (!data.success) { contentArea.innerHTML = '<div class="profile-empty">尚未生成世界观</div>'; return; }
            const ws = data.data;
            const phases = ws.narrative_schedule?.phases || [];
            const goals = ws.life_goals || [];
            let html = '<div class="profile-ws-summary">';
            html += `<div><strong>核心矛盾：</strong>${ws.central_conflict || '无'}</div>`;
            html += `<div><strong>目标：</strong>${goals.map(g => g.goal).join('、') || '无'}</div>`;
            html += '<div><strong>日程阶段：</strong></div><ul>';
            phases.forEach(p => {
                html += `<li>${p.phase} (第${p.day_range[0]}-${p.day_range[1]}天) - ${(p.stage_events||[]).length}个事件</li>`;
            });
            html += '</ul></div>';
            html += '<button class="btn btn-sm" onclick="CharacterProfilePanel._previewWorldView()">查看完整 JSON</button>';
            contentArea.innerHTML = html;
            window._currentWorldView = ws;
        } catch(e) {
            contentArea.innerHTML = '<div class="profile-empty">加载失败</div>';
        }
    },

    async _loadCases() {
        const container = document.getElementById('profile-cases-list');
        // 不再预填 world_prompt 叙事；真实 prompt 在点击「生成任务」后由 _rendered_prompt 写入
        const genLabel = '生成任务';
        const btnGen = document.getElementById('btn-generate-case');
        if (btnGen) btnGen.textContent = genLabel;

        try {
            const apiUrl = `/api/character/${this._characterName}/missions`;
            const r = await fetch(apiUrl);
            const data = await r.json();
            const items = data.data || [];

            // 检测 has_pending_mission → 显示重试按钮
            if (data.data && data.data.has_pending_mission) {
                if (btnGen) {
                    btnGen.textContent = '重新生成任务';
                    btnGen.style.background = 'var(--warning)';
                }
            }

            // 保存到全局供编辑使用
            window._currentMissionItems = items;

            // 生成前预览：调用 preview-prompt 接口渲染当前真实 prompt，填到文本框供编辑
            try {
                const pr = await fetch(`/api/character/${this._characterName}/missions/preview-prompt`);
                const prData = await pr.json();
                const previewPrompt = prData.success ? (prData.data?.prompt || '') : '';
                const ta = document.getElementById('profile-world-prompt');
                if (ta) {
                    // 优先用实时渲染的 prompt；接口失败则回退到已保存任务的 rendered_prompt
                    if (previewPrompt) {
                        ta.value = previewPrompt;
                    } else if (items.length > 0) {
                        const latest = items[items.length - 1];
                        const rp = latest.rendered_prompt || latest._rendered_prompt || '';
                        if (rp) ta.value = rp;
                    }
                }
            } catch(e) {
                // preview 失败不阻塞面板渲染；回退到已保存 rendered_prompt
                if (items.length > 0) {
                    const latest = items[items.length - 1];
                    const rp = latest.rendered_prompt || latest._rendered_prompt || '';
                    const ta = document.getElementById('profile-world-prompt');
                    if (ta && rp) ta.value = rp;
                }
            }

            if (!items.length) {
                container.innerHTML = '<div class="profile-empty">尚无任务，点击上方「生成任务」按钮开始</div>';
                return;
            }
            let html = '';
            items.forEach((c, idx) => {
                const st = c.status === 'running' ? '🔄 进行中' : c.status === 'completed' ? '✅ 已完结' : c.status === 'archived' ? '📦 已归档' : '⏳ 等待中';
                const stages = c.stages || c.stages_list || [];
                const npcs = c.npc_roster || c.npc_list || [];
                const nameKey = 'mission_name';
                const idKey = 'mission_id';
                // 五幕进度：fired_events / total_events
                const totalEvents = stages.reduce((sum, s) => sum + (s.events?.length || 0), 0);
                const firedCount = (c.fired_events || []).length;
                const progressStr = totalEvents > 0 ? `${firedCount}/${totalEvents} 事件` : `阶段 ${c.current_stage_index + 1}/${stages.length}`;
                const currentStage = stages[c.current_stage_index];
                const stageName = currentStage?.name || '--';
                html += `<div class="profile-arc-card">
                    <div style="display:flex;justify-content:space-between;align-items:center">
                        <div><strong>${c[nameKey]}</strong> <small>${st}</small></div>
                        <div style="display:flex;gap:4px">
                            <button class="btn-sm" onclick="CharacterProfilePanel._editMission(${idx})" title="编辑任务内容">✏️ 编辑</button>
                            <button class="btn-sm" onclick="CharacterProfilePanel._toggleCaseDetail(${idx})">展开</button>
                            ${(c.status === 'running' || c.status === 'pending') ? `<button class="btn-sm" style="color:var(--error);border-color:var(--error)" onclick="CharacterProfilePanel._deleteMission(${idx})" title="删除任务（仅进行中，归档任务不可删）">🗑️ 删除</button>` : ''}
                        </div>
                    </div>
                    <div style="font-size:12px;color:var(--text-secondary);margin-top:6px">
                        <label>起始天：<input type="number" class="case-day-input" value="${c.start_day}" data-${idKey}="${c.id}" data-is-mission="1" data-field="start_day" onchange="CharacterProfilePanel._updateCaseTime(this)"></label>
                        <label style="margin-left:10px">结束天：<input type="number" class="case-day-input" value="${c.end_day}" data-${idKey}="${c.id}" data-is-mission="1" data-field="end_day" onchange="CharacterProfilePanel._updateCaseTime(this)"></label>
                        <span style="margin-left:10px">${c.phase_name ? '幕：' + c.phase_name + ' | ' : ''}进度：${progressStr}</span>
                        ${c.tone ? `<span style="margin-left:10px">基调：${c.tone}</span>` : ''}
                    </div>
                    <div style="font-size:12px;margin-top:4px;display:${idx === window._expandedCaseIdx ? '' : 'none'}" id="case-detail-${idx}">
                        ${c.core_conflict ? `<div style="margin-bottom:6px"><strong>🎬 核心冲突：</strong><span style="font-style:italic">${c.core_conflict}</span></div>` : ''}
                        <div><strong>📌 五幕阶段 (${stages.length})：</strong></div>
                        ${stages.map((s, si) => {
                            const evts = s.events || [];
                            const firedInStage = evts.filter((_, ei) => (c.fired_events || []).includes(`${si}_${ei}`)).length;
                            return `<div style="padding:2px 0;font-size:11px;color:var(--text-secondary)">
                                ${si === c.current_stage_index ? '👉 ' : ''}${s.name}（第${c.start_day + (s.day_offset||0)}天）${evts.length ? ` · ${firedInStage}/${evts.length} 事件` : ''}${evts.some(e => e.is_choice) ? ' 🔀含抉择' : ''}
                            </div>`;
                        }).join('')}
                        <div style="margin-top:8px"><strong>👥 涉及人物 (${npcs.length})：</strong></div>
                        ${npcs.map(n => `<span style="display:inline-block;padding:1px 6px;margin:2px;background:rgba(124,92,252,0.08);border-radius:4px;font-size:11px">${n.name}（${n.role}·${n.relation_type || ''}）</span>`).join('')}
                        ${c.goal_key ? `<div style="margin-top:8px"><strong>🎯 任务目标：</strong>${c.goal_label || c.goal_key} <span style="color:var(--accent)">[${c.goal_target || 100}%]</span></div>` : ''}
                    </div>
                </div>`;
            });
            container.innerHTML = html;
        } catch(e) {
            container.innerHTML = '<div class="profile-error">加载失败</div>';
        }
    },

    _toggleCaseDetail(idx) {
        const prev = window._expandedCaseIdx;
        if (prev !== undefined && prev !== null) {
            const prevEl = document.getElementById(`case-detail-${prev}`);
            if (prevEl) prevEl.style.display = 'none';
        }
        const el = document.getElementById(`case-detail-${idx}`);
        if (el) {
            el.style.display = el.style.display === 'none' ? '' : 'none';
            window._expandedCaseIdx = el.style.display === '' ? idx : null;
        }
    },

    async _deleteMission(idx) {
        const items = window._currentMissionItems || [];
        const c = items[idx];
        if (!c || !c.id) return;
        if (!confirm(`确定删除任务「${c.mission_name}」？\n将同时删除该任务生成的 NPC、成就及其触发绑定（初始自带数据保留，删除前已自动备份数据库）。此操作不可恢复。`)) {
            return;
        }
        try {
            const r = await fetch(`/api/character/${this._characterName}/missions/${c.id}`, { method: 'DELETE' });
            const data = await r.json();
            if (data.success) {
                const d = data.deleted || {};
                this._showToast(`🗑️ 任务已删除（清理 NPC ${d.friends||0} / 成就 ${d.achievements||0} / 绑定 ${d.bindings||0}）`);
                this._loadCases();
                this._loadRelations();
                this._refreshGlobalFriendsAndArcs();
            } else {
                this._showToast('❌ 删除失败：' + (data.error || ''), true);
            }
        } catch(e) {
            this._showToast('❌ 请求失败：' + e.message, true);
        }
    },

    async _updateCaseTime(input) {
        const isMission = input.dataset.isMission === '1';
        const idKey = isMission ? 'missionId' : 'caseId';
        const itemId = input.dataset[idKey];
        const field = input.dataset.field;
        const value = parseInt(input.value);
        if (isNaN(value) || value < 0) return;
        try {
            const body = {};
            body[field] = value;
            const url = isMission
                ? `/api/character/${this._characterName}/missions/${itemId}/settings`
                : `/api/character/${this._characterName}/cases/${itemId}/settings`;
            await fetch(url, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body),
            });
        } catch(e) {
            console.error('更新时间节点失败', e);
        }
    },

    _previewData: null,  // 暂存生成的预览数据

    async _generateCase() {
        const btn = document.getElementById('btn-generate-case');
        btn.disabled = true;
        btn.textContent = '生成中...';
        try {
            const apiUrl = `/api/character/${this._characterName}/missions/generate`;
            // 取文本框当前内容（用户可能已编辑），作为 custom_prompt 传给 LLM
            const ta = document.getElementById('profile-world-prompt');
            const customPrompt = ta ? ta.value.trim() : '';
            const r = await fetch(apiUrl, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(customPrompt ? { custom_prompt: customPrompt } : {}),
            });
            const data = await r.json();
            if (data.success) {
                // C 方案：防御性兜底 — 后端解析异常可能返回非完整 main 数据
                // 必须校验 mission_name / stages / npc_roster 三个核心字段非空
                const main = data.data?.main;
                if (!main || !main.mission_name || !Array.isArray(main.stages) || main.stages.length === 0
                    || !Array.isArray(main.npc_roster) || main.npc_roster.length === 0) {
                    alert('生成数据不完整（缺少任务名/阶段/NPC），请重试');
                } else {
                    // 把后端返回的 _rendered_prompt 写入 textarea，让用户看到实际投喂给 LLM 的 prompt
                    if (data.data._rendered_prompt) {
                        const ta = document.getElementById('profile-world-prompt');
                        if (ta) ta.value = data.data._rendered_prompt;
                    }
                    this._previewData = data.data;
                    this._showMissionReview(data.data);
                }
            } else {
                alert('生成失败：' + (data.error || ''));
            }
        } catch(e) {
            alert('请求失败：' + e.message);
        }
        btn.disabled = false;
        btn.textContent = '生成任务';
    },

    _showMissionReview(preview) {
        const main = preview.main || {};
        const sub = preview.subsystems || {};
        const stages = main.stages || [];
        const npcs = main.npc_roster || [];
        const hasExisting = preview.has_existing;
        const existingName = preview.existing_name || '';

        // 生成唯一 ID 前缀，避免多实例冲突
        const uid = 'mr_' + Date.now();

        const html = `
            <div class="mission-review-overlay" id="mission-review-overlay" onclick="if(event.target===this)this.remove()">
                <div class="mission-review-modal" style="max-width:680px;max-height:85vh;overflow-y:auto">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                        <h3 style="margin:0">📋 任务预览</h3>
                        <button class="btn" id="${uid}_editToggle" onclick="CharacterProfilePanel._togglePreviewEdit('${uid}')">✏️ 编辑模式</button>
                    </div>
                    <div class="mission-review-body" id="${uid}_body">
                        <!-- 基本信息 -->
                        <div class="preview-section">
                            <input id="${uid}_name" value="${(main.mission_name || '').replace(/"/g,'&quot;')}" placeholder="任务名称"
                                style="font-size:16px;font-weight:bold;width:100%;border:1px solid transparent;background:transparent;padding:4px 0;color:var(--text-primary)"
                                onfocus="this.style.borderColor='var(--primary)';this.style.background='var(--bg-card)'"
                                onblur="this.style.borderColor='transparent';this.style.background='transparent';CharacterProfilePanel._syncPreviewField('mission_name',this.value)" />
                            <textarea id="${uid}_desc" rows=2 placeholder="任务描述"
                                style="width:100%;border:1px solid transparent;background:transparent;padding:4px 0;margin-top:4px;resize:vertical;font-size:14px;color:var(--text-secondary)"
                                onfocus="this.style.borderColor='var(--primary)';this.style.background='var(--bg-card)'"
                                onblur="this.style.borderColor='transparent';this.style.background='transparent';CharacterProfilePanel._syncPreviewField('mission_description',this.value)">${(main.mission_description || '').replace(/</g,'&lt;')}</textarea>
                            <div style="display:flex;gap:8px;margin-top:6px">
                                <input id="${uid}_conflict" value="${(main.core_conflict || '').replace(/"/g,'&quot;')}" placeholder="🎬 核心冲突"
                                    style="flex:1;border:1px solid transparent;background:transparent;padding:4px 8px;font-size:14px;font-style:italic;color:var(--text-secondary)"
                                    onfocus="this.style.borderColor='var(--primary)';this.style.background='var(--bg-card)'"
                                    onblur="this.style.borderColor='transparent';this.style.background='transparent';CharacterProfilePanel._syncPreviewField('core_conflict',this.value)" />
                                <input id="${uid}_tone" value="${(main.tone || '').replace(/"/g,'&quot;')}" placeholder="基调"
                                    style="width:80px;border:1px solid transparent;background:transparent;padding:4px 8px;font-size:14px;color:var(--text-primary)"
                                    onfocus="this.style.borderColor='var(--primary)';this.style.background='var(--bg-card)'"
                                    onblur="this.style.borderColor='transparent';this.style.background='transparent';CharacterProfilePanel._syncPreviewField('tone',this.value)" />
                                <span style="font-size:14px;color:var(--text-secondary);padding:4px 0">第${main.start_day||'?'}-${main.end_day||'?'}天</span>
                            </div>
                        </div>
                        <!-- 阶段（可编辑） -->
                        <div class="preview-section">
                            <strong>📌 阶段 (${stages.length})</strong>
                            <div id="${uid}_stages">
                                ${stages.map((s, i) => `
                                    <div style="display:flex;align-items:center;gap:4px;padding:2px 0;font-size:14px">
                                        <span style="color:var(--text-secondary);min-width:18px">${i+1}.</span>
                                        <input data-idx="${i}" data-field="name" value="${(s.name||'').replace(/"/g,'&quot;')}"
                                            style="flex:1;border:1px solid transparent;background:transparent;padding:2px 4px;color:var(--text-primary)"
                                            onfocus="this.style.borderColor='var(--primary)'"
                                            onblur="this.style.borderColor='transparent';CharacterProfilePanel._syncPreviewStage(${i},'name',this.value)" />
                                        ${s.choices?.length ? '<span title="有分支选择">🔀</span>' : ''}
                                        <span style="color:var(--text-secondary);font-size:14px">第${(main.start_day||0)+(s.day_offset||0)}天</span>
                                    </div>`).join('')}
                            </div>
                        </div>
                        <!-- NPC（可编辑） -->
                        <div class="preview-section">
                            <strong>👥 NPC (${npcs.length})</strong>
                            <div id="${uid}_npcs" style="display:flex;flex-wrap:wrap;gap:4px">
                                ${npcs.map((n, i) => `
                                    <span style="display:inline-flex;align-items:center;gap:3px;padding:2px 6px;background:rgba(124,92,252,0.1);border-radius:4px;font-size:14px">
                                        <input data-nidx="${i}" data-field="name" value="${(n.name||'').replace(/"/g,'&quot;')}"
                                            style="border:1px solid transparent;background:transparent;width:auto;min-width:40px;padding:1px 3px;font-size:14px;color:var(--text-primary)"
                                            onfocus="this.style.borderColor='var(--primary)';this.style.background='#fff'"
                                            onblur="this.style.borderColor='transparent';this.style.background='transparent';CharacterProfilePanel._syncPreviewNpc(${i},'name',this.value)" />
                                        (<input data-nidx="${i}" data-field="role" value="${(n.role||'').replace(/"/g,'&quot;')}"
                                            style="border:1px solid transparent;background:transparent;width:36px;padding:1px 3px;font-size:14px;color:var(--text-primary)"
                                            onfocus="this.style.borderColor='var(--primary)'"
                                            onblur="this.style.borderColor='transparent';CharacterProfilePanel._syncPreviewNpc(${i},'role',this.value)" />·
                                        <select data-nidx="${i}" data-field="relation_type"
                                            style="border:1px solid transparent;background:transparent;padding:1px 3px;font-size:14px"
                                            onchange="CharacterProfilePanel._syncPreviewNpc(${i},'relation_type',this.value)">
                                            ${['朋友','导师','对手','同事','恋人','家人','客户','前辈','其他'].map(t =>
                                                `<option value="${t}" ${(n.relation_type||'')===t?'selected':''}>${t}</option>`
                                            ).join('')}
                                        </select>)
                                        <button onclick="CharacterProfilePanel._removePreviewNpc(${i})" title="移除" style="background:none;border:none;color:var(--error);cursor:pointer;padding:0 2px;font-size:14px">✕</button>
                                    </span>`).join('')}
                                <button onclick="CharacterProfilePanel._addPreviewNpc()" style="font-size:14px;border:1px dashed var(--border);background:none;cursor:pointer;padding:2px 8px;border-radius:4px;color:var(--text-secondary)">+ 添加 NPC</button>
                            </div>
                        </div>
                        <!-- 成就（五幕结构不再生成专属成就，保留兼容） -->
                        ${(sub.achievements || []).length ? `<div class="preview-section"><strong>🏆 成就 (${(sub.achievements || []).length})</strong>
                            <div id="${uid}_achs">
                                ${(sub.achievements || []).map((a, i) => `
                                    <div style="display:flex;align-items:center;gap:4px;padding:2px 0;font-size:14px">
                                        <span>🏆</span>
                                        <input data-aidx="${i}" value="${(a.name || a.achievement_id || '').replace(/"/g,'&quot;')}"
                                            style="flex:1;border:1px solid transparent;background:transparent;padding:2px 4px;color:var(--text-primary)"
                                            onfocus="this.style.borderColor='var(--primary)')"
                                            onblur="this.style.borderColor='transparent';CharacterProfilePanel._syncPreviewAchievement(${i},this.value)" />
                                    </div>
                                    ${(a.conditions && a.conditions.length) ? `<div style="font-size:10px;color:var(--text-secondary);width:100%;padding-left:18px;margin-top:-2px">触发条件: ${CharacterProfilePanel._fmtConditions(a.conditions)}</div>` : ''}`).join('')}
                            </div>
                        </div>` : ''}
                        ${(sub.achievements || []).length ? '' : ''}
                        <!-- 条件事件（五幕结构不再注入 ConditionMatcher，保留兼容） -->
                        ${(sub.event_templates || []).length ? `<div class="preview-section"><strong>⚡ 条件事件 (${(sub.event_templates || []).length})</strong>
                            <div id="${uid}_evts">
                                ${(sub.event_templates || []).slice(0, 8).map((e, i) => `
                                    <div style="display:flex;align-items:center;gap:4px;padding:2px 0;font-size:14px">
                                        <span>⚡</span>
                                        <input data-eidx="${i}" value="${(e.title || e.trigger_id || e.event_type || '').replace(/"/g,'&quot;')}"
                                            style="flex:1;border:1px solid transparent;background:transparent;padding:2px 4px;color:var(--text-primary)"
                                            onfocus="this.style.borderColor='var(--primary)')"
                                            onblur="this.style.borderColor='transparent';CharacterProfilePanel._syncPreviewEvent(${i},this.value)" />
                                        <span style="font-size:14px;color:var(--text-secondary)">${e.trigger_type||''}</span>
                                    </div>`).join('')}
                                ${(sub.event_templates || []).length > 8 ? `<div style="font-size:14px;color:var(--text-secondary)">...等 ${(sub.event_templates || []).length} 个事件（仅前 8 项可编辑标题）</div>` : ''}
                            </div>
                        </div>` : ''}
                    </div>
                    ${hasExisting ? `<div style="padding:8px 12px;background:rgba(255,180,0,0.1);border-radius:4px;margin-bottom:12px;font-size:14px">⚠️ 已有任务「${existingName}」正在进行中</div>` : ''}
                    <div class="mission-review-actions" style="display:flex;gap:8px;flex-wrap:wrap">
                        <button class="btn" onclick="CharacterProfilePanel._regenerateMission()">🔄 重新生成</button>
                        <button class="btn" onclick="CharacterProfilePanel._confirmMission('create_new')">➕ 创建新任务</button>
                        <button class="btn btn-primary" onclick="CharacterProfilePanel._confirmMission('overwrite')">${hasExisting ? '🔄 覆盖当前任务' : '✅ 确认创建'}</button>
                    </div>
                </div>
            </div>`;
        document.body.insertAdjacentHTML('beforeend', html);
        // 存储预览数据引用和 UID
        this._previewUid = uid;
        this._previewData = preview;
    },

    /* ── 预览编辑同步方法 ── */

    _syncPreviewField(field, value) {
        if (this._previewData && this._previewData.main) {
            this._previewData.main[field] = value;
        }
    },

    _syncPreviewStage(idx, field, value) {
        const main = this._previewData?.main;
        if (main && main.stages && main.stages[idx]) {
            main.stages[idx][field] = value;
        }
    },

    _syncPreviewNpc(idx, field, value) {
        const main = this._previewData?.main;
        if (main && main.npc_roster && main.npc_roster[idx]) {
            main.npc_roster[idx][field] = value;
        }
    },

    _syncPreviewAchievement(idx, value) {
        const sub = this._previewData?.subsystems;
        if (sub && sub.achievements && sub.achievements[idx]) {
            if (typeof sub.achievements[idx] === 'string') {
                sub.achievements[idx] = value;
            } else {
                sub.achievements[idx].name = value;
            }
        }
    },

    /** 将成就触发条件数组格式化为可读文本 */
    _fmtConditions(conds) {
        if (!conds || !conds.length) return '';
        return conds.map(c => {
            if (c.attr) return `${c.attr} ${c.op || ''} ${c.value ?? ''}`.trim();
            if (c.at_location) return `位于 ${c.at_location}`;
            if (c.time_between) return `时间 ${c.time_between}`;
            if (c.not_cooldown) return `冷却 ${c.not_cooldown}`;
            return JSON.stringify(c);
        }).join(' & ');
    },

    /** 渲染条件事件模板的详细信息（触发条件 + 效果） */
    _renderEventTemplateDetails(events) {
        const max = Math.min(events.length, 8);
        let html = '';
        for (let i = 0; i < max; i++) {
            const e = events[i];
            const title = e.title || e.trigger_id || e.event_type || '(无标题)';
            const triggerType = e.trigger_type || '';
            const conditions = e.conditions || e.requirements || [];
            const effects = e.effects?.state_changes || [];
            const conditionsText = this._fmtConditions(conditions);
            const effectsText = effects.length
                ? effects.map(ef => `${ef.attribute || ''}${ef.delta > 0 ? '+' : ''}${ef.delta || ''}`).join(', ')
                : '';
            const location = e.location || '';
            const timeRange = e.game_time_range || '';
            html += `<div style="padding:3px 0;font-size:14px;border-bottom:1px solid var(--border-color);margin-bottom:3px">
                <div><span style="color:var(--accent)">⚡</span> <strong>${this._escHtml(title)}</strong> ${triggerType ? `<code style="font-size:10px;color:var(--text-secondary)">${triggerType}</code>` : ''}</div>
                ${location || timeRange ? `<div style="color:var(--text-secondary);font-size:10px;margin-top:1px">📍 ${[location, timeRange].filter(Boolean).join(' · ')}</div>` : ''}
                ${conditionsText ? `<div style="color:var(--text-secondary);font-size:10px;margin-top:1px">🔹 满足条件：${conditionsText}</div>` : '<div style="color:var(--text-secondary);font-size:10px;margin-top:1px">🔹 触发条件：无（随时触发）</div>'}
                ${effectsText ? `<div style="color:var(--text-secondary);font-size:10px;margin-top:1px">📊 效果：${effectsText}</div>` : ''}
            </div>`;
        }
        if (events.length > max) {
            html += `<div style="font-size:10px;color:var(--text-secondary)">...等 ${events.length} 个条件事件（仅展示前 ${max} 项）</div>`;
        }
        return html;
    },

    _escHtml(str) {
        return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },

    _syncPreviewEvent(idx, value) {
        const sub = this._previewData?.subsystems;
        if (sub && sub.event_templates && sub.event_templates[idx]) {
            sub.event_templates[idx].title = value;
        }
    },

    _removePreviewNpc(idx) {
        const main = this._previewData?.main;
        if (main && main.npc_roster) {
            main.npc_roster.splice(idx, 1);
            // 刷新 NPC 区域显示
            this._refreshPreviewNpcs();
        }
    },

    _addPreviewNpc() {
        const main = this._previewData?.main;
        if (main) {
            if (!main.npc_roster) main.npc_roster = [];
            main.npc_roster.push({ name: '新角色', role: '', relation_type: '其他' });
            this._refreshPreviewNpcs();
        }
    },

    /** 刷新预览弹窗中 NPC 区域的 DOM */
    _refreshPreviewNpcs() {
        const uid = this._previewUid;
        if (!uid) return;
        const container = document.getElementById(uid + '_npcs');
        const npcs = (this._previewData?.main?.npc_roster) || [];
        if (!container) return;
        container.innerHTML = npcs.map((n, i) => `
            <span style="display:inline-flex;align-items:center;gap:3px;padding:2px 6px;background:rgba(124,92,252,0.1);border-radius:4px;font-size:14px">
                <input data-nidx="${i}" data-field="name" value="${(n.name||'').replace(/"/g,'&quot;')}"
                    style="border:1px solid transparent;background:transparent;width:auto;min-width:40px;padding:1px 3px;font-size:14px;color:var(--text-primary)"
                    onfocus="this.style.borderColor='var(--primary)';this.style.background='#fff'"
                    onblur="this.style.borderColor='transparent';this.style.background='transparent';CharacterProfilePanel._syncPreviewNpc(${i},'name',this.value)" />
                (<input data-nidx="${i}" data-field="role" value="${(n.role||'').replace(/"/g,'&quot;')}"
                    style="border:1px solid transparent;background:transparent;width:36px;padding:1px 3px;font-size:14px;color:var(--text-primary)"
                    onfocus="this.style.borderColor='var(--primary)')"
                    onblur="this.style.borderColor='transparent';CharacterProfilePanel._syncPreviewNpc(${i},'role',this.value)" />·
                <select data-nidx="${i}" data-field="relation_type"
                    style="border:1px solid transparent;background:transparent;padding:1px 3px;font-size:14px"
                    onchange="CharacterProfilePanel._syncPreviewNpc(${i},'relation_type',this.value)">
                    ${['朋友','导师','对手','同事','恋人','家人','客户','前辈','其他'].map(t =>
                        `<option value="${t}" ${(n.relation_type||'')===t?'selected':''}>${t}</option>`
                    ).join('')}
                </select>)
                <button onclick="CharacterProfilePanel._removePreviewNpc(${i})" title="移除" style="background:none;border:none;color:var(--error);cursor:pointer;padding:0 2px;font-size:14px">✕</button>
            </span>`
        ) + `
        <button onclick="CharacterProfilePanel._addPreviewNpc()" style="font-size:14px;border:1px dashed var(--border);background:none;cursor:pointer;padding:2px 8px;border-radius:4px;color:var(--text-secondary)">+ 添加 NPC</button>`;
    },

    _togglePreviewEdit(uid) {
        const body = document.getElementById(uid + '_body');
        const btn = document.getElementById(uid + '_editToggle');
        if (!body || !btn) return;
        const isEditing = body.classList.toggle('editing');
        btn.textContent = isEditing ? '🔒 锁定预览' : '✏️ 编辑模式';
        btn.classList.toggle('btn-primary', isEditing);
        // 给所有 input 加显式边框提示
        body.querySelectorAll('input, textarea, select').forEach(el => {
            el.style.borderStyle = isEditing ? 'solid' : (el.tagName === 'SELECT' ? 'solid' : 'transparent');
            if (isEditing) {
                el.style.borderColor = 'var(--border)';
                el.style.backgroundColor = el.tagName === 'TEXTAREA' ? 'var(--bg-card)' : 'rgba(255,255,255,0.05)';
            }
        });
    },

    /** 重新生成（关闭当前预览，重新调用 generate） */
    async _regenerateMission() {
        const overlay = document.getElementById('mission-review-overlay');
        if (overlay) overlay.remove();
        this._previewData = null;
        this._generateCase(); // 复用生成入口
    },

    async _confirmMission(action) {
        const overlay = document.getElementById('mission-review-overlay');
        if (overlay) overlay.remove();
        if (!this._previewData) return;

        const btn = document.getElementById('btn-generate-case');
        btn.disabled = true;
        btn.textContent = '写入中...';
        try {
            const r = await fetch(`/api/character/${this._characterName}/missions/confirm`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ preview_data: this._previewData, action }),
            });
            const data = await r.json();
            if (data.success) {
                const msg = action === 'overwrite' ? '✅ 任务已覆盖' : '✅ 任务已创建，NPC 已加入社交关系';
                this._showToast(msg);
                // 实时刷新：任务面板 + 社交关系
                this._loadCases();
                this._loadRelations();
                this._refreshGlobalFriendsAndArcs();
            } else {
                this._showToast('❌ 写入失败：' + (data.error || ''), true);
            }
        } catch(e) {
            this._showToast('❌ 请求失败：' + e.message, true);
        }
        btn.disabled = false;
        btn.textContent = '生成任务';
        this._previewData = null;
    },

    /** 全屏编辑提示词：把目标 textarea 内容放进大编辑器，点「应用修改」写回 */
    _openBigEditor(targetId, title) {
        const target = document.getElementById(targetId);
        if (!target) return;
        const old = document.getElementById('big-editor-overlay');
        if (old) old.remove();

        const overlay = document.createElement('div');
        overlay.id = 'big-editor-overlay';
        overlay.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;padding:24px';
        overlay.innerHTML = `
            <div style="width:min(1100px,94vw);height:88vh;display:flex;flex-direction:column;background:var(--bg-card,#fff);border:1px solid var(--border-color,#ddd);border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,0.35);overflow:hidden">
                <div style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--border-color,#ddd)">
                    <strong style="font-size:15px;color:var(--text-primary,#222)">⛶ ${title || '编辑提示词'}</strong>
                    <span id="big-editor-count" style="font-size:12px;color:var(--text-secondary,#888)"></span>
                </div>
                <textarea id="big-editor-text" spellcheck="false" style="flex:1;width:100%;border:none;outline:none;resize:none;padding:18px;background:var(--bg-input,#fafafa);color:var(--text-primary,#222);font-size:14px;line-height:1.8;font-family:Consolas,Monaco,monospace"></textarea>
                <div style="display:flex;gap:10px;justify-content:flex-end;padding:12px 18px;border-top:1px solid var(--border-color,#ddd)">
                    <button class="btn" id="big-editor-cancel">取消</button>
                    <button class="btn btn-primary" id="big-editor-apply">应用修改</button>
                </div>
            </div>`;
        document.body.appendChild(overlay);

        const ta = overlay.querySelector('#big-editor-text');
        const cnt = overlay.querySelector('#big-editor-count');
        ta.value = target.value;
        const updateCount = () => { cnt.textContent = `${ta.value.length} 字符`; };
        updateCount();
        ta.addEventListener('input', updateCount);
        ta.focus();

        const onKey = (e) => { if (e.key === 'Escape') close(); };
        const close = () => {
            document.removeEventListener('keydown', onKey);
            overlay.remove();
        };
        document.addEventListener('keydown', onKey);

        overlay.querySelector('#big-editor-cancel').onclick = close;
        overlay.querySelector('#big-editor-apply').onclick = () => {
            target.value = ta.value;
            close();
            this._showToast('✅ 已应用修改');
        };
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    },

    _previewWorldView() {
        const ws = window._currentWorldView;
        if (!ws) return;
        const modal = document.getElementById('profile-json-modal');
        document.getElementById('profile-json-content').textContent = JSON.stringify(ws, null, 2);
        modal.style.display = 'flex';
    },

    async _generateWorldView(continueFrom) {
        const btn = document.getElementById('btn-generate-worldview');
        const seedEl = document.getElementById('profile-world-seed');
        // 「继续生成」：叙事文本作为创作要求追加；主路径：文本框内容即编辑后的完整 prompt，直接投喂
        const isContinue = (continueFrom !== undefined);
        const seedPrompt = isContinue ? continueFrom : (seedEl ? seedEl.value.trim() : '');
        const reqBody = isContinue
            ? { seed_prompt: seedPrompt }
            : { custom_prompt: seedPrompt };
        btn.disabled = true;
        btn.textContent = '生成中...';
        try {
            const r = await fetch(`/api/character/${this._characterName}/world-setting/generate`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(reqBody),
            });
            const data = await r.json();
            if (data.success) {
                const ws = data.data || {};
                const worldPrompt = ws.world_prompt || seedPrompt;
                // 回填实际投喂 LLM 的完整 prompt（仅世界观框，不污染任务面板的提示词框）
                if (ws._rendered_prompt && seedEl) seedEl.value = ws._rendered_prompt;
                this._renderWorldViewResult(ws._rendered_prompt || worldPrompt, ws);
                window._currentWorldView = ws;
                // A1：生成后弹出结构化审核弹窗，可编辑每个字段
                this._showWorldViewReview(ws);
            } else {
                alert('生成失败：' + (data.error || '未知错误'));
            }
        } catch(e) {
            alert('请求失败：' + e.message);
        }
        btn.disabled = false;
        btn.textContent = '🌍 生成世界观';
    },

    /** 生成结果区：可编辑文本 + 修改/继续生成/保存 操作 */
    _renderWorldViewResult(worldPrompt) {
        const contentArea = document.getElementById('profile-worldview-content');
        if (!contentArea) return;
        contentArea.innerHTML = `
            <div style="margin-top:10px">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
                    <strong>🌐 生成的世界观提示词</strong>
                    <span style="font-size:14px;color:var(--text-secondary)">可修改后「继续生成」或「保存」</span>
                </div>
                <textarea id="profile-worldview-text" style="width:100%;min-height:140px;padding:10px;border:1px solid var(--border-color);border-radius:8px;background:var(--bg-input);color:var(--text-primary);font-size:14px">${(worldPrompt || '').replace(/</g,'&lt;')}</textarea>
                <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
                    <button class="btn" onclick="document.getElementById('profile-worldview-text').focus()">✏️ 修改</button>
                    <button class="btn" onclick="CharacterProfilePanel._continueWorldView()">🔄 继续生成</button>
                    <button class="btn btn-primary" onclick="CharacterProfilePanel._saveWorldPrompt()">💾 保存</button>
                </div>
                <div id="profile-worldview-save-tip" style="font-size:14px;color:var(--text-secondary);margin-top:6px"></div>
            </div>`;
    },

    /** 继续生成：用当前编辑框文本作为 seed 再调一次生成 */
    _continueWorldView() {
        const t = document.getElementById('profile-worldview-text');
        const text = t ? t.value.trim() : '';
        this._generateWorldView(text);
    },

    /** 保存：把编辑后的 world_prompt PUT 回后端（同步 cases 标签页共享框） */
    async _saveWorldPrompt() {
        const t = document.getElementById('profile-worldview-text');
        const text = t ? t.value.trim() : '';
        const tip = document.getElementById('profile-worldview-save-tip');
        try {
            const r = await fetch(`/api/character/${this._characterName}/world-prompt`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ world_prompt: text }),
            });
            const data = await r.json();
            if (data.success) {
                const seedEl = document.getElementById('profile-world-seed');
                if (seedEl) seedEl.value = text;
                const caseEl = document.getElementById('profile-world-prompt');
                if (caseEl) caseEl.value = text;
                if (tip) tip.textContent = '✅ 已保存';
            } else {
                if (tip) tip.textContent = '❌ 保存失败：' + (data.error || '');
            }
        } catch(e) {
            if (tip) tip.textContent = '❌ 请求失败：' + e.message;
        }
    },

    async _loadRelations() {
        const container = document.getElementById('profile-relations');
        try {
            const r = await fetch('/api/friends');
            const data = await r.json();
            // 按当前角色过滤（/api/friends 依赖全局活跃角色，增加兜底隔离）
            const all = (data.data || []).filter(f => !f.character_name || f.character_name === this._characterName);
            if (!data.success || !all.length) {
                container.innerHTML = '<div class="profile-empty">暂无社交关系数据</div>';
                return;
            }
            const typeLabel = {friend:'朋友',family:'家人',mentor:'导师',rival:'对手',enemy:'敌人',
                colleague:'同事',ex_boyfriend:'前男友',client:'客户',acquaintance:'普通关系',protege:'后辈',other:'其他'};
            // friend 表 gender 统一存英文（male/female），前端展示映射中文
            const genderLabel = g => g === 'male' ? '男' : g === 'female' ? '女' : (g || '—');
            let html = '<table class="profile-table"><tr><th>名称</th><th>性别</th><th>类型</th><th>亲密度</th><th>信任</th><th>好感</th><th>竞争度</th><th>敌意</th></tr>';
            all.forEach(f => {
                html += `<tr><td>${f.name}</td><td>${genderLabel(f.gender)}</td><td>${typeLabel[f.relation_type] || f.relation_type}</td>
                    <td>${f.closeness?.toFixed(0)}</td><td>${f.trust?.toFixed(0)}</td><td>${f.affection?.toFixed(0)}</td>
                    <td>${f.rivalry?.toFixed(0)}</td><td>${f.hostility?.toFixed(0)}</td></tr>`;
            });
            html += '</table>';
            container.innerHTML = html;
        } catch(e) {
            container.innerHTML = '<div class="profile-error">加载失败</div>';
        }
    },

    /**
     * 确认任务后，实时刷新左侧主社交列表（friends-list）。
     * 任务生成的 NPC 已写入 Friend 表，重新拉取即可实时呈现。
     */
    async _refreshGlobalFriendsAndArcs() {
        try {
            const r = await fetch('/api/friends');
            const d = await r.json();
            if (d.success && d.data && window.RelationshipPanel) {
                const filtered = d.data.filter(f => !f.character_name || f.character_name === this._characterName);
                RelationshipPanel.update(filtered);
            }
        } catch(e) {}
    },

    /* ── 已确认任务编辑 ── */

    /** 打开已有任务的编辑弹窗 */
    _editMission(idx) {
        const items = window._currentMissionItems || [];
        const c = items[idx];
        if (!c || !c.id) return;
        this._editingMissionId = c.id;
        this._editingMissionIdx = idx;

        // 构造与预览相同格式的数据
        const stages = c.stages || c.stages_list || [];
        const npcs = c.npc_roster || c.npc_list || [];
        const editData = {
            main: {
                mission_name: c.mission_name || '',
                mission_description: c.mission_description || '',
                core_conflict: c.core_conflict || '',
                tone: c.tone || '',
                start_day: c.start_day,
                end_day: c.end_day,
                stages: stages,
                npc_roster: npcs,
                goal: c.goal_key ? { key: c.goal_key, label: c.goal_label, target: c.goal_target } : null,
            },
            subsystems: {}
        };
        // 复用预览弹窗 UI，但标记为编辑模式
        this._previewData = editData;
        this._isEditMode = true;

        const uid = 'me_' + Date.now();
        this._previewUid = uid;

        const html = `
            <div class="mission-review-overlay" id="mission-edit-overlay" onclick="if(event.target===this)this.remove()">
                <div class="mission-review-modal" style="max-width:680px;max-height:85vh;overflow-y:auto">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                        <h3 style="margin:0">✏️ 编辑任务</h3>
                        <span style="font-size:14px;color:var(--text-secondary)">修改后点保存生效</span>
                    </div>
                    <div class="mission-review-body editing" id="${uid}_body">
                        <div class="preview-section">
                            <label style="font-size:14px;color:var(--text-secondary)">任务名称</label>
                            <input id="${uid}_name" value="${(c.mission_name||'').replace(/"/g,'&quot;')}" style="font-size:16px;font-weight:bold;width:100%;border:1px solid var(--border);padding:6px 8px;border-radius:4px;color:var(--text-primary)"
                                oninput="CharacterProfilePanel._syncPreviewField('mission_name',this.value)" />
                            <label style="font-size:14px;color:var(--text-secondary);margin-top:8px">描述</label>
                            <textarea id="${uid}_desc" rows=2 style="width:100%;border:1px solid var(--border);padding:6px 8px;border-radius:4px;font-size:14px;resize:vertical;color:var(--text-primary)"
                                oninput="CharacterProfilePanel._syncPreviewField('mission_description',this.value)">${(c.mission_description||'').replace(/</g,'&lt;')}</textarea>
                            <div style="display:flex;gap:8px;margin-top:8px">
				<input id="${uid}_conflict" value="${(c.core_conflict||'').replace(/"/g,'&quot;')}" placeholder="🎬 核心冲突"
					style="flex:1;border:1px solid var(--border);padding:4px 8px;font-size:14px;border-radius:4px;color:var(--text-primary)"
					oninput="CharacterProfilePanel._syncPreviewField('core_conflict',this.value)" />
				<input id="${uid}_tone" value="${(c.tone||'').replace(/"/g,'&quot;')}" placeholder="基调"
					style="width:80px;border:1px solid var(--border);padding:4px 8px;font-size:14px;border-radius:4px;color:var(--text-primary)"
                                    oninput="CharacterProfilePanel._syncPreviewField('tone',this.value)" />
                            </div>
                        </div>
                        <!-- 阶段 -->
                        <div class="preview-section">
                            <strong>📌 阶段 (${stages.length})</strong>
                            <div id="${uid}_stages">
                                ${(stages||[]).map((s, i) => this._renderEditStage(i)).join('')}
                            </div>
                            <button onclick="CharacterProfilePanel._addEditStage()" style="font-size:14px;border:1px dashed var(--border);background:none;cursor:pointer;padding:2px 8px;border-radius:4px;margin-top:4px">+ 添加阶段</button>
                        </div>
                        <!-- NPC -->
                        <div class="preview-section">
                            <strong>👥 NPC (${npcs.length})</strong>
                            <div id="${uid}_npcs" style="display:flex;flex-wrap:wrap;gap:4px">
                                ${npcs.map((n, i) => `
                                    <span style="display:inline-flex;align-items:center;gap:3px;padding:2px 6px;background:rgba(124,92,252,0.1);border-radius:4px;font-size:14px">
                                        <input value="${(n.name||'').replace(/"/g,'&quot;')}" style="border:1px solid var(--border);width:auto;min-width:40px;padding:1px 3px;font-size:14px;border-radius:3px;color:var(--text-primary)"
                                            oninput="CharacterProfilePanel._syncPreviewNpc(${i},'name',this.value)" />
                                        (<input value="${(n.role||'').replace(/"/g,'&quot;')}" style="border:1px solid var(--border);width:36px;padding:1px 3px;font-size:14px;border-radius:3px;color:var(--text-primary)"
                                            oninput="CharacterProfilePanel._syncPreviewNpc(${i},'role',this.value)" />·
                                        <select style="border:1px solid var(--border);padding:1px 3px;font-size:14px;border-radius:3px;color:var(--text-primary)"
                                            onchange="CharacterProfilePanel._syncPreviewNpc(${i},'relation_type',this.value)">
                                            ${['朋友','导师','对手','同事','恋人','家人','客户','前辈','其他'].map(t =>
                                                `<option value="${t}" ${(n.relation_type||'')===t?'selected':''}>${t}</option>`
                                            ).join('')}
                                        </select>)
                                        <button onclick="CharacterProfilePanel._removeEditNpc(${i})" style="background:none;border:none;color:var(--error);cursor:pointer;padding:0 2px;font-size:14px">✕</button>
                                    </span>`).join('')}
                            </div>
                            <button onclick="CharacterProfilePanel._addEditNpc()" style="font-size:14px;border:1px dashed var(--border);background:none;cursor:pointer;padding:2px 8px;border-radius:4px;margin-top:4px">+ 添加 NPC</button>
                        </div>
                    </div>
                    <div class="mission-review-actions" style="display:flex;gap:8px;margin-top:12px">
                        <button class="btn" onclick="document.getElementById('mission-edit-overlay').remove()">取消</button>
                        <button class="btn btn-primary" onclick="CharacterProfilePanel._saveMissionEdit()">💾 保存修改</button>
                    </div>
                </div>
            </div>`;
        document.body.insertAdjacentHTML('beforeend', html);
    },

    _addEditStage() {
        const main = this._previewData?.main;
        if (main) {
            if (!main.stages) main.stages = [];
            main.stages.push({ name: '新阶段', day_offset: main.stages.length, events: [] });
            this._refreshEditStages();
        }
    },

    _removeEditStage(idx) {
        const main = this._previewData?.main;
        if (main && main.stages) { main.stages.splice(idx, 1); this._refreshEditStages(); }
    },

    _refreshEditStages() {
        const uid = this._previewUid; if (!uid) return;
        const container = document.getElementById(uid + '_stages');
        const stages = (this._previewData?.main?.stages) || [];
        if (!container) return;
        container.innerHTML = stages.map((s, i) => this._renderEditStage(i)).join('') + `
            <button onclick="CharacterProfilePanel._addEditStage()" style="font-size:14px;border:1px dashed var(--border);background:none;cursor:pointer;padding:2px 8px;border-radius:4px;margin-top:4px">+ 添加阶段</button>`;
    },

    /* 渲染单个阶段的编辑块（含嵌套事件），供 _editMission 初始渲染与 _refreshEditStages 复用 */
    _renderEditStage(si) {
        const main = this._previewData?.main;
        const s = main?.stages?.[si];
        if (!s) return '';
        const uid = this._previewUid || '';
        const events = s.events || [];
        const absDay = (main?.start_day || 0) + (s.day_offset || 0);
        return `
            <div class="edit-stage" data-sidx="${si}" style="border:1px solid var(--border-color);border-radius:6px;padding:8px;margin:6px 0">
                <div style="display:flex;align-items:center;gap:4px">
                    <span style="color:var(--text-secondary);min-width:18px">${si+1}.</span>
                    <input value="${(s.name||'').replace(/"/g,'&quot;')}" style="flex:1;border:1px solid var(--border);padding:2px 6px;border-radius:3px;color:var(--text-primary);background:var(--bg-input);font-size:14px"
                        oninput="CharacterProfilePanel._syncPreviewStage(${si},'name',this.value)" />
                    <span style="color:var(--text-secondary);font-size:13px">第${absDay}天</span>
                    <button onclick="CharacterProfilePanel._removeEditStage(${si})" style="background:none;border:none;color:var(--error);cursor:pointer;font-size:14px" title="删除阶段">✕</button>
                </div>
                <div id="${uid}_evt_${si}" class="edit-events">
                    ${events.map((e, ei) => this._renderEditEvent(si, ei)).join('')}
                </div>
                <button onclick="CharacterProfilePanel._addEditEvent(${si})" style="font-size:13px;border:1px dashed var(--border);background:none;cursor:pointer;padding:2px 8px;border-radius:4px;margin-top:4px;color:var(--text-secondary)">+ 添加事件</button>
            </div>`;
    },

    /* 渲染单个事件的编辑块（标题/触发天/地点/是否抉择/描述/选项/属性变化） */
    _renderEditEvent(si, ei) {
        const ev = this._previewData?.main?.stages?.[si]?.events?.[ei];
        if (!ev) return '';
        const isChoice = !!ev.is_choice;
        const choices = ev.choices || [];
        const scText = JSON.stringify(ev.state_changes || {}).replace(/</g, '&lt;');
        let html = `
            <div class="edit-event" data-eidx="${ei}" style="border-left:2px solid var(--accent);padding:4px 8px;margin:6px 0;background:rgba(124,92,252,0.06);border-radius:4px">
                <div style="display:flex;align-items:center;gap:4px">
                    <span style="color:var(--text-secondary);font-size:12px;min-width:32px">事件${ei+1}</span>
                    <input value="${(ev.title||'').replace(/"/g,'&quot;')}" placeholder="事件标题" style="flex:1;border:1px solid var(--border);padding:2px 6px;border-radius:3px;color:var(--text-primary);background:var(--bg-input);font-size:13px"
                        oninput="CharacterProfilePanel._syncPreviewEvent(${si},${ei},'title',this.value)" />
                    <input value="${ev.day_offset||0}" type="number" title="触发天偏移" style="width:58px;border:1px solid var(--border);padding:2px 4px;border-radius:3px;color:var(--text-primary);background:var(--bg-input);font-size:13px"
                        oninput="CharacterProfilePanel._syncPreviewEvent(${si},${ei},'day_offset',this.value)" />
                    <button onclick="CharacterProfilePanel._removeEditEvent(${si},${ei})" style="background:none;border:none;color:var(--error);cursor:pointer;font-size:13px" title="删除事件">✕</button>
                </div>
                <div style="display:flex;gap:6px;margin-top:4px;align-items:center">
                    <input value="${(ev.location||'').replace(/"/g,'&quot;')}" placeholder="地点" style="flex:1;border:1px solid var(--border);padding:2px 6px;border-radius:3px;color:var(--text-primary);background:var(--bg-input);font-size:13px"
                        oninput="CharacterProfilePanel._syncPreviewEvent(${si},${ei},'location',this.value)" />
                    <label style="font-size:12px;color:var(--text-secondary);display:flex;align-items:center;gap:3px;white-space:nowrap">
                        <input type="checkbox" ${isChoice?'checked':''} onchange="CharacterProfilePanel._syncPreviewEvent(${si},${ei},'is_choice',this.checked)" />抉择
                    </label>
                </div>
                <textarea placeholder="事件描述" rows=2 style="width:100%;border:1px solid var(--border);padding:4px 6px;border-radius:3px;font-size:13px;resize:vertical;color:var(--text-primary);background:var(--bg-input);margin-top:4px"
                    oninput="CharacterProfilePanel._syncPreviewEvent(${si},${ei},'description',this.value)">${(ev.description||'').replace(/</g,'&lt;')}</textarea>`;
        if (isChoice) {
            html += `
                <div class="edit-choices" style="margin-top:4px;padding-left:10px;border-left:1px dashed var(--border-color)">
                    <div style="font-size:12px;color:var(--text-secondary);margin-bottom:2px">选项 (${choices.length}):</div>
                    ${choices.map((ch, ci) => `
                        <div style="margin:3px 0">
                            <div style="display:flex;gap:4px;align-items:flex-start">
                                <input value="${(ch.text||'').replace(/"/g,'&quot;')}" placeholder="选项文本" style="flex:1;border:1px solid var(--border);padding:2px 6px;border-radius:3px;color:var(--text-primary);background:var(--bg-input);font-size:13px"
                                    oninput="CharacterProfilePanel._syncPreviewChoice(${si},${ei},${ci},'text',this.value)" />
                                <button onclick="CharacterProfilePanel._removeEditChoice(${si},${ei},${ci})" style="background:none;border:none;color:var(--error);cursor:pointer;font-size:13px" title="删除选项">✕</button>
                            </div>
                            <textarea placeholder="state_changes (JSON)" rows=2 style="width:100%;border:1px solid var(--border);padding:3px 6px;border-radius:3px;font-size:12px;font-family:monospace;resize:vertical;color:var(--text-primary);background:var(--bg-input);margin-top:2px"
                                onblur="CharacterProfilePanel._syncPreviewEventJSON(${si},${ei},'choice',${ci},this.value)">${JSON.stringify(ch.state_changes||{}).replace(/</g,'&lt;')}</textarea>
                        </div>`).join('')}
                    <button onclick="CharacterProfilePanel._addEditChoice(${si},${ei})" style="font-size:13px;border:1px dashed var(--border);background:none;cursor:pointer;padding:2px 8px;border-radius:4px;margin-top:2px;color:var(--text-secondary)">+ 添加选项</button>
                </div>`;
        } else {
            html += `
                <textarea placeholder="state_changes (JSON)" rows=2 style="width:100%;border:1px solid var(--border);padding:3px 6px;border-radius:3px;font-size:12px;font-family:monospace;resize:vertical;color:var(--text-primary);background:var(--bg-input);margin-top:4px"
                    onblur="CharacterProfilePanel._syncPreviewEventJSON(${si},${ei},'event',null,this.value)">${scText}</textarea>`;
        }
        html += `</div>`;
        return html;
    },

    /* ── 事件/选项 的同步与增删 ── */
    _syncPreviewEvent(si, ei, field, value) {
        const ev = this._previewData?.main?.stages?.[si]?.events?.[ei];
        if (!ev) return;
        ev[field] = value;
        // 切换“是否抉择”时需重渲染以显示/隐藏选项区
        if (field === 'is_choice') this._refreshEditStages();
    },

    _syncPreviewChoice(si, ei, ci, field, value) {
        const ch = this._previewData?.main?.stages?.[si]?.events?.[ei]?.choices?.[ci];
        if (ch) ch[field] = value;
    },

    _syncPreviewEventJSON(si, ei, kind, ci, value) {
        let parsed;
        try { parsed = JSON.parse(value); }
        catch (e) { this._showToast('❌ state_changes JSON 格式错误，未保存', true); return; }
        const ev = this._previewData?.main?.stages?.[si]?.events?.[ei];
        if (!ev) return;
        if (kind === 'choice') {
            if (!ev.choices) ev.choices = [];
            if (ev.choices[ci]) ev.choices[ci].state_changes = parsed;
        } else {
            ev.state_changes = parsed;
        }
    },

    _addEditEvent(si) {
        const s = this._previewData?.main?.stages?.[si];
        if (s) {
            if (!s.events) s.events = [];
            s.events.push({ title: '新事件', day_offset: 0, description: '', location: '', is_choice: false, choices: [], state_changes: {} });
            this._refreshEditStages();
        }
    },

    _removeEditEvent(si, ei) {
        const s = this._previewData?.main?.stages?.[si];
        if (s && s.events) { s.events.splice(ei, 1); this._refreshEditStages(); }
    },

    _addEditChoice(si, ei) {
        const ev = this._previewData?.main?.stages?.[si]?.events?.[ei];
        if (ev) {
            if (!ev.choices) ev.choices = [];
            ev.choices.push({ text: '新选项', state_changes: {} });
            this._refreshEditStages();
        }
    },

    _removeEditChoice(si, ei, ci) {
        const ev = this._previewData?.main?.stages?.[si]?.events?.[ei];
        if (ev && ev.choices) { ev.choices.splice(ci, 1); this._refreshEditStages(); }
    },

    _addEditNpc() {
        const main = this._previewData?.main;
        if (main) {
            if (!main.npc_roster) main.npc_roster = [];
            main.npc_roster.push({ name: '新角色', role: '', relation_type: '其他' });
            this._refreshEditNpcs();
        }
    },

    _removeEditNpc(idx) {
        const main = this._previewData?.main;
        if (main && main.npc_roster) { main.npc_roster.splice(idx, 1); this._refreshEditNpcs(); }
    },

    /** 保存编辑后的任务到后端 */
    async _saveMissionEdit() {
        if (!this._editingMissionId || !this._previewData) return;
        const overlay = document.getElementById('mission-edit-overlay');
        const main = this._previewData.main || {};
        try {
            const r = await fetch(`/api/character/${this._characterName}/missions/${this._editingMissionId}`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    mission_name: main.mission_name,
                    mission_description: main.mission_description,
                    core_conflict: main.core_conflict,
                    tone: main.tone,
                    stages: main.stages,
                    npc_roster: main.npc_roster,
                }),
            });
            const data = await r.json();
            if (data.success) {
                this._showToast('✅ 任务已更新');
                if (overlay) overlay.remove();
                this._loadCases();           // 刷新任务列表
                this._loadRelations();         // 刷新社交关系（NPC 可能变了）
                this._refreshGlobalFriendsAndArcs();
            } else {
                this._showToast('❌ 更新失败：' + (data.error || ''), true);
            }
        } catch(e) {
            this._showToast('❌ 请求失败：' + e.message, true);
        }
    },

    /* ── 角色专属事件模板（原 GM「事件模板」面板的「角色专属」Tab 迁移至此）── */

    _renderEventCard(t, i) {
        const isMission = t._source === 'mission';
        const title = t.narrative?.title || t.trigger_id || '(未命名)';
        const desc = (t.narrative?.description || '').substring(0, 60);
        const changes = (t.state_changes || []).map(c => `${c.attribute}${c.delta > 0 ? '+' : ''}${c.delta}`).join(', ');
        const loc = t.location || '?';
        const missionBadge = isMission
            ? `<span class="et-location" style="background:rgba(124,92,252,0.12);color:var(--accent,#7c5cfc);border-radius:4px;padding:1px 6px;font-size:14px">🎬 ${t._mission_name || '任务'}</span>`
            : '';
        const actionBtn = isMission
            ? `<span style="font-size:14px;color:var(--text-secondary)">任务生成·只读</span>`
            : `<button class="btn-sm" onclick="CharacterProfilePanel._editEventTemplate(${i})">编辑</button>`;
        return `<div class="et-card">
            <div class="et-header">
                <span class="et-trigger">${t.trigger_id || ''}</span>
                <span class="et-title">${title}</span>
                ${missionBadge}
                <span class="et-location">📍${loc}</span>
                ${actionBtn}
            </div>
            <div class="et-desc">${desc}</div>
            <div class="et-changes">${changes || '无属性变化'}</div>
        </div>`;
    },

    async _loadEventTemplates() {
        const container = document.getElementById('profile-event-templates');
        if (!container) return;
        const name = this._characterName;
        if (!name) { container.innerHTML = '<div class="profile-empty">未激活角色</div>'; return; }
        container.innerHTML = '<div class="profile-loading">加载中...</div>';
        try {
            const r = await fetch(`/api/${encodeURIComponent(name)}/event-templates`);
            const data = await r.json();
            const templates = data.templates || [];
            this._eventTemplates = templates;
            let html = `<div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap">
                <button class="btn btn-primary" id="btn-generate-event-tpl">生成角色专属模板（LLM）</button>
                <button class="btn" id="btn-save-event-tpl">保存修改</button>
            </div>`;
            html += `<div id="profile-event-tpl-list">${this._renderEventTemplateListInner()}</div>`;
            // 编辑弹窗（动态创建，避免依赖已废弃的通用事件模板弹窗）
            html += `<div id="et-edit-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);z-index:9999;align-items:center;justify-content:center">
                <div style="background:var(--bg-primary);border-radius:12px;padding:16px;max-width:600px;width:90%;max-height:80vh;overflow:auto">
                    <h3>编辑事件模板</h3>
                    <textarea id="et-edit-json" style="width:100%;min-height:300px;padding:10px;border:1px solid var(--border-color);border-radius:8px;background:var(--bg-input);color:var(--text-primary);font-family:monospace;font-size:14px"></textarea>
                    <div style="display:flex;gap:8px;margin-top:10px;justify-content:flex-end">
                        <button class="btn" onclick="document.getElementById('et-edit-modal').style.display='none'">取消</button>
                        <button class="btn btn-primary" onclick="CharacterProfilePanel._saveSingleEventTemplate()">保存此条</button>
                    </div>
                </div>
            </div>`;
            container.innerHTML = html;
            document.getElementById('btn-generate-event-tpl')?.addEventListener('click', () => this._generateEventTemplates());
            document.getElementById('btn-save-event-tpl')?.addEventListener('click', () => this._saveEventTemplates());
        } catch (e) {
            container.innerHTML = '<div class="profile-error">加载失败</div>';
        }
    },

    _renderEventTemplateListInner() {
        const templates = this._eventTemplates || [];
        if (!templates.length) {
            return '<div class="profile-empty">暂无专属事件模板（任务生成的事件由任务系统管理，不计入）</div>';
        }
        return templates.map((t, i) => this._renderEventCard(t, i)).join('');
    },

    _editEventTemplate(index) {
        this._editEventIndex = index;
        const t = this._eventTemplates[index];
        if (!t) return;
        document.getElementById('et-edit-json').value = JSON.stringify(t, null, 2);
        document.getElementById('et-edit-modal').style.display = 'flex';
    },

    _saveSingleEventTemplate() {
        try {
            const newData = JSON.parse(document.getElementById('et-edit-json').value);
            this._eventTemplates[this._editEventIndex] = newData;
            document.getElementById('et-edit-modal').style.display = 'none';
            const list = document.getElementById('profile-event-tpl-list');
            if (list) list.innerHTML = this._renderEventTemplateListInner();
        } catch (e) {
            alert('JSON 格式错误: ' + e.message);
        }
    },

    async _saveEventTemplates() {
        const btn = document.getElementById('btn-save-event-tpl');
        if (!btn) return;
        btn.disabled = true; btn.textContent = '保存中...';
        const name = this._characterName;
        try {
            const r = await fetch(`/api/${encodeURIComponent(name)}/event-templates`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ templates: this._eventTemplates }),
            });
            const data = await r.json();
            alert(data.success ? `保存成功！共 ${data.saved ?? this._eventTemplates.length} 条（任务生成的事件由任务系统管理，不计入）` : '保存失败: ' + (data.error || ''));
        } catch (e) {
            alert('请求失败：' + e.message);
        }
        btn.disabled = false; btn.textContent = '保存修改';
    },

    async _generateEventTemplates() {
        const btn = document.getElementById('btn-generate-event-tpl');
        if (!btn) return;
        btn.disabled = true; btn.textContent = '生成中...';
        const name = this._characterName;
        try {
            const r = await fetch(`/api/${encodeURIComponent(name)}/event-templates/generate`, { method: 'POST' });
            const data = await r.json();
            if (data.success) {
                this._eventTemplates = data.templates || [];
                const list = document.getElementById('profile-event-tpl-list');
                if (list) list.innerHTML = this._renderEventTemplateListInner();
                alert(`生成成功！共 ${this._eventTemplates.length} 条专属模板`);
            } else {
                alert('生成失败：' + (data.error || ''));
            }
        } catch (e) {
            alert('请求失败：' + e.message);
        }
        btn.disabled = false; btn.textContent = '生成角色专属模板（LLM）';
    },

    /* ── 世界观结构化审核弹窗（A1）── */

    /** 渲染世界观结构化审核弹窗：所有字段可编辑，可保存 */
    _showWorldViewReview(ws) {
        const uid = 'wr_' + Date.now();
        this._wvUid = uid;
        // 深拷贝一份作为工作数据
        this._wvData = JSON.parse(JSON.stringify(ws || {}));

        const anchor = this._wvData.anchor || (this._wvData.anchor = {});
        const constraints = this._wvData.constraints || (this._wvData.constraints = {});
        const narrative = this._wvData.narrative_schedule || (this._wvData.narrative_schedule = { phases: [] });
        if (!Array.isArray(narrative.phases)) narrative.phases = [];

        const lifeGoals = Array.isArray(this._wvData.life_goals) ? this._wvData.life_goals : [];
        const stressSources = Array.isArray(this._wvData.stress_sources) ? this._wvData.stress_sources : [];
        const comfortActivities = Array.isArray(this._wvData.comfort_activities) ? this._wvData.comfort_activities : [];
        const forbiddenBehaviors = Array.isArray(constraints.forbidden_behaviors) ? constraints.forbidden_behaviors : [];
        const storyTones = Array.isArray(this._wvData.story_tones) ? this._wvData.story_tones : [];

        const html = `
            <div class="mission-review-overlay" id="worldview-review-overlay" onclick="if(event.target===this)this.remove()">
                <div class="mission-review-modal" style="max-width:780px;max-height:88vh;overflow-y:auto;padding:24px">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
                        <h3 style="margin:0;font-size:17px">🌐 世界观审核（结构化编辑）</h3>
                        <div style="display:flex;gap:8px">
                            <button class="btn" id="${uid}_editToggle" onclick="CharacterProfilePanel._toggleWVEdit()" style="font-size:13px;padding:6px 12px">✏️ 编辑模式</button>
                            <button class="btn" onclick="document.getElementById('worldview-review-overlay').remove()" style="font-size:13px;padding:6px 12px">关闭</button>
                        </div>
                    </div>

                    <!-- 1. 实际投喂 LLM 的提示词（只读预览，真实 prompt） -->
                    <div class="wv-section">
                        <div class="wv-section-title">📜 实际投喂 LLM 的提示词（真实 prompt，只读）</div>
                        <textarea id="${uid}_world_prompt" rows="6" class="wv-input wv-textarea" readonly>${this._escHtml(this._wvData._rendered_prompt || this._wvData.world_prompt || '')}</textarea>
                    </div>

                    <!-- 2. 核心矛盾 -->
                    <div class="wv-section">
                        <div class="wv-section-title">⚡ 核心矛盾 central_conflict</div>
                        <textarea id="${uid}_central_conflict" rows="2" class="wv-input wv-textarea" readonly
                            oninput="CharacterProfilePanel._syncWVField('central_conflict', this.value)">${this._escHtml(this._wvData.central_conflict || '')}</textarea>
                    </div>

                    <!-- 3. 角色锚定 -->
                    <div class="wv-section">
                        <div class="wv-section-title">🎯 角色锚定 anchor</div>
                        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
                            <label class="wv-label">核心性格 core_trait
                                <input id="${uid}_core_trait" value="${this._escAttr(anchor.core_trait || '')}" class="wv-input" readonly
                                    oninput="CharacterProfilePanel._syncWVAnchor('core_trait', this.value)" />
                            </label>
                            <label class="wv-label">核心价值观 value
                                <input id="${uid}_value" value="${this._escAttr(anchor.value || '')}" class="wv-input" readonly
                                    oninput="CharacterProfilePanel._syncWVAnchor('value', this.value)" />
                            </label>
                            <label class="wv-label">底线 boundary
                                <input id="${uid}_boundary" value="${this._escAttr(anchor.boundary || '')}" class="wv-input" readonly
                                    oninput="CharacterProfilePanel._syncWVAnchor('boundary', this.value)" />
                            </label>
                            <label class="wv-label">内心冲突区 conflict_zone
                                <input id="${uid}_conflict_zone" value="${this._escAttr(anchor.conflict_zone || '')}" class="wv-input" readonly
                                    oninput="CharacterProfilePanel._syncWVAnchor('conflict_zone', this.value)" />
                            </label>
                        </div>
                    </div>

                    <!-- 4. 人生目标 -->
                    <div class="wv-section">
                        <div class="wv-section-title">🌱 人生目标 life_goals（${lifeGoals.length}）</div>
                        <div id="${uid}_life_goals" class="wv-list">
                            ${lifeGoals.map((g, i) => this._renderLifeGoalRow(uid, i, g)).join('')}
                        </div>
                        <button class="wv-add-btn" onclick="CharacterProfilePanel._addLifeGoal()">+ 添加目标</button>
                    </div>

                    <!-- 5. 情感压力 -->
                    <div class="wv-section">
                        <div class="wv-section-title">💢 压力源 stress_sources（${stressSources.length}）</div>
                        <div id="${uid}_stress_sources" class="wv-tags">
                            ${stressSources.map((s, i) => this._renderTag(uid, 'stress_sources', i, s)).join('')}
                            <button class="wv-add-tag" onclick="CharacterProfilePanel._addWVTag('stress_sources')">+ 添加</button>
                        </div>
                    </div>

                    <div class="wv-section">
                        <div class="wv-section-title">☀️ 安慰活动 comfort_activities（${comfortActivities.length}）</div>
                        <div id="${uid}_comfort_activities" class="wv-tags">
                            ${comfortActivities.map((s, i) => this._renderTag(uid, 'comfort_activities', i, s)).join('')}
                            <button class="wv-add-tag" onclick="CharacterProfilePanel._addWVTag('comfort_activities')">+ 添加</button>
                        </div>
                    </div>

                    <div class="wv-section">
                        <div class="wv-section-title">🚫 禁止行为 forbidden_behaviors（${forbiddenBehaviors.length}）</div>
                        <div id="${uid}_forbidden_behaviors" class="wv-tags">
                            ${forbiddenBehaviors.map((s, i) => this._renderTag(uid, 'forbidden_behaviors', i, s)).join('')}
                            <button class="wv-add-tag" onclick="CharacterProfilePanel._addWVTag('forbidden_behaviors')">+ 添加</button>
                        </div>
                    </div>

                    <!-- 6. 故事基调 -->
                    <div class="wv-section">
                        <div class="wv-section-title">🎭 故事基调 story_tones（${storyTones.length}）</div>
                        <div id="${uid}_story_tones" class="wv-tags">
                            ${storyTones.map((s, i) => this._renderTag(uid, 'story_tones', i, s)).join('')}
                            <button class="wv-add-tag" onclick="CharacterProfilePanel._addWVTag('story_tones')">+ 添加</button>
                        </div>
                    </div>

                    <!-- 7. 阶段表 -->
                    <div class="wv-section">
                        <div class="wv-section-title">📅 阶段表 narrative_schedule.phases（${narrative.phases.length}）</div>
                        <div id="${uid}_phases">
                            ${narrative.phases.map((p, i) => this._renderPhaseRow(uid, i, p)).join('')}
                        </div>
                        <button class="wv-add-btn" onclick="CharacterProfilePanel._addWVPhase()">+ 添加阶段</button>
                    </div>

                    <!-- 底部操作 -->
                    <div class="wv-actions">
                        <button class="btn" onclick="document.getElementById('worldview-review-overlay').remove()" style="font-size:14px;padding:10px 18px">取消</button>
                        <button class="btn btn-primary" onclick="CharacterProfilePanel._saveWorldViewReview()" style="font-size:14px;padding:10px 18px">💾 保存到世界观</button>
                    </div>
                </div>
            </div>`;
        document.body.insertAdjacentHTML('beforeend', html);
    },

    /** HTML 转义（防 XSS） */
    _escHtml(s) { return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); },
    _escAttr(s) { return this._escHtml(s).replace(/"/g,'&quot;'); },

    /** 渲染单个 life_goal 行 */
    _renderLifeGoalRow(uid, i, g) {
        g = g || {};
        return `<div class="wv-life-goal-row" data-idx="${i}">
            <input class="wv-input wv-life-goal-input" placeholder="目标内容" value="${this._escAttr(g.goal || '')}"
                oninput="CharacterProfilePanel._syncLifeGoal(${i}, 'goal', this.value)" />
            <select class="wv-input wv-life-goal-type" onchange="CharacterProfilePanel._syncLifeGoal(${i}, 'type', this.value)">
                ${['career','personal','relationship','health','creative','other'].map(t =>
                    `<option value="${t}" ${g.type===t?'selected':''}>${t}</option>`).join('')}
            </select>
            <input type="number" min="1" max="10" class="wv-input wv-life-goal-urgency" placeholder="紧急度" value="${g.urgency || 5}"
                oninput="CharacterProfilePanel._syncLifeGoal(${i}, 'urgency', parseInt(this.value)||5)" />
            <button class="wv-del" onclick="CharacterProfilePanel._delLifeGoal(${i})" title="删除">✕</button>
        </div>`;
    },

    /** 渲染单个标签 */
    _renderTag(uid, field, i, val) {
        return `<span class="wv-tag">
            <input class="wv-tag-input" value="${this._escAttr(val || '')}"
                oninput="CharacterProfilePanel._syncWVTag('${field}', ${i}, this.value)" />
            <button class="wv-del" onclick="CharacterProfilePanel._delWVTag('${field}', ${i})" title="删除">✕</button>
        </span>`;
    },

    /** 渲染单个 phase 行 */
    _renderPhaseRow(uid, i, p) {
        p = p || {};
        const dayRange = Array.isArray(p.day_range) ? p.day_range : [null, null];
        return `<div class="wv-phase-row" data-idx="${i}">
            <div style="display:grid;grid-template-columns:1.5fr 1fr 1.5fr 36px;gap:8px;margin-bottom:6px">
                <input class="wv-input" placeholder="阶段名（phase）" value="${this._escAttr(p.phase || '')}"
                    oninput="CharacterProfilePanel._syncWVPhase(${i}, 'phase', this.value)" />
                <input class="wv-input" placeholder="天数 [start,end]" value="${this._escAttr((dayRange[0]||'')+'~'+(dayRange[1]||''))}"
                    oninput="CharacterProfilePanel._syncWVPhaseDay(${i}, this.value)" />
                <input class="wv-input" placeholder="地点（location）" value="${this._escAttr(p.location || '')}"
                    oninput="CharacterProfilePanel._syncWVPhase(${i}, 'location', this.value)" />
                <button class="wv-del" onclick="CharacterProfilePanel._delWVPhase(${i})" title="删除">✕</button>
            </div>
            <textarea class="wv-input wv-textarea" rows="2" placeholder="阶段描述（description）"
                oninput="CharacterProfilePanel._syncWVPhase(${i}, 'description', this.value)">${this._escHtml(p.description || '')}</textarea>
        </div>`;
    },

    /** 字段同步（顶层标量字段） */
    _syncWVField(field, value) {
        if (this._wvData) this._wvData[field] = value;
    },
    _syncWVAnchor(field, value) {
        if (this._wvData) {
            if (!this._wvData.anchor) this._wvData.anchor = {};
            this._wvData.anchor[field] = value;
        }
    },
    _syncLifeGoal(i, field, value) {
        if (this._wvData && Array.isArray(this._wvData.life_goals)) {
            if (!this._wvData.life_goals[i]) this._wvData.life_goals[i] = {};
            this._wvData.life_goals[i][field] = value;
        }
    },
    _addLifeGoal() {
        if (!this._wvData) return;
        if (!Array.isArray(this._wvData.life_goals)) this._wvData.life_goals = [];
        this._wvData.life_goals.push({ goal: '新目标', type: 'personal', urgency: 5 });
        this._refreshWVLifeGoals();
    },
    _delLifeGoal(i) {
        if (!this._wvData || !Array.isArray(this._wvData.life_goals)) return;
        this._wvData.life_goals.splice(i, 1);
        this._refreshWVLifeGoals();
    },
    _refreshWVLifeGoals() {
        const uid = this._wvUid;
        if (!uid) return;
        const box = document.getElementById(`${uid}_life_goals`);
        if (box) {
            box.innerHTML = (this._wvData.life_goals || []).map((g, i) => this._renderLifeGoalRow(uid, i, g)).join('');
        }
        // 更新 section 标题计数
        const sec = box?.parentElement;
        if (sec) {
            const title = sec.querySelector('.wv-section-title');
            if (title) title.textContent = `🌱 人生目标 life_goals（${(this._wvData.life_goals || []).length}）`;
        }
    },

    /** 标签数组同步 */
    _syncWVTag(field, i, value) {
        if (!this._wvData) return;
        // forbidden_behaviors 在 constraints 下
        const target = field === 'forbidden_behaviors' ? this._wvData.constraints : this._wvData;
        if (!target[field]) target[field] = [];
        target[field][i] = value;
    },
    _addWVTag(field) {
        if (!this._wvData) return;
        const target = field === 'forbidden_behaviors' ? this._wvData.constraints : this._wvData;
        if (!Array.isArray(target[field])) target[field] = [];
        target[field].push('新标签');
        this._refreshWVTags(field);
    },
    _delWVTag(field, i) {
        if (!this._wvData) return;
        const target = field === 'forbidden_behaviors' ? this._wvData.constraints : this._wvData;
        if (!Array.isArray(target[field])) return;
        target[field].splice(i, 1);
        this._refreshWVTags(field);
    },
    _refreshWVTags(field) {
        const uid = this._wvUid;
        const box = document.getElementById(`${uid}_${field}`);
        if (!box) return;
        const target = field === 'forbidden_behaviors' ? this._wvData.constraints : this._wvData;
        const arr = Array.isArray(target[field]) ? target[field] : [];
        box.innerHTML = arr.map((s, i) => this._renderTag(uid, field, i, s)).join('') +
            `<button class="wv-add-tag" onclick="CharacterProfilePanel._addWVTag('${field}')">+ 添加</button>`;
        // 更新 section 标题计数
        const sec = box.parentElement;
        if (sec) {
            const title = sec.querySelector('.wv-section-title');
            const titleBase = {
                stress_sources: '💢 压力源',
                comfort_activities: '☀️ 安慰活动',
                forbidden_behaviors: '🚫 禁止行为',
                story_tones: '🎭 故事基调',
            };
            if (title && titleBase[field]) title.textContent = `${titleBase[field]} ${field}（${arr.length}）`;
        }
    },

    /** phase 同步 */
    _syncWVPhase(i, field, value) {
        if (!this._wvData || !this._wvData.narrative_schedule) return;
        const phases = this._wvData.narrative_schedule.phases || [];
        if (!phases[i]) phases[i] = {};
        phases[i][field] = value;
    },
    _syncWVPhaseDay(i, value) {
        // 解析 "start~end" 或 "start-end" 或 "start,end"
        const m = String(value || '').split(/[~\-,，]/).map(s => parseInt(s.trim()));
        const start = m[0] || null;
        const end = m[1] || null;
        if (!this._wvData || !this._wvData.narrative_schedule) return;
        const phases = this._wvData.narrative_schedule.phases || [];
        if (!phases[i]) phases[i] = {};
        phases[i].day_range = [start, end];
    },
    _addWVPhase() {
        if (!this._wvData) return;
        if (!this._wvData.narrative_schedule) this._wvData.narrative_schedule = {};
        if (!Array.isArray(this._wvData.narrative_schedule.phases)) this._wvData.narrative_schedule.phases = [];
        this._wvData.narrative_schedule.phases.push({ phase: '新阶段', day_range: [null, null], location: '', description: '' });
        this._refreshWVPhases();
    },
    _delWVPhase(i) {
        if (!this._wvData || !this._wvData.narrative_schedule) return;
        if (!Array.isArray(this._wvData.narrative_schedule.phases)) return;
        this._wvData.narrative_schedule.phases.splice(i, 1);
        this._refreshWVPhases();
    },
    _refreshWVPhases() {
        const uid = this._wvUid;
        const box = document.getElementById(`${uid}_phases`);
        if (!box) return;
        const phases = this._wvData?.narrative_schedule?.phases || [];
        box.innerHTML = phases.map((p, i) => this._renderPhaseRow(uid, i, p)).join('');
        const sec = box.parentElement;
        if (sec) {
            const title = sec.querySelector('.wv-section-title');
            if (title) title.textContent = `📅 阶段表 narrative_schedule.phases（${phases.length}）`;
        }
    },

    /** 编辑模式切换 */
    _toggleWVEdit() {
        const uid = this._wvUid;
        if (!uid) return;
        const modal = document.getElementById('worldview-review-overlay');
        if (!modal) return;
        const inputs = modal.querySelectorAll('input.wv-input, textarea.wv-input');
        const btn = document.getElementById(`${uid}_editToggle`);
        const isReadonly = inputs[0]?.readOnly ?? true;
        inputs.forEach(el => { el.readOnly = !isReadonly; });
        if (btn) btn.textContent = isReadonly ? '🔒 锁定预览' : '✏️ 编辑模式';
    },

    /** 保存：POST 到 /api/character/<name>/world-setting */
    async _saveWorldViewReview() {
        if (!this._wvData) return;
        try {
            const r = await fetch(`/api/character/${this._characterName}/world-setting`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(this._wvData),
            });
            const data = await r.json();
            if (data.success) {
                this._showToast('✅ 世界观已保存');
                // 关闭弹窗
                const ov = document.getElementById('worldview-review-overlay');
                if (ov) ov.remove();
                // 刷新面板
                this._loadWorldView();
                // 同时清掉 _rendered_prompt，让下次重新生成（因为已修改过）
                // （可选：保留也行）
            } else {
                this._showToast('❌ 保存失败：' + (data.error || ''), true);
            }
        } catch (e) {
            this._showToast('❌ 请求失败：' + e.message, true);
        }
    },

    /** 简易 toast（页面顶部浮窗，3 秒自动消失） */
    _showToast(msg, isError = false) {
        // 移除已存在的 toast
        const old = document.getElementById('character-profile-toast');
        if (old) old.remove();
        const toast = document.createElement('div');
        toast.id = 'character-profile-toast';
        toast.textContent = msg;
        toast.style.cssText = `
            position:fixed; top:24px; left:50%; transform:translateX(-50%);
            background:${isError ? 'var(--error)' : 'var(--success)'};
            color:#fff; padding:12px 22px; border-radius:8px;
            font-size:14px; font-weight:500; box-shadow:0 4px 16px rgba(0,0,0,0.2);
            z-index:11000; opacity:0; transition:opacity 0.2s;
        `;
        document.body.appendChild(toast);
        // 触发淡入
        requestAnimationFrame(() => { toast.style.opacity = '1'; });
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 250);
        }, 3000);
    },
};

// 注册到 window
window.CharacterProfilePanel = CharacterProfilePanel;
