"""Fix condition_matcher.py: replace state_changes and effects blocks"""
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
filepath = os.path.join(script_dir, '..', 'game', 'condition_matcher.py')
filepath = os.path.normpath(filepath)

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Find the 3 blocks by unique markers
marker1_start = "state_changes = template.get('state_changes', [])"
marker2_start = "effects=json.dumps(applied, ensure_ascii=False),"
marker3_start = "memory_text = f\"第{char.game_day}天：{description}\""
marker_after = "# 写入向量记忆"  # after block 3

idx1 = content.find(marker1_start)
idx2 = content.find(marker2_start)
idx3 = content.find(marker3_start)
idx_comment = content.find(marker_after)

print(f"idx1={idx1}, idx2={idx2}, idx3={idx3}, comment={idx_comment}")
if -1 in (idx1, idx2, idx3):
    print("ERROR: markers not found")
    exit(1)

# Block 1: from marker1_start to just before marker2_start's line
# Find the end of the first block (the line before effects=)
line_before_effects = content.rfind('\n', 0, idx2)
line_start_effects = content.rfind('\n', 0, line_before_effects - 1) + 1
block1_end = idx2  # up to effects=
# Actually we need from marker1_start to before the 'event = EventLog(' line
idx_event = content.find('event = EventLog(', idx1)
block1_end = content.rfind('\n', 0, idx_event) + 1  # start of 'event = EventLog' line

old_block1 = content[idx1:block1_end]

new_block1 = """state_changes = template.get('state_changes', [])
        applied = {}
        old_values = {}
        for sc in state_changes:
            attr = sc.get('attribute', '')
            delta = sc.get('delta', 0)
            if not attr or delta == 0:
                continue
            if '.' in attr:
                parts = attr.split('.', 1)
                obj = getattr(char, parts[0], None)
                if isinstance(obj, dict):
                    old = obj.get(parts[1], 0)
                    old_values[attr] = old
                    new_val = max(0, min(100, old + delta))
                    obj[parts[1]] = new_val
                    applied[attr] = delta
            else:
                old = getattr(char, attr, 0)
                if old is None:
                    old = 0
                old_values[attr] = old
                new_val = max(0, min(100, old + delta))
                setattr(char, attr, new_val)
                applied[attr] = delta

        if applied:
            db.session.commit()

        # 构建 state_changes 数组（与 event.py 格式一致）
        state_changes_arr = []
        for key, delta in applied.items():
            old_val = old_values.get(key, 0)
            new_val = getattr(char, key, None)
            if new_val is not None:
                state_changes_arr.append({
                    'attribute': key,
                    'old': round(old_val),
                    'new': round(new_val),
                    'delta': round(delta),
                })

"""

print(f"Block 1: replacing {len(old_block1)} chars with {len(new_block1)} chars")

# Replace block 1
content = content[:idx1] + new_block1 + content[block1_end:]
print("Block 1 done")

# Now find the updated marker positions
idx2_new = content.find(marker2_start)
if idx2_new >= 0:
    # Replace effects line: find the full line
    line_start = content.rfind('\n', 0, idx2_new) + 1
    line_end = content.find('\n', idx2_new)
    old_line2 = content[line_start:line_end]
    new_line2 = "            effects=json.dumps(full_effects, ensure_ascii=False),"
    print(f"Block 2: replacing '{old_line2.strip()}'")
    content = content[:line_start] + new_line2 + content[line_end:]
    print("Block 2 done")

# Block 3: importance grading
idx3_new = content.find(marker3_start)
if idx3_new >= 0:
    # Find the line before: 'memory_text = f"第{char.game_day}天：{description}"'
    line_start = content.rfind('\n', 0, idx3_new) + 1
    # Find 'embedding_blob =' line after
    idx_embed = content.find('embedding_blob', idx3_new)
    line_end = content.find('\n', idx_embed) + 1
    
    old_block3 = content[line_start:line_end]
    new_block3 = """            cat = template.get('category', 'daily')
            imp_map = {'case':70,'narrative':80,'relation_initiated':70,'story_arc':75,'achievement':80,'news':60}
            importance = imp_map.get(cat, 30)
            total_delta = sum(abs(d) for d in applied.values())
            if total_delta > 20:
                importance = min(90, importance + 10)

            memory_text = f"第{char.game_day}天：{description}"
            embedding_blob = _compute_memory_embedding(memory_text)
"""
    print(f"Block 3: replacing {len(old_block3)} chars")
    content = content[:line_start] + new_block3 + content[line_end:]
    print("Block 3 done")

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

# Verify syntax
import py_compile
try:
    py_compile.compile(filepath, doraise=True)
    print("Syntax OK!")
except py_compile.PyCompileError as e:
    print(f"Syntax ERROR: {e}")
