"""Provider-neutral seat invocation dispatcher."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .cursor_adapter import invoke_cursor_seat
from .normalise_result import ensure_under, write_json
from .opencode_adapter import invoke_opencode_seat, prepare_opencode_dispatch
from .repository_exploration import (
    budget_for,
    resolve_primary_workdir,
    resolve_workspace_root,
)
from .resolve_profile import (
    ProfileError,
    get_frozen_or_resolve,
    load_profile,
    match_opencode_model,
    resolve_active_profile,
    seat_from_freeze,
)

SKILL_ROOT = Path(__file__).resolve().parents[3]
PROMPTS = {
    "implementation": SKILL_ROOT / "prompts" / "reviewers.md",
    "plan": SKILL_ROOT / "prompts" / "plan-reviewers.md",
    "document": SKILL_ROOT / "prompts" / "document-reviewers.md",
}
SCHEMAS = {
    "implementation": SKILL_ROOT / "contracts" / "finding.schema.json",
    "plan": SKILL_ROOT / "contracts" / "plan-finding.schema.json",
    "document": SKILL_ROOT / "contracts" / "document-finding.schema.json",
}


def _load_session(session_dir: Path) -> dict[str, Any]:
    return json.loads((session_dir / "session.json").read_text(encoding="utf-8"))


def build_invocation(
    session_dir: Path,
    seat: str,
    *,
    freeze: dict[str, Any] | None = None,
    workdir: Path | None = None,
) -> dict[str, Any]:
    session_dir = Path(session_dir).resolve()
    session = _load_session(session_dir)
    freeze = freeze or get_frozen_or_resolve(session_dir)
    row = seat_from_freeze(freeze, seat)
    if not row:
        raise ProfileError("invalid_model_mapping", f"seat {seat} not in frozen profile")

    packet = session_dir / "packet.md"
    meta = session_dir / "packet.meta.json"
    if not packet.is_file():
        raise ProfileError("invalid_profile", "missing packet.md - pin packet before invoking seats")
    packet_hash = session.get("packet_hash") or ""
    if meta.is_file():
        try:
            packet_hash = json.loads(meta.read_text(encoding="utf-8")).get("packet_hash") or packet_hash
        except json.JSONDecodeError:
            pass
    if not packet_hash:
        raise ProfileError("invalid_profile", "missing packet_hash")

    review_type = session.get("review_type") or "implementation"
    out_dir = session_dir / "runtime" / seat
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = ensure_under(session_dir, out_dir / "findings.json")

    profile = resolve_active_profile()
    # Prefer frozen profile id over live marker for reproducibility
    profile_id = freeze.get("executionProfile") or profile.get("id")
    try:
        profile = load_profile(profile_id)
    except ProfileError:
        pass
    seat_mapping = (profile.get("seats") or {}).get(seat) or {}

    prompt_path = PROMPTS.get(review_type, PROMPTS["implementation"])
    schema_path = SCHEMAS.get(review_type, SCHEMAS["implementation"])

    model = row.get("model") or ""
    configured_model = row.get("configured_model") or model
    resolved_model = row.get("resolved_model") or model
    runtime_options: dict[str, Any] = {}
    if seat_mapping.get("model"):
        runtime_options["model_spec"] = seat_mapping["model"]
    review_mode = str(row.get("review_mode") or "packet_only")
    workspace_root = resolve_workspace_root(session_dir)
    resolved_workdir = Path(workdir).resolve() if workdir else (
        resolve_primary_workdir(session_dir)
        if row["runtime"] == "opencode"
        and review_mode == "packet_plus_workspace_read"
        else session_dir
    )

    inv = {
        "schema_version": 1,
        "session_id": session.get("session_id") or session_dir.name,
        "session_dir": str(session_dir),
        "review_type": review_type,
        "seat": seat,
        "runtime": row["runtime"],
        "model": resolved_model,
        "model_configured": configured_model,
        "model_resolved": resolved_model,
        "packet_path": str(packet),
        "packet_hash": packet_hash,
        "prompt_path": str(prompt_path),
        "schema_path": str(schema_path),
        "output_path": str(output_path),
        "timeout_sec": int(row.get("timeout_sec") or seat_mapping.get("timeout_sec") or 600),
        "permissions": {
            "read": True,
            "write": False if row.get("read_only", row["runtime"] == "opencode") else False,
        },
        "workdir": str(resolved_workdir),
        "workspace_root": str(workspace_root),
        "review_mode": review_mode,
        "exploration_budget": (
            budget_for(session_dir, seat)
            if review_mode == "packet_plus_workspace_read"
            else {}
        ),
        "execution_profile": profile_id,
        "profile_fingerprint": freeze.get("profile_fingerprint"),
        "model_selection_version": freeze.get("model_selection_version"),
        "runtime_options": runtime_options,
    }
    return inv


def invoke_seat(
    session_dir: Path,
    seat: str,
    *,
    workdir: Path | None = None,
    skip_if_not_routed: bool = True,
    execute: bool = False,
) -> dict[str, Any]:
    """Dispatch one seat through the frozen profile runtime mapping.

    Core workflow must call this - never branch on profile id here.

    OpenCode seats default to Chair Task dispatch only (visibility tiles).
    Pass ``execute=True`` (``invoke-seat.sh --execute``) from the wrapper Task
    to run the OpenCode CLI.
    """
    session_dir = Path(session_dir)
    freeze = get_frozen_or_resolve(session_dir)

    if skip_if_not_routed and seat != "chair":
        routing_path = session_dir / "evidence" / "routing.json"
        if routing_path.is_file():
            try:
                routing = json.loads(routing_path.read_text(encoding="utf-8"))
                seats = routing.get("seats") or []
                if seat not in seats:
                    from .normalise_result import empty_result

                    result = empty_result(
                        seat=seat,
                        runtime=(seat_from_freeze(freeze, seat) or {}).get("runtime") or "cursor",
                        model_configured=(seat_from_freeze(freeze, seat) or {}).get("model") or "",
                        completed=False,
                        skipped_by_routing=True,
                        failure_category=None,
                        failure_message="seat not in routing.json",
                    )
                    out_dir = session_dir / "runtime" / seat
                    out_dir.mkdir(parents=True, exist_ok=True)
                    write_json(out_dir / "result.json", result)
                    return result
            except json.JSONDecodeError:
                pass

    inv = build_invocation(session_dir, seat, freeze=freeze, workdir=workdir)
    write_json(session_dir / "runtime" / seat / "invocation.json", inv)

    runtime = inv["runtime"]
    if runtime == "cursor":
        return invoke_cursor_seat(inv)
    if runtime == "opencode":
        # Resolve unresolved models against live list at invoke time
        if str(inv.get("model") or "").startswith("unresolved:"):
            from .opencode_adapter import list_models

            mapping = {"model": inv.get("runtime_options", {}).get("model_spec") or {}}
            if not mapping["model"]:
                needles = inv["model"].replace("unresolved:", "").split("+")
                mapping = {"model": {"match_substrings": needles}}
            inv["model"] = match_opencode_model(mapping, list_models())
        if not execute:
            return prepare_opencode_dispatch(inv)
        return invoke_opencode_seat(inv)
    raise ProfileError("invalid_model_mapping", f"unsupported runtime: {runtime}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Yonko provider-neutral seat invoke")
    p.add_argument("--session", required=True)
    p.add_argument("--seat", required=True)
    p.add_argument("--workdir", default=None)
    p.add_argument(
        "--execute",
        action="store_true",
        help="OpenCode only: run CLI (wrapper Task). Default is Cursor Task dispatch.",
    )
    p.add_argument("--json", action="store_true", help="print result JSON")
    args = p.parse_args(argv)
    try:
        result = invoke_seat(
            Path(args.session),
            args.seat,
            workdir=Path(args.workdir) if args.workdir else None,
            execute=bool(args.execute),
        )
    except ProfileError as e:
        err = {
            "schema_version": 1,
            "completed": False,
            "failure_category": e.category,
            "failure_message": e.message,
            "seat": args.seat,
        }
        print(json.dumps(err, indent=2))
        return 2
    if args.json or True:
        print(json.dumps(result, indent=2))
    return 0 if result.get("completed") or result.get("awaiting_chair_dispatch") or result.get("skipped_by_routing") else 1


if __name__ == "__main__":
    raise SystemExit(main())
