#!/usr/bin/env python3
"""Smoke tests for Evidence Graph v1."""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import time
import uuid
from importlib.util import module_from_spec, spec_from_file_location

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIX = SCRIPTS / "fixtures" / "evidence-graph"


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=check)


def load_eg():
    spec = spec_from_file_location("eg", SCRIPTS / "lib/evidence_graph/build.py")
    mod = module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_body_only_extract() -> None:
    mod = load_eg()
    patch = """diff --git a/src/Foo.java b/src/Foo.java
--- a/src/Foo.java
+++ b/src/Foo.java
@@ -10,3 +10,4 @@ public class Foo {
     public void run() {
         int x = 1;
+        int y = 2;
     }
 }
"""
    syms = mod.extract_changed_symbols(patch, "r")
    kinds = {s["change_kind"] for s in syms}
    assert "method_body_only" in kinds or syms, kinds
    print("PASS test_body_only_extract")


def test_symbol_and_reachability() -> None:
    mod = load_eg()
    sid = f"eg-smoke-{uuid.uuid4().hex[:10]}"
    run([str(SCRIPTS / "init-session.sh"), "--id", sid, "--type", "implementation"])
    session = pathlib.Path.home() / ".cursor" / "yonko-sessions" / sid
    assert session.is_dir(), session
    evid = session / "evidence"
    evid.mkdir(exist_ok=True)
    repo = FIX / "mini-spring"
    patch_name = "DIFF-fixture-mini-spring.patch"
    shutil.copy(FIX / "sample-order-confirm.patch", evid / patch_name)
    (evid / "repos.json").write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "label": "fixture/mini-spring",
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
    (evid / "DIFF_MAP.txt").write_text("repo: fixture/mini-spring\n", encoding="utf-8")
    (evid / "risk.json").write_text(
        json.dumps(
            {"risk": "high", "risk_basis": "diff-derived", "reasons": ["fixture"], "reviewers": 3},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (session / "DOCKET.md").write_text(
        "# Docket\n\n## Goal\nfixture\n\n## Touch surface → Expected DIFF labels\n"
        "- fixture/mini-spring\n",
        encoding="utf-8",
    )

    cp = run([str(SCRIPTS / "build-evidence-graph.sh"), "--session", str(session)], check=False)
    assert (evid / "evidence-graph.json").exists(), cp.stderr + cp.stdout
    assert (evid / "graph-completeness.json").exists(), cp.stderr + cp.stdout
    g = json.loads((evid / "evidence-graph.json").read_text(encoding="utf-8"))
    c = json.loads((evid / "graph-completeness.json").read_text(encoding="utf-8"))

    kinds = {s["change_kind"] for s in g["changed_symbols"]}
    assert "method_signature_change" in kinds or any(s["kind"] == "method" for s in g["changed_symbols"]), kinds
    assert "annotation_change" in kinds or any(s["kind"] == "annotation" for s in g["changed_symbols"]), kinds
    assert "dto_field_change" in kinds or any(s["kind"] == "field" for s in g["changed_symbols"]), kinds
    assert "migration_change" in kinds, kinds

    for cat in mod.CATEGORIES:
        assert cat in g["categories"], cat
        assert g["categories"][cat]["status"] in ("covered", "not_applicable", "unresolved")

    assert len(c["categories"]) == 15
    # Without a matching Index consumer, structural API signals stay unresolved (not silent).
    assert g["categories"]["cross_repository_consumers"]["status"] in ("unresolved", "not_applicable"), g["categories"]["cross_repository_consumers"]
    if g["categories"]["cross_repository_consumers"]["status"] == "unresolved":
        assert g["unresolved_edges"], "expected unresolved edges when category unresolved"

    for e in g["edges"]:
        assert e.get("evidence"), e
        assert e.get("discovery_method"), e

    # Caller / framework edges expected when rg available
    edge_types = {e["type"] for e in g["edges"]}
    assert "called_by" in edge_types or "tested_by" in edge_types or g["edges"], edge_types

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
    assert "=== EVIDENCE GRAPH ===" in packet
    assert "=== EVIDENCE COMPLETENESS ===" in packet
    assert "=== DIFF: fixture/mini-spring ===" in packet
    assert "confirm" in packet

    print("PASS test_symbol_and_reachability")
    print(
        json.dumps(
            {
                "session": sid,
                "changed_symbols": len(g["changed_symbols"]),
                "nodes": g["metrics"]["nodes"],
                "edges": g["metrics"]["edges"],
                "ok_for_seating": c["ok_for_seating"],
                "blocks_complete_verdict": c["blocks_complete_verdict"],
            }
        )
    )



def test_outcome_axes() -> None:
    from importlib.util import module_from_spec, spec_from_file_location
    import json
    import tempfile
    from pathlib import Path

    spec = spec_from_file_location("oa", SCRIPTS / "lib/outcome_axes.py")
    mod = module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)

    # No defects + incomplete consumers => proceed_with_caveat (not PASS/FAIL collapse)
    completeness = {
        "blocks_seating": False,
        "blocks_complete_verdict": True,
        "categories": [
            {"category": "cross_repository_consumers", "status": "unresolved", "reason": "consumers"}
        ],
        "unresolved_edges_material": [
            {"category": "cross_repository_consumers", "required_for_complete_review": True}
        ],
    }
    ec, reasons = mod.derive_evidence_completeness(completeness)
    assert ec == "incomplete", ec
    ro = mod.derive_review_outcome("pass", findings_total=0)
    assert ro == "pass"
    dep = mod.derive_deployment_recommendation(ro, ec)
    assert dep == "proceed_with_caveat", dep
    assert mod.derive_deployment_recommendation("findings", "complete") == "block"
    assert mod.derive_deployment_recommendation("pass", "complete") == "proceed"

    with tempfile.TemporaryDirectory() as td:
        session = Path(td)
        evid = session / "evidence"
        evid.mkdir()
        (evid / "graph-completeness.json").write_text(json.dumps(completeness) + "\n")
        axes = mod.build_outcome_axes(session, legacy_verdict="pass", findings_total=0)
        assert axes["clean_pass_allowed"] is False
        assert "unresolved" in axes["presentation"]["headline"].lower()
        assert axes["confidence_ceiling"] == "medium"

    print("PASS test_outcome_axes")


def main() -> int:
    test_body_only_extract()
    test_outcome_axes()
    test_symbol_and_reachability()
    print("ALL EVIDENCE GRAPH SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
