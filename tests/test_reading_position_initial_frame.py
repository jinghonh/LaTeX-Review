"""Controlled initial-frame ordering; not a reconstruction of a hosted CI failure."""
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from test_reading_position import CAPTURE, SAVE, ERROR, long_preview
from test_report_reading import browser_page


# Hold callbacks, not native resize/scroll events. Release registers the original
# callbacks with native rAF in FIFO order; callbacks added during dispatch run in
# a later native frame. Cancellation preserves the browser's one-shot semantics.
HOLD_INITIAL_FRAMES = '''(() => {
  const raf = requestAnimationFrame.bind(window), cancel = cancelAnimationFrame.bind(window);
  const pending = new Map(); let next = 1, holding = true;
  const executed = [];
  function dispatch(id, item) {
    item.native = raf(time => {
      if (!pending.has(id)) return;
      pending.delete(id); executed.push(id); item.callback(time);
    });
  }
  window.requestAnimationFrame = callback => {
    const id = next++, item = {callback, native: null}; pending.set(id, item);
    if (!holding) dispatch(id, item);
    return id;
  };
  window.cancelAnimationFrame = id => {
    const item = pending.get(id); if (!item) return;
    pending.delete(id); if (item.native !== null) cancel(item.native);
  };
  window.initialFrameTest = {executed, released: [], release() {
    this.released = [...pending.keys()]; holding = false;
    for (const [id, item] of pending) if (item.native === null) dispatch(id, item);
  }};
})();'''

FIXED_INITIAL_FRAME = '''  requestAnimationFrame(() => {
    // A delayed first frame must not replace the pre-resize reading anchor.
    if (!restoring && signature() === dimensions) remember();
    else schedule();
  });'''
LEGACY_INITIAL_FRAME = '  requestAnimationFrame(remember);'


def live_fixture(tmp_path):
    return long_preview.__wrapped__(SimpleNamespace(param='live'), tmp_path)[1]


@pytest.mark.parametrize('legacy', [True, False], ids=['literal-before', 'guarded-after'])
def test_late_first_frame_keeps_saved_character(tmp_path, browser_page, legacy):
    path = live_fixture(tmp_path)
    if legacy:
        html = path.read_text()
        assert html.count(FIXED_INITIAL_FRAME) == 1
        # Restore only the exact pre-fix initial callback, not a simulated reader.
        path.write_text(html.replace(FIXED_INITIAL_FRAME, LEGACY_INITIAL_FRAME, 1))
    page, errors = browser_page, []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script(HOLD_INITIAL_FRAMES)
    page.goto(path.as_uri())
    page.wait_for_timeout(150)
    page.evaluate(CAPTURE, 'new')
    page.wait_for_timeout(100)
    assert page.evaluate(SAVE, 'new')
    page.set_viewport_size({'width': 1040, 'height': 780})
    page.wait_for_timeout(150)
    held_error = page.evaluate(ERROR, 'new')
    assert held_error > 3  # This fixture actually needs restoration.
    page.evaluate('window.initialFrameTest.release()')
    page.wait_for_timeout(150)
    error = page.evaluate(ERROR, 'new')  # Same Range, never recaptured.
    fifo = page.evaluate('window.initialFrameTest')
    assert fifo['executed'][:len(fifo['released'])] == fifo['released']
    if legacy:
        assert error > 3 and abs(error - held_error) < 1
    else:
        assert error < 3
    assert not page.evaluate('window.reviewReadingPosition.isResizing()')
    assert not errors


@pytest.mark.parametrize('focused', [False, True], ids=['native-burst', 'focused-control-burst'])
def test_resize_burst_retains_character_and_control_focus(tmp_path, browser_page, focused):
    page, errors = browser_page, []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(live_fixture(tmp_path).as_uri())
    page.wait_for_timeout(150)
    if focused:
        page.locator('#translation-mode').focus()
    page.evaluate(CAPTURE, 'new')
    page.wait_for_timeout(100)
    assert page.evaluate(SAVE, 'new')
    for width, height in ((1040, 780), (760, 900), (1280, 900)):
        page.set_viewport_size({'width': width, 'height': height})
    page.wait_for_timeout(150)
    assert page.evaluate(ERROR, 'new') < 3
    if focused:
        assert page.locator('#translation-mode').evaluate('node => node === document.activeElement')
    assert not errors


@pytest.mark.parametrize(('restoring', 'changed', 'expected'), [
    (False, False, {'remember': 1, 'schedule': 0}),
    (True, False, {'remember': 0, 'schedule': 1}),
    (False, True, {'remember': 0, 'schedule': 1}),
], ids=['unchanged-initial-frame', 'active-restoration', 'changed-dimensions'])
def test_actual_initial_callback_guard_branches(restoring, changed, expected):
    # Defensive guard branches use the actual callback bytes. This unit check is
    # separate from the real-browser FIFO bug proof above; no private state API.
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for the isolated callback branch check')
    script = (Path(__file__).parents[1] / 'src/latex_review/reading_position.js').read_text()
    registration = 'requestAnimationFrame(' + script.rsplit('  requestAnimationFrame(', 1)[1].rsplit('\n})();', 1)[0]
    harness = '''let callback;
const counts = {remember: 0, schedule: 0};
const remember = () => counts.remember++, schedule = () => counts.schedule++;
const requestAnimationFrame = value => { callback = value; };
const dimensions = 'original', signature = () => SIGNATURE;
const restoring = RESTORING;
REGISTRATION
callback(); console.log(JSON.stringify(counts));'''
    harness = harness.replace('SIGNATURE', json.dumps('changed' if changed else 'original'))
    harness = harness.replace('RESTORING', json.dumps(restoring)).replace('REGISTRATION', registration)
    result = subprocess.run([node, '-e', harness], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == expected
