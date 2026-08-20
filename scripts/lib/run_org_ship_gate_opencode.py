#!/usr/bin/env python3
"""Run the optional org ship gate via OpenCode Go (OpenAI model).

Default model: opencode-go/gpt-5.6-luna (same OpenAI-family Go path as Buggy).
Not the standalone OpenAI CLI path. Reviews the live workspace with hostile posture.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_MODEL = "opencode-go/gpt-5.6-luna"
GATE_DIRNAME = "org-ship-gate"


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _extract_findings(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "findings" in obj:
            return obj
        if isinstance(obj, dict) and "result" in obj:
            inner = _extract_findings(str(obj.get("result") or ""))
            if inner:
                return inner
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict) and "findings" in obj:
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
                        if isinstance(obj, dict) and "findings" in obj:
                            return obj
                    except json.JSONDecodeError:
                        break
        idx = text.find("{", idx + 1)
    return None


def _repo_lines(session: Path) -> str:
    repos_json = session / "evidence" / "repos.json"
    if not repos_json.is_file():
        return "- (no evidence/repos.json - review workspace root)"
    try:
        data = json.loads(repos_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "- (invalid repos.json)"
    repos = data.get("repos") or (data if isinstance(data, list) else [])
    lines = []
    for r in repos:
        if isinstance(r, dict):
            path = r.get("path") or ""
            label = r.get("label") or r.get("name") or path
            if path:
                lines.append(f"- {label}: {path}")
    return "\n".join(lines) if lines else "- (empty repos list)"


def _policy_paths(session: Path) -> list[str]:
    from lib.org_ship_gate import load_adapter_org_gate  # noqa: E402

    cfg = load_adapter_org_gate(session)
    paths: list[str] = []
    for item in cfg.get("skills") or []:
        if item:
            paths.append(str(item))
    adv = cfg.get("adversarial_rule")
    if adv:
        paths.append(str(adv))
    return paths


def _write_gate_md(session: Path, workspace: Path, out_json: Path) -> Path:
    gate_dir = session / GATE_DIRNAME
    gate_dir.mkdir(parents=True, exist_ok=True)
    brief = gate_dir / "GATE.md"
    policy_paths = _policy_paths(session)
    if policy_paths:
        policy_block = "\n".join(f"{i}. `{p}`" for i, p in enumerate(policy_paths, 1))
    else:
        policy_block = (
            "No adapter policy files are configured. Review the live working tree "
            "with hostile posture and a filled Attack card."
        )
    brief.write_text(
        f"""# Org ship gate (hostile) - OpenCode Go

You are **not** the author. You are **not** a Yonko council seat.
You are a hostile org-standards reviewer running **locally**
via **OpenCode Go** on an **OpenAI** model (`{DEFAULT_MODEL}`).

Yonko seats may already have returned Content. **Ignore that.** Attack this change
as if you never saw their Pass and as if a stranger opened the merge request.

## Mandatory policy (read verbatim, then review)

{policy_block}

## Workspace

- Root: `{workspace}`
- Review the **live working tree / branch diff** against default branch (main or master).
- Session repos:
{_repo_lines(session)}
- Diff patches from the Yonko session are attached as files - use them, then open
  leaves in the live tree (read/grep/lsp). Do not trust council Pass.

## How to review (hostile posture)

- Hunt bugs, regressions, ownership boundary violations, missing adversary tests.
- Do **not** run Gradle / lint / test / build (bash is denied).
- Do **not** rubber-stamp "helper was called", "tests exist", or "matches absorb/move".
- Open side-effect leaves. Name principal vs resource identity. Enumerate reserved-key
  lifecycle when uniqueness / lease / claim rows are touched.
- Empty findings **require** a filled Attack card plus one sentence: what would the
  org ship gate still try to break?

## Output

Put the JSON object in your **final message** (stdout). Prefer also writing it to:
`{out_json}`

```json
{{
  "schema_version": 1,
  "verdict": "pass",
  "disposition": "Content",
  "posture": "hostile",
  "model": "{DEFAULT_MODEL}",
  "findings": [],
  "attack_card": "Attack card:\\n- Golden path compared to: ...\\n- Identity sources in diff: ...\\n- Reserved-key lifecycle: ...\\n...",
  "one_sentence_bot_would_break": "...",
  "reviewed_repos": []
}}
```

If material bugs: verdict/disposition = fail/Remand with non-empty findings.
Forbidden: confirmatory Pass because Yonko agreed; skipping Attack card.
""",
        encoding="utf-8",
    )
    return brief


def _permission_json(*, workspace: Path, session: Path, gate_dir: Path) -> str:
    sensitive = ".env"
    external: dict[str, str] = {}
    for path in (session, gate_dir, workspace):
        resolved = str(path.resolve())
        external[resolved] = "allow"
        external[resolved + "/**"] = "allow"
    for raw in _policy_paths(session):
        p = Path(os.path.expandvars(str(raw))).expanduser()
        try:
            resolved = str(p.resolve())
        except OSError:
            continue
        external[resolved] = "allow"
        if p.is_dir() or resolved.endswith("/**"):
            external[resolved.rstrip("/*") + "/**"] = "allow"
        elif p.parent.is_dir():
            external[str(p.parent.resolve()) + "/**"] = "allow"
    permission = {
        "edit": "deny",
        "bash": "deny",
        "todowrite": "deny",
        "question": "deny",
        "task": "deny",
        "webfetch": "deny",
        "websearch": "deny",
        "doom_loop": "deny",
        "write": {
            "*": "deny",
            str(gate_dir.resolve()) + "/**": "allow",
            str((gate_dir / "result.json").resolve()): "allow",
        },
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "lsp": "allow",
        "read": {
            "*": "allow",
            f"**/{sensitive}": "deny",
            f"**/{sensitive}.*": "deny",
            "**/credentials.json": "deny",
            "**/id_rsa": "deny",
            "**/*.pem": "deny",
        },
        "external_directory": external,
    }
    return json.dumps(permission, separators=(",", ":"))


def _extra_files(session: Path) -> list[str]:
    evid = session / "evidence"
    out: list[str] = []
    if not evid.is_dir():
        return out
    for name in ("DIFF_MAP.txt", "DIFF_LABELS.txt"):
        p = evid / name
        if p.is_file():
            out.append(str(p))
    for p in sorted(evid.glob("DIFF-*.patch")):
        out.append(str(p))
    for p in sorted(evid.glob("*.patch")):
        s = str(p)
        if s not in out:
            out.append(s)
    return out[:40]


def run_gate(
    *,
    session: Path,
    workspace: Path,
    model: str,
    export_only: bool,
    timeout_sec: int,
) -> int:
    sys.path.insert(0, str(_skill_root() / "scripts"))
    from lib.runtime.opencode_adapter import (  # noqa: E402
        build_opencode_agent_config,
        build_opencode_run_args,
        resolve_opencode_bin,
        run_opencode,
    )
    from lib.org_ship_gate import validate_session_gate, write_status  # noqa: E402

    session = session.expanduser().resolve()
    workspace = workspace.expanduser().resolve()
    gate_dir = session / GATE_DIRNAME
    gate_dir.mkdir(parents=True, exist_ok=True)
    out_json = gate_dir / "result.json"
    raw_out = gate_dir / "cli.raw.txt"
    brief = _write_gate_md(session, workspace, out_json)

    if export_only:
        print(json.dumps({"ok": True, "export_only": True, "brief": str(brief)}, indent=2))
        return 0

    bin_path = resolve_opencode_bin()
    if not bin_path:
        write_status(
            session,
            {
                "ok": False,
                "required": True,
                "code": "ORG_SHIP_GATE_REQUIRED",
                "message": "opencode binary not found - install OpenCode Go and auth",
                "model": model,
            },
        )
        print("org-ship-gate: FAIL (opencode not found)", file=sys.stderr)
        return 2

    # Prefer a DIFF patch as the primary --file anchor; fall back to GATE.md.
    extras = _extra_files(session)
    packet_like = extras[0] if extras else str(brief)
    extra_rest = extras[1:] if extras else []
    if str(brief) not in extra_rest and packet_like != str(brief):
        extra_rest = [str(brief)] + extra_rest

    prompt_path = gate_dir / "PROMPT.txt"
    prompt_path.write_text(
        f"Read and obey {brief}. You are the hostile org ship-gate reviewer "
        f"on OpenCode Go ({model}). Review the attached diffs and the live workspace. "
        f"Emit findings JSON in your final message (and write {out_json} if write is allowed). "
        f"Do not rubber-stamp Yonko council Pass.",
        encoding="utf-8",
    )

    perm = _permission_json(workspace=workspace, session=session, gate_dir=gate_dir)
    env = os.environ.copy()
    env["OPENCODE_PERMISSION"] = perm
    env["OPENCODE_AGENT"] = build_opencode_agent_config(perm)
    env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "1"

    args = build_opencode_run_args(
        model=model,
        title="yonko-org-ship-gate",
        prompt=prompt_path.read_text(encoding="utf-8"),
        packet_path=packet_like,
        workdir=str(workspace),
        extra_files=extra_rest,
        prompt_file=prompt_path,
        agent="yonko-reviewer",
    )

    try:
        proc = run_opencode(args, env=env, timeout_sec=timeout_sec)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        rc = proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = f"timeout after {timeout_sec}s"
        rc = 124
        raw_out.write_text(stdout + "\n" + stderr, encoding="utf-8")
        write_status(
            session,
            {
                "ok": False,
                "required": True,
                "code": "ORG_SHIP_GATE_FAILED",
                "message": stderr,
                "model": model,
            },
        )
        print(f"org-ship-gate: FAIL ({stderr})", file=sys.stderr)
        return 1

    raw_out.write_text(
        json.dumps({"returncode": rc, "stdout": stdout, "stderr": stderr}, indent=2) + "\n",
        encoding="utf-8",
    )

    obj = None
    if out_json.is_file() and out_json.stat().st_size > 0:
        try:
            candidate = json.loads(out_json.read_text(encoding="utf-8"))
            if isinstance(candidate, dict) and "findings" in candidate:
                obj = candidate
        except json.JSONDecodeError:
            pass
    if obj is None:
        obj = _extract_findings(stdout)
    if obj is None:
        write_status(
            session,
            {
                "ok": False,
                "required": True,
                "code": "ORG_SHIP_GATE_FAILED",
                "message": f"OpenCode Go exited {rc} but no findings JSON was produced",
                "model": model,
            },
        )
        print("org-ship-gate: FAIL (no findings JSON)", file=sys.stderr)
        return 1

    obj.setdefault("model", model)
    obj.setdefault("posture", "hostile")
    out_json.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    result = validate_session_gate(session)
    write_status(session, result)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(os.environ.get("YONKO_PROJECT_ROOT") or Path.cwd()),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--timeout-sec", type=int, default=900)
    args = parser.parse_args(argv)
    return run_gate(
        session=args.session,
        workspace=args.workspace,
        model=args.model,
        export_only=args.export_only,
        timeout_sec=args.timeout_sec,
    )


if __name__ == "__main__":
    raise SystemExit(main())
