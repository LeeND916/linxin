# -*- coding: utf-8 -*-
"""验证 P-followup 修复：职业服务场景下 process_dialogue 不得注入 refuse 冷淡指令。
- 职业场景（is_professional_service=True）：拒绝聊天逻辑被跳过，用户消息不含 [系统指令：不想说话]
- 非职业场景：保持原行为，用户消息含 refuse_instruction
打桩所有 DB 触碰点，纯逻辑验证，不产生任何持久化副作用。
"""
import sys, os
sys.path.insert(0, os.getcwd())

from backend.app import create_app
import backend.game.dialogue as D
import backend.game.profession_rules as PR

app = create_app()
with app.app_context():
    captured = {}

    # ── 打桩：隔离 DB 与外部调用 ──
    D.call_llm = lambda user_message, history, is_new_session=False, thinking_enabled=False, **kw: (
        captured.update({'content': history[-1]['content'] if history else user_message}),
        {"content": "（测试）好的。", "reasoning": ""}
    )[1]
    D.get_character = lambda: FAKE
    D.check_dialogue_constraints = lambda char: (1.0, 'angry_cold', None)   # 强制高拒绝率
    D.get_relationship_tier = lambda char: 1
    D.get_tier_refuse_adjustment = lambda tier: 0.5
    D.get_tier_config = lambda tier: {'name': '泛泛之交', 'effect_scale': 1.0}
    D.apply_attr_changes = lambda *a, **k: None
    D._run_post_dialogue_extras = lambda **k: None
    D.trigger_summary_if_needed = lambda *a, **k: None
    D.db.session.commit = lambda: None
    import backend.chat_history as CH
    CH.total_message_count = lambda: 0

    class FakeChar:
        name = '晏微安'
        identity_label = '心理咨询师'
        major = '应用心理学'
        location = 'clinic'
        player_identity = '病人'
        player_trust = 10; player_affection = 10; player_intimacy = 10; player_respect = 10
        dialogue_rounds = 0
        profile_json = ''
        game_day = 0; game_hour = 9; game_minute = 14; game_second = 39
        def to_dict(self): return {'name': self.name}

    # ── 场景1：职业服务场景（is_professional_service 返回 True）──
    FAKE = FakeChar()
    PR.is_professional_service = lambda char, um='': True
    captured.clear()
    D.process_dialogue('为什么', [{'role': 'user', 'content': '为什么'}])
    s1 = captured.get('content', '')
    ok1 = ('系统指令：你现在情绪很低' not in s1)
    print(f"S1 职业场景 refuse 跳过: {'PASS' if ok1 else 'FAIL'} | 送入call_llm的用户消息含refuse注入={'系统指令：你现在情绪很低' in s1}")

    # ── 场景2：非职业场景（is_professional_service 返回 False）──
    FAKE = FakeChar()
    PR.is_professional_service = lambda char, um='': False
    captured.clear()
    D.process_dialogue('为什么', [{'role': 'user', 'content': '为什么'}])
    s2 = captured.get('content', '')
    ok2 = ('系统指令：你现在情绪很低' in s2)
    print(f"S2 非职业场景 refuse 保留: {'PASS' if ok2 else 'FAIL'} | 送入call_llm的用户消息含refuse注入={'系统指令：你现在情绪很低' in s2}")

    print(f"\n总结果: {'ALL PASS' if (ok1 and ok2) else 'FAIL'}")
    sys.exit(0 if (ok1 and ok2) else 1)
