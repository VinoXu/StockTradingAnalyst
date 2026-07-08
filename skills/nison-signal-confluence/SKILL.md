---
name: nison-signal-confluence
description: >-
  Nison signal confluence: multiple candlestick signals at same zone raise
  confidence. Operational rules only.
disable-model-invocation: true
---

# 尼森信号汇聚判据

> **运行时只读本文件。** `modules/skill_loader.load_skill("nison-signal-confluence")`

## 何时使用

- 同一价格区域出现 2 个及以上蜡烛图信号
- 在 `nison-candlestick-patterns` 识别形态后，评估是否汇聚加分
- 不替代单形态定义

## 核心判据

| 情形 | 处理 |
|------|------|
| 同一支撑区：锤子/刺透/启明星等多信号 | 底部反转置信度↑ |
| 同一阻力区：上吊/乌云/黄昏星等多信号 | 顶部反转置信度↑ |
| 趋势中：窗口+三法/并列阳线同向 | 持续看多/看空置信度↑ |
| 仅单一孤立形态 | 不因「汇聚」加分 |
| 同区信号方向矛盾 | 观望；说明冲突 |
| 蜡烛信号+西方工具（趋势线/RSI背离等）同向 | 再升一档（见 nison-ta-integration） |

## 分析步骤

1. 列出已识别蜡烛信号
2. 标注各信号位置（支撑/阻力/中部）
3. 统计同向信号是否共享关键位
4. 按上表调整置信度；冲突则观望

## 禁止事项

- 单一形态凭空「汇聚加分」
- 矛盾信号仍给强结论
