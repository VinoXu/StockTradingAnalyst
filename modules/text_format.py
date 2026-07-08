"""Plain-text formatting for chat replies."""

from __future__ import annotations

import re

_META_HEADING_RE = re.compile(
    r"^(?:【[^】]{0,30}】\s*)?"
    r"(?:立即化作|可参考|手语|批语|口诀|落地指导|精确操作|活用口诀|操作指导|指导建议)"
)
_JARGON_PHRASES = (
    "立即化作可操作",
    "手语谈话批语",
    "可参考如下",
    "落地指导",
    "活用口诀",
    "精确操作",
)


def humanize_reply(text: str) -> str:
    """Strip markdown artifacts; keep readable spoken Chinese."""
    if not text:
        return text

    t = text.replace("\r\n", "\n")
    # 多轮清除，含未闭合的 ** **
    for _ in range(4):
        t = re.sub(r"\*\*(.+?)\*\*", r"\1", t, flags=re.DOTALL)
        t = re.sub(r"\*(.+?)\*", r"\1", t, flags=re.DOTALL)
    t = t.replace("**", "").replace("*", "")
    t = re.sub(r"`(.+?)`", r"\1", t)
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"-{3,}", "", t)
    t = re.sub(r"^---+\s*$", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\*\*\*+\s*$", "", t, flags=re.MULTILINE)
    t = re.sub(r"^[|\-+_=]{3,}.*$", "", t, flags=re.MULTILINE)
    t = re.sub(r"^[▁▂▃▄▅▆▇█░▓▒─│┌┐└┘├┤┬┴┼].*$", "", t, flags=re.MULTILINE)

    out_lines: list[str] = []
    for raw in t.split("\n"):
        line = raw.strip()
        if not line:
            if out_lines and out_lines[-1] != "":
                out_lines.append("")
            continue
        if _META_HEADING_RE.search(line):
            continue
        for phrase in _JARGON_PHRASES:
            if phrase in line:
                line = line.split(phrase, 1)[-1].lstrip("：:，, ")
                break
        if line.startswith(("- ", "* ", "+ ")):
            line = line[2:].strip()
        elif re.match(r"^\d+\.\s+", line):
            line = re.sub(r"^\d+\.\s+", "", line)
        line = line.strip("•·")
        if line:
            out_lines.append(line)

    merged: list[str] = []
    buf: list[str] = []
    for line in out_lines:
        if line == "":
            if buf:
                merged.append("".join(_join_clauses(buf)))
                buf = []
            if merged and merged[-1] != "":
                merged.append("")
        else:
            buf.append(line)
    if buf:
        merged.append("".join(_join_clauses(buf)))

    result = "\n".join(merged)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _join_clauses(parts: list[str]) -> list[str]:
    if len(parts) == 1:
        return [_ensure_end(parts[0])]
    joined: list[str] = []
    for i, p in enumerate(parts):
        p = p.strip()
        if not p:
            continue
        if i < len(parts) - 1 and not p.endswith(("。", "！", "？", "；", "…")):
            p += "；"
        joined.append(p)
    text = "".join(joined)
    return [_ensure_end(text)]


def _ensure_end(s: str) -> str:
    s = s.strip()
    if not s:
        return s
    if s[-1] not in "。！？…":
        return s + "。"
    return s
