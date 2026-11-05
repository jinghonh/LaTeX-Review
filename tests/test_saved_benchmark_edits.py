"""The saved benchmark retains comments and independently proves visible edits."""
import importlib.util
import json
from pathlib import Path

import pytest


path = Path(__file__).resolve().parents[1] / 'Docs/design/writing-check/evidence/benchmark_saved_checks.py'
spec = importlib.util.spec_from_file_location('saved_benchmark', path)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_comment_recipe_exact_and_prose_changes_one_word_only(tmp_path):
    root = tmp_path / 'paper'
    root.mkdir()
    benchmark.generate(root, 2, 10)
    path = root / 'part0.tex'
    original = path.read_bytes()
    assert benchmark.write_saved_revision(path, 1, 'comment') is None
    assert path.read_bytes() == original + b'% saved revision 1\n'
    path.write_bytes(original)
    before = path.read_text().split()
    for index in range(1, 4):
        change = benchmark.write_saved_revision(path, index, 'prose')
        current = path.read_text().split()
        assert len(before) == len(current)
        assert sum(a != b for a, b in zip(before, current)) == 1
        assert path.read_text().count('\n\n') == original.decode().count('\n\n')
        assert change['after_text'] in path.read_text()
        assert change['before_text'] not in path.read_text()
        before = current
    assert sorted(p.name for p in root.iterdir()) == ['main.tex', 'part0.tex']


def test_prose_does_not_guess_missing_previous_revision(tmp_path):
    path = tmp_path / 'part0.tex'
    path.write_text('Unrelated text.')
    with pytest.raises(RuntimeError, match='exactly one'):
        benchmark.write_saved_revision(path, 1, 'prose')
    assert path.read_text() == 'Unrelated text.'


def test_visible_proof_requires_current_manifest_and_changed_text(tmp_path):
    change = {'before_text': 'Old sentence.', 'after_text': 'New sentence.', 'changed_ordinary_word': True}
    def publish(text, version='v2'):
        (tmp_path / 'annotation-content.json').write_text(json.dumps({'version_id': version, 'units': [
            {'kind': 'text', 'id': 'u', 'node_id': 'n', 'order': 0, 'text': text}]}))
    assert benchmark.visible_publication_proof(tmp_path, change, 'v2', 1) is None
    publish('New sentence.', 'v1')
    assert benchmark.visible_publication_proof(tmp_path, change, 'v2', 1) is None
    publish('Old sentence.')
    assert benchmark.visible_publication_proof(tmp_path, change, 'v2', 1) is None
    publish('Old sentence. New sentence.')
    assert benchmark.visible_publication_proof(tmp_path, change, 'v2', 1) is None
    publish('New sentence.')
    assert benchmark.visible_publication_proof(tmp_path, change, 'v2', 2) is None
    proof = benchmark.visible_publication_proof(tmp_path, change, 'v2', 1)
    assert proof['verified_visible_text_change']
    assert proof['unit_text_sha256'] == benchmark.digest(b'New sentence.')
    assert proof['manifest_version_id'] == 'v2' and proof['unit_id'] == 'u'
