"""内容预览回归：复杂表体、逐项公式状态与指定页 PDF。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import pytest

from latex_review import compare_projects, parse_project, resolve_sources, write_report
from latex_review.fragment_render import render_fragment
from latex_review.preview import _math
from latex_review.report import _graphic_page, _pdf_preview
from latex_review.table_model import display_tables, parse_table


def _two_page_pdf() -> bytes:
    red = b"1 0 0 rg 0 0 100 100 re f\n"
    blue = b"0 0 1 rg 0 0 100 100 re f\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(red) + red + b"endstream",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> /Contents 6 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(blue) + blue + b"endstream",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(result)


def _pair(tmp_path: Path, tex: str, pdf: bytes | None = None):
    for side in ("old", "new"):
        root = tmp_path / side
        root.mkdir(parents=True)
        (root / "main.tex").write_text(r"\begin{document}" + "\n" + tex + "\n" + r"\end{document}", encoding="utf-8")
        if pdf is not None:
            (root / "fig").mkdir()
            (root / "fig/chart.pdf").write_bytes(pdf)
    with resolve_sources(entry="main.tex", old_dir=tmp_path / "old", new_dir=tmp_path / "new") as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        return before, after, compare_projects(before, after)


def test_complex_table_display_is_independent_of_cell_comparison(tmp_path):
    raw = (r"\begin{table*}\resizebox{\linewidth}{!}{\begin{tabular*}{\textwidth}{@{}p{2cm}cc@{}}"
           r"\toprule \multicolumn{2}{c}{A and B}&C\\"
           r"\multirow{2}{*}{X}&\makecell{1\\2}&2\\ &3&4\\\bottomrule"
           r"\end{tabular*}}\end{table*}")
    assert parse_table(raw)[0] is None
    grids = display_tables(raw)
    assert len(grids) == 1 and grids[0][0][0].colspan == 2
    assert grids[0][1][0].rowspan == 2
    before, after, comparison = _pair(tmp_path, raw)
    html = write_report(before, after, comparison, tmp_path / "report", pdf_converter="").html.read_text()
    assert 'colspan="2"' in html and 'rowspan="2"' in html
    assert "A and B" in html and r"\begin{table*}" not in html
    assert re.search(r"1\s*<br>\s*2", html)


def test_math_ordinary_command_is_sent_to_renderer_but_unsafe_command_is_blocked():
    assert 'class="math-tex"' in _math(r"\mathscr{F}+\coloneqq x", True)
    assert "此处暂无法预览" in _math(r"\href{javascript:x}{x}", True)


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js 运行页面脚本样例")
def test_math_page_marks_each_formula_result(tmp_path):
    before, after, comparison = _pair(tmp_path, r"First $x$. Second $y$.")
    html = write_report(before, after, comparison, tmp_path / "report", pdf_converter="").html.read_text()
    script = re.search(r"<script>\n(.*?)\n</script>", html, re.S).group(1)
    harness = r"""
const vm=require('vm');
function node(value){return {textContent:value,dataset:{display:'false'},style:{},classList:{add(){}},replaceChildren(value){this.child=value;},querySelector(){return null;}};}
const nodes=[node('good'),node('bad')];
const status={textContent:'',dataset:{}};
const context={window:{},document:{getElementById:()=>status,querySelectorAll:()=>nodes,createElement:()=>({})},setTimeout:()=>1,clearTimeout:()=>{}};
vm.runInNewContext(%s,context);
context.MathJax=context.window.MathJax;
context.MathJax.startup.promise=Promise.resolve();
context.MathJax.tex2chtmlPromise=async (value)=>({querySelector:()=>value==='bad'?{}:null});
context.window.mathDependencyReady();
setTimeout(()=>{if(status.dataset.state!=='partial'||nodes[0].style.visibility!=='visible'||nodes[1].textContent!=='此处暂无法预览')process.exit(1);},0);
""" % json.dumps(script)
    subprocess.run(["node", "-e", harness], check=True)


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="需要 PDF 转换器")
def test_pdf_figure_uses_requested_second_page(tmp_path):
    pdf = _two_page_pdf()
    before, after, comparison = _pair(tmp_path, r"\begin{figure}\includegraphics[page=2]{fig/chart.pdf}\end{figure}", pdf)
    report = write_report(before, after, comparison, tmp_path / "report")
    image = report.directory / "previews/new/fig/chart.pdf.page-2.png"
    assert image.is_file()
    html = report.html.read_text()
    assert 'alt="PDF 图 fig/chart.pdf 的第 2 页预览"' in html
    source = report.directory / "assets/new/fig/chart.pdf"
    direct = tmp_path / "direct"
    subprocess.run([shutil.which("pdftoppm"), "-f", "2", "-l", "2", "-singlefile", "-scale-to", "1200",
                    "-png", str(source), str(direct)], check=True)
    assert image.read_bytes() == direct.with_suffix(".png").read_bytes()
    first = tmp_path / "first"
    subprocess.run([shutil.which("pdftoppm"), "-f", "1", "-l", "1", "-singlefile", "-scale-to", "1200",
                    "-png", str(source), str(first)], check=True)
    assert image.read_bytes() != first.with_suffix(".png").read_bytes()


def test_pdf_converter_uses_next_tool_and_reports_failure(tmp_path):
    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(_two_page_pdf())
    bad = tmp_path / "bad"
    # 这些脚本只使用标准库；真实解释器路径避免虚拟环境路径中的空格拆开 shebang。
    bad.write_text(f"#!{Path(sys.executable).resolve()}\nimport sys\nsys.exit(7)\n")
    good = tmp_path / "good"
    good.write_text(f"#!{Path(sys.executable).resolve()}\nimport pathlib,sys\npathlib.Path(sys.argv[-1]+'.png').write_bytes(b'PNG')\n")
    bad.chmod(0o755)
    good.chmod(0o755)
    output = tmp_path / "out"
    output.mkdir()
    image, reason = _pdf_preview(pdf, output, Path("previews/ok.png"),
                                 (("pdftoppm", str(bad)), ("pdftoppm", str(good))), 2, 1)
    assert image is not None and reason == ""
    image, reason = _pdf_preview(pdf, output, Path("previews/fail.png"), (("pdftoppm", str(bad)),), 2, 1)
    assert image is None and "退出状态 7" in reason


def test_graphic_options_do_not_silently_show_wrong_pdf_content():
    assert _graphic_page(r"\includegraphics[width=.8\textwidth,page=3]{chart.pdf}", "chart.pdf") == (3, None)
    assert _graphic_page(r"\includegraphics[page=2,trim=1cm 0 0 0,clip]{chart.pdf}", "chart.pdf")[0] is None
    assert _graphic_page(r"\includegraphics[page=zero]{chart.pdf}", "chart.pdf")[0] is None
    repeated = r"\includegraphics[page=1]{chart.pdf}\includegraphics[page=2]{chart.pdf}"
    assert _graphic_page(repeated, "chart.pdf", 1) == (2, None)


@pytest.mark.skipif(shutil.which("pdflatex") is None or shutil.which("pdftoppm") is None,
                    reason="需要现有的 LaTeX 与 PDF 工具")
def test_fragment_compilation_uses_isolated_output_and_version_key(tmp_path):
    before, _, _ = _pair(tmp_path, "$x$.")
    output = tmp_path / "report"
    output.mkdir()
    image, reason = render_fragment("$x^2$", "equation", before.expanded.source, output,
                                    (("pdftoppm", shutil.which("pdftoppm")),), timeout=6)
    assert reason == "" and image is not None and image.is_file()
    table, table_reason = render_fragment(r"\begin{tabular}{c}Value\end{tabular}", "table",
                                          before.expanded.source, output,
                                          (("pdftoppm", shutil.which("pdftoppm")),), timeout=6)
    assert table_reason == "" and table is not None and table.is_file()
    assert not (tmp_path / "old/fragment.pdf").exists()
    unavailable, reason = render_fragment(r"\input{/etc/passwd}", "equation", before.expanded.source, output,
                                           (("pdftoppm", shutil.which("pdftoppm")),), timeout=1)
    assert unavailable is None and "允许" in reason


def test_fragment_compile_timeout_is_bounded(tmp_path, monkeypatch):
    before, _, _ = _pair(tmp_path, "$x$.")
    output = tmp_path / "report"
    output.mkdir()
    slow = tmp_path / "slow-tex"
    slow.write_text(f"#!{Path(sys.executable).resolve()}\nimport sys,time\n"
                    "print('pdfTeX') if '--version' in sys.argv else time.sleep(5)\n")
    slow.chmod(0o755)
    monkeypatch.setattr("latex_review.fragment_render._program", lambda *_: str(slow))
    start = time.monotonic()
    image, reason = render_fragment("$x$", "equation", before.expanded.source, output, (), timeout=.1)
    assert image is None and "超时" in reason
    assert time.monotonic() - start < 3


def test_fragment_without_engine_ends_with_machine_reason(tmp_path, monkeypatch):
    before, _, _ = _pair(tmp_path, "$x$.")
    output = tmp_path / "report"
    output.mkdir()
    monkeypatch.setattr("latex_review.fragment_render._program", lambda *_: None)
    image, reason = render_fragment("$x$", "equation", before.expanded.source, output, (), timeout=1)
    assert image is None and "缺少可用" in reason
