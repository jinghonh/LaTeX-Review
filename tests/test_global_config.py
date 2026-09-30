"""全局翻译默认值、项目覆盖、术语合并及预览自动加载。"""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from urllib.request import urlopen

import pytest

from latex_review.cli import ConfigurationError, _config, global_config_path


def shared_config(text):
    path = global_config_path()
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


BASE = '[translation]\nbase_url="https://example.com/v1"\nmodel="shared-model"\nconcurrency=8\n'


def test_default_location_and_xdg_override(tmp_path, monkeypatch):
    assert global_config_path() == Path(os.environ["XDG_CONFIG_HOME"]) / "latex-review/config.toml"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert global_config_path() == tmp_path / ".config/latex-review/config.toml"
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative-path")
    with pytest.raises(ConfigurationError, match="绝对路径"):
        global_config_path()


def test_projects_without_configuration_share_defaults(tmp_path):
    shared_config(BASE)
    for name in ("paper-a", "paper-b"):
        project = tmp_path / name
        project.mkdir()
        config = _config(project / ".latex-review.toml", False)
        assert config == {"translation": {"base_url": "https://example.com/v1", "model": "shared-model", "concurrency": 8}}


def test_partial_project_override_and_glossary_merge(tmp_path):
    shared_config(BASE + '[translation.glossary]\noperator="算子"\n"reachable set"="可达集"\n')
    project = tmp_path / ".latex-review.toml"
    project.write_text('entry="main.tex"\noutput="project-report"\n[diff]\ncomments=true\n'
                       '[translation]\nmodel="project-model"\ntimeout=30\n'
                       '[translation.glossary]\noperator="运算子"\nnetwork="网络"\n')
    merged = _config(project, True)
    assert merged["entry"] == "main.tex" and merged["output"] == "project-report"
    assert merged["diff"] == {"comments": True}
    assert merged["translation"] == {"base_url": "https://example.com/v1", "model": "project-model",
        "concurrency": 8, "timeout": 30, "glossary": {"operator": "运算子", "reachable set": "可达集", "network": "网络"}}
    assert _config(tmp_path / "other-project.toml", False)["translation"]["glossary"]["operator"] == "算子"


def test_explicit_file_selects_project_layer(tmp_path, monkeypatch):
    shared_config(BASE)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".latex-review.toml").write_text('[translation]\nmodel="ignored-cwd-model"\n')
    (tmp_path / "selected.toml").write_text('[translation]\nconcurrency=4\n')
    config = _config(Path("selected.toml"), True)
    assert config["translation"]["model"] == "shared-model"
    assert config["translation"]["concurrency"] == 4
    with pytest.raises(ConfigurationError, match="无法读取"):
        _config(tmp_path / "missing.toml", True)


@pytest.mark.parametrize("content", [
    'entry="unexpected-paper.tex"\n' + BASE,
    '[compile]\nenabled=true\n' + BASE,
    '[translation]\napi_key="never-store-secrets"\nbase_url="https://example.com"\nmodel="test"\n',
    '[translation]\nbase_url="https://example.com"\nmodel="test"\nglossary=[]\n',
    '[translation]\nbase_url="https://example.com"\n',
    '[translation',
])
def test_invalid_global_configuration_reports_error_without_echoing_values(tmp_path, content):
    shared_config(content)
    with pytest.raises(ConfigurationError) as error:
        _config(tmp_path / ".latex-review.toml", False)
    assert "never-store-secrets" not in str(error.value)


def test_empty_template_does_not_enable_translation(tmp_path):
    shared_config('# 在此填写全局翻译配置\n')
    assert _config(tmp_path / ".latex-review.toml", False) == {}


def test_preview_automatically_loads_global_defaults_without_api_call(tmp_path):
    shared_config(BASE)
    project = tmp_path / "paper"
    report = project / ".latex-review/latest"
    report.mkdir(parents=True)
    (report / "report.html").write_text('<h1>原文报告</h1>', encoding="utf-8")
    process = subprocess.Popen([sys.executable, '-m', 'latex_review.serve', str(report)],
                               cwd=project, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with ThreadPoolExecutor() as pool:
            address = pool.submit(process.stdout.readline).result(timeout=10).strip()
        assert address.startswith('http://127.0.0.1:')
        base = address.removesuffix('/report.html')
        with urlopen(base + '/api/translation/status', timeout=3) as response:
            status = json.load(response)
        assert status["enabled"] and status["concurrency"] == 8
        assert status["states"] == {} and status["results"] == {}
        with urlopen(address, timeout=3) as response:
            assert '原文报告' in response.read().decode()
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()
