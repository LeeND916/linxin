"""统一对话意图分类层（向量原型匹配）。

设计目标（对应需求「把 INSULT_STRONG 那套原型拦截思路泛化成统一对话意图分类层」）：
- 用项目里已常驻的 BGE-small-zh 向量模型，把玩家发言编码为向量，与「意图原型库」做余弦相似度匹配。
- 表达再多花样（"母狗 滚开" / "你算什么东西" / "滚蛋"）只要语义命中同一原型簇，就能稳定识别。
- 分类器**只看玩家说了什么**（user_message），不掺入角色回复，因此能修掉「温柔回复掩盖攻击」：
  玩家嘴上骂人、角色温柔包容，分类结果仍是 insult，属性变化该重罚就重罚。

文本通道专用；送礼/约会/肢体接触等**行为**走 event.py 事件系统，不进本分类器。
"""

import logging
import re
import threading

logger = logging.getLogger('sim_life.intent_classifier')

# BGE 检索查询前缀（仅加在 query 上，符合 BGE 官方用法，提升短句匹配质量）
_BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："

# 低置信阈值：低于此值视为「无明确意图」，交由 1.5B/LLM/Python 兜底
CONFIDENCE_THRESHOLD = 0.40

# insult_mild 二次校验关键词：向量命中 insult_mild 后，必须至少命中一个关键词，
# 否则降级为「无明确意图」交下游处理，防止"你需要洗澡吗"之类的关怀句被误判为侮辱。
# 关键词从 insult_mild 原型句和常见轻度攻击用语中提取。
INSULT_MILD_TRIGGERS = (
    '烦', '滚', '懒得', '傻', '蠢', '笨', '废物', '没意思', '没劲',
    '恶心', '闭嘴', '你也就', '脑子有', '服了你了', '别说了',
)

# threat 二次校验关键词：向量命中 threat 后，必须至少命中一个关键词，
# 否则降级为「无明确意图」交下游处理，防止"以后我再给你带"之类的正常承诺句
# 因与"你等着"的语义相似度被误判为威胁。
# 关键词从 threat 原型句和常见威胁用语中提取。
THREAT_TRIGGERS = (
    '弄死', '打死', '弄残', '让你好看', '让你后悔', '付出代价',
    '等着瞧', '等着', '给老子', '别逼我', '动手', '收拾你',
    '弄你', '收拾', '报复', '记住了', '走着瞧',
    '没好果子', '找死', '不放过', '要你命', '弄死你',
    '弄臭', '让你身败名裂', '威胁', '恐吓',
)

# 这些意图属于「强负向 / 强正向」，命中后即便角色回复温柔，也要落地（用于 emotion 时刻修正）
NEGATIVE_INTENTS = {
    'insult_strong', 'insult_mild', 'threat', 'anger_outburst',
    'argument', 'blame', 'dismiss', 'boundary_set', 'break_up',
}
POSITIVE_INTENTS = {
    'affection_verbal', 'confession', 'praise', 'gratitude',
    'care_concern', 'comfort', 'encouragement', 'apology',
    'teasing', 'flirting', 'missing', 'jealousy', 'possessive',
    'confiding', 'vulnerable_share', 'support_seek', 'physical_compliment',
}

# ============ 意图原型库：action_type -> 原型句列表 ============
# 覆盖 50+ 类，远超原 TOPICS 的 36 类；每类多个原型以覆盖表达花样。
INTENT_PROTOTYPES = {
    # ---- 攻击 / 负向（嘴硬心软也照罚）----
    'insult_strong': [
        "滚开你这废物", "我操你妈", "你算什么东西", "闭嘴吧废物", "去死吧垃圾",
        "你个贱人", "狗日的滚蛋", "傻逼一样的玩意", "蠢货滚远点", "婊子养的",
        "你他妈算老几", "废物一个", "滚你妈的", "贱货", "你就是个垃圾",
    ],
    'insult_mild': [
        "你真烦", "就这? 真没意思", "你不行啊", "算了懒得理你", "随便你吧",
        "真无聊跟你聊", "你脑子有泡?", "服了你了", "你也就这样", "别说了没劲",
    ],
    'threat': [
        "你再这样我弄死你", "信不信我让你好看", "你等着我记住了", "我会让你付出代价",
        "你给老子等着", "别逼我动手",
    ],
    'anger_outburst': [
        "我受够了", "气死我了", "你太过分了", "混蛋!", "我忍你很久了", "烦死了",
    ],
    'argument': [
        "我们吵架了", "你根本不对", "凭什么啊", "我不同意", "你错了别嘴硬", "反驳你",
        "你讲不讲理", "凭什么听你的",
    ],
    'blame': [
        "都是你的错", "怪你害的", "你怎么这样", "你总是这样", "都怪你",
    ],
    'dismiss': [
        "嗯", "哦", "随便", "你说了算", "没空聊", "随便吧我不想说", "懒得回",
    ],
    'boundary_set': [
        "别这样我不喜欢", "请你尊重我", "适可而止", "别越界了", "请保持距离",
        "我不舒服你别", "别碰我",
    ],
    'break_up': [
        "我们结束吧", "分手", "别再联系我了", "我受够你了滚", "到此为止吧",
    ],

    # ---- 爱意 / 亲密（嘴上原谅心里也暖）----
    'affection_verbal': [
        "我爱你", "喜欢你", "想你了宝贝", "亲爱的抱抱", "亲亲你", "mua 爱你哟",
        "你对我真好", "我的心肝", "么么哒",
    ],
    'confession': [
        "我喜欢你很久了", "我们在一起吧", "做我女朋友好吗", "我爱上你了",
        "想和你谈恋爱", "你愿意当我男朋友吗",
    ],
    'praise': [
        "你真厉害", "你好棒啊", "我佩服你", "天才少女", "你太优秀了", "牛逼",
        "你简直完美", "厉害了我的",
    ],
    'gratitude': [
        "谢谢你", "感谢你", "幸亏有你", "多亏你帮忙", "有你真好", "感激不尽",
    ],
    'care_concern': [
        "你累不累", "注意身体别太辛苦", "天冷加衣服", "按时吃饭", "早点睡别熬夜",
        "多喝热水", "别着凉了",
    ],
    'comfort': [
        "别难过有我在", "没事都会过去的", "别哭", "别怕我陪你", "放松有我呢",
        "别紧张一切有我",
    ],
    'encouragement': [
        "加油你可以的", "相信你", "别放弃我挺你", "你一定行", "我信你",
    ],
    'apology': [
        "对不起是我错", "抱歉怪我", "原谅我好不好", "都是我的错", "我道歉",
    ],
    'teasing': [
        "逗你玩的啦", "哈哈哈小傻瓜", "你真笨笨的", "调皮", "略略略逗你",
    ],
    'flirting': [
        "想不想我呀", "你今天真好看", "靠近你一点", "你猜我在想什么", "撩你一下",
        "心跳加速了吗",
    ],
    'missing': [
        "好想你", "想见你一面", "盼着你回来", "等你好久了", "见不到你好寂寞",
    ],
    'jealousy': [
        "你跟谁聊天呢", "是不是喜欢别人了", "你只对我这样吗", "你不在乎我了",
        "你心里还有我吗", "你跟她什么关系",
    ],
    'possessive': [
        "你是我的", "不许看别人", "只能喜欢我", "你归我了", "别对别人笑",
    ],
    'confiding': [
        "跟你说个秘密", "其实我心里", "偷偷告诉你", "想跟你说件事", "只告诉你",
    ],
    'vulnerable_share': [
        "我好怕", "我撑不住了", "我好累没人懂我", "我好孤独", "我快崩溃了",
        "我其实很委屈",
    ],
    'support_seek': [
        "陪陪我", "我需要你", "抱抱我好吗", "在吗陪我说说话", "别走陪我",
    ],
    'physical_compliment': [
        "你好美", "你好漂亮", "身材真好", "声音真好听", "你笑起来真好看",
    ],

    # ---- 中性 / 日常话题 ----
    'greeting': ["你好", "在吗", "早啊", "嗨", "在干嘛呢", "哟来了"],
    'farewell': ["晚安", "拜拜", "回头聊", "我先走了", "睡了", "下次见"],
    'request_help': ["帮帮我", "能帮我个忙吗", "请教你个问题", "救救孩子"],
    'share_daily': ["今天去了", "我刚吃完", "刚看到个", "跟你分享", "今天遇到"],
    'ask_about': ["你呢最近怎么样", "你今天好吗", "你那边如何", "你怎么看"],
    'laughter': ["哈哈哈笑死", "太好笑了", "2333", "笑不活了", "哈哈哈哈"],
    'excited_share': ["太棒了!", "我过了终于", "激动死了", "等不及了", "哇塞"],
    'worried': ["怎么办啊", "我好怕万一", "不安", "害怕出事", "担心死了"],
    'complaining': ["真服了这破", "太差劲了", "受不了了无语", "什么破玩意", "服气"],
    'bored': ["好无聊", "没事做", "好闷", "闲得慌", "无所事事"],
    'self_deprecating': ["我太菜了", "我废物一个", "我不行啊", "我好笨", "我啥也不会"],
    'nostalgic': ["想起以前", "那时候真好", "小时候", "还记得吗", "旧时光"],
    'future_plan': ["以后我们", "将来打算", "我的计划是", "梦想是", "以后想"],
    'topic_weather': ["今天天气", "下雨了", "晴天暖和", "降温了", "下雪啦"],
    'topic_food': ["吃了吗", "好饿啊", "吃什么呢", "这饭真香", "外卖到了"],
    'topic_study': ["作业写完了吗", "考试加油", "论文好难", "复习中", "这题不会"],
    'topic_work': ["项目进度", "又加班", "deadline 要命", "需求又改了", "上班好累"],
    'topic_hobby': ["打游戏", "画画去", "听歌中", "看书呢", "看电影"],
    'topic_family': ["我妈催我", "我爸说了", "家里事儿", "爸妈身体", "回老家"],
    'topic_health': ["感冒了头疼", "胃疼难受", "不舒服", "发烧了", "身体抱恙"],
}


class IntentResult:
    __slots__ = ('action_type', 'confidence', 'scores')

    def __init__(self, action_type, confidence, scores):
        self.action_type = action_type
        self.confidence = confidence
        self.scores = scores

    def __repr__(self):
        return f"IntentResult(type={self.action_type}, conf={self.confidence:.3f})"


# 原型向量缓存：action_type -> numpy 向量（多个原型取平均）
_proto_cache = {}          # action_type -> np.ndarray (normalized)
_proto_loaded = False
_proto_lock = threading.Lock()


def _normalize_text(text: str) -> str:
    if not text:
        return ""
    t = text.lower().strip()
    # 去除多余空白
    t = re.sub(r'\s+', ' ', t)
    return t


def _encode(texts):
    """统一编码入口：仅复用 LM Studio Embedding，失败交给调用方回退。

    注意：调用方负责给「查询句」加 BGE 前缀（classify_intent 已处理），此处按传入文本原样编码。
    """
    from backend.game.memory import embed_text_vector
    import numpy as np
    vecs = [embed_text_vector(t) for t in texts]
    return np.array(vecs, dtype='float32')


def _ensure_prototypes():
    """懒加载并编码所有意图原型（进程内只做一次）。线程安全。"""
    global _proto_loaded
    if _proto_loaded:
        return
    with _proto_lock:
        if _proto_loaded:
            return
        import numpy as np
        # 一次性编码所有原型句
        all_phrases = []
        mapping = []  # (action_type, idx_in_all)
        for at, phrases in INTENT_PROTOTYPES.items():
            for p in phrases:
                all_phrases.append(p)
                mapping.append(at)
        if not all_phrases:
            _proto_loaded = True
            return
        vectors = _encode(all_phrases)  # list/np of shape (N, D)
        # 按 action_type 聚合成平均向量
        import collections
        buckets = collections.defaultdict(list)
        for vec, at in zip(vectors, mapping):
            buckets[at].append(np.asarray(vec, dtype='float32'))
        for at, vecs_list in buckets.items():
            mat = np.stack(vecs_list, axis=0)        # (k, D)
            centroid = mat.mean(axis=0)
            norm = np.linalg.norm(centroid)
            if norm > 0:
                centroid = centroid / norm
            _proto_cache[at] = centroid
        _proto_loaded = True
        logger.info(f"[IntentClassifier] 原型库已加载：{len(_proto_cache)} 类意图")


def classify_intent(text: str, threshold: float = CONFIDENCE_THRESHOLD,
                   character_reply: str = '') -> IntentResult:
    """对玩家发言做意图分类。

    Args:
        text: 玩家发言原文
        threshold: 置信度阈值
        character_reply: 女主回复（可选，用于 insult_mild 二次校验的交叉验证）

    返回 IntentResult：
        action_type=None, confidence<threshold  -> 低置信，应回退下游模型
        否则 action_type=命中意图, confidence=最大余弦相似度
    """
    norm = _normalize_text(text)
    if not norm:
        return IntentResult(None, 0.0, {})

    try:
        _ensure_prototypes()
    except Exception as e:
        logger.warning(f"[IntentClassifier] 原型加载失败，回退下游: {e}")
        return IntentResult(None, 0.0, {})

    if not _proto_cache:
        return IntentResult(None, 0.0, {})

    import numpy as np
    try:
        qvec = _encode([_BGE_QUERY_PREFIX + norm])[0]
        qvec = np.asarray(qvec, dtype='float32')
        n = np.linalg.norm(qvec)
        if n > 0:
            qvec = qvec / n
    except Exception as e:
        logger.warning(f"[IntentClassifier] 编码失败，回退下游: {e}")
        return IntentResult(None, 0.0, {})

    scores = {}
    best_at, best_sim = None, -1.0
    for at, pvec in _proto_cache.items():
        sim = float(np.dot(qvec, pvec))
        scores[at] = round(sim, 4)
        if sim > best_sim:
            best_sim, best_at = sim, at

    conf = best_sim if best_at else 0.0
    if best_at is None or conf < threshold:
        return IntentResult(None, conf, scores)

    # insult_mild 二次校验：向量易把中性关怀句（如"你需要洗澡吗"）误判为轻度攻击。
    # 先查玩家原文，再查女主回复做交叉验证——双方都不含攻击关键词才降级。
    if best_at == 'insult_mild':
        player_hit = any(trigger in text for trigger in INSULT_MILD_TRIGGERS)
        reply_hit = any(trigger in (character_reply or '') for trigger in INSULT_MILD_TRIGGERS)
        if not (player_hit and reply_hit):
            logger.info(
                f"[IntentClassifier] insult_mild 命中但玩家/女主双方均无攻击关键词，降级交下游。"
                f" text={text[:40]!r} conf={conf:.3f}"
            )
            return IntentResult(None, conf, scores)

    # threat 二次校验：向量易把正常承诺句（如"以后我再给你带"）误判为威胁，
    # 因为 threat 原型句含"你等着"等未来时态表述，与承诺句在向量空间相似。
    # 命中后玩家原文必须含真正的威胁关键词，否则降级交下游。
    if best_at == 'threat':
        player_hit = any(trigger in text for trigger in THREAT_TRIGGERS)
        if not player_hit:
            logger.info(
                f"[IntentClassifier] threat 命中但玩家原文无威胁关键词，降级交下游。"
                f" text={text[:40]!r} conf={conf:.3f}"
            )
            return IntentResult(None, conf, scores)

    return IntentResult(best_at, conf, scores)


def is_negative_intent(action_type: str) -> bool:
    return action_type in NEGATIVE_INTENTS


def is_positive_intent(action_type: str) -> bool:
    return action_type in POSITIVE_INTENTS


# 给 emotion 时刻检测用：意图 -> 期望的 moment_type 修正
INTENT_TO_MOMENT = {
    'insult_strong': 'conflict', 'insult_mild': 'conflict', 'threat': 'conflict',
    'anger_outburst': 'conflict', 'argument': 'conflict', 'blame': 'conflict',
    'break_up': 'conflict', 'boundary_set': 'conflict', 'dismiss': 'conflict',
    'affection_verbal': 'tender', 'confession': 'tender', 'comfort': 'tender',
    'care_concern': 'tender', 'teasing': 'tender', 'flirting': 'tender',
    'missing': 'tender', 'gratitude': 'tender', 'physical_compliment': 'tender',
    'vulnerable_share': 'tender', 'support_seek': 'tender', 'confiding': 'tender',
    'praise': 'breakthrough', 'encouragement': 'breakthrough', 'excited_share': 'breakthrough',
}
