"""Project-shared annotations, versioned preview coordinates and conservative relocation.

All public values are JSON objects. Text offsets are UTF-16 code units, as in DOM
Range. Formula atoms count as one U+FFFC; MathJax descendants never supply offsets.

``content`` owns the annotation content manifest and anchoring; ``store`` owns the
annotation transaction. Modules prefixed with ``_`` are private to this package.
"""
from ._validation import (MANIFEST, MAX_BODY, SCHEMA_VERSION, AnnotationConflict, AnnotationError,
                          thread_metadata, utf16_length)
from .content import (PreparedContent, build_content, content_meta, load_content, locate, make_anchor,
                      validate_content)
from .store import AnnotationStore, execute_request, overlapping_threads, query_threads
# Transitional: callers outside the package still use these private helpers.
from ._validation import _atomic_write, _hash, _integer, _keys, _text
