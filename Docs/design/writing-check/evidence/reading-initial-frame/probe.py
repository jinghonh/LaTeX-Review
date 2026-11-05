"""Capture ONE actual-app controlled late-first-frame scenario, never retry to green.

Run from an installed checkout, with its test/browser dependencies available:
  python probe.py --output NEW_DIRECTORY --phase before-or-after
Set LATEX_REVIEW_BROWSER only when a non-default Chromium executable is needed.
This loads the current renderer unchanged; capture Before from the old source,
After from the guarded source. It does not manufacture a previous UI revision.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / 'tests'))
import test_reading_position as target
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--phase', choices=('before', 'after'), required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    image, artifact = output / (args.phase + '.png'), output / (args.phase + '.json')
    if image.exists() or artifact.exists():
        parser.error('Refusing to overwrite an existing capture; use a fresh output directory')
    result = {
        'scope': 'ONE controlled original-FIFO initial-rAF delay; not historical CI reproduction',
        'phase': args.phase,
        'head': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
        'asset_sha256': {name: hashlib.sha256((ROOT / 'src/latex_review' / name).read_bytes()).hexdigest()
                         for name in ('reading_position.js', 'report.css', 'review_workspace.js', 'report_sections.js')},
        'observations': [], 'pageerrors': [], 'screenshot': image.name,
    }
    init = Path(__file__).with_name('fifo-delay.js').read_text()
    with tempfile.TemporaryDirectory(prefix='reading-initial-frame-') as temporary:
        _, path = target.long_preview.__wrapped__(SimpleNamespace(param='live'), Path(temporary).resolve())
        with sync_playwright() as api:
            browser = api.chromium.launch(executable_path=os.environ.get('LATEX_REVIEW_BROWSER'))
            result['browser_version'] = browser.version
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                page.route('**/*', lambda route: route.abort() if route.request.url.startswith('https://') else route.continue_())
                page.on('pageerror', lambda error: result['pageerrors'].append(str(error)))
                page.add_init_script(init)

                def sample(stage, error=None):
                    value = {'stage': stage, **page.evaluate('window.__initialRAFProbe.state()')}
                    if error is not None:
                        value.update(error_px=error, strict_pass=error < 3)
                    result['observations'].append(value)

                page.goto(path.as_uri())
                page.wait_for_timeout(150)
                sample('initial-original-150ms-callbacks-held')
                page.evaluate(target.CAPTURE, 'new')
                page.wait_for_timeout(100)
                result['saved_character'] = page.evaluate(target.SAVE, 'new')
                assert result['saved_character']
                sample('after-original-CAPTURE-100ms-SAVE')
                result['pending_before_resize'] = page.evaluate('window.__initialRAFProbe.pending()')
                page.set_viewport_size({'width': 1040, 'height': 780})
                page.wait_for_timeout(150)
                sample('after-native-resize-original-150ms-still-held', page.evaluate(target.ERROR, 'new'))
                result['pending_before_release'] = page.evaluate('window.__initialRAFProbe.pending()')
                page.evaluate('window.__initialRAFProbe.release()')
                page.wait_for_timeout(150)
                sample('original-150ms-after-original-FIFO-release', page.evaluate(target.ERROR, 'new'))
                result['failure_context_after_original_sample'] = page.evaluate(target.FAILURE_CONTEXT, 'new')
                page.screenshot(path=str(image), full_page=False)
                result['trace'] = page.evaluate('window.__initialRAFProbe.trace')
                result['completed'] = True
            finally:
                browser.close()
    artifact.write_text(json.dumps(result, indent=2))
    print(json.dumps({'artifact': artifact.name, 'observations': result['observations'],
                      'pageerrors': result['pageerrors']}, indent=2))


if __name__ == '__main__':
    main()
