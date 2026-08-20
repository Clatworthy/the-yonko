"""Packet assembly helpers for V4 Phase 1 (structural only).

- Linked-plan handoff: embed PLAN.approved.md only (never plan session noise)
- Deterministic paragraph deduplication with receipt

Information Preservation Principle: optimise representation, never discard
engineering information. DIFF bodies and fenced code are never rewritten.
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Any

HEADER_RE = re.compile(r"(?m)^(=== .+? ===)\s*$")
MIN_DEDUP_CHARS = 120


def resolve_linked_session_dir(session_dir: pathlib.Path, linked_raw: str | None) -> pathlib.Path | None:
    if not linked_raw:
        return None
    p = pathlib.Path(linked_raw)
    if p.is_dir():
        return p.resolve()
    cand = (session_dir.parent / linked_raw)
    if cand.is_dir():
        return cand.resolve()
    # basename under sessions root
    cand2 = session_dir.parent / pathlib.Path(linked_raw).name
    if cand2.is_dir():
        return cand2.resolve()
    return None


def load_linked_approved_plan(
    session_dir: pathlib.Path,
    scrub_fn,
) -> tuple[str, dict[str, Any]]:
    """Load PLAN.approved.md from linked_session. Fail closed if linked but missing.

    Returns (scrubbed_text, handoff_meta). handoff_meta empty if no linked_session.
    """
    session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    linked_raw = session.get("linked_session")
    if not linked_raw:
        return "", {}

    linked_dir = resolve_linked_session_dir(session_dir, linked_raw)
    if linked_dir is None:
        raise SystemExit(f"yonko: linked_session not found: {linked_raw}")

    # Prefer plan session marker when present
    linked_sess = linked_dir / "session.json"
    if linked_sess.exists():
        try:
            ls = json.loads(linked_sess.read_text(encoding="utf-8"))
            rt = ls.get("review_type")
            if rt and rt != "plan":
                raise SystemExit(
                    f"yonko: linked_session review_type is {rt!r}, expected plan: {linked_dir}"
                )
        except json.JSONDecodeError:
            pass

    plan_path = linked_dir / "PLAN.approved.md"
    if not plan_path.is_file():
        raise SystemExit(
            f"yonko: linked plan handoff requires PLAN.approved.md at {plan_path}"
        )

    raw = plan_path.read_text(encoding="utf-8", errors="replace")
    scrubbed, notes = scrub_fn(raw)
    staged = session_dir / "evidence" / "approved-plan.md"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(scrubbed, encoding="utf-8")

    meta = {
        "linked_session": str(linked_dir),
        "source_path": str(plan_path.resolve()),
        "staged_path": "evidence/approved-plan.md",
        "bytes": len(scrubbed.encode("utf-8")),
        "scrub_notes": sorted(set(notes)),
        "excluded": [
            "findings.json",
            "packet.md",
            "rejected findings",
            "prior packets",
            "planning dialogue",
            "bulletins.md",
            "events.jsonl",
        ],
    }
    return scrubbed, meta


def _norm_para(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _split_sections(packet: str) -> list[tuple[str | None, str]]:
    """Return ordered (header_or_None, body) segments preserving order."""
    parts = HEADER_RE.split(packet)
    if not parts:
        return [(None, "")]
    out: list[tuple[str | None, str]] = []
    if parts[0]:
        out.append((None, parts[0]))
    i = 1
    while i + 1 < len(parts):
        out.append((parts[i], parts[i + 1]))
        i += 2
    if i < len(parts) and parts[i]:
        out.append((None, parts[i]))
    return out


def _section_name(header: str | None) -> str:
    if not header:
        return "preamble"
    return header.strip("= ").strip()


def _is_protected(name: str) -> bool:
    # Never dedupe diffs or code-bearing reader sections
    return (
        name.startswith("DIFF:")
        or name == "IMPACT READERS"
        or name.startswith("READER:")
    )


def _process_body(
    body: str,
    section_name: str,
    protected: bool,
    seen: dict[str, dict[str, Any]],
    next_ref: list[int],
    replacements: list[dict[str, Any]],
) -> str:
    if protected or not body:
        return body

    lines = body.splitlines(True)
    out_chunks: list[str] = []
    para_buf: list[str] = []
    in_fence = False

    def flush_para():
        if not para_buf:
            return
        para = "".join(para_buf)
        para_buf.clear()
        stripped = para.strip("\n")
        # Preserve leading/trailing newlines structure roughly
        leading = ""
        trailing = ""
        m_lead = re.match(r"^(\n*)", para)
        if m_lead:
            leading = m_lead.group(1)
        # Keep original if too short or only whitespace
        core = stripped
        if len(core) < MIN_DEDUP_CHARS:
            out_chunks.append(para)
            return
        key = _norm_para(core)
        if not key:
            out_chunks.append(para)
            return
        if key not in seen:
            seen[key] = {
                "ref": next_ref[0],
                "source_section": section_name,
                "original": core,
                "occurrences": 1,
                "duplicate_sections": [],
            }
            next_ref[0] += 1
            out_chunks.append(para)
            return
        # Duplicate
        info = seen[key]
        info["occurrences"] += 1
        if section_name not in info["duplicate_sections"] and section_name != info["source_section"]:
            info["duplicate_sections"].append(section_name)
        ref_line = f"[dedup:ref={info['ref']} source={info['source_section']}]\n"
        # Prefer single trailing newline after ref
        out_chunks.append(leading + ref_line if leading else ref_line)
        # track savings later from seen dict

    for line in lines:
        if line.lstrip().startswith("```"):
            if in_fence:
                # closing fence - include line, leave fence
                para_buf.append(line)
                # flush as protected-ish: dump buffer without dedupe while in fence
                out_chunks.append("".join(para_buf))
                para_buf.clear()
                in_fence = False
            else:
                # flush any open para first
                flush_para()
                in_fence = True
                para_buf.append(line)
            continue
        if in_fence:
            para_buf.append(line)
            continue
        # blank line ends paragraph
        if re.match(r"^\s*$", line):
            flush_para()
            out_chunks.append(line)
        else:
            para_buf.append(line)
    flush_para()
    return "".join(out_chunks)


def dedupe_packet(packet: str) -> tuple[str, dict[str, Any]]:
    """Deterministic exact paragraph dedupe. Never touches DIFF sections or fenced code."""
    sections = _split_sections(packet)
    seen: dict[str, dict[str, Any]] = {}
    next_ref = [1]
    replacements: list[dict[str, Any]] = []

    new_parts: list[str] = []
    for header, body in sections:
        name = _section_name(header)
        protected = _is_protected(name)
        new_body = _process_body(body, name, protected, seen, next_ref, replacements)
        if header:
            new_parts.append(header if header.endswith("\n") else header + "\n")
        new_parts.append(new_body)

    # Build replacement receipt for keys that actually duplicated
    for info in sorted(seen.values(), key=lambda x: x["ref"]):
        if info["occurrences"] < 2:
            continue
        orig = info["original"]
        ref_line = f"[dedup:ref={info['ref']} source={info['source_section']}]\n"
        saved_each = len(orig.encode("utf-8")) - len(ref_line.encode("utf-8"))
        replacements.append({
            "ref": info["ref"],
            "source_section": info["source_section"],
            "duplicate_sections": info["duplicate_sections"],
            "chars_normalized": len(_norm_para(orig)),
            "occurrences": info["occurrences"],
            "bytes_saved_estimate": max(0, saved_each) * (info["occurrences"] - 1),
        })

    bytes_saved = sum(r["bytes_saved_estimate"] for r in replacements)
    receipt = {
        "min_chars": MIN_DEDUP_CHARS,
        "replacements": replacements,
        "bytes_saved_estimate": bytes_saved,
    }
    return "".join(new_parts), receipt
