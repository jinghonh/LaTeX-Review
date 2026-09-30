"""配置测试与命令行子进程使用独立的用户配置目录。"""

import pytest


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path, monkeypatch):
    # 子进程继承该环境变量，避免个人全局配置改变测试的模型或参数。
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
