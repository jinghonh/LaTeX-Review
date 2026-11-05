"""Frozen pre-memoization extraction oracle; independent control flow for regressions."""
import re
from pathlib import Path
from latex_review.annotations.content import _PreviewParser, _Element, _semantic_units
from latex_review.annotations._validation import SCHEMA_VERSION, digest, utf16_length

def legacy_build_content(project, preview_html: str, *, project_root: Path | None = None, changes=()):
    """Extract actual preview text, including unmodified prose and degraded formulas.

    Call while the source snapshot exists. Content identity includes resolved source
    text, bibliography/macro interpretation and semantic preview coordinates.
    Selectors and comparison-specific change membership do not affect identity.
    """
    from dataclasses import asdict
    from latex_review.cache import dependency_fingerprints
    root = (project_root or project.expanded.source.worktree_origin or project.expanded.source.root).resolve()
    entry = project.expanded.source.entry
    project_id = digest({'root': str(root), 'entry': entry})
    nodes = {n.review.id: n for n in project.nodes}
    parser = _PreviewParser()
    parser.feed(preview_html)
    blocks, formulas, counters = [], [], {}
    block_tags = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'figcaption', 'td', 'th', 'blockquote', 'li'}

    def descendants(element):
        for child in element.children:
            if isinstance(child, _Element):
                yield child
                yield from descendants(child)

    def node_owner(element):
        while element and element.attrs.get('data-node-id') not in nodes:
            element = element.parent
        return nodes[element.attrs['data-node-id']] if element else None

    def display_only(element):
        while element:
            if {'asset', 'asset-card', 'asset-metadata', 'asset-fallback', 'structure-number'} & set(element.attrs.get('class', '').split()):
                return True
            element = element.parent
        return False

    def contents(element, atoms):
        if display_only(element):
            return ''
        if 'data-annotation-latex' in element.attrs:
            owner = node_owner(element)
            raw = element.attrs['data-annotation-latex']
            formula_id = owner.review.id if owner and owner.review.type in ('inline_math', 'equation') else None
            atoms.append({'offset': 0, 'formula_node_id': formula_id, 'raw_latex': raw,
                          'selector': element.selector()})
            return '\ufffc'
        text = ''
        for child in element.children:
            if isinstance(child, str):
                text += child
            else:
                child_atoms = []
                piece = contents(child, child_atoms)
                for atom in child_atoms:
                    atom['offset'] += utf16_length(text)
                    atoms.append(atom)
                text += piece
        return text

    for side in descendants(parser.root):
        if 'preview-side' not in side.attrs.get('class', '').split() or side.attrs.get('data-side') != 'new':
            continue
        for element in descendants(side):
            if display_only(element):
                continue
            owner = node_owner(element)
            if not owner:
                continue
            if 'data-annotation-latex' in element.attrs:
                ordinal = sum(f['node_id'] == owner.review.id for f in formulas)
                formulas.append({'id': f'{owner.review.id}:formula:{ordinal}', 'kind': 'formula',
                                 'node_id': owner.review.id, 'parent_node_id': owner.review.parent_id,
                                 'raw_latex': element.attrs['data-annotation-latex'],
                                 'text': element.attrs['data-annotation-latex'], 'selector': element.selector(),
                                 'source': asdict(owner.review.source), 'section_path': list(owner.review.section_path),
                                 'labels': list(owner.labels), 'atoms': []})
                continue
            # Each leaf block is a paragraph boundary. Container nodes cannot merge paragraphs.
            is_block = element.tag in block_tags
            is_leaf_node = (element.attrs.get('data-node-id') in nodes and
                            owner.review.type not in ('equation', 'inline_math', 'math_array', 'comment', 'bibliography_change'))
            if not (is_block or is_leaf_node):
                continue
            if any(x.tag in block_tags or x.attrs.get('data-node-id') in nodes for x in descendants(element) if not display_only(x)
                   if x.attrs.get('data-node-id') not in nodes or nodes[x.attrs['data-node-id']].review.type not in ('inline_math', 'citation', 'reference')):
                continue
            if 'preview-unavailable' in element.attrs.get('class', '').split():
                continue
            atoms = []
            raw_text = contents(element, atoms)
            def normalize(value):
                return re.sub(r'[ \t\r\n\f]+', ' ', value).strip(' ')
            # Sentence highlighting introduces additional DOM whitespace. Coordinates
            # follow visible collapsed whitespace and remain stable across report/live.
            for atom in atoms:
                before = raw_text.encode('utf-16-le')[:atom['offset'] * 2].decode('utf-16-le')
                normalized_before = re.sub(r'[ \t\r\n\f]+', ' ', before).lstrip(' ')
                atom['offset'] = utf16_length(normalized_before)
            text = normalize(raw_text)
            if not text.strip() or text == '\ufffc':
                continue
            ordinal = counters.get(owner.review.id, 0)
            counters[owner.review.id] = ordinal + 1
            blocks.append({'id': f'{owner.review.id}:block:{ordinal}', 'kind': 'text',
                           'node_id': owner.review.id, 'parent_node_id': owner.review.parent_id,
                           'text': text, 'raw_latex': owner.review.raw_latex, 'atoms': atoms,
                           'selector': element.selector(), 'source': asdict(owner.review.source),
                           'section_path': list(owner.review.section_path), 'labels': list(owner.labels)})
            role = element.attrs.get('data-annotation-subunit')
            if element.tag == 'figcaption' or 'table-caption' in element.attrs.get('class', '').split():
                role = 'caption'
            if role:
                subunit = {'type': role}
                for attribute, key in (('data-row', 'row'), ('data-column', 'column'), ('data-table-index', 'table_index')):
                    if attribute in element.attrs:
                        subunit[key] = int(element.attrs[attribute])
                blocks[-1]['subunit'] = subunit
    # DOM order, not parser node ID, defines navigation order.
    positions = {}
    for section in descendants(parser.root):
        if 'preview-side' in section.attrs.get('class', '').split() and section.attrs.get('data-side') == 'new':
            for i, el in enumerate(descendants(section)):
                if node_owner(el):
                    positions[el.selector()] = i
    units = sorted(blocks + formulas, key=lambda u: positions[u['selector']])
    for order, unit in enumerate(units):
        unit['order'] = order
        unit['change_ids'] = [c.id for c in changes if c.new_node_id in (unit['node_id'], unit['parent_node_id'])]
    from latex_review.annotation_sources import enrich_content
    enrich_content(project, units, root)
    # Keep legacy coordinates under their old version. Different parse/render
    # options must never claim the same version with different semantic units.
    version_id = digest({'entry': entry, 'content_model': 3, 'files': dependency_fingerprints(project.expanded),
                        'macros': {k: asdict(v) for k, v in project.macros.items()},
                        'units': _semantic_units(units)})
    return {'schema_version': SCHEMA_VERSION, 'project_id': project_id, 'project_root': str(root),
            'entry': entry, 'version_id': version_id, 'coordinate_system': 'utf16-normalized-whitespace-with-formula-atoms',
            'units': units}

