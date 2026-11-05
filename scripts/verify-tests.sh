#!/bin/sh
# Full acceptance by default; the browser subset is for development feedback.
set -eu
case "${1:-full}" in
  full)
    shard_output="${TMPDIR:-/tmp}/latex-review-test-shards.$$"
    LATEX_REVIEW_SKIP_CLI_SMOKE=1 python3 scripts/run-test-shards.py --output "$shard_output"
    ;;
  browser)
    python3 -m pytest -q -ra \
      tests/test_browser_resources.py \
      tests/test_annotation_delivery_browser.py \
      tests/test_annotation_suggestions_browser.py \
      tests/test_publication_browser.py \
      tests/test_report_annotations_browser.py \
      tests/test_math_render_browser.py \
      tests/test_report_reading.py \
      tests/test_reader_tools.py \
      tests/test_report_sections.py \
      tests/test_reading_features.py \
      tests/test_reading_position.py \
      tests/test_reading_position_initial_frame.py
    ;;
  *)
    echo 'Usage: scripts/verify-tests.sh [full|browser]' >&2
    exit 2
    ;;
esac
latex-review --help >/dev/null
latex-review --version >/dev/null
latex-review-check --help >/dev/null
latex-review-check query --help >/dev/null
latex-review-check semantic export --help >/dev/null
