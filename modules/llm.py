"""LLM bridge: OpenAI-compatible APIs (URL + Key) or local Ollama."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Literal

from modules.advisor import build_agent_prompt, build_chat_context
from modules.env_loader import load_env
from modules.skill_loader import load_skill, runtime_skill_names  # runtime: skills/ only

load_env()

Provider = Literal["bailian", "openai", "ollama"]

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_BAILIAN_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_BAILIAN_MODEL = "deepseek-r1"

CHAT_SYSTEM = """你是墨菲《金融市场技术分析》风格的 A 股投研助手。像跟朋友微信聊股票一样回答：简单、直白、好懂。

文风（最重要）：
1. 用日常口语短句，一段话说清楚一件事；禁止绕口令、生造词、文艺腔、接口话术
2. 禁止出现「手语」「批语」「口诀」「落地指导」「化作可操作」「可参考如下」等元话术或小标题
3. 全文连贯：先讲大盘/环境背景，再讲板块或个股，再讲风险，最后 1～2 句直接说仓位态度；不要东一句西一句
3a. 用户问「哪个板块看好/推荐/有机会」时：第一句话必须直接说出 1～3 个板块名称；理由必须基于股价形态和趋势（见 sector_picks.pick_reason），不是单纯说今天涨得多；禁止只列回避板块、禁止先讲大盘绕弯
4. 能用「涨了不少」「多数股票在跌」就别写「冰火对冲」「裂筋态度」这类拗口比喻

输出格式：
1. 纯中文口语，禁止 Markdown：不要用 **、##、---、- 列表、编号列表、表格、代码块
2. 禁止 ASCII 艺术图；需要展示走势时文字描述即可，界面会自动配图
3. 开头说明数据时间：若 live_quote 存在则先说盘中价与抓取时刻；技术指标仍说明日K截止日
4. 涉及具体标的分析时，必须先给「观点结论」：短期（1～3个交易日）与中期（1～2周）各标明偏多观察、偏空观察或观望；然后再展开正文
5. 正文结束后，最后 1～2 句直接写操作建议（如轻仓观望、逢高减仓、不新开仓），含板块或标的指向；不要另起标题，不要复述「以下是建议」
6. 缺数据就说「这块还没验证到」，禁止编造
7. 可用加仓、减仓、空仓、观望、低吸、止盈等词，但禁止具体价位与「买入/卖出」下单指令

分析依据：系统消息 Skill 判据 + 用户消息中的「对话记忆」「本轮检索数据」；环境优先于个股。
"""


def get_provider() -> Provider:
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit in ("ollama", "local"):
        return "ollama"
    if explicit in ("openai", "compatible", "custom", "openai_compatible"):
        return "openai"
    if explicit in ("bailian", "dashscope", "百炼"):
        return "bailian"
    if os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("DASHSCOPE_API_KEY"):
        return "bailian"
    return "ollama"


def _resolve_llm_credentials() -> tuple[str, str, str]:
    """Return (api_key, base_url, model) for OpenAI-compatible providers."""
    api_key = (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or ""
    )
    base = (
        os.environ.get("LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("DASHSCOPE_BASE_URL")
        or DEFAULT_BAILIAN_BASE
    ).rstrip("/")
    model = (
        os.environ.get("LLM_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or os.environ.get("DASHSCOPE_MODEL")
        or DEFAULT_BAILIAN_MODEL
    )
    return api_key, base, model


def openai_config() -> tuple[str, str, str]:
    return _resolve_llm_credentials()


def llm_config() -> dict[str, str]:
    """Active provider + model label for UI messages."""
    provider = get_provider()
    if provider == "ollama":
        host, model = ollama_config()
        return {"provider": "ollama", "model": model, "base_url": host}
    key, base, model = openai_config()
    return {"provider": provider, "model": model, "base_url": base, "has_key": bool(key)}


def ollama_config() -> tuple[str, str]:
    host = os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST).rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    return host, model


def bailian_config() -> tuple[str, str, str]:
    return openai_config()


def llm_available(timeout: float = 5.0) -> bool:
    provider = get_provider()
    if provider == "ollama":
        return ollama_available(timeout=timeout)
    key, _, _ = openai_config()
    return bool(key)


def ollama_available(host: str | None = None, timeout: float = 3.0) -> bool:
    base = (host or ollama_config()[0]).rstrip("/")
    req = urllib.request.Request(f"{base}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def list_models(host: str | None = None, timeout: float = 5.0) -> list[str]:
    if get_provider() == "bailian":
        _, _, model = bailian_config()
        return [model]
    base = (host or ollama_config()[0]).rstrip("/")
    req = urllib.request.Request(f"{base}/api/tags", method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]


def _http_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 LLM 服务：{exc}") from exc


def _chat_openai_compatible(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.35,
    timeout: float = 600.0,
) -> str:
    api_key, base, default_model = openai_config()
    if not api_key:
        raise RuntimeError("未配置 API Key。请在 API 设置中填写。")

    model = model or default_model
    out_messages: list[dict[str, str]] = []
    system_chunks: list[str] = []
    for msg in messages:
        if msg.get("role") == "system":
            system_chunks.append(msg.get("content", ""))
        else:
            out_messages.append({"role": msg["role"], "content": msg.get("content", "")})

    if system_chunks and out_messages:
        prefix = "\n\n".join(system_chunks)
        if out_messages[0]["role"] == "user":
            out_messages[0] = {
                "role": "user",
                "content": f"{prefix}\n\n{out_messages[0]['content']}",
            }
        else:
            out_messages.insert(0, {"role": "user", "content": prefix})
    elif system_chunks:
        out_messages = [{"role": "user", "content": "\n\n".join(system_chunks)}]

    payload: dict[str, Any] = {
        "model": model,
        "messages": out_messages,
        "temperature": temperature,
    }
    data = _http_json(
        f"{base}/chat/completions",
        payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        timeout=timeout,
    )

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"模型返回无 choices：{data!r}")
    message = choices[0].get("message") or {}
    text = (message.get("content") or "").strip()
    if not text:
        raise RuntimeError(f"模型返回空 content：{data!r}")
    return text


def _chat_bailian(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.35,
    timeout: float = 600.0,
) -> str:
    return _chat_openai_compatible(messages, model=model, temperature=temperature, timeout=timeout)


def _chat_ollama(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    host: str | None = None,
    temperature: float = 0.35,
    timeout: float = 600.0,
) -> str:
    base, default_model = ollama_config()
    base = (host or base).rstrip("/")
    model = model or default_model

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }
    data = _http_json(
        f"{base}/api/chat",
        payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    text = (data.get("message") or {}).get("content", "").strip()
    if not text:
        raise RuntimeError(f"Ollama 返回空内容：{data!r}")
    return text


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    host: str | None = None,
    temperature: float = 0.35,
    timeout: float = 600.0,
) -> str:
    """Multi-turn chat (OpenAI-compatible / Bailian / Ollama)."""
    provider = get_provider()
    if provider == "ollama":
        return _chat_ollama(messages, model=model, host=host, temperature=temperature, timeout=timeout)
    return _chat_openai_compatible(messages, model=model, temperature=temperature, timeout=timeout)


def generate(
    prompt: str,
    *,
    model: str | None = None,
    host: str | None = None,
    system: str | None = None,
    temperature: float = 0.3,
    timeout: float = 600.0,
) -> str:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return chat(messages, model=model, host=host, temperature=temperature, timeout=timeout)


def build_chat_session_messages(
    *,
    scope: str = "portfolio",
    symbol: str | None = None,
    symbols: list[str] | None = None,
    include_skills: bool = True,
) -> list[dict[str, str]]:
    ctx = build_chat_context(scope=scope, symbol=symbol, symbols=symbols)
    from modules.data_timestamps import collect_reference_meta

    ref_meta = collect_reference_meta(
        [symbol] if scope == "symbol" and symbol else (symbols or [])
    )
    ctx["data_reference"] = ref_meta
    parts = [CHAT_SYSTEM, "", "# 当前结构化数据", "```json", json.dumps(ctx, ensure_ascii=False, indent=2, default=str), "```"]

    if include_skills:
        parts.append("\n# Skill 判据（运行时唯一依据）")
        for name in runtime_skill_names():
            parts.append(f"\n## {name}\n{load_skill(name)}")

    system_content = "\n".join(parts)
    opener = (
        "请用对话体简要介绍：当前环境、组合/标的整体判断、最该优先关注的 2～3 点。"
        if scope == "portfolio"
        else f"请用对话体简要介绍 {symbol} 当前技术状态与最该关注的 2～3 点。"
    )
    if scope == "open":
        opener = (
            "用户未勾选分析标的。后续请根据其提问匹配板块（sectors）与市场（market）数据作答。"
            "若问哪个板块看好，开头第一句直接点名板块，不要绕弯。"
        )
    if scope == "portfolio" and symbols:
        opener = (
            f"用户已勾选分析标的：{', '.join(symbols)}。"
            "后续默认以这些标的为主作答；仅在用户明确问大盘/板块时再展开环境。"
        )
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": opener},
    ]


def generate_murphy_advice(
    symbol: str,
    *,
    model: str | None = None,
    host: str | None = None,
    include_skills: bool = True,
    conversational: bool = True,
) -> str:
    if conversational:
        messages = build_chat_session_messages(scope="symbol", symbol=symbol, include_skills=include_skills)
        messages.append(
            {"role": "user", "content": "请给出完整但简洁的技术分析建议（对话体，不要研报格式）。"}
        )
        return chat(messages, model=model, host=host, temperature=0.3)

    prompt = build_agent_prompt(symbol, include_skills=include_skills)
    if prompt.startswith("无本地数据"):
        return prompt
    return generate(prompt, model=model, host=host, system=CHAT_SYSTEM, temperature=0.25)


def generate_portfolio_advice(
    *,
    model: str | None = None,
    host: str | None = None,
    include_skills: bool = True,
    symbols: list[str] | None = None,
) -> str:
    ctx = build_chat_context(scope="portfolio", symbols=symbols)
    if not ctx.get("symbols"):
        return "组合为空。请先：`python scripts/portfolio_cli.py add <code> --qty <n> --cost <price>`"

    parts = [CHAT_SYSTEM, "", "# 组合结构化数据", "```json", json.dumps(ctx, ensure_ascii=False, indent=2, default=str), "```"]
    if include_skills:
        parts.append("\n# Skill 判据")
        for name in runtime_skill_names():
            parts.append(f"\n## {name}\n{load_skill(name)}")

    messages = [
        {"role": "system", "content": "\n".join(parts)},
        {
            "role": "user",
            "content": (
                "我持有以上组合。请用对话体回答：\n"
                "1）一句话说环境和组合整体\n"
                "2）按优先级说哪几只最该关注、为什么（结合仓位）\n"
                "3）组合层面 2～3 个风险\n"
                "4）结尾给 2 个我可以继续问你的问题"
            ),
        },
    ]
    return chat(messages, model=model, host=host, temperature=0.3)


def llm_setup_hint() -> str:
    cfg = llm_config()
    if cfg["provider"] == "ollama":
        return f"Ollama · {cfg['model']}"
    base = cfg.get("base_url") or ""
    host = base.replace("https://", "").replace("http://", "").split("/")[0]
    return f"{cfg['model']} @ {host}" if host else str(cfg["model"])
