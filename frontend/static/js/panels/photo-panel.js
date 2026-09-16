/**
 * 聊天生图前端面板（P6 + P8）
 * - 输入区「加号」菜单：手动触发 6 种拍照场景
 * - 消息流内渲染照片卡片（pending 骨架 → 轮询 → done 图片）
 * - 灯箱：图文成对（scene_memo / vlm_caption）
 * - share_recall：女主主动回忆时渲染真实旧照画廊
 *
 * 由 dialogue-panel.js 在收到 /api/dialogue 响应后调用 PhotoPanel.handleDialogueResult(data)。
 */
const PhotoPanel = {
    // 场景中文名 + 图标（Material Symbols）
    SCENES: [
        { key: 'photo_take',    cn: '拍张照',   icon: 'photo_camera' },
        { key: 'selfie',        cn: '来张自拍', icon: 'ar_on_you' },
        { key: 'activity_shot', cn: '活动特写', icon: 'directions_run' },
        { key: 'outfit_change', cn: '换装展示', icon: 'checkroom' },
        { key: 'scene_freeze',  cn: '场景定格', icon: 'filter_hdr' },
        { key: 'gift_photo',    cn: '礼物合影', icon: 'featured_seasonal_and_gifts' },
    ],
    SCENE_CN: {
        photo_take: '拍照', selfie: '自拍', activity_shot: '活动特写',
        outfit_change: '换装展示', scene_freeze: '场景定格', gift_photo: '礼物合影',
        share_new: '主动分享', share_recall: '旧照回忆',
    },
    _pollTimers: {},
    _lightboxRecords: [],
    _lightboxIndex: -1,

    init() {
        this._wirePlusMenu();
        this._wireDesktopPlusMenu();
        this._injectMobilePhotoMenu();
        this._ensureLightbox();
    },

    /* ── 加号菜单（桌面 + 移动 双入口共用）──────── */
    _wirePlusMenu() {
        this._wireOneMenu('btn-photo-plus', 'photo-plus-menu');
        this._wireOneMenu('btn-photo-plus-mobile', 'photo-plus-menu-mobile');
        // 点击空白处关闭所有菜单（桌面 / 移动）
        document.addEventListener('click', (e) => {
            const inside = e.target.closest('#photo-plus-menu, #photo-plus-menu-mobile');
            const onBtn = e.target.closest('#btn-photo-plus, #btn-photo-plus-mobile');
            if (!inside && !onBtn) {
                document.querySelectorAll('.photo-plus-menu').forEach(m => m.classList.remove('show'));
            }
        });
    },

    _wireOneMenu(btnId, menuId) {
        const btn = document.getElementById(btnId);
        const menu = document.getElementById(menuId);
        if (!btn || !menu) return;
        menu.innerHTML = this.SCENES.map(s =>
            `<button class="photo-menu-item" data-scene="${s.key}">
                <span class="material-symbols-outlined">${s.icon}</span>
                <span>${s.cn}</span>
            </button>`).join('');
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            menu.classList.toggle('show');
        });
        menu.addEventListener('click', (e) => {
            const item = e.target.closest('.photo-menu-item');
            if (!item) return;
            menu.classList.remove('show');
            this.triggerManual(item.getAttribute('data-scene'));
        });
    },

    /* ── 桌面端统一加号面板（时钟/拍照/相册/情感快捷合并）──────── */
    _wireDesktopPlusMenu() {
        const btn = document.getElementById('btn-chat-plus');
        const menu = document.getElementById('chat-plus-menu');
        const sceneGrid = document.getElementById('chat-plus-scenes');
        if (!btn || !menu || !sceneGrid) return;

        sceneGrid.innerHTML = this.SCENES.map(s =>
            `<button class="chat-plus-item photo-scene-item" data-scene="${s.key}" type="button">
                <span class="material-symbols-outlined">${s.icon}</span>
                <span>${s.cn}</span>
            </button>`).join('');

        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            menu.classList.toggle('show');
        });

        menu.addEventListener('click', (e) => {
            const sceneItem = e.target.closest('.photo-scene-item');
            if (sceneItem) {
                menu.classList.remove('show');
                this.triggerManual(sceneItem.getAttribute('data-scene'));
                return;
            }
            const albumItem = e.target.closest('#chat-plus-album');
            if (albumItem) {
                menu.classList.remove('show');
                this.openAlbum();
                return;
            }
            const tlItem = e.target.closest('#chat-plus-tl');
            if (tlItem) {
                menu.classList.remove('show');
                if (typeof DialoguePanel !== 'undefined' && DialoguePanel._openTimeLocationPicker) {
                    DialoguePanel._openTimeLocationPicker('time');
                }
            }
        });

        document.addEventListener('click', (e) => {
            const inside = e.target.closest('#chat-plus-menu');
            const onBtn = e.target.closest('#btn-chat-plus');
            if (!inside && !onBtn) menu.classList.remove('show');
        });
    },

    /* ── 移动端加号面板补拍照场景 + 相册入口 ─────── */
    _injectMobilePhotoMenu() {
        const grid = document.getElementById('mobile-photo-scenes');
        const albumBtn = document.getElementById('mobile-open-album');
        if (!grid) return;
        grid.innerHTML = this.SCENES.map(s =>
            `<button class="quick-rel-btn photo-scene-item" data-scene="${s.key}" type="button">
                <span class="material-symbols-outlined">${s.icon}</span>
                <span class="rel-label">${s.cn}</span>
            </button>`).join('');
        grid.addEventListener('click', (e) => {
            const item = e.target.closest('.photo-scene-item');
            if (!item) return;
            const mobileMenu = document.getElementById('mobile-chat-menu');
            if (mobileMenu) mobileMenu.classList.remove('show');
            this.triggerManual(item.getAttribute('data-scene'));
        });
        if (albumBtn) {
            albumBtn.addEventListener('click', () => {
                const mobileMenu = document.getElementById('mobile-chat-menu');
                if (mobileMenu) mobileMenu.classList.remove('show');
                this.openAlbum();
            });
        }
    },

    /* ── 手动触发（加号菜单）───────────────────── */
    async triggerManual(sceneType) {
        let gift = null, outfitName = null;
        if (sceneType === 'gift_photo') {
            gift = prompt('要送她什么？（例如：一束红玫瑰）', '一束花');
            if (gift === null) return;
        }
        if (sceneType === 'outfit_change') {
            this.openOutfitPicker();
            return;
        }
        try {
            const resp = await fetch('/api/photo/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    scene_type: sceneType, gift, outfit_name: outfitName,
                    game_day: App.gameDay, game_hour: App.gameHour,
                    game_minute: App.gameMinute,
                })
            });
            const data = await resp.json();
            if (data.success && data.record) {
                this.renderCard(data.record);
            } else {
                const reasonCN = {
                    status_low: '她现在状态不太好，不想拍照',
                    busy: '上一张还在生成中，稍等一下',
                    round_cooldown: '刚拍过啦，歇一会儿再拍',
                    daily_cap: '今天拍得够多啦，明天再来吧',
                }[data.error] || (data.reject_context || '现在不方便拍照');
                App.announce(reasonCN, '📷');
            }
        } catch (err) {
            console.error('[PhotoPanel] 手动触发失败', err);
            App.announce('生图请求失败，请检查后端 / ComfyUI', '📷');
        }
    },

    /* ── 换装展示：穿搭选择器（复用角色肖像自定义穿搭逻辑）──── */
    _ensureOutfitPicker() {
        if (document.getElementById('outfit-pick-modal')) return;
        const m = document.createElement('div');
        m.id = 'outfit-pick-modal';
        m.className = 'outfit-pick-modal';
        m.style.display = 'none';
        m.innerHTML = `
            <div class="outfit-pick-backdrop"></div>
            <div class="outfit-pick-body">
                <div class="outfit-pick-head">
                    <span><span class="material-symbols-outlined">checkroom</span> 换装展示 · 选择穿搭</span>
                    <button class="outfit-pick-close" id="outfit-pick-close" title="关闭">&times;</button>
                </div>
                <div class="outfit-pick-sub">选择整套穿搭则整体替换；选择内衣等单件则只换对应部件、保留其余穿搭</div>
                <select class="pb-outfit-select" id="outfit-pick-select" data-comp="preset">
                    <option value="" data-desc="">无 · 用当前穿搭</option>
                </select>
                <div class="pb-outfit-desc" id="outfit-pick-desc"></div>
                <input class="pb-custom" id="outfit-pick-custom" placeholder="或自定义覆盖（直接填写穿搭描述，留空则用上方选择）" />
                <div class="outfit-pick-footer">
                    <button class="btn-cancel" id="outfit-pick-cancel" type="button">取消</button>
                    <button class="btn-generate" id="outfit-pick-gen" type="button">生成换装照</button>
                </div>
            </div>`;
        document.body.appendChild(m);
    },

    async openOutfitPicker() {
        this._ensureOutfitPicker();
        const modal = document.getElementById('outfit-pick-modal');
        const sel = modal.querySelector('#outfit-pick-select');
        const desc = modal.querySelector('#outfit-pick-desc');
        const custom = modal.querySelector('#outfit-pick-custom');
        const genBtn = modal.querySelector('#outfit-pick-gen');
        sel.innerHTML = '<option value="" data-desc="">按地点/天气/季节规则选择</option>';
        desc.textContent = '';
        custom.value = '';
        genBtn.disabled = false;
        try {
            const resp = await fetch('/api/photo/outfits').then(r => r.json());
            const outfits = (resp.success && resp.outfits) || [];
            if (outfits.length) {
                const normal = outfits.filter(o => o.kind !== 'underwear');
                const underwear = outfits.filter(o => o.kind === 'underwear');
                const group = (label, rows) => rows.length
                    ? `<optgroup label="${label}">${rows.map(o =>
                        `<option value="${this._esc(o.name)}" data-kind="${o.kind || 'preset'}" data-desc="${this._esc(o.description || '')}">${this._esc(o.name)}</option>`
                    ).join('')}</optgroup>` : '';
                sel.insertAdjacentHTML('beforeend',
                    group('角色专属普通穿搭', normal) + group('角色专属内衣（局部替换）', underwear));
            }
        } catch (e) {
            console.error('[PhotoPanel] 拉取穿搭列表失败', e);
        }
        // 选中即更新描述预览
        sel.onchange = () => {
            const opt = sel.selectedOptions[0];
            desc.textContent = opt ? (opt.dataset.desc || '') : '';
        };
        const close = () => { modal.style.display = 'none'; };
        modal.querySelector('#outfit-pick-close').onclick = close;
        modal.querySelector('#outfit-pick-cancel').onclick = close;
        modal.querySelector('.outfit-pick-backdrop').onclick = close;
        genBtn.onclick = async () => {
            const name = (custom.value || '').trim() || sel.value;
            close();
            await this._generateOutfitPhoto(name);
        };
        modal.style.display = 'flex';
    },

    async _generateOutfitPhoto(outfitName) {
        try {
            const resp = await fetch('/api/photo/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    scene_type: 'outfit_change', outfit_name: outfitName,
                    activity_text: (document.getElementById('outfit-pick-custom')?.value || ''),
                    game_day: App.gameDay, game_hour: App.gameHour,
                    game_minute: App.gameMinute,
                })
            });
            const data = await resp.json();
            if (data.success && data.record) {
                this.renderCard(data.record);
            } else {
                const reasonCN = {
                    status_low: '她现在状态不太好，不想拍照',
                    busy: '上一张还在生成中，稍等一下',
                    round_cooldown: '刚拍过啦，歇一会儿再拍',
                    daily_cap: '今天拍得够多啦，明天再来吧',
                }[data.error] || (data.reject_context || '现在不方便拍照');
                App.announce(reasonCN, '📷');
            }
        } catch (err) {
            console.error('[PhotoPanel] 换装照生成失败', err);
            App.announce('生图请求失败，请检查后端 / ComfyUI', '📷');
        }
    },

    /* ── 对话响应处理入口 ──────────────────────── */
    handleDialogueResult(data) {
        if (!data) return;
        if (data.photo) this.renderCard(data.photo);
        if (data.recall_photos && data.recall_photos.length) this.renderRecallGallery(data.recall_photos);
        if (data.photo_reject) App.announce(String(data.photo_reject).replace(/[\[\]系统：]/g, ''), '📷');
    },

    /* ── 刷新重载：按 photo_id 回查并还原照片卡片（P0-1）──── */
    async restoreCard(photoId, gd, gt) {
        if (!photoId) return;
        try {
            const resp = await fetch(`/api/photo/record/${photoId}`).then(r => r.json()).catch(() => null);
            if (resp && resp.success && resp.record) {
                this.renderCard(resp.record, gd, gt);
            }
        } catch (e) { /* 还原失败不阻塞对话流 */ }
    },

    /* ── 渲染照片卡片（pending → 轮询 → done）──── */
    /* 由游戏时间(天/时分秒)计算排序键，无法定位时间则返回 null */
    _sortKeyOf(el) {
        const gd = el.getAttribute('data-gd');
        if (gd === null || gd === '') return null;
        const gt = el.getAttribute('data-gt') || '';
        return [parseInt(gd, 10) || 0, gt];
    },

    /* 按游戏时间把照片卡片插入到正确位置：找到第一条时间严格晚于当前卡片的消息并插在其前；
       找不到（已是末尾或当前最晚）则追加到末尾。无时间键时调用方直接 append。 */
    _insertPhotoByTime(container, el, gd, gt) {
        const cur = [parseInt(gd, 10) || 0, gt || ''];
        const kids = Array.from(container.children);
        for (const k of kids) {
            const kk = this._sortKeyOf(k);
            if (kk && (kk[0] > cur[0] || (kk[0] === cur[0] && kk[1] > cur[1]))) {
                container.insertBefore(el, k);
                return;
            }
        }
        container.appendChild(el);
    },

    /* 渲染照片卡片（pending → 轮询 → done）。gd/gt 为游戏日/时间，用于按时间线归位插入 */
    renderCard(rec, gd, gt) {
        if (!rec || !rec.id) return;
        const cardHTML = this._buildCardHTML(rec);
        const containers = (DialoguePanel._getAllMsgContainers
            ? DialoguePanel._getAllMsgContainers()
            : [document.getElementById('dialogue-messages')]).filter(Boolean);
        const hasTime = gd !== null && gd !== undefined && gd !== '' && !isNaN(parseInt(gd, 10));
        containers.forEach(c => {
            const wrap = document.createElement('div');
            wrap.className = 'msg character photo-msg';
            wrap.setAttribute('data-photo-card', rec.id);
            if (hasTime) {
                wrap.setAttribute('data-gd', String(gd));
                if (gt) wrap.setAttribute('data-gt', String(gt));
            }
            wrap.innerHTML = cardHTML;
            if (hasTime) {
                this._insertPhotoByTime(c, wrap, gd, gt);
            } else {
                c.appendChild(wrap);
            }
            c.scrollTop = c.scrollHeight;
        });
        this._bindCardClick(rec.id);
        if (rec.status === 'pending') this._startPoll(rec.id);
    },

    _buildCardHTML(rec) {
        const cn = rec.scene_type_cn || this.SCENE_CN[rec.scene_type] || '照片';
        if (rec.status === 'done' && rec.image_url) {
            const memo = rec.scene_memo || '';
            return `
                <div class="photo-card done" data-photo-id="${rec.id}">
                    <div class="photo-card-tag"><span class="material-symbols-outlined">image</span>${cn}</div>
                    <img class="photo-card-img" src="${rec.image_url}" alt="${cn}" loading="lazy">
                    ${memo ? `<div class="photo-card-memo">${this._esc(memo)}</div>` : ''}
                </div>`;
        }
        if (rec.status === 'failed') {
            return `<div class="photo-card failed"><span class="material-symbols-outlined">broken_image</span>${cn}生成失败</div>`;
        }
        // pending / drift(仍显示图)
        return `
            <div class="photo-card pending" data-photo-id="${rec.id}">
                <div class="photo-card-tag"><span class="material-symbols-outlined">hourglass_top</span>${cn}·生成中</div>
                <div class="photo-skeleton"><div class="photo-skeleton-shimmer"></div>
                    <span class="material-symbols-outlined">photo_camera</span></div>
            </div>`;
    },

    _startPoll(id, tries = 0) {
        if (tries > 60) { this._updateCard(id, { id, status: 'failed' }); return; }  // 最多 ~120s
        this._pollTimers[id] = setTimeout(async () => {
            try {
                const resp = await fetch(`/api/photo/record/${id}`);
                const data = await resp.json();
                if (data.success && data.record) {
                    const st = data.record.status;
                    if (st === 'pending') { this._startPoll(id, tries + 1); }
                    else { this._updateCard(id, data.record); }  // done/failed/drift
                } else { this._startPoll(id, tries + 1); }
            } catch (e) { this._startPoll(id, tries + 1); }
        }, 2000);
    },

    _updateCard(id, rec) {
        if (this._pollTimers[id]) { clearTimeout(this._pollTimers[id]); delete this._pollTimers[id]; }
        const html = this._buildCardHTML(rec);
        document.querySelectorAll(`[data-photo-card="${id}"]`).forEach(wrap => {
            wrap.innerHTML = html;
        });
        this._bindCardClick(id);
    },

    _bindCardClick(id) {
        document.querySelectorAll(`[data-photo-card="${id}"] .photo-card.done`).forEach(card => {
            card.style.cursor = 'zoom-in';
            card.onclick = async () => {
                const resp = await fetch(`/api/photo/record/${id}`).then(r => r.json()).catch(() => null);
                if (resp && resp.success) this.openLightbox(resp.record);
            };
        });
    },

    /* ── share_recall 旧照画廊 ─────────────────── */
    renderRecallGallery(photos) {
        const items = photos.filter(p => p.image_url).slice(0, 6);
        if (!items.length) return;
        const inner = items.map(p =>
            `<img class="recall-thumb" src="${p.image_url}" data-rid="${p.id}" alt="${p.scene_type_cn || ''}" loading="lazy">`
        ).join('');
        const html = `
            <div class="photo-recall">
                <div class="photo-recall-title"><span class="material-symbols-outlined">photo_library</span>她翻出了以前的照片</div>
                <div class="photo-recall-grid">${inner}</div>
            </div>`;
        const containers = (DialoguePanel._getAllMsgContainers
            ? DialoguePanel._getAllMsgContainers()
            : [document.getElementById('dialogue-messages')]).filter(Boolean);
        containers.forEach(c => {
            const wrap = document.createElement('div');
            wrap.className = 'msg character photo-msg';
            wrap.innerHTML = html;
            c.appendChild(wrap);
            c.scrollTop = c.scrollHeight;
        });
        // 点击缩略图 → 灯箱
        document.querySelectorAll('.recall-thumb').forEach(t => {
            t.onclick = async () => {
                const rid = t.getAttribute('data-rid');
                const resp = await fetch(`/api/photo/record/${rid}`).then(r => r.json()).catch(() => null);
                if (resp && resp.success) this.openLightbox(resp.record);
            };
        });
    },

    /* ── 灯箱 ──────────────────────────────────── */
    _ensureLightbox() {
        if (document.getElementById('photo-lightbox')) return;
        const box = document.createElement('div');
        box.id = 'photo-lightbox';
        box.className = 'photo-lightbox';
        box.style.display = 'none';
        box.innerHTML = `
            <div class="photo-lightbox-backdrop"></div>
            <div class="photo-lightbox-body">
                <div class="photo-lightbox-toolbar">
                    <div class="lb-tools-left">
                        <button class="photo-lightbox-close" title="关闭">&times;</button>
                    </div>
                    <div class="lb-tools-right">
                        <a class="photo-lb-btn" id="photo-lb-download" title="下载原图" download target="_blank">
                            <span class="material-symbols-outlined">download</span></a>
                        <button class="photo-lb-btn" id="photo-lb-fav" title="收藏">
                            <span class="material-symbols-outlined">star</span></button>
                        <button class="photo-lb-btn" id="photo-lb-recaption" title="重新描述（VLM 回看）">
                            <span class="material-symbols-outlined">auto_awesome</span></button>
                        <button class="photo-lb-btn photo-lb-del" id="photo-lb-delete" title="删除照片">
                            <span class="material-symbols-outlined">delete</span></button>
                    </div>
                </div>
                <button class="photo-lightbox-nav photo-lightbox-prev" title="上一张" aria-label="上一张">‹</button>
                <img class="photo-lightbox-img" src="" alt="">
                <button class="photo-lightbox-nav photo-lightbox-next" title="下一张" aria-label="下一张">›</button>
                <div class="photo-lightbox-meta">
                    <div class="photo-lightbox-tag"></div>
                    <div class="photo-lightbox-memo"></div>
                    <div class="photo-lightbox-vlm"></div>
                </div>
            </div>`;
        document.body.appendChild(box);
        box.querySelector('.photo-lightbox-close').onclick = () => this.closeLightbox();
        box.querySelector('.photo-lightbox-backdrop').onclick = () => this.closeLightbox();
        box.querySelector('.photo-lightbox-prev').onclick = (e) => {
            e.stopPropagation();
            this._showLightboxIndex(this._lightboxIndex - 1);
        };
        box.querySelector('.photo-lightbox-next').onclick = (e) => {
            e.stopPropagation();
            this._showLightboxIndex(this._lightboxIndex + 1);
        };
        box.querySelector('.photo-lightbox-img').onclick = (e) => {
            e.stopPropagation();
            const rect = e.currentTarget.getBoundingClientRect();
            const isPrev = e.clientX < rect.left + rect.width / 2;
            this._showLightboxIndex(this._lightboxIndex + (isPrev ? -1 : 1));
        };
        box.addEventListener('click', (e) => {
            if (e.target === box) this.closeLightbox();
        });
        box.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowLeft') this._showLightboxIndex(this._lightboxIndex - 1);
            if (e.key === 'ArrowRight') this._showLightboxIndex(this._lightboxIndex + 1);
            if (e.key === 'Escape') this.closeLightbox();
        });
        box.querySelector('#photo-lb-download').onclick = () => {
            if (this._currentRec && this._currentRec.image_url) {
                const a = box.querySelector('#photo-lb-download');
                a.href = this._currentRec.image_url;
                a.download = `photo_${this._currentRec.id}.png`;
            }
        };
        box.querySelector('#photo-lb-fav').onclick = () => this._toggleFavorite();
        box.querySelector('#photo-lb-recaption').onclick = () => this._recaptionCurrent();
        box.querySelector('#photo-lb-delete').onclick = () => this.deletePhoto(this._currentRecId);
    },

    async _toggleFavorite() {
        if (!this._currentRecId) return;
        try {
            const resp = await fetch(`/api/photo/record/${this._currentRecId}/favorite`, { method: 'POST' });
            const data = await resp.json();
            if (data.success) {
                this._currentRec = this._currentRec || {};
                this._currentRec.is_favorite = data.is_favorite;
                this._renderFavState();
            }
        } catch (e) { console.error('[PhotoPanel] favorite', e); }
    },

    _renderFavState() {
        const btn = document.getElementById('photo-lb-fav');
        if (!btn) return;
        const fav = this._currentRec && this._currentRec.is_favorite;
        btn.classList.toggle('active', !!fav);
        btn.querySelector('.material-symbols-outlined').textContent = fav ? 'star' : 'star_border';
    },

    async _recaptionCurrent() {
        if (!this._currentRecId) return;
        const btn = document.getElementById('photo-lb-recaption');
        if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
        try {
            const resp = await fetch(`/api/photo/record/${this._currentRecId}/recaption`, { method: 'POST' });
            const data = await resp.json();
            if (data.success && data.record) {
                this._currentRec = data.record;
                this.openLightbox(data.record, this._lightboxRecords);
                this._toast('已重新描述');
            } else {
                this._toast(data.error || '重新描述失败');
            }
        } catch (e) {
            this._toast('重新描述失败');
        } finally {
            if (btn) { btn.disabled = false; }
        }
    },

    openLightbox(rec, records = null) {
        if (!rec || !rec.image_url) return;
        this._ensureLightbox();
        if (Array.isArray(records) && records.length) {
            this._lightboxRecords = records.filter(r => r && r.image_url);
        } else {
            // 非独立相册入口（聊天卡片/回忆缩略图）不复用上一次相册列表，避免串到旧照片集合。
            this._lightboxRecords = [rec];
        }
        const idx = this._lightboxRecords.findIndex(r => String(r.id) === String(rec.id));
        this._lightboxIndex = idx >= 0 ? idx : 0;
        this._renderLightboxRecord(rec);
        const box = document.getElementById('photo-lightbox');
        box.style.display = 'flex';
        box.tabIndex = -1;
        box.focus({ preventScroll: true });
    },

    _renderLightboxRecord(rec) {
        if (!rec || !rec.image_url) return;
        this._currentRec = rec;
        this._currentRecId = rec.id;
        const box = document.getElementById('photo-lightbox');
        if (!box) return;
        box.querySelector('.photo-lightbox-img').src = rec.image_url;
        const cn = rec.scene_type_cn || this.SCENE_CN[rec.scene_type] || '照片';
        const day = rec.source_day ? `第${rec.source_day}天 ` : '';
        const loc = rec.location_name || rec.location || '';
        box.querySelector('.photo-lightbox-tag').textContent = `${day}${rec.source_time || ''} · ${cn}${loc ? ' · ' + loc : ''}`;
        box.querySelector('.photo-lightbox-memo').textContent = rec.scene_memo || '';
        const vlm = box.querySelector('.photo-lightbox-vlm');
        if (rec.vlm_caption) { vlm.textContent = '「回看」' + rec.vlm_caption; vlm.style.display = ''; }
        else vlm.style.display = 'none';
        const prev = box.querySelector('.photo-lightbox-prev');
        const next = box.querySelector('.photo-lightbox-next');
        const canNavigate = this._lightboxRecords.length > 1;
        prev.disabled = !canNavigate || this._lightboxIndex <= 0;
        next.disabled = !canNavigate || this._lightboxIndex >= this._lightboxRecords.length - 1;
        prev.style.visibility = canNavigate ? 'visible' : 'hidden';
        next.style.visibility = canNavigate ? 'visible' : 'hidden';
        this._renderFavState();
    },

    _showLightboxIndex(index) {
        if (index < 0 || index >= this._lightboxRecords.length) return;
        const rec = this._lightboxRecords[index];
        if (!rec || !rec.image_url) return;
        this._lightboxIndex = index;
        this._renderLightboxRecord(rec);
    },

    closeLightbox() {
        const box = document.getElementById('photo-lightbox');
        if (box) box.style.display = 'none';
    },

    /* ── 独立相册页（P2-C，时间线 + 删除）────────── */
    _ensureAlbum() {
        if (document.getElementById('photo-album-modal')) return;
        const m = document.createElement('div');
        m.id = 'photo-album-modal';
        m.className = 'photo-album-modal';
        m.style.display = 'none';
        m.innerHTML = `
            <div class="photo-album-backdrop"></div>
            <div class="photo-album-body">
                <div class="photo-album-head">
                    <span>📷 相册</span>
                    <button class="photo-album-close" title="关闭">&times;</button>
                </div>
                <div class="photo-album-main">
                    <div class="photo-album-rail" id="photo-album-rail"></div>
                    <div class="photo-album-grid" id="photo-album-grid"></div>
                </div>
            </div>`;
        document.body.appendChild(m);
        m.querySelector('.photo-album-close').onclick = () => this.closeAlbum();
        m.querySelector('.photo-album-backdrop').onclick = () => this.closeAlbum();
    },

    async openAlbum() {
        this._ensureAlbum();
        const m = document.getElementById('photo-album-modal');
        const grid = document.getElementById('photo-album-grid');
        const rail = document.getElementById('photo-album-rail');
        grid.innerHTML = '<div class="photo-album-loading">加载中…</div>';
        if (rail) rail.innerHTML = '';
        m.style.display = 'flex';
        try {
            const resp = await fetch('/api/photo/records?limit=300&all=1').then(r => r.json());
            const records = (resp.success && resp.records) || [];
            if (!records.length) {
                grid.innerHTML = '<div class="photo-album-loading">还没有照片</div>';
                return;
            }
            // 按游戏日分组（时间线），无游戏日则归入“其他”
            const groups = {};
            records.forEach(r => {
                const key = (r.source_day && r.source_day > 0) ? r.source_day : ('x_' + (r.created_at || ''));
                (groups[key] = groups[key] || []).push(r);
            });
            const keys = Object.keys(groups).sort((a, b) => (Number(b) || 0) - (Number(a) || 0));
            let railHTML = '', gridHTML = '';
            keys.forEach(k => {
                const items = groups[k];
                const sample = items[0];
                const day = (sample.source_day && sample.source_day > 0) ? `第${sample.source_day}天` : '其他';
                const realDate = this._formatPhotoDate(sample.created_at);
                const gid = `album-group-${k}`;
                // 优先显示游戏时间（source_time），缺失时回退真实世界日期
                const gameTime = (sample.source_time && sample.source_time.trim()) ? sample.source_time.trim() : '';
                const railTime = gameTime ? gameTime.slice(0, 5) : (realDate.split(' ')[0] || '');
                railHTML += `<div class="photo-album-rail-item" data-target="${gid}">
                    <div class="rail-day">${day}</div>
                    <div class="rail-date">${railTime}</div>
                    <div class="rail-count">${items.length} 张</div>
                </div>`;
                const cells = items.map(p => this._albumCellHTML(p)).join('');
                gridHTML += `<div class="photo-album-group" id="${gid}">
                    <div class="photo-album-group-head">${day} · ${gameTime ? gameTime.slice(0, 5) : realDate} · 共 ${items.length} 张</div>
                    <div class="photo-album-cells">${cells}</div>
                </div>`;
            });
            if (rail) rail.innerHTML = railHTML;
            grid.innerHTML = gridHTML;
            // 绑定格子：删除按钮（阻止冒泡）+ 点击预览
            grid.querySelectorAll('.photo-album-cell').forEach(cell => {
                const rid = cell.getAttribute('data-rid');
                const del = cell.querySelector('.photo-album-del');
                if (del) del.addEventListener('click', (e) => { e.stopPropagation(); this.deletePhoto(rid); });
                cell.addEventListener('click', async () => {
                    const r = await fetch(`/api/photo/record/${rid}`).then(r => r.json()).catch(() => null);
                    if (r && r.success) this.openLightbox(r.record, records);
                });
            });
            // 绑定时间线栏跳转
            if (rail) rail.querySelectorAll('.photo-album-rail-item').forEach(it => {
                it.addEventListener('click', () => {
                    const t = document.getElementById(it.getAttribute('data-target'));
                    if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' });
                });
            });
        } catch (e) {
            grid.innerHTML = '<div class="photo-album-loading">加载失败</div>';
        }
    },

    _albumCellHTML(p) {
        const time = p.source_time || (p.created_at ? this._formatPhotoDate(p.created_at).split(' ')[1] : '');
        const loc = p.location_name || p.location || '';
        const cap = this._esc(p.scene_type_cn || p.scene_type || '');
        const img = (p.status === 'done' && p.image_url)
            ? `<img src="${p.image_url}" alt="${cap}" loading="lazy">
               ${p.is_favorite ? '<span class="photo-album-star">★</span>' : ''}`
            : `<div class="photo-album-ph"></div>`;
        const sub = [time, loc].filter(Boolean).join(' · ');
        return `<div class="photo-album-cell" data-rid="${p.id}" data-status="${p.status}">
            ${img}
            <button class="photo-album-del" title="删除">&times;</button>
            <div class="photo-album-cap">${this._esc(cap)}</div>
            ${sub ? `<div class="photo-album-sub">${this._esc(sub)}</div>` : ''}
        </div>`;
    },

    _formatPhotoDate(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (isNaN(d.getTime())) return '';
        const p = n => String(n).padStart(2, '0');
        return `${d.getFullYear()}年${p(d.getMonth() + 1)}月${p(d.getDate())}日 ${p(d.getHours())}:${p(d.getMinutes())}`;
    },

    async deletePhoto(rid) {
        if (!rid) return;
        if (!window.confirm('确定删除这张照片吗？删除后不可恢复。')) return;
        try {
            const resp = await fetch(`/api/photo/record/${rid}`, { method: 'DELETE' })
                .then(r => r.json()).catch(() => ({ success: false }));
            if (resp.success) {
                if (this._currentRecId === rid) this.closeLightbox();
                this.openAlbum();   // 重新拉取，刷新时间线与格子
                this._toast('已删除');
            } else {
                this._toast('删除失败');
            }
        } catch (e) { this._toast('删除失败'); }
    },

    closeAlbum() {
        const m = document.getElementById('photo-album-modal');
        if (m) m.style.display = 'none';
    },

    /* ── 小工具 ────────────────────────────────── */
    _toast(msg) {
        let t = document.getElementById('photo-toast');
        if (!t) {
            t = document.createElement('div');
            t.id = 'photo-toast';
            t.className = 'photo-toast';
            document.body.appendChild(t);
        }
        t.textContent = msg;
        t.classList.add('show');
        clearTimeout(this._toastTimer);
        this._toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
    },

    _esc(s) {
        return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    },
};

document.addEventListener('DOMContentLoaded', () => {
    try { PhotoPanel.init(); } catch (e) { console.error('[PhotoPanel] init 失败', e); }
});
