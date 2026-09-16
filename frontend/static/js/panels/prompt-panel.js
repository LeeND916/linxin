/**
 * 提示词管理面板 — 系统设置「提示词」Tab
 * 支持：分类浏览、编辑、预览、保存、重置、版本历史、安全分级
 *
 * 编辑器策略：默认使用 textarea（稳定可靠），Monaco 加载成功后自动升级
 */
const PromptPanel = {
    _prompts: [],
    _current: null,
    _loading: false,
    _mcEditor: null,          // Monaco editor 实例（null = 未加载）
    _useMonaco: false,
    _testPassed: false,       // 🧪 当前选中项是否通过测试

    async init() {
        this.bindButtons();
        this._tryInitMonaco();   // 不阻塞，失败也不影响功能
        await this.loadPrompts();
    },

    bindButtons() {
        document.getElementById('btn-prompt-save').addEventListener('click', () => this.save());
        document.getElementById('btn-prompt-reset').addEventListener('click', () => this.reset());
        document.getElementById('btn-prompt-preview').addEventListener('click', () => this.preview());
        document.getElementById('btn-prompt-test').addEventListener('click', () => this.test());
        document.getElementById('btn-prompt-versions').addEventListener('click', () => this.showVersions());
    },

    /** 尝试加载 Monaco，成功则隐藏 textarea 启用 Monaco */
    _tryInitMonaco() {
        if (typeof require === 'undefined') {
            console.warn('PromptPanel: Monaco loader 未加载，使用 textarea');
            return;
        }
        try {
            require.config({
                paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs' }
            });
            require(['vs/editor/editor.main'], () => {
                try {
                    // 注册变量占位符语言高亮
                    monaco.languages.setMonarchTokensProvider('prompt-template', {
                        tokenizer: {
                            root: [[/\{[a-zA-Z_][\w.]*\}/, 'variable']]
                        }
                    });
                    monaco.editor.defineTheme('prompt-theme', {
                        base: 'vs',
                        inherit: true,
                        rules: [{ token: 'variable', foreground: 'e06c75', fontStyle: 'bold' }],
                        colors: { 'editor.background': '#fafbfc' }
                    });

                    const container = document.getElementById('prompt-editor-mc');
                    if (!container) return;

                    this._mcEditor = monaco.editor.create(container, {
                        value: '',
                        language: 'prompt-template',
                        theme: 'prompt-theme',
                        readOnly: true,
                        minimap: { enabled: false },
                        lineNumbers: 'on',
                        wordWrap: 'on',
                        fontSize: 13,
                        lineHeight: 20,
                        scrollBeyondLastLine: false,
                        automaticLayout: true,
                        tabSize: 2,
                    });

                    // 变量自动补全
                    monaco.languages.registerCompletionItemProvider('prompt-template', {
                        triggerCharacters: ['{'],
                        provideCompletionItems: () => {
                            const vars = [
                                'character.name', 'character.age', 'character.identity_label',
                                'character.major', 'character.personality', 'character.personality_tone',
                                'character.hometown', 'character.hobbies', 'character.family',
                                'character.location', 'character.skills_summary', 'character.goals_summary',
                                'player.nickname', 'player.identity',
                                'status.health', 'status.energy', 'status.mood', 'status.stress',
                                'status.happiness', 'status.loneliness', 'status.confidence',
                                'status.joy', 'status.anger', 'status.hunger', 'status.hygiene',
                                'relation.player_trust', 'relation.player_affection',
                                'relation.player_respect', 'relation.player_intimacy',
                                'context.history', 'context.user_message', 'context.news_section',
                                'time.game_day', 'time.period_text', 'weather.desc',
                            ];
                            return {
                                suggestions: vars.map(name => ({
                                    label: name,
                                    kind: monaco.languages.CompletionItemKind.Variable,
                                    insertText: name + '}',
                                    detail: name,
                                }))
                            };
                        }
                    });

                    // 切换：隐藏 textarea，显示 Monaco
                    document.getElementById('prompt-editor').style.display = 'none';
                    container.style.display = 'block';
                    this._useMonaco = true;

                    // 如果已有选中内容，同步到 Monaco
                    if (this._current) {
                        this._mcEditor.setValue(this._current.content);
                        this._mcEditor.updateOptions({ readOnly: this._current.is_system });
                    }
                    console.log('PromptPanel: Monaco 编辑器已启动');
                } catch (e) {
                    console.warn('PromptPanel: Monaco 初始化失败，使用 textarea', e);
                }
            }, (err) => {
                console.warn('PromptPanel: Monaco 模块加载失败，使用 textarea', err);
            });
        } catch (e) {
            console.warn('PromptPanel: Monaco 配置失败，使用 textarea', e);
        }
    },

    _getContent() {
        return this._useMonaco && this._mcEditor
            ? this._mcEditor.getValue()
            : document.getElementById('prompt-editor').value;
    },

    _setContent(text) {
        if (this._useMonaco && this._mcEditor) {
            this._mcEditor.setValue(text);
        } else {
            document.getElementById('prompt-editor').value = text;
        }
    },

    _setReadOnly(ro) {
        if (this._useMonaco && this._mcEditor) {
            this._mcEditor.updateOptions({ readOnly: ro });
        } else {
            document.getElementById('prompt-editor').readOnly = ro;
        }
    },

    async loadPrompts() {
        try {
            const resp = await fetch('/api/prompts');
            const data = await resp.json();
            if (data.success) {
                this._prompts = data.data;
                this.renderSidebar();
                if (this._current) this.selectPrompt(this._current.id);
            }
        } catch (e) {
            console.error('PromptPanel: 加载失败', e);
        }
    },

    renderSidebar() {
        const cats = {};
        for (const p of this._prompts) {
            const cat = p.category || 'other';
            if (!cats[cat]) cats[cat] = [];
            cats[cat].push(p);
        }
        for (const cat of Object.keys(cats)) {
            const el = document.getElementById('prompt-items-' + cat);
            if (!el) continue;
            el.innerHTML = cats[cat].map(p => {
                const locked = p.is_system ? ' 🔒' : '';
                const test = p.require_test ? ' 🧪' : '';
                const custom = p.has_custom ? ' *' : '';
                const think = p.thinking_disabled
                    ? ' <span style="font-size:10px;line-height:1;padding:1px 5px;border-radius:6px;background:#fdecea;color:#c0392b;border:1px solid #f5c6c0;white-space:nowrap;" title="该提示词对应的函数已关闭思考模式">🚫思考关</span>'
                    : '';
                return `<div class="prompt-item" data-id="${p.id}" onclick="PromptPanel.selectPrompt('${p.id}')">
                    <span>${p.label}${locked}${test}${custom}${think}</span>
                    <span class="prompt-item-ver">v${p.version}</span>
                </div>`;
            }).join('');
            el.style.display = 'block';
        }
    },

    toggleCategory(cat) {
        const el = document.getElementById('prompt-items-' + cat);
        if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
    },

    async selectPrompt(id) {
        if (this._loading) return;
        this._loading = true;

        document.querySelectorAll('.prompt-item').forEach(e => e.classList.remove('active'));
        const itemEl = document.querySelector(`.prompt-item[data-id="${id}"]`);
        if (itemEl) itemEl.classList.add('active');

        try {
            const resp = await fetch(`/api/prompts/${encodeURIComponent(id)}`);
            const data = await resp.json();
            if (!data.success) { this._loading = false; return; }
            this._current = data.data;

            document.getElementById('prompt-editor-title').textContent = data.data.label;
            const thinkInfo = document.getElementById('prompt-think-info');
            if (data.data.thinking_disabled) {
                thinkInfo.textContent = '🚫 思考已关闭';
                thinkInfo.style.color = '#c0392b';
                thinkInfo.style.background = '#fdecea';
                thinkInfo.style.border = '1px solid #f5c6c0';
            } else {
                thinkInfo.textContent = '💡 思考开启';
                thinkInfo.style.color = '#2e7d32';
                thinkInfo.style.background = '#eafaf0';
                thinkInfo.style.border = '1px solid #bfe6c8';
            }
            thinkInfo.style.fontSize = '11px';
            thinkInfo.style.lineHeight = '1';
            thinkInfo.style.padding = '2px 8px';
            thinkInfo.style.borderRadius = '8px';
            thinkInfo.style.marginLeft = '8px';
            thinkInfo.style.whiteSpace = 'nowrap';
            const badge = document.getElementById('prompt-editor-badge');
            if (data.data.is_system) {
                badge.textContent = '🔒 只读';
                badge.className = 'prompt-badge badge-red';
            } else if (data.data.require_test) {
                badge.textContent = '🧪 需验证';
                badge.className = 'prompt-badge badge-yellow';
            } else {
                badge.textContent = data.data.has_custom ? '✏️ 已自定义' : '';
                badge.className = 'prompt-badge badge-green';
            }

            this._setContent(data.data.content);
            this._setReadOnly(data.data.is_system);

            this._testPassed = false;   // 切换 prompt 时重置测试状态
            document.getElementById('btn-prompt-save').disabled = data.data.is_system || data.data.require_test;
            document.getElementById('btn-prompt-reset').disabled = false;
            document.getElementById('btn-prompt-preview').disabled = false;
            document.getElementById('btn-prompt-test').style.display = data.data.require_test ? 'inline-block' : 'none';
            document.getElementById('btn-prompt-test').disabled = false;
            document.getElementById('prompt-status').textContent = '';
        } catch (e) {
            console.error('PromptPanel: 加载提示词失败', e);
        }
        this._loading = false;
    },

    async save() {
        if (!this._current || this._current.is_system) return;
        // 🧪 需验证级 — 必须先通过测试
        if (this._current.require_test && !this._testPassed) {
            alert('请先点击 🧪 测试 验证后再保存');
            return;
        }
        const content = this._getContent();
        if (!content.trim()) return;

        const st = document.getElementById('prompt-status');
        st.textContent = '保存中...';
        try {
            const resp = await fetch(`/api/prompts/${encodeURIComponent(this._current.id)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content })
            });
            const data = await resp.json();
            st.textContent = data.success ? '✅ 已保存' : '❌ ' + data.error;
            if (data.success) this._current.has_custom = true;
        } catch (e) {
            st.textContent = '❌ 网络错误';
        }
        setTimeout(() => { st.textContent = ''; }, 3000);
        this.renderSidebar();
    },

    async reset() {
        if (!this._current || !confirm('确定重置为代码默认值？')) return;
        const st = document.getElementById('prompt-status');
        st.textContent = '重置中...';
        try {
            const resp = await fetch(`/api/prompts/${encodeURIComponent(this._current.id)}/reset`, { method: 'POST' });
            const data = await resp.json();
            st.textContent = data.success ? '✅ 已重置' : '❌ ' + data.error;
            if (data.success) {
                this.selectPrompt(this._current.id);
                this._current.has_custom = false;
            }
        } catch (e) {
            st.textContent = '❌ 网络错误';
        }
        setTimeout(() => { st.textContent = ''; }, 3000);
    },

    async preview() {
        if (!this._current) return;
        const panel = document.getElementById('prompt-preview');
        const content = document.getElementById('prompt-preview-content');
        panel.style.display = 'block';
        content.textContent = '加载中...';
        try {
            const charResp = await fetch('/api/character');
            const charData = await charResp.json();
            const charId = charData.data?.id;
            if (!charId) { content.textContent = '无活跃角色'; return; }
            const resp = await fetch(`/api/prompts/${encodeURIComponent(this._current.id)}/preview?char_id=${charId}`);
            const data = await resp.json();
            content.textContent = data.success ? data.data.rendered : data.error;
        } catch (e) {
            content.textContent = '预览失败';
        }
    },

    test() {
        if (!this._current) return;
        const st = document.getElementById('prompt-status');
        const btn = document.getElementById('btn-prompt-test');
        st.textContent = '⏳ 测试中...';
        btn.disabled = true;

        fetch('/api/character').then(r => r.json()).then(charData => {
            const charId = charData.data?.id;
            return fetch(`/api/prompts/${encodeURIComponent(this._current.id)}/preview?char_id=${charId}`);
        }).then(r => r.json()).then(previewData => {
            if (!previewData.success) throw new Error(previewData.error || '预览失败');
            return fetch(`/api/prompts/${encodeURIComponent(this._current.id)}/validate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sample: previewData.data.rendered })
            });
        }).then(r => r.json()).then(result => {
            if (result.success) {
                st.textContent = '✅ 格式验证通过';
                this._testPassed = true;
                document.getElementById('btn-prompt-save').disabled = false;
            } else {
                st.textContent = '❌ ' + (result.error || '格式异常');
            }
        }).catch(e => {
            st.textContent = '⚠ ' + (e.message || '测试失败');
        }).finally(() => {
            btn.disabled = false;
            setTimeout(() => { st.textContent = ''; btn.disabled = false; }, 5000);
        });
    },

    async showVersions() {
        if (!this._current) return;
        const st = document.getElementById('prompt-status');
        st.textContent = '加载版本...';
        try {
            const resp = await fetch(`/api/prompts/${encodeURIComponent(this._current.id)}/versions`);
            const data = await resp.json();
            if (!data.success || !data.data.length) {
                st.textContent = '📭 暂无版本历史（保存后自动记录）';
                setTimeout(() => { st.textContent = ''; }, 3000);
                return;
            }
            const versions = data.data;
            const msg = versions.map(v => `v${v.version} — ${v.updated_at?.slice(0,16) || '?'}`).join('\n');
            const rollbackTo = prompt(`版本历史：\n\n${msg}\n\n输入版本号回退，或取消`, '');
            if (rollbackTo) {
                const resp2 = await fetch(`/api/prompts/${encodeURIComponent(this._current.id)}/rollback/${rollbackTo}`, { method: 'POST' });
                const d2 = await resp2.json();
                alert(d2.success ? '✅ 已回退' : '❌ ' + d2.error);
                if (d2.success) this.selectPrompt(this._current.id);
            }
        } catch (e) {
            alert('获取版本失败');
        }
    }
};
