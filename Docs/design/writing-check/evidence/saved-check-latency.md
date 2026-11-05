# Actual saved-edit → current-report latency

## Next fixed application b742583 — original-boundary measurements pending

[Fixed-source instructions](<../checkpoint-b742583.md>): application `b742583254a69dd7103be07b4e5f110f3c4c2c5e`, 120-file app/helper aggregate `818b02326ecffdf402ac0387541de6ad921e1a051daad0f0e9a9d26ca3994a8e`. Two local producer-path optimizations remove unused translation imports and equivalent whitespace-regex work without skipping rendering or changing output/source/authority semantics. Combined relevant validation: **440 passed / 2 report-only skipped / 92.08s**, not a full-suite result. New comment/prose N10 and full CI are pending; no performance-pass claim. Micro CPU and prior-source samples below do not substitute for this application's full saved-edit boundary. The prior physical 466 source still strictly fails at 2.026850125s / 2.019279333s; no rounding or selective rerun. The old hosted standalone restoration failure remains unexplained and now retains failure-only geometry diagnostics, without altered waits/Range/<3px.

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

That profile also times actual generated-manifest validation **after disabling cProfile**, without HTTP or the notifier. Its manifest is 7,916,089 bytes and has 5,000 units. Five local full-read/validation calls take 0.139–0.177 s, whereas five unchanged-byte identity observations take 0.022–0.034 s. These local microdiagnostics explain the observation cache; they are not an end-to-end speed claim. Mutation route reads remain fully validated. Extending that cache to mutation routes would not directly improve the default saved notifier, which submits to the coordinator without using the worker POST route.

A first attempt at that inclusive run was deliberately interrupted during baseline settlement to check a newly reported CI correctness failure. [The interruption artifact](<linux-saved-performance-optimized2.json>) is `complete: false`, has **zero saved samples and no p95**, and records successful owned entry/worker/endpoint cleanup. The requested canonical raw-rules test passed on current Linux (**1 passed in 0.62 s**) before restarting the clean remeasurement; this does not claim that macOS CI passed.

## Pre-memoization complete inclusive remeasurement — still fails the goal

[Pre-memoization complete N=10 evidence](<linux-saved-performance-optimized3.json>) includes all narrow optimizations above and the accepted-watcher-event quiet-period deadline. The default remains 800 ms; the complete rebuild/publication is measured, and notifications still cannot run before successful publication. Initial fixture size, the actual private native-watcher/notifier/coordinator/HTTP chain, and the 500 ms observer sleep are unchanged.

| Result | Seconds |
| --- | ---: |
| Median saved edit → verified current | 6.721270 |
| p95, nearest rank, N=10 | **6.904089** |
| Minimum | 6.468223 |
| Maximum | 6.904089 |

Individual samples: 6.904089, 6.566726, 6.704336, 6.642616, 6.632992, 6.858653, 6.749564, 6.809377, 6.468223, and 6.738204 seconds. Every sample passed actual saved-byte SHA-256, status/export/current identity, non-stale current report, and new-run checks. Requested changed scope fell back correctly to full evaluation for global rules. Owned entry exited with code 0, the owned worker exited, and the isolated endpoint was removed. No measured source hash changed. No pytest/browser/profile jobs overlapped; DSH and low-CPU resident services still shared this two-CPU host.

First-observed publication occurs 4.740–5.562 s after saving. Publication-to-first-new-run observations span 0.469–1.285 s, new-run-to-completed-status approximately 0.570–0.575 s, and final export verification 0.138–0.205 s. These are **observer intervals**, with overlapping work and quantization, not isolated internal stage timings. Even the observed publication boundary is well above two seconds, so reducing coordinator execution or observer load alone does not establish the requested target.

**Conclusion at that checkpoint: comment-only p95 improved from the first complete optimized run's 10.795668 s to 6.904089 s, but the inclusive ≤2 s saved-hot goal was still unmet.** Neither the original 77.207815 s N=1 diagnostic nor profiler elapsed is used as a comparison p95. Further progress likely needs verified incremental preview/manifest work, not excluding rendering, weakening input freshness, reducing the configured quiet period, or substituting core-only timings.

## Immutable reuse added after that measurement

- A locked two-entry exact-HTML cache stores only frozen extraction facts: rendered text, immutable atom tuples, selectors, subunit facts, ordering, and node IDs. Rendered node membership/type is rechecked on every lookup because it affects owner resolution and formula identity. No parsed project/DOM, source metadata, source span, permission, mutable unit dictionary, page binding or manifest identity is retained. Every call freshly binds owner parent/section/labels/raw text and changes, enriches from the current capture with permission/configuration fences, rereads dependencies, recomputes identity, and validates the complete manifest. Oversized HTML/large unit sets bypass retention.
- Sentence segmentation now reuses only raw immutable source strings and tuples of frozen sentence offset/text values. Storage is limited to 8,192 entries and at most 4 KiB UTF-8 per key; large strings and string subclasses take the existing scanner path. Scanner differential tests explicitly call the uncached implementation, so memoization cannot hide a faulty fast path.
- A frozen pre-memoization annotation extraction oracle proves complete output equivalence on cold and hot paths, including Unicode, math/formula atoms, captions, table cells, lists, unsupported constructs and newlines. Additional cases cover changed saved bytes/read-only configuration with identical HTML; fresh parent/labels/section/raw/change metadata; rendered owner/type/HTML invalidation; project roots and macros; mutation isolation; concurrent hits; size/entry bounds; and mid-enrichment configuration changes.

The larger combined annotation/snapshot/coordinator/HTTP-page/translation/scanner/memo/benchmark-edit regression suite passed **189 tests in 16.89 s**. A disjoint saved-notifier/live/dependency/translation-publication selection passed **23 tests in 4.03 s**. The [tiny two-save prose smoke](<linux-saved-prose-smoke.json>) proves the new publication-text checks and cleanup only; its 2-file / 10-paragraph workload is **not** reference-fixture performance acceptance. The [first warm-cache profile](<linux-saved-build-optimized4-warm.json>) confirms zero HTML extraction/parser calls on the saved rebuild, but shared-host contention makes its elapsed time incomparable to the earlier cold profiles. The separate source-pinned quiet full-size comment and visible-prose remeasurements are recorded below.

## Pre-fragment pinned paired N=10 remeasurement — both workloads still fail the goal

Both sequential runs used committed source **`87f740489ae31a8b998f240ff8308615be0c306d`**, verified against actual Git HEAD and an unchanged package/helper working tree before/after measurement. Both have the identical 118-file package-plus-harness source aggregate SHA-256 **`32dfe568ac8ed4db7a826fd6809d6f258a477e984267d98d7711138054fcfc51`**. The new hash guard includes package resources/CSS as well as Python/JavaScript; runtime bytecode is excluded. Initial fixture: 40 files, 5,000 paragraphs, 1,479,521 bytes. The configured quiet deadline is still 800 ms and both use observer sleep **0.5 s plus actual HTTP work**. No other local pytest/browser/profile/capture job overlapped either run; this remains a shared two-CPU host, not an exclusive hardware claim.

| Inclusive saved edit → verified current | [Comment-only](<linux-saved-performance-optimized4-comment.json>) | [Visible prose](<linux-saved-performance-optimized4-prose.json>) |
| --- | ---: | ---: |
| Completed authoritative samples | 10 | 10 |
| Median, seconds | 4.756490 | 5.710073 |
| p95 nearest rank, seconds | **6.692495** | **7.626229** |
| Minimum, seconds | 4.253798 | 5.281424 |
| Maximum, seconds | 6.692495 | 7.626229 |
| Inclusive ≤2 s p95 target | **FAIL** | **FAIL** |

The first sample is the maximum in each run and is **retained**, not dropped as an outlier or relabeled startup. An authoritative settled baseline precedes every saved-edit sequence. Comment samples (seconds): 6.692495, 4.586595, 4.253798, 4.681410, 4.730086, 4.710714, 4.803680, 4.816215, 4.782894, 4.788168. Prose samples: 7.626229, 5.497418, 5.384268, 5.990222, 5.774737, 6.199775, 5.645409, 5.281424, 5.403891, 6.803150.

All **20** samples passed actual saved-file SHA-256, changed run/input identity, non-stale current status, exported report identity and current published-capture agreement. All requested changed-scope runs correctly used full evaluation for global rules. Both runs have `source_paths_changed_during_measurement: []`; both owned entries exited with code 0, owned workers exited, and isolated endpoints were removed. All **10 prose** samples additionally prove the new visible paragraph prefix, absence of its preceding revision, and exactly **5,000 published text units** from the current bound annotation manifest. The extra proof's actual read/JSON/verification cost is measured inside the prose interval: median **0.061619 s**, range **0.053291–0.106502 s**. It is not subtracted to improve the result.

| First-observed interval, seconds (not isolated CPU stages) | Comment range | Prose range |
| --- | ---: | ---: |
| Save → matching preview publication | 2.735048–4.894357 | 3.499223–5.938043 |
| Publication → first new run | 0.310982–1.123546 | 0.343978–1.288045 |
| First new run → completed-status observation | 0.571249–0.578405 | 0.571057–0.575071 |
| Completed-status observation → final export/proof verification | 0.138363–0.230042 | 0.192475–0.322509 |

**Pre-fragment measured conclusion: comment-only p95 was 6.692495 s and visible-prose p95 was 7.626229 s; neither met ≤2 s.** Comment median improved relative to the earlier 6.721270 s median, but that is not visible-writing acceptance or a controlled causal speedup guarantee. Full rendering/publication and all configured/default/freshness boundaries remain in the measurements; no core-only/profile number replaces these failed-target results.

## Actual saved-pipeline diagnostic — cache behavior and CPU attribution only

After preserving those uninstrumented N=10 measurements, a private external `sitecustomize` instrumented the **actual** entry/native-watcher/default-notifier/HTTP pipeline for two comment and two prose saves. Package and benchmark helper sources remained unchanged at the same pin and aggregate. All state/cache variables were private **before imports**. Per-build/entry-thread cProfile, wall/thread-CPU timings, actual extractor-call counts and sentence-cache deltas are retained in the [analysis](<linux-saved-pipeline-profile-analysis.json>), [raw reproducible instrumentation/profile archive](<linux-saved-pipeline-profile-raw.zip>), and diagnostic [comment](<linux-saved-pipeline-profile-comment.json>) / [prose](<linux-saved-pipeline-profile-prose.json>) traces. All four saves were authoritative; prose publication checks passed; all owned service cleanup checks passed and measured source changes are empty. These instrumented N=2 timings are **not acceptance/p95 evidence** and must not be compared to uninstrumented latency as a speedup.

| Actual serving-worker saved build | HTML extraction cache | Sentence hits / misses | Instrumented build thread CPU, s |
| --- | --- | ---: | ---: |
| First comment save | Miss | 5,000 / 5,000 | 9.557227 |
| Second comment save | Hit (zero extraction calls) | 10,000 / 0 | 5.159138 |
| First prose save | Miss | 5,000 / 5,000 | 8.025127 |
| Second prose save | Miss | 9,999 / 1 | 7.203248 |

**Important lifecycle finding:** the initial preparation build and the serving live worker have different PIDs in both real runs. The serving worker starts from an already published preview without running the initial build in its own process, so its first real saved edit has cold process-local caches despite a settled authoritative baseline. That is a real first-save cost, not permission to discard or relabel the first saved sample. The earlier same-process warm-build profile therefore does **not** represent this first actual save.

On the second visible edit, annotation extraction still executes fully: `_extract_html_units` is 2.959 s cumulative profile wall time within a 4.215 s `build_content`; the corresponding hot comment `build_content` is 1.475 s. These are overlapping **profile wall** costs, not isolated CPU or a controlled speedup ratio. Precise stage thread-CPU observations put status freshness at roughly 0.060–0.098 s median per call, source expansion at 0.049–0.058 s, final identity verification at 0.136–0.137 s, full snapshot capture around 1.15–1.22 s, coordinator execution around 1.47–1.56 s, and the saved notifier around 0.78–0.80 s. Stages are nested and must not be summed as an end-to-end CPU budget.

This evidence favors safe renderer-owned fragment extraction reuse for visible edits over a transitive source/topology identity cache: full HTML extraction is larger, while identity reuse would require substantially more closure/freshness proof for a smaller gain. Separately, any startup-cache handoff or lifecycle change must retain the first measured save and never move changed-input work outside its timer.

## Renderer-owned fragments and a single initial production build

The next implementation adds an optional renderer-owned root-fragment plan bound to exact default composed HTML. It reuses only frozen extraction facts from the **same existing HTML parser/extractor**, not parsed projects or manifests. Exact full-HTML reconstruction, fully consumed/balanced outer parser context, a single anchored root, fresh rendered owner membership/type, and globally disjoint owner/DOM IDs prove that selectors and per-owner text/anonymous-formula counters remain unchanged. Global order is assigned freshly. Uncertain context, custom/diff composition, malformed HTML, duplicates, missing owners, or oversized chunks use the existing whole-parser path. Each root is limited to 1 MiB; an 8,192-entry / 16 MiB UTF-8 key-plus-fact-payload budget bounds retention. Source metadata, byte-derived spans, permissions, configuration fences, dependency/macros identity, page bindings and full-manifest validation remain fresh.

Cold live activation now performs its **one normal initial production build in the resident worker before readiness**, instead of discarding a separate preparer's process-local caches. There is no additional warm build or benchmark preheat. Replacing an existing live worker retains separate candidate preparation and last-good rollback; legacy direct candidate/preparer paths and diff behavior remain supported. A private nonce/PID-bound build-completion marker preserves the prior separate 600-second build and 600-second server-readiness budgets without advertising readiness before authenticated worker health. Cancellation inside an initial build aborts its report transaction rather than promoting it. Deterministic slow-clock, forged-progress, cancellation, replacement and real private-entry first-save/PID tests passed. The combined regression selection passed **268 tests in 37.41 s** before freezing source.

## Post-fragment Linux paired N=10 remeasurement — both workloads still fail the goal

Both new sequential quiet runs used actual pushed Git HEAD **`f3dd5ad493897ebb0df2b75efbcba5c870492a5f`**, checked before/after, with unchanged package and measurement helpers. Their identical **120-file** package-plus-harness aggregate is **`f30440841d4e21c94bdef3806e799d93b4e7c6ee2efe53cb47683420d4d55418`**. The entire reference workload remains **40 files / 5,000 paragraphs / 1,479,521 initial bytes**, with default **800 ms** quiet deadline and observer **0.5 s sleep plus actual HTTP work**. All other known local tests, browsers, captures, profilers and synthetic services were stopped; no source/helper/CI edits overlapped either run. This is still a shared two-CPU Linux host, not exclusive hardware. Remote macOS CI is independent. No instrumentation or private `PYTHONPATH` override was present.

| Inclusive saved edit → verified current | [Comment-only](<linux-saved-performance-optimized5-comment.json>) | [Visible prose](<linux-saved-performance-optimized5-prose.json>) |
| --- | ---: | ---: |
| Completed authoritative samples | 10 | 10 |
| Median, seconds | 4.741462 | 4.523316 |
| p95 nearest rank, seconds | **5.101345** | **4.753084** |
| First saved edit, seconds — retained | 5.101345 | 4.432345 |
| Minimum, seconds | 4.498456 | 4.310191 |
| Maximum, seconds | 5.101345 | 4.753084 |
| Inclusive ≤2 s p95 target | **FAIL** | **FAIL** |

All individual comment samples (seconds): 5.101345, 4.746596, 4.744536, 4.959871, 4.498456, 4.596181, 4.578245, 4.738389, 4.571185, 4.787375. All prose samples: 4.432345, 4.310191, 4.311926, 4.643720, 4.502590, 4.753084, 4.539045, 4.635037, 4.507588, 4.719922. No first sample was dropped or retimed. The first comment save is still the maximum; **its remaining excess is not attributed to a cause without new internal evidence**. These runs contain no direct cache-hit/PID trace; source-pinned lifecycle/equivalence regressions establish the implemented path, not an invented observation in the latency JSONs.

All **20** saves passed saved-file hash, changed run/input identity, current non-stale status, exported report identity, and current published-capture checks. Both have `source_paths_changed_during_measurement: []`, identical begin/end aggregate hashes, owned entry exit code 0, owned worker exit, and removed private endpoint. All **10 prose** saves also prove exactly **5,000 published text units**, the new one-word revision and absence of the preceding prefix, bound to the current manifest/page version. The extra proof's included cost is median **0.059730 s**, range **0.051606–0.116650 s**; none is subtracted.

| First-observed interval, seconds — not isolated CPU stages | Comment range | Prose range |
| --- | ---: | ---: |
| Save → matching preview publication | 2.560270–3.693278 | 2.564233–2.852216 |
| Publication → first new run | 0.435847–1.295277 | 0.737042–1.281799 |
| First new run → completed-status observation | 0.571780–1.295875 | 0.570959–0.576306 |
| Completed-status observation → final export/proof verification | 0.139938–0.245145 | 0.196882–0.356046 |

The separate cold ensure-to-worker-ready observations are **7.130497 s comment / 7.219116 s prose**; neither is a saved-hot sample or a claim about no-observer build CPU. Publication first-observation medians are **2.783731 s / 2.697717 s**, again upper bounds including observer work, not internal stage measurements.

**Measured Linux conclusion at this checkpoint: inclusive comment p95 is 5.101345 s and visible-prose p95 is 4.753084 s; neither meets spec425's ≤2 s goal.** The visible-edit result is lower than the preceding source's result, but multiple implementation changes and separate shared-host runs are not a controlled causal speedup experiment. Comment-only success could not substitute for visible-writing acceptance, and no core-only check/profile timing substitutes for either failed-target inclusive result. All historical N=1/N=10 and actual-pipeline diagnostic artifacts remain intact.

## macOS baseline and next fixed checkpoint

The [separate macOS record](<macos-f563199-baseline.md>) preserves four complete hosted N=10 runs at `f563199` (p95 comment/prose 3.349016/3.357771 s on push, 4.793702/3.767862 s on PR) and the requester's independently verified physical-Mac N=10 evidence (6.111938/35.028981 s). **All fail ≤2 s**; all first/slow samples and the prior N=0 startup records remain. Functional full-suite passes are recorded separately, not treated as saved-latency passes. Private raw feedback stays private; the public issue feedback is cited.

The [7744314 fixed checkpoint](<../checkpoint-7744314.md>) adds proof-tested binary SourceMap lookup and fixes canonical raw-source/engine-version authority. Its combined relevant regression passed **430 tests / 106.76s / 0 skips** before the pin. This was not a full-suite/performance pass; the completed source-pinned remeasurements and full macOS results are now recorded below. It does not move the timer boundary, reduce the configured quiet deadline, or claim that binary lookup explains the physical Mac's long samples.

## 7744314 / docs-only 6310822: completed reference remeasurement, all p95 targets still fail

Application **`7744314e36634cdba55a306d9386b01107005336`** and its pushed docs-only child **`63108221c0f1f691fa98197fb74868b2eff8cfb6`** have identical application and measurement-helper sources (Git comparison checked). All six public runs below record the same **120-file** begin/end aggregate **`42a8e701d215c4d937320986af55a1a987451083233d608b660a7775c1b92f31`**, no source changes, 40 files / 5,000 units, default 800ms quiet deadline and observer sleep 0.5s plus real HTTP work. Full native watcher/render/publication/notify/queue/check/status/export boundaries are unchanged. No first sample is discarded or retimed; no browser paint is measured.

| Platform / run / workload | Public raw evidence | Median (s) | Nearest-rank p95, N=10 (s) | ≤2s |
| --- | --- | ---: | ---: | --- |
| Linux, comment | [JSON](<linux-saved-performance-optimized6-comment.json>) | 4.3285965164 | **7.5897699429** | FAIL |
| Linux, visible prose | [JSON](<linux-saved-performance-optimized6-prose.json>) | 4.2752680921 | **5.301649058** | FAIL |
| Hosted macOS push, comment | [JSON](<macos-saved-6310822-push-comment.json>) | 3.3012312085 | **3.599518708** | FAIL |
| Hosted macOS push, visible prose | [JSON](<macos-saved-6310822-push-prose.json>) | 2.771450542 | **3.720795292** | FAIL |
| Hosted macOS PR, comment | [JSON](<macos-saved-6310822-pr-comment.json>) | 2.809923354 | **3.275192416** | FAIL |
| Hosted macOS PR, visible prose | [JSON](<macos-saved-6310822-pr-prose.json>) | 3.163168167 | **4.973879875** | FAIL |

All six JSONs are `complete: true`, with 10/10 new authoritative completed/non-stale current runs, actual saved-byte hashes, status/export/publication identity agreement and successful owned cleanup (entry exit 0, entry/worker exited, endpoint removed). Every prose run proves 10/10 actual published ordinary-word changes and exactly 5,000 text units, with proof cost inside the timer. Linux prose proof cost is median **0.077682252s**, range **0.056250796–0.107488947s**, not subtracted. Linux separate cold ensure-to-ready is **6.769893s / 6.757763s**; neither is a saved-hot sample.

Linux comment's first **7.589770s** is its maximum. Linux prose's maximum is the **second** sample; its first **4.766394s** remains included. Hosted comment maxima are also first saves; hosted prose first saves are **2.456123s / 2.666886s**, with maxima at saves 8 / 9. Different source revisions and uncontrolled shared-host loads do not establish causes for slow samples or a controlled speedup/regression attribution. Do not reclassify all maxima as startup, merge platforms, or rerun selectively until green.

The [hosted and physical macOS baseline](<macos-7744314-baseline.md>) separates functional verification from latency. Hosted [push](<https://github.com/jinghonh/LaTeX-Review/actions/runs/41983158922>) and [PR](<https://github.com/jinghonh/LaTeX-Review/actions/runs/41983165340>) verify jobs both passed **1326 tests / 24 skips / 1350 total**, in **519.32s / 676.05s**; only saved_current failed. The skip reasons remain optional TeX/PDF tools plus report-only comparison, not missing browsers/history. This is no longer the historical F3 N=0 startup condition.

### Physical Mac 7744314 repeat: fulfilled, not a saved-performance pass

The [requester's public report](<https://github.com/jinghonh/LaTeX-Review/issues/55#issuecomment-6652141178>) records macOS 27.2 arm64 / Python 3.11.14, **1348 passed / 2 report-only skips / 0 failed / 1350 total**, verify exit 0 in about 790.51s. Full regression finished before independent sequential reference-size comment/prose N=10 runs; other user load was not controlled.

Parent-verified neutral raw aggregates: comment median **1.95837414599s**, p95 **2.053136207978241s**; prose median **2.836784916988s**, p95 **5.056157624989282s**. **Both FAIL ≤2s; 2.053136 cannot be rounded into a pass.** First samples 1.937551s / 2.097751s and every slow sample are retained. Both complete, all 20 authoritative, all 10 prose visible-change/5,000-unit proofs and owned cleanup pass; source boundaries match the same 120-file aggregate with no tracked-source changes.

The public baseline quotes the public sample table and the parent's independently verified aggregates only. The parent verified ten privately supplied artifact hashes, JUnit and raw proof data; **private raw files, hashes and private directories remain private and are not copied or linked here**. No direct private-file verification by this ledger agent is claimed. The lower measured prose p95 than the previous physical f563199 run's 35.028981s is not proof of a single optimization's causal benefit. That older baseline and every N=0/N=1/N=10 artifact remain intact.

**Conclusion at 7744314:** functional macOS verification and that physical repeat were completed, but saved-hot ≤2s remained unmet. The next exact-byte structural-validation receipt was pinned at [application checkpoint 466a321](<../checkpoint-466a321.md>) with 273 relevant tests / 46.42s / 18 modules and scoped independent review. Its completed measurements and real hosted failures now follow; the older results above are not relabeled as new-source evidence.

## 466a321 / d2fc739: completed N=10, physical repeat fulfilled, all latency targets still fail

Application **`466a3213230dc59fc6405871eeb3a84c47a2d5bd`**, measured HEAD **`d2fc739b819e5c4bd8079db992b7edbe4cbcf4e2`**, have identical app/helper sources (Git comparison checked). Six new public JSONs have **120-file** begin/end aggregate **`de00f087600364452c93faee440e9ece9435fb19b99c1d3fd91799e6877d560b`** and no source changes. Workload remains 40 files / 5000 units / 1,479,521 initial bytes, default 800ms quiet deadline and 0.5s observer sleep plus actual HTTP work. Timer includes native detection, full rebuild/publication, notify/queue/check, HTTP status/current/export and prose publication proof; no browser paint. First and slow samples remain in original order.

| Platform / run / workload | Public raw evidence | Median (s) | Nearest-rank p95, N=10 (s) | Maximum at save | ≤2s |
| --- | --- | ---: | ---: | ---: | --- |
| Linux / comment | [JSON](<linux-saved-performance-optimized7-comment.json>) | 4.087779192952439 | **4.7473551540169865** | 4 | FAIL |
| Linux / visible prose | [JSON](<linux-saved-performance-optimized7-prose.json>) | 4.002629752038047 | **4.444787323009223** | 5 | FAIL |
| Hosted push / comment | [JSON](<macos-saved-d2fc739-push-comment.json>) | 4.4347147915 | **5.278743875** | 2 | FAIL |
| Hosted push / prose | [JSON](<macos-saved-d2fc739-push-prose.json>) | 3.049801938 | **3.358164417** | 8 | FAIL |
| Hosted PR / comment | [JSON](<macos-saved-d2fc739-pr-comment.json>) | 3.452289687 | **4.710165584** | 1 | FAIL |
| Hosted PR / prose | [JSON](<macos-saved-d2fc739-pr-prose.json>) | 3.127583813 | **3.480522750** | 2 | FAIL |

All six complete, each 10/10 authoritative/completed/non-stale with saved-byte/status/export/current-published identities and owned cleanup (entry exit 0, entry/worker exited, endpoint removed). All prose groups prove 10/10 real ordinary-word changes and 5000 published units, cost included. Linux proof cost min/median/max **0.051231908 / 0.062742495 / 0.116048791s**. Linux first saves **4.060014976 / 3.973113607s** remain; none of its maxima is the first save. Hosted first saves are push **4.802216 / 3.073136s**, PR **4.710166 / 3.423659s**. These are not discarded startup/outlier cases.

Linux first-observed interval medians (comment/prose) are publication **2.739426 / 2.668927s**, publication→new run **0.677188 / 0.513167s**, run→completed observation **0.573933 / 0.572771s**, and completed→export (prose includes proof) **0.074017 / 0.166556s**. These are observation intervals, **not CPU timings**, and do not isolate internal queue/notifier cost.

### Physical Mac 466a321: complete functional pass, two strict latency failures

[The requester's public report](<https://github.com/jinghonh/LaTeX-Review/issues/55#issuecomment-6653186690>) records macOS 27.2 arm64 / Python 3.11.14 / Playwright 1.64.1 / Chromium 156; verify exit 0, **1420 passed / 2 report-only skips / 0 failed / 1422 total**, 695.61s (parent-verified script total 695.610144138s). CLI/core gates passed, not saved latency. This fixed-source repeat is fulfilled; do not wait for the same Mac attention again.

Public exact aggregates: comment median **1.9407115414796863s**, p95 **2.02685012499569s**; prose median **1.963177708501462s**, p95 **2.019279333006125s**. **Both FAIL ≤2s without rounding.** Maxima are saves 2 / 6; first saves **1.9282454999629408 / 1.9486694170045666s** are retained. Each group was run once after regression, sequentially; other user load was uncontrolled. All 20 authoritative, both complete with same source120 de00… boundary/no changes and owned cleanup; all 10 prose published-change/5000-unit proofs pass. First-observed publication medians **1.19471556 / 1.19122177s** are not CPU measurements. Full public sample table and provenance are in the [466 baseline](<macos-466a321-baseline.md>).

Parent independently verified eleven private artifact hashes/JUnit and raw proof data; this agent reads only public feedback/public evidence. No private path, raw file or per-file hash manifest is copied or linked. Lower measurements than previous physical sources are not a controlled causal speedup, and physical functional success does not waive hosted failures.

### Hosted functional failures remain real, not performance-only failures

At the same source, [push 41989929700](<https://github.com/jinghonh/LaTeX-Review/actions/runs/41989929700>) verify is **1397 passed / 24 skipped / 1 failed / 544.06s**, manual entry-proxy run-ID completion wait exceeded 20s after headers/202/proxy/content-ID assertions passed. [PR 41989935454](<https://github.com/jinghonh/LaTeX-Review/actions/runs/41989935454>) verify is **1397 passed / 24 skipped / 1 failed / 772.47s**, standalone resize character error **9743.559203655352px** against **<3px**. Both workflows also have failed saved_current jobs. Neither is waived by the physical full pass.

Controlled same-content UI/proxy work supports an automatic B replacing manual A; a **test-only** explicit `on_save=false` isolates the manual header test, retaining 20s/exact run-ID/header assertions, and related 26 tests passed / 15.02s. This does not prove original CI ordering or new CI success. A single bounded Linux Chromium155 probe retained the original three 150ms checks, observed small errors and paper→window→paper scroll-root transitions; it is not CI156 cause/flake proof. The only resize-test change adds diagnostics **after failure**, not new passing-path RPCs, waits, Range replacements or a weaker threshold; 4 passed / 2 skipped / 16.94s is not the old failure's resolution. New hosted trace remains required; product JS/CSS unchanged. See the [baseline's functional investigation boundaries](<macos-466a321-baseline.md>).

### Light7 diagnostic: attribution only, never an acceptance p95

[Corrected light7b analysis](<linux-saved-pipeline-light7b-analysis.json#L1-L20>), [GC summary](<linux-saved-pipeline-light7b-gc-summary.json>) and [raw archive](<linux-saved-pipeline-light7b-raw.zip>) retain actual-pipeline comment/prose **2+2** saves on de00…, without cProfile/tracemalloc or wrapped canonical predicate/JSON helpers. Producer snapshots show three absent→present receipts (baseline plus two saves); notifier/status/export before-read observations show 6/3/3 receipts present per phase, at most 3 entries against bound128, with fresh nested decode. These are read-only before/after snapshots, not atomic branch counters; concurrent readers can prime receipts.

Parent-audited saved-worker thread CPU spans **2.130–2.357s**, GC **0.279–0.420s (13.1–17.8%)**, coordinator root **0.775–0.870s**, notifier **0.187–0.326s** are diagnostic observations. Nested stages overlap and must not be summed; GC trigger stage is not allocation origin/cause. Observer metadata CPU **0.312 / 0.335s** is phase total including startup, not per save. Worker/root RSS observations are cumulative peak ru_maxrss, **not live heap or a leak measurement**; old cProfile-inflated timings do not establish a causal ratio.

The [first-attempt JSON](<linux-saved-pipeline-light7-comment.json>) and [exact failed raw archive](<linux-saved-pipeline-light7-attempt1-raw.zip>) remain intact: one authoritative save, RuntimeError and original 180s timeout, cleanup retained. Suspected external GC aggregation iteration is not a proven production regression. Observer-only correction yielded collected authoritative/unchanged-source/cleanup/prose-proof results for both 2-save phases; these instrumented elapsed times are not N=10/p95.

**Current conclusion:** complete reference N=10 measurements still fail on all platforms, and hosted functional failures remain under investigation. Physical 466 repeat is complete, not pending or a resource blocker. Continue feasible implementation; current import-laziness/whitespace work is not yet validated or pinned/rebenchmarked and inherits none of these results. No timer/observer/debounce change, first-save deletion, selective rerun, core-only result or diagnostic profile can turn these failures into acceptance. Private-paper/few explicitly authorized real-model/author-quality evidence remains independently pending, not a reason to stop feasible work.
