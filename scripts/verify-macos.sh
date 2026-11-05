#!/bin/sh
set -eu
if [ "$(uname -s)" != "Darwin" ]; then
  echo '仅在 macOS 上运行此验证入口' >&2
  exit 1
fi
python3 -m pytest -q -ra
latex-review --help >/dev/null
latex-review --version >/dev/null
latex-review-check --help >/dev/null
latex-review-check query --help >/dev/null
latex-review-check semantic export --help >/dev/null
# Functional-only validation honors an explicit performance-work hold without
# changing the default benchmark or its acceptance criteria.
if [ "${LATEX_REVIEW_FUNCTIONAL_ONLY:-0}" = "1" ]; then
  echo 'Functional verification complete; performance benchmarks were not run.'
  exit 0
fi
# Synthetic core timing only; no provider, TeX compilation, or private paper.
performance_json=$(mktemp "${TMPDIR:-/tmp}/latex-review-check-performance.XXXXXX")
python3 Docs/design/writing-check/evidence/benchmark_checks.py --output "$performance_json"
python3 - "$performance_json" <<'PY'
import json
import sys
from pathlib import Path
result = json.loads(Path(sys.argv[1]).read_text())
assert result['cold_target_met'] and result['hot_target_met'], result
PY
