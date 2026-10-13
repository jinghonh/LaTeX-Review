from pathlib import Path
import time

import pytest

from latex_review.file_events import ProjectEvents


def wait_changed(events, previous):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = events.wait(previous, 0.1)
        if current != previous:
            return current
    pytest.fail('project change was missed')


@pytest.mark.parametrize('polling', [False, True])
def test_events_track_atomic_dependencies_and_directory_recreation(tmp_path, polling):
    root = tmp_path / 'paper'
    root.mkdir()
    entry = root / 'main.tex'
    entry.write_text('before')
    with ProjectEvents(root, root / 'output', root / 'cache', force_polling=polling) as events:
        if not polling:
            assert not events.warning
        previous = events.generation
        for name in ('chapter.tex', 'refs.bib', 'image.png', 'figure.pdf', '.latex-review.toml'):
            path = root / name
            path.write_text('added')
            previous = wait_changed(events, previous)
            temporary = root / (name + '.tmp')
            temporary.write_text('atomic save')
            temporary.replace(path)
            previous = wait_changed(events, previous)
            path.rename(root / (name + '.renamed'))
            previous = wait_changed(events, previous)
            (root / (name + '.renamed')).unlink()
            previous = wait_changed(events, previous)
        folder = root / 'chapters'
        folder.mkdir()
        (folder / 'part.tex').write_text('included')
        previous = wait_changed(events, previous)
        (folder / 'part.tex').unlink()
        folder.rmdir()
        previous = wait_changed(events, previous)
        folder.mkdir()
        (folder / 'part.tex').write_text('recreated')
        wait_changed(events, previous)


@pytest.mark.parametrize('polling', [False, True])
def test_idle_never_reads_contents_and_outputs_do_not_trigger(tmp_path, monkeypatch, polling):
    (tmp_path / 'main.tex').write_text('text')
    output = tmp_path / 'output'
    output.mkdir()
    cache = tmp_path / 'cache'
    cache.mkdir()
    def forbidden(*args, **kwargs):
        pytest.fail('idle watcher read file contents')
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    monkeypatch.setattr(Path, 'read_text', forbidden)
    with ProjectEvents(tmp_path, output, cache, force_polling=polling, audit_interval=0.2) as events:
        previous = events.generation
        (output / 'report.html').write_text('generated')
        (cache / 'translation.json').write_text('generated')
        hidden = tmp_path / '.latex-review'
        hidden.mkdir()
        (hidden / 'annotations.json').write_text('generated')
        time.sleep(1.5)
        assert events.generation == previous


def test_events_preserve_saves_during_build_and_symlink_target(tmp_path):
    target = tmp_path / 'part.tex'
    target.write_text('first')
    (tmp_path / 'alias.tex').symlink_to(target)
    with ProjectEvents(tmp_path, tmp_path / 'output', tmp_path / 'cache') as events:
        previous = events.generation
        target.write_text('second')
        consumed = wait_changed(events, previous)
        # A build consumes this generation, then another save occurs during the build.
        target.write_text('third')
        pending = wait_changed(events, consumed)
        assert events.wait(consumed, 0) == pending
        (tmp_path / 'alias.tex').unlink()
        wait_changed(events, pending)


def test_native_failure_is_visible_and_fallback_detects_changes(tmp_path, monkeypatch):
    import watchfiles._rust_notify
    def broken(*args):
        raise OSError('injected unavailable event backend')
    monkeypatch.setattr(watchfiles._rust_notify, 'RustNotify', broken)
    with ProjectEvents(tmp_path, tmp_path / 'output', tmp_path / 'cache') as events:
        assert '降级' in events.warning
        assert 'injected' in events.warning
        previous = events.generation
        (tmp_path / 'main.tex').write_text('changed')
        wait_changed(events, previous)


def test_root_replacement_falls_back_without_losing_updates(tmp_path):
    root = tmp_path / 'paper'
    root.mkdir()
    (root / 'main.tex').write_text('old')
    with ProjectEvents(root, root / 'output', root / 'cache') as events:
        previous = events.generation
        root.rename(tmp_path / 'old-paper')
        root.mkdir()
        (root / 'main.tex').write_text('new')
        previous = wait_changed(events, previous)
        deadline = time.monotonic() + 4
        while not events.warning and time.monotonic() < deadline:
            time.sleep(0.1)
        assert '降级' in events.warning
        (root / 'main.tex').write_text('newer')
        wait_changed(events, previous)


def test_external_config_atomic_save_is_detected(tmp_path):
    root = tmp_path / 'paper'
    root.mkdir()
    config = tmp_path / 'settings.toml'
    config.write_text('first')
    with ProjectEvents(root, root / 'output', root / 'cache', extra_files=(config,)) as events:
        previous = events.generation
        temporary = tmp_path / 'settings.tmp'
        temporary.write_text('second')
        temporary.replace(config)
        wait_changed(events, previous)


def test_fallback_scan_failure_and_recovery_are_visible(tmp_path, monkeypatch):
    from threading import Event
    failed = Event()
    failed.set()
    original = ProjectEvents.snapshot
    def sometimes_broken(self):
        if failed.is_set():
            raise PermissionError('injected inaccessible project')
        return original(self)
    monkeypatch.setattr(ProjectEvents, 'snapshot', sometimes_broken)
    with ProjectEvents(tmp_path, tmp_path / 'output', tmp_path / 'cache', force_polling=True) as events:
        deadline = time.monotonic() + 3
        while '可能过期' not in events.warning and time.monotonic() < deadline:
            time.sleep(.05)
        assert '可能过期' in events.warning
        previous = events.generation
        (tmp_path / 'main.tex').write_text('restored content')
        failed.clear()
        wait_changed(events, previous)
        assert '降级' in events.warning
        assert '可能过期' not in events.warning
        assert events.thread.is_alive()


def test_metadata_audit_recovers_an_undelivered_event(tmp_path, monkeypatch):
    import watchfiles._rust_notify
    class SilentBackend:
        def __init__(self, *args):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def watch(self, debounce, step, timeout, stop):
            stop.wait(.1)
            return set()
    monkeypatch.setattr(watchfiles._rust_notify, 'RustNotify', SilentBackend)
    (tmp_path / 'main.tex').write_text('old')
    with ProjectEvents(tmp_path, tmp_path / 'output', tmp_path / 'cache', audit_interval=.2) as events:
        previous = events.generation
        (tmp_path / 'main.tex').write_text('new without event')
        wait_changed(events, previous)
        assert not events.warning


def test_root_only_native_notice_distinguishes_output_and_source(tmp_path, monkeypatch):
    import watchfiles._rust_notify
    output=tmp_path/'output';output.mkdir()
    cache=tmp_path/'cache';cache.mkdir()
    entry=tmp_path/'main.tex';entry.write_text('original')
    class RootNotice:
        def __init__(self,*args): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def watch(self,debounce,step,timeout,stop):
            return set() if stop.wait(.05) else {(2,str(tmp_path.resolve()))}
    monkeypatch.setattr(watchfiles._rust_notify,'RustNotify',RootNotice)
    with ProjectEvents(tmp_path,output,cache) as events:
        previous=events.generation
        (output/'report.html').write_text('generated')
        (cache/'translation.json').write_text('generated')
        time.sleep(.25)
        assert events.generation==previous
        entry.write_text('source changed')
        wait_changed(events,previous)
        assert not events.warning
