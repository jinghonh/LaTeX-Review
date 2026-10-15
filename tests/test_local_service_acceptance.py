"""Independent acceptance of public local-service entry points.

All state, configuration, papers and subprocesses belong to the test directory.
No existing user service is queried or stopped.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import signal
import shlex
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


def eventually(check, timeout=15):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = check()
            if last:
                return last
        except (OSError, ValueError):
            pass
        time.sleep(.05)
    raise AssertionError(f'condition did not become true: {last!r}')


def unused_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def paper(root, text='Initial acceptance text.'):
    root.mkdir(parents=True, exist_ok=True)
    entry = root / 'main.tex'
    entry.write_text(r'\documentclass{article}\begin{document}\section{Acceptance}' + text + r'\end{document}')
    return entry


def fetch(url, *, method='GET', headers=None, data=None):
    from urllib.request import build_opener, ProxyHandler
    request = Request(url, method=method, headers=headers or {}, data=data)
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            return response.status, response.read(), response.geturl()
    except HTTPError as error:
        return error.code, error.read(), error.geturl()


def fetch_report(link):
    status, html, address = fetch(link)
    assert status == 200 and address == link
    worker = re.search(rb'<iframe[^>]+src="([^"]+)"', html).group(1).decode()
    from urllib.parse import urljoin
    return fetch(urljoin(link, worker))


class LocalHarness:
    """Commands are public modules; production locks and workers remain real."""
    def __init__(self, root):
        self.root = root
        self.state = root / 'state'
        self.port = unused_port()
        self.env = dict(os.environ)
        self.env['BROWSER'] = '/usr/bin/true'
        self.env['ACCEPTANCE_TRANSLATION_KEY'] = 'fake-local-only-key'
        self.env.update(XDG_CONFIG_HOME=str(root / 'config'), XDG_STATE_HOME=str(root / 'xdg-state'))
        self.env['LATEX_REVIEW_STATE_HOME'] = str(self.state.resolve())
        self.env['LATEX_REVIEW_LOCAL_PORT'] = str(self.port)
        self.env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'src')
        self.processes = []

    def cli(self, module, *args, timeout=150):
        return subprocess.run([sys.executable, '-m', f'latex_review.{module}', *map(str, args)],
                              cwd=self.root, env=self.env, capture_output=True, text=True, timeout=timeout)

    def spawn(self, module, *args):
        log = (self.root / f'client-{len(self.processes)}.log').open('w')
        process = subprocess.Popen([sys.executable, '-m', f'latex_review.{module}', *map(str, args)],
                                   cwd=self.root, env=self.env, stdout=log, stderr=log)
        self.processes.append((process, log))
        return process

    def close_clients(self):
        for process, log in self.processes:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            log.close()


# Production-adapter tests are completed after T1–T5 returns a completed turn.
# Required acceptance: A1–A11 and A17–A20. No skip may stand in for a failed mechanism.


def test_a4_identity_paths_and_main_entries(tmp_path):
    from latex_review.identity import paper_identity
    root = tmp_path / 'paper'
    entry = paper(root)
    alias = tmp_path / 'alias'
    alias.symlink_to(root, target_is_directory=True)
    main_alias = root / 'alias.tex'
    main_alias.symlink_to(entry)
    canonical = paper_identity(root, entry, require_entry=True)
    assert paper_identity(alias, alias / 'main.tex', require_entry=True) == canonical
    assert paper_identity(root, main_alias, require_entry=True) == canonical
    supplement = root / 'supplement.tex'
    supplement.write_text(entry.read_text())
    checkout = tmp_path / 'checkout'
    checkout_entry = paper(checkout)
    assert paper_identity(root, supplement)['paper_id'] != canonical['paper_id']
    assert paper_identity(checkout, checkout_entry)['paper_id'] != canonical['paper_id']
    assert paper_identity(tmp_path, entry)['paper_id'] != canonical['paper_id']


def test_a18_offline_output_leases_reject_parent_and_child(tmp_path, monkeypatch):
    """A real child process holds the lease while no resident service runs."""
    state = tmp_path / 'state'
    monkeypatch.setenv('LATEX_REVIEW_STATE_HOME', str(state))
    output = tmp_path / 'output'
    holder_code = ('import sys; from latex_review.output_leases import output_lease; '
                   'with_context = output_lease(sys.argv[1]); with_context.__enter__(); '
                   'print("ready", flush=True); sys.stdin.read(); with_context.__exit__(None,None,None)')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    holder = subprocess.Popen([sys.executable, '-c', holder_code, str(output)], env=env,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with ThreadPoolExecutor() as pool:
            assert pool.submit(holder.stdout.readline).result(timeout=10).strip() == 'ready'
        from latex_review.output_leases import output_lease
        for overlapping in (output, output / 'child', tmp_path):
            with pytest.raises(ValueError, match='认领'):
                with output_lease(overlapping, wait=False):
                    pass
        with output_lease(tmp_path / 'independent', wait=False):
            assert not (state / 'endpoint.json').exists()
    finally:
        holder.stdin.close()
        holder.wait(timeout=10)
    assert holder.returncode == 0
    with output_lease(output, wait=False):
        pass


@pytest.fixture
def local(tmp_path):
    harness = LocalHarness(tmp_path)
    owner = harness.spawn('local_service', 'run', '--port', harness.port)
    try:
        eventually(lambda: (harness.state / 'endpoint.json').exists())
        harness.endpoint = json.loads((harness.state / 'endpoint.json').read_text())
        harness.base = f'http://127.0.0.1:{harness.port}'
        assert fetch(harness.base + '/health')[0] == 200
        yield harness
    finally:
        if owner.poll() is None:
            owner.terminate()
            try:
                owner.wait(timeout=12)
            except subprocess.TimeoutExpired:
                owner.kill()
                owner.wait(timeout=5)
        harness.close_clients()


def live_args(entry, root, output, *extra):
    return (entry, '--root', root, '--output', output, *extra)


def stable_link(result):
    assert result.returncode == 0, result.stderr + result.stdout
    links = re.findall(r'http://127\.0\.0\.1:\d+/papers/[a-f0-9]{64}/(?:live|diff)', result.stdout)
    assert links, result.stdout
    return links[-1]


def papers(local):
    status, body, _ = fetch(local.base + '/api/v1/papers')
    assert status == 200
    return json.loads(body)['papers']


def control(local, path, body):
    return fetch(local.base + path, method='POST', data=json.dumps(body).encode(),
                 headers={'Content-Type': 'application/json',
                          'X-LaTeX-Review-Token': local.endpoint['token']})


def test_a1_twenty_four_public_clients_share_one_generation(local):
    entry = paper(local.root / 'paper')
    output = entry.parent / '.latex-review' / 'live'
    args = live_args(entry, entry.parent, output, '--poll-files')
    with ThreadPoolExecutor(max_workers=24) as pool:
        results = list(pool.map(lambda _: local.cli('live', *args), range(24)))
    links = {stable_link(result) for result in results}
    assert len(links) == 1
    records = papers(local)
    assert len(records) == 1
    slot = records[0]['slots']['live']
    assert slot['state'] == 'ready'
    assert fetch(next(iter(links)))[0] == 200
    log = (local.state / 'logs' / f"{records[0]['paper_id']}.live.log").read_text()
    assert len(re.findall(r'worker_started .*kind=live(?: |$)', log)) == 1
    # The kernel, not a PID count, proves the live slot is held exclusively.
    import fcntl
    with (local.state / 'slots' / f"{records[0]['paper_id']}.live.lock").open('r+') as stream:
        with pytest.raises(BlockingIOError):
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_a11_control_security_boundaries(local):
    assert fetch(local.base + '/health', headers={'Host': 'evil.example'})[0] == 403
    assert fetch(local.base + '/api/v1/papers', headers={'Origin': 'https://evil.example'})[0] == 403
    for headers in ({}, {'X-LaTeX-Review-Token': 'wrong'},
                    {'X-LaTeX-Review-Token': local.endpoint['token'], 'Origin': 'https://evil.example'}):
        assert fetch(local.base + '/api/v1/shutdown', method='POST', data=b'{}',
                     headers={'Content-Type': 'application/json', **headers})[0] == 403
    assert fetch(local.base + '/health')[0] == 200
    assert fetch(local.base + '/papers/../../secret/live')[0] in (400, 403, 404)
    # Management pages may expose their same-origin control token. Public JSON must not.
    assert local.endpoint['token'].encode() not in fetch(local.base + '/api/v1/papers')[1]


def generate_diff(local, root, *, old='Old baseline.', new='New manuscript.', output=None):
    entry = paper(root, new)
    before = root / 'before.tex'
    before.write_text(entry.read_text().replace(new, old))
    output = output or root / '.latex-review' / 'latest'
    result = local.cli('cli', before, entry, '--output', output)
    assert result.returncode in (0, 2), result.stderr
    assert (output / 'report.html').is_file()
    return entry, output


def test_a2_a3_a9_two_kinds_two_papers_and_alias(local):
    entry, output = generate_diff(local, local.root / 'first')
    second = paper(local.root / 'second')
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(local.cli, 'live', *live_args(entry, entry.parent, entry.parent / 'live', '--poll-files')),
                   pool.submit(local.cli, 'serve', output),
                   pool.submit(local.cli, 'live', *live_args(second, second.parent, second.parent / 'live', '--poll-files'))]
        links = [stable_link(f.result()) for f in futures]
    assert len(papers(local)) == 2
    assert all(fetch_report(link)[0] == 200 for link in links)
    first = next(record for record in papers(local) if record['root'] == str(entry.parent.resolve()))
    assert first['slots']['live']['state'] == first['slots']['diff']['state'] == 'ready'
    generation = first['slots']['live']['generation']
    alias = local.root / 'alias'
    alias.symlink_to(entry.parent, target_is_directory=True)
    alias_result = local.cli('live', *live_args(alias / 'main.tex', alias, alias / 'live', '--poll-files'))
    assert stable_link(alias_result) == links[0]
    assert next(p for p in papers(local) if p['paper_id'] == first['paper_id'])['slots']['live']['generation'] == generation
    # A different output must replace a recipe, never add another live slot.
    replacement = local.cli('live', *live_args(entry, entry.parent, entry.parent / 'live-alternate', '--poll-files'))
    assert stable_link(replacement) == links[0]
    assert next(p for p in papers(local) if p['paper_id'] == first['paper_id'])['slots']['live']['generation'] != generation
    assert len(papers(local)) == 2


def test_a5_a9_a19_diff_copy_updates_and_failure_preserves_old(local):
    entry, output = generate_diff(local, local.root / 'paper')
    link = stable_link(local.cli('serve', output))
    _, body, worker = fetch_report(link)
    base = worker.rsplit('/', 1)[0]
    metadata = json.loads(fetch(base + '/api/report')[1])
    copied = Path(metadata['report_directory'])
    assert copied != output.resolve()
    slot = papers(local)[0]['slots']['diff']
    assert Path(slot['request']['cache']) == output.parent / 'translation-cache'
    for path in output.rglob('*'):
        if path.is_file():
            assert (copied / path.relative_to(output)).read_bytes() == path.read_bytes()
    before = body
    (output / 'report.html').write_text('UNREGISTERED REPLACEMENT')
    assert fetch_report(link)[1] == before
    _, new_output = generate_diff(local, entry.parent, old='Another baseline.', new='Updated manuscript.',
                                  output=entry.parent / 'report-second')
    assert stable_link(local.cli('serve', new_output)) == link
    updated = fetch_report(link)[1]
    assert b'Updated manuscript.' in updated
    (new_output / 'escape').symlink_to(local.root / 'outside-secret')
    result = local.cli('serve', new_output)
    assert result.returncode == 4
    assert fetch_report(link)[1] == updated
    assert b'outside-secret' not in fetch_report(link)[1]


@pytest.mark.parametrize('poll', [False, True])
def test_a20_managed_live_refresh(local, poll):
    entry = paper(local.root / 'paper', 'Before refresh.')
    extra = ('--poll-files',) if poll else ()
    link = stable_link(local.cli('live', *live_args(entry, entry.parent, entry.parent / 'live', *extra)))
    assert b'Before refresh.' in fetch_report(link)[1]
    entry.write_text(entry.read_text().replace('Before refresh.', 'After refresh.'))
    eventually(lambda: b'After refresh.' in fetch_report(link)[1], timeout=40)


def test_a10_worker_root_apis_and_write_tokens(local):
    entry, output = generate_diff(local, local.root / 'paper')
    links = [stable_link(local.cli('serve', output)),
             stable_link(local.cli('live', *live_args(entry, entry.parent, entry.parent / 'live', '--poll-files')))]
    for link in links:
        _, _, worker = fetch_report(link)
        base = worker.rsplit('/', 1)[0]
        for route in ('/api/report', '/api/annotations/content', '/api/annotations/status',
                      '/api/annotations/export', '/api/translation/status'):
            status, body, _ = fetch(base + route)
            assert status == 200, (route, body)
            assert isinstance(json.loads(body), dict)
        exported = fetch(base + '/api/translation/export')
        assert exported[0] == 200
        assert b'<html' in exported[1]
        for route in ('/api/annotations/update', '/api/translation/start'):
            assert fetch(base + route, method='POST', data=b'{}',
                         headers={'Content-Type': 'application/json'})[0] == 403
        assert fetch(base + '/api/report', headers={'Host': 'evil.example'})[0] == 403
        assert fetch(base + '/api/report', headers={'Origin': 'https://evil.example'})[0] == 403


def process_running(pid):
    # Query only a PID written by our controlled child. Zombies have exited.
    result = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True)
    return result.returncode == 0 and bool(result.stdout.strip()) and not result.stdout.strip().startswith('Z')


@pytest.mark.parametrize('action', ['stop', 'sigterm', 'sigkill'])
def test_a6_owner_exit_during_real_rebuild_compile(tmp_path, action):
    import tempfile
    harness = LocalHarness(tmp_path)
    owner = None
    compiler_pids = []
    with tempfile.TemporaryDirectory(prefix='latex-review-acceptance-tools-') as tool_dir:
        tools = Path(tool_dir).resolve()
        marker = tmp_path / 'compiler.json'
        program = ('import json,os,subprocess,sys,time\n'
                  'from pathlib import Path\n'
                  'if "--version" in sys.argv: print("Latexmk pdfTeX"); sys.exit(0)\n'
                  'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(120)"])\n'
                  f'Path({str(marker)!r}).write_text(json.dumps('
                  '{"pid":os.getpid(),"child":child.pid,"pgid":os.getpgrp()}))\n'
                  'time.sleep(120)\n')
        script = '#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -c ' + shlex.quote(program) + ' "$@"\n'
        (tools / 'latexmk').write_text(script)
        (tools / 'latexmk').chmod(0o755)
        (tools / 'pdflatex').write_text('#!/bin/sh\necho pdfTeX\nexit 0\n')
        (tools / 'pdflatex').chmod(0o755)
        harness.env.update(PATH=str(tools) + os.pathsep + harness.env['PATH'],
                           ACCEPTANCE_COMPILER_MARKER=str(marker))
        try:
            entry, output = generate_diff(harness, tmp_path / 'paper')
            recipe_path = output / '.rebuild' / 'recipe.json'
            recipe = json.loads(recipe_path.read_text())
            recipe['configuration']['compile'] = {'enabled': True, 'new_only': True,
                                                  'engine': 'pdflatex', 'timeout': 120,
                                                  'sandbox_render': False}
            recipe_path.write_text(json.dumps(recipe))
            owner = harness.spawn('local_service', 'run', '--port', harness.port)
            eventually(lambda: (harness.state / 'endpoint.json').exists())
            harness.endpoint = json.loads((harness.state / 'endpoint.json').read_text())
            harness.base = f'http://127.0.0.1:{harness.port}'
            link = stable_link(harness.cli('serve', output))
            _, _, worker_url = fetch_report(link)
            base = worker_url.rsplit('/', 1)[0]
            content = json.loads(fetch(base + '/api/annotations/content')[1])
            unit = next(unit for unit in content['units'] if unit['kind'] == 'text' and 'New manuscript.' in unit['text'])
            status = json.loads(fetch(base + '/api/annotations/status')[1])
            def update(request):
                envelope = {'project_id': content['project_id'], 'version_id': content['version_id'], 'request': request}
                return fetch(base + '/api/annotations/update', method='POST', data=json.dumps(envelope).encode(),
                             headers={'Content-Type': 'application/json', 'X-LaTeX-Review-Token': status['token']})
            start = unit['text'].index('New manuscript.')
            code, body, _ = update({'operation': 'create', 'expected_revision': status['revision'],
                                   'selection': {'unit_id': unit['id'], 'start': start, 'end': start + 3},
                                   'body': 'Controlled rebuild.', 'author': {'name': 'Acceptance', 'type': 'user'},
                                   'category': 'suggestion', 'suggested_replacement':
                                   {'original_chunk': 'New manuscript.', 'replacement_chunk': 'Rebuilt manuscript.'}})
            assert code == 200, body
            created = json.loads(body)
            with ThreadPoolExecutor() as pool:
                pending = pool.submit(update, {'operation': 'suggestion_apply', 'expected_revision': created['revision'],
                                               'thread_id': created['result']['thread_id'],
                                               'message_id': created['result']['message_id']})
                compiler = eventually(lambda: json.loads(marker.read_text()) if marker.exists() else None, timeout=20)
                compiler_pids = [compiler['pid'], compiler['child']]
                assert all(process_running(pid) for pid in compiler_pids)
                if action == 'stop':
                    result = harness.cli('local_service', 'stop')
                    assert result.returncode == 0, result.stderr
                else:
                    owner.send_signal(signal.SIGTERM if action == 'sigterm' else signal.SIGKILL)
                    owner.wait(timeout=15)
                eventually(lambda: all(not process_running(pid) for pid in compiler_pids), timeout=10)
                try:
                    pending.result(timeout=5)
                except OSError:
                    pass  # Closing the HTTP owner may close the caller's connection.
            from latex_review.local_state import lock_busy
            eventually(lambda: not lock_busy(harness.state / 'slots' / f"{content['project_id']}.diff.lock"))
        finally:
            # These exact PIDs came from the compiler this test spawned.
            for pid in compiler_pids:
                if process_running(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            harness.close_clients()


def test_a10_a17_browser_dashboard_and_report_tools(local):
    from playwright.sync_api import sync_playwright
    entry, output = generate_diff(local, local.root / 'paper')
    diff = stable_link(local.cli('serve', output))
    live = stable_link(local.cli('live', *live_args(entry, entry.parent, entry.parent / 'live', '--poll-files')))
    evidence = Path(os.environ.get('LATEX_REVIEW_ACCEPTANCE_EVIDENCE', str(local.root / 'browser-evidence')))
    evidence.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page.goto(local.base)
            page.wait_for_load_state('networkidle')
            page.locator('.paper').wait_for()
            assert page.locator('.paper').count() == 1
            assert page.get_by_role('link', name='打开').count() == 2
            page.screenshot(path=str(evidence / 'dashboard-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(evidence / 'dashboard-mobile.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            for kind, link in (('diff', diff), ('live', live)):
                page.set_viewport_size({'width': 1280, 'height': 900})
                page.goto(link)
                page.wait_for_load_state('networkidle')
                assert page.url == link
                report = page.frames[1]
                report.wait_for_function('window.reviewAnnotations?.getContent() !== null')
                assert report.locator('body').inner_text().find('New manuscript.') >= 0
                # Paper APIs and annotation mutation use the same-origin worker prefix.
                created = report.evaluate('''async () => {
                    const api=reviewAnnotations;
                    const u=api.getContent().units.find(u=>u.kind==='text'&&u.text.includes('New manuscript.'));
                    const start=u.text.indexOf('New manuscript.');
                    return api.update({operation:'create',selection:{unit_id:u.id,start,end:start+3,
                      quote:'New',side:'new'},body:'Browser acceptance note',
                      author:{name:'Acceptance',type:'user'},category:'note'});
                }''')
                assert created
                assert report.evaluate("JSON.stringify(reviewAnnotations.getSnapshot()).includes('Browser acceptance note')")
                assert report.evaluate("fetch('api/annotations/export').then(r=>r.ok)")
                assert report.evaluate("fetch('api/translation/export').then(r=>r.ok)")
                # Search selectors are observed in the live and diff templates.
                search = report.locator('#live-search' if kind == 'live' else '#report-search')
                assert search.count() == 1
                search.evaluate('(e)=>{for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==="DETAILS")p.open=true}')
                search.fill('manuscript')
                search_status = report.locator('#live-search-status' if kind == 'live' else '#search-status')
                eventually(lambda: '输入' not in search_status.inner_text() and bool(search_status.inner_text()))
                page.screenshot(path=str(evidence / f'{kind}-desktop.png'), full_page=True)
                (evidence / f'{kind}-math-status.txt').write_text(report.locator('#math-status').inner_text())
            page.goto(local.base)
            page.wait_for_load_state('networkidle')
            live_slot = page.locator('.slot').filter(has=page.get_by_role('heading', name='持续预览', exact=True))
            live_slot.get_by_role('button', name='停止', exact=True).click()
            eventually(lambda: live_slot.locator('.state').inner_text() == '已停止', timeout=20)
            live_slot.get_by_role('button', name='启动', exact=True).click()
            eventually(lambda: live_slot.locator('.state').inner_text() == '运行中', timeout=30)
            assert live_slot.get_by_role('link', name='打开').evaluate('(a)=>a.href') == live
        finally:
            browser.close()


def test_a8_entry_port_conflict_and_protocol_mismatch(tmp_path):
    harness = LocalHarness(tmp_path)
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', harness.port))
        occupied.listen()
        result = harness.cli('local_service', 'run', '--port', harness.port)
        assert result.returncode == 4
        assert not (harness.state / 'endpoint.json').exists()
    harness.state.mkdir(parents=True, exist_ok=True)
    (harness.state / 'endpoint.json').write_text(json.dumps({'protocol': 999, 'nonce': 'old', 'port': harness.port,
                                                         'pid': os.getpid(), 'token': 'untrusted'}))
    result = harness.cli('local_service', 'start')
    assert result.returncode == 4 and '协议' in result.stderr
    assert os.getpid() > 0  # A diagnostic PID never authorizes termination.


def test_a8_explicit_worker_port_conflict(local):
    _, output = generate_diff(local, local.root / 'paper')
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        result = local.cli('serve', output, '--port', occupied.getsockname()[1])
        assert result.returncode == 4
    link = stable_link(local.cli('serve', output))
    result = local.cli('serve', output, '--port', unused_port())
    assert result.returncode == 4 and ('端口' in result.stderr or 'stop' in result.stderr)
    assert fetch_report(link)[0] == 200


def test_a17_public_entry_aliases_and_wait_detaches(local):
    entry, output = generate_diff(local, local.root / 'paper', output=local.root / '.latex-review' / 'latest')
    link = stable_link(local.cli('serve', output))
    assert stable_link(local.cli('serve', output / 'report.html')) == link
    assert stable_link(local.cli('serve', entry)) == link
    console = Path(sys.executable).parent / 'latex-review-preview'
    assert console.is_file(), 'Install project entry points before acceptance'
    result = subprocess.run([str(console), str(output)], cwd=local.root, env=local.env,
                            capture_output=True, text=True, timeout=150)
    assert stable_link(result) == link
    before = local.root / 'paper' / 'before.tex'
    assert stable_link(local.cli('cli', before, entry, '--output', output, '--open-browser')) == link
    waiting = local.spawn('serve', output, '--wait')
    eventually(lambda: link in Path(local.processes[-1][1].name).read_text(), timeout=20)
    waiting.send_signal(signal.SIGINT)
    waiting.wait(timeout=10)
    assert waiting.returncode == 0
    assert fetch_report(link)[0] == 200
    for module in ('live', 'serve'):
        assert '--standalone' not in local.cli(module, '--help').stdout
    assert local.cli('local_service', 'list').returncode == 0
    assert local.cli('local_service', 'doctor').returncode == 0


def test_a7_a9_crash_restart_stable_url_and_corrupt_registry(local):
    entry = paper(local.root / 'paper')
    args = live_args(entry, entry.parent, entry.parent / 'live', '--poll-files')
    link = stable_link(local.cli('live', *args))
    old_slot = papers(local)[0]['slots']['live']
    owner = local.processes[0][0]
    owner.kill()
    owner.wait(timeout=5)
    eventually(lambda: not process_running(old_slot['pid']), timeout=12)
    restarted = local.spawn('local_service', 'run', '--port', local.port)
    eventually(lambda: fetch(local.base + '/health')[0] == 200)
    local.endpoint = json.loads((local.state / 'endpoint.json').read_text())
    assert stable_link(local.cli('live', *args)) == link
    assert papers(local)[0]['slots']['live']['generation'] != old_slot['generation']
    assert fetch_report(link)[0] == 200
    # A forged PID in corrupt state must not authorize killing this test runner.
    restarted.terminate()
    restarted.wait(timeout=15)
    (local.state / 'registry.json').write_text('{"pid":' + str(os.getpid()))
    local.spawn('local_service', 'run', '--port', local.port)
    eventually(lambda: fetch(local.base + '/health')[0] == 200)
    assert list(local.state.glob('registry.corrupt-*.json'))
    assert papers(local) == []


def test_a7_legacy_slot_lock_prevents_second_worker(local):
    import fcntl
    from latex_review.identity import paper_identity
    entry = paper(local.root / 'paper')
    identity = paper_identity(entry.parent, entry)
    lock = local.state / 'slots' / f"{identity['paper_id']}.live.lock"
    lock.parent.mkdir(exist_ok=True)
    with lock.open('w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = local.cli('live', *live_args(entry, entry.parent, entry.parent / 'live', '--poll-files'))
        assert result.returncode == 4
        assert '锁' in result.stderr
        assert papers(local)[0]['slots']['live']['state'] == 'blocked'
    link = stable_link(local.cli('live', *live_args(entry, entry.parent, entry.parent / 'live', '--poll-files')))
    assert fetch_report(link)[0] == 200


def test_a3_baselines_update_in_accepted_order(local):
    entry, first = generate_diff(local, local.root / 'paper')
    link = stable_link(local.cli('serve', first))
    _, second = generate_diff(local, entry.parent, old='Second baseline.', new='Second revision.',
                              output=entry.parent / 'second-report')
    _, third = generate_diff(local, entry.parent, old='Third baseline.', new='Third revision.',
                             output=entry.parent / 'third-report')
    record = papers(local)[0]
    request = record['slots']['diff']['request']
    operations = []
    for index, directory in enumerate((second, third)):
        status, body, _ = control(local, f"/api/v1/papers/{record['paper_id']}/diff/ensure",
                                  {**request, 'directory': str(directory), 'request_id': f'baseline-{index}'})
        assert status == 202, body
        operations.append(json.loads(body)['operation_id'])
    for operation in operations:
        state = eventually(lambda: (value if (value := json.loads(fetch(local.base + '/api/v1/operations/' + operation)[1]))['state']
                                     in ('ready', 'failed', 'cancelled') else None), timeout=30)
        assert state['state'] == 'ready', state
    assert b'Third revision.' in fetch_report(link)[1]
    assert len(papers(local)) == 1
    assert papers(local)[0]['slots']['diff']['state'] == 'ready'


def test_a19_missing_resource_rejects_candidate_without_losing_old(local):
    import base64
    _, output = generate_diff(local, local.root / 'paper')
    assets = output / 'assets'
    assets.mkdir(exist_ok=True)
    image = assets / 'acceptance.png'
    image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jTXkAAAAASUVORK5CYII='))
    report = output / 'report.html'
    report.write_text(report.read_text().replace('</body>', '<img src="assets/acceptance.png" alt="Acceptance"></body>'))
    link = stable_link(local.cli('serve', output))
    original = fetch_report(link)[1]
    _, _, worker = fetch_report(link)
    assert fetch(worker.rsplit('/', 1)[0] + '/assets/acceptance.png')[0] == 200
    image.unlink()
    result = local.cli('serve', output)
    assert result.returncode == 4, 'Missing referenced resource must not become an active candidate'
    assert fetch_report(link)[1] == original


def test_a10_managed_translation_uses_real_provider_and_preserves_cache(local):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    calls = []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            source = json.loads(request['messages'][1]['content'])
            calls.append(source)
            translated = {'sentences': [{'id': sentence['id'], 'text': '译：' + sentence['text']}
                                       for sentence in source['sentences']]}
            data = json.dumps({'choices': [{'finish_reason': 'stop', 'message':
                              {'content': json.dumps(translated, ensure_ascii=False)}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    with ThreadingHTTPServer(('127.0.0.1', 0), Provider) as provider:
        thread = Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        local.env['ACCEPTANCE_TRANSLATION_KEY'] = 'fake-local-only-key'
        config = local.root / 'translation.toml'
        config.write_text('[translation]\n' + f'base_url="http://127.0.0.1:{provider.server_port}/v1"\n'
                          'model="acceptance"\napi_key_env="ACCEPTANCE_TRANSLATION_KEY"\nmax_retries=0\n')
        try:
            entry, output = generate_diff(local, local.root / 'paper')
            for kind, args in (('diff', (output, '--config', config)),
                               ('live', live_args(entry, entry.parent, entry.parent / 'live', '--poll-files', '--config', config))):
                link = stable_link(local.cli('serve' if kind == 'diff' else 'live', *args))
                _, _, worker = fetch_report(link)
                base = worker.rsplit('/', 1)[0]
                snapshot = json.loads(fetch(base + '/api/translation/status')[1])
                assert snapshot['enabled']
                units = [unit['id'] for unit in json.loads((output if kind == 'diff' else entry.parent / 'live').joinpath('translation-units.json').read_text())]
                status, body, _ = fetch(base + '/api/translation/start', method='POST',
                                       data=json.dumps({'units': units}).encode(),
                                       headers={'Content-Type': 'application/json', 'X-LaTeX-Review-Token': snapshot['token']})
                assert status == 200, body
                translated = eventually(lambda: (value if len((value := json.loads(fetch(base + '/api/translation/status')[1]))['results']) == len(units) else None))
                assert translated['results']
                assert '译：'.encode() in fetch(base + '/api/translation/export')[1]
                assert calls
                assert b'fake-local-only-key' not in fetch(local.base + '/api/v1/papers')[1]
                call_count = len(calls)
                record = papers(local)[0]
                stopped = local.cli('local_service', 'stop-paper', record['paper_id'], '--kind', kind)
                assert stopped.returncode == 0, stopped.stderr
                assert stable_link(local.cli('serve' if kind == 'diff' else 'live', *args)) == link
                _, _, restored_worker = fetch_report(link)
                restored = json.loads(fetch(restored_worker.rsplit('/', 1)[0] + '/api/translation/status')[1])
                assert restored['results']
                assert len(calls) == call_count
        finally:
            provider.shutdown()
            thread.join()


def test_a18_public_writer_cannot_overlap_live_output(local):
    entry, _ = generate_diff(local, local.root / 'paper')
    live_output = entry.parent / 'managed-output' / 'live'
    link = stable_link(local.cli('live', *live_args(entry, entry.parent, live_output, '--poll-files')))
    before = entry.parent / 'before.tex'
    for output in (live_output, live_output / 'child', live_output.parent):
        result = local.cli('cli', before, entry, '--output', output, timeout=135)
        assert result.returncode == 4, result.stderr
        assert fetch_report(link)[0] == 200
    separate = local.cli('cli', before, entry, '--output', entry.parent / 'independent')
    assert separate.returncode in (0, 2), separate.stderr
    assert fetch_report(link)[0] == 200
