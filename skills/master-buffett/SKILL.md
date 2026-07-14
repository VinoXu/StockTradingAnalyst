---
name: master-buffett
description: >-
  Buffett lens: financial quality, valuation margin of safety.
  Symbol only; uses research EPS forecasts and structured fundamentals.
disable-model-invocation: true
---

# 巴菲特视角（财务与估值）

## 输入
- `research_reports` 盈利预测字段
- 结构化财报（ROE、负债、经营现金流）
- 行情估值 PE/PB（Python 预计算）

## 输出
- score 1～5；stance: 便宜 / 合理 / 偏贵
- 安全边际一句话
- falsify: 估值或盈利假设被证伪的条件

## 规则
- 所有比率引用 JSON 数值，禁止心算
- 研报 EPS 与财报趋势矛盾时标「灰色」
