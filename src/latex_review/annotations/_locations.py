"""Compact derived location cache; never changes the annotation revision."""
from copy import deepcopy
import json


def compact_location(association):
    result = {k: deepcopy(v) for k, v in association.items() if k not in ('anchor', 'candidates')}
    result['anchor'] = association.get('anchor')
    result['candidates'] = [dict(candidate) for candidate in association['candidates']]
    for entry in [result, *result['candidates']]:
        anchor = entry.get('anchor')
        if anchor:
            entry['anchor'] = {k: anchor[k] for k in ('unit_id', 'kind', 'start', 'end', 'quote')}
    return result


def expand_location(content, association):
    from .content import make_anchor
    result = deepcopy(association)
    for entry in [result, *result['candidates']]:
        old = entry.get('anchor')
        if old:
            selection = {'unit_id': old['unit_id']}
            if old['kind'] == 'text':
                selection.update(start=old['start'], end=old['end'], quote=old['quote'])
            entry['anchor'] = make_anchor(content, selection)
    container = result.get('container')
    if container:
        unit = next(u for u in content['units'] if u['id'] == container['unit_id'])
        container.update(selector=unit['selector'], order=unit['order'])
    return result


class LocationCache:
    """Access only while the project's existing storage lock is held."""
    def __init__(self, store, content):
        from ._validation import AnnotationError, _hash
        self.path = store.directory / f'{store.project_id}.locations.json'
        if self.path.is_symlink():
            raise AnnotationError('定位缓存不可为符号链接')
        self.version = content['version_id']
        # Selector/order can change with rendering; source/text may not.
        self.fingerprint = _hash([{k: v for k, v in u.items() if k not in ('selector', 'change_ids')}
                                  for u in content['units']])
        try:
            self.data = json.loads(self.path.read_text())
            if (not isinstance(self.data, dict) or self.data.get('fingerprint') != self.fingerprint
                    or not isinstance(self.data.get('entries', {}), dict)):
                self.data = {}
        except (OSError, ValueError):
            self.data = {}
        self.entries = self.data.setdefault('entries', {})
        self.dirty = False

    def get(self, anchor):
        from ._validation import _hash
        return self.entries.get(_hash(anchor))

    def put(self, anchor, association):
        from ._validation import _hash
        self.entries[_hash(anchor)] = compact_location(association)
        while len(self.entries) > 512:
            self.entries.pop(next(iter(self.entries)))
        self.dirty = True

    def save(self):
        from ._validation import _atomic_write
        if self.dirty:
            self.data.update(fingerprint=self.fingerprint, version_id=self.version)
            _atomic_write(self.path, self.data)
