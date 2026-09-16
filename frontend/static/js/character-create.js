/* ============================================================
   角色创建 — LLM 驱动的三阶段流程（表单 → 生成 → 预览编辑 → 创建）
   ============================================================ */

const CharacterCreate = (function () {
    'use strict';

    // 当前阶段: 'form' | 'loading' | 'preview'
    let stage = 'form';
    // LLM 生成的完整数据
    let generatedData = null;
    // 用户表单数据
    let formData = {};

    // ========== 行业大类 → 具体职业 ==========
    const INDUSTRY_PROFESSIONS = {
        '教育与学术': ['大学教授', '中学教师', '小学教师', '在读研究生', '在读博士生', '科研研究员', '图书馆馆员'],
        '医疗与健康': ['临床医生', '中医师', '护士', '药剂师', '心理咨询师', '牙医', '兽医', '康复治疗师'],
        '法律与公共安全': ['刑事律师', '民事律师', '公司法务', '法官', '检察官', '警察', '法医'],
        '金融与商业': ['投资银行家', '基金经理', '财务分析师', '会计师', '精算师', '创业者', '商业咨询师'],
        '科技与互联网': ['软件工程师', '产品经理', 'UI/UX设计师', '数据分析师', 'AI算法工程师', '游戏开发者', '运维工程师'],
        '文化与艺术': ['作家', '画家', '音乐家', '摄影师', '舞蹈演员', '戏剧演员', '插画师'],
        '传媒与内容': ['记者', '电视编导', '播客主播', '自媒体博主', '广告创意总监', '翻译'],
        '餐饮与服务': ['主厨', '烘焙师', '咖啡师', '餐厅经理', '酒店管理', '健身教练'],
        '建筑与工程': ['建筑师', '土木工程师', '室内设计师', '景观设计师', '机械工程师', '城市规划师'],
        '时尚与设计': ['时装设计师', '珠宝设计师', '化妆师', '时尚买手'],
        '政府与公共管理': ['公务员', '外交官', '社会工作者'],
        '农业与环保': ['农业技术员', '园艺师', '环境工程师', '生态研究员'],
        '体育': ['职业运动员', '体育教练', '运动康复师'],
        '交通与物流': ['飞行员', '物流经理', '轨道交通工程师'],
    };

    // ========== 省份 → 城市 ==========
    const PROVINCE_CITIES = {
        '直辖市': ['北京', '上海', '天津', '重庆'],
        '江苏': ['南京', '苏州', '无锡', '常州', '南通', '徐州', '扬州', '镇江', '盐城', '泰州'],
        '浙江': ['杭州', '宁波', '温州', '嘉兴', '湖州', '绍兴', '金华', '台州'],
        '安徽': ['合肥', '芜湖', '蚌埠', '安庆', '黄山'],
        '福建': ['福州', '厦门', '泉州', '漳州'],
        '江西': ['南昌', '赣州', '九江', '景德镇'],
        '山东': ['济南', '青岛', '烟台', '威海', '潍坊', '淄博', '临沂'],
        '广东': ['广州', '深圳', '珠海', '佛山', '东莞', '中山', '汕头', '湛江'],
        '广西': ['南宁', '柳州', '桂林', '北海'],
        '海南': ['海口', '三亚'],
        '湖南': ['长沙', '株洲', '湘潭', '衡阳', '岳阳'],
        '湖北': ['武汉', '宜昌', '襄阳', '荆州'],
        '河南': ['郑州', '洛阳', '开封', '新乡', '南阳'],
        '河北': ['石家庄', '唐山', '保定', '秦皇岛', '邯郸'],
        '山西': ['太原', '大同', '晋中', '临汾'],
        '内蒙古': ['呼和浩特', '包头', '鄂尔多斯', '赤峰'],
        '辽宁': ['沈阳', '大连', '鞍山', '抚顺', '锦州'],
        '吉林': ['长春', '吉林', '延边'],
        '黑龙江': ['哈尔滨', '齐齐哈尔', '大庆', '牡丹江'],
        '四川': ['成都', '绵阳', '德阳', '宜宾', '泸州', '乐山'],
        '云南': ['昆明', '大理', '丽江', '曲靖'],
        '贵州': ['贵阳', '遵义', '六盘水'],
        '西藏': ['拉萨'],
        '陕西': ['西安', '咸阳', '宝鸡', '渭南'],
        '甘肃': ['兰州', '天水', '敦煌'],
        '青海': ['西宁'],
        '宁夏': ['银川'],
        '新疆': ['乌鲁木齐', '喀什', '伊犁'],
    };

    // ========== 玩家身份预设 ==========
    const PLAYER_IDENTITIES = [
        '同班同学', '学长学姐', '学弟学妹', '室友', '同事', '上司', '下属',
        '合作伙伴', '竞争对手', '客户', '邻居', '青梅竹马', '老乡', '网友',
        '前任', '委托人', '健身私教', '咖啡店常客', '志愿者同伴', '远房亲戚',
    ];

    // ========== 性格预设模版：核心底色 + 正向 + 中性 + 短板（30 个女性角色） ==========
    // 字段：name 模版名；core 核心底色；positive 正向；neutral 中性；weakness 短板
    const TRAIT_TEMPLATES = [
        { name: '温暖治愈系姐姐', core: '温柔包容的底色', positive: '共情力强、极有耐心', neutral: '慢热、偏爱稳定', weakness: '容易心软、边界感弱' },
        { name: '高冷御姐', core: '疏离而优雅的底色', positive: '专业过硬、临危冷静', neutral: '话少、毒舌但有分寸', weakness: '不擅长表达亲密、怕麻烦' },
        { name: '元气少女', core: '明亮热烈的底色', positive: '乐观、感染力爆棚', neutral: '三分钟热度、爱热闹', weakness: '粗心、情绪来得快去得快' },
        { name: '腹黑学霸', core: '冷静通透的底色', positive: '智商高、洞察力极强', neutral: '喜欢观察、记仇', weakness: '懒得解释、疏于维系关系' },
        { name: '居家温柔系', core: '细致妥帖的底色', positive: '会照顾人、条理性强', neutral: '有点洁癖、爱操心', weakness: '控制欲强、难放手' },
        { name: '酷飒机车妹', core: '自由不羁的底色', positive: '重义气、胆大心细', neutral: '随性、爱熬夜', weakness: '冲动、没耐心' },
        { name: '文艺清冷女', core: '疏淡如水的底色', positive: '审美好、安静可靠', neutral: '独处、敏感', weakness: '回避冲突、社交低欲' },
        { name: '职场精英姐', core: '进取果决的底色', positive: '执行力强、目标感满', neutral: '工作狂、直接', weakness: '完美主义、忽视生活' },
        { name: '邻家知心姐姐', core: '温厚可靠的底色', positive: '体贴、动手能力强', neutral: '热心肠、慢半拍', weakness: '不够浪漫、容易吃闷亏' },
        { name: '傲娇大小姐', core: '骄傲娇憨的底色', positive: '真诚、极度护短', neutral: '嘴硬、爱面子', weakness: '任性、不会示弱' },
        { name: '沉稳知性学姐', core: '温润如玉的底色', positive: '包容、有担当', neutral: '理性、略保守', weakness: '优柔、怕伤人' },
        { name: '古灵精怪', core: '跳脱鲜活的底色', positive: '脑洞大、有趣', neutral: '善变、爱恶作剧', weakness: '没常性、容易分心' },
        { name: '禁欲系御姐', core: '克制自持的底色', positive: '自律、渊博', neutral: '严肃、有距离感', weakness: '情感钝感、过度理性' },
        { name: '甜美软妹', core: '软糯无害的底色', positive: '会撒娇、治愈', neutral: '依赖、爱睡', weakness: '玻璃心、选择困难' },
        { name: '硬核女汉子', core: '爽利坦荡的底色', positive: '仗义、抗压', neutral: '大咧咧、直球', weakness: '粗线条、不懂委婉' },
        { name: '神秘冰山', core: '深不可测的底色', positive: '沉稳、守口如瓶', neutral: '神秘、少言', weakness: '难以靠近、慢热至极' },
        { name: '阳光运动少女', core: '热烈明亮的底色', positive: '活力、团队感强', neutral: '简单直率、爱竞技', weakness: '急性子、不喜复杂' },
        { name: '知性御姐', core: '从容智慧的底色', positive: '通透、善解人意', neutral: '慢生活、爱读书', weakness: '偶尔佛系、不够狠' },
        { name: '小恶魔系', core: '狡黠撩人的底色', positive: '情商高、会撩', neutral: '爱试探、掌握主动', weakness: '容易腻、怕无聊' },
        { name: '黏人小女友', core: '忠诚依恋的底色', positive: '专一、顺从', neutral: '黏人、听话', weakness: '容易失去自我、易焦虑' },
        { name: '独立大女主', core: '清醒自洽的底色', positive: '独立、头脑清醒', neutral: '享受独处、有主见', weakness: '不轻易依赖、怕被束缚' },
        { name: '温柔医者', core: '悲悯沉静的底色', positive: '责任感强、镇定', neutral: '常加班、隐忍', weakness: '把情绪藏起来、易内耗' },
        { name: '毒舌闺蜜', core: '互损亲昵的底色', positive: '嘴替、仗义', neutral: '爱吐槽、糙', weakness: '嘴比脑子快、不会哄人' },
        { name: '盐系少女', core: '干净疏朗的底色', positive: '清爽、专注', neutral: '寡言、慢热', weakness: '木讷、不善表达' },
        { name: '妩媚御姐', core: '风情慵懒的底色', positive: '自信、会生活', neutral: '晚起、爱享受', weakness: '拖延、怕麻烦事' },
        { name: '爽朗女侠', core: '朴实热烈的底色', positive: '真诚、够意思', neutral: '嗓门大、直来直去', weakness: '不会拐弯、易得罪人' },
        { name: '病娇系', core: '黏腻偏执的底色', positive: '极致专注、深情', neutral: '占有欲强、敏感', weakness: '控制欲过强、易崩' },
        { name: '国学温润女', core: '含蓄内敛的底色', positive: '有涵养、稳重', neutral: '慢热、重礼数', weakness: '不擅主动、含蓄到闷' },
        { name: '俏皮辣妹', core: '张扬鲜亮的底色', positive: '自信、敢穿敢说', neutral: '爱玩、社交牛', weakness: '三分钟热度、怕无聊' },
        { name: '沉静匠人', core: '专注忘我的底色', positive: '有匠心、极有耐心', neutral: '宅、手作控', weakness: '社交少、生活能力弱' },
    ];

    // ========== 工具函数 ==========
    function showToast(msg, type) {
        if (typeof Toast !== 'undefined') Toast.show(msg, type);
    }

    function switchStage(s) {
        stage = s;
        document.querySelectorAll('.create-stage').forEach(el => el.style.display = 'none');
        const target = document.getElementById('create-stage-' + s);
        if (target) target.style.display = '';
    }

    function _esc(s) {
        return String(s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    // ========== 性格预设模版 UI ==========
    function _populateTraitTemplateSelect() {
        const sel = document.getElementById('cc-trait-template');
        if (!sel || sel.dataset.filled) return;
        TRAIT_TEMPLATES.forEach((t, i) => {
            const opt = document.createElement('option');
            opt.value = String(i);
            opt.textContent = t.name;
            sel.appendChild(opt);
        });
        const optOther = document.createElement('option');
        optOther.value = '__custom__';
        optOther.textContent = '✏️ 自定义（手动输入）';
        sel.appendChild(optOther);
        sel.dataset.filled = '1';
    }

    function _onTraitTemplateChange() {
        const sel = document.getElementById('cc-trait-template');
        const preview = document.getElementById('cc-trait-preview');
        const custom = document.getElementById('cc-trait-custom');
        if (!sel || !preview || !custom) return;
        const v = sel.value;
        if (v === '__custom__') {
            custom.style.display = '';
            preview.style.display = 'none';
            preview.innerHTML = '';
            return;
        }
        if (v === '') {
            custom.style.display = 'none';
            preview.style.display = 'none';
            preview.innerHTML = '';
            return;
        }
        const t = TRAIT_TEMPLATES[parseInt(v, 10)];
        if (!t) return;
        custom.style.display = 'none';
        preview.style.display = 'flex';
        preview.innerHTML =
            `<div class="trait-chip t-core"><span class="trait-label">核心底色</span>${_esc(t.core)}</div>` +
            `<div class="trait-chip t-pos"><span class="trait-label">正向</span>${_esc(t.positive)}</div>` +
            `<div class="trait-chip t-neu"><span class="trait-label">中性</span>${_esc(t.neutral || '—')}</div>` +
            `<div class="trait-chip t-weak"><span class="trait-label">短板</span>${_esc(t.weakness || '—')}</div>`;
    }

    // 返回选中的性格预设对象（或 null）
    function _readTraitPreset() {
        const sel = document.getElementById('cc-trait-template');
        if (!sel) return null;
        const v = sel.value;
        if (v === '__custom__') {
            const core = (document.getElementById('cc-core')?.value || '').trim();
            const positive = (document.getElementById('cc-positive')?.value || '').trim();
            if (!core && !positive) return null;
            return {
                core: core || '（未填）',
                positive: positive || '（未填）',
                neutral: (document.getElementById('cc-neutral')?.value || '').trim(),
                weakness: (document.getElementById('cc-weak')?.value || '').trim(),
            };
        }
        if (v === '' || v === undefined) return null;
        const t = TRAIT_TEMPLATES[parseInt(v, 10)];
        return t ? { core: t.core, positive: t.positive, neutral: t.neutral, weakness: t.weakness } : null;
    }

    function _resetTraitUI() {
        const sel = document.getElementById('cc-trait-template');
        if (sel) sel.value = '';
        const preview = document.getElementById('cc-trait-preview');
        if (preview) { preview.style.display = 'none'; preview.innerHTML = ''; }
        const custom = document.getElementById('cc-trait-custom');
        if (custom) custom.style.display = 'none';
        ['cc-core', 'cc-positive', 'cc-neutral', 'cc-weak'].forEach(id => {
            const e = document.getElementById(id);
            if (e) e.value = '';
        });
    }

    // ========== 初始化下拉选项 ==========
    function _populateSelect(selId, items, noneLabel) {
        const sel = document.getElementById(selId);
        if (!sel) return;
        // 保留第一个占位选项
        const placeholder = sel.options[0];
        sel.innerHTML = '';
        sel.appendChild(placeholder);
        items.forEach(v => {
            const opt = document.createElement('option');
            opt.value = v;
            opt.textContent = v;
            sel.appendChild(opt);
        });
        // 加「其他」选项
        const optOther = document.createElement('option');
        optOther.value = '__other__';
        optOther.textContent = '✏️ 其他（手动输入）';
        sel.appendChild(optOther);
    }

    function _toggleCustomInput(selId, customId) {
        const sel = document.getElementById(selId);
        const custom = document.getElementById(customId);
        if (sel && custom) {
            custom.style.display = sel.value === '__other__' ? '' : 'none';
            if (sel.value !== '__other__') custom.value = '';
        }
    }

    function initDropdowns() {
        // 行业大类
        const industrySel = document.getElementById('cc-industry');
        if (industrySel && !industrySel.options.length) {
            const industries = Object.keys(INDUSTRY_PROFESSIONS);
            industries.forEach(ind => {
                const opt = document.createElement('option');
                opt.value = ind;
                opt.textContent = ind;
                industrySel.appendChild(opt);
            });
        }

        // 具体职业 — 初始填全部职业
        const allProfs = new Set();
        Object.values(INDUSTRY_PROFESSIONS).forEach(list => list.forEach(p => allProfs.add(p)));
        _populateSelect('cc-profession', Array.from(allProfs).sort(), '-- 请先选行业大类 --');

        // 省份
        const provinceSel = document.getElementById('cc-province');
        if (provinceSel && !provinceSel.options.length) {
            Object.keys(PROVINCE_CITIES).forEach(prov => {
                const opt = document.createElement('option');
                opt.value = prov;
                opt.textContent = prov;
                provinceSel.appendChild(opt);
            });
        }

        // 城市 — 初始填全部城市
        const allCities = new Set();
        Object.values(PROVINCE_CITIES).forEach(list => list.forEach(c => allCities.add(c)));
        _populateSelect('cc-city', Array.from(allCities).sort(), '-- 请先选省份 --');

        // 玩家身份
        _populateSelect('cc-player-identity', PLAYER_IDENTITIES, '-- 请选择 --');

        // 性格预设模版
        _populateTraitTemplateSelect();
        const traitSel = document.getElementById('cc-trait-template');
        if (traitSel && !traitSel.dataset.handlerAttached) {
            traitSel.addEventListener('change', _onTraitTemplateChange);
            traitSel.dataset.handlerAttached = '1';
        }

        // === 联动：行业大类 → 具体职业 ===
        industrySel?.addEventListener('change', () => {
            const industry = industrySel.value;
            const profs = INDUSTRY_PROFESSIONS[industry] || [];
            _populateSelect('cc-profession', profs.length ? profs : Array.from(allProfs).sort(), '-- 请选择职业 --');
        });

        // === 联动：省份 → 城市 ===
        provinceSel?.addEventListener('change', () => {
            const prov = provinceSel.value;
            const cities = PROVINCE_CITIES[prov] || [];
            _populateSelect('cc-city', cities.length ? cities : Array.from(allCities).sort(), '-- 请选择城市 --');
        });

        // === 「其他」选项 → 显示自定义文本框 ===
        const attachOtherHandler = (selId, customId) => {
            const sel = document.getElementById(selId);
            sel?.addEventListener('change', () => _toggleCustomInput(selId, customId));
        };
        attachOtherHandler('cc-profession', 'cc-profession-custom');
        attachOtherHandler('cc-city', 'cc-city-custom');
        attachOtherHandler('cc-player-identity', 'cc-player-identity-custom');
    }

    // ========== 表单收集 ==========
    function _readSelectOrCustom(selId, customId) {
        const sel = document.getElementById(selId);
        const custom = document.getElementById(customId);
        const selVal = sel?.value || '';
        if (selVal === '__other__') return (custom?.value || '').trim();
        return selVal;
    }

    function collectForm() {
        const name = (document.getElementById('cc-name')?.value || '').trim();
        const age = parseInt(document.getElementById('cc-age')?.value) || 20;
        const gender = document.getElementById('cc-gender')?.value || 'female';
        const profession = _readSelectOrCustom('cc-profession', 'cc-profession-custom');
        const city = _readSelectOrCustom('cc-city', 'cc-city-custom');
        const playerIdentity = _readSelectOrCustom('cc-player-identity', 'cc-player-identity-custom');
        const playerNickname = (document.getElementById('cc-player-nickname')?.value || '').trim();
        const descriptionRaw = (document.getElementById('cc-description')?.value || '').trim();
        const worldPrompt = (document.getElementById('cc-world-prompt')?.value || '').trim();

        if (!name) { showToast('请填写姓名', 'error'); return null; }
        if (/[\\/:*?"<>|]/.test(name)) { showToast('姓名不能包含特殊字符', 'error'); return null; }
        if (!profession) { showToast('请选择或输入职业', 'error'); return null; }
        if (!city) { showToast('请选择或输入城市', 'error'); return null; }
        if (!playerIdentity) { showToast('请选择或输入玩家身份', 'error'); return null; }
        if (!playerNickname) { showToast('请填写玩家昵称', 'error'); return null; }

        // 性格预设模版（核心底色 + 正向 + 中性 + 短板）→ 合并进描述，供 LLM 生成参考
        let description = descriptionRaw;
        const trait = _readTraitPreset();
        if (trait) {
            const traitText = `【性格预设】核心底色：${trait.core}；正向：${trait.positive}；中性：${trait.neutral || '—'}；短板：${trait.weakness || '—'}`;
            description = descriptionRaw ? (descriptionRaw + '\n' + traitText) : traitText;
        }

        return { name, age, gender, profession, city, player_identity: playerIdentity, player_nickname: playerNickname, description, world_prompt: worldPrompt };
    }

    // ========== 草稿持久化（防刷新丢失未保存角色） ==========
    const DRAFT_KEY = 'simlife_char_draft';
    function _saveDraft() {
        try {
            if (!generatedData || !generatedData.profile) return; // 仅在有完整生成数据时存
            const name = (generatedData.profile.identity && generatedData.profile.identity.name) || formData.name || '';
            if (!name) return;
            const draft = { name: name, savedAt: Date.now(), stage: 'preview', formData: formData, generatedData: generatedData };
            localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
        } catch (e) { /* 容量超限等异常忽略 */ }
    }
    function _loadDraft() {
        try {
            const raw = localStorage.getItem(DRAFT_KEY);
            return raw ? JSON.parse(raw) : null;
        } catch (e) { return null; }
    }
    function _clearDraft() {
        try { localStorage.removeItem(DRAFT_KEY); } catch (e) {}
    }
    function _restoreDraft(draft) {
        if (!draft) return;
        formData = draft.formData || {};
        generatedData = draft.generatedData || null;
        if (generatedData && generatedData.profile) {
            renderPreview();
            switchStage('preview');
        } else {
            switchStage('form');
        }
    }
    function _maybePromptDraft() {
        const draft = _loadDraft();
        if (!draft || !draft.generatedData || !draft.generatedData.profile) return;
        const name = (draft.generatedData.profile.identity && draft.generatedData.profile.identity.name) || draft.name || '未命名';
        const modal = document.getElementById('character-create-modal');
        if (!modal) return;
        let bar = document.getElementById('cc-draft-bar');
        if (!bar) {
            bar = document.createElement('div');
            bar.id = 'cc-draft-bar';
            bar.style.cssText = 'display:none;align-items:center;gap:12px;padding:12px 16px;margin-bottom:12px;background:rgba(124,92,252,0.08);border:1px solid rgba(124,92,252,0.3);border-radius:8px;font-size:13px';
            const content = modal.querySelector('.modal-content');
            const header = modal.querySelector('.modal-header');
            if (content && header) content.insertBefore(bar, header.nextSibling);
        }
        bar.innerHTML = '';
        const txt = document.createElement('span');
        txt.style.flex = '1';
        txt.textContent = '检测到未保存的新角色【' + name + '】，是否导入上次生成的数据？';
        const btnImport = document.createElement('button');
        btnImport.className = 'btn-primary';
        btnImport.style.padding = '6px 14px';
        btnImport.textContent = '导入';
        btnImport.addEventListener('click', () => { _restoreDraft(draft); bar.style.display = 'none'; });
        const btnDiscard = document.createElement('button');
        btnDiscard.className = 'btn-secondary';
        btnDiscard.style.padding = '6px 14px';
        btnDiscard.textContent = '放弃';
        btnDiscard.addEventListener('click', () => { _clearDraft(); bar.style.display = 'none'; });
        bar.appendChild(txt);
        bar.appendChild(btnImport);
        bar.appendChild(btnDiscard);
        bar.style.display = 'flex';
    }
    function _onBeforeUnload(e) {
        const draft = _loadDraft();
        if (draft && draft.generatedData && draft.generatedData.profile) {
            e.preventDefault();
            e.returnValue = '';
            return '';
        }
    }

    // ========== 阶段1 → 阶段2: 生成角色 ==========
    async function handleGenerate() {
        const form = collectForm();
        if (!form) return;
        formData = form;

        switchStage('loading');
        updateLoadingText('正在生成角色画像...');

        try {
            const resp = await fetch('/api/character/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(form),
            });
            const data = await resp.json();

            if (!data.success) {
                showToast(data.error || '生成失败', 'error');
                switchStage('form');
                return;
            }

            generatedData = data.data;
            renderPreview();
            switchStage('preview');
        } catch (err) {
            showToast('网络错误: ' + err.message, 'error');
            switchStage('form');
        }
    }

    function updateLoadingText(text) {
        const el = document.getElementById('create-loading-text');
        if (el) el.textContent = text;
    }

    // ========== 阶段3: 预览编辑 ==========
    function renderPreview() {
        const container = document.getElementById('create-preview-content');
        if (!container || !generatedData) return;

        const p = generatedData.profile || {};
        const identity = p.identity || {};
        const personality = p.personality || {};
        const dreams = p.dreams || {};
        const background = p.background || {};
        const social = p.social_circle || {};
        const friends = social.friends || [];
        const achievements = generatedData.achievements || [];
        const goals = generatedData.goals || [];
        const activityMap = generatedData.activity_map || [];
        const outfit = generatedData.outfit || {};
        const components = outfit.components || [];
        const presets = outfit.presets || [];

        const esc = s => String(s || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');

        container.innerHTML = `
            <div class="preview-section">
                <div class="preview-section-header">
                    <h4>基本信息</h4>
                </div>
                <div class="preview-grid">
                    <label>姓名 <input type="text" id="pv-name" value="${esc(identity.name)}"></label>
                    <label>年龄 <input type="number" id="pv-age" value="${identity.age || 20}"></label>
                    <label>身份 <input type="text" id="pv-identity-label" value="${esc(identity.identity_label)}"></label>
                    <label>专业 <input type="text" id="pv-major" value="${esc(identity.major)}"></label>
                    <label>学历 <input type="text" id="pv-education" value="${esc(identity.education)}"></label>
                    <label>外貌 <input type="text" id="pv-appearance" value="${esc(identity.appearance)}"></label>
                </div>
            </div>

            <div class="preview-section">
                <div class="preview-section-header">
                    <h4>性格</h4>
                </div>
                <div class="preview-grid">
                    <label>标签 <input type="text" id="pv-personality-type" value="${esc(Array.isArray(personality.type) ? personality.type.join(', ') : personality.type)}"></label>
                    <label>语气 <input type="text" id="pv-tone" value="${esc(personality.tone)}"></label>
                    <label>表达风格 <input type="text" id="pv-expression" value="${esc(personality.expression_style)}"></label>
                    <label>MBTI <input type="text" id="pv-mbti" value="${esc(personality.mbti)}"></label>
                </div>
            </div>

            <div class="preview-section">
                <div class="preview-section-header">
                    <h4>梦想</h4>
                </div>
                <div class="preview-grid">
                    <label>主梦想 <input type="text" id="pv-dream-primary" value="${esc(dreams.primary)}"></label>
                    <label>次梦想 <input type="text" id="pv-dream-secondary" value="${esc(dreams.secondary)}"></label>
                    <label class="full-width">动机 <input type="text" id="pv-dream-motivation" value="${esc(dreams.motivation)}"></label>
                </div>
            </div>

            <div class="preview-section">
                <div class="preview-section-header">
                    <h4>背景</h4>
                </div>
                <div class="preview-grid">
                    <label>家乡 <input type="text" id="pv-hometown" value="${esc(background.hometown)}"></label>
                    <label>家庭 <input type="text" id="pv-family" value="${esc(background.family)}"></label>
                    <label>经济 <input type="text" id="pv-economic" value="${esc(background.economic)}"></label>
                    <label>恋爱史 <input type="text" id="pv-relationship-history" value="${esc(background.relationship_history)}"></label>
                    <label class="full-width">爱好 <input type="text" id="pv-hobbies" value="${esc(background.hobbies)}"></label>
                </div>
            </div>

            <div class="preview-section">
                <div class="preview-section-header">
                    <h4>朋友圈</h4>
                    <button class="btn-regen-module" data-module="profile" title="重新生成画像（含朋友圈）">重新生成</button>
                </div>
                <div class="preview-list" id="pv-friends-list">
                    ${friends.map((f, i) => `
                        <div class="preview-item">
                            <input type="text" class="pv-friend-name" data-idx="${i}" value="${esc(f.name)}" placeholder="姓名">
                            <input type="text" class="pv-friend-role" data-idx="${i}" value="${esc(f.role)}" placeholder="关系">
                            <input type="text" class="pv-friend-personality" data-idx="${i}" value="${esc(f.personality)}" placeholder="性格">
                            <input type="text" class="pv-friend-bio" data-idx="${i}" value="${esc(f.bio)}" placeholder="简介">
                        </div>
                    `).join('')}
                </div>
            </div>

            <div class="preview-section">
                <div class="preview-section-header">
                    <h4>成就系统（${achievements.length}个）</h4>
                    <button class="btn-regen-module" data-module="achievements">重新生成</button>
                </div>
                <div class="preview-list">
                    ${achievements.map((a, i) => `
                        <div class="preview-item">
                            <span class="material-symbols-outlined pv-ach-icon" data-idx="${i}">${esc(a.icon || 'star')}</span>
                            <input type="text" class="pv-ach-name" data-idx="${i}" value="${esc(a.name)}" placeholder="名称">
                            <input type="text" class="pv-ach-desc" data-idx="${i}" value="${esc(a.description)}" placeholder="描述">
                            <span class="preview-cond">${esc((window.GoalFormat && window.GoalFormat.formatAchievementRule(a)) || a.hint || '')}</span>
                        </div>
                    `).join('')}
                </div>
            </div>

            <div class="preview-section">
                <div class="preview-section-header">
                    <h4>成长目标（${goals.length}个）</h4>
                    <button class="btn-regen-module" data-module="achievements">重新生成</button>
                </div>
                <div class="preview-list">
                    ${goals.map((g, i) => `
                        <div class="preview-item">
                            <input type="text" class="pv-goal-label" data-idx="${i}" value="${esc(g.label || g.key)}" placeholder="目标名">
                            <span class="preview-cond">${esc((window.GoalFormat && window.GoalFormat.formatGoalRule(g)) || '')}</span>
                        </div>
                    `).join('')}
                </div>
            </div>

            <div class="preview-section">
                <div class="preview-section-header">
                    <h4>活动地图（${activityMap.length}个地点）</h4>
                    <button class="btn-regen-module" data-module="activity_map">重新生成</button>
                </div>
                <div class="preview-list">
                    ${activityMap.map((v, i) => `
                        <div class="preview-item">
                            <input type="text" class="pv-venue-id" data-idx="${i}" value="${esc(v.venue_id)}" placeholder="ID" style="flex:0.5">
                            <input type="text" class="pv-venue-name" data-idx="${i}" value="${esc(v.venue_name)}" placeholder="地点名">
                        </div>
                    `).join('')}
                </div>
            </div>

            <div class="preview-section">
                <div class="preview-section-header">
                    <h4>穿搭数据（${components.length}件组件 / ${presets.length}套预设）</h4>
                    <button class="btn-regen-module" data-module="outfit">重新生成</button>
                </div>
                <div class="preview-outfit-summary">
                    <p>组件类型分布：
                        ${Object.entries(groupBy(components, 'type')).map(([k, v]) => `${k}(${v.length})`).join(' / ')}
                    </p>
                    <p>预设季节分布：
                        ${Object.entries(groupBy(presets, 'season')).map(([k, v]) => `${k}(${v.length})`).join(' / ')}
                    </p>
                </div>
            </div>

            <!-- 新增：世界观预览 -->
            <div class="preview-section">
                <div class="preview-section-header">
                    <h4>世界观</h4>
                    <button class="btn-regen-module" id="pv-gen-worldview" data-module="worldview" type="button">生成世界观</button>
                </div>
                <div id="pv-worldview-preview" style="padding:8px;font-size:13px;color:var(--text-secondary)">创建角色后可生成</div>
            </div>

            <!-- 新增：专属事件模板预览 -->
            <div class="preview-section">
                <div class="preview-section-header">
                    <h4>专属事件模板</h4>
                    <button class="btn-regen-module" id="pv-gen-templates" data-module="event_templates" type="button">生成专属模板</button>
                </div>
                <div id="pv-templates-preview" style="padding:8px;font-size:13px;color:var(--text-secondary)">创建角色后可生成</div>
            </div>
        `;

        // 绑定重新生成按钮
        container.querySelectorAll('.btn-regen-module').forEach(btn => {
            btn.addEventListener('click', () => handleRegenerateModule(btn.dataset.module));
        });
        _saveDraft();
    }

    function groupBy(arr, key) {
        const result = {};
        arr.forEach(item => {
            const k = item[key] || 'unknown';
            if (!result[k]) result[k] = [];
            result[k].push(item);
        });
        return result;
    }

    // ========== 收集预览阶段的编辑数据 ==========
    function collectEditedData() {
        if (!generatedData) return null;

        const p = generatedData.profile || {};
        const identity = p.identity || {};
        const personality = p.personality || {};
        const dreams = p.dreams || {};
        const background = p.background || {};
        const social = p.social_circle || {};

        // 更新 profile 中的可编辑字段
        identity.name = val('pv-name', identity.name);
        identity.age = parseInt(val('pv-age', identity.age)) || identity.age;
        identity.identity_label = val('pv-identity-label', identity.identity_label);
        identity.major = val('pv-major', identity.major);
        identity.education = val('pv-education', identity.education);
        identity.appearance = val('pv-appearance', identity.appearance);

        const ptypeRaw = val('pv-personality-type', '');
        personality.type = ptypeRaw.split(',').map(s => s.trim()).filter(Boolean);
        personality.tone = val('pv-tone', personality.tone);
        personality.expression_style = val('pv-expression', personality.expression_style);
        personality.mbti = val('pv-mbti', personality.mbti);

        dreams.primary = val('pv-dream-primary', dreams.primary);
        dreams.secondary = val('pv-dream-secondary', dreams.secondary);
        dreams.motivation = val('pv-dream-motivation', dreams.motivation);

        background.hometown = val('pv-hometown', background.hometown);
        background.family = val('pv-family', background.family);
        background.economic = val('pv-economic', background.economic);
        background.relationship_history = val('pv-relationship-history', background.relationship_history);
        background.hobbies = val('pv-hobbies', background.hobbies);

        // 更新朋友
        const friends = social.friends || [];
        document.querySelectorAll('.pv-friend-name').forEach(inp => {
            const idx = parseInt(inp.dataset.idx);
            if (friends[idx]) friends[idx].name = inp.value;
        });
        document.querySelectorAll('.pv-friend-role').forEach(inp => {
            const idx = parseInt(inp.dataset.idx);
            if (friends[idx]) friends[idx].role = inp.value;
        });
        document.querySelectorAll('.pv-friend-personality').forEach(inp => {
            const idx = parseInt(inp.dataset.idx);
            if (friends[idx]) friends[idx].personality = inp.value;
        });
        document.querySelectorAll('.pv-friend-bio').forEach(inp => {
            const idx = parseInt(inp.dataset.idx);
            if (friends[idx]) friends[idx].bio = inp.value;
        });

        // 更新成就
        const achievements = generatedData.achievements || [];
        document.querySelectorAll('.pv-ach-name').forEach(inp => {
            const idx = parseInt(inp.dataset.idx);
            if (achievements[idx]) achievements[idx].name = inp.value;
        });
        document.querySelectorAll('.pv-ach-desc').forEach(inp => {
            const idx = parseInt(inp.dataset.idx);
            if (achievements[idx]) achievements[idx].description = inp.value;
        });

        // 更新活动地图
        const activityMap = generatedData.activity_map || [];
        document.querySelectorAll('.pv-venue-id').forEach(inp => {
            const idx = parseInt(inp.dataset.idx);
            if (activityMap[idx]) activityMap[idx].venue_id = inp.value;
        });
        document.querySelectorAll('.pv-venue-name').forEach(inp => {
            const idx = parseInt(inp.dataset.idx);
            if (activityMap[idx]) activityMap[idx].venue_name = inp.value;
        });

        return generatedData;
    }

    function val(id, fallback) {
        const el = document.getElementById(id);
        return el ? el.value : fallback;
    }

    // ========== 重新生成模块 ==========
    async function handleRegenerateModule(module) {
        // 先收集当前编辑的数据（保留用户修改）
        collectEditedData();

        const btn = document.querySelector(`.btn-regen-module[data-module="${module}"]`);
        if (btn) { btn.disabled = true; btn.textContent = '生成中...'; }

        // 世界观/专属模板：创建后到角色面板生成
        if (['worldview', 'event_templates'].includes(module)) {
            showToast('请先完成角色创建，然后在「角色属性」面板中生成', 'info');
            if (btn) { btn.disabled = false; btn.textContent = '创建后生成'; }
            return;
        }

        updateLoadingText(module === 'profile' ? '正在重新生成完整画像...' : `正在重新生成${moduleLabel(module)}...`);

        try {
            const resp = await fetch('/api/character/regenerate-module', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    module,
                    profile: generatedData.profile,
                    form_data: formData,
                    // 仅「重新生成画像」时不级联子模块，保留用户已编辑的成就/活动地图/穿搭（#22）
                    cascade: module !== 'profile',
                }),
            });
            const data = await resp.json();

            if (!data.success) {
                showToast(data.error || '重新生成失败', 'error');
                if (btn) { btn.disabled = false; btn.textContent = '重新生成'; }
                return;
            }

            if (module === 'profile') {
                // 仅替换画像，保留用户已编辑的成就 / 活动地图 / 穿搭（#22）
                generatedData.profile = data.data.profile;
                if (data.data.form_data) generatedData.form_data = data.data.form_data;
            } else {
                // 单模块重新生成
                generatedData[module] = data.data;
            }

            renderPreview();
            showToast(`${moduleLabel(module)}已重新生成`, 'success');
        } catch (err) {
            showToast('网络错误: ' + err.message, 'error');
            if (btn) { btn.disabled = false; btn.textContent = '重新生成'; }
        }
    }

    function moduleLabel(module) {
        const labels = { profile: '画像', achievements: '成就', activity_map: '活动地图', outfit: '穿搭', worldview: '世界观', event_templates: '专属模板' };
        return labels[module] || module;
    }

    /** 创建后自动生成第一个任务并展示审核 */
    async function _generateFirstCase(charName) {
        const container = document.getElementById('create-preview-content');

        const label = '任务';
        container.innerHTML = `
            <div class="create-stage" style="text-align:center;padding:40px 24px">
                <div class="loading-spinner"></div>
                <p style="margin-top:16px;color:var(--text-secondary)">正在用 LLM 生成第一个${label}...</p>
            </div>
        `;
        document.getElementById('btn-preview-cancel').style.display = 'none';

        // 1. 先检查或生成世界观
        let worldPrompt = '';
        try {
            const wsR = await fetch(`/api/character/${encodeURIComponent(charName)}/world-setting`);
            const wsData = await wsR.json();
            worldPrompt = wsData.success ? (wsData.data?.world_prompt || '') : '';
        } catch(e) {}

        if (!worldPrompt) {
            try {
                await fetch(`/api/character/${encodeURIComponent(charName)}/world-setting/generate`, {method:'POST'});
            } catch(e) {}
            worldPrompt = '一名大学生/职场人士的成长故事';
        }

        // 2. 生成
        const apiUrl = `/api/character/${encodeURIComponent(charName)}/missions/generate`;
        try {
            const r = await fetch(apiUrl, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({world_prompt: worldPrompt}),
            });
            const data = await r.json();
            if (!data.success) {
                container.innerHTML = `
                    <div style="text-align:center;padding:40px">
                        <p>${label}生成失败: ${data.error || ''}</p>
                        <button class="btn btn-primary" onclick="location.reload()">跳过，直接进入游戏</button>
                    </div>
                `;
                return;
            }
            const c = data.data;
            const stages = c.stages || c.stages_list || [];
            const npcs = c.npc_roster || c.npc_list || [];
            const nameKey = 'mission_name';

            container.innerHTML = `
                <div style="padding:20px">
                    <h3 style="margin-bottom:8px">📋 第一个${label}已生成</h3>
                    <p style="color:var(--text-secondary);margin-bottom:16px">请审核以下内容，确认后进入游戏</p>
                    <div class="preview-section">
                        <div class="preview-section-header">
                            <h4>${c[nameKey]}</h4>
                            <span style="font-size:13px;color:var(--text-secondary)">第${c.start_day}-${c.end_day}天</span>
                        </div>
                        <div style="font-size:13px;line-height:1.8">
                            <strong>阶段 (${stages.length}个)：</strong>
                            ${stages.map((s, i) => `<div style="padding:2px 0">${i+1}. ${s.name}（第${c.start_day + (s.day_offset||0)}天）${s.choices ? '🔀' : ''}</div>`).join('')}
                        </div>
                    </div>
                    <div class="preview-section">
                        <div class="preview-section-header">
                            <h4>涉及人物 (${npcs.length}个)</h4>
                        </div>
                        <div style="font-size:13px">${npcs.map(n => `<span style="display:inline-block;padding:2px 8px;margin:3px;background:rgba(124,92,252,0.08);border-radius:4px">${n.name}（${n.role}，${n.relation_type}）</span>`).join('')}</div>
                    </div>
                    <div style="display:flex;gap:10px;margin-top:16px">
                        <button class="btn" style="flex:1;padding:10px" onclick="location.reload()">跳过</button>
                        <button class="btn btn-primary" style="flex:1;padding:10px" onclick="location.reload()">✅ 确认，开始游戏</button>
                    </div>
                </div>
            `;
        } catch(e) {
            container.innerHTML = `
                <div style="text-align:center;padding:40px">
                    <p>${label}生成出错: ${e.message}</p>
                    <button class="btn btn-primary" onclick="location.reload()">跳过，直接进入游戏</button>
                </div>
            `;
        }
    }

    // ========== 确认创建 ==========
    async function handleConfirmCreate() {
        const edited = collectEditedData();
        if (!edited) return;

        const btn = document.getElementById('btn-preview-confirm');
        if (btn) { btn.disabled = true; btn.textContent = '创建中...'; }

        try {
            const resp = await fetch('/api/character/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(edited),
            });
            const data = await resp.json();

            if (data.success) {
                const charName = data.data?.name || '';
                _clearDraft();
                if (typeof App !== 'undefined') App._skipNextSave = true;
                // 角色已创建；世界观/任务提示词初始化中（种子已写入，后台 LLM 精修），非阻塞轮询
                showToast('角色创建成功！世界观生成中…', 'success');
                _pollWorldSettingReady(charName);
            } else {
                showToast(data.error || '创建失败', 'error');
                if (btn) { btn.disabled = false; btn.textContent = '确认创建'; }
            }
        } catch (err) {
            showToast('网络错误: ' + err.message, 'error');
            if (btn) { btn.disabled = false; btn.textContent = '确认创建'; }
        }
    }

    // 非阻塞轮询世界观生成状态，就绪后进入游戏
    function _pollWorldSettingReady(charName) {
        if (!charName) { setTimeout(() => window.location.reload(), 800); return; }
        const url = `/api/character/${encodeURIComponent(charName)}/world-setting/status`;
        const poll = setInterval(async () => {
            try {
                const r = await fetch(url);
                const j = await r.json();
                if (j.success && (j.data.status === 'done' || j.data.exists)) {
                    clearInterval(poll);
                    showToast('世界观已就绪，进入游戏', 'success');
                    setTimeout(() => window.location.reload(), 800);
                }
            } catch (e) { /* 忽略轮询错误，依赖兜底 */ }
        }, 2000);
        // 兜底：最多等待 5 分钟，避免永远转圈
        setTimeout(() => {
            clearInterval(poll);
            window.location.reload();
        }, 300000);
    }

    // ========== 全部重新生成 ==========
    async function handleRegenerateAll() {
        const btn = document.getElementById('btn-preview-regenerate-all');
        if (btn) { btn.disabled = true; btn.textContent = '生成中...'; }

        switchStage('loading');
        updateLoadingText('正在重新生成全部数据...');

        try {
            const resp = await fetch('/api/character/regenerate-module', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    module: 'profile',
                    profile: generatedData?.profile || {},
                    form_data: formData,
                }),
            });
            const data = await resp.json();

            if (data.success) {
                generatedData = data.data;
                renderPreview();
                switchStage('preview');
                showToast('全部数据已重新生成', 'success');
            } else {
                showToast(data.error || '重新生成失败', 'error');
                switchStage('preview');
            }
        } catch (err) {
            showToast('网络错误: ' + err.message, 'error');
            switchStage('preview');
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = '全部重新生成'; }
        }
    }

    // ========== 公共方法 ==========
    function open() {
        const modal = document.getElementById('character-create-modal');
        if (modal) modal.style.display = 'flex';
        switchStage('form');
        initDropdowns();
        _resetTraitUI();
        _maybePromptDraft();
    }

    function close() {
        const modal = document.getElementById('character-create-modal');
        if (modal) modal.style.display = 'none';
    }

    function init() {
        window.addEventListener('beforeunload', _onBeforeUnload);
        document.getElementById('btn-character-create')?.addEventListener('click', open);
        document.getElementById('btn-close-create')?.addEventListener('click', close);
        document.getElementById('btn-create-cancel')?.addEventListener('click', close);
        document.getElementById('btn-create-generate')?.addEventListener('click', handleGenerate);
        document.getElementById('btn-preview-back')?.addEventListener('click', () => switchStage('form'));
        document.getElementById('btn-preview-confirm')?.addEventListener('click', handleConfirmCreate);
        document.getElementById('btn-preview-regenerate-all')?.addEventListener('click', handleRegenerateAll);

        const modal = document.getElementById('character-create-modal');
        modal?.addEventListener('click', e => {
            if (e.target === modal) close();
        });
    }

    return { init, open, close };
})();

document.addEventListener('DOMContentLoaded', () => CharacterCreate.init());
