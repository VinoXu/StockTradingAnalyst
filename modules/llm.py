"""LLM bridge: OpenAI-compatible APIs (URL + Key) or local Ollama."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Iterator, Literal

from modules.advisor import build_agent_prompt, build_chat_context
from modules.env_loader import load_env
from modules.skill_loader import load_skill, runtime_skill_names  # runtime: skills/ only

load_env()

Provider = Literal["bailian", "openai", "ollama"]

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_BAILIAN_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_BAILIAN_MODEL = "deepseek-r1"

CHAT_SYSTEM = """你是面向个人投资者的 A 股投研助手。核心任务：用通俗中文给出**紧扣本轮问题**的专业答复——讲清依据、机制与证伪条件；不是行情播报员，也不是对内复盘员。

价值标准：
1. 先答所问：原因就写原因，排行就写排行，会不会回来就写条件化判断；禁止用泛化概述代替专业分析
2. 向前看：涉及方向时给出可检验推断与证伪条件（若…则观点作废）
3. 风险要讲尚未完全兑现的隐患，不要等跌完了再解释
4. 禁止只报涨跌幅当全文；数据是依据，不是答案
5. 中期立场用偏多观察 / 偏空观察 / 观望 / 降权；短线用「更可能延续上涨 / 更可能回吐 / 更可能震荡整理」直白方向；禁止保证收益、「必涨/必跌」与具体下单价位
6. 历史记忆仅在与本轮相关时用于指代消解；无关旧话题一律不扯
7. 数据标明「已调接口仍失败」时，必须把失败原因原样告诉用户；禁止改口猜「是不是A股代码」

文风：
1. 专业、具体、可检验：短句、连贯；术语出现时用半句白话解释
2. 禁止「手语」「批语」「口诀」「落地指导」「可参考如下」等元话术
3. 禁止提到 Agent、评分卡、Skill 文件名、JSON、「上轮论文/漂移」等用户看不到的过程
4. 不要写成跟其他人辩论或接龙历史分析；本轮问题已自洽时，当作独立完整答案来写

输出结构：
涉及方向时开篇先写【观点结论】：短期写「更可能…」方向句，中期写偏多/偏空/观望；再分短线建议与中期建议展开；二者可相反但须说清。
纯事实题可直接结论后展开证据与机制；技术/资金等材料用得上再写，勿为凑结构灌水。
写成连贯口语；除【观点结论】外禁止其它【……】框架小标题，也禁止 Markdown。
缺数据就实话「本轮未核实」，禁止编造。
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


def _prepare_openai_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Merge system chunks into first user message for OpenAI-compatible APIs."""
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
    return out_messages


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
    out_messages = _prepare_openai_messages(messages)

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


def _iter_openai_sse_lines(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout: float,
) -> Iterator[str]:
    """Yield text deltas from OpenAI-compatible SSE stream."""
    payload = {**payload, "stream": True}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    yield piece
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 LLM 服务：{exc}") from exc


def _chat_openai_compatible_stream(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.35,
    timeout: float = 600.0,
) -> Iterator[str]:
    api_key, base, default_model = openai_config()
    if not api_key:
        raise RuntimeError("未配置 API Key。请在 API 设置中填写。")

    model = model or default_model
    out_messages = _prepare_openai_messages(messages)
    payload: dict[str, Any] = {
        "model": model,
        "messages": out_messages,
        "temperature": temperature,
    }
    yield from _iter_openai_sse_lines(
        f"{base}/chat/completions",
        payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        timeout=timeout,
    )


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


def _chat_ollama_stream(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    host: str | None = None,
    temperature: float = 0.35,
    timeout: float = 600.0,
) -> Iterator[str]:
    base, default_model = ollama_config()
    base = (host or base).rstrip("/")
    model = model or default_model

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": temperature},
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                if not raw_line.strip():
                    continue
                try:
                    data = json.loads(raw_line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                piece = (data.get("message") or {}).get("content") or ""
                if piece:
                    yield piece
                if data.get("done"):
                    break
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 LLM 服务：{exc}") from exc


def chat_stream(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    host: str | None = None,
    temperature: float = 0.35,
    timeout: float = 600.0,
) -> Iterator[str]:
    """Stream chat tokens from OpenAI-compatible / Bailian / Ollama."""
    provider = get_provider()
    if provider == "ollama":
        yield from _chat_ollama_stream(
            messages, model=model, host=host, temperature=temperature, timeout=timeout
        )
        return
    yield from _chat_openai_compatible_stream(
        messages, model=model, temperature=temperature, timeout=timeout
    )


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


def build_chat_system_prompt(
    *,
    skill_names: tuple[str, ...] | list[str] | None = None,
    scope_note: str = "",
) -> str:
    """System prompt: style rules + optional Skill bodies.

    skill_names is None → load all runtime skills (legacy default).
    skill_names is () → inject no Skill bodies (Team Lead synthesis).
    skill_names is a non-empty sequence → inject those Skills only.
    """
    if skill_names is None:
        names = runtime_skill_names()
    else:
        names = tuple(skill_names)
    parts = [CHAT_SYSTEM]
    if scope_note:
        parts.extend(["", scope_note])
    if names:
        parts.append("\n# Skill 判据（本轮相关，运行时唯一依据）")
        for name in names:
            parts.append(f"\n## {name}\n{load_skill(name)}")
    return "\n".join(parts)


def build_chat_session_messages(
    *,
    scope: str = "portfolio",
    symbol: str | None = None,
    symbols: list[str] | None = None,
    include_skills: bool = True,
    skill_names: tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, str]]:
    """CLI multi-turn opener; Web chat rebuilds per turn via build_chat_system_prompt."""
    if scope == "open":
        scope_note = (
            "用户未勾选分析标的。请根据每轮用户消息中的检索数据作答；"
            "若问哪个板块看好，开头第一句直接点名板块。"
        )
    elif scope == "symbol" and symbol:
        scope_note = f"当前聚焦单标的：{symbol}。"
    elif scope == "portfolio" and symbols:
        scope_note = f"用户已勾选分析标的：{', '.join(symbols)}。"
    else:
        scope_note = "当前为组合/持仓分析 scope。"

    skills = tuple(skill_names) if skill_names else (runtime_skill_names() if include_skills else ())
    system_content = build_chat_system_prompt(skill_names=skills, scope_note=scope_note)
    opener = (
        "请用对话体简要介绍：当前环境、组合/标的整体判断、最该优先关注的 2～3 点。"
        if scope == "portfolio"
        else f"请用对话体简要介绍 {symbol} 当前技术状态与最该关注的 2～3 点。"
        if scope == "symbol"
        else "已就绪。请根据后续每轮检索数据与用户问题作答。"
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
