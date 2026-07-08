# Profit Protector

> **Local A-share TA assistant: skills + indicators + optional LLM chat. No auto-trading.**

本地 A 股技术分析投研助手：墨菲 **7 个 TA Skill** + 尼森 **3 个 Skill** + **Python 数值计算** + **Web / CLI 问答**（可选百炼或 Ollama LLM）。

> GitHub 仓库名可能为 `StockTradingAnalyst`，与产品名 Profit Protector 指同一项目。

## 目录结构

```text
Profit_Protector/
├── README.md              ← 本文件
├── app.py                 ← Web 入口 (http://127.0.0.1:7860)
├── requirements.txt
├── .env.example
├── data/
│   └── portfolio.db       ← 本地生成，不入库
├── skills/                ← 分析判据（10 个 SKILL.md）
├── modules/               ← 指标、分析、LLM、对话、持仓
├── scripts/               ← CLI 工具
└── web/                   ← 静态前端
```

## 架构分工

| 层 | 职责 |
|----|------|
| **skills/** | 墨菲 + 尼森 + 资金面判据（10 个 `SKILL.md`） |
| **modules/** | MACD/RSI/形态/趋势等结构化计算 + API |
| **web/** | 浏览器界面 |

## 环境要求

- Python **3.12+**
- 可选：百炼 API Key 或本地 Ollama

## 快速开始

```powershell
# 1. 安装依赖并初始化数据库
pip install -r requirements.txt
python scripts/init_db.py

# 2. 同步行情数据
python scripts/sync_market.py
python scripts/sync_symbol.py 600000

# 3. 录入持仓（可选）
python scripts/portfolio_cli.py add 600000 --name 浦发银行 --qty 1000 --cost 10.5

# 4. CLI 问答（需 .env 配置 LLM Key）
python scripts/chat_advisor.py

# 5. 规则建议
python scripts/advise_portfolio.py
python scripts/advise_symbol.py 600000
```

复制 `.env.example` 为 `.env`：

```text
LLM_PROVIDER=bailian
DASHSCOPE_API_KEY=sk-your-key-here
DASHSCOPE_MODEL=deepseek-r1
```

也可改用本地 Ollama：设置 `LLM_PROVIDER=ollama`。

## Web 界面

```powershell
pip install -r requirements.txt
python app.py
# 或双击 scripts/start_app.bat
```

浏览器打开 http://127.0.0.1:7860 — 聊天 / 持仓 / 同步 / API 设置。

## 脚本一览

| 脚本 | 用途 |
|------|------|
| `init_db.py` | 初始化 SQLite 数据库 |
| `sync_market.py` | 同步指数 + 市场广度 |
| `sync_symbol.py` | 同步个股行情、指标、资金流 |
| `portfolio_cli.py` | 持仓 add / list / remove |
| `chat_advisor.py` | CLI 交互问答 |
| `advise_portfolio.py` | 组合批量建议 |
| `advise_symbol.py` | 单标的建议 |
| `diagnose_akshare.py` | 数据源连通诊断 |

## 功能范围

- **Skill 判据**：墨菲 7 + 尼森 3 + 资金面
- **modules**：指标、形态、趋势、大盘广度、组合建议、LLM 问答
- **不做**：自动交易、券商下单

## 输出约定

- 用语为「偏多观察 / 观望 / 降权」，**非买卖指令**
- 无数据时标「未验证」

## 免责声明

本工具仅供个人学习与技术研究，**不构成任何投资建议或证券买卖推荐**。分析结果基于历史行情与规则计算，可能存在延迟、误差或失效，使用者须自行判断并承担全部风险。详见 [SECURITY.md](SECURITY.md)。

## 许可证

[MIT License](LICENSE)
