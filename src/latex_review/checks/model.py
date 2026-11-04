"""Independent JSON contract and deterministic result helpers."""
from copy import deepcopy
from importlib.resources import files
import json

from jsonschema import Draft202012Validator
from ..identity import identity_hash

SCHEMA_VERSION = "1.0"
ENGINE_VERSION = "1.0"
RULE_VERSION = "1.0"


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_report(report):
    schema = json.loads(files("latex_review").joinpath("schemas/checks.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(report)
    if report["input_id"] != identity_hash(report["input"]):
        raise ValueError("input_id does not match complete input")
    seen = set()
    for finding in report["findings"]:
        if finding["finding_key"] in seen:
            raise ValueError("duplicate logical finding_key")
        seen.add(finding["finding_key"])
        for evidence in [*finding["evidence"], *(e for o in finding["related_occurrences"] for e in o["evidence"])]:
            if evidence["end"] - evidence["start"] != len(evidence["quote"]):
                raise ValueError("evidence Unicode span disagrees with quote")
            if evidence["end_line"] < evidence["start_line"]:
                raise ValueError("evidence line range is inverted")
            if finding["lifecycle"] == "current" and (evidence["content_id"] != report["input"]["content_id"] or
                    evidence["file_sha256"] != report["input"]["files"].get(evidence["file"])):
                raise ValueError("current evidence is not bound to input bytes")
    return report


def make_finding(rule_id, key, evidence, *, severity="warning", message=None, related_occurrences=None):
    return {"rule_id": rule_id, "rule_version": RULE_VERSION,
            "finding_key": identity_hash({"rule_id": rule_id, "key": key}),
            "target": key, "origin": "deterministic", "assessment": "rule_violation",
            "severity": severity, "message": message or f"{rule_id}: {key}",
            "review_state": "unreviewed", "lifecycle": "current", "delta_status": "not_comparable",
            "evidence": evidence, "related_occurrences": related_occurrences or [], "change_ids": []}


def summarize(report):
    current = [f for f in report["findings"] if f["lifecycle"] == "current"]
    return {"finding_count": len(current),
            "occurrence_count": sum(len(f["related_occurrences"]) for f in current),
            "candidate_count": sum(f["assessment"] == "candidate" for f in current),
            "coverage_complete": report["status"] == "completed"}


def compare_reports(previous, current):
    """Only infer disappearance after a complete comparable rule execution.

    Incomplete prior runs cannot establish a trustworthy baseline. A current
    partial run may preserve known positives, but never resolve missing results.
    """
    result = deepcopy(current)
    reasons = []
    if previous is None:
        reasons.append("no_baseline")
    else:
        for field in ("paper_id", "entry", "parser_version", "knowledge_hash", "knowledge_revision",
                      "engine_version", "rules", "configuration_hash", "scope"):
            if previous["input"].get(field) != current["input"].get(field):
                reasons.append(field + "_changed")
        if previous["status"] != "completed":
            reasons.append("baseline_incomplete")
    result["comparison"] = {"baseline_input_id": previous["input_id"] if previous else None,
                            "comparable": not reasons, "reasons": reasons}
    before = {f["finding_key"]: f for f in previous["findings"] if f["lifecycle"] == "current"} if previous else {}
    now = {f["finding_key"]: f for f in result["findings"]}
    coverage = {c["rule_id"]: c for c in current["coverage"]}
    for finding in now.values():
        complete = coverage.get(finding["rule_id"], {}).get("complete", False)
        finding["delta_status"] = ("existing" if finding["finding_key"] in before else "new") if not reasons and complete else "not_comparable"
    for key in sorted(before.keys() - now.keys()):
        old = deepcopy(before[key])
        complete = coverage.get(old["rule_id"], {}).get("complete", False)
        if not reasons and complete:
            old.update(lifecycle="resolved", delta_status="resolved")
        else:
            old.update(lifecycle="unknown", delta_status="not_comparable")
        result["findings"].append(old)
    result["findings"].sort(key=lambda f: (f["rule_id"], f["finding_key"], f["lifecycle"]))
    result["summary"] = summarize(result)
    return result
