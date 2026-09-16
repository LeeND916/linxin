/** 任务抉择事件选择面板 — 当任务事件含 choices 时弹出（五幕结构：event_id + choice_index） */
const ChoicePanel = {
    _eventId: null,
    _callback: null,

    init() {
        document.getElementById('choice-overlay')?.addEventListener('click', (e) => {
            if (e.target.id === 'choice-overlay') this.close();
        });
    },

    /** 显示抉择面板
     * @param {string|number} eventId - EventLog ID
     * @param {Array} choices - 选项列表 [{text, state_changes?}]
     * @param {Function} [callback] - 选择完成回调
     */
    show(eventId, choices, callback) {
        this._eventId = eventId;
        this._callback = callback;
        this._renderChoices(choices);
    },

    _renderChoices(choices) {
        const container = document.getElementById('choice-options');
        container.innerHTML = choices.map((c, i) => `
            <button class="choice-btn" data-index="${i}">
                <span class="choice-icon">${i === 0 ? '🔹' : '🔸'}</span>
                <span class="choice-text">${c.text || c}</span>
            </button>
        `).join('');

        container.querySelectorAll('.choice-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.index);
                this._select(idx);
            });
        });

        document.getElementById('choice-overlay').style.display = 'flex';
    },

    async _select(index) {
        try {
            const url = `/api/character/${App.characterName}/missions/choose`;
            const body = JSON.stringify({ event_id: this._eventId, choice_index: index });

            const r = await fetch(url, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body,
            });
            const data = await r.json();
            if (data.success) {
                if (this._callback) this._callback(data.data);
                this._appendChoiceResult(index);
            } else {
                console.warn('Choice failed:', data.error);
            }
        } catch(e) {
            console.error('Choice error:', e);
        }
        this.close();
    },

    _appendChoiceResult(index) {
        const choiceBtns = document.querySelectorAll('#choice-options .choice-btn');
        const text = choiceBtns[index]?.querySelector('.choice-text')?.textContent || '选择了选项' + (index+1);
        const msgDiv = document.createElement('div');
        msgDiv.className = 'choice-result-msg';
        msgDiv.innerHTML = `<span class="material-symbols-outlined">touch_app</span> 你的选择：${text}`;
        const chatArea = document.querySelector('.dialogue-messages');
        if (chatArea) {
            chatArea.appendChild(msgDiv);
            chatArea.scrollTop = chatArea.scrollHeight;
        }
    },

    close() {
        document.getElementById('choice-overlay').style.display = 'none';
        this._eventId = null;
    },
};

window.ChoicePanel = ChoicePanel;
