"""Single source of truth loader for Yonko model selections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[3]
SELECTIONS_PATH = SKILL_ROOT / "config" / "model-selections.json"
SELECTIONS_SCHEMA_PATH = SKILL_ROOT / "contracts" / "model-selections.schema.json"


def _profile_error(category: str, message: str) -> Exception:
    from .resolve_profile import ProfileError

    return ProfileError(category, message)


def load_model_selections(path: Path | None = None) -> dict[str, Any]:
    p = path or SELECTIONS_PATH
    if not p.is_file():
        raise _profile_error("invalid_model_mapping", f"missing model-selections file: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise _profile_error("invalid_model_mapping", f"malformed model-selections: {e}") from e
    if not isinstance(data, dict):
        raise _profile_error("invalid_model_mapping", "model-selections must be an object")
    if data.get("schema_version") != 1:
        raise _profile_error("invalid_model_mapping", "model-selections.schema_version must be 1")
    if not isinstance(data.get("version"), str) or not data["version"].strip():
        raise _profile_error("invalid_model_mapping", "model-selections.version required")
    panels = data.get("panels")
    if not isinstance(panels, dict) or not panels:
        raise _profile_error("invalid_model_mapping", "model-selections.panels required")
    return data


def get_panel(panel_id: str, selections: dict[str, Any] | None = None) -> dict[str, Any]:
    data = selections or load_model_selections()
    panel = (data.get("panels") or {}).get(panel_id)
    if not isinstance(panel, dict):
        raise _profile_error("invalid_model_mapping", f"unknown model-selection panel: {panel_id}")
    seats = panel.get("seats")
    if not isinstance(seats, dict):
        raise _profile_error("invalid_model_mapping", f"panel {panel_id}: seats must be object")
    for seat in ("chair", "shanks", "blackbeard", "buggy", "luffy"):
        if seat not in seats:
            raise _profile_error("invalid_model_mapping", f"panel {panel_id}: missing seat {seat}")
    return panel


def seat_selection(panel_id: str, seat: str, selections: dict[str, Any] | None = None) -> dict[str, Any]:
    panel = get_panel(panel_id, selections)
    row = (panel.get("seats") or {}).get(seat)
    if not isinstance(row, dict):
        raise _profile_error("invalid_model_mapping", f"panel {panel_id}: missing seat {seat}")
    return row


def apply_panel_to_profile(profile: dict[str, Any], selections: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge panel model IDs into a profile copy. Profile must declare model_selection_panel."""
    panel_id = profile.get("model_selection_panel")
    if not panel_id:
        return profile
    data = selections or load_model_selections()
    panel = get_panel(str(panel_id), data)
    out = json.loads(json.dumps(profile))
    out["_model_selection_version"] = data["version"]
    out["_model_selection_panel"] = str(panel_id)
    seats = out.setdefault("seats", {})
    for seat, sel in (panel.get("seats") or {}).items():
        mapping = seats.setdefault(seat, {})
        if mapping.get("runtime") and mapping["runtime"] != sel.get("runtime"):
            raise _profile_error(
                "invalid_model_mapping",
                f"seat {seat}: profile runtime {mapping['runtime']!r} != selection {sel.get('runtime')!r}",
            )
        mapping["runtime"] = sel["runtime"]
        mapping.pop("model_policy_ref", None)
        mapping.pop("model_preference", None)
        model: dict[str, Any] = {
            "configured": sel["configured"],
            "display_name": sel.get("display_name"),
            "provider_hint": sel.get("provider_hint"),
            "resolve_mode": sel.get("resolve_mode") or "exact",
        }
        if sel.get("match_substrings"):
            model["match_substrings"] = list(sel["match_substrings"])
        if sel.get("resolved"):
            model["resolved"] = sel["resolved"]
        if sel.get("activation"):
            model["activation"] = sel["activation"]
        mapping["model"] = model
        if sel.get("notes") and not mapping.get("notes"):
            mapping["notes"] = sel["notes"]
    return out


def alternate_ids(selections: dict[str, Any] | None = None) -> dict[str, str]:
    data = selections or load_model_selections()
    out: dict[str, str] = {}
    for key, row in (data.get("alternates") or {}).items():
        if isinstance(row, dict) and row.get("configured"):
            out[str(key)] = str(row["configured"])
    return out
