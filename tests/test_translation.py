"""段落翻译的结构保护、真实本机 HTTP、缓存、取消与浏览器回归。"""

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
from threading import Event, Lock, Thread
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from latex_review import compare_projects, parse_project, resolve_sources, write_report
from latex_review.cli import _config, ConfigurationError
from latex_review.serve import create_server
from latex_review.translation import (TranslationConfig, TranslationError, TranslationManager,
                                      cache_key, rendered_result, request_translation, validate_translation)


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
    valid["sentences"][0]["text"] = '<img src=x onerror="alert(1)">'
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


def test_concurrency_bound_and_cancel_stops_pending_requests(provider, translation_report, tmp_path):
    config, calls, _, gate = provider
    gate.clear()
    base = translation_report[1][0]
    units = []
    for number in range(5):
        unit = json.loads(json.dumps(base))
        unit["id"] += "-" + str(number)
        unit["sentences"][0]["text"] += " unique " + str(number)
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


def test_browser_translation_inline_controls_highlights_and_static_export(provider, translation_report, tmp_path):
    api = pytest.importorskip("playwright.sync_api")
    config, calls, _, _ = provider
    report, units = translation_report
    with create_server(report.directory, translation_config=config) as server, api.sync_playwright() as playwright:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            browser = playwright.chromium.launch(executable_path=os.environ.get("LATEX_REVIEW_BROWSER"))
        except api.Error as exc:
            server.shutdown()
            worker.join()
            pytest.skip(f"浏览器运行环境不可用：{exc}")
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.route("https://**/*", lambda route: route.abort())
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/report.html")
            page.locator("#translation-status").get_by_text("译文 0 / 2 段").wait_for()
            assert not calls
            page.locator("#next-change").click()
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
            assert page.locator('.inline-change[open] .old-excerpt .translation-block').is_visible()
            page.locator("#translation-mode").select_option("original")
            assert new_block.is_hidden()
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
