import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backend.app import create_app
from backend.models import Character, CharacterActivityMap
from backend.game import activity as act_mod

REQUIRED = {"duration_minutes", "effects", "energy_cost", "id", "name", "comfyui_pose"}
# 直接列属性（可被 apply_attr_changes 落地）
DIRECT_COLS = {
    "health","energy","hunger","hygiene","brain_health","heart_health","lung_health",
    "liver_health","skin_health","eye_health","mood","stress","happiness","loneliness",
    "confidence","motivation","creativity","joy","anger","disappointment","boredom",
    "fulfillment","player_trust","player_affection","player_respect","player_intimacy",
}

app = create_app()
problems = []
with app.app_context():
    for c in Character.query.filter_by(gender="female").all():
        if c.name == "测试历史角色":
            continue
        rows = CharacterActivityMap.query.filter_by(character_name=c.name).all()
        vids = [r.venue_id for r in rows]
        if len(vids) != len(set(vids)):
            problems.append(f"{c.name}: venue_id 重复 {vids}")
        if len(rows) < 10 or len(rows) > 13:
            problems.append(f"{c.name}: 地点数 {len(rows)} 不在 10-13")
        if "家里" not in vids and "home" not in vids:
            problems.append(f"{c.name}: 缺少『家里』地点（home）")
        valid_skill = set(c.skill_display) if isinstance(c.skill_display, dict) else set()
        for r in rows:
            try:
                acts = json.loads(r.custom_activities) if r.custom_activities else []
            except Exception as e:
                problems.append(f"{c.name}/{r.venue_id}: JSON 解析失败 {e}")
                continue
            # 家里地点允许 4 个活动，其余 2-3
            _cap = 4 if r.venue_id in ("home", "家里") else 3
            _lo = 2
            if len(acts) < _lo or len(acts) > _cap:
                problems.append(f"{c.name}/{r.venue_id}: 活动数 {len(acts)} 不在 {_lo}-{_cap}")
            ids = [a.get("id") for a in acts]
            if len(ids) != len(set(ids)):
                problems.append(f"{c.name}/{r.venue_id}: 活动 id 重复 {ids}")
            for a in acts:
                miss = REQUIRED - set(a.keys())
                if miss:
                    problems.append(f"{c.name}/{r.venue_id}/{a.get('id')}: 缺字段 {miss}")
                eff = a.get("effects", {})
                if "energy" not in eff:
                    problems.append(f"{c.name}/{r.venue_id}/{a.get('id')}: effects 缺 energy")
                for k in eff:
                    if k in DIRECT_COLS or k in valid_skill:
                        continue
                    problems.append(f"{c.name}/{r.venue_id}/{a.get('id')}: 无效 effect 键 {k}（不在列也不在技能）")
        # 用真实函数验证可读
        venues = act_mod.get_character_venues(c)
        if not venues:
            problems.append(f"{c.name}: get_character_venues 返回空")
        # 抽一个地点验证 get_activities_for_location 能返回专属活动
        if rows:
            sample = rows[0]
            got = act_mod.get_activities_for_location(sample.venue_id, character=c)
            if not got:
                problems.append(f"{c.name}: get_activities_for_location({sample.venue_id}) 返回空")

    # 上游污染扫描：任何『游览观光』/sightsee 的遗留活动都说明旧版自定义地点逻辑残留
    for r in CharacterActivityMap.query.all():
        ca = r.custom_activities or ""
        if "游览观光" in ca or "sightsee" in ca:
            problems.append(f"{r.character_name}/{r.venue_id}: 发现遗留『游览观光』(上游污染未清)")

    print("=== 校验完成 ===")
    if problems:
        print(f"发现 {len(problems)} 个问题：")
        for p in problems[:60]:
            print("  -", p)
    else:
        print("全部通过：格式/数量/字段/有效键/可读 均正常")

    # 抽样展示苏晴一个地点，确认格式
    su = Character.query.filter_by(name="苏晴").first()
    r0 = CharacterActivityMap.query.filter_by(character_name="苏晴").first()
    print("\n样例（苏晴 /", r0.venue_id, r0.venue_name, "）：")
    print(json.dumps(json.loads(r0.custom_activities), ensure_ascii=False, indent=2))
    print("get_character_venues 苏晴 地点数：", len(act_mod.get_character_venues(su)))
