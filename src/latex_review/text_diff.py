"""保留 LaTeX 语义边界的正文词元与注释扫描。"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata


_COMMAND = re.compile(r"\\(?:[A-Za-z@]+\*?|.)")
_VERBATIM = re.compile(r"\\begin\{(verbatim\*?|Verbatim|lstlisting|minted)\}")
CITATION_COMMANDS = ("cite", "citep", "citet", "nocite", "parencite", "textcite")
_CITATION = re.compile(r"\\(?:" + "|".join(CITATION_COMMANDS) + r")\b")
_REFERENCE = re.compile(r"\\(?:ref|eqref|autoref|pageref|cref|Cref)\b")
_TEXT_ARGUMENT_COMMANDS = {"textbf", "textit", "emph", "texttt", "textsc", "underline", "footnote"}


@dataclass(frozen=True)
class TextToken:
    text: str
    kind: str  # word、command、citation、reference、math、verbatim、symbol
    words: int = 0


@dataclass(frozen=True)
class TokenEdit:
    kind: str  # added、removed、modified
    old: tuple[TextToken, ...]
    new: tuple[TextToken, ...]


@dataclass(frozen=True)
class CommentSpan:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Sentence:
    start: int
    end: int
    text: str


_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "fig", "figs", "eq", "eqs", "sec", "secs",
                  "ref", "refs", "no", "nos", "vol", "vs", "etc", "al", "e.g", "i.e", "cf", "st"}


def split_sentences(raw: str) -> tuple[Sentence, ...]:
    """按原文偏移切句；公式、引用和完整宏调用作为不可分割片段。"""
    result: list[Sentence] = []
    start = index = 0
    while index < len(raw):
        verbatim = _VERBATIM.match(raw, index)
        if verbatim:
            closing = f"\\end{{{verbatim.group(1)}}}"
            end = raw.find(closing, verbatim.end())
            if end >= 0:
                index = end + len(closing)
                continue
        backslashes = 0
        if raw[index] == "%":
            previous = index - 1
            while previous >= 0 and raw[previous] == "\\":
                backslashes += 1
                previous -= 1
        if raw[index] == "%" and backslashes % 2 == 0:
            end = raw.find("\n", index)
            leading_comment = not raw[start:index].strip()
            index = len(raw) if end < 0 else end + 1
            if leading_comment:
                start = index
            continue
        if raw.startswith((r"\(", r"\["), index):
            opening = raw[index:index + 2]
            end = _math_end(raw, index, opening, r"\)" if opening == r"\(" else r"\]")
            if end:
                index = end
                continue
        if raw[index] == "$":
            opening = "$$" if raw.startswith("$$", index) else "$"
            end = _math_end(raw, index, opening, opening)
            if end:
                index = end
                continue
        command = _COMMAND.match(raw, index)
        if command:
            end = command.end()
            if raw.startswith(r"\verb", index) and end < len(raw):
                delimiter = end + (raw[end] == "*")
                finish = raw.find(raw[delimiter], delimiter + 1) if delimiter < len(raw) else -1
                if finish >= 0:
                    index = finish + 1
                    continue
            while True:
                opening = end
                while opening < len(raw) and raw[opening].isspace():
                    opening += 1
                if opening >= len(raw) or raw[opening] not in "[{":
                    break
                close = "]" if raw[opening] == "[" else "}"
                next_end = _group_end(raw, opening, raw[opening], close)
                if next_end is None:
                    break
                end = next_end
            index = end
            continue
        if raw[index] in ".!?。！？":
            char = raw[index]
            before = raw[start:index]
            next_char = raw[index + 1:index + 2]
            following = raw[index + 1:].lstrip()[:1]
            abbreviation = re.search(r"([A-Za-z]+(?:\.[A-Za-z]+)?)$", before)
            short = abbreviation.group(1).lower() if abbreviation else ""
            protected = (char == "." and (
                (before[-1:].isdigit() and next_char.isdigit()) or
                (short in _ABBREVIATIONS and not (short in {"al", "etc"} and following.isupper())) or
                (len(short) == 1 and (next_char.isalpha() or following.isupper())) or
                bool(re.search(r"(?:[A-Za-z]\.)+[A-Za-z]$", before) and not following.isupper())))
            if not protected:
                end = index + 1
                while end < len(raw) and raw[end] in '”’"\')]}':
                    end += 1
                if end == len(raw) or raw[end].isspace() or char in "。！？":
                    left = start
                    while left < end and raw[left].isspace():
                        left += 1
                    if left < end and _without_comments(raw[left:end])[0].strip():
                        result.append(Sentence(left, end, raw[left:end]))
                    start = end
                    index = end
                    continue
        index += 1
    left = start
    while left < len(raw) and raw[left].isspace():
        left += 1
    end = len(raw)
    while end > left and raw[end - 1].isspace():
        end -= 1
    if left < end and _without_comments(raw[left:end])[0].strip():
        result.append(Sentence(left, end, raw[left:end]))
    return tuple(result)


def _group_end(text: str, start: int, opening: str, closing: str) -> int | None:
    depth = 1
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if not depth:
                return index + 1
        index += 1
    return None


def _math_end(text: str, start: int, opening: str, closing: str) -> int | None:
    index = start + len(opening)
    while index < len(text):
        if text.startswith(closing, index):
            return index + len(closing)
        if text[index] == "\\":
            index += 2
        else:
            index += 1
    return None


def _latin_or_digit(char: str) -> bool:
    return char.isdecimal() or (unicodedata.category(char).startswith("L") and
                                unicodedata.name(char, "").startswith("LATIN "))


def _word_end(text: str, start: int) -> int | None:
    if not _latin_or_digit(text[start]):
        return None
    index = start + 1
    while index < len(text):
        char = text[index]
        if _latin_or_digit(char) or unicodedata.category(char).startswith("M"):
            index += 1
        elif char in "'’" and index + 1 < len(text) and _latin_or_digit(text[index + 1]):
            index += 1
        else:
            break
    return index


def _without_comments(raw: str) -> tuple[str, tuple[CommentSpan, ...]]:
    """按 TeX 规则移除注释及紧随的换行，逐字片段除外。"""
    parts: list[str] = []
    comments: list[CommentSpan] = []
    index = 0
    while index < len(raw):
        verbatim = _VERBATIM.match(raw, index)
        if verbatim:
            closing = f"\\end{{{verbatim.group(1)}}}"
            end = raw.find(closing, verbatim.end())
            if end >= 0:
                end += len(closing)
                parts.append(raw[index:end])
                index = end
                continue
        if raw.startswith(r"\verb", index) and (index + 5 == len(raw) or not raw[index + 5].isalpha()):
            delimiter_at = index + 5 + (index + 5 < len(raw) and raw[index + 5] == "*")
            if delimiter_at < len(raw):
                end = raw.find(raw[delimiter_at], delimiter_at + 1)
                if end >= 0:
                    parts.append(raw[index:end + 1])
                    index = end + 1
                    continue
        if raw[index] == "%":
            backslashes = 0
            previous = index - 1
            while previous >= 0 and raw[previous] == "\\":
                backslashes += 1
                previous -= 1
            if backslashes % 2 == 0:
                end = index
                while end < len(raw) and raw[end] not in "\r\n":
                    end += 1
                comments.append(CommentSpan(index, end, raw[index:end]))
                index = end + 2 if raw.startswith("\r\n", end) else end + 1 if end < len(raw) else end
                continue
        parts.append(raw[index])
        index += 1
    return "".join(parts), tuple(comments)


def scan_latex(raw: str) -> tuple[tuple[TextToken, ...], tuple[CommentSpan, ...]]:
    """普通正文按词扫描；宏参数、数学和逐字片段保持原样。"""
    raw, comments = _without_comments(raw)
    tokens: list[TextToken] = []
    index = 0
    ignored_control_space = False
    while index < len(raw):
        verbatim = _VERBATIM.match(raw, index)
        if verbatim:
            closing = f"\\end{{{verbatim.group(1)}}}"
            end = raw.find(closing, verbatim.end())
            if end >= 0:
                end += len(closing)
                tokens.append(TextToken(raw[index:end], "verbatim"))
                index = end
                ignored_control_space = False
                continue
        if raw.startswith(r"\verb", index) and (index + 5 == len(raw) or not raw[index + 5].isalpha()):
            delimiter_at = index + 5 + (index + 5 < len(raw) and raw[index + 5] == "*")
            if delimiter_at < len(raw):
                end = raw.find(raw[delimiter_at], delimiter_at + 1)
                if end >= 0:
                    tokens.append(TextToken(raw[index:end + 1], "verbatim"))
                    index = end + 1
                    ignored_control_space = False
                    continue
        if raw.startswith(r"\(", index) or raw.startswith(r"\[", index):
            opening = raw[index:index + 2]
            closing = r"\)" if opening == r"\(" else r"\]"
            end = _math_end(raw, index, opening, closing)
            if end:
                tokens.append(TextToken(raw[index:end], "math"))
                index = end
                ignored_control_space = False
                continue
        if raw[index] == "$":
            opening = "$$" if raw.startswith("$$", index) else "$"
            end = _math_end(raw, index, opening, opening)
            if end:
                tokens.append(TextToken(raw[index:end], "math"))
                index = end
                ignored_control_space = False
                continue
        command = _COMMAND.match(raw, index)
        if command:
            end = command.end()
            if raw[index:end] == r"\%":
                tokens.append(TextToken("%", "symbol"))
                index = end
                ignored_control_space = False
                continue
            # 空白只在命令紧邻参数时归入命令；参数内原文不折叠。
            arguments = []
            while True:
                opening = end
                while opening < len(raw) and raw[opening].isspace():
                    opening += 1
                if opening >= len(raw) or raw[opening] not in "[{":
                    break
                close = "]" if raw[opening] == "[" else "}"
                next_end = _group_end(raw, opening, raw[opening], close)
                if next_end is None:
                    break
                arguments.append(raw[opening:next_end])
                end = next_end
            name = command.group()
            kind = "citation" if _CITATION.fullmatch(name) else "reference" if _REFERENCE.fullmatch(name) else "command"
            words = 0
            if name.lstrip("\\").rstrip("*") in _TEXT_ARGUMENT_COMMANDS:
                content = next((argument[1:-1] for argument in arguments if argument.startswith("{")), None)
                if content is not None:
                    words = sum(token.words for token in scan_latex(content)[0])
            tokens.append(TextToken(name + "".join(arguments), kind, words))
            index = end
            ignored_control_space = not arguments and bool(re.fullmatch(r"\\[A-Za-z@]+", name))
            continue
        if raw[index].isspace():
            while index < len(raw) and raw[index].isspace():
                index += 1
            if tokens and index < len(raw) and not ignored_control_space:
                tokens.append(TextToken("␠", "space"))
            continue
        if raw[index] == "~":
            tokens.append(TextToken("~", "space"))
            index += 1
            ignored_control_space = False
            continue
        end = _word_end(raw, index)
        if end is not None:
            tokens.append(TextToken(raw[index:end], "word", 1))
            index = end
            ignored_control_space = False
            continue
        if unicodedata.category(raw[index]).startswith("P") or raw[index] in "{}":
            tokens.append(TextToken(raw[index], "symbol"))
        else:
            tokens.append(TextToken(raw[index], "word", 1))
        index += 1
        ignored_control_space = False
    return tuple(tokens), comments


def token_edits(old: tuple[TextToken, ...], new: tuple[TextToken, ...]) -> tuple[TokenEdit, ...]:
    result = []
    matcher = SequenceMatcher(None, [(t.kind, t.text) for t in old], [(t.kind, t.text) for t in new], autojunk=False)
    for operation, a, b, c, d in matcher.get_opcodes():
        if operation != "equal":
            result.append(TokenEdit({"insert": "added", "delete": "removed", "replace": "modified"}[operation], old[a:b], new[c:d]))
    return tuple(result)


def normalized_text(raw: str) -> str:
    """匹配用指纹；保护片段的空白保留。"""
    return " ".join(token.text for token in scan_latex(raw)[0])
