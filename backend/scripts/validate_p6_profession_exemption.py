"""
P6 职业关系豁免 —— 验证脚本
覆盖场景：
  S1. 刚认识（profile_json 空）      -> 显示 [关系状态]，不进 [紧急约束-生命安全]
  S2. 关系已建立 + 低关系值          -> 显示 [一般约束] 生气/不信任，severity=warning，不进 critical
  S3. 职业服务场景（晏微安@clinic）  -> 抑制关系警告，注入 professional 语气
  S4. 首次正向互动 hook              -> 应用正值后 profile_json 写入 relationship_established=True
"""
import os
import sys
import json

# 自引导：让 backend 可作为包导入（backend 无 __init__.py，是 namespace package）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.game import constraints as C
from backend.game.profession_rules import is_professional_service, resolve_profession


class FakeChar:
    """最小化角色对象：仅含约束系统读取的字段，缺失属性回退到安全默认值。"""
    _defaults = dict(
        energy=80, hunger=20, health=90, hygiene=80, brain_health=90, eye_health=90,
        stress=10, mood=80, loneliness=30, confidence=70, motivation=70, creativity=70,
        anger=10, happiness=70, joy=70, boredom=20, disappointment=10, fulfillment=60,
        player_affection=10, player_trust=10, player_intimacy=10, player_respect=10,
        name="晏微安", identity_label="心理咨询师", major="", location="clinic",
        player_identity="来访者", profile_json="",
    )

    def __init__(self, **over):
        d = dict(self._defaults)
        d.update(over)
        for k, v in d.items():
            setattr(self, k, v)


def sev_of(warnings, wtype):
    return [w for w in warnings if w.get("type") == wtype]


def run():
    print("=" * 60)
    print("S1. 刚认识（profile_json 空）-> 应显示 [关系状态]，无 critical")
    c1 = FakeChar(profile_json="")
    w1 = C.get_all_active_warnings(c1)
    rel = sev_of(w1, "relationship")
    relstate = sev_of(w1, "relationship_state")
    critical = sev_of(w1, "critical")
    print(f"  relationship 警告数={len(rel)} (期望0) | relationship_state 数={len(relstate)} (期望1) | critical 数={len(critical)} (期望0)")
    print(f"  -> {'PASS' if len(rel) == 0 and len(relstate) == 1 and len(critical) == 0 else 'FAIL'}")
    prompt1 = C.get_llm_constraints_prompt(c1)
    print(f"  提示词含[关系状态]: {'[关系状态]' in prompt1} | 含[紧急约束]: {'[紧急约束' in prompt1}")

    print("=" * 60)
    print("S2. 关系已建立 + 低关系值 -> [一般约束] 生气/不信任, severity=warning, 不进 critical")
    prof = json.dumps({"relationship_established": True, "relationship_baseline": {}}, ensure_ascii=False)
    c2 = FakeChar(profile_json=prof)
    w2 = C.get_all_active_warnings(c2)
    rel = sev_of(w2, "relationship")
    relstate = sev_of(w2, "relationship_state")
    critical = sev_of(w2, "critical")
    print(f"  relationship 警告数={len(rel)} (期望>=1) | 全部 severity={set(w['severity'] for w in rel)} (期望{{'warning'}})")
    print(f"  relationship_state 数={len(relstate)} (期望0) | critical 数={len(critical)} (期望0)")
    ok = len(rel) >= 1 and all(w["severity"] == "warning" for w in rel) and len(relstate) == 0 and len(critical) == 0
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    prompt2 = C.get_llm_constraints_prompt(c2)
    print(f"  提示词含[一般约束]: {'[一般约束]' in prompt2} | 含[关系状态]: {'[关系状态]' in prompt2} | 含[紧急约束]: {'[紧急约束' in prompt2}")

    print("=" * 60)
    print("S3. 职业服务场景（晏微安@clinic, player_identity=来访者）-> 抑制关系警告 + professional 语气")
    # 职业规则判定需查询 ProfessionRule / CharacterActivityMap，需要 app context
    from backend.app import create_app
    app = create_app()
    with app.app_context():
        c3 = FakeChar(profile_json="")  # 刚认识也走职业豁免
        is_ps = is_professional_service(c3)
        rp = resolve_profession(c3)
        print(f"  is_professional_service={is_ps} (期望 True) | resolve_profession={rp}")
        w3 = C.get_all_active_warnings(c3)
        rel = sev_of(w3, "relationship")
        print(f"  relationship 警告数={len(rel)} (期望0，被抑制)")
        _, tone3, _ = C.check_dialogue_constraints(c3, "")
        print(f"  check_dialogue_constraints tone={tone3!r} (期望 'professional')")
        prompt3 = C.get_llm_constraints_prompt(c3)
        # professional 警告在 get_llm_constraints_prompt 内注入，从提示词验证
        prof_in_prompt = ('保持专业、耐心、先共情' in prompt3)
        print(f"  职业提示注入={prof_in_prompt} (期望 True) | 含[关系状态]={'[关系状态]' in prompt3} (期望 False)")
        ok = is_ps and len(rel) == 0 and tone3 == "professional" and prof_in_prompt and ('[关系状态]' not in prompt3)
        print(f"  -> {'PASS' if ok else 'FAIL'}")

    print("=" * 60)
    print("S4. 首次正向互动 hook round-trip（复刻 character.py apply_attr_changes 内逻辑）")
    # 仅验证 hook 块对 profile_json 的 set/dict->str 处理正确（不触碰真实 DB）
    pj = {}
    applied = {"player_trust": 5, "player_affection": 0, "player_respect": 0, "player_intimacy": 0}
    char = FakeChar(profile_json=json.dumps(pj, ensure_ascii=False))
    # 复刻 hook 逻辑
    _REL_KEYS = ("player_trust", "player_affection", "player_respect", "player_intimacy")
    if any(applied.get(k, 0) > 0 for k in _REL_KEYS):
        pj_dict = json.loads(char.profile_json) if isinstance(char.profile_json, str) else (char.profile_json or {})
        if not pj_dict.get("relationship_established"):
            pj_dict["relationship_established"] = True
            pj_dict["relationship_baseline"] = {
                "player_trust": char.player_trust, "player_affection": char.player_affection,
                "player_respect": char.player_respect, "player_intimacy": char.player_intimacy,
            }
            char.profile_json = json.dumps(pj_dict, ensure_ascii=False)  # 关键：写回 str 而非 dict
    parsed = json.loads(char.profile_json)
    print(f"  写回后 profile_json 类型={type(char.profile_json).__name__} (期望 str)")
    print(f"  relationship_established={parsed.get('relationship_established')} (期望 True)")
    print(f"  relationship_baseline={parsed.get('relationship_baseline')}")
    ok = isinstance(char.profile_json, str) and parsed.get("relationship_established") is True and "relationship_baseline" in parsed
    print(f"  -> {'PASS' if ok else 'FAIL'}")

    print("=" * 60)
    print("全部场景验证完成。")


if __name__ == "__main__":
    run()
