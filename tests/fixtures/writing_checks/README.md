# Synthetic writing-check acceptance manuscript

All text is synthetic and distributable. No private paper or production service is used.

- [Main entry](<paper/main.tex>): abstract, body, methods, results, appendix and bibliography.
- [Supplement](<paper/supplement.tex>): independent entry sharing a source dependency.
- [Repeated observation](<paper/shared/observation.tex>): included twice in the main entry; distinct include instances must survive mapping.
- [UTF-8/CRLF source](<paper/shared/crlf.tex>): exactly two CRLF line endings, café/中文/🧪; retain bytes, not text-mode normalized newlines.
- [External read-only profile](<read-only.toml>): register only against a temporary manuscript copy; real read-only permissions are not implied by the stored fixture.

Intentional issues: missing bibliography key `missing2026`, missing cross-reference `sec:missing`, unreferenced labeled figure/table, inconsistent name `adaptive estimation method`, implementation identifier `fast_estimator`, and a scope-sensitive reuse of `$s$`. The latter three are **not** baseline deterministic findings without registered author knowledge. Verbatim/commented missing references are negative controls. The fixture has a simple tagged equation and a complex align expression; neither demonstrates arbitrary TeX or mathematical correctness.

The reproducible capture harness [capture_reading.py](<../../../Docs/design/writing-check/evidence/capture_reading.py>) copies this tree outside the repository. For the old version it changes only `improves reproducibility` → `supports reproducibility` and removes ` and the unregistered source \\cite{missing2026}` from the introduction. The current/live copy is byte-identical to this fixture. Do not edit this scenario between Before and After; measurement metadata stores each file's SHA-256.

```sh
.venv/bin/python Docs/design/writing-check/evidence/capture_reading.py --phase before
# After implementing the workspace, with the same fixture/browser settings:
.venv/bin/python Docs/design/writing-check/evidence/capture_reading.py --phase after --validate-workspace
# Optional: after baseline screenshots, register author knowledge and validate fixed AI responses offline.
.venv/bin/python Docs/design/writing-check/evidence/capture_reading.py --phase after --validate-workspace --validate-offline-panels
```

The optional [offline-panel probe](<../../../Docs/design/writing-check/evidence/validate_offline_panels.py>) runs only after both baseline four-finding captures. It registers a forbidden-name policy in the temporary copy, verifies knowledge staleness and a new exact-source finding, exports a local semantic request, and imports one fixed valid plus one deliberately invalid candidate. It never invokes a provider and cannot establish model usefulness. The original manuscript bytes must remain unchanged. After measurements distinguish partial captures (`capture_complete: false`) from fully completed runs, identify the Git revision when production sources are clean independently of dirty docs/evidence, and record initial/end application-source hashes/mutation. Actual POST diagnostics retain only non-secret headers, scope, frozen report keys/content ID and the bound sidecar; they verify the real entry→worker proxy rather than substituting direct worker requests.

Before always imports frozen `fe82620af8bbbb028809d59ceeaaad7085427880` source via `git archive`, even if worktree code has changed. After imports worktree source. Each invocation uses an ephemeral loopback port, private state/config/cache roots and stops only its own daemon. Non-loopback browser traffic is blocked, so math is captured in the real offline-fallback state rather than pretending online MathJax succeeded. Baseline build/parse timings are descriptive microbenchmarks, not a pass against the 2 MiB/40-source/5,000-unit NFR target.
