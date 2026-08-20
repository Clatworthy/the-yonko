#!/usr/bin/env python3
"""Optional org ship gate: validate hostile post-council review of the live tree.

Required before finalize --verdict pass on implementation reviews only when the
matched project adapter sets org_ship_gate.enabled: true.

Council Content is not enough when the adapter enables this gate. The gate must
attack the working-tree change as if the reviewer did not implement it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

GATE_DIRNAME = "org-ship-gate"
RESULT_NAME = "result.json"
STATUS_NAME = "status.json"
BRIEF_NAME = "GATE.md"
ATTACK_CARD_MARKERS = (
    "Attack card",
    "Golden path",
    "Identity sources",
    "Reserved-key lifecycle",
)


def gate_dir(session_dir: Path) -> Path:
    return session_dir / GATE_DIRNAME


def result_path(session_dir: Path) -> Path:
    return gate_dir(session_dir) / RESULT_NAME


def status_path(session_dir: Path) -> Path:
    return gate_dir(session_dir) / STATUS_NAME


def load_adapter_org_gate(session_dir: Path) -> dict[str, Any]:
    """Return org_ship_gate config from session or skill adapters (best-effort)."""
    for rel in (
        "evidence/project-adapter.json",
        "runtime/project-adapter.json",
        "adapter.json",
    ):
        p = session_dir / rel
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            gate = data.get("org_ship_gate") or {}
            if isinstance(gate, dict) and gate:
                return gate
    root = Path.home() / ".cursor" / "skills" / "the-yonko"
    for name in ("project-adapters.local.yaml", "project-adapters.yaml"):
        p = root / "config" / name
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if re.search(
            r"org_ship_gate:\s*\n(?:[ \t].*\n)*?[ \t]+enabled:\s*true",
            text,
        ):
            return {"enabled": True, "source": str(p)}
    return {"enabled": False}


def gate_required(session_dir: Path, review_type: str | None = None) -> bool:
    if review_type is None:
        try:
            review_type = json.loads(
                (session_dir / "session.json").read_text(encoding="utf-8")
            ).get("review_type")
        except (OSError, json.JSONDecodeError):
            review_type = "implementation"
    if review_type != "implementation":
        return False
    cfg = load_adapter_org_gate(session_dir)
    return bool(cfg.get("enabled"))


def validate_result_obj(obj: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return False, ["result_not_object"]
    findings = obj.get("findings")
    if not isinstance(findings, list):
        errors.append("findings_missing_or_not_list")
    elif findings:
        errors.append("findings_non_empty")
    attack = obj.get("attack_card") or obj.get("attackCard") or ""
    if not isinstance(attack, str) or len(attack.strip()) < 80:
        errors.append("attack_card_missing_or_too_short")
    else:
        if "Attack card" not in attack and not any(
            m in attack for m in ATTACK_CARD_MARKERS[1:]
        ):
            errors.append("attack_card_missing_markers")
    verdict = str(obj.get("verdict") or "").lower()
    disposition = str(obj.get("disposition") or "").lower()
    if findings == [] and not errors:
        if verdict not in ("pass", "content", ""):
            # allow empty verdict if disposition Content
            if disposition not in ("content", "pass"):
                errors.append("verdict_not_pass")
        if disposition and disposition not in ("content", "pass"):
            errors.append("disposition_not_content")
    bot_break = obj.get("one_sentence_bot_would_break") or obj.get("org_gate_would_break")
    if not bot_break or not str(bot_break).strip():
        errors.append("missing_what_org_gate_would_still_break")
    # Hostile posture flag - optional but Fail if author admits confirmatory pass
    posture = str(obj.get("posture") or "").lower()
    if posture in ("confirmatory", "author", "rubber_stamp", "rubber-stamp"):
        errors.append("posture_confirmatory_forbidden")
    summary = str(obj.get("summary") or obj.get("notes") or "").lower()
    for banned in (
        "mirrors helper",
        "tests exist so",
        "council already passed",
        "yonko seats agreed",
        "luffy already",
    ):
        if banned in summary:
            errors.append("confirmatory_language_in_summary")
            break
    return (len(errors) == 0), errors


def validate_session_gate(session_dir: Path) -> dict[str, Any]:
    required = gate_required(session_dir)
    path = result_path(session_dir)
    if not required:
        return {
            "ok": True,
            "required": False,
            "code": None,
            "message": "org_ship_gate not required for this session",
        }
    if not path.is_file():
        return {
            "ok": False,
            "required": True,
            "code": "ORG_SHIP_GATE_REQUIRED",
            "message": (
                "Implementation Pass requires scripts/run-org-ship-gate.sh "
                "(OpenCode Go / opencode-go/gpt-5.6-luna hostile org ship gate) "
                "before finalize."
            ),
            "path": str(path),
        }
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "required": True,
            "code": "ORG_SHIP_GATE_FAILED",
            "message": f"result.json invalid JSON: {exc}",
            "path": str(path),
        }
    ok, errors = validate_result_obj(obj)
    if not ok:
        return {
            "ok": False,
            "required": True,
            "code": "ORG_SHIP_GATE_FAILED",
            "message": "Org ship gate did not Pass",
            "errors": errors,
            "findings_count": len(obj.get("findings") or [])
            if isinstance(obj.get("findings"), list)
            else None,
            "path": str(path),
        }
    return {
        "ok": True,
        "required": True,
        "code": None,
        "message": "org_ship_gate Pass",
        "path": str(path),
        "model": obj.get("model"),
    }


def write_status(session_dir: Path, payload: dict[str, Any]) -> Path:
    d = gate_dir(session_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = status_path(session_dir)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    session = args.session.expanduser().resolve()
    result = validate_session_gate(session)
    write_status(session, result)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"org-ship-gate: {'PASS' if result.get('ok') else 'FAIL'} "
            f"({result.get('code') or result.get('message')})"
        )
        if result.get("errors"):
            for e in result["errors"]:
                print(f"  - {e}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
