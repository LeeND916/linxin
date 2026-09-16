def remove_photo_marker(photo_id):
    """删除所有角色聊天 MD 文件中指向该照片 id 的 [photo: {"id": X}] 标记。

    相册删除照片（api_photo_delete）时调用，消除聊天流里的 ghost round
    （照片记录已删、聊天 MD 仍残留标记，导致刷新后查无此图、只剩空时间行气泡）。

    - 命中标记的整轮消息若去掉标记后变空（仅剩块头/时间行/effects/推理，无任何可见正文），
      则整轮一并移除；
    - 命中标记但仍有正文/推理等内容，则仅删标记行，保留对话文字。
    Returns:
        int: 被修改并写回的文件数
    """
    target = int(photo_id)
    marker_re = re.compile(r'^\[photo:\s*\{"id":\s*' + str(target) + r'(?:,[^{}]*)?\}\]\s*$')
    changed_files = 0
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in sorted(os.listdir(root)):
        folder = os.path.join(root, name)
        if not (os.path.isdir(folder) and name.startswith(_CHAT_DIR_PREFIX) and name.endswith(_CHAT_DIR_SUFFIX)):
            continue
        for fn in sorted(os.listdir(folder)):
            if not fn.endswith('.md'):
                continue
            fp = os.path.join(folder, fn)
            try:
                with open(fp, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception:
                continue
            new_content = _remove_marker_from_content(content, target, marker_re)
            if new_content != content:
                try:
                    with _write_lock:
                        with open(fp, 'w', encoding='utf-8') as f:
                            f.write(new_content)
                    changed_files += 1
                except Exception as e:
                    print(f"[ChatHistory] remove_photo_marker 写回失败 {fp}: {e}")
    return changed_files


def _remove_marker_from_content(content, target, marker_re):
    """从单个 MD 文件内容中删除目标照片标记，并清理因此变空的整轮消息。

    按消息头(## 时间戳 - 角色)切分为消息块；首块通常是文件标题，不含标记，原样保留。
    """
    parts = re.split(r'(?=\n## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - )', content)
    out = []
    changed = False
    for part in parts:
        if not any(marker_re.match(ln) for ln in part.split('\n')):
            out.append(part)
            continue
        # 去掉目标 marker 行
        lines = [ln for ln in part.split('\n') if not marker_re.match(ln)]
        # 判断去掉 marker 后该块是否为空（无任何用户可见内容）：
        # 移去 时间行 / 块头 / 仅 effects 行 / <details> 推理块 / 空白 后若仍无内容，则整块删除
        body = '\n'.join(lines)
        body = re.sub(r'(?m)^\s*> 第\d+天[^\n]*\n?', '', body)
        body = re.sub(r'(?m)^## \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - .*\n?', '', body)
        body = re.sub(r'(?m)^\s*\[effects:[^\]]*\]\s*\n?', '', body)
        body = re.sub(r'<details>.*?</details>\s*', '', body, flags=re.DOTALL)
        if body.strip() == '':
            changed = True
            continue
        new_part = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines))
        out.append(new_part)
        changed = True
    if not changed:
        return content
    result = ''.join(out)
    result = re.sub(r'\n{3,}', '\n\n', result).strip('\n')
    return result + ('\n' if result else '')
