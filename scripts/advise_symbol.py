"""Murphy-style actionable advice for one symbol.

Usage:
    python scripts/advise_symbol.py 600000              # 规则综合 + 大会串
    python scripts/advise_symbol.py 600000 --agent      # LLM 深度报告
    python scripts/advise_symbol.py 600000 --prompt     # 导出 LLM 上下文
    python scripts/advise_symbol.py 600000 --json

Environment:
    .env 中配置 LLM_BASE_URL + LLM_API_KEY + LLM_MODEL（任意 OpenAI 兼容 API）
    或 LLM_PROVIDER=ollama + OLLAMA_HOST / OLLAMA_MODEL
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.advisor import build_agent_prompt, collect_analysis, render_advice_report, synthesize_advice
from modules.llm import generate_murphy_advice, llm_available, llm_setup_hint
from modules.ta_analysis import ensure_demo_data


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: python scripts/advise_symbol.py <symbol> [--demo] [--agent] [--prompt] [--json]"
        )

    symbol = sys.argv[1]
    if "--demo" in sys.argv:
        ensure_demo_data(symbol)

    if "--prompt" in sys.argv:
        print(build_agent_prompt(symbol, include_skills=True))
        return

    if "--json" in sys.argv:
        data = collect_analysis(symbol)
        out = {
            "analysis": {k: v for k, v in data.items() if k != "snapshot"},
            "advice": synthesize_advice(data),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return

    if "--agent" in sys.argv:
        if not llm_available():
            raise SystemExit(
                "LLM 未就绪。请在 .env 或 Web「API 设置」中配置 LLM_BASE_URL + LLM_API_KEY，或启动 Ollama。"
            )
        try:
            print(generate_murphy_advice(symbol, conversational=True))
        except RuntimeError as exc:
            raise SystemExit(f"{exc}\n当前：{llm_setup_hint()}") from exc
        return

    print(render_advice_report(symbol))


if __name__ == "__main__":
    main()
