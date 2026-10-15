"""独立发布不能依赖受管服务；网络仅使用本地 SSH stub。"""
from contextlib import contextmanager
import json
import os
import re
import signal
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace

import pytest
from latex_review import cli, delivery, serve, upload
from test_cli import project
from test_annotations import envelope

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def paper(tmp_path, monkeypatch):
    root = tmp_path / 'paper'
    root.mkdir()
    project(root)
    monkeypatch.chdir(root)
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('LATEX_REVIEW_STATE_HOME', str(tmp_path / 'state'))
    config = tmp_path / 'config' / 'latex-review' / 'upload.toml'
    config.parent.mkdir(parents=True)
    config.write_text(f'host="stub"\nbase_url="https://example.invalid"\n[[papers]]\nentry="{root / "main.tex"}"\nslug="paper"\n')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(config.parent.parent))
    return root


class Remote:
    """检查真正的归档和回执协议，不替换 upload_report。"""
    def __init__(self, monkeypatch, fail=False, before_query=None):
        self.calls = []
        self.archives = []
        self.fail = fail
        self.before_query = before_query
        monkeypatch.setattr(upload, '_ssh', self.ssh)

    def ssh(self, host, command, **kwargs):
        assert host == 'stub'
        self.calls.append(command)
        if self.fail:
            return SimpleNamespace(returncode=255, stdout=b'', stderr=b'stub offline')
        if command == 'resource-query-v1':
            if self.before_query:
                self.before_query()
            manifest = json.loads(kwargs['input'])
            value = {'protocol': 1, 'slug': manifest['slug'],
                     'manifest_id': delivery.manifest_id(manifest),
                     'missing': sorted({r['sha256'] for r in manifest['resources'].values()})}
        else:
            assert command == 'publish-incremental-v1'
            with tarfile.open(fileobj=kwargs['stdin']) as archive:
                files = {name: archive.extractfile(name).read() for name in archive.getnames()}
            self.archives.append(files)
            metadata = json.loads(files['release.json'])
            value = {'slug': metadata['slug'], 'release': metadata['release']}
        return SimpleNamespace(returncode=0, stdout=json.dumps(value).encode(), stderr=b'')


@contextmanager
def forbid_entry_client(monkeypatch):
    from latex_review import local_client
    def forbidden(*args, **kwargs):
        raise AssertionError('纯上传调用入口客户端')
    with monkeypatch.context() as patch:
        for name in ('endpoint', 'request', 'start', 'ensure', 'follow', 'stop'):
            patch.setattr(local_client, name, forbidden)
        yield


@pytest.mark.parametrize('entry_state', ['absent', 'corrupt', 'port-conflict'])
def test_upload_ignores_entry_service_state(paper, monkeypatch, entry_state):
    state = Path(os.environ['LATEX_REVIEW_STATE_HOME'])
    state.mkdir()
    if entry_state == 'corrupt':
        for name in ('endpoint.json', 'registry.json'):
            (state / name).write_text('{broken')
    before = {p.name: p.read_bytes() for p in state.iterdir()}
    remote = Remote(monkeypatch)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        if entry_state == 'port-conflict':
            (state / 'config.json').write_text(json.dumps({'port': listener.getsockname()[1]}))
            before['config.json'] = (state / 'config.json').read_bytes()
        with forbid_entry_client(monkeypatch):
            assert cli.main(['main.tex', '--upload']) == 0
        # 上传不得关闭其他监听者。
        with socket.create_connection(listener.getsockname(), timeout=1):
            pass
    assert remote.calls == ['resource-query-v1', 'publish-incremental-v1']
    assert all((state / name).read_bytes() == value for name, value in before.items())


@pytest.mark.parametrize('upload_ok,browser,preview_code,expected', [
    (True, False, 0, 0), (False, False, 0, 16),
    (True, True, 0, 0), (False, True, 0, 16),
    (True, True, 4, 4), (False, True, 4, 4),
])
def test_upload_preview_exit_priority(paper, monkeypatch, upload_ok, browser, preview_code, expected):
    remote = Remote(monkeypatch, fail=not upload_ok)
    preview_calls = []
    def preview(args):
        assert remote.calls, '预览必须在上传之后'
        preview_calls.append(args)
        return preview_code
    monkeypatch.setattr(serve, 'main', preview)
    assert cli.main(['main.tex', '--upload'] + (['--open-browser'] if browser else [])) == expected
    assert bool(preview_calls) == browser
    run = json.loads((paper / '.latex-review/latest/run.json').read_text())
    assert run['stages']['upload'] == ('complete' if upload_ok else 'failed')
    if browser:
        assert run['stages']['preview'] == ('complete' if preview_code == 0 else 'failed')


@pytest.mark.parametrize('upload_ok,browser,preview_code,expected', [
    (True, False, 0, 2), (True, True, 0, 2),
    (False, False, 0, 16), (True, True, 4, 4),
])
def test_upload_preserves_diagnostic_exit_code(paper, monkeypatch, upload_ok, browser, preview_code, expected):
    (paper / 'main.tex').write_text('New text. \\odd{unpreviewable}\n')
    remote = Remote(monkeypatch, fail=not upload_ok)
    monkeypatch.setattr(serve, 'main', lambda args: preview_code)
    assert cli.main(['main.tex', '--upload'] + (['--open-browser'] if browser else [])) == expected
    assert len(remote.archives) == int(upload_ok)


def test_repeated_baselines_outputs_and_papers_publish_independently(paper, monkeypatch):
    remote = Remote(monkeypatch)
    for index in range(3):
        baseline = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=paper, text=True).strip()
        (paper / 'main.tex').write_text(f'\\section{{A}}\nRevision {index}.\n')
        assert cli.main(['main.tex', '--old', baseline, '--upload', '--output', str(paper / f'output-{index}')]) == 0
        subprocess.run(['git', 'add', 'main.tex'], cwd=paper, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-qm', f'baseline-{index}'], cwd=paper, check=True, capture_output=True)
    other = paper / 'other'
    other.mkdir()
    project(other)
    config = Path(os.environ['XDG_CONFIG_HOME']) / 'latex-review/upload.toml'
    with config.open('a') as stream:
        stream.write(f'[[papers]]\nentry="{other / "main.tex"}"\nslug="other-paper"\n')
    assert cli.main([str(other / 'main.tex'), '--upload', '--output', str(paper / 'other-output')]) == 0
    releases = [json.loads(a['release.json'])['release'] for a in remote.archives]
    assert len(set(releases)) == 4
    assert len({json.loads(a['release.json'])['comparison'] for a in remote.archives[:3]}) == 3
    assert remote.calls == ['resource-query-v1', 'publish-incremental-v1'] * 4
    assert json.loads(remote.archives[-1]['release.json'])['slug'] == 'other-paper'
    for index, archive in enumerate(remote.archives[:3]):
        assert f'Revision {index}.' in archive['report.html'].decode()


@pytest.mark.parametrize('browser', [False, True])
def test_cli_freezes_current_report_and_releases_lock_before_ssh(paper, monkeypatch, browser):
    output = paper / 'report'
    (paper / 'main.tex').write_text('Frozen publication text.\n')
    def mutate_after_freeze():
        # 独立进程取得真实 flock，证明网络阶段没有继承或持有生成锁。
        code = '''from pathlib import Path
import fcntl,os,sys
from latex_review.output_leases import output_lease
report=Path(sys.argv[1])
with output_lease(report,timeout=2):
    fd=os.open(report.parent/('.'+report.name+'.rebuild.lock'),os.O_CREAT|os.O_RDWR,0o600)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    (report/'report.html').write_text('<html><head></head><body>Foreign replacement.</body></html>')
    (report/'diff.json').write_text('{}')
'''
        result = subprocess.run([sys.executable, '-c', code, str(output)],
                                env=dict(os.environ, PYTHONPATH=str(ROOT / 'src')),
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
    remote = Remote(monkeypatch, before_query=mutate_after_freeze)
    previews = []
    def preview(args):
        previews.append(args)
        snapshot = (Path(args[0]) / 'report.html').read_text()
        assert 'Frozen publication text.' in snapshot
        assert 'Foreign replacement.' not in snapshot
        return 0
    monkeypatch.setattr(serve, 'main', preview)
    assert cli.main(['main.tex', '--upload', '--output', str(output)] + (['--open-browser'] if browser else [])) == 0
    assert bool(previews) == browser
    html = remote.archives[0]['report.html'].decode()
    assert 'Frozen publication text.' in html
    assert 'Foreign replacement.' not in html
    assert 'Foreign replacement.' in (output / 'report.html').read_text()



def test_freeze_keeps_resource_bytes_and_annotation_revision(paper, monkeypatch):
    from latex_review.annotations import AnnotationStore, load_content
    output = paper / 'resource-report'
    original = cli.write_report
    notes = {}
    def add_resource_and_note(*args, **kwargs):
        result = original(*args, **kwargs)
        resource = output / 'assets/new/chart.png'
        resource.parent.mkdir(parents=True, exist_ok=True)
        resource.write_bytes(b'original-resource')
        entry = output / 'report.html'
        entry.write_text(entry.read_text().replace('</main>', '<img src="assets/new/chart.png"></main>'))
        content = load_content(output)
        store = AnnotationStore.open(content)
        unit = next(u for u in content['units'] if u['kind'] == 'text' and len(u['text']) >= 3)
        created = store.execute(envelope(content, {'operation': 'create', 'expected_revision': 0,
            'selection': {'unit_id': unit['id'], 'start': 0, 'end': 3},
            'body': 'Frozen note.', 'author': {'name': 'Tester', 'type': 'user'}, 'category': 'note'}))
        notes.update(content=content, store=store, thread=created['result']['thread_id'])
        return result
    monkeypatch.setattr(cli, 'write_report', add_resource_and_note)
    def after_freeze():
        (output / 'assets/new/chart.png').write_bytes(b'replaced-resource')
        notes['store'].execute(envelope(notes['content'], {'operation': 'reply', 'expected_revision': 1,
            'thread_id': notes['thread'], 'body': 'Later note.',
            'author': {'name': 'Tester', 'type': 'user'}, 'category': 'note'}))
    remote = Remote(monkeypatch, before_query=after_freeze)
    assert cli.main(['main.tex', '--upload', '--output', str(output)]) == 0
    archive = remote.archives[0]
    assert archive['assets/new/chart.png'] == b'original-resource'
    html = archive['report.html'].decode()
    assert 'src="assets/new/chart.png"' in html
    packed = json.loads(re.search(r'id="annotation-backup-gzip">(.*?)</script>', html, re.S)[1])
    bundle = delivery.decode_annotation_backup(packed)
    assert bundle['revision'] == 1
    assert bundle['threads'][notes['thread']]['messages'][0]['body'] == 'Frozen note.'
    assert len(bundle['threads'][notes['thread']]['messages']) == 1
    assert notes['store'].export()['revision'] == 2

def test_static_publication_has_no_entry_credentials_or_path_prefix(paper, monkeypatch):
    state = Path(os.environ['LATEX_REVIEW_STATE_HOME'])
    state.mkdir()
    sentinel = 'private-entry-token-should-never-be-published'
    (state / 'endpoint.json').write_text(json.dumps({'token': sentinel, 'nonce': 'private-entry-nonce'}))
    remote = Remote(monkeypatch)
    assert cli.main(['main.tex', '--upload']) == 0
    archive = remote.archives[0]
    html = archive['report.html'].decode()
    assert 'name="latex-review-publication" content="static"' in html
    assert '/papers/' not in html
    assert sentinel not in html and 'private-entry-nonce' not in html
    assert 'X-LaTeX-Review-Local-Token' not in html
    assert 'localhost:8765' not in html and '127.0.0.1:8765' not in html
    assert set(archive) == {'report.html', 'release.json'}
    assert 'id="annotation-backup-gzip"' in html


# 子进程在真正 write_report 内暂停。该暂停只用于观察输出事务。
_CHILD = '''import json,sys,time
from pathlib import Path
from latex_review import cli
output,marker,release=map(Path,sys.argv[1:4])
original=cli.write_report
def paused(*args,**kwargs):
    marker.write_text('entered')
    deadline=time.monotonic()+20
    while not release.exists():
        if time.monotonic()>deadline: raise RuntimeError('test barrier timeout')
        time.sleep(.025)
    return original(*args,**kwargs)
cli.write_report=paused
marker.with_suffix('.started').write_text('started')
raise SystemExit(cli.main(['main.tex','--output',str(output),'--no-cache']))
'''


def wait_file(path, process, timeout=10):
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert process.poll() is None, f'子进程提前退出：{process.returncode}'
        assert time.monotonic() < deadline, f'等待标记超时：{path}'
        time.sleep(.025)


@pytest.mark.parametrize('same_output', [True, False])
def test_real_cli_output_serialization(paper, tmp_path, same_output):
    outputs = [paper / 'serial-output', paper / ('serial-output' if same_output else 'parallel-output')]
    markers = [tmp_path / f'entered-{index}' for index in range(2)]
    release = tmp_path / 'release'
    processes = []
    logs = []
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
    try:
        for index in range(2):
            log = (tmp_path / f'child-{index}.log').open('w+')
            logs.append(log)
            processes.append(subprocess.Popen([sys.executable, '-c', _CHILD, str(outputs[index]),
                                               str(markers[index]), str(release)], cwd=paper, env=env,
                                              stdout=log, stderr=log))
            if index == 0:
                wait_file(markers[0], processes[0])
        wait_file(markers[1].with_suffix('.started'), processes[1])
        probe = subprocess.run([sys.executable, '-c',
            'import fcntl,os,sys; fd=os.open(sys.argv[1],os.O_RDWR); fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)',
            str(outputs[0].parent / ('.' + outputs[0].name + '.rebuild.lock'))],
            capture_output=True, text=True, timeout=5)
        assert probe.returncode != 0 and 'BlockingIOError' in probe.stderr, probe.stderr
        if same_output:
            # 第二请求已启动，却不能进入写报告阶段。
            time.sleep(.5)
            assert not markers[1].exists()
            assert processes[1].poll() is None
        else:
            wait_file(markers[1], processes[1])
        release.touch()
        for index, process in enumerate(processes):
            code = process.wait(timeout=20)
            logs[index].seek(0)
            assert code == 0, logs[index].read()
        assert all(marker.exists() for marker in markers)
    finally:
        release.touch(exist_ok=True)
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        for log in logs:
            log.close()


def test_real_replay_subprocess_does_not_deadlock(paper):
    output = paper / 'replay-report'
    assert cli.main(['main.tex', '--output', str(output)]) == 0
    (paper / 'main.tex').write_text('Updated by replay.\n')
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
    process = subprocess.Popen([sys.executable, '-c',
        'from latex_review.report_rebuild import rebuild_report; import json,sys; print(json.dumps(rebuild_report(sys.argv[1])))',
        str(output)], cwd=paper, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        # 仅清理此测试创建的独立进程组，包含它的 replay 子进程。
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)
        raise
    assert process.returncode == 0, stderr
    value = json.loads(stdout.splitlines()[-1])
    assert value['status'] == 'updated', value
    assert 'Updated by replay.' in (output / 'report.html').read_text()
