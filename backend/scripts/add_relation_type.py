"""Add relation_type to all friends in character_profile.py"""
import re

filepath = 'backend/character_profile.py'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Mapping: role contains keyword → relation_type
KEYWORD_MAP = [
    (['导师', '教授', '学长/助教', '学长', '指导老师', '师傅'], 'mentor'),
    (['闺蜜', '室友', '好友', '朋友', '闺'], 'friend'),
    (['搭档', '同学', '同事', '律所', '编程搭档'], 'colleague'),
    (['前男友', '前任'], 'ex_boyfriend'),
    (['对手', '劲敌'], 'rival'),
    (['家人', '母亲', '父亲', '妹妹', '弟弟', '姐姐', '哥哥', '表', '堂'], 'family'),
    (['后辈', '学妹', '徒弟'], 'protege'),
    (['客户', '委托人'], 'client'),
    (['敌人', '仇人'], 'enemy'),
]

def guess_relation_type(role_str):
    for keywords, rtype in KEYWORD_MAP:
        if any(k in role_str for k in keywords):
            return rtype
    return 'friend'  # default

# Find each friend entry that has "role" but no "relation_type" yet
# Pattern: match a friend dict that has "role" but no "relation_type"
pattern = r'(\s*\{\s*\n(?:\s*"[^"]+":\s*"[^"]*",?\n)*?\s*"role":\s*"([^"]+)"\s*,?\n(?:(?!relation_type).)*?\})'

def add_relation_type(match):
    full = match.group(1)
    role = match.group(2)
    if '"relation_type"' in full:
        return full  # already has it
    rtype = guess_relation_type(role)
    # Insert relation_type after "bio" or at the end before closing brace
    full = full.rstrip()
    if full.endswith('}'):
        # Check if bio is the last field
        full = full[:-1].rstrip()
        if full.endswith(','):
            full = full[:-1].rstrip()
        full += f',\n            "relation_type": "{rtype}"\n        }}'
    return full

updated = re.sub(pattern, add_relation_type, content, flags=re.DOTALL)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(updated)

print("Done! relation_type added.")
# Verify
count = updated.count('"relation_type"')
print(f"Total relation_type fields: {count}")
