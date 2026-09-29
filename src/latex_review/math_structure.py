"""受限公式语法的差异摘要；不判断数学等价。"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class MathNode:
    kind: str
    value: str = ""
    children: tuple["MathNode", ...] = ()

    def display(self) -> str:
        if self.kind == "atom":
            return self.value
        if self.kind == "sequence":
            return "".join(child.display() for child in self.children)
        if self.kind == "group":
            return "(" + self.children[0].display() + ")"
        if self.kind == "call":
            return self.value + "(" + self.children[0].display() + ")"
        if self.kind == "fraction":
            return "\\frac{" + self.children[0].display() + "}{" + self.children[1].display() + "}"
        if self.kind == "root":
            return "\\sqrt{" + self.children[0].display() + "}"
        if self.kind == "script":
            return self.children[0].display() + self.value + "{" + self.children[1].display() + "}"
        return self.value


_OPS = {"+", "-", "=", "<", ">", ",", r"\cdot", r"\times", r"\pm", r"\leq", r"\geq"}
_SYMBOLS = {r"\alpha", r"\beta", r"\gamma", r"\delta", r"\epsilon", r"\theta",
            r"\lambda", r"\mu", r"\pi", r"\sigma", r"\omega", r"\Gamma", r"\Delta"}
_FUNCTIONS = {r"\sin", r"\cos", r"\tan", r"\log", r"\ln", r"\exp"}


class _Parser:
    def __init__(self, tokens: tuple[str, ...]):
        self.tokens = tokens
        self.index = 0

    def peek(self) -> str | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> str:
        token = self.peek()
        if token is None:
            raise ValueError("公式不完整")
        self.index += 1
        return token

    def group(self, opening: str = "{", closing: str = "}") -> MathNode:
        if self.take() != opening:
            raise ValueError("缺少分组")
        content = self.expression(closing)
        if self.take() != closing:
            raise ValueError("分组未闭合")
        return content

    def base_atom(self) -> MathNode:
        token = self.peek()
        if token == "{":
            node = self.group()
        elif token == "(":
            node = MathNode("group", children=(self.group("(", ")"),))
        elif token in {r"\frac", r"\dfrac", r"\tfrac"}:
            self.take()
            node = MathNode("fraction", children=(self.group(), self.group()))
        elif token == r"\sqrt":
            self.take()
            node = MathNode("root", children=(self.group(),))
        elif token and (token.isalnum() or token in _SYMBOLS or token in _FUNCTIONS):
            node = MathNode("atom", self.take())
        else:
            raise ValueError("不支持的公式结构")
        return node

    def atom(self) -> MathNode:
        node = self.base_atom()
        if self.peek() == "(" and node.kind == "atom":
            node = MathNode("call", node.value, (self.group("(", ")"),))
        while self.peek() in {"_", "^"}:
            script = self.take()
            argument = self.group() if self.peek() == "{" else self.base_atom()
            node = MathNode("script", script, (node, argument))
        return node

    def expression(self, closing: str | None = None) -> MathNode:
        children: list[MathNode] = []
        while self.peek() is not None and self.peek() != closing:
            token = self.peek()
            if token in _OPS:
                if not children or children[-1].kind == "operator":
                    raise ValueError("运算符缺少操作数")
                children.append(MathNode("operator", self.take()))
            else:
                children.append(self.atom())
        if not children or children[-1].kind == "operator":
            raise ValueError("表达式不完整")
        return children[0] if len(children) == 1 else MathNode("sequence", children=tuple(children))


def parse_math(tokens: tuple[str, ...]) -> MathNode | None:
    try:
        parser = _Parser(tokens)
        result = parser.expression()
        return result if parser.peek() is None else None
    except (ValueError, RecursionError):
        return None


def structure_summary(old: tuple[str, ...], new: tuple[str, ...]) -> str | None:
    """只描述受支持语法的语法树变化，不声称表达式等价。"""
    left, right = parse_math(old), parse_math(new)
    if left is None or right is None or left == right:
        return None
    if left.kind == right.kind == "script" and left.value == right.value and left.children[0] == right.children[0]:
        name = "下标" if left.value == "_" else "上标"
        return f"{name}替换：{left.children[1].display()} → {right.children[1].display()}"
    if left.kind == right.kind == "call" and left.value == right.value:
        return f"参数变更：{left.children[0].display()} → {right.children[0].display()}"
    if left.kind == right.kind == "fraction":
        changed = [name for index, name in enumerate(("分子", "分母")) if left.children[index] != right.children[index]]
        if len(changed) == 1:
            index = 0 if changed[0] == "分子" else 1
            return f"{changed[0]}变更：{left.children[index].display()} → {right.children[index].display()}"
    if left.kind == right.kind == "root":
        return f"根式内容变更：{left.children[0].display()} → {right.children[0].display()}"
    if left.kind == right.kind == "sequence":
        matcher = SequenceMatcher(None, left.children, right.children, autojunk=False)
        edits = [(op, a, b, c, d) for op, a, b, c, d in matcher.get_opcodes() if op != "equal"]
        if len(edits) == 1:
            op, a, b, c, d = edits[0]
            before = "".join(node.display() for node in left.children[a:b])
            after = "".join(node.display() for node in right.children[c:d])
            if op == "insert":
                return f"子表达式新增：{after}"
            if op == "delete":
                return f"子表达式删除：{before}"
            return f"子表达式替换：{before} → {after}"
    if left.kind == right.kind == "atom":
        return f"变量或常量替换：{left.display()} → {right.display()}"
    return f"结构变化：{left.display()} → {right.display()}"
