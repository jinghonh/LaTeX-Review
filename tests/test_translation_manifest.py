"""Sessions and publication reject damaged current manifests before using translations."""
from copy import deepcopy
import json

import pytest

from latex_review.delivery import publication_html
from latex_review.translation import RULE_VERSION, TranslationConfig, _protected_sentence, validate_manifest
from latex_review.translation_session import TranslationSession


def manifest():
    first = _protected_sentence(r'\textbf{$x$ and \emph{$y$}}.', lambda raw: raw)
    second = _protected_sentence('Then $z$.', lambda raw: raw, token_start=len(first['protected']))
    return [{'id': 'new-paragraph', 'side': 'new', 'node_id': 'paragraph', 'kind': 'paragraph',
             'change_id': 'change', 'rule_version': RULE_VERSION,
             'sentences': [{'number': 1, 'changed': True, **first},
                           {'number': 3, 'changed': False, **second}]}]


MISSING = object()


@pytest.mark.parametrize('path,value', [
    ((0, 'sentences', 0, 'protected', 0, 'kind'), MISSING),
    ((0, 'sentences', 0, 'protected', 0, 'kind'), 'atom'),
    ((0, 'sentences', 0, 'protected', 0, 'source'), MISSING),
    ((0, 'sentences', 0, 'protected', 4, 'pair'), MISSING),
    ((0, 'sentences', 0, 'protected', 4, 'pair'), '[[LR_0]]'),
    ((0, 'sentences', 0, 'protected', 1, 'html'), []),
    ((0, 'sentences', 0, 'protected', 1, 'unexpected'), True),
    ((0, 'sentences', 0, 'text'), 'Missing protected content.'),
    ((0, 'sentences', 1, 'protected', 0, 'token'), '[[LR_1]]'),
    ((0, 'sentences', 1, 'number'), 1),
    ((0, 'sentences', 0, 'number'), True),
    ((0, 'sentences', 0, 'changed'), 'false'),
    ((0, 'sentences'), {}),
    ((0, 'sentences'), []),
    ((0, 'node_id'), 'different-node'),
    ((0, 'rule_version'), 'obsolete'),
    ((0, 'unexpected'), 'field'),
], ids=['missing-kind', 'unknown-kind', 'missing-source', 'missing-pair', 'wrong-pair',
        'invalid-html', 'unknown-placeholder-field', 'missing-token', 'reused-token',
        'duplicate-sentence', 'boolean-sentence', 'invalid-changed', 'invalid-sentences',
        'empty-sentences', 'wrong-identity', 'old-rules', 'unknown-unit-field'])
def test_invalid_manifest_blocks_session_and_warns_in_publication(tmp_path, path, value):
    units = manifest()
    target = units
    for key in path[:-1]:
        target = target[key]
    if value is MISSING:
        target.pop(path[-1])
    else:
        target[path[-1]] = value
    (tmp_path / 'translation-units.json').write_text(json.dumps(units))
    (tmp_path / 'report.html').write_text(
        '<body>Original content.<script type="application/json" id="translation-results">'
        '{"stale-translation": {}}</script></body>')
    values = {'base_url': 'https://example.invalid/v1', 'model': 'test'}
    with pytest.raises(ValueError, match='翻译清单.*重新生成报告'):
        TranslationSession(tmp_path, TranslationConfig.from_mapping(values), tmp_path / 'cache')
    html = publication_html(tmp_path, values)
    assert 'delivery-warning' in html and 'Original content.' in html
    assert 'stale-translation' not in html
    assert not (tmp_path / 'cache').exists()


def test_current_manifest_allows_blocked_units_and_preserves_input():
    units = manifest()
    blocked = deepcopy(units[0])
    blocked.update(id='new-blocked', node_id='blocked', sentences=[], error='原文包含保留的翻译占位符')
    units.append(blocked)
    original = deepcopy(units)
    assert validate_manifest(units) == original
    assert units == original
    assert validate_manifest([]) == []


def test_duplicate_units_and_crossed_format_pairs_are_rejected():
    units = manifest()
    with pytest.raises(ValueError, match='内容身份'):
        validate_manifest(units + deepcopy(units))
    sentence = units[0]['sentences'][0]
    sentence['protected'][-2:] = reversed(sentence['protected'][-2:])
    sentence['text'] = ''.join(fragment['token'] for fragment in sentence['protected'])
    with pytest.raises(ValueError, match='格式配对'):
        validate_manifest(units)
