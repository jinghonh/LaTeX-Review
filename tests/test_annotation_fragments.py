"""Renderer root hints must be exactly equivalent or use the legacy parser."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256

import pytest

from annotation_content_oracle import legacy_build_content
from test_annotation_html_memo import BODIES, parsed
from latex_review.annotations import content
from latex_review.annotations import html_fragments as module
from latex_review.preview import render_preview
from latex_review.preview_fragments import bind_fragments
from latex_review import project_profile
from latex_review.sources import SourceError


@pytest.fixture(autouse=True)
def clear_fragments():
    with module._CACHE_LOCK:
        module._CACHE.clear()
        module._CACHE_BYTES = 0
    yield
    with module._CACHE_LOCK:
        module._CACHE.clear()
        module._CACHE_BYTES = 0


def build(project, preview):
    return content.build_content(project, preview.html, _fragment_plan=preview._fragment_plan)


def altered(plan, roots=None, before=None, after=None):
    roots = plan.roots if roots is None else tuple(roots)
    before = plan.before if before is None else before
    after = plan.after if after is None else after
    html = before + ''.join(root.html for root in roots) + after
    return replace(plan, html=html, before=before, after=after, roots=roots)


@pytest.mark.parametrize('body', BODIES)
def test_cold_hot_fragments_equal_complete_old_oracle(tmp_path, body, monkeypatch):
    project = parsed(tmp_path, body)
    preview = render_preview(None, project)
    assert preview._fragment_plan is not None
    assert module.fragment_units(project, preview.html, preview._fragment_plan) is not None
    expected = legacy_build_content(project, preview.html)
    assert build(project, preview) == expected
    monkeypatch.setattr(module, '_extract_fragment', lambda *a: pytest.fail('hot chunk reparsed'))
    assert build(project, preview) == expected


def test_one_visible_word_reextracts_only_one_root_and_rebinds_every_source_hash(tmp_path, monkeypatch):
    body = '\n\n'.join(f'Paragraph {i} has stable text.' for i in range(10))
    project = parsed(tmp_path, body)
    initial = render_preview(None, project)
    build(project, initial)
    current = parsed(tmp_path, body.replace('Paragraph 0 has stable', 'Paragraph 0 has edited'))
    preview = render_preview(None, current)
    assert preview.html != initial.html
    extract = module._extract_fragment
    calls = []
    def counting(*args):
        calls.append(args[1].node_id)
        return extract(*args)
    monkeypatch.setattr(module, '_extract_fragment', counting)
    actual = build(current, preview)
    assert actual == legacy_build_content(current, preview.html)
    assert len(calls) == 1
    expected_hash = sha256((tmp_path / 'main.tex').read_bytes()).hexdigest()
    assert all(unit['source_span']['sha256'] == expected_hash for unit in actual['units'])


def test_root_reordering_keeps_global_order_and_cached_local_selectors(tmp_path, monkeypatch):
    project = parsed(tmp_path, 'First $x$.\n\nSecond $y$.\n\nThird.')
    preview = render_preview(None, project)
    first = build(project, preview)
    current = replace(project, nodes=tuple(reversed(project.nodes)))
    reordered = render_preview(None, current)
    monkeypatch.setattr(module, '_extract_fragment', lambda *a: pytest.fail('same root HTML should reuse facts'))
    actual = build(current, reordered)
    assert actual == legacy_build_content(current, reordered.html)
    assert [unit['order'] for unit in actual['units']] == list(range(len(actual['units'])))
    assert [unit['id'] for unit in actual['units']] != [unit['id'] for unit in first['units']]


def test_diff_custom_compose_and_tampered_html_use_full_parser(tmp_path):
    project = parsed(tmp_path, 'First.')
    preview = render_preview(project, project)
    assert preview._fragment_plan is None
    live = render_preview(None, project)
    custom = live.compose(body_class='asset')
    assert module.fragment_units(project, custom, live._fragment_plan) is None
    assert content.build_content(project, custom, _fragment_plan=live._fragment_plan) == legacy_build_content(project, custom)
    assert module.fragment_units(project, live.html + '<p>extra</p>', live._fragment_plan) is None


def test_outer_display_context_and_omitted_root_fall_back(tmp_path):
    project = parsed(tmp_path, 'First.\n\nSecond.')
    preview = render_preview(None, project)
    plan = preview._fragment_plan
    suppressed = altered(plan, before=plan.before.replace('class="preview-side"', 'class="preview-side asset"'))
    assert module.fragment_units(project, suppressed.html, suppressed) is None
    assert content.build_content(project, suppressed.html, _fragment_plan=suppressed) == legacy_build_content(project, suppressed.html)
    omitted = altered(plan, roots=plan.roots[1:], before=plan.before + plan.roots[0].html)
    assert module.fragment_units(project, omitted.html, omitted) is None
    # A parser can have an apparently correct stack while buffering an open
    # comment/tag. Roots inside that pending context must never be extracted.
    commented = altered(plan, before=plan.before + '<!--', after='-->' + plan.after)
    assert module.fragment_units(project, commented.html, commented) is None
    assert content.build_content(project, commented.html, _fragment_plan=commented) == legacy_build_content(project, commented.html)


def test_cross_fragment_duplicate_owner_and_dom_ids_fall_back(tmp_path):
    project = parsed(tmp_path, 'First $x$.\n\nSecond.')
    preview = render_preview(None, project)
    plan = preview._fragment_plan
    inline = next(node for node in project.nodes if node.review.type == 'inline_math')
    # Repeated owner means anonymous/formula counters would be global: decline.
    injected = ('<span id="duplicate-child" data-node-id="' + inline.review.id
                + '" data-annotation-latex="x">x</span>')
    root = plan.roots[1]
    changed = replace(root, html=root.html.replace('</p>', injected + '</p>'))
    duplicate_owner = altered(plan, roots=(plan.roots[0], changed))
    assert module.fragment_units(project, duplicate_owner.html, duplicate_owner) is None
    assert content.build_content(project, duplicate_owner.html, _fragment_plan=duplicate_owner) == legacy_build_content(project, duplicate_owner.html)
    first_id = 'id="' + plan.roots[0].html.split('id="', 1)[1].split('"', 1)[0] + '"'
    second_id = 'id="' + root.html.split('id="', 1)[1].split('"', 1)[0] + '"'
    changed = replace(root, html=root.html.replace(second_id, first_id, 1))
    duplicate_dom = altered(plan, roots=(plan.roots[0], changed))
    assert module.fragment_units(project, duplicate_dom.html, duplicate_dom) is None


def test_unbalanced_or_nested_side_context_and_large_roots_fall_back(tmp_path, monkeypatch):
    project = parsed(tmp_path, 'First.\n\nSecond.')
    preview = render_preview(None, project)
    plan = preview._fragment_plan
    root = plan.roots[0]
    bad = altered(plan, roots=(replace(root, html=root.html.replace('</p>', '', 1)), *plan.roots[1:]))
    assert module.fragment_units(project, bad.html, bad) is None
    assert content.build_content(project, bad.html, _fragment_plan=bad) == legacy_build_content(project, bad.html)
    nested = altered(plan, roots=(replace(root, html=root.html.replace('<p', '<section class="preview-side" data-side="new"><p', 1).replace('</p>', '</p></section>', 1)), *plan.roots[1:]))
    assert module.fragment_units(project, nested.html, nested) is None
    monkeypatch.setattr(module, '_FRAGMENT_MAX_BYTES', 0)
    assert module.fragment_units(project, preview.html, plan) is None
    assert build(project, preview) == legacy_build_content(project, preview.html)


def test_large_section_roots_within_shared_budget_are_supported(tmp_path):
    body = r'\section{Chapter}' + '\n\n' + '\n\n'.join(f'Paragraph {i} has stable ordinary words.' for i in range(400))
    project = parsed(tmp_path, body)
    preview = render_preview(None, project)
    assert any(64 * 1024 < module._size(root.html) <= module._FRAGMENT_MAX_BYTES
               for root in preview._fragment_plan.roots)
    assert module.fragment_units(project, preview.html, preview._fragment_plan) is not None
    assert build(project, preview) == legacy_build_content(project, preview.html)
    assert module._CACHE_BYTES <= module._CACHE_MAX_BYTES


def test_anonymous_formula_ordinals_are_per_disjoint_owner(tmp_path):
    project = parsed(tmp_path, 'First.\n\nSecond.')
    preview = render_preview(None, project)
    plan = preview._fragment_plan
    changed = []
    for index, root in enumerate(plan.roots):
        math = '<span data-annotation-latex="x">x</span>' * (2 - index)
        changed.append(replace(root, html=root.html.replace('</p>', math + '</p>')))
    plan = altered(plan, roots=changed)
    assert module.fragment_units(project, plan.html, plan) is not None
    actual = content.build_content(project, plan.html, _fragment_plan=plan)
    assert actual == legacy_build_content(project, plan.html)
    formulas = [unit for unit in actual['units'] if unit['kind'] == 'formula']
    assert len(formulas) == 3 and len({unit['id'] for unit in formulas}) == 3
    assert len({unit['node_id'] for unit in formulas}) == 2


def test_cache_entry_and_utf8_payload_limits(tmp_path, monkeypatch):
    project = parsed(tmp_path, '\n\n'.join(f'Paragraph {i}.' for i in range(6)))
    preview = render_preview(None, project)
    monkeypatch.setattr(module, '_CACHE_MAX_ENTRIES', 2)
    assert build(project, preview) == legacy_build_content(project, preview.html)
    assert len(module._CACHE) <= 2 and module._CACHE_BYTES <= module._CACHE_MAX_BYTES
    monkeypatch.setattr(module, '_CACHE_MAX_BYTES', 100)
    with module._CACHE_LOCK:
        module._CACHE.clear()
        module._CACHE_BYTES = 0
    assert build(project, preview) == legacy_build_content(project, preview.html)
    assert not module._CACHE and module._CACHE_BYTES == 0


def test_return_mutation_concurrency_and_readonly_rebinding(tmp_path, monkeypatch):
    project = parsed(tmp_path, 'First $x$.\n\nSecond.')
    preview = render_preview(None, project)
    damaged = build(project, preview)
    damaged['units'][0]['text'] = 'corrupted'
    damaged['units'][0]['atoms'].clear()
    current = replace(project, expanded=replace(project.expanded, source=replace(
        project.expanded.source, configuration={'project': {'read_only': True}})))
    expected = legacy_build_content(current, preview.html)
    monkeypatch.setattr(module, '_extract_fragment', lambda *a: pytest.fail('warm facts missing'))
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(lambda _: build(current, preview), range(8)))
    assert values == [expected] * 8
    assert all(not unit['source_span']['writable'] for unit in values[0]['units'])


def test_hot_fragments_keep_mid_enrichment_configuration_fence(tmp_path, monkeypatch):
    project = parsed(tmp_path, 'First.')
    preview = render_preview(None, project)
    build(project, preview)
    settings = iter([{'project': {'read_only': False}}, {'project': {'read_only': True}}])
    monkeypatch.setattr(project_profile, 'settings', lambda source: next(settings))
    with pytest.raises(SourceError, match='项目配置已变化'):
        build(project, preview)
