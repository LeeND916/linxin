"""更新现有 Friend 表 relation_type：根据 role 关键词批量推断"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from backend.app import create_app
from backend.models import db, Friend

# 关键词映射（与 init_db.py 保持一致）
RELATION_KEYWORDS = [
    (['导师','教授','学长','助教','老师','师傅','指导'], 'mentor'),
    (['闺蜜','室友','好友','闺'], 'friend'),
    (['搭档','同事','律所','编程搭档'], 'colleague'),
    (['后辈','学妹','徒弟'], 'protege'),
    (['前男友','前任'], 'ex_boyfriend'),
    (['对手','劲敌','竞争对手','检察官','警察','侦探','刑警','法官'], 'rival'),
    (['家人','母亲','妈妈','爸爸','父亲','哥哥','弟弟','姐姐','妹妹','爷爷','奶奶','外公','外婆','表','姑姑','叔叔','舅舅'], 'family'),
    (['客户','委托人'], 'client'),
    (['敌人','仇人'], 'enemy'),
    (['证人','路人','陌生人','旁观者'], 'acquaintance'),
]

app = create_app()
with app.app_context():
    all_friends = Friend.query.all()
    updated = 0
    skipped = 0
    for f in all_friends:
        role_str = (f.role or '')
        current_type = f.relation_type or 'friend'
        
        # 推断新的类型
        new_type = 'friend'
        for keywords, rtype in RELATION_KEYWORDS:
            if any(k in role_str for k in keywords):
                new_type = rtype
                break
        
        if new_type != current_type:
            # 只在当前值为 friend/neutral（默认/无效）时才更新，保留已有明确值
            if current_type in ('friend', 'neutral'):
                f.relation_type = new_type
                updated += 1
                print(f'  {f.name:10s} role={role_str:16s} {current_type:14s} -> {new_type}')
            else:
                print(f'  ⏭ {f.name:10s} role={role_str:16s} 保留原有值 {current_type}')
        elif current_type not in ('friend',) or current_type != new_type:
            # Already has a non-friend type and it matches
            if current_type != 'friend' and current_type not in [r for _, r in RELATION_KEYWORDS]:
                print(f'  ⚠ {f.name:10s} role={role_str:16s} 当前={current_type}（未识别类型）')
    
    db.session.commit()
    print(f'\n更新: {updated} 条, 跳过: {len(all_friends) - updated} 条')

    # 验证
    from collections import Counter
    types = Counter(f.relation_type for f in Friend.query.all())
    print(f'\n更新后 relation_type 分布:')
    for t, c in types.most_common():
        print(f'  {t}: {c}')
