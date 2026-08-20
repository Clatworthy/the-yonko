#!/usr/bin/env python3
"""Yonko Engineering Evidence Index CLI (stdlib only).

Subcommands:
  init-repo       Create/seed a local canonical evidence repository checkout
  candidate       Build a candidate record from a completed Yonko session
  validate        Validate a candidate or canonical record directory
  publish         Safe UX: candidate -> preview -> validate/secret-scan ->
                  explicit hash approval -> publish-local (no git commit/push)
  publish-local   Copy a hash-confirmed candidate into the local evidence repo
  append-event    Append an outcome event to a canonical record (append-only)
  rebuild         Rebuild inverted indexes from canonical records
  refresh-cache   Copy indexes into the disposable local read cache
  query           Structured, explainable retrieval over the cache or repo

Never runs git commit, git push, or contacts a remote.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = SKILL_ROOT / "contracts" / "evidence"
TAXONOMY = SKILL_ROOT / "config" / "evidence-taxonomy" / "v1"
CONFIG_JSON = SKILL_ROOT / "config" / "evidence-index.json"
CONFIG_YAML = SKILL_ROOT / "config" / "evidence-index.yaml"

SECRET_PATH = re.compile(r"(?i)((?:^|/)\.env(?:\.[^/]+)?$|credentials\.json|id_rsa|\.pem$)")
SECRET_LINE = re.compile(
    r"(?i)^\s*(export\s+)?[A-Z0-9_]*(PASSWORD|SECRET|TOKEN|API_KEY|ACCESS_KEY)[A-Z0-9_]*\s*="
)
TICKET_RE = re.compile(r"\b([A-Z]{2,10}-\d+)\b")


# ---------------------------------------------------------------------------
# Tiny YAML subset (lists/dicts of scalars) - avoid PyYAML dependency
# ---------------------------------------------------------------------------

def load_simple_yaml(text: str) -> dict:
    """Parse the small taxonomy/config YAML files used by this skill."""
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    except Exception:
        pass
    # Fallback: JSON-compatible subset after light transforms is too fragile.
    # Prefer a minimal line parser for our known shapes.
    return _parse_minimal_yaml(text)


def _parse_minimal_yaml(text: str) -> Any:
    """Indent-based parser for the flat YAML shapes in this skill."""
    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        lines.append(raw.rstrip())

    def parse_block(start: int, indent: int) -> tuple[Any, int]:
        if start >= len(lines):
            return {}, start
        # Detect list vs mapping at this indent
        first = lines[start]
        cur_indent = len(first) - len(first.lstrip(" "))
        if cur_indent < indent:
            return {}, start
        if first.lstrip().startswith("- "):
            items = []
            i = start
            while i < len(lines):
                line = lines[i]
                ind = len(line) - len(line.lstrip(" "))
                if ind < indent:
                    break
                if ind > indent and not line.lstrip().startswith("- "):
                    break
                if ind != indent or not line.lstrip().startswith("- "):
                    if ind > indent:
                        # nested under previous item - shouldn't happen for our files often
                        break
                    break
                body = line.lstrip()[2:]
                if ":" in body and not body.startswith("{"):
                    # inline mapping item: "- id: foo"
                    key, _, val = body.partition(":")
                    nested, i2 = parse_block(i + 1, indent + 2)
                    obj = {key.strip(): _scalar(val.strip())}
                    if isinstance(nested, dict):
                        obj.update(nested)
                    items.append(obj)
                    i = i2
                elif body.endswith(":") and not body.startswith("["):
                    key = body[:-1].strip()
                    nested, i2 = parse_block(i + 1, indent + 2)
                    items.append({key: nested})
                    i = i2
                else:
                    items.append(_scalar(body))
                    i += 1
            return items, i

        mapping: dict[str, Any] = {}
        i = start
        while i < len(lines):
            line = lines[i]
            ind = len(line) - len(line.lstrip(" "))
            if ind < indent:
                break
            if ind > indent:
                break
            if line.lstrip().startswith("- "):
                break
            if ":" not in line:
                i += 1
                continue
            key, _, rest = line.lstrip().partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest in ("", "|", ">"):
                # nested
                nested, i2 = parse_block(i + 1, indent + 2)
                # check if next sibling is a list at indent+2 starting with -
                mapping[key] = nested
                i = i2
            elif rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1].strip()
                if not inner:
                    mapping[key] = []
                else:
                    mapping[key] = [_scalar(x.strip()) for x in inner.split(",")]
                i += 1
            else:
                mapping[key] = _scalar(rest)
                i += 1
        return mapping, i

    root, _ = parse_block(0, 0)
    return root if root is not None else {}


def _scalar(s: str) -> Any:
    if s in ("true", "True"):
        return True
    if s in ("false", "False"):
        return False
    if s in ("null", "Null", "~", ""):
        return None
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


def load_config() -> dict:
    if CONFIG_JSON.exists():
        cfg = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
    elif CONFIG_YAML.exists():
        cfg = load_simple_yaml(CONFIG_YAML.read_text(encoding="utf-8"))
    else:
        die("missing config/evidence-index.json")
    if not isinstance(cfg, dict):
        die("invalid evidence-index config")
    return cfg


def path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def die(msg: str, code: int = 1) -> None:
    print(f"evidence-index: ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def info(msg: str) -> None:
    print(f"evidence-index: {msg}", file=sys.stderr)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(obj: Any) -> str:
    """Stable JSON for hashing: sorted keys, no trailing whitespace variance."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash_payload(rec: dict) -> dict:
    """Normalize fields that may change after publication before hashing.

    Content identity is fixed at candidate time. Lifecycle and human_publication
    may be updated on the canonical copy without changing record_hash.
    """
    payload = dict(rec)
    payload["record_hash"] = ""
    payload["lifecycle"] = "candidate"
    payload["human_publication"] = None
    return payload


def compute_record_hash(rec: dict) -> str:
    return sha256_text(canonical_json(content_hash_payload(rec)))


def expand_path(p: str) -> Path:
    return Path(os.path.expanduser(p)).resolve()


def evidence_repo_path(cfg: dict) -> Path | None:
    env = os.environ.get("YONKO_EVIDENCE_REPO", "").strip()
    if env:
        return expand_path(env)
    raw = (cfg.get("evidence_repo") or "").strip()
    if not raw:
        return None
    return expand_path(raw)


def cache_root(cfg: dict) -> Path:
    return expand_path(cfg.get("cache_root") or "~/.cursor/yonko-evidence-cache")


def _load_tax_file(stem: str) -> dict:
    jp = TAXONOMY / f"{stem}.json"
    yp = TAXONOMY / f"{stem}.yaml"
    if jp.exists():
        data = json.loads(jp.read_text(encoding="utf-8"))
    elif yp.exists():
        data = load_simple_yaml(yp.read_text(encoding="utf-8"))
    else:
        die(f"missing taxonomy file: {stem}.json")
    if not isinstance(data, dict):
        die(f"invalid taxonomy file: {stem}")
    return data


def load_taxonomy() -> dict:
    concepts = _load_tax_file("concepts")
    aliases = _load_tax_file("aliases")
    path_rules = _load_tax_file("path-rules")
    patterns = _load_tax_file("finding-patterns")
    return {
        "version": concepts.get("version") or "1.0.0",
        "concepts": concepts.get("concepts") or [],
        "aliases": aliases,
        "path_rules": path_rules.get("rules") or [],
        "finding_patterns": patterns.get("patterns") or {},
    }


# ---------------------------------------------------------------------------
# Structural validation (stdlib - mirror validate-artifact style)
# ---------------------------------------------------------------------------

def validate_record_struct(rec: dict) -> list[str]:
    problems = []
    required = [
        "schema_version", "evidence_id", "activity_id", "session_id", "review_type",
        "lifecycle", "final_status", "completed_at", "owner", "tickets", "concepts",
        "findings", "engineering_confidence", "source_evidence", "artifacts", "record_hash",
    ]
    for f in required:
        if f not in rec or rec[f] in (None, ""):
            problems.append(f"missing {f}")
    if rec.get("schema_version") not in (None, "1.0.0"):
        problems.append("schema_version must be 1.0.0")
    if rec.get("review_type") not in (None, "implementation", "plan", "document"):
        problems.append("invalid review_type")
    if rec.get("lifecycle") not in (None, "candidate", "canonical", "superseded"):
        problems.append("invalid lifecycle")
    if not isinstance(rec.get("tickets"), list) or not rec.get("tickets"):
        problems.append("tickets must be a non-empty array")
    if not isinstance(rec.get("artifacts"), list) or not rec.get("artifacts"):
        problems.append("artifacts must be a non-empty array")
    findings = rec.get("findings")
    if not isinstance(findings, dict):
        problems.append("findings must be an object")
    else:
        for k in ("accepted", "validated", "rejected", "unresolved"):
            if k not in findings or not isinstance(findings[k], list):
                problems.append(f"findings.{k} must be an array")
    conf = rec.get("engineering_confidence")
    if not isinstance(conf, dict) or conf.get("level") not in ("high", "medium", "low"):
        problems.append("engineering_confidence.level must be high|medium|low")
    if rec.get("review_type") == "document" and not rec.get("artifact_type"):
        problems.append("document records require artifact_type")
    return problems


def validate_event_struct(ev: dict) -> list[str]:
    problems = []
    for f in ("schema_version", "event_id", "evidence_id", "event_type", "timestamp",
              "actor", "payload", "event_hash"):
        if f not in ev or ev[f] in (None, ""):
            problems.append(f"missing {f}")
    allowed = {
        "canonicalized", "implementation_merged", "deployment_started",
        "deployment_completed", "rollback_performed", "incident_linked",
        "assumption_invalidated", "risk_realized", "outcome_recorded", "record_superseded",
    }
    et = ev.get("event_type")
    if et is not None and et not in allowed:
        problems.append("invalid event_type")
    if "previous_event_hash" not in ev:
        problems.append("missing previous_event_hash")
    return problems


def secret_scan_text(text: str, path_hint: str = "") -> list[str]:
    notes = []
    if SECRET_PATH.search(path_hint or ""):
        notes.append(f"secret-looking path: {path_hint}")
    if re.search(r"(?i)(?:^|/)\.env(?:\.|\s|$)", text) and re.search(
        r"(?i)(PASSWORD|SECRET|TOKEN)\s*=\s*\S+", text
    ):
        notes.append("possible dotenv leak in content")
    for i, line in enumerate(text.splitlines(), 1):
        if SECRET_LINE.search(line):
            notes.append(f"secret-looking assignment at line {i}")
    return notes


def secret_scan_tree(root: Path) -> list[str]:
    problems = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if SECRET_PATH.search(str(p)):
            problems.append(f"forbidden path: {p}")
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        problems.extend(f"{p.name}: {n}" for n in secret_scan_text(text, str(p)))
    return problems


# ---------------------------------------------------------------------------
# Concept / finding-pattern derivation
# ---------------------------------------------------------------------------

def derive_concepts(blobs: list[str], paths: list[str], taxonomy: dict) -> list[dict]:
    out: dict[str, dict] = {}
    version = taxonomy["version"]
    aliases = (taxonomy.get("aliases") or {}).get("concept_aliases") or {}
    known = set(taxonomy.get("concepts") or [])

    def add(value: str, derivation: str, source: str) -> None:
        value = aliases.get(value, value)
        value = value.replace("_", "-").lower()
        if value not in known:
            return
        if value not in out:
            out[value] = {
                "value": value,
                "derivation": derivation,
                "source_reference": source,
                "rule_version": version,
            }

    blob = "\n".join(blobs)
    for raw, controlled in aliases.items():
        if re.search(rf"(?i)\b{re.escape(raw)}\b", blob):
            add(controlled, "explicit", f"alias:{raw}")

    for rule in taxonomy.get("path_rules") or []:
        pat = rule.get("pattern")
        if not pat:
            continue
        try:
            rx = re.compile(pat)
        except re.error:
            continue
        joined = "\n".join(paths)
        if rx.search(joined) or rx.search(blob):
            for c in rule.get("concepts") or []:
                add(c, "path_rule", f"rule:{rule.get('id')}")

    return [out[k] for k in sorted(out)]


def derive_technologies(blobs: list[str], taxonomy: dict) -> list[str]:
    aliases = (taxonomy.get("aliases") or {}).get("technology_aliases") or {}
    found = set()
    blob = "\n".join(blobs)
    for raw, canon in aliases.items():
        if re.search(rf"(?i)\b{re.escape(raw)}\b", blob):
            found.add(canon)
    return sorted(found)


def assign_finding_pattern(finding: dict, taxonomy: dict) -> str | None:
    patterns = taxonomy.get("finding_patterns") or {}
    cat = (finding.get("category") or "").lower()
    title = finding.get("title") or ""
    claim = finding.get("claim") or ""
    text = f"{title}\n{claim}"
    for key, spec in patterns.items():
        cats = [c.lower() for c in (spec.get("match_categories") or [])]
        if cats and cat not in cats:
            continue
        rx = spec.get("match_title_regex")
        if rx and re.search(rx, text):
            return key
    return None


# ---------------------------------------------------------------------------
# Candidate extraction
# ---------------------------------------------------------------------------

HANDOFF = {
    "plan": ["PLAN.approved.md"],
    "document": {
        "pap": ["PAP.final.md"],
        "prd": ["PRD.final.md"],
        "adr": ["ADR.final.md"],
        "design": ["DESIGN.final.md"],
    },
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_session(session_dir: Path) -> dict:
    return read_json(session_dir / "session.json")


def extract_tickets(*texts: str, explicit: list[str] | None = None) -> list[str]:
    found = set(explicit or [])
    for t in texts:
        found.update(TICKET_RE.findall(t or ""))
    return sorted(found)


def load_findings(session_dir: Path) -> list[dict]:
    for name in ("findings.json", "findings.raw.json"):
        p = session_dir / name
        if not p.exists():
            continue
        data = read_json(p)
        if isinstance(data, dict):
            for key in ("findings", "plan_findings", "document_findings"):
                if isinstance(data.get(key), list):
                    return data[key]
            if isinstance(data.get("accepted"), list):
                # already split
                out = []
                for bucket in ("accepted", "validated", "rejected", "unresolved", "dropped", "held"):
                    out.extend(data.get(bucket) or [])
                return out
        elif isinstance(data, list):
            return data
    return []


def load_verifications(session_dir: Path) -> dict[str, str]:
    """Map finding_id -> verdict."""
    mapping: dict[str, str] = {}
    for name in ("verification.json", "verifications.json"):
        p = session_dir / name
        if not p.exists():
            continue
        data = read_json(p)
        items = data if isinstance(data, list) else data.get("verifications") or (
            [data] if isinstance(data, dict) and "verdict" in data else []
        )
        for item in items:
            if not isinstance(item, dict):
                continue
            verdict = item.get("verdict")
            for fid in item.get("finding_ids") or []:
                mapping[str(fid)] = verdict
    return mapping


def split_findings(
    findings: list[dict],
    verifications: dict[str, str],
    taxonomy: dict,
) -> dict:
    accepted, validated, rejected, unresolved = [], [], [], []
    for f in findings:
        if not isinstance(f, dict):
            continue
        entry = {
            "id": f.get("id"),
            "reviewer": f.get("reviewer"),
            "category": f.get("category"),
            "severity": f.get("severity"),
            "title": f.get("title"),
            "claim": f.get("claim"),
            "verification": verifications.get(str(f.get("id"))),
            "finding_pattern": assign_finding_pattern(f, taxonomy),
            "locus_repository": (f.get("locus") or {}).get("repository") if isinstance(f.get("locus"), dict) else None,
            "locus_path": (f.get("locus") or {}).get("path") if isinstance(f.get("locus"), dict) else None,
        }
        status = (f.get("status") or f.get("adjudication") or "").lower()
        ver = entry["verification"]
        if status in ("dropped", "drop", "rejected") or ver == "rejected":
            rejected.append(entry)
        elif ver == "confirmed" or status in ("accepted", "apply", "applied"):
            validated.append(entry)
            accepted.append(entry)
        elif status in ("held", "hold", "unresolved"):
            unresolved.append(entry)
        else:
            # Default: treat present findings as accepted hypotheses until verified
            accepted.append(entry)
            if ver == "inconclusive":
                unresolved.append(entry)
    return {
        "accepted": accepted,
        "validated": validated,
        "rejected": rejected,
        "unresolved": unresolved,
    }


def collect_paths_and_blobs(session_dir: Path, session: dict) -> tuple[list[str], list[str]]:
    paths: list[str] = []
    blobs: list[str] = []
    evid = session_dir / "evidence"
    for name in ("plan.md", "document.md", "recon.md", "DIFF_MAP.txt", "REPO_CONTEXT.txt"):
        p = evid / name
        if p.exists():
            blobs.append(p.read_text(encoding="utf-8", errors="replace"))
            paths.append(name)
    for sub in ("sources",):
        d = evid / sub
        if d.is_dir():
            for p in sorted(d.glob("*")):
                if p.is_file():
                    blobs.append(p.read_text(encoding="utf-8", errors="replace"))
                    paths.append(str(p.relative_to(evid)))
    repos_json = evid / "repos.json"
    if repos_json.exists():
        repos = read_json(repos_json).get("repos") or []
        for r in repos:
            paths.append(r.get("label") or r.get("path") or "")
            patch = evid / r.get("patch", "")
            if patch.exists():
                text = patch.read_text(encoding="utf-8", errors="replace")
                blobs.append(text)
                for line in text.splitlines():
                    if line.startswith("+++ b/") or line.startswith("diff --git"):
                        paths.append(line)
    for art in ("PLAN.approved.md", "PLAN.revised.md", "PAP.final.md", "PRD.final.md",
                "ADR.final.md", "DESIGN.final.md", "DOCKET.md"):
        p = session_dir / art
        if p.exists():
            blobs.append(p.read_text(encoding="utf-8", errors="replace"))
            paths.append(art)
    return paths, blobs


def resolve_final_artifacts(session_dir: Path, session: dict) -> list[Path]:
    rt = session.get("review_type") or "implementation"
    if rt == "plan":
        names = HANDOFF["plan"]
    elif rt == "document":
        names = HANDOFF["document"].get(session.get("artifact_type") or "", [])
    else:
        # implementation: prefer explicit final.patch; else concatenate evidence patches
        p = session_dir / "final.patch"
        if p.exists():
            return [p]
        evid = session_dir / "evidence"
        repos_json = evid / "repos.json"
        patches = []
        if repos_json.exists():
            for r in read_json(repos_json).get("repos") or []:
                pp = evid / r.get("patch", "")
                if pp.exists():
                    patches.append(pp)
        return patches
    out = []
    for n in names:
        p = session_dir / n
        if p.exists():
            out.append(p)
    return out


def gate_check(
    session: dict,
    session_dir: Path,
    artifacts: list[Path],
    tickets: list[str],
    owner: str,
    final_status: str,
) -> list[str]:
    problems = []
    rt = session.get("review_type") or "implementation"
    status = session.get("status")
    if status not in ("finalized",) and not (session_dir / "SUMMARY.md").exists():
        problems.append("session is not finalized (missing SUMMARY.md / status!=finalized)")
    if not artifacts:
        problems.append("no final artifact found for review type")
    if not tickets:
        problems.append("no ticket identifiers (pass --ticket or include TA-NNN in Docket/plan)")
    if not owner:
        problems.append("owner required (--owner)")
    if not final_status:
        problems.append("final_status required (--final-status)")

    if rt == "plan":
        if not (session_dir / "PLAN.approved.md").exists():
            problems.append("plan gate: PLAN.approved.md required")
        if not (session_dir / "evidence" / "plan-refs.json").exists():
            problems.append("plan gate: evidence/plan-refs.json required")
    elif rt == "document":
        at = session.get("artifact_type")
        expected = HANDOFF["document"].get(at or "", [])
        if not any((session_dir / n).exists() for n in expected):
            problems.append(f"document gate: final artifact required ({expected})")
    elif rt == "implementation":
        # verification result preferred
        has_verify = (session_dir / "verification.json").exists() or any(
            True for _ in []  # placeholder
        )
        events = session_dir / "events.jsonl"
        if events.exists():
            for line in events.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") in ("verification_completed", "scoped_verify"):
                    has_verify = True
        if not has_verify and final_status not in ("abandoned", "rejected", "adjourned"):
            problems.append(
                "implementation gate: verification/scoped_verify evidence required "
                "(or final-status abandoned|rejected|adjourned)"
            )
    return problems


def build_candidate(
    session_dir: Path,
    *,
    activity_id: str | None,
    owner: str,
    tickets: list[str],
    final_status: str,
    title: str | None,
    summary: str | None,
    decisions: list[str],
    assumptions: list[str],
    unresolved_risks: list[str],
    rollout: str | None,
    rollback: str | None,
    commit_refs: list[str],
    mr_refs: list[str],
    relationships: list[dict],
    informed_by: list[str],
    human_concepts: list[str],
) -> tuple[dict, Path, list[str]]:
    session = load_session(session_dir)
    taxonomy = load_taxonomy()
    cfg = load_config()
    rt = session.get("review_type") or "implementation"
    sid = session.get("session_id") or session_dir.name
    evidence_id = f"{sid}__{rt}"
    year = (session.get("started_at") or utc_now())[:4]
    if year.isdigit() is False:
        year = utc_now()[:4]

    paths, blobs = collect_paths_and_blobs(session_dir, session)
    docket = session_dir / "DOCKET.md"
    if docket.exists():
        blobs.append(docket.read_text(encoding="utf-8", errors="replace"))

    ticket_list = extract_tickets(*blobs, explicit=tickets)
    act = activity_id or (ticket_list[0] if ticket_list else None)
    if not act:
        die("activity_id required (pass --activity-id or ensure a ticket id exists)")

    artifacts_paths = resolve_final_artifacts(session_dir, session)
    problems = gate_check(session, session_dir, artifacts_paths, ticket_list, owner, final_status)
    if problems:
        die("candidate gates failed:\n  - " + "\n  - ".join(problems))

    # For implementation without final.patch, materialize a combined patch into candidate
    out_dir = session_dir / (cfg.get("candidate") or {}).get("dir_name", "evidence-candidate")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    art_dir = out_dir / "artifacts"
    art_dir.mkdir(parents=True)

    artifact_entries = []
    if rt == "implementation" and not (session_dir / "final.patch").exists():
        # stitch patches
        parts = []
        for p in artifacts_paths:
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
            if not parts[-1].endswith("\n"):
                parts.append("\n")
        combined = "".join(parts) if parts else "# empty patch\n"
        dest = art_dir / "final.patch"
        dest.write_text(combined, encoding="utf-8")
        artifact_entries.append({
            "name": "final.patch",
            "role": "final_patch",
            "sha256": sha256_file(dest),
            "bytes": dest.stat().st_size,
            "path": "artifacts/final.patch",
        })
    else:
        for p in artifacts_paths:
            dest = art_dir / p.name
            shutil.copy2(p, dest)
            role = "other"
            if p.name == "PLAN.approved.md":
                role = "plan_approved"
            elif p.name.endswith(".final.md"):
                role = "document_final"
            elif p.name == "final.patch":
                role = "final_patch"
            artifact_entries.append({
                "name": p.name,
                "role": role,
                "sha256": sha256_file(dest),
                "bytes": dest.stat().st_size,
                "path": f"artifacts/{p.name}",
            })

    findings_raw = load_findings(session_dir)
    verifications = load_verifications(session_dir)
    findings = split_findings(findings_raw, verifications, taxonomy)

    concepts = derive_concepts(blobs, paths, taxonomy)
    for hc in human_concepts:
        concepts.append({
            "value": hc.replace("_", "-").lower(),
            "derivation": "human_confirmed",
            "source_reference": "cli:--concept",
            "rule_version": taxonomy["version"],
        })
    # dedupe concepts by value preferring human_confirmed
    by_val = {}
    for c in concepts:
        prev = by_val.get(c["value"])
        if not prev or c["derivation"] == "human_confirmed":
            by_val[c["value"]] = c
    concepts = [by_val[k] for k in sorted(by_val)]

    technologies = derive_technologies(blobs, taxonomy)

    # repos / services
    repositories = []
    services = []
    evid = session_dir / "evidence"
    if (evid / "repos.json").exists():
        for r in read_json(evid / "repos.json").get("repos") or []:
            label = r.get("label") or Path(r.get("path", "")).name
            repositories.append(label)
            if "/" in label:
                services.append(label.split("/", 1)[-1])
            else:
                services.append(label)
    for refs_name in ("plan-refs.json", "doc-refs.json"):
        rp = evid / refs_name
        if not rp.exists():
            continue
        refs = read_json(rp)
        for key in ("repositories_named", "repositories_inspected"):
            for r in refs.get(key) or []:
                label = r.get("label") or Path(r.get("path", "")).name
                if label not in repositories:
                    repositories.append(label)

    risk = {}
    for risk_name in ("risk.json", "scope-risk.json"):
        rp = evid / risk_name
        if rp.exists():
            rj = read_json(rp)
            risk = {
                "band": rj.get("risk"),
                "basis": rj.get("risk_basis") or session.get("risk_basis"),
                "reasons": rj.get("reasons") or session.get("risk_reasons") or [],
            }
            break

    confidence = {}
    if (session_dir / "confidence.json").exists():
        confidence = read_json(session_dir / "confidence.json")
    else:
        confidence = {
            "level": session.get("engineering_confidence") or "medium",
            "source": "session",
            "mechanical": {},
            "chair_reasons": [],
        }

    reviewers = sorted({
        (f.get("reviewer") or "").lower()
        for f in findings_raw
        if isinstance(f, dict) and f.get("reviewer")
    } - {""})

    # Contracts / apis / events / tables from text (deterministic regex)
    blob = "\n".join(blobs)
    apis = sorted(set(re.findall(r"(?i)(/v\d+/[a-z0-9_\-/]+)", blob)))
    events = sorted(set(
        re.findall(r"(?i)\b([A-Z][A-Za-z0-9]+(?:Event|Message|DTO))\b", blob)
        + re.findall(r"(?i)\b((?:sns|sqs|kafka)[:/\-][a-z0-9_\-\.]+)\b", blob)
    ))
    tables = sorted(set(re.findall(r"(?i)\b(?:table|from|into|update)\s+([a-z][a-z0-9_]+)\b", blob)))[:40]
    contracts = sorted(set(re.findall(r"(?i)\b([a-z0-9\-]+-model(?:-[a-z0-9]+)?)\b", blob)))

    deployment_type = None
    if re.search(r"(?i)two.?phase|deploy order", blob):
        deployment_type = "two-phase"
    elif re.search(r"(?i)feature.?flag|unleash", blob):
        deployment_type = "feature-flag"
    elif re.search(r"(?i)flyway|migration", blob):
        deployment_type = "migration"
    elif len(repositories) > 1:
        deployment_type = "multi-service"
    elif repositories:
        deployment_type = "single-service"
    else:
        deployment_type = "none"

    packet_hash = session.get("packet_hash")
    findings_hash = None
    if (session_dir / "findings.json").exists():
        findings_hash = sha256_file(session_dir / "findings.json")
    verification_hash = None
    if (session_dir / "verification.json").exists():
        verification_hash = sha256_file(session_dir / "verification.json")

    record = {
        "schema_version": "1.0.0",
        "evidence_id": evidence_id,
        "activity_id": act,
        "session_id": sid,
        "review_type": rt,
        "artifact_type": session.get("artifact_type"),
        "document_mode": session.get("document_mode"),
        "lifecycle": "candidate",
        "final_status": final_status,
        "completed_at": session.get("finalized_at") or utc_now(),
        "owner": owner,
        "tickets": ticket_list,
        "title": title or f"{rt} {act}",
        "summary": summary or "",
        "repositories": sorted(set(repositories)),
        "services": sorted(set(services)),
        "apis": apis,
        "events": events,
        "database_tables": tables,
        "technologies": technologies,
        "architectural_patterns": [],
        "external_integrations": [
            t for t in technologies if t in ("Auth0", "Kafka", "SQS", "SNS", "OpenSearch", "Unleash")
        ],
        "contracts": contracts,
        "concepts": concepts,
        "risk": risk,
        "deployment_type": deployment_type,
        "commit_refs": commit_refs,
        "merge_request_refs": mr_refs,
        "reviewers": reviewers,
        "findings": findings,
        "decisions": decisions,
        "rollout_strategy": rollout,
        "rollback_strategy": rollback,
        "assumptions": assumptions,
        "unresolved_risks": unresolved_risks,
        "engineering_confidence": {
            "level": confidence.get("level") or "medium",
            "source": confidence.get("source"),
            "mechanical": confidence.get("mechanical") or {},
            "chair_reasons": confidence.get("chair_reasons") or [],
        },
        "source_evidence": {
            "session_path_relative": sid,
            "packet_hash": packet_hash,
            "packet_version": session.get("packet_version"),
            "risk_file": "evidence/risk.json" if (evid / "risk.json").exists() else (
                "evidence/scope-risk.json" if (evid / "scope-risk.json").exists() else None
            ),
            "findings_file_hash": findings_hash,
            "verification_file_hash": verification_hash,
        },
        "artifacts": artifact_entries,
        "relationships": relationships,
        "informed_by": informed_by,
        "taxonomy_version": taxonomy["version"],
        "human_publication": None,
        "record_hash": "",  # filled below
    }

    digest = compute_record_hash(record)
    record["record_hash"] = digest

    struct_problems = validate_record_struct(record)
    if struct_problems:
        die("record schema problems:\n  - " + "\n  - ".join(struct_problems))

    (out_dir / "record.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema_version": "1.0.0",
        "evidence_id": evidence_id,
        "activity_id": act,
        "lifecycle": "candidate",
        "record_hash": digest,
        "artifacts": artifact_entries,
        "created_at": utc_now(),
        "year": year,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "events.jsonl").write_text("", encoding="utf-8")
    (out_dir / "CANDIDATE.md").write_text(
        _candidate_summary_md(record, digest), encoding="utf-8"
    )

    scan = secret_scan_tree(out_dir)
    if scan:
        die("secret scan failed:\n  - " + "\n  - ".join(scan))

    return record, out_dir, []


def _candidate_summary_md(record: dict, digest: str) -> str:
    concepts = ", ".join(c["value"] for c in record.get("concepts") or []) or "none"
    return f"""# Evidence candidate

- Evidence id: `{record['evidence_id']}`
- Activity id: `{record['activity_id']}`
- Review type: {record['review_type']} ({record.get('artifact_type') or 'n/a'})
- Final status: {record['final_status']}
- Owner: {record['owner']}
- Tickets: {', '.join(record['tickets'])}
- Repositories: {', '.join(record.get('repositories') or []) or 'none'}
- Concepts: {concepts}
- Confidence: {record['engineering_confidence']['level']}
- Candidate hash (record_hash): `{digest}`

## Publish (local only - no commit/push)

```bash
scripts/evidence-index.py publish-local \\
  --session <session-dir> \\
  --candidate-hash {digest} \\
  --approved-by <your-handle>
```

Human approval is required. This command never commits or pushes.
"""


# ---------------------------------------------------------------------------
# Init repo / publish / events / rebuild / cache / query
# ---------------------------------------------------------------------------

def cmd_init_repo(args: argparse.Namespace) -> None:
    cfg = load_config()
    target = expand_path(args.path) if args.path else evidence_repo_path(cfg)
    if target is None:
        die("pass --path or set YONKO_EVIDENCE_REPO / config evidence_repo")
    if target.exists() and any(target.iterdir()) and not args.force:
        die(f"target exists and is not empty: {target} (pass --force to re-seed schemas only)")
    target.mkdir(parents=True, exist_ok=True)
    for sub in ("schema/v1", "taxonomy/v1", "records", "indexes/v1"):
        (target / sub).mkdir(parents=True, exist_ok=True)

    # Copy schemas and taxonomy from skill
    for src in CONTRACTS.glob("*.json"):
        shutil.copy2(src, target / "schema" / "v1" / src.name)
    for src in TAXONOMY.glob("*"):
        if src.suffix in (".yaml", ".json"):
            shutil.copy2(src, target / "taxonomy" / "v1" / src.name)

    readme = target / "README.md"
    if not readme.exists() or args.force:
        readme.write_text(
            """# Engineering Evidence Index

Institutional, Git-backed structured evidence of completed Yonko engineering work.

## Lifecycle

- `candidate` - local Yonko session staging only
- `canonical` - human-approved record in this repository checkout
- `superseded` - derived from append-only events / superseding records

## Safety

- Never store chat, chain-of-thought, or secrets
- Publication into this checkout is always an explicit human CLI action
- This adapter never runs `git commit` or `git push`

## Layout

See Yonko `DOCUMENTATION.md` (Evidence Index section) and `config/evidence-index.yaml`.
""",
            encoding="utf-8",
        )

    # empty index stubs
    empty = {"_meta": {"version": "1.0.0", "entries": {}}}
    for name in (
        "by-ticket", "by-repository", "by-service", "by-concept",
        "by-artifact-type", "by-contract", "by-technology", "by-finding-pattern",
    ):
        p = target / "indexes" / "v1" / f"{name}.json"
        if not p.exists() or args.force:
            p.write_text(json.dumps(empty, indent=2) + "\n", encoding="utf-8")

    # Optional local git init WITHOUT remote - never commit
    if args.git_init and not (target / ".git").exists():
        import subprocess
        subprocess.run(["git", "init"], cwd=target, check=True, capture_output=True)
        info(f"git init at {target} (no commit, no remote)")

    info(f"evidence repo ready at {target}")
    print(json.dumps({"ok": True, "path": str(target)}, indent=2))


def cmd_candidate(args: argparse.Namespace) -> None:
    session_dir = expand_path(args.session)
    if not (session_dir / "session.json").exists():
        die(f"not a yonko session: {session_dir}")
    relationships = []
    for rel in args.relationship or []:
        # type:target
        if ":" not in rel:
            die(f"relationship must be type:target_evidence_id, got {rel}")
        typ, _, target = rel.partition(":")
        relationships.append({"type": typ, "target_evidence_id": target})
    record, out_dir, _ = build_candidate(
        session_dir,
        activity_id=args.activity_id,
        owner=args.owner,
        tickets=args.ticket or [],
        final_status=args.final_status,
        title=args.title,
        summary=args.summary,
        decisions=args.decision or [],
        assumptions=args.assumption or [],
        unresolved_risks=args.unresolved_risk or [],
        rollout=args.rollout,
        rollback=args.rollback,
        commit_refs=args.commit or [],
        mr_refs=args.mr or [],
        relationships=relationships,
        informed_by=args.informed_by or [],
        human_concepts=args.concept or [],
    )
    print(json.dumps({
        "ok": True,
        "evidence_id": record["evidence_id"],
        "candidate_hash": record["record_hash"],
        "path": str(out_dir),
    }, indent=2))


def _load_candidate_dir(path: Path) -> tuple[dict, dict]:
    rec = read_json(path / "record.json")
    man = read_json(path / "manifest.json")
    return rec, man


def cmd_validate(args: argparse.Namespace) -> None:
    path = expand_path(args.path)
    rec, man = _load_candidate_dir(path)
    problems = validate_record_struct(rec)
    # verify hash
    stored = rec.get("record_hash")
    actual = compute_record_hash(rec)
    if stored != actual:
        problems.append(f"record_hash mismatch: stored={stored} actual={actual}")
    if man.get("record_hash") != stored:
        problems.append("manifest.record_hash does not match record")
    # artifact hashes
    for art in rec.get("artifacts") or []:
        ap = path / art["path"]
        if not ap.exists():
            problems.append(f"missing artifact: {art['path']}")
            continue
        if sha256_file(ap) != art["sha256"]:
            problems.append(f"artifact hash mismatch: {art['name']}")
    problems.extend(secret_scan_tree(path))
    # events
    events_path = path / "events.jsonl"
    if events_path.exists():
        prev = None
        for i, line in enumerate(events_path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            ev = json.loads(line)
            problems.extend(f"event[{i}]: {p}" for p in validate_event_struct(ev))
            if ev.get("previous_event_hash") != prev:
                problems.append(f"event[{i}]: broken chain (previous_event_hash)")
            # recompute hash
            body = {k: v for k, v in ev.items() if k != "event_hash"}
            if sha256_text(canonical_json(body)) != ev.get("event_hash"):
                problems.append(f"event[{i}]: event_hash mismatch")
            prev = ev.get("event_hash")
    ok = len(problems) == 0
    print(json.dumps({"ok": ok, "problems": problems}, indent=2))
    raise SystemExit(0 if ok else 1)


def cmd_publish_local(args: argparse.Namespace) -> None:
    cfg = load_config()
    repo = evidence_repo_path(cfg)
    if repo is None or not repo.exists():
        die("canonical evidence repo not configured or missing "
            "(set YONKO_EVIDENCE_REPO and run init-repo)")

    session_dir = expand_path(args.session)
    cand = session_dir / (cfg.get("candidate") or {}).get("dir_name", "evidence-candidate")
    if not cand.exists():
        die(f"candidate not found: {cand} (run candidate first)")

    rec, man = _load_candidate_dir(cand)
    # re-validate
    class NS:
        path = str(cand)
    # inline validate
    problems = validate_record_struct(rec)
    stored = rec.get("record_hash")
    actual = compute_record_hash(rec)
    if stored != actual:
        problems.append("record_hash mismatch")
    if args.candidate_hash != stored:
        problems.append(
            f"candidate-hash does not match (got {args.candidate_hash}, expected {stored})"
        )
    if not args.approved_by:
        problems.append("--approved-by required")
    problems.extend(secret_scan_tree(cand))
    if problems:
        die("publish refused:\n  - " + "\n  - ".join(problems))

    year = (rec.get("completed_at") or utc_now())[:4]
    dest = repo / "records" / year / rec["evidence_id"]
    if dest.exists():
        existing = read_json(dest / "record.json")
        if existing.get("record_hash") != stored:
            die(f"evidence_id already exists with different content: {dest}")
        info("identical record already present; refreshing indexes only")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(cand, dest)

    # Update lifecycle + human_publication on the CANONICAL copy only
    crec = read_json(dest / "record.json")
    crec["lifecycle"] = "canonical"
    crec["human_publication"] = {
        "approved_by": args.approved_by,
        "approved_at": utc_now(),
        "candidate_hash": stored,
        "note": args.note or "",
    }
    # Re-hash after lifecycle change? Plan says base record is immutable after
    # canonicalization. Store publication metadata WITHOUT changing record_hash
    # of the candidate content - keep original record_hash as content identity,
    # and put publication in a side field. The candidate hash remains the content hash.
    # We intentionally do NOT recompute record_hash so content identity is stable.
    (dest / "record.json").write_text(
        json.dumps(crec, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    cman = read_json(dest / "manifest.json")
    cman["lifecycle"] = "canonical"
    cman["published_at"] = utc_now()
    cman["approved_by"] = args.approved_by
    (dest / "manifest.json").write_text(
        json.dumps(cman, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    # Append canonicalized event
    _append_event_to_record(
        dest,
        event_type="canonicalized",
        actor=args.approved_by,
        source_reference=None,
        payload={"candidate_hash": stored, "note": args.note or ""},
    )

    # Remove candidate-only files that must not live in canonical
    for junk in ("CANDIDATE.md",):
        p = dest / junk
        if p.exists():
            p.unlink()

    rebuild_indexes(repo)
    refresh_cache(cfg, repo)

    print(json.dumps({
        "ok": True,
        "evidence_id": rec["evidence_id"],
        "canonical_path": str(dest),
        "note": "published locally only - no git commit or push was performed",
        "next_optional": (
            "Optional continuous improvement (suggest-only): "
            "scripts/continuous-improvement.py analyze "
            "or /yonko improve"
        ),
    }, indent=2))
    info("Optional next: /yonko improve (pattern analysis - suggests only, never rewrites protocol)")


def _append_event_to_record(
    record_dir: Path,
    *,
    event_type: str,
    actor: str,
    source_reference: str | None,
    payload: dict,
) -> dict:
    events_path = record_dir / "events.jsonl"
    prev_hash = None
    if events_path.exists():
        lines = [ln for ln in events_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            prev = json.loads(lines[-1])
            prev_hash = prev.get("event_hash")
    rec = read_json(record_dir / "record.json")
    event_id = f"{rec['evidence_id']}__{event_type}__{utc_now().replace(':', '')}"
    body = {
        "schema_version": "1.0.0",
        "event_id": event_id,
        "evidence_id": rec["evidence_id"],
        "event_type": event_type,
        "timestamp": utc_now(),
        "actor": actor,
        "source_reference": source_reference,
        "payload": payload,
        "previous_event_hash": prev_hash,
    }
    body["event_hash"] = sha256_text(canonical_json(body))
    problems = validate_event_struct(body)
    if problems:
        die("event invalid:\n  - " + "\n  - ".join(problems))
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(body, sort_keys=True) + "\n")
    return body


def cmd_append_event(args: argparse.Namespace) -> None:
    cfg = load_config()
    repo = evidence_repo_path(cfg)
    if repo is None:
        die("YONKO_EVIDENCE_REPO not set")
    # find record
    matches = list((repo / "records").glob(f"*/{args.evidence_id}"))
    if not matches:
        die(f"record not found: {args.evidence_id}")
    dest = matches[0]
    payload = {}
    if args.payload_json:
        payload = json.loads(args.payload_json)
    ev = _append_event_to_record(
        dest,
        event_type=args.type,
        actor=args.actor,
        source_reference=args.source_reference,
        payload=payload,
    )
    if args.type == "record_superseded":
        # mark lifecycle on a derived view file only - do not rewrite record_hash
        view = {
            "evidence_id": args.evidence_id,
            "lifecycle": "superseded",
            "updated_at": utc_now(),
            "from_event": ev["event_id"],
        }
        (dest / "lifecycle.derived.json").write_text(
            json.dumps(view, indent=2) + "\n", encoding="utf-8"
        )
    rebuild_indexes(repo)
    refresh_cache(cfg, repo)
    print(json.dumps({"ok": True, "event": ev}, indent=2))


def derived_lifecycle(record_dir: Path, rec: dict) -> str:
    derived = record_dir / "lifecycle.derived.json"
    if derived.exists():
        return read_json(derived).get("lifecycle") or rec.get("lifecycle")
    # scan events for record_superseded
    events_path = record_dir / "events.jsonl"
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("event_type") == "record_superseded":
                return "superseded"
    return rec.get("lifecycle") or "canonical"


def rebuild_indexes(repo: Path) -> dict:
    indexes = {
        "by-ticket": defaultdict(list),
        "by-repository": defaultdict(list),
        "by-service": defaultdict(list),
        "by-concept": defaultdict(list),
        "by-artifact-type": defaultdict(list),
        "by-contract": defaultdict(list),
        "by-technology": defaultdict(list),
        "by-finding-pattern": defaultdict(list),
    }
    record_ids = []
    records_root = repo / "records"
    if records_root.exists():
        for year_dir in sorted(records_root.iterdir()):
            if not year_dir.is_dir():
                continue
            for rec_dir in sorted(year_dir.iterdir()):
                if not (rec_dir / "record.json").exists():
                    continue
                rec = read_json(rec_dir / "record.json")
                eid = rec["evidence_id"]
                record_ids.append(eid)
                life = derived_lifecycle(rec_dir, rec)
                meta = {
                    "evidence_id": eid,
                    "activity_id": rec.get("activity_id"),
                    "review_type": rec.get("review_type"),
                    "artifact_type": rec.get("artifact_type"),
                    "lifecycle": life,
                    "final_status": rec.get("final_status"),
                    "owner": rec.get("owner"),
                    "completed_at": rec.get("completed_at"),
                    "path": str(rec_dir.relative_to(repo)),
                    "confidence": (rec.get("engineering_confidence") or {}).get("level"),
                    "title": rec.get("title"),
                }
                for t in rec.get("tickets") or []:
                    indexes["by-ticket"][t].append(meta)
                for r in rec.get("repositories") or []:
                    indexes["by-repository"][r].append(meta)
                for s in rec.get("services") or []:
                    indexes["by-service"][s].append(meta)
                for c in rec.get("concepts") or []:
                    indexes["by-concept"][c.get("value")].append(meta)
                at = rec.get("artifact_type") or rec.get("review_type")
                if at:
                    indexes["by-artifact-type"][at].append(meta)
                for c in rec.get("contracts") or []:
                    indexes["by-contract"][c].append(meta)
                for t in rec.get("technologies") or []:
                    indexes["by-technology"][t].append(meta)
                for bucket in ("accepted", "validated", "rejected", "unresolved"):
                    for f in (rec.get("findings") or {}).get(bucket) or []:
                        fp = f.get("finding_pattern")
                        if fp:
                            indexes["by-finding-pattern"][fp].append({
                                **meta,
                                "finding_id": f.get("id"),
                                "finding_bucket": bucket,
                                "severity": f.get("severity"),
                                "title": f.get("title"),
                            })

    out_dir = repo / "indexes" / "v1"
    out_dir.mkdir(parents=True, exist_ok=True)
    index_meta = {}
    hash_parts = []
    for name, data in sorted(indexes.items()):
        # stable sort lists
        serializable = {
            "_meta": {"version": "1.0.0"},
            "entries": {
                k: sorted(v, key=lambda x: x.get("evidence_id") or "")
                for k, v in sorted(data.items())
            },
        }
        text = json.dumps(serializable, indent=2, sort_keys=True) + "\n"
        path = out_dir / f"{name}.json"
        path.write_text(text, encoding="utf-8")
        digest = sha256_text(text)
        hash_parts.append(f"{name}:{digest}")
        index_meta[name] = {
            "path": f"indexes/v1/{name}.json",
            "sha256": digest,
            "entry_count": len(serializable["entries"]),
        }

    manifest = {
        "schema_version": "1.0.0",
        "built_at": utc_now(),
        "record_count": len(record_ids),
        "taxonomy_version": load_taxonomy()["version"],
        "scoring_version": (load_config().get("scoring_version") or "1.0.0"),
        "index_hash": sha256_text("\n".join(hash_parts)),
        "indexes": index_meta,
        "record_ids": sorted(record_ids),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    info(f"rebuilt indexes: {len(record_ids)} records")
    return manifest


def cmd_rebuild(args: argparse.Namespace) -> None:
    cfg = load_config()
    repo = expand_path(args.path) if args.path else evidence_repo_path(cfg)
    if repo is None or not repo.exists():
        die("evidence repo not found")
    manifest = rebuild_indexes(repo)
    print(json.dumps({"ok": True, "manifest": manifest}, indent=2))


def refresh_cache(cfg: dict, repo: Path) -> Path:
    cache = cache_root(cfg)
    if cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True)
    # copy indexes + thin record pointers
    src_idx = repo / "indexes" / "v1"
    dst_idx = cache / "indexes" / "v1"
    shutil.copytree(src_idx, dst_idx)
    # pointer file
    (cache / "SOURCE.json").write_text(
        json.dumps({
            "evidence_repo": str(repo),
            "refreshed_at": utc_now(),
            "index_hash": read_json(src_idx / "manifest.json").get("index_hash"),
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    # optional: symlink records for artifact access (relative copy of paths only)
    records_map = {}
    for year_dir in sorted((repo / "records").glob("*")):
        if not year_dir.is_dir():
            continue
        for rec_dir in year_dir.iterdir():
            if (rec_dir / "record.json").exists():
                records_map[rec_dir.name] = str(rec_dir)
    (cache / "records-map.json").write_text(
        json.dumps(records_map, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    info(f"cache refreshed at {cache}")
    return cache


def cmd_refresh_cache(args: argparse.Namespace) -> None:
    cfg = load_config()
    repo = evidence_repo_path(cfg)
    if repo is None or not repo.exists():
        die("evidence repo not found")
    cache = refresh_cache(cfg, repo)
    print(json.dumps({"ok": True, "cache": str(cache)}, indent=2))


def load_index_entries(root: Path, name: str) -> dict:
    p = root / "indexes" / "v1" / f"{name}.json"
    if not p.exists():
        return {}
    return (read_json(p).get("entries") or {})


def resolve_query_root(cfg: dict, prefer_cache: bool) -> Path:
    cache = cache_root(cfg)
    if prefer_cache and (cache / "indexes" / "v1" / "manifest.json").exists():
        return cache
    repo = evidence_repo_path(cfg)
    if repo and (repo / "indexes" / "v1" / "manifest.json").exists():
        return repo
    die("no indexes found - run publish-local/rebuild and refresh-cache")


def cmd_query(args: argparse.Namespace) -> None:
    cfg = load_config()
    root = resolve_query_root(cfg, prefer_cache=not args.from_repo)
    weights = ((cfg.get("retrieval") or {}).get("weights") or {})
    min_score = float((cfg.get("retrieval") or {}).get("min_score") or 3)
    strong_fields = set((cfg.get("retrieval") or {}).get("strong_fields") or [])
    min_concept = int((cfg.get("retrieval") or {}).get("min_concept_overlap") or 2)

    # Collect filter posting lists
    filters = []
    if args.ticket:
        filters.append(("ticket", args.ticket, load_index_entries(root, "by-ticket").get(args.ticket, [])))
    if args.repository:
        filters.append(("repository", args.repository, load_index_entries(root, "by-repository").get(args.repository, [])))
    if args.service:
        filters.append(("service", args.service, load_index_entries(root, "by-service").get(args.service, [])))
    if args.concept:
        filters.append(("concept", args.concept, load_index_entries(root, "by-concept").get(args.concept, [])))
    if args.artifact_type:
        filters.append(("artifact_type", args.artifact_type, load_index_entries(root, "by-artifact-type").get(args.artifact_type, [])))
    if args.contract:
        filters.append(("contract", args.contract, load_index_entries(root, "by-contract").get(args.contract, [])))
    if args.technology:
        filters.append(("technology", args.technology, load_index_entries(root, "by-technology").get(args.technology, [])))
    if args.finding_pattern:
        filters.append(("finding_pattern", args.finding_pattern, load_index_entries(root, "by-finding-pattern").get(args.finding_pattern, [])))

    # Similarity mode: multiple soft terms
    soft_terms: list[tuple[str, str]] = []
    for api in args.api or []:
        soft_terms.append(("api", api))
    for ev in args.event or []:
        soft_terms.append(("event", ev))
    for tb in args.table or []:
        soft_terms.append(("database_table", tb))
    for c in args.like_concept or []:
        soft_terms.append(("concept", c))
    for r in args.like_repository or []:
        soft_terms.append(("repository", r))
    for s in args.like_service or []:
        soft_terms.append(("service", s))

    # Build candidate universe
    by_id: dict[str, dict] = {}
    contributions: dict[str, list[dict]] = defaultdict(list)

    def add_hits(field: str, value: str, hits: list[dict], weight_key: str) -> None:
        w = float(weights.get(weight_key, weights.get(field, 1.0)))
        for h in hits:
            eid = h["evidence_id"]
            by_id[eid] = h
            contributions[eid].append({
                "field": field,
                "value": value,
                "weight": w,
            })

    # Hard filters: if present, start from intersection
    if filters and not args.similar:
        sets = []
        for field, value, hits in filters:
            sets.append({h["evidence_id"] for h in hits})
            for h in hits:
                by_id[h["evidence_id"]] = h
                contributions[h["evidence_id"]].append({
                    "field": field,
                    "value": value,
                    "weight": float(weights.get(field, 1.0)),
                })
        keep = set.intersection(*sets) if sets else set()
        by_id = {k: v for k, v in by_id.items() if k in keep}
        contributions = {k: v for k, v in contributions.items() if k in keep}
    else:
        for field, value, hits in filters:
            add_hits(field, value, hits, field)

    if args.similar or soft_terms:
        # Load all records via by-artifact-type or scan record map
        # Soft match by reading record.json for apis/events/tables
        repo = evidence_repo_path(cfg)
        cache = cache_root(cfg)
        records_map = {}
        map_path = (root / "records-map.json")
        if map_path.exists():
            records_map = read_json(map_path)
        elif repo:
            for year_dir in (repo / "records").glob("*"):
                for rec_dir in year_dir.iterdir():
                    if (rec_dir / "record.json").exists():
                        records_map[rec_dir.name] = str(rec_dir)

        # Seed from artifact type if provided
        seed_ids = set(by_id) if by_id else set(records_map)
        if args.artifact_type and not seed_ids:
            seed_ids = {
                h["evidence_id"]
                for h in load_index_entries(root, "by-artifact-type").get(args.artifact_type, [])
            }

        for eid in sorted(seed_ids or records_map.keys()):
            path = records_map.get(eid)
            if not path:
                # try reconstruct from index meta
                continue
            rec_path = Path(path) / "record.json"
            if not rec_path.exists():
                continue
            rec = read_json(rec_path)
            if args.artifact_type and rec.get("artifact_type") != args.artifact_type and rec.get("review_type") != args.artifact_type:
                continue
            # soft field matches
            field_values = {
                "api": set(rec.get("apis") or []),
                "event": set(rec.get("events") or []),
                "database_table": set(rec.get("database_tables") or []),
                "contract": set(rec.get("contracts") or []),
                "repository": set(rec.get("repositories") or []),
                "service": set(rec.get("services") or []),
                "concept": {c.get("value") for c in (rec.get("concepts") or [])},
                "technology": set(rec.get("technologies") or []),
                "architectural_pattern": set(rec.get("architectural_patterns") or []),
                "artifact_type": {rec.get("artifact_type") or rec.get("review_type")},
            }
            matched = False
            for field, value in soft_terms:
                if value in field_values.get(field, set()):
                    matched = True
                    w = float(weights.get(field, 1.0))
                    by_id[eid] = {
                        "evidence_id": eid,
                        "activity_id": rec.get("activity_id"),
                        "review_type": rec.get("review_type"),
                        "artifact_type": rec.get("artifact_type"),
                        "lifecycle": derived_lifecycle(Path(path), rec),
                        "final_status": rec.get("final_status"),
                        "owner": rec.get("owner"),
                        "completed_at": rec.get("completed_at"),
                        "path": (
                            str(Path(path).relative_to(repo))
                            if repo and path_is_under(Path(path), repo)
                            else path
                        ),
                        "confidence": (rec.get("engineering_confidence") or {}).get("level"),
                        "title": rec.get("title"),
                    }
                    contributions[eid].append({"field": field, "value": value, "weight": w})
            if args.artifact_type and eid in by_id:
                contributions[eid].append({
                    "field": "artifact_type",
                    "value": args.artifact_type,
                    "weight": float(weights.get("artifact_type", 1.0)),
                })

    # Score + gate
    results = []
    for eid, meta in by_id.items():
        if args.lifecycle and meta.get("lifecycle") != args.lifecycle:
            continue
        if args.status and meta.get("final_status") != args.status:
            continue
        contrib = contributions.get(eid) or []
        # dedupe identical field+value
        seen = set()
        uniq = []
        for c in contrib:
            key = (c["field"], c["value"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(c)
        score = sum(c["weight"] for c in uniq)
        strong = sum(1 for c in uniq if c["field"] in strong_fields)
        concept_hits = sum(1 for c in uniq if c["field"] == "concept")
        if args.similar:
            if strong < 1 and concept_hits < min_concept:
                continue
            if score < min_score:
                continue
        receipt = {
            "evidence_id": eid,
            "score": score,
            "matched": uniq,
            "lifecycle": meta.get("lifecycle"),
            "final_status": meta.get("final_status"),
            "confidence": meta.get("confidence"),
            "title": meta.get("title"),
            "path": meta.get("path"),
            "activity_id": meta.get("activity_id"),
            "review_type": meta.get("review_type"),
            "artifact_type": meta.get("artifact_type"),
        }
        results.append(receipt)

    results.sort(key=lambda r: (-r["score"], r["evidence_id"]))
    if args.limit:
        results = results[: args.limit]

    if args.repeated_mistakes:
        # aggregate validated finding patterns
        fp_index = load_index_entries(root, "by-finding-pattern")
        agg = []
        for pattern, hits in sorted(fp_index.items()):
            if args.finding_pattern and pattern != args.finding_pattern:
                continue
            validated = [h for h in hits if h.get("finding_bucket") == "validated"]
            if args.service:
                svc_ids = {
                    h["evidence_id"]
                    for h in load_index_entries(root, "by-service").get(args.service, [])
                }
                validated = [h for h in validated if h["evidence_id"] in svc_ids]
            if len(validated) < 2:
                continue
            agg.append({
                "finding_pattern": pattern,
                "count": len(validated),
                "evidence_ids": sorted({h["evidence_id"] for h in validated}),
                "titles": [h.get("title") for h in validated[:5]],
            })
        print(json.dumps({
            "ok": True,
            "mode": "repeated_mistakes",
            "results": agg,
            "note": "Only validated findings with controlled finding_pattern keys are counted.",
        }, indent=2))
        return

    print(json.dumps({
        "ok": True,
        "root": str(root),
        "count": len(results),
        "results": results,
        "scoring_version": cfg.get("scoring_version") or "1.0.0",
        "note": "Explainable structured retrieval - not semantic search.",
    }, indent=2))



def _preview_payload(rec: dict, cand_dir: Path) -> dict:
    """Human-facing summary of exactly what would be indexed."""
    arts = [
        {"name": a.get("name"), "sha256": a.get("sha256"), "bytes": a.get("bytes"), "path": a.get("path")}
        for a in (rec.get("artifacts") or [])
    ]
    findings = rec.get("findings") or {}
    return {
        "evidence_id": rec.get("evidence_id"),
        "activity_id": rec.get("activity_id"),
        "session_id": rec.get("session_id"),
        "review_type": rec.get("review_type"),
        "artifact_type": rec.get("artifact_type"),
        "final_status": rec.get("final_status"),
        "owner": rec.get("owner"),
        "tickets": rec.get("tickets"),
        "title": rec.get("title"),
        "repositories": rec.get("repositories"),
        "services": rec.get("services"),
        "apis": rec.get("apis"),
        "events": rec.get("events"),
        "database_tables": rec.get("database_tables"),
        "technologies": rec.get("technologies"),
        "contracts": rec.get("contracts"),
        "concepts": [c.get("value") for c in (rec.get("concepts") or [])],
        "risk": rec.get("risk"),
        "deployment_type": rec.get("deployment_type"),
        "findings_counts": {
            k: len(findings.get(k) or []) for k in ("accepted", "validated", "rejected", "unresolved")
        },
        "informed_by": rec.get("informed_by"),
        "relationships": rec.get("relationships"),
        "artifacts": arts,
        "candidate_hash": rec.get("record_hash"),
        "candidate_dir": str(cand_dir),
        "candidate_summary": str(cand_dir / "CANDIDATE.md"),
        "git_commit": False,
        "git_push": False,
        "remote": False,
    }


def _run_validate_dir(path: Path) -> list[str]:
    rec, man = _load_candidate_dir(path)
    problems = validate_record_struct(rec)
    stored = rec.get("record_hash")
    actual = compute_record_hash(rec)
    if stored != actual:
        problems.append(f"record_hash mismatch: stored={stored} actual={actual}")
    if man.get("record_hash") != stored:
        problems.append("manifest.record_hash does not match record")
    for art in rec.get("artifacts") or []:
        ap = path / art["path"]
        if not ap.exists():
            problems.append(f"missing artifact: {art['path']}")
            continue
        if sha256_file(ap) != art["sha256"]:
            problems.append(f"artifact hash mismatch: {art['name']}")
    problems.extend(secret_scan_tree(path))
    return problems


def cmd_publish(args: argparse.Namespace) -> None:
    """Safe publish UX for /yonko evidence publish.

    Phase A (default): build candidate, validate + secret-scan, print preview, stop.
    Phase B (--confirm-hash + --approved-by): publish-local only if hash matches.

    Never git commit / push / remote.
    """
    cfg = load_config()
    repo = evidence_repo_path(cfg)
    if repo is None or not repo.exists():
        die(
            "YONKO_EVIDENCE_REPO not set or missing. "
            "Run: evidence-index.py init-repo --path <dir> --git-init && export YONKO_EVIDENCE_REPO=<dir>"
        )

    session_dir = expand_path(args.session)
    if not (session_dir / "session.json").exists():
        die(f"not a yonko session: {session_dir}")

    cand_name = (cfg.get("candidate") or {}).get("dir_name", "evidence-candidate")
    cand = session_dir / cand_name

    # Phase B: confirm existing candidate
    if args.confirm_hash:
        if not args.approved_by:
            die("--approved-by required with --confirm-hash")
        if not cand.exists():
            die(f"candidate missing at {cand} - run without --confirm-hash first")
        rec, _ = _load_candidate_dir(cand)
        problems = _run_validate_dir(cand)
        if problems:
            die("publish refused (validation/secret scan):\n  - " + "\n  - ".join(problems))
        stored = rec.get("record_hash")
        if args.confirm_hash != stored:
            die(f"confirm-hash mismatch (got {args.confirm_hash}, expected {stored})")
        # Delegate to publish-local
        ns = argparse.Namespace(
            session=str(session_dir),
            candidate_hash=stored,
            approved_by=args.approved_by,
            note=args.note,
        )
        cmd_publish_local(ns)
        return

    # Phase A: generate + preview + stop for explicit approval
    if not args.owner or not args.final_status:
        die("phase A requires --owner and --final-status (and usually --ticket)")

    relationships = []
    for rel in args.relationship or []:
        if ":" not in rel:
            die(f"relationship must be type:target_evidence_id, got {rel}")
        typ, _, target = rel.partition(":")
        relationships.append({"type": typ, "target_evidence_id": target})

    record, out_dir, _ = build_candidate(
        session_dir,
        activity_id=args.activity_id,
        owner=args.owner,
        tickets=args.ticket or [],
        final_status=args.final_status,
        title=args.title,
        summary=args.summary,
        decisions=args.decision or [],
        assumptions=args.assumption or [],
        unresolved_risks=args.unresolved_risk or [],
        rollout=args.rollout,
        rollback=args.rollback,
        commit_refs=args.commit or [],
        mr_refs=args.mr or [],
        relationships=relationships,
        informed_by=args.informed_by or [],
        human_concepts=args.concept or [],
    )
    problems = _run_validate_dir(out_dir)
    if problems:
        die("candidate failed validation/secret scan:\n  - " + "\n  - ".join(problems))

    preview = _preview_payload(record, out_dir)
    digest = record["record_hash"]
    summary_md = (out_dir / "CANDIDATE.md").read_text(encoding="utf-8") if (out_dir / "CANDIDATE.md").exists() else ""

    # Human-readable block on stderr; machine JSON on stdout
    info("=== EVIDENCE PUBLISH PREVIEW (not published yet) ===")
    info(f"evidence_id: {preview['evidence_id']}")
    info(f"activity_id: {preview['activity_id']}")
    info(f"review_type: {preview['review_type']} artifact_type={preview.get('artifact_type')}")
    info(f"final_status: {preview['final_status']} owner={preview['owner']}")
    info(f"tickets: {', '.join(preview.get('tickets') or [])}")
    info(f"repos: {', '.join(preview.get('repositories') or []) or '(none)'}")
    info(f"concepts: {', '.join(preview.get('concepts') or []) or '(none)'}")
    info(f"artifacts: {', '.join(a['name'] for a in preview.get('artifacts') or [])}")
    info(f"findings: {preview.get('findings_counts')}")
    info(f"candidate_hash: {digest}")
    info("This will NOT git commit, git push, or contact a remote.")
    info("To publish locally after explicit approval, re-run:")
    info(
        f"  scripts/evidence-index.py publish --session {session_dir} "
        f"--confirm-hash {digest} --approved-by <you>"
    )

    print(json.dumps({
        "ok": True,
        "phase": "awaiting_approval",
        "preview": preview,
        "candidate_summary_md": summary_md,
        "approve_command": (
            f"scripts/evidence-index.py publish --session {session_dir} "
            f"--confirm-hash {digest} --approved-by <you>"
        ),
        "note": (
            "Candidate built and validated. Not published. "
            "Re-run with --confirm-hash and --approved-by to publish-local only."
        ),
    }, indent=2))



def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="evidence-index.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init-repo", help="Seed a local evidence repository checkout")
    s.add_argument("--path", help="Target directory (or YONKO_EVIDENCE_REPO)")
    s.add_argument("--force", action="store_true")
    s.add_argument("--git-init", action="store_true", help="git init only - never commits")
    s.set_defaults(func=cmd_init_repo)

    s = sub.add_parser("candidate", help="Build candidate from a finalized Yonko session")
    s.add_argument("--session", required=True)
    s.add_argument("--owner", required=True)
    s.add_argument("--final-status", required=True)
    s.add_argument("--activity-id")
    s.add_argument("--ticket", action="append")
    s.add_argument("--title")
    s.add_argument("--summary")
    s.add_argument("--decision", action="append")
    s.add_argument("--assumption", action="append")
    s.add_argument("--unresolved-risk", action="append")
    s.add_argument("--rollout")
    s.add_argument("--rollback")
    s.add_argument("--commit", action="append")
    s.add_argument("--mr", action="append")
    s.add_argument("--relationship", action="append", help="type:target_evidence_id")
    s.add_argument("--informed-by", action="append")
    s.add_argument("--concept", action="append", help="human-confirmed concept")
    s.set_defaults(func=cmd_candidate)

    s = sub.add_parser("validate", help="Validate candidate or canonical record dir")
    s.add_argument("--path", required=True)
    s.set_defaults(func=cmd_validate)


    s = sub.add_parser(
        "publish",
        help="Safe publish: candidate -> preview/validate -> explicit hash approval -> publish-local",
    )
    s.add_argument("--session", required=True)
    s.add_argument("--owner", help="Required for phase A (build candidate)")
    s.add_argument("--final-status", help="Required for phase A")
    s.add_argument("--activity-id")
    s.add_argument("--ticket", action="append")
    s.add_argument("--title")
    s.add_argument("--summary")
    s.add_argument("--decision", action="append")
    s.add_argument("--assumption", action="append")
    s.add_argument("--unresolved-risk", action="append")
    s.add_argument("--rollout")
    s.add_argument("--rollback")
    s.add_argument("--commit", action="append")
    s.add_argument("--mr", action="append")
    s.add_argument("--relationship", action="append")
    s.add_argument("--informed-by", action="append")
    s.add_argument("--concept", action="append")
    s.add_argument("--confirm-hash", help="Phase B: must match candidate record_hash")
    s.add_argument("--approved-by", help="Phase B: human approver handle")
    s.add_argument("--note")
    s.set_defaults(func=cmd_publish)

    s = sub.add_parser("publish-local", help="Publish candidate into local evidence repo")
    s.add_argument("--session", required=True)
    s.add_argument("--candidate-hash", required=True)
    s.add_argument("--approved-by", required=True)
    s.add_argument("--note")
    s.set_defaults(func=cmd_publish_local)

    s = sub.add_parser("append-event", help="Append outcome event to canonical record")
    s.add_argument("--evidence-id", required=True)
    s.add_argument("--type", required=True)
    s.add_argument("--actor", required=True)
    s.add_argument("--source-reference")
    s.add_argument("--payload-json", default="{}")
    s.set_defaults(func=cmd_append_event)

    s = sub.add_parser("rebuild", help="Rebuild inverted indexes")
    s.add_argument("--path")
    s.set_defaults(func=cmd_rebuild)

    s = sub.add_parser("refresh-cache", help="Refresh disposable local read cache")
    s.set_defaults(func=cmd_refresh_cache)

    s = sub.add_parser("query", help="Structured explainable query")
    s.add_argument("--from-repo", action="store_true", help="Query repo directly, not cache")
    s.add_argument("--ticket")
    s.add_argument("--repository")
    s.add_argument("--service")
    s.add_argument("--concept")
    s.add_argument("--artifact-type")
    s.add_argument("--contract")
    s.add_argument("--technology")
    s.add_argument("--finding-pattern")
    s.add_argument("--lifecycle")
    s.add_argument("--status")
    s.add_argument("--similar", action="store_true", help="Weighted set-overlap ranking")
    s.add_argument("--api", action="append")
    s.add_argument("--event", action="append")
    s.add_argument("--table", action="append")
    s.add_argument("--like-concept", action="append")
    s.add_argument("--like-repository", action="append")
    s.add_argument("--like-service", action="append")
    s.add_argument("--repeated-mistakes", action="store_true")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_query)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
