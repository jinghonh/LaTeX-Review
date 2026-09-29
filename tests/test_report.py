"""报告目录的资源隔离、降级和同源数据集成样例。"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
import shutil
import struct
import sys
import time
import zlib

import pytest

from latex_review import compare_projects, parse_project, resolve_sources, write_report
from latex_review.report import ReportPathError


def _png(rgb: tuple[int, int, int]) -> bytes:
    def chunk(kind: bytes, content: bytes) -> bytes:
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">2I5B", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0" + bytes(rgb))) + chunk(b"IEND", b""))


def _project(root: Path, text: str, rgb: tuple[int, int, int]) -> None:
    (root / "fig").mkdir(parents=True)
    (root / "main.tex").write_text(text, encoding="utf-8")
    (root / "fig" / "same.png").write_bytes(_png(rgb))


def test_report_is_movable_and_uses_same_document_for_html_and_json(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    _project(old, r"""\section{Results}
% old note
Old text \cite{a}.
\begin{figure}\includegraphics{fig/same.png}\caption{Old image}\end{figure}
""", (255, 0, 0))
    _project(new, r"""\section{Results}
% new note
New text \cite{b}.
\begin{figure}\includegraphics{fig/same.png}\caption{New image}\end{figure}
\begin{figure}\includegraphics{fig/missing.png}\caption{Missing image}\end{figure}
""", (0, 0, 255))
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        result = compare_projects(before, after, review_comments=True)
        report = write_report(before, after, result, tmp_path / "output", pdf_converter="")
    data = json.loads(report.diff_json.read_text(encoding="utf-8"))
    html = report.html.read_text(encoding="utf-8")
    assert data["summary"]["changes"] == len(data["changes"]) == html.count('class="change-card"')
    assert f'data-changes="{data["summary"]["changes"]}"' in html
    assert data["summary"]["category_hits"]["citation"] == 1
    for change in data["changes"]:
        assert f'id="{change["id"]}"' in html
        if change["old_node_id"]:
            assert f'id="old-{change["old_node_id"]}"' in html
        if change["new_node_id"]:
            assert f'id="new-{change["new_node_id"]}"' in html
    assert any(item["code"] == "report_asset_missing" for item in data["diagnostics"])
    assert f'data-count="{len(data["diagnostics"])}"' in html
    for diagnostic in data["diagnostics"]:
        assert diagnostic["code"] in html
        assert escape(diagnostic["message"]) in html
    assert "图缺失：fig/missing.png" in html and "Missing image" in html
    assert "data-old-location=" in html and "data-new-location=" in html
    assert 'id="old-comment-old-000001"' in html and 'id="new-comment-new-000001"' in html
    assert "fetch(" not in html
    assert (report.directory / "assets/old/fig/same.png").read_bytes() != (report.directory / "assets/new/fig/same.png").read_bytes()
    moved = tmp_path / "moved"
    shutil.move(report.directory, moved)
    assert (moved / "assets/old/fig/same.png").is_file()
    assert (moved / "assets/new/fig/same.png").is_file()
    assert 'src="assets/old/fig/same.png"' in (moved / "report.html").read_text(encoding="utf-8")
    assert 'src="assets/new/fig/same.png"' in (moved / "report.html").read_text(encoding="utf-8")


def test_pdf_preview_failure_keeps_provenance_and_caption(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    for root, caption in ((old, "旧图注"), (new, "新图注")):
        (root / "fig").mkdir(parents=True)
        (root / "fig" / "chart.pdf").write_bytes(b"%PDF-1.4\ninvalid preview fixture\n")
        (root / "main.tex").write_text(
            "\\section{Results}\n\\begin{figure}\\includegraphics{fig/chart.pdf}\\caption{" + caption + "}\\end{figure}\n",
            encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        report = write_report(before, after, compare_projects(before, after), tmp_path / "output", pdf_converter="")
    html = report.html.read_text(encoding="utf-8")
    data = json.loads(report.diff_json.read_text(encoding="utf-8"))
    assert "旧图注" in html and "新图注" in html
    assert "PDF 预览不可用" in html and "尺寸：未知" in html
    assert 'href="assets/old/fig/chart.pdf"' in html
    assert 'href="assets/new/fig/chart.pdf"' in html
    assert sum(item["code"] == "report_pdf_preview_unavailable" for item in data["diagnostics"]) == 2

    converter = tmp_path / "slow-converter"
    converter.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(5)\n", encoding="utf-8")
    converter.chmod(0o755)
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        start = time.monotonic()
        timed = write_report(before, after, compare_projects(before, after), tmp_path / "timed",
                             pdf_converter=str(converter), conversion_timeout=0.1)
    assert time.monotonic() - start < 2
    assert "PDF 预览不可用" in timed.html.read_text(encoding="utf-8")


@pytest.mark.parametrize("managed_path", ["assets/old/fig", "diff.json", "diagnostics.json", "report.html"])
def test_managed_output_rejects_symlinks_without_touching_external_file(tmp_path: Path, managed_path: str) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    _project(old, r"\begin{figure}\includegraphics{fig/same.png}\caption{Old}\end{figure}", (255, 0, 0))
    _project(new, r"\begin{figure}\includegraphics{fig/same.png}\caption{New}\end{figure}", (0, 0, 255))
    output, external = tmp_path / "output", tmp_path / "external"
    output.mkdir(); external.mkdir()
    target = output / managed_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if managed_path.startswith("assets"):
        (external / "same.png").write_bytes(b"outside")
        target.symlink_to(external, target_is_directory=True)
        outside_file = external / "same.png"
    else:
        outside_file = external / "outside.txt"
        outside_file.write_bytes(b"outside")
        target.symlink_to(outside_file)
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        with pytest.raises(ReportPathError):
            write_report(before, after, compare_projects(before, after), output, pdf_converter="")
    assert outside_file.read_bytes() == b"outside"


def test_graphics_resolve_by_command_origin_and_include_instance(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    for root in (old, new):
        (root / "fig").mkdir(parents=True)
        (root / "fig/same.png").write_bytes(_png((10, 20, 30)))
        for name, color in (("left", (255, 0, 0)), ("right", (0, 0, 255))):
            folder = root / name
            folder.mkdir()
            (folder / "same.png").write_bytes(_png(color))
            (folder / "part.tex").write_text(
                f"\\begin{{figure}}\\includegraphics{{same}}\\caption{{{name}}}\\end{{figure}}\n",
                encoding="utf-8")
        (root / "main.tex").write_text(
            r"\begin{figure}\includegraphics{./fig/same.png}\caption{main}\end{figure}" + "\n"
            + r"\input{left/part}\input{right/part}" + "\n", encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        report = write_report(before, after, compare_projects(before, after), tmp_path / "output", pdf_converter="")
    html = report.html.read_text(encoding="utf-8")
    for side in ("old", "new"):
        for path in ("fig/same.png", "left/same.png", "right/same.png"):
            assert f'src="assets/{side}/{path}"' in html
            assert (report.directory / "assets" / side / path).is_file()
    assert (report.directory / "assets/old/left/same.png").read_bytes() != (report.directory / "assets/old/right/same.png").read_bytes()
    assert not any(item.code == "report_asset_missing" for item in report.document.diagnostics)


def test_inline_details_jump_to_their_own_anchors(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(); new.mkdir()
    (old / "main.tex").write_text(r"\section{Results}" + "\n" + r"Text \cite{old} and $x$.", encoding="utf-8")
    (new / "main.tex").write_text(r"\section{Results}" + "\n" + r"Text \cite{new} and $y$.", encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        report = write_report(before, after, compare_projects(before, after), tmp_path / "output", pdf_converter="")
    html = report.html.read_text(encoding="utf-8")
    for category, node_type in (("citation", "citation"), ("equation", "inline_math")):
        change = next(change for change in report.document.changes
                      if any(detail.category == category for detail in change.details))
        old_child = next(node for node in report.document.nodes_old
                         if node.parent_id == change.old_node_id and node.type == node_type)
        new_child = next(node for node in report.document.nodes_new
                         if node.parent_id == change.new_node_id and node.type == node_type)
        assert f'id="old-{old_child.id}"' in html and f'id="new-{new_child.id}"' in html
        assert f'class="detail-jump" data-old="{old_child.id}" data-new="{new_child.id}"' in html


def test_svg_script_is_not_copied_or_linked(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    for root in (old, new):
        (root / "fig").mkdir(parents=True)
        (root / "main.tex").write_text(
            r"\begin{figure}\includegraphics{fig/attack.svg}\caption{safe}\end{figure}", encoding="utf-8")
        (root / "fig/attack.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>', encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        report = write_report(before, after, compare_projects(before, after), tmp_path / "out")
    html = report.html.read_text()
    assert "图资源格式未支持" in html and "alert(1)" not in html
    assert not (report.directory / "assets/old/fig/attack.svg").exists()
    assert any(item.code == "report_asset_unsupported" for item in report.document.diagnostics)
