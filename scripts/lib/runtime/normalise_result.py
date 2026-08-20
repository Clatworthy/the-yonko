"""Normalise runtime adapter outputs into the shared runtime-result contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def empty_result(
    *,
    seat: str,
    runtime: str,
    model_configured: str,
    **kwargs: Any,
) -> dict[str, Any]:
    base = {
        "schema_version": 1,
        "seat": seat,
        "runtime": runtime,
        "model_configured": model_configured,
        "model_resolved": kwargs.pop("model_resolved", None),
        "model_actual": None,
        "completed": False,
        "awaiting_chair_dispatch": False,
        "exit_status": None,
        "duration_ms": None,
        "started_at": None,
        "ended_at": None,
        "attempts": 0,
        "schema_valid": False,
        "timeout": False,
        "failure_category": None,
        "failure_message": None,
        "output_path": None,
        "raw_log_path": None,
        "stderr_log_path": None,
        "dispatch_path": None,
        "fallback_occurred": False,
        "skipped_by_routing": False,
        "usage": None,
        "prompt": None,
    }
    base.update(kwargs)
    if base.get("model_resolved") is None:
        base["model_resolved"] = model_configured
    return base


def extract_json_candidate(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    idx = text.find("{")
    while idx != -1:
        depth = 0
        for i in range(idx, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    chunk = text[idx : i + 1]
                    try:
                        obj = json.loads(chunk)
                        if isinstance(obj, dict) and (
                            "findings" in obj
                            or "plan_findings" in obj
                            or "document_findings" in obj
                        ):
                            return obj
                    except json.JSONDecodeError:
                        break
        idx = text.find("{", idx + 1)
    return None


def _payload_has_findings(obj: dict[str, Any]) -> bool:
    return any(
        isinstance(obj.get(k), list)
        for k in ("findings", "plan_findings", "document_findings")
    )


def _text_blobs_from_opencode_event(ev: dict[str, Any]) -> list[str]:
    """Pull assistant text payloads from an OpenCode --format json NDJSON event."""
    blobs: list[str] = []
    part = ev.get("part")
    if isinstance(part, dict):
        for key in ("text", "content", "message", "result"):
            val = part.get(key)
            if isinstance(val, str) and val.strip():
                blobs.append(val)
    for key in ("text", "message", "content", "result"):
        val = ev.get(key)
        if isinstance(val, str) and val.strip():
            blobs.append(val)
    return blobs


def extract_usage_from_opencode_stdout(text: str) -> dict[str, Any] | None:
    """Aggregate cost/tokens from OpenCode NDJSON `step_finish` events."""
    steps: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
        ev_type = str(ev.get("type") or "")
        part_type = str(part.get("type") or "")
        if ev_type not in ("step_finish", "step-finish") and part_type not in (
            "step_finish",
            "step-finish",
        ):
            continue
        tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
        cost = part.get("cost")
        try:
            cost_f = float(cost) if cost is not None else None
        except (TypeError, ValueError):
            cost_f = None
        steps.append(
            {
                "reason": part.get("reason"),
                "cost": cost_f,
                "tokens": {
                    "total": tokens.get("total"),
                    "input": tokens.get("input"),
                    "output": tokens.get("output"),
                    "reasoning": tokens.get("reasoning"),
                    "cache_read": (tokens.get("cache") or {}).get("read")
                    if isinstance(tokens.get("cache"), dict)
                    else None,
                    "cache_write": (tokens.get("cache") or {}).get("write")
                    if isinstance(tokens.get("cache"), dict)
                    else None,
                },
            }
        )
    if not steps:
        return None

    def _sum(key: str) -> int:
        total = 0
        saw = False
        for step in steps:
            val = (step.get("tokens") or {}).get(key)
            if isinstance(val, (int, float)):
                total += int(val)
                saw = True
        return total if saw else 0

    cost_total = 0.0
    cost_saw = False
    for step in steps:
        if isinstance(step.get("cost"), (int, float)):
            cost_total += float(step["cost"])
            cost_saw = True

    return {
        "source": "opencode_step_finish",
        "steps": len(steps),
        "cost": cost_total if cost_saw else None,
        "tokens": {
            "total": _sum("total"),
            "input": _sum("input"),
            "output": _sum("output"),
            "reasoning": _sum("reasoning"),
            "cache_read": _sum("cache_read"),
            "cache_write": _sum("cache_write"),
        },
        "step_details": steps,
    }


def extract_findings_from_opencode_stdout(text: str) -> dict[str, Any] | None:
    """Extract findings object from OpenCode `run --format json` NDJSON stream.

    Live shape (1.18.x): each line is an event. Findings arrive in a late
    `{"type":"text","part":{"type":"text","text":"<json or ```json ...```>"}}`
    event. Whole-stdout brace walks fail because tool_use events contain large
    nested JSON without a findings array.
    """
    text = text or ""
    if not text.strip():
        return None

    # Prefer NDJSON line walk (last matching text event wins).
    last: dict[str, Any] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        if _payload_has_findings(ev):
            last = ev
            continue
        for blob in _text_blobs_from_opencode_event(ev):
            cand = extract_json_candidate(blob)
            if isinstance(cand, dict) and _payload_has_findings(cand):
                last = cand
    if last is not None:
        return last

    # Fallback: whole buffer (plain JSON / fenced JSON, no event stream)
    return extract_json_candidate(text)


def extract_repository_exploration(
    text: str,
    *,
    workspace_root: Path | str,
    budget: dict[str, Any] | None = None,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    files: list[dict[str, Any]] = []
    searches: list[dict[str, Any]] = []
    lsp: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    seen_searches: set[str] = set()
    seen_lsp: set[str] = set()

    for line in (text or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        tool = str(
            part.get("tool")
            or part.get("toolName")
            or event.get("tool")
            or ""
        ).lower()
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        payload = state.get("input") if isinstance(state.get("input"), dict) else {}
        if not payload:
            raw = part.get("input") or event.get("input") or event.get("arguments")
            payload = raw if isinstance(raw, dict) else {}

        if tool in ("read", "readfile"):
            raw_path = (
                payload.get("filePath")
                or payload.get("path")
                or payload.get("file")
            )
            if raw_path:
                path = Path(str(raw_path)).expanduser()
                if not path.is_absolute():
                    path = root / path
                path = path.resolve()
                try:
                    rel = str(path.relative_to(root))
                    repository = rel.split("/", 1)[0]
                except ValueError:
                    rel = str(path)
                    repository = "external"
                if rel not in seen_files:
                    seen_files.add(rel)
                    files.append(
                        {
                            "repository": repository,
                            "path": rel,
                            "reason": "OpenCode read tool",
                        }
                    )
        elif tool in ("grep", "glob", "list"):
            query = str(
                payload.get("pattern")
                or payload.get("query")
                or payload.get("glob")
                or payload.get("path")
                or ""
            )
            key = f"{tool}:{query}"
            if query and key not in seen_searches:
                seen_searches.add(key)
                searches.append(
                    {
                        "tool": tool,
                        "query": query,
                        "resultCount": None,
                    }
                )
        elif tool == "lsp":
            symbol = str(
                payload.get("symbol")
                or payload.get("query")
                or payload.get("name")
                or ""
            )
            operation = str(
                payload.get("operation")
                or payload.get("method")
                or payload.get("action")
                or ""
            )
            key = f"{operation}:{symbol}"
            if key not in seen_lsp:
                seen_lsp.add(key)
                lsp.append({"symbol": symbol, "operation": operation})

    limits = dict(budget or {})
    used = {
        "files_read": len(files),
        "searches": len(searches),
        "lsp_queries": len(lsp),
        "extra_bytes": 0,
        "duration_seconds": int(duration_seconds or 0),
    }
    for item in files:
        candidate = root / str(item.get("path") or "")
        try:
            used["extra_bytes"] += candidate.stat().st_size
        except OSError:
            pass
    truncated = (
        used["files_read"] > int(limits.get("max_files_read") or 0)
        if limits.get("max_files_read") is not None
        else False
    ) or (
        used["searches"] > int(limits.get("max_searches") or 0)
        if limits.get("max_searches") is not None
        else False
    ) or (
        used["lsp_queries"] > int(limits.get("max_lsp_queries") or 0)
        if limits.get("max_lsp_queries") is not None
        else False
    ) or (
        used["extra_bytes"] > int(limits.get("max_extra_bytes") or 0)
        if limits.get("max_extra_bytes") is not None
        else False
    ) or (
        used["duration_seconds"]
        > int(limits.get("max_duration_seconds") or 0)
        if limits.get("max_duration_seconds") is not None
        else False
    )
    return {
        "schema_version": 1,
        "enabled": True,
        "mode": "packet_plus_workspace_read",
        "workspace_root": str(root),
        "filesRead": files,
        "searches": searches,
        "lspLookups": lsp,
        "budget": {**limits, **used},
        "truncated": truncated,
    }


def redact_secrets(text: str) -> str:
    """Strip credential-looking values from logs (never print secrets)."""
    patterns = [
        (re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"), r"\1=***"),
        (re.compile(r"(?i)authorization:\s*\S+"), "authorization: ***"),
        (re.compile(r"sk-[A-Za-z0-9]{10,}"), "sk-***"),
    ]
    out = text
    for rx, repl in patterns:
        out = rx.sub(repl, out)
    return out


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def ensure_under(base: Path, target: Path) -> Path:
    """Reject path traversal outside base."""
    base_r = base.resolve()
    target_r = target.resolve()
    if base_r != target_r and base_r not in target_r.parents:
        raise ValueError(f"path escapes session directory: {target}")
    return target_r
