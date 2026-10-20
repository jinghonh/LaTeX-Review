"""Local service boundary tests use private state and real owned subprocesses."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen, ProxyHandler, build_opener

import pytest

from latex_review import local_client
from latex_review.identity import identity_hash, paper_identity
from latex_review.live import prepare_request
from latex_review.local_paths import state_directory
from latex_review.local_protocol import LocalError
from latex_review.local_state import lock_busy, write_json
from latex_review.output_leases import output_lease


def paper(tmp_path):
    root = tmp_path / 'paper'
    root.mkdir()
    entry = root / 'main.tex'
    entry.write_text(r'\documentclass{article}\begin{document}Current paper.\end{document}')
    return root, entry


def diff_request(root, entry, directory, config):
    identity = paper_identity(root, entry)
    directory.mkdir()
    (directory / 'report.html').write_text('<h1>First report</h1>')
    write_json(directory / '.local-identity.json', identity)
    return {**identity, 'kind': 'diff', 'directory': str(directory), 'port': 0,
            'config': str(config), 'config_explicit': False, 'cache': str(directory.parent / 'translation-cache'),
            'configuration_fingerprint': identity_hash({})}


def wait_until(check, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.05)
    raise AssertionError('condition timeout')


def report_url(stable_url):
    with urlopen(stable_url) as response:
        assert response.status == 200 and response.url == stable_url
        html = response.read().decode()
        worker_url = re.search(r'<iframe[^>]+src="([^"]+)"', html).group(1)
        from urllib.parse import urljoin
        worker_url = urljoin(stable_url, worker_url)
        assert "frame-src 'self'" in response.headers['Content-Security-Policy']
    return worker_url


def test_parallel_requests_share_worker_and_stable_link(tmp_path):
    root, entry = paper(tmp_path)
    request = prepare_request(entry, root=root, poll_files=True)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: local_client.ensure(request, timeout=30), range(12)))
    assert len({r['pid'] for r in results}) == 1
    assert len({r['url'] for r in results}) == 1
    with urlopen(report_url(results[0]['url'])) as response:
        assert response.url.endswith('/report.html')
        assert b'Current paper.' in response.read()
    endpoint = local_client.endpoint()
    with pytest.raises(HTTPError) as error:
        urlopen(Request(f"http://127.0.0.1:{endpoint['port']}/api/v1/shutdown", data=b'{}',
                        headers={'Content-Type': 'application/json'}))
    assert error.value.code == 403
    result = local_client.request(f"/api/v1/papers/{request['paper_id']}/live/stop", {})
    wait_until(lambda: local_client.request('/api/v1/operations/' + result['operation_id'])['state'] == 'ready')
    assert not lock_busy(state_directory() / 'slots' / f"{request['paper_id']}.live.lock")


def test_diff_replacement_does_not_rewrite_original(tmp_path):
    root, entry = paper(tmp_path)
    request = diff_request(root, entry, tmp_path / 'report', tmp_path / 'missing.toml')
    first = local_client.ensure(request)
    original_worker_url = report_url(first['url'])
    original = Path(request['directory']) / 'report.html'
    original.write_text('<h1>Second baseline</h1>')
    second = local_client.ensure(request)
    assert first['url'] == second['url'] and first['pid'] != second['pid']
    assert report_url(second['url']) == original_worker_url
    with urlopen(report_url(second['url'])) as response:
        assert b'Second baseline' in response.read()
    assert original.read_text() == '<h1>Second baseline</h1>'
    (Path(request['directory']) / 'escape').symlink_to(entry)
    with pytest.raises(LocalError):
        local_client.ensure(request)
    with urlopen(report_url(second['url'])) as response:
        assert response.status == 200


def test_owner_sigkill_releases_worker_lifetime_lock(tmp_path):
    root, entry = paper(tmp_path)
    request = prepare_request(entry, root=root, poll_files=True)
    result = local_client.ensure(request)
    owned_endpoint = local_client.endpoint()
    os.kill(owned_endpoint['pid'], signal.SIGKILL)
    lock = state_directory() / 'slots' / f"{request['paper_id']}.live.lock"
    wait_until(lambda: not lock_busy(lock))
    restored = local_client.ensure(request, timeout=30)
    assert restored['pid'] != result['pid']
    assert restored['url'] == result['url']


def test_output_lease_rejects_cross_process_ancestor_overlap(tmp_path):
    output = tmp_path / 'output'
    with output_lease(output):
        code = ('from latex_review.output_leases import output_lease; import sys; '
                'lease=output_lease(sys.argv[1],wait=False); lease.__enter__()')
        result = subprocess.run([sys.executable, '-c', code, str(output / 'child')], capture_output=True)
        assert result.returncode != 0 and '认领'.encode() in result.stderr
    with output_lease(output / 'child', wait=False):
        pass


def test_request_id_conflict_and_host_origin_checks(tmp_path):
    root, entry = paper(tmp_path)
    request = prepare_request(entry, root=root, poll_files=True)
    request['request_id'] = 'fixed'
    first = local_client.ensure(request)
    again = local_client.ensure(request)
    assert first['operation_id'] == again['operation_id']
    changed = {**request, 'output': str(root / 'elsewhere')}
    with pytest.raises(LocalError) as error:
        local_client.ensure(changed)
    assert error.value.status == 409
    endpoint = local_client.endpoint()
    for headers in ({'Host': 'evil.example'}, {'Origin': 'https://evil.example'}):
        with pytest.raises(HTTPError) as error:
            build_opener(ProxyHandler({})).open(Request(f"http://127.0.0.1:{endpoint['port']}/", headers=headers))
        assert error.value.code == 403


def test_entry_port_conflict_does_not_fall_back():
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        with pytest.raises(LocalError):
            local_client.start(port=occupied.getsockname()[1], timeout=5)
    assert not lock_busy(state_directory() / 'local.lock')


def test_failed_live_reconfiguration_keeps_previous_worker(tmp_path):
    root, entry = paper(tmp_path)
    request = prepare_request(entry, root=root, poll_files=True)
    first = local_client.ensure(request)
    bad = tmp_path / 'invalid.toml'
    bad.write_text('unsupported = true')
    changed = {**request, 'config': str(bad), 'config_explicit': True}
    with pytest.raises(LocalError):
        local_client.ensure(changed)
    current = local_client.request('/api/v1/papers')['papers'][0]['slots']['live']
    assert current['pid'] == first['pid'] and current['state'] == 'ready'
    with urlopen(report_url(first['url'])) as response:
        assert b'Current paper.' in response.read()


def test_same_paper_live_and_diff_coexist_and_offline_conflict_is_bounded(tmp_path):
    root, entry = paper(tmp_path)
    live = prepare_request(entry, root=root, poll_files=True)
    live_result = local_client.ensure(live)
    diff = diff_request(root, entry, tmp_path / 'report', tmp_path / 'missing.toml')
    diff_result = local_client.ensure(diff)
    assert live_result['pid'] != diff_result['pid']
    slots = local_client.request('/api/v1/papers')['papers'][0]['slots']
    assert slots['live']['state'] == slots['diff']['state'] == 'ready'
    with pytest.raises(ValueError, match='认领'):
        with output_lease(Path(live['output']) / 'nested'):
            pass


def test_worker_task_tracker_cancels_separate_session_on_eof(tmp_path):
    """Actual child groups must stop even when compilation uses a new session."""
    ready = tmp_path / 'task-ready.json'
    reader, writer = os.pipe()
    code = '''
import json,os,subprocess,sys,threading
from pathlib import Path
from latex_review.local_tasks import install,cancel,popen
original_popen=subprocess.Popen
install()
assert subprocess.Popen is original_popen
fd=int(sys.argv[1]);os.set_inheritable(fd,False)
def watch():
    os.read(fd,1);cancel()
threading.Thread(target=watch,daemon=True).start()
child=popen([sys.executable,'-c','import time; time.sleep(120)'],start_new_session=True)
Path(sys.argv[2]).write_text(json.dumps({'pid':child.pid}))
child.wait()
'''
    owned = subprocess.Popen([sys.executable, '-c', code, str(reader), str(ready)], pass_fds=(reader,))
    os.close(reader)
    try:
        wait_until(ready.exists)
        task = json.loads(ready.read_text())['pid']
        os.close(writer)
        writer = -1
        assert owned.wait(timeout=5) == 0
        with pytest.raises(ProcessLookupError):
            os.kill(task, 0)
    finally:
        if writer >= 0:
            os.close(writer)
        if owned.poll() is None:
            owned.kill()
            owned.wait()


def test_missing_resource_candidate_and_corrupt_registry_recovery(tmp_path):
    root, entry = paper(tmp_path)
    request = diff_request(root, entry, tmp_path / 'report', tmp_path / 'missing.toml')
    first = local_client.ensure(request)
    (Path(request['directory']) / 'report.html').write_text('<img src="assets/missing.png">')
    with pytest.raises(LocalError, match='资源缺失'):
        local_client.ensure(request)
    with urlopen(report_url(first['url'])) as response:
        assert b'First report' in response.read()
    local_client.stop()
    state = state_directory()
    wait_until(lambda: not lock_busy(state / 'local.lock'))
    (state / 'registry.json').write_text('{broken')
    (state / 'endpoint.json').write_text('{broken')
    local_client.start()
    assert list(state.glob('registry.corrupt-*.json'))
    assert local_client.request('/api/v1/papers')['papers'] == []


def test_diff_retries_and_equivalent_requests_do_not_copy_again(tmp_path, monkeypatch):
    import logging
    from latex_review import local_service
    root, entry = paper(tmp_path)
    request = diff_request(root, entry, tmp_path / 'report', tmp_path / 'missing.toml')
    resource = Path(request['directory']) / 'resource.bin'
    resource.write_bytes(bytes(1024 * 1024))
    copied = []
    original = local_service.copy_report
    def copy_report(source, target):
        result = original(source, target)
        copied.append((target / resource.name).stat().st_size)
        return result
    monkeypatch.setattr(local_service, 'copy_report', copy_report)
    # Hold operations in the queue so this test needs no worker process.
    monkeypatch.setattr(local_service.Slot, '_run', lambda self: None)
    state = state_directory()
    manager = local_service.Manager(state, 8765, logging.getLogger(__name__))
    try:
        first = manager.ensure(request['paper_id'], 'diff', {**request, 'request_id': 'first'})
        retry = manager.ensure(request['paper_id'], 'diff', {**request, 'request_id': 'first'})
        equivalent = manager.ensure(request['paper_id'], 'diff', {**request, 'request_id': 'second'})
        assert first['operation_id'] == retry['operation_id'] == equivalent['operation_id']
        assert copied == [resource.stat().st_size]
        resource.write_bytes(b'changed')
        with pytest.raises(LocalError, match='内容不同'):
            manager.ensure(request['paper_id'], 'diff', {**request, 'request_id': 'first'})
        assert len(copied) == 1
        changed = manager.ensure(request['paper_id'], 'diff', {**request, 'request_id': 'third'})
        assert changed['operation_id'] != first['operation_id']
        assert len(copied) == 2
    finally:
        manager.close()


def test_upload_network_phase_releases_generation_lease(tmp_path, monkeypatch):
    """Network negotiation may run while another task acquires the output."""
    import tarfile
    from types import SimpleNamespace
    from latex_review import cli, delivery, upload
    from test_cli import project
    project(tmp_path)
    monkeypatch.chdir(tmp_path)
    settings = Path(os.environ['XDG_CONFIG_HOME']) / 'latex-review' / 'upload.toml'
    settings.parent.mkdir(parents=True)
    settings.write_text(f'host="stub"\nbase_url="https://example.invalid"\n[[papers]]\nentry="{tmp_path / "main.tex"}"\nslug="paper"\n')
    output = tmp_path / '.latex-review' / 'latest'
    calls = []
    def ssh(host, command, **kwargs):
        calls.append(command)
        if command == 'resource-query-v1':
            code = ('import sys; from latex_review.output_leases import output_lease; '
                    'lease=output_lease(sys.argv[1],wait=False); lease.__enter__(); lease.__exit__(None,None,None)')
            result = subprocess.run([sys.executable, '-c', code, str(output)], capture_output=True)
            assert result.returncode == 0, result.stderr.decode()
            manifest = json.loads(kwargs['input'])
            receipt = {'protocol': 1, 'slug': 'paper', 'manifest_id': delivery.manifest_id(manifest),
                       'missing': sorted({r['sha256'] for r in manifest['resources'].values()})}
        else:
            with tarfile.open(fileobj=kwargs['stdin']) as archive:
                metadata = json.load(archive.extractfile('release.json'))
            receipt = {'slug': 'paper', 'release': metadata['release']}
        return SimpleNamespace(returncode=0, stdout=json.dumps(receipt).encode(), stderr=b'')
    monkeypatch.setattr(upload, '_ssh', ssh)
    monkeypatch.setattr(local_client, 'start', lambda **kwargs: pytest.fail('pure upload started service'))
    assert cli.main(['main.tex', '--upload']) == 0
    assert calls == ['resource-query-v1', 'publish-incremental-v1']


def test_bounded_compile_is_cancelled_with_its_separate_group(tmp_path):
    """Run the real compilation runner with a sleeping isolated tool."""
    reader, writer = os.pipe()
    ready = tmp_path / 'compile-ready'
    code = '''
import os,sys,threading
from pathlib import Path
from latex_review.local_tasks import install,cancel,popen
from latex_review.compilation import _run_bounded
install()
fd=int(sys.argv[1]);os.set_inheritable(fd,False)
def watch():
    os.read(fd,1);cancel()
threading.Thread(target=watch,daemon=True).start()
tool="from pathlib import Path; import time; Path("+repr(sys.argv[3])+").write_text('ready'); time.sleep(120)"
result=_run_bounded([sys.executable,'-c',tool],Path(sys.argv[2]),dict(os.environ),120,4096)
assert result[0] != 0, result
'''
    proc = subprocess.Popen([sys.executable, '-c', code, str(reader), str(tmp_path), str(ready)], pass_fds=(reader,))
    os.close(reader)
    try:
        wait_until(ready.exists)
        os.close(writer)
        writer = -1
        assert proc.wait(timeout=5) == 0
    finally:
        if writer >= 0:
            os.close(writer)
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_forwarded_entry_uses_same_origin_worker_routes(tmp_path):
    from urllib.parse import urljoin
    urlopen = build_opener(ProxyHandler({})).open
    root, entry = paper(tmp_path)
    result = local_client.ensure(prepare_request(entry, root=root, poll_files=True))
    headers = {'Host': 'localhost:49123', 'Origin': 'http://localhost:49123'}
    with urlopen(Request(result['url'], headers=headers)) as response:
        html = response.read().decode()
    frame = re.search(r'<iframe[^>]+src="([^"]+)"', html).group(1)
    assert frame.startswith('/') and '127.0.0.1:' not in frame
    worker = urljoin(result['url'], frame)
    with urlopen(Request(worker, headers=headers)) as response:
        assert b'Current paper.' in response.read()
    with urlopen(Request(urljoin(worker, 'api/report'), headers=headers)) as response:
        assert json.load(response)['report_url'] == 'http://localhost:49123' + frame
    status = urljoin(worker, 'api/translation/status')
    with urlopen(Request(status, headers=headers)) as response:
        token = json.load(response)['token']
        assert token
    with urlopen(Request(urljoin(worker, 'live-status.json'), headers=headers)) as response:
        assert response.status == 200
    # The worker, not the entry management API, validates paper write tokens.
    with pytest.raises(HTTPError) as error:
        urlopen(Request(urljoin(worker, 'api/translation/start'), data=b'{}',
                        headers={**headers, 'Content-Type': 'application/json',
                                 'X-LaTeX-Review-Token': token}))
    assert error.value.code == 400
    for bad in ({'Host': 'evil.example'},
                {'Host': 'localhost:49123', 'Origin': 'http://localhost:49124'},
                {'Host': 'localhost:49123', 'Sec-Fetch-Site': 'cross-site'}):
        with pytest.raises(HTTPError) as error:
            urlopen(Request(status, headers=bad))
        assert error.value.code == 403
    with pytest.raises(HTTPError) as error:
        urlopen(Request(urljoin(worker, 'api/translation/start'), data=b'{}',
                        headers={**headers, 'Content-Type': 'application/json'}))
    assert error.value.code == 403
