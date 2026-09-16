# -*- coding: utf-8 -*-
"""
生成 40 条通用硬规则（triggered_events.json）
从活跃 LLM 配置读取 API 设置，调用 AI 批量生成规则。

用法:
    cd <项目根目录>
    python -m backend.scripts.generate_rules
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.app import create_app
from backend.models import db, LLMConfig

PROMPT = """你是一个游戏事件设计师。请生成 40 条通用事件规则，用于一个邻信游戏中。

每条规则用 JSON 对象表示，格式如下：
{
  "trigger_id": "英文唯一标识",
  "category": "daily",  // 固定为 daily
  "conditions": [
    // 条件数组（AND 语义，全部满足才触发）
    // 支持操作符: < > <= >= == between
    // 支持 not_cooldown 做冷却检查
  ],
  "state_changes": [
    // 属性变化数组，每个: {"attribute": "属性名", "delta": 整数值}
    // 单属性 delta 绝对值 ≤ 25，所有 delta 绝对值总和 ≤ 50
  ],
  "narrative": {
    "title": "事件标题（10字以内）",
    "description": "事件详细描述，可用 {name} 替代角色名"
  },
  "auto_message_probability": 0.0~1.0,
  "progress_increment": 0
}

可用属性名（注意：技能类通过 JSON 点路径访问）：
- 物理: health, energy, hunger, hygiene
- 心理: mood, stress, happiness, loneliness, confidence, motivation, creativity, joy, anger, disappointment, boredom, fulfillment
- 技能（JSON点路径）: skills.writing, skills.coding, skills.social, skills.learning, skills.fitness, skills.painting, skills.debate, skills.game_design, skills.teamwork, skills.legal_knowledge, skills.medical_knowledge
- 目标: goals.writer_progress, goals.coder_progress

请生成以下 4 大类共 40 条规则，每一类 10 条：

A. 健康/身体类（10条）
   涉及生病、运动、饮食、睡眠、意外等。条件应基于健康/精力/饥饿等物理属性。

B. 学业/职业类（10条）
   涉及学习、项目、论文、实习等。条件应基于技能水平、压力、动力等。

C. 社交/关系类（10条）
   涉及朋友帮助、社交活动、孤独感等。条件应基于孤独感、幸福感、心情等。

D. 环境/成长类（10条）
   涉及天气、意外状况、自我感悟等。条件可基于随机或属性组合。

要求：
1. 条件和变化要合理，符合真实生活逻辑
2. 条件不要过于苛刻（否则游戏里永远不会触发）
3. 事件之间要有区分度，不要雷同
4. 每条事件的 trigger_id 用英文小写+下划线，如 exam_stress
5. conditions 数组可包含 0~3 个条件
6. 冷却时间为 24~96 小时不等，用 not_cooldown 字段

返回一个 JSON 数组，包含 40 个事件对象。不要其他文字解释，只返回合法的 JSON 数组。"""


def main():
    app = create_app()
    with app.app_context():
        config = LLMConfig.query.filter_by(is_active=True).first()
        if not config:
            print("错误：没有活跃的 LLM 配置")
            return

        llm_config = config.to_secret_dict()
        print(f"使用 LLM: {llm_config.get('model_name', '?')} @ {llm_config.get('api_url', '?')}")

        # 调用 LLM
        from backend.game.llm_utils import safe_llm_post
        result = safe_llm_post(
            api_url=llm_config['api_url'],
            api_key=llm_config['api_key'],
            model=llm_config.get('model_name', 'gpt-4o-mini'),
            messages=[
                {"role": "system", "content": "你是一个游戏事件设计师。只返回 JSON，不要任何解释。"},
                {"role": "user", "content": PROMPT},
            ],
            max_tokens=16000,
            temperature=0.8,
            timeout=(60, 180),
            call_type="generate_rules",
            character_name="system",
        )

        if not result:
            print("错误：LLM 返回为空")
            return

        content = result["choices"][0]["message"]["content"]

        # 解析 JSON
        from backend.game.event import extract_json_from_llm_response
        data, parse_error = extract_json_from_llm_response(content)
        if parse_error:
            print(f"JSON 解析失败: {parse_error}")
            print("原始响应前 500 字符:", content[:500])
            return

        if not isinstance(data, list):
            print(f"错误：期望返回数组，实际返回 {type(data).__name__}")
            return

        print(f"成功生成 {len(data)} 条规则")

        # 打印统计
        categories = {}
        for rule in data:
            c = rule.get('category', 'unknown')
            categories[c] = categories.get(c, 0) + 1
        for c, n in sorted(categories.items()):
            print(f"  [{c}] {n} 条")

        # 保存到文件
        output_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'game', 'triggered_events.json'
        )
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n已保存到: {output_path}")
        print("请检查内容后，根据需要编辑修改。")


if __name__ == '__main__':
    main()
