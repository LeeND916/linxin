import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.app import create_app
from backend.models import Character

app = create_app()
result = []
with app.app_context():
    chars = Character.query.all()
    for c in chars:
        result.append({
            'name': c.name,
            'preset_id': c.preset_id,
            'age': c.age,
            'gender': c.gender,
            'identity': c.identity_label,
            'major': c.major,
            'personality_type': c.personality_type,
            'personality_tone': c.personality_tone,
            'social_tendency': c.social_tendency,
            'emotional_stability': c.emotional_stability,
            'expression': c.expression_style,
            'appearance': c.appearance[:80] if c.appearance else '',
            'hobbies': c.hobbies,
            'hometown': c.hometown,
            'dream_primary': c.dream_primary,
        })

with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'char_info.json'), 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print("Done")
