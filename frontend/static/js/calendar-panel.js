/* 日历面板：点击天气区域弹出日历，显示事件标记，允许修改游戏日期 */
const CalendarPanel = {
    START_DATE: new Date(2024, 8, 1),
    viewYear: 2024,
    viewMonth: 8,
    currentGameDay: 0,
    selectedDate: null,
    _eventsByDay: {}, // "YYYY-MM-DD" → [{title, type, ...}]
    _cases: [],

    init() {
        document.getElementById('weather-widget').addEventListener('click', () => {
            if (App.speedActive) {
                Toast.show('请先停止加速再使用日历', 'warning');
                return;
            }
            this.open();
        });
        const capsule = document.getElementById('mobile-info-capsule');
        if (capsule) {
            capsule.addEventListener('click', () => {
                if (App.speedActive) {
                    Toast.show('请先停止加速再使用日历', 'warning');
                    return;
                }
                this.open();
            });
        }
        document.getElementById('cal-prev').addEventListener('click', () => {
            this.viewMonth--;
            if (this.viewMonth < 0) { this.viewMonth = 11; this.viewYear--; }
            this.render();
        });
        document.getElementById('cal-next').addEventListener('click', () => {
            this.viewMonth++;
            if (this.viewMonth > 11) { this.viewMonth = 0; this.viewYear++; }
            this.render();
        });
        document.getElementById('cal-confirm').addEventListener('click', () => this.confirm());
        document.getElementById('cal-cancel').addEventListener('click', () => this.close());
        document.getElementById('cal-close-detail')?.addEventListener('click', () => this._closeDetail());
        document.getElementById('calendar-modal-overlay').addEventListener('click', (e) => {
            if (e.target.id === 'calendar-modal-overlay') this.close();
        });
    },

    open() {
        const gameDate = this._dayToDate(App.gameDay || 0);
        this.viewYear = gameDate.getFullYear();
        this.viewMonth = gameDate.getMonth();
        this.currentGameDay = App.gameDay || 0;
        this.selectedDate = null;
        document.getElementById('cal-selected').textContent = '未选择日期';
        document.getElementById('cal-day-detail').style.display = 'none';
        document.getElementById('calendar-modal-overlay').style.display = 'flex';
        this._loadEvents();
    },

    close() {
        document.getElementById('calendar-modal-overlay').style.display = 'none';
    },

    /* 游戏时间被时间选择器/时钟推进改变后，若日历正打开则重渲染“今天”高亮 */
    syncToCurrentDay() {
        const gd = App.gameDay || 0;
        if (gd === this.currentGameDay) return;  // 未变化则跳过，避免重复渲染
        const gameDate = this._dayToDate(gd);
        this.viewYear = gameDate.getFullYear();
        this.viewMonth = gameDate.getMonth();
        this.currentGameDay = gd;
        this.render();
    },

    async _loadEvents() {
        const name = App?.characterName || '';
        try {
            const [eventsR, casesR, missionsR] = await Promise.all([
                fetch('/api/events?limit=200'),
                name ? fetch(`/api/character/${name}/cases`) : Promise.resolve({json: () => ({success: true, data: []})}),
                name ? fetch(`/api/character/${name}/missions`).catch(() => ({json: () => ({success: true, data: []})})) : Promise.resolve({json: () => ({success: true, data: []})}),
            ]);
            const eventsData = await eventsR.json();
            const casesData = await casesR.json();
            const missionsData = await missionsR.json();

            // 按日期分组事件
            this._eventsByDay = {};
            (eventsData.data || []).forEach(e => {
                const day = e.game_day ?? e.day;
                if (day === undefined || day === null) return;
                const date = this._dayToDate(day);
                const key = this._dateKey(date);
                if (!this._eventsByDay[key]) this._eventsByDay[key] = [];
                this._eventsByDay[key].push({
                    title: e.title || e.content || '',
                    type: e.event_type || e.type || '',
                    time: e.game_time || e.time || '',
                });
            });

            // 合并任务
            const cases = (casesData.data || []).filter(c => c.status === 'running' || c.status === 'pending');
            const missions = (missionsData.data || []).filter(m => m.status === 'running' || m.status === 'pending');
            this._cases = [
                ...cases.map(c => ({ ...c, name: c.case_name, _isMission: false })),
                ...missions.map(m => ({ ...m, name: m.mission_name, _isMission: true })),
            ];
            this.render();
        } catch(e) {
            console.error('[Calendar] 加载事件失败:', e);
            this.render();
        }
    },

    _dateKey(date) {
        return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
    },

    render() {
        const grid = document.getElementById('calendar-grid');
        const title = document.getElementById('cal-title');
        title.textContent = `${this.viewYear}年${this.viewMonth + 1}月`;

        const firstDay = new Date(this.viewYear, this.viewMonth, 1);
        let startWeekday = firstDay.getDay() - 1;
        if (startWeekday < 0) startWeekday = 6;
        const daysInMonth = new Date(this.viewYear, this.viewMonth + 1, 0).getDate();

        const todayDate = this._dayToDate(this.currentGameDay);
        const todayY = todayDate.getFullYear();
        const todayM = todayDate.getMonth();
        const todayD = todayDate.getDate();

        const startY = this.START_DATE.getFullYear();
        const startM = this.START_DATE.getMonth();
        const startD = this.START_DATE.getDate();

        // 任务时间范围标记
        const caseDayRanges = this._cases.map(c => ({
            name: c.case_name,
            startDay: c.start_day,
            endDay: c.end_day,
            status: c.status,
        }));

        let html = '';
        for (let i = 0; i < startWeekday; i++) {
            html += '<div class="cal-day empty"></div>';
        }

        for (let d = 1; d <= daysInMonth; d++) {
            const classes = ['cal-day'];
            const dateObj = new Date(this.viewYear, this.viewMonth, d);
            const key = this._dateKey(dateObj);

            // 今天
            if (this.viewYear === todayY && this.viewMonth === todayM && d === todayD) {
                classes.push('today');
            }
            // 选中
            if (this.selectedDate &&
                this.selectedDate.getFullYear() === this.viewYear &&
                this.selectedDate.getMonth() === this.viewMonth &&
                this.selectedDate.getDate() === d) {
                classes.push('selected');
            }
            // 早于起始
            const isBeforeStart = (this.viewYear < startY) ||
                (this.viewYear === startY && this.viewMonth < startM) ||
                (this.viewYear === startY && this.viewMonth === startM && d < startD);
            if (isBeforeStart) {
                classes.push('past');
            }

            // 事件标记：小圆点（多种颜色）
            const events = this._eventsByDay[key] || [];
            const hasCase = caseDayRanges.some(c => {
                const cStart = this._dayToDate(c.startDay);
                const cEnd = this._dayToDate(c.endDay);
                return dateObj >= cStart && dateObj <= cEnd;
            });

            let dotsHtml = '';
            if (events.length > 0) dotsHtml += '<span class="cal-dot cal-dot-event" title="有事件"></span>';
            if (hasCase) dotsHtml += '<span class="cal-dot cal-dot-case" title="有任务进行中"></span>';

            html += `<div class="${classes.join(' ')}" data-day="${d}">${d}${dotsHtml}</div>`;
        }

        grid.innerHTML = html;

        // 绑定点击
        grid.querySelectorAll('.cal-day:not(.empty):not(.past)').forEach(el => {
            el.addEventListener('click', () => {
                const day = parseInt(el.dataset.day);
                this.selectedDate = new Date(this.viewYear, this.viewMonth, day);
                grid.querySelectorAll('.cal-day.selected').forEach(s => s.classList.remove('selected'));
                el.classList.add('selected');
                const weekdayNames = ['日', '一', '二', '三', '四', '五', '六'];
                const wd = weekdayNames[this.selectedDate.getDay()];
                document.getElementById('cal-selected').textContent =
                    `已选择：${this.viewYear}年${this.viewMonth + 1}月${day}日 星期${wd}`;
                this._showDayDetail(day);
            });
        });
    },

    _showDayDetail(day) {
        const dateObj = new Date(this.viewYear, this.viewMonth, day);
        const key = this._dateKey(dateObj);
        const events = this._eventsByDay[key] || [];

        // 当天运行的任务
        const name = App?.characterName || '';
        const runningCases = this._cases.filter(c => {
            const cStart = this._dayToDate(c.startDay);
            const cEnd = this._dayToDate(c.endDay);
            return dateObj >= cStart && dateObj <= cEnd;
        });

        const detailEl = document.getElementById('cal-day-detail');
        let html = `<div class="cal-detail-header">
            <span>${this.viewYear}年${this.viewMonth + 1}月${day}日</span>
            <button class="btn-icon" id="cal-close-detail" onclick="CalendarPanel._closeDetail()"><span class="material-symbols-outlined">close</span></button>
        </div>`;

        if (runningCases.length > 0) {
            html += '<div class="cal-detail-section"><strong>📋 进行中的任务</strong></div>';
            runningCases.forEach(c => {
                const label = c._isMission ? '任务' : '任务';
                html += `<div class="cal-detail-item cal-detail-case">${label}：${c.name} (${c.status === 'running' ? '进行中' : '等待中'})</div>`;
            });
        }

        if (events.length > 0) {
            html += `<div class="cal-detail-section"><strong>📌 当日事件 (${events.length})</strong></div>`;
            events.slice(0, 10).forEach(e => {
                const time = e.time ? e.time.slice(0, 5) : '';
                html += `<div class="cal-detail-item">${time} ${e.title}</div>`;
            });
            if (events.length > 10) {
                html += `<div class="cal-detail-item" style="color:var(--text-tertiary)">...还有 ${events.length - 10} 条</div>`;
            }
        } else {
            html += '<div class="cal-detail-section" style="color:var(--text-tertiary)">暂无事件记录</div>';
        }

        detailEl.innerHTML = html;
        detailEl.style.display = 'block';
    },

    _closeDetail() {
        document.getElementById('cal-day-detail').style.display = 'none';
    },

    async confirm() {
        if (!this.selectedDate) {
            Toast.show('请先选择一个日期', 'warning');
            return;
        }
        const year = this.selectedDate.getFullYear();
        const month = this.selectedDate.getMonth() + 1;
        const day = this.selectedDate.getDate();
        try {
            const resp = await fetch('/api/calendar/set-date', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ year, month, day })
            });
            const data = await resp.json();
            if (data.success) {
                App.gameDay = data.game_day;
                if (data.character) await App.refreshAll(data.character);
                if (data.weather) App.updateWeather(data.weather);
                Toast.show(`日期已调整为 ${year}年${month}月${day}日`, 'success');
                this.close();
            } else {
                Toast.show('日期设置失败：' + (data.error || '未知错误'), 'error');
            }
        } catch (err) {
            console.error('[Calendar] 设置日期失败:', err);
            Toast.show('网络错误，请检查连接', 'error');
        }
    },

    _dayToDate(gameDay) {
        const d = new Date(this.START_DATE);
        d.setDate(d.getDate() + gameDay);
        return d;
    }
};
