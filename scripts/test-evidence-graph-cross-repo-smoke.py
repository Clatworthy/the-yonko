#!/usr/bin/env python3
"""Cross-repo Evidence Index + session co-collected resolution smoke for Evidence Graph."""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import uuid
from importlib.util import module_from_spec, spec_from_file_location

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIX = SCRIPTS / "fixtures" / "evidence-graph"
EI = SCRIPTS / "evidence-index.py"


def load(name: str, path: pathlib.Path):
    spec = spec_from_file_location(name, path)
    mod = module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def run_ei(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(EI), *args],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )


def write_published_record(
    repo: pathlib.Path,
    *,
    evidence_id: str,
    repositories: list[str],
    apis: list[str],
    events: list[str] | None = None,
    contracts: list[str] | None = None,
) -> None:
    year = "2026"
    rec_dir = repo / "records" / year / evidence_id
    rec_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": "1.0.0",
        "evidence_id": evidence_id,
        "activity_id": "TA-EG-CROSS",
        "session_id": evidence_id,
        "review_type": "implementation",
        "lifecycle": "canonical",
        "final_status": "pass",
        "completed_at": "2026-08-01T00:00:00Z",
        "owner": "smoke",
        "tickets": ["TA-EG-CROSS"],
        "title": f"indexed {evidence_id}",
        "summary": f"APIs {apis}",
        "repositories": repositories,
        "services": [r.split("/")[-1] for r in repositories],
        "apis": apis,
        "events": events or [],
        "database_tables": [],
        "technologies": [],
        "architectural_patterns": [],
        "external_integrations": [],
        "contracts": contracts or [],
        "concepts": [],
        "risk": "medium",
        "deployment_type": "multi-service",
        "commit_refs": ["deadbeef"],
        "merge_request_refs": [],
        "reviewers": [],
        "findings": {"accepted": [], "validated": [], "rejected": [], "unresolved": []},
        "decisions": [],
        "rollout_strategy": "deploy consumer after producer",
        "rollback_strategy": "revert consumer first",
        "assumptions": [],
        "unresolved_risks": [],
        "engineering_confidence": {"level": "high"},
        "relationships": [],
        "informed_by": [],
    }
    (rec_dir / "record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    (rec_dir / "manifest.json").write_text(json.dumps({"evidence_id": evidence_id}) + "\n", encoding="utf-8")
    (rec_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")


def rebuild_map(repo: pathlib.Path) -> None:
    mapping = {}
    for year in (repo / "records").glob("*"):
        for rec in year.iterdir():
            if (rec / "record.json").exists():
                mapping[rec.name] = str(rec)
    (repo / "records-map.json").write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    idx = repo / "indexes" / "v1"
    idx.mkdir(parents=True, exist_ok=True)
    (idx / "manifest.json").write_text(json.dumps({"ok": True}) + "\n", encoding="utf-8")


def test_exact_consumer_resolved() -> None:
    cr = load("cr", SCRIPTS / "lib/evidence_graph/cross_repo.py")
    with tempfile.TemporaryDirectory(prefix="yonko-xref-") as td:
        repo = pathlib.Path(td) / "index"
        old = os.environ.get("YONKO_EVIDENCE_REPO")
        os.environ["YONKO_EVIDENCE_REPO"] = str(repo)
        env = os.environ.copy()
        try:
            run_ei(["init-repo", "--path", str(repo)], env)
            write_published_record(
                repo,
                evidence_id="producer-st",
                repositories=["fixture/mini-spring"],
                apis=["/v1/orders/{id}/confirm"],
            )
            write_published_record(
                repo,
                evidence_id="consumer-frontend",
                repositories=["frontend/web-app"],
                apis=["/v1/orders/{id}/confirm"],
                contracts=["order-service-model"],
            )
            rebuild_map(repo)

            signals = {
                "apis": ["/v1/orders/{id}/confirm"],
                "events": [],
                "contracts": [],
                "permissions": [],
            }
            out = cr.resolve_cross_repo_consumers(
                producer_repository="fixture/mini-spring",
                signals=signals,
                risk_band="high",
                prefer_cache=False,
            )
            assert out["status"] == "resolved", out
            assert any(c["repository"] == "frontend/web-app" for c in out["consumers"]), out
            assert out["consumers"][0]["deployment_order_known"] is True
        finally:
            if old is None:
                os.environ.pop("YONKO_EVIDENCE_REPO", None)
            else:
                os.environ["YONKO_EVIDENCE_REPO"] = old
        print("PASS test_exact_consumer_resolved")


def test_no_match_unresolved() -> None:
    cr = load("cr2", SCRIPTS / "lib/evidence_graph/cross_repo.py")
    with tempfile.TemporaryDirectory(prefix="yonko-xref2-") as td:
        repo = pathlib.Path(td) / "index"
        old = os.environ.get("YONKO_EVIDENCE_REPO")
        os.environ["YONKO_EVIDENCE_REPO"] = str(repo)
        env = os.environ.copy()
        try:
            run_ei(["init-repo", "--path", str(repo)], env)
            write_published_record(
                repo,
                evidence_id="unrelated",
                repositories=["frontend/web-app"],
                apis=["/v1/other"],
            )
            rebuild_map(repo)
            out = cr.resolve_cross_repo_consumers(
                producer_repository="fixture/mini-spring",
                signals={"apis": ["/v1/orders/{id}/confirm"], "events": [], "contracts": [], "permissions": []},
                risk_band="high",
                prefer_cache=False,
            )
            assert out["status"] == "unresolved", out
            assert out["unresolved"]
        finally:
            if old is None:
                os.environ.pop("YONKO_EVIDENCE_REPO", None)
            else:
                os.environ["YONKO_EVIDENCE_REPO"] = old
        print("PASS test_no_match_unresolved")


def test_session_co_collected_resolves_without_index() -> None:
    cr = load("cr_session", SCRIPTS / "lib/evidence_graph/cross_repo.py")
    with tempfile.TemporaryDirectory(prefix="yonko-xref-session-") as td:
        base = pathlib.Path(td)
        evid = base / "evidence"
        evid.mkdir()
        consumer_patch = evid / "DIFF-services-data-ingestion-service.patch"
        consumer_patch.write_text(
            "diff --git a/src/UseCreditClient.java b/src/UseCreditClient.java\n"
            "+++ b/src/UseCreditClient.java\n"
            "+import com.example.UseCreditResponseDTO;\n"
            "+// calls /v1/credits/use\n",
            encoding="utf-8",
        )
        (evid / "DIFF-services-subscription-service.patch").write_text(
            "+public class UseCreditResponseDTO {}\n"
            '+@PostMapping("/v1/credits/use")\n',
            encoding="utf-8",
        )
        old = os.environ.pop("YONKO_EVIDENCE_REPO", None)
        try:
            signals = {
                "apis": ["/v1/credits/use"],
                "events": ["UseCreditResponseDTO"],
                "contracts": [],
                "permissions": [],
            }
            session_repos = [
                {
                    "label": "services/subscription-service",
                    "path": str(base / "subscription-service"),
                    "patch": "DIFF-services-subscription-service.patch",
                },
                {
                    "label": "services/data-ingestion-service",
                    "path": str(base / "data-ingestion-service"),
                    "patch": "DIFF-services-data-ingestion-service.patch",
                },
            ]
            out = cr.resolve_cross_repo_consumers(
                producer_repository="services/subscription-service",
                signals=signals,
                risk_band="critical",
                prefer_cache=False,
                session_repos=session_repos,
                evid_dir=evid,
            )
            assert out["status"] == "resolved", out
            assert any(
                c["repository"] == "services/data-ingestion-service" for c in out["consumers"]
            ), out
            assert "session_co_collected_signal_match" in (out.get("discovery_method") or "")
            assert not out.get("unresolved"), out
        finally:
            if old is not None:
                os.environ["YONKO_EVIDENCE_REPO"] = old
        print("PASS test_session_co_collected_resolves_without_index")


def test_session_presence_alone_does_not_resolve() -> None:
    cr = load("cr_session2", SCRIPTS / "lib/evidence_graph/cross_repo.py")
    with tempfile.TemporaryDirectory(prefix="yonko-xref-presence-") as td:
        base = pathlib.Path(td)
        evid = base / "evidence"
        evid.mkdir()
        (evid / "DIFF-producer.patch").write_text(
            '+@PostMapping("/v1/credits/use")\n+class UseCreditResponseDTO {}\n',
            encoding="utf-8",
        )
        (evid / "DIFF-unrelated.patch").write_text(
            "+# unrelated alarm stack\n+Description: data-ingestion-service-alarms\n",
            encoding="utf-8",
        )
        old = os.environ.pop("YONKO_EVIDENCE_REPO", None)
        try:
            out = cr.resolve_cross_repo_consumers(
                producer_repository="services/subscription-service",
                signals={
                    "apis": ["/v1/credits/use"],
                    "events": ["UseCreditResponseDTO"],
                    "contracts": [],
                    "permissions": [],
                },
                risk_band="high",
                prefer_cache=False,
                session_repos=[
                    {
                        "label": "services/subscription-service",
                        "patch": "DIFF-producer.patch",
                    },
                    {
                        "label": "services/data-ingestion-service",
                        "patch": "DIFF-unrelated.patch",
                    },
                ],
                evid_dir=evid,
            )
            assert out["status"] == "unresolved", out
            assert "session_repos_considered" in out
            assert "services/data-ingestion-service" in out["session_repos_considered"]
        finally:
            if old is not None:
                os.environ["YONKO_EVIDENCE_REPO"] = old
        print("PASS test_session_presence_alone_does_not_resolve")


def test_session_tree_scan_resolves_clean_consumer() -> None:
    cr = load("cr_session3", SCRIPTS / "lib/evidence_graph/cross_repo.py")
    with tempfile.TemporaryDirectory(prefix="yonko-xref-tree-") as td:
        base = pathlib.Path(td)
        evid = base / "evidence"
        evid.mkdir()
        consumer = base / "services" / "data-ingestion-service" / "src"
        consumer.mkdir(parents=True)
        (consumer / "DebitClient.java").write_text(
            "package x;\nimport UseCreditResponseDTO;\n// /v1/credits/use\n",
            encoding="utf-8",
        )
        (evid / "DIFF-producer.patch").write_text(
            '+@PostMapping("/v1/credits/use")\n',
            encoding="utf-8",
        )
        (evid / "DIFF-consumer.patch").write_text("", encoding="utf-8")
        old = os.environ.pop("YONKO_EVIDENCE_REPO", None)
        try:
            out = cr.resolve_cross_repo_consumers(
                producer_repository="services/subscription-service",
                signals={
                    "apis": ["/v1/credits/use"],
                    "events": ["UseCreditResponseDTO"],
                    "contracts": [],
                    "permissions": [],
                },
                risk_band="high",
                prefer_cache=False,
                session_repos=[
                    {"label": "services/subscription-service", "patch": "DIFF-producer.patch"},
                    {
                        "label": "services/data-ingestion-service",
                        "path": str(base / "services" / "data-ingestion-service"),
                        "patch": "DIFF-consumer.patch",
                    },
                ],
                evid_dir=evid,
            )
            assert out["status"] == "resolved", out
            assert any(
                c["repository"] == "services/data-ingestion-service" for c in out["consumers"]
            ), out
        finally:
            if old is not None:
                os.environ["YONKO_EVIDENCE_REPO"] = old
        print("PASS test_session_tree_scan_resolves_clean_consumer")


def test_graph_uses_index() -> None:
    build = load("egbuild", SCRIPTS / "lib/evidence_graph/build.py")
    with tempfile.TemporaryDirectory(prefix="yonko-xref3-") as td:
        base = pathlib.Path(td)
        idx = base / "index"
        env = os.environ.copy()
        env["YONKO_EVIDENCE_REPO"] = str(idx)
        run_ei(["init-repo", "--path", str(idx)], env)
        write_published_record(
            idx,
            evidence_id="consumer-fe",
            repositories=["frontend/web-app"],
            apis=["/v1/orders/{id}/confirm"],
        )
        rebuild_map(idx)

        sid = f"eg-xref-{uuid.uuid4().hex[:8]}"
        subprocess.run(
            [str(SCRIPTS / "init-session.sh"), "--id", sid, "--type", "implementation"],
            check=True,
            env=env,
        )
        session = pathlib.Path.home() / ".cursor" / "yonko-sessions" / sid
        evid = session / "evidence"
        evid.mkdir(exist_ok=True)
        shutil.copy(FIX / "sample-order-confirm.patch", evid / "DIFF-x.patch")
        (evid / "repos.json").write_text(
            json.dumps(
                {
                    "repos": [
                        {
                            "label": "fixture/mini-spring",
                            "path": str((FIX / "mini-spring").resolve()),
                            "branch": "f",
                            "patch": "DIFF-x.patch",
                            "secrets_excluded": [],
                            "dirty": True,
                        }
                    ]
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (evid / "DIFF_MAP.txt").write_text("repo: fixture/mini-spring\n", encoding="utf-8")
        (evid / "risk.json").write_text(
            json.dumps({"risk": "high", "reasons": ["api"], "reviewers": 3}) + "\n",
            encoding="utf-8",
        )
        old = os.environ.get("YONKO_EVIDENCE_REPO")
        os.environ["YONKO_EVIDENCE_REPO"] = str(idx)
        try:
            g = build.build_evidence_graph(session)
        finally:
            if old is None:
                os.environ.pop("YONKO_EVIDENCE_REPO", None)
            else:
                os.environ["YONKO_EVIDENCE_REPO"] = old

        cat = g["categories"]["cross_repository_consumers"]
        assert cat["status"] == "covered", cat
        assert any(e.get("type") == "consumes" for e in g["edges"]), g["edges"]
        assert not any(
            u.get("relationship") == "external_consumers" for u in g["unresolved_edges"]
        ), g["unresolved_edges"]
        print("PASS test_graph_uses_index")


def test_graph_uses_session_co_collected() -> None:
    build = load("egbuild2", SCRIPTS / "lib/evidence_graph/build.py")
    with tempfile.TemporaryDirectory(prefix="yonko-xref-graph-session-") as td:
        base = pathlib.Path(td)
        producer = base / "subscription-service"
        consumer = base / "data-ingestion-service" / "src"
        producer.mkdir()
        consumer.mkdir(parents=True)
        (producer / "Api.java").write_text(
            '@PostMapping("/v1/credits/use")\nclass UseCreditResponseDTO {}\n',
            encoding="utf-8",
        )
        (consumer / "Client.java").write_text(
            "import UseCreditResponseDTO;\n// /v1/credits/use\n",
            encoding="utf-8",
        )

        sid = f"eg-xref-sess-{uuid.uuid4().hex[:8]}"
        env = os.environ.copy()
        env.pop("YONKO_EVIDENCE_REPO", None)
        subprocess.run(
            [str(SCRIPTS / "init-session.sh"), "--id", sid, "--type", "implementation"],
            check=True,
            env=env,
        )
        session = pathlib.Path.home() / ".cursor" / "yonko-sessions" / sid
        evid = session / "evidence"
        evid.mkdir(exist_ok=True)
        (evid / "DIFF-services-subscription-service.patch").write_text(
            "diff --git a/Api.java b/Api.java\n"
            "+++ b/Api.java\n"
            '+@PostMapping("/v1/credits/use")\n'
            "+class UseCreditResponseDTO {}\n",
            encoding="utf-8",
        )
        (evid / "DIFF-services-data-ingestion-service.patch").write_text(
            "diff --git a/src/Client.java b/src/Client.java\n"
            "+++ b/src/Client.java\n"
            "+import UseCreditResponseDTO;\n"
            "+// /v1/credits/use\n",
            encoding="utf-8",
        )
        (evid / "repos.json").write_text(
            json.dumps(
                {
                    "repos": [
                        {
                            "label": "services/subscription-service",
                            "path": str(producer),
                            "branch": "f",
                            "patch": "DIFF-services-subscription-service.patch",
                            "secrets_excluded": [],
                            "dirty": True,
                        },
                        {
                            "label": "services/data-ingestion-service",
                            "path": str(base / "data-ingestion-service"),
                            "branch": "f",
                            "patch": "DIFF-services-data-ingestion-service.patch",
                            "secrets_excluded": [],
                            "dirty": True,
                        },
                    ]
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (evid / "DIFF_MAP.txt").write_text(
            "repo: services/subscription-service\nrepo: services/data-ingestion-service\n",
            encoding="utf-8",
        )
        (evid / "risk.json").write_text(
            json.dumps({"risk": "critical", "reasons": ["api"], "reviewers": 4}) + "\n",
            encoding="utf-8",
        )
        old = os.environ.pop("YONKO_EVIDENCE_REPO", None)
        try:
            g = build.build_evidence_graph(session)
        finally:
            if old is not None:
                os.environ["YONKO_EVIDENCE_REPO"] = old

        cat = g["categories"]["cross_repository_consumers"]
        assert cat["status"] == "covered", cat
        assert any(
            e.get("type") == "consumes"
            and "session_co_collected" in (e.get("discovery_method") or "")
            for e in g["edges"]
        ), g["edges"]
        assert not any(
            u.get("category") == "cross_repository_consumers" for u in g["unresolved_edges"]
        ), g["unresolved_edges"]
        print("PASS test_graph_uses_session_co_collected")


def test_signals_extract() -> None:
    cr = load("cr3", SCRIPTS / "lib/evidence_graph/cross_repo.py")
    patch = (FIX / "sample-order-confirm.patch").read_text(encoding="utf-8")
    sig = cr.extract_producer_signals(patch)
    assert "/v1/orders/{id}/confirm" in sig["apis"], sig
    assert "ORDER_CONFIRM" in sig["permissions"], sig
    print("PASS test_signals_extract")


def test_ops_infra_alarms_not_cross_repo_producer() -> None:
    cr = load("cr_ops", SCRIPTS / "lib/evidence_graph/cross_repo.py")
    build = load("egbuild_ops", SCRIPTS / "lib/evidence_graph/build.py")
    assert cr.is_ops_infra_path("cloudFormation/data-ingestion-service-alarms.yaml")
    assert cr.is_ops_infra_path("infra/terraform/main.tf")
    assert not cr.is_ops_infra_path("openapi/order-service-model.yaml")

    symbols = build.extract_changed_symbols(
        "diff --git a/cloudFormation/data-ingestion-service-alarms.yaml "
        "b/cloudFormation/data-ingestion-service-alarms.yaml\n"
        "--- a/cloudFormation/data-ingestion-service-alarms.yaml\n"
        "+++ b/cloudFormation/data-ingestion-service-alarms.yaml\n"
        "@@ -1,0 +1,2 @@\n"
        "+Condition: ConditionOnlyProd\n"
        "+Type: AWS::Logs::MetricFilter\n",
        "services/data-ingestion-service",
    )
    assert symbols and symbols[0]["change_kind"] == "operational_infra_change", symbols
    assert symbols[0]["kind"] == "infra"

    sig = cr.extract_producer_signals(
        "+ILoggingEvent e = null;\n+getFormattedMessage();\n",
        changed_symbols=symbols,
    )
    assert "data-ingestion-service-alarms" not in (sig.get("contracts") or []), sig
    assert "ILoggingEvent" not in (sig.get("events") or []), sig
    assert "getFormattedMessage" not in (sig.get("events") or []), sig

    out = cr.resolve_cross_repo_consumers(
        producer_repository="services/data-ingestion-service",
        signals={
            "apis": [],
            "events": ["ILoggingEvent"],
            "contracts": ["data-ingestion-service-alarms"],
            "permissions": [],
        },
        risk_band="critical",
        prefer_cache=False,
        session_repos=[
            {"label": "services/data-ingestion-service"},
            {"label": "services/subscription-service"},
        ],
    )
    assert out["status"] == "not_applicable", out
    assert out["unresolved"] == [], out
    print("PASS test_ops_infra_alarms_not_cross_repo_producer")


def main() -> int:
    test_signals_extract()
    test_ops_infra_alarms_not_cross_repo_producer()
    test_exact_consumer_resolved()
    test_no_match_unresolved()
    test_session_co_collected_resolves_without_index()
    test_session_presence_alone_does_not_resolve()
    test_session_tree_scan_resolves_clean_consumer()
    test_graph_uses_index()
    test_graph_uses_session_co_collected()
    print("ALL CROSS-REPO SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
