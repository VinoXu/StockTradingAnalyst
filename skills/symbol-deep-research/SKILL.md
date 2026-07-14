---
name: symbol-deep-research
description: >-
  Orchestrates symbol deep-research: 6 agents, research reports, Team Lead memo.
disable-model-invocation: true
---

# 个股深研主编排

## 步骤
1. Python 拉取：K线/指标、资金流、东财研报、财报摘要
2. Agent-1 Nison + Agent-2 Murphy 评分卡
3. Agent-3～6 四大师评分卡（读研报+财报 JSON）
4. Python 交叉验证（financial_rigor）
5. Team Lead 综合备忘录

## 输出模板
- 一句话结论（偏多观察/观望/降权）
- 六维评分与矛盾点
- 参与方式 + ≥2 证伪
- 数据置信度 A/B/C

## 禁止
- 无研报时假装有共识
- 保证收益、具体下单价
