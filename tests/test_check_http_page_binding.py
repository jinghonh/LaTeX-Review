"""A blocked HTTP body cannot combine old credentials with a new publication."""
from http.client import HTTPConnection
import json
from threading import Event, Thread
from urllib.parse import parse_qs

import pytest

from latex_review.annotations import load_content
from latex_review.checks.http import dispatch
from latex_review.checks.service import CheckCoordinator, CheckError
from latex_review.contract import ComparisonSource
from latex_review.identity import paper_identity
from latex_review.live import build, _check_saved_recipe
from latex_review.serve import create_server
from latex_review.sources import ProjectSource


@pytest.fixture
def publication(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'xdg'))
    root = tmp_path / 'paper'
    root.mkdir()
    main = root / 'main.tex'
    main.write_text('\\documentclass{article}\\begin{document}\\section{Original}Old evidence.\\end{document}')
    output = tmp_path / 'report'
    output.mkdir()
    source = ProjectSource('new', root, 'main.tex', ComparisonSource('directory', str(root)))
    build(source, output)
    identity = paper_identity(root, 'main.tex', require_entry=True)
    coordinator = CheckCoordinator(tmp_path / 'state', lambda pid: identity if pid == identity['paper_id'] else None)
    forwarded = []
    def bridge(pid, suffix, body=None, *, query=''):
        forwarded.append((pid, suffix, body))
        return dispatch(coordinator, pid, suffix, 'POST' if body is not None else 'GET', parse_qs(query), body)[0]
    monkeypatch.setattr('latex_review.local_client.checks_request', bridge)
    server = create_server(output, annotation_project_root=root)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield root, source, output, identity, coordinator, server, forwarded
    finally:
        server.shutdown()
        thread.join(timeout=10)
        server.server_close()
        coordinator.close()
        for context in coordinator.papers.values():
            context['executor'].shutdown(wait=True)
        coordinator.semantic.executor.shutdown(wait=True)


def connection(publication, suffix, body, *, headers=None):
    root, source, output, identity, coordinator, server, forwarded = publication
    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=15)
    raw = json.dumps(body).encode()
    connection.putrequest('POST', '/api/checks/' + suffix)
    values = {'Content-Type': 'application/json', 'Content-Length': str(len(raw)),
              'X-LaTeX-Review-Token': server.checks_token,
              'X-LaTeX-Review-Paper-ID': identity['paper_id'],
              'X-LaTeX-Review-Page-Version': load_content(output)['version_id'],
              'X-LaTeX-Review-Checks-Content-ID': json.loads((output / 'checks-page.json').read_text())['content_id']}
    values.update(headers or {})
    for key, value in values.items():
        if value is not None:
            connection.putheader(key, value)
    connection.endheaders()
    return connection, raw


def response(connection, raw):
    try:
        connection.send(raw)
        result = connection.getresponse()
        return result.status, json.loads(result.read())
    finally:
        connection.close()


@pytest.mark.parametrize('operation', ['runs', 'semantic/tasks', 'invoke'])
def test_rebuild_while_body_blocked_rejects_without_queuing_or_provider(publication, monkeypatch, operation):
    root, source, output, identity, coordinator, server, forwarded = publication
    pid = identity['paper_id']
    suffix = operation
    body = {} if operation == 'runs' else {'task': 'concept_definition', 'mode': 'export'}
    request_id = None
    if operation == 'invoke':
        state = coordinator.semantic.start(pid, {'task': 'concept_definition', 'mode': 'export'})
        request_id = state['request_id']
        coordinator.semantic.futures[(pid, request_id)].result(timeout=15)
        suffix, body = 'semantic/tasks/' + request_id + '/invoke', {}
    calls = []
    coordinator.semantic.transport = lambda *args: calls.append(args)
    import latex_review.checks.http as http
    reading_body = Event()
    original = http.read_body
    def blocked(handler):
        reading_body.set()
        return original(handler)
    monkeypatch.setattr(http, 'read_body', blocked)
    old_version = load_content(output)['version_id']
    conn, raw = connection(publication, suffix, body)
    assert reading_body.wait(10)  # Headers authorized, but no body bytes sent.
    (root / 'main.tex').write_text('\\documentclass{article}\\begin{document}\\section{New}Different evidence.\\end{document}')
    build(source, output)
    assert load_content(output)['version_id'] != old_version
    status, result = response(conn, raw)
    assert status == 409 and result['code'] == 'page_changed'
    assert forwarded == [] and calls == []
    assert coordinator._paper(pid)['tasks'] == {}
    if request_id:
        assert coordinator.semantic.get(pid, request_id)['called'] is False
    else:
        assert coordinator.semantic.futures == {}


@pytest.mark.parametrize('suffix,body', [('runs', {}), ('semantic/tasks', {'task': 'concept_definition', 'mode': 'export'})])
def test_unchanged_publication_forwards_exact_version_bound_capture(publication, suffix, body):
    root, source, output, identity, coordinator, server, forwarded = publication
    metadata = json.loads((output / 'checks-page.json').read_text())
    assert metadata['page_version'] == load_content(output)['version_id']
    conn, raw = connection(publication, suffix, body)
    status, result = response(conn, raw)
    assert status == 202
    assert len(forwarded) == 1 and forwarded[0][2]['_page_snapshot'] == metadata
    if suffix == 'runs':
        coordinator._paper(identity['paper_id'])['executor'].submit(lambda: None).result(timeout=15)
        assert coordinator.run(identity['paper_id'], result['run_id'])['state'] in {'completed', 'partial'}
    else:
        coordinator.semantic.futures[(identity['paper_id'], result['request_id'])].result(timeout=15)
        assert coordinator.semantic.get(identity['paper_id'], result['request_id'])['state'] == 'ready'


@pytest.mark.parametrize('mutation,code', [('legacy', 'page_snapshot_unavailable'), ('mixed', 'page_changed')])
def test_legacy_or_mixed_sidecar_fails_closed_before_forward(publication, mutation, code):
    root, source, output, identity, coordinator, server, forwarded = publication
    path = output / 'checks-page.json'
    metadata = json.loads(path.read_text())
    if mutation == 'legacy':
        del metadata['page_version']
    else:
        metadata['page_version'] = 'different-publication'
    path.write_text(json.dumps(metadata))
    conn, raw = connection(publication, 'runs', {})
    status, result = response(conn, raw)
    assert status == 409 and result['code'] == code
    assert forwarded == []


def test_metadata_change_without_annotation_change_during_body_is_rejected(publication, monkeypatch):
    root, source, output, identity, coordinator, server, forwarded = publication
    import latex_review.checks.http as http
    original = http.read_body
    def replace_metadata(handler):
        path = output / 'checks-page.json'
        metadata = json.loads(path.read_text())
        metadata['configuration_hash'] = 'changed-config-with-same-annotation'
        path.write_text(json.dumps(metadata))
        return original(handler)
    monkeypatch.setattr(http, 'read_body', replace_metadata)
    conn, raw = connection(publication, 'runs', {})
    status, result = response(conn, raw)
    assert status == 409 and result['code'] == 'page_changed'
    assert forwarded == []


@pytest.mark.parametrize('mutation', ['unavailable', 'final-manifest-race'])
def test_manifest_reread_failure_or_second_swap_refuses_forward(publication, monkeypatch, mutation):
    root, source, output, identity, coordinator, server, forwarded = publication
    original = load_content(output)
    captures = iter([original, ValueError('publication temporarily unavailable')] if mutation == 'unavailable'
                    else [original, original, {**original, 'version_id': 'second-swap'}])
    def load(_):
        value = next(captures)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr('latex_review.annotations.load_content', load)
    conn, raw = connection(publication, 'runs', {})
    status, result = response(conn, raw)
    assert status == 409 and result['code'] == 'page_changed'
    assert forwarded == []


@pytest.mark.parametrize('mutation', ['legacy', 'mixed', 'manifest-race'])
def test_saved_notifier_refuses_legacy_mixed_or_racing_publication(publication, monkeypatch, mutation):
    root, source, output, identity, coordinator, server, forwarded = publication
    metadata = json.loads((output / 'checks-page.json').read_text())
    if mutation == 'manifest-race':
        original = load_content(output)
        captures = iter([original, {**original, 'version_id': 'new-publication'}])
        monkeypatch.setattr('latex_review.annotations.load_content', lambda _: next(captures))
    else:
        if mutation == 'legacy':
            del metadata['page_version']
        else:
            metadata['page_version'] = 'new-publication'
        (output / 'checks-page.json').write_text(json.dumps(metadata))
    _check_saved_recipe({**identity, 'output': str(output)}, {})
    assert forwarded == []


@pytest.mark.parametrize('suffix', ['runs', 'semantic/tasks', 'semantic/tasks/fixed/invoke', 'knowledge', 'findings/fixed/decisions'])
def test_all_worker_writes_require_frozen_checks_content_header(publication, suffix):
    root, source, output, identity, coordinator, server, forwarded = publication
    conn, raw = connection(publication, suffix, {}, headers={'X-LaTeX-Review-Checks-Content-ID': None})
    status, result = response(conn, raw)
    assert status == 409 and result['code'] == 'page_changed'
    assert forwarded == []


def test_preexisting_tab_config_only_rebuild_same_annotation_version_is_stale(publication):
    root, source, output, identity, coordinator, server, forwarded = publication
    from dataclasses import replace
    old_content = load_content(output)
    old_snapshot = json.loads((output / 'checks-page.json').read_text())
    build(replace(source, configuration={'checks': {'debounce_ms': 901}}), output)
    new_snapshot = json.loads((output / 'checks-page.json').read_text())
    assert load_content(output)['version_id'] == old_content['version_id']
    assert new_snapshot['content_id'] != old_snapshot['content_id']
    conn, raw = connection(publication, 'runs', {}, headers={'X-LaTeX-Review-Checks-Content-ID': old_snapshot['content_id']})
    status, result = response(conn, raw)
    assert status == 409 and result['code'] == 'page_changed'
    assert forwarded == []


def test_dispatch_requires_body_and_trusted_snapshot_page_version_agreement(publication):
    root, source, output, identity, coordinator, server, forwarded = publication
    metadata = json.loads((output / 'checks-page.json').read_text())
    with pytest.raises(CheckError) as caught:
        dispatch(coordinator, identity['paper_id'], 'runs', 'POST', {},
                 {'page_version': 'different-page', '_page_snapshot': metadata})
    assert caught.value.code == 'page_changed' and caught.value.status == 409
    assert coordinator._paper(identity['paper_id'])['tasks'] == {}
