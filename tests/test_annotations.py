"""Annotation contracts exercised against real parsed/rendered LaTeX projects."""
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from latex_review.annotations import (
    AnnotationConflict, AnnotationError, AnnotationStore, MANIFEST, build_content,
    execute_request, locate, make_anchor, overlapping_threads, query_threads, utf16_length,
)
from latex_review.agent_tools import annotations_main
from latex_review.contract import ComparisonSource
from latex_review.preview import render_preview
from latex_review.sources import ProjectSource
from latex_review.structure import parse_project
from latex_review.serve import create_server

AUTHOR = {'name': '审阅者', 'type': 'user'}


def content_at(root, body, *, old_body=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'main.tex').write_text(r'\documentclass{article}\begin{document}' + body + r'\end{document}', encoding='utf-8')
    source = ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root)))
    project = parse_project(source.expand())
    old = None
    if old_body is not None:
        old_root = root.parent / 'old'
        old_root.mkdir(exist_ok=True)
        (old_root / 'main.tex').write_text(r'\documentclass{article}\begin{document}' + old_body + r'\end{document}')
        old = parse_project(ProjectSource('old', old_root, 'main.tex', ComparisonSource('directory', str(old_root))).expand())
    preview = render_preview(old, project)
    return build_content(project, preview.html), preview.html


def select(content, quote, occurrence=0):
    units = [u for u in content['units'] if u['kind'] == 'text' and quote in u['text']]
    unit = units[occurrence]
    index = unit['text'].index(quote)
    return {'unit_id': unit['id'], 'start': utf16_length(unit['text'][:index]),
            'end': utf16_length(unit['text'][:index + len(quote)]), 'quote': quote}


def mutate(store, content, operation, **fields):
    return store.dispatch(content, {'operation': operation, 'expected_revision': store.read(content)['revision'], **fields})


def create(store, content, quote, **extra):
    return mutate(store, content, 'create', selection=select(content, quote), body='A question $x$ <script>bad</script>',
                  author=AUTHOR, category='question', **extra)


def test_actual_preview_coordinates_all_new_content_and_formula_atoms(tmp_path):
    content, html = content_at(tmp_path / 'paper', r'\section{Heading}Unchanged \textbf{bold} 😀 $x+1$ end.' + '\n\n' +
                               r'Other text.\begin{equation}a=b\label{eq:a}\end{equation}', old_body='Old only.')
    assert any('Unchanged bold 😀' in u['text'] for u in content['units'])
    assert not any('Old only' in u['text'] for u in content['units'])
    text = next(u for u in content['units'] if 'Unchanged' in u['text'])
    assert '\ufffc' in text['text'] and len(text['atoms']) == 1
    formula = next(u for u in content['units'] if u['kind'] == 'formula' and u['raw_latex'] == '$x+1$')
    display = next(u for u in content['units'] if u['kind'] == 'formula' and 'eq:a' in u['raw_latex'])
    assert 'data-annotation-latex' in html and display['labels'] == ['eq:a']
    anchor = make_anchor(content, select(content, '😀'))
    assert anchor['end'] - anchor['start'] == 2
    with pytest.raises(AnnotationError, match='Unicode'):
        make_anchor(content, {'unit_id': text['id'], 'start': anchor['start'], 'end': anchor['end'] - 1})
    assert make_anchor(content, {'unit_id': formula['id']})['quote'] == '$x+1$'
    for selection in ({'unit_id': formula['id'], 'start': 0, 'end': 1},
                      {**select(content, 'bold'), 'side': 'old'},
                      {**select(content, 'bold'), 'end_unit_id': display['id']}):
        with pytest.raises(AnnotationError):
            make_anchor(content, selection)
    with pytest.raises(AnnotationConflict):
        make_anchor(content, {**select(content, 'bold'), 'quote': 'stale'})
    # A text selection may include a complete inline formula atom, never its internal characters.
    span = make_anchor(content, select(content, '😀 \ufffc end.'))
    assert span['atoms'][0]['raw_latex'] == '$x+1$'


def test_leaf_blocks_do_not_merge_lists_captions_or_table_cells(tmp_path):
    content, _ = content_at(tmp_path, r'\begin{itemize}\item First item.\item Second item.\end{itemize}' +
                           r'\begin{figure}\caption{Figure caption.}\end{figure}' +
                           r'\begin{table}\caption{Table caption.}\begin{tabular}{cc}One & Two \\ Three & Four\end{tabular}\end{table}')
    texts = [u['text'] for u in content['units'] if u['kind'] == 'text']
    for expected in ('First item.', 'Second item.', 'Figure caption.', 'Table caption.', 'One', 'Two', 'Three', 'Four'):
        assert any(expected in text for text in texts), (expected, texts)
    assert not any('First item' in text and 'Second item' in text for text in texts)
    assert not any('One' in text and 'Two' in text for text in texts)


def test_formula_failures_keep_complete_original_latex(tmp_path):
    content, _ = content_at(tmp_path, r'Text $\href{bad}{x}$ after.\begin{equation}\href{bad}{y}\end{equation}')
    formulas = [u for u in content['units'] if u['kind'] == 'formula']
    assert len(formulas) == 2
    assert formulas[0]['raw_latex'] == r'$\href{bad}{x}$'
    assert '\\begin{equation}' in formulas[1]['raw_latex']


def test_lifecycle_overlap_authors_filters_and_persistence(tmp_path):
    content, _ = content_at(tmp_path, 'A sufficiently long unchanged sentence.')
    store = AnnotationStore.for_content(content)
    value = create(store, content, 'sufficiently long')
    tid, mid = value['result']['thread_id'], value['result']['message_id']
    create(store, content, 'long unchanged')
    reply = mutate(store, content, 'reply', thread_id=tid, body='Reply \\[y\\]',
                   author={'name': 'Codex', 'type': 'agent'}, category='suggestion')
    rid = reply['result']['message_id']
    mutate(store, content, 'edit', thread_id=tid, message_id=rid, body='Revised reply', category='note')
    value = mutate(store, content, 'resolve', thread_id=tid)
    resolved = query_threads(value, resolved=True, author_type='agent', category='note', search='REVISED')
    assert len(resolved) == 1 and resolved[0]['messages'][1]['edits'][0]['category'] == 'suggestion'
    anchor = make_anchor(content, select(content, 'long'))
    assert len(overlapping_threads(value, anchor['unit_id'], anchor['start'], anchor['end'])) == 2
    mutate(store, content, 'reopen', thread_id=tid)
    mutate(store, content, 'delete_message', thread_id=tid, message_id=rid)
    with pytest.raises(AnnotationError):
        mutate(store, content, 'edit', thread_id=tid, message_id=rid, body='x', category='note')
    restored = AnnotationStore.for_content(content).read(content)
    assert restored['threads'][0]['messages'][0]['id'] == mid
    assert '<script>' in restored['threads'][0]['messages'][0]['body']  # Data, never HTML.
    mutate(store, content, 'delete_thread', thread_id=tid)
    assert len(store.read(content)['threads']) == 1
    assert store.export()['threads'][tid]['deleted_at']


def test_version_identity_project_isolation_and_stale_writes(tmp_path):
    a, _ = content_at(tmp_path / 'a', 'Same text.')
    same, _ = content_at(tmp_path / 'a', 'Same text.', old_body='Different baseline.')
    b, _ = content_at(tmp_path / 'b', 'Same text.')
    assert a['project_id'] == same['project_id'] and a['version_id'] == same['version_id']
    assert a['project_id'] != b['project_id'] and a['version_id'] == b['version_id']
    store = AnnotationStore.for_content(a)
    create(store, a, 'Same')
    with pytest.raises(AnnotationError):
        store.read(b)
    with pytest.raises(AnnotationError):
        AnnotationStore.for_content(a, tmp_path / 'b')
    with pytest.raises(AnnotationConflict):
        store.dispatch(a, {'operation': 'resolve', 'expected_revision': 0, 'thread_id': 'any'})
    changed, _ = content_at(tmp_path / 'a', 'Changed text.')
    with pytest.raises(AnnotationConflict):
        execute_request(store, changed, {'project_id': a['project_id'], 'version_id': a['version_id'],
                                         'request': {'operation': 'reconcile', 'expected_revision': 1}})
    assert store.read(a)['threads'][0]['association']['status'] == 'exact'
    assert store.read(changed)['threads'][0]['association']['anchor'] is None


def test_exact_movement_fuzzy_change_ambiguity_low_confidence_and_deletion(tmp_path):
    root = tmp_path / 'paper'
    original, _ = content_at(root, 'A long selected passage describes a reliable measurement in this experiment.' + '\n\nOther paragraph.')
    anchor = make_anchor(original, select(original, 'selected passage describes a reliable measurement'))
    moved, _ = content_at(root, 'Other paragraph.\n\nA long selected passage describes a reliable measurement in this experiment.')
    assert locate(moved, anchor)['status'] == 'exact'
    fuzzy, _ = content_at(root, 'A long selected passage describes a reliable measurements in this experiment.')
    result = locate(fuzzy, anchor)
    assert result['status'] == 'auto_migrated' and result['anchor']['quote'].endswith('measurements')
    duplicate, _ = content_at(root, 'A long selected passage describes a reliable measurement in this experiment.\n\n' * 2)
    result = locate(duplicate, anchor)
    assert result['status'] == 'needs_relocation' and len(result['candidates']) == 2
    near, _ = content_at(root, 'A long selected passage describes a reliable measurements in this experiment.\n\n' +
                         'A long selected passage describes a reliable measurementz in this experiment.')
    assert locate(near, anchor)['status'] == 'needs_relocation'
    low, _ = content_at(root, 'A long selected passage describes an unreliable guess in another context.')
    result = locate(low, anchor)
    assert result['status'] == 'needs_relocation' and result['candidates']
    removed, _ = content_at(root, 'A completely unrelated remaining paragraph.')
    assert locate(removed, anchor)['status'] == 'needs_relocation'
    # A unique common token in unrelated prose is insufficient evidence for an exact migration.
    short = make_anchor(original, select(original, 'a'))
    unrelated, _ = content_at(root, 'a wholly different statement.')
    assert locate(unrelated, short)['status'] == 'needs_relocation'


def test_history_only_content_changes_manual_association_and_original_survives(tmp_path):
    root = tmp_path / 'paper'
    first, _ = content_at(root, 'A long selected passage describes a reliable measurement in this experiment.\n\nOther paragraph.')
    store = AnnotationStore.for_content(first)
    tid = create(store, first, 'selected passage describes a reliable measurement')['result']['thread_id']
    moved, _ = content_at(root, 'Other paragraph.\n\nA long selected passage describes a reliable measurement in this experiment.')
    result = mutate(store, moved, 'reconcile')['threads'][0]
    assert len(result['history']) == 1
    changed, _ = content_at(root, 'Other paragraph.\n\nA long selected passage describes a reliable measurements in this experiment.')
    result = mutate(store, changed, 'reconcile')['threads'][0]
    assert result['association']['status'] == 'auto_migrated' and len(result['history']) == 2
    mutate(store, changed, 'reconcile')
    assert len(store.read(changed)['threads'][0]['history']) == 2
    deleted, _ = content_at(root, 'Other paragraph.')
    result = mutate(store, deleted, 'reconcile')['threads'][0]
    assert result['association']['anchor'] is None and result['original_anchor']['quote'].endswith('measurement')
    assert len(result['history']) == 2
    result = mutate(store, deleted, 'associate', thread_id=tid, selection=select(deleted, 'Other paragraph.'))['threads'][0]
    assert result['association']['status'] == 'manual' and len(result['history']) == 3
    assert result['history'][-1]['method'] == 'manual'
    assert store.read(first)['threads'][0]['association']['anchor']['quote'].endswith('measurement')


def test_formula_matching_duplicate_and_manual_history(tmp_path):
    original, _ = content_at(tmp_path, r'\begin{equation}abcdefghijk=123456789\end{equation}')
    selection = {'unit_id': original['units'][0]['id']}
    anchor = make_anchor(original, selection)
    revised, _ = content_at(tmp_path, r'\begin{equation}abcdefghijk=123456780\end{equation}')
    assert locate(revised, anchor)['status'] == 'auto_migrated'
    duplicated, _ = content_at(tmp_path, r'\begin{equation}abcdefghijk=123456789\end{equation}' * 2)
    assert locate(duplicated, anchor)['status'] == 'needs_relocation'


def _concurrent_create(content, number):
    store = AnnotationStore.for_content(content)
    for _ in range(30):
        revision = store.read(content)['revision']
        try:
            store.dispatch(content, {'operation': 'create', 'expected_revision': revision,
                                     'selection': select(content, 'Concurrent'), 'body': str(number),
                                     'author': AUTHOR, 'category': 'note'})
            return True
        except AnnotationConflict:
            continue
    return False


def test_interprocess_writes_are_atomic_and_never_lose_threads(tmp_path):
    content, _ = content_at(tmp_path, 'Concurrent writes retain every thread.')
    with ProcessPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(_concurrent_create, [content] * 12, range(12)))
    store = AnnotationStore.for_content(content)
    snapshot = store.read(content)
    assert snapshot['revision'] == 12 and len(snapshot['threads']) == 12
    assert sorted(int(t['messages'][0]['body']) for t in snapshot['threads']) == list(range(12))
    assert not list(store.directory.glob('.annotations-*.tmp'))
    with pytest.raises(AnnotationConflict):
        store.dispatch(content, {'operation': 'create', 'expected_revision': 0,
                                'selection': select(content, 'Concurrent'), 'body': 'lost', 'author': AUTHOR, 'category': 'note'})


def test_import_deduplicates_preserves_tombstones_and_refuses_conflicts(tmp_path):
    content, _ = content_at(tmp_path, 'Import backup.')
    store = AnnotationStore.for_content(content)
    value = create(store, content, 'Import')
    tid, mid = value['result']['thread_id'], value['result']['message_id']
    mutate(store, content, 'delete_message', thread_id=tid, message_id=mid)
    backup = store.export()
    assert mutate(store, content, 'import', bundle=backup)['result']['duplicates'] == 1
    conflict = deepcopy(backup)
    conflict['threads'][tid]['messages'][0]['body'] = 'Conflicting content'
    before = store.export()
    with pytest.raises(AnnotationConflict):
        mutate(store, content, 'import', bundle=conflict)
    assert store.export() == before
    invalid = deepcopy(backup)
    invalid['threads'][tid]['original_anchor']['start'] = 999999
    with pytest.raises(AnnotationError):
        mutate(store, content, 'import', bundle=invalid)
    store.path.unlink()
    result = mutate(store, content, 'import', bundle=backup)
    assert result['result']['imported'] == [tid]
    assert result['threads'][0]['messages'][0]['deleted_at']


@pytest.mark.parametrize('fields', [
    {'author': {'name': '', 'type': 'user'}}, {'author': {'name': 'A', 'type': 'system'}},
    {'category': 'bogus'}, {'body': ''}, {'body': 'x' * (64 * 1024 + 1)},
    {'selection': {'unit_id': 'missing', 'start': 0, 'end': 1}},
    {'selection': {'unit_id': 'missing', 'start': True, 'end': 1}},
])
def test_invalid_writes_leave_store_unchanged(tmp_path, fields):
    content, _ = content_at(tmp_path, 'Valid content.')
    store = AnnotationStore.for_content(content)
    request = {'operation': 'create', 'expected_revision': 0, 'selection': select(content, 'Valid'),
               'body': 'note', 'author': AUTHOR, 'category': 'note', **fields}
    with pytest.raises(AnnotationError):
        store.dispatch(content, request)
    assert store.read(content)['revision'] == 0 and not store.path.exists()


def test_cli_and_real_http_use_same_version_checked_contract(tmp_path, capsys):
    content, html = content_at(tmp_path / 'paper', 'Interface content.')
    report = tmp_path / 'report'
    report.mkdir()
    (report / 'report.html').write_text(html)
    (report / MANIFEST).write_text(json.dumps(content))
    assert annotations_main([str(report), '--content']) == 0
    assert json.loads(capsys.readouterr().out)['version_id'] == content['version_id']
    request = {'project_id': content['project_id'], 'version_id': content['version_id'],
               'request': {'operation': 'create', 'expected_revision': 0, 'selection': select(content, 'Interface'),
                           'body': 'CLI annotation', 'author': AUTHOR, 'category': 'note'}}
    path = tmp_path / 'request.json'
    path.write_text(json.dumps(request))
    assert annotations_main([str(report), '--request', str(path)]) == 0
    tid = json.loads(capsys.readouterr().out)['result']['thread_id']
    with create_server(report) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            state = json.load(urlopen(base + '/api/annotations/status'))
            assert state['threads'][0]['id'] == tid and state['token']
            assert json.load(urlopen(base + '/api/annotations/content')) == content
            envelope = {**request, 'request': {'operation': 'reply', 'expected_revision': 1, 'thread_id': tid,
                                              'body': 'HTTP reply', 'author': {'name': 'Agent', 'type': 'agent'}, 'category': 'suggestion'}}
            def post(value, token=state['token'], origin=None):
                headers = {'Content-Type': 'application/json', 'X-LaTeX-Review-Token': token}
                if origin:
                    headers['Origin'] = origin
                return urlopen(Request(base + '/api/annotations/update', json.dumps(value).encode(), headers))
            assert len(json.load(post(envelope))['threads'][0]['messages']) == 2
            with pytest.raises(HTTPError) as exc:
                post(envelope)
            assert exc.value.code == 409
            for token, origin in (('', None), ('bad', None), (state['token'], 'https://evil.example')):
                with pytest.raises(HTTPError) as exc:
                    post(envelope, token, origin)
                assert exc.value.code == 403
            bad = {**envelope, 'version_id': '0' * 64}
            with pytest.raises(HTTPError) as exc:
                post(bad)
            assert exc.value.code == 409
            with pytest.raises(HTTPError) as exc:
                post({'invalid': True})
            assert exc.value.code == 400
            backup = json.load(urlopen(base + '/api/annotations/export'))
            assert len(backup['threads'][tid]['messages']) == 2
            # The running service reloads a new manifest; stale requests cannot land on it.
            new_content, _ = content_at(tmp_path / 'paper', 'Updated interface content.')
            (report / MANIFEST).write_text(json.dumps(new_content))
            with pytest.raises(HTTPError) as exc:
                post({**envelope, 'request': {**envelope['request'], 'expected_revision': 2}})
            assert exc.value.code == 409
        finally:
            server.shutdown()
            worker.join()
    assert annotations_main([str(report)]) == 0
    assert len(json.loads(capsys.readouterr().out)['threads'][0]['messages']) == 2


def test_formula_boilerplate_and_text_atoms_cannot_inflate_match_confidence(tmp_path):
    original, _ = content_at(tmp_path, r'Text $x=1$ after.\begin{equation}x=1\end{equation}')
    display = next(u for u in original['units'] if u['kind'] == 'formula' and 'begin' in u['raw_latex'])
    equation_anchor = make_anchor(original, {'unit_id': display['id']})
    text_anchor = make_anchor(original, select(original, 'Text \ufffc after.'))
    revised, _ = content_at(tmp_path, r'Text $y=2$ after.\begin{equation}y=2\end{equation}')
    assert locate(revised, equation_anchor)['status'] == 'needs_relocation'
    assert locate(revised, text_anchor)['status'] == 'needs_relocation'


def test_near_duplicate_candidates_inside_one_paragraph_are_not_auto_migrated(tmp_path):
    original, _ = content_at(tmp_path, 'We measured a reliable baseline score and later measured a reliable baseline value.')
    anchor = make_anchor(original, select(original, 'a reliable baseline score'))
    revised, _ = content_at(tmp_path, 'We measured a reliable baseline scores and later measured a reliable baseline scorez.')
    result = locate(revised, anchor)
    assert result['status'] == 'needs_relocation'
    assert len(result['candidates']) >= 2


def test_failed_atomic_replace_retains_existing_file_and_cleans_temporary(tmp_path, monkeypatch):
    content, _ = content_at(tmp_path, 'Atomic update.')
    store = AnnotationStore.for_content(content)
    tid = create(store, content, 'Atomic')['result']['thread_id']
    before = store.path.read_bytes()
    def fail(*args):
        raise OSError('simulated disk failure')
    monkeypatch.setattr('latex_review.annotations.os.replace', fail)
    with pytest.raises(OSError):
        mutate(store, content, 'resolve', thread_id=tid)
    assert store.path.read_bytes() == before
    assert not list(store.directory.glob('.annotations-*.tmp'))


def test_report_and_live_share_coordinates_and_disk_store(tmp_path):
    from latex_review.comparison import compare_projects
    from latex_review.report import write_report
    from latex_review.live import build
    from latex_review.annotations import load_content
    old_root, new_root = tmp_path / 'old', tmp_path / 'new'
    for root, text in ((old_root, 'First original sentence. Second unchanged sentence $x$.'),
                       (new_root, 'First updated sentence. Second unchanged sentence $x$.')):
        content_at(root, text)
    old = parse_project(ProjectSource('old', old_root, 'main.tex', ComparisonSource('directory', str(old_root))).expand())
    source = ProjectSource('new', new_root, 'main.tex', ComparisonSource('directory', str(new_root)))
    new = parse_project(source.expand())
    report = write_report(old, new, compare_projects(old, new), tmp_path / 'report', pdf_converter='')
    report_content = load_content(report.directory)
    store = AnnotationStore.for_content(report_content)
    create(store, report_content, 'Second unchanged sentence')
    live = tmp_path / 'live'
    live.mkdir()
    build(source, live)
    live_content = load_content(live)
    assert live_content['project_id'] == report_content['project_id']
    assert live_content['version_id'] == report_content['version_id']
    assert [(u['id'], u['text']) for u in live_content['units']] == [(u['id'], u['text']) for u in report_content['units']]
    state = store.read(live_content)
    assert state['threads'][0]['association']['anchor']['quote'] == 'Second unchanged sentence'
    assert state['threads'][0]['association']['anchor']['change_ids'] == []
    # Same-version navigation metadata from another report must round-trip in backups.
    mutate(store, live_content, 'resolve', thread_id=state['threads'][0]['id'])
    mutate(store, live_content, 'import', bundle=store.export())


def test_report_output_cannot_remove_shared_annotation_storage(tmp_path):
    from latex_review.cli import ConfigurationError, _check_output_separate
    content, _ = content_at(tmp_path, 'Retain annotations.')
    store = AnnotationStore.for_content(content)
    create(store, content, 'Retain')
    before = store.path.read_bytes()
    options = {'old_dir': str(tmp_path), 'new_dir': str(tmp_path), 'entry': 'main.tex'}
    for output in (tmp_path / '.latex-review', store.directory, store.directory / 'report'):
        with pytest.raises(ConfigurationError, match='批注存储'):
            _check_output_separate(output, options)
    _check_output_separate(tmp_path / '.latex-review' / 'latest', options)
    assert store.path.read_bytes() == before


def test_source_fallback_removes_stale_coordinates_but_retains_threads(tmp_path):
    from latex_review.report import write_source_fallback
    root = tmp_path / 'paper'
    content, _ = content_at(root, 'Original body.')
    store = AnnotationStore.for_content(content)
    create(store, content, 'Original')
    before = store.path.read_bytes()
    report = tmp_path / 'report'
    report.mkdir()
    (report / MANIFEST).write_text(json.dumps(content))
    new = ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root))).expand()
    old = ProjectSource('old', root, 'main.tex', ComparisonSource('directory', str(root))).expand()
    write_source_fallback(old, new, report, parse_failures=[('new', 'test failure')])
    assert not (report / MANIFEST).exists()
    assert store.path.read_bytes() == before


def test_reply_parent_identity_persistence_and_validation(tmp_path):
    content, _ = content_at(tmp_path, 'A sufficiently long unchanged sentence.')
    store = AnnotationStore.for_content(content)
    result = create(store, content, 'sufficiently long')['result']
    tid, root = result['thread_id'], result['message_id']
    def reply(parent):
        return mutate(store, content, 'reply', thread_id=tid, reply_to=parent,
                      body='Same author', author=AUTHOR, category='note')['result']['message_id']
    first, second = reply(root), reply(root)
    child = reply(first)
    mutate(store, content, 'edit', thread_id=tid, message_id=child, body='No mention', category='note')
    messages = AnnotationStore.for_content(content).read(content)['threads'][0]['messages']
    assert messages[-1]['reply_to'] == first and messages[-1]['reply_to'] != second
    bundle = store.export()
    assert mutate(store, content, 'import', bundle=bundle)
    before = store.export()
    with pytest.raises(AnnotationError):
        reply('missing')
    assert store.export() == before
    invalid = deepcopy(bundle)
    invalid['threads'][tid]['messages'][1]['reply_to'] = child
    with pytest.raises(AnnotationError):
        mutate(store, content, 'import', bundle=invalid)
