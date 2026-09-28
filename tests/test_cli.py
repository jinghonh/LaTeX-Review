import os
import subprocess
import sys


def test_help_does_not_compile(tmp_path):
    marker = tmp_path / "called"
    fake = tmp_path / "latexmk"
    fake.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}")
    result = subprocess.run([sys.executable, "-m", "latex_review.cli", "--help"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0
    assert "尚未实现" in result.stdout
    assert result.stderr == ""
    assert not marker.exists()


def test_unimplemented_command_is_explicit(tmp_path):
    result = subprocess.run([sys.executable, "-m", "latex_review.cli", "main.tex"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 64
    assert result.stdout == ""
    assert result.stderr.startswith("latex-review: 错误：")


def test_invalid_option_uses_error_convention(tmp_path):
    result = subprocess.run([sys.executable, "-m", "latex_review.cli", "--missing"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 64
    assert result.stderr.startswith("latex-review: 错误：")
