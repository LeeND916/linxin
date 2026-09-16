/* 更新说明面板 */
const ChangelogPanel = {
    overlay: null,
    body: null,
    loaded: false,

    init() {
        this.overlay = document.getElementById('changelog-modal-overlay');
        this.body = document.getElementById('changelog-modal-body');

        document.getElementById('btn-changelog').addEventListener('click', () => this.open());
        document.getElementById('btn-close-changelog').addEventListener('click', () => this.close());
        this.overlay.addEventListener('click', (e) => {
            if (e.target === this.overlay) this.close();
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.overlay.style.display !== 'none') this.close();
        });
    },

    async open() {
        this.overlay.style.display = 'flex';

        if (!this.loaded) {
            try {
                const resp = await fetch('/api/changelog');
                const data = await resp.json();
                this.body.innerHTML = this.renderMarkdown(data.content);
                this.loaded = true;
            } catch (e) {
                this.body.innerHTML = '<div class="changelog-loading">加载失败，请稍后重试</div>';
            }
        }
    },

    close() {
        this.overlay.style.display = 'none';
    },

    renderMarkdown(md) {
        // 简单 Markdown → HTML（只处理 changelog 中会用到的语法）
        let html = md;
        // headings
        html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
        html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
        // hr
        html = html.replace(/^---$/gm, '<hr>');
        // unordered list items
        html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
        // wrap consecutive <li> in <ul>
        html = html.replace(/(<li>.*?<\/li>(\n|$))+/g, '<ul>$&</ul>');
        // bold
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        // inline code
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

        return html;
    }
};

// 自动初始化
document.addEventListener('DOMContentLoaded', () => {
    ChangelogPanel.init();
});
