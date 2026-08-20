"""Continuous improvement pattern analysis (V3.6).

Suggest only. Never rewrites protocol, routing, prompts, or workflow config.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}

FORBIDDEN_ACTIONS = [
    "rewrite SKILL.md",
    "edit routing-policy.yaml",
    "edit risk-policy.yaml",
    "edit workflow-policy.yaml",
    "edit prompts/",
    "auto-merge protocol changes",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_ci_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    # Minimal YAML subset for this config shape
    cfg: dict[str, Any] = {
        "version": 1,
        "enabled": True,
        "window_reviews": 40,
        "min_occurrences": 7,
        "finding_buckets": ["accepted", "validated"],
        "group_by": ["finding_pattern", "category"],
        "min_severity": ["medium", "high", "critical"],
        "output": {
            "relative_dir": "improvements",
            "json_name": "suggestions.json",
            "markdown_name": "ENGINEERING_IMPROVEMENT_SUGGESTIONS.md",
        },
        "never_write_globs": [],
    }
    # Parse simple key: value and list blocks
    section = None
    list_key = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0 and line.endswith(":") and ":" == line[-1]:
            key = line[:-1]
            if key in ("output",):
                section = key
                list_key = None
                cfg.setdefault(key, {})
            elif key in (
                "finding_buckets",
                "group_by",
                "min_severity",
                "never_write_globs",
            ):
                section = None
                list_key = key
                cfg[key] = []
            else:
                section = None
                list_key = None
            continue
        if line.startswith("- ") and list_key:
            cfg[list_key].append(_scalar(line[2:].strip()))
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            key = k.strip()
            val = v.strip()
            if section == "output" and val:
                cfg["output"][key] = _scalar(val)
            elif section is None and val and list_key is None:
                if key in ("window_reviews", "min_occurrences", "version"):
                    cfg[key] = int(_scalar(val)) if str(_scalar(val)).isdigit() else _scalar(val)
                elif key == "enabled":
                    cfg[key] = str(_scalar(val)).lower() in ("true", "yes", "1")
                elif key in ("suggestion_template", "isolated_note"):
                    pass  # multi-line ignored; hardcoded bodies below
            continue
    return cfg


def _scalar(v: str) -> Any:
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    if v.lower() in ("true", "yes"):
        return True
    if v.lower() in ("false", "no"):
        return False
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def assert_safe_output_path(path: Path, never_globs: list[str]) -> None:
    resolved = str(path.resolve())
    banned = (
        "/SKILL.md",
        "routing-policy.yaml",
        "risk-policy.yaml",
        "workflow-policy.yaml",
        "model-policy.yaml",
        "/prompts/",
        "/contracts/",
    )
    for b in banned:
        if b in resolved:
            raise ValueError(f"refusing to write near protocol path ({b}): {path}")
    for g in never_globs or []:
        # crude: if glob stem appears in path
        stem = g.replace("**/", "").replace("*", "")
        if stem and stem in resolved:
            raise ValueError(f"refusing to write path matching never_write_globs: {path}")


def iter_canonical_records(repo: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    root = repo / "records"
    if not root.exists():
        return records
    for path in sorted(root.glob("*/*/record.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("lifecycle") not in ("canonical", "candidate", None):
            # still include canonical primarily
            if rec.get("lifecycle") == "superseded":
                continue
        records.append(rec)
    records.sort(key=lambda r: str(r.get("completed_at") or ""), reverse=True)
    return records


def _severity_ok(sev: str, min_sevs: list[str]) -> bool:
    allowed = {s.lower() for s in min_sevs}
    return str(sev or "").lower() in allowed


def extract_pattern_events(
    records: list[dict[str, Any]],
    *,
    buckets: list[str],
    min_severity: list[str],
    group_by: list[str],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for rec in records:
        eid = str(rec.get("evidence_id") or "")
        findings = rec.get("findings") or {}
        if not isinstance(findings, dict):
            continue
        for bucket in buckets:
            for f in findings.get(bucket) or []:
                if not isinstance(f, dict):
                    continue
                if not _severity_ok(str(f.get("severity") or ""), min_severity):
                    continue
                key = None
                kind = None
                for g in group_by:
                    if g == "finding_pattern":
                        fp = f.get("finding_pattern")
                        if fp:
                            key = str(fp)
                            kind = "finding_pattern"
                            break
                    if g == "category":
                        cat = f.get("category")
                        if cat:
                            key = str(cat)
                            kind = "category"
                            break
                if not key or not kind:
                    continue
                events.append(
                    {
                        "pattern_key": key,
                        "group_by": kind,
                        "evidence_id": eid,
                        "title": str(f.get("title") or ""),
                        "severity": str(f.get("severity") or ""),
                        "category": str(f.get("category") or ""),
                    }
                )
    return events


def aggregate(events: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        k = (ev["group_by"], ev["pattern_key"])
        g = groups.setdefault(
            k,
            {
                "pattern_key": ev["pattern_key"],
                "group_by": ev["group_by"],
                "count": 0,
                "evidence_ids": [],
                "example_titles": [],
                "severities": defaultdict(int),
            },
        )
        g["count"] += 1
        if ev["evidence_id"] and ev["evidence_id"] not in g["evidence_ids"]:
            g["evidence_ids"].append(ev["evidence_id"])
        title = ev.get("title") or ""
        if title and title not in g["example_titles"] and len(g["example_titles"]) < 5:
            g["example_titles"].append(title)
        g["severities"][ev.get("severity") or "unknown"] += 1
    # freeze severities
    for g in groups.values():
        g["severities"] = dict(g["severities"])
    return groups


def build_report(
    records: list[dict[str, Any]],
    cfg: dict[str, Any],
    *,
    evidence_repo: str | None,
) -> dict[str, Any]:
    window = int(cfg.get("window_reviews") or 40)
    min_occ = int(cfg.get("min_occurrences") or 7)
    scanned = records[:window]
    events = extract_pattern_events(
        scanned,
        buckets=list(cfg.get("finding_buckets") or ["accepted", "validated"]),
        min_severity=list(cfg.get("min_severity") or ["medium", "high", "critical"]),
        group_by=list(cfg.get("group_by") or ["finding_pattern", "category"]),
    )
    groups = aggregate(events)
    suggestions: list[dict[str, Any]] = []
    below: list[dict[str, Any]] = []
    for (_kind, _key), g in sorted(groups.items(), key=lambda x: (-x[1]["count"], x[0][1])):
        stat = {
            "pattern_key": g["pattern_key"],
            "group_by": g["group_by"],
            "count": g["count"],
            "evidence_ids": g["evidence_ids"],
            "example_titles": g["example_titles"],
            "severities": g["severities"],
        }
        if g["count"] >= min_occ:
            body = (
                f"This class of issue has appeared {g['count']} times in the last "
                f"{window} reviews. Consider updating the review protocol or adding "
                f"canonical guidance.\n\n"
                f"Yonko suggests only. It will not rewrite the protocol itself. "
                f"A human still decides."
            )
            sid = hashlib.sha256(
                f"{g['group_by']}:{g['pattern_key']}:{g['count']}:{window}".encode()
            ).hexdigest()[:12]
            suggestions.append(
                {
                    "id": f"imp-{sid}",
                    "pattern_key": g["pattern_key"],
                    "group_by": g["group_by"],
                    "count": g["count"],
                    "window": window,
                    "classification": "process_signal",
                    "title": f"Engineering Improvement Suggestion: {g['pattern_key']}",
                    "body": body,
                    "evidence_ids": g["evidence_ids"],
                    "example_titles": g["example_titles"],
                    "human_decision_required": True,
                    "forbidden_actions": list(FORBIDDEN_ACTIONS),
                }
            )
        else:
            below.append(stat)

    return {
        "schema_version": "1",
        "generated_at": utc_now(),
        "window_reviews": window,
        "records_scanned": len(scanned),
        "min_occurrences": min_occ,
        "evidence_repo": evidence_repo,
        "mutates_protocol": False,
        "suggestions": suggestions,
        "below_threshold": below[:50],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Engineering Improvement Suggestions",
        "",
        f"Generated: {report.get('generated_at')}",
        f"Window: last {report.get('window_reviews')} reviews "
        f"(scanned {report.get('records_scanned')})",
        f"Threshold: {report.get('min_occurrences')} occurrences",
        "Protocol mutation: **never** (suggest only - human decides)",
        "",
        "---",
        "",
    ]
    suggestions = report.get("suggestions") or []
    if not suggestions:
        lines.append("_No process-level patterns crossed the threshold in this window._")
        lines.append("")
    for s in suggestions:
        lines.append(f"## {s.get('title')}")
        lines.append("")
        lines.append(f"- Pattern: `{s.get('pattern_key')}` ({s.get('group_by')})")
        lines.append(
            f"- Occurrences: **{s.get('count')}** in the last {s.get('window')} reviews"
        )
        lines.append(f"- Classification: {s.get('classification')}")
        lines.append(f"- Evidence ids: {', '.join(s.get('evidence_ids') or []) or '(none)'}")
        lines.append(
            f"- Example titles: {'; '.join(s.get('example_titles') or []) or '(none)'}"
        )
        lines.append("")
        lines.append(s.get("body") or "")
        lines.append("")
        lines.append(
            "**Human decision required.** Do not ask Yonko to rewrite SKILL, "
            "routing policy, or prompts from this file."
        )
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## Below threshold (informational)")
    lines.append("")
    below = report.get("below_threshold") or []
    if not below:
        lines.append("_None._")
    for b in below:
        lines.append(
            f"- `{b.get('pattern_key')}` ({b.get('group_by')}): {b.get('count')} occurrence(s)"
        )
    lines.append("")
    return "\n".join(lines)


def write_report(report: dict[str, Any], out_dir: Path, cfg: dict[str, Any]) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    names = cfg.get("output") or {}
    json_name = names.get("json_name") or "suggestions.json"
    md_name = names.get("markdown_name") or "ENGINEERING_IMPROVEMENT_SUGGESTIONS.md"
    json_path = out_dir / json_name
    md_path = out_dir / md_name
    never = list(cfg.get("never_write_globs") or [])
    assert_safe_output_path(json_path, never)
    assert_safe_output_path(md_path, never)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}
