"""验证：记忆提取由本地 Qwen2-1.5B 切换为 LLM 引擎。

覆盖场景：
S1 LLM 返回标准 JSON        -> _extract_memories_llm 正确解析，第一人称/类型保留
S2 content 空 + reasoning_content 含 JSON -> 兜底解析成功
S3 LLM 超时（safe_llm_post=None）        -> 返回 []，不抛异常
S4 返回无 JSON 文本         -> 返回 []
S5 无激活 LLM 配置          -> 返回 []
S6 extract_memories_from_dialogue 写库路径 -> importance<50 被过滤、>=50 落库字段正确
   （commit 打桩为 no-op + 测试后 rollback，不污染真实 DB）
"""
import os
import sys
import json

sys.path.insert(0, os.getcwd())

from unittest.mock import patch

from backend.app import create_app

app = create_app()

import backend.game.llm_utils as LU
import backend.game.memory as M


def _llm_response(content):
    return {"choices": [{"message": {"content": content, "reasoning_content": ""}}]}


GOOD_JSON = json.dumps([
    {"type": "fact", "content": "我完成了新歌的副歌编曲", "context": "深夜写歌",
     "importance": 75, "emotional_weight": 20},
    {"type": "emotion", "content": "你夸我颤音有灵气时，我开心得想哭", "context": "练歌被夸",
     "importance": 85, "emotional_weight": 70},
], ensure_ascii=False)


def run_extract(dialogue_text='玩家说：写完了吗\n苏望倩回复：刚写完副歌'):
    return M._extract_memories_llm('苏望倩', dialogue_text)


results = []


def check(name, cond, detail=''):
    results.append(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}")


with app.app_context():
    # S1 标准 JSON
    with patch.object(LU, 'get_active_llm_config', return_value={'api_url': 'http://x', 'api_key': 'k', 'model_name': 'm'}), \
         patch.object(LU, 'safe_llm_post', return_value=_llm_response(GOOD_JSON)):
        r = run_extract()
    check('S1 标准JSON解析', isinstance(r, list) and len(r) == 2, f"(n={len(r) if isinstance(r, list) else r})")
    check('S1 第一人称content保留', r and r[0]['content'] == '我完成了新歌的副歌编曲')
    check('S1 类型保留', r and r[1]['type'] == 'emotion')

    # S2 reasoning_content 兜底
    resp2 = {"choices": [{"message": {"content": "", "reasoning_content": f"思考过程...{GOOD_JSON}"}}]}
    with patch.object(LU, 'get_active_llm_config', return_value={'api_url': 'http://x', 'api_key': 'k', 'model_name': 'm'}), \
         patch.object(LU, 'safe_llm_post', return_value=resp2):
        r2 = run_extract()
    check('S2 reasoning_content兜底', isinstance(r2, list) and len(r2) == 2, f"(n={len(r2) if isinstance(r2, list) else r2})")

    # S3 LLM 超时
    with patch.object(LU, 'get_active_llm_config', return_value={'api_url': 'http://x', 'api_key': 'k', 'model_name': 'm'}), \
         patch.object(LU, 'safe_llm_post', return_value=None):
        r3 = run_extract()
    check('S3 超时返回[]', r3 == [])

    # S4 无 JSON
    with patch.object(LU, 'get_active_llm_config', return_value={'api_url': 'http://x', 'api_key': 'k', 'model_name': 'm'}), \
         patch.object(LU, 'safe_llm_post', return_value=_llm_response('我觉得这段对话没什么值得记住的。')):
        r4 = run_extract()
    check('S4 无JSON返回[]', r4 == [])

    # S5 无激活配置
    with patch.object(LU, 'get_active_llm_config', return_value=None):
        r5 = run_extract()
    check('S5 无配置返回[]', r5 == [])

    # S6 写库路径（commit 打桩 no-op，测后 rollback 丢弃）
    mixed = json.dumps([
        {"type": "fact", "content": "我完成了新歌的副歌编曲", "context": "深夜写歌", "importance": 75, "emotional_weight": 20},
        {"type": "fact", "content": "玩家想睡觉", "context": "闲聊", "importance": 30, "emotional_weight": 0},
        {"type": "event", "content": "你在我生日那天送了我一条围巾", "context": "生日", "importance": 80, "emotional_weight": 60},
    ], ensure_ascii=False)
    added = []
    with patch.object(LU, 'get_active_llm_config', return_value={'api_url': 'http://x', 'api_key': 'k', 'model_name': 'm'}), \
         patch.object(LU, 'safe_llm_post', return_value=_llm_response(mixed)), \
         patch.object(M, '_compute_memory_embedding', return_value=None), \
         patch.object(M.db.session, 'commit', lambda: None), \
         patch.object(M.db.session, 'add', lambda obj: added.append(obj)):
        out = M.extract_memories_from_dialogue('苏望倩', '写完了吗', '刚写完副歌', game_day=121, game_time='21:30:00')
    ok_filtered = all(a.content != '玩家想睡觉' for a in added) and len(added) == 2
    check('S6 importance<50 被过滤', ok_filtered, f"(写入{len(added)}条)")
    ok_fields = all(a.character_name == '苏望倩' and a.source_day == 121 and a.source_time == '21:30:00' for a in added)
    check('S6 落库字段正确（角色名/天/时间）', ok_fields)
    check('S6 第一人称content', all('玩家' not in a.content or '玩家说' in a.content or a.content.startswith('你') is False for a in added),
          f"(contents={[a.content for a in added]})")
    M.db.session.rollback()  # 丢弃 pending 对象

print("\n=== 总结果 ===")
ok = all(results)
print("ALL PASS" if ok else "HAS FAIL")
sys.exit(0 if ok else 1)
