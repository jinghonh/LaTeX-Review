"""仅在轻量预览不足时编译单个公式或表体；不读取论文目录。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .compilation import _ENGINE_MARKERS, _program, _run_bounded, _safe_path
from .sources import ProjectSource


_DENIED = re.compile(r"\\(?:input|include|write|openout|read|usepackage|documentclass|immediate|catcode)"
                     r"(?![A-Za-z@])", re.I)
_TABLE_START = re.compile(r"\\begin\{(?:tabular\*?|longtable)\}")


def fragment_key(source: ProjectSource, kind: str, raw: str) -> str:
    identity = json.dumps(source.identity.__dict__, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256((identity + "\0" + kind + "\0" + raw).encode("utf-8")).hexdigest()[:24]


def render_fragment(raw: str, kind: str, source: ProjectSource, directory: Path,
                    converters: tuple[tuple[str, str], ...], timeout: float = 12) -> tuple[Path | None, str]:
    """沿用隔离编译限制；成功时只导出 PNG，失败只返回机器诊断。"""
    if kind not in {"equation", "table"} or len(raw) > 20_000 or _DENIED.search(raw):
        return None, "片段超出允许的渲染范围"
    snippet = raw.strip()
    if kind == "table":
        opening = _TABLE_START.search(raw)
        if opening is None:
            return None, "未找到可编译的表体"
        environment = opening.group()[7:-1]
        closing = re.search(r"\\end\{" + re.escape(environment) + r"\}", raw[opening.end():])
        if closing is None:
            return None, "表体未闭合"
        snippet = raw[opening.start():opening.end() + closing.end()]
    safe_path, forbidden = _safe_path(source, directory)
    engine = next(((name, found) for name in ("pdflatex", "xelatex", "lualatex")
                   if (found := _program(name, safe_path, forbidden))), None)
    if engine is None:
        return None, "缺少可用的 LaTeX 编译引擎"
    name, program = engine
    with tempfile.TemporaryDirectory(prefix="latex-review-fragment-") as temporary:
        project = Path(temporary)
        (project / "build").mkdir()
        preamble = (r"\documentclass[preview,border=3pt]{standalone}" + "\n"
                    r"\usepackage{amsmath,amssymb,graphicx,array,booktabs,multirow}" + "\n"
                    r"\begin{document}" + "\n")
        document = preamble + snippet + "\n" + r"\end{document}" + "\n"
        (project / "fragment.tex").write_text(document, encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if key in {"LANG", "LC_ALL"}}
        env.update({"HOME": str(project), "TEXMFOUTPUT": str(project / "build"), "openin_any": "p",
                    "openout_any": "p", "shell_escape": "f", "TMPDIR": str(project / "build"), "PATH": safe_path})
        code, output, reason = _run_bounded([program, "--version"], project, env, 4, 4096)
        if code != 0 or _ENGINE_MARKERS[name].lower() not in output.decode("utf-8", "replace").lower():
            return None, reason or "无法在隔离目录验证编译引擎版本"
        code, output, reason = _run_bounded(
            [program, f"-fmt={name}", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error",
             "-output-directory=build", "fragment.tex"], project, env, timeout, 128 * 1024)
        pdf = project / "build/fragment.pdf"
        if code != 0 or reason or not pdf.is_file() or pdf.is_symlink():
            return None, reason or f"片段编译退出状态 {code}：{output[-300:].decode('utf-8', 'replace')}"
        from .report import _pdf_preview
        relative = Path("previews/fragments") / (fragment_key(source, kind, raw) + ".png")
        return _pdf_preview(pdf, directory, relative, converters, min(timeout, 8), 1)
