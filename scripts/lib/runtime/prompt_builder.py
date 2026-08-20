"""Deterministic, cache-friendly reviewer prompt construction.

Stable shared prefix (protocol + Packet + schema) first.
Seat / repair content last. Observational only - correctness never depends on cache.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PROMPT_FORMAT_VERSION = 1

# Stable protocol - no seats, paths, timestamps, models, or session ids.
STABLE_PROTOCOL_V1 = """\
Yonko external reviewer contract (promptFormatVersion 1).

You are an ADVISOR only. Read-only review of the Evidence Packet below.
Do NOT edit, create, delete, format, commit, push, or mutate any repository files.
Use only tools permitted by the frozen review mode. Do NOT share state with other seats.
Do NOT invent specs, tickets, APIs, files, or organisational policy.
Prefer omit over guess. Material findings only. No praise. No change summaries.

Evidence rules:
- Ground every finding in the Packet, or in repository evidence discovered through
  an explicitly enabled read-only exploration mode. Discovered evidence must cite
  repository path + symbol and explain reachability from the reviewed change.
- "Per the ticket/spec" only if that text appears in the Docket inside the Packet.
- Adapter deploy-order / lockfile reminders are notes only, never findings.
- Do not report pre-existing weaknesses unless this change makes them newly reachable,
  worse, or directly relevant to acceptance criteria / Done when.
- Review the FULL Packet for ANY material defect in ANY category.
- Cover every === DIFF: … === label. Roles are attention biases, not boundaries.

Output discipline:
- Return ONLY one JSON object (no markdown outside it).
- Include a findings array (or plan_findings / document_findings as appropriate).
- Confidence is low|medium|high only - never numeric.
- Empty findings still require a filled Attack card in the response.
- Disposition: Remand if findings non-empty; otherwise Content.
"""

ATTACK_CARD_ROWS_V1 = """\
Mandatory Attack card rows (every row; use n/a with reason only when truly inapplicable):
- Golden path compared to
- Precondition diffs vs golden path
- Sibling / shared-parent case
- Guarded delete vs irreversible side effects
- Partial leave vs dissolve
- Presence shapes (if API): omit / null-empty / value / invalid
- Side-effect leaf opened
- External identity / channel
- Leaf branch vs caller state
- Reconstructed outbound preserves sibling inbound fields
- Vendor/runtime event shape vs fixture
- Vendor doc / sample cite
- Hostile re-review of preserve/serialize fix
- Count-then-act lock scope: decision read + its lock vs mutation + its lock; other writers of these rows and their locks
- Transaction rollback vs returned value: what caller receives on rollback per catch; does it reflect rolled-back state?
- Accumulated external side effects: accumulator + remote calls; every exit type; compensation per exit
- Identity sources in diff: each scoped id principal vs resource; diverge case + test or Fail
- Reserved-key lifecycle: claim / mine / live-conflict / stale-repair / release / transfer; concurrent stale race test; batch doomed-destination test; or Fail
- Test asserts leaf effect (not only mid-layer mock)
- Tests added for adversary cases
"""

SEAT_META: dict[str, dict[str, str]] = {
    "shanks": {
        "name": "Shanks",
        "lens": "contracts, compatibility, requirements, API shapes, auth boundaries",
    },
    "blackbeard": {
        "name": "Blackbeard",
        "lens": "correctness, concurrency, retries, golden-path parity, side-effect leaves",
    },
    "buggy": {
        "name": "Buggy",
        "lens": "operational chaos, unusual inputs, ticket-omitted cases",
    },
    "luffy": {
        "name": "Luffy",
        "lens": "company-specific requirements (adapter-gated); still full-packet review",
    },
    "chair": {
        "name": "Chair",
        "lens": "orchestration only - not a reviewer seat",
    },
}


def _lf(text: str) -> str:
    """Normalise to LF newlines; strip trailing spaces per line; ensure trailing newline."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = [ln.rstrip(" \t") for ln in lines]
    body = "\n".join(lines)
    if not body.endswith("\n"):
        body += "\n"
    return body


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_schema_deterministic(schema_path: Path | str) -> str:
    """Stable JSON schema rendering (sorted keys, compact, LF)."""
    path = Path(schema_path)
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def load_packet_text(packet_path: Path | str) -> str:
    """Load Packet as UTF-8 text preserving content; normalise newlines only for prompt."""
    data = Path(packet_path).read_bytes()
    text = data.decode("utf-8")
    return _lf(text)


def build_shared_prefix(
    *,
    packet_text: str,
    packet_hash: str,
    schema_text: str,
    protocol_text: str | None = None,
    prompt_format_version: int = PROMPT_FORMAT_VERSION,
) -> str:
    protocol = _lf(protocol_text if protocol_text is not None else STABLE_PROTOCOL_V1)
    packet = _lf(packet_text)
    schema = _lf(schema_text)
    ph = (packet_hash or "").strip()
    parts = [
        f"=== YONKO_PROMPT_FORMAT_VERSION {prompt_format_version} ===\n",
        "=== SECTION protocol ===\n",
        protocol,
        f"=== SECTION packet packet_hash={ph} ===\n",
        packet,
        "=== SECTION finding_schema ===\n",
        schema,
    ]
    return "".join(parts)


def build_seat_suffix(
    *,
    seat: str,
    review_type: str = "implementation",
    attack_card_text: str | None = None,
    extra_seat_instructions: str | None = None,
) -> str:
    meta = SEAT_META.get(seat) or {"name": seat, "lens": "full-packet material defects"}
    attack = _lf(attack_card_text if attack_card_text is not None else ATTACK_CARD_ROWS_V1)
    lines = [
        "=== SECTION seat ===\n",
        f"Seat identity: {meta['name']} (key: {seat})\n",
        f"Review type: {review_type}\n",
        f"Attention bias (NOT a boundary): {meta['lens']}\n",
        "You still review the FULL Packet above for ANY material defect in ANY category.\n",
        "\n",
        attack,
    ]
    if extra_seat_instructions and extra_seat_instructions.strip():
        lines.append("=== SECTION seat_extra ===\n")
        lines.append(_lf(extra_seat_instructions.strip()))
    return "".join(lines)


def build_volatile_suffix(
    *,
    attempt: int = 1,
    repair_instruction: str | None = None,
    validation_errors: str | None = None,
) -> str:
    parts = ["=== SECTION run ===\n", f"Attempt: {attempt}\n"]
    if repair_instruction and repair_instruction.strip():
        parts.append(_lf(repair_instruction.strip()))
    if validation_errors and validation_errors.strip():
        parts.append("Validation errors:\n")
        parts.append(_lf(validation_errors.strip()))
    if attempt > 1 or (repair_instruction and repair_instruction.strip()):
        parts.append(
            "Return only corrected output matching the supplied finding schema.\n"
        )
    else:
        parts.append(
            "Return ONLY a JSON object matching the finding schema above "
            "(findings / plan_findings / document_findings as appropriate).\n"
        )
    return "".join(parts)


def default_repair_instruction() -> str:
    return (
        "Your previous response failed output validation or produced no extractable "
        "findings JSON.\n"
        "Do not call tools. Return ONLY one JSON object matching the supplied schema.\n"
        "If the Packet alone is insufficient for a claim, omit that claim - still emit "
        "a valid findings array (empty if nothing material).\n"
    )


def build_reviewer_prompt(
    *,
    packet_path: Path | str,
    packet_hash: str,
    schema_path: Path | str,
    seat: str,
    review_type: str = "implementation",
    attempt: int = 1,
    repair_instruction: str | None = None,
    validation_errors: str | None = None,
    extra_seat_instructions: str | None = None,
    protocol_text: str | None = None,
    prompt_format_version: int = PROMPT_FORMAT_VERSION,
) -> dict[str, Any]:
    """Build a byte-stable prompt with shared prefix then variable suffix."""
    packet_text = load_packet_text(packet_path)
    schema_text = render_schema_deterministic(schema_path)
    shared = build_shared_prefix(
        packet_text=packet_text,
        packet_hash=packet_hash,
        schema_text=schema_text,
        protocol_text=protocol_text,
        prompt_format_version=prompt_format_version,
    )
    seat_suffix = build_seat_suffix(
        seat=seat,
        review_type=review_type,
        extra_seat_instructions=extra_seat_instructions,
    )
    volatile = build_volatile_suffix(
        attempt=attempt,
        repair_instruction=repair_instruction,
        validation_errors=validation_errors,
    )
    variable = seat_suffix + volatile
    full = shared + variable
    shared_bytes = len(shared.encode("utf-8"))
    full_bytes = len(full.encode("utf-8"))
    return {
        "prompt_format_version": prompt_format_version,
        "prompt": full,
        "messages": [{"role": "user", "content": full}],
        "shared_prefix": shared,
        "variable_suffix": variable,
        "shared_prefix_hash": _sha256_text(shared),
        "full_prompt_hash": _sha256_text(full),
        "shared_prefix_bytes": shared_bytes,
        "full_prompt_bytes": full_bytes,
        "seat": seat,
        "review_type": review_type,
        "attempt": attempt,
        "packet_hash": packet_hash,
    }


def prompt_observability(
    built: dict[str, Any],
    *,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Observational prompt/cache fields for runtime result.json."""
    tokens = (usage or {}).get("tokens") if isinstance(usage, dict) else None
    cache_read = None
    cache_write = None
    if isinstance(tokens, dict):
        cache_read = tokens.get("cache_read")
        cache_write = tokens.get("cache_write")
    metrics_available = cache_read is not None or cache_write is not None
    # Never infer hit from latency - only when provider reports non-zero cache read.
    cache_hit: bool | None
    if not metrics_available:
        cache_hit = None
    elif isinstance(cache_read, (int, float)) and cache_read > 0:
        cache_hit = True
    elif isinstance(cache_read, (int, float)):
        cache_hit = False
    else:
        cache_hit = None
    return {
        "promptFormatVersion": built.get("prompt_format_version"),
        "sharedPrefixHash": built.get("shared_prefix_hash"),
        "fullPromptHash": built.get("full_prompt_hash"),
        "sharedPrefixBytes": built.get("shared_prefix_bytes"),
        "fullPromptBytes": built.get("full_prompt_bytes"),
        "cacheMetricsAvailable": metrics_available,
        "cacheReadTokens": cache_read,
        "cacheWriteTokens": cache_write,
        "cacheHit": cache_hit,
        "providerCacheKey": None,
    }
