# Actual saved-edit → current-report latency

## Workload classification

**All historical N=1 and complete N=10 results below, through the 6.904089-second p95 run, edit only an invisible TeX comment.** They exercise the full saved-check chain and freshness identities but do **not** establish the latency of visible prose edits. Comment and prose measurements must be reported separately. Pure exact-HTML reuse is expected to help comment-only saves more than changed rendered text.

## Reproduction

From the repository root, using the project's virtualenv:

```sh
.venv/bin/python Docs/design/writing-check/evidence/benchmark_saved_checks.py \
  --files 40 --paragraphs 5000 --saves 10 --settle-timeout 90 \
  --poll-seconds .5 \
  --output Docs/design/writing-check/evidence/linux-saved-performance-rerun.json \
  --concurrent-workload 'Describe other workloads running during this measurement.'
```

The [harness](<benchmark_saved_checks.py>) creates the same synthetic prose fixture as the [core benchmark](<benchmark_checks.py>): 40 source files and 5,000 paragraphs, roughly 1.48 MB. It runs the actual private local entry, `ensure`, managed live worker, native filesystem watcher, live preview rebuild and atomic publication, asynchronous saved-check notifier, and check coordinator. It never manually submits a check after saving. An explicit private configuration disables TeX compilation while leaving checks/on-save/debounce at their defaults. No model, external network service, private paper, or browser is used.

The additive `--edit-kind prose` mode keeps the same initial fixture but changes exactly one ordinary word in paragraph 0 (`synthetic` → `syntheticrevision1` → `syntheticrevision2`, etc.), preserving the file and paragraph counts. It also verifies the current published manifest's actual text: exactly one new paragraph prefix, no preceding prefix, and the expected paragraph count, plus current manifest/page/report agreement. This extra manifest read/JSON parse and proof is inside the measured interval and separately timed. The default `--edit-kind comment` preserves the historical append recipe exactly. Use distinct output files for both modes; never overwrite historical evidence.

The baseline must have an authoritative current report before the first edit. Each subsequent save appends a distinct comment to the same included source. The next save starts only after the previous save produces a different run ID and input ID, non-stale completed status, and an exported report whose input ID agrees with current status and whose content ID agrees with the newly published preview. The changed file's SHA-256 must also match the actual saved bytes.

Timing starts immediately before opening/writing the source and ends after verifying that authoritative report over the worker's real loopback HTTP route. Write close is included; this is not an `fsync` durability measurement. Startup/initial rendering and baseline settlement are recorded separately.

## Stage boundaries and limitations

- **800 ms remains the default saved-notifier quiet period**. The original diagnostic and first complete optimized run start that timer after publication. The latest notifier starts the deadline at the accepted monotonic watcher event and, only after successful promotion, waits any remaining quiet time. Rebuild work can overlap the configured deadline; notification never precedes publication and dirty watcher generations are rejected. Startup still waits the full configured delay. This is not observer cadence or browser refresh time.
- The original diagnostic observer sleeps 100 ms; optimized remeasurements sleep 500 ms between attempts. Actual cadence is that sleep **plus** HTTP and filesystem work. Per-request HTTP RTTs, counts, old-input/no-new-run observations, and observed states are retained.
- Publication and queued/running/completed timestamps are **first-observed upper bounds**, not instrumentation of internal CPU time or exact queue duration. Short-lived states may be missed. Derived intervals between observations must not be represented as isolated internal stage timings.
- Inclusive latency contains watcher detection, full live rebuild/publication, debounce, notifier loopback request, coordinator waiting and execution, current-status observation, and report-export verification. It does not measure browser rendering/paint.
- The saved-hot p95 goal is **≤2 seconds**, excluding TeX compilation and external network/model calls. Rendering and debounce are not excluded from the reported save latency. Passing the separate synchronous-core benchmark alone does not establish this goal.
- Ten sequential saves use nearest-rank p95, so p95 is the largest of those ten observed latencies. This is a small-sample local measurement, not a broad hardware guarantee.
- Source file hashes are recorded at both boundaries. They identify the observed workspace, not a claim of a final frozen revision. Host load, CPU quota/affinity, cgroup usage/throttling counters, and declared concurrent work are recorded.
- All entry state, caches, configuration, generated source and preview are private temporary resources. The harness stops and waits for its owned entry and checks that its worker exited; it never touches the resident user service. A bounded failure is preserved in the JSON instead of being called a pass.

## Measured result

The first actual saved edit on the reference fixture (**1,479,521 bytes, 40 files, 5,000 paragraphs**) took **77.208 seconds** to a verified current report. This single observed save strongly missed the ≤2-second saved-hot goal. At the parent's request, the run was interrupted gracefully after this sample rather than spending approximately ten more minutes on the same failure. **Only N=1 completed save is available; no reference p95 or completed 10-save benchmark is claimed.** The second save was interrupted, and the [raw evidence](<linux-saved-performance.json>) remains explicitly `complete: false`.

| First-observed boundary | Elapsed from saved write |
| --- | ---: |
| Matching preview publication | 73.808 s |
| New run, already running | 75.915 s |
| Current run completed | 77.060 s |
| Authoritative export verified | 77.208 s |

The dominant observed delay is **before preview publication**, not the observed running-to-completed interval (~1.146 s). Publication-to-first-observed-running (~2.106 s) includes the default 800 ms notifier debounce, notification/validation work, possible queue time, and observation delay; those components are not separately instrumented. There were 110 status observations, 107 no-new-run observations, 109 old-input observations, and no HTTP errors. The saved request used `changed` scope with honest `full` evaluation fallback (`full_scan_required_for_global_rules`).

Cold `ensure` to ready was 52.445 s, recorded separately; initial notifier baseline settled 3.059 s after worker readiness. The shared host exposed 2 logical/affinity CPUs. The parent reported the full regression and visual servers stopped; a small two-case suite was finishing near launch. Load average was approximately 1.08 at the saved sample's start and 1.48 at its end. The HTTP observer itself was nontrivial load (recorded RTTs frequently hundreds of milliseconds), so a separate no-observer build profile is being used to locate internal costs rather than attributing all 73.8 seconds to a single function.

Cleanup succeeded: owned entry exit code 0, owned worker exited, and isolated endpoint removed. The resident user service was not touched. Beginning/end source hashes are retained in the evidence; they do not assert a final revision.

## No-observer build profile

The [profile harness](<profile_saved_build.py>) profiles the actual full `live.build` plus atomic report publication at the same fixture size and with a saved comment appended. It does not run the HTTP observer, services, notifier, or coordinator. The [profile JSON](<linux-saved-build-profile.json>) and [raw cProfile data](<linux-saved-build.prof>) retain the result. Reproduce with:

```sh
.venv/bin/python Docs/design/writing-check/evidence/profile_saved_build.py \
  --output Docs/design/writing-check/evidence/linux-saved-build-profile.json \
  --profile Docs/design/writing-check/evidence/linux-saved-build.prof
```

The completed **instrumented** build took 131.349 s (124.710 s user CPU). `render_preview` accounted for 105.359 s cumulative; `_inline_html` was called 25,000 times and accounted for 97.889 s cumulative. `_parse_dom` ran 10,003 times (79.856 s cumulative), repeatedly initializing plasTeX and importing base macros. Translation-unit construction accounted for 18.190 s cumulative and annotation-content construction for 5.865 s. Cumulative times overlap and must not be summed. cProfile overhead is substantial, so these values are function-attribution evidence, **not** another saved-latency sample or an uninstrumented build benchmark.

This confirms a major renderer cost independent of observer load. It does not prove that the HTTP observer was cost-free: each worker status request also loaded and validated the full annotation manifest, and coordinator freshness re-expanded the source graph. Backend optimization and repeat measurements remain required; the ≤2-second goal is unchanged.

## Conservative backend optimizations

- Literal ASCII prose without inline anchors, TeX syntax, control/non-ASCII characters, or TeX typography shortcuts retains the exact legacy escaped HTML and whitespace padding without initializing plasTeX. All uncertain fragments keep the existing parser. Differential tests cover the legacy output, syntax exclusions, and parser non-entry on ordinary prose.
- Pure text scanning now jumps to sentence/TeX events instead of checking every ordinary character, skips comment removal when no `%` exists, and consumes ASCII word runs before the unchanged Unicode continuation logic. Seeded differential tests compare these paths with the original character-step algorithms on plain text, accents/combining marks, non-Latin digits, emoji, comments, escaping, macros, math and verbatim fragments.
- Only worker **GET status** caches a tiny validated manifest identity, keyed by SHA-256 of the actual manifest bytes read on every request. Same-size/same-mtime edits cannot reuse validation. A new byte sequence must pass full JSON/content validation. Mutation paths and their repeated publication reads are unchanged, as are coordinator source expansion, configuration/knowledge checks, containment, and missing-dependency discovery.

Repeated diagnostic builds use the same profiler and fixture. These are instrumented attribution results, not goal measurements:

| Diagnostic | Elapsed / user CPU | Preview cumulative | Translation cumulative | Annotation cumulative |
| --- | ---: | ---: | ---: | ---: |
| [Before optimization](<linux-saved-build-profile.json>) | 131.349 / 124.710 s | 105.359 s | 18.190 s | 5.865 s |
| [Literal path](<linux-saved-build-optimized1.json>) | 34.716 / 29.589 s | 7.705 s | 18.293 s | 6.811 s |
| [Plus scanner improvements](<linux-saved-build-optimized2.json>) | 13.602 / 13.195 s | 1.585 s | 4.710 s | 5.477 s |
| [Plus translation/enrichment reuse](<linux-saved-build-optimized3.json>) | 9.438 / 9.119 s | 1.904 s | 1.468 s | 4.177 s |

The first optimized profile overlapped small regression suites on this shared host. The second did not run a browser suite. Profiling overhead and variable shared load prevent interpreting the table as an exact wall-latency speedup. All profiles record no source-hash changes during their own build. The inclusive saved-chain remeasurement uses an observer sleep of **0.5 seconds plus measured HTTP work**, rather than the original 0.1 seconds; its first-observed boundaries remain upper bounds and are not corrected downward to manufacture a target pass.

## First optimized inclusive measurement — still fails the goal

[Complete N=10 evidence](<linux-saved-performance-optimized.json>) records the same 40-file / 5,000-paragraph / 1,479,521-byte initial fixture, full native watcher/render/notifier/coordinator route, and actual status plus export verification. This run includes the literal/scanner/status-cache changes but predates the next translation/enrichment/freshness optimizations.

| Result | Seconds |
| --- | ---: |
| Median saved edit → verified current | 10.211543 |
| p95, nearest rank, N=10 | **10.795668** |
| Minimum | 9.943788 |
| Maximum | 10.795668 |

The ten individual observations are 10.738933, 10.214263, 10.062640, 10.421905, 10.795668, 10.013118, 10.436188, 10.131691, 9.943788, and 10.208824 seconds. Every save has matching current/status/export input identities, a different run, and the expected actual-file SHA-256. All requested `changed` runs correctly fell back to full evaluation for global rules. All owned cleanup checks passed and no measured source path changed during this run. No pytest/browser/profile jobs ran concurrently; the machine still shared DSH and low-CPU resident user workers.

**Conclusion: substantial improvement over the original N=1 diagnostic, but the inclusive saved-hot ≤2-second p95 target remains unmet.** The original sample is not a baseline p95 and the profile table is not a replacement for this failed-target result.

## Subsequent narrow optimizations

- Protected translation text without commands, math, or active `~` spaces skips word-token construction but preserves exact whitespace folding, number placeholders, reserved-token rejection and HTML escaping. Seeded differential coverage forces the previous token walker as its oracle.
- Annotation source hashes are reused only within one enrichment call, from the immutable captured file strings, never from a process-global or metadata-based file cache. Permission/profile settings are frozen for that call and read again at the end; changed configuration aborts enrichment rather than publishing mixed permissions. Direct `_span` calls retain their dynamic settings default. Read-only and Git sources remain non-writable.
- The coordinator's final input verification calls `capture_content_id`, which shares the full capture implementation and exact identity formula, rather than parsing a second document that is discarded. Both include-graph expansions, actual-byte reads, include-instance checks, configuration reads, parser-version/title-policy binding and retries remain. Missing include/bibliography appearance, macro-driven graph changes, same-size/same-mtime edits, mid-verification writes, profile changes and root-containment diagnostics have direct regression coverage. Knowledge is still independently reread after evaluation.

The combined snapshot/coordinator/HTTP/page-publication/translation/annotation/literal suite passed **131 tests in 14.57 seconds**; two additional graph/containment cases brought the identity-only file to **8 passing tests**. These are correctness results, not latency evidence.

The latest profile also times actual generated-manifest validation **after disabling cProfile**, without HTTP or the notifier. Its manifest is 7,916,089 bytes and has 5,000 units. Five local full-read/validation calls take 0.139–0.177 s, whereas five unchanged-byte identity observations take 0.022–0.034 s. These local microdiagnostics explain the observation cache; they are not an end-to-end speed claim. Mutation route reads remain fully validated. Extending that cache to mutation routes would not directly improve the default saved notifier, which submits to the coordinator without using the worker POST route.

A first attempt at this latest inclusive run was deliberately interrupted during baseline settlement to check a newly reported CI correctness failure. [The interruption artifact](<linux-saved-performance-optimized2.json>) is `complete: false`, has **zero saved samples and no p95**, and records successful owned entry/worker/endpoint cleanup. The requested canonical raw-rules test passed on current Linux (**1 passed in 0.62 s**) before restarting the clean remeasurement; this does not claim that macOS CI passed.

## Latest complete inclusive remeasurement — still fails the goal

[Latest complete N=10 evidence](<linux-saved-performance-optimized3.json>) includes all narrow optimizations above and the accepted-watcher-event quiet-period deadline. The default remains 800 ms; the complete rebuild/publication is measured, and notifications still cannot run before successful publication. Initial fixture size, the actual private native-watcher/notifier/coordinator/HTTP chain, and the 500 ms observer sleep are unchanged.

| Result | Seconds |
| --- | ---: |
| Median saved edit → verified current | 6.721270 |
| p95, nearest rank, N=10 | **6.904089** |
| Minimum | 6.468223 |
| Maximum | 6.904089 |

Individual samples: 6.904089, 6.566726, 6.704336, 6.642616, 6.632992, 6.858653, 6.749564, 6.809377, 6.468223, and 6.738204 seconds. Every sample passed actual saved-byte SHA-256, status/export/current identity, non-stale current report, and new-run checks. Requested changed scope fell back correctly to full evaluation for global rules. Owned entry exited with code 0, the owned worker exited, and the isolated endpoint was removed. No measured source hash changed. No pytest/browser/profile jobs overlapped; DSH and low-CPU resident services still shared this two-CPU host.

First-observed publication occurs 4.740–5.562 s after saving. Publication-to-first-new-run observations span 0.469–1.285 s, new-run-to-completed-status approximately 0.570–0.575 s, and final export verification 0.138–0.205 s. These are **observer intervals**, with overlapping work and quantization, not isolated internal stage timings. Even the observed publication boundary is well above two seconds, so reducing coordinator execution or observer load alone does not establish the requested target.

**Final measured conclusion: p95 improved from the first complete optimized run's 10.795668 s to 6.904089 s, but the inclusive ≤2 s saved-hot goal is still unmet.** Neither the original 77.207815 s N=1 diagnostic nor profiler elapsed is used as a comparison p95. Further progress likely needs verified incremental preview/manifest work, not excluding rendering, weakening input freshness, reducing the configured quiet period, or substituting core-only timings.

## Immutable reuse added after that measurement

- A locked two-entry exact-HTML cache stores only frozen extraction facts: rendered text, immutable atom tuples, selectors, subunit facts, ordering, and node IDs. Rendered node membership/type is rechecked on every lookup because it affects owner resolution and formula identity. No parsed project/DOM, source metadata, source span, permission, mutable unit dictionary, page binding or manifest identity is retained. Every call freshly binds owner parent/section/labels/raw text and changes, enriches from the current capture with permission/configuration fences, rereads dependencies, recomputes identity, and validates the complete manifest. Oversized HTML/large unit sets bypass retention.
- Sentence segmentation now reuses only raw immutable source strings and tuples of frozen sentence offset/text values. Storage is limited to 8,192 entries and at most 4 KiB UTF-8 per key; large strings and string subclasses take the existing scanner path. Scanner differential tests explicitly call the uncached implementation, so memoization cannot hide a faulty fast path.
- A frozen pre-memoization annotation extraction oracle proves complete output equivalence on cold and hot paths, including Unicode, math/formula atoms, captions, table cells, lists, unsupported constructs and newlines. Additional cases cover changed saved bytes/read-only configuration with identical HTML; fresh parent/labels/section/raw/change metadata; rendered owner/type/HTML invalidation; project roots and macros; mutation isolation; concurrent hits; size/entry bounds; and mid-enrichment configuration changes.

The larger combined annotation/snapshot/coordinator/HTTP-page/translation/scanner/memo/benchmark-edit regression suite passed **189 tests in 16.89 s**. A disjoint saved-notifier/live/dependency/translation-publication selection passed **23 tests in 4.03 s**. The [tiny two-save prose smoke](<linux-saved-prose-smoke.json>) proves the new publication-text checks and cleanup only; its 2-file / 10-paragraph workload is **not** reference-fixture performance acceptance. The [first warm-cache profile](<linux-saved-build-optimized4-warm.json>) confirms zero HTML extraction/parser calls on the saved rebuild, but shared-host contention makes its elapsed time incomparable to the earlier cold profiles. Separate source-pinned quiet full-size comment and visible-prose remeasurements are still required.
