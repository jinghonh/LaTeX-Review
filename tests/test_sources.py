import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from latex_review.sources import SourceError, resolve_sources


def write(root: Path, name: str, content: str | bytes):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


def git(root: Path, *args: str):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def test_two_roots_nested_repeated_mapping_and_diagnostics(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(); new.mkdir()
    for root, word in ((old, "旧"), (new, "新")):
        write(root, "main.tex", "A\r\n\\input{sections/a}\n\\include{sections/a}\n\\bibliography{refs}\n\\includegraphics[width=1cm]{fig/plot}\n")
        write(root, "sections/a.tex", ("前言\n" if word == "新" else "") + f"{word}😀\r\\input{{../shared}}\n")
        write(root, "shared.tex", "共\r\n\\newcommand{\\foo}{保留}\n")
        write(root, "refs.bib", f"@book{{x,title={{{word}}}}}")
        write(root, "fig/plot.pdf", word.encode())
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before = (old / "main.tex").read_bytes(), (new / "main.tex").read_bytes()
        left, right = pair.old.expand(), pair.new.expand()
        assert "旧😀" in left.text and "新😀" not in left.text
        assert "新😀" in right.text and "旧😀" not in right.text
        assert "\\newcommand{\\foo}{保留}" in left.text
        assert [d.kind for d in left.dependencies].count("include") == 4
        assert {d.file for d in left.dependencies if d.kind != "include"} == {"refs.bib", "fig/plot.pdf"}
        assert left.diagnostics == right.diagnostics == ()
        assert before == ((old / "main.tex").read_bytes(), (new / "main.tex").read_bytes())
        first = left.text.index("旧😀")
        second = left.text.index("旧😀", first + 1)
        first_map = left.origin_ranges(first, first + 2)[0]
        second_map = left.origin_ranges(second, second + 2)[0]
        assert (first_map.expanded_start, first_map.origin.file, first_map.origin.start, first_map.origin.end) == (first, "sections/a.tex", 0, 2)
        assert (first_map.origin.start_line, first_map.origin.start_column, first_map.origin.end_line, first_map.origin.end_column) == (1, 1, 1, 3)
        assert first_map.origin.include_instance != second_map.origin.include_instance
        right_map = right.origin_ranges(right.text.index("新😀"), right.text.index("新😀") + 2)[0]
        assert right_map.origin.file == first_map.origin.file
        assert right_map.origin.start_line == 2 and first_map.origin.start_line == 1
        across = left.origin_ranges(left.text.index("旧😀"), left.text.index("共") + 1)
        assert {item.origin.file for item in across} == {"sections/a.tex", "shared.tex"}
        assert len(across) == 2
        assert left.origin_ranges(0, 3)[0].origin.end_line == 2  # CRLF 算一个换行。


def test_missing_cycle_unknown_and_comment_not_dependency(tmp_path):
    for side in ("old", "new"):
        root = tmp_path / side
        write(root, "main.tex", "% \\input{ignored}\r\\input{a}\n\\input{missing}\n\\input{\\dynamic}\n\\includegraphics{missing-image}\n")
        write(root, "a.tex", "\\input{b}\n")
        write(root, "b.tex", "\\input{a}\n")
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        project = pair.old.expand()
        assert [item.code for item in project.diagnostics] == ["include_cycle", "missing_dependency", "uncertain_dependency", "missing_dependency"]
        assert project.diagnostics[0].include_chain == ("main.tex", "a.tex", "b.tex", "a.tex")
        assert all(item.file != "ignored.tex" for item in project.dependencies)
        position = project.text.index("\\input{missing}")
        assert project.origin_ranges(position, position + 15)[0].origin.confidence == "unknown"
        assert "\\input{\\dynamic}" in project.text


def test_independent_files_and_invalid_inputs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write(a, "old.tex", "\\input{x}")
    write(a, "x.tex", "左")
    write(b, "new.tex", "\\input{x}")
    write(b, "x.tex", "右")
    with resolve_sources(old_file=a / "old.tex", new_file=b / "new.tex") as pair:
        assert pair.old.expand().text == "左"
        assert pair.new.expand().text == "右"
        assert pair.old.identity.kind == pair.new.identity.kind == "file"
    with pytest.raises(SourceError, match="互斥") as caught:
        with resolve_sources(entry="main.tex", old_dir=a, new_dir=b, old_revision="HEAD"):
            pass
    assert caught.value.code == "conflicting_modes"
    with pytest.raises(SourceError) as caught:
        with resolve_sources(entry="../elsewhere.tex", old_dir=a, new_dir=b):
            pass
    assert caught.value.code == "entry_outside_root"
    with pytest.raises(SourceError) as caught:
        with resolve_sources(entry="missing.tex", old_dir=a, new_dir=b):
            pass
    assert caught.value.code == "entry_missing" and caught.value.side == "old"
    with pytest.raises(SourceError) as caught:
        with resolve_sources(entry="main.tex", cwd=tmp_path):
            pass
    assert caught.value.code == "explicit_sources_required"


def test_graphicspath_unreadable_and_link_boundary(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    for root in (old, new):
        write(root, "main.tex", "\\graphicspath{{figures/}}\n\\includegraphics{chart}\n\\input{unreadable}\n\\input{escape}\n")
        write(root, "figures/chart.png", b"png")
        unreadable = write(root, "unreadable.tex", "不能展开")
        unreadable.chmod(0)
        (root / "escape.tex").symlink_to(tmp_path / "outside.tex")
    write(tmp_path, "outside.tex", "不可越界")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        result = pair.old.expand()
        assert ("graphic", "figures/chart.png") in {(item.kind, item.file) for item in result.dependencies}
        assert [item.code for item in result.diagnostics] == ["unreadable_dependency", "dependency_outside_root"]
        assert "不能展开" not in result.text and "不可越界" not in result.text
        marker = result.text.index("\\input{unreadable}")
        assert result.origin_ranges(marker, marker + 18)[0].origin.confidence == "unknown"


def test_git_two_commits_and_current_disk_snapshot(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    write(root, ".gitignore", "ignored.tex\n")
    write(root, "main.tex", "\\input{part}\n\\input{newdep}\n\\input{ignored}\n\\includegraphics{fig/logo}\n")
    write(root, "part.tex", "一")
    write(root, "newdep.tex", "起")
    write(root, "fig/logo.pdf", b"old-resource")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    first = git(root, "rev-parse", "HEAD")
    write(root, "part.tex", "二")
    write(root, "fig/logo.pdf", b"new-resource")
    git(root, "add", ".")
    git(root, "commit", "-qm", "second")
    second = git(root, "rev-parse", "HEAD")
    with resolve_sources(entry="main.tex", old_revision=first, new_revision=second, cwd=root) as pair:
        assert pair.old.identity.identifier == first and pair.new.identity.identifier == second
        assert "一" in pair.old.expand().text and "二" in pair.new.expand().text
        assert (pair.old.root / "fig/logo.pdf").read_bytes() == b"old-resource"
        assert (pair.new.root / "fig/logo.pdf").read_bytes() == b"new-resource"
        old_temp, new_temp = pair.old.root, pair.new.root
    assert not old_temp.exists() and not new_temp.exists()
    write(root, "part.tex", "已暂存")
    git(root, "add", "part.tex")
    write(root, "part.tex", "磁盘最终")
    (root / "newdep.tex").unlink()
    write(root, "newdep.tex", "未跟踪新增")
    write(root, "ignored.tex", "不应读取")
    # 新增未跟踪依赖另起路径，已跟踪文件删除应仍然缺失。
    (root / "newdep.tex").unlink()
    write(root, "fresh.tex", "新依赖")
    write(root, "main.tex", "\\input{part}\n\\input{newdep}\n\\input{fresh}\n\\input{ignored}\n")
    index_before = git(root, "ls-files", "--stage")
    branch_before = git(root, "symbolic-ref", "--short", "HEAD")
    with resolve_sources(entry="main.tex", cwd=root) as pair:
        old_result, new_result = pair.old.expand(), pair.new.expand()
        assert pair.old.identity.identifier == second
        assert "二" in old_result.text and "磁盘最终" in new_result.text
        assert "已暂存" not in new_result.text
        assert "新依赖" in new_result.text
        assert [d.code for d in new_result.diagnostics] == ["missing_dependency", "ignored_dependency"]
        assert "不应读取" not in new_result.text
        assert not (pair.new.root / "ignored.tex").exists()
        old_temp, new_temp = pair.old.root, pair.new.root
        assert (pair.old.root / "newdep.tex").read_text() == "起"
        assert (pair.old.root / "fig/logo.pdf").read_bytes() == b"new-resource"
    assert not old_temp.exists() and not new_temp.exists()
    assert git(root, "ls-files", "--stage") == index_before
    assert git(root, "symbolic-ref", "--short", "HEAD") == branch_before
    assert (root / "part.tex").read_text() == "磁盘最终"
    with pytest.raises(RuntimeError):
        with resolve_sources(entry="main.tex", cwd=root) as pair:
            temporary = pair.old.root
            raise RuntimeError("failure during review")
    assert not temporary.exists()


def test_no_head_and_cli_inspection(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    write(root, "main.tex", "内容")
    with pytest.raises(SourceError) as caught:
        with resolve_sources(entry="main.tex", cwd=root):
            pass
    assert caught.value.code == "explicit_sources_required"
    other = tmp_path / "other"
    write(other, "main.tex", "另一个")
    result = subprocess.run([sys.executable, "-m", "latex_review.cli", "--old-dir", str(root), "--new-dir", str(other), "--entry", "main.tex", "--inspect-sources"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["old"]["text"] == "内容" and payload["new"]["text"] == "另一个"


def test_macro_definition_body_is_preserved_without_executing_dependencies(tmp_path):
    for side in ("old", "new"):
        root = tmp_path / side
        source = "\\newcommand{\\later}{\\input{appendix}}\n\\def\\next{\\include{appendix}}\n\\later\n\\input{actual}\n"
        write(root, "main.tex", source)
        write(root, "appendix.tex", "不得提前展开")
        write(root, "actual.tex", "实际包含")
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        for side in (pair.old, pair.new):
            result = side.expand()
            assert result.text == source.replace("\\input{actual}", "实际包含")
            assert [(item.kind, item.file) for item in result.dependencies] == [("include", "actual.tex")]
            assert [item.code for item in result.diagnostics] == ["uncertain_dependency", "uncertain_dependency"]
            assert "不得提前展开" not in result.text


def test_input_argument_after_tex_comment(tmp_path):
    for side in ("old", "new"):
        root = tmp_path / side
        write(root, "main.tex", "前\\input% 注释内的 { } 不属于参数\r\n{chapter}后")
        write(root, "chapter.tex", "章节")
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        result = pair.old.expand()
        assert result.text == "前章节后"
        assert [(item.kind, item.file) for item in result.dependencies] == [("include", "chapter.tex")]
        assert result.diagnostics == ()
        assert result.origin_ranges(1, 3)[0].origin.file == "chapter.tex"


def test_missing_git_binary_is_structured_source_error(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    write(old, "main.tex", "旧")
    write(new, "main.tex", "新")
    environment = dict(os.environ, PATH="")
    missing_git = subprocess.run(
        [sys.executable, "-m", "latex_review.cli", "main.tex", "--inspect-sources"],
        cwd=old, env=environment, capture_output=True, text=True,
    )
    assert missing_git.returncode == 4
    assert '"code": "explicit_sources_required"' in missing_git.stderr
    assert "显式指定" in missing_git.stderr
    explicit = subprocess.run(
        [sys.executable, "-m", "latex_review.cli", "--old-dir", str(old), "--new-dir", str(new), "--entry", "main.tex", "--inspect-sources"],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
    )
    assert explicit.returncode == 0, explicit.stderr
