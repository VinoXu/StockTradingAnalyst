---
name: financial-data
description: Cross-validation and confidence rules for financial numbers.
disable-model-invocation: true
---

# 财务数据交叉验证

- 关键指标至少一个独立来源或 Python 复算
- 误差 >1% 告警，写入 confidence
- LLM 禁止心算 PE、YoY、市值
