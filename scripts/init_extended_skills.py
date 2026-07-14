"""Create extended Skill markdown stubs (17 files)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "skills"

TEMPLATES: dict[str, str] = {
    "master-duan": """---
name: master-duan
description: >-
  Duan Yongping lens: business quality, moat proxy, management trust.
  Symbol deep-research only; sector uses light industry logic.
disable-model-invocation: true
---

# 段永平视角（生意本质）

## 输入
- `research_reports.recent_reports`（行业、标题、机构观点）
- 结构化财报摘要（待 `fundamentals` 模块）
- `symbols` 技术侧仅作环境约束

## 输出评分卡
- score 1～5
- stance: 好生意 / 一般 / 差生意
- bullets: 2～3 条
- falsify: ≥1 条证伪
- confidence: A/B/C

## 判据
- 毛利率与同行比是否占优
- 研报是否强调可复制商业模式
- 管理层是否可信（研报措辞、历史资本配置）
- 数据不足标灰色，禁止编造
""",
    "master-buffett": """---
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
""",
    "master-munger": """---
name: master-munger
description: >-
  Munger lens: inversion, veto checklist, contrarian check.
  Used in symbol and sector deep-research.
disable-model-invocation: true
---

# 芒格视角（逆向与否决）

## 输入
- 技术结构（Murphy/Nison 结论）
- 板块广度、过热、独狼领涨（板块）
- 研报分歧（个股）

## 输出
- score 1～5；top_risks 2～3 条
- failure_scenarios: 「什么情况下会错」
- veto: 是否触发快速否决（广度<35%、异常领涨等）

## 规则
- 必须先想如何失败，再给乐观结论
- 与巴菲特/段永平结论冲突时写入矛盾点
""",
    "master-li": """---
name: master-li
description: >-
  Li Lu lens: 3-5 year industry certainty and civilizational trend.
  Light on sector; no individual earnings on sector path.
disable-model-invocation: true
---

# 李录视角（长期确定性）

## 输入
- 行业/产业关键词（板块）
- 研报产业表述（个股）
- 政策与需求趋势（仅引用数据字段）

## 输出
- score 1～5；horizon: 3～5 年确定性高/中/低
- trend_note: 产业逻辑 1～2 句
- falsify: 长期逻辑被破坏的条件

## 规则
- 10 年看不清的标「不确定就不买」语气（观望/降权）
""",
    "symbol-deep-research": """---
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
""",
    "sector-deep-research": """---
name: sector-deep-research
description: >-
  Sector deep-research without broker reports or stock earnings.
disable-model-invocation: true
---

# 板块深研主编排

## 步骤
1. sector_ta 预筛选 + participant_flow
2. Nison + Murphy + 芒格 + 李录
3. 广度/过热 Python 校验
4. Team Lead 板块轮动备忘录

## 输出
- 优选 1～3 板块 + 淘汰理由
- 证伪条件
- 禁止追涨幅榜

## 禁止
- 调用个股研报或财报
""",
    "ta-investment-checklist": """---
name: ta-investment-checklist
description: TA six-gate quick screen before deep-research.
disable-model-invocation: true
---

# TA 六关快筛

1. 趋势（Murphy regime）
2. 形态（Nison + price patterns）
3. 量价配合
4. 资金（北向/主力方向）
5. 板块广度（若板块题）
6. 过热否决（大涨低广度）

通过/不通过/灰色；10 分钟内决定是否深入。
""",
    "ta-quality-screen": """---
name: ta-quality-screen
description: Hard veto rules for weak structure boards or symbols.
disable-model-invocation: true
---

# TA 去劣硬筛

红线示例：
- 关键支撑放量跌破
- 板块广度 < 35%
- 领涨股涨跌异常 > 80%
- 板块涨 ≥6% 且广度 < 55%

触发则降权或排除，须写淘汰理由。
""",
    "sector-funnel": """---
name: sector-funnel
description: Sector funnel with explicit reject reasons per layer.
disable-model-invocation: true
---

# 板块漏斗

全量 → 资格过滤 → 行业/概念分桶 → K线深扫 → Top5

每层记录 reject_reason；终选按组合互补性，非单纯分数前 N。
""",
    "research-report-reader": """---
name: research-report-reader
description: Rules for reading East Money broker research metadata consensus.
disable-model-invocation: true
---

# 研报解读规范

## 输入
`research_reports` JSON：评级分布、recent_reports、confidence

## 规则
- 置信度 C：只能说「共识不可评估」
- 评级分歧大 → 灰色地带
- 禁止把单篇研报当确定性
- 须与四大师估值/风险交叉
""",
    "symbol-earnings-review": """---
name: symbol-earnings-review
description: A-share structured earnings tables (not PDF full text).
disable-model-invocation: true
---

# A 股财报精读（结构化）

## 输入
利润表/现金流/财务指标 JSON（akshare）

## 关注
营收与净利 YoY、毛利率、ROE、经营现金流/净利润

## 规则
- 缺失字段标未验证
- 与研报 EPS 预测对比偏差
""",
    "financial-data": """---
name: financial-data
description: Cross-validation and confidence rules for financial numbers.
disable-model-invocation: true
---

# 财务数据交叉验证

- 关键指标至少一个独立来源或 Python 复算
- 误差 >1% 告警，写入 confidence
- LLM 禁止心算 PE、YoY、市值
""",
    "thesis-tracker": """---
name: thesis-tracker
description: Investment thesis and falsification tracking after buy/watch.
disable-model-invocation: true
---

# 投资论文追踪

记录：看好理由、证伪条件、数据置信度；后续对话核对是否失效。
""",
    "thesis-drift": """---
name: thesis-drift
description: Compare two thesis snapshots; separate fact vs wording drift.
disable-model-invocation: true
---

# 论文漂移检测

对比：事实变化、估值变化、措辞变化；禁止把措辞升级当基本面改善。
""",
    "portfolio-review": """---
name: portfolio-review
description: Portfolio concentration, sector exposure, rebalance hints.
disable-model-invocation: true
---

# 组合复盘

输入 holdings 权重；输出集中度风险、相关板块暴露、再平衡建议语气。
""",
    "news-pulse": """---
name: news-pulse
description: 10-minute price move attribution; not full deep-research.
disable-model-invocation: true
---

# 异动快速归因

输出：候选解释、贡献估算、性质（价值/情绪/真因不明）、是否触发深研。
""",
    "dyp-ask": """---
name: dyp-ask
description: Duan Yongping-style first-principles Q&A for business and investing.
disable-model-invocation: true
---

# 段永平式问答

用简单语言问「本质是什么」「10 年后还在吗」；可独立于深研调用。
""",
}


def main() -> None:
    for name, body in TEMPLATES.items():
        path = ROOT / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.strip() + "\n", encoding="utf-8")
        print("wrote", path)


if __name__ == "__main__":
    main()
