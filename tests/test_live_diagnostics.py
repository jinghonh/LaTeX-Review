from latex_review.contract import ComparisonSource
from latex_review.sources import ProjectSource
from latex_review.structure import parse_project
from latex_review.live import build


def test_live_body_title_and_supported_commands(tmp_path):
    (tmp_path / 'main.tex').write_text(r'''\documentclass{article}
\begin{document}
\title{Geometry with $K$}
\author{}
\maketitle
Body before metadata.
\date{}
Body after metadata, with $\oplus$.
\begin{equation}K=\bigoplus_q E_q\end{equation}
\begin{tabular}{cc}A&B\\\cmidrule(lr){1-2}C&D\\\end{tabular}
\end{document}''')
    source = ProjectSource('new', tmp_path, 'main.tex', ComparisonSource('directory', str(tmp_path)))
    parsed = parse_project(source.expand(), include_title=True)
    assert len([n for n in parsed.nodes if n.review.type == 'title']) == 1
    output = tmp_path / 'output'
    output.mkdir()
    messages = build(source, output)
    html = (output / 'report.html').read_text()
    assert not messages
    assert 'Body before metadata.' in html and 'Body after metadata' in html
    assert '此处暂无法预览' not in html.split('<script>')[0]


def test_live_diagnostics_show_severity_and_location(tmp_path, monkeypatch):
    import latex_review.sources as sources
    from types import SimpleNamespace
    monkeypatch.setattr(sources.shutil, 'which', lambda name, **kw: '/fake/kpsewhich' if name == 'kpsewhich' else None)
    monkeypatch.setattr(sources.local_tasks, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b'/tex/IEEEtranN.bst'))
    (tmp_path / 'main.tex').write_text('\\begin{document}\n\\unknownthing{content}\n\\bibliographystyle{IEEEtranN}\n\\end{document}')
    source = ProjectSource('new', tmp_path, 'main.tex', ComparisonSource('directory', str(tmp_path)))
    output = tmp_path / 'output'
    output.mkdir()
    build(source, output)
    html = (output / 'report.html').read_text().split('id="live-diagnostics"', 1)[1].split('</details>', 1)[0]
    assert '警告' in html and 'main.tex:2' in html
    assert '信息' in html and 'main.tex:3' in html
    parsed = parse_project(source.expand())
    assert next(d for d in parsed.diagnostics if d.code == 'tex_distribution_style').severity == 'info'
