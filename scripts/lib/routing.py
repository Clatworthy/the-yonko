"""V3.5 deterministic change classification and reviewer routing.

No AI. No graph engine. Policy owns seats; scripts classify; advisory tags
may only add seats from the closed class enum.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

KNOWN_SEATS = ("shanks", "blackbeard", "buggy", "luffy")
BAND_ORDER = ("trivial", "low", "medium", "high", "critical")

DEFAULT_CLOSED = (
    "auth",
    "database",
    "api",
    "infrastructure",
    "documentation",
    "configuration",
    "dependency",
    "concurrency",
    "performance",
    "billing",
    "async-messaging",
    "frontend",
)


def policy_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_routing_policy(path: Path) -> dict[str, Any]:
    """Parse routing-policy.yaml with a minimal indentation-aware subset reader."""
    text = path.read_text(encoding="utf-8")
    ph = policy_hash(text)
    lines = text.splitlines()
    root: dict[str, Any] = {
        "version": 1,
        "known_seats": list(KNOWN_SEATS),
        "closed_classes": list(DEFAULT_CLOSED),
        "band_baseline": {},
        "band_floor": {},
        "band_require_verifier": [],
        "classes": {},
        "signals": {},
        "_raw": text,
        "_hash": ph,
    }

    stack: list[tuple[int, str, Any]] = [(-1, "root", root)]
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        while stack and indent <= stack[-1][0] and stack[-1][1] != "root":
            stack.pop()
        parent = stack[-1][2]

        if line.endswith(":") and not line.startswith("- "):
            key = line[:-1].strip()
            # Peek: list of scalars vs mapping
            nxt = None
            for j in range(i, len(lines)):
                peek = lines[j]
                if not peek.strip() or peek.lstrip().startswith("#"):
                    continue
                nxt = peek
                break
            if nxt is not None:
                nindent = len(nxt) - len(nxt.lstrip(" "))
                nstrip = nxt.strip()
                if nindent > indent and nstrip.startswith("- "):
                    parent[key] = []
                    stack.append((indent, key, parent[key]))
                    continue
                if nindent > indent and nstrip.endswith(":") and not nstrip.startswith("- "):
                    parent[key] = {}
                    stack.append((indent, key, parent[key]))
                    continue
            parent[key] = {}
            stack.append((indent, key, parent[key]))
            continue

        if line.startswith("- "):
            item = line[2:].strip()
            if isinstance(parent, list):
                if ":" in item and not item.startswith('"') and not item.startswith("'"):
                    # mapping list item like path: "..."
                    k, _, v = item.partition(":")
                    parent.append({k.strip(): _scalar(v.strip())})
                else:
                    parent.append(_scalar(item))
            continue

        if ":" in line:
            k, _, v = line.partition(":")
            key = k.strip()
            val = v.strip()
            if isinstance(parent, dict):
                if val == "":
                    parent[key] = {}
                    stack.append((indent, key, parent[key]))
                else:
                    parent[key] = _scalar(val)
            continue

    # Normalise classes / signals structure from nested parse quirks
    root["classes"] = _normalise_classes(root.get("classes") or {})
    root["signals"] = _normalise_signals(root.get("signals") or {})
    root["band_baseline"] = {
        k: [str(x) for x in (v if isinstance(v, list) else [])]
        for k, v in (root.get("band_baseline") or {}).items()
    }
    root["band_floor"] = {
        k: int(v) for k, v in (root.get("band_floor") or {}).items()
    }
    brv = root.get("band_require_verifier") or []
    if isinstance(brv, list):
        root["band_require_verifier"] = [str(x) for x in brv]
    if not root.get("closed_classes"):
        root["closed_classes"] = list(DEFAULT_CLOSED)
    else:
        root["closed_classes"] = [str(x) for x in root["closed_classes"]]
    root["known_seats"] = [str(x) for x in (root.get("known_seats") or list(KNOWN_SEATS))]
    root["_hash"] = ph
    return root


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


def _normalise_classes(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, body in raw.items():
        if not isinstance(body, dict):
            continue
        seats = body.get("seats") or []
        if isinstance(seats, dict):
            seats = list(seats.keys())
        hints = body.get("focus_hints") or []
        if isinstance(hints, dict):
            hints = list(hints.keys())
        out[str(name)] = {
            "seats": [str(s) for s in seats],
            "require_verifier": bool(body.get("require_verifier", False)),
            "reason": str(body.get("reason") or f"Class {name}"),
            "focus_hints": [str(h) for h in hints],
        }
    return out


def _normalise_signals(raw: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for name, body in raw.items():
        items: list[dict[str, str]] = []
        if isinstance(body, list):
            for it in body:
                if isinstance(it, dict):
                    items.append({str(k): str(v) for k, v in it.items()})
                elif isinstance(it, str):
                    items.append({"path": it})
        elif isinstance(body, dict):
            # parser may have turned list into dict of path keys
            if "path" in body:
                items.append({"path": str(body["path"])})
            else:
                for k, v in body.items():
                    if k == "path" or isinstance(v, str):
                        items.append({"path": str(v) if k == "path" else str(k)})
        out[str(name)] = items
    return out


def merge_local_policy(base: dict[str, Any], local_path: Path | None) -> dict[str, Any]:
    if not local_path or not local_path.exists():
        return base
    local = load_routing_policy(local_path)
    # Local overrides class defs and signals; keep closed set union
    merged = dict(base)
    merged["classes"] = {**base.get("classes", {}), **local.get("classes", {})}
    merged["signals"] = {**base.get("signals", {}), **local.get("signals", {})}
    for key in ("band_baseline", "band_floor"):
        if local.get(key):
            merged[key] = {**base.get(key, {}), **local[key]}
    if local.get("band_require_verifier"):
        merged["band_require_verifier"] = list(
            dict.fromkeys(
                list(base.get("band_require_verifier") or [])
                + list(local.get("band_require_verifier") or [])
            )
        )
    closed = list(dict.fromkeys(list(base.get("closed_classes") or []) + list(local.get("closed_classes") or [])))
    merged["closed_classes"] = closed
    # Hash both files for reproducibility
    merged["_hash"] = hashlib.sha256(
        ((base.get("_raw") or "") + "\n" + (local.get("_raw") or "")).encode("utf-8")
    ).hexdigest()
    merged["version"] = max(int(base.get("version") or 1), int(local.get("version") or 1))
    return merged


def evidence_blob(session_dir: Path) -> tuple[str, list[str]]:
    evid = session_dir / "evidence"
    repos_path = evid / "repos.json"
    if not repos_path.exists():
        raise FileNotFoundError("evidence/repos.json missing - run collect-evidence.sh first")
    repos = json.loads(repos_path.read_text(encoding="utf-8"))["repos"]
    texts: list[str] = []
    paths: list[str] = []
    for r in repos:
        patch = evid / r["patch"]
        text = patch.read_text(encoding="utf-8", errors="replace") if patch.exists() else ""
        texts.append(text)
        for line in text.splitlines():
            if line.startswith("+++ b/") or line.startswith("diff --git") or line.startswith("--- a/"):
                paths.append(line)
                m = re.search(r"[ab]/(.+)$", line)
                if m:
                    paths.append(m.group(1))
    return "\n".join(texts + paths), paths


def classify_change(
    session_dir: Path,
    policy: dict[str, Any],
    advisory: list[str] | None = None,
) -> dict[str, Any]:
    closed = set(policy.get("closed_classes") or DEFAULT_CLOSED)
    blob, _paths = evidence_blob(session_dir)
    classes: list[str] = []
    reasons: list[dict[str, Any]] = []
    seen: set[str] = set()

    for cls, signals in (policy.get("signals") or {}).items():
        if cls not in closed:
            continue
        for sig in signals:
            pat = sig.get("path") or sig.get("content")
            if not pat:
                continue
            if re.search(pat, blob):
                if cls not in seen:
                    seen.add(cls)
                    classes.append(cls)
                    reason = (policy.get("classes") or {}).get(cls, {}).get("reason") or f"Matched signal for {cls}"
                    reasons.append(
                        {
                            "class": cls,
                            "reason": reason,
                            "source": "signal",
                            "path_hint": pat,
                        }
                    )
                break

    dropped: list[str] = []
    advisory_kept: list[str] = []
    for raw in advisory or []:
        tag = str(raw).strip().lower()
        if not tag:
            continue
        if tag not in closed:
            dropped.append(tag)
            continue
        advisory_kept.append(tag)
        if tag not in seen:
            seen.add(tag)
            classes.append(tag)
            reason = (policy.get("classes") or {}).get(tag, {}).get("reason") or f"Advisory class {tag}"
            reasons.append({"class": tag, "reason": reason, "source": "advisory"})

    classes.sort()
    advisory_kept = sorted(set(advisory_kept))
    return {
        "schema_version": "1",
        "policy_version": int(policy.get("version") or 1),
        "policy_hash": str(policy.get("_hash") or ""),
        "classes": classes,
        "reasons": reasons,
        "advisory_classes": advisory_kept,
        "dropped_advisory": dropped,
    }


def luffy_available(config_dir: Path, project_root: str | None = None) -> bool:
    """True when a matched adapter enables Luffy (shipped default is false; local overlay may enable)."""
    for name in ("project-adapters.local.yaml", "project-adapters.yaml"):
        path = config_dir / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        # Prefer local file if it explicitly enables
        if re.search(r"(?m)^\s*enabled:\s*true\s*$", text) and re.search(
            r"(?m)^\s*luffy:\s*$", text
        ):
            # Heuristic: if path_contains present and project_root given, require match
            m = re.search(r'path_contains:\s*["\']?([^"\'\n]+)', text)
            if m and project_root:
                needle = m.group(1).strip()
                if needle and needle not in project_root:
                    continue
            return True
    return False


def route_reviewers(
    change_classes: dict[str, Any],
    risk: dict[str, Any],
    policy: dict[str, Any],
    *,
    luffy_ok: bool,
) -> dict[str, Any]:
    band = str(risk.get("band") or risk.get("risk_band") or risk.get("risk") or "trivial").lower()
    if band not in BAND_ORDER:
        band = "trivial"

    known = set(policy.get("known_seats") or KNOWN_SEATS)
    for s in known:
        if s not in KNOWN_SEATS:
            raise ValueError(f"unknown seat in policy known_seats: {s}")

    baseline = list((policy.get("band_baseline") or {}).get(band) or ["blackbeard"])
    floor = int((policy.get("band_floor") or {}).get(band) or len(baseline) or 1)
    # Without Luffy adapter, high/critical cannot reach 4 seats - clamp floor.
    max_possible = 4 if luffy_ok else 3
    effective_floor = min(floor, max_possible)

    seats: list[str] = []
    reasons: list[dict[str, Any]] = []
    focus: dict[str, list[str]] = {}
    # Prefer verification-policy.require_verifier_bands (authoritative).
    verify_bands = set(policy.get("_verify_bands") or policy.get("band_require_verifier") or [])
    require_verifier = band in verify_bands
    class_cfgs = policy.get("classes") or {}
    applied = list(change_classes.get("classes") or [])
    advisory_set = set(change_classes.get("advisory_classes") or [])

    def add_seat(seat: str, reason: str, source: str, cls: str | None = None) -> None:
        nonlocal seats
        if seat not in known:
            raise ValueError(f"policy referenced unknown seat: {seat}")
        if seat == "luffy" and not luffy_ok:
            return
        if seat not in seats:
            seats.append(seat)
        entry: dict[str, Any] = {"seat": seat, "reason": reason, "source": source}
        if cls:
            entry["class"] = cls
        # Prefer class reasons over duplicates of same seat+source
        if not any(r.get("seat") == seat and r.get("reason") == reason for r in reasons):
            reasons.append(entry)

    # Band baseline first (stable order)
    for s in baseline:
        add_seat(s, f"risk band {band} baseline", "band_baseline")

    # Class seats (union)
    for cls in applied:
        cfg = class_cfgs.get(cls)
        if not cfg:
            continue
        for s in cfg.get("seats") or []:
            if s not in known:
                raise ValueError(f"class {cls} references unknown seat: {s}")
            src = "advisory" if cls in advisory_set else "class"
            add_seat(s, str(cfg.get("reason") or cls), src, cls)
        if cfg.get("require_verifier"):
            require_verifier = True
        for h in cfg.get("focus_hints") or []:
            focus.setdefault(cls, []).append(str(h))

    # Pad to effective floor from baseline order (then remaining known seats)
    pad_order = list(baseline) + [s for s in KNOWN_SEATS if s not in baseline]
    padded: list[str] = []
    for s in pad_order:
        if len(seats) >= effective_floor:
            break
        before = len(seats)
        add_seat(
            s,
            f"padded to band floor {effective_floor} ({band})",
            "band_pad",
        )
        if len(seats) > before:
            padded.append(s)

    # Preserve council order
    order = {s: i for i, s in enumerate(KNOWN_SEATS)}
    seats = sorted(seats, key=lambda s: order.get(s, 99))

    luffy_omitted = ("luffy" in baseline or any(
        "luffy" in (class_cfgs.get(c, {}).get("seats") or []) for c in applied
    )) and not luffy_ok and "luffy" not in seats

    return {
        "schema_version": "1",
        "policy_version": int(policy.get("version") or 1),
        "policy_hash": str(policy.get("_hash") or ""),
        "risk_band": band,
        "seats": seats,
        "require_verifier": bool(require_verifier),
        "reasons": reasons,
        "padded_from_band": padded,
        "focus_hints": focus,
        "luffy_available": bool(luffy_ok),
        "luffy_omitted": bool(luffy_omitted),
        "classes_applied": applied,
        "band_floor": floor,
        "effective_floor": effective_floor,
    }


def explain_routing(routing: dict[str, Any]) -> str:
    lines = ["=== Selected reviewers (V3.5 routing) ==="]
    by_seat: dict[str, list[str]] = {}
    for r in routing.get("reasons") or []:
        seat = r.get("seat")
        if not seat:
            continue
        bit = r.get("reason") or ""
        src = r.get("source") or ""
        cls = r.get("class")
        if cls:
            bit = f"{bit} (class: {cls}, source: {src})"
        else:
            bit = f"{bit} (source: {src})"
        by_seat.setdefault(str(seat), []).append(bit)
    for seat in routing.get("seats") or []:
        lines.append(f"  {seat}")
        for reason in by_seat.get(seat) or ["(no reason recorded)"]:
            lines.append(f"    - {reason}")
    lines.append(f"require_verifier: {routing.get('require_verifier')}")
    if routing.get("padded_from_band"):
        lines.append(f"padded_from_band: {', '.join(routing['padded_from_band'])}")
    if routing.get("luffy_omitted"):
        lines.append("luffy_omitted: true (adapter disabled or path not matched)")
    if routing.get("classes_applied"):
        lines.append(f"classes: {', '.join(routing['classes_applied'])}")
    return "\n".join(lines) + "\n"


def _require_verifier_bands_from_verification(config_dir: Path) -> list[str]:
    path = config_dir / "verification-policy.yaml"
    if not path.exists():
        return []
    bands: list[str] = []
    in_list = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip() == "require_verifier_bands:":
            in_list = True
            continue
        if not in_list:
            continue
        if raw and not raw.startswith(" ") and not raw.strip().startswith("-"):
            break
        if raw.strip().startswith("- "):
            bands.append(raw.strip()[2:].strip())
    return bands


def load_policy_pair(config_dir: Path) -> dict[str, Any]:
    base = load_routing_policy(config_dir / "routing-policy.yaml")
    merged = merge_local_policy(base, config_dir / "routing-policy.local.yaml")
    verify_bands = _require_verifier_bands_from_verification(config_dir)
    if verify_bands:
        merged["_verify_bands"] = verify_bands
        merged["band_require_verifier"] = verify_bands
    return merged
