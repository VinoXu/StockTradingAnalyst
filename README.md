# Profit Protector

> **Local A-share TA assistant: skills + indicators + optional LLM chat. No auto-trading.**

本地 A 股技术分析投研助手：墨菲 **7 个 TA Skill** + 尼森 **3 个 Skill** + **Python 数值计算** + **Web / CLI 问答**（可选 LLM 增强，支持任意 OpenAI 兼容 API）。

> GitHub 仓库名可能为 `StockTradingAnalyst`，与产品名 Profit Protector 指同一项目。

## 界面预览

本地 Web 对话界面：左侧导航（新建对话 / 历史 / 自选股 / API 设置），中间为 AI 投研问答，底部可勾选标的并一键问大盘、风险板块、机会板块。

![Profit Protector 对话界面](docs/images/chat-ui.png)

## 目录结构

```text
Profit_Protector/
├── README.md              ← 本文件
├── app.py                 ← Web 入口 (http://127.0.0.1:7860)
├── requirements.txt
├── .env.example
├── data/
│   └── portfolio.db       ← 本地生成（对话/持仓/行情），不入库
├── skills/                ← 分析判据（10 个 SKILL.md）
├── modules/               ← 指标、分析、LLM、对话、持仓
├── scripts/               ← CLI 工具
└── web/                   ← 静态前端
```

## 架构分工

| 层 | 职责 |
|----|------|
| **skills/** | 墨菲 + 尼森 + 资金面判据（10 个 `SKILL.md`） |
| **modules/** | 指标/形态/趋势计算、意图规划、按需取数、LLM 对话 |
| **web/** | 浏览器界面（流式聊天、持仓、API 设置） |

### 对话链路（Web）

```text
用户问题
  → query_planner（意图 / workflow / 标的槽位）
  → skill_mapper（按意图注入相关 Skill，非全量）
  → fetch_data_for_plan（大盘 / 板块 / 个股 / 参与者资金，按需 + TTL 缓存）
  → context_guard（payload 体积预检，超限渐进裁剪）
  → LLM（隐藏 COT 推理 + 流式输出 + Markdown 实时清理）
  → 会话摘要写入 SQLite（30 天保留）
```

## 环境要求

- Python **3.12+**
- 可选：任意 **OpenAI 兼容** 大模型 API（URL + Key），或本地 Ollama

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

# 4. CLI 问答（需配置 LLM，见下方）
python scripts/chat_advisor.py

# 5. 规则建议
python scripts/advise_portfolio.py
python scripts/advise_symbol.py 600000
```

复制 `.env.example` 为 `.env`，填入 **Base URL + API Key + 模型名**：

```text
LLM_PROVIDER=openai
LLM_API_KEY=sk-your-key-here
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

可选：限制单次 LLM 输入体积（字符数，默认 90000）：

```text
LLM_MAX_INPUT_CHARS=90000
LLM_WARN_INPUT_CHARS=75000
```

### 支持的 LLM 接入方式

| 方式 | 说明 |
|------|------|
| **OpenAI 兼容 API** | 填写 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，适用于 DeepSeek、百炼、OpenAI、Moonshot、智谱、SiliconFlow 及自建网关 |
| **本地 Ollama** | 设置 `LLM_PROVIDER=ollama`，配置 `OLLAMA_HOST` 与 `OLLAMA_MODEL`，无需 Key |

常用 Base URL 示例：

| 服务商 | LLM_BASE_URL | 示例模型 |
|--------|--------------|----------|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 阿里云百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `deepseek-r1` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 智谱 AI | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |

也可在 Web 界面侧边栏 **「API 设置」** 中可视化配置：选择服务商预设后自动填充 URL 与模型，填入 Key 保存即可（写入本地 `.env`，不入库）。

## Web 界面

```powershell
pip install -r requirements.txt
python app.py
# 或双击 scripts/start_app.bat
```

浏览器打开 http://127.0.0.1:7860

| 功能 | 说明 |
|------|------|
| **流式聊天** | `POST /api/chat/stream`，逐字输出；思考阶段显示动态提示与三点动画 |
| **对话历史** | 多会话持久化于 `data/portfolio.db`，默认保留 30 天 |
| **自选股 / 同步** | 勾选标的分析；支持盘中价与数据同步 |
| **API 设置** | OpenAI 兼容 URL + Key + 模型，支持预设与连接测试 |

### 分析特性

- **意图驱动**：板块机会、风险扫描、个股技术、资金行为等按问题自动选 Skill 与数据
- **向前看投研**：机会挖掘 + 尚未兑现的风险预警，非行情复读
- **参与者资金**：北向、板块主力净流入、大小单结构（东财估算口径）
- **上下文管控**：去掉全量板块 JSON，按需 Skill；超限时自动裁剪 payload
- **隐藏 COT**：模型内部五步推理，界面只展示最终口语结论

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
| `debug_context_size.py` | 调试某会话 LLM payload 体积分解 |

## 功能范围

- **Skill 判据**：墨菲 7 + 尼森 3 + 资金面
- **modules**：指标、形态、趋势、大盘广度、组合建议、LLM 流式问答
- **不做**：自动交易、券商下单

## 数据与隐私

以下文件**仅保存在本机**，不会提交到 Git 仓库：

| 路径 | 内容 |
|------|------|
| `.env` | API Key、LLM 配置 |
| `data/portfolio.db` | 对话历史、持仓、行情、同步日志 |

自选股勾选状态保存在浏览器 `localStorage`，换浏览器需重新勾选。

## 输出约定

- 用语为「偏多观察 / 观望 / 降权」，**非买卖指令**
- 无数据时标「未验证」
- 禁止「必涨」「保证收益」等承诺性表述

## 免责声明

本工具仅供个人学习与技术研究，**不构成任何投资建议或证券买卖推荐**。分析结果基于历史行情与规则计算，可能存在延迟、误差或失效，使用者须自行判断并承担全部风险。详见 [SECURITY.md](SECURITY.md)。

## 许可证

[MIT License](LICENSE)
