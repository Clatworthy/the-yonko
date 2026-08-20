"""Resolve, validate, and freeze Yonko execution profiles."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import DEFAULT_PROFILE_ID, REQUIRED_SEATS, SUPPORTED_RUNTIMES
from .repository_exploration import review_mode_for

SKILL_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = SKILL_ROOT / "config"
PROFILES_DIR = CONFIG_DIR / "execution-profiles"
MARKER_PATH = CONFIG_DIR / "execution-profile.json"
MODEL_POLICY_PATH = CONFIG_DIR / "model-policy.yaml"
CONTRACTS_DIR = SKILL_ROOT / "contracts"
PROFILE_SCHEMA_PATH = CONTRACTS_DIR / "execution-profile.schema.json"


class ProfileError(Exception):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ProfileError("invalid_profile", f"malformed JSON: {path}: {e}") from e


def read_marker(marker_path: Path | None = None) -> dict[str, Any]:
    path = marker_path or MARKER_PATH
    if not path.is_file():
        return {"schema_version": 1, "executionProfile": DEFAULT_PROFILE_ID}
    data = _load_json(path)
    if not isinstance(data, dict):
        raise ProfileError("invalid_profile", f"marker must be an object: {path}")
    if "executionProfile" not in data:
        return {"schema_version": 1, "executionProfile": DEFAULT_PROFILE_ID}
    pid = data.get("executionProfile")
    if not isinstance(pid, str) or not pid.strip():
        raise ProfileError("invalid_profile", "executionProfile must be a non-empty string")
    return data


def profile_path(profile_id: str, profiles_dir: Path | None = None) -> Path:
    return (profiles_dir or PROFILES_DIR) / f"{profile_id}.json"


def list_profile_ids(profiles_dir: Path | None = None) -> list[str]:
    root = profiles_dir or PROFILES_DIR
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.json"))


def load_profile(profile_id: str, profiles_dir: Path | None = None) -> dict[str, Any]:
    path = profile_path(profile_id, profiles_dir)
    if not path.is_file():
        raise ProfileError("invalid_profile", f"unknown execution profile: {profile_id}")
    data = _load_json(path)
    if data.get("id") != profile_id:
        raise ProfileError(
            "invalid_profile",
            f"profile id mismatch: file {profile_id} declares id={data.get('id')}",
        )
    if data.get("model_selection_panel"):
        from .model_selections import apply_panel_to_profile

        data = apply_panel_to_profile(data)
    validate_profile(data)
    return data


def validate_profile(profile: dict[str, Any]) -> None:
    if not isinstance(profile, dict):
        raise ProfileError("invalid_profile", "profile must be an object")
    if profile.get("schema_version") != 1:
        raise ProfileError("invalid_profile", "schema_version must be 1")
    pid = profile.get("id")
    if not isinstance(pid, str) or not pid:
        raise ProfileError("invalid_profile", "profile.id required")
    if profile.get("fallback_policy") != "none":
        raise ProfileError("invalid_profile", "fallback_policy must be 'none' in v1")
    seats = profile.get("seats")
    if not isinstance(seats, dict):
        raise ProfileError("invalid_profile", "seats must be an object")
    for seat in REQUIRED_SEATS:
        if seat not in seats:
            raise ProfileError("invalid_profile", f"missing required seat: {seat}")
    seen: set[str] = set()
    for seat, mapping in seats.items():
        if seat in seen:
            raise ProfileError("invalid_profile", f"duplicate seat: {seat}")
        seen.add(seat)
        if not isinstance(mapping, dict):
            raise ProfileError("invalid_model_mapping", f"seat {seat}: mapping must be object")
        if "runtime" not in mapping:
            raise ProfileError("invalid_model_mapping", f"seat {seat}: missing runtime")
        runtime = mapping.get("runtime")
        if runtime not in SUPPORTED_RUNTIMES:
            raise ProfileError(
                "invalid_model_mapping",
                f"seat {seat}: unsupported runtime {runtime!r}",
            )
        if runtime == "opencode":
            try:
                review_mode_for(mapping, runtime)
            except ValueError as error:
                raise ProfileError("invalid_profile", str(error)) from error
            model = mapping.get("model")
            if not isinstance(model, dict):
                raise ProfileError(
                    "invalid_model_mapping",
                    f"seat {seat}: opencode requires model object (set model_selection_panel or inline model)",
                )
            if not model.get("configured") and not model.get("match_substrings"):
                raise ProfileError(
                    "invalid_model_mapping",
                    f"seat {seat}: opencode model needs configured id or match_substrings",
                )
        else:
            has_ref = bool(mapping.get("model_policy_ref"))
            has_cfg = bool((mapping.get("model") or {}).get("configured"))
            if not has_ref and not has_cfg:
                raise ProfileError(
                    "invalid_model_mapping",
                    f"seat {seat}: cursor seat needs model_policy_ref or configured model",
                )


def fingerprint_profile(profile: dict[str, Any]) -> str:
    raw = json.dumps(profile, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolve_active_profile(
    marker_path: Path | None = None,
    profiles_dir: Path | None = None,
) -> dict[str, Any]:
    marker = read_marker(marker_path)
    pid = marker.get("executionProfile") or DEFAULT_PROFILE_ID
    return load_profile(pid, profiles_dir)


def _parse_model_policy_prefer(text: str, ref: str) -> list[str]:
    lines = text.splitlines()
    if ref == "chair.prefer":
        in_chair = False
        for line in lines:
            if re.match(r"^chair:\s*$", line):
                in_chair = True
                continue
            if in_chair and re.match(r"^[a-zA-Z]", line):
                break
            if in_chair:
                m = re.match(r"^\s+prefer:\s*\[(.*)\]\s*$", line)
                if m:
                    return [x.strip().strip("\"'") for x in m.group(1).split(",") if x.strip()]
        return ["composer", "grok"]

    m = re.match(r"^families\.(\w+)(?:\.(prefer_order|prefer))?$", ref)
    if not m:
        raise ProfileError("invalid_model_mapping", f"unsupported model_policy_ref: {ref}")
    family = m.group(1)
    in_family = False
    prefer: list[str] = []
    prefer_order: list[str] = []
    in_prefer_order = False
    for line in lines:
        if re.match(rf"^\s{{2}}{re.escape(family)}:\s*$", line):
            in_family = True
            in_prefer_order = False
            continue
        if in_family and re.match(r"^\s{2}\w+:\s*$", line):
            break
        if in_family and re.match(r"^[a-zA-Z]", line):
            break
        if not in_family:
            continue
        pm = re.match(r"^\s+prefer:\s*\[(.*)\]\s*$", line)
        if pm:
            prefer = [x.strip().strip("\"'") for x in pm.group(1).split(",") if x.strip()]
            continue
        if re.match(r"^\s+prefer_order:\s*$", line):
            in_prefer_order = True
            continue
        if in_prefer_order:
            im = re.match(r"^\s+-\s+(\S+)\s*$", line)
            if im:
                prefer_order.append(im.group(1))
            elif re.match(r"^\s+\w+:", line):
                in_prefer_order = False
    if prefer_order:
        return prefer_order
    if prefer:
        return prefer
    defaults = {
        "buggy": ["grok"],
        "shanks": ["luna", "terra", "sol"],
        "blackbeard": ["deepseek", "flash", "sonnet", "fable", "opus"],
        "luffy": ["kimi", "composer"],
    }
    if family in defaults:
        return defaults[family]
    raise ProfileError("invalid_model_mapping", f"no prefer list for model_policy_ref: {ref}")


def resolve_cursor_model_label(
    mapping: dict[str, Any],
    model_policy_path: Path | None = None,
) -> str:
    model = mapping.get("model") or {}
    if isinstance(model, dict) and model.get("configured"):
        configured = str(model["configured"])
        # Cursor Auto is orchestration - never expand into a provider family.
        if configured.lower() == "auto" or (model.get("resolve_mode") == "literal"):
            return str(model.get("resolved") or configured)
        return configured
    ref = mapping.get("model_policy_ref")
    if not ref:
        raise ProfileError("invalid_model_mapping", "cursor seat missing model_policy_ref")
    path = model_policy_path or MODEL_POLICY_PATH
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    prefer = _parse_model_policy_prefer(text, ref) if text else []
    if not prefer:
        raise ProfileError("invalid_model_mapping", f"empty prefer for {ref}")
    preference = mapping.get("model_preference") or "default"
    if preference == "strongest":
        return prefer[-1]
    return prefer[0]


def match_opencode_model(mapping: dict[str, Any], available: list[str]) -> str:
    """Resolve OpenCode model. Exact configured id preferred. Never silent substitute.

    Priority:
      1. configured id present in available (or available empty → trust configured)
      2. match_substrings with exactly one hit
      3. clear failure (missing or ambiguous)
    """
    model = mapping.get("model") or {}
    configured = model.get("configured")
    if configured:
        configured_s = str(configured)
        if available and configured_s not in available:
            # Exact-id miss: do not fall through to Flash/Qwen/etc.
            raise ProfileError(
                "model_unavailable",
                f"configured model {configured_s!r} not in opencode models list "
                f"(no silent substitute). Run `opencode models` and update "
                f"config/model-selections.json.",
            )
        return configured_s
    needles = [str(s).lower() for s in (model.get("match_substrings") or [])]
    if not needles:
        raise ProfileError(
            "invalid_model_mapping",
            "opencode model has no configured id or match_substrings",
        )
    hits = []
    for mid in available:
        low = mid.lower()
        if all(n in low for n in needles):
            hits.append(mid)
    if not hits:
        display = model.get("display_name") or needles
        raise ProfileError(
            "model_unavailable",
            f"no opencode model matched {display!r}; run `opencode models` and set "
            f"model.configured in config/model-selections.json (no silent substitute)",
        )
    if len(hits) > 1:
        raise ProfileError(
            "model_unavailable",
            f"ambiguous opencode model match for {needles!r}: {hits}. "
            f"Set an exact model.configured id (no silent pick).",
        )
    return hits[0]


def freeze_profile_into_session(
    session_dir: Path,
    profile: dict[str, Any] | None = None,
    *,
    opencode_models: list[str] | None = None,
    model_policy_path: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    session_dir = Path(session_dir)
    session_path = session_dir / "session.json"
    if not session_path.is_file():
        raise ProfileError("invalid_profile", f"missing session.json in {session_dir}")

    session = _load_json(session_path)
    existing = session.get("execution_profile")
    if isinstance(existing, dict) and existing.get("frozen") and not force:
        return existing

    profile = profile or resolve_active_profile()
    if profile.get("model_selection_panel") and "_model_selection_version" not in profile:
        from .model_selections import apply_panel_to_profile

        profile = apply_panel_to_profile(profile)
    fp = fingerprint_profile(profile)
    seats_out: list[dict[str, Any]] = []
    for seat, mapping in profile["seats"].items():
        runtime = mapping["runtime"]
        model_obj = mapping.get("model") or {}
        configured = None
        if isinstance(model_obj, dict) and model_obj.get("configured"):
            configured = str(model_obj["configured"])
        if runtime == "cursor":
            resolved = resolve_cursor_model_label(mapping, model_policy_path)
            if configured is None:
                configured = resolved
        else:
            available = opencode_models if opencode_models is not None else []
            if configured:
                if available:
                    resolved = match_opencode_model(mapping, available)
                else:
                    resolved = configured
            elif available:
                resolved = match_opencode_model(mapping, available)
                configured = resolved
            else:
                needles = model_obj.get("match_substrings") or []
                resolved = f"unresolved:{'+'.join(needles)}" if needles else "unresolved"
                configured = configured or resolved
        seats_out.append(
            {
                "seat": seat,
                "runtime": runtime,
                "model": resolved,
                "configured_model": configured,
                "resolved_model": resolved,
                "model_policy_ref": mapping.get("model_policy_ref"),
                "model_preference": mapping.get("model_preference"),
                "display_name": (mapping.get("model") or {}).get("display_name"),
                "read_only": bool(mapping.get("read_only", runtime == "opencode")),
                "review_mode": review_mode_for(mapping, runtime),
                "timeout_sec": mapping.get("timeout_sec"),
                "activation": (mapping.get("model") or {}).get("activation"),
            }
        )

    freeze = {
        "schema_version": 1,
        "frozen": True,
        "executionProfile": profile["id"],
        "profile_status": profile.get("status"),
        "profile_fingerprint": fp,
        "fallback_policy": profile.get("fallback_policy", "none"),
        "model_selection_panel": profile.get("model_selection_panel")
        or profile.get("_model_selection_panel"),
        "model_selection_version": profile.get("_model_selection_version"),
        "seats": seats_out,
    }
    session["execution_profile"] = freeze
    session_path.write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")

    evid = session_dir / "evidence"
    evid.mkdir(parents=True, exist_ok=True)
    (evid / "execution-profile.json").write_text(
        json.dumps(freeze, indent=2) + "\n", encoding="utf-8"
    )
    return freeze


def set_active_profile(profile_id: str, marker_path: Path | None = None) -> dict[str, Any]:
    load_profile(profile_id)
    path = marker_path or MARKER_PATH
    doc = {"schema_version": 1, "executionProfile": profile_id}
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return doc


def get_frozen_or_resolve(session_dir: Path) -> dict[str, Any]:
    session = _load_json(Path(session_dir) / "session.json")
    existing = session.get("execution_profile")
    if isinstance(existing, dict) and existing.get("frozen"):
        return existing
    return freeze_profile_into_session(session_dir)


def seat_from_freeze(freeze: dict[str, Any], seat: str) -> dict[str, Any] | None:
    for row in freeze.get("seats") or []:
        if row.get("seat") == seat:
            return row
    return None
