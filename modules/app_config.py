"""Persist user settings for web app (.env)."""

from __future__ import annotations

import os

from modules.env_loader import PROJECT_ROOT, load_env

ENV_PATH = PROJECT_ROOT / ".env"

_LLM_KEYS = (
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_BASE_URL",
    "DASHSCOPE_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "OLLAMA_HOST",
    "OLLAMA_MODEL",
)


def reload_env() -> None:
    import modules.env_loader as el

    el._ENV_LOADED = False
    load_env(ENV_PATH)
    if ENV_PATH.is_file():
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key in _LLM_KEYS:
                os.environ[key] = val


def _mask_key(key: str) -> str:
    if not key:
        return "（未配置）"
    if len(key) <= 8:
        return "***"
    return key[:4] + "..." + key[-4:]


def read_api_key_masked() -> str:
    load_env()
    reload_env()
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("DASHSCOPE_API_KEY", "")
    return _mask_key(key)


def read_settings() -> dict[str, str | bool]:
    load_env()
    reload_env()
    provider = os.environ.get("LLM_PROVIDER", "openai").strip().lower() or "openai"
    if provider in ("bailian", "dashscope", "百炼"):
        provider = "bailian"
    elif provider in ("ollama", "local"):
        provider = "ollama"
    else:
        provider = "openai"

    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("DASHSCOPE_API_KEY", "")
    base = (
        os.environ.get("LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("DASHSCOPE_BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    model = (
        os.environ.get("LLM_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or os.environ.get("DASHSCOPE_MODEL")
        or "deepseek-r1"
    )
    return {
        "provider": provider,
        "api_key_masked": _mask_key(key),
        "base_url": base,
        "model": model,
        "has_key": bool(key),
    }


def save_llm_settings(
    *,
    provider: str = "openai",
    api_key: str | None = None,
    base_url: str | None = None,
    model: str = "deepseek-r1",
) -> str:
    existing = (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or ""
    )
    key = (api_key or "").strip()
    if not key or key.startswith("（") or "..." in key:
        key = existing
    if provider != "ollama" and not key:
        return "请输入有效的 API Key"

    prov = (provider or "openai").strip().lower()
    if prov in ("bailian", "dashscope"):
        prov = "bailian"
    elif prov in ("ollama", "local"):
        prov = "ollama"
    else:
        prov = "openai"

    url = (base_url or "").strip() or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    mdl = (model or "deepseek-r1").strip()

    lines: list[str] = []
    if ENV_PATH.is_file():
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if any(raw.strip().startswith(f"{k}=") for k in _LLM_KEYS):
                continue
            lines.append(raw)
    while lines and not lines[-1].strip():
        lines.pop()

    lines.extend(["", f"LLM_PROVIDER={prov}"])
    if prov == "ollama":
        lines.extend([f"OLLAMA_HOST={url}", f"OLLAMA_MODEL={mdl}"])
    else:
        lines.extend(
            [
                f"LLM_API_KEY={key}",
                f"LLM_BASE_URL={url}",
                f"LLM_MODEL={mdl}",
                f"DASHSCOPE_API_KEY={key}",
                f"DASHSCOPE_BASE_URL={url}",
                f"DASHSCOPE_MODEL={mdl}",
            ]
        )

    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    reload_env()
    from modules.chat_service import reset_session

    reset_session()
    return f"已保存（{prov} · {mdl}）"


def save_api_key(api_key: str | None, model: str = "deepseek-r1") -> str:
    return save_llm_settings(provider="openai", api_key=api_key, model=model)
