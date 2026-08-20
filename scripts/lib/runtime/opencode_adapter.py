"""OpenCode CLI adapter - independent read-only packet reviewers."""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .normalise_result import (
    empty_result,
    extract_findings_from_opencode_stdout,
    extract_repository_exploration,
    extract_usage_from_opencode_stdout,
    redact_secrets,
    write_json,
)
from .prompt_builder import (
    build_reviewer_prompt,
    default_repair_instruction,
    prompt_observability,
)
from .resolve_profile import ProfileError, match_opencode_model

# Env override for tests / alternate binaries
OPENCODE_BIN_ENV = "YONKO_OPENCODE_BIN"
MAX_OUTPUT_BYTES = 2_000_000

# Windows CreateProcess rejects command lines over 32767 characters. Stay under it
# with headroom for the resolved binary path and quoting.
WINDOWS_CMDLINE_LIMIT = 30_000

PROMPT_FILE_MESSAGE = (
    "Follow the reviewer instructions in the attached prompt.txt exactly, including its "
    "output contract and JSON schema. The attached packet is the material under review. "
    "Return only the JSON artefact prompt.txt specifies - no prose, no code fences."
)

EXPLORATION_GRACE_SECONDS = 60
EXPLORATION_FINISH_WINDOW_SECONDS = 180
EXPLORATION_MAX_NUDGES = 2
EXPLORATION_STALL_SECONDS = 90


class ExplorationWatchdog:
    """Coach explore seats: visibility + soft nudge, hard cut only at absolute timeout.

    Soft interventions (nudge) fire when the seat is wandering past the soft budget
    without starting findings JSON. Finishing seats are never soft-interrupted.
    Absolute seat timeout remains the last resort.
    """

    def __init__(
        self,
        *,
        soft_sec: int,
        absolute_sec: int,
        grace_sec: int = EXPLORATION_GRACE_SECONDS,
        finish_window_sec: int = EXPLORATION_FINISH_WINDOW_SECONDS,
        tool_call_cap: int | None = None,
        max_nudges: int = EXPLORATION_MAX_NUDGES,
        status_path: Path | str | None = None,
        progress_path: Path | str | None = None,
    ) -> None:
        self.soft_sec = max(0, int(soft_sec))
        self.grace_sec = max(0, int(grace_sec))
        self.finish_window_sec = max(0, int(finish_window_sec))
        self.absolute_sec = max(1, int(absolute_sec))
        self.tool_call_cap = int(tool_call_cap) if tool_call_cap else None
        self.max_nudges = max(0, int(max_nudges))
        self.status_path = Path(status_path) if status_path else None
        self.progress_path = Path(progress_path) if progress_path else None
        self.tool_calls = 0
        self.invalid_tools = 0
        self.denied_signals = 0
        self.saw_text = False
        self.saw_finishing = False
        self.finishing_at: float | None = None
        self.session_id: str | None = None
        self.nudges = 0
        self.last_event_at: float | None = None
        self.last_event_type: str | None = None
        self.last_tool: str | None = None
        self.warnings: list[str] = []
        self.cut_reason: str | None = None
        self.phase = "exploring"
        self._write_status(elapsed=0.0)

    def observe_line(self, line: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        line = (line or "").strip()
        if not line.startswith("{"):
            if "permission requested" in line.lower() or "auto-rejecting" in line.lower():
                self.denied_signals += 1
                self._warn("permission_denied_loop")
                self._write_status(elapsed=None)
            return
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(ev, dict):
            return
        sid = ev.get("sessionID") or ev.get("session_id")
        if isinstance(sid, str) and sid.startswith("ses_"):
            self.session_id = sid
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
        ev_type = str(ev.get("type") or "")
        part_type = str(part.get("type") or "")
        tool = part.get("tool") if isinstance(part.get("tool"), str) else None
        self.last_event_at = now
        self.last_event_type = ev_type or part_type
        if tool:
            self.last_tool = tool
        if ev_type == "tool_use" or tool:
            self.tool_calls += 1
            if tool == "invalid":
                self.invalid_tools += 1
                self._warn("invalid_tool_spam")
            self._append_progress(
                {
                    "t": now,
                    "type": "tool_use",
                    "tool": tool,
                    "session_id": self.session_id,
                    "tool_calls": self.tool_calls,
                }
            )
            self._write_status(elapsed=None)
            return
        if part_type != "text" and ev_type != "text":
            self._append_progress(
                {
                    "t": now,
                    "type": ev_type or part_type or "event",
                    "session_id": self.session_id,
                }
            )
            self._write_status(elapsed=None)
            return
        self.saw_text = True
        blob = part.get("text")
        finishing = False
        if isinstance(blob, str) and _text_looks_like_findings(blob):
            finishing = True
        elif isinstance(blob, str) and extract_findings_from_opencode_stdout(blob) is not None:
            finishing = True
        if finishing:
            self._mark_finishing(now=now)
        self._append_progress(
            {
                "t": now,
                "type": "text",
                "finishing": finishing,
                "session_id": self.session_id,
            }
        )
        self._write_status(elapsed=None)

    def observe_buffer(self, buffer: str, *, now: float | None = None) -> None:
        if self.saw_finishing:
            return
        if extract_findings_from_opencode_stdout(buffer) is not None:
            self._mark_finishing(now=now)

    def observe_stderr(self, line: str) -> None:
        low = (line or "").lower()
        if "permission requested" in low or "auto-rejecting" in low:
            self.denied_signals += 1
            self._warn("permission_denied_loop")
            self._write_status(elapsed=None)

    def _mark_finishing(self, *, now: float | None = None) -> None:
        if self.saw_finishing:
            return
        self.saw_finishing = True
        self.saw_text = True
        self.phase = "finishing"
        self.finishing_at = time.monotonic() if now is None else now
        self._write_status(elapsed=None)

    def _warn(self, code: str) -> None:
        if code not in self.warnings:
            self.warnings.append(code)

    def decision(self, elapsed_sec: float) -> str | None:
        """Return intervention code, or None to keep the current turn running.

        Codes:
          - nudge:no_progress / nudge:tool_call_cap / nudge:denied_loop
          - cut:absolute_timeout
        """
        self._write_status(elapsed=elapsed_sec)
        if elapsed_sec >= self.absolute_sec:
            self.cut_reason = "cut:absolute_timeout"
            self.phase = "cut"
            self._write_status(elapsed=elapsed_sec)
            return self.cut_reason
        if self.saw_finishing:
            return None
        # Early stall: no events for a while after start - warn only until soft cut.
        if (
            self.last_event_at is not None
            and elapsed_sec >= EXPLORATION_STALL_SECONDS
            and (time.monotonic() - self.last_event_at) >= EXPLORATION_STALL_SECONDS
            and elapsed_sec < (self.soft_sec or EXPLORATION_STALL_SECONDS)
        ):
            self._warn("stalled_no_events")
            self._write_status(elapsed=elapsed_sec)

        soft_cut = self.soft_sec + self.grace_sec if self.soft_sec > 0 else 0
        can_nudge = self.nudges < self.max_nudges
        if (
            can_nudge
            and self.denied_signals >= 3
            and (self.soft_sec <= 0 or elapsed_sec >= min(self.soft_sec, 60))
        ):
            self.cut_reason = "nudge:denied_loop"
            self.phase = "nudge"
            return self.cut_reason
        if (
            can_nudge
            and self.tool_call_cap is not None
            and self.tool_calls >= self.tool_call_cap
            and (self.soft_sec <= 0 or elapsed_sec >= self.soft_sec)
        ):
            self.cut_reason = "nudge:tool_call_cap"
            self.phase = "nudge"
            return self.cut_reason
        if can_nudge and soft_cut > 0 and elapsed_sec >= soft_cut:
            self.cut_reason = "nudge:no_progress"
            self.phase = "nudge"
            return self.cut_reason
        if soft_cut > 0 and elapsed_sec >= soft_cut and not can_nudge:
            self.cut_reason = "cut:no_progress"
            self.phase = "cut"
            return self.cut_reason
        return None

    def should_cut(self, elapsed_sec: float) -> str | None:
        """Return a coach/cut decision code for the streaming runner."""
        return self.decision(elapsed_sec)

    def record_nudge(self, reason: str) -> None:
        self.nudges += 1
        self.phase = "nudged"
        self._warn(f"nudged:{reason}")
        self.tool_calls = 0
        self.invalid_tools = 0
        self.cut_reason = None
        self._write_status(elapsed=None)

    def _append_progress(self, row: dict[str, Any]) -> None:
        if not self.progress_path:
            return
        try:
            self.progress_path.parent.mkdir(parents=True, exist_ok=True)
            with self.progress_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _write_status(self, *, elapsed: float | None) -> None:
        if not self.status_path:
            return
        payload = {
            "phase": self.phase,
            "session_id": self.session_id,
            "tool_calls": self.tool_calls,
            "invalid_tools": self.invalid_tools,
            "denied_signals": self.denied_signals,
            "saw_finishing": self.saw_finishing,
            "nudges": self.nudges,
            "max_nudges": self.max_nudges,
            "last_event_type": self.last_event_type,
            "last_tool": self.last_tool,
            "warnings": list(self.warnings),
            "cut_reason": self.cut_reason,
            "elapsed_sec": None if elapsed is None else round(elapsed, 1),
            "soft_sec": self.soft_sec,
            "grace_sec": self.grace_sec,
            "absolute_sec": self.absolute_sec,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            self.status_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass


def coach_nudge_message(reason: str) -> str:
    return (
        "COACH NUDGE: you are off track for a Yonko review seat.\\n"
        f"Trigger: {reason}.\\n"
        "Stop calling tools immediately. Do not explore further.\\n"
        "Return ONLY one findings JSON object matching the supplied schema.\\n"
        "If exploration was incomplete, review from the Packet alone - an empty "
        "findings array is acceptable; missing JSON is a failure.\\n"
    )


def _text_looks_like_findings(text: str) -> bool:
    low = (text or "").lstrip()
    if not low:
        return False
    if low.startswith("```"):
        low = low.split("\n", 1)[-1]
    if '"findings"' in low or "'findings'" in low:
        return True
    if low.startswith("{") and "findings" in low[:200]:
        return True
    return False


# Deny write tools via OpenCode permission env when supported.
# Documented limitation: if the runtime ignores this, git dirty checks still apply.
# external_directory defaults to "ask" in OpenCode - non-interactive seats hang/fail
# when the packet/session lives outside --dir/workdir (normal Yonko layout).
DEFAULT_DENY_TOOLS = {
    "edit": "deny",
    "bash": "deny",
    "write": "deny",
    "todowrite": "deny",
    "question": "deny",
    "task": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "doom_loop": "deny",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _path_outside(workdir: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(workdir.resolve())
        return False
    except ValueError:
        return True


def _external_allow_patterns(*paths: Path | str | None) -> dict[str, str]:
    """Build OpenCode external_directory allow patterns for absolute trees."""
    patterns: dict[str, str] = {}
    for raw in paths:
        if not raw:
            continue
        root = Path(raw).expanduser().resolve()
        if root.is_file():
            root = root.parent
        # OpenCode patterns: last match wins; cover dir and descendants.
        base = str(root)
        patterns[base] = "allow"
        patterns[base + "/*"] = "allow"
        patterns[base + "/**"] = "allow"
    return patterns


def build_opencode_permission_json(
    *,
    workdir: Path | str,
    session_dir: Path | str,
    packet_path: Path | str | None = None,
    schema_path: Path | str | None = None,
    prompt_path: Path | str | None = None,
    review_mode: str = "packet_only",
    workspace_root: Path | str | None = None,
    env_override: str | None = None,
) -> str:
    """OPENCODE_PERMISSION for read-only packet review.

    If YONKO_/OPENCODE_PERMISSION env override is set, use it unchanged.
    Otherwise deny edits/shell and allow external_directory for session + packet
    artefacts that sit outside the review workdir.
    """
    if env_override is not None and str(env_override).strip():
        return str(env_override)

    work = Path(workdir)
    session = Path(session_dir)
    external: dict[str, str] = {}
    for candidate in (
        session,
        Path(packet_path) if packet_path else None,
        Path(schema_path) if schema_path else None,
        Path(prompt_path) if prompt_path else None,
    ):
        if candidate is None:
            continue
        target = candidate if candidate.is_dir() else candidate.parent
        if _path_outside(work, target):
            external.update(_external_allow_patterns(target))

    permission: dict[str, Any] = dict(DEFAULT_DENY_TOOLS)
    sensitive_env = ".env"
    permission["read"] = {
        "*": "allow",
        f"**/{sensitive_env}": "deny",
        f"**/{sensitive_env}.*": "deny",
        "**/credentials.json": "deny",
        "**/id_rsa": "deny",
        "**/*.pem": "deny",
    }
    if review_mode == "packet_plus_workspace_read":
        # bash stays denied: the Packet already carries the diff, and repeated
        # rejected shell calls burned entire turns without producing findings.
        permission.update(
            {
                "glob": "allow",
                "grep": "allow",
                "list": "allow",
                "lsp": "allow",
                "bash": "deny",
            }
        )
        if workspace_root:
            external.update(_external_allow_patterns(workspace_root))
    else:
        # Frozen packet review: deny exploration tools including read so the
        # model cannot burn its turn on rejected workspace tool calls and then
        # exit without findings JSON (observed on large real packets).
        permission["read"] = "deny"
        permission.update(
            {
                "glob": "deny",
                "grep": "deny",
                "list": "deny",
                "lsp": "deny",
            }
        )
    external[f"**/{sensitive_env}"] = "deny"
    external[f"**/{sensitive_env}.*"] = "deny"
    external["**/credentials.json"] = "deny"
    external["**/id_rsa"] = "deny"
    external["**/*.pem"] = "deny"
    if external:
        permission["external_directory"] = external
    else:
        # Still allow ask→allow for odd layouts where paths resolve inside workdir
        # but OpenCode still classifies them external (symlinks, --dir mismatch).
        permission["external_directory"] = "allow"
    return json.dumps(permission, separators=(",", ":"))


def build_opencode_agent_config(permission_json: str) -> str:
    permission = json.loads(permission_json)
    return json.dumps(
        {
            "agent": {
                "yonko-reviewer": {
                    "description": "Packet-anchored read-only code reviewer",
                    "mode": "primary",
                    "permission": permission,
                }
            }
        },
        separators=(",", ":"),
    )


def build_opencode_run_args(
    *,
    model: str,
    title: str,
    prompt: str,
    packet_path: str,
    workdir: str | None = None,
    extra_files: list[str] | None = None,
    prompt_file: Path | str | None = None,
    agent: str | None = None,
    session_id: str | None = None,
    message: str | None = None,
) -> list[str]:
    """Build `opencode run` argv.

    OpenCode 1.18.x treats the message as a filename when `--file` precedes the
    positional message ("File not found: <prompt text...>"). Message must come
    before `--file`.

    Windows CreateProcess rejects command lines over 32767 characters, and seat
    prompts inline the packet (tens to hundreds of KB). Pass `prompt_file` so the
    prompt travels as an attachment instead of argv.

    When `session_id` is set, continue that OpenCode session (coach nudge path).
    """
    args: list[str] = [
        "run",
        "--model",
        model,
        "--format",
        "json",
        "--title",
        title,
    ]
    if workdir:
        args.extend(["--dir", str(workdir)])
    if agent:
        args.extend(["--agent", agent])
    if session_id:
        args.extend(["--session", session_id])
    if message:
        args.append(message)
    elif prompt_file:
        args.append(PROMPT_FILE_MESSAGE)
        args.extend(["--file", str(prompt_file)])
    else:
        args.append(prompt)
    continuing = bool(session_id and message)
    if not continuing:
        args.extend(["--file", str(packet_path)])
    for path in extra_files or []:
        if path:
            args.extend(["--file", str(path)])
    return args


def cmdline_length(argv: list[str]) -> int:
    """Approximate CreateProcess command-line length (quotes plus separators)."""
    return sum(len(str(a)) + 3 for a in argv)


def exceeds_windows_cmdline_limit(argv: list[str]) -> bool:
    return cmdline_length(argv) > WINDOWS_CMDLINE_LIMIT


def resolve_opencode_bin() -> str | None:
    override = os.environ.get(OPENCODE_BIN_ENV)
    if override:
        return override if Path(override).exists() or shutil.which(override) else override
    return shutil.which("opencode")


def run_opencode(
    args: list[str],
    *,
    timeout_sec: int = 60,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    watchdog: ExplorationWatchdog | None = None,
) -> subprocess.CompletedProcess[str]:
    bin_path = resolve_opencode_bin()
    if not bin_path:
        raise ProfileError("runtime_not_installed", "OpenCode CLI not found on PATH")
    if os.name == "nt" and exceeds_windows_cmdline_limit([bin_path, *args]):
        raise ProfileError(
            "argv_too_long",
            (
                "OpenCode command line is "
                f"{cmdline_length([bin_path, *args])} characters; Windows allows 32767. "
                "Seat prompts must be attached with --file, not passed as arguments."
            ),
        )
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    if watchdog is None:
        return subprocess.run(
            [bin_path, *args],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=cwd,
            env=full_env,
            check=False,
        )
    return _run_opencode_with_watchdog(
        [bin_path, *args],
        timeout_sec=timeout_sec,
        cwd=cwd,
        env=full_env,
        watchdog=watchdog,
    )


def _run_opencode_with_watchdog(
    cmd: list[str],
    *,
    timeout_sec: int,
    cwd: str | None,
    env: dict[str, str],
    watchdog: ExplorationWatchdog,
) -> subprocess.CompletedProcess[str]:
    """Stream OpenCode NDJSON and cut only on no-progress, never a finishing seat early."""
    import queue
    import threading

    absolute = max(int(timeout_sec), int(watchdog.absolute_sec))
    watchdog.absolute_sec = absolute
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
        bufsize=1,
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    line_q: queue.Queue[str | None] = queue.Queue()
    err_q: queue.Queue[str | None] = queue.Queue()
    t0 = time.monotonic()

    def _reader(stream, q: queue.Queue[str | None]) -> None:
        try:
            assert stream is not None
            for line in stream:
                q.put(line)
        finally:
            q.put(None)

    threads = [
        threading.Thread(target=_reader, args=(proc.stdout, line_q), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr, err_q), daemon=True),
    ]
    for th in threads:
        th.start()

    stdout_done = False
    stderr_done = False
    cut_reason: str | None = None
    elapsed = 0.0
    try:
        while not (stdout_done and stderr_done and proc.poll() is not None):
            elapsed = time.monotonic() - t0
            reason = watchdog.should_cut(elapsed)
            if reason:
                cut_reason = reason
                break
            try:
                item = line_q.get(timeout=0.2)
            except queue.Empty:
                item = "__poll__"
            if item is None:
                stdout_done = True
            elif item != "__poll__":
                stdout_chunks.append(item)
                now = time.monotonic()
                watchdog.observe_line(item.rstrip("\n"), now=now)
                if len(stdout_chunks) % 8 == 0:
                    watchdog.observe_buffer("".join(stdout_chunks), now=now)
            while True:
                try:
                    err_item = err_q.get_nowait()
                except queue.Empty:
                    break
                if err_item is None:
                    stderr_done = True
                else:
                    stderr_chunks.append(err_item)
                    watchdog.observe_stderr(err_item)
            if proc.poll() is not None and stdout_done and stderr_done:
                break
            if proc.poll() is not None and elapsed > absolute:
                cut_reason = "absolute_timeout"
                break

        # Drain any remaining queued lines after exit/cut.
        while True:
            try:
                item = line_q.get_nowait()
            except queue.Empty:
                break
            if item is None:
                stdout_done = True
            else:
                stdout_chunks.append(item)
                watchdog.observe_line(item.rstrip("\n"), now=time.monotonic())
        while True:
            try:
                err_item = err_q.get_nowait()
            except queue.Empty:
                break
            if err_item is None:
                stderr_done = True
            else:
                stderr_chunks.append(err_item)

        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        if cut_reason:
            _terminate_process(proc)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _kill_process(proc)
            raise subprocess.TimeoutExpired(
                cmd,
                timeout_sec if "absolute" in str(cut_reason) else max(1, int(elapsed)),
                output=stdout,
                stderr=stderr or f"watchdog:{cut_reason}",
            )
        code = proc.wait(timeout=max(1, absolute - int(time.monotonic() - t0)))
        return subprocess.CompletedProcess(cmd, code, stdout, stderr)
    except subprocess.TimeoutExpired:
        raise
    except Exception:
        _terminate_process(proc)
        raise


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        _kill_process(proc)


def _kill_process(proc: subprocess.Popen[str]) -> None:
    try:
        proc.kill()
    except OSError:
        pass


def check_installed() -> tuple[bool, str]:
    bin_path = resolve_opencode_bin()
    if not bin_path:
        return False, "OpenCode CLI not found. Install OpenCode, then rerun /yonko doctor."
    try:
        proc = subprocess.run(
            [bin_path, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        ver = (proc.stdout or proc.stderr or "").strip().splitlines()[:1]
        return True, ver[0] if ver else bin_path
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"OpenCode CLI present but not runnable: {e}"


def check_auth() -> tuple[bool, str]:
    """Detect auth without printing secrets.

    Prefer `opencode auth list`. Presence of auth.json is a weak secondary signal
    (path only - never read/print contents in doctor output).
    """
    bin_path = resolve_opencode_bin()
    if not bin_path:
        return False, "OpenCode CLI not found"
    try:
        proc = run_opencode(["auth", "list"], timeout_sec=45)
    except ProfileError as e:
        return False, e.message
    except subprocess.TimeoutExpired:
        return False, "opencode auth list timed out"
    out = (proc.stdout or "") + (proc.stderr or "")
    redacted = redact_secrets(out).lower()
    # Heuristics: empty / "no providers" / errors -> missing
    if proc.returncode != 0:
        # Explicit binary override (tests / alternate install): trust CLI, no auth.json soft-pass.
        if os.environ.get(OPENCODE_BIN_ENV):
            return False, "OpenCode authentication missing. Run: opencode auth login"
        auth_path = Path.home() / ".local" / "share" / "opencode" / "auth.json"
        if auth_path.is_file():
            return True, "auth.json present (auth list non-zero; verify interactively)"
        return False, "OpenCode authentication missing. Run: opencode auth login"
    if re.search(r"no (authenticated )?providers|not (logged|authenticated)|empty", redacted):
        return False, "OpenCode authentication missing. Run: opencode auth login"
    # Avoid echoing provider tokens; report count of non-empty lines only
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return True, f"authenticated providers listed ({len(lines)} lines)"


def list_models(*, refresh: bool = False) -> list[str]:
    args = ["models"]
    if refresh:
        args.append("--refresh")
    proc = run_opencode(args, timeout_sec=120)
    if proc.returncode != 0:
        err = redact_secrets((proc.stderr or proc.stdout or "").strip())
        raise ProfileError(
            "provider_unavailable",
            f"opencode models failed (exit {proc.returncode}): {err[:400]}",
        )
    models: list[str] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Expect provider/model; ignore table chrome
        if "/" in line or re.match(r"^[\w.-]+$", line):
            # take first token
            token = line.split()[0]
            if token.lower() in ("provider/model", "model", "id", "name"):
                continue
            models.append(token)
    return models


def categorise_process_failure(stdout: str, stderr: str, exit_code: int) -> str:
    blob = redact_secrets(f"{stdout}\n{stderr}").lower()
    if "rate limit" in blob or "429" in blob:
        return "rate_limited"
    if "auth" in blob or "unauthorized" in blob or "401" in blob or "403" in blob:
        return "authentication_missing"
    if "model" in blob and ("not found" in blob or "unavailable" in blob or "unknown" in blob):
        return "model_unavailable"
    if "provider" in blob and ("unavailable" in blob or "down" in blob):
        return "provider_unavailable"
    if "external_directory" in blob or (
        "permission" in blob and ("denied" in blob or "reject" in blob or "ask" in blob)
    ):
        return "permission_violation"
    if "file not found" in blob:
        return "process_failure"
    if exit_code != 0:
        return "process_failure"
    return "unknown_runtime_error"


def _git_status_porcelain(workdir: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            capture_output=True,
            text=True,
            cwd=str(workdir),
            timeout=30,
            check=False,
        )
        return proc.stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _parse_porcelain_paths(porcelain: str) -> dict[str, str]:
    """Map relative path -> XY status code from `git status --porcelain`."""
    out: dict[str, str] = {}
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        rest = line[3:]
        # rename/copy: "R  old -> new"
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        path = rest.strip().strip('"')
        if path:
            out[path] = xy
    return out


def _file_digest(workdir: Path, rel: str) -> str | None:
    """Content digest of a working-tree path; None if missing."""
    p = workdir / rel
    try:
        if not p.is_file():
            return None
        h = hashlib.sha256()
        with p.open("rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def snapshot_workdir(workdir: Path) -> dict[str, dict[str, str | None]]:
    """Baseline snapshot: path -> {xy, digest}.

    Already-dirty trees are fine. Digests detect further edits to the same path
    when porcelain status text would otherwise look unchanged.
    """
    paths = _parse_porcelain_paths(_git_status_porcelain(workdir))
    snap: dict[str, dict[str, str | None]] = {}
    for rel, xy in paths.items():
        snap[rel] = {"xy": xy, "digest": _file_digest(workdir, rel)}
    return snap


def _rel_under(base: Path, target: Path) -> str | None:
    try:
        return str(target.resolve().relative_to(base.resolve())).replace("\\", "/")
    except ValueError:
        return None


def allowed_write_prefixes(workdir: Path, session_dir: Path) -> list[str]:
    """Session artefacts under the git workdir are permitted; source tree is not.

    Prefer stdout capture: adapter writes findings under session/runtime itself.
    If that session directory happens to live inside the reviewed repo, allow it.
    """
    prefixes: list[str] = [".yonko/"]
    for candidate in (session_dir / "runtime", session_dir):
        rel = _rel_under(workdir, candidate)
        if rel is not None:
            prefixes.append("." if rel == "." else rel.rstrip("/") + "/")
    seen: set[str] = set()
    out: list[str] = []
    for p in prefixes:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _is_allowed_path(rel: str, prefixes: list[str]) -> bool:
    norm = rel.replace("\\", "/").lstrip("./")
    for pref in prefixes:
        if not pref:
            continue
        pref_n = pref.replace("\\", "/").lstrip("./")
        if norm == pref_n.rstrip("/") or norm.startswith(pref_n):
            return True
    return False


def worktree_delta(
    before: dict[str, dict[str, str | None]],
    after: dict[str, dict[str, str | None]],
    *,
    allowed_prefixes: list[str],
) -> list[str]:
    """Return relative paths OpenCode introduced/changed beyond the baseline.

    Existing user dirt that is unchanged (same status + digest) is ignored.
    """
    changed: list[str] = []
    all_paths = set(before) | set(after)
    for rel in sorted(all_paths):
        if _is_allowed_path(rel, allowed_prefixes):
            continue
        b = before.get(rel)
        a = after.get(rel)
        if b == a:
            continue
        # New, removed, status change, or content digest change
        changed.append(rel)
    return changed


def _exploration_tool_call_cap(budget: dict[str, Any]) -> int:
    """Tool calls the seat may spend before it must emit findings.

    The frozen budget bounds what exploration is *allowed* to touch. It is far
    larger than what a seat can spend and still answer inside its wall clock, so
    the prompt advertises a tighter cap.
    """
    total = (
        int(budget.get("max_files_read") or 0)
        + int(budget.get("max_searches") or 0)
        + int(budget.get("max_lsp_queries") or 0)
    )
    if total <= 0:
        return 12
    return max(8, min(total, 24))


def _exploration_hard_timeout(seat_timeout_sec: int, budget: dict[str, Any]) -> int:
    """No-progress cut deadline (soft budget + grace), capped by the seat timeout.

    Finishing seats are not bound by this - the streaming watchdog only applies
    this cut when no findings text has appeared yet.
    """
    soft = int(budget.get("max_duration_seconds") or 0)
    if soft <= 0:
        return seat_timeout_sec
    return min(seat_timeout_sec, soft + EXPLORATION_GRACE_SECONDS)


def _frozen_recovery_timeout(seat_timeout_sec: int, explore_elapsed_sec: float = 0.0) -> int:
    """Dedicated budget for a tool-less recovery / repair turn."""
    remaining = int(seat_timeout_sec - explore_elapsed_sec)
    dedicated = max(180, min(seat_timeout_sec, 300))
    if remaining > 0:
        return max(120, min(dedicated, remaining))
    return dedicated


def _build_prompt(invocation: dict[str, Any], *, attempt: int = 1, repair: bool = False, validation_errors: str | None = None) -> dict[str, Any]:
    """Build cache-friendly prompt via shared prompt_builder (stable prefix first)."""
    extra = None
    if invocation.get("review_mode") == "packet_plus_workspace_read":
        budget = invocation.get("exploration_budget") or {}
        seat_to = int(invocation.get("timeout_sec") or 600)
        hard_cut = _exploration_hard_timeout(seat_to, budget)
        extra = (
            "Repository exploration mode: packet_plus_workspace_read.\n"
            "The Packet is the authoritative starting evidence and reviewed scope. "
            "You may inspect the declared workspace read-only to verify Packet "
            "claims, resolve unresolved edges, or identify material omissions. Do not "
            "put the repository into the response. Use read/glob/grep/LSP only; shell "
            "is denied. Any finding based "
            "on exploration must cite the discovered repository path and symbol and "
            "also cite the Packet locus that makes it reachable from the reviewed "
            "change. Do not inspect denied "
            "secret paths. Do not launch subagents.\n"
            f"Exploration budget: max_files_read={budget.get('max_files_read', 0)}, "
            f"max_searches={budget.get('max_searches', 0)}, "
            f"max_lsp_queries={budget.get('max_lsp_queries', 0)}, "
            f"max_extra_bytes={budget.get('max_extra_bytes', 0)}, "
            f"max_duration_seconds={budget.get('max_duration_seconds', 0)}.\n"
            "CONVERGENCE (hard law): exploration is optional, the findings JSON is "
            f"not. Make at most {_exploration_tool_call_cap(budget)} tool calls in "
            "total, and stop exploring once about half of "
            f"{budget.get('max_duration_seconds', 0)} seconds has passed, whichever "
            "comes first. Then emit the findings JSON. A seat coach watches your "
            "progress live: if you wander past {hard_cut}s without starting findings "
            "JSON, you will be re-prompted to stop tooling and emit JSON "
            f"(soft budget {budget.get('max_duration_seconds', 0)}s + "
            f"{EXPLORATION_GRACE_SECONDS}s grace). If findings JSON has started, you "
            "keep the remaining seat wall clock - slow finishers are not interrupted. "
            "Never end your turn with a tool call. If a tool is rejected, do not "
            "retry it or try another variant - fall back to the Packet and emit the "
            "JSON. Reviewing from the Packet alone is an acceptable outcome; "
            "returning no JSON is a failure.\n"
        )
    elif invocation.get("review_mode") == "packet_only":
        extra = (
            "Repository exploration mode: packet_only.\n"
            "HARD CONSTRAINT: do not call any tools (no read, glob, grep, list, LSP, "
            "shell, or external_directory). All tools are denied. The Evidence Packet "
            "below is your only evidence. Review it and return ONLY the required "
            "findings JSON object in this turn. If you already attempted a tool and it "
            "was rejected, stop immediately and emit the findings JSON from the Packet "
            "alone - an empty or missing JSON response is a failure.\n"
        )
    return build_reviewer_prompt(
        packet_path=invocation["packet_path"],
        packet_hash=str(invocation.get("packet_hash") or ""),
        schema_path=invocation["schema_path"],
        seat=str(invocation["seat"]),
        review_type=str(invocation.get("review_type") or "implementation"),
        attempt=attempt,
        extra_seat_instructions=extra,
        repair_instruction=default_repair_instruction() if repair else None,
        validation_errors=validation_errors if repair else None,
    )


def _write_prompt_artefacts(out_dir: Path, built: dict[str, Any]) -> None:
    (out_dir / "prompt.txt").write_text(built["prompt"], encoding="utf-8")
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


def _run_opencode_once(
    *,
    invocation: dict[str, Any],
    model: str,
    prompt: str,
    workdir: Path,
    timeout_sec: int,
    env: dict[str, str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
    prompt_file: Path | str | None = None,
    watchdog: ExplorationWatchdog | None = None,
    session_id: str | None = None,
    message: str | None = None,
) -> tuple[int | None, str, str, bool, str | None, str | None]:
    """Returns exit_status, stdout, stderr, timed_out, failure_category, failure_message."""
    title = f"yonko-{invocation['seat']}"
    extra_files: list[str] = []
    schema_path = invocation.get("schema_path")
    if schema_path and Path(schema_path).is_file():
        extra_files.append(str(schema_path))
    if prompt_file and not Path(prompt_file).is_file():
        prompt_file = None
    args = build_opencode_run_args(
        model=model,
        title=title,
        prompt=prompt,
        packet_path=str(invocation["packet_path"]),
        workdir=str(workdir),
        extra_files=extra_files,
        prompt_file=prompt_file,
        agent="yonko-reviewer",
        session_id=session_id,
        message=message,
    )
    try:
        proc = runner(args, env=env, timeout_sec=timeout_sec, watchdog=watchdog)
        return proc.returncode, proc.stdout or "", proc.stderr or "", False, None, None
    except subprocess.TimeoutExpired as e:
        stdout = (e.stdout or "") if isinstance(e.stdout, str) else ""
        stderr = (e.stderr or "") if isinstance(e.stderr, str) else "timeout"
        cut = None
        if watchdog is not None and watchdog.cut_reason:
            cut = watchdog.cut_reason
        elif isinstance(stderr, str) and "watchdog:" in stderr:
            cut = stderr.split("watchdog:", 1)[-1].strip() or None
        if cut and str(cut).startswith("nudge:"):
            return None, stdout, stderr, True, "coach_nudge", (
                f"coach nudge ({cut.split(':', 1)[-1]}) after {int(e.timeout)}s"
            )
        msg = f"opencode timed out after {timeout_sec}s"
        if cut and str(cut).startswith("cut:"):
            msg = f"opencode explore cut ({cut.split(':', 1)[-1]}) after {int(e.timeout)}s"
        elif cut:
            msg = f"opencode explore cut ({cut}) after {int(e.timeout)}s"
        return None, stdout, stderr, True, "timeout", msg


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


def _join_running_execute(out_dir: Path, *, timeout_sec: int) -> dict[str, Any] | None:
    """If another live invoke holds execute.pid, wait for its result and return it.

    Parent --kickoff and wrapper Tasks may both call --execute; only one runs OpenCode.
    """
    pid_path = out_dir / "execute.pid"
    if not pid_path.is_file():
        return None
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None
    my_pid = os.getpid()
    if pid == my_pid or not _pid_alive(pid):
        return None

    deadline = time.monotonic() + max(30, int(timeout_sec) + 30)
    result_path = out_dir / "result.json"
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            break
        if result_path.is_file():
            try:
                data = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
            if data.get("completed") or data.get("failure_category"):
                if not data.get("execute_in_progress"):
                    return data
        time.sleep(1.0)

    if result_path.is_file():
        try:
            return json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return empty_result(
        seat=out_dir.name,
        runtime="opencode",
        model_configured="",
        completed=False,
        failure_category="process_failure",
        failure_message=f"joined execute.pid={pid} but no completed result",
        attempts=1,
        execute_in_progress=False,
    )


def prepare_opencode_dispatch(invocation: dict[str, Any]) -> dict[str, Any]:
    """Write a Chair Task dispatch artefact; do not run OpenCode yet.

    Parent ``seat-council --kickoff`` starts OpenCode. Wrapper Tasks are visibility
    tiles that may also Shell ``--execute`` (joins if already running).
    """
    started = _utc_now()
    seat = invocation["seat"]
    model = invocation.get("model") or ""
    session_dir = Path(invocation["session_dir"])
    out_dir = session_dir / "runtime" / seat
    out_dir.mkdir(parents=True, exist_ok=True)
    dispatch_path = out_dir / "dispatch.json"
    seat_title = str(seat).replace("_", " ").title()
    scripts_hint = str(Path(__file__).resolve().parents[2] / "invoke-seat.sh")
    model_short = str(model).split("/")[-1] if model else "opencode"

    dispatch = {
        "schema_version": 1,
        "runtime": "opencode",
        "dispatch_mode": "cursor_task_wrapper",
        "seat": seat,
        "model_preference": model,
        "packet_path": invocation["packet_path"],
        "packet_hash": invocation["packet_hash"],
        "prompt_path": invocation["prompt_path"],
        "schema_path": invocation["schema_path"],
        "output_path": invocation["output_path"],
        "review_type": invocation["review_type"],
        "execution_profile": invocation.get("execution_profile"),
        "timeout_sec": invocation.get("timeout_sec"),
        "dispatched_at": started,
        "task_description": f"{seat_title} · {model_short} (OpenCode)",
        "execute_command": (
            f"{scripts_hint} --session {session_dir} --seat {seat} --execute"
        ),
        "instructions": (
            f"Chair (Zoro) must seat {seat_title} via a Cursor Task (subagent tile). "
            f"Description MUST be task_description "
            f"('{seat_title} · {model_short} (OpenCode)') so the tile names the "
            "real reviewer model (Cursor badge will still say Composer/Grok). "
            "Wrapper model: cheap Composer or Grok. "
            "HARD tools: Shell ONLY - run execute_command exactly once (no head/tail). "
            "Do NOT Edit/Write/StrReplace/Delete any file (triggers Allow prompts). "
            "Do not review the packet yourself. Wait for findings.json / result.json, "
            "then return a short summary (completed, failure_category, findings count, "
            "output_path, model_actual if present). "
            "If Shell needs approval, Chair should prefer auto-run for agent Shell; "
            "seats must never request Edit. "
            "Chair must not background-invoke OpenCode from the parent turn."
        ),
    }
    write_json(dispatch_path, dispatch)

    ended = _utc_now()
    result = empty_result(
        seat=seat,
        runtime="opencode",
        model_configured=str(invocation.get("model_configured") or model),
        model_resolved=str(invocation.get("model_resolved") or model),
        model_actual=None,
        completed=False,
        awaiting_chair_dispatch=True,
        exit_status=0,
        duration_ms=0,
        started_at=started,
        ended_at=ended,
        attempts=0,
        schema_valid=False,
        dispatch_path=str(dispatch_path),
        output_path=invocation.get("output_path"),
        failure_category=None,
        failure_message=None,
    )
    write_json(out_dir / "result.json", result)
    return result


def invoke_opencode_seat(
    invocation: dict[str, Any],
    *,
    list_models_fn: Callable[[], list[str]] | None = None,
    run_fn: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, Any]:
    seat = invocation["seat"]
    session_dir = Path(invocation["session_dir"])
    out_dir = session_dir / "runtime" / seat
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "cli.stdout.txt"
    err_path = out_dir / "cli.stderr.txt"
    output_path = Path(invocation["output_path"])
    started = _utc_now()
    t0 = time.monotonic()

    # If parent --kickoff (or another wrapper) already holds the seat, join it.
    joined = _join_running_execute(out_dir, timeout_sec=int(invocation.get("timeout_sec") or 600))
    if joined is not None:
        return joined

    bin_ok, bin_msg = check_installed()
    if not bin_ok:
        result = empty_result(
            seat=seat,
            runtime="opencode",
            model_configured=invocation.get("model_configured") or invocation.get("model") or "",
            model_resolved=invocation.get("model_resolved") or invocation.get("model"),
            completed=False,
            failure_category="runtime_not_installed",
            failure_message=bin_msg,
            started_at=started,
            ended_at=_utc_now(),
            attempts=0,
            raw_log_path=str(raw_path),
            stderr_log_path=str(err_path),
        )
        write_json(out_dir / "result.json", result)
        return result

    auth_ok, auth_msg = check_auth()
    if not auth_ok:
        result = empty_result(
            seat=seat,
            runtime="opencode",
            model_configured=invocation.get("model_configured") or invocation.get("model") or "",
            model_resolved=invocation.get("model_resolved") or invocation.get("model"),
            completed=False,
            failure_category="authentication_missing",
            failure_message=auth_msg,
            started_at=started,
            ended_at=_utc_now(),
            attempts=0,
        )
        write_json(out_dir / "result.json", result)
        return result

    model_configured = str(invocation.get("model_configured") or invocation.get("model") or "")
    model = str(invocation.get("model_resolved") or invocation.get("model") or "")
    # Re-resolve only when still unresolved - never substitute a different family.
    if model.startswith("unresolved:") or not model:
        try:
            models = (list_models_fn or list_models)()
            mapping = {
                "model": invocation.get("runtime_options", {}).get("model_spec")
                or {
                    "configured": None,
                    "match_substrings": model.replace("unresolved:", "").split("+")
                    if model.startswith("unresolved:")
                    else [],
                }
            }
            if invocation.get("runtime_options", {}).get("model_spec"):
                mapping = {"model": invocation["runtime_options"]["model_spec"]}
            model = match_opencode_model(mapping, models)
            model_configured = str(
                (mapping.get("model") or {}).get("configured") or model_configured or model
            )
        except ProfileError as e:
            result = empty_result(
                seat=seat,
                runtime="opencode",
                model_configured=model_configured or invocation.get("model") or "",
                model_resolved=None,
                completed=False,
                failure_category=e.category,
                failure_message=e.message,
                started_at=started,
                ended_at=_utc_now(),
                attempts=0,
            )
            write_json(out_dir / "result.json", result)
            return result

    workdir = Path(invocation.get("workdir") or session_dir)
    # Baseline-delta guard: pre-existing dirty trees are allowed. Fail only when
    # OpenCode introduces new/changed paths beyond permitted session artefacts.
    before_snap = snapshot_workdir(workdir)
    allowed = allowed_write_prefixes(workdir, session_dir)
    built = _build_prompt(invocation, attempt=1, repair=False)
    _write_prompt_artefacts(out_dir, built)
    prompt = built["prompt"]
    timeout_sec = int(invocation.get("timeout_sec") or 600)
    seat_timeout_sec = timeout_sec
    exploration_budget = invocation.get("exploration_budget") or {}
    review_mode = str(invocation.get("review_mode") or "packet_only")
    explore_watchdog: ExplorationWatchdog | None = None
    if review_mode == "packet_plus_workspace_read":
        explore_watchdog = ExplorationWatchdog(
            soft_sec=int(exploration_budget.get("max_duration_seconds") or 0),
            absolute_sec=seat_timeout_sec,
            grace_sec=EXPLORATION_GRACE_SECONDS,
            finish_window_sec=EXPLORATION_FINISH_WINDOW_SECONDS,
            tool_call_cap=_exploration_tool_call_cap(exploration_budget),
            max_nudges=EXPLORATION_MAX_NUDGES,
            status_path=out_dir / "seat-status.json",
            progress_path=out_dir / "progress.jsonl",
        )
    frozen_timeout_sec = _frozen_recovery_timeout(seat_timeout_sec)

    permission_json = build_opencode_permission_json(
        workdir=workdir,
        session_dir=session_dir,
        packet_path=invocation.get("packet_path"),
        schema_path=invocation.get("schema_path"),
        prompt_path=invocation.get("prompt_path"),
        review_mode=review_mode,
        workspace_root=invocation.get("workspace_root"),
        env_override=os.environ.get("OPENCODE_PERMISSION")
        or os.environ.get("YONKO_OPENCODE_PERMISSION"),
    )
    env = {
        "OPENCODE_PERMISSION": permission_json,
        "OPENCODE_CONFIG_CONTENT": build_opencode_agent_config(permission_json),
    }

    # Repair re-invokes always run frozen (all exploration tools denied). An
    # exploration seat that ran out of turn without emitting JSON must converge,
    # not explore again.
    repair_permission_json = build_opencode_permission_json(
        workdir=workdir,
        session_dir=session_dir,
        packet_path=invocation.get("packet_path"),
        schema_path=invocation.get("schema_path"),
        prompt_path=invocation.get("prompt_path"),
        review_mode="packet_only",
        workspace_root=invocation.get("workspace_root"),
        env_override=os.environ.get("OPENCODE_PERMISSION")
        or os.environ.get("YONKO_OPENCODE_PERMISSION"),
    )
    repair_env = {
        "OPENCODE_PERMISSION": repair_permission_json,
        "OPENCODE_CONFIG_CONTENT": build_opencode_agent_config(repair_permission_json),
    }

    runner = run_fn or (
        lambda args, **kw: run_opencode(
            args,
            timeout_sec=int(kw.get("timeout_sec") or timeout_sec),
            env=kw.get("env") or env,
            cwd=str(workdir),
            watchdog=kw.get("watchdog"),
        )
    )

    attempts = 0
    schema_valid = False
    failure_category = None
    failure_message = None
    exit_status = None
    timed_out = False
    parsed: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    shared_prefix_hash = built["shared_prefix_hash"]

    # Fresh session per seat: do not pass --continue / --session
    # Message before --file (OpenCode 1.18.x CLI quirk).
    attempts = 1
    # Persist in-progress BEFORE the CLI starts. Without this, result.json still
    # shows attempts=0 from prepare and --execute-awaiting re-kicks every OpenCode
    # seat that is already running (not only the starved ones).
    write_json(
        out_dir / "result.json",
        empty_result(
            seat=seat,
            runtime="opencode",
            model_configured=model_configured,
            model_resolved=model,
            model_actual=model,
            completed=False,
            awaiting_chair_dispatch=False,
            execute_in_progress=True,
            attempts=1,
            started_at=started,
            ended_at=None,
            schema_valid=False,
            raw_log_path=str(raw_path),
            stderr_log_path=str(err_path),
            output_path=str(output_path),
            prompt=prompt_observability(built),
        ),
    )
    (out_dir / "execute.pid").write_text(str(os.getpid()), encoding="utf-8")
    try:
        try:
            stdout_parts: list[str] = []
            stderr_parts: list[str] = []
            opencode_session_id: str | None = None
            nudge_message: str | None = None
            turn_env = env
            turn_watchdog = explore_watchdog
            while True:
                remaining = max(
                    60,
                    int(seat_timeout_sec - (time.monotonic() - t0)),
                )
                turn_timeout = (
                    min(timeout_sec, remaining)
                    if turn_watchdog is not None
                    else min(frozen_timeout_sec, remaining)
                )
                exit_status, turn_out, turn_err, timed_out, failure_category, failure_message = (
                    _run_opencode_once(
                        invocation=invocation,
                        model=model,
                        prompt=prompt,
                        workdir=workdir,
                        timeout_sec=turn_timeout,
                        env=turn_env,
                        runner=runner,
                        prompt_file=None if nudge_message else (out_dir / "prompt.txt"),
                        watchdog=turn_watchdog,
                        session_id=opencode_session_id if nudge_message else None,
                        message=nudge_message,
                    )
                )
                if turn_out:
                    stdout_parts.append(turn_out)
                if turn_err:
                    stderr_parts.append(turn_err)
                if explore_watchdog and explore_watchdog.session_id:
                    opencode_session_id = explore_watchdog.session_id
                stdout = "\n".join(stdout_parts)
                stderr = "\n".join(stderr_parts)
                if failure_category == "coach_nudge" and explore_watchdog is not None:
                    reason = (explore_watchdog.cut_reason or "nudge:no_progress").split(":", 1)[-1]
                    if (
                        explore_watchdog.nudges < explore_watchdog.max_nudges
                        and opencode_session_id
                    ):
                        explore_watchdog.record_nudge(reason)
                        attempts += 1
                        nudge_message = coach_nudge_message(reason)
                        # Nudge continues the OpenCode session under frozen perms so
                        # the model is pushed to emit JSON instead of exploring more.
                        turn_env = repair_env
                        turn_watchdog = ExplorationWatchdog(
                            soft_sec=0,
                            absolute_sec=min(300, remaining),
                            grace_sec=0,
                            tool_call_cap=0,
                            max_nudges=0,
                            status_path=out_dir / "seat-status.json",
                            progress_path=out_dir / "progress.jsonl",
                        )
                        turn_watchdog.session_id = opencode_session_id
                        turn_watchdog.phase = "coaching"
                        turn_watchdog.nudges = explore_watchdog.nudges
                        turn_watchdog.warnings = list(explore_watchdog.warnings)
                        turn_watchdog._write_status(elapsed=time.monotonic() - t0)
                        write_json(
                            out_dir / "result.json",
                            empty_result(
                                seat=seat,
                                runtime="opencode",
                                model_configured=model_configured,
                                model_resolved=model,
                                model_actual=model,
                                completed=False,
                                execute_in_progress=True,
                                attempts=attempts,
                                started_at=started,
                                ended_at=None,
                                schema_valid=False,
                                failure_category="coach_nudge",
                                failure_message=failure_message,
                                raw_log_path=str(raw_path),
                                stderr_log_path=str(err_path),
                                output_path=str(output_path),
                                prompt=prompt_observability(built),
                            ),
                        )
                        continue
                break
        except ProfileError as e:
            result = empty_result(
                seat=seat,
                runtime="opencode",
                model_configured=model_configured,
                model_resolved=model,
                completed=False,
                failure_category=e.category,
                failure_message=e.message,
                started_at=started,
                ended_at=_utc_now(),
                attempts=1,
                execute_in_progress=False,
                prompt=prompt_observability(built),
            )
            write_json(out_dir / "result.json", result)
            return result
    finally:
        try:
            (out_dir / "execute.pid").unlink(missing_ok=True)
        except OSError:
            pass
        try:
            (out_dir / "kickoff.pid").unlink(missing_ok=True)
        except OSError:
            pass

    if len(stdout) > MAX_OUTPUT_BYTES:
        stdout = stdout[:MAX_OUTPUT_BYTES] + "\n/* truncated */\n"
    raw_path.write_text(redact_secrets(stdout), encoding="utf-8")
    err_path.write_text(redact_secrets(stderr), encoding="utf-8")
    exploration_path = None
    if invocation.get("review_mode") == "packet_plus_workspace_read":
        exploration = extract_repository_exploration(
            stdout,
            workspace_root=invocation.get("workspace_root") or workdir,
            budget=exploration_budget,
            duration_seconds=time.monotonic() - t0,
        )
        exploration_path = out_dir / "repository-exploration.json"
        write_json(exploration_path, exploration)
        if exploration.get("truncated"):
            failure_category = "exploration_budget_exceeded"
            failure_message = (
                "OpenCode repository exploration exceeded the frozen risk budget"
            )

    after_snap = snapshot_workdir(workdir)
    illicit = worktree_delta(before_snap, after_snap, allowed_prefixes=allowed)
    if illicit:
        failure_category = "permission_violation"
        shown = ", ".join(illicit[:8]) + ("..." if len(illicit) > 8 else "")
        failure_message = (
            "OpenCode seat changed paths beyond the baseline working tree "
            f"(not pre-existing dirt): {shown}. "
            "Expected read-only packet review. Changes were not discarded."
        )
        write_json(
            out_dir / "worktree-delta.json",
            {
                "baseline_paths": len(before_snap),
                "after_paths": len(after_snap),
                "allowed_prefixes": allowed,
                "illicit_paths": illicit,
            },
        )
        usage = extract_usage_from_opencode_stdout(stdout)
        result = empty_result(
            seat=seat,
            runtime="opencode",
            model_configured=model_configured,
            model_resolved=model,
            model_actual=model,
            completed=False,
            exit_status=exit_status,
            duration_ms=int((time.monotonic() - t0) * 1000),
            started_at=started,
            ended_at=_utc_now(),
            attempts=attempts,
            schema_valid=False,
            timeout=timed_out,
            failure_category=failure_category,
            failure_message=failure_message,
            raw_log_path=str(raw_path),
            stderr_log_path=str(err_path),
            output_path=str(output_path),
            usage=usage,
            prompt=prompt_observability(built, usage=usage),
        )
        write_json(out_dir / "result.json", result)
        return result

    recovered_from_timeout = False
    if timed_out:
        # The explore turn hit its hard cut without findings. Force a frozen
        # recovery immediately - do not let a wandering explore burn the seat.
        attempts += 1
        frozen_invocation = {**invocation, "review_mode": "packet_only"}
        explore_elapsed = time.monotonic() - t0
        recovery_timeout = _frozen_recovery_timeout(seat_timeout_sec, explore_elapsed)
        recovery_built = _build_prompt(
            frozen_invocation,
            attempt=attempts,
            repair=True,
            validation_errors=(
                f"the previous attempt exceeded its {timeout_sec}s exploration "
                "hard-cut while calling tools and returned no findings JSON"
            ),
        )
        if recovery_built["shared_prefix_hash"] == shared_prefix_hash:
            built = recovery_built
            _write_prompt_artefacts(out_dir, built)
            r_exit, r_stdout, r_stderr, r_timed_out, _, r_fail_msg = _run_opencode_once(
                invocation=frozen_invocation,
                model=model,
                prompt=built["prompt"],
                workdir=workdir,
                timeout_sec=recovery_timeout,
                env=repair_env,
                runner=runner,
                prompt_file=out_dir / "prompt.txt",
            )
            recovered = None
            if not r_timed_out:
                recovered = extract_findings_from_opencode_stdout(r_stdout)
                if recovered is None and output_path.is_file() and output_path.stat().st_size > 0:
                    try:
                        candidate = json.loads(output_path.read_text(encoding="utf-8"))
                        if isinstance(candidate, dict) and any(
                            isinstance(candidate.get(k), list)
                            for k in ("findings", "plan_findings", "document_findings")
                        ):
                            recovered = candidate
                    except json.JSONDecodeError:
                        recovered = None
            if recovered is not None:
                stdout = r_stdout
                stderr = r_stderr
                exit_status = r_exit
                timed_out = False
                failure_message = None
                recovered_from_timeout = True
                write_json(output_path, recovered)
                if len(stdout) > MAX_OUTPUT_BYTES:
                    stdout = stdout[:MAX_OUTPUT_BYTES] + "\n/* truncated */\n"
                raw_path.write_text(redact_secrets(stdout), encoding="utf-8")
                err_path.write_text(redact_secrets(stderr), encoding="utf-8")
            else:
                failure_message = r_fail_msg or failure_message

    if timed_out:
        usage = extract_usage_from_opencode_stdout(stdout)
        result = empty_result(
            seat=seat,
            runtime="opencode",
            model_configured=model_configured,
            model_resolved=model,
            model_actual=model,
            completed=False,
            exit_status=exit_status,
            duration_ms=int((time.monotonic() - t0) * 1000),
            started_at=started,
            ended_at=_utc_now(),
            attempts=attempts,
            timeout=True,
            failure_category="timeout",
            failure_message=failure_message,
            raw_log_path=str(raw_path),
            stderr_log_path=str(err_path),
            usage=usage,
            prompt=prompt_observability(built, usage=usage),
        )
        write_json(out_dir / "result.json", result)
        return result

    if exit_status not in (0, None) and exit_status != 0:
        failure_category = categorise_process_failure(stdout, stderr, exit_status or 1)
        first_line = ""
        for line in (stderr or stdout or "").splitlines():
            if line.strip():
                first_line = redact_secrets(line.strip())[:240]
                break
        tip = ""
        low = (stderr or stdout or "").lower()
        if "file not found" in low:
            tip = (
                " (OpenCode treated the prompt as a filename - ensure message precedes --file)"
            )
        elif "external_directory" in low:
            tip = " (packet/session outside workdir - adapter must allow external_directory)"
        failure_message = f"opencode exited {exit_status}"
        if first_line:
            failure_message += f": {first_line}"
        failure_message += tip

    # Prefer file written by model, else OpenCode NDJSON / stdout extraction
    if output_path.is_file() and output_path.stat().st_size > 0:
        try:
            parsed = json.loads(output_path.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict) or not any(
                isinstance(parsed.get(k), list)
                for k in ("findings", "plan_findings", "document_findings")
            ):
                parsed = None
        except json.JSONDecodeError:
            parsed = None

    if parsed is None:
        parsed = extract_findings_from_opencode_stdout(stdout)

    usage = extract_usage_from_opencode_stdout(stdout)

    if parsed is None:
        # Bounded repair re-invoke instead of hard-failing: models often burn the
        # first turn on denied tool calls and emit no JSON (especially packet_only).
        parsed = {}
        write_json(output_path, parsed)
        schema_valid, attempts, failure_category, failure_message, built, usage = (
            _validate_with_optional_repair(
                session_dir=session_dir,
                output_path=output_path,
                parsed=parsed,
                attempts=attempts,
                seat=seat,
                invocation=invocation,
                model=model,
                workdir=workdir,
                timeout_sec=frozen_timeout_sec,
                env=env,
                runner=runner,
                built=built,
                shared_prefix_hash=shared_prefix_hash,
                before_snap=before_snap,
                allowed=allowed,
                raw_path=raw_path,
                err_path=err_path,
                usage=usage,
                repair_env=repair_env,
                force_repair_errors=(
                    "could not extract findings JSON from OpenCode output "
                    "(likely tool calls with no final JSON). Do not call tools. "
                    "Return ONLY one findings JSON object matching the schema."
                ),
            )
        )
    else:
        write_json(output_path, parsed)
        schema_valid, attempts, failure_category, failure_message, built, usage = (
            _validate_with_optional_repair(
                session_dir=session_dir,
                output_path=output_path,
                parsed=parsed,
                attempts=attempts,
                seat=seat,
                invocation=invocation,
                model=model,
                workdir=workdir,
                timeout_sec=frozen_timeout_sec,
                env=env,
                runner=runner,
                built=built,
                shared_prefix_hash=shared_prefix_hash,
                before_snap=before_snap,
                allowed=allowed,
                raw_path=raw_path,
                err_path=err_path,
                usage=usage,
                repair_env=repair_env,
            )
        )

    completed = schema_valid and failure_category is None
    # Signal exits (e.g. -15 SIGTERM) must not count as success even if JSON parsed.
    if timed_out or (exit_status is not None and exit_status != 0):
        completed = False
        if failure_category is None:
            if exit_status is not None and exit_status < 0:
                failure_category = "process_failure"
                failure_message = failure_message or (
                    f"opencode killed by signal {-exit_status}"
                )
            else:
                failure_category = failure_category or "process_failure"
                failure_message = failure_message or f"opencode exited {exit_status}"
    result = empty_result(
        seat=seat,
        runtime="opencode",
        model_configured=model_configured,
        model_resolved=model,
        model_actual=model,
        completed=completed,
        exit_status=exit_status if exit_status is not None else (0 if completed else 1),
        duration_ms=int((time.monotonic() - t0) * 1000),
        started_at=started,
        ended_at=_utc_now(),
        attempts=attempts,
        schema_valid=schema_valid,
        timeout=bool(timed_out),
        failure_category=None if completed else (failure_category or "schema_validation_failure"),
        failure_message=None if completed else failure_message,
        raw_log_path=str(raw_path),
        stderr_log_path=str(err_path),
        output_path=str(output_path),
        usage=usage,
        prompt=prompt_observability(built, usage=usage),
        execute_in_progress=False,
        repository_exploration_path=(
            str(exploration_path) if exploration_path else None
        ),
    )
    if recovered_from_timeout:
        result["recovered_from_timeout"] = True
    write_json(out_dir / "result.json", result)
    return result


def _validate_with_optional_repair(
    *,
    session_dir: Path,
    output_path: Path,
    parsed: dict[str, Any],
    attempts: int,
    seat: str,
    invocation: dict[str, Any],
    model: str,
    workdir: Path,
    timeout_sec: int,
    env: dict[str, str],
    runner: Callable[..., subprocess.CompletedProcess[str]],
    built: dict[str, Any],
    shared_prefix_hash: str,
    before_snap: dict[str, dict[str, str | None]],
    allowed: list[str],
    raw_path: Path,
    err_path: Path,
    usage: dict[str, Any] | None,
    force_repair_errors: str | None = None,
    repair_env: dict[str, str] | None = None,
) -> tuple[bool, int, str | None, str | None, dict[str, Any], dict[str, Any] | None]:
    scripts = Path(__file__).resolve().parents[2]
    validate = scripts / "validate-artifact.sh"
    kind = "findings"
    review_type = "implementation"
    try:
        session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        review_type = session.get("review_type") or "implementation"
    except (OSError, json.JSONDecodeError):
        pass
    if review_type == "plan":
        kind = "plan-findings"
    elif review_type == "document":
        kind = "document-findings"

    def _run_validate() -> tuple[bool, str]:
        if not validate.is_file():
            keys = ("findings", "plan_findings", "document_findings")
            if any(isinstance(parsed.get(k), list) for k in keys):
                return True, "ok"
            return False, "missing findings array"
        proc = subprocess.run(
            ["bash", str(validate), "--kind", kind, "--file", str(output_path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode == 0:
            return True, "ok"
        return False, redact_secrets((proc.stdout or proc.stderr or "")[:500])

    if force_repair_errors:
        ok, msg = False, force_repair_errors
    else:
        ok, msg = _run_validate()
        if ok:
            return True, attempts, None, None, built, usage

    # One bounded model repair: same shared prefix, volatile repair suffix only.
    # The repair always runs frozen - exploration already had its turn, and a
    # seat that failed to emit JSON must converge rather than explore again.
    attempts += 1
    repair_invocation = {**invocation, "review_mode": "packet_only"}
    repair_built = _build_prompt(
        repair_invocation,
        attempt=attempts,
        repair=True,
        validation_errors=msg,
    )
    if repair_built["shared_prefix_hash"] != shared_prefix_hash:
        return False, attempts, "schema_validation_failure", (
            "repair prompt shared-prefix drift - refusing re-invoke"
        ), built, usage
    built = repair_built
    _write_prompt_artefacts(Path(output_path).parent, built)

    exit_status, stdout, stderr, timed_out, fail_cat, fail_msg = _run_opencode_once(
        invocation=repair_invocation,
        model=model,
        prompt=built["prompt"],
        workdir=workdir,
        timeout_sec=timeout_sec,
        env=repair_env or env,
        runner=runner,
        prompt_file=Path(output_path).parent / "prompt.txt",
    )
    if len(stdout) > MAX_OUTPUT_BYTES:
        stdout = stdout[:MAX_OUTPUT_BYTES] + "\n/* truncated */\n"
    raw_path.write_text(redact_secrets(stdout), encoding="utf-8")
    err_path.write_text(redact_secrets(stderr), encoding="utf-8")

    after_snap = snapshot_workdir(workdir)
    illicit = worktree_delta(before_snap, after_snap, allowed_prefixes=allowed)
    if illicit:
        return False, attempts, "permission_violation", (
            "OpenCode repair attempt changed paths beyond baseline"
        ), built, usage

    if timed_out:
        usage = extract_usage_from_opencode_stdout(stdout) or usage
        return False, attempts, "timeout", fail_msg or "repair timed out", built, usage

    repaired = None
    if output_path.is_file() and output_path.stat().st_size > 0:
        try:
            repaired = json.loads(output_path.read_text(encoding="utf-8"))
            if not isinstance(repaired, dict) or not any(
                isinstance(repaired.get(k), list)
                for k in ("findings", "plan_findings", "document_findings")
            ):
                repaired = None
        except json.JSONDecodeError:
            repaired = None
    if repaired is None:
        repaired = extract_findings_from_opencode_stdout(stdout)
    usage = extract_usage_from_opencode_stdout(stdout) or usage
    if repaired is None:
        return False, attempts, "malformed_output", (
            fail_msg or "repair could not extract findings JSON"
        ), built, usage

    parsed.clear()
    parsed.update(repaired)
    write_json(output_path, parsed)
    ok2, msg2 = _run_validate()
    if ok2:
        return True, attempts, None, None, built, usage
    return False, attempts, "schema_validation_failure", msg2 or msg, built, usage
