from pathlib import Path

from latex_review.contract import ComparisonSource
from latex_review.live import build, fingerprint
from latex_review.sources import ProjectSource


def test_current_preview_updates_without_old_version(tmp_path):
    entry = tmp_path / 'main.tex'
    entry.write_text(r'\documentclass{article}\begin{document}Hello current.\end{document}')
    output = tmp_path / '.latex-review' / 'live'
    output.mkdir(parents=True)
    source = ProjectSource('new', tmp_path, 'main.tex', ComparisonSource('directory', str(tmp_path)))
    build(source, output)
    html = (output / 'report.html').read_text()
    assert 'Hello current.' in html
    assert '当前论文' in html
    assert '修改前' not in html
    assert 'data-side="old"' not in html
    assert 'live-status.json' in html
    entry.write_text(entry.read_text().replace('Hello current.', 'Updated current.'))
    build(source, output, 2)
    html = (output / 'report.html').read_text()
    assert 'Updated current.' in html
    assert 'Hello current.' not in html
    assert 'let revision = 2;' in html
    assert not (output / 'diff.json').exists()


def test_fingerprint_tracks_dependencies_and_ignores_output(tmp_path):
    output = tmp_path / 'preview'
    output.mkdir()
    (tmp_path / 'main.tex').write_text('main')
    initial = fingerprint(tmp_path, output)
    (output / 'report.html').write_text('generated')
    assert fingerprint(tmp_path, output) == initial
    chapter = tmp_path / 'chapter.tex'
    chapter.write_text('chapter')
    added = fingerprint(tmp_path, output)
    assert added != initial
    chapter.write_text('changed')
    assert fingerprint(tmp_path, output) != added
    chapter.unlink()
    assert fingerprint(tmp_path, output) == initial
