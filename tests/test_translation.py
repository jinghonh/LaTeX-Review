"""段落翻译的结构保护、真实本机 HTTP、缓存、取消与浏览器回归。"""

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from threading import Event, Lock, Thread
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from latex_review import compare_projects, parse_project, resolve_sources, write_report
from latex_review import translation as translation_module
from latex_review.cli import _config, ConfigurationError
from latex_review.serve import create_server
from latex_review.translation import (TranslationConfig, TranslationError, TranslationManager,
                                      RULE_VERSION, _protected_sentence, cache_key, rendered_result, request_translation,
                                      validate_translation)


def _browser_api():
    if os.environ.get("LATEX_REVIEW_REQUIRE_BROWSER") == "1":
        from playwright import sync_api
        return sync_api
    return pytest.importorskip("playwright.sync_api")


def _launch_browser(playwright, api):
    try:
        return playwright.chromium.launch(executable_path=os.environ.get("LATEX_REVIEW_BROWSER"))
    except api.Error as exc:
        if os.environ.get("LATEX_REVIEW_REQUIRE_BROWSER") == "1":
            pytest.fail(f"必需的浏览器运行环境不可用：{exc}")
        pytest.skip(f"浏览器运行环境不可用：{exc}")


def _content_report(tmp_path, before, after):
    for side, content in (("old", before), ("new", after)):
        root = tmp_path / side
        root.mkdir()
        (root / "main.tex").write_text("\\begin{document}\n" + content + "\n\\end{document}")
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        return write_report(old, new, compare_projects(old, new), tmp_path / "report", pdf_converter="")


@pytest.fixture
def translation_report(tmp_path):
    for side, word in (("old", "small"), ("new", "large")):
        directory = tmp_path / side
        directory.mkdir()
        (directory / "main.tex").write_text(
            "\\begin{document}\n\\section{Results}\n"
            + "Keep this sentence. % private note\n"
            + rf"We found a {word} gain of 3.14 with $x_1$ \cite{{same}} and \textbf{{strong evidence}}. "
            + "Another stable sentence.\n\nAn entirely unchanged paragraph.\n\n"
            + rf"\begin{{table}}\begin{{tabular}}{{l}}{word} cell\\\end{{tabular}}\end{{table}}"
            + "\n\\end{document}", encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        report = write_report(old, new, compare_projects(old, new), tmp_path / "report", pdf_converter="")
    return report, json.loads((report.directory / "translation-units.json").read_text())


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "fake-translation-key")
    calls, codes = [], []
    lock = Lock()
    gate = Event()
    gate.set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with lock:
                calls.append({"path": self.path, "authorization": self.headers.get("Authorization"), "body": data})
                code = codes.pop(0) if codes else 200
            gate.wait(5)
            if code != 200:
                self.send_response(code)
                self.end_headers()
                self.wfile.write(b"fake-translation-key remote failure")
                return
            source = json.loads(data["messages"][1]["content"])
            translated = {"sentences": [{"id": s["id"], "text": "译：" + s["text"]} for s in source["sentences"]]}
            body = json.dumps({"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(translated, ensure_ascii=False)}}]}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        config = TranslationConfig(f"http://127.0.0.1:{server.server_port}/v1", "test-model",
                                   api_key_env="TEST_TRANSLATION_KEY", max_retries=0)
        yield config, calls, codes, gate
        gate.set()
        server.shutdown()
        worker.join()


def _wait(condition):
    deadline = time.monotonic() + 8
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("异步翻译未在预期时间内完成")
        time.sleep(.02)


def test_changed_paragraph_includes_unchanged_sentences_but_skips_unchanged_nodes_and_tables(translation_report):
    report, units = translation_report
    assert len(units) == 2
    assert {u["side"] for u in units} == {"old", "new"}
    for unit in units:
        assert unit["kind"] == "paragraph"
        assert len(unit["sentences"]) == 3
        assert [s["changed"] for s in unit["sentences"]] == [False, True, False]
        assert "private note" not in json.dumps(unit)
        assert "entirely unchanged" not in json.dumps(unit)
        protected = unit["sentences"][1]["protected"]
        assert {p["latex"] for p in protected} >= {"3.14", "$x_1$", r"\cite{same}", r"\textbf{", "}"}
        assert "strong evidence" in unit["sentences"][1]["text"]
    html = report.html.read_text()
    assert 'id="translation-units"' in html and 'id="translation-results"' in html
    assert "fake-translation-key" not in html


def test_title_and_caption_text_are_selected_without_image_paths(tmp_path):
    for side, word in (("old", "Previous"), ("new", "Current")):
        root = tmp_path / side
        root.mkdir()
        (root / "main.tex").write_text(r"\begin{document}\section{" + word + " title}"
            + r"\begin{figure}\includegraphics{private.png}\caption{" + word + " caption}"
            + r"\end{figure}\end{document}")
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        old, new = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        report = write_report(old, new, compare_projects(old, new), tmp_path / "report", pdf_converter="")
    units = json.loads((report.directory / "translation-units.json").read_text())
    assert len(units) == 4
    assert {u["kind"] for u in units} == {"section", "figure"}
    assert "private.png" not in json.dumps(units)


def test_configuration_validation_and_cache_invalidation(tmp_path, translation_report):
    path = tmp_path / "config.toml"
    path.write_text('[translation]\nbase_url="https://example.com/v1"\nmodel="custom"\n'
                    '[translation.glossary]\noperator="算子"\n')
    config = TranslationConfig.from_mapping(_config(path, True)["translation"])
    assert config.concurrency == 8 and config.timeout == 60 and config.max_retries == 2
    unit = translation_report[1][0]
    key = cache_key(unit, config)
    assert key == cache_key(unit, replace(config, concurrency=4, timeout=30))
    assert key != cache_key(unit, replace(config, model="other"))
    assert key != cache_key(unit, replace(config, glossary={"operator": "运算子"}))
    for value in ({"concurrency": True}, {"timeout": 0}, {"api_key": "secret"},
                  {"base_url": "https://user:password@example.com"}, {"glossary": {"a": 1}}):
        with pytest.raises(ValueError):
            TranslationConfig.from_mapping({"base_url": config.base_url, "model": config.model, **value})
    path.write_text('[translation]\nbase_url="https://example.com"\n')
    with pytest.raises(ConfigurationError):
        _config(path, True)


def test_missing_reordered_or_modified_sentence_and_protected_content_is_rejected(translation_report):
    unit = translation_report[1][0]
    valid = {"sentences": [{"id": s["number"], "text": "中文 " + s["text"]} for s in unit["sentences"]]}
    texts = validate_translation(unit, valid)
    rendered = rendered_result(unit, texts)
    assert 'class="math-tex"' in rendered["sentences"][1]["html"]
    assert "[[LR_" not in rendered["sentences"][1]["html"]
    for mutation in (lambda d: d["sentences"].pop(), lambda d: d["sentences"].reverse(),
                     lambda d: d["sentences"][1].update(text="漏掉全部占位符"),
                     lambda d: d["sentences"][0].update(text=r"中文 \input{secret}")):
        changed = json.loads(json.dumps(valid))
        mutation(changed)
        with pytest.raises(TranslationError):
            validate_translation(unit, changed)
    valid["sentences"][0]["text"] = '<img src=x onerror="alert()">'
    assert "&lt;img" in rendered_result(unit, validate_translation(unit, valid))["sentences"][0]["html"]


def test_real_compatible_request_and_http_failure_is_sanitized(provider, translation_report):
    config, calls, codes, _ = provider
    unit = translation_report[1][0]
    texts = request_translation(unit, config, Event())
    assert len(texts) == 3 and len(calls) == 1
    assert calls[0]["path"] == "/v1/chat/completions"
    assert calls[0]["authorization"] == "Bearer fake-translation-key"
    assert calls[0]["body"]["model"] == "test-model"
    assert "private note" not in json.dumps(calls[0])
    codes.extend([429, 200])
    assert request_translation(unit, replace(config, max_retries=1), Event())
    codes.append(401)
    with pytest.raises(TranslationError, match="401") as error:
        request_translation(unit, replace(config, max_retries=2), Event())
    assert "fake-translation-key" not in str(error.value)
    assert len(calls) == 4


@pytest.mark.parametrize("key", [None, "", "   ", "invalid\nkey", "invalid\rkey"])
def test_missing_key_blocks_batch_before_scheduling(provider, translation_report, tmp_path, monkeypatch, key):
    config, calls, _, _ = provider
    if key is None:
        monkeypatch.delenv(config.api_key_env, raising=False)
    else:
        monkeypatch.setenv(config.api_key_env, key)
    units = translation_report[1]
    manager = TranslationManager(units, config, tmp_path / "cache")
    try:
        status = manager.snapshot()
        assert status["enabled"] and not status["available"]
        assert config.api_key_env in status["message"]
        assert status["states"] == {} and status["results"] == {}
        with pytest.raises(TranslationError, match=config.api_key_env):
            manager.submit([unit["id"] for unit in units])
        assert manager.jobs == {} and manager.snapshot()["states"] == {}
        assert not calls
        monkeypatch.setenv(config.api_key_env, "fake-translation-key")
        assert manager.snapshot()["available"]
        manager.submit([unit["id"] for unit in units])
        _wait(lambda: len(manager.snapshot()["results"]) == 2)
        assert len(calls) == 2
    finally:
        manager.close()


def test_cached_translations_remain_readable_without_key(provider, translation_report, tmp_path, monkeypatch):
    config, calls, _, _ = provider
    units = translation_report[1]
    directory = tmp_path / "cache"
    manager = TranslationManager(units, config, directory)
    try:
        manager.submit([unit["id"] for unit in units])
        _wait(lambda: len(manager.snapshot()["results"]) == 2)
    finally:
        manager.close()
    monkeypatch.delenv(config.api_key_env)
    cached = TranslationManager(units, config, directory)
    try:
        status = cached.snapshot()
        assert not status["available"] and len(status["results"]) == 2
        cached.submit([unit["id"] for unit in units])
        assert len(calls) == 2 and not cached.jobs
    finally:
        cached.close()


def _reordered_translation_responses(monkeypatch, unit, *, recover):
    monkeypatch.setenv("TEST_REPAIR_KEY", "fake-repair-key")
    valid = {"sentences": [{"id": s["number"], "text": "译：" + s["text"]} for s in unit["sentences"]]}
    invalid = json.loads(json.dumps(valid))
    source = next(s for s in unit["sentences"] if len(s["protected"]) >= 2)
    first, second = [p["token"] for p in source["protected"][:2]]
    entry = next(s for s in invalid["sentences"] if s["id"] == source["number"])
    entry["text"] = entry["text"].replace(first, "__FIRST__").replace(second, first).replace("__FIRST__", second)
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(json.loads(request.data))
            payload = valid if recover and len(requests) > 1 else invalid
            return BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(payload, ensure_ascii=False)}}]}).encode())

    monkeypatch.setattr(translation_module, "build_opener", lambda *_: Opener())
    return requests


def test_reordered_placeholders_retry_with_exact_sequence_feedback(translation_report, monkeypatch):
    unit = translation_report[1][0]
    requests = _reordered_translation_responses(monkeypatch, unit, recover=True)
    config = TranslationConfig("https://example.invalid/v1", "test-model", api_key_env="TEST_REPAIR_KEY",
                               max_retries=1)
    texts = request_translation(unit, config, Event())
    assert len(texts) == len(unit["sentences"]) and len(requests) == 2
    original = json.loads(requests[0]["messages"][1]["content"])
    retry = json.loads(requests[1]["messages"][1]["content"])
    assert "validation_feedback" not in original
    assert retry["sentences"] == original["sentences"]
    assert retry["validation_feedback"]["placeholder_order"] == [
        {"id": s["number"], "tokens": [p["token"] for p in s["protected"]]} for s in unit["sentences"]]


@pytest.mark.parametrize("max_retries", [0, 2])
def test_invalid_translation_retries_remain_bounded(translation_report, monkeypatch, max_retries):
    unit = translation_report[1][0]
    requests = _reordered_translation_responses(monkeypatch, unit, recover=False)
    config = TranslationConfig("https://example.invalid/v1", "test-model", api_key_env="TEST_REPAIR_KEY",
                               max_retries=max_retries)
    with pytest.raises(TranslationError, match="占位符"):
        request_translation(unit, config, Event())
    assert len(requests) == max_retries + 1


def test_cross_report_cache_is_reused_and_corruption_retranslates(provider, translation_report, tmp_path):
    config, calls, _, _ = provider
    units = translation_report[1]
    directory = tmp_path / "cache"
    manager = TranslationManager(units, config, directory)
    try:
        manager.submit([u["id"] for u in units])
        _wait(lambda: len(manager.snapshot()["results"]) == 2)
    finally:
        manager.close()
    assert len(calls) == 2
    second = TranslationManager(units, config, directory)
    try:
        assert len(second.snapshot()["results"]) == 2
        second.submit([u["id"] for u in units])
        assert len(calls) == 2
    finally:
        second.close()
    (directory / (cache_key(units[0], config) + ".json")).write_text('{"corrupted":true}')
    third = TranslationManager(units, config, directory)
    try:
        assert len(third.snapshot()["results"]) == 1
        third.submit([u["id"] for u in units])
        _wait(lambda: len(third.snapshot()["results"]) == 2)
        assert len(calls) == 3
    finally:
        third.close()


def test_older_protection_rule_cache_and_manifest_are_not_reused(provider, translation_report, tmp_path):
    config, calls, _, _ = provider
    report, units = translation_report
    unit = units[0]
    directory = tmp_path / "cache"
    directory.mkdir()
    key = cache_key(unit, config)
    (directory / (key + ".json")).write_text(json.dumps({"version": "1", "key": key,
        "translation": {"sentences": [{"id": s["number"], "text": "旧译文 " + s["text"]}
                                    for s in unit["sentences"]]}}))
    assert RULE_VERSION != "1"
    manager = TranslationManager(units, config, directory)
    try:
        assert not manager.snapshot()["results"]
        manager.submit([unit["id"]])
        _wait(lambda: unit["id"] in manager.snapshot()["results"])
        assert len(calls) == 1
    finally:
        manager.close()
    for old_unit in units:
        old_unit.pop("rule_version")
    (report.directory / "translation-units.json").write_text(json.dumps(units))
    with pytest.raises(ValueError, match="重新生成报告"):
        create_server(report.directory, translation_config=config)


def test_concurrency_bound_and_cancel_stops_pending_requests(provider, translation_report, tmp_path):
    config, calls, _, gate = provider
    gate.clear()
    base = translation_report[1][0]
    units = []
    for number in range(5):
        unit = json.loads(json.dumps(base))
        unit["id"] += "-" + str(number)
        unit["sentences"][0]["text"] += " unique " + chr(65 + number)
        units.append(unit)
    manager = TranslationManager(units, replace(config, concurrency=2), tmp_path / "cache")
    try:
        manager.submit([u["id"] for u in units])
        _wait(lambda: len(calls) == 2)
        manager.cancel()
        assert sum(s["state"] == "cancelled" for s in manager.snapshot()["states"].values()) == 3
        gate.set()
        _wait(lambda: len(manager.snapshot()["results"]) == 2)
        assert len(calls) == 2
    finally:
        gate.set()
        manager.close()


def test_partial_failure_keeps_success_and_only_retries_failed_content(provider, translation_report, tmp_path):
    config, calls, codes, _ = provider
    units = translation_report[1]
    codes.extend([401, 200])
    manager = TranslationManager(units, config, tmp_path / "cache")
    try:
        manager.submit([u["id"] for u in units])
        _wait(lambda: {s["state"] for s in manager.snapshot()["states"].values()} == {"ready", "failed"})
        assert len(manager.snapshot()["results"]) == 1
        failed = [unit_id for unit_id, state in manager.snapshot()["states"].items() if state["state"] == "failed"]
        manager.submit(failed)
        _wait(lambda: len(manager.snapshot()["results"]) == 2)
        assert len(calls) == 3
    finally:
        manager.close()


def test_http_translation_rejects_foreign_origin_unknown_text_and_exports_cached_results(provider, translation_report):
    config, calls, _, _ = provider
    report, units = translation_report
    with create_server(report.directory, translation_config=config) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}/api/translation/"
        try:
            with urlopen(base + "status") as response:
                status = json.load(response)
            assert status["enabled"]
            def post(body, token=status["token"], origin=None):
                headers = {"Content-Type": "application/json", "X-LaTeX-Review-Token": token}
                if origin:
                    headers["Origin"] = origin
                return urlopen(Request(base + "start", data=json.dumps(body).encode(), headers=headers))
            for body, token, origin, code in (({"units": [units[0]["id"]]}, "", None, 403),
                ({"units": [units[0]["id"]]}, status["token"], "https://evil.example", 403),
                ({"units": ["arbitrary supplied text"]}, status["token"], None, 400)):
                with pytest.raises(HTTPError) as error:
                    post(body, token, origin)
                assert error.value.code == code
            assert not calls
            with post({"units": [u["id"] for u in units]}):
                pass
            _wait(lambda: len(server.translation.snapshot()["results"]) == 2)
            with urlopen(base + "export") as response:
                html = response.read().decode()
                assert response.headers["Content-Disposition"].endswith('"report-translated.html"')
            exported = json.loads(re.search(r'id="translation-results">(.*?)</script>', html, re.S)[1])
            assert len(exported) == 2
            assert "fake-translation-key" not in html
            assert server.translation_token not in html
        finally:
            server.shutdown()
            worker.join()


def test_browser_missing_key_shows_global_guidance_and_preserves_cached_translation(
        provider, translation_report, monkeypatch):
    api = _browser_api()
    config, calls, _, _ = provider
    report, units = translation_report
    monkeypatch.delenv(config.api_key_env)
    with create_server(report.directory, translation_config=config) as server, api.sync_playwright() as playwright:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        browser = None
        try:
            browser = _launch_browser(playwright, api)
            page = browser.new_page()
            page.route("https://**/*", lambda route: route.abort())
            base = f"http://127.0.0.1:{server.server_port}"
            page.goto(base + "/report.html")
            page.wait_for_function("document.querySelector('#translation-status').textContent.includes('翻译尚未就绪')")
            assert config.api_key_env in page.locator("#translation-status").inner_text()
            assert "段失败" not in page.locator("#translation-status").inner_text()
            assert page.locator("#translate-all").is_disabled()
            assert page.locator(".change-card [data-translate-change]").is_disabled()
            status = page.request.get(base + "/api/translation/status").json()
            response = page.request.post(base + "/api/translation/start",
                data={"units": [unit["id"] for unit in units]},
                headers={"X-LaTeX-Review-Token": status["token"]})
            assert response.status == 400 and config.api_key_env in response.json()["message"]
            assert server.translation.snapshot()["states"] == {} and not calls
            monkeypatch.setenv(config.api_key_env, "fake-translation-key")
            page.reload()
            page.wait_for_function("!document.querySelector('#translate-all').disabled")
            page.locator("#translate-all").click()
            page.wait_for_function("document.querySelector('#translation-status').textContent.includes('译文 2 / 2 段')")
            assert len(calls) == 2
            monkeypatch.delenv(config.api_key_env)
            page.reload()
            page.wait_for_function("document.querySelector('#translation-status').textContent.includes('翻译尚未就绪')")
            assert "译文 2 / 2 段" in page.locator("#translation-status").inner_text()
            assert page.locator("#translate-all").is_disabled()
            assert page.locator("#translation-export").is_enabled()
            button = page.locator(".change-card [data-translate-change]")
            assert button.is_enabled() and button.inner_text() == "显示译文"
            page.locator("#next-change").click()
            assert page.locator('.inline-change[open] .inline-translation .translation-block').first.is_hidden()
            page.locator('.inline-change[open] .inline-translation > summary').click()
            page.locator(".inline-change[open] [data-translate-change]").click()
            assert page.locator('.preview-side[data-side="new"] .translation-block').first.is_visible()
            assert len(calls) == 2
        finally:
            if browser:
                browser.close()
            server.shutdown()
            worker.join()


def test_browser_translation_inline_controls_highlights_and_static_export(provider, translation_report, tmp_path):
    api = _browser_api()
    config, calls, _, _ = provider
    report, units = translation_report
    with create_server(report.directory, translation_config=config) as server, api.sync_playwright() as playwright:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            browser = _launch_browser(playwright, api)
        except BaseException:
            server.shutdown()
            worker.join()
            raise
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.route("https://**/*", lambda route: route.abort())
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/report.html")
            page.locator("#translation-status").get_by_text("译文 0 / 2 段").wait_for()
            assert not calls
            page.locator("#next-change").click()
            assert page.locator('.inline-change[open] .inline-translation').get_attribute('open') is None
            page.locator('.inline-change[open] .inline-translation > summary').click()
            page.locator(".inline-change[open] [data-translate-change]").click()
            page.wait_for_function("document.querySelector('#translation-status').textContent.includes('译文 2 / 2 段')")
            assert len(calls) == 2
            new_block = page.locator('.preview-side[data-side="new"] > .paper-body .translation-block')
            if not new_block.count():
                new_block = page.locator('.preview-side[data-side="new"] .review-node .translation-block').first
            assert new_block.is_visible()
            assert new_block.locator('.translated-sentence').count() == 3
            assert new_block.locator('.sentence-changed').count() == 1
            assert new_block.locator('.math-tex').evaluate("node => getComputedStyle(node).visibility") == 'visible'
            new_block.locator('.translated-sentence.sentence-changed').click()
            new_unit = next(unit for unit in units if unit['side'] == 'new')
            assert 'is-highlighted' in page.locator('#' + new_unit['id'] + '-sentence-2').get_attribute('class')
            for number in (1, 3):
                assert 'is-highlighted' not in page.locator('#' + new_unit['id'] + '-sentence-' + str(number)).get_attribute('class')
            assert page.locator('.inline-change[open] .inline-translation-side[data-side="old"] .translation-block').is_visible()
            assert page.locator('.inline-change[open] .old-excerpt').is_hidden()
            page.locator("#translation-mode").select_option("original")
            assert new_block.is_hidden()
            assert page.locator('.inline-change[open] .inline-translation .translation-block').first.is_visible()
            page.locator('.inline-change[open] .inline-translation > summary').click()
            assert page.locator('.inline-change[open] .inline-translation .translation-block').first.is_hidden()
            page.locator("#translation-mode").select_option("bilingual")
            with page.expect_download() as pending:
                page.locator("#translation-export").click()
            exported = tmp_path / "translated.html"
            pending.value.save_as(exported)
            page.goto(exported.as_uri())
            assert page.locator('.preview-side[data-side="new"] .translation-block').first.is_visible()
            assert len(calls) == 2
            page.locator("#translate-all").click()
            assert len(calls) == 2
            assert not errors
        finally:
            browser.close()
            server.shutdown()
            worker.join()


@pytest.mark.parametrize("raw,number", [
    ("Error -3.14e-5.", "-3.14e-5"), ("Error +2.5E+3.", "+2.5E+3"),
    (r"Gain 30\%.", "30%"), ("Error −0.5.", "−0.5"),
    ("Probability .25.", ".25"), ("Count 1,234.5.", "1,234.5"),
])
def test_numeric_expression_is_protected_as_a_whole(raw, number):
    source = _protected_sentence(raw, lambda text: text)
    assert [p["latex"] for p in source["protected"]] == [number]
    assert number not in source["text"]


@pytest.mark.parametrize("extra", ["999%", "-1e-3", "１２３"])
def test_translation_cannot_introduce_unprotected_numbers(extra):
    source = _protected_sentence("Gain 3.14 units.", lambda text: text)
    unit = {"sentences": [{"number": 1, **source}]}
    with pytest.raises(TranslationError, match="数值"):
        validate_translation(unit, {"sentences": [{"id": 1, "text": "增益 [[LR_0]]，增加 " + extra}]})


@pytest.mark.parametrize("text", ["增益 -[[LR_0]]", "增益 [[LR_0]]%", "增益 [[LR_0]]e-"])
def test_translation_cannot_add_numeric_affixes(text):
    source = _protected_sentence("Gain 3.14 units.", lambda text: text)
    unit = {"sentences": [{"number": 1, **source}]}
    with pytest.raises(TranslationError, match="数值"):
        validate_translation(unit, {"sentences": [{"id": 1, "text": text}]})


@pytest.mark.parametrize("raw", [
    r"See \footnote[12]{strong \textbf{evidence} with $x$}.",
    r"See \href{https://example.org/path?q=12}{strong \textbf{evidence} with $x$}.",
])
def test_text_arguments_with_optional_or_multiple_parameters_are_translatable(raw):
    source = _protected_sentence(raw, lambda text: text)
    assert "strong" in source["text"] and "evidence" in source["text"]
    assert "12" not in source["text"] and "$x$" not in source["text"]
    assert any(p["latex"] == "$x$" for p in source["protected"])
    assert any(p["html"] == "<strong>" for p in source["protected"])


@pytest.mark.parametrize("address", ["javascript:alert(1)", "https://[invalid"])
def test_translated_link_does_not_enable_unsafe_url(address):
    source = _protected_sentence(r"See \href{" + address + r"}{our website}.", lambda text: text)
    assert "our website" in source["text"]
    assert not any("href=" in p["html"] for p in source["protected"])


@pytest.mark.parametrize("before,after,category", [
    ("We use $x_1$ here.", "We use $x_2$ here.", "equation"),
    (r"We cite \cite{alpha} here.", r"We cite \cite{beta} here.", "citation"),
])
def test_inline_only_changes_mark_and_locate_the_changed_sentence(tmp_path, before, after, category):
    report = _content_report(tmp_path, "Keep this sentence. " + before + " Another stable sentence.",
                             "Keep this sentence. " + after + " Another stable sentence.")
    detail = next(d for c in report.document.changes for d in c.details if d.category == category)
    assert detail.old_sentences == detail.new_sentences == (2,)
    units = json.loads((report.directory / "translation-units.json").read_text())
    assert len(units) == 2
    assert all([s["changed"] for s in unit["sentences"]] == [False, True, False] for unit in units)


def test_repeated_citation_changes_keep_the_correct_sentence(tmp_path):
    report = _content_report(tmp_path,
        r"We cite \cite{same}. We cite \cite{same} again. A stable sentence.",
        r"We cite \cite{same}. We cite \cite{other} again. A stable sentence.")
    details = [d for c in report.document.changes for d in c.details if d.category == "citation"]
    assert len(details) == 1
    assert details[0].old_sentences == details[0].new_sentences == (2,)


@pytest.mark.parametrize("before,after", [
    ("We use $x_1$ here.", "We use $x_2$ here."),
    (r"We cite \cite{alpha} here.", r"We cite \cite{beta} here."),
])
def test_inline_only_translation_click_jumps_to_original_sentence(tmp_path, provider, before, after):
    api = _browser_api()
    report = _content_report(tmp_path, "Keep this sentence. " + before + " Another stable sentence.",
                             "Keep this sentence. " + after + " Another stable sentence.")
    with create_server(report.directory, translation_config=provider[0]) as server, api.sync_playwright() as playwright:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        browser = None
        try:
            browser = _launch_browser(playwright, api)
            page = browser.new_page()
            page.route("https://**/*", lambda route: route.abort())
            page.goto(f"http://127.0.0.1:{server.server_port}/report.html")
            page.locator("#translate-all").click()
            page.wait_for_function("document.querySelector('#translation-status').textContent.includes('译文 2 / 2 段')")
            new_unit = next(unit for unit in server.translation.units.values() if unit["side"] == "new")
            original = page.locator("#" + new_unit["id"])
            translated = original.locator('.translated-sentence.sentence-changed')
            assert translated.count() == 1
            translated.click()
            highlighted = original.locator('.review-sentence.is-highlighted')
            assert highlighted.count() == 1
            assert highlighted.get_attribute("id").endswith("-sentence-2")
        finally:
            if browser:
                browser.close()
            server.shutdown()
            worker.join()


def test_reserved_placeholder_reports_blocked_content_and_never_requests_provider(tmp_path, provider):
    report = _content_report(tmp_path, "Marker [[LR_0]] has small gains.", "Marker [[LR_0]] has large gains.")
    units = json.loads((report.directory / "translation-units.json").read_text())
    assert len(units) == 2
    assert all("占位符" in unit["error"] and not unit["sentences"] for unit in units)
    assert any(d.code == "translation_unavailable" for d in report.document.diagnostics)
    config, calls, _, _ = provider
    manager = TranslationManager(units, config, tmp_path / "cache")
    try:
        assert {s["state"] for s in manager.snapshot()["states"].values()} == {"blocked"}
        manager.submit([u["id"] for u in units])
        assert not calls and not manager.snapshot()["results"]
    finally:
        manager.close()


def test_cached_translation_math_renders_when_engine_arrives_later(provider, translation_report):
    api = _browser_api()
    config = provider[0]
    report = translation_report[0]
    with create_server(report.directory, translation_config=config) as server, api.sync_playwright() as playwright:
        manager = server.translation
        for unit_id, unit in manager.units.items():
            manager.results[manager.keys[unit_id]] = ["译：" + s["text"] for s in unit["sentences"]]
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        browser = None
        try:
            browser = _launch_browser(playwright, api)
            page = browser.new_page()
            pending = []
            page.route("https://**/*", lambda route: pending.append(route))
            page.goto(f"http://127.0.0.1:{server.server_port}/report.html", wait_until="domcontentloaded")
            page.wait_for_selector(".translation-block", state="attached")
            page.wait_for_function("window.MathJax && !window.MathJax.tex2chtmlPromise")
            for route in pending:
                route.fulfill(status=200, content_type="text/javascript", body="""
                    window.MathJax.startup.promise = Promise.resolve();
                    window.MathJax.tex2chtmlPromise = async function(text) {
                      const rendered = document.createElement('mjx-container');
                      rendered.textContent = text;
                      return rendered;
                    };
                """)
            page.wait_for_function("document.getElementById('math-status').dataset.state === 'ready'")
            page.wait_for_function("Array.from(document.querySelectorAll('.translation-block .math-tex')).every(node => node.querySelector('mjx-container'))", timeout=2000)
            assert page.locator(".translation-block .math-tex mjx-container").count() >= 2
        finally:
            if browser:
                browser.close()
            server.shutdown()
            worker.join()


def test_blocked_translation_is_visible_and_batch_keeps_other_successes(tmp_path, provider):
    api = _browser_api()
    report = _content_report(tmp_path,
        "Marker [[LR_0]] has small gains.\n\nWe report a small improvement.",
        "Marker [[LR_0]] has large gains.\n\nWe report a large improvement.")
    config, calls, _, _ = provider
    with create_server(report.directory, translation_config=config) as server, api.sync_playwright() as playwright:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        browser = None
        try:
            browser = _launch_browser(playwright, api)
            page = browser.new_page()
            page.route("https://**/*", lambda route: route.abort())
            page.goto(f"http://127.0.0.1:{server.server_port}/report.html")
            page.wait_for_function("document.querySelector('#translation-status').textContent.includes('2 段无法翻译')")
            blocked = page.locator('.change-card [data-translate-change]').filter(has_text="此段无法翻译")
            assert blocked.count() == 1 and blocked.is_disabled()
            assert "占位符" in blocked.locator("..").inner_text()
            page.locator("#translate-all").click()
            page.wait_for_function("document.querySelector('#translation-status').textContent.includes('译文 2 / 4 段')")
            assert len(calls) == 2
            assert "2 段无法翻译" in page.locator("#translation-status").inner_text()
        finally:
            if browser:
                browser.close()
            server.shutdown()
            worker.join()


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
def test_preview_exits_promptly_with_translation_in_flight(provider, translation_report, tmp_path, stop_signal):
    config, calls, _, gate = provider
    report, units = translation_report
    path = tmp_path / "config.toml"
    path.write_text('[translation]\nbase_url=' + json.dumps(config.base_url) + '\nmodel="test-model"\n'
                    'api_key_env="TEST_TRANSLATION_KEY"\ntimeout=30\n')
    gate.clear()
    process = subprocess.Popen([sys.executable, "-m", "latex_review.serve", str(report.directory),
                                "--config", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        address = process.stdout.readline().strip()
        assert address.startswith("http://127.0.0.1:")
        base = address.removesuffix("/report.html")
        with urlopen(base + "/api/translation/status") as response:
            token = json.load(response)["token"]
        request = Request(base + "/api/translation/start", data=json.dumps({"units": [units[0]["id"]]}).encode(),
                          headers={"Content-Type": "application/json", "X-LaTeX-Review-Token": token})
        with urlopen(request):
            pass
        _wait(lambda: len(calls) == 1)
        process.send_signal(stop_signal)
        assert process.wait(timeout=1.5) == 0
        with create_server(report.directory, int(base.rsplit(":", 1)[1])):
            pass
    finally:
        gate.set()
        if process.poll() is None:
            process.kill()
        process.communicate()
