"""Load self-contained Skill markdown for runtime analysis.

Only reads whitelisted ``skills/`` under the project root.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from modules.skill_registry import (
    ALL_SKILL_NAMES,
    CAPITAL_SKILL_NAMES,
    LEGACY_SKILL_NAMES,
    MURPHY_SKILL_NAMES,
)
from modules.skill_registry import (
    NISON_SKILL_NAMES as _NISON_NAMES,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = PROJECT_ROOT / "skills"

# 向后兼容：原 SKILL_NAMES = 墨菲 + 资金面（不含 Nison 目录）
SKILL_NAMES = MURPHY_SKILL_NAMES + CAPITAL_SKILL_NAMES

# 尼森框架目录名（Agent-1）
NISON_SKILL_NAMES = _NISON_NAMES


def runtime_skill_names() -> tuple[str, ...]:
    """All skills that may be injected into LLM runtime context."""
    return ALL_SKILL_NAMES


def murphy_skill_names() -> tuple[str, ...]:
    return MURPHY_SKILL_NAMES


def nison_skill_names() -> tuple[str, ...]:
    return NISON_SKILL_NAMES


def capital_skill_names() -> tuple[str, ...]:
    return CAPITAL_SKILL_NAMES


def legacy_skill_names() -> tuple[str, ...]:
    return LEGACY_SKILL_NAMES


def skill_path(name: str) -> Path:
    if name not in runtime_skill_names():
        raise KeyError(f"Unknown skill: {name}. Available: {', '.join(runtime_skill_names())}")
    path = (SKILLS_DIR / name / "SKILL.md").resolve()
    if SKILLS_DIR.resolve() not in path.parents:
        raise RuntimeError(f"Skill path outside skills/: {path}")
    return path


def load_skill(name: str) -> str:
    """Return full Skill text — sole source of judgment rules at runtime."""
    return _load_skill_cached(name)


@lru_cache(maxsize=32)
def _load_skill_cached(name: str) -> str:
    path = skill_path(name)
    if not path.is_file():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def load_all_skills() -> dict[str, str]:
    return {name: load_skill(name) for name in runtime_skill_names() if skill_path(name).is_file()}


def list_skills() -> list[str]:
    return [n for n in runtime_skill_names() if (SKILLS_DIR / n / "SKILL.md").is_file()]
