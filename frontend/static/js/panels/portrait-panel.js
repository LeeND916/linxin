/**
 * ComfyUI 肖像生成面板
 * 接管原 AvatarPanel 的肖像展示区域 + 穿搭更新逻辑
 *
 * 生成入口：
 *   - 「生成肖像」按钮：快速生成（后端自动拼角色状态提示词，不弹窗）
 *   - 「✏️ 自定义」按钮：打开弹窗，按 10 段拼中文提示词后提交 ComfyUI
 *
 * 弹窗内 12 段渲染顺序（固定）：
 *   1 画风定调 / 2 构图与景别 / 3 身份锚点(固定) / 4 五官与微表情(可编辑)
 *   / 5 穿搭与造型 / 6 气质关键词(固定) / 7 当前情绪(可编辑) / 8 光影与色调
 *   / 9 画面减法 / 10 背景地点(女主专属) / 11 动作(女主专属) / 12 自定义追加(末尾)
 *
 * 词库类（1/2/8/9）支持：
 *   - 小类之间多选；同一小类内单选（再次点击取消）
 *   - 选中后预览使用 PORTRAIT_VOCAB 中的增强正向提示词，而非仅选项名
 */
const PortraitPanel = {
    loading: false,
    builderData: null,

    // 10 段顺序与类型：vocab=词库类, fixed=只读固定段, outfit=穿搭下拉
    SEGMENTS: [
        { key: 'style',         kind: 'vocab' },
        { key: 'composition',   kind: 'vocab' },
        { key: 'identity_anchor', kind: 'fixed', src: 'identity_anchor' },
        { key: 'appearance',    kind: 'editable', src: 'appearance' },
        { key: 'outfit',        kind: 'outfit' },
        { key: 'temperament',   kind: 'fixed', src: 'temperament' },
        { key: 'mood',          kind: 'editable', src: 'mood' },
        { key: 'lighting',      kind: 'vocab' },
        { key: 'simplify',      kind: 'vocab' },
        { key: 'background',    kind: 'location' },
        { key: 'action',        kind: 'activity' },
        { key: 'custom_tail',   kind: 'tail' },
    ],

    init() {
        this.bindEvents();
        this.checkStatus();
        this.initPhotoToggle();
        this.loadLatestImage();
    },

    /**
     * 照片（肖像）默认隐藏，点击姓名块切换显示/隐藏；照片显示时单击照片本身也可收起。
     * photo-hidden 同步到 wrap 容器，连同 import/history 覆盖按钮一起进退，不再留 180×180 空盒子。
     */
    initPhotoToggle() {
        const display = document.getElementById('portrait-display');
        const wrap = document.getElementById('portrait-display-wrap');
        const label = document.querySelector('.avatar-label');
        if (!display || !label) return;

        display.classList.add('photo-hidden');
        if (wrap) wrap.classList.add('photo-hidden');
        label.classList.remove('photo-shown');

        if (this._photoToggleReady) return;
        this._photoToggleReady = true;

        const toggle = () => {
            const hidden = display.classList.toggle('photo-hidden');
            if (wrap) wrap.classList.toggle('photo-hidden', hidden);
            label.classList.toggle('photo-shown', !hidden);
        };
        label.addEventListener('click', toggle);

        display.addEventListener('click', () => {
            if (!display.classList.contains('photo-hidden')) {
                display.classList.add('photo-hidden');
                if (wrap) wrap.classList.add('photo-hidden');
                label.classList.remove('photo-shown');
            }
        });
    },

    bindEvents() {
        const btn = document.getElementById('btn-generate-portrait');
        if (btn) btn.addEventListener('click', () => this.generate());

        const customBtn = document.getElementById('btn-custom-portrait');
        if (customBtn) customBtn.addEventListener('click', () => this.openBuilder());

        // 头像导入 / 历史回滚（覆盖在肖像图右下角，仅在确有肖像图时显示）
        const importBtn = document.getElementById('btn-import-avatar');
        if (importBtn) importBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.importAvatar();
        });
        const histBtn = document.getElementById('btn-avatar-history');
        if (histBtn) histBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleHistoryMenu();
        });
        // 点击肖像区之外时收起历史菜单
        document.addEventListener('click', (e) => {
            const wrap = document.getElementById('portrait-display-wrap');
            if (wrap && !wrap.contains(e.target)) this._closeHistoryMenu();
        });

        const closeBtn = document.getElementById('btn-portrait-builder-close');
        if (closeBtn) closeBtn.addEventListener('click', () => this.closeBuilder());

        const cancelBtn = document.getElementById('btn-portrait-builder-cancel');
        if (cancelBtn) cancelBtn.addEventListener('click', () => this.closeBuilder());

        const genBtn = document.getElementById('btn-portrait-builder-generate');
        if (genBtn) genBtn.addEventListener('click', () => this.onBuilderGenerate());

        const overlay = document.getElementById('portrait-builder-overlay');
        if (overlay) {
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) this.closeBuilder();
            });
        }
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && overlay && overlay.style.display !== 'none') {
                this.closeBuilder();
            }
        });

        // 弹窗内事件用委托（body 容器常驻，innerHTML 会重渲染）
        const body = document.getElementById('portrait-builder-body');
        if (body) {
            body.addEventListener('click', (e) => {
                const chip = e.target.closest('.pb-chip');
                if (chip) { this.toggleChip(chip); return; }
                const hairTrigger = e.target.closest('.pb-hair-trigger');
                if (hairTrigger) {
                    const list = hairTrigger.parentElement.querySelector('.pb-hair-list');
                    if (list) list.hidden = !list.hidden;
                    return;
                }
                const hairOpt = e.target.closest('.pb-hair-opt');
                if (hairOpt) { this.selectHairOption(hairOpt); return; }
                this.closeHairLists();
            });
            body.addEventListener('mouseover', (e) => {
                const opt = e.target.closest('.pb-hair-opt');
                if (opt) this.showHairPreview(opt);
            });
            body.addEventListener('mouseout', (e) => {
                const opt = e.target.closest('.pb-hair-opt');
                if (opt) this.hideHairTip(opt);
            });
            body.addEventListener('input', (e) => {
                if (e.target.classList.contains('pb-custom') ||
                    e.target.classList.contains('pb-outfit-select') ||
                    e.target.classList.contains('pb-editable') ||
                    e.target.classList.contains('pb-tail-input')) {
                    this.updatePreview();
                }
            });
            body.addEventListener('change', (e) => {
                if (e.target.classList.contains('pb-outfit-select')) {
                    const comp = e.target.getAttribute('data-comp');
                    const descEl = e.target.parentElement.querySelector(
                        `.pb-outfit-desc[data-comp="${comp}"]`
                    );
                    if (descEl) {
                        const opt = e.target.options[e.target.selectedIndex];
                        descEl.textContent = opt ? (opt.dataset.desc || '') : '';
                    }
                    this.updatePreview();
                }
            });
        }

        // 预览框为只读：仅展示自动组装结果，不可手动编辑
        const resetBtn = document.getElementById('btn-portrait-preview-reset');
        if (resetBtn) resetBtn.addEventListener('click', () => this.resetPreview());
    },

    async checkStatus() {
        try {
            const resp = await fetch('/api/portrait/status');
            const data = await resp.json();
            const indicator = document.getElementById('portrait-status-indicator');
            const text = document.getElementById('portrait-status-text');
            if (indicator) {
                indicator.className = data.data?.available
                    ? 'status-dot online'
                    : 'status-dot offline';
            }
            if (text) {
                text.textContent = data.data?.available ? 'ComfyUI 在线' : 'ComfyUI 离线';
            }
        } catch {
            const indicator = document.getElementById('portrait-status-indicator');
            const text = document.getElementById('portrait-status-text');
            if (indicator) indicator.className = 'status-dot offline';
            if (text) text.textContent = 'ComfyUI 离线';
        }
    },

    async loadLatestImage() {
        try {
            const resp = await fetch('/api/portrait/latest');
            const data = await resp.json();
            if (data.success && data.data?.image_url) {
                this.showImage(data.data.image_url);
            }
        } catch {
            // 无历史图片时不显示任何内容
        }
    },

    /* ===================== 自定义弹窗逻辑 ===================== */

    async openBuilder() {
        try {
            const resp = await fetch('/api/portrait/options');
            const data = await resp.json();
            if (!data.success || !data.data) {
                this.showError(data.error || '获取选项失败');
                return;
            }
            this.builderData = data.data;
            this.renderBuilder(data.data);
            const overlay = document.getElementById('portrait-builder-overlay');
            if (overlay) overlay.style.display = 'flex';
            this.updatePreview();
        } catch {
            this.showError('网络错误，无法加载选项');
        }
    },

    closeBuilder() {
        const overlay = document.getElementById('portrait-builder-overlay');
        if (overlay) overlay.style.display = 'none';
    },

    /** 计算默认选中：词库 default + 按角色当前地点/活动名匹配覆盖 */
    computeDefaults(opts) {
        const out = {};
        for (const seg of this.SEGMENTS) {
            if (seg.kind === 'vocab') {
                out[seg.key] = Object.assign({}, PORTRAIT_VOCAB[seg.key].default || {});
            }
        }
        // 按女主招牌画风命中画风核心风格（personality_tone/outfit_style 派生）
        if (opts.signature_style) {
            const coreOpts = (PORTRAIT_VOCAB.style.subGroups[0] || {}).options || [];
            if (coreOpts.some(o => o.value === opts.signature_style)) {
                out.style = Object.assign({}, PORTRAIT_VOCAB.style.default || {});
                out.style.core = opts.signature_style;
            }
        }
        return out;
    },

    _matchOption(options, raw) {
        if (!raw) return null;
        for (const o of options) {
            if (o.value === raw) return o.value;
            if (o.alias && o.alias.some(a => raw.indexOf(a) !== -1)) return o.value;
        }
        return null;
    },

    renderBuilder(data) {
        const body = document.getElementById('portrait-builder-body');
        if (!body) return;

        const fixed = data.fixed_segments || {};
        const outfits = data.outfits && data.outfits.length ? data.outfits : [];
        const def = data.defaults || {};
        const defaults = this.computeDefaults(def);

        let html = '';
        html += this._renderTraitCard(data);
        for (const seg of this.SEGMENTS) {
            if (seg.kind === 'vocab') {
                html += this._renderVocabSegment(seg.key, defaults);
            } else if (seg.kind === 'location') {
                if (data.locations && data.locations.length) {
                    html += this._renderLocationSegment(data.locations, def.current_location_id);
                } else {
                    html += this._renderVocabSegment('background', defaults);
                }
            } else if (seg.kind === 'activity') {
                if (data.locations && data.locations.length) {
                    html += this._renderActivitySegment(data.locations, def.current_activity);
                } else {
                    html += this._renderVocabSegment('action', defaults);
                }
            } else if (seg.kind === 'fixed') {
                html += this._renderFixedSegment(seg.key, seg.src, fixed[seg.src] || '');
            } else if (seg.kind === 'editable') {
                let val = fixed[seg.src] || '';
                if (seg.key === 'mood') val = (def && def.mood_phrase) || '';
                html += this._renderEditableSegment(seg.key, seg.src, val);
            } else if (seg.kind === 'outfit') {
                html += this._renderOutfitSegment(outfits, def, data.underwear, data.accessory, data.hairstyles);
            } else if (seg.kind === 'tail') {
                html += this._renderTailSegment();
            }
        }
        body.innerHTML = html;
    },

    _renderVocabSegment(catKey, defaults) {
        const v = PORTRAIT_VOCAB[catKey];
        const def = defaults[catKey] || {};
        let h = `<div class="pb-segment pb-segment-wide" data-key="${catKey}">`;
        h += `<div class="pb-seg-head"><span>${this._esc(v.label)}</span></div>`;
        for (const sg of v.subGroups) {
            h += `<div class="pb-subgroup"><div class="pb-sub-label">${this._esc(sg.label)}</div><div class="pb-chips">`;
            const active = def[sg.key] || null;
            for (const opt of sg.options) {
                const sel = (opt.value === active) ? ' pb-chip-active' : '';
                h += `<button type="button" class="pb-chip${sel}" data-cat="${catKey}" data-sub="${sg.key}" data-value="${this._esc(opt.value)}">${this._esc(opt.value)}</button>`;
            }
            h += `</div></div>`;
        }
        h += `<div class="pb-sub-label">自定义补充</div>`;
        h += `<input class="pb-custom" placeholder="可追加额外描述（将直接拼入提示词）" />`;
        h += `</div>`;
        return h;
    },

    _renderFixedSegment(key, src, value) {
        return `<div class="pb-segment" data-key="${key}">`
            + `<div class="pb-seg-head"><span>${this._esc(this._fixedLabel(src))}</span>`
            + `<span class="pb-fixed-tag">固定</span></div>`
            + `<div class="pb-fixed-val">${this._esc(value)}</div></div>`;
    },

    _renderEditableSegment(key, src, value) {
        // 五官与微表情为长文本，默认 4 行更舒服；其他可编辑段（当前情绪等）保持 2 行
        const rows = (src === 'appearance') ? 4 : 2;
        const placeholder = (src === 'appearance')
            ? '可修改五官与微表情描述（建议保留脸型/眉眼/鼻/唇/肤质等结构化要素）'
            : '可修改文本';
        return `<div class="pb-segment" data-key="${key}">`
            + `<div class="pb-seg-head"><span>${this._esc(this._fixedLabel(src))}</span>`
            + `<span class="pb-fixed-tag">可编辑</span></div>`
            + `<textarea class="pb-editable" rows="${rows}" placeholder="${this._esc(placeholder)}">${this._esc(value)}</textarea>`
            + `</div>`;
    },

    /** 顶部「本角色专属特质」卡：从角色属性自动带入，作为下方各段的按女主默认值来源 */
    _renderTraitCard(data) {
        const fixed = (data && data.fixed_segments) || {};
        const def = (data && data.defaults) || {};
        const tone = def.personality_tone || '';
        // 三段分组：① 身份 / 气质 / 情绪（3列）  ② 外貌（独占一行全宽）  ③ 地点 / 动作 / 画风 / 光影（4列）
        const rowTop = [
            ['身份', fixed.identity_anchor || ''],
            ['气质基调', (fixed.temperament || '') + (tone ? '（' + tone + '）' : '')],
            ['当前情绪', def.mood_phrase || ''],
        ];
        const rowWide = [
            ['外貌', fixed.appearance || ''],
        ];
        const rowBottom = [
            ['地点', def.current_location || ''],
            ['动作', def.current_activity || ''],
            ['招牌画风', def.signature_style || '（通用默认）'],
            ['光影', def.light_mood || ''],
        ];
        const grid = (items, cls) => {
            let g = `<div class="pb-trait-grid ${cls}">`;
            for (const [k, v] of items) {
                g += `<div class="pb-trait-item"><span class="pb-trait-k">${this._esc(k)}</span>`
                    + `<span class="pb-trait-v">${this._esc(v)}</span></div>`;
            }
            return g + `</div>`;
        };
        let h = `<div class="pb-trait-card">`;
        h += `<div class="pb-trait-head">本角色专属特质 · 自动带入下方对应段（千人千面）</div>`;
        h += grid(rowTop, 'pb-trait-grid-3');
        h += grid(rowWide, 'pb-trait-grid-wide');
        h += grid(rowBottom, 'pb-trait-grid-4');
        h += `</div>`;
        return h;
    },

    _fixedLabel(src) {
        return {
            identity_anchor: '3 · 身份锚点',
            appearance: '4 · 五官与微表情',
            temperament: '6 · 气质关键词',
            mood: '7 · 当前情绪',
        }[src] || src;
    },

    _renderOutfitSegment(outfits, def, underwear, accessory, hairstyles) {
        let h = `<div class="pb-segment" data-key="outfit">`;
        h += `<div class="pb-seg-head"><span>5 · 穿搭与造型</span></div>`;

        // 当前穿搭预设（默认无）
        h += `<div class="pb-sub-label">当前穿搭</div>`;
        h += `<select class="pb-outfit-select" data-comp="preset">`;
        h += `<option value="" data-desc="">无 · 不指定穿搭设定</option>`;
        for (const o of outfits) {
            const desc = this._esc(o.description || '');
            h += `<option value="${this._esc(o.name)}" data-desc="${desc}">${this._esc(o.name)}</option>`;
        }
        h += `</select>`;
        h += `<div class="pb-outfit-desc" data-comp="preset"></div>`;

        // 内衣（来自 outfit_components type=underwear，默认无）
        h += `<div class="pb-sub-label">内衣</div>`;
        h += `<select class="pb-outfit-select" data-comp="underwear">`;
        h += `<option value="" data-desc="">无 · 沿用当前穿搭</option>`;
        for (const o of (underwear || [])) {
            const desc = this._esc(o.description || '');
            h += `<option value="${this._esc(o.name)}" data-desc="${desc}">${this._esc(o.name)}</option>`;
        }
        h += `</select>`;
        h += `<div class="pb-outfit-desc" data-comp="underwear"></div>`;

        // 配饰（来自 outfit_components type=accessory，默认无）
        h += `<div class="pb-sub-label">配饰</div>`;
        h += `<select class="pb-outfit-select" data-comp="accessory">`;
        h += `<option value="" data-desc="">无 · 沿用当前穿搭</option>`;
        for (const o of (accessory || [])) {
            const desc = this._esc(o.description || '');
            h += `<option value="${this._esc(o.name)}" data-desc="${desc}">${this._esc(o.name)}</option>`;
        }
        h += `</select>`;
        h += `<div class="pb-outfit-desc" data-comp="accessory"></div>`;

        // 发型（来自 outfit_components type=hairstyle, 共享池 character_id=0，所有人可用，默认无）
        // 自定义美化下拉：鼠标悬停选项即时弹出预览图（data-img 指向 data/character_gen/hair_examples 示例图）；不再显示 description 文字
        const hair = (hairstyles && hairstyles.length) ? hairstyles : [];
        let hairHidden = `<select class="pb-outfit-select pb-hidden" data-comp="hairstyle">`;
        hairHidden += `<option value="" data-img="">无 · 沿用当前穿搭</option>`;
        let hairBtns = '';
        for (const o of hair) {
            const label = `${o.name}${o.style_tags ? '（' + o.style_tags + '）' : ''}`;
            const desc = this._esc(o.description || '');
            const le = this._esc(label);
            const im = this._esc(o.image_url || '');
            hairHidden += `<option value="${le}" data-img="${im}" data-desc="${desc}">${le}</option>`;
            hairBtns += `<button type="button" class="pb-hair-opt" data-value="${le}" data-img="${im}">${le}</button>`;
        }
        hairHidden += `</select>`;
        h += `<div class="pb-sub-label">发型</div>`;
        h += `<div class="pb-hair-wrap" data-comp="hairstyle">`;
        h += `<button type="button" class="pb-hair-trigger">无 · 沿用当前穿搭</button>`;
        h += `<div class="pb-hair-list" hidden>${hairBtns}</div>`;
        h += `<div class="pb-hair-tip" hidden></div>`;
        h += `</div>`;
        h += hairHidden;

        h += `<input class="pb-custom" placeholder="或自定义覆盖（直接填写穿搭描述）" />`;
        h += `</div>`;
        return h;
    },

    /** 发型自定义下拉：选中某项 → 同步隐藏 select 并触发 change（更新预览/描述） */
    selectHairOption(opt) {
        const wrap = opt.closest('.pb-hair-wrap');
        if (!wrap) return;
        const seg = wrap.closest('.pb-segment');
        const hidden = seg ? seg.querySelector('select.pb-outfit-select[data-comp="hairstyle"]') : null;
        const val = opt.dataset.value || '';
        const trigger = wrap.querySelector('.pb-hair-trigger');
        if (trigger) trigger.textContent = val || '无 · 沿用当前穿搭';
        const list = wrap.querySelector('.pb-hair-list');
        if (list) list.hidden = true;
        this.hideHairTip(opt);
        if (hidden) {
            hidden.value = val;
            hidden.dispatchEvent(new Event('change', { bubbles: true }));
        }
    },

    /** 悬停发型项：在选项下方弹出预览图（水平居中对齐，不再贴左覆盖右侧面板），移开由 hideHairTip 隐藏 */
    showHairPreview(opt) {
        const wrap = opt.closest('.pb-hair-wrap');
        if (!wrap) return;
        const tip = wrap.querySelector('.pb-hair-tip');
        if (!tip) return;
        const url = opt.dataset.img || '';
        if (!url) { tip.hidden = true; return; }
        tip.innerHTML = `<img class="pb-hair-prev-img" src="${url}" alt="">`;
        tip.hidden = false;
        const wrapRect = wrap.getBoundingClientRect();
        const optRect = opt.getBoundingClientRect();
        tip.style.top = (optRect.bottom - wrapRect.top + 4) + 'px';
        // 水平居中对齐选项（与 CSS max-width:240px 对应），钳制到 wrap 范围内
        const tipW = 240;
        let left = (optRect.left - wrapRect.left) + optRect.width / 2 - tipW / 2;
        const minL = 0;
        const maxL = Math.max(0, wrapRect.width - tipW);
        left = Math.max(minL, Math.min(left, maxL));
        tip.style.left = left + 'px';
    },

    hideHairTip(opt) {
        const wrap = opt.closest('.pb-hair-wrap');
        if (!wrap) return;
        const tip = wrap.querySelector('.pb-hair-tip');
        if (tip) tip.hidden = true;
    },

    closeHairLists() {
        document.querySelectorAll('.pb-hair-list').forEach(l => { l.hidden = true; });
        document.querySelectorAll('.pb-hair-tip').forEach(t => { t.hidden = true; });
    },

    /** 段10 背景地点：渲染女主专属活动地点下拉（来自 CharacterActivityMap + 预设活动涉及的地点） */
    _renderLocationSegment(locations, defaultId) {
        let h = `<div class="pb-segment" data-key="background">`;
        h += `<div class="pb-seg-head"><span>${this._esc(PORTRAIT_VOCAB.background.label)}</span>`
           + `<span class="pb-fixed-tag">女主专属</span></div>`;
        h += `<div class="pb-sub-label">活动地点（来自女主专属地图）</div>`;
        h += `<select class="pb-outfit-select" data-comp="location">`;
        h += `<option value="" data-desc="">无 · 不指定地点</option>`;
        for (const loc of locations) {
            const sel = (loc.id === defaultId) ? ' selected' : '';
            h += `<option value="${this._esc(loc.id)}" data-desc="${this._esc(loc.name)}"${sel}>${this._esc(loc.name)}</option>`;
        }
        h += `</select>`;
        h += `<div class="pb-outfit-desc" data-comp="location"></div>`;
        h += `<input class="pb-custom" placeholder="或自定义地点（直接填写，将覆盖上方选择）" />`;
        h += `</div>`;
        return h;
    },

    /** 段11 动作：渲染女主专属活动下拉（来自其各地点可用活动并集，千人千面） */
    _renderActivitySegment(locations, defaultName) {
        const actMap = {};
        for (const loc of locations) {
            for (const a of (loc.activities || [])) {
                if (a && a.name) actMap[a.name] = a.comfyui_pose || '';
            }
        }
        const actNames = Object.keys(actMap);
        let h = `<div class="pb-segment" data-key="action">`;
        h += `<div class="pb-seg-head"><span>${this._esc(PORTRAIT_VOCAB.action.label)}</span>`
           + `<span class="pb-fixed-tag">女主专属</span></div>`;
        h += `<div class="pb-sub-label">当前动作（来自女主专属活动）</div>`;
        h += `<select class="pb-outfit-select" data-comp="activity">`;
        h += `<option value="" data-desc="">无 · 不指定动作</option>`;
        for (const name of actNames) {
            const sel = (name === defaultName) ? ' selected' : '';
            const desc = this._esc(actMap[name] || '');
            h += `<option value="${this._esc(name)}" data-desc="${desc}"${sel}>${this._esc(name)}</option>`;
        }
        h += `</select>`;
        h += `<div class="pb-outfit-desc" data-comp="activity"></div>`;
        h += `<input class="pb-custom" placeholder="或自定义动作（直接填写）" />`;
        h += `</div>`;
        return h;
    },

    /** 段12 自定义追加：用户输入任意文本，生成时追加到提示词最末尾 */
    _renderTailSegment() {
        return `<div class="pb-segment pb-segment-wide" data-key="custom_tail">`
            + `<div class="pb-seg-head"><span>12 · 自定义追加</span>`
            + `<span class="pb-fixed-tag">追加至末尾</span></div>`
            + `<textarea class="pb-tail-input" rows="3" placeholder="在此输入任意自定义描述，生成时将追加到提示词最末尾（例如：手持一束向日葵、背景飘落樱花、4K 高清、电影感构图）"></textarea>`
            + `</div>`;
    },

    /** 小类内单选、跨小类多选；再次点击已选项可取消 */
    toggleChip(chip) {
        const cat = chip.dataset.cat;
        const sub = chip.dataset.sub;
        const wasActive = chip.classList.contains('pb-chip-active');
        const group = document.querySelectorAll(
            `.pb-chip[data-cat="${cat}"][data-sub="${sub}"]`
        );
        group.forEach(c => c.classList.remove('pb-chip-active'));
        if (!wasActive) chip.classList.add('pb-chip-active');
        this.updatePreview();
    },

    _card(key) {
        return document.querySelector(`.pb-segment[data-key="${key}"]`);
    },

    _optPrompt(catKey, subKey, value) {
        const v = PORTRAIT_VOCAB[catKey];
        if (!v) return '';
        for (const sg of v.subGroups) {
            if (sg.key !== subKey) continue;
            const opt = sg.options.find(o => o.value === value);
            if (opt) return opt.prompt;
        }
        return '';
    },

    /** 按 10 段顺序拼中文增强提示词 */
    assemblePrompt() {
        const parts = [];
        for (const seg of this.SEGMENTS) {
            const card = this._card(seg.key);
            if (!card) continue;

            if (seg.kind === 'vocab') {
                const v = PORTRAIT_VOCAB[seg.key];
                for (const sg of v.subGroups) {
                    const chip = card.querySelector(
                        `.pb-chip[data-sub="${sg.key}"].pb-chip-active`
                    );
                    if (chip) {
                        const p = this._optPrompt(seg.key, sg.key, chip.dataset.value);
                        if (p) parts.push(p);
                    }
                }
                const custom = card.querySelector('.pb-custom');
                if (custom && custom.value.trim()) parts.push(custom.value.trim());
            } else if (seg.kind === 'fixed') {
                const fv = card.querySelector('.pb-fixed-val');
                if (fv) {
                    const t = fv.textContent.trim();
                    if (t) parts.push(t);
                }
            } else if (seg.kind === 'editable') {
                const ta = card.querySelector('.pb-editable');
                if (ta && ta.value.trim()) parts.push(ta.value.trim());
            } else if (seg.kind === 'outfit' || seg.kind === 'location' || seg.kind === 'activity') {
                const custom = card.querySelector('.pb-custom');
                if (custom && custom.value.trim()) {
                    parts.push(custom.value.trim());
                } else {
                    const bits = [];
                    card.querySelectorAll('.pb-outfit-select').forEach(sel => {
                        if (!sel.value) return;
                        const opt = sel.options[sel.selectedIndex];
                        const desc = opt ? (opt.dataset.desc || '').trim() : '';
                        bits.push(desc || sel.value);
                    });
                    if (bits.length) parts.push(bits.join('，'));
                }
            } else if (seg.kind === 'tail') {
                // 第十二段：用户自定义内容，追加到提示词最末尾
                const ta = card.querySelector('.pb-tail-input');
                if (ta && ta.value.trim()) parts.push(ta.value.trim());
            }
        }
        return parts.filter(Boolean).join('，');
    },

    updatePreview() {
        const el = document.getElementById('portrait-prompt-preview');
        if (!el) return;
        el.value = this.assemblePrompt();
    },

    /** 重新按当前选项组装预览（预览框为只读，此按钮仅用于刷新） */
    resetPreview() {
        const el = document.getElementById('portrait-prompt-preview');
        if (!el) return;
        el.value = this.assemblePrompt();
    },

    async onBuilderGenerate() {
        const prev = document.getElementById('portrait-prompt-preview');
        const prompt = prev ? (prev.value || '').trim() : this.assemblePrompt();
        const pixelEl = document.getElementById('pb-pixel');
        const style = (pixelEl && pixelEl.checked) ? 'pixel_art' : null;
        this.closeBuilder();
        await this.generate(prompt, style);
    },

    _esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },

    /* ===================== 生成 ===================== */

    /**
     * 提交 ComfyUI 生成肖像。
     * prompt 为空 → 后端自动拼角色状态提示词；style='pixel_art' → 启用像素 LoRA。
     */
    async generate(prompt = null, style = null) {
        if (this.loading) return;
        this.loading = true;
        this.setGeneratingState(true);

        try {
            const body = {};
            if (prompt) body.prompt = prompt;
            if (style) body.style = style;

            const resp = await fetch('/api/portrait/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });

            const data = await resp.json();
            if (data.success && data.data?.image_url) {
                this.showImage(data.data.image_url);
                const display = document.getElementById('portrait-display');
                const wrap = document.getElementById('portrait-display-wrap');
                const label = document.querySelector('.avatar-label');
                if (display) display.classList.remove('photo-hidden');
                if (wrap) wrap.classList.remove('photo-hidden');
                if (label) label.classList.add('photo-shown');
            } else {
                this.showError(data.error || '生成失败，请检查 ComfyUI 是否运行');
            }
        } catch (err) {
            this.showError('网络错误，无法连接后端');
        } finally {
            this.loading = false;
            this.setGeneratingState(false);
        }
    },

    setGeneratingState(active) {
        const display = document.getElementById('portrait-display');
        const btn = document.getElementById('btn-generate-portrait');
        if (active) {
            if (display) {
                display.innerHTML = `
                    <div class="portrait-generating">
                        <div class="generating-spinner"></div>
                        <span>AI 绘制中...</span>
                        <span class="generating-hint">若 ComfyUI 未运行将自动启动，首次约需 1-2 分钟，请耐心等待</span>
                    </div>`;
            }
            if (btn) btn.disabled = true;
        } else {
            if (btn) btn.disabled = false;
            this._refreshImportUi();
        }
    },

    showImage(url) {
        const display = document.getElementById('portrait-display');
        if (display) {
            display.innerHTML = `<img src="${url}" class="portrait-result" alt="角色肖像" />`;
        }
        this._refreshImportUi();
    },

    showError(msg) {
        const display = document.getElementById('portrait-display');
        if (display) {
            display.innerHTML = `
                <div class="portrait-error">
                    <span class="error-icon">!</span>
                    <span>${msg}</span>
                </div>`;
        }
        this._refreshImportUi();
    },

    /* ===================== 头像导入 / 历史回滚 ===================== */

    /** 仅当肖像区确有实际图片时，显示右下角导入按钮与历史按钮 */
    _refreshImportUi() {
        const display = document.getElementById('portrait-display');
        const img = display && display.querySelector('img.portrait-result');
        const show = !!img;
        const imp = document.getElementById('btn-import-avatar');
        const hist = document.getElementById('btn-avatar-history');
        if (imp) imp.hidden = !show;
        if (hist) hist.hidden = !show;
        if (!show) this._closeHistoryMenu();
    },

    /** 把当前肖像图导入为聊天头像 */
    async importAvatar() {
        const display = document.getElementById('portrait-display');
        const img = display && display.querySelector('img.portrait-result');
        if (!img) return;
        const url = img.getAttribute('src');
        try {
            const resp = await fetch('/api/character/set-avatar-from-portrait', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ portrait_url: url }),
            });
            const data = await resp.json();
            if (data.success) {
                this._applyAvatar(data.avatar);
                Toast.show('已导入为聊天头像', 'success');
                this._flashTopButtons();
            } else {
                Toast.show(data.error || '导入失败', 'error');
            }
        } catch (e) {
            Toast.show('网络错误，无法连接后端', 'error');
        }
    },

    /** 写入 App.character.avatar 并刷新聊天面板已有的头像 img（带缓存破坏） */
    _applyAvatar(avatarUrl) {
        if (App.character) App.character.avatar = avatarUrl;
        const name = App.character && App.character.name;
        document.querySelectorAll('img.message-avatar').forEach((im) => {
            if (!name || im.alt === name) im.src = avatarUrl;
        });
    },

    /** 顶部「生成肖像 / 自定义」按钮组短暂高亮，提示已生效到聊天头像 */
    _flashTopButtons() {
        ['btn-generate-portrait', 'btn-custom-portrait'].forEach((id) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.classList.add('avatar-synced');
            setTimeout(() => el.classList.remove('avatar-synced'), 1600);
        });
    },

    /** 拉取并切换历史菜单显隐 */
    async toggleHistoryMenu() {
        const menu = document.getElementById('avatar-history-menu');
        if (!menu) return;
        if (!menu.hidden) { this._closeHistoryMenu(); return; }
        try {
            const resp = await fetch('/api/character/avatar-history');
            const data = await resp.json();
            const history = (data.success && data.history) || [];
            if (!history.length) {
                menu.innerHTML = `<div class="portrait-history-empty">暂无历史头像</div>`;
            } else {
                menu.innerHTML = history.map((h) => `
                    <div class="portrait-history-item" data-ts="${h.ts}">
                        <img src="${h.path}" alt="历史头像" />
                        <span class="portrait-history-ts">${this._fmtTs(h.ts)}</span>
                    </div>`).join('');
                menu.querySelectorAll('.portrait-history-item').forEach((item) => {
                    item.addEventListener('click', (e) => {
                        e.stopPropagation();
                        this.rollbackAvatar(item.dataset.ts);
                    });
                });
            }
            menu.hidden = false;
        } catch (e) {
            menu.innerHTML = `<div class="portrait-history-empty">加载失败</div>`;
            menu.hidden = false;
        }
    },

    _closeHistoryMenu() {
        const menu = document.getElementById('avatar-history-menu');
        if (menu) menu.hidden = true;
    },

    _fmtTs(ts) {
        try {
            const d = new Date(Number(ts));
            const p = (n) => String(n).padStart(2, '0');
            return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
        } catch (e) {
            return '';
        }
    },

    /** 回滚到指定历史快照 */
    async rollbackAvatar(ts) {
        try {
            const resp = await fetch('/api/character/rollback-avatar', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ts: Number(ts) }),
            });
            const data = await resp.json();
            if (data.success) {
                this._applyAvatar(data.avatar);
                this._closeHistoryMenu();
                Toast.show('已回滚到该历史头像', 'success');
            } else {
                Toast.show(data.error || '回滚失败', 'error');
            }
        } catch (e) {
            Toast.show('网络错误，无法连接后端', 'error');
        }
    },

    /**
     * 更新角色信息（接管原 AvatarPanel.update）
     */
    update(character) {
        this.updateOutfit(character);
        this.updateLabel(character);
        this.loadLatestImage();
    },

    updateOutfit(character) {
        const outfit = character?.current_outfit;
        const nameEl = document.getElementById('outfit-name');
        const descEl = document.getElementById('outfit-desc');
        const detailsEl = document.getElementById('outfit-details');

        if (!nameEl || !descEl || !detailsEl) return;

        if (outfit && outfit.name) {
            nameEl.textContent = outfit.name;
            descEl.textContent = outfit.description || '';
            const parts = [];
            const comps = outfit.components || {};
            const typeLabels = {
                top: '上衣', bottom: '下装', outer: '外套', shoes: '鞋',
                accessory: '配饰', hairstyle: '发型',
                underwear: '内衣', sleepwear: '睡衣',
            };
            const displayOrder = ['top', 'bottom', 'outer', 'shoes', 'accessory', 'hairstyle', 'underwear', 'sleepwear'];
            for (const key of displayOrder) {
                const v = comps[key];
                if (!v) continue;
                const label = typeLabels[key] || key;
                if (Array.isArray(v) && v.length) {
                    parts.push(`${label}：${v.map(a => a.name).join('、')}`);
                } else if (v && typeof v === 'object' && v.name) {
                    parts.push(`${label}：${v.name}`);
                }
            }
            detailsEl.textContent = parts.join(' | ');
        } else {
            nameEl.textContent = '未换装';
            descEl.textContent = '';
            detailsEl.textContent = '';
        }
    },

    updateLabel(character) {
        const avatarName = document.getElementById('avatar-name');
        const avatarAge = document.getElementById('avatar-age');
        const avatarDream = document.getElementById('avatar-dreams');
        if (avatarName && character?.name) avatarName.textContent = character.name;
        if (avatarAge && character?.age) avatarAge.textContent = `${character.age}岁`;
        if (avatarDream && character?.dream) avatarDream.textContent = character.dream;
    }
};
