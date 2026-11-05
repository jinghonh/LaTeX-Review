"""配置测试与命令行子进程使用独立的用户配置目录。"""

import pytest
import os
from pathlib import Path
import socket


def browser_scope(fixture_name, config):
    scope = os.environ.get('LATEX_REVIEW_BROWSER_SCOPE', 'module')
    if scope not in ('module', 'function'):
        raise pytest.UsageError('LATEX_REVIEW_BROWSER_SCOPE must be module or function')
    return scope


@pytest.fixture(scope=browser_scope)
def module_browser():
    # Other modules still manage their own sync Playwright runtimes. Limiting
    # reuse to a module avoids nesting their event loops in a session runtime.
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def isolated_browser(module_browser):
    # Preserve Browser.new_page(): every implicit page has its OWN context.
    # Explicit new_context() still lets a test share storage between its tabs.
    assert not module_browser.contexts, 'A previous test left browser contexts open'
    try:
        yield module_browser
    finally:
        for context in list(module_browser.contexts):
            context.close()
        assert not module_browser.contexts


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
