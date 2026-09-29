"""#40：常见论文模板的结构与词数回归样例。"""

from pathlib import Path
import re
import subprocess

from latex_review import compare_projects, parse_project, render_preview, resolve_sources, scan_latex
import latex_review.sources as sources_module


def _parse(tmp_path: Path, old: str, new: str):
    for side, source in (("old", old), ("new", new)):
        root = tmp_path / side
        root.mkdir(parents=True)
        (root / "main.tex").write_text(source, encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        return parse_project(pair.old.expand()), parse_project(pair.new.expand())


def test_frontmatter_abstract_is_paired_and_metadata_is_not_prose(tmp_path):
    def source(claim):
        return (r"\documentclass{elsarticle}\begin{document}\begin{frontmatter}"
                r"\title{Paper title}\author[one]{Alice}\affiliation[one]{organization={Research Lab}}"
                r"\begin{abstract}Stable sentence. " + claim +
                r" uses $x^2$ and \citep[see]{key}.\end{abstract}"
                r"\begin{keyword}Operator \sep geometry\end{keyword}\end{frontmatter}"
                r"\section{Body}Unchanged body.\end{document}")
    old, new = _parse(tmp_path, source("Method A"), source("Method B"))
    for project in (old, new):
        nodes = project.nodes
        front = next(n for n in nodes if n.review.type == "frontmatter")
        abstract = next(n for n in nodes if n.review.type == "abstract")
        assert abstract.review.parent_id == front.review.id
        paragraph = next(n for n in nodes if n.review.type == "paragraph" and "Stable sentence" in n.review.raw_latex)
        assert paragraph.review.parent_id == abstract.review.id
        assert paragraph.review.source.file == "main.tex"
        assert {project.by_id()[child].review.type for child in paragraph.review.child_ids} >= {"inline_math", "citation"}
        assert any(n.review.type == "keywords" for n in nodes)
    result = compare_projects(old, new)
    assert [(c.kind, c.node_type) for c in result.document.changes] == [("modified", "paragraph")]
    assert (result.document.summary.added_words, result.document.summary.removed_words) == (1, 1)


def test_plain_abstract_and_math_matrix_not_counted_as_table(tmp_path):
    def source(value):
        return (r"\begin{document}\begin{abstract}A result with $x$ and \eqref{eq:a}.\end{abstract}"
                r"\begin{equation}\begin{bmatrix}a&" + value +
                r"\\c&d\end{bmatrix}\label{eq:a}\end{equation}"
                r"\begin{table}\caption{Real table}\begin{tabular}{cc}a&b\end{tabular}\end{table}\end{document}")
    old, new = _parse(tmp_path, source("b"), source("q"))
    assert any(n.review.type == "abstract" for n in old.nodes)
    assert sum(n.review.type == "table" for n in old.nodes) == 1
    result = compare_projects(old, new)
    assert result.document.summary.category_hits.get("table", 0) == 0
    assert result.document.summary.category_hits.get("equation", 0) == 1


def test_citation_and_reference_arguments_are_consumed_without_word_noise(tmp_path):
    old, new = _parse(tmp_path,
        r"\begin{document}See \citep[chapter]{key} and \eqref{eq:a}.\end{document}",
        r"\begin{document}See \citep[chapter]{key2} and \eqref{eq:b}.\end{document}")
    paragraph = next(n for n in new.nodes if n.review.type == "paragraph")
    children = [new.by_id()[child] for child in paragraph.review.child_ids]
    assert [(n.review.type, n.review.raw_latex) for n in children] == [
        ("citation", r"\citep[chapter]{key2}"), ("reference", r"\eqref{eq:b}")]
    result = compare_projects(old, new)
    assert (result.document.summary.added_words, result.document.summary.removed_words) == (0, 0)
    assert all(n.review.type != "fallback" for n in new.nodes)
    assert [token.text for token in scan_latex(r"\author{Alice} \affiliation{Research Lab}")[0] if token.words] == []
    html = render_preview(old, new).html
    assert not re.search(r"\]</span>key2\b", html)
    assert not re.search(r"\?\? \(eq:b\)</span>eq:b\b", html)


def test_keywords_change_is_reported_without_body_word_count(tmp_path):
    old, new = _parse(tmp_path,
        r"\begin{document}\begin{frontmatter}\begin{keyword}operator \sep geometry\end{keyword}\end{frontmatter}\end{document}",
        r"\begin{document}\begin{frontmatter}\begin{keyword}operator \sep analysis\end{keyword}\end{frontmatter}\end{document}")
    result = compare_projects(old, new)
    assert [(change.node_type, change.kind) for change in result.document.changes] == [("keywords", "modified")]
    assert (result.document.summary.added_words, result.document.summary.removed_words) == (0, 0)


def test_standard_math_commands_do_not_become_unknown_nodes(tmp_path):
    formula = r"\begin{equation}\widehat x\in\mathbb R,\quad \Omega=\bigcap_i A_i\pm\varsigma\odot\mathop{f}\end{equation}"
    old, new = _parse(tmp_path, r"\begin{document}" + formula + r"\end{document}",
                       r"\begin{document}" + formula + r"\end{document}")
    assert all(node.review.type != "fallback" for project in (old, new) for node in project.nodes)


def test_unique_short_headings_pair_despite_terminal_punctuation(tmp_path):
    def source(suffix):
        return (r"\begin{document}\section{Benchmarks}" + "\n"
                + r"\paragraph{Benchmark A" + suffix + "}\n\n" + r"\paragraph{Benchmark B" + suffix +
                "}\n\n" + r"\paragraph{Benchmark C" + suffix + r"}\end{document}")
    old, new = _parse(tmp_path, source("."), source(""))
    result = compare_projects(old, new)
    assert len(result.mapping.pairs) == 4
    assert [(change.kind, change.node_type) for change in result.document.changes] == [
        ("modified", "paragraph")] * 3
    assert not any(d.code == "low_confidence_match" for d in result.document.diagnostics)


def test_distribution_bibliography_style_is_not_missing_project_resource(tmp_path, monkeypatch):
    original_run = sources_module.subprocess.run
    monkeypatch.setattr(sources_module.shutil, "which", lambda name: "/fake/kpsewhich" if name == "kpsewhich" else None)
    def run(args, **kwargs):
        if args[0] == "/fake/kpsewhich":
            return subprocess.CompletedProcess(args, 0, b"/texmf/elsarticle-num.bst\n", b"")
        return original_run(args, **kwargs)
    monkeypatch.setattr(sources_module.subprocess, "run", run)
    old, new = _parse(tmp_path,
        r"\begin{document}Text.\bibliographystyle{elsarticle-num}\end{document}",
        r"\begin{document}Text.\bibliographystyle{elsarticle-num}\end{document}")
    assert all(any(issue.code == "tex_distribution_style" for issue in project.expanded.diagnostics)
               for project in (old, new))
