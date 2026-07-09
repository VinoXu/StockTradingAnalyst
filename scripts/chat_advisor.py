"""Interactive Murphy Q&A advisor (portfolio or single symbol).

Usage:
    python scripts/chat_advisor.py                    # 组合问答
    python scripts/chat_advisor.py --symbol 600000    # 单标的问答

Commands in session:
    /refresh   重新加载数据
    /quit      退出
    /help      帮助
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.advisor import build_chat_context
from modules.llm import (
    CHAT_SYSTEM,
    build_chat_session_messages,
    chat,
    llm_available,
    llm_setup_hint,
)
from modules.skill_loader import load_skill, runtime_skill_names
import json


HELP = """命令：
  /refresh  重新加载行情与分析数据
  /quit     退出
  /help     显示帮助

示例问题：
  现在环境适合加仓吗？
  持仓里哪只股票更该先处理？
  600000 离支撑还有多远，跌破怎么办？
  组合里哪只风险最大？
"""


def _rebuild_system(scope: str, symbol: str | None, include_skills: bool) -> str:
    ctx = build_chat_context(scope=scope, symbol=symbol)
    parts = [CHAT_SYSTEM, "", "# 当前结构化数据", "```json", json.dumps(ctx, ensure_ascii=False, indent=2, default=str), "```"]
    if include_skills:
        parts.append("\n# Skill 判据")
        for name in runtime_skill_names():
            parts.append(f"\n## {name}\n{load_skill(name)}")
    return "\n".join(parts)


def main() -> None:
    symbol = None
    if "--symbol" in sys.argv:
        i = sys.argv.index("--symbol")
        if i + 1 < len(sys.argv):
            symbol = sys.argv[i + 1]
    scope = "symbol" if symbol else "portfolio"

    if not llm_available():
        raise SystemExit(
            "LLM 未就绪。请在 .env 或 Web「API 设置」中配置 LLM_BASE_URL + LLM_API_KEY；"
            "本地 Ollama 请运行 ollama serve。"
        )

    no_skills = "--no-skills" in sys.argv
    messages = build_chat_session_messages(scope=scope, symbol=symbol, include_skills=not no_skills)

    title = f"单标的 {symbol}" if symbol else "组合"
    print(f"墨菲投研问答（{title}）— {llm_setup_hint()} — /quit 退出\n")

    try:
        opening = chat(messages)
        print(f"助手：{opening}\n")
        messages.append({"role": "assistant", "content": opening})
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    while True:
        try:
            user = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            break

        if not user:
            continue
        if user in ("/quit", "/exit", "退出"):
            print("再见。")
            break
        if user == "/help":
            print(HELP)
            continue
        if user == "/refresh":
            messages[0]["content"] = _rebuild_system(scope, symbol, include_skills=not no_skills)
            print("（已刷新数据上下文）\n")
            continue

        messages.append({"role": "user", "content": user})
        try:
            reply = chat(messages)
        except RuntimeError as exc:
            print(f"错误：{exc}\n")
            messages.pop()
            continue
        print(f"\n助手：{reply}\n")
        messages.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
