"""Standalone writing-check CLI; the legacy latex-review protocol is untouched."""
import argparse
import json
from pathlib import Path
import sys

from ..identity import paper_identity, identity_hash
from ..cli import _config
from ..project_profile import settings
from .document import build_check_document
from .engine import evaluate_current
from .model import canonical_json
from .snapshot import capture_snapshot, canonical_check_configuration
from .store import CheckStore, default_store_path


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def exit_code(report, fail_on=None):
    if report["status"] == "failed":
        return 1
    if report["status"] != "completed":
        return 2
    levels = {"info": 0, "warning": 1, "error": 2}
    if fail_on and any(f["lifecycle"] == "current" and f["assessment"] != "candidate" and
                       levels[f["severity"]] >= levels[fail_on] for f in report["findings"]):
        return 3
    return 0


def _parser():
    parser = Parser(prog="latex-review-check")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
    run = commands.add_parser("run")
    run.add_argument("entry", nargs="?")
    run.add_argument("--project", type=Path)
    run.add_argument("--all-entries", action="store_true")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--changed", action="store_true")
    run.add_argument("--semantic", action="store_true")
    run.add_argument("--config", type=Path)
    run.add_argument("--store", type=Path)
    run.add_argument("--output", type=Path)
    run.add_argument("--format", choices=("json", "text"), default="text")
    run.add_argument("--fail-on", choices=("info", "warning", "error"))
    query = commands.add_parser("query")
    query.add_argument("--run", required=True)
    query.add_argument("--store", type=Path)
    query.add_argument("--format", choices=("json", "text"), default="text")
    query.add_argument("--evidence", action="store_true")
    query.add_argument("--offset", type=int, default=0)
    query.add_argument("--limit", type=int, default=100)
    semantic = commands.add_parser("semantic")
    operations = semantic.add_subparsers(dest="operation", required=True, parser_class=Parser)
    export = operations.add_parser("export")
    export.add_argument("--run", required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--task", choices=("concept_definition", "name_mapping", "cross_section_comparison"), default="concept_definition")
    load = operations.add_parser("import")
    load.add_argument("--request", type=Path, required=True)
    load.add_argument("--response", type=Path, required=True)
    for operation in (export, load):
        operation.add_argument("--store", type=Path)
        operation.add_argument("--project", type=Path)
        operation.add_argument("--config", type=Path)
    return parser


def _find_run(args):
    if args.store:
        return CheckStore(args.store).get_run(args.run)
    from ..local_paths import state_directory
    for path in sorted((state_directory() / "checks").glob("*/checks.sqlite3")):
        run = CheckStore(path).get_run(args.run)
        if run:
            return run
    return None


def _query(args):
    if args.offset < 0 or not 1 <= args.limit <= 1000:
        raise ValueError("offset must be nonnegative and limit must be 1..1000")
    run = _find_run(args)
    if run is None:
        raise ValueError("run_not_found")
    report = run["report"]
    all_findings = report["findings"] if report else []
    findings = all_findings[args.offset:args.offset + args.limit]
    if not args.evidence:
        findings = [{k: v for k, v in f.items() if k not in {"evidence", "related_occurrences"}} for f in findings]
    next_offset = args.offset + len(findings)
    payload = {"run_id": run["run_id"], "input_id": run["input_id"], "status": run["status"],
               "summary": report["summary"] if report else None, "error": run["error"],
               "findings": findings, "total": len(all_findings), "offset": args.offset,
               "next_offset": next_offset if next_offset < len(all_findings) else None,
               "truncated": next_offset < len(all_findings), "evidence_included": args.evidence,
               "coverage": report["coverage"] if report else []}
    print(canonical_json(payload) if args.format == "json" else
          f"run={args.run} status={run['status']} findings={len(all_findings)} returned={len(findings)} next_offset={payload['next_offset']}")
    return 0


COORDINATOR_TIMEOUT = 300


class CoordinatorError(RuntimeError):
    def __init__(self, message, *, run_id=None, exit_code=1):
        super().__init__(message)
        self.code, self.run_id, self.exit_code = "coordinator_run", run_id, exit_code


def _coordinator_run(identity, config, args):
    """Reuse an already-running entry, never start one or duplicate a submitted run."""
    if args.store:
        return None  # Explicit database selection requests independent execution.
    import time
    from .. import local_client
    from ..local_protocol import LocalError
    from ..sources import ProjectSource
    from ..contract import ComparisonSource
    from ..project_profile import settings
    from .model import validate_report
    try:
        local_client.endpoint()  # Authenticated nonce/protocol discovery; no spawn.
        status = local_client.checks_request(identity["paper_id"], "status")
    except LocalError as exc:
        if exc.status in {404, 503}:
            return None
        raise
    effective = settings(ProjectSource("new", Path(identity["root"]), identity["entry"],
                                       ComparisonSource("directory", identity["root"]), configuration=config))
    configuration_hash = identity_hash(canonical_check_configuration(effective))
    if status.get("configuration_hash") != configuration_hash:
        return None
    # Once POST is attempted, a network error can mean the server accepted it.
    # Never switch to local execution after this point.
    try:
        task = local_client.checks_request(identity["paper_id"], "runs", {
            "scope": "changed" if args.changed else "all", "configuration_hash": configuration_hash})
    except LocalError as exc:
        raise CoordinatorError(f"Coordinator submission failed or uncertain; no duplicate local run started: {exc}") from exc
    run_id = task["run_id"]
    deadline = time.monotonic() + COORDINATOR_TIMEOUT
    while task["state"] not in {"completed", "partial", "failed", "cancelled", "interrupted", "stale"}:
        if time.monotonic() >= deadline:
            raise CoordinatorError("Coordinator run is still pending; query its run_id", run_id=run_id, exit_code=2)
        try:
            time.sleep(0.1)
            task = local_client.checks_request(identity["paper_id"], "runs/" + run_id)
        except KeyboardInterrupt as exc:
            try:
                local_client.checks_request(identity["paper_id"], "runs/" + run_id + "/cancel", {})
            except LocalError:
                pass
            raise CoordinatorError("Cancellation requested; query run_id for final state", run_id=run_id, exit_code=2) from exc
        except LocalError as exc:
            raise CoordinatorError(f"Coordinator connection lost; query run_id: {exc}", run_id=run_id, exit_code=2) from exc
    if task["state"] not in {"completed", "partial"}:
        raise CoordinatorError(f"Coordinator run ended as {task['state']}", run_id=run_id,
                               exit_code=1 if task["state"] == "failed" else 2)
    try:
        report = local_client.checks_request(identity["paper_id"], "export", query="run_id=" + run_id)
    except LocalError as exc:
        raise CoordinatorError(f"Cannot export coordinator run: {exc}", run_id=run_id, exit_code=2) from exc
    validate_report(report)
    if report["input"]["paper_id"] != identity["paper_id"] or report["input"]["configuration_hash"] != configuration_hash:
        raise CoordinatorError("Coordinator report identity/configuration mismatch", run_id=run_id)
    return {"run_id": run_id, "report": report}


def _semantic_exit(result):
    status = result.get("status", result.get("state", "completed"))
    if status in {"failed", "error", "budget_exceeded"}:
        return 1
    return 0 if status in {"completed", "valid", "ready"} else 2


def _semantic(args):
    from .semantic_cli import export_run, import_file
    if args.operation == "export":
        result = export_run(args.run, store=args.store, output=args.output, task=args.task,
                            project=args.project, config=args.config)
    else:
        result = import_file(args.request, args.response, store=args.store,
                             project=args.project, config=args.config)
    print(canonical_json(result))
    return _semantic_exit(result)


def _remember_and_semantic(identity, configuration, store_path, output, args):
    from .semantic_cli import remember_context, run_semantic
    remember_context(identity, configuration, store_path, config_path=args.config)
    if not args.semantic:
        return 0
    if output.get("state") == "stale":
        output["semantic"] = {"state": "stale", "error": {"code": "input_changed"}}
        return 2
    try:
        output["semantic"] = run_semantic(identity, configuration, store_path, config_path=args.config,
                                          expected_input_id=output["report"]["input_id"], use_coordinator=not bool(args.store))
    except KeyboardInterrupt:
        output["semantic"] = {"state": "cancelled", "error": {"code": "cancelled", "message": "Semantic check cancelled"}}
    except Exception as exc:
        output["semantic"] = {"state": "failed", "error": {"code": getattr(exc, "code", "semantic_failed"), "message": str(exc)}}
    return _semantic_exit(output["semantic"])


def _read_knowledge(root, configuration):
    from .registry import KnowledgeStore, RegistryError, empty_registry
    from ..local_paths import state_directory
    policy = configuration.get("knowledge", {})
    try:
        return KnowledgeStore(root, state_directory(), source=policy.get("storage", "auto"),
                              path=policy.get("path"), read_only=configuration.get("project", {}).get("read_only", False)).read()
    except RegistryError as exc:
        return {"registry": empty_registry(), "status": "unavailable", "content_hash": None, "error": str(exc)}


def _effective_configuration(identity, configuration):
    from ..sources import ProjectSource
    from ..contract import ComparisonSource
    root = Path(identity["root"])
    return canonical_check_configuration(settings(ProjectSource("new", root, identity["entry"], ComparisonSource("directory", str(root)), configuration=configuration)))


def _snapshot_still_current(snapshot, identity, knowledge, args):
    from .snapshot import _raw_files, _expansion_identity
    from ..sources import SourceError
    try:
        fresh_raw = _config(args.config or Path(identity["root"]) / ".latex-review.toml", args.config is not None)
        if _effective_configuration(identity, fresh_raw) != snapshot.configuration:
            return False
        expanded = snapshot.parsed.expanded.source.expand()
        if _raw_files(expanded) != dict(snapshot.files) or _expansion_identity(expanded) != _expansion_identity(snapshot.parsed.expanded):
            return False
        fresh_knowledge = _read_knowledge(Path(identity["root"]), snapshot.configuration)
        return (fresh_knowledge.get("status"), fresh_knowledge.get("content_hash")) == (knowledge.get("status"), knowledge.get("content_hash"))
    except (OSError, ValueError, SourceError):
        return False


def _run(args):
    if args.all_entries and (args.entry or args.project is None):
        raise ValueError("--all-entries requires --project and cannot accompany entry")
    if not args.entry and not args.all_entries:
        raise ValueError("entry or --project --all-entries is required")
    root = args.project.resolve() if args.project else Path(args.entry).resolve().parent
    config = _config(args.config or root / ".latex-review.toml", args.config is not None)
    if args.all_entries:
        from ..project_profile import load
        profile = load(root)
        entries = config.get("project", {}).get("entries") or profile.get("project", {}).get("entries", [])
        if not entries:
            raise ValueError("no configured project.entries")
    else:
        entry = Path(args.entry).expanduser()
        entries = [entry.resolve() if not args.project else entry]
    outputs, codes = [], []
    for entry in entries:
        identity = paper_identity(root, entry, require_entry=True)
        effective = _effective_configuration(identity, config)
        coordinated = _coordinator_run(identity, effective, args)
        if coordinated is not None:
            codes.append(_remember_and_semantic(identity, effective, default_store_path(identity["paper_id"]), coordinated, args))
            outputs.append(coordinated)
            codes.append(exit_code(coordinated["report"], args.fail_on))
            continue
        store = CheckStore(args.store or default_store_path(identity["paper_id"]))
        with store.paper_lock(identity["paper_id"]):
            store.recover_interrupted(identity["paper_id"])
            run_id = store.begin_run(identity["paper_id"])
            try:
                snapshot = capture_snapshot(identity["entry"], root=root, configuration=config)
                knowledge = _read_knowledge(root, snapshot.configuration)
                baseline = store.latest(identity["paper_id"])
                report = evaluate_current(build_check_document(snapshot), knowledge=knowledge["registry"],
                                          knowledge_status=knowledge, previous=baseline["report"] if baseline else None,
                                          scope="changed" if args.changed else "all")
                still_current = _snapshot_still_current(snapshot, identity, knowledge, args)
                store.save_report(report, run_id=run_id, make_current=still_current)
            except BaseException as exc:
                store.finish_failed(run_id, {"code": getattr(exc, "code", "execution_failed"), "message": str(exc)},
                                    status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed")
                raise
        output = {"run_id": run_id, "report": report}
        if not still_current:
            output["state"] = "stale"
            codes.append(2)
        codes.append(_remember_and_semantic(identity, snapshot.configuration, store.path, output, args))
        outputs.append(output)
        codes.append(exit_code(report, args.fail_on))
    payload = outputs[0] if len(outputs) == 1 else {"runs": outputs}
    if args.output:
        # Explicit output is the only project-adjacent write made by this command.
        args.output.write_text(canonical_json(outputs[0]["report"] if len(outputs) == 1 else payload) + "\n", encoding="utf-8")
    if args.format == "json":
        print(canonical_json(payload))
    else:
        for output in outputs:
            report = output["report"]
            print(f"run={output['run_id']} status={output.get('state', report['status'])} findings={report['summary']['finding_count']} evaluation_mode=full baseline={report['comparison']['baseline_input_id']} fallback={report['fallback_reason']}")
    return next((code for code in (1, 2, 3) if code in codes), 0)


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        if args.command == "run":
            return _run(args)
        return _query(args) if args.command == "query" else _semantic(args)
    except KeyboardInterrupt:
        print('{"error":{"code":"cancelled","message":"Check cancelled"}}', file=sys.stderr)
        return 2
    except Exception as exc:
        error = {"code": getattr(exc, "code", "check_failed"), "message": str(exc)}
        if getattr(exc, "run_id", None):
            error["run_id"] = exc.run_id
        print(canonical_json({"error": error}), file=sys.stderr)
        return getattr(exc, "exit_code", 1)


if __name__ == "__main__":
    raise SystemExit(main())
