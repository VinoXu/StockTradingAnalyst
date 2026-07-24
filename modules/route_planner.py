"""语义多路由：可叠加（direct_chat 除外，互斥）。

正式路由白名单与定义见 ROUTE_DEFINITIONS / ALLOWED_ROUTES。
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 路由定义（单一事实源：白名单 + 说明 + 取数 + 主 workflow）
# ---------------------------------------------------------------------------

ROUTE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "market": {
        "title": "大盘环境",
        "when": (
            "用户问大盘/指数/两市整体、成交额、涨跌家数、市场情绪、牛熊环境等；"
            "需要指数与市场广度类数据作答。"
        ),
        "not_when": "仅问个股且明确不要大盘背景；纯闲聊。",
        "may_stack_with": ["sector", "symbol", "capital", "holdings", "discuss"],
        "fetch": {"market": True},
        "primary_workflow": "market_overview",
        "exclusive": False,
    },
    "sector": {
        "title": "板块/主线",
        "when": (
            "用户问行业/概念/题材/主线轮动、板块强弱、哪条赛道、区间板块排行等；"
            "需要板块涨跌与资金结构数据。"
        ),
        "not_when": "只问单一已知名个股技术、且不涉及板块对照。",
        "may_stack_with": ["market", "symbol", "capital", "holdings", "discuss"],
        "fetch": {"sectors": True, "sector_picks": False},
        "primary_workflow": "sector_deep_dive",
        "exclusive": False,
    },
    "symbol": {
        "title": "个股/点名标的",
        "when": (
            "用户点名股票代码或公司名，要走势、技术、估值、研报、异动原因等个股级分析；"
            "或明确要对勾选/点名标的做研究。"
        ),
        "not_when": "整盘账户处境（优先 discuss）；纯大盘/纯板块且未点名个股。",
        "may_stack_with": ["market", "sector", "capital", "holdings"],
        "fetch": {"quotes": True},
        "primary_workflow": "named_symbols",
        "exclusive": False,
    },
    "discuss": {
        "title": "讨论式处境/整盘策略",
        "when": (
            "被套、解套、回本、整盘怎么操作、账户亏损策略等开放式处境讨论；"
            "即使没有个股代码也要给可执行框架，禁止因无代码拒答。"
        ),
        "not_when": "单纯问大盘涨跌或单一股票技术细节且无处境诉求。",
        "may_stack_with": ["market", "holdings", "sector", "capital"],
        "fetch": {"market": True, "holdings": True},
        "primary_workflow": "situation_advice",
        "exclusive": False,
    },
    "capital": {
        "title": "资金面",
        "when": (
            "主力/北向/融资/龙虎榜/板块资金流入流出等资金行为问题；"
            "需要资金流向类证据。"
        ),
        "not_when": "无关资金的纯指数点位播报或纯基本面问答。",
        "may_stack_with": ["market", "sector", "symbol", "holdings", "discuss"],
        "fetch": {"market": True},
        "primary_workflow": "capital_flow",
        "exclusive": False,
    },
    "holdings": {
        "title": "持仓/自选对照",
        "when": (
            "要结合用户持仓或自选做复盘、仓位结构、集中度、组合风险对照；"
            "或明确「我的持仓/组合怎么样」。"
        ),
        "not_when": "未涉及持仓/自选的公开市场问答。",
        "may_stack_with": ["market", "sector", "symbol", "capital", "discuss"],
        "fetch": {"holdings": True, "quotes": True},
        "primary_workflow": "portfolio_review",
        "exclusive": False,
    },
    "direct_chat": {
        "title": "非投研直接对话",
        "when": (
            "与 A 股投研无关：寒暄打招呼、天气、编程、生活闲聊、"
            "问本助手怎么用但不要取行情、其它非投资主题。"
            "判定标准：不需要行情/板块/资金/持仓等投研取数即可作答。"
        ),
        "not_when": (
            "任何需要市场数据或投研结论的问题；"
            "承接上文投研话题的追问（如「怎么看待这件事」若上文是大盘分析）。"
        ),
        "may_stack_with": [],
        "fetch": {},
        "primary_workflow": "direct_chat",
        "exclusive": True,
        "behavior": (
            "互斥：不得与其它投研路由叠加；"
            "不拉行情/板块/资金/持仓；不派投研 Agent/Skill；"
            "轻量 LLM 直接对话。"
        ),
    },
}

ALLOWED_ROUTES: tuple[str, ...] = tuple(ROUTE_DEFINITIONS.keys())

# 兼容旧代码：由定义表派生
ROUTE_FETCH: dict[str, dict[str, bool]] = {
    name: dict(meta.get("fetch") or {}) for name, meta in ROUTE_DEFINITIONS.items()
}

_ROUTE_PRIMARY_WORKFLOW: dict[str, str] = {
    name: str(meta.get("primary_workflow") or "question_deep_dive")
    for name, meta in ROUTE_DEFINITIONS.items()
}

# 规则回退用关键词（仅 SEMANTIC_LLM 失败时）；主路径禁止依赖
_DISCUSS_HINTS = (
    "被套", "解套", "回本", "赚回来", "扳回来", "翻本", "摊薄", "补仓",
    "浮亏", "套牢", "怎么操作", "如何回本", "该怎么操作",
    "整盘", "总仓", "盘子", "账户亏损", "整体亏损", "全部仓位", "所有仓",
    "手里所有", "我的仓", "仓位怎么办",
)

_HOLDINGS_HINTS = (
    "持仓", "自选", "组合复盘", "我的持仓", "仓位结构", "整盘", "盘子",
)


def route_catalog_for_llm() -> dict[str, Any]:
    """给语义规划器的路由说明书（含 direct_chat）。"""
    out: dict[str, Any] = {}
    for name, meta in ROUTE_DEFINITIONS.items():
        out[name] = {
            "title": meta.get("title"),
            "when": meta.get("when"),
            "not_when": meta.get("not_when"),
            "exclusive": bool(meta.get("exclusive")),
            "may_stack_with": list(meta.get("may_stack_with") or []),
            "fetch": dict(meta.get("fetch") or {}),
            "primary_workflow": meta.get("primary_workflow"),
            "behavior": meta.get("behavior"),
        }
    return out


def normalize_routes(routes: list[str]) -> list[str]:
    """
    规范化路由列表：
    - 去重、保序、仅白名单
    - 含 direct_chat → 只保留 direct_chat（互斥）
    - 空列表视为未指定（上层可再默认 direct_chat）
    """
    cleaned: list[str] = []
    for name in routes:
        n = str(name or "").strip().lower()
        if n in ALLOWED_ROUTES and n not in cleaned:
            cleaned.append(n)
    if "direct_chat" in cleaned:
        return ["direct_chat"]
    return cleaned[:6]


def sanitize_routes(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return normalize_routes([str(item or "") for item in raw])


def detect_routes(
    message: str,
    *,
    intents: list[str],
    symbols: list[str],
    matched_sectors: list[str] | None = None,
    selected: list[str] | None = None,
) -> list[str]:
    """规则层多路由检测（仅 LLM 失败回退）；可叠加，无匹配返回 [direct_chat]。"""
    msg = (message or "").strip()
    intents_set = set(intents or [])
    matched = matched_sectors or []
    routes: list[str] = []

    def _add(name: str) -> None:
        if name in ALLOWED_ROUTES and name not in routes and name != "direct_chat":
            routes.append(name)

    if any(h in msg for h in _DISCUSS_HINTS):
        _add("discuss")
    if "ETF" in msg.upper() and any(k in msg for k in ("亏", "套", "回本", "赚回", "操作", "盘子")):
        _add("discuss")
    if "亏损" in msg and any(k in msg for k in ("操作", "回本", "赚回", "怎么办", "解套", "被套", "仓")):
        _add("discuss")

    if "market" in intents_set or any(
        k in msg for k in ("大盘", "市场", "指数", "两市", "上证", "深证", "创业板", "成交额", "涨跌家数")
    ):
        _add("market")

    if "sector" in intents_set or matched or any(
        k in msg for k in ("板块", "行业", "主线", "题材", "概念")
    ):
        _add("sector")

    if symbols:
        _add("symbol")
    elif selected and "discuss" not in routes:
        _add("symbol")

    if "capital_flow" in intents_set or any(
        k in msg for k in ("资金", "北向", "主力", "流入", "流出", "融资", "龙虎榜")
    ):
        _add("capital")

    if any(h in msg for h in _HOLDINGS_HINTS) or "我的持仓" in msg:
        _add("holdings")
    if "discuss" in routes:
        _add("holdings")
        _add("market")

    if not routes:
        return ["direct_chat"]
    return normalize_routes(routes)


def merge_routes(*route_lists: list[str]) -> list[str]:
    out: list[str] = []
    for lst in route_lists:
        for name in sanitize_routes(lst):
            if name not in out:
                out.append(name)
    return normalize_routes(out)


def fetch_flags_for_routes(routes: list[str]) -> dict[str, bool]:
    """多路由取数并集；direct_chat → 全 false。"""
    flags = {
        "market": False,
        "sectors": False,
        "sector_picks": False,
        "quotes": False,
        "holdings": False,
        "research_reports": False,
        "fundamentals": False,
        "news": False,
    }
    routes = normalize_routes(list(routes or []))
    if not routes or routes == ["direct_chat"]:
        return flags
    for r in routes:
        for k, v in (ROUTE_FETCH.get(r) or {}).items():
            if v:
                flags[k] = True
    if "symbol" in routes:
        flags["quotes"] = True
    if "sector" in routes:
        flags["sectors"] = True
    return flags


def is_direct_chat_routes(routes: list[str] | None) -> bool:
    r = normalize_routes(list(routes or []))
    return (not r) or r == ["direct_chat"]


def primary_workflow(routes: list[str], *, has_symbols: bool = False) -> str:
    """兼容旧单字段 workflow：按优先级选主标签，不表示互斥。"""
    routes = normalize_routes(list(routes or []))
    if is_direct_chat_routes(routes):
        return "direct_chat"
    priority = ("discuss", "symbol", "sector", "holdings", "capital", "market")
    for name in priority:
        if name in routes:
            if name == "symbol" and has_symbols:
                return "symbol_research"
            return _ROUTE_PRIMARY_WORKFLOW[name]
    return "question_deep_dive"


def compose_hint(routes: list[str]) -> str:
    """注入给最终执笔：多路由如何综合。"""
    routes = normalize_routes(list(routes or []))
    if is_direct_chat_routes(routes):
        return (
            "【路由·direct_chat】本轮与投研取数无关：禁止拉行情/板块/资金当依据，"
            "禁止套用大盘或个股分析模板；用直接、简短的对话回答用户所问。"
        )
    labels = {name: str(meta.get("title") or name) for name, meta in ROUTE_DEFINITIONS.items()}
    parts = [labels[r] for r in routes if r in labels and r != "direct_chat"]
    if len(parts) <= 1:
        if "discuss" in routes:
            return (
                "【路由·讨论式】本轮按整盘/处境策略回答，可结合已取大盘与持仓；"
                "禁止因无单码拒答，禁止扭成杠杆/单票鉴定。"
            )
        return f"【路由】本轮主路径：{parts[0] if parts else '综合'}。"
    return (
        "【多路由合成】本轮同时激活："
        + " + ".join(parts)
        + "。先按用户真正要的主结论写开篇，再把其余路由当作约束与证据织进同一篇口语答复；"
        "禁止只答其中一路而忽略其他；禁止用「未提供代码」堵讨论式路由。"
    )
