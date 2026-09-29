"""由双侧编译文档生成安全位图、页面配对与审阅节点关联。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import zlib

from .compilation import CompileStatus
from .contract import Diagnostic, ReviewDocument, SourceLocation
from .report import _write_managed


@dataclass(frozen=True)
class PagePair:
    old: int | None
    new: int | None
    kind: str
    difference: float | None
    image: str | None


def _pgm(data: bytes) -> tuple[int, int, bytes]:
    if not data.startswith(b"P5"):
        raise ValueError("页面转换器未输出灰度位图")
    pos = 2
    tokens = []
    while len(tokens) < 3:
        while pos < len(data) and chr(data[pos]).isspace():
            pos += 1
        if pos < len(data) and data[pos] == 35:
            pos = data.find(b"\n", pos) + 1
            continue
        match = re.match(rb"\d+", data[pos:])
        if not match:
            raise ValueError("页面位图头无效")
        tokens.append(int(match.group()))
        pos += len(match.group())
    if pos >= len(data) or data[pos:pos + 1] not in (b"\n", b" ", b"\r"):
        raise ValueError("页面位图尺寸无效")
    pos += 1
    width, height, depth = tokens
    if depth != 255 or not 0 < width <= 1200 or not 0 < height <= 1200 or len(data) - pos != width * height:
        raise ValueError("页面位图超过限制或内容无效")
    return width, height, data[pos:]


def _png(width: int, height: int, pixels: bytes, *, color: bool = False) -> bytes:
    channels = 3 if color else 1
    if len(pixels) != width * height * channels:
        raise ValueError("像素尺寸不一致")
    def chunk(name: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + name + body + struct.pack(">I", zlib.crc32(name + body))
    raw = b"".join(b"\0" + pixels[row * width * channels:(row + 1) * width * channels]
                   for row in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">2I5B", width, height, 8, 2 if color else 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _run(command: list[str], timeout: float = 10) -> bytes:
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
        raise OSError(result.stderr[:300].decode("utf-8", "replace") or "页面转换失败")
    return result.stdout


def _render_page(pdf: Path, page: int, converter: str, scale: int) -> tuple[int, int, bytes]:
    return _pgm(_run([converter, "-f", str(page), "-l", str(page), "-singlefile",
                      "-scale-to", str(scale), "-gray", str(pdf)]))


def _pages(pdf: Path, output: Path, side: str, converter: str, info: str) -> tuple[list[tuple[int, int, bytes]], list[str]]:
    metadata = _run([info, str(pdf)]).decode("utf-8", "replace")
    match = re.search(r"^Pages:\s*(\d+)", metadata, re.M)
    if not match or not 0 < int(match.group(1)) <= 80:
        raise ValueError("文档页数未知或超过 80 页预览限制")
    low = []
    images = []
    for page in range(1, int(match.group(1)) + 1):
        small = _render_page(pdf, page, converter, 96)
        large = _render_page(pdf, page, converter, 900)
        relative = f"pages/{side}/{page:04d}.png"
        _write_managed(output, Path(relative), content=_png(*large))
        low.append(small)
        images.append(relative)
    return low, images


def _cost(a: tuple[int, int, bytes], b: tuple[int, int, bytes]) -> float:
    if a[:2] != b[:2]:
        return 1.0
    left, right = a[2], b[2]
    dark = sum((255 - x) + (255 - y) for x, y in zip(left, right))
    return min(1.0, sum(abs(x - y) for x, y in zip(left, right)) / max(1, dark / 2))


def align_pages(old: list[tuple[int, int, bytes]], new: list[tuple[int, int, bytes]]) -> list[PagePair]:
    """有插入/删除代价的全局配对；页码平移不会形成级联误报。"""
    rows, cols = len(old), len(new)
    dp = [[0.0] * (cols + 1) for _ in range(rows + 1)]
    moves = [[""] * (cols + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        dp[i][0], moves[i][0] = i * .55, "remove"
    for j in range(1, cols + 1):
        dp[0][j], moves[0][j] = j * .55, "add"
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            candidates = ((dp[i - 1][j - 1] + _cost(old[i - 1], new[j - 1]), "pair"),
                          (dp[i - 1][j] + .55, "remove"), (dp[i][j - 1] + .55, "add"))
            dp[i][j], moves[i][j] = min(candidates, key=lambda item: item[0])
    pairs = []
    i, j = rows, cols
    while i or j:
        move = moves[i][j]
        if move == "pair":
            difference = _cost(old[i - 1], new[j - 1])
            pairs.append(PagePair(i, j, "unchanged" if difference < .01 else "changed", round(difference, 4), None))
            i -= 1; j -= 1
        elif move == "remove":
            pairs.append(PagePair(i, None, "removed", None, None)); i -= 1
        else:
            pairs.append(PagePair(None, j, "added", None, None)); j -= 1
    return list(reversed(pairs))


def _page_for_location(location: SourceLocation | None, status: CompileStatus, output: Path) -> tuple[int | None, str]:
    if status.status != "success" or not status.pdf or not status.synctex:
        return None, "该侧编译文档或位置索引不可用"
    if location is None or not location.file or not location.start_line or not location.end_line or location.confidence < .8:
        return None, "原始源码位置不确定"
    binary = shutil.which("synctex")
    if not binary:
        return None, "缺少 synctex 定位工具"
    pages = []
    for line in {location.start_line, location.end_line}:
        try:
            output_text = _run([binary, "view", "-i", f"{line}:0:{location.file}", "-o", str(output / status.pdf)],
                               timeout=4).decode("utf-8", "replace")
        except (OSError, subprocess.TimeoutExpired):
            return None, "SyncTeX 查询失败"
        found = {int(value) for value in re.findall(r"^Page:(\d+)", output_text, re.M)}
        if len(found) != 1:
            return None, "SyncTeX 对该源码位置没有唯一页码"
        pages.extend(found)
    return (pages[0], "确定") if len(set(pages)) == 1 else (None, "审阅节点跨页")


def build_visual(old: CompileStatus, new: CompileStatus, output: Path, document: ReviewDocument | None = None
                 ) -> tuple[dict, tuple[Diagnostic, ...]]:
    result: dict = {"old": [], "new": [], "pairs": [], "changes": {}, "comparable": False}
    diagnostics: list[Diagnostic] = []
    converter, info = shutil.which("pdftoppm"), shutil.which("pdfinfo")
    if not converter or not info:
        diagnostics.append(Diagnostic("page_tools_missing", "warning", "页面预览需要 pdftoppm 和 pdfinfo"))
    else:
        bitmaps = {}
        for side, status in (("old", old), ("new", new)):
            if status.status != "success" or not status.pdf:
                continue
            try:
                bitmaps[side], result[side] = _pages(output / status.pdf, output, side, converter, info)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                diagnostics.append(Diagnostic("page_preview_failed", "warning", f"{side} 侧页面预览失败：{exc}"))
        if "old" in bitmaps and "new" in bitmaps:
            result["comparable"] = True
            pairs = align_pages(bitmaps["old"], bitmaps["new"])
            for pair in pairs:
                image = None
                if pair.old and pair.new and pair.kind == "changed":
                    try:
                        a = _render_page(output / old.pdf, pair.old, converter, 900)
                        b = _render_page(output / new.pdf, pair.new, converter, 900)
                        if a[:2] == b[:2]:
                            rgb = bytes(channel for x, y in zip(a[2], b[2])
                                        for channel in (255, max(0, 255 - abs(x - y) * 3),
                                                        max(0, 255 - abs(x - y) * 3)))
                            image = f"pages/diff/{pair.old:04d}-{pair.new:04d}.png"
                            _write_managed(output, Path(image), content=_png(a[0], a[1], rgb, color=True))
                    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                        diagnostics.append(Diagnostic("page_diff_failed", "warning",
                                                      f"页面差异图 {pair.old} → {pair.new} 生成失败：{exc}"))
                result["pairs"].append(asdict(PagePair(pair.old, pair.new, pair.kind, pair.difference, image)))
        else:
            diagnostics.append(Diagnostic("pages_not_comparable", "warning", "缺少双侧编译文档；页面视觉差异不可比较"))
    if document:
        for change in document.changes:
            result["changes"][change.id] = {}
            for side, status in (("old", old), ("new", new)):
                page, reason = _page_for_location(getattr(change, f"source_{side}"), status, output)
                result["changes"][change.id][side] = {"page": page, "reason": reason}
    _write_managed(output, Path("pages/visual.json"),
                   content=(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())
    return result, tuple(diagnostics)
