import json


def coordinator_paper(tmp_path, monkeypatch):
    monkeypatch.setenv('LATEX_REVIEW_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    root = tmp_path / 'paper'
    root.mkdir()
    path = root / 'main.tex'
    path.write_text('Body')
    from latex_review import local_client
    monkeypatch.setattr(local_client, 'start', lambda **kwargs: (_ for _ in ()).throw(AssertionError('must not spawn entry')))
    return path

from latex_review.checks.cli import main


def test_cli_reuses_existing_entry_and_exports_exact_report(tmp_path, capsys, monkeypatch):
    path = coordinator_paper(tmp_path, monkeypatch)
    from latex_review import local_client
    from latex_review.checks import capture_snapshot, build_check_document, evaluate_current
    from latex_review.identity import identity_hash
    report = evaluate_current(build_check_document(capture_snapshot(path)))
    calls = []
    monkeypatch.setattr(local_client, 'endpoint', lambda: {'port': 1})
    def request(paper_id, suffix, body=None, **kwargs):
        calls.append((suffix, body))
        if suffix == 'status':
            return {'configuration_hash': identity_hash({})}
        if suffix == 'runs':
            assert body == {'scope': 'all', 'configuration_hash': identity_hash({})}
            return {'run_id': 'shared-run', 'state': 'queued'}
        if suffix == 'runs/shared-run':
            return {'run_id': 'shared-run', 'state': 'completed'}
        assert suffix == 'export' and kwargs['query'] == 'run_id=shared-run'
        return report
    monkeypatch.setattr(local_client, 'checks_request', request)
    assert main(['run', str(path), '--format', 'json']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {'run_id': 'shared-run', 'report': report}
    assert [c[0] for c in calls] == ['status', 'runs', 'runs/shared-run', 'export']
    import sqlite3
    databases = list((tmp_path / 'state' / 'checks').glob('*/checks.sqlite3'))
    assert len(databases) == 1
    with sqlite3.connect(databases[0]) as db:
        assert db.execute('SELECT count(*) FROM runs').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM semantic_context').fetchone()[0] == 1


def test_cli_offline_does_not_start_entry(tmp_path, capsys, monkeypatch):
    path = coordinator_paper(tmp_path, monkeypatch)
    from latex_review import local_client
    from latex_review.local_protocol import LocalError
    def unavailable():
        raise LocalError('not running', 503)
    monkeypatch.setattr(local_client, 'endpoint', unavailable)
    assert main(['run', str(path), '--format', 'json']) == 0
    assert json.loads(capsys.readouterr().out)['report']['status'] == 'completed'


def test_cli_lost_active_run_never_falls_back_to_duplicate(tmp_path, capsys, monkeypatch):
    path = coordinator_paper(tmp_path, monkeypatch)
    from latex_review import local_client
    from latex_review.identity import identity_hash
    from latex_review.local_protocol import LocalError
    monkeypatch.setattr(local_client, 'endpoint', lambda: {})
    def request(paper_id, suffix, body=None, **kwargs):
        if suffix == 'status':
            return {'configuration_hash': identity_hash({})}
        if suffix == 'runs':
            return {'run_id': 'pending-run', 'state': 'running'}
        raise LocalError('connection lost', 503)
    monkeypatch.setattr(local_client, 'checks_request', request)
    assert main(['run', str(path), '--format', 'json']) == 2
    assert json.loads(capsys.readouterr().err)['error']['run_id'] == 'pending-run'
    assert not (tmp_path / 'state' / 'checks').exists()


def test_cli_run_query_full_fallback_and_exit_protocol(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv('LATEX_REVIEW_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    tmp_path = tmp_path / 'paper'
    tmp_path.mkdir()
    path = tmp_path / 'main.tex'
    path.write_text(r'\label{x}\label{x}')
    store = tmp_path / 'state' / 'custom.sqlite3'
    args = ['run', str(path), '--changed', '--store', str(store), '--format', 'json']
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first['report']['evaluation_mode'] == 'full'
    assert first['report']['fallback_reason'] == 'no_baseline'
    assert main(args + ['--fail-on', 'warning']) == 3
    second = json.loads(capsys.readouterr().out)
    assert second['report']['findings'][0]['delta_status'] == 'existing'
    assert main(['query', '--run', first['run_id'], '--store', str(store), '--format', 'json', '--evidence']) == 0
    query = json.loads(capsys.readouterr().out)
    assert query['findings'][0]['evidence'][0]['quote'] == r'\label{x}'
    assert query['total'] == 1 and query['truncated'] is False
    path.write_text(r'\input{missing}\label{x}\label{x}')
    assert main(args + ['--fail-on', 'warning']) == 2
    capsys.readouterr()
    assert main(args + ['--bogus']) == 1
    assert 'error' in json.loads(capsys.readouterr().err)


def test_cli_source_change_during_evaluation_is_historical_not_current(tmp_path, capsys, monkeypatch):
    from latex_review.checks import cli
    from latex_review.checks.store import CheckStore
    path = coordinator_paper(tmp_path, monkeypatch)
    store = tmp_path / 'state' / 'custom.sqlite3'
    args = ['run', str(path), '--store', str(store), '--format', 'json']
    assert main(args) == 0
    baseline = json.loads(capsys.readouterr().out)
    real_evaluate = cli.evaluate_current
    def mutate(*args, **kwargs):
        report = real_evaluate(*args, **kwargs)
        path.write_text('Source changed after evaluation.')
        return report
    monkeypatch.setattr(cli, 'evaluate_current', mutate)
    assert main(args + ['--semantic']) == 2
    stale = json.loads(capsys.readouterr().out)
    assert stale['state'] == stale['semantic']['state'] == 'stale'
    assert stale['report']['status'] == 'completed'
    database = CheckStore(store)
    assert database.get_run(stale['run_id'])['status'] == 'stale'
    assert database.current(stale['report']['input']['paper_id'])['run_id'] == baseline['run_id']


def test_semantic_disabled_or_missing_key_preserves_deterministic_output(tmp_path, capsys, monkeypatch):
    from latex_review.checks.semantic_service import SemanticService
    from latex_review.checks.store import CheckStore
    network_calls = []
    def forbidden_network(*args, **kwargs):
        network_calls.append((args, kwargs))
        raise AssertionError('semantic preconditions must fail before any provider call')
    monkeypatch.setattr(SemanticService, '_network', forbidden_network)
    monkeypatch.delenv('LATEX_REVIEW_TEST_MISSING_KEY', raising=False)
    for enabled in (False, True):
        case = tmp_path / str(enabled)
        case.mkdir()
        path = coordinator_paper(case, monkeypatch)
        path.write_text(r'\begin{document}\section{Introduction}We define a compact estimator for this synthetic study.\end{document}')
        store, artifact, config = case / 'state' / 'checks.sqlite3', case / 'checks.json', case / 'config.toml'
        config.write_text('[semantic]\nenabled=' + str(enabled).lower() + '\nbase_url="https://example.invalid/v1"\nmodel="test"\napi_key_env="LATEX_REVIEW_TEST_MISSING_KEY"\n')
        assert main(['run', str(path), '--semantic', '--store', str(store), '--config', str(config), '--output', str(artifact), '--format', 'json']) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload['semantic']['state'] == 'failed'
        assert not network_calls
        assert payload['report']['status'] == 'completed'
        assert json.loads(artifact.read_text()) == payload['report']
        assert CheckStore(store).get_run(payload['run_id'])['report'] == payload['report']


def test_run_semantic_is_explicit_and_candidates_do_not_fail_threshold(tmp_path, capsys, monkeypatch):
    path = coordinator_paper(tmp_path, monkeypatch)
    from latex_review.checks import semantic_cli
    calls = []
    def run_semantic(identity, configuration, store_path, **kwargs):
        calls.append(kwargs)
        return {'state': 'completed', 'candidates': [{'severity': 'error'}]}
    monkeypatch.setattr(semantic_cli, 'run_semantic', run_semantic)
    store = tmp_path / 'state' / 'custom.sqlite3'
    assert main(['run', str(path), '--semantic', '--store', str(store), '--fail-on', 'info', '--format', 'json']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['semantic']['state'] == 'completed'
    assert calls[0]['expected_input_id'] == payload['report']['input_id']
    assert calls[0]['use_coordinator'] is False


def test_cli_semantic_export_import_uses_retained_authority(tmp_path, capsys, monkeypatch):
    path = coordinator_paper(tmp_path, monkeypatch)
    path.write_text(r'\begin{document}\section{Introduction}We define a compact estimator for this synthetic study.\end{document}')
    store = tmp_path / 'state' / 'custom.sqlite3'
    assert main(['run', str(path), '--store', str(store), '--format', 'json']) == 0
    run = json.loads(capsys.readouterr().out)
    request, response = tmp_path / 'request.json', tmp_path / 'response.json'
    assert main(['semantic', 'export', '--run', run['run_id'], '--store', str(store), '--output', str(request)]) == 0
    authority = json.loads(capsys.readouterr().out)
    assert authority == json.loads(request.read_text())
    response.write_text(json.dumps({'schema_version': '1.0', 'request_id': authority['request_id'], 'task': authority['task'], 'candidates': []}))
    args = ['semantic', 'import', '--request', str(request), '--response', str(response), '--store', str(store)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'completed'
    authority['policy'] = 'Untrusted request files must not become authority'
    request.write_text(json.dumps(authority))
    assert main(args) == 1
    assert json.loads(capsys.readouterr().err)['error']['code'] == 'request_tampered'


def test_cli_multi_entry_and_machine_output(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv('LATEX_REVIEW_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    tmp_path = tmp_path / 'paper'
    tmp_path.mkdir()
    (tmp_path / 'main.tex').write_text('Main')
    (tmp_path / 'supp.tex').write_text('Supplement')
    (tmp_path / '.latex-review.toml').write_text('[project]\nentries=["main.tex","supp.tex"]\n')
    output = tmp_path / 'checks.json'
    assert main(['run', '--project', str(tmp_path), '--all-entries', '--output', str(output), '--format', 'json']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload['runs']) == 2
    assert payload == json.loads(output.read_text())
    assert payload['runs'][0]['report']['input']['paper_id'] != payload['runs'][1]['report']['input']['paper_id']
