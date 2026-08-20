"""Prepare / status / require-complete / kickoff for routed Yonko seats."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .invoke_seat import invoke_seat
from .normalise_result import write_json

# Cursor Task wrappers starve: tile "running" with zero assistant turns, or they
# call UpdateCurrentStep before Shell. OpenCode must start from the parent via
# --kickoff (background --execute). Wrappers remain for named-tile visibility only.
OPENCODE_KICKOFF_WATCHDOG_SEC = 20

WRAPPER_SHELL_FIRST = (
    "OpenCode wrapper (visibility tile). OpenCode is started by parent --kickoff. "
    "First tool call MUST be Shell of execute_command below (joins/waits if already "
    "running). No Read/Grep/UpdateCurrentStep before that Shell. Do not edit files. "
    "required_permissions: all. Do not pipe through head/tail."
)

_FINDINGS_KEYS = ("findings", "plan_findings", "document_findings")


def _routing_seats(session_dir: Path) -> list[str]:
    path = session_dir / "evidence" / "routing.json"
    if not path.is_file():
        raise SystemExit("yonko: missing evidence/routing.json - route reviewers first")
    routing = json.loads(path.read_text(encoding="utf-8"))
    seats = routing.get("seats") or []
    if not seats:
        raise SystemExit("yonko: routing.json has empty seats[]")
    return [str(s) for s in seats]


def _is_opencode_row(row: dict[str, Any]) -> bool:
    if row.get("runtime") == "opencode":
        return True
    cmd = str(row.get("execute_command") or "")
    return " --execute" in cmd


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_execute_pid(runtime_dir: Path) -> int | None:
    pid_path = runtime_dir / "execute.pid"
    if not pid_path.is_file():
        return None
    try:
        return int(pid_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _live_holder_pid(runtime_dir: Path) -> int | None:
    """Pid of a live kickoff/execute holder, if any."""
    for name in ("execute.pid", "kickoff.pid"):
        path = runtime_dir / name
        if not path.is_file():
            continue
        try:
            pid = int(path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            continue
        if _pid_alive(pid):
            return pid
    return None


def _execute_in_progress(runtime_dir: Path, result: dict[str, Any] | None = None) -> bool:
    """True only when a live --execute / kickoff process holds the seat."""
    return _live_holder_pid(runtime_dir) is not None


def _abandoned_execute(runtime_dir: Path, result: dict[str, Any] | None) -> bool:
    """True when a prior --execute left markers but the process is dead."""
    if not result:
        return False
    if result.get("completed"):
        return False
    if _live_holder_pid(runtime_dir) is not None:
        return False
    attempts = result.get("attempts")
    flagged = bool(result.get("execute_in_progress"))
    started = attempts is not None and int(attempts) >= 1
    return flagged or started


def _valid_findings_file(path: Path) -> bool:
    """Reject NDJSON stream junk (e.g. step_start) written into findings.json."""
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    return any(isinstance(data.get(k), list) for k in _FINDINGS_KEYS)


def _seat_status(session_dir: Path, seat: str) -> dict[str, Any]:
    runtime_dir = session_dir / "runtime" / seat
    result_path = runtime_dir / "result.json"
    findings_path = runtime_dir / "findings.json"
    dispatch_path = runtime_dir / "dispatch.json"
    status: dict[str, Any] = {
        "seat": seat,
        "has_findings": _valid_findings_file(findings_path),
        "has_dispatch": dispatch_path.is_file(),
        "has_result": result_path.is_file(),
        "runtime": None,
        "completed": False,
        "awaiting_chair_dispatch": False,
        "duration_ms": None,
        "failure_category": None,
        "schema_valid": None,
        "execute_command": None,
        "task_description": None,
        "attempts": None,
        "never_started": False,
        "execute_in_progress": False,
        "abandoned": False,
    }
    if dispatch_path.is_file():
        try:
            dispatch = json.loads(dispatch_path.read_text(encoding="utf-8"))
            status["runtime"] = dispatch.get("runtime")
            status["execute_command"] = dispatch.get("execute_command")
            status["task_description"] = dispatch.get("task_description")
        except json.JSONDecodeError:
            pass
    result: dict[str, Any] | None = None
    if result_path.is_file():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            status["runtime"] = status["runtime"] or result.get("runtime")
            status["completed"] = bool(result.get("completed"))
            status["awaiting_chair_dispatch"] = bool(result.get("awaiting_chair_dispatch"))
            status["duration_ms"] = result.get("duration_ms")
            status["failure_category"] = result.get("failure_category")
            status["schema_valid"] = result.get("schema_valid")
            status["skipped_by_routing"] = bool(result.get("skipped_by_routing"))
            attempts = result.get("attempts")
            status["attempts"] = attempts
            in_progress = _execute_in_progress(runtime_dir, result)
            abandoned = _abandoned_execute(runtime_dir, result)
            status["execute_in_progress"] = in_progress
            status["abandoned"] = abandoned
            # Needs parent/wrapper --execute: prepare-only OR killed mid-flight with no findings.
            if (
                _is_opencode_row(status)
                and not status["has_findings"]
                and not status["completed"]
                and not in_progress
            ):
                if attempts is None or int(attempts) == 0 or abandoned:
                    status["never_started"] = True
        except json.JSONDecodeError:
            pass
    elif _execute_in_progress(runtime_dir, None):
        status["execute_in_progress"] = True
    return status


def _spawn_order_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenCode wrappers first (parallel tiles), then Cursor seats."""
    opencode = [r for r in rows if _is_opencode_row(r)]
    cursor = [r for r in rows if not _is_opencode_row(r)]
    ordered = opencode + cursor
    out = []
    for r in ordered:
        item = {
            "seat": r["seat"],
            "task_description": r.get("task_description") or f"{r['seat'].title()} Yonko review",
            "runtime": r.get("runtime"),
            "execute_command": r.get("execute_command"),
            "spawn_priority": "opencode_first" if _is_opencode_row(r) else "cursor",
            "run_in_background": True if _is_opencode_row(r) else False,
        }
        if _is_opencode_row(r) and r.get("execute_command"):
            item["wrapper_prompt"] = (
                f"{WRAPPER_SHELL_FIRST}\n\n"
                f"Shell this exact command:\n{r['execute_command']}\n"
            )
        out.append(item)
    return out


def prepare(session_dir: Path, *, kickoff: bool = False) -> dict[str, Any]:
    seats = _routing_seats(session_dir)
    rows = []
    for seat in seats:
        result = invoke_seat(session_dir, seat, execute=False)
        status = _seat_status(session_dir, seat)
        status["prepare_result"] = {
            "completed": result.get("completed"),
            "awaiting_chair_dispatch": result.get("awaiting_chair_dispatch"),
            "runtime": result.get("runtime"),
        }
        rows.append(status)
    spawn = _spawn_order_rows(rows)
    council = {
        "schema_version": 1,
        "session_dir": str(session_dir),
        "seats": rows,
        "task_spawn_order": spawn,
        "kickoff": {
            "opencode_first": True,
            "parent_starts_opencode": True,
            "watchdog_sec": OPENCODE_KICKOFF_WATCHDOG_SEC,
            "parallel": True,
            "recovery": (
                "Parent must run seat-council.sh --kickoff (or --prepare --kickoff) so "
                "OpenCode --execute starts immediately. Wrappers are visibility tiles only. "
                "If any OpenCode seat is still never_started after watchdog_sec, run "
                "--execute-awaiting or --kickoff again."
            ),
        },
    }
    write_json(session_dir / "council.json", council)
    if kickoff:
        council["kickoff_result"] = kickoff_opencode(session_dir, background=True)
        # Refresh seat rows after kickoff markers land.
        council["seats"] = [_seat_status(session_dir, seat) for seat in seats]
        write_json(session_dir / "council.json", council)
    return council


def never_started_opencode_seats(session_dir: Path) -> list[str]:
    report = status_report(session_dir)
    return [
        row["seat"]
        for row in report["seats"]
        if _is_opencode_row(row)
        and not row.get("skipped_by_routing")
        and row.get("never_started")
        and not row.get("has_findings")
    ]


def _invoke_seat_script() -> Path:
    return Path(__file__).resolve().parents[2] / "invoke-seat.sh"


def kickoff_opencode(session_dir: Path, *, background: bool = True) -> dict[str, Any]:
    """Start OpenCode --execute for never-started seats.

    background=True (default): detach one process per seat and return immediately.
    This is the durable fix for Cursor wrapper starvation - do not wait for Tasks.
    """
    seats = never_started_opencode_seats(session_dir)
    if not seats:
        out = {
            "schema_version": 1,
            "mode": "background" if background else "blocking",
            "started": [],
            "ok": True,
            "note": "no never-started OpenCode seats",
        }
        write_json(session_dir / "council-kickoff.json", out)
        return out

    if not background:
        return execute_awaiting(session_dir)

    script = _invoke_seat_script()
    started: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for seat in seats:
        runtime_dir = session_dir / "runtime" / seat
        runtime_dir.mkdir(parents=True, exist_ok=True)
        log_path = runtime_dir / "kickoff.log"
        cmd = [
            str(script),
            "--session",
            str(session_dir),
            "--seat",
            seat,
            "--execute",
        ]
        try:
            with log_path.open("ab") as log:
                log.write(f"\n--- kickoff {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ---\n".encode())
                log.flush()
                proc = subprocess.Popen(  # noqa: S603 - controlled local script
                    cmd,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    cwd=str(session_dir),
                )
            # Soft marker so status does not treat seat as never_started before
            # invoke_opencode_seat writes execute.pid (race window of milliseconds).
            soft = runtime_dir / "result.json"
            if soft.is_file():
                try:
                    data = json.loads(soft.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    data = {}
            else:
                data = {
                    "schema_version": 1,
                    "seat": seat,
                    "runtime": "opencode",
                    "completed": False,
                    "awaiting_chair_dispatch": False,
                }
            data["attempts"] = max(1, int(data.get("attempts") or 0))
            data["execute_in_progress"] = True
            data["kickoff_pid"] = proc.pid
            write_json(soft, data)
            # kickoff.pid only - never write execute.pid here (that is the invoke
            # process itself via exec; writing it would make join() wait on itself).
            (runtime_dir / "kickoff.pid").write_text(str(proc.pid), encoding="utf-8")
            started.append({"seat": seat, "pid": proc.pid, "log": str(log_path)})
        except OSError as exc:
            errors[seat] = f"{type(exc).__name__}: {exc}"

    out = {
        "schema_version": 1,
        "mode": "background",
        "started": started,
        "errors": errors,
        "ok": len(errors) == 0 and len(started) == len(seats),
        "note": (
            "OpenCode --execute detached from parent. Wrapper Tasks are optional "
            "visibility; they join via the same invoke-seat --execute command."
        ),
    }
    write_json(session_dir / "council-kickoff.json", out)
    return out


def execute_awaiting(session_dir: Path) -> dict[str, Any]:
    """Blocking parallel --execute for never-started OpenCode seats (watchdog recovery)."""
    seats = never_started_opencode_seats(session_dir)
    if not seats:
        out = {
            "schema_version": 1,
            "executed": [],
            "results": {},
            "ok": True,
            "note": "no never-started OpenCode seats",
        }
        write_json(session_dir / "council-execute-awaiting.json", out)
        return out

    results: dict[str, Any] = {}
    errors: dict[str, str] = {}

    def _run(seat: str) -> tuple[str, dict[str, Any]]:
        return seat, invoke_seat(session_dir, seat, execute=True)

    with ThreadPoolExecutor(max_workers=max(1, len(seats))) as pool:
        futures = {pool.submit(_run, seat): seat for seat in seats}
        for fut in as_completed(futures):
            seat = futures[fut]
            try:
                _, result = fut.result()
                results[seat] = {
                    "completed": result.get("completed"),
                    "schema_valid": result.get("schema_valid"),
                    "failure_category": result.get("failure_category"),
                    "duration_ms": result.get("duration_ms"),
                }
            except Exception as exc:  # noqa: BLE001 - surface per-seat failure
                errors[seat] = f"{type(exc).__name__}: {exc}"

    report = status_report(session_dir)
    out = {
        "schema_version": 1,
        "executed": seats,
        "results": results,
        "errors": errors,
        "ok": len(errors) == 0
        and all(_valid_findings_file(session_dir / "runtime" / s / "findings.json") for s in seats),
        "incomplete_seats": report.get("incomplete_seats"),
    }
    write_json(session_dir / "council-execute-awaiting.json", out)
    return out


def status_report(session_dir: Path) -> dict[str, Any]:
    seats = _routing_seats(session_dir)
    rows = [_seat_status(session_dir, seat) for seat in seats]
    incomplete = []
    for row in rows:
        if row.get("skipped_by_routing"):
            continue
        is_opencode = _is_opencode_row(row)
        if is_opencode:
            if row.get("execute_in_progress") and not row.get("has_findings"):
                incomplete.append(row["seat"])
            elif not row.get("has_findings") and (
                row.get("awaiting_chair_dispatch")
                or not row.get("completed")
                or row.get("never_started")
                or row.get("abandoned")
            ):
                incomplete.append(row["seat"])
            elif row.get("has_findings") and row.get("failure_category") == "schema_validation_failure":
                incomplete.append(row["seat"])
            elif row.get("has_findings") and row.get("schema_valid") is False and not row.get("completed"):
                incomplete.append(row["seat"])
        elif row.get("runtime") == "cursor":
            if not row.get("has_findings") and not row.get("completed"):
                incomplete.append(row["seat"])
    return {
        "schema_version": 1,
        "seats": rows,
        "incomplete_seats": incomplete,
        "ok": len(incomplete) == 0,
        "failure_code": None if not incomplete else "OPENCODE_EXECUTE_MISSING",
    }


def require_complete(session_dir: Path) -> dict[str, Any]:
    report = status_report(session_dir)
    write_json(session_dir / "council-status.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Yonko seat council prepare/status/kickoff")
    p.add_argument("--session", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--prepare", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--require-complete", action="store_true")
    g.add_argument(
        "--execute-awaiting",
        action="store_true",
        help="Blocking parallel --execute for never-started OpenCode seats",
    )
    g.add_argument(
        "--kickoff",
        action="store_true",
        help="Detach parallel --execute for never-started OpenCode seats (parent start)",
    )
    p.add_argument(
        "--with-kickoff",
        action="store_true",
        help="With --prepare: also detach OpenCode --execute immediately",
    )
    args = p.parse_args(argv)
    session_dir = Path(args.session).resolve()
    if args.prepare:
        out = prepare(session_dir, kickoff=bool(args.with_kickoff))
        print(json.dumps(out, indent=2))
        return 0
    if args.kickoff:
        out = kickoff_opencode(session_dir, background=True)
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 2
    if args.status:
        out = status_report(session_dir)
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1
    if args.execute_awaiting:
        out = execute_awaiting(session_dir)
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 2
    out = require_complete(session_dir)
    print(json.dumps(out, indent=2))
    if not out.get("ok"):
        print(
            f"yonko: {out.get('failure_code')}: incomplete seats={out.get('incomplete_seats')}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
