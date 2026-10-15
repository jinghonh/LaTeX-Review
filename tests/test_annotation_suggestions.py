from copy import deepcopy
import hashlib
import pytest
from latex_review.annotations import AnnotationStore, AnnotationError
from test_annotations import content_at, select, create, mutate


def setup(tmp_path):
    content, _ = content_at(tmp_path / 'paper', 'Alpha beta gamma.')
    unit = next(u for u in content['units'] if 'Alpha' in u['text'])
    path = tmp_path / 'paper' / 'main.tex'
    raw = path.read_bytes(); text = raw.decode(); start = text.index('Alpha')
    unit['source_span'] = dict(file='main.tex', start=start, end=start+17, sha256=hashlib.sha256(raw).hexdigest(), original_chunk='Alpha beta gamma.', reliable=True, writable=True, reason=None)
    store = AnnotationStore.for_content(content)
    state = create(store, content, 'Alpha', suggested_replacement=dict(original_chunk='beta',replacement_chunk='better'))
    request = dict(thread_id=state['result']['thread_id'],message_id=state['result']['message_id'])
    return content, store, path, request


def test_preview_apply_and_no_automatic_resolve(tmp_path):
    content, store, path, request = setup(tmp_path)
    raw = path.read_bytes(); path.chmod(0o640)
    before = store.read(content)['revision']
    preview = mutate(store, content, 'suggestion_preview', **request)
    assert 'Alpha beta gamma.' in preview['result']['diff'] and 'Alpha better gamma.' in preview['result']['diff']
    assert path.read_bytes() == raw and store.read(content)['revision'] == before
    result = mutate(store, content, 'suggestion_apply', **request)
    assert 'Alpha better gamma.' in path.read_text()
    assert path.stat().st_mode & 0o777 == 0o640
    assert not result['threads'][0]['resolved']
    assert result['threads'][0]['messages'][0]['suggested_replacement']['status'] == 'applied'
    with pytest.raises(AnnotationError): mutate(store, content, 'suggestion_apply', **request)
    bundle = store.export(); assert bundle['threads'][request['thread_id']]['messages'][0]['suggested_replacement']['status'] == 'applied'


def test_source_changed_during_preparation_is_not_overwritten(tmp_path, monkeypatch):
    from latex_review.annotations import _suggestions as annotation_suggestions
    content, store, path, request = setup(tmp_path)
    original = path.read_bytes()
    real_inspect = annotation_suggestions.inspect
    def changed(root, suggestion):
        result = real_inspect(root, suggestion)
        path.write_bytes(original + b'\n% editor changed the source\n')
        return result
    monkeypatch.setattr(annotation_suggestions, 'inspect', changed)
    with pytest.raises(AnnotationError, match='拒绝覆盖'):
        mutate(store, content, 'suggestion_apply', **request)
    assert path.read_bytes() == original + b'\n% editor changed the source\n'
    assert store.read(content)['threads'][0]['messages'][0]['suggested_replacement']['status'] == 'pending'


def test_stale_and_record_failure_preserve_source(tmp_path, monkeypatch):
    content, store, path, request = setup(tmp_path)
    raw = path.read_bytes()
    import os
    persisted = store.path.read_bytes()
    def fail_persist(data):
        raise OSError('disk full')
    monkeypatch.setattr(store, '_persist', fail_persist)
    with pytest.raises(OSError, match='disk full'):
        mutate(store, content, 'suggestion_apply', **request)
    assert path.read_bytes() == raw
    assert store.path.read_bytes() == persisted
    reopened = AnnotationStore.for_content(content).read(content)
    assert reopened['threads'][0]['messages'][0]['suggested_replacement']['status'] == 'pending'
    path.write_bytes(raw+b'\n')
    with pytest.raises(AnnotationError, match='过期'): mutate(store, content, 'suggestion_preview', **request)


def test_unreliable_and_outside_node_refused(tmp_path):
    content, store, path, request = setup(tmp_path)
    with pytest.raises(AnnotationError, match='唯一'): create(store, content, 'Alpha', suggested_replacement=dict(original_chunk='documentclass',replacement_chunk='x'))
    content['version_id'] = 'f' * 64
    next(u for u in content['units'] if 'Alpha' in u['text'])['source_span'] = dict(reliable=False,writable=False,reason='跨来源')
    # Legacy messages remain valid; suggestions can be stored as explanations.
    result = create(store, content, 'Alpha', suggested_replacement=dict(original_chunk='Alpha',replacement_chunk='x'))
    assert next(t for t in result['threads'] if t['id'] == result['result']['thread_id'])['messages'][0]['suggested_replacement']['source_span'] is None


def test_formula_environment_quote_preserves_cli_support(tmp_path):
    from argparse import Namespace
    from latex_review.annotation_cli import find_selection, SelectionError
    content, _ = content_at(tmp_path / 'formula', r'\begin{equation}x+1=y\end{equation}')
    args = Namespace(quote=r'\begin{equation}x+1=y\end{equation}',formula=True,unit_id=None,occurrence=None)
    assert find_selection(content,args)['unit_id']
    args.quote='x+1=y'
    with pytest.raises(SelectionError): find_selection(content,args)


def test_crlf_preserved(tmp_path):
    content, store, path, request = setup(tmp_path)
    # Rebuild a real CRLF source manifest so offsets and full fingerprint agree.
    from latex_review.sources import ProjectSource
    from latex_review.contract import ComparisonSource
    from latex_review.structure import parse_project
    from latex_review.preview import render_preview
    from latex_review.annotations import build_content
    path.write_bytes(b'\\documentclass{article}\r\n\\begin{document}\r\nAlpha beta gamma.\r\n\\end{document}\r\n')
    project=parse_project(ProjectSource('new',path.parent,'main.tex',ComparisonSource('directory',str(path.parent))).expand())
    content=build_content(project,render_preview(None,project).html)
    state=create(store,content,'Alpha',suggested_replacement=dict(original_chunk='beta',replacement_chunk='better\nnext'))
    mutate(store,content,'suggestion_apply',thread_id=state['result']['thread_id'],message_id=state['result']['message_id'])
    raw=path.read_bytes()
    assert b'better\r\nnext' in raw and b'\n' not in raw.replace(b'\r\n',b'')


def test_suggestion_import_keeps_bound_source_metadata(tmp_path):
    content, store, path, request=setup(tmp_path)
    bundle=store.export()
    store.path.unlink()
    result=mutate(store,content,'import',bundle=bundle)
    suggestion=result['threads'][0]['messages'][0]['suggested_replacement']
    assert suggestion['source_span']['file']=='main.tex'
    preview=mutate(store,content,'suggestion_preview',**request)
    assert 'better' in preview['result']['diff']
    mutate(store,content,'suggestion_apply',**request)
    assert 'Alpha better gamma.' in path.read_text()


def test_sqlite_commit_failure_compensates_source(tmp_path, monkeypatch):
    import sqlite3
    from latex_review.annotations._storage import Database
    content, store, path, request = setup(tmp_path)
    before = path.read_bytes()
    revision = store.metadata(content)['revision']
    class FailingConnection(sqlite3.Connection):
        def commit(self):
            raise OSError('commit failed')
    def connect(database):
        return sqlite3.connect(database.path, factory=FailingConnection)
    with monkeypatch.context() as patch:
        patch.setattr(Database, 'connect', connect)
        with pytest.raises(OSError, match='commit failed'):
            store.dispatch(content, dict(operation='suggestion_apply', expected_revision=revision, **request))
        assert path.read_bytes() == before
    assert store.metadata(content)['revision'] == revision
    thread = store.read(content)['threads'][0]
    assert thread['messages'][0]['suggested_replacement']['status'] == 'pending'
