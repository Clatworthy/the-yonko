"""Cursor runtime adapter - thin chair-dispatch layer (no recursive Cursor invoke)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .normalise_result import empty_result, write_json
from .prompt_builder import build_reviewer_prompt, prompt_observability


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def invoke_cursor_seat(invocation: dict[str, Any]) -> dict[str, Any]:
    """Write a dispatch artefact for Chair to run via Cursor Task.

    Does not spawn Cursor. Preserves existing Task/subagent semantics.
    """
    started = _utc_now()
    seat = invocation["seat"]
    model = invocation["model"]
    session_dir = Path(invocation["session_dir"])
    out_dir = session_dir / "runtime" / seat
    out_dir.mkdir(parents=True, exist_ok=True)
    dispatch_path = out_dir / "dispatch.json"

    prompt_meta: dict[str, Any] | None = None
    try:
        built = build_reviewer_prompt(
            packet_path=invocation["packet_path"],
            packet_hash=str(invocation.get("packet_hash") or ""),
            schema_path=invocation["schema_path"],
            seat=str(seat),
            review_type=str(invocation.get("review_type") or "implementation"),
            attempt=1,
        )
        (out_dir / "prompt.txt").write_text(built["prompt"], encoding="utf-8")
        prompt_meta = prompt_observability(built)
        write_json(
            out_dir / "prompt.meta.json",
            {
                "promptFormatVersion": built["prompt_format_version"],
                "sharedPrefixHash": built["shared_prefix_hash"],
                "fullPromptHash": built["full_prompt_hash"],
                "sharedPrefixBytes": built["shared_prefix_bytes"],
                "fullPromptBytes": built["full_prompt_bytes"],
                "seat": built["seat"],
                "attempt": built["attempt"],
                "packet_hash": built["packet_hash"],
            },
        )
    except (OSError, ValueError, json.JSONDecodeError):
        prompt_meta = None

    dispatch = {
        "schema_version": 1,
        "runtime": "cursor",
        "seat": seat,
        "model_preference": model,
        "packet_path": invocation["packet_path"],
        "packet_hash": invocation["packet_hash"],
        "prompt_path": invocation["prompt_path"],
        "schema_path": invocation["schema_path"],
        "output_path": invocation["output_path"],
        "review_type": invocation["review_type"],
        "execution_profile": invocation.get("execution_profile"),
        "shared_prefix_hash": (prompt_meta or {}).get("sharedPrefixHash"),
        "prompt_format_version": (prompt_meta or {}).get("promptFormatVersion"),
        "dispatched_at": started,
        "task_description": f"{str(seat).replace('_', ' ').title()} Yonko review",
        "instructions": (
            "Chair (Zoro) must seat this reviewer via Cursor Task using the "
            "resolved model preference against the live Task allowlist. "
            "Task description: use task_description (seat name only is fine for Cursor seats). "
            "Prefer runtime/prompt.txt (stable prefix ordering) when pasting evidence. "
            "HARD: the Task is read-only advisor. It must NOT call Edit, Write, "
            "StrReplace, Delete, or Notebook edit tools - those trigger human Allow "
            "prompts. Return findings JSON + Attack card in the Task reply only. "
            "Chair (not the seat) may persist findings via record-cursor-seat / "
            "validate-artifact after the Task returns. "
            "Do not invent slugs. After Task returns, call "
            "scripts/record-cursor-seat.sh --session DIR --seat SEAT "
            "(or record_cursor_completion) so duration_ms is recorded. "
            "Do not recurse into invoke-seat for the same Cursor seat."
        ),
    }
    write_json(dispatch_path, dispatch)

    ended = _utc_now()
    result = empty_result(
        seat=seat,
        runtime="cursor",
        model_configured=str(invocation.get("model_configured") or model),
        model_resolved=str(invocation.get("model_resolved") or model),
        model_actual=None,
        completed=False,
        awaiting_chair_dispatch=True,
        exit_status=0,
        duration_ms=0,
        started_at=started,
        ended_at=ended,
        attempts=1,
        schema_valid=False,
        dispatch_path=str(dispatch_path),
        output_path=invocation.get("output_path"),
        failure_category=None,
        failure_message=None,
        prompt=prompt_meta,
    )
    write_json(out_dir / "result.json", result)
    return result


def _duration_ms(started_at: str | None, ended_at: str) -> int | None:
    if not started_at:
        return None
    try:
        t0 = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        t1 = datetime.strptime(ended_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return max(0, int((t1 - t0).total_seconds() * 1000))
    except ValueError:
        return None


def record_cursor_completion(
    session_dir: Path,
    seat: str,
    *,
    model_actual: str | None = None,
    output_path: str | None = None,
    schema_valid: bool = False,
    completed: bool = True,
) -> dict[str, Any]:
    """Mark a Cursor Task seat complete and record elapsed duration_ms."""
    out_dir = Path(session_dir) / "runtime" / seat
    out_dir.mkdir(parents=True, exist_ok=True)
    prev: dict[str, Any] = {}
    prev_path = out_dir / "result.json"
    if prev_path.is_file():
        try:
            prev = json.loads(prev_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}
    ended = _utc_now()
    started = prev.get("started_at")
    findings_path = output_path or prev.get("output_path")
    if findings_path and Path(findings_path).is_file() and not schema_valid:
        # Best-effort: presence of findings file does not imply schema_valid;
        # caller should pass schema_valid after validate-artifact.
        pass
    result = empty_result(
        seat=seat,
        runtime="cursor",
        model_configured=str(prev.get("model_configured") or model_actual or prev.get("model_resolved")),
        model_resolved=str(prev.get("model_resolved") or model_actual or prev.get("model_configured")),
        model_actual=model_actual,
        completed=completed,
        awaiting_chair_dispatch=False,
        exit_status=0 if completed else 1,
        duration_ms=_duration_ms(started, ended),
        attempts=int(prev.get("attempts") or 1),
        schema_valid=schema_valid,
        output_path=findings_path,
        dispatch_path=prev.get("dispatch_path"),
        started_at=started,
        ended_at=ended,
        prompt=prev.get("prompt"),
    )
    write_json(prev_path, result)
    return result
