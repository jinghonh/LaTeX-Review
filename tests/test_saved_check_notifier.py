"""Deterministic save quiet-period overlap and post-publication notifier fences."""
from contextlib import contextmanager, nullcontext
from threading import Event
from types import SimpleNamespace

import pytest

import latex_review.live as live


@pytest.fixture
def clock_notifier(monkeypatch):
    clock = SimpleNamespace(now=10.0)
    timers, sent = [], []

    class FakeTimer:
        def __init__(self, delay, callback, args):
            self.delay, self.callback, self.args = delay, callback, args
            self.started = self.cancelled = False
            timers.append(self)

        def start(self):
            self.started = True

        def cancel(self):
            self.cancelled = True

        def fire(self):
            # Deliberately also fire cancelled callbacks to exercise generation fences.
            self.callback(*self.args)

    monkeypatch.setattr(live, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(live, 'Timer', FakeTimer)
    monkeypatch.setattr(live, '_check_saved_recipe', lambda recipe, config: sent.append((recipe, config)))
    stop = Event()
    notifier = live._SavedCheckNotifier({'paper_id': 'owned'}, stop)
    yield clock, timers, sent, stop, notifier
    notifier.close()


@pytest.mark.parametrize(('elapsed', 'debounce_ms', 'remaining'), [
    (.25, 800, .55), (1.2, 800, 0), (.25, 1350, 1.1), (0, 800, .8),
])
def test_rebuild_overlaps_configured_quiet_period(clock_notifier, elapsed, debounce_ms, remaining):
    clock, timers, sent, _, notifier = clock_notifier
    observed = clock.now
    clock.now += elapsed
    config = {'checks': {'debounce_ms': debounce_ms}}
    notifier.schedule(config, observed_at=observed)
    assert timers[-1].delay == pytest.approx(remaining)
    assert timers[-1].started and timers[-1].daemon
    assert sent == []
    timers[-1].fire()
    assert sent == [({'paper_id': 'owned'}, config)]


def test_startup_retains_full_default_delay(clock_notifier):
    clock, timers, sent, _, notifier = clock_notifier
    clock.now += 20
    notifier.schedule({})
    assert timers[-1].delay == .8
    assert sent == []


def test_repeated_saves_cancel_old_generation(clock_notifier):
    clock, timers, sent, _, notifier = clock_notifier
    notifier.schedule({}, observed_at=clock.now)
    old = timers[-1]
    clock.now += .3
    notifier.schedule({}, observed_at=clock.now)
    assert old.cancelled and timers[-1].delay == .8
    old.fire()
    assert sent == []
    timers[-1].fire()
    assert len(sent) == 1


def test_new_watcher_generation_suppresses_schedule_and_callback(clock_notifier):
    clock, timers, sent, _, notifier = clock_notifier
    watcher = SimpleNamespace(generation=1)
    current = lambda: watcher.generation == 1
    notifier.schedule({}, observed_at=clock.now, is_current=current)
    old = timers[-1]
    watcher.generation = 2
    old.fire()
    assert sent == []
    notifier.schedule({}, observed_at=clock.now, is_current=current)
    assert old.cancelled and len(timers) == 1


@pytest.mark.parametrize('action', ['cancel', 'close', 'stop', 'disabled', 'on_save_disabled'])
def test_cancellation_close_stop_and_disabled_fence_callbacks(clock_notifier, action):
    clock, timers, sent, stop, notifier = clock_notifier
    notifier.schedule({}, observed_at=clock.now - 2)
    old = timers[-1]
    if action == 'cancel':
        notifier.cancel_pending()
    elif action == 'close':
        notifier.close()
    elif action == 'stop':
        stop.set()
    else:
        notifier.schedule({'checks': {'enabled' if action == 'disabled' else 'on_save': False}},
                          observed_at=clock.now)
    old.fire()
    assert sent == []
    if action == 'close':
        notifier.schedule({}, observed_at=clock.now)
        assert len(timers) == 1


@pytest.mark.parametrize('new_save_during_build', [False, True])
def test_worker_notifies_only_after_clean_publication(clock_notifier, monkeypatch, tmp_path, new_save_during_build):
    clock, timers, sent, stop, _ = clock_notifier
    log, builds = [], []

    class FakeEvents:
        generation = 0
        warning = ''

        def __init__(self, *args, **kwargs):
            self.waits = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self, previous, timeout):
            self.waits += 1
            if self.waits == 1:
                self.generation = 1
                return 1
            if new_save_during_build and self.waits == 2:
                # Dirty publication was skipped; rebuild the latest event next.
                assert len(timers) == 1
                return self.generation
            timers[-1].fire()
            stop.set()
            return self.generation

    events = FakeEvents()

    @contextmanager
    def publication(output):
        log.append('building')
        yield SimpleNamespace(directory=output / 'stage')
        # Even after build() returns, no new timer may exist until promotion
        # completes (a zero-delay real Timer could otherwise notify too soon).
        assert len(timers) == (0 if len(builds) == 1 else 1)
        log.append('published')

    def build(source, directory, revision=1):
        builds.append(revision)
        assert not sent
        if revision > 1:
            assert len(timers) == 1 and timers[0].cancelled
            clock.now += 1.2
            if new_save_during_build and revision == 2:
                events.generation += 1
        return []

    def notify(recipe, config):
        assert log[-1] == 'published'
        assert events.generation == (2 if new_save_during_build else 1)
        assert timers[-1].delay == 0
        sent.append((recipe, config))

    monkeypatch.setattr('latex_review.file_events.ProjectEvents', lambda *args, **kwargs: events)
    monkeypatch.setattr('latex_review.output_leases.output_lease', lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr('latex_review.report_rebuild.rebuild_lock', lambda *args: nullcontext())
    monkeypatch.setattr('latex_review.report_directory.report_directory', publication)
    monkeypatch.setattr('latex_review.cli._config', lambda *args: {})
    monkeypatch.setattr('latex_review.project_profile.settings', lambda source: {})
    monkeypatch.setattr('latex_review.local_state.write_json', lambda *args: None)
    monkeypatch.setattr(live, '_write', lambda *args: None)
    monkeypatch.setattr(live, 'build', build)
    monkeypatch.setattr(live, '_check_saved_recipe', notify)
    monkeypatch.setattr(live, 'create_server', lambda *args, **kwargs: SimpleNamespace(
        server_port=12345, serve_forever=lambda **kwargs: None,
        server_close=lambda: None, shutdown=lambda: None))
    recipe = {'root': str(tmp_path), 'output': str(tmp_path / 'output'), 'cache': str(tmp_path / 'cache'),
              'config': str(tmp_path / 'config.toml'), 'config_explicit': False, 'entry': 'main.tex',
              'poll_files': False, 'nonce': 'test', 'ready': str(tmp_path / 'ready.json')}
    assert live.run_worker(recipe, stop) == 0
    assert builds == ([1, 2, 3] if new_save_during_build else [1, 2])
    assert len(sent) == 1 and len(timers) == 2
    assert timers[0].delay == .8 and timers[0].cancelled
