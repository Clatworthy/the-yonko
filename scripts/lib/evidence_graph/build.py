"""Yonko Evidence Graph v1 - deterministic change-impact evidence."""
from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import re
import subprocess
import time
from typing import Any

CATEGORIES = [
    "changed_symbols",
    "upstream_entry_points",
    "inbound_callers",
    "outbound_dependencies",
    "framework_reachability",
    "contracts",
    "persistence",
    "events_and_messages",
    "remote_services",
    "security_and_permissions",
    "configuration",
    "deployment_and_compatibility",
    "tests",
    "cross_repository_consumers",
    "operational_side_effects",
]

BEHAVIOURS = [
    "successful_path",
    "invalid_input",
    "unauthorised_caller",
    "missing_record",
    "duplicate_request",
    "downstream_failure",
    "timeout_or_retry",
    "concurrent_update",
    "old_new_version_coexistence",
    "migration_ordering",
    "rollback_path",
]

CLASS_RE = re.compile(
    r"(?m)^(?P<prefix>[+\-]?)\s*(?:public\s+|protected\s+|private\s+)?(?:static\s+)?"
    r"(?:final\s+)?(?:class|interface|enum)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
METHOD_RE = re.compile(
    r"(?m)^(?P<prefix>[+\-]?)\s*(?:public\s+|protected\s+|private\s+)?(?:static\s+)?"
    r"(?:final\s+)?(?P<ret>[\w.<>,\[\] ?]+)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)"
)
ANN_RE = re.compile(r"(?m)^(?P<prefix>[+\-]?)\s*(?P<ann>@[A-Za-z][A-Za-z0-9_.]*(?:\([^)]*\))?)")
FIELD_RE = re.compile(
    r"(?m)^(?P<prefix>[+\-]?)\s*(?:private|protected|public)\s+(?:static\s+)?(?:final\s+)?"
    r"(?P<type>[\w.<>,\[\]]+)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[;=]"
)
CONFIG_KEY_RE = re.compile(r"(?m)^(?P<prefix>[+\-]?)\s*(?P<key>[A-Za-z0-9_.\-]+)\s*[:=]")
CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
DIFF_HUNK_PATH = re.compile(r"(?m)^diff --git a/(.+?) b/(.+)$")
SKIP_METHOD_NAMES = {"if", "for", "while", "switch", "catch", "return", "new", "super", "this"}
CTOR_NEW_RE = re.compile(r"\bnew\s+(?P<type>[A-Z][A-Za-z0-9_]*)\s*\((?P<args>[^;]*)\)")
BUILDER_FIELD_RE = re.compile(r"\.(?P<field>[a-z][A-Za-z0-9_]*)\s*\(")
RETURN_LINE_RE = re.compile(r"\breturn\b")
POPULATION_CHANGE_KINDS = frozenset(
    {"public_return_population_change", "dto_field_population_change"}
)
DTO_TYPE_HINTS = ("Response", "DTO", "Dto", "Event", "Request", "Message", "Payload", "Result")


def _normalize_args(args: str) -> str:
    return re.sub(r"\s+", " ", (args or "").strip())


def _is_dtoish_type(type_name: str) -> bool:
    return any(h in type_name for h in DTO_TYPE_HINTS)


def _getter_name(field: str) -> str:
    if not field:
        return field
    return "get" + field[:1].upper() + field[1:]


def _context_method_names(body: str) -> list[str]:
    names: list[str] = []
    for m in METHOD_RE.finditer(body):
        ret = (m.group("ret") or "").strip()
        name = m.group("name")
        if name in SKIP_METHOD_NAMES:
            continue
        if "return" in ret.split():
            continue
        if name[0].isupper() and "new" in ret.split():
            continue
        if name not in names:
            names.append(name)
    return names


def _population_symbols_from_body(
    path: str, body: str, repo_label: str
) -> list[dict[str, Any]]:
    """Detect return/DTO field population semantic changes in a Java file hunk."""
    minus_ctors: dict[str, set[str]] = {}
    plus_ctors: dict[str, set[str]] = {}
    minus_fields: set[str] = set()
    plus_fields: set[str] = set()
    return_touched = False

    for ln in body.splitlines():
        if ln.startswith("+++") or ln.startswith("---"):
            continue
        if not (ln.startswith("+") or ln.startswith("-")):
            continue
        sign = ln[0]
        text = ln[1:]
        if RETURN_LINE_RE.search(text):
            return_touched = True
        for m in CTOR_NEW_RE.finditer(text):
            tname = m.group("type")
            args = _normalize_args(m.group("args"))
            bucket = plus_ctors if sign == "+" else minus_ctors
            bucket.setdefault(tname, set()).add(args)
        for m in BUILDER_FIELD_RE.finditer(text):
            field = m.group("field")
            if field in ("equals", "toString", "hashCode", "builder", "build", "of"):
                continue
            if sign == "+":
                plus_fields.add(field)
            else:
                minus_fields.add(field)

    changed_types: list[str] = []
    for tname in sorted(set(minus_ctors) | set(plus_ctors)):
        if minus_ctors.get(tname, set()) != plus_ctors.get(tname, set()):
            if _is_dtoish_type(tname) or return_touched:
                changed_types.append(tname)

    changed_fields = sorted(minus_fields ^ plus_fields)
    if not changed_types and not changed_fields:
        return []

    methods = _context_method_names(body)
    primary_method = methods[0] if methods else pathlib.Path(path).stem
    affected: list[str] = []
    for name in methods:
        if name not in affected:
            affected.append(name)
    for tname in changed_types:
        if tname not in affected:
            affected.append(tname)
    for field in changed_fields:
        if field not in affected:
            affected.append(field)
        getter = _getter_name(field)
        if getter not in affected:
            affected.append(getter)

    # Constructor-only population often has no builder field name; keep method + type.
    kind = (
        "public_return_population_change"
        if return_touched or any(_is_dtoish_type(t) for t in changed_types)
        else "dto_field_population_change"
    )
    if changed_fields and not return_touched and not changed_types:
        kind = "dto_field_population_change"

    symbol_name = primary_method
    if changed_fields and kind == "dto_field_population_change":
        symbol_name = changed_fields[0]

    return [
        {
            "id": _stable_id(repo_label, path, "population", symbol_name, kind),
            "kind": "method_body" if kind == "public_return_population_change" else "field",
            "name": symbol_name,
            "path": path,
            "repository": repo_label,
            "change_kind": kind,
            "affected_names": affected,
            "evidence": f"diff:{path}:population:{symbol_name}",
            "discovery_method": "git_diff",
            "confidence": "static" if changed_fields or changed_types else "likely",
        }
    ]


def _snippet_around(text: str, line_no: int, radius: int = 40) -> str:
    lines = text.splitlines()
    if not lines:
        return ""
    idx = max(0, min(len(lines) - 1, line_no - 1))
    start = max(0, idx - radius)
    end = min(len(lines), idx + radius + 1)
    numbered = []
    for i in range(start, end):
        numbered.append(f"{i + 1}|{lines[i]}")
    return "\n".join(numbered) + "\n"


def _stage_impact_reader(
    evid: pathlib.Path,
    *,
    repo_label: str,
    rel: str,
    hit_line: int,
    file_text: str,
    symbol: dict[str, Any],
    search_term: str,
    staged: dict[str, dict[str, Any]],
    max_bytes: int,
) -> None:
    key = f"{repo_label}|{rel}"
    if key in staged:
        existing = staged[key]
        if search_term not in existing["search_terms"]:
            existing["search_terms"].append(search_term)
        if symbol.get("name") and symbol["name"] not in existing["symbols"]:
            existing["symbols"].append(symbol["name"])
        return
    snippet = _snippet_around(file_text, hit_line)
    if len(snippet.encode("utf-8")) > max_bytes:
        snippet = snippet.encode("utf-8")[:max_bytes].decode("utf-8", errors="replace")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{repo_label}__{rel}")
    staged_dir = evid / "staged-readers"
    staged_dir.mkdir(parents=True, exist_ok=True)
    out_path = staged_dir / f"{safe}.txt"
    out_path.write_text(snippet, encoding="utf-8")
    staged[key] = {
        "repository": repo_label,
        "path": rel,
        "hit_line": hit_line,
        "search_terms": [search_term],
        "symbols": [symbol.get("name")] if symbol.get("name") else [],
        "change_kinds": [symbol.get("change_kind")] if symbol.get("change_kind") else [],
        "staged_file": str(out_path.relative_to(evid)),
        "inclusion_reason": f"reader_of_population:{symbol.get('name')}",
    }


def _skill_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[3]


def _load_json_config(name: str) -> dict[str, Any]:
    path = _skill_root() / "config" / "evidence-graph" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "n_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _edge_id(src: str, tgt: str, etype: str) -> str:
    return "e_" + hashlib.sha1(f"{src}|{etype}|{tgt}".encode("utf-8")).hexdigest()[:12]


def _run_rg(pattern: str, cwd: pathlib.Path, glob: str = "*.java", max_hits: int = 40) -> list[dict[str, Any]]:
    try:
        r = subprocess.run(
            ["rg", "-n", "--glob", glob, "-m", str(max_hits), pattern, str(cwd)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    hits = []
    for line in (r.stdout or "").splitlines():
        m = re.match(r"^(.+?):(\d+):(.*)$", line)
        if not m:
            continue
        hits.append({"path": m.group(1), "line": int(m.group(2)), "text": m.group(3).strip()})
    return hits


def _split_file_hunks(patch: str) -> dict[str, str]:
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in patch.splitlines(True):
        m = DIFF_HUNK_PATH.match(line.rstrip("\n"))
        if m:
            current = m.group(2)
            out.setdefault(current, []).append(line)
            continue
        if current:
            out[current].append(line)
    return {k: "".join(v) for k, v in out.items()}


def extract_changed_symbols(patch: str, repo_label: str) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    hunks = _split_file_hunks(patch)
    for path, body in sorted(hunks.items()):
        lower = path.lower()
        if lower.endswith(".java"):
            symbols.extend(_extract_java_symbols(path, body, repo_label))
        elif re.search(r"db/migration|V\d+__.*\.sql$", path, re.I):
            symbols.append(
                {
                    "id": _stable_id(repo_label, path, "migration"),
                    "kind": "migration",
                    "name": pathlib.Path(path).name,
                    "path": path,
                    "repository": repo_label,
                    "change_kind": "migration_change",
                    "evidence": f"diff:{path}",
                    "discovery_method": "git_diff",
                    "confidence": "static",
                }
            )
        elif re.search(r"application(-[a-z0-9]+)?\.(yml|yaml|properties)$", path, re.I):
            for m in CONFIG_KEY_RE.finditer(body):
                if m.group("prefix") not in ("+", "-"):
                    continue
                key = m.group("key")
                if key.startswith("#") or key in ("---", "..."):
                    continue
                symbols.append(
                    {
                        "id": _stable_id(repo_label, path, "config", key),
                        "kind": "config_key",
                        "name": key,
                        "path": path,
                        "repository": repo_label,
                        "change_kind": "config_key_change",
                        "evidence": f"diff:{path}:{key}",
                        "discovery_method": "git_diff",
                        "confidence": "static",
                    }
                )
        elif lower.endswith((".yml", ".yaml")) and "src/test" not in path:
            if any(ln.startswith(("+", "-")) and not ln.startswith(("+++", "---")) for ln in body.splitlines()):
                try:
                    from .cross_repo import is_ops_infra_path, is_openapi_or_model_path
                except ImportError:
                    import importlib.util as _ilu
                    _cr = pathlib.Path(__file__).resolve().parent / "cross_repo.py"
                    _spec = _ilu.spec_from_file_location("yonko_eg_cross_repo_path", _cr)
                    _mod = _ilu.module_from_spec(_spec)
                    assert _spec.loader is not None
                    _spec.loader.exec_module(_mod)
                    is_ops_infra_path = _mod.is_ops_infra_path
                    is_openapi_or_model_path = _mod.is_openapi_or_model_path
                if is_ops_infra_path(path):
                    symbols.append(
                        {
                            "id": _stable_id(repo_label, path, "infra"),
                            "kind": "infra",
                            "name": pathlib.Path(path).name,
                            "path": path,
                            "repository": repo_label,
                            "change_kind": "operational_infra_change",
                            "evidence": f"diff:{path}",
                            "discovery_method": "git_diff",
                            "confidence": "static",
                        }
                    )
                elif is_openapi_or_model_path(path, body):
                    symbols.append(
                        {
                            "id": _stable_id(repo_label, path, "schema"),
                            "kind": "schema",
                            "name": pathlib.Path(path).name,
                            "path": path,
                            "repository": repo_label,
                            "change_kind": "contract_change",
                            "evidence": f"diff:{path}",
                            "discovery_method": "git_diff",
                            "confidence": "likely",
                        }
                    )
    seen: set[str] = set()
    deduped = []
    for s in symbols:
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        deduped.append(s)
    return deduped


def _extract_java_symbols(path: str, body: str, repo_label: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    method_sig_changed: set[str] = set()
    anns: list[str] = []
    for m in CLASS_RE.finditer(body):
        if m.group("prefix") not in ("+", "-"):
            continue
        out.append(
            {
                "id": _stable_id(repo_label, path, "class", m.group("name")),
                "kind": "class",
                "name": m.group("name"),
                "path": path,
                "repository": repo_label,
                "change_kind": "class_modified",
                "evidence": f"diff:{path}:{m.group('name')}",
                "discovery_method": "git_diff",
                "confidence": "static",
            }
        )
    for m in METHOD_RE.finditer(body):
        if m.group("prefix") not in ("+", "-"):
            continue
        name = m.group("name")
        ret = (m.group("ret") or "").strip()
        if name in SKIP_METHOD_NAMES:
            continue
        # Avoid matching `return new TypeName(...)` as a method signature.
        if "return" in ret.split() or ("new" in ret.split() and name[0].isupper()):
            continue
        method_sig_changed.add(name)
        out.append(
            {
                "id": _stable_id(repo_label, path, "method", name),
                "kind": "method",
                "name": name,
                "path": path,
                "repository": repo_label,
                "signature": f"{m.group('ret')} {name}({m.group('params').strip()})",
                "change_kind": "method_signature_change",
                "evidence": f"diff:{path}:{name}",
                "discovery_method": "git_diff",
                "confidence": "static",
            }
        )
    for m in ANN_RE.finditer(body):
        if m.group("prefix") not in ("+", "-"):
            continue
        anns.append(m.group("ann"))
        out.append(
            {
                "id": _stable_id(repo_label, path, "ann", m.group("ann")),
                "kind": "annotation",
                "name": m.group("ann").split("(")[0],
                "path": path,
                "repository": repo_label,
                "change_kind": "annotation_change",
                "evidence": f"diff:{path}:{m.group('ann')}",
                "discovery_method": "git_diff",
                "confidence": "static",
            }
        )
    for m in FIELD_RE.finditer(body):
        if m.group("prefix") not in ("+", "-"):
            continue
        out.append(
            {
                "id": _stable_id(repo_label, path, "field", m.group("name")),
                "kind": "field",
                "name": m.group("name"),
                "path": path,
                "repository": repo_label,
                "change_kind": "dto_field_change",
                "evidence": f"diff:{path}:{m.group('name')}",
                "discovery_method": "git_diff",
                "confidence": "static",
            }
        )
    population = _population_symbols_from_body(path, body, repo_label)
    out.extend(population)
    code_pm = [
        ln
        for ln in body.splitlines()
        if (ln.startswith("+") or ln.startswith("-")) and not ln.startswith("+++") and not ln.startswith("---")
    ]
    if code_pm and not method_sig_changed and not anns and not out:
        out.append(
            {
                "id": _stable_id(repo_label, path, "body"),
                "kind": "method_body",
                "name": pathlib.Path(path).stem,
                "path": path,
                "repository": repo_label,
                "change_kind": "method_body_only",
                "evidence": f"diff:{path}:body",
                "discovery_method": "git_diff",
                "confidence": "likely",
            }
        )
    return out


def _read_file(repo_path: pathlib.Path, rel: str, max_bytes: int) -> str:
    p = repo_path / rel
    if not p.is_file():
        return ""
    data = p.read_bytes()[:max_bytes]
    return data.decode("utf-8", errors="replace")


def build_evidence_graph(session_dir: pathlib.Path) -> dict[str, Any]:
    t0 = time.time()
    session_dir = session_dir.resolve()
    evid = session_dir / "evidence"
    session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    review_type = session.get("review_type") or "implementation"
    policy = _load_json_config("policy.json")
    adapters = _load_json_config("java-spring-adapters.json")
    budgets = policy["budgets"]
    collectors: list[str] = []

    risk_band = "unknown"
    if (evid / "risk.json").exists():
        risk_band = json.loads((evid / "risk.json").read_text(encoding="utf-8")).get("risk") or "unknown"
    elif (evid / "scope-risk.json").exists():
        risk_band = json.loads((evid / "scope-risk.json").read_text(encoding="utf-8")).get("risk") or "unknown"

    depth = policy["depth_by_band"].get(risk_band) or policy["depth_by_band"]["medium"]
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []
    paths: list[dict[str, Any]] = []
    changed_symbols: list[dict[str, Any]] = []
    notes: list[str] = []
    cat_evidence: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    touched: set[str] = set()
    staged_readers: dict[str, dict[str, Any]] = {}
    population_reader_hits: dict[str, int] = {}

    def add_node(n: dict[str, Any]) -> None:
        if len(nodes) >= budgets["max_nodes"]:
            return
        nodes[n["id"]] = n

    def add_edge(e: dict[str, Any]) -> None:
        if len(edges) >= budgets["max_edges"]:
            return
        edges[e["id"]] = e

    repos: list[dict[str, Any]] = []
    if review_type == "implementation" and (evid / "repos.json").exists():
        repos = json.loads((evid / "repos.json").read_text(encoding="utf-8")).get("repos") or []
    elif review_type == "plan" and (evid / "plan-refs.json").exists():
        # Proposed impact: named repos only; mark many categories unresolved/N/A later
        refs = json.loads((evid / "plan-refs.json").read_text(encoding="utf-8"))
        for r in refs.get("repositories_named") or []:
            repos.append({"label": r.get("label") or "named", "path": r.get("path") or "", "patch": None})
        notes.append("plan_review_proposed_impact_graph")
    else:
        notes.append(f"review_type_{review_type}_limited_graph")

    collectors.append("extract_symbols")
    for repo in repos:
        label = repo["label"]
        repo_path = pathlib.Path(repo["path"]) if repo.get("path") else None
        patch = ""
        if repo.get("patch") and (evid / repo["patch"]).exists():
            patch = (evid / repo["patch"]).read_text(encoding="utf-8", errors="replace")
        elif review_type == "implementation":
            continue
        syms = extract_changed_symbols(patch, label) if patch else []
        for s in syms[: budgets["max_changed_symbols"]]:
            changed_symbols.append(s)
            add_node(
                {
                    "id": s["id"],
                    "type": s["kind"],
                    "name": s["name"],
                    "path": s.get("path"),
                    "repository": label,
                    "inclusion_reason": f"changed_symbol:{s['change_kind']}",
                    "evidence": [s["evidence"]],
                }
            )
            cat_evidence["changed_symbols"].append(s["evidence"])
            ck = s["change_kind"]
            if "migration" in ck:
                touched.add("persistence")
                touched.add("deployment_and_compatibility")
                cat_evidence["persistence"].append(s["evidence"])
                cat_evidence["deployment_and_compatibility"].append(s["evidence"])
            if (
                "contract" in ck
                or s["kind"] == "schema"
                or s["kind"] == "field"
                or ck in POPULATION_CHANGE_KINDS
            ):
                touched.add("contracts")
                cat_evidence["contracts"].append(s["evidence"])
            if ck in POPULATION_CHANGE_KINDS:
                touched.add("inbound_callers")
                touched.add("operational_side_effects")
                cat_evidence["operational_side_effects"].append(s["evidence"])
                population_reader_hits.setdefault(s["id"], 0)
            if "config" in ck:
                touched.add("configuration")
                cat_evidence["configuration"].append(s["evidence"])
            if ck == "operational_infra_change" or s["kind"] == "infra":
                touched.add("operational_side_effects")
                touched.add("deployment_and_compatibility")
                cat_evidence["operational_side_effects"].append(s["evidence"])
                cat_evidence["deployment_and_compatibility"].append(s["evidence"])
            if s["kind"] == "annotation" and any(
                s["name"].startswith(a) for a in adapters["annotations"].get("security", [])
            ):
                touched.add("security_and_permissions")
                cat_evidence["security_and_permissions"].append(s["evidence"])
            if s["kind"] == "annotation" and any(
                s["name"].startswith(a) for a in adapters["annotations"].get("transactional", [])
            ):
                touched.add("operational_side_effects")
                cat_evidence["operational_side_effects"].append(s["evidence"])

        if not repo_path or not repo_path.is_dir() or not patch:
            for s in changed_symbols:
                if s["repository"] != label:
                    continue
                if s.get("change_kind") not in POPULATION_CHANGE_KINDS:
                    continue
                if population_reader_hits.get(s["id"], 0) > 0:
                    continue
                names = s.get("affected_names") or [s.get("name")]
                named = ", ".join(str(n) for n in names if n)
                unresolved.append(
                    {
                        "source": s["id"],
                        "relationship": "called_by",
                        "category": "inbound_callers",
                        "reason": (
                            f"no in-repo readers found for population change "
                            f"affecting {named or s.get('name')}"
                        ),
                        "symbol": s.get("name"),
                        "affected_names": names,
                        "required_for_complete_review": risk_band
                        in ("medium", "high", "critical"),
                        "discovery_method": "ripgrep",
                        "confidence": "low",
                    }
                )
                cat_evidence["inbound_callers"].append(
                    f"unresolved:readers:{s.get('name')}"
                )
                unresolved.append(
                    {
                        "source": s["id"],
                        "relationship": "side_effect_keyed_on_return",
                        "category": "operational_side_effects",
                        "reason": (
                            f"side effects keyed on returned field readers unproven "
                            f"for {named or s.get('name')}"
                        ),
                        "symbol": s.get("name"),
                        "affected_names": names,
                        "required_for_complete_review": risk_band
                        in ("medium", "high", "critical"),
                        "discovery_method": "population_reader_gate",
                        "confidence": "low",
                    }
                )
                cat_evidence["operational_side_effects"].append(
                    f"unresolved:readers:{s.get('name')}"
                )
            continue

        collectors.append("discover_spring")
        collectors.append("trace_references")
        for s in changed_symbols:
            if s["repository"] != label:
                continue
            name = s["name"].lstrip("@")
            search_terms: list[str] = []
            is_population = s.get("change_kind") in POPULATION_CHANGE_KINDS
            if s["kind"] in ("method", "class", "method_body", "field") or is_population:
                for term in [name, *(s.get("affected_names") or [])]:
                    t = str(term).lstrip("@")
                    if t and t not in search_terms and t not in SKIP_METHOD_NAMES:
                        search_terms.append(t)
            seen_hit_keys: set[str] = set()
            for term in search_terms:
                hits = _run_rg(
                    rf"\b{re.escape(term)}\b",
                    repo_path,
                    max_hits=budgets["max_rg_hits_per_symbol"],
                )
                for h in hits:
                    rel = str(pathlib.Path(h["path"]).resolve())
                    try:
                        rel = str(pathlib.Path(h["path"]).resolve().relative_to(repo_path.resolve()))
                    except ValueError:
                        rel = h["path"]
                    if rel == s.get("path"):
                        continue
                    hit_key = f"{rel}:{h['line']}:{term}"
                    if hit_key in seen_hit_keys:
                        continue
                    seen_hit_keys.add(hit_key)
                    if "/test/" in rel.replace("\\", "/") or rel.endswith("Test.java") or rel.endswith("IT.java"):
                        nid = _stable_id(label, rel, "test")
                        add_node(
                            {
                                "id": nid,
                                "type": "test",
                                "name": pathlib.Path(rel).name,
                                "path": rel,
                                "repository": label,
                                "inclusion_reason": f"tests_reference:{s['name']}",
                                "evidence": [f"{rel}:{h['line']}"],
                            }
                        )
                        eid = _edge_id(nid, s["id"], "tested_by")
                        add_edge(
                            {
                                "id": eid,
                                "source": nid,
                                "target": s["id"],
                                "type": "tested_by",
                                "evidence": [f"{rel}:{h['line']}"],
                                "discovery_method": "ripgrep",
                                "confidence": "likely",
                                "traversal_depth": 1,
                                "materiality": "medium",
                                "status": "accepted",
                                "inclusion_reason": f"test references {s['name']}",
                            }
                        )
                        cat_evidence["tests"].append(f"{rel}:{h['line']}")
                        continue
                    file_text = _read_file(repo_path, rel, budgets["max_file_bytes_read"])
                    is_entry = any(a in file_text for a in adapters["annotations"]["rest_controller"])
                    is_sched = any(a in file_text for a in adapters["annotations"]["scheduled"])
                    is_listener = any(
                        a in file_text
                        for a in adapters["annotations"]["event_listener"] + adapters["annotations"]["message_listener"]
                    )
                    ntype = "file"
                    if is_entry:
                        ntype = "endpoint"
                        cat_evidence["upstream_entry_points"].append(f"{rel}:{h['line']}")
                        cat_evidence["framework_reachability"].append(f"{rel}:{h['line']}")
                        touched.add("framework_reachability")
                    elif is_sched:
                        ntype = "scheduled_job"
                        cat_evidence["upstream_entry_points"].append(f"{rel}:{h['line']}")
                        cat_evidence["framework_reachability"].append(f"{rel}:{h['line']}")
                    elif is_listener:
                        ntype = "event_listener"
                        cat_evidence["upstream_entry_points"].append(f"{rel}:{h['line']}")
                        cat_evidence["events_and_messages"].append(f"{rel}:{h['line']}")
                        touched.add("events_and_messages")
                    else:
                        cat_evidence["inbound_callers"].append(f"{rel}:{h['line']}")
                    nid = _stable_id(label, rel, ntype, name)
                    add_node(
                        {
                            "id": nid,
                            "type": ntype,
                            "name": pathlib.Path(rel).stem,
                            "path": rel,
                            "repository": label,
                            "inclusion_reason": f"caller_of:{s['name']}",
                            "evidence": [f"{rel}:{h['line']}"],
                        }
                    )
                    eid = _edge_id(nid, s["id"], "called_by")
                    add_edge(
                        {
                            "id": eid,
                            "source": nid,
                            "target": s["id"],
                            "type": "called_by",
                            "evidence": [f"{rel}:{h['line']}"],
                            "discovery_method": "ripgrep",
                            "confidence": "framework" if ntype != "file" else "likely",
                            "traversal_depth": 1,
                            "materiality": "high" if ntype == "endpoint" else "medium",
                            "status": "accepted",
                            "inclusion_reason": f"{ntype} references {s['name']}",
                        }
                    )
                    if is_population:
                        population_reader_hits[s["id"]] = population_reader_hits.get(s["id"], 0) + 1
                        _stage_impact_reader(
                            evid,
                            repo_label=label,
                            rel=rel,
                            hit_line=int(h["line"]),
                            file_text=file_text,
                            symbol=s,
                            search_term=term,
                            staged=staged_readers,
                            max_bytes=min(budgets.get("max_file_bytes_read", 12000), 12000),
                        )
                        if re.search(
                            r"publish|send\(|convertAndSend|KafkaTemplate|SNS|SQS|EventBridge",
                            file_text,
                        ):
                            cat_evidence["operational_side_effects"].append(f"{rel}:{h['line']}")
                            touched.add("operational_side_effects")
                            touched.add("events_and_messages")
                            cat_evidence["events_and_messages"].append(f"{rel}:{h['line']}")

            if is_population and population_reader_hits.get(s["id"], 0) == 0:
                names = s.get("affected_names") or [s.get("name")]
                named = ", ".join(str(n) for n in names if n)
                unresolved.append(
                    {
                        "source": s["id"],
                        "relationship": "called_by",
                        "category": "inbound_callers",
                        "reason": (
                            f"no in-repo readers found for population change "
                            f"affecting {named or s.get('name')}"
                        ),
                        "symbol": s.get("name"),
                        "affected_names": names,
                        "required_for_complete_review": risk_band
                        in ("medium", "high", "critical"),
                        "discovery_method": "ripgrep",
                        "confidence": "low",
                    }
                )
                cat_evidence["inbound_callers"].append(
                    f"unresolved:readers:{s.get('name')}"
                )
                unresolved.append(
                    {
                        "source": s["id"],
                        "relationship": "side_effect_keyed_on_return",
                        "category": "operational_side_effects",
                        "reason": (
                            f"side effects keyed on returned field readers unproven "
                            f"for {named or s.get('name')}"
                        ),
                        "symbol": s.get("name"),
                        "affected_names": names,
                        "required_for_complete_review": risk_band
                        in ("medium", "high", "critical"),
                        "discovery_method": "population_reader_gate",
                        "confidence": "low",
                    }
                )
                cat_evidence["operational_side_effects"].append(
                    f"unresolved:readers:{s.get('name')}"
                )

            # Downstream from changed file content
            if s.get("path"):
                src = _read_file(repo_path, s["path"], budgets["max_file_bytes_read"])
                if src:
                    for ann_group, cat in (
                        ("security", "security_and_permissions"),
                        ("transactional", "operational_side_effects"),
                        ("retry", "operational_side_effects"),
                        ("entity", "persistence"),
                        ("repository", "persistence"),
                    ):
                        for a in adapters["annotations"].get(ann_group, []):
                            if a in src:
                                cat_evidence[cat].append(f"{s['path']}:{a}")
                                touched.add(cat)
                    if re.search(r"RestTemplate|WebClient|FeignClient|HttpClient", src):
                        cat_evidence["remote_services"].append(s["path"])
                        touched.add("remote_services")
                    if re.search(r"publishEvent|ApplicationEventPublisher|KafkaTemplate|convertAndSend", src):
                        cat_evidence["events_and_messages"].append(s["path"])
                        touched.add("events_and_messages")
                    # simple outbound calls (interesting identifiers only)
                    interesting = {
                        "save", "findById", "delete", "update", "publishConfirmed",
                        "publishEvent", "convertAndSend",
                    }
                    hops = 0
                    for m in CALL_RE.finditer(src):
                        cal = m.group(1)
                        if cal in SKIP_METHOD_NAMES:
                            continue
                        if cal not in interesting and not cal[0].isupper():
                            continue
                        if hops >= depth["downstream_hops"]:
                            break
                        tid = _stable_id(label, "callee", cal)
                        add_node(
                            {
                                "id": tid,
                                "type": "method",
                                "name": cal,
                                "path": s["path"],
                                "repository": label,
                                "inclusion_reason": f"callee_of:{s['name']}",
                                "evidence": [f"{s['path']}:call:{cal}"],
                            }
                        )
                        eid = _edge_id(s["id"], tid, "calls")
                        add_edge(
                            {
                                "id": eid,
                                "source": s["id"],
                                "target": tid,
                                "type": "calls",
                                "evidence": [f"{s['path']}:call:{cal}"],
                                "discovery_method": "source_scan",
                                "confidence": "likely",
                                "traversal_depth": hops + 1,
                                "materiality": "medium",
                                "status": "accepted",
                                "inclusion_reason": f"{s['name']} calls {cal}",
                            }
                        )
                        cat_evidence["outbound_dependencies"].append(f"{s['path']}:{cal}")
                        hops += 1

        # Cross-repo via Evidence Index (exact api/event/contract matches only)
        collectors.append("query_cross_repo")
        try:
            from .cross_repo import extract_producer_signals, resolve_cross_repo_consumers
        except ImportError:
            import importlib.util as _ilu
            _cr = pathlib.Path(__file__).resolve().parent / "cross_repo.py"
            _spec = _ilu.spec_from_file_location("yonko_eg_cross_repo", _cr)
            _mod = _ilu.module_from_spec(_spec)
            assert _spec.loader is not None
            _spec.loader.exec_module(_mod)
            extract_producer_signals = _mod.extract_producer_signals
            resolve_cross_repo_consumers = _mod.resolve_cross_repo_consumers

        signals = extract_producer_signals(patch, changed_symbols)
        if signals.get("apis") or signals.get("events"):
            touched.add("cross_repository_consumers")
        if signals.get("contracts"):
            touched.add("contracts")
            touched.add("cross_repository_consumers")
        if signals.get("permissions"):
            touched.add("security_and_permissions")
            touched.add("cross_repository_consumers")

        xref = resolve_cross_repo_consumers(
            producer_repository=label,
            signals=signals,
            risk_band=risk_band,
            prefer_cache=False,
            session_repos=repos,
            evid_dir=evid,
        )
        notes.append(f"cross_repo_status:{xref.get('status')}")
        if xref.get("discovery_method"):
            notes.append(f"cross_repo_method:{xref['discovery_method']}")
        if xref.get("index_root"):
            notes.append(f"cross_repo_index:{xref['index_root']}")

        if xref["status"] == "not_applicable":
            cat_evidence["cross_repository_consumers"].extend(xref.get("evidence_refs") or [])
        elif xref["status"] == "resolved":
            touched.add("cross_repository_consumers")
            for c in xref.get("consumers") or []:
                cid = _stable_id(label, "consumer", c["repository"], c.get("evidence_id") or "")
                discovery = c.get("discovery_method") or xref.get("discovery_method") or "cross_repo"
                add_node(
                    {
                        "id": cid,
                        "type": "external_consumer",
                        "name": c["repository"],
                        "path": c.get("record_path"),
                        "repository": c["repository"],
                        "inclusion_reason": f"{discovery}:{c.get('relationship_type')}",
                        "evidence": c.get("compatibility_evidence") or [c.get("evidence_source") or ""],
                    }
                )
                producer_node = next(
                    (x["id"] for x in changed_symbols if x.get("repository") == label),
                    _stable_id(label, "repo"),
                )
                eid = _edge_id(producer_node, cid, "consumes")
                add_edge(
                    {
                        "id": eid,
                        "source": producer_node,
                        "target": cid,
                        "type": "consumes",
                        "evidence": [
                            c.get("evidence_source") or "",
                            *[f"{m['field']}={m['value']}" for m in (c.get("matched") or [])],
                        ],
                        "discovery_method": discovery,
                        "confidence": c.get("confidence") or "static",
                        "traversal_depth": 1,
                        "materiality": "high",
                        "status": "accepted",
                        "inclusion_reason": (
                            f"{c['repository']} as {c.get('relationship_type')} "
                            f"via {c.get('evidence_id')} ({discovery})"
                        ),
                        "deployment_order_known": c.get("deployment_order_known"),
                        "last_seen_revision": c.get("last_seen_revision"),
                    }
                )
                cat_evidence["cross_repository_consumers"].append(
                    f"resolved:{c['repository']}:{c.get('evidence_id')}"
                )
            for c in xref.get("candidates") or []:
                cat_evidence["cross_repository_consumers"].append(
                    f"candidate:{c['repository']}:{c.get('evidence_id')}"
                )
                notes.append(f"cross_repo_candidate:{c['repository']}")
        else:
            for u in xref.get("unresolved") or []:
                # Tighten required_for_complete_review when contracts/events touched
                if "contracts" in touched or "events_and_messages" in touched or signals.get("apis"):
                    u["required_for_complete_review"] = risk_band in ("medium", "high", "critical")
                unresolved.append(u)
            cat_evidence["cross_repository_consumers"].extend(
                xref.get("evidence_refs") or ["unresolved:consumers_unproven"]
            )

    # Paths (simple)
    for e in list(edges.values())[: budgets["max_paths"]]:
        if e["type"] == "called_by":
            paths.append(
                {
                    "id": "p_" + e["id"][2:],
                    "nodes": [e["source"], e["target"]],
                    "edges": [e["id"]],
                    "stopping_reason": "bounded_direct_caller",
                    "evidence": e["evidence"],
                    "confidence": e["confidence"],
                }
            )

    # Behaviour coverage heuristic
    behaviour_coverage = {}
    test_nodes = [n for n in nodes.values() if n["type"] == "test"]
    for b in BEHAVIOURS:
        if not test_nodes and "tests" not in touched and not cat_evidence["tests"]:
            behaviour_coverage[b] = {
                "status": "unresolved",
                "reason": "no tests mapped to changed symbols",
                "tests": [],
            }
        elif b == "successful_path" and test_nodes:
            behaviour_coverage[b] = {
                "status": "covered",
                "reason": "at least one referencing test found",
                "tests": [n["path"] for n in test_nodes[:5]],
            }
        elif b in ("migration_ordering", "rollback_path") and "persistence" not in touched:
            behaviour_coverage[b] = {
                "status": "not_applicable",
                "reason": "no persistence/migration touch detected",
                "tests": [],
            }
        else:
            behaviour_coverage[b] = {
                "status": "uncovered",
                "reason": "no behaviour-specific test mapping in v1 heuristic",
                "tests": [],
            }

    # Category status draft (completeness script refines gates)
    categories: dict[str, Any] = {}
    for c in CATEGORIES:
        refs = cat_evidence.get(c) or []
        if refs and all(str(r).startswith("not_applicable:") for r in refs):
            status = "not_applicable"
            reason = "no cross-repo producer signals in this change"
            conf = "medium"
        elif any(str(r).startswith("resolved:") for r in refs):
            status = "covered"
            reason = f"{sum(1 for r in refs if str(r).startswith('resolved:'))} resolved consumer(s)"
            conf = "high"
        elif refs and not all(str(r).startswith("unresolved:") or str(r).startswith("candidate:") for r in refs):
            status = "covered"
            reason = f"{len(refs)} evidence ref(s)"
            conf = "medium"
        elif c == "cross_repository_consumers" and any(str(r).startswith("unresolved:") for r in refs):
            status = "unresolved"
            reason = next(
                (str(u.get("reason")) for u in unresolved if u.get("category") == "cross_repository_consumers"),
                "no exact Evidence Index consumer match",
            )
            conf = "low"
        elif c not in touched and not refs:
            # N/A if clearly not implicated (avoid false incomplete from empty collectors)
            if c in (
                "events_and_messages",
                "remote_services",
                "persistence",
                "security_and_permissions",
                "deployment_and_compatibility",
                "contracts",
                "cross_repository_consumers",
                "framework_reachability",
                "configuration",
                "operational_side_effects",
                "inbound_callers",
                "outbound_dependencies",
                "upstream_entry_points",
            ):
                status = "not_applicable"
                reason = "no diff signal for this category in collected evidence"
                conf = "medium"
            elif c == "changed_symbols" and not changed_symbols:
                status = "unresolved"
                reason = "no changed symbols extracted"
                conf = "low"
            elif c == "tests":
                status = "not_applicable"
                reason = "no test mapping required or detected for this change"
                conf = "medium"
            else:
                status = "unresolved"
                reason = "collector found no evidence"
                conf = "low"
        else:
            status = "unresolved"
            reason = "only unresolved refs"
            conf = "low"
        categories[c] = {
            "status": status,
            "reason": reason,
            "discovery_method": (
                "evidence_index_exact_field_match"
                if c == "cross_repository_consumers"
                else "deterministic_collectors"
            ),
            "confidence": conf,
            "evidence_refs": refs[:20],
            "unresolved_details": "" if status != "unresolved" else reason,
        }

    if not changed_symbols and review_type == "implementation":
        categories["changed_symbols"] = {
            "status": "unresolved",
            "reason": "no parseable changed symbols in diffs",
            "discovery_method": "git_diff",
            "confidence": "low",
            "evidence_refs": [],
            "unresolved_details": "diff present but symbol extraction empty or non-Java",
        }

    if staged_readers:
        impact_doc = {
            "schema_version": "1",
            "readers": sorted(staged_readers.values(), key=lambda r: r.get("path") or ""),
        }
        (evid / "impact-readers.json").write_text(
            json.dumps(impact_doc, indent=2) + "\n", encoding="utf-8"
        )
        notes.append(f"impact_readers_staged:{len(staged_readers)}")
        collectors.append("stage_impact_readers")

    duration_ms = int((time.time() - t0) * 1000)
    graph = {
        "schema_version": "1",
        "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "risk_band": risk_band,
        "review_type": review_type,
        "changed_symbols": sorted(changed_symbols, key=lambda x: x["id"]),
        "nodes": sorted(nodes.values(), key=lambda x: x["id"]),
        "edges": sorted(edges.values(), key=lambda x: x["id"]),
        "paths": sorted(paths, key=lambda x: x["id"]),
        "unresolved_edges": unresolved[: budgets["max_unresolved"]],
        "categories": categories,
        "behaviour_coverage": behaviour_coverage,
        "metrics": {
            "duration_ms": duration_ms,
            "changed_symbols": len(changed_symbols),
            "nodes": len(nodes),
            "edges": len(edges),
            "paths": len(paths),
            "unresolved_edges": len(unresolved),
            "upstream_hops_budget": depth["upstream_hops"],
            "downstream_hops_budget": depth["downstream_hops"],
        },
        "collectors": sorted(set(collectors)),
        "notes": notes,
        "touched_categories": sorted(touched),
    }
    return graph


def evaluate_completeness(
    session_dir: pathlib.Path,
    graph: dict[str, Any] | None = None,
    waive: bool = False,
    waive_reason: str = "",
    approved_by: str = "",
) -> dict[str, Any]:
    evid = session_dir / "evidence"
    if graph is None:
        graph = json.loads((evid / "evidence-graph.json").read_text(encoding="utf-8"))
    gates = _load_json_config("completeness-gates.json")
    band = graph.get("risk_band") or "unknown"
    band_rules = gates.get("band_rules", {}).get(band) or {}
    defaults = gates.get("defaults") or {}
    material = set(gates.get("material_when_touched") or [])
    touched = set(graph.get("touched_categories") or [])

    categories_out = []
    blocks_seating = False
    blocks_verdict = False
    material_unresolved = []

    for c in CATEGORIES:
        info = (graph.get("categories") or {}).get(c) or {
            "status": "unresolved",
            "reason": "missing from graph",
            "discovery_method": "none",
            "confidence": "low",
            "evidence_refs": [],
        }
        status = info["status"]
        bs = False
        bv = False
        if status == "unresolved":
            key = f"unresolved_{c}"
            rule = band_rules.get(key) or {}
            # Only apply seating/verdict blocks when category is material for this change
            implicated = c in touched or c in material and any(
                u.get("category") == c and u.get("required_for_complete_review")
                for u in graph.get("unresolved_edges") or []
            )
            # cross-repo: use unresolved edge flag
            if c == "cross_repository_consumers":
                implicated = any(
                    u.get("category") == c and u.get("required_for_complete_review")
                    for u in graph.get("unresolved_edges") or []
                ) or c in touched
            if implicated or (c == "changed_symbols" and status == "unresolved"):
                bs = bool(rule.get("blocksSeating", defaults.get("blocksSeating", False)))
                bv = bool(rule.get("blocksCompleteVerdict", defaults.get("blocksCompleteVerdict", False)))
            if c == "changed_symbols" and status == "unresolved" and graph.get("review_type") == "implementation":
                # empty symbol extract should not hard-block seating for non-Java diffs
                bs = False
                bv = False
            if bs or bv:
                material_unresolved.append(
                    {"category": c, "reason": info.get("reason"), "blocksSeating": bs, "blocksCompleteVerdict": bv}
                )
        blocks_seating = blocks_seating or bs
        blocks_verdict = blocks_verdict or bv
        categories_out.append(
            {
                "category": c,
                "status": status,
                "reason": info.get("reason") or "",
                "discovery_method": info.get("discovery_method") or "deterministic_collectors",
                "confidence": info.get("confidence") or "low",
                "evidence_refs": info.get("evidence_refs") or [],
                "missing_evidence": [info.get("unresolved_details")] if status == "unresolved" else [],
                "blocksSeating": bs,
                "blocksCompleteVerdict": bv,
                "unresolved_details": info.get("unresolved_details") or "",
            }
        )

    # Also surface unresolved edges marked required
    for u in graph.get("unresolved_edges") or []:
        if u.get("required_for_complete_review"):
            key = f"unresolved_{u.get('category') or 'cross_repository_consumers'}"
            rule = band_rules.get(key) or {}
            bs = bool(rule.get("blocksSeating", False))
            bv = bool(rule.get("blocksCompleteVerdict", True))
            blocks_seating = blocks_seating or bs
            blocks_verdict = blocks_verdict or bv
            material_unresolved.append({**u, "blocksSeating": bs, "blocksCompleteVerdict": bv})

    waiver = {"used": bool(waive), "reason": waive_reason or "", "approved_by": approved_by or ""}
    ok = not blocks_seating or waive
    evidence_completeness = "incomplete" if (blocks_verdict or material_unresolved or any(
        c.get("status") == "unresolved" for c in categories_out
    )) else "complete"
    return {
        "schema_version": "1",
        "risk_band": band,
        "categories": categories_out,
        "blocks_seating": blocks_seating and not waive,
        "blocks_complete_verdict": blocks_verdict,
        "evidence_completeness": evidence_completeness,
        "ok_for_seating": ok,
        "waiver": waiver,
        "unresolved_edges_material": material_unresolved,
        "note": (
            "evidence_completeness is independent of defect findings. "
            "Combine with review_outcome at finalize (outcome.json)."
        ),
    }


def render_report(graph: dict[str, Any], completeness: dict[str, Any]) -> str:
    lines = [
        "# Evidence Graph Report",
        "",
        f"- Risk band: `{graph.get('risk_band')}`",
        f"- Changed symbols: {graph.get('metrics', {}).get('changed_symbols')}",
        f"- Nodes / edges: {graph.get('metrics', {}).get('nodes')} / {graph.get('metrics', {}).get('edges')}",
        f"- Unresolved edges: {graph.get('metrics', {}).get('unresolved_edges')}",
        f"- Seating OK: {completeness.get('ok_for_seating')}",
        f"- Blocks complete verdict: {completeness.get('blocks_complete_verdict')}",
        "",
        "## Changed symbols",
    ]
    for s in graph.get("changed_symbols") or []:
        lines.append(f"- `{s.get('change_kind')}` `{s.get('path')}#{s.get('name')}` ({s.get('confidence')})")
    lines.append("")
    lines.append("## Categories")
    for c in completeness.get("categories") or []:
        lines.append(
            f"- **{c['category']}**: {c['status']} - {c['reason']} "
            f"(seat_block={c['blocksSeating']}, verdict_block={c['blocksCompleteVerdict']})"
        )
    lines.append("")
    lines.append("## Unresolved edges")
    for u in graph.get("unresolved_edges") or []:
        lines.append(
            f"- {u.get('source')} / {u.get('relationship')}: {u.get('reason')} "
            f"(required={u.get('required_for_complete_review')})"
        )
    if not graph.get("unresolved_edges"):
        lines.append("- (none)")
    lines.append("")
    lines.append("## Explainability")
    lines.append("Every node/edge includes inclusion_reason or evidence refs in evidence-graph.json.")
    lines.append("")
    return "\n".join(lines) + "\n"
