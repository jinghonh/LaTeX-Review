"""静态读取 BibTeX 字面元数据；不执行 BibTeX 宏或外部工具。"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .contract import Diagnostic, SourceLocation
from .sources import ExpandedProject


@dataclass(frozen=True)
class BibliographyEntry:
    key: str
    author: str | None = None
    title: str | None = None
    year: str | None = None
    file: str | None = None


def _location(file: str, text: str, offset: int) -> SourceLocation:
    line = text.count("\n", 0, offset) + 1
    return SourceLocation(file, line, line, offset - text.rfind("\n", 0, offset),
                          offset - text.rfind("\n", 0, offset), 1.0)


def _group(text: str, start: int, opening: str, closing: str, *, protect_quotes: bool = True) -> int | None:
    depth = 1
    braces = 0
    quoted = False
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if protect_quotes and char == '"' and ((opening == "{" and depth == 1) or (opening == "(" and braces == 0)):
            quoted = not quoted
        elif quoted:
            pass
        elif opening == "(" and char == "{":
            braces += 1
        elif opening == "(" and char == "}" and braces:
            braces -= 1
        elif opening == "(" and braces:
            pass
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    cursor = 0
    while cursor < len(body):
        match = re.search(r"([A-Za-z][\w-]*)\s*=\s*", body[cursor:])
        if not match:
            break
        name = match.group(1).lower()
        start = cursor + match.end()
        if start >= len(body):
            break
        if body[start] in "{\"":
            closing = "}" if body[start] == "{" else '"'
            end = _group(body, start, "{", "}", protect_quotes=False) if closing == "}" else None
            if closing == '"':
                found = re.search(r'(?<!\\)"', body[start + 1:])
                end = start + 2 + found.start() if found else None
            if end is None:
                break
            value = body[start + 1:end - 1]
        else:
            found = re.match(r"[^,\s}]+", body[start:])
            if not found:
                break
            end = start + len(found.group())
            value = found.group()
            if not value.isdigit():
                value = ""  # 字符串宏不作猜测性求值。
        fields[name] = " ".join(value.split())
        cursor = end
    return fields


def read_bibliography(project: ExpandedProject) -> tuple[dict[str, BibliographyEntry], tuple[Diagnostic, ...]]:
    entries: dict[str, BibliographyEntry] = {}
    diagnostics: list[Diagnostic] = []
    side = project.source.side

    def warn(code: str, message: str, source: SourceLocation) -> None:
        diagnostics.append(Diagnostic(code, "warning", message,
                                      source_old=source if side == "old" else None,
                                      source_new=source if side == "new" else None))

    visited: set[str] = set()
    for dependency in project.dependencies:
        if dependency.kind != "bibliography" or not dependency.file.endswith(".bib"):
            continue
        file = dependency.file
        if file in visited:
            continue
        visited.add(file)
        try:
            content = (project.source.root / file).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            warn("bibliography_unreadable", f"参考文献文件无法读取：{file}",
                 SourceLocation(file, None, None, confidence=0, uncertainty_reason="文件不可读"))
            continue
        for match in re.finditer(r"@([A-Za-z]+)\s*([({])", content):
            kind = match.group(1).lower()
            end = _group(content, match.end() - 1, match.group(2), "}" if match.group(2) == "{" else ")")
            loc = _location(file, content, match.start())
            if end is None:
                warn("bibliography_unsupported", f"参考文献条目未闭合：{file}", loc)
                continue
            if kind in {"comment", "preamble", "string"}:
                continue
            body = content[match.end():end - 1]
            head = re.match(r"\s*([^,\s{}()]+)\s*,", body)
            if not head:
                warn("bibliography_unsupported", f"参考文献条目格式不支持：{file}", loc)
                continue
            key = head.group(1)
            if key in entries:
                warn("bibliography_duplicate_key", f"重复引用键 {key}；保留首次解析的条目", loc)
                continue
            values = _fields(body[head.end():])
            entry = BibliographyEntry(key, values.get("author") or None, values.get("title") or None,
                                      values.get("year") or None, file)
            entries[key] = entry
            for field, label in (("author", "作者"), ("title", "题目"), ("year", "年份")):
                if not getattr(entry, field):
                    warn("bibliography_missing_field", f"引用键 {key} 缺少{label}；该字段单独降级", loc)
    return entries, tuple(diagnostics)
