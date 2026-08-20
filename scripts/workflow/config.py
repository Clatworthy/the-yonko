"""Load workflow-policy.yaml (stdlib-only, minimal parser for our keys)."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_SKILL = Path(__file__).resolve().parents[2]
_POLICY = _SKILL / "config" / "workflow-policy.yaml"
_REVIEW_TYPES = _SKILL / "config" / "review-types.yaml"
_ROUTING = _SKILL / "config" / "routing-policy.yaml"
_VERIFICATION = _SKILL / "config" / "verification-policy.yaml"


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Minimal YAML subset: scalars, lists, nested maps of scalars/lists."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    list_key: str | None = None
    list_indent = -1

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
            list_key = None
        parent = stack[-1][1]

        if line.startswith("- "):
            val = line[2:].strip().strip('"').strip("'")
            if list_key and list_key in parent and isinstance(parent[list_key], list):
                parent[list_key].append(_coerce(val))
            continue

        if ":" in line:
            key, _, rest = line.partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest == "":
                parent[key] = {}
                stack.append((indent, parent[key]))
                list_key = None
            elif rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1].strip()
                items = [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
                # inline map like { a: 1, b: 2 }
                if ":" in rest and rest.startswith("{"):
                    parent[key] = _parse_inline_map(rest)
                else:
                    parent[key] = [_coerce(x) for x in items]
                list_key = None
            elif rest.startswith("{") and rest.endswith("}"):
                parent[key] = _parse_inline_map(rest)
                list_key = None
            else:
                parent[key] = _coerce(rest.strip('"').strip("'"))
                list_key = key if False else None
                # following list items under this key
                if rest == "" or rest is None:
                    pass
            # Prepare for nested list under key when next lines are "- "
            if rest == "":
                list_key = None
            else:
                # Allow subsequent "- " under same indent+2 for list-valued keys we create empty
                pass
    return root


def _parse_inline_map(s: str) -> dict[str, Any]:
    s = s.strip()[1:-1]
    out: dict[str, Any] = {}
    for part in s.split(","):
        if ":" not in part:
            continue
        k, _, v = part.partition(":")
        out[k.strip()] = _coerce(v.strip().strip('"').strip("'"))
    return out


def _coerce(v: str) -> Any:
    if v in ("true", "True"):
        return True
    if v in ("false", "False"):
        return False
    if v in ("null", "None", "~"):
        return None
    try:
        return int(v)
    except ValueError:
        return v


def load_policy() -> dict[str, Any]:
    if not _POLICY.exists():
        out = {
            "default_mode": "enforce",
            "env_override": "YONKO_WORKFLOW_MODE",
            "authoritative_guards": [],
            "seating": {"implementation": {}},
            "max_confirmation_rounds": {"plan": 1, "document": 1},
            "verify_required_bands": ["medium", "high", "critical"],
        }
    else:
        text = _POLICY.read_text(encoding="utf-8")
        out = _load_policy_structured(text)
    # Authoritative seating floors: routing-policy band_floor (implementation)
    impl_floors = _band_floor_from_routing()
    if impl_floors:
        out["seating"]["implementation"] = impl_floors
    # plan/document floors from review-types.yaml
    out["seating"].update(_seating_from_review_types())
    # Authoritative verify bands: verification-policy.require_verifier_bands
    verify_bands = _require_verifier_bands()
    if verify_bands:
        out["verify_required_bands"] = verify_bands
    elif not out.get("verify_required_bands"):
        out["verify_required_bands"] = ["medium", "high", "critical"]
    return out


def _load_policy_structured(text: str) -> dict[str, Any]:
    """Structured parse tuned to workflow-policy.yaml shape."""
    out: dict[str, Any] = {
        "default_mode": "enforce",
        "env_override": "YONKO_WORKFLOW_MODE",
        "authoritative_guards": [],
        "seating": {"implementation": {}},
        "max_confirmation_rounds": {"plan": 1, "document": 1},
        "verify_required_bands": [],
    }
    section = None
    sub = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0 and line.endswith(":") and not line.startswith("-"):
            section = line[:-1]
            sub = None
            continue
        if line.startswith("- ") and section == "authoritative_guards":
            out["authoritative_guards"].append(line[2:].strip())
            continue
        # Legacy: still parse if present in old local overrides
        if line.startswith("- ") and section == "verify_required_bands":
            out["verify_required_bands"].append(line[2:].strip())
            continue
        if section == "seating" and indent == 2 and line.endswith(":"):
            sub = line[:-1]
            out["seating"].setdefault(sub, {})
            continue
        if section == "seating" and sub and ":" in line and indent >= 4:
            k, _, v = line.partition(":")
            out["seating"][sub][k.strip()] = int(v.strip())
            continue
        if section == "max_confirmation_rounds" and ":" in line:
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k in ("plan", "document") and v.isdigit():
                out["max_confirmation_rounds"][k] = int(v)
            continue
        if indent == 0 and ":" in line:
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip('"')
            if k in ("default_mode", "env_override", "principle", "information_preservation"):
                out[k] = v
                section = None
                sub = None
    return out


def _band_floor_from_routing() -> dict[str, int]:
    if not _ROUTING.exists():
        return {}
    floors: dict[str, int] = {}
    in_floor = False
    for raw in _ROUTING.read_text(encoding="utf-8").splitlines():
        if raw.strip() == "band_floor:":
            in_floor = True
            continue
        if not in_floor:
            continue
        if raw and not raw.startswith(" ") and raw.strip().endswith(":"):
            break
        if raw.startswith(" ") and ":" in raw.strip() and not raw.strip().startswith("-"):
            k, _, v = raw.strip().partition(":")
            v = v.strip()
            if v.isdigit():
                floors[k.strip()] = int(v)
    return floors


def _require_verifier_bands() -> list[str]:
    if not _VERIFICATION.exists():
        return []
    bands: list[str] = []
    in_list = False
    for raw in _VERIFICATION.read_text(encoding="utf-8").splitlines():
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


def _seating_from_review_types() -> dict[str, dict[str, int]]:
    if not _REVIEW_TYPES.exists():
        return {}
    text = _REVIEW_TYPES.read_text(encoding="utf-8")
    seating: dict[str, dict[str, int]] = {}
    section = None
    rtype = None
    in_seating = False
    for raw in text.splitlines():
        if raw.strip() == "seating:":
            in_seating = True
            continue
        if not in_seating:
            continue
        if raw.startswith("omission_hunt:") or (raw and not raw.startswith(" ") and raw.strip().endswith(":") and "seating" not in raw):
            if raw.strip() and not raw.startswith(" ") and raw.strip() != "seating:":
                break
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 2 and line.endswith(":") and not line.startswith("budgets"):
            rtype = line[:-1]
            seating[rtype] = {}
            continue
        if rtype and indent >= 4 and ":" in line and not line.startswith("budgets"):
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if v.isdigit():
                seating[rtype][k] = int(v)
    return seating


def resolve_mode(existing_workflow: dict[str, Any] | None = None) -> str:
    policy = load_policy()
    env_name = policy.get("env_override") or "YONKO_WORKFLOW_MODE"
    env = (os.environ.get(env_name) or "").strip().lower()
    if env in ("shadow", "enforce"):
        return env
    if existing_workflow and existing_workflow.get("mode") in ("shadow", "enforce"):
        return str(existing_workflow["mode"])
    return str(policy.get("default_mode") or "enforce")


def min_seats(review_type: str, band: str) -> int:
    policy = load_policy()
    seating = policy.get("seating") or {}
    table = seating.get(review_type) or seating.get("implementation") or {}
    return int(table.get(band) or table.get("medium") or 2)


def max_confirmation_rounds(review_type: str) -> int:
    policy = load_policy()
    m = policy.get("max_confirmation_rounds") or {}
    if review_type in ("plan", "document"):
        return int(m.get(review_type) or 1)
    return 10**9


def verify_required(band: str) -> bool:
    policy = load_policy()
    bands = policy.get("verify_required_bands") or ["medium", "high", "critical"]
    return band.lower() in {b.lower() for b in bands}


def authoritative_codes() -> set[str]:
    policy = load_policy()
    return set(policy.get("authoritative_guards") or [])
