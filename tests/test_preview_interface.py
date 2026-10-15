"""内容预览 interface 的页面组装与句子副本行为。"""
import re

import pytest

from latex_review import parse_project, render_preview, resolve_sources
from latex_review.preview import render_sentence_copy


@pytest.fixture
def projects(tmp_path):
    source = r'''\begin{document}
\section{Method}\label{sec:method}
See \ref{sec:method} and \cite{key} with \textbf{bold} and $x+1$.
\begin{thebibliography}{9}\bibitem{key}Reference.\end{thebibliography}
\end{document}'''
    for side in ('old', 'new'):
        directory = tmp_path / side
        directory.mkdir()
        (directory / 'main.tex').write_text(source, encoding='utf-8')
    with resolve_sources(entry='main.tex', old_dir=tmp_path / 'old', new_dir=tmp_path / 'new') as pair:
        yield parse_project(pair.old.expand()), parse_project(pair.new.expand())


@pytest.mark.parametrize('two_sides', [False, True])
def test_composition_preserves_content_and_math_with_custom_presentation(projects, two_sides):
    old, new = projects
    preview = render_preview(old if two_sides else None, new)
    html = preview.compose(
        title='Paper <review>', body_class='reading-view',
        head='<meta name="review" content="custom">', style='.custom{color:red}',
        before_main='<header id="custom-header">Review<p id="math-status"></p></header>',
        main_end='<aside id="changes">Changes</aside>',
        after_main='<footer id="diagnostics">Diagnostics</footer>',
        scripts_before_math='<script id="before-math"></script>',
        scripts_after_math='<script id="after-math"></script>')
    assert '<title>Paper &lt;review&gt;</title>' in html
    assert '<body class="reading-view">' in html
    assert '<meta name="review" content="custom">' in html
    assert '.custom{color:red}' in html
    assert html.index('id="custom-header"') < html.index('<main>')
    assert html.index('id="changes"') < html.index('</main>') < html.index('id="diagnostics"')
    assert html.index('id="before-math"') < html.index('window.MathJax=') < html.index('id="after-math"')
    assert 'mathDependencyFailed()' in html
    assert set(re.findall(r'\bid="([^"]+)"', preview.html)) - {'math-status'} == preview.anchors()
    assert preview.anchors() <= set(re.findall(r'\bid="([^"]+)"', html))
    assert 'data-display="false">x+1</span>' in html
    assert 'LaTeX 内容预览</h1>' not in html
    assert preview.compose() == preview.html


def test_sentence_copy_preserves_rendering_without_duplicate_interaction(projects):
    _, project = projects
    paragraph = next(node for node in project.nodes if node.review.type == 'paragraph')
    raw = paragraph.review.raw_latex
    html = render_sentence_copy(raw, project, 'new', paragraph.review.id, (1,))
    assert '<strong>bold</strong>' in html
    assert 'x+1' in html and 'citation' in html and '<span>1</span>' in html
    assert ' id=' not in html and '<a' not in html and 'href=' not in html
    assert render_sentence_copy(None, project, 'new', None, ()) == '该侧无内容'
    for unsafe in ('<script>alert(1)</script>', r'\href{javascript:alert(1)}{click}'):
        assert render_sentence_copy(unsafe, project, 'new', paragraph.review.id, (1,)) == '此处暂无法预览'
    assert render_sentence_copy(raw, project, 'new', paragraph.review.id, (999,)) == '此处暂无法预览'
