/* 对话面板 */
function formatTime(gameDay, gameTime) {
    if (gameDay === null || gameDay === undefined || !gameTime) return '';
    return `第${gameDay}天 ${gameTime}`;
}

const DialoguePanel = {
    history: [],
    isNewSession: true,  // 页面加载后第一条消息标记（刷新后置 true，发送后置 false）
    _sending: false,     // 防止并发发送（快捷按钮双击等场景）
    _pendingRenders: 0,  // 正在渲染的分片数量（>0 时新消息排队，防止插话乱序）
    _pollVersion: 0,     // 轮询版本号，切角色时递增以停止旧轮询链
    _historyLoading: false, // 加载更早记录时的并发锁（防止重复拉取），与自动滚底无关
    thinkingEnabled: localStorage.getItem('sim_life_thinking_enabled') === 'true',

    // 属性名中英文映射表（含物理/心理 + 全部技能标签）
    ATTR_CN_MAP: {
        boredom: '无聊', coding_skill: '编程', disappointment: '失望',
        energy: '精力', fulfillment: '充实', joy: '开心',
        player_respect: '玩家尊重', mood: '情绪', motivation: '动力',
        stress: '压力', health: '健康', hunger: '饥饿', hygiene: '卫生',
        happiness: '幸福度', loneliness: '孤独感', confidence: '自信',
        creativity: '创造力', anger: '愤怒',
        player_trust: '玩家信任', player_affection: '玩家好感',
        player_intimacy: '亲密', writing_skill: '写作', social_skill: '社交',
        learning_skill: '学习', fitness: '健身',
        writer_progress: '作家进度', coder_progress: '计算机学家进度',
        // 新式技能键（skills JSON 字段，不含 _skill 后缀）
        writing: '写作', coding: '编程', social: '社交', learning: '学习',
        legal_knowledge: '法律知识', debate: '辩论', case_analysis: '案例分析', negotiation: '谈判',
        painting: '绘画', art_theory: '艺术理论', observation: '观察',
        medical_knowledge: '医学知识', diagnosis: '诊断能力', surgery: '手术技能',
        empathy: '同理心', stress_resistance: '抗压能力',
        literary_analysis: '文学分析',
        game_design: '游戏设计', project_management: '项目管理', teamwork: '协作',
    },

    init() {
        // 角色切换/页面刷新后，首条消息需注入完整规则（含玩家身份铁律）
        this.isNewSession = true;

        // ── 主聊天思考模式：全局开关，默认关闭；不影响照片/记忆等后台调用 ──
        this._initThinkingToggle();

        // ── PC 端：会话窗口左上角切换中间栏显示/隐藏 ──
        this._initCenterCollapseToggle();

        // ── 桌面端事件绑定 ──
        document.getElementById('btn-send').addEventListener('click', () => this.sendMessage());
        document.getElementById('dialogue-input').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this.sendMessage();
        });

        document.querySelectorAll('.hint-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.getElementById('dialogue-input').value = btn.dataset.msg;
                this.sendMessage();
            });
        });

        // 快捷关系按钮 — 事件委托（按钮可能位于桌面/移动加号面板内，动态生成）
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.quick-rel-btn');
            if (!btn || !btn.dataset.relation) return;
            DialoguePanel.loadRecommendations(btn.dataset.relation);
            // 关闭可能打开的加号面板
            document.getElementById('chat-plus-menu')?.classList.remove('show');
            document.getElementById('mobile-chat-menu')?.classList.remove('show');
        });

        // ── 移动端事件绑定 ──
        const mobileSendBtn = document.getElementById('mobile-btn-send');
        const mobileInput = document.getElementById('mobile-chat-input');
        if (mobileSendBtn) {
            mobileSendBtn.addEventListener('click', () => this.sendMessage());
        }
        if (mobileInput) {
            mobileInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.sendMessage();
                }
            });
            // textarea 自动撑高
            mobileInput.addEventListener('input', () => {
                mobileInput.style.height = 'auto';
                mobileInput.style.height = Math.min(mobileInput.scrollHeight, 100) + 'px';
            });
        }
        // 推荐面板关闭按钮
        const recClose = document.getElementById('recommend-close');
        if (recClose) {
            recClose.addEventListener('click', () => this.closeRecommendPanel());
        }

        // 重新聊天按钮
        const resetBtn = document.getElementById('btn-reset-chat');
        if (resetBtn) {
            resetBtn.addEventListener('click', () => this.resetChat());
        }

        // 加载历史聊天记录
        this.loadHistory();

        // 时间 / 地点选择器（聊天联动游戏时间/地点）
        this._initTimeLocationPicker();

        // 页面刷新时同步当前关系阶梯标签（避免默认“陌生人”）
        if (App.character && App.character.relationship_tier) {
            this.updateRelationshipTier(App.character.relationship_tier);
        }

        // 页面刷新时清除上一轮异步 extras 缓存，避免显示旧数据
        fetch('/api/dialogue/extras?clear=1').catch(() => {});
        // 切角色时递增版本号，停止旧轮询链
        this._pollVersion++;

        // 重置“情感时刻仅在新阶段出现一次”的门控状态，确保切换角色后首条情感时刻仍可在对话面板浮现
        this._lastAnnouncedTier = undefined;
        this._currentTier = 0;
        this._panelEmotionShownStage = null;
        // 清掉上一角色可能残留的悬浮提示（移动端若不清理会一直卡在顶部）
        if (this._memoryHintTimer) { clearTimeout(this._memoryHintTimer); this._memoryHintTimer = null; }
        this._fadeMemoryHint();

        // 从 DB 加载最新快捷建议（刷新后恢复上一轮按钮）
        this._loadHintsFromDB();
    },

    /**
     * 从数据库加载当前角色最新一轮的快捷建议（带重试）。
     */
    async _loadHintsFromDB(retry = 0) {
        try {
            const resp = await fetch('/api/dialogue/hints');
            const data = await resp.json();
            if (data.success && Array.isArray(data.hints) && data.hints.length > 0) {
                this.updateHintButtons(data.hints);
            } else if (retry < 2) {
                // DB 返回空时重试（可能上一轮异步线程还没写入）
                setTimeout(() => this._loadHintsFromDB(retry + 1), 1500);
            }
        } catch (e) {
            if (retry < 2) {
                setTimeout(() => this._loadHintsFromDB(retry + 1), 1500);
            }
        }
    },

    async loadHistory() {
        const container = this._msgContainer();
        // 清空容器和历史数组，避免角色切换时旧角色的消息残留导致串台
        if (container) container.innerHTML = '';
        const mobileContainer = document.getElementById('mobile-chat-messages');
        if (mobileContainer) mobileContainer.innerHTML = '';
        this.history = [];
        this._historyAllLoaded = false;
        this._historyLoading = false;

        try {
            const resp = await fetch('/api/chat/history?limit=10');
            const data = await resp.json();
            console.log('[loadHistory] API 返回:', data.success, '消息数:', data.data ? data.data.length : 0);
            if (data.success && data.data) {
                await this._renderHistoryMessages(data.data);
                this._getAllMsgContainers().forEach(c => this._sortHistoryMessages(c));
                if (data.has_more) {
                    this._showLoadMoreButton();
                } else {
                    this._historyAllLoaded = true;
                }
            }
        } catch (err) {
            console.error('加载聊天历史失败:', err);
        }
    },

    async _renderHistoryMessages(messages, updateHistory = true) {
        const photoRestoreTasks = [];
        messages.forEach((log, idx) => {
            // 照片独占消息（手动拍照追加的聊天记录）：跳过空内容气泡，直接还原照片卡片
            const isPhotoOnly = !log.content && log.photo && log.photo.id;
            if (!isPhotoOnly) {
                if (log.segments && Array.isArray(log.segments) && log.segments.length > 0) {
                    const hasMultiSegments = log.segments.length > 1 || log.segments.some(s => s.background);
                    if (hasMultiSegments) {
                        const replySegments = log.segments.filter(s => s.reply);
                        const lastReplyIdx = replySegments.length > 0 ? replySegments[replySegments.length - 1] : null;
                        for (const seg of log.segments) {
                            if (seg.background) {
                                this.appendBackground(seg.background, log.game_day, log.game_time);
                            }
                            if (seg.reply) {
                                const isLast = (seg === lastReplyIdx);
                                this.appendMessage(log.speaker, seg.reply, log.game_day, log.game_time,
                                    isLast ? log.reasoning : null,
                                    isLast ? log.effects : null,
                                    null, isLast, idx);
                            }
                        }
                    } else {
                        this.appendMessage(log.speaker, log.content, log.game_day, log.game_time, log.reasoning, log.effects, idx);
                    }
                } else {
                    this.appendMessage(log.speaker, log.content, log.game_day, log.game_time, log.reasoning, log.effects, idx);
                }
            }
            // P0-1：该轮对话关联了照片 → 还原照片卡片（刷新后不丢失）
            if (log.photo && log.photo.id && typeof PhotoPanel !== 'undefined') {
                // 先等待整批照片回查完成，避免历史文字已搬到顶部、异步照片随后单独追加到末尾。
                photoRestoreTasks.push(
                    Promise.resolve()
                        .then(() => PhotoPanel.restoreCard(log.photo.id, log.game_day, log.game_time))
                        .catch(e => console.error('[PhotoPanel] restore', e))
                );
            }
        });
        await Promise.all(photoRestoreTasks);
        if (updateHistory) {
            messages.forEach(log => {
                const role = log.speaker === 'player' ? 'user' : 'assistant';
                this.history.push({ role, content: log.content, game_day: log.game_day, game_time: log.game_time });
            });
        }
    },

    /** 将带游戏时间键的文字/照片/背景节点按时间稳定排序；无时间键的系统提示保留原位置。 */
    _sortHistoryMessages(container) {
        if (!container) return;
        const keyed = Array.from(container.children).filter(el =>
            el.getAttribute('data-gd') !== null
        );
        if (keyed.length < 2) return;
        const sorted = keyed.slice().sort((a, b) => {
            const ad = parseInt(a.getAttribute('data-gd'), 10) || 0;
            const bd = parseInt(b.getAttribute('data-gd'), 10) || 0;
            if (ad !== bd) return ad - bd;
            const at = a.getAttribute('data-gt') || '';
            const bt = b.getAttribute('data-gt') || '';
            return at.localeCompare(bt);
        });
        // 先放置稳定锚点，再移动节点；不能在节点已被 fragment 移走后继续把它当作 insertBefore 的参考节点。
        const marker = document.createComment('history-sort-anchor');
        container.insertBefore(marker, keyed[0]);
        const fragment = document.createDocumentFragment();
        sorted.forEach(el => fragment.appendChild(el));
        container.insertBefore(fragment, marker);
        marker.remove();
    },

    _isNearBottom(container, threshold = 32) {
        if (!container) return true;
        return container.scrollHeight - container.clientHeight - container.scrollTop <= threshold;
    },

    _shouldAutoScroll(container) {
        // 以“是否处于底部”作为唯一判断条件来控制自动滚动：
        // 处于底部 → 新内容到达时自动滚到最新消息位置；
        // 不在底部(停留中/上方) → 保持当前滚动位置，不抢滚动。
        return this._isNearBottom(container);
    },

    // 在内容增长【之前】抓取每个容器当前的“是否贴底”状态。
    // 关键：自动跟随的判断必须发生在新节点插入前，否则长消息会让 scrollHeight 增大，
    // 使相对位置被误判为“离底部很远”，导致本该跟随的新消息反而丢失跟随（见 bug 修复）。
    _captureNearBottom(containers) {
        const m = new Map();
        (containers || []).forEach(c => m.set(c, this._isNearBottom(c)));
        return m;
    },

    // 仅对“插入前就贴底”的容器执行滚底；其余容器保持用户当前浏览位置不动。
    _applyNearBottomScroll(containers, nearMap) {
        if (!nearMap) return;
        (containers || []).forEach(c => {
            if (nearMap.get(c)) c.scrollTop = c.scrollHeight;
        });
    },

    _showLoadMoreButton() {
        this._getAllMsgContainers().forEach(container => {
            container.querySelectorAll('.history-load-control').forEach(el => el.remove());
            const div = document.createElement('div');
            div.className = 'msg system-msg history-load-control';
            div.innerHTML = '<a href="#" class="load-more-link" style="color:#666;text-decoration:underline;">加载更早的聊天记录...</a>';
            div.querySelector('.load-more-link').addEventListener('click', (e) => {
                e.preventDefault();
                this._loadMoreHistory();
            });
            container.insertBefore(div, container.firstChild);
        });
    },

    async _loadMoreHistory() {
        if (this._historyLoading || this._historyAllLoaded) return;
        this._historyLoading = true;
        const containers = this._getAllMsgContainers();
        const snapshots = new Map();
        containers.forEach(c => snapshots.set(c, {
            scrollTop: c.scrollTop,
            scrollHeight: c.scrollHeight,
            children: new Set(Array.from(c.children)),
            oldFirst: Array.from(c.children).find(el => el.getAttribute('data-gd') !== null) || null
        }));

        try {
            const resp = await fetch('/api/chat/history?offset=' + this.history.length + '&limit=20');
            const data = await resp.json();
            if (!data.success) return;
            if (!data.data || data.data.length === 0) {
                this._historyAllLoaded = true;
                containers.forEach(c => c.querySelectorAll('.history-load-control').forEach(el => el.remove()));
                return;
            }

            // 入口是稳定的历史区域锚点：先移除旧入口，批次插入旧消息之前，最后在顶部恢复入口。
            containers.forEach(c => c.querySelectorAll('.history-load-control').forEach(el => el.remove()));
            await this._renderHistoryMessages(data.data, false);

            containers.forEach(c => {
                const snapshot = snapshots.get(c);
                const newNodes = Array.from(c.children).filter(el =>
                    !snapshot.children.has(el) && !el.classList.contains('system-msg')
                );
                const sorted = newNodes.slice().sort((a, b) => {
                    const ak = this._historyNodeSortKey(a);
                    const bk = this._historyNodeSortKey(b);
                    if (ak[0] !== bk[0]) return ak[0] - bk[0];
                    return ak[1].localeCompare(bk[1]);
                });
                const fragment = document.createDocumentFragment();
                sorted.forEach(el => fragment.appendChild(el));
                if (snapshot.oldFirst && snapshot.oldFirst.parentElement === c) {
                    c.insertBefore(fragment, snapshot.oldFirst);
                    const sep = document.createElement('div');
                    sep.className = 'msg system-msg history-separator';
                    sep.textContent = '—— 更早的记录 ——';
                    c.insertBefore(sep, snapshot.oldFirst);
                } else {
                    c.appendChild(fragment);
                }
                // 保持用户正在看的旧消息不动：补偿新增历史批次占用的高度，而不是滚到底部。
                c.scrollTop = snapshot.scrollTop + (c.scrollHeight - snapshot.scrollHeight);
            });

            const newEntries = data.data.map(log => ({
                role: log.speaker === 'player' ? 'user' : 'assistant',
                content: log.content,
                game_day: log.game_day,
                game_time: log.game_time
            }));
            this.history = [...newEntries, ...this.history];

            if (!data.has_more) {
                this._historyAllLoaded = true;
            } else {
                this._showLoadMoreButton();
            }
        } catch (err) {
            console.error('加载更早记录失败:', err);
        } finally {
            this._historyLoading = false;
        }
    },

    _historyNodeSortKey(el) {
        const day = parseInt(el.getAttribute('data-gd'), 10);
        return [Number.isFinite(day) ? day : -1, el.getAttribute('data-gt') || ''];
    },

    async resetChat() {
        const name = App.character?.name || '角色';
        if (!confirm(`确定清空与${name}的聊天记录？`)) return;

        const fullReset = confirm(`是否也清除${name}的记忆和过往事件日志？\n\n"确定" — 彻底重来（聊天+记忆+事件全部清除）\n"取消" — 只清空聊天记录和快捷建议`);
        const mode = fullReset ? 'full' : 'light';

        try {
            const resp = await fetch('/api/chat/reset', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode })
            });
            const data = await resp.json();
            if (data.success) {
                const resetMsg = '<div class="msg system-msg">与' + name + '的对话已重新开始</div>';
                document.getElementById('dialogue-messages').innerHTML = resetMsg;
                const mobileMsgs = document.getElementById('mobile-chat-messages');
                if (mobileMsgs) mobileMsgs.innerHTML = resetMsg;
                this.history = [];
                this.isNewSession = true;
                // 清除缓存的快捷建议（桌面端 + 移动端）
                const hintContainer = document.getElementById('hint-buttons');
                if (hintContainer) hintContainer.innerHTML = '';
                const mobileHints = document.getElementById('mobile-hint-buttons');
                if (mobileHints) mobileHints.innerHTML = '';
            }
        } catch (err) {
            console.error('清空聊天记录失败:', err);
        }
    },

    updateHintButtons(hints) {
        // 桌面端快捷对话
        const container = document.getElementById('hint-buttons');
        if (container) {
            container.innerHTML = hints.map(h =>
                `<button class="hint-btn" data-msg="${h}">${h}</button>`
            ).join('');
            container.querySelectorAll('.hint-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    document.getElementById('dialogue-input').value = btn.dataset.msg;
                    this.sendMessage();
                });
            });
        }
        // 移动端快捷对话（横向滚动通栏）
        const mobileContainer = document.getElementById('mobile-hint-buttons');
        if (mobileContainer) {
            mobileContainer.innerHTML = hints.map(h =>
                `<button class="hint-btn" data-msg="${h}">${h}</button>`
            ).join('');
            mobileContainer.querySelectorAll('.hint-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    document.getElementById('mobile-chat-input').value = btn.dataset.msg;
                    this.sendMessage();
                });
            });
        }
    },

    updateConstraints(constraints) {
        const tagEls = [
            document.getElementById('dialogue-tone-tag'),
            document.getElementById('mobile-chat-tone')
        ];
        tagEls.forEach(tagEl => {
            if (!tagEl) return;
            if (constraints && constraints.tone) {
                const toneLabels = {
                    'irritable': '烦躁', 'breakdown': '崩溃', 'desperate': '绝望',
                    'depressed': '抑郁', 'sad': '难过', 'angry_cold': '冰冷',
                    'distant': '疏远', 'polite_but_guarded': '客气',
                    'suspicious': '猜疑', 'cautious': '谨慎'
                };
                tagEl.textContent = toneLabels[constraints.tone] || constraints.tone;
                tagEl.style.display = 'inline-block';
                tagEl.className = 'tone-tag tone-' + (constraints.tone || 'default');
            } else {
                tagEl.style.display = 'none';
            }
        });
    },

    updateRelationshipTier(tierData) {
        const tier = (tierData && tierData.tier) || 0;
        // 仅在关系阶梯真正“进阶到新阶段”时，向小喇叭通知条播报一次（避免每次刷新都播报）
        if (this._lastAnnouncedTier !== undefined && this._lastAnnouncedTier !== tier) {
            const name = (tierData && tierData.name) || '新关系';
            if (typeof App !== 'undefined' && App.announce) {
                App.announce(`关系进阶：${name}`, '💞');
            }
        }
        this._lastAnnouncedTier = tier;
        this._currentTier = tier;

        const tagEls = [
            document.getElementById('dialogue-tier-tag'),
            document.getElementById('mobile-chat-tier')
        ];
        tagEls.forEach(tagEl => {
            if (!tagEl || !tierData) return;
            tagEl.textContent = tierData.name || '陌生人';
            tagEl.className = 'tier-tag tier-' + (tierData.tier || 0);
        });
    },

    showRefuseHint(constraints) {
        // 去重：同一时刻只允许一个 refuse-hint 提示，已存在则更新文本，不再重复堆叠
        const containers = this._getAllMsgContainers ? this._getAllMsgContainers() : [this._msgContainer()];
        // 插入前抓取贴底快照（含主容器），保证跟随判断不受新节点高度影响。
        const _pc = this._msgContainer();
        const _containers = containers.slice();
        if (_pc && !_containers.includes(_pc)) _containers.push(_pc);
        const _near = this._captureNearBottom(_containers);
        // user_hint 已是后端生成的玩家侧叙事化文本（职业场景/陌生人/关系未下跌 时为 None）；
        // 为空则这轮不弹气泡，避免把给 LLM 的 system 指令原文泄漏到聊天框。
        if (!constraints || !constraints.user_hint) return;
        const newText = constraints.user_hint;
        let appended = 0;
        containers.forEach((container) => {
            if (!container) return;
            let existing = container.querySelector('.refuse-hint');
            if (existing) {
                existing.textContent = newText;
            } else {
                const div = document.createElement('div');
                div.className = 'msg refuse-hint';
                div.textContent = newText;
                container.appendChild(div);
                appended++;
            }
            if (_near.get(container)) container.scrollTop = container.scrollHeight;
        });
        // 至少要保证主容器有一条 hint（如果容器列表为空则强制走一次 _msgContainer）
        if (appended === 0 && !containers.includes(this._msgContainer())) {
            const c = this._msgContainer();
            if (c) {
                let ex = c.querySelector('.refuse-hint');
                if (ex) ex.textContent = newText;
                else {
                    const div = document.createElement('div');
                    div.className = 'msg refuse-hint';
                    div.textContent = newText;
                    c.appendChild(div);
                }
                if (_near.get(c)) c.scrollTop = c.scrollHeight;
            }
        }
    },

    /** 收回失败发送时已上屏的玩家消息（DOM + history） */
    _undoPendingPlayerMessage() {
        const container = this._msgContainer();
        const playerMsgs = container.querySelectorAll('.msg.player');
        if (playerMsgs.length > 0) {
            const lastPlayerMsg = playerMsgs[playerMsgs.length - 1];
            lastPlayerMsg.remove();
        }
        // 从 history 中移除最后一条 user 消息
        for (let i = this.history.length - 1; i >= 0; i--) {
            if (this.history[i].role === 'user') {
                this.history.splice(i, 1);
                break;
            }
        }
    },

    /** 获取当前活跃的输入元素（桌面端或移动端） */
    _activeInput() {
        const mobileInput = document.getElementById('mobile-chat-input');
        if (mobileInput && document.getElementById('mobile-chat-overlay').classList.contains('show')) {
            return mobileInput;
        }
        return document.getElementById('dialogue-input');
    },

    /** 当前主消息容器（移动端全屏 → mobile，否则 → 桌面端） */
    _msgContainer() {
        if (this._isMobileChat()) {
            return document.getElementById('mobile-chat-messages');
        }
        return document.getElementById('dialogue-messages');
    },

    /** 同时写入主容器和桌面端容器（移动端全屏模式下保证双写） */
    _getAllMsgContainers() {
        if (this._isMobileChat()) {
            return [
                document.getElementById('mobile-chat-messages'),
                document.getElementById('dialogue-messages')
            ].filter(Boolean);
        }
        return [document.getElementById('dialogue-messages')].filter(Boolean);
    },

    /** 移动端全屏模式下，所有分段渲染完成后解锁输入框 */
    _onSegmentsAllRendered(input) {
        if (!input || !this._isMobileChat()) return;
        const check = () => {
            if (this._pendingRenders > 0) {
                setTimeout(check, 100);
            } else {
                input.disabled = false;
                App.dialogueInProgress = false;
                this._sending = false;
            }
        };
        setTimeout(check, 100);
    },

    _initThinkingToggle() {
        const buttons = [
            document.getElementById('chat-thinking-toggle'),
            document.getElementById('mobile-chat-thinking-toggle'),
        ].filter(Boolean);
        const sync = () => {
            buttons.forEach(btn => {
                const on = this.thinkingEnabled;
                btn.setAttribute('aria-pressed', String(on));
                btn.classList.toggle('is-on', on);
                const label = btn.querySelector('[id$="thinking-label"]');
                if (label) label.textContent = on ? '思考：开' : '思考：关';
                const icon = btn.querySelector('[id$="thinking-icon"]');
                if (icon) icon.textContent = on ? 'psychology_alt' : 'psychology';
                btn.title = on ? '主聊天思考模式：开启' : '主聊天思考模式：关闭';
            });
        };
        buttons.forEach(btn => btn.addEventListener('click', (e) => {
            e.stopPropagation();
            this.thinkingEnabled = !this.thinkingEnabled;
            localStorage.setItem('sim_life_thinking_enabled', String(this.thinkingEnabled));
            sync();
        }));
        sync();
    },

    _initCenterCollapseToggle() {
        const btn = document.getElementById('btn-toggle-center');
        const icon = document.getElementById('btn-toggle-center-icon');
        const app = document.querySelector('.app-container');
        if (!btn || !icon || !app) return;

        const apply = (collapsed) => {
            app.classList.toggle('center-collapsed', collapsed);
            icon.textContent = collapsed ? 'view_stream' : 'view_sidebar';
            btn.title = collapsed ? '显示中间栏' : '隐藏中间栏';
        };

        // 恢复上次状态（仅在 PC 端有效；移动端 CSS 已隐藏按钮）
        let saved = false;
        try {
            saved = localStorage.getItem('sim_life_center_collapsed') === 'true';
        } catch (e) {
            saved = false;
        }
        apply(saved);

        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const collapsed = !app.classList.contains('center-collapsed');
            apply(collapsed);
            try {
                localStorage.setItem('sim_life_center_collapsed', String(collapsed));
            } catch (e) {}
        });
    },

    /** 当前是否在移动端全屏聊天模式 */
    _isMobileChat() {
        const overlay = document.getElementById('mobile-chat-overlay');
        return overlay && overlay.classList.contains('show');
    },

    async sendMessage() {
        // 防止并发发送（快捷按钮双击 / 历史未加载完时误触）
        if (this._sending) return;

        const isMobile = this._isMobileChat();
        const input = this._activeInput();
        const msg = input.value.trim();
        if (!msg) return;

        // 选择器覆盖：优先用玩家在 🕐/📍 设定的时间/地点，否则用当前游戏状态
        const effTime = this._computeSendTime();

        this._sending = true;
        App.dialogueInProgress = true;
        input.value = '';
        input.disabled = true;

        const playerTimeStr = `${String(effTime.hour).padStart(2, '0')}:${String(effTime.minute).padStart(2, '0')}:${String(effTime.second).padStart(2, '0')}`;
        // 如果有分片正在渲染（上一条回复还没显示完），player 消息的 DOM 追加排队
        if (this._pendingRenders > 0) {
            // 延迟到所有分片渲染完毕后再追加 player 消息，防止 DOM 插话乱序
            const checkAndAppend = () => {
                if (this._pendingRenders > 0) {
                    setTimeout(checkAndAppend, 100);
                } else {
                    this.appendMessage('player', msg, effTime.day, playerTimeStr);
                }
            };
            setTimeout(checkAndAppend, 100);
        } else {
            this.appendMessage('player', msg, effTime.day, playerTimeStr);
        }
        this.history.push({ role: 'user', content: msg, game_day: effTime.day, game_time: playerTimeStr });

        // 记录是否为新会话首条消息，并在发送后置 false
        const isNew = this.isNewSession;
        this.isNewSession = false;

        let data = null;
        try {
            const resp = await fetch('/api/dialogue', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: msg, history: this.history.slice(-40),
                    is_new_session: isNew,
                    game_day: effTime.day, game_hour: effTime.hour,
                    game_minute: effTime.minute, game_second: effTime.second,
                    location: this.pendingLocationId || null,
                    thinking_enabled: this.thinkingEnabled
                })
            });
            const data = await resp.json();

            if (data.success) {
                // 如果聊天记录写入失败，提醒用户刷新后会丢失
                if (data.chat_saved === false) {
                    this.appendMessage('system', '⚠ 本段对话暂未存档，刷新后可能丢失');
                }
                // 更新语气标签
                if (data.constraints) {
                    this.updateConstraints(data.constraints);
                    // 拒绝提示的"是否该弹"完全由后端算好的 user_hint 决定：有内容才弹，否则不弹
                    if (data.constraints && data.constraints.user_hint) {
                        this.showRefuseHint(data.constraints);
                    }
                }

                // 更新关系阶梯标签
                if (data.relationship_tier) {
                    this.updateRelationshipTier(data.relationship_tier);
                }

                let reply = data.reply;
                // ── AI伴侣系统升级 — 阶段三：结构化输出渲染 ──
                if (data.segments && Array.isArray(data.segments) && data.segments.length > 0) {
                    // 多段渲染：背景居中灰色斜体，回复进入气泡
                    // 使用后端返回的精确消息索引（避免 DOM 计数受 background-msg/分片/主动消息干扰）
                    const baseMsgIndex = data.message_index;
                    // 找到最后一个有 reply 的 segment
                    const replySegs = data.segments.filter(s => s.reply);
                    const lastReplySeg = replySegs.length > 0 ? replySegs[replySegs.length - 1] : null;
                    // 收集所有 reply 文本用于 TTS
                    const fullReplyText = replySegs.map(s => s.reply).join('');
                                    
                    // 每段间隔 1~2 秒随机，累加延迟，每段取当前实际游戏时间
                    let cumDelay = 0;
                    this._pendingRenders += data.segments.length;
                    data.segments.forEach((seg, segIdx) => {
                        setTimeout(() => {
                            // 每段显示渲染时的实际游戏时间
                            const segGameTime = `${String(App.gameHour).padStart(2, '0')}:${String(App.gameMinute).padStart(2, '0')}:${String(Math.floor(App.gameSecond)).padStart(2, '0')}`;
                            if (seg.background) {
                                this.appendBackground(seg.background, App.gameDay, segGameTime);
                            }
                            if (seg.reply) {
                                const isLast = (seg === lastReplySeg);
                                this.appendMessage('character', seg.reply, App.gameDay, segGameTime,
                                    isLast ? data.reasoning : null,
                                    isLast ? data.effects : null,
                                    null,           // msgIndex: auto from DOM
                                    isLast,         // isLastBubble
                                    baseMsgIndex,   // backendMsgIndex: 分片共享同一后端索引
                                    isLast ? (data.instruct_text || '') : null,  // instruct_text 只在最后一段
                                    isLast ? fullReplyText : null  // ttsFullText: 完整回复用于 TTS
                                );
                            }
                            this._pendingRenders--;
                        }, cumDelay);
                        // 下一段延迟 1~2 秒
                        cumDelay += 1000 + Math.random() * 1000;
                    });
                    // 移动端：等待所有分段渲染完成后解锁输入框（不聚焦）
                    if (isMobile) {
                        this._onSegmentsAllRendered(input);
                    }
                } else {
                    // 无分段时按原方式显示
                    const nowTime = `${String(App.gameHour).padStart(2, '0')}:${String(App.gameMinute).padStart(2, '0')}:${String(Math.floor(App.gameSecond)).padStart(2, '0')}`;
                    this.appendMessage('character', reply, App.gameDay, nowTime, data.reasoning, data.effects, null, true, data.message_index, data.instruct_text || '');
                }
                const histTime = `${String(App.gameHour).padStart(2, '0')}:${String(App.gameMinute).padStart(2, '0')}:${String(Math.floor(App.gameSecond)).padStart(2, '0')}`;
                this.history.push({ role: 'assistant', content: data.reply, game_day: App.gameDay, game_time: histTime });

                // 空正文兜底：后端检测到模型"想完就停"未写正文时，弹叙事化系统提示
                if (data.empty_reply_hint) {
                    this.appendMessage('system', data.empty_reply_hint, App.gameDay, histTime);
                }

                // ── 聊天生图：渲染照片卡片 / 旧照回忆 / 拒绝提示 ──
                if (typeof PhotoPanel !== 'undefined') {
                    try { PhotoPanel.handleDialogueResult(data); } catch (e) { console.error('[PhotoPanel]', e); }
                }

                // 角色回复显示后，再更新状态面板
                if (data.effects && Object.keys(data.effects).length > 0) {
                    StatusPanel.recordEffects(data.effects);
                }
                if (data.character) {
                    App.refreshAll(data.character, true);
                }

                // 选择器覆盖已随对话落库并由后端返回，清除本地暂存
                this.timeOverride = null;
                this.pendingLocationId = null;
                this.pendingLocationName = null;
                if (this._tlLocInput) this._tlLocInput.value = '';
                this._updateTimePreview();
                this._updateLocPreview();

                // ── 异步 extras：轮询获取快捷建议 / 记忆提示 / 情感时刻 / 情绪属性 ──
                this._extrasProcessed = { hints: false, memory: false, emotion: false, effects: false };
                this._currentExtrasSessionId = data.session_id || null;
                this._pollExtras();
                
                // 对话记录已由后端 process_dialogue() 通过 chat_history.py 持久化到 MD 文件
            } else {
                // 请求失败（非异常）：收回已上屏的玩家消息
                this._undoPendingPlayerMessage();
                this.appendMessage('system', '发送失败，请重试');
            }
        } catch (err) {
            // 网络异常：收回已上屏的玩家消息
            this._undoPendingPlayerMessage();
            this.appendMessage('system', '网络错误，请检查连接');
            console.error(err);
        }

        // ── 解锁输入框 ──
        if (isMobile && data && data.success && data.segments && Array.isArray(data.segments) && data.segments.length > 0) {
            // 移动端有分段：已通过 _onSegmentsAllRendered 延迟解锁，这里不做任何操作
            return;
        }
        // 桌面端或无分段移动端：立即解锁
        input.disabled = false;
        App.dialogueInProgress = false;
        this._sending = false;
        // 移动端禁止自动聚焦（防止弹出键盘）
        if (!isMobile) {
            input.focus();
        }
        // 移动端 textarea 重置高度
        if (isMobile && input.tagName === 'TEXTAREA') {
            input.style.height = 'auto';
        }
    },

    /* ========== 时间 / 地点选择器（聊天联动游戏时间/地点） ========== */
    _initTimeLocationPicker() {
        this.timeOverride = null;           // {day,hour,minute,second} 或 null
        this.pendingLocationId = null;
        this.pendingLocationName = null;
        this._venuesCache = {};

        this._tlPanel = document.getElementById('tl-picker');
        this._tlBackdrop = document.getElementById('tl-picker-backdrop');
        this._tlTimePreview = document.getElementById('tl-time-preview');
        this._scenePanel = document.getElementById('tl-scene-panel');
        this._sceneList = document.getElementById('tl-scene-list');
        this._tlLocInput = document.getElementById('tl-loc-input');
        this._tlLocPreview = document.getElementById('tl-loc-preview');
        this._tlVenueList = document.getElementById('tl-venue-list');
        this._sceneData = null;
        this._activeScene = null;

        const openPicker = (focus) => this._openTimeLocationPicker(focus);
        document.getElementById('btn-tl-pick')?.addEventListener('click', () => openPicker('time'));

        document.getElementById('tl-picker-close')?.addEventListener('click', () => this._closeTimeLocationPicker());
        this._tlBackdrop?.addEventListener('click', () => this._closeTimeLocationPicker());

        // 天数快捷
        document.querySelectorAll('.tl-day-chips .tl-chip').forEach((btn) => {
            btn.addEventListener('click', () => {
                const op = btn.dataset.day;
                if (op === 'now') {
                    this.timeOverride = null;
                } else {
                    const add = op === '+1' ? 1 : 3;
                    this.timeOverride = {
                        day: App.gameDay + add,
                        hour: App.gameHour, minute: App.gameMinute, second: Math.floor(App.gameSecond)
                    };
                }
                this._updateTimePreview();
            });
        });

        // 场景按钮：切换展开/收起（事件委托绑在 #tl-picker 上，幂等且不被 init 重复执行影响）
        this._sceneData = null;
        this._activeScene = null;
        if (!this._tlPanel.dataset.sceneDelegated) {
            this._tlPanel.addEventListener('click', (e) => {
                const btn = e.target.closest('.tl-scene-toggle');
                if (!btn || !this._tlPanel.contains(btn)) return;
                const cat = btn.dataset.scene;
                if (this._activeScene === cat) {
                    // 再次点击同一个 → 收起
                    this._activeScene = null;
                    if (this._scenePanel) this._scenePanel.hidden = true;
                    document.querySelectorAll('.tl-scene-toggle').forEach(b => b.classList.remove('active'));
                } else {
                    // 切换类别
                    this._activeScene = cat;
                    if (this._scenePanel) this._scenePanel.hidden = false;
                    document.querySelectorAll('.tl-scene-toggle').forEach(b => b.classList.toggle('active', b.dataset.scene === cat));
                    if (!this._sceneData) this._loadSceneDescriptors();
                    else this._renderScenePanel(cat);
                }
            });
            this._tlPanel.dataset.sceneDelegated = '1';
        }

        // 场景项点击（事件委托在 tl-scene-list 上）
        this._sceneList?.addEventListener('click', (e) => {
            const chip = e.target.closest('.tl-scene-chip');
            if (!chip) return;
            const mode = chip.dataset.mode;
            if (mode === 'range') {
                const hh = parseInt(chip.dataset.hh) || 0;
                const mm = parseInt(chip.dataset.mm) || 0;
                const ss = Math.floor(Math.random() * 60);
                this.timeOverride = { day: App.gameDay, hour: hh, minute: mm, second: ss };
                // 今天该时段已过 → 顺延到明天
                const curM = App.gameHour * 60 + App.gameMinute;
                const tgtM = hh * 60 + mm;
                if (tgtM <= curM) this.timeOverride.day += 1;
            } else if (mode === 'delta') {
                const dm = parseInt(chip.dataset.dm) || 0;
                let totalMin = App.gameHour * 60 + App.gameMinute + dm;
                const newDay = App.gameDay + Math.floor(totalMin / 1440);
                totalMin %= 1440;
                this.timeOverride = {
                    day: newDay,
                    hour: Math.floor(totalMin / 60),
                    minute: totalMin % 60,
                    second: Math.floor(App.gameSecond)
                };
            }
            this._updateTimePreview();
            // 高亮选中项（纯单选：清掉自动匹配高亮 + 旧选中，只保留当前点击项）
            this._sceneList.querySelectorAll('.tl-scene-chip').forEach(c => c.classList.remove('tl-scene-selected', 'tl-scene-active'));
            chip.classList.add('tl-scene-selected');
        });

        // 精确设置
        this._tlPreciseDay = document.getElementById('tl-precise-day');
        this._tlPreciseHour = document.getElementById('tl-precise-hour');
        this._tlPreciseMinute = document.getElementById('tl-precise-minute');
        this._tlPreciseSecond = document.getElementById('tl-precise-second');
        document.getElementById('tl-precise-apply')?.addEventListener('click', () => {
            this.timeOverride = {
                day: parseInt(this._tlPreciseDay?.value) || App.gameDay,
                hour: parseInt(this._tlPreciseHour?.value) || 0,
                minute: parseInt(this._tlPreciseMinute?.value) || 0,
                second: parseInt(this._tlPreciseSecond?.value) || 0
            };
            this._updateTimePreview();
        });

        // 地点：选择或新建
        document.getElementById('tl-loc-add')?.addEventListener('click', () => this._addCustomLocation());
        this._tlLocInput?.addEventListener('change', () => {
            const name = this._tlLocInput.value.trim();
            if (!name) return;
            const hit = Object.values(this._venuesCache).find((v) => v.name === name);
            if (hit) this._selectLocation(hit.id, name);
            else this._addCustomLocation();
        });

        document.getElementById('tl-clear')?.addEventListener('click', () => {
            this.timeOverride = null;
            this.pendingLocationId = null;
            this.pendingLocationName = null;
            this._activeScene = null;
            if (this._scenePanel) this._scenePanel.hidden = true;
            document.querySelectorAll('.tl-scene-toggle').forEach(b => b.classList.remove('active'));
            if (this._tlLocInput) this._tlLocInput.value = '';
            this._updateTimePreview();
            this._updateLocPreview();
        });
        document.getElementById('tl-apply')?.addEventListener('click', () => this._applyTimeLocation());

        this._loadVenues();
    },

    _openTimeLocationPicker(focus) {
        if (!this._tlPanel) return;
        // 预填精确设置为当前游戏时间
        if (this._tlPreciseDay && !this._tlPreciseDay.dataset.filled) {
            this._tlPreciseDay.value = App.gameDay;
            this._tlPreciseHour.value = App.gameHour;
            this._tlPreciseMinute.value = App.gameMinute;
            this._tlPreciseSecond.value = Math.floor(App.gameSecond);
            this._tlPreciseDay.dataset.filled = '1';
        }
        this._updateTimePreview();
        this._updateLocPreview();
        this._tlBackdrop.hidden = false;
        this._tlPanel.hidden = false;
        if (window.matchMedia('(max-width: 768px)').matches) {
            this._tlPanel.classList.add('tl-mobile-sheet');
        } else {
            this._tlPanel.classList.remove('tl-mobile-sheet');
        }
        if (focus === 'loc') {
            document.getElementById('tl-loc-section')?.scrollIntoView({ block: 'center' });
        } else if (focus === 'time') {
            document.getElementById('tl-time-section')?.scrollIntoView({ block: 'center' });
        }
    },

    _closeTimeLocationPicker() {
        if (this._tlPanel) this._tlPanel.hidden = true;
        if (this._tlBackdrop) this._tlBackdrop.hidden = true;
        // 重置场景面板
        this._activeScene = null;
        if (this._scenePanel) this._scenePanel.hidden = true;
        document.querySelectorAll('.tl-scene-toggle').forEach(b => b.classList.remove('active'));
    },

    async _applyTimeLocation() {
        // 立即把选择器里设定的时间/地点落库并刷新顶栏与地图（不依赖发聊天消息）
        const body = {};
        if (this.timeOverride) {
            body.game_day = this.timeOverride.day;
            body.game_hour = this.timeOverride.hour;
            body.game_minute = this.timeOverride.minute;
            body.game_second = this.timeOverride.second;
        }
        if (this.pendingLocationId) body.location = this.pendingLocationId;

        if (body.game_day === undefined && !body.location) {
            // 没有任何修改，仅关闭
            this._closeTimeLocationPicker();
            return;
        }
        try {
            const resp = await fetch('/api/apply-context', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await resp.json();
            if (data.success && data.character) {
                // skipTimeSync=false：让顶栏游戏时间也随返回的角色同步刷新
                App.refreshAll(data.character, false);
            } else if (data.error) {
                if (this._tlLocPreview) this._tlLocPreview.textContent = '应用失败：' + data.error;
                return; // 不关闭，让用户修正
            }
        } catch (e) {
            console.warn('应用时间/地点失败', e);
            if (this._tlLocPreview) this._tlLocPreview.textContent = '应用失败（网络错误）';
            return;
        }
        // 已落库，清除本地暂存
        this.timeOverride = null;
        this.pendingLocationId = null;
        this.pendingLocationName = null;
        if (this._tlLocInput) this._tlLocInput.value = '';
        this._updateTimePreview();
        this._updateLocPreview();
        this._closeTimeLocationPicker();
    },

    _gameDayToDateTimeLocal(day, h, m, s) {
        const d = new Date(2024, 8, 1 + day, h, m, s);
        const pad = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(h)}:${pad(m)}:${pad(s)}`;
    },

    _nextInSegment(seg) {
        // 区间与后端 TIME_PERIOD_MAP 时段划分对齐；深夜跨午夜（23→次日04:59）
        const SEGMENTS = { '早上': [6, 12], '中午': [11, 14], '下午': [14, 18], '晚上': [18, 23], '深夜': [23, 30] };
        const [lo, hi] = SEGMENTS[seg] || [8, 9];
        let hh = lo + Math.floor(Math.random() * (hi - lo));
        let mm = Math.floor(Math.random() * 60);
        let ss = Math.floor(Math.random() * 60);
        let dayOff = 0;
        if (hh >= 24) { hh -= 24; dayOff = 1; }
        const curM = App.gameHour * 60 + App.gameMinute;
        const tgtM = hh * 60 + mm + dayOff * 1440;
        // 今天该时段已过 → 顺延到明天（深夜不递延，直接落到今夜）
        if (tgtM <= curM && seg !== '深夜') dayOff += 1;
        return { day: App.gameDay + dayOff, hour: hh, minute: mm, second: ss };
    },

    async _loadSceneDescriptors() {
        try {
            const resp = await fetch('/api/time-scene-descriptors');
            const json = await resp.json();
            if (!json.success) return;
            this._sceneData = json.data;
            if (this._activeScene) this._renderScenePanel(this._activeScene);
        } catch (e) {
            console.warn('加载时间场景描述词失败', e);
        }
    },

    _renderScenePanel(category) {
        if (!this._sceneData) return;
        const items = this._sceneData[category];
        if (!items || !this._sceneList) return;
        this._sceneList.innerHTML = items.map((it) => {
            const label = it.mode === 'range'
                ? `${it.phrase}（${it.start}~${it.end}）`
                : `${it.phrase}（+${it.delta_minutes}分钟）`;
            let attrs = `data-mode="${it.mode}"`;
            if (it.mode === 'range') {
                const [hh, mm] = (it.start || '08:00').split(':');
                attrs += ` data-hh="${hh}" data-mm="${mm}"`;
            } else {
                attrs += ` data-dm="${it.delta_minutes}"`;
            }
            // 高亮匹配当前时间的 range 项
            let cls = 'tl-scene-chip';
            if (it.mode === 'range' && this._isInRange(it)) cls += ' tl-scene-active';
            return `<button class="${cls}" ${attrs} type="button">${label}</button>`;
        }).join('');
    },

    _isInRange(item) {
        if (item.mode !== 'range') return false;
        const [sh, sm] = (item.start || '00:00').split(':');
        const [eh, em] = (item.end || '23:59').split(':');
        const sMin = parseInt(sh) * 60 + parseInt(sm);
        const eMin = parseInt(eh) * 60 + parseInt(em);
        const curM = App.gameHour * 60 + App.gameMinute;
        return eMin >= sMin
            ? (curM >= sMin && curM < eMin)
            : (curM >= sMin || curM < eMin); // 跨午夜
    },

    _highlightCurrentScene() {
        // 已合并到 _renderScenePanel 中，打开面板时直接渲染高亮
    },

    _updateTimePreview() {
        if (!this._tlTimePreview) return;
        if (!this.timeOverride) {
            this._tlTimePreview.textContent = '未修改（将保持当前游戏时间）';
            return;
        }
        const o = this.timeOverride;
        const pad = (n) => String(n).padStart(2, '0');
        this._tlTimePreview.textContent =
            `将应用到下一句：第${o.day}天 ${pad(o.hour)}:${pad(o.minute)}:${pad(o.second)}`;
    },

    _updateLocPreview() {
        if (!this._tlLocPreview) return;
        if (!this.pendingLocationId) {
            this._tlLocPreview.textContent = '未指定（保持当前地点）';
            return;
        }
        this._tlLocPreview.textContent = `将移动到：${this.pendingLocationName || this.pendingLocationId}`;
    },

    async _loadVenues() {
        const name = App.character && App.character.name;
        if (!name) return;
        try {
            const resp = await fetch(`/api/character-locations?character_name=${encodeURIComponent(name)}`);
            const data = await resp.json();
            if (!data.success) return;
            const items = Object.entries(data.data || {}).map(([id, v]) => ({ id, name: v.name }));
            this._venuesCache = {};
            items.forEach((it) => { this._venuesCache[it.id] = it; });
            if (this._tlVenueList) {
                this._tlVenueList.innerHTML = items.map((it) => `<option value="${it.name}">`).join('');
            }
        } catch (e) {
            console.warn('加载地点列表失败', e);
        }
    },

    _selectLocation(id, name) {
        this.pendingLocationId = id;
        this.pendingLocationName = name;
        if (this._tlLocInput) this._tlLocInput.value = name || '';
        this._updateLocPreview();
    },

    async _addCustomLocation() {
        const name = (this._tlLocInput && this._tlLocInput.value || '').trim();
        if (!name) return;
        // 同名已知地点直接选中
        const hit = Object.values(this._venuesCache).find((v) => v.name === name);
        if (hit) { this._selectLocation(hit.id, name); return; }
        try {
            const resp = await fetch('/api/locations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name })
            });
            const data = await resp.json();
            if (data.success && data.venue_id) {
                this._selectLocation(data.venue_id, data.venue_name || name);
                await this._loadVenues();
            } else {
                this._tlLocPreview.textContent = '创建地点失败：' + (data.error || '未知错误');
            }
        } catch (e) {
            console.warn('创建地点失败', e);
            this._tlLocPreview.textContent = '创建地点失败（网络错误）';
        }
    },

    _computeSendTime() {
        if (this.timeOverride) {
            return { day: this.timeOverride.day, hour: this.timeOverride.hour, minute: this.timeOverride.minute, second: this.timeOverride.second };
        }
        return { day: App.gameDay, hour: App.gameHour, minute: App.gameMinute, second: Math.floor(App.gameSecond) };
    },

    /** 构建消息 innerHTML（不绑定事件） */
    _buildMsgHTML(speaker, content, gameDay, gameTime, reasoning, effects, msgIndex, isLastBubble, backendMsgIndex, instructText, ttsFullText) {
        let avatarHtml = '';
        if (speaker === 'character' && App.character?.name) {
            const char = App.character;
            let avatarPath;
            if (char?.avatar) {
                avatarPath = char.avatar;
            } else {
                // 按人物表 id 匹配头像（姓氏匹配会因多角色同姓而错乱，如沈念/沈疏筠都姓沈）
                const charId = char?.id != null ? char.id : 'default';
                avatarPath = `/static/avatars/${encodeURIComponent(charId)}.png`;
            }
            // onerror：先清掉自身避免死循环，再回退到 default.png（缺图角色统一占位）
            avatarHtml = `<img class="message-avatar" src="${avatarPath}" alt="${App.character.name}" onerror="this.onerror=null;this.src='/static/avatars/default.png'">`;
        }
        let html = avatarHtml + '<div class="message-content-wrapper">';
        html += content.replace(/\n/g, '<br>');
        if (isLastBubble && effects && Object.keys(effects).length > 0) {
            const effectText = Object.entries(effects)
                .map(([k, v]) => {
                    const cn = this.ATTR_CN_MAP[k] || k;
                    return `${cn}${v > 0 ? '+' : ''}${Number(v).toFixed(0)}`;
                })
                .join(', ');
            html += `<br><span class="effect-tag" data-effect-source="sync">[${effectText}]</span>`;
        }
        if (isLastBubble && speaker === 'character' && (!effects || Object.keys(effects).length === 0)) {
            html += `<br><span class="effect-tag" data-effect-source="async" style="display:none"></span>`;
        }
        if (gameDay !== null && gameDay !== undefined && gameTime) {
            html += `<div class="message-time">${formatTime(gameDay, gameTime)}</div>`;
        }
        if (isLastBubble && reasoning) {
            html += `<details class="reasoning-block"><summary> ${(App.character?.name || '角色')}的推理过程</summary><div class="reasoning-content">${reasoning.replace(/\n/g, '<br>')}</div></details>`;
        }
        const reanalyzeIndex = backendMsgIndex !== null ? backendMsgIndex : msgIndex;
        if (isLastBubble && speaker === 'character') {
            html += `<button class="btn-reanalyze" data-msg-index="${reanalyzeIndex}" title="重新分析本条回复的情绪变化">🔄 重新分析情绪</button>`;
        }
        if (speaker === 'character' && content) {
            const ttsText = ttsFullText || content;
            html += `<button class="btn-tts" data-text="${encodeURIComponent(ttsText)}" data-character="${App.character?.name || ''}" data-instruct="${encodeURIComponent(instructText || '')}" title="播放语音">🔊</button>`;
        }
        html += '</div>';
        return html;
    },

    appendMessage(speaker, content, gameDay = null, gameTime = null, reasoning = null, effects = null, msgIndex = null, isLastBubble = true, backendMsgIndex = null, instructText = null, ttsFullText = null) {
        if (msgIndex === null) {
            const mainContainer = document.getElementById('dialogue-messages');
            const existingMsgs = mainContainer.querySelectorAll('.msg:not(.system-msg)');
            msgIndex = existingMsgs.length;
        }
        const innerHTML = this._buildMsgHTML(speaker, content, gameDay, gameTime, reasoning, effects, msgIndex, isLastBubble, backendMsgIndex, instructText, ttsFullText);

        // 写入所有活跃容器（移动端全屏模式下双写同步）
        const containers = this._getAllMsgContainers();
        // 关键：在内容增长【前】抓取每个容器是否贴底（见 _captureNearBottom 注释）。
        const _nearBottom = this._captureNearBottom(containers);
        containers.forEach((container, idx) => {
            const div = document.createElement('div');
            div.className = `msg ${speaker}`;
            if (speaker === 'system') div.className = 'msg system-msg';
            div.setAttribute('data-msg-index', msgIndex);
            if (backendMsgIndex !== null) {
                div.setAttribute('data-backend-msg-index', backendMsgIndex);
            }
            // 打游戏时间键，供照片卡片按时间线归位（_insertPhotoByTime 使用）
            if (gameDay !== null && gameDay !== undefined && gameDay !== '') {
                div.setAttribute('data-gd', String(gameDay));
            }
            if (gameTime !== null && gameTime !== undefined && gameTime !== '') {
                div.setAttribute('data-gt', String(gameTime));
            }
            div.innerHTML = innerHTML;
            container.appendChild(div);

            // 只在主容器上绑定事件
            if (idx === 0) {
                if (speaker === 'character') {
                    const btn = div.querySelector('.btn-reanalyze');
                    if (btn) {
                        btn.addEventListener('click', (e) => {
                            e.stopPropagation();
                            this.reanalyzeEmotion(parseInt(btn.getAttribute('data-msg-index')), btn);
                        });
                    }
                    const ttsBtn = div.querySelector('.btn-tts');
                    if (ttsBtn) {
                        ttsBtn.addEventListener('click', (e) => {
                            e.stopPropagation();
                            this.playTTS(ttsBtn);
                        });
                    }
                }
            }
        });

        // 滚到最后：仅当用户【原本】就贴近底部（未向上浏览历史、且不在加载更早记录时）才自动跟随，
        // 否则保持用户当前滚动位置，避免浏览历史期间被新消息强行拽到底部。
        // 用的是插入前的贴底快照（_nearBottom），不受新消息高度影响。
        this._applyNearBottomScroll(containers, _nearBottom);
    },

    // ── AI伴侣系统升级 — 阶段三：TTS 语音播放（Web Audio API） ──
    _audioCtx: null,
    _currentAudioSource: null,
    _currentBtn: null,
    
    async playTTS(btn) {
        const text = decodeURIComponent(btn.getAttribute('data-text'));
        const characterName = btn.getAttribute('data-character');
        
        if (!text || !characterName) {
            console.warn('[TTS] 缺少文本或角色名');
            return;
        }
        
        // 提取最近 6 条对话历史（3 轮）
        const dialogueHistory = this.history.slice(-6).map(h => ({
            role: h.role,
            content: h.content
        }));
        
        // 状态：空闲(🔊) → 生成中(⏳) → 播放中(▶) → 完成(🔊)
        btn.textContent = '⏳ 生成中';
        btn.className = 'btn-tts tts-loading';
        btn.disabled = true;
        
        try {
            const response = await fetch('/api/tts/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text,
                    character_name: characterName,
                    dialogue_history: dialogueHistory
                })
            });
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.error || 'TTS 合成失败');
            }
            
            // 使用 Web Audio API 解码并播放
            const arrayBuffer = await response.arrayBuffer();
            
            if (!this._audioCtx) {
                this._audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            }
            
            // 停止上一次播放
            if (this._currentAudioSource) {
                try { this._currentAudioSource.stop(); } catch(e) {}
                this._currentAudioSource = null;
            }
            
            const audioBuffer = await this._audioCtx.decodeAudioData(arrayBuffer);
            const source = this._audioCtx.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(this._audioCtx.destination);
            
            this._currentAudioSource = source;
            this._currentBtn = btn;
            
            // 状态2: 播放中
            btn.textContent = '🔈 播放中';
            btn.className = 'btn-tts tts-playing';
            btn.disabled = true;
            
            source.start(0);
            
            // 状态3: 播放完毕
            source.onended = () => {
                btn.textContent = '✅ 已播放';
                btn.className = 'btn-tts tts-done';
                btn.disabled = false;
                this._currentAudioSource = null;
                this._currentBtn = null;
            };
            
        } catch (error) {
            console.error('[TTS] 播放失败:', error);
            btn.textContent = '🔊 重试';
            btn.className = 'btn-tts';
            btn.disabled = false;
            this._currentAudioSource = null;
            this._currentBtn = null;
            this.addSystemMsg(`🔊 语音合成失败: ${error.message}`);
        }
    },

    // ── AI伴侣系统升级 — 阶段三：背景描述渲染 ──
    appendBackground(background, gameDay = null, gameTime = null) {
        const containers = this._getAllMsgContainers();
        const _near = this._captureNearBottom(containers);
        containers.forEach(container => {
            const div = document.createElement('div');
            div.className = 'msg background-msg';
            if (gameDay !== null && gameDay !== undefined && gameDay !== '') {
                div.setAttribute('data-gd', String(gameDay));
            }
            if (gameTime !== null && gameTime !== undefined && gameTime !== '') {
                div.setAttribute('data-gt', String(gameTime));
            }
            div.innerHTML = `<span class="background-text">${background}</span>`;
            container.appendChild(div);
        });
        this._applyNearBottomScroll(containers, _near);
    },

    addSystemMsg(content) {
        const containers = this._getAllMsgContainers();
        const _near = this._captureNearBottom(containers);
        containers.forEach(container => {
            const div = document.createElement('div');
            div.className = 'msg system-msg';
            div.textContent = content;
            container.appendChild(div);
        });
        this._applyNearBottomScroll(containers, _near);
    },

    // ========== LLM 推荐面板 ==========

    _recommendLoading: false,

    /** 获取推荐面板元素（桌面端或移动端） */
    _recPanelEls() {
        if (this._isMobileChat()) {
            return {
                panel: document.getElementById('mobile-recommend-panel'),
                list: document.getElementById('mobile-recommend-list'),
                title: document.getElementById('mobile-recommend-title'),
                close: document.getElementById('mobile-recommend-close')
            };
        }
        return {
            panel: document.getElementById('recommend-panel'),
            list: document.getElementById('recommend-list'),
            title: document.getElementById('recommend-title'),
            close: document.getElementById('recommend-close')
        };
    },

    async loadRecommendations(relationType) {
        if (this._recommendLoading) return;

        const els = this._recPanelEls();
        if (!els.panel || !els.list || !els.title) return;

        this._recommendLoading = true;

        // 显示加载状态
        els.panel.classList.add('show');
        els.title.textContent = 'LLM 正在分析' + (App.character?.name || '角色') + '的状态...';
        els.list.innerHTML = '<div class="recommend-loading">思考中<span class="loading-dots"></span></div>';

        try {
            const resp = await fetch('/api/dialogue/recommend', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ relation_type: relationType })
            });
            const data = await resp.json();

            if (data.success && data.recommendations && data.recommendations.length > 0) {
                const relationCnMap = { trust: '信任', liking: '好感', respect: '尊重', intimacy: '亲密' };
                els.title.textContent = `LLM 推荐 — 提升${relationCnMap[relationType] || relationType}`;
                this._renderRecommendations(data.recommendations);
            } else {
                els.list.innerHTML = `<div class="recommend-error">${data.error || '推荐生成失败，请重试'}</div>`;
            }
        } catch (err) {
            console.error('加载推荐失败:', err);
            els.list.innerHTML = '<div class="recommend-error">网络错误，请重试</div>';
        } finally {
            this._recommendLoading = false;
        }
    },

    _renderRecommendations(items) {
        const els = this._recPanelEls();
        const list = els.list;
        if (!list) return;

        list.innerHTML = items.map((item, idx) => `
            <div class="recommend-item" data-idx="${idx}">
                <div class="rec-body">
                    <div class="rec-text">${this._escapeHtml(item.text)}</div>
                    ${item.reason ? `<div class="rec-reason">${this._escapeHtml(item.reason)}</div>` : ''}
                </div>
                <span class="rec-arrow">&#10132;</span>
            </div>
        `).join('');

        // 绑定点击事件 — 填入输入框，不自动发送
        list.querySelectorAll('.recommend-item').forEach(el => {
            el.addEventListener('click', () => {
                const text = items[parseInt(el.dataset.idx)].text;
                const inputEl = this._activeInput();
                if (inputEl) {
                    inputEl.value = text;
                    // 移动端禁止聚焦（键盘弹出问题）
                    if (!this._isMobileChat()) {
                        inputEl.focus();
                    }
                }
                this.closeRecommendPanel();
            });
        });

        // 移动端推荐面板关闭按钮
        if (els.close) {
            els.close.addEventListener('click', () => this.closeRecommendPanel());
        }
    },

    closeRecommendPanel() {
        const panel = document.getElementById('recommend-panel');
        if (panel) panel.classList.remove('show');
        const mobilePanel = document.getElementById('mobile-recommend-panel');
        if (mobilePanel) mobilePanel.classList.remove('show');
    },

    _reanalyzeLoading: false,

    async reanalyzeEmotion(msgIndex, btnElement) {
        if (this._reanalyzeLoading) return;
        this._reanalyzeLoading = true;

        // 显示加载状态
        const originalText = btnElement.textContent;
        btnElement.textContent = '⏳ 分析中...';
        btnElement.disabled = true;

        try {
            const resp = await fetch('/api/emotion/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message_index: msgIndex })
            });
            const data = await resp.json();

            if (data.success) {
                // 更新 effect-tag 显示
                const msgDiv = btnElement.closest('.msg');
                const effectTag = msgDiv.querySelector('.effect-tag');
                const hasEffects = data.effects && Object.keys(data.effects).length > 0;
                if (hasEffects) {
                    const effectText = Object.entries(data.effects)
                        .map(([k, v]) => {
                            const cn = this.ATTR_CN_MAP[k] || k;
                            return `${cn}${v > 0 ? '+' : ''}${Number(v).toFixed(0)}`;
                        })
                        .join(', ');
                    if (effectTag) {
                        effectTag.textContent = `[${effectText}]`;
                    } else {
                        // 重新创建 effect-tag
                        const newTag = document.createElement('span');
                        newTag.className = 'effect-tag';
                        newTag.textContent = `[${effectText}]`;
                        msgDiv.insertBefore(newTag, btnElement);
                    }
                } else {
                    if (effectTag) effectTag.remove();
                    btnElement.remove();
                }

                // 刷新角色状态面板
                StatusPanel.recordEffects(data.effects);
                if (data.character) {
                    App.refreshAll(data.character, true);
                }

                // 更新关系阶梯标签
                if (data.relationship_tier) {
                    this.updateRelationshipTier(data.relationship_tier);
                }

                btnElement.textContent = '✅ 已更新';
                setTimeout(() => {
                    btnElement.textContent = originalText;
                    btnElement.disabled = false;
                }, 2000);
            } else {
                btnElement.textContent = '❌ 失败';
                setTimeout(() => {
                    btnElement.textContent = originalText;
                    btnElement.disabled = false;
                }, 2000);
                console.error('重新分析失败:', data.error);
            }
        } catch (err) {
            console.error('重新分析异常:', err);
            const errMsg = err.message ? `❌ ${err.message.slice(0, 20)}` : '❌ 网络错误';
            btnElement.textContent = errMsg;
            setTimeout(() => {
                btnElement.textContent = originalText;
                btnElement.disabled = false;
            }, 2500);
        } finally {
            this._reanalyzeLoading = false;
        }
    },

    /**
     * 显示"她记住了..."提示条（点击可打开回忆面板）
     * @param {string} text - 提示文本
     */
    _showMemoryHint(text) {
        // 优先挂到独立的悬浮图层（不占用聊天流高度，避免布局跳动）
        const layer = this._isMobileChat() ? document.getElementById('mobile-memory-hint-layer') : document.getElementById('memory-hint-layer');
        const mount = layer || this._msgContainer();
        if (!mount) return;

        const now = Date.now();
        const COALESCE_MS = 4000; // 方案C：4秒内的多次提示合并为同一条，减少闪烁

        // 若已有提示条且仍在“新鲜期”，追加内容并重置淡出计时，而不是新建一条
        if (this._memoryHintEl && this._memoryHintShownAt && (now - this._memoryHintShownAt) < COALESCE_MS) {
            this._memoryHintEl.textContent += ` · ${text}`;
            if (this._memoryHintTimer) clearTimeout(this._memoryHintTimer);
            this._memoryHintTimer = setTimeout(() => this._fadeMemoryHint(), 5000);
            return;
        }

        const div = document.createElement('div');
        div.className = 'memory-hint-msg';
        div.textContent = `✨ ${text}`;
        div.title = '点击查看她的回忆';
        div.addEventListener('click', () => {
            if (typeof MemoryPanel !== 'undefined') {
                MemoryPanel.open();
            }
        });
        mount.appendChild(div);

        // 方案A：不再强制滚动到底部，避免打断用户翻阅历史消息
        // 方案B：挂在悬浮图层后，插入/淡出均不影响聊天区布局

        this._memoryHintEl = div;
        this._memoryHintShownAt = now;
        // 兜底 5 秒淡出必须始终存在：移动端触屏不会触发 mouseleave，
        // 若把暂停逻辑无条件绑定，会导致计时被清后永不重置、提示条卡死在顶部（①的根因）
        if (this._memoryHintTimer) clearTimeout(this._memoryHintTimer);
        this._memoryHintTimer = setTimeout(() => this._fadeMemoryHint(), 5000);

        // 仅在支持“真正悬停”的设备（桌面鼠标）上才暂停淡出倒计时；触屏设备不绑定，避免卡死
        const canHover = window.matchMedia && window.matchMedia('(hover: hover)').matches;
        if (canHover) {
            div.addEventListener('mouseenter', () => {
                if (this._memoryHintTimer) clearTimeout(this._memoryHintTimer);
            });
            div.addEventListener('mouseleave', () => {
                if (this._memoryHintEl) {
                    this._memoryHintTimer = setTimeout(() => this._fadeMemoryHint(), 2000);
                }
            });
        }
    },

    /** 提示条淡出并移除（与 _showMemoryHint 共用，避免重复逻辑） */
    _fadeMemoryHint() {
        const el = this._memoryHintEl;
        this._memoryHintEl = null;
        this._memoryHintShownAt = 0;
        if (!el) return;
        el.style.opacity = '0';
        el.style.transition = 'opacity 0.5s';
        setTimeout(() => { if (el.parentNode) el.remove(); }, 500);
    },

    /**
     * 轮询 /api/dialogue/extras，获取异步结果（快捷建议、记忆提示、情感时刻）。
     * 后端每项独立 ready 标志，前端逐项处理，全部完成后停止轮询。
     * 最多轮询 15 次，每次间隔 2 秒（总计最多 30 秒）。
     */
    _pollExtras(attempt = 0) {
        const MAX_ATTEMPTS = 15;  // 15次 × 2s = 30s，给推理模型足够时间
        const INTERVAL_MS = 2000;
        if (attempt >= MAX_ATTEMPTS) return;

        // 捕获当前版本号，防止切角色后旧轮询链继续消费新角色 extras
        const myVersion = this._pollVersion;

        // 记录本轮已处理过的项，避免重复显示
        this._extrasProcessed = this._extrasProcessed || { hints: false, memory: false, emotion: false, effects: false };

        setTimeout(async () => {
            try {
                const sid = this._currentExtrasSessionId;
                const url = sid ? `/api/dialogue/extras?session_id=${sid}` : '/api/dialogue/extras';
                const resp = await fetch(url);
                const data = await resp.json();
                if (!data.success) return;

                let allDone = true;

                // 1) 快捷建议按钮
                if (data.hints_ready && !this._extrasProcessed.hints) {
                    this._extrasProcessed.hints = true;
                    if (Array.isArray(data.hints) && data.hints.length > 0) {
                        this.updateHintButtons(data.hints);
                    }
                    // 注意：即使 hints 为空也标记为已处理，因为后端已生成完毕
                } else if (!data.hints_ready) {
                    allDone = false;
                }

                // 2) 记忆提示（仅汇总进小喇叭通知条，不再在对话面板浮现，避免与通知条重复）
                if (data.memory_ready && !this._extrasProcessed.memory) {
                    this._extrasProcessed.memory = true;
                    if (data.memory_count > 0) {
                        const txt = `她记住了${data.memory_count}件事`;
                        if (typeof App !== 'undefined' && App.announce) App.announce(txt, '✨');
                    }
                } else if (!data.memory_ready) {
                    allDone = false;
                }

                // 3) 情感时刻提示（仅汇总进小喇叭通知条，不再在对话面板浮现，避免与通知条重复）
                if (data.emotion_ready && !this._extrasProcessed.emotion) {
                    this._extrasProcessed.emotion = true;
                    if (data.emotion) {
                        const em = data.emotion;
                        const typeEmoji = { tender: '💕', conflict: '💔', breakthrough: '✨', memory: '🌙' };
                        const emoji = typeEmoji[em.moment_type] || '';
                        const text = `${emoji} ${em.summary || '特别的时刻'}`;
                        if (typeof App !== 'undefined' && App.announce) App.announce(text, '💗');
                    }
                } else if (!data.emotion_ready) {
                    allDone = false;
                }

                // 4) 情绪属性变化（异步本地 1.5B 分析，结果回传前端）
                if (data.effects_ready && !this._extrasProcessed.effects) {
                    this._extrasProcessed.effects = true;
                    if (data.effects && Object.keys(data.effects).length > 0) {
                        StatusPanel.recordEffects(data.effects);
                        // 回填到最后一条角色消息气泡的异步占位 effect-tag
                        this._injectEffectTag(data.effects);
                    }
                } else if (!data.effects_ready) {
                    allDone = false;
                }

                // 全部完成则停止，否则继续轮询
                if (!allDone) {
                    // 版本号变更（切角色）时停止旧轮询链
                    if (this._pollVersion !== myVersion) return;
                    this._pollExtras(attempt + 1);
                }
            } catch (e) {
                console.warn('[Extras] 轮询失败:', e);
            }
        }, INTERVAL_MS);
    },

    /**
     * 将异步情绪分析结果回填到最后一条角色消息气泡的 effect-tag 占位符。
     * 配合 appendMessage 中 data-effect-source="async" 的隐藏 span 使用。
     */
    _injectEffectTag(effects) {
        const container = this._msgContainer();
        if (!container) return;
        // 找到最后一条角色消息里的异步占位 effect-tag
        const asyncTags = container.querySelectorAll('.effect-tag[data-effect-source="async"]');
        if (asyncTags.length === 0) return;
        const target = asyncTags[asyncTags.length - 1]; // 用最后一条（对应最后一条角色消息）
        const effectText = Object.entries(effects)
            .map(([k, v]) => {
                const cn = this.ATTR_CN_MAP[k] || k;
                return `${cn}${v > 0 ? '+' : ''}${Number(v).toFixed(0)}`;
            })
            .join(', ');
        target.textContent = `[${effectText}]`;
        target.style.display = '';
        target.removeAttribute('data-effect-source');
    },

    _escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
};
