#!/bin/sh
set -eu
if [ "$(uname -s)" != "Darwin" ]; then
  echo '仅在 macOS 上运行此验证入口' >&2
  exit 1
fi
python3 -m pytest -q
latex-review --help >/dev/null
latex-review --version >/dev/null
