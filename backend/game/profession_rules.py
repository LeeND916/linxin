"""职业关系规则：数据驱动的职业变量 + 职业服务场景判定。

所有角色（预设/自定义/历史）的职业豁免都走这里，不写死任何角色名。
职业来源：Character.identity_label（自由文本）+ major，实时匹配 ProfessionRule 表（DB）。
"""
import json
import re

from backend.models import ProfessionRule, CharacterActivityMap


def resolve_profession(char):
    """按 identity_label+major 命中 match_keywords，返回归一化 profession_type；命不中返回 None。

    一句话作用：把自由文本职业（如「心理咨询师」）翻译为系统能用的职业 key（如 psychologist）。
    """
    if char is None:
        return None
    text = ((getattr(char, 'identity_label', '') or '') + ' '
            + (getattr(char, 'major', '') or ''))
    text = text.strip()
    if not text:
        return None
    rules = ProfessionRule.query.all()
    for r in rules:
        try:
            kws = json.loads(r.match_keywords or '[]') or []
        except Exception:
            kws = []
        if any(k and k in text for k in kws):
            return r.profession_type
    return None


def _current_venue_name(char):
    """读取角色当前地点的中文名（character_activity_map.venue_name），用于中文场景匹配。"""
    if not char or not getattr(char, 'name', ''):
        return ''
    loc = getattr(char, 'location', '') or ''
    if not loc:
        return ''
    row = CharacterActivityMap.query.filter_by(character_name=char.name, venue_id=loc).first()
    return row.venue_name if row else ''


# 玩家括号里的「服务场景」辅信号词（按职业大类归类）
_SCENE_KW = (r'就诊|问诊|看诊|诊疗|看病|体检|开庭|办案|会见|咨询|'
            r'乘机|值机|登机|看房|买房|办业务|开户|理发|做造型|'
            r'健身|私教|按摩|推拿|采耳|化妆|宠物|打疫苗|报案|办证')


def is_professional_service(char, user_message=''):
    """判断当前是否处于「职业服务场景」（双信号 + 服务身份门控）。

    一句话作用：只有当角色是服务型职业、当前在职业地点（或玩家括号点明）、且玩家是服务接受方时，才返回 True。
    """
    if char is None:
        return False
    ptype = resolve_profession(char)
    rule = ProfessionRule.query.get(ptype) if ptype else None
    if not rule or not rule.service_type:
        return False
    # 信号①：当前地点（venue_id 或 中文 venue_name）命中职业场景
    try:
        scenes = json.loads(rule.scenes or '[]') or []
    except Exception:
        scenes = []
    loc = getattr(char, 'location', '') or ''
    vname = _current_venue_name(char)
    scene_hit = any((s and (s in loc or s in vname)) for s in scenes)
    # 信号②（辅）：玩家括号含职业场景关键词
    kw_hit = bool(user_message) and bool(
        re.search(r'[（(].*?(' + _SCENE_KW + r')', user_message))
    if not (scene_hit or kw_hit):
        return False
    # 门控③：玩家须是服务接受方（否则同事/朋友关系不算服务场景）
    try:
        svc_ids = json.loads(rule.service_identities or '[]') or []
    except Exception:
        svc_ids = []
    if svc_ids and (getattr(char, 'player_identity', '') or '') not in svc_ids:
        return False
    return True
