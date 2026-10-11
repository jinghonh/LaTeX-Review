import json
from latex_review.agent_tools import annotations_main
from latex_review.annotations import AnnotationStore, MANIFEST
from test_annotations import content_at


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
    preview = json.loads(capsys.readouterr().out)
    store = AnnotationStore.for_content(content)
    assert preview['dry_run'] and store.read(content)['revision'] == 0
    assert annotations_main(arguments) == 4
    receipt = json.loads(capsys.readouterr().out)
    assert receipt['committed'] and [item['ok'] for item in receipt['results']] == [True, False]
    assert receipt['audit']['ok'] and len(store.read(content)['threads']) == 1
    assert annotations_main(arguments) == 4
    retry = json.loads(capsys.readouterr().out)
    assert retry['results'] == receipt['results'] and store.read(content)['revision'] == 1
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
