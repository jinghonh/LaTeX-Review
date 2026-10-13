from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import os
import time

import pytest

from latex_review import pdf_cache
from latex_review.report import _pdf_preview
from latex_review.visual import _png, _render_page


def setup(tmp_path):
    output = tmp_path / 'paper/.latex-review/report'
    output.mkdir(parents=True)
    pdf = tmp_path / 'input.pdf'; pdf.write_bytes(b'%PDF-one')
    converter = tmp_path / 'converter'
    converter.write_text('#!/bin/sh\necho version-one\n'); converter.chmod(0o755)
    return output, pdf, converter


def test_content_parameters_tool_and_paper_scope(tmp_path):
    output, pdf, converter = setup(tmp_path)
    calls = []
    def produce(snapshot):
        calls.append(snapshot.read_bytes()); return b'result'
    def get(**params):
        return pdf_cache.converted(pdf, output, str(converter), params, produce, lambda b: b == b'result')
    get(page=1, dpi=120, format='png', crop=None); get(page=1, dpi=120, format='png', crop=None)
    assert len(calls) == 1
    stamp = pdf.stat().st_mtime_ns
    pdf.write_bytes(b'%PDF-two'); os.utime(pdf, ns=(stamp, stamp))
    get(page=1, dpi=120, format='png', crop=None)
    for params in ({'page': 2}, {'dpi': 121}, {'format': 'pgm'}, {'crop': [1, 2, 3, 4]}):
        get(**params)
    converter.write_text('#!/bin/sh\necho version-two\n')
    get(page=1, dpi=120, format='png', crop=None)
    assert len(calls) == 7
    other = tmp_path / 'other/.latex-review/report'; other.mkdir(parents=True)
    pdf_cache.converted(pdf, other, str(converter), {}, produce, lambda b: True)
    assert len(calls) == 8


def test_corruption_failure_atomicity_and_snapshot(tmp_path):
    output, pdf, converter = setup(tmp_path)
    def good(snapshot):
        assert snapshot.read_bytes() == b'%PDF-one'
        return b'good'
    get = lambda produce: pdf_cache.converted(pdf, output, str(converter), {}, produce, lambda b: b == b'good')
    assert get(good) == b'good'
    entry = next(output.parent.joinpath('pdf-cache').glob('*.bin'))
    original = entry.read_bytes()
    pdf.write_bytes(b'%PDF-new')
    def fail(snapshot):
        raise OSError('failed conversion')
    with pytest.raises(OSError): get(fail)
    assert entry.read_bytes() == original
    assert len(list(entry.parent.glob('*.bin'))) == 1
    pdf.write_bytes(b'%PDF-one')
    entry.write_bytes(b'partial')
    assert get(good) == b'good'
    entry.write_bytes(sha256(b'bad').hexdigest().encode()+b'\n'+b'bad')
    assert get(good) == b'good'
    assert not list(entry.parent.glob('.tmp-*'))


def test_concurrent_miss_single_conversion(tmp_path):
    output, pdf, converter = setup(tmp_path); calls = []
    def produce(snapshot):
        calls.append(1); time.sleep(.05); return b'good'
    def get(_): return pdf_cache.converted(pdf, output, str(converter), {}, produce, lambda b: True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(get, range(6))) == [b'good'] * 6
    assert len(calls) == 1


def test_cleanup_limits_age_and_partial_directories(tmp_path, monkeypatch):
    output, pdf, converter = setup(tmp_path)
    monkeypatch.setattr(pdf_cache, 'MAX_ENTRIES', 2)
    monkeypatch.setattr(pdf_cache, 'MAX_BYTES', 140)
    for page in range(6):
        pdf_cache.converted(pdf, output, str(converter), {'page': page}, lambda _: b'ok', lambda _: True)
    cache = output.parent / 'pdf-cache'
    assert len(list(cache.glob('*.bin'))) <= 2
    assert sum(p.stat().st_size for p in cache.glob('*.bin')) <= 140
    orphan = cache / '.tmp-orphan'; orphan.mkdir(); (orphan/'partial').write_bytes(b'bad')
    for p in cache.glob('*.bin'): os.utime(p, (1, 1))
    pdf_cache.converted(pdf, output, str(converter), {'page': 7}, lambda _: b'ok', lambda _: True)
    assert not orphan.exists()
    assert len(list(cache.glob('*.bin'))) == 1


def test_preview_and_raster_cache(tmp_path, monkeypatch):
    output, pdf, converter = setup(tmp_path)
    import latex_review.report as report
    import latex_review.visual as visual
    png = _png(1, 1, b'\xff'); calls = []
    def preview(source, directory, relative, converters, timeout, page):
        calls.append(page); path = directory / relative; path.write_bytes(png); return path, ''
    monkeypatch.setattr(report, '_pdf_preview_uncached', preview)
    for side in ('old', 'new'):
        path, reason = _pdf_preview(pdf, output, Path(f'previews/{side}.png'), (('pdftoppm', str(converter)),), 8, 1)
        assert path.read_bytes() == png and not reason
    assert calls == [1]
    runs = []
    monkeypatch.setattr(visual, '_run', lambda command: runs.append(command) or b'P5\n1 1\n255\n\xff')
    assert _render_page(pdf, 1, str(converter), 96, output) == (1, 1, b'\xff')
    _render_page(pdf, 1, str(converter), 96, output)
    _render_page(pdf, 1, str(converter), 900, output)
    assert len(runs) == 2


def test_invalid_png_and_symlink_cache(tmp_path):
    output, pdf, converter = setup(tmp_path)
    png = _png(1, 1, b'\xff')
    assert pdf_cache.valid_png(png)
    assert not pdf_cache.valid_png(png[:-1])
    assert not pdf_cache.valid_png(png+b'extra')
    assert not pdf_cache.valid_png(png[:25]+b'bad'+png[28:])
    foreign = tmp_path / 'foreign'; foreign.mkdir()
    (output.parent / 'pdf-cache').symlink_to(foreign, target_is_directory=True)
    with pytest.raises(OSError):
        pdf_cache.converted(pdf, output, str(converter), {}, lambda _: png, pdf_cache.valid_png)
    assert list(foreign.iterdir()) == []


def test_report_publication_and_single_file_keep_resources(tmp_path, monkeypatch):
    from latex_review import resolve_sources, parse_project, compare_projects, write_report
    from latex_review.delivery import prepare_publication
    import latex_review.report as report
    output, _, converter = setup(tmp_path)
    old, new = tmp_path/'old', tmp_path/'new'
    for root in (old, new):
        root.mkdir(); (root/'chart.pdf').write_bytes(b'%PDF-fixture')
        (root/'main.tex').write_text(r'\section{Test}Text.\begin{figure}\includegraphics{chart.pdf}\caption{Chart}\end{figure}')
    calls = []
    png = _png(1, 1, b'\xff')
    def preview(source, directory, relative, converters, timeout, page):
        calls.append(source.read_bytes()); path=directory/relative; path.write_bytes(png); return path, ''
    monkeypatch.setattr(report, '_pdf_preview_uncached', preview)
    for revision in range(2):
        if revision: (new/'main.tex').write_text((new/'main.tex').read_text()+' Body update.')
        with resolve_sources(entry='main.tex', old_dir=old, new_dir=new) as pair:
            before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
            result = write_report(before, after, compare_projects(before, after), output,
                                  pdf_converter=str(converter), single_file=True)
        publication = prepare_publication(output, 'paper')
        assert len(publication.resources) == 4
        assert all(name.startswith(('assets/', 'previews/')) for name in publication.resources)
        assert png in publication.resources.values()
        assert b'%PDF-fixture' in publication.resources.values()
        single = result.single_html.read_text()
        assert 'data:image/png;base64,' in single
        assert 'data:application/pdf;base64,' in single
        assert 'pdf-cache/' not in single
    assert calls == [b'%PDF-fixture']
    monkeypatch.setattr(pdf_cache, 'MAX_ENTRIES', 0)
    pdf_cache.converted(old/'chart.pdf', output, str(converter), {}, lambda _: b'ok', lambda _: True)
    assert not list(output.parent.joinpath('pdf-cache').glob('*.bin'))
    assert (output/'previews/new/chart.pdf.page-1.png').read_bytes() == png
