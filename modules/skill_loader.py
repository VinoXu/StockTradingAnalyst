"""Load self-contained Skill markdown for runtime analysis.

Only reads whitelisted ``skills/`` under the project root.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = PROJECT_ROOT / "skills"

# 原有墨菲融合版 Skill（勿改目录内文件）
SKILL_NAMES = (
    "ta-oscillators",
    "ta-moving-average-boll",
    "ta-volume-price",
    "ta-trend-structure",
    "ta-candlestick",
    "ta-price-patterns",
    "a-share-capital-flow",
)

# 《日本蜡烛图技术》提炼版，独立目录，不影响上方原有 Skill
NISON_SKILL_NAMES = (
    "nison-candlestick-patterns",
    "nison-signal-confluence",
    "nison-ta-integration",
)


def runtime_skill_names() -> tuple[str, ...]:
    """All skills injected into LLM runtime context."""
    return SKILL_NAMES + NISON_SKILL_NAMES


def skill_path(name: str) -> Path:
    if name not in runtime_skill_names():
        raise KeyError(f"Unknown skill: {name}. Available: {', '.join(runtime_skill_names())}")
    path = (SKILLS_DIR / name / "SKILL.md").resolve()
    if SKILLS_DIR.resolve() not in path.parents:
        raise RuntimeError(f"Skill path outside skills/: {path}")
    return path


def load_skill(name: str) -> str:
    """Return full Skill text — sole source of judgment rules at runtime."""
    path = skill_path(name)
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def load_all_skills() -> dict[str, str]:
    return {name: load_skill(name) for name in runtime_skill_names()}


def list_skills() -> list[str]:
    return list(runtime_skill_names())
