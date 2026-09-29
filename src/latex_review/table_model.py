"""保守解析简单表格，并只对确定的行列给出坐标差异。"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from .structure import _masked


_OPEN = re.compile(r"\\begin\{(tabular|array|matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix)\}")
_MATRIX = {"matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix"}
_RULE = re.compile(r"\\(?:hline|toprule|midrule|bottomrule)(?![A-Za-z@])|\\cline\s*\{[^{}]*\}")
_COMPLEX = re.compile(r"\\(?:multicolumn|multirow|makecell|shortstack|resizebox|substack|begin|end)(?![A-Za-z@])")
_SAFE = {
    "cite", "citet", "citep", "parencite", "textcite", "ref", "eqref", "textbf", "textit", "emph",
    "texttt", "text", "mathrm", "mathbf", "mathit", "mathsf", "operatorname", "frac", "dfrac",
    "tfrac", "sqrt", "sum", "prod", "int", "lim", "sin", "cos", "tan", "log", "ln", "exp",
    "alpha", "beta", "gamma", "delta", "epsilon", "theta", "lambda", "mu", "pi", "sigma",
    "omega", "Gamma", "Delta", "Theta", "Lambda", "Pi", "Sigma", "Omega", "cdot", "times",
    "pm", "mp", "leq", "geq", "neq", "infty", "left", "right", "big", "Big", "bigg",
    "quad", "qquad", "ldots", "cdots", "dots", "partial", "nabla", "overline", "hat",
    "bar", "tilde", "vec", "mathbf", "mathbb", "mathrm", "begin", "end", "&", "%", "_", "#",
}


@dataclass(frozen=True)
class TableGrid:
    rows: tuple[tuple[str, ...], ...]
    columns: int
    start: int
    end: int
    environment: str
    specification: str


@dataclass(frozen=True)
class TableEdit:
    kind: str
    old_text: str | None
    new_text: str | None
    summary: str
    row_old: int | None = None
    column_old: int | None = None
    row_new: int | None = None
    column_new: int | None = None


def parse_table(raw: str) -> tuple[TableGrid | None, str]:
    opening = _OPEN.search(_masked(raw))
    if not opening:
        return None, "未找到受支持的简单表格环境"
    environment = opening.group(1)
    cursor = opening.end()
    spec_columns = None
    specification = ""
    if environment not in _MATRIX:
        optional = re.match(r"\s*\[[^\]]*\]", raw[cursor:])
        if optional:
            cursor += optional.end()
        argument = re.match(r"\s*\{([^{}]*)\}", raw[cursor:])
        if not argument or not re.fullmatch(r"[clr|\s]+", argument.group(1)):
            return None, "列规格含复杂构造"
        spec_columns = sum(char in "clr" for char in argument.group(1))
        specification = argument.group(1)
        cursor += argument.end()
    closing = re.search(r"\\end\{" + re.escape(environment) + r"\}", _masked(raw)[cursor:])
    if not closing:
        return None, "表格环境未闭合"
    end = cursor + closing.start()
    if _OPEN.search(_masked(raw), end + len(closing.group())):
        return None, "多个表格环境无法可靠对齐"
    body = raw[cursor:end]
    mask = _masked(body)
    if _COMPLEX.search(mask):
        return None, "跨行跨列、嵌套环境或复杂宏无法可靠对齐"
    for command in re.finditer(r"(?<!\\)\\([A-Za-z@]+)", mask):
        if command.group(1) not in _SAFE and not _RULE.match(mask, command.start()):
            return None, "单元格含未支持的复杂宏"
    # 横线命令不占单元格，保留等长位置以便扫描分隔符。
    cleaned = list(body)
    for rule in _RULE.finditer(mask):
        cleaned[rule.start():rule.end()] = " " * (rule.end() - rule.start())
    body = "".join(cleaned)
    rows: list[tuple[str, ...]] = []
    cells: list[str] = []
    cell_start = 0
    depth = 0
    math: str | None = None
    i = 0

    def finish_row(stop: int, *, explicit: bool = False) -> None:
        nonlocal cell_start, cells
        cells.append(body[cell_start:stop].strip())
        if explicit or any(cells) or len(cells) > 1:
            rows.append(tuple(cells))
        cells = []

    while i < len(body):
        char = body[i]
        if char == "\\":
            command = body[i:i + 2]
            if command in (r"\(", r"\["):
                if math is not None:
                    return None, "数学定界符嵌套"
                math = r"\)" if command == r"\(" else r"\]"
                i += 2
                continue
            if math == command and command in (r"\)", r"\]"):
                math = None
                i += 2
                continue
            if command in (r"\)", r"\]") and math != command:
                return None, "数学定界符不匹配"
            if command == r"\\" and depth == 0 and math is None:
                finish_row(i, explicit=True)
                i += 2
                optional = re.match(r"\s*\[[^\]]*\]", body[i:])
                if optional:
                    i += optional.end()
                cell_start = i
                continue
            # 转义字符及命令名不可充当分隔符。
            match = re.match(r"\\[A-Za-z@]+|\\.", body[i:])
            i += len(match.group()) if match else 1
            continue
        if char == "$" and depth >= 0:
            delim = "$$" if body.startswith("$$", i) else "$"
            if math is not None and math != delim:
                return None, "数学定界符混用"
            math = None if math == delim else delim
            i += len(delim)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return None, "单元格括号不平衡"
        elif char == "&" and depth == 0 and math is None:
            cells.append(body[cell_start:i].strip())
            cell_start = i + 1
        i += 1
    if depth or math:
        return None, "单元格括号或数学定界符不平衡"
    finish_row(len(body))
    if not rows:
        return None, "表格没有可对齐的行"
    columns = spec_columns if spec_columns is not None else len(rows[0])
    if columns < 1 or any(len(row) != columns for row in rows):
        return None, "各行列数与列规格不一致"
    return TableGrid(tuple(rows), columns, opening.start(), end + len(closing.group()), environment,
                     specification), ""


def _align(old: tuple[object, ...], new: tuple[object, ...]) -> list[tuple[int | None, int | None]] | None:
    if len(old) == len(new):
        return [(i, i) for i in range(len(old))]
    # 结构变动时，重复行与空列不能给出可靠的唯一锚点。
    if len(set(old)) != len(old) or len(set(new)) != len(new):
        return None
    shared = set(old) & set(new)
    anchors = [(i, new.index(value)) for i, value in enumerate(old) if value in shared]
    if any(a[1] >= b[1] for a, b in zip(anchors, anchors[1:])):
        return None
    aligned: list[tuple[int | None, int | None]] = []
    previous_old = previous_new = -1
    for next_old, next_new in [*anchors, (len(old), len(new))]:
        left = list(range(previous_old + 1, next_old))
        right = list(range(previous_new + 1, next_new))
        if left and right:
            if len(left) == len(right):
                aligned.extend(zip(left, right))
            elif min(len(left), len(right)) == 1:
                scores = [(SequenceMatcher(None, str(old[i]), str(new[j]), autojunk=False).ratio(), i, j)
                          for i in left for j in right]
                scores.sort(reverse=True)
                if scores[0][0] < .65 or len(scores) > 1 and scores[0][0] - scores[1][0] < .15:
                    return None
                _, matched_old, matched_new = scores[0]
                aligned.extend((i, None) for i in left if i < matched_old)
                aligned.extend((None, j) for j in right if j < matched_new)
                aligned.append((matched_old, matched_new))
                aligned.extend((i, None) for i in left if i > matched_old)
                aligned.extend((None, j) for j in right if j > matched_new)
            else:
                return None
        else:
            aligned.extend((i, None) for i in left)
            aligned.extend((None, j) for j in right)
        if next_old < len(old):
            aligned.append((next_old, next_new))
        previous_old, previous_new = next_old, next_new
    return aligned


def table_edits(old: TableGrid, new: TableGrid) -> list[TableEdit] | None:
    row_pairs = _align(old.rows, new.rows)
    old_columns = tuple(tuple(row[i] for row in old.rows) for i in range(old.columns))
    new_columns = tuple(tuple(row[i] for row in new.rows) for i in range(new.columns))
    column_pairs = _align(old_columns, new_columns)
    if row_pairs is None or column_pairs is None:
        return None
    edits: list[TableEdit] = []
    for i, j in row_pairs:
        if i is None or j is None:
            cells = old.rows[i] if i is not None else new.rows[j]
            edits.append(TableEdit("removed" if i is not None else "added", " & ".join(cells) if i is not None else None,
                                   " & ".join(cells) if j is not None else None,
                                   f"第 {(i if i is not None else j) + 1} 行{'删除' if i is not None else '新增'}",
                                   row_old=i + 1 if i is not None else None, row_new=j + 1 if j is not None else None))
    for i, j in column_pairs:
        if i is None or j is None:
            cells = old_columns[i] if i is not None else new_columns[j]
            edits.append(TableEdit("removed" if i is not None else "added", " / ".join(cells) if i is not None else None,
                                   " / ".join(cells) if j is not None else None,
                                   f"第 {(i if i is not None else j) + 1} 列{'删除' if i is not None else '新增'}",
                                   column_old=i + 1 if i is not None else None, column_new=j + 1 if j is not None else None))
    for row_old, row_new in row_pairs:
        if row_old is None or row_new is None:
            continue
        for column_old, column_new in column_pairs:
            if column_old is None or column_new is None:
                continue
            left, right = old.rows[row_old][column_old], new.rows[row_new][column_new]
            if " ".join(_masked(left).split()) != " ".join(_masked(right).split()):
                edits.append(TableEdit("modified", left, right,
                                       f"单元格第 {row_old + 1} 行 {column_old + 1} 列 → 第 {row_new + 1} 行 {column_new + 1} 列",
                                       row_old + 1, column_old + 1, row_new + 1, column_new + 1))
    return edits
