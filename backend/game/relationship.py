"""关系系统 — 朋友关系动态变化"""
from datetime import datetime
from backend.config import beijing_now
from backend.models import db, Friend, Character
from backend.game.character import get_character


def get_all_friends():
    """获取当前角色的所有朋友（按角色名隔离）"""
    char = get_character()
    if char and char.name:
        return Friend.query.filter_by(character_name=char.name).order_by(Friend.closeness.desc()).all()
    # 向后兼容：没有角色时返回全部
    return Friend.query.order_by(Friend.closeness.desc()).all()


def get_friend(friend_id):
    return Friend.query.get(friend_id)


def update_relationship(friend_id, closeness_delta=0, trust_delta=0, affection_delta=0):
    """更新朋友关系值"""
    friend = Friend.query.get(friend_id)
    if not friend:
        return None
    friend.closeness = max(0, min(100, friend.closeness + closeness_delta))
    friend.trust = max(0, min(100, friend.trust + trust_delta))
    friend.affection = max(0, min(100, friend.affection + affection_delta))
    friend.last_interaction = beijing_now()
    db.session.commit()
    return friend.to_dict()


def tick_relationship_decay():
    """关系自然衰减：长时间不联系的关系慢慢降低"""
    friends = Friend.query.all()
    changes = {}
    for f in friends:
        delta = -0.3
        f.closeness = max(0, f.closeness + delta)
        changes[f.name] = round(delta, 1)
    db.session.commit()
    return changes


def add_new_friend(name, gender='female', personality='', role='同学', bio='', character_name=''):
    friend = Friend(name=name, gender=gender, personality=personality,
                    role=role, bio=bio, closeness=30, trust=30, affection=30,
                    character_name=character_name)
    db.session.add(friend)
    db.session.commit()
    return friend.to_dict()
