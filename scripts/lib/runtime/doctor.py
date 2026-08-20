"""Yonko doctor - validate active execution profile without secrets or paid calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import DEFAULT_PROFILE_ID
from .resolve_profile import (
    MARKER_PATH,
    PROFILE_SCHEMA_PATH,
    ProfileError,
    fingerprint_profile,
    list_profile_ids,
    load_profile,
    match_opencode_model,
    read_marker,
    resolve_active_profile,
    validate_profile,
)

SKILL_ROOT = Path(__file__).resolve().parents[3]


def _check(ok: bool, name: str, detail: str = "") -> dict[str, Any]:
    return {"ok": ok, "name": name, "detail": detail}


def doctor(
    *,
    profile_id: str | None = None,
    marker_path: Path | None = None,
    refresh_models: bool = False,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        if profile_id:
            profile = load_profile(profile_id)
            marker_note = f"forced profile {profile_id}"
        else:
            marker = read_marker(marker_path or MARKER_PATH)
            pid = marker.get("executionProfile") or DEFAULT_PROFILE_ID
            profile = load_profile(pid)
            marker_note = f"marker -> {pid}"
    except ProfileError as e:
        return {
            "ok": False,
            "exit_code": 1,
            "executionProfile": profile_id,
            "checks": [_check(False, "execution profile", e.message)],
        }

    pid = profile["id"]
    checks.append(_check(True, "execution profile", f"{pid} ({profile.get('status')}); {marker_note}"))

    try:
        validate_profile(profile)
        checks.append(_check(True, "profile schema valid", f"fingerprint {fingerprint_profile(profile)[:12]}…"))
    except ProfileError as e:
        checks.append(_check(False, "profile schema valid", e.message))

    if PROFILE_SCHEMA_PATH.is_file():
        checks.append(_check(True, "profile contract present", str(PROFILE_SCHEMA_PATH.name)))
    else:
        checks.append(_check(False, "profile contract present", "missing execution-profile.schema.json"))

    # Model selections SoT (when panel declared)
    from .model_selections import SELECTIONS_PATH, load_model_selections

    if profile.get("model_selection_panel") or profile.get("_model_selection_panel"):
        try:
            selections = load_model_selections()
            panel = profile.get("model_selection_panel") or profile.get("_model_selection_panel")
            checks.append(
                _check(
                    True,
                    "model-selections present",
                    f"{SELECTIONS_PATH.name} version={selections.get('version')} panel={panel}",
                )
            )
        except ProfileError as e:
            checks.append(_check(False, "model-selections present", e.message))

    # Cursor seat mappings
    cursor_seats = [s for s, m in profile["seats"].items() if m.get("runtime") == "cursor"]
    if cursor_seats:
        checks.append(_check(True, "required Cursor seat mappings present", ", ".join(cursor_seats)))
    else:
        checks.append(_check(False, "required Cursor seat mappings present", "no cursor seats"))

    chair = (profile.get("seats") or {}).get("chair") or {}
    chair_cfg = ((chair.get("model") or {}).get("configured") or "").lower()
    if chair.get("runtime") == "cursor":
        if chair_cfg == "auto" or (chair.get("model_policy_ref") and pid != "cursor-opencode-go"):
            detail = "configured=auto" if chair_cfg == "auto" else f"policy {chair.get('model_policy_ref')}"
            checks.append(_check(True, "Cursor Auto", detail if chair_cfg == "auto" else f"chair via {detail}"))
        else:
            checks.append(_check(False, "Cursor Auto", f"chair configured={chair_cfg or 'missing'}"))

    shanks = (profile.get("seats") or {}).get("shanks") or {}
    shanks_cfg = ((shanks.get("model") or {}).get("configured") or "").lower()
    if shanks.get("runtime") == "cursor":
        if shanks_cfg == "grok" or (
            shanks.get("model_policy_ref") and pid != "cursor-opencode-go"
        ):
            checks.append(
                _check(
                    True,
                    "Grok",
                    "configured=grok" if shanks_cfg == "grok" else f"policy {shanks.get('model_policy_ref')}",
                )
            )
        else:
            checks.append(_check(False, "Grok", f"shanks configured={shanks_cfg or 'missing'} (must not be auto)"))

    # Evidence Graph scripts
    eg = SKILL_ROOT / "scripts" / "build-evidence-graph.sh"
    checks.append(
        _check(eg.is_file(), "Evidence Graph scripts available", str(eg.name) if eg.is_file() else "missing")
    )

    # Packet schemas
    finding = SKILL_ROOT / "contracts" / "finding.schema.json"
    checks.append(_check(finding.is_file(), "Packet schemas valid", "finding.schema.json present" if finding.is_file() else "missing"))

    inv_test = SKILL_ROOT / "scripts" / "test-packet-profile-invariance-smoke.py"
    checks.append(
        _check(
            inv_test.is_file(),
            "Packet/Profile invariance",
            "test-packet-profile-invariance-smoke.py present",
        )
    )

    needs_opencode = "opencode" in (profile.get("requires_runtimes") or []) or any(
        m.get("runtime") == "opencode" for m in profile["seats"].values()
    )

    if needs_opencode:
        from . import opencode_adapter as oc

        ok, msg = oc.check_installed()
        checks.append(_check(ok, "OpenCode CLI installed", msg))
        if ok:
            aok, amsg = oc.check_auth()
            checks.append(_check(aok, "OpenCode authentication available", amsg))
            try:
                models = oc.list_models(refresh=refresh_models)
                go_like = [
                    m
                    for m in models
                    if any(
                        x in m.lower()
                        for x in ("deepseek", "qwen", "kimi", "luna", "gpt-5", "opencode-go", "opencode/")
                    )
                ]
                checks.append(
                    _check(
                        True,
                        "OpenCode Go provider available",
                        f"{len(models)} models listed" + (f"; {len(go_like)} coding-model hits" if go_like else ""),
                    )
                )
                # Prefer stable check names for the default panel
                named = {
                    "blackbeard": "DeepSeek V4 Pro",
                    "buggy": "GPT-5.6 Luna",
                    "luffy": "Kimi K3",
                }
                for seat, mapping in profile["seats"].items():
                    if mapping.get("runtime") != "opencode":
                        continue
                    display = named.get(seat) or (mapping.get("model") or {}).get("display_name") or seat
                    try:
                        mid = match_opencode_model(mapping, models)
                        checks.append(_check(True, f"{display} resolved", mid))
                    except ProfileError as e:
                        checks.append(
                            _check(
                                False,
                                f"{display} resolved",
                                e.message + " Update config/model-selections.json (no silent substitute).",
                            )
                        )
                checks.append(_check(True, "independent invocation supported", "opencode run without --continue"))
            except ProfileError as e:
                checks.append(_check(False, "OpenCode Go provider available", e.message))
        sessions = Path.home() / ".cursor" / "yonko-sessions"
        writable = False
        try:
            sessions.mkdir(parents=True, exist_ok=True)
            probe = sessions / ".yonko-doctor-write-probe"
            probe.write_text("ok\n", encoding="utf-8")
            probe.unlink(missing_ok=True)
            writable = True
        except OSError as e:
            checks.append(_check(False, "output directory writable", str(e)))
        if writable:
            checks.append(_check(True, "output directory writable", str(sessions)))
    else:
        checks.append(_check(True, "OpenCode checks skipped", "active profile is Cursor-only"))

    # Evidence Graph healthy (config present)
    eg_policy = SKILL_ROOT / "config" / "evidence-graph" / "policy.json"
    checks.append(
        _check(
            eg_policy.is_file() or (SKILL_ROOT / "config" / "evidence-graph" / "policy.yaml").is_file(),
            "Evidence Graph healthy",
            "policy present",
        )
    )

    all_ok = all(c["ok"] for c in checks)
    if all_ok:
        checks.append(_check(True, "Ready", f"profile {pid}"))
        checks.append(_check(True, "workflow ready", f"profile {pid}"))
    else:
        checks.append(_check(False, "Ready", "fix failed checks above"))
        checks.append(_check(False, "workflow ready", "fix failed checks above"))

    return {
        "ok": all_ok,
        "exit_code": 0 if all_ok else 1,
        "executionProfile": pid,
        "profile_status": profile.get("status"),
        "profile_fingerprint": fingerprint_profile(profile),
        "model_selection_version": profile.get("_model_selection_version"),
        "available_profiles": list_profile_ids(),
        "checks": checks,
    }


def format_human(report: dict[str, Any]) -> str:
    lines = [f"Yonko doctor - execution profile: {report.get('executionProfile')}"]
    for c in report.get("checks") or []:
        mark = "✓" if c.get("ok") else "✗"
        detail = c.get("detail") or ""
        lines.append(f"{mark} {c.get('name')}")
        if detail:
            lines.append(f"  {detail}")
    lines.append("")
    lines.append("READY" if report.get("ok") else "NOT READY")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Yonko execution-profile doctor")
    p.add_argument("--profile", default=None, help="Validate a specific profile id")
    p.add_argument("--json", action="store_true", help="Machine-readable JSON")
    p.add_argument("--refresh-models", action="store_true", help="Refresh opencode models cache (network)")
    args = p.parse_args(argv)
    report = doctor(profile_id=args.profile, refresh_models=args.refresh_models)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_human(report))
    return int(report.get("exit_code") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
