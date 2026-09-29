"""受限的声明式宏占位定义；只产生文本预览，不执行 TeX。"""

from __future__ import annotations

from dataclasses import dataclass
import re


_NAME = re.compile(r"[A-Za-z@]+\Z")
_COMMAND = re.compile(r"\\([A-Za-z@]+)")
_RESERVED = {"input", "include", "begin", "end", "documentclass", "usepackage", "bibliography",
             "addbibresource", "includegraphics", "newcommand", "renewcommand", "providecommand",
             "def", "gdef", "edef", "xdef", "write", "write18", "openout", "read", "catcode"}
MAX_DEPTH = 8
MAX_CALLS = 128
MAX_OUTPUT = 4096


@dataclass(frozen=True)
class MacroPlaceholder:
    arguments: int
    strategy: str
    template: str = ""


def validate_macros(data: object) -> dict[str, MacroPlaceholder]:
    if not isinstance(data, dict) or len(data) > 64:
        raise ValueError("[macros] 必须是表，且至多定义 64 个宏")
    names = set(data)
    result = {}
    for name, value in data.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in _RESERVED:
            raise ValueError(f"宏名无效：{name}")
        if not isinstance(value, dict) or set(value) - {"arguments", "strategy", "template"}:
            raise ValueError(f"[macros.{name}] 只能设置 arguments、strategy、template")
        count = value.get("arguments")
        strategy = value.get("strategy")
        template = value.get("template", "")
        if type(count) is not int or not 0 <= count <= 4 or not isinstance(strategy, str) or strategy not in {"replace", "raw"}:
            raise ValueError(f"[macros.{name}] 参数数量须为 0–4，策略须为 replace 或 raw")
        if not isinstance(template, str) or len(template) > MAX_OUTPUT or (strategy == "raw" and template):
            raise ValueError(f"[macros.{name}] 替换文本无效")
        if strategy == "replace":
            if re.search(r"#(?![1-4])", template) or any(int(n) > count for n in re.findall(r"#([1-4])", template)):
                raise ValueError(f"[macros.{name}] 参数占位符超出声明数量")
            if any(command not in names for command in _COMMAND.findall(template)):
                raise ValueError(f"[macros.{name}] 替换文本只能调用已配置的占位宏")
        result[name] = MacroPlaceholder(count, strategy, template)
    return result


def _argument(text: str, position: int) -> tuple[str, int] | None:
    while position < len(text) and text[position].isspace():
        position += 1
    if position >= len(text) or text[position] != "{":
        return None
    depth = 1
    cursor = position + 1
    while cursor < len(text):
        if text[cursor] == "\\" and cursor + 1 < len(text) and text[cursor + 1] in "{}":
            cursor += 2
            continue
        if text[cursor] == "{":
            depth += 1
        elif text[cursor] == "}":
            depth -= 1
            if depth == 0:
                return text[position + 1:cursor], cursor + 1
        cursor += 1
    return None


def expand_call(text: str, start: int, name: str, definitions: dict[str, MacroPlaceholder]) -> tuple[str, int, str | None]:
    """返回安全的纯文本占位结果、调用末尾和降级原因。"""
    calls = 0

    def expand(value: str, stack: tuple[str, ...]) -> tuple[str, str | None]:
        nonlocal calls
        output = []
        cursor = 0
        while (match := _COMMAND.search(value, cursor)) is not None:
            if match.group(1) not in definitions:
                output.append(value[cursor:match.end()])
                cursor = match.end()
                continue
            output.append(value[cursor:match.start()])
            rendered, end, problem = call(value, match.start(), match.group(1), stack)
            if problem:
                return value, problem
            output.append(rendered)
            cursor = end
        output.append(value[cursor:])
        joined = "".join(output)
        return (joined, "展开超过 4096 字符") if len(joined) > MAX_OUTPUT else (joined, None)

    def call(value: str, position: int, macro: str, stack: tuple[str, ...]) -> tuple[str, int, str | None]:
        nonlocal calls
        calls += 1
        if macro in stack or len(stack) >= MAX_DEPTH:
            return value[position:], len(value), "递归宏定义或展开层数超限"
        if calls > MAX_CALLS:
            return value[position:], len(value), "宏调用次数超限"
        definition = definitions[macro]
        cursor = position + len(macro) + 1
        arguments = []
        for _ in range(definition.arguments):
            parsed = _argument(value, cursor)
            if parsed is None:
                end = position + len(macro) + 1
                return value[position:end], end, "宏调用参数数量不匹配或参数未闭合"
            argument, cursor = parsed
            arguments.append(argument)
        original = value[position:cursor]
        if len(original) > MAX_OUTPUT:
            return original, cursor, "宏调用超过 4096 字符"
        if definition.strategy == "raw":
            return original, cursor, None
        rendered = re.sub(r"#([1-4])", lambda match: arguments[int(match.group(1)) - 1], definition.template)
        rendered, problem = expand(rendered, (*stack, macro))
        return (original if problem else rendered), cursor, problem

    return call(text, start, name, ())
