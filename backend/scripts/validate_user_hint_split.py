"""验证：constraints 拆字段（system_message / user_hint）+ 乙版 baseline 跌幅判定。

覆盖场景：
S1 职业场景（晏微安@clinic）          -> user_hint=None（职业豁免，玩家不弹）
S2 陌生人（关系未建立）               -> user_hint=None
S3 老熟人 + 关系较基线跌>=15          -> user_hint=叙事化人话（且不含 system 指令原文）
S4 老熟人 + 关系跌<15                 -> user_hint=None（一直冷但没"跌下来"不弹）
S5 字段完整性                        -> constraints 无 'warning' 键；system_message 存在
"""
import os
import sys
import json

sys.path.insert(0, os.getcwd())

from unittest.mock import patch

from backend.app import create_app

app = create_app()

# 让 profession_rules.is_professional_service 走 mock（不依赖 DB 真实行）
import backend.game.profession_rules as PR


def make_char(name, location, identity_label, player_identity, profile_json,
              trust=50, affection=50, respect=50, intimacy=50):
    class C:
        pass
    c = C()
    c.name = name
    c.location = location
    c.identity_label = identity_label
    c.major = ''
    c.player_identity = player_identity
    c.profile_json = profile_json
    c.player_trust = trust
    c.player_affection = affection
    c.player_respect = respect
    c.player_intimacy = intimacy
    c.dialogue_rounds = 0
    c.to_dict = lambda: {}
    return c


# process_dialogue 内部依赖（用 mock 隔离外部 LLM / DB）
FAKE_REPLY = '（第0天 09:00:00）晏微安：你好，我在听。'


def run(char, professional, refuse_chance, tone):
    """驱动真实 process_dialogue，但把外部依赖全部 mock 掉。"""
    import backend.game.dialogue as D

    with patch.object(PR, 'is_professional_service', return_value=professional), \
         patch.object(D, 'call_llm', return_value={'content': FAKE_REPLY, 'reasoning': ''}), \
         patch.object(D, 'get_character', return_value=char), \
         patch.object(D, 'check_dialogue_constraints', return_value=(refuse_chance, tone, '（mock 警告文本）')), \
         patch.object(D, 'get_llm_constraints_prompt', return_value='[SYSTEM约束模拟] 你正以心理咨询师身份…保持专业、耐心、先共情'):
        res = D.process_dialogue('我最近压力很大', history=[])
    return res


results = []


def check(name, cond, detail=''):
    results.append((name, cond, detail))
    print(f"  [{ 'PASS' if cond else 'FAIL' }] {name} {detail}")


with app.app_context():
    # S1 职业场景
    c1 = make_char('晏微安', 'clinic', '心理咨询师', '来访者',
                  profile_json='')  # established=False 也无所谓，职业场景恒 None
    r1 = run(c1, professional=True, refuse_chance=0.5, tone='distant')
    check('S1 职业场景 user_hint=None', r1['constraints'].get('user_hint') is None,
          f"(user_hint={r1['constraints'].get('user_hint')!r})")

    # S2 陌生人
    c2 = make_char('晏微安', 'dorm', '心理咨询师', '来访者',
                  profile_json='')  # established=False
    r2 = run(c2, professional=False, refuse_chance=0.5, tone='distant')
    check('S2 陌生人 user_hint=None', r2['constraints'].get('user_hint') is None,
          f"(user_hint={r2['constraints'].get('user_hint')!r})")

    # S3 老熟人 + 跌幅>=15（基线 trust=80，当前=50，drop=30）
    baseline = {'player_trust': 80, 'player_affection': 70, 'player_respect': 75, 'player_intimacy': 60}
    c3 = make_char('晏微安', 'dorm', '心理咨询师', '来访者',
                  profile_json=json.dumps({'relationship_established': True,
                                           'relationship_baseline': baseline}),
                  trust=50, affection=55, respect=60, intimacy=45)
    r3 = run(c3, professional=False, refuse_chance=0.5, tone='distant')
    uh3 = r3['constraints'].get('user_hint')
    no_leak = ('系统指令' not in uh3) and ('心理咨询师身份' not in uh3) if uh3 else False
    check('S3 老熟人跌>=15 user_hint 非空', bool(uh3), f"(user_hint={uh3!r})")
    check('S3 不泄漏 system 指令原文', no_leak)

    # S4 老熟人 + 跌幅<15（基线 trust=80，当前=75，drop=5）
    c4 = make_char('晏微安', 'dorm', '心理咨询师', '来访者',
                  profile_json=json.dumps({'relationship_established': True,
                                           'relationship_baseline': baseline}),
                  trust=75, affection=68, respect=73, intimacy=58)
    r4 = run(c4, professional=False, refuse_chance=0.5, tone='distant')
    check('S4 老熟人跌<15 user_hint=None', r4['constraints'].get('user_hint') is None,
          f"(user_hint={r4['constraints'].get('user_hint')!r})")

    # S5 字段完整性
    no_warning_key = 'warning' not in r1['constraints']
    has_system_msg = bool(r1['constraints'].get('system_message'))
    check('S5 constraints 无 warning 键（删除回显）', no_warning_key)
    check('S5 system_message 存在（给 LLM）', has_system_msg)

print("\n=== 总结果 ===")
ok = all(c for _, c, _ in results)
print("ALL PASS" if ok else "HAS FAIL")
sys.exit(0 if ok else 1)
