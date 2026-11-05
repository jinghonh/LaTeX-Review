"""Run baseline tests from a full frozen git archive, never mutable worktree code."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[4]
BASE = 'fe82620af8bbbb028809d59ceeaaad7085427880'
OUT = Path(__file__).resolve().parent / 'before'
OUT.mkdir(parents=True, exist_ok=True)
FROZEN = Path(tempfile.mkdtemp(prefix='writing-check-baseline-tests-')).resolve()
archive = subprocess.check_output(['git', 'archive', '--format=tar', BASE], cwd=ROOT)
with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
    bundle.extractall(FROZEN, filter='data')
env = {**os.environ, 'PYTHONPATH': str(FROZEN / 'src')}
command = [str(Path(sys.executable).absolute()), '-m', 'pytest', '-q']
start = time.perf_counter()
print(json.dumps({'revision': BASE, 'frozen_root': str(FROZEN), 'command': command}), flush=True)
with (OUT / 'baseline-pytest.log').open('w', encoding='utf-8') as log:
    result = subprocess.run(command, cwd=FROZEN, env=env, stdout=log, stderr=subprocess.STDOUT)
metadata = {'application_revision': BASE, 'frozen_root': str(FROZEN), 'command': command,
            'exit_code': result.returncode, 'wall_seconds': round(time.perf_counter() - start, 3),
            'log': str((OUT / 'baseline-pytest.log').relative_to(ROOT)),
            'note': 'Frozen archive retained for debugging; no worktree code imported.'}
(OUT / 'baseline-pytest.json').write_text(json.dumps(metadata, indent=2) + '\n')
print(json.dumps(metadata, indent=2), flush=True)
sys.exit(result.returncode)
