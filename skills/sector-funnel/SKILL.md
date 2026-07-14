---
name: sector-funnel
description: Sector funnel with explicit reject reasons per layer.
disable-model-invocation: true
---

# 板块漏斗

全量 → 资格过滤 → 行业/概念分桶 → K线深扫 → Top5

每层记录 reject_reason；终选按组合互补性，非单纯分数前 N。
