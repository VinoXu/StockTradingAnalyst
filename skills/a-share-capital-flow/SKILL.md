---
name: a-share-capital-flow
description: >-
  Interprets A-share capital flow including main force net inflow, northbound
  holdings, margin balance, and dragon-tiger list context. Use when analyzing
  fund flow, smart money behavior, or capital support for price moves.
disable-model-invocation: true
---

# A股资金面分析

> **运行时只读本文件。** `modules/skill_loader.load_skill("a-share-capital-flow")`

## 何时使用

- 主力/大单方向、连续流入、北向、两融、龙虎榜
- 验证技术突破是否有资金配合

## 输入数据

`capital_flow` 表：`main_net_inflow`, `super_large_net`, `large_net`, `northbound_hold_shares`, `northbound_hold_ratio`, `margin_balance`, `short_balance`, `on_lhb`。同步：`python scripts/sync_symbol.py <code>`。

### 数据口径与缺失降级（必遵）

| 字段 | 口径 | 缺失时 |
|------|------|--------|
| `main_net_inflow` 等 | **东方财富算法估算**，非交易所逐笔席位；大单阈值随市值变化 | 不得输出「主力建仓/出货」强结论；改写「资金数据不可用」 |
| `northbound_*` | 沪深港通持股；非通标的无此字段 | 跳过北向段，不推断 |
| `margin_balance` | 交易所公布融资余额 | 缺失则跳过两融段 |
| `on_lhb` | 当日是否上龙虎榜 | 无则写「未上榜」 |

**连续 N 日**：「连续≥3 日主力净流入」指**最近连续 3 个交易日**均为正，中断则重新计数；勿与「近 20 日内曾流入」混淆。

## 核心判据

### 主力净流入

| 迹象 | 含义 |
|------|------|
| 主力净流入>0 + 价涨 + 量比>1.2 | 价量资金同向，偏多 |
| 价涨 + 主力净流出 | 量价资金背离 |
| 价跌 + 主力大幅净流出 | 抛压/撤离 |
| 连续≥3 日主力净流入 | 短期持续关注 |
| 突破日主力大幅流入 | 突破有配合 |
| 反弹至阻力主力由入转出 | 弱反弹/派发 |

### 北向

| 迹象 | 含义 |
|------|------|
| 持股占比连续上升 | 中期配置偏多 |
| 调整中逆势加仓 | 承接强 |
| 反弹中持续减仓 | 偏空 |
| 单日大幅流出入 | 可能调仓，看 5～10 日趋势 |

### 两融

| 迹象 | 含义 |
|------|------|
| 融资温和升 + 价稳 | 杠杆情绪偏多 |
| 价滞涨 + 融资急增 | 过热/杠杆堆积 |
| 急跌 + 融资仍高 | 被动平仓风险 |

### 龙虎榜

| 迹象 | 含义 |
|------|------|
| on_lhb=1 | 当日异动 |
| 机构净买 + 价未严重偏离均线 | 中性偏多 |
| 游资主导 + 主力流出 | 短线题材，持续性存疑 |

### 组合

| 组合 | 解读 |
|------|------|
| MACD 金叉 + 主力流入 + 放量突破 | 三向共振 |
| 技术偏多 + 主力连续流出 | 标注背离，降权 |
| bear + 主力单日流入 | 反弹脉冲，非反转 |
| 接口失败/无数据 | 不输出资金面强结论 |

### 常见误判

- 单日大额流入即建仓（可能对倒）
- 上龙虎榜即利好（可能是出货榜）
- 北向单日流入定底
- 把估算主力当真实席位

### 失效条件

- 涨跌停日净流入失真
- 小盘股大单噪声
- 非沪深港通标的无北向
- East Money 接口不可用

## 分析步骤

1. 读近 10 日 `capital_flow` + 连续净流入天数
2. 对照上表判主力/北向/两融/龙虎榜
3. 与量价、趋势组合查「组合」表
4. **必须标注**数据口径与缺失情况

## 输出模板

```markdown
### 资金面
**主力行为**：
**北向/两融**：（缺失则写明）
**龙虎榜**：
**资金意愿**：
**数据局限**：
```

## 禁止事项

- 捏造净流入
- 单一资金指标定买卖
- 隐瞒平台估算性质

## 程序对接

- `modules/capital_flow.py`，`modules/ta_analysis.analyze_capital_flow`
