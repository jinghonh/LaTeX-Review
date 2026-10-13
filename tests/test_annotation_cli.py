"""High-level CLI behavior against real preview manifests and shared storage."""
import json

import pytest

from latex_review.agent_tools import annotations_main
from latex_review.annotations import AnnotationStore, MANIFEST, utf16_length
from test_annotations import content_at


@pytest.fixture
def report(tmp_path):
    content, _ = content_at(tmp_path / 'paper', '😀 Alpha beta Alpha.' + '\n\n' +
                           r'Other Alpha beta. Formula $x+1$ end.')
    directory = tmp_path / 'report'
    directory.mkdir()
    (directory / MANIFEST).write_text(json.dumps(content))
    return directory, content, AnnotationStore.for_content(content)


def call(report, capsys, *args):
    if args and args[0] in ('add', 'reply', 'edit', 'delete', 'resolve'):
        args = (*args, '--full')
    code = annotations_main([str(report[0]), *args])
    return code, json.loads(capsys.readouterr().out)


def test_ambiguous_quote_no_write_and_explicit_utf16_selection(report, capsys):
    code, value = call(report, capsys, 'add', '--quote', 'Alpha', '--body', 'Note', '--author', 'Codex')
    assert code == 4 and len(value['candidates']) == 3
    assert report[2].read(report[1])['revision'] == 0
    first = value['candidates'][0]['selection']
    assert first['start'] == utf16_length('😀 ')
    code, value = call(report, capsys, 'add', '--quote', 'Alpha', '--occurrence', '2',
                       '--body', 'Note', '--author', 'Codex')
    assert code == 0
    anchor = value['threads'][0]['original_anchor']
    assert anchor['start'] == utf16_length('😀 Alpha beta ')
    code, value = call(report, capsys, 'add', '--quote', ' Alpha\n\t beta ',
                       '--unit-id', first['unit_id'], '--body', 'Normalized', '--author', 'Agent')
    assert code == 0 and value['threads'][0]['original_anchor']['quote'] == 'Alpha beta'


def test_reply_nested_edit_resolve_and_delete(report, capsys):
    code, value = call(report, capsys, 'add', '--quote', 'Other Alpha beta.', '--body', 'Initial',
                       '--author', 'Codex', '--category', 'question')
    assert code == 0
    tid, initial = value['result']['thread_id'], value['result']['message_id']
    code, value = call(report, capsys, 'reply', tid, '--body', 'First reply', '--author', 'Agent',
                       '--category', 'suggestion')
    assert code == 0
    rid = value['result']['message_id']
    assert value['threads'][0]['messages'][-1]['reply_to'] == initial
    code, value = call(report, capsys, 'reply', tid, '--body', 'Nested reply', '--author', 'Other',
                       '--parent-message-id', rid)
    assert code == 0 and value['threads'][0]['messages'][-1]['reply_to'] == rid
    code, value = call(report, capsys, 'edit', tid, rid, '--body', 'Edited reply')
    edited = value['threads'][0]['messages'][1]
    assert code == 0 and edited['body'] == 'Edited reply'
    assert edited['category'] == 'suggestion' and edited['author']['name'] == 'Agent'
    code, value = call(report, capsys, 'edit', tid, rid, '--body', 'Reclassified', '--category', 'note')
    assert code == 0 and value['threads'][0]['messages'][1]['category'] == 'note'
    code, value = call(report, capsys, 'resolve', tid)
    assert code == 0 and value['threads'][0]['resolved']
    revision = value['revision']
    code, _ = call(report, capsys, 'delete', tid)
    assert code == 4 and report[2].read(report[1])['revision'] == revision
    code, _ = call(report, capsys, 'delete', tid, '--message-id', rid, '--yes')
    assert code == 0 and report[2].export()['threads'][tid]['messages'][1]['deleted_at']
    code, _ = call(report, capsys, 'delete', tid, '--yes')
    assert code == 0 and report[2].export()['threads'][tid]['deleted_at']


@pytest.mark.parametrize('by_id', [False, True])
def test_complete_formula_selection(report, capsys, by_id):
    formula = next(u for u in report[1]['units'] if u['kind'] == 'formula')
    locator = ['--unit-id', formula['id']] if by_id else ['--quote', '$x+1$']
    code, value = call(report, capsys, 'add', '--formula', *locator, '--body', 'Formula note', '--author', 'Codex')
    assert code == 0
    anchor = value['threads'][0]['original_anchor']
    assert anchor['unit_id'] == formula['id'] and anchor['start'] is None


@pytest.mark.parametrize('locator', [[], ['--quote', ' '], ['--quote', 'missing'],
                                    ['--formula', '--quote', 'x+1'],
                                    ['--quote', 'Alpha', '--occurrence', '0'],
                                    ['--quote', 'Alpha', '--occurrence', '4'],
                                    ['--quote', 'Alpha', '--unit-id', 'missing']])
def test_invalid_location_does_not_write(report, capsys, locator):
    code, value = call(report, capsys, 'add', *locator, '--body', 'Note', '--author', 'Codex')
    assert code == 4 and 'candidates' in value
    assert report[2].read(report[1])['revision'] == 0


@pytest.mark.parametrize('command', [['add', '--quote', 'Other Alpha beta.', '--body', 'Note'],
                                     ['reply', 'thread', '--body', 'Note']])
def test_author_required(report, capsys, command):
    with pytest.raises(SystemExit) as exc:
        annotations_main([str(report[0]), *command])
    assert exc.value.code == 2 and not report[2].path.exists()
    capsys.readouterr()


def test_conflict_not_retried(report, capsys, monkeypatch):
    original = AnnotationStore.dispatch
    calls = []

    def concurrent_write(store, content, request):
        calls.append(request)
        competing = {**request, 'body': 'Concurrent note'}
        original(store, content, competing)
        return original(store, content, request)

    monkeypatch.setattr(AnnotationStore, 'dispatch', concurrent_write)
    code, value = call(report, capsys, 'add', '--quote', 'Other Alpha beta.', '--body', 'Note', '--author', 'Codex')
    assert code == 4 and '其他审阅者' in value['error'] and len(calls) == 1
    state = report[2].read(report[1])
    assert state['revision'] == 1 and state['threads'][0]['messages'][0]['body'] == 'Concurrent note'


def test_legacy_modes_preserved_and_cannot_mix(report, capsys):
    full = call(report, capsys, '--content', '--full')[1]
    assert full['units'] == report[1]['units']
    assert call(report, capsys)[1]['revision'] == 0
    assert call(report, capsys, '--export')[0] == 0
    code, value = call(report, capsys, '--content', 'add', '--quote', 'Other Alpha beta.',
                       '--body', 'Note', '--author', 'Codex')
    assert code == 4 and '子命令不能' in value['error']


@pytest.mark.parametrize('operation', ['reply', 'edit', 'delete', 'resolve'])
def test_invalid_message_or_thread_leaves_revision_unchanged(report, capsys, operation):
    _, created = call(report, capsys, 'add', '--quote', 'Other Alpha beta.', '--body', 'Initial', '--author', 'Codex')
    tid = created['result']['thread_id']
    arguments = {
        'reply': [tid, '--body', 'Reply', '--author', 'Codex', '--parent-message-id', 'missing'],
        'edit': [tid, 'missing', '--body', 'Edited'],
        'delete': [tid, '--message-id', 'missing', '--yes'],
        'resolve': ['missing'],
    }
    code, value = call(report, capsys, operation, *arguments[operation])
    assert code == 4 and '不存在' in value['error']
    assert report[2].read(report[1])['revision'] == 1


def test_repeated_formulas_return_candidates_before_write(tmp_path, capsys):
    content, _ = content_at(tmp_path / 'paper', 'First $x+1$.' + '\n\n' + 'Second $x+1$.')
    directory = tmp_path / 'report'
    directory.mkdir()
    (directory / MANIFEST).write_text(json.dumps(content))
    report = directory, content, AnnotationStore.for_content(content)
    code, value = call(report, capsys, 'add', '--formula', '--quote', '$x+1$', '--body', 'Note', '--author', 'Codex')
    assert code == 4 and len(value['candidates']) == 2
    assert not report[2].path.exists()
    code, value = call(report, capsys, 'add', '--formula', '--quote', '$x+1$', '--occurrence', '2',
                       '--body', 'Note', '--author', 'Codex')
    assert code == 0 and value['threads'][0]['original_anchor']['order'] == 3


def test_default_write_summary_and_filtered_read(report, capsys):
    code = annotations_main([str(report[0]), 'add', '--quote', 'Other Alpha beta.',
                             '--body', 'Initial', '--author', 'Codex'])
    summary = json.loads(capsys.readouterr().out)
    assert code == 0 and summary['success'] and summary['thread_id']
    assert 'threads' not in summary and summary['request_id'] is None
    code = annotations_main([str(report[0]), 'edit', summary['thread_id'], summary['message_id'], '--body', 'Updated'])
    edited = json.loads(capsys.readouterr().out)
    assert code == 0 and edited['thread_id'] == summary['thread_id'] and edited['message_id'] == summary['message_id']
    code = annotations_main([str(report[0]), 'resolve', summary['thread_id']])
    resolved = json.loads(capsys.readouterr().out)
    assert code == 0 and resolved['thread_id'] == summary['thread_id'] and 'threads' not in resolved
    code, value = call(report, capsys, '--thread-id', summary['thread_id'], '--author', 'Codex',
                       '--fields', 'id,resolved', '--limit', '1')
    assert code == 0 and value['total_matched'] == 1
    assert value['threads'] == [{'id': summary['thread_id'], 'resolved': True}]
    code, value = call(report, capsys, '--content', '--kind', 'formula', '--summary')
    assert code == 0 and value['total_matched'] == 1
    assert 'raw_latex' not in value['units'][0]


def test_inline_formula_quote_and_wrong_formula_diagnostics(report, capsys):
    code, value = call(report, capsys, 'add', '--quote', r'Formula \( x + 1 \) end.',
                       '--body', 'Math', '--author', 'Codex')
    assert code == 0
    anchor = value['threads'][0]['original_anchor']
    assert anchor['quote'] == 'Formula \ufffc end.'
    revision = value['revision']
    code, value = call(report, capsys, 'add', '--quote', 'Formula $y+1$ end.',
                       '--body', 'Wrong', '--author', 'Codex')
    assert code == 4 and 0 < len(value['candidates']) <= 3
    assert {'distance', 'context', 'diff', 'source'} <= value['candidates'][0].keys()
    assert report[2].read(report[1])['revision'] == revision


def test_multiple_atoms_exact_formula_check(tmp_path, capsys):
    content, _ = content_at(tmp_path / 'paper', r'😀 Same $x+1$ and $y+2$. Same $y+1$ and $x+2$.')
    directory = tmp_path / 'report'
    directory.mkdir()
    (directory / MANIFEST).write_text(json.dumps(content))
    report = directory, content, AnnotationStore.for_content(content)
    code, value = call(report, capsys, 'add', '--quote', 'Same $x + 1$ and $y + 2$.',
                       '--body', 'Exact', '--author', 'Codex')
    assert code == 0
    assert value['threads'][0]['original_anchor']['start'] == utf16_length('😀 ')
    code, value = call(report, capsys, 'add', '--quote', 'Same $x+1$ and $x+2$.',
                       '--body', 'Wrong', '--author', 'Codex')
    assert code == 4


def test_low_level_request_keeps_full_result(report, capsys, tmp_path):
    unit = next(u for u in report[1]['units'] if u['kind'] == 'text')
    request = {'project_id': report[1]['project_id'], 'version_id': report[1]['version_id'],
               'request': {'operation': 'create', 'expected_revision': 0,
                           'selection': {'unit_id': unit['id'], 'start': utf16_length('😀 '),
                                         'end': utf16_length('😀 Alpha'), 'quote': 'Alpha'},
                           'body': 'Low level', 'author': {'name': 'Codex', 'type': 'agent'},
                           'category': 'note'}}
    path = tmp_path / 'request.json'
    path.write_text(json.dumps(request))
    code, value = call(report, capsys, '--request', str(path))
    assert code == 0 and value['result']['thread_id'] and len(value['threads']) == 1


def test_formula_normalization_preserves_meaning():
    from latex_review.quote_matching import normalize_formula
    assert normalize_formula('$x + 1$') == normalize_formula(r'\(x+1\)')
    assert normalize_formula(r'$\alpha x$') != normalize_formula(r'$\alphax$')
    assert normalize_formula(r'$\text{a b}$') != normalize_formula(r'$\text{ab}$')


def test_metadata_summary_filters_and_backup_round_trip(report, capsys):
    code, created = call(report, capsys, 'add', '--quote', 'Other Alpha beta.',
                         '--body', '\nLegacy title\nLong details', '--author', 'Codex')
    assert code == 0
    tid = created['result']['thread_id']
    code, summary = call(report, capsys, '--summary')
    item = summary['threads'][0]
    assert item['title'] == 'Legacy title' and item['author'] == 'Codex' and item['tags'] == []
    assert set(item) == {'id', 'title', 'author', 'tags', 'resolved', 'updated_at'}
    code, _ = call(report, capsys, 'metadata', tid, '--title', 'Explicit title', '--tag', 'non-experiment', '--tag', 'review')
    assert code == 0
    code, summary = call(report, capsys, '--summary', '--tag', 'non-experiment', '--tag', 'review', '--status', 'unresolved')
    assert summary['total_matched'] == 1 and summary['threads'][0]['title'] == 'Explicit title'
    assert call(report, capsys, '--summary', '--tag', 'missing')[1]['total_matched'] == 0
    backup = report[2].export()
    assert report[2].dispatch(report[1], {'operation': 'import', 'expected_revision': 2, 'bundle': backup})['result']['duplicates'] == 1
    code, fields = call(report, capsys, '--fields', 'id,title,author,tags')
    assert fields['threads'][0]['tags'] == ['non-experiment', 'review']
    code, _ = call(report, capsys, 'metadata', tid, '--title', '', '--clear-tags')
    assert code == 0
    assert call(report, capsys, '--summary')[1]['threads'][0]['title'] == 'Legacy title'
    assert call(report, capsys, '--summary')[1]['threads'][0]['tags'] == []


@pytest.mark.parametrize('options', [[], ['--tag', 'duplicate', '--tag', 'duplicate'], ['--title', '界' * 171]])
def test_invalid_metadata_does_not_write(report, capsys, options):
    _, created = call(report, capsys, 'add', '--quote', 'Other Alpha beta.', '--body', 'Initial', '--author', 'Codex')
    tid = created['result']['thread_id']
    code, _ = call(report, capsys, 'metadata', tid, *options)
    assert code == 4 and report[2].metadata(report[1])['revision'] == 1


def test_add_metadata_and_module_entry(report, capsys):
    import subprocess
    import sys
    code, created = call(report, capsys, 'add', '--quote', 'Other Alpha beta.', '--body', 'Initial',
                         '--author', 'Codex', '--title', 'Short title', '--tag', 'topic')
    assert code == 0 and created['threads'][0]['title'] == 'Short title'
    result = subprocess.run([sys.executable, '-m', 'latex_review.annotation_cli', str(report[0]), '--summary', '--tag', 'topic'],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['threads'][0]['title'] == 'Short title'
