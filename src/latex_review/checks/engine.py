"""Current-paper deterministic checking, suitable for CLI and service callers."""
from dataclasses import asdict

from ..identity import identity_hash
from ..rules import current_findings
from .model import ENGINE_VERSION, RULE_VERSION, compare_reports, make_finding, validate_report

BUILTIN_RULES = ("bibliography_key_unresolved", "duplicate_label", "figure_unreferenced",
                 "table_unreferenced", "unresolved_reference")


def evaluate_current(document, knowledge=None, rule_set=None, *, configuration=None, previous=None, scope="all", knowledge_status=None):
    snapshot, parsed = document.snapshot, document.parsed
    from .snapshot import canonical_check_configuration
    configuration = canonical_check_configuration(snapshot.configuration if configuration is None else configuration)
    checks = configuration.get("checks", {})
    default_rules = BUILTIN_RULES if checks.get("enabled", True) and "references" in checks.get("rules", ["references"]) else ()
    enabled = set(default_rules if rule_set is None else rule_set)
    unknown = enabled - set(BUILTIN_RULES)
    if unknown:
        raise ValueError("unknown rules: " + ", ".join(sorted(unknown)))
    if scope not in {"all", "changed"}:
        raise ValueError("unsupported scope")
    knowledge = knowledge or {}
    scan = current_findings(parsed)
    blind_spots = [{"code": "uncaptured_dependency", "message": "Capture-time dependency bytes unavailable", "file": name}
                   for name in snapshot.configuration.get("_snapshot_unverified_dependencies", [])]
    if scan["execution_uncertain"]:
        blind_spots.append({"code": "unexpanded_conditional", "message": "TeX conditionals are not executed by static rules", "file": None})
    for macro in scan["unexpanded_rule_macros"]:
        fragments = document.evidence(macro["start"], macro["end"])
        blind_spots.append({"code": "unexpanded_rule_macro", "message": "Macro definitions may create rule facts not statically executed",
                           "file": fragments[0]["file"] if fragments else None})
    for issue in parsed.expanded.diagnostics:
        if issue.certainty == "exact":
            continue
        blind_spots.append({"code": issue.code, "message": issue.message, "file": issue.file})
    rendering_blind_spots, bibliography_blind_spots = [], []
    for diagnostic in parsed.diagnostics:
        if diagnostic.code in {"unknown_latex", "plastex_parse_error", "low_confidence_source",
                               "bibliography_unreadable", "bibliography_unsupported"}:
            location = diagnostic.source_new or diagnostic.source_old
            target = bibliography_blind_spots if diagnostic.code.startswith("bibliography_") else rendering_blind_spots
            target.append({"code": diagnostic.code, "message": diagnostic.message,
                           "file": location.file if location else None})
    blind_spots = sorted({(b["code"], b["message"], b["file"]): b for b in blind_spots}.values(),
                        key=lambda b: (b["code"], b["file"] or "", b["message"]))
    complete_source = not blind_spots
    coverage = []
    for rule in BUILTIN_RULES:
        reasons = []
        if rule not in enabled:
            reasons.append("disabled")
        if not complete_source:
            reasons.append("source_incomplete")
        if rule in {"figure_unreferenced", "table_unreferenced"} and any(b["code"] == "plastex_parse_error" for b in rendering_blind_spots):
            reasons.append("structure_parse_failed")
        if rule == "bibliography_key_unresolved" and not scan["bibliography_reliable"]:
            reasons.append("bibliography_unreliable")
        complete = not reasons
        count = scan["applicable"][rule]
        coverage.append({"rule_id": rule, "rule_version": RULE_VERSION, "entry": snapshot.entry,
                         "scope": "all", "status": "disabled" if rule not in enabled else "unsupported" if reasons else "no_applicable_objects" if not count else "executed",
                         "complete": complete, "applicable_count": count if complete_source else None,
                         "reasons": reasons, "blind_spots": (blind_spots + (bibliography_blind_spots if rule == "bibliography_key_unresolved" else []) +
                             ([b for b in rendering_blind_spots if b["code"] == "plastex_parse_error"] if rule in {"figure_unreferenced", "table_unreferenced"} else [])) if rule in enabled else []})
    if rendering_blind_spots:
        # Preview warnings do not erase exact raw label/citation evidence.
        coverage.append({"rule_id": "source_rendering", "rule_version": RULE_VERSION, "entry": snapshot.entry,
                         "scope": "all", "status": "unsupported", "complete": False, "applicable_count": None,
                         "reasons": ["preview_rendering_incomplete"], "blind_spots": rendering_blind_spots})
    # Equation-number comparison requires two versions; do not fabricate one.
    coverage.append({"rule_id": "equation_number_changed", "rule_version": RULE_VERSION,
                     "entry": snapshot.entry, "scope": "all", "status": "prerequisite_missing",
                     "complete": False, "applicable_count": None,
                     "reasons": ["requires_two_versions"], "blind_spots": []})
    findings = []
    for item in scan["findings"]:
        rule = item["rule_id"]
        if rule not in enabled or scan["execution_uncertain"]:
            continue
        # Only duplicate labels have affirmative evidence independent of an
        # absence proof across the whole document.
        if rule != "duplicate_label" and not next(c for c in coverage if c["rule_id"] == rule)["complete"]:
            continue
        occurrences, evidence = [], []
        for index, site in enumerate(item["sites"]):
            fragments = document.evidence(site["start"], site["end"])
            if not fragments or any(fragment["reliability"] != "exact" for fragment in fragments):
                continue
            evidence.extend(fragments)
            occurrences.append({"entry": snapshot.entry, "node_id": site["node_id"],
                                "reading_order": site["start"], "evidence": fragments})
        if evidence and (rule != "duplicate_label" or len(occurrences) >= 2):
            findings.append(make_finding(rule, item["key"], evidence,
                                         severity="warning" if rule in {"bibliography_key_unresolved", "duplicate_label"} else "info",
                                         related_occurrences=occurrences))
    invalid_knowledge = knowledge_status and knowledge_status.get("status") not in {"ok", "missing"}
    if invalid_knowledge:
        coverage.append({"rule_id": "knowledge_registry", "rule_version": RULE_VERSION, "entry": snapshot.entry,
                         "scope": "all", "status": "failed", "complete": False, "applicable_count": None,
                         "reasons": ["knowledge_" + knowledge_status["status"]], "blind_spots": []})
        enabled.add("knowledge_registry")
    elif knowledge:
        from .knowledge_rules import evaluate_knowledge
        knowledge_findings, knowledge_coverage = evaluate_knowledge(document, knowledge, configuration=configuration)
        findings.extend(knowledge_findings)
        coverage.extend(knowledge_coverage)
        enabled.update(c["rule_id"] for c in knowledge_coverage if c["status"] != "disabled")
    input_data = {**snapshot.identity, "knowledge_hash": identity_hash({"invalid_content_hash": knowledge_status.get("content_hash"), "status": knowledge_status["status"]}) if invalid_knowledge else identity_hash(knowledge),
                  "knowledge_revision": knowledge.get("revision", 0), "engine_version": ENGINE_VERSION,
                  "rules": {rule: RULE_VERSION for rule in sorted(enabled)},
                  "configuration_hash": identity_hash(configuration), "scope": "all"}
    report = {"schema_version": "1.0", "input_id": identity_hash(input_data), "input": input_data,
              "status": "completed" if all(c["complete"] for c in coverage if c["rule_id"] in enabled) else "partial",
              "evaluation_mode": "full", "requested_scope": scope,
              "fallback_reason": "full_scan_required_for_global_rules" if scope == "changed" and previous else "no_baseline" if scope == "changed" else None,
              "findings": findings, "coverage": coverage}
    report = compare_reports(previous, report)
    return validate_report(report)
