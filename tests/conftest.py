"""配置测试与命令行子进程使用独立的用户配置目录。"""

import pytest
import os
from pathlib import Path
import socket


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path, monkeypatch):
    # 子进程继承该环境变量，避免个人全局配置改变测试的模型或参数。
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    monkeypatch.setenv("LATEX_REVIEW_STATE_HOME", str(tmp_path / "local-state"))
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        monkeypatch.setenv('LATEX_REVIEW_LOCAL_PORT', str(probe.getsockname()[1]))
    yield
    # Only the daemon created in this test's private state is eligible for stop.
    if not (Path(os.environ['LATEX_REVIEW_STATE_HOME']) / 'endpoint.json').exists():
        return
    from latex_review import local_client
    try:
        local_client.stop()
    except (ValueError, OSError):
        pass
