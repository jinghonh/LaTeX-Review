import json
from latex_review.agent_tools import annotations_main
from latex_review.annotations import AnnotationStore, MANIFEST
from test_annotations import content_at
from test_annotations import envelope


def test_batch_partial_preflight_retry_audit_and_external_changes(tmp_path, capsys):
    content, _ = content_at(tmp_path / 'paper', 'First $x$ sentence.\n\nSecond sentence.')
    report = tmp_path / 'report'
    report.mkdir()
    (report / MANIFEST).write_text(json.dumps(content))
    items = [{'id': 'one', 'operation': 'add', 'quote': 'First $x$ sentence.', 'body': 'Check formula.'},
             {'id': 'bad', 'operation': 'add', 'quote': 'absent', 'body': 'Missing.'}]
    batch = tmp_path / 'batch.json'
    batch.write_text(json.dumps(items))
    arguments = [str(report), 'batch', str(batch), '--request-id', 'review-1', '--author', 'Codex']
    assert annotations_main([*arguments, '--dry-run']) == 4
    output = capsys.readouterr()
    preview = json.loads(output.out)
    assert '预检 2/2' in output.err
    store = AnnotationStore.open(content)
    assert preview['dry_run'] and store.read()['revision'] == 0
    assert annotations_main(arguments) == 4
    output = capsys.readouterr()
    receipt = json.loads(output.out)
    assert '复用预检 2/2' in output.err and receipt['preflight_reused']
    assert receipt['committed'] and [item['ok'] for item in receipt['results']] == [True, False]
    assert receipt['audit']['ok'] and len(store.read()['threads']) == 1
    assert annotations_main(arguments) == 4
    retry = json.loads(capsys.readouterr().out)
    assert retry['results'] == receipt['results'] and store.read()['revision'] == 1
    assert annotations_main([str(report), 'add', '--quote', 'Second sentence.', '--body', 'Human arrived.', '--author', 'Other']) == 0
    capsys.readouterr()
    assert annotations_main([str(report), 'audit', receipt['snapshot_file']]) == 0
    audit = json.loads(capsys.readouterr().out)
    assert audit['ok'] and len(audit['external_thread_ids']) == 1


def test_batch_atomic_cli_and_saved_revision_conflict(tmp_path, capsys):
    content, _ = content_at(tmp_path / 'paper', 'Sentence.')
    report = tmp_path / 'report'
    report.mkdir()
    (report / MANIFEST).write_text(json.dumps(content))
    batch = tmp_path / 'batch.json'
    batch.write_text(json.dumps([{'id': 'a', 'operation': 'add', 'quote': 'Sentence.', 'body': 'A'},
                                 {'id': 'b', 'operation': 'add', 'quote': 'missing', 'body': 'B'}]))
    assert annotations_main([str(report), 'batch', str(batch), '--request-id', 'atomic', '--author', 'Codex', '--atomic']) == 4
    result = json.loads(capsys.readouterr().out)
    assert not result['committed'] and result['revision'] == 0
    assert annotations_main([str(report), 'batch', str(batch), '--request-id', 'stale', '--author', 'Codex', '--expected-revision', '5']) == 4
    assert '其他审阅者' in json.loads(capsys.readouterr().out)['error']


def test_snapshot_accepts_directory_alias_but_refuses_symlink_file(tmp_path, capsys):
    content, _ = content_at(tmp_path / 'paper', 'Sentence.')
    report = tmp_path / 'report'
    report.mkdir()
    (report / MANIFEST).write_text(json.dumps(content))
    batch = tmp_path / 'batch.json'
    batch.write_text(json.dumps([{'id': 'a', 'operation': 'add', 'quote': 'Sentence.', 'body': 'A'}]))
    alias = tmp_path / 'alias'
    alias.symlink_to(tmp_path, target_is_directory=True)
    snapshot = alias / 'receipt.json'
    args = [str(report), 'batch', str(batch), '--request-id', 'alias', '--author', 'Codex', '--snapshot', str(snapshot)]
    assert annotations_main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['snapshot_file'] == str(tmp_path.resolve() / 'receipt.json')
    link = tmp_path / 'receipt-link.json'
    link.symlink_to(snapshot)
    args[-1] = str(link)
    assert annotations_main(args) == 4
    assert '符号链接' in json.loads(capsys.readouterr().out)['error']


def test_cli_preflight_diagnostics_and_fixed_retry(tmp_path, capsys):
    content, _ = content_at(tmp_path / 'paper', 'Sentence.')
    report = tmp_path / 'report'
    report.mkdir()
    (report / MANIFEST).write_text(json.dumps(content))
    batch = tmp_path / 'batch.json'
    batch.write_text(json.dumps([{'id': 'a', 'operation': 'add', 'quote': 'Sentence.', 'body': 'A'}]))
    args = [str(report), 'batch', str(batch), '--request-id', 'recheck', '--author', 'Codex', '--atomic']
    assert annotations_main([*args, '--dry-run']) == 0
    capsys.readouterr()
    assert annotations_main([str(report), 'add', '--quote', 'Sentence.', '--body', 'Other writer', '--author', 'Other']) == 0
    capsys.readouterr()
    assert annotations_main(args) == 4
    failure = json.loads(capsys.readouterr().out)
    assert failure['code'] == 'preflight_stale'
    assert failure['reasons'] == ['annotation_revision_changed']
    assert failure['expected_revision'] == 0 and failure['actual_revision'] == 1
    assert annotations_main([*args, '--dry-run']) == 0
    capsys.readouterr()
    assert annotations_main(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt['preflight_reused'] and receipt['revision'] == 2 and receipt['audit']['ok']
    assert len(AnnotationStore.open(content).read()['threads']) == 2


def test_preflight_11_items_hides_63_existing_ids_and_saves_full_snapshot(tmp_path, capsys):
    content, _ = content_at(tmp_path / 'paper', 'Sentence.')
    report = tmp_path / 'report'
    report.mkdir()
    (report / MANIFEST).write_text(json.dumps(content))
    store = AnnotationStore.open(content)
    unit = content['units'][0]
    store.execute(envelope(content, {'operation': 'batch', 'expected_revision': 0, 'request_id': 'seed',
                         'items': [{'id': str(i), 'operation': 'create', 'selection': {'unit_id': unit['id'], 'start': 0, 'end': 8},
                                    'body': 'Existing', 'author': {'name': 'Other', 'type': 'agent'}, 'category': 'note'} for i in range(63)]}))
    batch = tmp_path / 'batch.json'
    batch.write_text(json.dumps([{'id': str(i), 'operation': 'add', 'quote': 'Sentence.', 'body': 'Review',
                                 'file': 'main.tex', 'line': unit['source']['start_line']} for i in range(11)]))
    args = [str(report), 'batch', str(batch), '--author', 'Codex', '--request-id', 'review-11', '--atomic', '--dry-run']
    assert annotations_main(args) == 0
    value = json.loads(capsys.readouterr().out)
    assert len(value['results']) == 11 and value['start_thread_count'] == 63 and 'start_thread_ids' not in value
    saved = json.loads(__import__('pathlib').Path(value['preflight_file']).read_text())
    assert len(saved['result']['start_thread_ids']) == len(saved['before']['threads']) == 63
    assert annotations_main([*args, '--full']) == 0
    assert len(json.loads(capsys.readouterr().out)['start_thread_ids']) == 63


def test_review_record_and_batch_source_location(tmp_path, capsys):
    content, _ = content_at(tmp_path / 'paper', 'Sentence.')
    report = tmp_path / 'report'
    report.mkdir()
    (report / MANIFEST).write_text(json.dumps(content))
    source = tmp_path / 'paper' / 'main.tex'
    before = source.read_bytes()
    batch = tmp_path / 'batch.json'
    batch.write_text(json.dumps([{'id': 'one', 'operation': 'add', 'file': 'main.tex', 'line': 1,
                                 'quote': 'Sentence.', 'body': '问题：结构不清。依据：原文。建议：补充解释。'},
                                {'id': 'bad', 'operation': 'add', 'file': 'main.tex', 'line': 999,
                                 'quote': 'Missing.', 'body': 'Unmatched review'}]))
    record = tmp_path / 'review.md'
    args = [str(report), 'batch', str(batch), '--author', 'Codex', '--request-id', 'record', '--review-record', str(record)]
    assert annotations_main(args) == 4
    result = json.loads(capsys.readouterr().out)
    assert result['committed'] and result['audit']['ok']
    text = record.read_text()
    assert content['version_id'] in text and 'main.tex:1-' in text
    assert result['results'][0]['thread_id'] in text and 'Sentence.' in text
    assert 'Unmatched review' in text and 'main.tex:999-' in text
    assert source.read_bytes() == before
    assert annotations_main(args) == 4
    assert json.loads(capsys.readouterr().out)['results'] == result['results']
    assert len(AnnotationStore.open(content).read()['threads']) == 1


def test_record_export_failure_retains_committed_receipt(tmp_path, capsys, monkeypatch):
    from latex_review import annotation_review_record
    content, _ = content_at(tmp_path / 'paper', 'Sentence.')
    report = tmp_path / 'report'
    report.mkdir()
    (report / MANIFEST).write_text(json.dumps(content))
    batch = tmp_path / 'batch.json'
    batch.write_text(json.dumps([{'id': 'one', 'operation': 'add', 'quote': 'Sentence.', 'body': 'Review'}]))
    def unavailable(*args):
        raise OSError('record directory unavailable')
    original = annotation_review_record.write_record
    monkeypatch.setattr(annotation_review_record, 'write_record', unavailable)
    args = [str(report), 'batch', str(batch), '--author', 'Codex', '--request-id', 'record-fail', '--review-record', str(tmp_path / 'review.md')]
    assert annotations_main(args) == 4
    result = json.loads(capsys.readouterr().out)
    assert result['committed'] and result['results'][0]['ok']
    assert 'record directory unavailable' in result['review_record_error']
    monkeypatch.setattr(annotation_review_record, 'write_record', original)
    assert annotations_main(args) == 0
    capsys.readouterr()
    assert len(AnnotationStore.open(content).read()['threads']) == 1
