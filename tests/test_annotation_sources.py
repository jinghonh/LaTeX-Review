"""Original-source mapping against actual parsing and preview extraction."""
from hashlib import sha256

import pytest

from latex_review.annotation_sources import enrich_content
from latex_review.annotations import build_content, _PreviewParser, _Element
from latex_review.contract import ComparisonSource
from latex_review.preview import render_preview
from latex_review.sources import ProjectSource
from latex_review.structure import parse_project


def content(root, body, *, kind='directory', include=None):
    root.mkdir(exist_ok=True)
    (root / 'main.tex').write_text(r'\documentclass{article}\begin{document}' + body + r'\end{document}')
    if include:
        (root / 'part.tex').write_text(include)
    project = parse_project(ProjectSource('new', root, 'main.tex', ComparisonSource(kind, str(root))).expand())
    html = render_preview(None, project).html
    result = build_content(project, html)
    # Exercise the helper before build_content is wired to the new attributes.
    parser = _PreviewParser()
    parser.feed(html)
    elements = {}

    def visit(element):
        owner = element
        while owner and 'data-node-id' not in owner.attrs:
            owner = owner.parent
        if owner:
            elements[element.selector()] = element
        for child in element.children:
            if isinstance(child, _Element):
                visit(child)

    visit(parser.root)
    for unit in result['units']:
        attrs = elements[unit['selector']].attrs
        role = attrs.get('data-annotation-subunit') or ('caption' if elements[unit['selector']].tag == 'figcaption' or 'table-caption' in attrs.get('class', '').split() else None)
        if role:
            unit['subunit'] = {'type': role}
            for attr, key in [('data-row', 'row'), ('data-column', 'column'), ('data-table-index', 'table_index')]:
                if attr in attrs:
                    unit['subunit'][key] = int(attrs[attr])
    enrich_content(project, result['units'], root)
    return result


def assert_span(root, unit):
    span = unit['source_span']
    assert span['reliable']
    source = (root / span['file']).read_text()
    assert source[span['start']:span['end']] == span['original_chunk'] == unit['raw_latex']
    assert span['sha256'] == sha256(source.encode()).hexdigest()
    assert unit['source']['start_line'] == source[:span['start']].count('\n') + 1


def test_simple_caption_cells_repeated_text_and_body(tmp_path):
    result = content(tmp_path, 'Same.\n\n' + r'Other $x$.' + '\n\n' +
                     r'\begin{table}\caption{Same}\begin{tabular}{cc}H & H\\ Same & Same\\\end{tabular}\end{table}')
    for unit in result['units']:
        assert_span(tmp_path, unit)
    cells = [u for u in result['units'] if u.get('subunit', {}).get('type') in ('header', 'cell')]
    assert len(cells) == 4
    assert len({u['source_span']['start'] for u in cells}) == 4
    assert [u['subunit']['column'] for u in cells] == [1, 2, 1, 2]
    assert [u['raw_latex'] for u in cells] == ['H', 'H', 'Same', 'Same']
    assert all(u['kind'] == 'text' for u in cells)
    caption = next(u for u in result['units'] if u.get('subunit', {}).get('type') == 'caption')
    assert caption['raw_latex'] == 'Same'


def test_include_original_file_and_git_read_only(tmp_path):
    result = content(tmp_path, r'\input{part}', include='Included.\n\n' +
                     r'\begin{figure}\caption{Included caption}\end{figure}', kind='git')
    for unit in result['units']:
        assert_span(tmp_path, unit)
        assert unit['source_span']['file'] == 'part.tex'
        assert not unit['source_span']['writable']


@pytest.mark.parametrize('cell', [r'\multicolumn{2}{c}{Same}', r'\multirow{2}{*}{Same}', r'\custom{Same}'])
def test_complex_cells_explicitly_degrade(tmp_path, cell):
    continuation = r' & Same' if cell.startswith(r'\multirow') else r' Same & Same'
    result = content(tmp_path, r'\begin{table}\caption{Simple}\begin{tabular}{cc}' + cell +
                     r' & Other\\' + continuation + r'\\\end{tabular}\end{table}')
    cells = [u for u in result['units'] if u.get('subunit', {}).get('type') in ('cell', 'header')]
    assert cells
    assert all(not u['source_span']['reliable'] and not u['source_span']['writable'] for u in cells)
    caption = next(u for u in result['units'] if u.get('subunit', {}).get('type') == 'caption')
    assert_span(tmp_path, caption)


def test_cross_file_owner_degrades(tmp_path):
    result = content(tmp_path, r'\begin{table}\caption{Caption}\input{part}\end{table}',
                     include=r'\begin{tabular}{cc}A & B\\\end{tabular}')
    assert result['units']
    assert all(not u['source_span']['reliable'] for u in result['units'])


def test_strict_locator_uses_saved_include_locations_without_reading_disk(tmp_path):
    from types import SimpleNamespace
    from latex_review.annotation_cli import find_selection
    result = content(tmp_path, '\nSame.\n\n' + r'\input{part}', include='Same.', kind='git')
    unit = next(u for u in result['units'] if u['source']['file'] == 'part.tex')
    (tmp_path / 'part.tex').unlink()
    selection = find_selection(result, SimpleNamespace(file='part.tex', line=unit['source']['start_line'],
                                                       quote='Same.', formula=False, unit_id=None, occurrence=None))
    assert selection['unit_id'] == unit['id']


def test_strict_locator_preserves_ambiguity_within_same_source_line(tmp_path):
    from types import SimpleNamespace
    from latex_review.annotation_cli import find_selection, SelectionError
    result = content(tmp_path, 'Same Same.')
    with pytest.raises(SelectionError) as caught:
        find_selection(result, SimpleNamespace(file='main.tex', line=1, quote='Same', formula=False, unit_id=None, occurrence=None))
    assert len(caught.value.candidates) == 2
