"""Cross-repository consumer resolution (Evidence Graph).

Resolution order:
  1. Session co-collected repos (exact api/event/contract signal match in sibling
     patch and/or working tree) - preferred for multi-repo reviews in one packet
  2. Evidence Index exact field match (institutional memory)

Presence of another --repo alone is not proof. Exact signal match is required.
Fuzzy/semantic overlap is a candidate, never proof.

Statuses: resolved | unresolved | not_applicable
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess
from typing import Any

API_RE = re.compile(r"(?i)(/v\d+/[a-z0-9_\-/]+)")
# PascalCase type names only - do not use (?i) here (would make [A-Z] match camelCase methods).
EVENT_RE = re.compile(r"\b([A-Z][A-Za-z0-9]+(?:Event|Message|DTO))\b")
TOPIC_RE = re.compile(r"(?i)\b((?:sns|sqs|kafka)[:/\-][a-z0-9_\-\.]+)\b")
CONTRACT_RE = re.compile(r"(?i)\b([a-z0-9\-]+-model(?:-[a-z0-9]+)?)\b")
AUTHORITY_RE = re.compile(
    r"(?i)has(?:Authority|Role)\(\s*['\"]([A-Za-z0-9_.:\-]+)['\"]\s*\)"
)
MAPPING_PATH_RE = re.compile(
    r"(?i)@(?:Get|Post|Put|Patch|Delete|Request)Mapping\(\s*(?:value\s*=\s*)?['\"]([^'\"]+)['\"]"
)

# Paths that are ops/deploy surfaces - not shared API/model contracts for consumer lookup.
OPS_INFRA_PATH_RE = re.compile(
    r"(?i)("
    r"cloudformation/|cloudFormation/|"
    r"terraform/|\.tf$|"
    r"(?:^|/)cdk(?:\.|/)|"
    r"serverless\.ya?ml$|"
    r"docker-compose|"
    r"\.github/workflows/|"
    r"(?:^|/)(?:deploy|helm|kustomize|charts)/|"
    r"(?:^|/)[^/\s]*alarms?[^/\s]*\.ya?ml$|"
    r"-alarms?\.ya?ml$"
    r")"
)
OPS_CONTRACT_STEM_RE = re.compile(
    r"(?i)^(?:.*-)?(?:alarms?|alarm-stack|cfn|cloudformation|infra|stack|dashboard)(?:-.*)?$"
)
NOISE_EVENT_RE = re.compile(
    r"(?i)^(?:I?Logging|Status|Watch|PropertyChange|Hierarchy|ServletRequest)Event$"
    r"|^(?:Formatted|Structured)?Message$"
    r"|Logging"
)

# Bounded tree scan for co-collected consumers (not a full call graph).
_SESSION_RG_GLOBS = (
    "*.java",
    "*.kt",
    "*.ts",
    "*.tsx",
    "*.js",
    "*.jsx",
    "*.yaml",
    "*.yml",
    "*.json",
    "*.xml",
    "*.gradle",
    "*.properties",
    "*.tf",
    "*.md",
)


def _skill_scripts() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _load_evidence_index():
    path = _skill_scripts() / "evidence-index.py"
    spec = importlib.util.spec_from_file_location("yonko_evidence_index", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def is_ops_infra_path(path: str) -> bool:
    """True for CloudFormation / alarms / terraform / deploy paths (no code consumers)."""
    return bool(path and OPS_INFRA_PATH_RE.search(path.replace("\\", "/")))


def is_openapi_or_model_path(path: str, body: str = "") -> bool:
    """True for OpenAPI / shared-model contract files."""
    lower = (path or "").replace("\\", "/").lower()
    if "openapi" in lower or "-model" in lower or "/model/" in lower:
        return True
    if re.search(r"(?m)^\s*openapi:\s*['\"]?\d", body or ""):
        return True
    return False


def _is_cross_repo_event_signal(name: str) -> bool:
    if not name or not name[0].isupper():
        return False
    if NOISE_EVENT_RE.search(name):
        return False
    return True


def _is_cross_repo_contract_signal(name: str, path: str = "") -> bool:
    if not name:
        return False
    if path and is_ops_infra_path(path):
        return False
    if OPS_CONTRACT_STEM_RE.match(pathlib.Path(name).stem):
        return False
    return True


def filter_cross_repo_producer_signals(signals: dict[str, list[str]]) -> dict[str, list[str]]:
    """Drop ops/infra and logging noise that cannot have in-repo code consumers."""
    return {
        "apis": list(signals.get("apis") or []),
        "events": sorted(
            e for e in (signals.get("events") or []) if _is_cross_repo_event_signal(e)
        ),
        "contracts": sorted(
            c for c in (signals.get("contracts") or []) if _is_cross_repo_contract_signal(c)
        ),
        "permissions": list(signals.get("permissions") or []),
    }


def extract_producer_signals(patch_text: str, changed_symbols: list[dict[str, Any]] | None = None) -> dict[str, list[str]]:
    """Deterministic producer signals from the current change.

    Ops/infra paths and logging framework types are excluded from cross-repo
    consumer lookup (see filter_cross_repo_producer_signals).
    """
    blob = patch_text or ""
    apis = set(API_RE.findall(blob))
    for m in MAPPING_PATH_RE.finditer(blob):
        p = m.group(1)
        if p.startswith("/"):
            apis.add(p)
    events = {
        e for e in (set(EVENT_RE.findall(blob)) | set(TOPIC_RE.findall(blob)))
        if _is_cross_repo_event_signal(e)
    }
    contracts = set(CONTRACT_RE.findall(blob))
    permissions = set(AUTHORITY_RE.findall(blob))
    for s in changed_symbols or []:
        ck = str(s.get("change_kind") or "")
        path = str(s.get("path") or "")
        if ck == "operational_infra_change" or is_ops_infra_path(path):
            continue
        if s.get("kind") == "schema" or ck == "contract_change":
            name = s.get("name") or ""
            if name and _is_cross_repo_contract_signal(name, path):
                contracts.add(pathlib.Path(name).stem)
        if s.get("kind") == "annotation" and "PreAuthorize" in (s.get("name") or ""):
            pass
        if ck == "config_key_change" and s.get("name"):
            pass
    return filter_cross_repo_producer_signals(
        {
            "apis": sorted(apis),
            "events": sorted(events),
            "contracts": sorted(contracts),
            "permissions": sorted(permissions),
        }
    )


def _iter_records(root: pathlib.Path, ei) -> list[tuple[str, pathlib.Path, dict[str, Any]]]:
    out: list[tuple[str, pathlib.Path, dict[str, Any]]] = []
    map_path = root / "records-map.json"
    records_map: dict[str, str] = {}
    if map_path.exists():
        records_map = ei.read_json(map_path)
    else:
        records = root / "records"
        if records.is_dir():
            for year_dir in records.glob("*"):
                if not year_dir.is_dir():
                    continue
                for rec_dir in year_dir.iterdir():
                    if (rec_dir / "record.json").exists():
                        records_map[rec_dir.name] = str(rec_dir)
    for eid, path in sorted(records_map.items()):
        rec_path = pathlib.Path(path) / "record.json"
        if not rec_path.is_file():
            alt = pathlib.Path(path)
            if alt.name == "record.json" and alt.is_file():
                rec_path = alt
            elif (alt / "record.json").is_file():
                rec_path = alt / "record.json"
            else:
                continue
        try:
            rec = ei.read_json(rec_path)
        except Exception:
            continue
        out.append((eid, rec_path.parent, rec))
    return out


def _lifecycle_ok(rec: dict[str, Any]) -> bool:
    life = (rec.get("lifecycle") or "").lower()
    status = (rec.get("final_status") or "").lower()
    if life in ("candidate", "superseded", "rejected"):
        return False
    if status in ("fail", "deadlock", "rejected"):
        return False
    return True


def _match_signals_in_text(signals: dict[str, list[str]], blob: str) -> list[dict[str, str]]:
    matched: list[dict[str, str]] = []
    if not blob:
        return matched
    for api in signals.get("apis") or []:
        if api and api in blob:
            matched.append({"field": "api", "value": api, "relationship": "api_consumer"})
    for ev in signals.get("events") or []:
        if ev and ev in blob:
            matched.append({"field": "event", "value": ev, "relationship": "event_consumer"})
    for c in signals.get("contracts") or []:
        if c and c in blob:
            matched.append({"field": "contract", "value": c, "relationship": "shared_library_consumer"})
    return matched


def _rg_fixed_hit(needle: str, cwd: pathlib.Path) -> bool:
    """True if fixed-string needle appears under cwd (bounded globs)."""
    if not needle or not cwd.is_dir():
        return False
    for glob in _SESSION_RG_GLOBS:
        try:
            r = subprocess.run(
                ["rg", "-F", "-l", "--glob", glob, "-m", "1", needle, str(cwd)],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        if (r.stdout or "").strip():
            return True
    return False


def _match_signals_in_repo_tree(signals: dict[str, list[str]], repo_path: pathlib.Path) -> list[dict[str, str]]:
    matched: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for field, relationship, values in (
        ("api", "api_consumer", signals.get("apis") or []),
        ("event", "event_consumer", signals.get("events") or []),
        ("contract", "shared_library_consumer", signals.get("contracts") or []),
    ):
        for value in values:
            if not value or (field, value) in seen:
                continue
            if _rg_fixed_hit(value, repo_path):
                seen.add((field, value))
                matched.append({"field": field, "value": value, "relationship": relationship})
    return matched


def resolve_session_co_collected_consumers(
    *,
    producer_repository: str,
    signals: dict[str, list[str]],
    session_repos: list[dict[str, Any]] | None,
    evid_dir: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    """Prove consumers from other repos attached in this session's evidence packet.

    Exact api/event/contract match in the sibling patch and/or working tree.
    Attaching a second repo with no signal overlap does not resolve.
    """
    has_structural = bool(signals.get("apis") or signals.get("events") or signals.get("contracts"))
    if not has_structural or not session_repos:
        return []

    producer_norm = producer_repository.strip().lower()
    consumers: list[dict[str, Any]] = []

    for repo in session_repos:
        label = str(repo.get("label") or "").strip()
        if not label or label.lower() == producer_norm:
            continue

        blob = ""
        patch_name = repo.get("patch")
        if evid_dir and patch_name and (evid_dir / patch_name).is_file():
            blob = (evid_dir / patch_name).read_text(encoding="utf-8", errors="replace")

        matched = _match_signals_in_text(signals, blob)
        evidence_paths: list[str] = []
        if matched and patch_name:
            evidence_paths.append(f"session_patch:{patch_name}")

        repo_path_raw = repo.get("path")
        repo_path = pathlib.Path(repo_path_raw) if repo_path_raw else None
        if repo_path and repo_path.is_dir():
            tree_hits = _match_signals_in_repo_tree(signals, repo_path)
            for hit in tree_hits:
                key = (hit["field"], hit["value"])
                if not any((m["field"], m["value"]) == key for m in matched):
                    matched.append(hit)
            if tree_hits:
                evidence_paths.append(f"session_tree:{label}")

        if not matched:
            continue

        consumers.append(
            {
                "repository": label,
                "relationship_type": matched[0]["relationship"],
                "matched": matched,
                "evidence_source": f"session_repo:{label}",
                "evidence_id": f"session:{label}",
                "record_path": str(repo_path) if repo_path else None,
                "last_seen_revision": None,
                "version_hint": None,
                "confidence": "static",
                "compatibility_evidence": [
                    *[f"{m['field']}={m['value']}" for m in matched],
                    *evidence_paths,
                ],
                "deployment_order_known": True,
                "deployment_type": "multi-service",
                "rollout_strategy": "co_collected_in_session",
                "status": "resolved",
                "producer_repository": producer_repository,
                "discovery_method": "session_co_collected_signal_match",
            }
        )

    return sorted(consumers, key=lambda x: x["repository"])


def resolve_cross_repo_consumers(
    *,
    producer_repository: str,
    signals: dict[str, list[str]],
    risk_band: str = "medium",
    prefer_cache: bool = True,
    session_repos: list[dict[str, Any]] | None = None,
    evid_dir: pathlib.Path | str | None = None,
) -> dict[str, Any]:
    """Resolve known consumers from session co-collected repos, then Evidence Index.

    Returns:
      status: resolved | unresolved | not_applicable
      consumers: list of proven consumer edges
      candidates: fuzzy/weak overlaps (not proof)
      unresolved: list of gap records
      evidence_refs: strings for the graph category
    """
    evid_path = pathlib.Path(evid_dir) if evid_dir else None
    signals = filter_cross_repo_producer_signals(signals)
    result: dict[str, Any] = {
        "status": "unresolved",
        "consumers": [],
        "candidates": [],
        "unresolved": [],
        "evidence_refs": [],
        "signals": signals,
        "index_root": None,
        "discovery_method": "none",
        "session_repos_considered": [
            str(r.get("label") or "")
            for r in (session_repos or [])
            if str(r.get("label") or "").strip()
            and str(r.get("label") or "").strip().lower() != producer_repository.strip().lower()
        ],
    }

    has_structural = bool(signals.get("apis") or signals.get("events") or signals.get("contracts"))
    has_perm_only = bool(signals.get("permissions")) and not has_structural
    if not has_structural and not has_perm_only:
        result["status"] = "not_applicable"
        result["evidence_refs"] = [
            "not_applicable:no_cross_repo_producer_signals"
        ]
        result["discovery_method"] = "not_applicable"
        return result

    session_consumers = resolve_session_co_collected_consumers(
        producer_repository=producer_repository,
        signals=signals,
        session_repos=session_repos,
        evid_dir=evid_path,
    )

    index_consumers: list[dict[str, Any]] = []
    index_candidates: list[dict[str, Any]] = []
    index_gaps: list[dict[str, Any]] = []
    index_ok = False
    methods: list[str] = []

    if session_consumers:
        methods.append("session_co_collected_signal_match")

    # Index path - soft fail when session already resolved
    try:
        ei = _load_evidence_index()
        cfg = ei.load_config()
    except Exception as e:
        if not session_consumers:
            result["unresolved"].append(
                {
                    "source": producer_repository,
                    "relationship": "external_consumers",
                    "reason": f"Evidence Index loader failed: {e}",
                    "risk": "medium",
                    "required_for_complete_review": risk_band in ("medium", "high", "critical"),
                    "suggested_resolution": (
                        "fix evidence-index.py import / config, or attach consumer "
                        "repos with signal overlap in this session"
                    ),
                    "category": "cross_repository_consumers",
                }
            )
            result["evidence_refs"] = ["unresolved:index_loader_failed"]
            result["discovery_method"] = "evidence_index_exact_field_match"
            return result
        index_gaps.append({"reason": f"index_loader_failed:{e}"})
        ei = None
        cfg = None

    if ei is not None:
        repo = ei.evidence_repo_path(cfg)
        if not repo and not os.environ.get("YONKO_EVIDENCE_REPO"):
            if not session_consumers:
                result["unresolved"].append(
                    {
                        "source": producer_repository,
                        "relationship": "external_consumers",
                        "reason": "Evidence Index unavailable (YONKO_EVIDENCE_REPO unset)",
                        "risk": "medium" if risk_band in ("high", "critical") else "low",
                        "required_for_complete_review": (
                            risk_band in ("medium", "high", "critical") and has_structural
                        ),
                        "suggested_resolution": (
                            "set YONKO_EVIDENCE_REPO and publish prior sessions, or attach "
                            "consumer repository in collect-evidence (--repo) with signal overlap"
                        ),
                        "category": "cross_repository_consumers",
                    }
                )
                result["evidence_refs"] = ["unresolved:index_unavailable"]
                result["discovery_method"] = "evidence_index_exact_field_match"
                return result
        else:
            try:
                repo = ei.evidence_repo_path(cfg)
                if prefer_cache:
                    root = ei.resolve_query_root(cfg, prefer_cache=True)
                elif repo and (repo / "records").is_dir():
                    root = repo
                else:
                    root = ei.resolve_query_root(cfg, prefer_cache=False)
            except Exception:
                root = repo or ei.cache_root(cfg)
            result["index_root"] = str(root) if root else None

            if not root or not pathlib.Path(root).exists():
                if not session_consumers:
                    result["unresolved"].append(
                        {
                            "source": producer_repository,
                            "relationship": "external_consumers",
                            "reason": "Evidence Index root missing on disk",
                            "risk": "medium",
                            "required_for_complete_review": True,
                            "suggested_resolution": "init-repo / refresh-cache, or attach consumer repos",
                            "category": "cross_repository_consumers",
                        }
                    )
                    result["evidence_refs"] = ["unresolved:index_root_missing"]
                    result["discovery_method"] = "evidence_index_exact_field_match"
                    return result
            else:
                index_ok = True
                producer_norm = producer_repository.strip().lower()
                consumers_by_key: dict[str, dict[str, Any]] = {}

                for eid, rec_dir, rec in _iter_records(pathlib.Path(root), ei):
                    if not _lifecycle_ok(rec):
                        continue
                    repos = [str(r) for r in (rec.get("repositories") or [])]
                    other_repos = [r for r in repos if r.strip().lower() != producer_norm]
                    if not other_repos:
                        continue

                    rec_apis = set(rec.get("apis") or [])
                    rec_events = set(rec.get("events") or [])
                    rec_contracts = set(rec.get("contracts") or [])

                    matched: list[dict[str, str]] = []
                    for api in signals.get("apis") or []:
                        if api in rec_apis:
                            matched.append(
                                {"field": "api", "value": api, "relationship": "api_consumer"}
                            )
                    for ev in signals.get("events") or []:
                        if ev in rec_events:
                            matched.append(
                                {"field": "event", "value": ev, "relationship": "event_consumer"}
                            )
                    for c in signals.get("contracts") or []:
                        if c in rec_contracts:
                            matched.append(
                                {
                                    "field": "contract",
                                    "value": c,
                                    "relationship": "shared_library_consumer",
                                }
                            )

                    perm_hits = []
                    findings = ((rec.get("findings") or {}).get("validated") or []) + (
                        (rec.get("findings") or {}).get("accepted") or []
                    )
                    claim_blob = " ".join(
                        str(f.get("claim") or "") + " " + str(f.get("title") or "")
                        for f in findings
                        if isinstance(f, dict)
                    )
                    summary_blob = f"{rec.get('summary') or ''} {rec.get('title') or ''} {claim_blob}"
                    for perm in signals.get("permissions") or []:
                        if perm and perm in summary_blob:
                            perm_hits.append(perm)

                    if not matched and not perm_hits:
                        continue

                    deploy_known = bool(rec.get("deployment_type") or rec.get("rollout_strategy"))
                    for consumer_repo in other_repos:
                        if matched:
                            key = f"{consumer_repo}|exact|{eid}"
                            consumers_by_key[key] = {
                                "repository": consumer_repo,
                                "relationship_type": matched[0]["relationship"],
                                "matched": matched,
                                "evidence_source": f"evidence_index:{eid}",
                                "evidence_id": eid,
                                "record_path": str(rec_dir),
                                "last_seen_revision": (rec.get("commit_refs") or [None])[0],
                                "version_hint": rec.get("schema_version"),
                                "confidence": "static",
                                "compatibility_evidence": [
                                    f"{m['field']}={m['value']}" for m in matched
                                ],
                                "deployment_order_known": deploy_known,
                                "deployment_type": rec.get("deployment_type"),
                                "rollout_strategy": rec.get("rollout_strategy"),
                                "status": "resolved",
                                "producer_repository": producer_repository,
                                "discovery_method": "evidence_index_exact_field_match",
                            }
                        if perm_hits and not matched:
                            key = f"{consumer_repo}|perm|{eid}"
                            consumers_by_key[key] = {
                                "repository": consumer_repo,
                                "relationship_type": "permission_consumer_candidate",
                                "matched": [
                                    {"field": "permission_text", "value": p} for p in perm_hits
                                ],
                                "evidence_source": f"evidence_index:{eid}",
                                "evidence_id": eid,
                                "confidence": "likely",
                                "status": "candidate",
                                "note": (
                                    "Permission string found in record text; "
                                    "not a typed permissions index proof"
                                ),
                                "producer_repository": producer_repository,
                                "deployment_order_known": deploy_known,
                                "discovery_method": "evidence_index_permission_text",
                            }

                index_consumers = [
                    c for c in consumers_by_key.values() if c.get("status") == "resolved"
                ]
                index_candidates = [
                    c for c in consumers_by_key.values() if c.get("status") == "candidate"
                ]
                if index_consumers:
                    methods.append("evidence_index_exact_field_match")

    # Dedupe consumers by repository (prefer session proof when both exist)
    by_repo: dict[str, dict[str, Any]] = {}
    for c in session_consumers + index_consumers:
        key = str(c["repository"]).strip().lower()
        existing = by_repo.get(key)
        if existing is None:
            by_repo[key] = c
            continue
        # Prefer session over index for same repo
        if (
            existing.get("discovery_method") == "evidence_index_exact_field_match"
            and c.get("discovery_method") == "session_co_collected_signal_match"
        ):
            by_repo[key] = c

    consumers = sorted(by_repo.values(), key=lambda x: (x["repository"], x.get("evidence_id") or ""))
    candidates = sorted(index_candidates, key=lambda x: (x["repository"], x.get("evidence_id") or ""))
    result["consumers"] = consumers
    result["candidates"] = candidates

    if methods:
        result["discovery_method"] = "+".join(methods)
    elif index_ok:
        result["discovery_method"] = "evidence_index_exact_field_match"
    else:
        result["discovery_method"] = "session_co_collected_signal_match"

    if consumers:
        result["status"] = "resolved"
        for c in consumers:
            result["evidence_refs"].append(
                f"resolved:{c['repository']}:{c['relationship_type']}:{c['evidence_id']}"
            )
        for c in candidates:
            result["evidence_refs"].append(f"candidate:{c['repository']}:{c['evidence_id']}")
        return result

    # Structural signals with no exact consumers
    if has_structural:
        result["status"] = "unresolved"
        session_note = ""
        considered = result.get("session_repos_considered") or []
        if considered:
            session_note = (
                f" Co-collected session repos checked without signal overlap: {', '.join(considered)}."
            )
        result["unresolved"].append(
            {
                "source": producer_repository,
                "relationship": "external_consumers",
                "reason": (
                    "No co-collected session repo or Evidence Index record with exact "
                    f"api/event/contract match outside {producer_repository}.{session_note}"
                ),
                "risk": "medium" if risk_band in ("high", "critical") else "low",
                "required_for_complete_review": risk_band in ("medium", "high", "critical"),
                "suggested_resolution": (
                    "attach the consumer repository in collect-evidence (--repo) so its "
                    "patch/tree overlaps producer signals, or publish consumer sessions "
                    "to Evidence Index"
                ),
                "category": "cross_repository_consumers",
                "signals_searched": {
                    "apis": signals.get("apis") or [],
                    "events": signals.get("events") or [],
                    "contracts": signals.get("contracts") or [],
                },
            }
        )
        result["evidence_refs"] = ["unresolved:no_exact_consumer_match"]
        for c in candidates:
            result["evidence_refs"].append(f"candidate:{c['repository']}:{c['evidence_id']}")
        return result

    # Permissions-only: cannot prove without typed index
    result["status"] = "unresolved"
    result["unresolved"].append(
        {
            "source": producer_repository,
            "relationship": "permission_consumers",
            "reason": "Permission/claim change detected but Evidence Index has no typed permissions field",
            "risk": "medium",
            "required_for_complete_review": risk_band in ("high", "critical"),
            "suggested_resolution": "index permission enforcement paths or attach frontend/auth repos",
            "category": "cross_repository_consumers",
        }
    )
    result["evidence_refs"] = ["unresolved:permissions_index_absent"]
    result["discovery_method"] = "evidence_index_exact_field_match"
    return result


def signals_from_session_patches(evid_dir: pathlib.Path, repos: list[dict[str, Any]]) -> dict[str, list[str]]:
    blobs: list[str] = []
    for repo in repos:
        patch = repo.get("patch")
        if patch and (evid_dir / patch).exists():
            blobs.append((evid_dir / patch).read_text(encoding="utf-8", errors="replace"))
    return extract_producer_signals("\n".join(blobs))
