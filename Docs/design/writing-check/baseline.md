# Writing-check M0 baseline — Issue #55

## Scope and identity

Baseline application source: **`fe82620af8bbbb028809d59ceeaaad7085427880`** (`main`/HEAD confirmed before implementation). The [archived specification](<spec.md>) and [archived plan](<plan.md>) remain the authority for requested scope, not proof of implementation. M0 does not implement production UI or create a new parser/service/editing subsystem.

Investigation covers the actual [source expansion](<../../../src/latex_review/sources.py>), [source mapping](<../../../src/latex_review/source_map.py>), [structure parser](<../../../src/latex_review/structure.py>), [rules](<../../../src/latex_review/rules.py>), [diff report](<../../../src/latex_review/report.py>), [live report](<../../../src/latex_review/live.py>), [shared reading assembly](<../../../src/latex_review/reading.py>), [report directory publishing](<../../../src/latex_review/report_directory.py>), [CLI/configuration](<../../../src/latex_review/cli.py>), and existing [local browser tests](<../../../tests/test_local_browser.py>) / [file-event browser tests](<../../../tests/test_file_events_browser.py>). These relative source links resolve to the working tree; all claims in this document are explicitly about the frozen baseline unless stated otherwise.

Read guidance: [AGENTS](<../../../AGENTS.md>), [CONTEXT](<../../../CONTEXT.md>), [domain guidance](<../../agents/domain.md>), [issue tracking guidance](<../../agents/issue-tracker.md>), [README](<../../../README.md>), [paper rules](<../../paper-rules.md>), [live preview](<../../live-preview.md>), [multiple entries/read-only projects](<../../multi-entry-projects.md>), [primary-change ADR](<../../adr/0001-count-primary-changes.md>), and [resident-entry ADR including proxy/history revisions](<../../adr/0002-resident-local-entry.md>). Repository paths use uppercase `Docs/` despite older guidance links using lowercase.

## Environment and reproducible commands

Reference host: Linux `6.8.0-138-generic`, x86_64, glibc 2.39; **2 logical/2 physical CPUs, 3,904,782,336 bytes RAM**. Python **3.11.17**; plasTeX **3.1**; jsonschema **4.26.0**; watchfiles **1.3.0**; pytest **9.2.0**; Playwright **1.64.1**; actual captured Chromium **156.0.8078.4**. Exact package versions and fixture hashes are in [measurements](<evidence/before/measurements.json>).

TeX is installed: latexmk **4.83**, pdfTeX **1.40.25**, TeX Live 2023/Debian; xelatex/lualatex are also present. **No TeX compilation was requested or used in these captures.** MathJax/remote network are blocked deliberately. The screenshots therefore record genuine offline fallback behavior, not successful online math rendering. No LLM request or private manuscript was used.

System Python initially encountered PEP 668 / missing ensurepip; the parent created the local environment using `uv venv --allow-existing .venv` and installed declared dependencies with `uv pip`. No extra runtime framework is required.

```sh
# Genuine BEFORE, always frozen fe82620 source, irrespective of concurrent edits:
.venv/bin/python Docs/design/writing-check/evidence/capture_reading.py --phase before
# Same fixture/scenario/browser settings against implementation, when ready:
.venv/bin/python Docs/design/writing-check/evidence/capture_reading.py --phase after
# Targeted fixture invariants:
.venv/bin/python -m pytest -q tests/test_writing_fixture_baseline.py
# Whole baseline test suite from a frozen full-repository archive:
.venv/bin/python Docs/design/writing-check/evidence/run_frozen_tests.py
```

[Capture harness](<evidence/capture_reading.py>): exports baseline source with `git archive`, imports that source explicitly, makes temporary old/current manuscript copies, reserves a loopback port, sets private absolute state/config/cache directories, starts only owned local services, and stops only its own daemon. It uses actual live `prepare_request` / local-client service and real diff CLI output, **not** the fake minimal HTML report used by a browser unit-test helper. The old version changes `improves reproducibility` to `supports reproducibility` and removes the missing citation clause; current/live bytes match the stored fixture. Browser settings: **1440 × 1000, scale 1, light, en-US, UTC**, fresh contexts, top of default reading page, no external requests. Both screenshots show the stable same-origin report iframe/proxy, not a mockup or HTML source listing.

### Test results

- [Fixture invariants](<../../../tests/test_writing_fixture_baseline.py>): **3 passed in 0.87s**. Tests exercise exact Unicode/CRLF source slicing, two distinct repeated include origins, the four baseline rule codes, comment/verbatim negative controls, and external profile validation. This targeted run used the worktree parser/rules; the capture independently used frozen baseline source and asserted the distinct include-instance invariant.
- The initial parent-owned full test run collected **881 tests**, but implementation edits began while it was running. It was stopped; **do not classify that mixed run as a completed baseline**.
- A separate full frozen baseline run using the [frozen test runner](<evidence/run_frozen_tests.py>) completed: **860 passed, 19 skipped, 2 failed in 758.98 s** (runner wall 760.613 s, exit 1). See [raw log](<evidence/before/baseline-pytest.log>) and [run identity](<evidence/before/baseline-pytest.json>). Both baseline failures are the SIGINT/SIGTERM variants of `test_preview_exits_promptly_with_translation_in_flight`: [the baseline translation test:906](<../../../tests/test_translation.py#L906>) calls `create_server` on a just-stopped worker port and receives `OSError: [Errno 98] Address already in use`. This occurs in frozen fe82620, not new writing-check code. A [targeted frozen rerun](<evidence/before/baseline-pytest-rerun.log>) yielded **1 failed, 1 passed, 57 deselected in 5.16 s**: SIGINT again failed with the same port bind error, SIGTERM passed. Thus a baseline shutdown/port-release timing failure is reproducible, although individual variants can vary; no green full baseline is claimed.

## BEFORE screenshots

| Mode | Evidence | Actual baseline observation |
| --- | --- | --- |
| Live reading | [Live BEFORE](<evidence/before/live-reading.png>) | Chapter directory, native reading tools/translation/annotation controls; preview diagnostics; no writing-check workspace. |
| Diff reading | [Diff BEFORE](<evidence/before/diff-reading.png>) | Chapter directory, primary-change summary, default new-version reading mode, existing change tools; no writing-check workspace. |

Both pages had **zero uncaught browser `pageerror` events** in this scenario. Blocking remote requests is intentional; it is not evidence of fully typeset equations or configured translation.

## Synthetic manuscript and supported baseline surface

The [fixture guide](<../../../tests/fixtures/writing_checks/README.md>) documents all cases. The [main entry](<../../../tests/fixtures/writing_checks/paper/main.tex>) has abstract/body/appendix, simple and complex mathematics, labeled figure/table, bibliography, UTF-8/astral text and two CRLF lines. A [supplement entry](<../../../tests/fixtures/writing_checks/paper/supplement.tex>) shares a dependency. An [external read-only profile](<../../../tests/fixtures/writing_checks/read-only.toml>) declares both entries; profile validation is not a full read-only runtime acceptance test.

Fixture scale: **9 files, 3,115 bytes total**, including the independent supplement and bibliography; current main parses into **34 nodes**. It is deliberately small and is not a substitute for the specified 2 MiB / 40 files / 5,000 reading-unit workload.

| Surface | Inspected / observed support | Boundary |
| --- | --- | --- |
| Source graph | Recursive `input`/`include`; dependency/resource tracking; repeated source instances; supported include-macro configuration | Dynamic/unknown dependencies, outside-root paths, missing/unreadable sources require explicit diagnostics. |
| Source coordinates | Immutable decoded UTF-8 content, Unicode character `[start,end)` spans, multiple origins; CRLF retained by byte decode | Browser display remains UTF-16; normalized/plain/MathJax text offsets are not raw-source offsets. |
| Repeated include | Shared observation has two distinct child `SourceSegment.include_instance` values, with identical raw source range | Dependency records describe the including file instance (both are `new:1` here); **do not mistake that field for the included child's occurrence identity**. Actual child segments are retained in measurements. |
| Basic reference rules | `ref`, `eqref`, `autoref`, `pageref`; comments/verbatim masked; duplicate labels; unreferenced figures/tables | Do not infer cleveref/glossary/unknown macro semantics from successful rendering. Ordinary `ref{a,b}` is not a generic multi-key reference. |
| Bibliography | Read `.bib` entries / explicit bibitems; reliability gate suppresses unsupported/unreadable bibliography missing-key claims | Citation existence does not establish whether a source supports the scientific claim. |
| Equations | Existing simple numbering evidence and dual-version number comparison | Single-version checks must not synthesize old/new comparison; complex math is not mathematical proof/type inference. |
| Names/concepts/symbols | No baseline author-knowledge consistency engine | Inconsistent name, code identifier, acronym and reused `$s$` in this fixture are scenarios, not baseline confirmed errors. |
| Read-only/multi-entry | Existing external-profile/root+entry identity design | Full checks-specific runtime acceptance is future work, not passed by a TOML fixture. |

### Observed baseline degradation, not new regressions

The main parsed input has `unknown_latex` at the figure's `fbox` placeholder and the verbatim block, `low_confidence_source` for a cross-origin structural unit, and informational `tex_distribution_style` for `plain`. The live screenshot reports seven preview diagnostics after rendering. Evidence must retain all origin pieces and explain uncertain display navigation rather than inventing one contiguous file span.

The old/current diff reports **5 primary changes**: 1 modification plus 2 additions / 2 deletions associated with the unchanged repeated-observation area, with +49/−45 word summary. This is the **actual baseline matcher behavior**, not an expected one-change assertion; keep the baseline and compare rather than hiding it. Four current-vs-self rule diagnostics occur independently: `bibliography_key_unresolved`, `unresolved_reference`, `figure_unreferenced`, `table_unreferenced`. In old/current mode the missing bibliography key is `new`, the remaining three are `existing`. These diagnostics do not contribute to primary-change totals.

All three baseline CLI builds exit **2** because of non-rule preview/source warnings. Existing CLI logic excludes `rule_status` diagnostics from that degraded exit-code gate. Preserve this protocol; the new check CLI has its own specification and must not retrofit it into the old entry point.

## Performance baseline

[Raw measurements and each command](<evidence/before/measurements.json>) are retained. Timings are wall clock on a shared host and can include contention; no CPU isolation is claimed.

| Operation | Samples | Median |
| --- | --- | --- |
| Expand + parse main entry, same process | 5 | **0.108765 s** |
| Full diff CLI, `--no-cache`, no TeX/network | 3 | **0.960460 s** |

The first measurement includes source expansion and parse, not a writing-check engine. The second includes two sides, comparison and HTML/JSON assembly. **Cold checks / hot-save p95 / 500-finding panel latency are not implemented/measured by this baseline.** No claim against the specification's ≤10 s cold / ≤2 s hot p95 target is justified by this tiny fixture. The first implementation should prefer a full deterministic recheck with incremental presentation before dependency-scan optimization.

## Integration and issue coordination decisions

1. **Reuse the real pipeline.** Adapt `ProjectSource.expand` → `ExpandedProject` / `SourceMap` → `ParsedProject`; no replacement parser or duplicate document tree. Snapshots must hash raw dependency bytes/configuration, including untracked dependencies, and reject mixed reads.
2. **One single-side scanner.** Baseline `_findings(project)` is already the private single-side core; expose/reuse it and keep `check_rules(old,new)` as the compatibility adapter. Preserve bibliography reliability, source evidence and numbering-only dual-side semantics.
3. **Separate results from primary changes.** Add checks schema 1.0 separately from diff schema 1.1. Keep input/finding identity, review state, lifecycle and delta status independent; first-run findings are not_comparable, not automatically new. Grouped issue and occurrence counts are different metrics.
4. **Shared reading integration.** Baseline `build_reading` is the genuine shared live/diff hook for one workspace implementation. Live assembles its own HTML, uses private report helpers and `scripts_after_math`; placing all initialization after remote MathJax could break the required offline workflow.
5. **Two service slots, root+entry identity.** Canonical checks coordination belongs in existing local-entry ownership, worker APIs bridge to it; no third daemon/slot. Preserve Host/Origin/token checks, stable same-origin proxy, and sandboxed read-only history. Rendering a disabled button does not replace backend permission checks.
6. **Configuration changes are real backend work.** Baseline `cli._config` rejects unknown top-level settings; new checks/knowledge/semantic settings must update explicit validators and external profiles, not only front-end settings.
7. **#50 is OPEN.** Queried via `gh issue view 50 --json ...`. [Issue #50](https://github.com/jinghonh/LaTeX-Review/issues/50) asks to consolidate report directory construction. At fe82620, the actual module is only a 50-line candidate-directory/atomic-publication context manager, **not an already-merged shared builder**. Use the existing atomic publish boundary without pretending this refactor exists. Its older instruction that live does not check rules must be explicitly superseded for this feature.
8. **#53 is OPEN.** [Issue #53](https://github.com/jinghonh/LaTeX-Review/issues/53) is a broader multi-place annotation/suggestion transaction specification, not a delivered patch API. Writing checks should associate existing annotations, not implement a competing editor or silently apply source changes. Precise cross-file patch application remains outside P0/P1.

## Acceptance gaps and honest completion boundary

- **macOS:** unavailable in this environment; first-priority macOS acceptance **not run**. Linux synthetic browser evidence does not imply macOS success.
- **Author's real paper:** none provided or authorized; real-paper M3/M5 acceptance **incomplete**, not replaced by synthetic text. No private project was opened.
- **Real LLM/quality assessment:** no provider call or human-reviewed semantic evaluation was made. Later mock/schema tests are not real-provider acceptance.
- **Scale and interaction:** the [Linux synthetic core reference](<evidence/linux-performance.json>) now passes its recorded targets:1,479,521 bytes/40files/5,000units, cold0.765306s,10-hot p95=1.315557s. This excludes save debounce, queueing/publication, compilation/network and500-findings browser response, so end-to-end NFR and full knowledge lifecycle remain separate acceptance. Actual keyboard/narrow-screen workspace and a synthetic registration→stale→recheck loop have the integrated AFTER evidence below.
- **Read-only/history/export:** existing design was inspected; new checks permissions, frozen matching-version payload and no-token export require implementation tests before claiming completion.

The [acceptance/progress matrix](<acceptance.md>) tracks every T0–T5 task and AC-01–24 separately, so a completed baseline cannot be confused with full M0–M5 delivery.

## Integrated AFTER verification (separate from frozen baseline)

**Refresh pending:** the subsequent actual `33135ce13b8fc4776320f0525c7477ece1375cef` capture exposed a diff chapter-sidebar overlay blocking the first workspace tabs ([screenshot](<evidence/after/diff-navigation-failure.png>), [diagnostic](<evidence/after/diff-navigation-failure.json>)). Its partial/mixed After artifacts are not final delivery evidence. The complete `4408564` observations below are historical until the corrected immutable comparison finishes; frozen BEFORE remains unchanged.

The [After measurements](<evidence/after/measurements.json>) and [acceptance ledger](<acceptance.md>) contain the final actual browser/service results for production revision `44085641b20d99b86d56fcd0a9f6a0ccfb5a660d`: **capture complete, application-source mutation false, all114 tracked source files covered by identical initial/end SHA maps**. Real live/diff POST content-ID headers match the embedded frozen checks input and bound sidecar. The frozen BEFORE artifacts above are never replaced by current application output. Compare [BEFORE diff reading](<evidence/before/diff-reading.png>) with [AFTER diff reading](<evidence/after/diff-reading.png>): the workspace adds five shared panels while retaining the original five primary changes and +49/−45 word counts.

Both actual live/diff current-check runs completed with the four known lexical findings and precise source drawers. Registered author knowledge produced a fifth forbidden-name finding after invalidation/recheck. Offline semantic request export and fixed-response import preserved one valid candidate, rejected an invented evidence ID, and displayed partial status plus candidate-only evidence. No model was called and manuscript bytes were unchanged. See the [semantic evidence view](<evidence/after/diff-semantic-evidence.png>) for explicit origin, assessment, review state, rationale and missing context.

Real integration probes found and helped isolate rendering warnings suppressing unrelated lexical rules, detached-trigger focus restoration, a live/diff configuration identity conflict caused by unrelated CLI compile/diff defaults, and a dropped checks-content-ID header in the real entry→worker proxy after security hardening. The final complete loops pass after those corrections. Historical failure artifacts are diagnostic history, not final failing tests. Whole-delivery acceptance still requires the per-row tests and resource-gap treatment in the ledger; small synthetic timing samples and fixed AI responses cannot establish NFR-scale performance or semantic usefulness.
