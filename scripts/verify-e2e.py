#!/usr/bin/env python3
"""在隔离副本中固定 Git 旧版与当前磁盘新版，并核验端到端报告。"""

from __future__ import annotations

import argparse
import hashlib
from html import unescape
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time


TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_ROOT / "src"))


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=root, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        check=True, capture_output=True,
    ).stdout


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files_manifest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): digest(path)
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def manifest_digest(manifest: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def old_snapshot(project: Path, revision: str, scope: str, destination: Path) -> None:
    archive = git(project, "archive", "--format=tar", revision, "--", scope)
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        for member in stream:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise ValueError(f"旧版归档含不安全路径：{member.name}")
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = stream.extractfile(member)
                assert source is not None
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)


def new_snapshot(project: Path, scope: str, destination: Path) -> None:
    names = git(project, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", scope)
    for raw in names.split(b"\0"):
        if not raw:
            continue
        name = os.fsdecode(raw)
        relative = PurePosixPath(name)
        source = project.joinpath(*relative.parts)
        if source.is_symlink():
            raise ValueError(f"工作区含符号链接，无法安全固定副本：{name}")
        if not source.is_file():  # 已跟踪但从磁盘删除的文件不能从索引回填。
            continue
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def check_location(location: dict | None, root: Path) -> None:
    if location is None:
        return
    name = location["file"]
    start, end = location["start_line"], location["end_line"]
    if name is None or start is None or end is None:
        assert location["uncertainty_reason"], location
        return
    relative = PurePosixPath(name)
    assert not relative.is_absolute() and ".." not in relative.parts, location
    source = root.joinpath(*relative.parts)
    assert source.is_file(), location
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    assert 1 <= start <= end <= len(lines) + 1, location


def check_report(output: Path, old: Path, new: Path) -> dict:
    diff = json.loads((output / "diff.json").read_text(encoding="utf-8"))
    diagnostics = json.loads((output / "diagnostics.json").read_text(encoding="utf-8"))
    html = (output / "report.html").read_text(encoding="utf-8")
    changes = diff["changes"]
    assert diff["summary"]["changes"] == len(changes)
    assert len(changes) == len({change["id"] for change in changes})
    assert html.count('class="change-card"') == len(changes)
    assert f'data-changes="{len(changes)}"' in html
    cards = re.findall(
        r'<article class="change-card" id="([^"]+)" data-kind="([^"]+)" '
        r'data-categories="([^"]*)"><button type="button" class="change-jump" '
        r'data-old="([^"]*)" data-new="([^"]*)" '
        r'data-old-location="([^"]*)" data-new-location="([^"]*)">', html)
    assert len(cards) == len(changes)
    by_id = {change["id"]: change for change in changes}
    for identifier, kind, categories, old_id, new_id, old_location, new_location in cards:
        change = by_id[unescape(identifier)]
        expected_categories = set(change["categories"]) | {detail["category"] for detail in change["details"]}
        assert kind == change["kind"] and set(categories.split()) == expected_categories
        assert unescape(old_id) == (change["old_node_id"] or "")
        assert unescape(new_id) == (change["new_node_id"] or "")
        for side, shown in (("old", old_location), ("new", new_location)):
            source = change[f"source_{side}"]
            if source is None:
                assert unescape(shown) == "该侧无内容"
            elif source["file"] is not None and source["start_line"] is not None:
                span = str(source["start_line"])
                if source["end_line"] != source["start_line"]:
                    span += f"–{source['end_line']}"
                assert f"{source['file']}，第 {span} 行" in unescape(shown)
            else:
                assert "位置约略" in unescape(shown) and source["uncertainty_reason"]
    assert diagnostics["diagnostics"] == diff["diagnostics"]
    for side, root in (("old", old), ("new", new)):
        for node in diff[f"nodes_{side}"]:
            check_location(node["source"], root)
    for change in changes:
        for side, root in (("old", old), ("new", new)):
            check_location(change.get(f"source_{side}"), root)
        for detail in change["details"]:
            for side, root in (("old", old), ("new", new)):
                check_location(detail.get(f"source_{side}"), root)
    for diagnostic in diff["diagnostics"]:
        for side, root in (("old", old), ("new", new)):
            check_location(diagnostic.get(f"source_{side}"), root)
    return diff


def run(args: argparse.Namespace) -> Path:
    project = Path(args.project_root).expanduser().resolve(strict=True)
    assert git(project, "rev-parse", "--show-toplevel").decode().strip() == str(project)
    entry = PurePosixPath(args.entry)
    assert not entry.is_absolute() and ".." not in entry.parts and entry.suffix == ".tex"
    scope = args.scope or entry.parent.as_posix()
    assert scope and not PurePosixPath(scope).is_absolute()
    assert ".." not in PurePosixPath(scope).parts
    assert entry == PurePosixPath(scope) or PurePosixPath(scope) in entry.parents
    revision = git(project, "rev-parse", f"{args.old_revision}^{{commit}}").decode().strip()
    assert re.fullmatch(r"[0-9a-f]{40}", revision)

    temp_root = Path(tempfile.gettempdir()).resolve()
    destination = Path(args.private_dir).expanduser().resolve() if args.private_dir else temp_root
    if destination == project or project in destination.parents or destination == TOOL_ROOT or TOOL_ROOT in destination.parents:
        raise ValueError("私有验收目录必须位于论文和工具仓库之外")
    if not args.private_dir:
        destination = Path(tempfile.mkdtemp(prefix="latex-review-e2e-", dir=temp_root)).resolve()
    if args.private_dir:
        destination.mkdir(mode=0o700)  # 已存在的目录绝不覆盖。
    destination.chmod(0o700)
    old, new, output = (destination / name for name in ("old", "new", "report"))
    old.mkdir()
    new.mkdir()
    index_path = Path(git(project, "rev-parse", "--git-path", "index").decode().strip())
    if not index_path.is_absolute():
        index_path = project / index_path
    index_before = digest(index_path)
    status_before = git(project, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    started = time.monotonic()
    old_snapshot(project, revision, scope, old)
    new_snapshot(project, scope, new)
    copied_seconds = time.monotonic() - started
    old_manifest, new_manifest = files_manifest(old), files_manifest(new)
    assert entry.as_posix() in old_manifest and entry.as_posix() in new_manifest
    from latex_review import resolve_sources

    with resolve_sources(entry=entry.as_posix(), old_dir=str(old), new_dir=str(new)) as pair:
        dependency_paths = {}
        for side, source, manifest in (("old", pair.old, old_manifest), ("new", pair.new, new_manifest)):
            expanded = source.expand()
            names = {entry.as_posix(), *(dependency.file for dependency in expanded.dependencies)}
            dependency_paths[side] = {name: manifest[name] for name in sorted(names) if name in manifest}

    fake_bin = destination / "fake-tex-bin"
    fake_bin.mkdir()
    marker = destination / "tex-invoked"
    for engine in ("latexmk", "pdflatex", "xelatex", "lualatex", "tectonic"):
        executable = fake_bin / engine
        executable.write_text(f'#!/bin/sh\nprintf "%s\\n" "{engine}" >> "{marker}"\nexit 97\n')
        executable.chmod(0o755)
    env = {**os.environ, "PYTHONPATH": str(TOOL_ROOT / "src"),
           "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
    command = [sys.executable, "-m", "latex_review.cli", "--old-dir", str(old),
               "--new-dir", str(new), "--entry", entry.as_posix(), "--output", str(output)]
    started = time.monotonic()
    completed = subprocess.run(command, cwd=destination, env=env, capture_output=True, text=True)
    review_seconds = time.monotonic() - started
    (destination / "stdout.log").write_text(completed.stdout, encoding="utf-8")
    (destination / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    status_after = git(project, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    assert status_before == status_after, "原项目状态发生变化"
    assert index_before == digest(index_path), "原项目索引发生变化"
    assert not marker.exists(), "运行时调用了 TeX 引擎"
    assert completed.returncode in (0, 2), f"报告生成失败：退出码 {completed.returncode}；见 stderr.log"
    diff = check_report(output, old, new)
    metadata = {
        "project_root": str(project), "entry": entry.as_posix(), "scope": scope,
        "old_revision": revision, "new_source": "磁盘工作区快照（含未忽略的未跟踪文件）",
        "old_files": old_manifest, "new_files": new_manifest,
        "old_fingerprint": manifest_digest(old_manifest), "new_fingerprint": manifest_digest(new_manifest),
        "dependency_files": dependency_paths,
        "dependency_fingerprints": {side: manifest_digest(paths) for side, paths in dependency_paths.items()},
        "status_porcelain_hex": status_before.hex(), "index_sha256": index_before,
        "command": command, "exit_code": completed.returncode,
        "copy_seconds": round(copied_seconds, 3), "review_seconds": round(review_seconds, 3),
        "summary": diff["summary"],
        "diagnostic_codes": sorted({item["code"] for item in diff["diagnostics"]}),
    }
    (destination / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"private_dir": str(destination), "exit_code": completed.returncode,
                      "review_seconds": metadata["review_seconds"], "summary": diff["summary"]},
                     ensure_ascii=False))
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, help="论文 Git 仓库根目录")
    parser.add_argument("--entry", required=True, help="相对项目根目录的 TeX 入口")
    parser.add_argument("--old-revision", required=True, help="固定的旧版 Git 提交")
    parser.add_argument("--scope", help="快照范围，默认入口所在目录；必须包含所有依赖")
    parser.add_argument("--private-dir", help="新建私有结果目录，默认系统临时目录")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
