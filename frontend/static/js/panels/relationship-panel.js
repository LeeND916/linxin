/* 朋友关系面板 */
const RelationshipPanel = {
    update(friends) {
        const container = document.getElementById('friends-list');
        if (!friends || friends.length === 0) {
            container.innerHTML = '<div style="color:var(--text-muted);font-size:12px;">暂无好友</div>';
            return;
        }
        container.innerHTML = friends.map(f => `
            <div class="friend-card" data-name="${this._escAttr(f.name)}">
                <div class="friend-header">
                    <span class="friend-name">${this._escHtml(f.name)}</span>
                    <span class="friend-role">${this._escHtml(f.role || '')}</span>
                    <span class="friend-relation-tag">${this._relationLabel(f.relation_type)}</span>
                </div>
                <div class="friend-bars">
                    <span class="friend-bar-item" data-attr="closeness">
                        <span class="bar-dot bar-closeness"></span>亲密度 <span class="friend-value">${(f.closeness || 0).toFixed(0)}</span>
                        <span class="friend-delta"></span>
                    </span>
                    <span class="friend-bar-item" data-attr="trust">
                        <span class="bar-dot bar-trust"></span>信任 <span class="friend-value">${(f.trust || 0).toFixed(0)}</span>
                        <span class="friend-delta"></span>
                    </span>
                    <span class="friend-bar-item" data-attr="affection">
                        <span class="bar-dot bar-affection"></span>好感 <span class="friend-value">${(f.affection || 0).toFixed(0)}</span>
                        <span class="friend-delta"></span>
                    </span>
                </div>
                <div class="friend-states">
                    <span class="friend-state-tag state-loneliness">孤 ${(f.loneliness || 0).toFixed(0)}</span>
                    <span class="friend-state-tag state-happiness">幸 ${(f.happiness || 0).toFixed(0)}</span>
                    <span class="friend-state-tag state-stress">压 ${(f.stress || 0).toFixed(0)}</span>
                    <span class="friend-state-tag state-mood">心 ${(f.mood || 0).toFixed(0)}</span>
                    <span class="friend-state-tag state-energy">精 ${(f.energy || 0).toFixed(0)}</span>
                    <span class="friend-state-tag state-clarity">清 ${(f.clarity || 0).toFixed(0)}</span>
                </div>
                <div class="friend-bio">${this._escHtml(f.personality || '')} · ${this._escHtml(f.bio || '')}</div>
                ${f.last_interaction_time ? `
                <div class="friend-last-interaction">
                    <span class="friend-interaction-time">${this._escHtml(f.last_interaction_time)}</span>
                    <span class="friend-interaction-content">${this._escHtml(f.last_interaction_content || '')}</span>
                </div>` : ''}
            </div>
        `).join('');
    },

    /**
     * 批量应用关系变化（从加速器返回），更新数值并触发闪烁 5 秒
     */
    applyRelationChanges(relationChanges) {
        if (!relationChanges) return;

        const changes = relationChanges.changes || [];
        changes.forEach(change => {
            const name = change.name;
            const card = document.querySelector(`.friend-card[data-name="${this._escAttr(name)}"]`);
            if (!card) return;

            const updates = [
                { attr: 'closeness', newVal: change.new ? change.new.closeness : null, delta: change.intimacy },
                { attr: 'trust', newVal: change.new ? change.new.trust : null, delta: change.trust },
                { attr: 'affection', newVal: change.new ? change.new.affection : null, delta: change.affection },
                { attr: 'loneliness', newVal: change.new ? change.new.loneliness : null, delta: change.loneliness },
                { attr: 'happiness', newVal: change.new ? change.new.happiness : null, delta: change.happiness },
                { attr: 'stress', newVal: change.new ? change.new.stress : null, delta: change.stress },
                { attr: 'mood', newVal: change.new ? change.new.mood : null, delta: change.mood },
                { attr: 'energy', newVal: change.new ? change.new.energy : null, delta: change.energy },
                { attr: 'clarity', newVal: change.new ? change.new.clarity : null, delta: change.clarity },
            ];

            updates.forEach(u => {
                if (u.newVal === null && u.delta === 0) return;
                const barItem = card.querySelector(`.friend-bar-item[data-attr="${u.attr}"]`);
                if (!barItem) return;

                const valueEl = barItem.querySelector('.friend-value');
                const deltaEl = barItem.querySelector('.friend-delta');

                if (valueEl && u.newVal !== null) {
                    valueEl.textContent = u.newVal.toFixed(0);
                }

                if (deltaEl && u.delta !== 0) {
                    const sign = u.delta > 0 ? '+' : '';
                    const cls = u.delta > 0 ? 'delta-up' : 'delta-down';
                    deltaEl.textContent = `${sign}${u.delta.toFixed(0)}`;
                    deltaEl.className = `friend-delta ${cls} flash`;
                }
            });

            card.classList.add('card-highlight');
            setTimeout(() => {
                card.classList.remove('card-highlight');
                card.querySelectorAll('.friend-delta.flash').forEach(el => {
                    el.classList.remove('flash');
                });
            }, 5000);
        });
    },

    _escHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    _relationLabel(type) {
        const map = {friend:'朋友',family:'家人',mentor:'导师',rival:'对手',enemy:'敌人',
            colleague:'同事',ex_boyfriend:'前男友',client:'客户',acquaintance:'普通关系',protege:'后辈'};
        const label = map[type] || type || '';
        return label ? `<span class="relation-type-tag relation-${type || 'friend'}">${label}</span>` : '';
    },

    _escAttr(text) {
        return (text || '').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
};
