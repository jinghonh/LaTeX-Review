"""Exercise the measurement CLIs using synthetic evidence and tiny pytest runs."""

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASE_NODEID = "tests/test_original.py::test_original"
REQUIRED_ENVIRONMENT_FIELDS = (
    "platform", "machine", "python", "dependencies", "require_browser",
    "browser_executable", "browser_manifest", "pytest_environment", "pytest_args",
    "cpus", "instrumentation_sha256", "tools", "custom_browser_sha256",
)


@pytest.fixture
def baseline():
    return {
        "sha": "before",
        "platform": "Linux-test",
        "machine": "x86_64",
        "python": "3.11.9 (synthetic test environment)",
        "dependencies": {"pytest": "8.3.5", "playwright": "1.51.0"},
        "require_browser": None,
        "browser_executable": None,
        "custom_browser_sha256": None,
        "tools": {"pdflatex": "/usr/bin/pdflatex", "latexdiff": "/usr/bin/latexdiff"},
        "browser_manifest": {"chromium": {"revision": "1234", "version": "123.0"}},
        "pytest_environment": {"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        "pytest_args": [],
        "cpus": 4,
        "instrumentation_sha256": "a" * 64,
        "exitstatus": 0,
        "wall_seconds": 10.0,
        "collected": [BASE_NODEID],
        "reports": [
            {"nodeid": BASE_NODEID, "phase": "setup", "outcome": "passed", "seconds": 2.0},
            {"nodeid": BASE_NODEID, "phase": "call", "outcome": "passed", "seconds": 5.0},
            {"nodeid": BASE_NODEID, "phase": "teardown", "outcome": "passed", "seconds": 1.0},
        ],
        "outcomes": {"passed": 1},
        "phases": {"setup": 2.0, "call": 5.0, "teardown": 1.0},
        "modules": {"tests/test_original.py": {"setup": 2.0, "call": 5.0, "teardown": 1.0}},
    }


def _compare(tmp_path, before, after):
    paths = [tmp_path / "before.json", tmp_path / "after.json"]
    for path, evidence in zip(paths, (before, after)):
        path.write_text(json.dumps(evidence), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/compare-test-runs.py"), *map(str, paths)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.stdout, result.stderr
    return result, json.loads(result.stdout)


def test_compare_rejects_missing_baseline_test(tmp_path, baseline):
    after = deepcopy(baseline)
    after.update(wall_seconds=6.0, collected=[], reports=[], outcomes={}, phases={}, modules={})

    result, summary = _compare(tmp_path, baseline, after)

    assert result.returncode != 0
    assert summary["comparison_valid"] is False
    assert summary["reduction_percent"] is None
    assert summary["missing"] == [BASE_NODEID]
    assert summary["raw_wall_delta_percent"] == pytest.approx(40.0)


def test_compare_rejects_lost_duplicate_collection(tmp_path, baseline):
    before = deepcopy(baseline)
    before["collected"] = [BASE_NODEID, BASE_NODEID]
    before["reports"] *= 2
    before["outcomes"] = {"passed": 2}
    before["phases"] = {"setup": 4.0, "call": 10.0, "teardown": 2.0}
    before["modules"] = {"tests/test_original.py": dict(before["phases"])}

    result, summary = _compare(tmp_path, before, baseline)

    assert result.returncode == 1
    assert summary["comparison_valid"] is False
    assert summary["reduction_percent"] is None
    assert summary["missing"] == [BASE_NODEID]


@pytest.mark.parametrize("field,value", [
    ("dependencies", {"pytest": "9.0.0", "playwright": "1.51.0"}),
    ("tools", {"pdflatex": None, "latexdiff": "/usr/bin/latexdiff"}),
    ("browser_manifest", {"chromium": {"revision": "5678", "version": "124.0"}}),
    ("browser_executable", "/different/chromium"),
    ("require_browser", "1"),
    ("pytest_args", ["-q"]),
])
def test_compare_rejects_changed_environment(tmp_path, baseline, field, value):
    after = deepcopy(baseline)
    after[field] = value

    result, summary = _compare(tmp_path, baseline, after)

    assert result.returncode == 1
    assert summary["comparison_valid"] is False
    assert summary["reduction_percent"] is None
    assert field in json.dumps(summary["environment_mismatches"])
    assert summary["missing"] == summary["regressions"] == []


@pytest.mark.parametrize("field", REQUIRED_ENVIRONMENT_FIELDS)
def test_compare_rejects_missing_required_environment_field(tmp_path, baseline, field):
    # Removing from both sides must not make incomplete evidence comparable.
    before, after = deepcopy(baseline), deepcopy(baseline)
    del before[field]
    del after[field]

    result, summary = _compare(tmp_path, before, after)

    assert result.returncode == 1
    assert summary["comparison_valid"] is False
    assert summary["reduction_percent"] is None
    assert field in json.dumps(summary["environment_mismatches"])


def test_compare_rejects_passed_test_becoming_skipped(tmp_path, baseline):
    after = deepcopy(baseline)
    after["reports"][1]["outcome"] = "skipped"
    after["outcomes"] = {"skipped": 1}

    result, summary = _compare(tmp_path, baseline, after)

    assert result.returncode != 0
    assert summary["comparison_valid"] is False
    assert summary["reduction_percent"] is None
    assert summary["missing"] == []
    assert summary["regressions"] == [BASE_NODEID]


@pytest.mark.parametrize("failed_side", ["before", "after"])
def test_compare_rejects_nonzero_run_even_without_test_regressions(tmp_path, baseline, failed_side):
    before, after = deepcopy(baseline), deepcopy(baseline)
    # An interrupted run can have passing reports but still be unsuccessful.
    (before if failed_side == "before" else after)["exitstatus"] = 2

    result, summary = _compare(tmp_path, before, after)

    assert result.returncode != 0
    assert summary["comparison_valid"] is False
    assert summary["reduction_percent"] is None
    assert summary[f"{failed_side}_exitstatus"] == 2
    assert summary["missing"] == summary["regressions"] == []


@pytest.mark.parametrize("wall_seconds,reduction", [(6.0, 40.0), (12.5, -25.0)])
def test_compare_allows_added_tests_and_retains_phase_and_wall_timings(
    tmp_path, baseline, wall_seconds, reduction,
):
    added = "tests/test_added.py::test_added"
    after = deepcopy(baseline)
    after.update({
        "sha": "after",
        "exitstatus": 0,
        "wall_seconds": wall_seconds,
        "collected": [BASE_NODEID, added],
        "reports": [
            {"nodeid": BASE_NODEID, "phase": "setup", "outcome": "passed", "seconds": 1.0},
            {"nodeid": BASE_NODEID, "phase": "call", "outcome": "passed", "seconds": 2.0},
            {"nodeid": BASE_NODEID, "phase": "teardown", "outcome": "passed", "seconds": 0.5},
            {"nodeid": added, "phase": "setup", "outcome": "passed", "seconds": 0.25},
            {"nodeid": added, "phase": "call", "outcome": "passed", "seconds": 0.5},
            {"nodeid": added, "phase": "teardown", "outcome": "passed", "seconds": 0.25},
        ],
        "outcomes": {"passed": 2},
        "phases": {"setup": 1.25, "call": 2.5, "teardown": 0.75},
        "modules": {
            "tests/test_original.py": {"setup": 1.0, "call": 2.0, "teardown": 0.5},
            "tests/test_added.py": {"setup": 0.25, "call": 0.5, "teardown": 0.25},
        },
    })

    result, summary = _compare(tmp_path, baseline, after)

    assert result.returncode == 0, result.stderr
    assert summary["comparison_valid"] is True
    assert not summary["environment_mismatches"]
    assert summary["added"] == [added]
    assert summary["missing"] == summary["regressions"] == []
    assert summary["before_wall_seconds"] == 10.0
    assert summary["after_wall_seconds"] == wall_seconds
    assert summary["reduction_percent"] == pytest.approx(reduction)
    assert summary["raw_wall_delta_percent"] == pytest.approx(reduction)
    assert summary["before_phases"] == baseline["phases"]
    assert summary["after_phases"] == after["phases"]
    modules = {entry["module"]: entry for entry in summary["modules"]}
    assert modules["tests/test_original.py"]["before_total"] == 8.0
    assert modules["tests/test_original.py"]["after_total"] == 3.5
    assert modules["tests/test_added.py"]["before_total"] == 0
    assert modules["tests/test_added.py"]["after_total"] == 1.0


def _measure(tmp_path, output, source):
    tiny_test = tmp_path / "test_tiny.py"
    tiny_test.write_text(source, encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               LATEX_REVIEW_TEST_SHARDS="0", LATEX_REVIEW_SKIP_CLI_SMOKE="1")
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    return subprocess.run(
        [
            sys.executable, str(ROOT / "scripts/measure-tests.py"),
            "--output", str(output), "--", "-c", str(config),
            "--rootdir", str(tmp_path), "--confcutdir", str(tmp_path), str(tiny_test),
        ],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )


@pytest.mark.parametrize("other_outcome,exitstatus", [("skipped", 0), ("failed", 1)])
def test_measure_preserves_pytest_status_collection_and_phases(tmp_path, other_outcome, exitstatus):
    body = 'pytest.skip("intentional skip")' if other_outcome == "skipped" else 'assert False, "intentional failure"'
    output = tmp_path / "evidence"
    result = _measure(
        tmp_path, output,
        f"import pytest\n\ndef test_pass():\n    pass\n\ndef test_other():\n    {body}\n",
    )

    assert result.returncode == exitstatus, result.stdout + result.stderr
    evidence = json.loads((output / "timings.json").read_text(encoding="utf-8"))
    assert evidence["exitstatus"] == exitstatus
    assert evidence["pytest_exitstatus"] == exitstatus
    assert set(evidence["collected"]) == {"test_tiny.py::test_pass", "test_tiny.py::test_other"}
    assert evidence["outcomes"] == {"passed": 1, other_outcome: 1}
    assert evidence["wall_seconds"] > 0
    for name, outcome in (("test_pass", "passed"), ("test_other", other_outcome)):
        reports = [report for report in evidence["reports"] if report["nodeid"] == f"test_tiny.py::{name}"]
        assert {report["phase"] for report in reports} == {"setup", "call", "teardown"}
        assert next(report["outcome"] for report in reports if report["phase"] == "call") == outcome
        assert all(report["seconds"] >= 0 for report in reports)
    assert set(evidence["phases"]) == {"setup", "call", "teardown"}
    assert (output / "console.log").is_file()
    assert (output / "junit.xml").is_file()


def test_measure_rejects_existing_output_without_overwriting_evidence(tmp_path):
    output = tmp_path / "evidence"
    output.mkdir()
    original = b"previous measurement must survive\n"
    (output / "timings.json").write_bytes(original)

    result = _measure(tmp_path, output, "def test_pass():\n    pass\n")

    assert result.returncode != 0
    assert (output / "timings.json").read_bytes() == original
    assert {path.name for path in output.iterdir()} == {"timings.json"}
