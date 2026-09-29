"""编译页链接遇到被筛选隐藏的主变更时仍可定位。"""

from __future__ import annotations

import pytest

from latex_review import compare_projects, parse_project, resolve_sources, write_report
from latex_review.compilation import CompileStatus


def test_page_link_reveals_filtered_change_and_updates_preview(tmp_path) -> None:
    browser_api = pytest.importorskip("playwright.sync_api")
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(); new.mkdir()
    (old / "main.tex").write_text("\\section{Results}\nOld text.\n", encoding="utf-8")
    (new / "main.tex").write_text("\\section{Results}\nNew text.\n", encoding="utf-8")
    with resolve_sources(entry="main.tex", old_dir=old, new_dir=new) as pair:
        before, after = parse_project(pair.old.expand()), parse_project(pair.new.expand())
        comparison = compare_projects(before, after)
        change = comparison.document.changes[0]
        rendering = {
            "comparable": True,
            "old": ["pages/old/0001.png"], "new": ["pages/new/0001.png"],
            "pairs": [{"old": 1, "new": 1, "kind": "changed", "difference": .1, "image": None}],
            "changes": {change.id: {"old": {"page": 1, "reason": "确定"},
                                    "new": {"page": 1, "reason": "确定"}}},
        }
        statuses = (CompileStatus("old", "success", "pdflatex", 0, None, None, None, None),
                    CompileStatus("new", "success", "pdflatex", 0, None, None, None, None))
        report = write_report(before, after, comparison, tmp_path / "output", pdf_converter="",
                              rendering=rendering, statuses=statuses)
    with browser_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except browser_api.Error as exc:
            pytest.skip(f"浏览器运行环境不可用：{exc}")
        try:
            page = browser.new_page()
            page.goto(report.html.as_uri(), wait_until="domcontentloaded")
            card = page.locator(f"#{change.id}")
            other_kind = "added" if change.kind != "added" else "removed"
            page.locator("#kind-filter").select_option(other_kind)
            assert card.is_hidden()
            page.locator(f'.rendered-pages a[href="#{change.id}"]').first.click()
            assert card.is_visible()
            assert page.locator("#kind-filter").input_value() == "all"
            assert page.evaluate("document.activeElement.classList.contains('change-jump')")
            assert page.locator("#jump-status").inner_text().strip()
            assert page.locator(".preview-side .is-highlighted").count() > 0
        finally:
            browser.close()
