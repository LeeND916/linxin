import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend.app import create_app
from backend.models import Character, CharacterActivityMap

app = create_app()
with app.app_context():
    chars = Character.query.all()
    print("=== ALL CHARACTERS ===")
    for c in chars:
        cam = CharacterActivityMap.query.filter_by(character_name=c.name).all()
        print(f"\n--- {c.name} | gender={c.gender} | age={c.age} ---")
        print(f"  identity_label={c.identity_label!r}")
        print(f"  major={c.major!r}")
        print(f"  personality_type={c.personality_type!r}")
        print(f"  personality_tone={c.personality_tone!r}")
        print(f"  social_tendency={c.social_tendency!r}")
        print(f"  emotional_stability={c.emotional_stability!r}")
        print(f"  expression_style={c.expression_style!r}")
        print(f"  hometown={c.hometown!r}")
        print(f"  hobbies={c.hobbies!r}")
        print(f"  dream_primary={c.dream_primary!r}")
        sd = c.skill_display if isinstance(c.skill_display, dict) else {}
        print(f"  skill_display keys={list(sd.keys())}")
        gd = c.goal_display if isinstance(c.goal_display, dict) else {}
        print(f"  goal_display keys={list(gd.keys())}")
        print(f"  CAM rows={len(cam)}")
        for m in cam:
            print(f"    CAM: {m.venue_id!r} ({m.venue_name!r}) unlocked={m.unlocked} acts={len(m.custom_activities) if m.custom_activities else 0}")
