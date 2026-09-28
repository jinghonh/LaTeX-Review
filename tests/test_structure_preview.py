import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from latex_review import (
    ReviewDocument, build_summary, parse_project, render_preview, resolve_sources,
    validate_document,
)


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _pair(tmp_path: Path, source: str):
    for side in ("old", "new"):
        root = tmp_path / side
        root.mkdir()
        _write(root, "main.tex", source)
    return tmp_path / "old", tmp_path / "new"


def test_structure_nested_content_metadata_and_visible_fallback(tmp_path):
    source = r"""\documentclass{article}
\usepackage{graphicx}
\newtheorem{claim}{Claim}
\begin{document}
\section{方法}\label{sec:method}
开头 \textbf{重点 $x+1$}，见 \ref{sec:method} 和 \cite[第2页]{a,b}。\odd{原样}

\begin{claim}结论。\begin{itemize}\item 首项\item 次项 $z$\end{itemize}\end{claim}
\begin{equation}E=mc^2\label{eq:e}\end{equation}
\begin{figure}\includegraphics{fig/chart.png}\caption{结果图}\label{fig:chart}\end{figure}
\begin{table}\caption{结果表}\begin{tabular}{cc}a&b\\\end{tabular}\end{table}
\begin{thebibliography}{9}\bibitem{a}作者 A。\bibitem{b}作者 B。\end{thebibliography}
\begin{mystery}不可丢失 \odd{内文}\end{mystery}
\end{document}"""
    old, new = _pair(tmp_path, source)
    _write(old, "fig/chart.png", "image")
    _write(new, "fig/chart.png", "image")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        parsed = parse_project(pair.old.expand())
        nodes = parsed.nodes
        types = {node.review.type for node in nodes}
        assert {"section", "paragraph", "inline_math", "reference", "citation", "theorem", "list", "list_item",
                "equation", "figure", "table", "bibliography", "bibliography_entry", "fallback"} <= types
        assert parsed.labels["sec:method"] == next(node.review.id for node in nodes if node.review.type == "section")
        assert {"eq:e", "fig:chart", "bib:a", "bib:b"} <= parsed.labels.keys()
        paragraph = next(node for node in nodes if node.review.type == "paragraph" and "开头" in node.review.raw_latex)
        assert paragraph.citations == ("a", "b") and paragraph.references == ("sec:method",)
        assert paragraph.review.raw_latex == source[paragraph.expanded_start:paragraph.expanded_end]
        assert paragraph.origins[0].origin.file == "main.tex"
        assert paragraph.review.source.start_line == 5  # 前置标签属于同一原文段落。
        figure = next(node for node in nodes if node.review.type == "figure")
        assert figure.assets == ("fig/chart.png",)
        assert any("\\begin{mystery}" in node.review.raw_latex for node in nodes if node.review.type == "fallback")
        assert any("\\odd{原样}" == node.review.raw_latex for node in nodes if node.review.type == "fallback")
        assert any(item.code == "unknown_latex" for item in parsed.diagnostics)
        document = ReviewDocument("main.tex", pair.old.identity, pair.new.identity, parsed.review_nodes,
                                  parse_project(pair.new.expand()).review_nodes, (), parsed.diagnostics, build_summary(()))
        validate_document(document)


def test_repeated_include_and_uncertain_source_keep_all_origins(tmp_path):
    old, new = _pair(tmp_path, "\\begin{document}\n\\input{part}\n\\input{part}\n\\input{missing}\n\\end{document}\n")
    for root in (old, new):
        _write(root, "part.tex", "跨文件段落 $α$。\n")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        parsed = parse_project(pair.old.expand())
        paragraphs = [node for node in parsed.nodes if node.review.type == "paragraph" and "跨文件段落" in node.review.raw_latex]
        assert len(paragraphs) == 2
        assert [node.review.source.file for node in paragraphs] == ["part.tex", "part.tex"]
        assert paragraphs[0].origins[0].origin.include_instance != paragraphs[1].origins[0].origin.include_instance
        assert all(node.review.source.start_line == 1 for node in paragraphs)
        missing = next(node for node in parsed.nodes if node.review.type == "fallback" and "\\input{missing}" in node.review.raw_latex)
        assert missing.origins[0].origin.confidence == "unknown"
        assert missing.review.source.confidence < 1
        assert {diagnostic.code for diagnostic in missing.diagnostics} >= {"unknown_latex", "low_confidence_source", "missing_dependency"}


def test_two_side_preview_math_failure_and_anchor_isolation(tmp_path):
    source = r"""\begin{document}
\section{标题 $q$}\label{sec:x}
见 \ref{sec:x}、\ref{missing}；公式 $α+β$ 和 \cite{key}。\unknown{<script>alert(1)</script>}
\begin{equation}x^2+y^2=z^2\end{equation}
\begin{thebibliography}{9}\bibitem{key}引用资料。\end{thebibliography}
\end{document}"""
    old, new = _pair(tmp_path, source)
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        parsed_old, parsed_new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        result = render_preview(parsed_old, parsed_new)
    html = result.html
    assert "内容预览" in html and "不代表最终编译版式" in html
    assert "\\(α+β\\)" in html and "x^2+y^2=z^2" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "<script>alert(1)</script>" not in html
    assert "\\unknown" in html and "查看 LaTeX 原文" in html
    assert {diagnostic.code for diagnostic in result.diagnostics} >= {"unknown_latex", "unresolved_reference"}
    for side, project in (("old", parsed_old), ("new", parsed_new)):
        ids = re.findall(rf'id="({side}-[^" ]+)"', html)
        assert len(ids) == len(set(ids))
        assert set(ids) == {f"{side}-{node.review.id}" for node in project.nodes}
        assert re.search(rf'href="#{side}-section-[^"]+"', html)
        assert re.search(rf'href="#{side}-bibliography_entry-[^"]+"', html)
    assert "onerror=\"mathDependencyFailed()\"" in html
    assert "在线公式排版不可用；页面保留原始 TeX 公式供阅读。" in html
    if shutil.which("node"):
        script = re.search(r"<script>\n(.*?)\n</script>", html, re.S).group(1)
        harness = """
const vm = require('vm');
const status = {textContent:'',dataset:{}};
const context = {document:{getElementById:()=>status},window:{},setTimeout:()=>1,clearTimeout:()=>{}};
vm.runInNewContext(%s, context);
context.window.mathDependencyFailed();
if(status.dataset.state !== 'failed' || !status.textContent.includes('原始 TeX')) process.exit(1);
""" % json.dumps(script)
        subprocess.run(["node", "-e", harness], check=True)
