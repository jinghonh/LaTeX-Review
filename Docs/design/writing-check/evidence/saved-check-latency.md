# Actual saved-edit → current-report latency

## Reproduction

From the repository root, using the project's virtualenv:

```sh
.venv/bin/python Docs/design/writing-check/evidence/benchmark_saved_checks.py \
  --files 40 --paragraphs 5000 --saves 10 --settle-timeout 90 \
  --output Docs/design/writing-check/evidence/linux-saved-performance.json \
  --concurrent-workload 'Describe other workloads running during this measurement.'
```

The [harness](<benchmark_saved_checks.py>) creates the same synthetic prose fixture as the [core benchmark](<benchmark_checks.py>): 40 source files and 5,000 paragraphs, roughly 1.48 MB. It runs the actual private local entry, `ensure`, managed live worker, native filesystem watcher, live preview rebuild and atomic publication, asynchronous saved-check notifier, and check coordinator. It never manually submits a check after saving. An explicit private configuration disables TeX compilation while leaving checks/on-save/debounce at their defaults. No model, external network service, private paper, or browser is used.

The baseline must have an authoritative current report before the first edit. Each subsequent save appends a distinct comment to the same included source. The next save starts only after the previous save produces a different run ID and input ID, non-stale completed status, and an exported report whose input ID agrees with current status and whose content ID agrees with the newly published preview. The changed file's SHA-256 must also match the actual saved bytes.

Timing starts immediately before opening/writing the source and ends after verifying that authoritative report over the worker's real loopback HTTP route. Write close is included; this is not an `fsync` durability measurement. Startup/initial rendering and baseline settlement are recorded separately.

## Stage boundaries and limitations

- **800 ms is the default saved-notifier debounce**, scheduled after a successful live preview publication. It is not the benchmark's observer cadence and is not browser refresh time.
- The observer sleeps 100 ms between attempts; actual cadence is that sleep **plus** HTTP and filesystem work. Per-request HTTP RTTs, counts, old-input/no-new-run observations, and observed states are retained.
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
