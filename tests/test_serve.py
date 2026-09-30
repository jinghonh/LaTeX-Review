"""报告预览的真实 HTTP 访问与进程生命周期。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import signal
import socket
import subprocess
import sys
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from latex_review.serve import create_server


@pytest.fixture
def report(tmp_path):
    root = tmp_path / '报告 目录'
    root.mkdir()
    (root / 'report.html').write_text('<h1>审阅</h1>', encoding='utf-8')
    (root / 'assets').mkdir()
    (root / 'assets' / 'image.svg').write_text('<svg/>')
    return root


def test_http_report_resources_and_directory_boundary(report, tmp_path):
    outside = tmp_path / 'secret.txt'
    outside.write_text('secret')
    (report / 'escape').symlink_to(outside)
    with create_server(report) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base + '/report.html') as response:
                assert '审阅' in response.read().decode()
                assert response.headers['Cache-Control'] == 'no-store'
            with urlopen(base + '/assets/image.svg') as response:
                assert response.read() == b'<svg/>'
            for path in ('/', '/assets/', '/missing', '/escape', '/../secret.txt', '/%2e%2e/secret.txt'):
                with pytest.raises(HTTPError) as error:
                    urlopen(base + path)
                assert error.value.code in (403, 404)
        finally:
            server.shutdown()
            worker.join()


def test_server_start_does_not_require_host_name_lookup(report, monkeypatch):
    def unavailable(*args):
        raise OSError("主机名反查不可用")
    monkeypatch.setattr(socket, "getfqdn", unavailable)
    with create_server(report) as server:
        assert server.server_name == "localhost" and server.server_port > 0


@pytest.mark.parametrize('stop_signal', [signal.SIGINT, signal.SIGTERM])
def test_cli_stops_and_releases_port(report, stop_signal):
    process = subprocess.Popen([sys.executable, '-m', 'latex_review.serve', str(report)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(process.stdout.readline)
            try:
                address = pending.result(timeout=10).strip()
            except TimeoutError:
                process.kill()
                raise
        assert address.startswith('http://127.0.0.1:')
        with urlopen(address, timeout=3) as response:
            assert response.status == 200
        port = int(address.split(':')[2].split('/')[0])
        process.send_signal(stop_signal)
        assert process.wait(timeout=5) == 0
        with create_server(report, port):
            pass
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def test_invalid_report_and_occupied_port(report, tmp_path):
    with pytest.raises(ValueError):
        create_server(tmp_path)
    with create_server(report) as server:
        with pytest.raises(OSError):
            create_server(report, server.server_port)


@pytest.mark.parametrize('input_kind', ['html', 'tex', 'configured_tex'])
def test_cli_accepts_report_file_or_paper_entry(tmp_path, input_kind):
    configured = input_kind == 'configured_tex'
    directory = tmp_path / ('自定义报告' if configured else '.latex-review/latest')
    directory.mkdir(parents=True)
    (directory / 'report.html').write_text('<h1>已有审阅报告</h1>', encoding='utf-8')
    entry = tmp_path / 'neural_networks/main.tex'
    entry.parent.mkdir()
    entry.write_text(r'\begin{document}论文\end{document}', encoding='utf-8')
    target = directory / 'report.html' if input_kind == 'html' else entry.relative_to(tmp_path)
    command = [sys.executable, '-m', 'latex_review.serve', str(target)]
    if configured:
        config = tmp_path / 'selected.toml'
        config.write_text('output="自定义报告"\n', encoding='utf-8')
        command.extend(['--config', str(config)])
    process = subprocess.Popen(command, cwd=tmp_path, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(process.stdout.readline)
            try:
                address = pending.result(timeout=10).strip()
            except TimeoutError:
                process.kill()
                raise
        assert address.startswith('http://127.0.0.1:'), process.stderr.read()
        with urlopen(address, timeout=3) as response:
            assert '已有审阅报告' in response.read().decode()
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()
