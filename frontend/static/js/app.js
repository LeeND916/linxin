/* ============================================================
   邻信 主应用
   ============================================================ */

/* Toast 通知 */
const Toast = {
    show(msg, type = 'info', duration = 3000) {
        const container = document.getElementById('toast-container');
        const el = document.createElement('div');
        el.className = `toast ${type}`;
        el.textContent = msg;
        container.appendChild(el);
        let timer = setTimeout(() => el.remove(), duration);
        // 鼠标悬停时暂停倒计时，移开后按原时长自动消失
        el.addEventListener('mouseenter', () => clearTimeout(timer));
        el.addEventListener('mouseleave', () => { timer = setTimeout(() => el.remove(), duration); });
        return el;
    }
};

/* 主应用 */
const App = {
    character: null,
    characters: [],  // 所有角色列表
    sessionId: null,  // 当前会话 ID，格式 YYYY-MM-DD_HH-mm-ss

    /* 自动保存 */
    autoSaveInterval: null,
    lastSaveTime: null,

    /* 前端时钟状态 — 独立于后端运行 */
    gameDay: 0,
    gameHour: 8,
    gameMinute: 0,
    gameSecond: 0,
    gameClockInterval: null,

    /* 加速引擎状态 */
    speedActive: false,
    speedMultiplier: 0,
    accumulatedGameSeconds: 0,  // 本次加速累计的游戏秒数
    speedStartDay: 0,
    speedStartHour: 0,
    speedStartMinute: 0,

    dialogueInProgress: false,   // 对话请求进行中，轮询跳过

    /* 天气图标映射 */
    WEATHER_ICONS: {
        '晴': '☀️', '少云': '🌤️', '多云': '⛅', '阴': '☁️',
        '小雨': '🌧️', '中雨': '🌧️', '大雨': '⛈️', '暴雨': '⛈️',
        '小雪': '🌨️', '中雪': '🌨️', '大雪': '❄️',
        '雾': '🌫️', '霾': '🌫️', '风': '💨', '微风': '🍃',
    },
    /* 后端天气类型枚举（英文，见 backend/game/weather.py 的 WEATHER_POOL）
       对应的图标，优先于上面的中文名映射，避免中英不匹配落到温度计兜底 */
    WEATHER_ICON_BY_TYPE: {
        'sunny': '☀️', 'cloudy': '⛅', 'light_rain': '🌧️',
        'heavy_rain': '⛈️', 'windy': '💨', 'foggy': '🌫️', 'snow': '❄️',
    },

    async init() {
        // 生成当前会话 ID
        this.sessionId = this.generateSessionId();

        // 加载所有角色列表
        await this.loadCharacterList();

        // 初始化各面板
        await MapPanel.init();
        GMPanel.init();
        if (typeof NewsPanel !== 'undefined') NewsPanel.init();
        CalendarPanel.init();
        PortraitPanel.init();
        MemoryPanel.init();
        if (typeof VoicePanel !== 'undefined') VoicePanel.init();
        if (typeof CharacterProfilePanel !== 'undefined') CharacterProfilePanel.init();
        if (typeof ChoicePanel !== 'undefined') ChoicePanel.init();

        // 初始化移动端适配（必须在数据加载前执行，确保即使数据加载失败也能展示 UI）
        MobileUI.init();

        // 加载初始数据
        await this.loadAllData();

        // 0 角色空态兜底：删光角色后自动引导创建新角色（避免白屏/无操作入口）
        if (!this.character) {
            Toast.show('当前没有任何角色，请创建一位女主', 'info', 4000);
            const _createBtn = document.getElementById('btn-character-create');
            if (_createBtn) _createBtn.click();
        }

        // 初始化对话面板（依赖角色数据需先加载）
        DialoguePanel.init();

        // 加载天气
        await this.loadWeather();

        // 绑定加速面板开关 → 现在速度面板始终可见
        const speedPanel = document.getElementById('speed-panel');
        if (speedPanel) {
            speedPanel.style.display = '';  // 确保可见
        }

        // 绑定加速按钮
        document.getElementById('speed-go-btn').addEventListener('click', () => {
            if (this.speedActive) return;
            this.speedMultiplier = parseInt(document.getElementById('speed-select').value);
            this.startAcceleration();
        });

        // 绑定停止按钮
        document.getElementById('speed-stop-btn').addEventListener('click', () => {
            this.stopAcceleration();
        });

        // 全选 / 取消全选跳过选项
        document.getElementById('speed-skip-toggle-all').addEventListener('click', () => {
            const checkboxes = document.querySelectorAll('#speed-skip-options input[type="checkbox"]');
            const allChecked = Array.from(checkboxes).every(cb => cb.checked);
            checkboxes.forEach(cb => cb.checked = !allChecked);
        });

        // 绑定角色切换按钮
        document.getElementById('btn-char-switch').addEventListener('click', () => this.showCharSwitch());
        document.getElementById('btn-char-switch-cancel').addEventListener('click', () => this.closeCharSwitch());

        // 绑定保存按钮
        document.getElementById('btn-save').addEventListener('click', () => this.saveAll());

        // 启动自动保存
        this.startAutoSave();

        // 启动前端游戏时钟
        this.startGameClock();

        // 前端轮询：每3秒检查角色属性是否变化，实现多标签/后台修改的实时同步
        this._pollingInterval = setInterval(async () => {
            // GM 面板打开时跳过（避免覆盖用户正在修改的值）
            const gmOverlay = document.getElementById('gm-modal-overlay');
            if (gmOverlay && gmOverlay.style.display === 'flex') return;
            // 加速进行中时跳过
            if (this.speedActive) return;
            if (this.dialogueInProgress) return;
            // 正在执行完整角色切换（renderForCharacter）时跳过本轮，避免重入
            if (this._isSwitching) return;

            try {
                const resp = await fetch('/api/character');
                const data = await resp.json();
                if (!data.success || !data.data) return;

                const fresh = data.data;
                const cached = this._lastCharacter;
                if (!cached) return;

                // 关键：先比“身份”是否变了（激活角色被其他窗口切换）。
                // 身份变化必须走完整切换（renderForCharacter），否则聊天记录/头像/天气等按角色隔离的模块不会刷新。
                if (fresh.id !== cached.id) {
                    await this.renderForCharacter(fresh);
                    return;
                }

                // 同角色下，对比关键属性是否变化，做轻量属性同步
                let changed = false;
                const nestedGroups = [
                    { fresh: fresh.physical || {}, cached: cached.physical || {},
                      keys: ['health', 'energy', 'hunger', 'hygiene'] },
                    { fresh: fresh.mental || {}, cached: cached.mental || {},
                      keys: ['mood', 'stress', 'happiness', 'motivation', 'joy',
                             'anger', 'disappointment', 'boredom', 'fulfillment',
                             'loneliness', 'confidence', 'creativity'] },
                    { fresh: fresh.player_relation || {}, cached: cached.player_relation || {},
                      keys: ['trust', 'affection', 'respect', 'intimacy'] },
                ];
                for (const grp of nestedGroups) {
                    for (const key of grp.keys) {
                        if (Math.round(grp.fresh[key] || 0) !== Math.round(grp.cached[key] || 0)) {
                            changed = true;
                            break;
                        }
                    }
                    if (changed) break;
                }
                if (!changed) {
                    const freshSkills = JSON.stringify(fresh.skills || {});
                    const cachedSkills = JSON.stringify(cached.skills || {});
                    const freshGoals = JSON.stringify(fresh.goals || {});
                    const cachedGoals = JSON.stringify(cached.goals || {});
                    if (freshSkills !== cachedSkills || freshGoals !== cachedGoals) {
                        changed = true;
                    }
                }

                // 游戏时间方向性同步（#time-persist，暗坑B）：
                // 仅当 DB 时间“领先”本地显示时钟时才采纳（如其他窗口已加速/日历跳转推进过时间），
                // 不允许 DB 把本地正常前滚的时钟拉回。否则 saveAll 刚把本地时间写回 DB，
                // 下一轮轮询会立刻把本地时钟拽回几秒，造成抖动与累积后退。
                if (!changed) {
                    const dbTotal = (fresh.game_day||0)*86400 + (fresh.game_hour||0)*3600 + (fresh.game_minute||0)*60 + (fresh.game_second||0);
                    const liveTotal = (this.gameDay||0)*86400 + (this.gameHour||0)*3600 + (this.gameMinute||0)*60 + Math.floor(this.gameSecond||0);
                    if (dbTotal > liveTotal) {
                        changed = true;
                    }
                }

                if (changed) {
                    await this.refreshAll(data.data);
                }

                // 事件日志实时刷新（带 id 守卫，无变化不重渲）：覆盖跨窗口 / 后台写入
                // （新闻注入、朋友关系更新、另一窗口加速等）产生的事件，使其无需整页刷新即实时可见。
                try { await EventLogPanel.refreshIfNeeded(); } catch (e) {}
            } catch (err) {
                // 静默失败
            }
        }, 3000);
    },

    generateSessionId() {
        const now = new Date();
        const pad2 = n => String(n).padStart(2, '0');
        return `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}_${pad2(now.getHours())}-${pad2(now.getMinutes())}-${pad2(now.getSeconds())}`;
    },

    async loadWeather() {
        try {
            const resp = await fetch('/api/weather');
            const data = await resp.json();
            if (data.success && data.data) {
                this.updateWeather(data.data);
            }
        } catch (err) {
            console.error('加载天气失败:', err);
        }
    },

    updateWeather(weatherData) {
        const w = weatherData.weather;
        const iconEl = document.getElementById('weather-icon');
        const textEl = document.getElementById('weather-text');

        // 优先用后端英文 weather_type 映射图标；再退回中文 weather_name；
        // 都查不到才用温度计兜底（避免中英不匹配时整片显示温度计）
        const icon = this.WEATHER_ICON_BY_TYPE[w.weather_type]
                     || this.WEATHER_ICONS[w.weather_name]
                     || this.WEATHER_ICONS[w.weather_type]
                     || '🌡️';
        iconEl.textContent = icon;

        // 日期 + 星期显示（后端已返回 weekday，如"星期一"）
        const gds = weatherData.game_date_short;
        const gdd = weatherData.game_date_display;
        let weekday = weatherData.weekday || '';
        const weekdayEn = this._weekdayToEn(weatherData.weekday);
        this._weekdayEn = weekdayEn;
        // dateOnly：不含星期的纯日期（PC 拼接中文星期，移动端拼接英文星期）
        let dateOnly;
        if (gds) {
            dateOnly = gds;
        } else if (gdd) {
            const match = gdd.match(/(\d+)年(\d+)月(\d+)日\s*(.+)/);
            if (match) {
                dateOnly = `${parseInt(match[2])}月${parseInt(match[3])}日`;
            } else {
                dateOnly = gdd.replace(/\s*星期[一二三四五六日天]$/, '');
            }
        } else {
            const month = w.date ? w.date.substring(5, 7).replace(/^0/, '') : '';
            const day = w.date ? w.date.substring(8, 10).replace(/^0/, '') : '';
            dateOnly = `${month}月${day}日`;
        }

        // 若已有基于游戏日的日期标签（时间选择器/时钟推进后），优先用它，保证实时同步
        if (this._dateLabel) {
            dateOnly = this._dateLabel.dateOnly;
            weekday = this._dateLabel.weekday;
        }

        const weatherName = w.weather_name || w.weather_type || '晴';
        const temp = Math.round(w.temperature || 22);

        // 同步天气到角色对象，确保 saveAll 能发送正确的天气
        if (this.character) {
            this.character.weather = weatherName;
        }

        // PC 文本（含中文星期与天气名，保持原样）；移动端精简：emoji 由天气图标承担，星期改为三字母英文
        this._weatherTextPC = `${dateOnly} ${weekday} ${weatherName} ${temp}°`;
        this._weatherTextMobile = `${dateOnly} ${weekdayEn} ${temp}°`;
        this.applyWeatherText();

        if (weatherData.is_weekend) {
            textEl.classList.add('weekend');
        } else {
            textEl.classList.remove('weekend');
        }

        this._mobileWeatherIcon = icon;
        this._mobileWeatherTemp = temp;
        this.updateMobileInfoCapsule();
    },

    /* 中文星期 → 三字母英文（移动端紧凑显示用） */
    _weekdayToEn(cn) {
        const map = {
            '星期一': 'MON', '星期二': 'TUE', '星期三': 'WED', '星期四': 'THU',
            '星期五': 'FRI', '星期六': 'SAT', '星期日': 'SUN', '星期天': 'SUN'
        };
        return map[cn] || cn || '';
    },

    /* 由当前游戏日推算日历日期标签（与后端 2024-09-01 基准一致） */
    _computeDateLabel() {
        const base = new Date(2024, 8, 1);
        const d = new Date(base);
        d.setDate(d.getDate() + (this.gameDay || 0));
        const month = d.getMonth() + 1;
        const day = d.getDate();
        const wd = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
        return { dateOnly: `${month}月${day}日`, weekday: `星期${wd}` };
    },

    /* 按视口应用天气文本：PC 显示完整中文，移动端显示精简版（emoji 图标 + 英文星期） */
    applyWeatherText() {
        const textEl = document.getElementById('weather-text');
        if (!textEl) return;
        const isMobile = window.matchMedia('(max-width: 768px)').matches;
        textEl.textContent = isMobile
            ? (this._weatherTextMobile || this._weatherTextPC || '')
            : (this._weatherTextPC || '');
    },

    async loadAllData(skipCharacter = false) {
        try {
            if (skipCharacter) {
                // 角色切换后：已持有目标角色数据，仅刷新辅助数据
                const [friendsResp, eventsResp] = await Promise.all([
                    fetch('/api/friends'),
                    fetch('/api/events?limit=100')
                ]);
                const friendsData = await friendsResp.json();
                const eventsData = await eventsResp.json();
                if (friendsData.success) {
                    RelationshipPanel.update(friendsData.data);
                }
                if (eventsData.success) {
                    EventLogPanel.render(eventsData.data);
                }
                await this.refreshAll(this.character);
                await MapPanel.loadLocationsAndRender();
            } else {
                const [charResp, friendsResp, eventsResp] = await Promise.all([
                    fetch('/api/character'),
                    fetch('/api/friends'),
                    fetch('/api/events?limit=100')
                ]);

                const charData = await charResp.json();
                const friendsData = await friendsResp.json();
                const eventsData = await eventsResp.json();

                if (charData.success) {
                    this.character = charData.data;
                    this.syncGameTime(charData.data);
                    await this.refreshAll(this.character);
                    await MapPanel.loadLocationsAndRender();
                }
                if (friendsData.success) {
                    RelationshipPanel.update(friendsData.data);
                }
                if (eventsData.success) {
                    EventLogPanel.render(eventsData.data);
                }
            }

            GoalTracker.updateAchievements();

            // 加载角色画像信息并动态更新 UI
            await this.loadCharacterProfile();

        } catch (err) {
            console.error('加载数据失败:', err);
            Toast.show('数据加载失败，请刷新页面', 'danger');
        }
    },

    async loadCharacterProfile() {
        try {
            const resp = await fetch('/api/character/profile');
            const data = await resp.json();
            if (!data.success || !data.data) return;

            const profile = data.data;

            // 页面标题
            document.title = `邻信 - ${profile.name}的世界`;

            // Logo 名称
            const logoName = document.getElementById('logo-name');
            if (logoName) logoName.textContent = `${profile.name}的世界`;

            // 角色名
            const avatarName = document.getElementById('avatar-name');
            if (avatarName) avatarName.textContent = profile.name;

            // 年龄 + 身份
            const avatarAge = document.getElementById('avatar-age');
            if (avatarAge) avatarAge.textContent = `${profile.age || '?'}岁 · ${profile.identity_label || ''}`;

            // 梦想标签
            const avatarDreams = document.getElementById('avatar-dreams');
            if (avatarDreams) {
                const dreams = [];
                if (profile.dream_primary) dreams.push(profile.dream_primary);
                if (profile.dream_secondary) dreams.push(profile.dream_secondary);
                avatarDreams.textContent = dreams.length > 0 ? dreams.join(' · ') : '暂无梦想';
            }

            // 对话面板角色名
            const dialogueName = document.getElementById('dialogue-character-name');
            if (dialogueName) dialogueName.textContent = profile.name;

            // 动态渲染目标追踪
            this.renderGoals(profile);
        } catch (err) {
            console.error('加载角色画像失败:', err);
        }
    },

    renderGoals(profile) {
        const container = document.getElementById('goals-container');
        if (!container) return;

        const goalsJson = (this.character && this.character.goals) || {};
        const goalDisplay = (this.character && this.character.goal_display) || {};
        const goalRules = (this.character && this.character.goal_rules) || {};
        const goalKeys = Object.keys(goalsJson);

        if (goalKeys.length === 0) {
            container.innerHTML = '<div class="goal-item"><span class="goal-info"><span class="goal-name">暂无梦想目标</span></span></div>';
            return;
        }

        container.innerHTML = goalKeys.map((key, i) => {
            const val = goalsJson[key] || 0;
            const name = goalDisplay[key] || key;
            const barId = `goal-bar-${i}`;
            const pctId = `goal-pct-${i}`;
            const cond = (goalRules[key] && window.GoalFormat)
                ? window.GoalFormat.formatGoalRule(goalRules[key]) : '';
            const condHtml = cond ? `<span class="goal-cond">${cond}</span>` : '';
            return `<div class="goal-item" data-goal-key="${key}">
                <div class="goal-info">
                    <span class="goal-name">${name}</span>
                    <div class="progress-bar">
                        <div class="progress-fill" id="${barId}" style="width:${val}%"></div>
                    </div>
                    <span class="goal-pct" id="${pctId}">${val}%</span>
                    ${condHtml}
                </div>
            </div>`;
        }).join('');
    },

    async refreshAll(character, skipTimeSync = false) {
        this.character = character;
        this._lastCharacter = character;
        StatusPanel.update(character);
        PortraitPanel.update(character);
        GoalTracker.update(character);
        MapPanel.updateLocation(character.location);
        this.updateLocationDisplay(character);
        await MapPanel.loadLocationsAndRender();
        if (!skipTimeSync) {
            this.updateGameTime(character);
        }

        // 同步对话面板关系阶梯标签，避免页面刚加载时显示默认的“陌生人”
        if (character.relationship_tier) {
            DialoguePanel.updateRelationshipTier(character.relationship_tier);
        }
    },

    updateGameTime(character) {
        if (character) {
            this.syncGameTime(character);
        }
        const char = character || this.character;
        if (!char && this.gameDay === undefined) return;
        const hh = String(this.gameHour).padStart(2, '0');
        const mm = String(this.gameMinute).padStart(2, '0');
        const ss = String(Math.floor(this.gameSecond)).padStart(2, '0');

        let timeStr = `第${this.gameDay}天 ${hh}:${mm}:${ss}`;
        // 移动端在游戏时间后追加三字母英文星期，紧凑成一行
        if (window.matchMedia('(max-width: 768px)').matches && this._weekdayEn) {
            timeStr += ` ${this._weekdayEn}`;
        }
        document.getElementById('game-time-text').textContent = timeStr;

        // 同步天气区的日历日期为当前游戏日（让日期实时更新，不再依赖天气接口）
        this._dateLabel = this._computeDateLabel();
        // 用新日期重建天气文本（保留已缓存的天气名和温度），否则 applyWeatherText
        // 读的是 updateWeather 上次设的 _weatherTextPC 旧值，日期不会实时变化
        const wName = (this.character && this.character.weather) || '晴';
        const wTemp = this._mobileWeatherTemp != null ? this._mobileWeatherTemp : 22;
        const wdEn = this._weekdayToEn(this._dateLabel.weekday);
        this._weatherTextPC = `${this._dateLabel.dateOnly} ${this._dateLabel.weekday} ${wName} ${wTemp}°`;
        this._weatherTextMobile = `${this._dateLabel.dateOnly} ${wdEn} ${wTemp}°`;
        this.applyWeatherText();

        // 若日历弹窗正打开，随游戏日重渲染“今天”高亮
        const calOverlay = document.getElementById('calendar-modal-overlay');
        if (calOverlay && calOverlay.style.display === 'flex' && typeof CalendarPanel !== 'undefined') {
            CalendarPanel.syncToCurrentDay();
        }

        this.updateMobileInfoCapsule();
    },

    updateLocationDisplay(character) {
        const loc = (typeof character === 'object' && character) ? character.location : character;
        const name = (typeof character === 'object' && character && character.location_name)
            ? character.location_name : (loc || '');
        const text = name || '未知';
        const el = document.getElementById('location-text');
        if (el) {
            el.textContent = text;
        }
        this._mobileLocation = text;
        this.updateMobileInfoCapsule();
    },

    /* -------- 移动端信息胶囊 -------- */
    updateMobileInfoCapsule() {
        const el = document.getElementById('mobile-info-text');
        if (!el) return;
        const icon = this._mobileWeatherIcon || '🌡️';
        const temp = this._mobileWeatherTemp != null ? this._mobileWeatherTemp + '°' : '';
        const loc = this._mobileLocation || '';
        const hh = String(this.gameHour || 0).padStart(2, '0');
        const mm = String(this.gameMinute || 0).padStart(2, '0');
        const day = this.gameDay != null ? `第${this.gameDay}天` : '';
        el.textContent = `${icon} ${temp} · ${loc} · ${day} ${hh}:${mm}`;
    },

    /* ── 小喇叭通知条：情感时刻 / 记忆 / 关系进阶 / 约束警告 汇总，上下自动滚动 ── */
    announce(text, icon = '🔔') {
        const targets = [
            { vp: 'announce-viewport', track: 'announce-track' },
            { vp: 'mobile-announce-viewport', track: 'mobile-announce-track' }
        ];
        targets.forEach(({ vp, track }) => {
            const vpEl = document.getElementById(vp);
            const trackEl = document.getElementById(track);
            if (!vpEl || !trackEl) return;
            const emptyEl = trackEl.querySelector('.announce-empty');
            if (emptyEl) emptyEl.remove();
            const item = document.createElement('div');
            item.className = 'announce-item';
            const ic = document.createElement('span');
            ic.className = 'announce-item-icon';
            ic.textContent = icon;
            const tx = document.createElement('span');
            tx.className = 'announce-item-text';
            tx.textContent = text;
            item.appendChild(ic);
            item.appendChild(tx);
            trackEl.insertBefore(item, trackEl.firstChild);
            // 只保留最近 3 条，更早的不再显示
            while (trackEl.children.length > 3) {
                trackEl.removeChild(trackEl.lastChild);
            }
        });
        this._startAnnounceScroll();
    },

    _announceTimer: null,
    _startAnnounceScroll() {
        if (this._announceTimer) return;
        // 每 40ms 上移 0.5px（约 12.5px/s），内容溢出时循环滚动；未溢出则不滚动
        this._announceTimer = setInterval(() => {
            ['announce-viewport', 'mobile-announce-viewport'].forEach((vpId) => {
                const vp = document.getElementById(vpId);
                if (!vp) return;
                const track = vp.querySelector('.announce-track');
                if (!track) return;
                if (track.scrollHeight <= vp.clientHeight + 2) return;
                vp.scrollTop = Math.round((vp.scrollTop + 0.5) * 10) / 10;
                if (vp.scrollTop + vp.clientHeight >= track.scrollHeight - 0.5) {
                    vp.scrollTop = 0;
                }
            });
        }, 40);
    },

    /* -------- 前端游戏时钟 -------- */

    syncGameTime(character) {
        if (!character) return;
        this.gameDay = character.game_day ?? 0;
        this.gameHour = character.game_hour ?? 9;
        this.gameMinute = character.game_minute ?? 0;
        this.gameSecond = character.game_second ?? 0;
    },

    startGameClock() {
        if (this.gameClockInterval) return;
        this.gameClockInterval = setInterval(() => this.advanceGameTick(), 200);  // 每 200ms 刷新一次，保证视觉流畅
    },

    stopGameClock() {
        if (this.gameClockInterval) {
            clearInterval(this.gameClockInterval);
            this.gameClockInterval = null;
        }
    },

    /** 每个真实 tick 推进的游戏秒数 */
    getGameSecondsPerTick() {
        if (!this.speedActive) return 1;  // 正常速度：1 真秒 = 1 游戏秒（每 200ms tick 时按比例）
        // 加速模式：1 真秒 = speedMultiplier * 60 游戏秒
        return this.speedMultiplier * 60;
    },

    advanceGameTick() {
        const gameSecondsPerRealSecond = this.getGameSecondsPerTick();
        const tickFraction = 0.2;  // 200ms / 1000ms
        const gameSeconds = gameSecondsPerRealSecond * tickFraction;

        if (this.speedActive) {
            // 加速期间：累计“应推进的游戏秒数”供结束提交（提交量不变）。
            // 本地显示时钟仍继续往下推进，使加速时游戏时间实时往前跳动；
            // 结束由 submitBatchTick 从服务端定稿。因方案③(DB 时钟每 5 分钟≈live)已落地，
            // 当初“前后端漂移导致结束回退”的根因基本消除，故恢复加速期实时前滚显示。
            this.accumulatedGameSeconds += gameSeconds;
        }

        // 正常模式与加速模式共用：推进本地显示时钟（加速时实时往前走）
        this.gameSecond += gameSeconds;

        while (this.gameSecond >= 60) {
            this.gameSecond -= 60;
            this.gameMinute++;
        }
        while (this.gameMinute >= 60) {
            this.gameMinute -= 60;
            this.gameHour++;
        }
        while (this.gameHour >= 24) {
            this.gameHour -= 24;
            this.gameDay++;
        }

        this.updateGameTime();
    },

    /* -------- 时间加速引擎 -------- */

    startAcceleration(multiplier) {
        if (this.speedActive) return;

        // 倍速：优先用传入值，否则读桌面端选择器（保持原有行为）
        if (multiplier) {
            this.speedMultiplier = multiplier;
        } else {
            this.speedMultiplier = parseInt(document.getElementById('speed-select').value);
        }

        this.speedActive = true;
        this.accumulatedGameSeconds = 0;
        this.speedStartDay = this.gameDay;
        this.speedStartHour = this.gameHour;
        this.speedStartMinute = this.gameMinute;

        // UI 切换
        document.getElementById('speed-go-btn').classList.add('active');
        document.getElementById('speed-select').disabled = true;
        document.querySelectorAll('#speed-skip-options input').forEach(cb => cb.disabled = true);
        document.getElementById('speed-skip-toggle-all').disabled = true;
        document.getElementById('speed-stop-btn').style.display = 'block';

        // 更新顶部状态提示
        this.updateSpeedStatus();

        const speedToast = Toast.show(`加速中：${this.speedMultiplier}X`, 'warning', 1000);
        if (speedToast) setTimeout(() => speedToast.remove(), 1000);
    },

    async stopAcceleration() {
        if (!this.speedActive) return;

        this.speedActive = false;

        // UI 恢复
        document.getElementById('speed-go-btn').classList.remove('active');
        document.getElementById('speed-select').disabled = false;
        document.querySelectorAll('#speed-skip-options input').forEach(cb => cb.disabled = false);
        document.getElementById('speed-skip-toggle-all').disabled = false;
        document.getElementById('speed-stop-btn').style.display = 'none';
        this.updateSpeedStatus();

        // 计算总推进的游戏分钟数
        const totalGameMinutes = Math.floor(this.accumulatedGameSeconds / 60);

        if (totalGameMinutes > 0) {
            Toast.show(`停止加速，提交 ${totalGameMinutes} 分钟的事件...`, 'info');
            await this.submitBatchTick(totalGameMinutes);
        } else {
            Toast.show('已停止加速', 'info');
        }
    },

    updateSpeedStatus() {
        const statusEl = document.getElementById('auto-tick-status');
        if (this.speedActive) {
            statusEl.textContent = `${this.speedMultiplier}X 加速中...`;
        } else {
            statusEl.textContent = '';
        }
    },

    async submitBatchTick(gameMinutes) {
        try {
            const skipOptions = {
                skip_random_event: document.getElementById('skip-random-event').checked,
                skip_tick_llm: document.getElementById('skip-tick-llm').checked,
                skip_auto_message: document.getElementById('skip-auto-msg').checked,
                skip_friend_update: document.getElementById('skip-friend-update').checked,
            };
            const resp = await fetch('/api/tick/batch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    game_minutes_to_advance: gameMinutes,
                    ...skipOptions,
                })
            });
            const data = await resp.json();

            if (data.success) {
                // ① 事件日志：优先且独立渲染，避免被后续 applyStateChanges / refreshAll 的异常连坐丢弃
                // （此前若 refreshAll 抛错，事件渲染（appendEvents）根本不会执行，导致“加速结束也看不到事件”）
                try {
                    if (data.events && data.events.length > 0) {
                        EventLogPanel.appendEvents(data.events);
                        Toast.show(`生成了 ${data.events.length} 个事件`, 'info');
                        // 检查任务抉择事件（五幕结构 is_choice 事件）
                        data.events.forEach(e => {
                            const ev = e.event || e;
                            const effects = ev.effects || {};
                            if ((ev.event_type === 'mission' || ev.event_category === 'mission')
                                && effects.is_choice && effects.choices && effects.choices.length > 0) {
                                setTimeout(() => {
                                    if (typeof ChoicePanel !== 'undefined') {
                                        ChoicePanel.show(ev.id, effects.choices);
                                    }
                                }, 500);
                            }
                        });
                    }
                } catch (evErr) {
                    console.error('事件日志渲染失败（已忽略，不影响其它刷新）:', evErr);
                }

                // ② 状态变化与面板刷新（独立 try，失败不影响事件已显示）
                try {
                    // 状态变化先记录（供面板渲染时读取 delta，显示闪烁）
                    if (data.state_changes && data.state_changes.length > 0) {
                        StatusPanel.applyStateChanges(data.state_changes);
                    }
                    // 更新角色状态（渲染面板，此时已记录 delta 会自动显示）
                    if (data.character) {
                        await this.refreshAll(data.character);
                    }
                } catch (panelErr) {
                    console.error('批量推进面板刷新失败（已忽略）:', panelErr);
                }

                // 主动消息
                if (data.auto_message) {
                    const am = data.auto_message;
                    const gd = am.game_day ?? App.gameDay;
                    const gt = am.game_time ?? `${String(App.gameHour).padStart(2,'0')}:${String(App.gameMinute).padStart(2,'0')}:${String(Math.floor(App.gameSecond)).padStart(2,'0')}`;
                    DialoguePanel.addSystemMsg(`[${this.character?.name || '角色'}主动发来消息] ${am.message}`);
                    DialoguePanel.appendMessage('character', am.message, gd, gt);
                    Toast.show((this.character?.name || '角色') + '发来了一条消息！', 'warning');
                }

                // 新成就
                if (data.new_achievements && data.new_achievements.length > 0) {
                    data.new_achievements.forEach(a => {
                        Toast.show(`成就解锁：${a.name}`, 'success');
                    });
                    GoalTracker.updateAchievements();
                }

                // 朋友关系（先更新面板，再叠加高亮动画）
                const friendsResp = await fetch('/api/friends');
                const friendsData = await friendsResp.json();
                if (friendsData.success) {
                    RelationshipPanel.update(friendsData.data);
                }
                if (data.relation_changes) {
                    RelationshipPanel.applyRelationChanges(data.relation_changes);
                }

                // 天气
                await this.loadWeather();

                // 穿搭
                if (data.outfit_changed && data.character) {
                    PortraitPanel.update(data.character);
                    Toast.show((this.character?.name || '角色') + '换上了新衣服！', 'info');
                }

                // 警告：统一进小喇叭通知条（不再用 Toast 闪横幅，与状态面板约束通知保持一致）
                if (data.active_warnings && data.active_warnings.length > 0) {
                    data.active_warnings.forEach(w => App.announce(w, '⚠️'));
                }

            } else {
                Toast.show('批量推进失败', 'danger');
            }
        } catch (err) {
            console.error('批量推进失败:', err);
            Toast.show('批量推进失败', 'danger');
        }
    },

    /* ========== 角色切换 ========== */

    async loadCharacterList() {
        try {
            const resp = await fetch('/api/character/list');
            const data = await resp.json();
            if (data.success) {
                this.characters = data.data;
                this.updateCharDisplay();
            }
        } catch (err) {
            console.error('加载角色列表失败:', err);
        }
    },

    updateCharDisplay() {
        const nameEl = document.getElementById('current-char-name');
        if (nameEl && this.character) {
            nameEl.textContent = this.character.name;
        }
    },

    async renderForCharacter(character) {
        // 统一入口：手动切换与轮询发现“激活角色变了”都走这里做完整重渲染。
        // 本函数只重渲染前端，不向后端 POST 任何切换请求，避免多窗口抢切 active 角色。
        this._isSwitching = true;
        this._skipNextSave = true;   // 切换瞬间冻结保存，防止把旧角色屏幕数据写进新角色
        try {
            this.character = character;
            this._lastCharacter = character;
            this.sessionId = this.generateSessionId();

            // 同步游戏时间到前端时钟
            this.gameDay = character.game_day || 0;
            this.gameHour = character.game_hour || 8;
            this.gameMinute = character.game_minute || 0;
            this.gameSecond = character.game_second || 0;

            // 完整刷新所有面板（聊天/立绘/头像/天气/好友/事件/画像/目标等）
            if (typeof MapPanel !== 'undefined') await MapPanel.loadLocationsAndRender();
            await this.loadWeather();
            if (typeof PortraitPanel !== 'undefined') PortraitPanel.init();
            if (typeof DialoguePanel !== 'undefined') DialoguePanel.init();
            await this.loadAllData(true);

            this.updateCharDisplay();
        } finally {
            this._isSwitching = false;
            this._skipNextSave = false;
        }
    },

    async switchCharacter(characterId) {
        try {
            const resp = await fetch('/api/character/switch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ character_id: characterId })
            });
            const data = await resp.json();
            if (data.success && data.data.character) {
                ['announce-track', 'mobile-announce-track'].forEach(trackId => {
                    const track = document.getElementById(trackId);
                    if (track) track.querySelectorAll('.announce-item').forEach(item => item.remove());
                });
                await this.renderForCharacter(data.data.character);
                this.closeCharSwitch();
                Toast.show(`已切换到 ${this.character.name}`, 'success');
            }
        } catch (err) {
            console.error('切换角色失败:', err);
            Toast.show('切换角色失败', 'danger');
        }
    },

    showCharSwitch() {
        const overlay = document.getElementById('char-switch-overlay');
        const list = document.getElementById('char-switch-list');
        if (!overlay || !list) return;

        list.innerHTML = this.characters.map(c => `
            <div class="char-item ${this.character && this.character.id === c.id ? 'active' : ''}"
                 data-id="${c.id}">
                <div class="char-item-name">${c.name}</div>
                <div class="char-item-info">${c.identity_label} · ${c.major} · 第${c.game_day}天</div>
            </div>
        `).join('');

        list.querySelectorAll('.char-item').forEach(el => {
            el.addEventListener('click', () => this.switchCharacter(parseInt(el.dataset.id)));
        });

        overlay.style.display = 'flex';
    },

    closeCharSwitch() {
        const overlay = document.getElementById('char-switch-overlay');
        if (overlay) overlay.style.display = 'none';
    },

    /* ========== 保存功能 ========== */

    async saveAll() {
        try {
            // 注意：to_dict() 把 physical/mental 嵌套为子对象，后端 save-all 期望的是顶层列，
            // 因此必须从 this.character.physical.* / this.character.mental.* 正确路径取值，
            // 否则 energy/health/mood 等会被读成 undefined（#3）。
            const c = this.character || {};
            const phys = c.physical || {};
            const mental = c.mental || {};
            // 加速期间本地时钟冻结在起点、DB 也本就该是起点值，跳过时间字段写入避免冗余/冲突（#time-persist，暗坑C）
            const includeTime = !this.speedActive;
            const saveData = {
                energy: phys.energy,
                health: phys.health,
                mood: mental.mood,
                money: c.money,
                intelligence: c.intelligence,
                charm: c.charm,
                creativity: mental.creativity,
                motivation: mental.motivation,
                fulfillment: mental.fulfillment,
                stress: mental.stress,
                confidence: mental.confidence,
                inspiration: c.inspiration,
                weather: c.weather,
                location: c.location || (typeof MapPanel !== 'undefined' ? MapPanel.currentLocation : ''),
                skills: c.skills,
                goals: c.goals,
                // 游戏时钟持久化（#time-persist）：正常游玩时把本地 live 时钟写回 DB，
                // 刷新/重载后保留在最近 5 分钟内的进度，不再跳回很早的 DB 旧值。
                // game_second 为浮点(每 tick +0.2)，必须 floor 成整数，否则污染下游整数运算（暗坑A）。
                game_day: includeTime ? this.gameDay : undefined,
                game_hour: includeTime ? this.gameHour : undefined,
                game_minute: includeTime ? this.gameMinute : undefined,
                game_second: includeTime ? Math.floor(this.gameSecond) : undefined,
            };

            const resp = await fetch('/api/character/save-all', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(saveData)
            });
            const data = await resp.json();
            if (data.success) {
                this.lastSaveTime = new Date();
                this.updateSaveTimeDisplay();
                Toast.show('保存成功', 'success');
            } else {
                Toast.show('保存失败: ' + (data.error || '未知错误'), 'danger');
            }
        } catch (err) {
            console.error('保存失败:', err);
            Toast.show('保存失败', 'danger');
        }
    },

    updateSaveTimeDisplay() {
        const el = document.getElementById('auto-save-time');
        if (el && this.lastSaveTime) {
            const t = this.lastSaveTime;
            const pad = n => String(n).padStart(2, '0');
            el.textContent = `${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
        }
    },

    startAutoSave() {
        // 每5分钟自动保存一次；GM 刚做完重置时会置 _skipNextSave，跳过本次以免覆盖（#5）
        this.autoSaveInterval = setInterval(() => {
            if (this._skipNextSave) return;
            this.saveAll();
        }, 5 * 60 * 1000);

        // 页面隐藏时（切换标签页/最小化/锁屏）自动保存
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden' && !this._skipNextSave) {
                this.saveAll();
            }
        });

        // 页面刷新/关闭时使用 sendBeacon 保底保存
        window.addEventListener('beforeunload', () => {
            if (this._skipNextSave) return;
            // 加速期间跳过时间字段（同 saveAll 的 includeTime 逻辑），避免写回冻结起点值（#time-persist，暗坑C）
            const includeTime = !this.speedActive;
            const saveData = {
                energy: this.character?.energy,
                health: this.character?.health,
                mood: this.character?.mood,
                money: this.character?.money,
                intelligence: this.character?.intelligence,
                charm: this.character?.charm,
                creativity: this.character?.creativity,
                motivation: this.character?.motivation,
                fulfillment: this.character?.fulfillment,
                stress: this.character?.stress,
                confidence: this.character?.confidence,
                inspiration: this.character?.inspiration,
                weather: this.character?.weather,
                location: this.character?.location || (typeof MapPanel !== 'undefined' ? MapPanel.currentLocation : ''),
                skills: this.character?.skills,
                goals: this.character?.goals,
                // 游戏时钟持久化（#time-persist，暗坑A：game_second 取 floor）
                game_day: includeTime ? this.gameDay : undefined,
                game_hour: includeTime ? this.gameHour : undefined,
                game_minute: includeTime ? this.gameMinute : undefined,
                game_second: includeTime ? Math.floor(this.gameSecond) : undefined,
            };
            navigator.sendBeacon('/api/character/save-all', JSON.stringify(saveData));
        });

        // 角色切换时先保存当前角色再切换
        const originalSwitch = this.switchCharacter.bind(this);
        this.switchCharacter = async function (id) {
            await App.saveAll();
            await originalSwitch(id);
        };
    }
};

/* ============================================================
   MobileUI — 移动端响应式增强
   ============================================================ */
const MobileUI = {
    isMobile: false,
    mediaQuery: window.matchMedia('(max-width: 768px)'),

    init() {
        this.isMobile = this.mediaQuery.matches;
        this.mediaQuery.addEventListener('change', e => {
            this.isMobile = e.matches;
            this.onBreakpointChange();
        });

        // 汉堡菜单在 PC 与移动端都需可用（PC 顶栏功能按钮仅经此展开）
        this.initHamburgerMenu();

        if (!this.isMobile) return;

        this.initTabs();
        this.initCollapsiblePanels();
        this.initChatFullscreen();
        this.initKeyboardHandler();
        this.initQuickRelToggle();
        this.initDialogueHintsToggle();
        this.initSpeedSkipToggle();
        this.initStatusGroupCollapse();
    },

    /* ---- Tab 切换 ---- */
    initTabs() {
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const panelId = btn.dataset.panel;

                // 移动端"对话"Tab → 打开独立全屏聊天，不切换三栏面板
                if (this.isMobile && panelId === 'right-panel') {
                    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    this.openChatFullscreen();
                    return;
                }

                // 普通 Tab 切换
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                document.querySelectorAll('.left-panel, .center-panel, .right-panel')
                    .forEach(p => p.classList.remove('active'));
                const panel = document.getElementById(panelId);
                if (panel) {
                    panel.classList.add('active');
                    panel.scrollTop = 0;
                }
            });
        });

        // 默认激活：桌面端对话，移动端事件/状态
        if (!this.isMobile) {
            const dialogueTab = document.querySelector('.tab-btn[data-panel="right-panel"]');
            if (dialogueTab && !document.querySelector('.right-panel.active')) {
                dialogueTab.click();
            }
        } else {
            const centerTab = document.querySelector('.tab-btn[data-panel="center-panel"]');
            if (centerTab && !document.querySelector('.center-panel.active')) {
                centerTab.click();
            }
        }
    },

    /* ---- Section 折叠 ---- */
    initCollapsiblePanels() {
        // 初始化：data-collapsed 属性的面板默认折叠（仅 left/center）
        document.querySelectorAll('.left-panel .panel[data-collapsed="true"], .center-panel .panel[data-collapsed="true"]')
            .forEach(panel => panel.classList.add('collapsed'));

        // 绑定折叠切换
        document.querySelectorAll('.left-panel .panel h3, .center-panel .panel h3')
            .forEach(h3 => {
                h3.addEventListener('click', () => {
                    const panel = h3.closest('.panel');
                    if (panel) panel.classList.toggle('collapsed');
                });
            });
    },

    /* ---- 汉堡菜单 ---- */
    initHamburgerMenu() {
        const menuBtn = document.getElementById('mobile-menu-btn');
        const dropdown = document.getElementById('mobile-menu-dropdown');

        if (!menuBtn || !dropdown) return;

        menuBtn.addEventListener('click', e => {
            e.stopPropagation();
            dropdown.classList.toggle('open');
        });

        // 点击外部关闭
        document.addEventListener('click', e => {
            if (!dropdown.contains(e.target) && e.target !== menuBtn) {
                dropdown.classList.remove('open');
            }
        });
    },

    /* ---- 移动端聊天全屏视图 ---- */
    initChatFullscreen() {
        const overlay = document.getElementById('mobile-chat-overlay');
        const appContainer = document.querySelector('.app-container');
        if (!overlay || !appContainer) return;

        // 返回按钮 → 关闭聊天全屏
        const backBtn = document.getElementById('mobile-chat-back');
        if (backBtn) {
            backBtn.addEventListener('click', () => this.closeChatFullscreen());
        }

        // + 按钮：切换“更多”菜单（内含设置时间/地点入口与关系快捷）
        const plusBtn = document.getElementById('mobile-chat-plus');
        const menu = document.getElementById('mobile-chat-menu');
        if (plusBtn && menu) {
            plusBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                menu.classList.toggle('show');
            });
            document.addEventListener('click', (e) => {
                if (menu.classList.contains('show') && !menu.contains(e.target) && e.target !== plusBtn) {
                    menu.classList.remove('show');
                }
            });
        }
        // 菜单内“设置游戏时间 / 地点”入口 → 打开选择器
        const openTlBtn = document.getElementById('mobile-open-tl');
        if (openTlBtn && typeof DialoguePanel !== 'undefined') {
            openTlBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                menu?.classList.remove('show');
                DialoguePanel._openTimeLocationPicker('time');
            });
        }

        // 移动端重置聊天按钮
        const resetBtn = document.getElementById('mobile-chat-reset');
        if (resetBtn) {
            resetBtn.addEventListener('click', () => {
                if (typeof DialoguePanel !== 'undefined') {
                    DialoguePanel.resetChat();
                }
            });
        }
    },

    openChatFullscreen() {
        const overlay = document.getElementById('mobile-chat-overlay');
        const appContainer = document.querySelector('.app-container');
        if (!overlay || !appContainer) return;

        overlay.classList.add('show');
        appContainer.classList.add('chat-fullscreen');

        // 立即按当前 visualViewport 同步浮层高度（跟随地址栏/键盘，见 initKeyboardHandler）
        if (this._adjustOverlayForKeyboard) this._adjustOverlayForKeyboard();

        // 同步角色名到导航栏
        const nameEl = document.getElementById('mobile-chat-name');
        if (nameEl && App.character?.name) {
            nameEl.textContent = App.character.name;
        }

        // 同步关系阶梯
        if (typeof DialoguePanel !== 'undefined' && App.character?.relationship_tier) {
            DialoguePanel.updateRelationshipTier(App.character.relationship_tier);
        }

        // 同步桌面端现有消息到移动端容器（避免空白）
        const desktopMsgs = document.getElementById('dialogue-messages');
        const mobileMsgs = document.getElementById('mobile-chat-messages');
        if (desktopMsgs && mobileMsgs) {
            mobileMsgs.innerHTML = desktopMsgs.innerHTML;
            // 重新绑定事件到克隆的消息
            setTimeout(() => {
                if (typeof DialoguePanel !== 'undefined') {
                    mobileMsgs.querySelectorAll('.msg.character .btn-reanalyze').forEach(btn => {
                        btn.addEventListener('click', (e) => {
                            e.stopPropagation();
                            DialoguePanel.reanalyzeEmotion(parseInt(btn.getAttribute('data-msg-index')), btn);
                        });
                    });
                    mobileMsgs.querySelectorAll('.msg.character .btn-tts').forEach(btn => {
                        btn.addEventListener('click', (e) => {
                            e.stopPropagation();
                            DialoguePanel.playTTS(btn);
                        });
                    });
                }
            }, 50);
        }

        // 滚动到底部
        setTimeout(() => {
            const msgs = document.getElementById('mobile-chat-messages');
            if (msgs) msgs.scrollTop = msgs.scrollHeight;
        }, 150);
    },

    closeChatFullscreen() {
        const overlay = document.getElementById('mobile-chat-overlay');
        const appContainer = document.querySelector('.app-container');
        if (!overlay || !appContainer) return;

        overlay.classList.remove('show');
        appContainer.classList.remove('chat-fullscreen');
        // 清空 JS 同步的高度，恢复 CSS 默认，避免下次打开残留
        overlay.style.height = '';

        // 关闭 + 菜单和推荐面板
        const menu = document.getElementById('mobile-chat-menu');
        if (menu) menu.classList.remove('show');

        if (typeof DialoguePanel !== 'undefined') {
            DialoguePanel.closeRecommendPanel();
        }
    },

    /* ---- 键盘适配 ----
     * Android 上 100dvh 不会随虚拟键盘缩小，所以不依赖 dvh。
     * 改用 visualViewport 计算键盘高度，用 transform: translateY(-键盘高) 把整个浮层上顶，
     * 使 absolute 定位在 overlay 底部的输入框始终紧贴在键盘上方。
     * 注意：只监听 resize，不监听 visualViewport 的 scroll —— 聊天内滚动会触发 scroll 且
     * offsetTop 随页面滚动变化，重算会把 overlay 顶来顶去（跳动）。仅输入聚焦时才允许重算。
     */
    initKeyboardHandler() {
        const input = document.getElementById('dialogue-input');
        const messages = document.getElementById('dialogue-messages');
        const mobileInput = document.getElementById('mobile-chat-input');
        const mobileMessages = document.getElementById('mobile-chat-messages');

        // 浮层高度 = visualViewport 真实可见高度。
        // 地址栏伸缩、键盘弹出/收起都会引起 vv.height 变化，同步后浮层始终占满可见区：
        // 永不露白、不依赖 dvh（兼容性更好）、不需要 transform 推算键盘高度（无跳动）。
        let rafId = null;
        let kbTimer = null;   // 键盘弹出期间的周期校正定时器
        const syncOverlayHeight = () => {
            if (rafId) return;
            rafId = requestAnimationFrame(() => {
                rafId = null;
                const overlay = document.getElementById('mobile-chat-overlay');
                if (!overlay || !overlay.classList.contains('show')) return;
                const vv = window.visualViewport;
                if (vv && vv.height > 0) {
                    const h = Math.round(vv.height);
                    // 仅在高度变化时赋值，避免无谓重排
                    if (overlay.style.height !== h + 'px') {
                        overlay.style.height = h + 'px';
                    }
                }
                // vv 不可用（极老内核）时保持 CSS 的 100vh 兜底，不做处理
            });
        };
        this._adjustOverlayForKeyboard = syncOverlayHeight;
        if (window.visualViewport) {
            // 只监听 resize：地址栏/键盘变化时触发；不监听 scroll（聊天内滚动会触发 scroll 且
            // offsetTop 随页面滚动变化，响应它会导致跳动）。
            window.visualViewport.addEventListener('resize', syncOverlayHeight);
        }
        // 部分机型只触发 window.resize 而不触发 visualViewport 事件
        window.addEventListener('resize', syncOverlayHeight);

        // 把移动端消息滚到底部（输入框贴键盘时仍能看到最新消息）
        const scrollMobileToBottom = () => {
            if (mobileMessages) mobileMessages.scrollTop = mobileMessages.scrollHeight;
        };

        if (mobileInput) {
            mobileInput.addEventListener('focus', () => {
                // 键盘弹出动画期间 vv.height 逐步变化，立即 + 多次补偿重试，
                // 保证浮层高度最终与键盘稳定后的可见区一致。
                syncOverlayHeight();
                [80, 200, 400, 600].forEach(d => setTimeout(syncOverlayHeight, d));
                // 周期校正：部分内核在键盘弹出/收起、或发送消息后 vv 高度恢复滞后甚至不发 resize 事件，
                // 导致浮层高度停在错误值（输入框与键盘分离、中间留空白）。每 200ms 主动校正一次，
                // 任何漏掉的变化都会被自动修正。
                if (!kbTimer) kbTimer = setInterval(syncOverlayHeight, 200);
                setTimeout(scrollMobileToBottom, 300);
                // 键盘弹起：去掉输入栏底部安全区内边距，输入框紧贴键盘上沿，消除两者间空白。
                document.getElementById('mobile-chat-overlay')?.classList.add('keyboard-open');
            });
            mobileInput.addEventListener('blur', () => {
                // 键盘收起有延迟且 vv 恢复滞后，停掉周期校正前先延后多次同步恢复全高
                if (kbTimer) { clearInterval(kbTimer); kbTimer = null; }
                [250, 800, 1500].forEach(d => setTimeout(syncOverlayHeight, d));
                // 键盘收起：恢复安全区内边距（避免输入框与底部 Home 指示条重叠）
                document.getElementById('mobile-chat-overlay')?.classList.remove('keyboard-open');
            });
        }

        // 桌面端 focus：滚到底部
        if (input && messages) {
            input.addEventListener('focus', () => {
                setTimeout(() => {
                    messages.scrollTop = messages.scrollHeight;
                }, 350);
            });
        }

        // 发送按钮点击后也滚动
        document.getElementById('btn-send')?.addEventListener('click', () => {
            setTimeout(() => {
                const m = document.getElementById('dialogue-messages');
                if (m) m.scrollTop = m.scrollHeight;
            }, 200);
        });
        document.getElementById('mobile-btn-send')?.addEventListener('click', () => {
            setTimeout(() => {
                const m = document.getElementById('mobile-chat-messages');
                if (m) m.scrollTop = m.scrollHeight;
            }, 200);
            // 发送后键盘可能收起，补偿同步一次（周期校正由 focus 期间的 kbTimer 兜底）
            setTimeout(syncOverlayHeight, 250);
        });
    },

    /* ---- 快捷关系按钮折叠 ---- */
    initQuickRelToggle() {
        const toggle = document.getElementById('quick-rel-toggle');
        if (!toggle) return;
        toggle.addEventListener('click', () => {
            toggle.parentElement.classList.toggle('expanded');
        });
    },

    /* ---- 快捷对话折叠 ---- */
    initDialogueHintsToggle() {
        const hints = document.getElementById('dialogue-hints');
        if (!hints) return;
        // 点击 hint-label 或空白区域切换展开
        const label = hints.querySelector('.hint-label');
        if (label) {
            label.style.cursor = 'pointer';
            label.addEventListener('click', () => {
                hints.classList.toggle('expanded');
            });
        }
    },

    /* ---- 加速控件：跳过选项下拉 ---- */
    initSpeedSkipToggle() {
        const toggle = document.getElementById('speed-skip-mobile-toggle');
        const options = document.getElementById('speed-skip-options');
        if (!toggle || !options) return;
        toggle.addEventListener('click', (e) => {
            e.stopPropagation();
            toggle.classList.toggle('open');
            options.classList.toggle('open');
        });
        // 点击外部关闭
        document.addEventListener('click', (e) => {
            if (!toggle.contains(e.target) && !options.contains(e.target)) {
                toggle.classList.remove('open');
                options.classList.remove('open');
            }
        });
    },

    /* ---- 状态面板卡片折叠 ---- */
    initStatusGroupCollapse() {
        document.querySelectorAll('.status-group h4').forEach(h4 => {
            h4.addEventListener('click', () => {
                h4.parentElement.classList.toggle('collapsed');
            });
        });
    },

    /* ---- 断点切换时的清理 ---- */
    onBreakpointChange() {
        // 切换断点时按新视口重排天气文本（PC 完整 / 移动端精简）
        App.applyWeatherText();
        if (!this.isMobile) {
            // 从手机切回桌面：恢复所有面板显示
            document.querySelectorAll('.left-panel, .center-panel, .right-panel')
                .forEach(p => {
                    p.classList.remove('active');
                    p.style.display = '';
                });
            // 移除所有折叠状态
            document.querySelectorAll('.left-panel .panel.collapsed, .center-panel .panel.collapsed')
                .forEach(p => p.classList.remove('collapsed'));
            // 关闭汉堡菜单
            const dropdown = document.getElementById('mobile-menu-dropdown');
            if (dropdown) dropdown.classList.remove('open');
        } else {
            // 切到移动端：若无 active 面板，激活默认面板
            const hasActive = document.querySelector('.left-panel.active, .center-panel.active, .right-panel.active');
            if (!hasActive) {
                this.initTabs();
            }
        }
    }
};

// 启动应用
document.addEventListener('DOMContentLoaded', () => App.init());
