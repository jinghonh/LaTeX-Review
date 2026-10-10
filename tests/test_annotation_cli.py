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
    assert call(report, capsys, '--content')[1] == report[1]
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
