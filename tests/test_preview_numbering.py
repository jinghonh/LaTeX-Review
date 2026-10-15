"""Preview counters are checked against real LaTeX output, including unlabeled content."""
from dataclasses import replace
import json
import os
import re
import shutil

import pytest

from latex_review import parse_project, render_preview
from latex_review.compilation import compile_side
from latex_review.contract import ComparisonSource
from latex_review.sources import ProjectSource


def project_at(tmp_path, body, preamble='', cls='article'):
    root = tmp_path / 'paper'
    root.mkdir()
    (root / 'main.tex').write_text('\\documentclass{' + cls + '}\n\\usepackage{amsmath}\n' + preamble
                                  + '\n\\begin{document}\n' + body + '\n\\end{document}')
    source = ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root)))
    return source, parse_project(source.expand())


BODY = r'''
\section{Main}\label{sec:main}
\subsection{Details}\label{sec:detail}
\begin{figure}\caption{First image}\label{fig:one}\end{figure}
\begin{table}\caption{First table}\label{tab:one}\begin{tabular}{cc}a&b\end{tabular}\end{table}
\begin{equation}x=1\label{eq:one}\end{equation}
\section*{Unnumbered}
\begin{equation*}y=2\end{equation*}
\appendix
\section{Extra}\label{sec:extra}
\begin{align}a&=b\label{eq:two}\\c&=d\notag\\e&=f\tag{Special}\label{eq:special}\end{align}
\begin{equation}z=3\label{eq:last}\end{equation}
\begin{figure}\caption{Extra image}\label{fig:extra}\end{figure}
See \ref{sec:main}, \eqref{eq:two}, \ref{fig:extra}, and \ref{tab:one}.
'''
PREAMBLE = r'''\numberwithin{equation}{section}
\renewcommand{\thefigure}{S\arabic{figure}}
\setcounter{figure}{4}'''


def test_source_numbering_appendix_custom_counters_and_references(tmp_path):
    _, project = project_at(tmp_path, BODY, PREAMBLE)
    assert project.numbering.labels == {'sec:main': '1', 'sec:detail': '1.1', 'fig:one': 'S5', 'tab:one': '1',
                                        'eq:one': '1.1', 'sec:extra': 'A', 'eq:two': 'A.1',
                                        'eq:special': 'Special', 'eq:last': 'A.2', 'fig:extra': 'S6'}
    html = render_preview(None, project).html
    assert '<span class="structure-number">1.1</span>' in html
    assert '<span class="structure-number">A</span>' in html
    assert '<span class="structure-number">Figure S6</span>' in html
    assert '<span class="structure-number">Table 1</span>' in html
    assert '>(A.1)</a>' in html
    assert r'\tag{A.1}' in html and r'\tag{Special}' in html
    assert not re.search(r'class="structure-number">[^<]*Unnumbered', html)
    assert not [item for item in render_preview(None, project).diagnostics if item.code == 'preview_number_unknown']


@pytest.mark.skipif(not all(shutil.which(tool) for tool in ('latexmk', 'pdflatex')), reason='requires TeX')
def test_source_numbers_match_latex_and_compile_collects_unlabeled_custom_numbers(tmp_path):
    source, project = project_at(tmp_path, BODY, PREAMBLE)
    output = tmp_path / 'report'
    output.mkdir()
    original = (source.root / source.entry).read_bytes()
    status, diagnostics = compile_side(source, output)
    assert status.status == 'success', diagnostics
    metadata = json.loads((output / 'compiled/new/numbering.json').read_text())
    compiled = replace(project, compiled_numbering=metadata)
    assert compiled.numbering.labels == project.numbering.labels
    assert (source.root / source.entry).read_bytes() == original
    assert all(key.startswith('latex-review-number-') is False for key in metadata['labels'])
    # A custom formatter that the static evaluator cannot execute still works for unlabeled nodes.
    (source.root / source.entry).write_text(r'''\documentclass{article}\usepackage{amsmath}
\renewcommand{\thesection}{\ifnum\value{section}=1 First\else Other\fi}
\renewcommand{\thefigure}{\ifodd\value{figure} Odd\else Even\fi}
\begin{document}\section{Heading}
\begin{figure}\caption{Image}\end{figure}
\begin{align}a&=b\\c&=d\notag\\d&=e\tag{ZZ}\end{align}
\end{document}''')
    project = parse_project(source.expand())
    status, diagnostics = compile_side(source, output)
    assert status.status == 'success', diagnostics
    metadata = json.loads((output / 'compiled/new/numbering.json').read_text())
    compiled = replace(project, compiled_numbering=metadata)
    html = render_preview(None, compiled).html
    assert '<span class="structure-number">First</span>' in html
    assert '<span class="structure-number">Figure Odd</span>' in html
    assert r'\tag{1}' in html and r'\tag{ZZ}' in html
    assert not compiled.numbering.unknown
    from latex_review import compare_projects, write_report
    report = write_report(project, project, compare_projects(project, project), output,
                          statuses=(status,), pdf_converter='')
    assert '<span class="structure-number">First</span>' in report.html.read_text()
    assert '<span class="structure-number">Figure Odd</span>' in report.html.read_text()


def test_valid_aux_overrides_source_but_stale_aux_does_not(tmp_path):
    source, project = project_at(tmp_path, r'\section{Hello}\label{sec:a}')
    aux = source.root / 'main.aux'
    aux.write_text(r'\newlabel{sec:a}{{CUSTOM-7}{1}{}{section.1}{}}')
    assert parse_project(source.expand()).numbering.nodes[project.nodes[0].review.id] == 'CUSTOM-7'
    os.utime(aux, ns=(1, 1))
    assert parse_project(source.expand()).numbering.labels['sec:a'] == '1'


def test_book_hierarchy_and_counter_resets(tmp_path):
    _, project = project_at(tmp_path, r'''\chapter{First}\section{One}\subsection{A}
\begin{equation}x=1\end{equation}\chapter{Second}\section{Two}
\begin{equation}x=2\end{equation}\appendix\chapter{Proof}\section{More}
\begin{equation}x=3\end{equation}''', cls='book')
    assert list(project.numbering.nodes.values()) == ['1', '1.1', '1.1.1', '1.1', '2', '2.1', '2.1', 'A', 'A.1', 'A.1']


def test_no_caption_no_number_and_nested_ams_rows(tmp_path):
    _, project = project_at(tmp_path, r'''\section{Main}
\begin{figure}No caption.\end{figure}
\begin{align}a&=\begin{aligned}b+c\\d+e\end{aligned}\\f&=g\end{align}
\begin{equation}z=3\end{equation}''')
    assert not any(node.review.id in project.numbering.nodes for node in project.nodes if node.review.type == 'figure')
    equations = list(project.numbering.equations.values())
    assert re.findall(r'\\tag\{([^{}]+)\}', equations[0]) == ['1', '2']
    assert r'\tag{3}' in equations[1]


def test_subequations_and_body_number_declarations(tmp_path):
    _, project = project_at(tmp_path, r'''\section{Main}
\begin{subequations}\label{eq:parent}
\begin{equation}a=1\label{eq:a}\end{equation}
\begin{equation}b=2\label{eq:b}\end{equation}
\end{subequations}
\renewcommand{\theequation}{E\arabic{equation}}
\setcounter{equation}{7}
\begin{equation}c=3\label{eq:c}\end{equation}''')
    assert project.numbering.labels == {'eq:parent': '1', 'eq:a': '1a', 'eq:b': '1b', 'eq:c': 'E8'}
    html = render_preview(None, project).html
    assert r'\tag{1a}' in html and r'\tag{1b}' in html and r'\tag{E8}' in html
    assert '此处暂无法预览' not in render_preview(None, project)._content


def test_display_numbers_do_not_enter_source_annotation_coordinates(tmp_path):
    from latex_review.annotations import build_content
    _, project = project_at(tmp_path, BODY, PREAMBLE)
    content = build_content(project, render_preview(None, project).html)
    text = [unit['text'] for unit in content['units'] if unit['kind'] == 'text']
    assert 'Main' in text and 'First image' in text and 'First table' in text
    assert not any(value.startswith(('Figure S5', 'Table 1', '1 Main')) for value in text)


@pytest.mark.skipif(not all(shutil.which(tool) for tool in ('latexmk', 'pdflatex')), reason='requires TeX')
def test_compiled_numbers_keep_repeated_inputs_distinct_and_source_lines(tmp_path):
    from latex_review.compilation import _copy_project
    from latex_review.compile_numbering import instrument
    source, _ = project_at(tmp_path, r'\input{piece}\input{piece}')
    piece = source.root / 'piece.tex'
    piece.write_text('\\section{Repeated}\n\\begin{equation}x=1\\end{equation}\n')
    project = parse_project(source.expand())
    output = tmp_path / 'output'
    output.mkdir()
    copy = tmp_path / 'private'
    copy.mkdir()
    _copy_project(source, copy, output)
    instrument(source, copy)
    assert (copy / source.entry).read_text().count('\n') == (source.root / source.entry).read_text().count('\n')
    assert (copy / 'piece.tex').read_text().count('\n') == piece.read_text().count('\n')
    status, diagnostics = compile_side(source, output)
    assert status.status == 'success', diagnostics
    metadata = json.loads((output / 'compiled/new/numbering.json').read_text())
    numbered = replace(project, compiled_numbering=metadata)
    assert list(metadata['nodes'].values()) == ['1', '2']
    assert [rows['0'] for rows in metadata['rows'].values()] == ['1', '2']
    assert list(numbered.numbering.nodes.values()) == ['1', '1', '2', '2']


def test_custom_class_without_compiled_numbers_is_explicitly_unknown(tmp_path):
    _, project = project_at(tmp_path, r'\section{Title}\begin{equation}x=1\end{equation}', cls='IEEEtran')
    preview = render_preview(None, project)
    assert not project.numbering.nodes
    assert len([item for item in preview.diagnostics if item.code == 'preview_number_unknown']) == 2


@pytest.mark.skipif(not all(shutil.which(tool) for tool in ('latexmk', 'pdflatex')), reason='requires TeX')
def test_compiled_number_capture_accepts_labels_hidden_inside_macros(tmp_path):
    source, project = project_at(tmp_path, r'\begin{align}x&=1\hiddenlabel{eq:hidden}\end{align}',
                                 r'\newcommand{\hiddenlabel}[1]{\label{#1}}')
    output = tmp_path / 'output'
    output.mkdir()
    status, diagnostics = compile_side(source, output)
    assert status.status == 'success', diagnostics
    metadata = json.loads((output / 'compiled/new/numbering.json').read_text())
    assert metadata['labels']['eq:hidden'] == '1'
    assert list(metadata['rows'].values()) == [{'0': '1'}]
