"""请求前缀稳定性、接口用量及会话记录。全部接口响应由本机模拟。"""

from dataclasses import replace
from io import BytesIO
import json
import stat
from threading import Event, Lock
import time
from urllib.error import HTTPError

import pytest

from latex_review import translation as translation_module
from latex_review.translation import TranslationConfig, TranslationError, TranslationManager, request_translation


def unit(name="paragraph"):
    return {"id": name, "side": "new", "node_id": name, "kind": "paragraph", "change_id": name,
            "sentences": [{"number": 1, "changed": True,
                           "text": "Private original [[LR_0]] and [[LR_1]].",
                           "protected": [{"token": "[[LR_0]]", "latex": "$x$", "html": "x"},
                                         {"token": "[[LR_1]]", "latex": "$y$", "html": "y"}]}]}


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("TEST_USAGE_KEY", "private-test-key")
    return TranslationConfig("https://example.invalid/v1", "test-model", api_key_env="TEST_USAGE_KEY",
                             glossary={"operator": "算子", "reachable set": "可达集"}, max_retries=0)


def provider(monkeypatch, responses):
    requests = []
    lock = Lock()

    class Opener:
        def open(self, request, timeout):
            data = json.loads(request.data)
            with lock:
                requests.append(data)
                response = responses[len(requests) - 1]
            if isinstance(response, Exception):
                raise response
            if callable(response):
                response = response(data)
            return BytesIO(json.dumps(response, ensure_ascii=False).encode())

    monkeypatch.setattr(translation_module, "build_opener", lambda *_: Opener())
    return requests


def response(data=None, *, usage=None, invalid=False):
    text = "中文 [[LR_1]] 和 [[LR_0]]。" if invalid else "中文 [[LR_0]] 和 [[LR_1]]。"
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
        "sentences": [{"id": 1, "text": text}]}, ensure_ascii=False)}}], "usage": usage}


def test_glossary_order_does_not_change_request_prefix(config, monkeypatch):
    requests = provider(monkeypatch, [response(), response()])
    request_translation(unit(), config, Event())
    request_translation(unit(), replace(config, glossary=dict(reversed(list(config.glossary.items())))), Event())
    assert requests[0]["messages"] == requests[1]["messages"]


def test_failed_attempt_usage_is_counted_before_validation_and_retry_keeps_prefix(config, monkeypatch):
    requests = provider(monkeypatch, [
        response(usage={"prompt_cache_hit_tokens": 20, "prompt_cache_miss_tokens": 80, "completion_tokens": 5}, invalid=True),
        response(usage={"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 90}, "completion_tokens": 8})])
    records = []
    texts = request_translation(unit(), replace(config, max_retries=1), Event(), on_attempt=records.append)
    assert texts == ["中文 [[LR_0]] 和 [[LR_1]]。"]
    assert requests[1]["messages"][:2] == requests[0]["messages"]
    assert requests[1]["messages"][2]["role"] == "assistant"
    feedback = json.loads(requests[1]["messages"][-1]["content"])["validation_feedback"]
    assert feedback["placeholder_order"] == [{"id": 1, "tokens": ["[[LR_0]]", "[[LR_1]]"]}]
    assert [r["outcome"] for r in records] == ["validation_failed", "success"]
    assert [r["attempt"] for r in records] == [1, 2]
    assert [r["prompt_tokens"] for r in records] == [100, 100]
    assert [r["prompt_cache_hit_tokens"] for r in records] == [20, 90]
    assert [r["prompt_cache_miss_tokens"] for r in records] == [80, 10]
    assert [r["completion_tokens"] for r in records] == [5, 8]
    assert len({r["system_prompt_fingerprint"] for r in records}) == 1
    assert all(r["elapsed_ms"] >= 0 and r["started_at"] for r in records)


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": True, "prompt_cache_hit_tokens": -1},
                                  {"prompt_tokens": 100, "prompt_cache_hit_tokens": 101},
                                  {"prompt_tokens": 100, "prompt_cache_hit_tokens": 20, "prompt_cache_miss_tokens": 90}])
def test_missing_or_invalid_cache_usage_is_unknown(config, monkeypatch, usage):
    provider(monkeypatch, [response(usage=usage)])
    records = []
    assert request_translation(unit(), config, Event(), on_attempt=records.append)
    assert records[0]["prompt_cache_hit_tokens"] is None
    assert records[0]["prompt_cache_miss_tokens"] is None


def test_http_retry_records_unknown_usage_and_final_invalid_response_keeps_reported_usage(config, monkeypatch):
    invalid = response(usage={"prompt_tokens": 100, "prompt_cache_hit_tokens": 40, "completion_tokens": 7})
    invalid["choices"][0]["message"]["content"] = "invalid json"
    provider(monkeypatch, [HTTPError(config.endpoint, 429, "limited", {}, BytesIO(b"private-test-key")), invalid])
    records = []
    with pytest.raises(TranslationError, match="响应格式无效"):
        request_translation(unit(), replace(config, max_retries=1), Event(), on_attempt=records.append)
    assert [r["outcome"] for r in records] == ["http_error", "invalid_response"]
    assert records[0]["http_status"] == 429 and records[0]["prompt_tokens"] is None
    assert records[1]["prompt_cache_hit_tokens"] == 40
    assert records[1]["completion_tokens"] == 7
    assert "private-test-key" not in json.dumps(records)


def test_later_retries_preserve_all_prior_messages(config, monkeypatch):
    requests = provider(monkeypatch, [response(invalid=True), response(invalid=True), response()])
    assert request_translation(unit(), replace(config, max_retries=2), Event())
    assert requests[2]["messages"][:len(requests[1]["messages"])] == requests[1]["messages"]
    assert requests[1]["messages"][:len(requests[0]["messages"])] == requests[0]["messages"]


def wait_for_results(manager, count):
    deadline = time.monotonic() + 5
    while len(manager.snapshot()["results"]) != count:
        assert time.monotonic() < deadline, "异步翻译未及时完成"
        time.sleep(.01)


def test_concurrent_session_usage_is_weighted_persisted_and_excludes_local_cache(config, monkeypatch, tmp_path):
    provider(monkeypatch, [
        response(usage={"prompt_tokens": 100, "prompt_cache_hit_tokens": 10, "completion_tokens": 5}),
        response(usage={"prompt_tokens": 900, "prompt_cache_hit_tokens": 810, "completion_tokens": 20})])
    units = [unit("first"), unit("second")]
    units[1]["sentences"][0]["text"] += " Changed wording."
    manager = TranslationManager(units, config, tmp_path)
    try:
        manager.submit([u["id"] for u in units])
        wait_for_results(manager, 2)
        usage = manager.snapshot()["usage"]
        assert usage["requests"] == usage["cache_reports"] == 2
        assert usage["retries"] == 0
        assert usage["prompt_tokens"] == 1000 and usage["completion_tokens"] == 25
        assert usage["prompt_cache_hit_tokens"] == 820 and usage["prompt_cache_miss_tokens"] == 180
        assert usage["cache_hit_rate"] == pytest.approx(.82)
        paths = list(tmp_path.glob("usage-*.json"))
        assert len(paths) == 1
        data = json.loads(paths[0].read_text())
        assert data["summary"]["cache_hit_rate"] == usage["cache_hit_rate"]
        assert len(data["requests"]) == 2
        assert "Private original" not in paths[0].read_text() and "private-test-key" not in paths[0].read_text()
        assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600
    finally:
        manager.close()
    cached = TranslationManager(units, config, tmp_path)
    try:
        assert len(cached.snapshot()["results"]) == 2
        cached.submit([u["id"] for u in units])
        assert cached.snapshot()["usage"]["requests"] == 0
        assert cached.snapshot()["usage"]["cache_hit_rate"] is None
        assert len(list(tmp_path.glob("usage-*.json"))) == 1
    finally:
        cached.close()


def test_usage_persistence_failure_does_not_fail_translation(config, monkeypatch, tmp_path):
    provider(monkeypatch, [response()])
    write_cache = translation_module._write_cache

    def write(path, value):
        if path.name.startswith("usage-"):
            raise OSError("不可写")
        return write_cache(path, value)

    monkeypatch.setattr(translation_module, "_write_cache", write)
    manager = TranslationManager([unit()], config, tmp_path)
    try:
        manager.submit(["paragraph"])
        wait_for_results(manager, 1)
        usage = manager.snapshot()["usage"]
        assert usage["requests"] == 1 and usage["cache_hit_rate"] is None
        assert "保存失败" in usage["message"]
        manager.close()
        manager._record_attempt("paragraph", "key", {})
        assert manager.snapshot()["usage"]["requests"] == 1
    finally:
        manager.close()


def test_failed_unit_keeps_all_attempt_costs_in_session_summary(config, monkeypatch, tmp_path):
    provider(monkeypatch, [
        response(usage={"prompt_tokens": 100, "prompt_cache_hit_tokens": 20, "completion_tokens": 5}, invalid=True),
        response(usage={"prompt_tokens": 100, "prompt_cache_hit_tokens": 50, "completion_tokens": 7}, invalid=True)])
    manager = TranslationManager([unit()], replace(config, max_retries=1), tmp_path)
    try:
        manager.submit(["paragraph"])
        deadline = time.monotonic() + 5
        while manager.snapshot()["states"]["paragraph"]["state"] != "failed":
            assert time.monotonic() < deadline, "翻译未及时返回校验失败"
            time.sleep(.01)
        usage = manager.snapshot()["usage"]
        assert usage["requests"] == 2 and usage["retries"] == 1
        assert usage["prompt_tokens"] == 200 and usage["completion_tokens"] == 12
        assert usage["cache_hit_rate"] == pytest.approx(.35)
        assert not manager.snapshot()["results"]
        records = json.loads(next(tmp_path.glob("usage-*.json")).read_text())["requests"]
        assert [record["outcome"] for record in records] == ["validation_failed", "validation_failed"]
    finally:
        manager.close()


def test_partial_usage_only_uses_reported_inputs_for_hit_rate(config, monkeypatch, tmp_path):
    provider(monkeypatch, [response(), response(usage={"prompt_tokens": 1000, "prompt_cache_hit_tokens": 250})])
    units = [unit("first"), unit("second")]
    units[1]["sentences"][0]["text"] += " Changed wording."
    manager = TranslationManager(units, config, tmp_path)
    try:
        manager.submit([u["id"] for u in units])
        wait_for_results(manager, 2)
        usage = manager.snapshot()["usage"]
        assert usage["requests"] == 2 and usage["cache_reports"] == usage["usage_reports"] == 1
        assert usage["cache_hit_rate"] == pytest.approx(.25)
        assert usage["prompt_tokens"] == 1000 and usage["completion_tokens"] is None
    finally:
        manager.close()
