#!/usr/bin/env python3
"""Regression: return-field semantic change must stage readers into the packet,
and incomplete evidence cannot render as a clean Pass.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import uuid
from importlib.util import module_from_spec, spec_from_file_location

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIX = SCRIPTS / "fixtures" / "evidence-graph" / "mini-spring-readers"


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=check)


def load_mod(rel: str, name: str):
    spec = spec_from_file_location(name, SCRIPTS / rel)
    mod = module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_return_population_extract() -> None:
    eg = load_mod("lib/evidence_graph/build.py", "eg_return_reader")
    patch = (FIX / "patches" / "return-population-only.patch").read_text(encoding="utf-8")
    syms = eg.extract_changed_symbols(patch, "fixture/mini-spring-readers")
    kinds = {s["change_kind"] for s in syms}
    assert "public_return_population_change" in kinds or "dto_field_population_change" in kinds, kinds
    names = set()
    for s in syms:
        for n in s.get("affected_names") or []:
            names.add(n)
        if s.get("name"):
            names.add(s["name"])
    assert "useCredits" in names or "requested" in names or "CreditResponse" in names, names
    print("PASS test_return_population_extract")


def test_reader_staged_into_packet() -> None:
    sid = f"return-reader-{uuid.uuid4().hex[:10]}"
    run([str(SCRIPTS / "init-session.sh"), "--id", sid, "--type", "implementation"])
    session = pathlib.Path.home() / ".cursor" / "yonko-sessions" / sid
    evid = session / "evidence"
    evid.mkdir(exist_ok=True)
    repo = FIX
    patch_name = "DIFF-fixture-mini-spring-readers.patch"
    shutil.copy(FIX / "patches" / "return-population-only.patch", evid / patch_name)
    (evid / "repos.json").write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "label": "fixture/mini-spring-readers",
                        "path": str(repo.resolve()),
                        "branch": "fixture",
                        "patch": patch_name,
                        "secrets_excluded": [],
                        "dirty": True,
                    }
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (evid / "DIFF_MAP.txt").write_text(
        "repo: fixture/mini-spring-readers\n files: M CreditRepository.java\n",
        encoding="utf-8",
    )
    (evid / "risk.json").write_text(
        json.dumps(
            {
                "risk": "high",
                "risk_basis": "diff-derived",
                "reasons": ["fixture return population"],
                "reviewers": 3,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (session / "DOCKET.md").write_text(
        "# Docket\n\n## Goal\nfixture return population\n\n"
        "## Touch surface → Expected DIFF labels\n- fixture/mini-spring-readers\n",
        encoding="utf-8",
    )

    cp = run([str(SCRIPTS / "build-evidence-graph.sh"), "--session", str(session)], check=False)
    assert (evid / "evidence-graph.json").exists(), cp.stderr + cp.stdout
    graph = json.loads((evid / "evidence-graph.json").read_text(encoding="utf-8"))
    kinds = {s["change_kind"] for s in graph["changed_symbols"]}
    assert "public_return_population_change" in kinds or "dto_field_population_change" in kinds, kinds

    reader_paths = {
        n.get("path")
        for n in graph.get("nodes") or []
        if "CreditService" in str(n.get("path") or "") or "CreditService" in str(n.get("name") or "")
    }
    assert reader_paths, "expected CreditService as inbound reader node"

    impact = evid / "impact-readers.json"
    assert impact.exists(), "expected evidence/impact-readers.json"
    impact_doc = json.loads(impact.read_text(encoding="utf-8"))
    staged = impact_doc.get("readers") or []
    assert staged, impact_doc
    assert any("CreditService" in str(r.get("path") or "") for r in staged), staged

    run(
        [
            str(SCRIPTS / "sanitise-and-hash-packet.sh"),
            "--session",
            str(session),
            "--docket",
            str(session / "DOCKET.md"),
        ]
    )
    packet = (session / "packet.md").read_text(encoding="utf-8")
    assert "=== IMPACT READERS ===" in packet
    assert "CreditService" in packet
    assert "getRequested" in packet
    assert "publish" in packet
    assert "=== DIFF: fixture/mini-spring-readers ===" in packet
    assert "CreditService.java" not in (evid / patch_name).read_text(encoding="utf-8")
    print("PASS test_reader_staged_into_packet", sid)


def test_missing_reader_names_symbol() -> None:
    sid = f"return-reader-gap-{uuid.uuid4().hex[:10]}"
    run([str(SCRIPTS / "init-session.sh"), "--id", sid, "--type", "implementation"])
    session = pathlib.Path.home() / ".cursor" / "yonko-sessions" / sid
    evid = session / "evidence"
    evid.mkdir(exist_ok=True)
    empty = evid / "empty-tree"
    empty.mkdir()
    (empty / "src").mkdir()
    patch_name = "DIFF-empty.patch"
    shutil.copy(FIX / "patches" / "return-population-only.patch", evid / patch_name)
    (evid / "repos.json").write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "label": "fixture/empty-readers",
                        "path": str(empty.resolve()),
                        "branch": "fixture",
                        "patch": patch_name,
                        "secrets_excluded": [],
                        "dirty": True,
                    }
                ]
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (evid / "DIFF_MAP.txt").write_text("repo: fixture/empty-readers\n", encoding="utf-8")
    (evid / "risk.json").write_text(
        json.dumps({"risk": "medium", "risk_basis": "diff-derived", "reasons": ["gap"], "reviewers": 3})
        + "\n",
        encoding="utf-8",
    )
    cp = run([str(SCRIPTS / "build-evidence-graph.sh"), "--session", str(session)], check=False)
    assert (evid / "evidence-graph.json").exists(), cp.stderr + cp.stdout
    graph = json.loads((evid / "evidence-graph.json").read_text(encoding="utf-8"))
    unresolved = graph.get("unresolved_edges") or []
    blob = json.dumps(unresolved) + json.dumps(graph.get("categories") or {})
    assert "useCredits" in blob or "requested" in blob or "CreditResponse" in blob, blob
    print("PASS test_missing_reader_names_symbol", sid)


def test_clean_pass_forbidden_when_incomplete() -> None:
    oa = load_mod("lib/outcome_axes.py", "oa_return_reader")

    completeness = {
        "blocks_seating": False,
        "blocks_complete_verdict": True,
        "categories": [
            {
                "category": "cross_repository_consumers",
                "status": "unresolved",
                "reason": "consumers",
            },
            {
                "category": "operational_side_effects",
                "status": "unresolved",
                "reason": "side effects",
            },
        ],
        "unresolved_edges_material": [
            {"category": "cross_repository_consumers", "required_for_complete_review": True}
        ],
    }

    sid = f"clean-pass-{uuid.uuid4().hex[:10]}"
    session = pathlib.Path.home() / ".cursor" / "yonko-sessions" / sid
    session.mkdir(parents=True, exist_ok=True)
    evid = session / "evidence"
    evid.mkdir(exist_ok=True)
    (session / "session.json").write_text(
        json.dumps({"session_id": sid, "review_type": "implementation"}) + "\n",
        encoding="utf-8",
    )
    (evid / "graph-completeness.json").write_text(json.dumps(completeness, indent=2) + "\n", encoding="utf-8")

    axes = oa.build_outcome_axes(session, legacy_verdict="pass", findings_total=0)
    assert axes["review_outcome"] == "pass"
    assert axes["evidence_completeness"] == "incomplete"
    assert axes["deployment_recommendation"] == "proceed_with_caveat"
    assert axes.get("clean_pass_allowed") is False
    presentation = axes.get("presentation") or {}
    headline = presentation.get("headline") or ""
    assert "unresolved" in headline.lower() or "incomplete" in headline.lower(), headline
    assert headline.strip().lower() != "pass"
    block = oa.render_final_verdict_block(axes)
    lowered = block.lower()
    headline_line = next(
        (ln for ln in block.splitlines() if ln.lower().startswith("headline:")),
        "",
    )
    hl = headline_line.lower()
    assert hl.strip() != "headline: pass"
    for forbidden in ("push-ready", "ready to push", "safe to merge", "clean pass"):
        assert forbidden not in hl, headline_line
    assert "pass with unresolved evidence" in lowered or "incomplete" in lowered
    print("PASS test_clean_pass_forbidden_when_incomplete")


def test_clean_pass_allowed_when_complete() -> None:
    oa = load_mod("lib/outcome_axes.py", "oa_return_reader3")
    sid = f"clean-pass-ok-{uuid.uuid4().hex[:10]}"
    session = pathlib.Path.home() / ".cursor" / "yonko-sessions" / sid
    session.mkdir(parents=True, exist_ok=True)
    evid = session / "evidence"
    evid.mkdir(exist_ok=True)
    (session / "session.json").write_text(
        json.dumps({"session_id": sid, "review_type": "implementation"}) + "\n",
        encoding="utf-8",
    )
    (evid / "graph-completeness.json").write_text(
        json.dumps(
            {
                "blocks_seating": False,
                "blocks_complete_verdict": False,
                "categories": [
                    {"category": "changed_symbols", "status": "covered"},
                    {"category": "cross_repository_consumers", "status": "not_applicable"},
                    {"category": "operational_side_effects", "status": "not_applicable"},
                ],
                "unresolved_edges_material": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    axes = oa.build_outcome_axes(session, legacy_verdict="pass", findings_total=0)
    assert axes["clean_pass_allowed"] is True
    assert axes["deployment_recommendation"] == "proceed"
    print("PASS test_clean_pass_allowed_when_complete")


def main() -> int:
    test_return_population_extract()
    test_clean_pass_forbidden_when_incomplete()
    test_clean_pass_allowed_when_complete()
    test_reader_staged_into_packet()
    test_missing_reader_names_symbol()
    print("ALL RETURN-READER PACKET SMOKES PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
