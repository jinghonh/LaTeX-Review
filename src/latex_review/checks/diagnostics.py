"""Display-only bridge for legacy diagnostics; never alters either result model."""
from dataclasses import asdict

from ..identity import identity_hash


_RULES = {'bibliography_key_unresolved', 'unresolved_reference', 'duplicate_label',
          'figure_unreferenced', 'table_unreferenced'}


def _target(item):
    # These are the explicitly supported legacy producers, not a general
    # message-similarity heuristic. Location and code must ALSO match in the UI.
    if item.code not in _RULES:
        return None
    if item.evidence and item.evidence[0].startswith('键：'):
        return item.evidence[0][2:]
    if item.code == 'unresolved_reference' and item.message.startswith('交叉引用目标不存在：'):
        return item.message[len('交叉引用目标不存在：'):]
    if item.code == 'bibliography_key_unresolved':
        prefix, suffix = '引用键 ', ' 无法解析文献元数据；已保留引用键'
        if item.message.startswith(prefix) and item.message.endswith(suffix):
            return item.message[len(prefix):-len(suffix)]
    return None


def preview_diagnostics_data(diagnostics, content_id):
    """Keep current-side/global diagnostics and all original provenance fields.

    Old-only/resolved diagnostics belong to the unchanged legacy diff view.
    Unknown locations are retained for display but cannot establish deduplication.
    """
    records, seen = [], set()
    for item in diagnostics:
        if item.rule_status == 'resolved' or (item.source_old and not item.source_new and not item.related_sources_new):
            continue
        raw = asdict(item)
        identifier = identity_hash(raw)
        if identifier in seen:
            continue
        seen.add(identifier)
        records.append({'id': identifier, 'target': _target(item), 'diagnostic': raw})
    return {'content_id': content_id, 'diagnostics': records}
