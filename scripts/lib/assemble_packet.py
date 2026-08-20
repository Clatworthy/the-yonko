"""Assemble + hash Yonko evidence packets (V4 Phase 1 structural discipline)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import re
import sys
from typing import Any

SECRET_NAME = re.compile(r"(?i)((?:^|/)\.env(?:\.|$)|credentials\.json|id_rsa|\.pem$)")
SECRET_LINE = re.compile(
    r"(?i)^\+?\s*(export\s+)?[A-Z0-9_]*(PASSWORD|SECRET|TOKEN|API_KEY|ACCESS_KEY)[A-Z0-9_]*\s*="
)


def _load_packet_ops():
    here = pathlib.Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("yonko_packet_ops", here / "packet_ops.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def scrub(text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    out: list[str] = []
    for line in text.splitlines(True):
        if SECRET_LINE.search(line):
            out.append("# redacted secret-looking assignment\n")
            notes.append("redacted_secret_line")
            continue
        out.append(line)
    return "".join(out), notes


def read_scrubbed(path: pathlib.Path) -> tuple[str, list[str]]:
    if not path.exists():
        return "", []
    return scrub(path.read_text(encoding="utf-8", errors="replace"))


def compact_evidence_graph(path: pathlib.Path) -> str:
    """Seat-prompt view of the graph: metrics + changed symbols only.

    Full evidence-graph.json stays on disk for scripts/workflow. Embedding the
    raw node/edge dump (often hundreds of KB) made OpenCode seats multi-minute
    for no review value - seats already get DIFF hunks + completeness + report.
    """
    graph = json.loads(path.read_text(encoding="utf-8"))
    metrics = graph.get("metrics") or {}
    lines = [
        "Compact Evidence Graph (full JSON: evidence/evidence-graph.json - omitted from seat prompt).",
        (
            f"risk_band={graph.get('risk_band')} nodes={metrics.get('nodes')} "
            f"edges={metrics.get('edges')} unresolved_edges={len(graph.get('unresolved_edges') or [])} "
            f"changed_symbols={metrics.get('changed_symbols')}"
        ),
        "changed_symbols:",
    ]
    for symbol in graph.get("changed_symbols") or []:
        lines.append(
            f"- {symbol.get('change_kind')} {symbol.get('repository')}/"
            f"{symbol.get('path')}#{symbol.get('name')} "
            f"(confidence={symbol.get('confidence')})"
        )
    categories = graph.get("categories") or {}
    if isinstance(categories, dict) and categories:
        lines.append("category_keys: " + ", ".join(sorted(categories.keys())))
    elif isinstance(categories, list) and categories:
        keys = []
        for row in categories:
            if isinstance(row, dict) and row.get("category"):
                keys.append(str(row["category"]))
        if keys:
            lines.append("category_keys: " + ", ".join(keys))
    return "\n".join(lines) + "\n"


def _append_evidence_graph_sections(
    parts: list[str],
    evid: pathlib.Path,
    all_notes: list[str],
    meta_extra: dict[str, Any] | None = None,
) -> None:
    graph_path = evid / "evidence-graph.json"
    if graph_path.exists():
        parts.append("=== EVIDENCE GRAPH ===\n")
        parts.append(compact_evidence_graph(graph_path))
        parts.append("\n")
        if meta_extra is not None:
            meta_extra["evidence_graph"] = True
            meta_extra["evidence_graph_compact"] = True
    for name, title in (
        ("graph-completeness.json", "EVIDENCE COMPLETENESS"),
        ("evidence-graph-report.md", "EVIDENCE GRAPH REPORT"),
    ):
        art = evid / name
        if art.exists():
            raw_art, n_art = read_scrubbed(art)
            all_notes.extend(n_art)
            parts.append(f"=== {title} ===\n")
            parts.append(raw_art.rstrip() + "\n\n")
            if meta_extra is not None:
                meta_extra[name.replace(".json", "").replace(".md", "").replace("-", "_")] = True


def assemble(session_dir: pathlib.Path, docket_path: pathlib.Path, review_type: str) -> dict[str, Any]:
    packet_ops = _load_packet_ops()
    evid = session_dir / "evidence"
    docket_raw = docket_path.read_text(encoding="utf-8")
    docket, n1 = scrub(docket_raw)
    all_notes = list(n1)
    parts: list[str] = []
    meta_extra: dict[str, Any] = {}
    linked_plan_handoff = None

    if review_type == "implementation":
        repos = json.loads((evid / "repos.json").read_text(encoding="utf-8"))["repos"]
        diff_map = (
            (evid / "DIFF_MAP.txt").read_text(encoding="utf-8")
            if (evid / "DIFF_MAP.txt").exists()
            else ""
        )

        parts.append("=== YONKO DOCKET ===\n")
        parts.append(docket.rstrip() + "\n\n")

        approved_text, handoff = packet_ops.load_linked_approved_plan(session_dir, scrub)
        if handoff:
            linked_plan_handoff = handoff
            all_notes.extend(handoff.get("scrub_notes") or [])
            parts.append("=== APPROVED PLAN (linked) ===\n")
            parts.append(
                "Handoff boundary: PLAN.approved.md only "
                "(decisions, risks, verification, evidence refs). "
                "Plan session findings, packets, and dialogue are excluded.\n\n"
            )
            parts.append(approved_text.rstrip() + "\n\n")

        parts.append("=== REPOS ===\n")
        for r in repos:
            parts.append(f"{r['label']} @ {r['path']} ({r['branch']})\n")
        parts.append("\n=== DIFF LABELS (must appear in repos_reviewed) ===\n")
        for r in repos:
            parts.append(f"{r['label']}\n")
        parts.append("\n=== DIFF MAP ===\n")
        parts.append(diff_map.rstrip() + "\n\n")

        for r in repos:
            patch_path = evid / r["patch"]
            raw = (
                patch_path.read_text(encoding="utf-8", errors="replace")
                if patch_path.exists()
                else ""
            )
            if SECRET_NAME.search(r.get("path", "")) or any(
                SECRET_NAME.search(x) for x in r.get("secrets_excluded", [])
            ):
                all_notes.append(f"secrets_excluded:{r['label']}")
            scrubbed, n = scrub(raw)
            all_notes.extend(n)
            parts.append(f"=== DIFF: {r['label']} ===\n")
            parts.append(scrubbed)
            if not scrubbed.endswith("\n"):
                parts.append("\n")
            parts.append("\n")
        meta_extra["diff_labels"] = [r["label"] for r in repos]

        # V3.5 routing artefacts (pinned with packet when present)
        for name, title in (
            ("change-classes.json", "CHANGE CLASSES"),
            ("routing.json", "REVIEWER ROUTING"),
        ):
            art = evid / name
            if art.exists():
                raw_art, n_art = read_scrubbed(art)
                all_notes.extend(n_art)
                parts.append(f"=== {title} ===\n")
                parts.append(raw_art.rstrip() + "\n\n")
                meta_extra[name.replace(".json", "").replace("-", "_")] = True

        # Evidence Graph v1 (compact in packet; full JSON stays under evidence/)
        _append_evidence_graph_sections(parts, evid, all_notes, meta_extra)

        impact_path = evid / "impact-readers.json"
        if impact_path.exists():
            impact = json.loads(impact_path.read_text(encoding="utf-8"))
            readers = impact.get("readers") or []
            if readers:
                parts.append("=== IMPACT READERS ===\n")
                parts.append(
                    "In-repo callers/readers of changed return/DTO population "
                    "(staged because the producer diff alone is not enough to review "
                    "side effects keyed on the returned field).\n\n"
                )
                meta_readers = []
                for reader in readers:
                    rel = reader.get("path") or "unknown"
                    parts.append(f"=== READER: {rel} ===\n")
                    staged_rel = reader.get("staged_file")
                    staged = evid / staged_rel if staged_rel else None
                    if staged and staged.is_file():
                        raw_r, n_r = read_scrubbed(staged)
                        all_notes.extend(n_r)
                        parts.append(raw_r.rstrip() + "\n\n")
                    else:
                        parts.append(
                            f"(missing staged snippet; symbols={reader.get('symbols')}; "
                            f"search_terms={reader.get('search_terms')})\n\n"
                        )
                    meta_readers.append(
                        {
                            "path": rel,
                            "repository": reader.get("repository"),
                            "symbols": reader.get("symbols") or [],
                        }
                    )
                meta_extra["impact_readers"] = meta_readers

    elif review_type == "plan":
        refs = json.loads((evid / "plan-refs.json").read_text(encoding="utf-8"))
        repos = refs.get("repositories_named") or []
        scope: dict[str, Any] = {}
        if (evid / "scope-risk.json").exists():
            scope = json.loads((evid / "scope-risk.json").read_text(encoding="utf-8"))

        parts.append("=== YONKO DOCKET ===\n")
        parts.append(docket.rstrip() + "\n\n")
        parts.append("=== REVIEW TYPE ===\n")
        parts.append("plan review - the artifact under review is a proposed implementation plan\n")
        parts.append(
            "risk basis: heuristic from stated scope and inspected context (NOT diff-derived)\n"
        )
        parts.append("omitted scope cannot be classified mechanically - hunt for it\n\n")
        parts.append("=== REPOSITORIES NAMED IN PLAN ===\n")
        if repos:
            for r in repos:
                parts.append(f"{r['label']} @ {r['path']} ({r.get('branch') or 'unknown'})\n")
        else:
            parts.append("(none named - treat missing repository scope as a review target)\n")
        if scope.get("terms_not_present"):
            parts.append("\n=== TERMS NOT PRESENT IN ARTIFACT (weak signal only) ===\n")
            for t in scope["terms_not_present"]:
                parts.append(f"{t}\n")
        parts.append("\n=== IMPLEMENTATION PLAN UNDER REVIEW ===\n")
        plan_text, n = read_scrubbed(evid / "plan.md")
        all_notes.extend(n)
        parts.append(plan_text.rstrip() + "\n\n")
        for s in refs.get("sources") or []:
            text, n = read_scrubbed(evid / "sources" / s["name"])
            all_notes.extend(n)
            parts.append(f"=== SOURCE MATERIAL: {s['name']} ===\n")
            parts.append(text.rstrip() + "\n\n")
        if refs.get("recon"):
            text, n = read_scrubbed(evid / "recon.md")
            all_notes.extend(n)
            parts.append("=== RECONNAISSANCE NOTES (paths and symbols already inspected) ===\n")
            parts.append(text.rstrip() + "\n\n")
        meta_extra["repositories_named"] = [r["label"] for r in repos]

        _append_evidence_graph_sections(parts, evid, all_notes)

    else:  # document
        refs = json.loads((evid / "doc-refs.json").read_text(encoding="utf-8"))
        artifact = refs.get("artifact_type")
        mode = refs.get("mode")
        repos = refs.get("repositories_inspected") or []
        scope = {}
        if (evid / "scope-risk.json").exists():
            scope = json.loads((evid / "scope-risk.json").read_text(encoding="utf-8"))

        parts.append("=== YONKO DOCKET ===\n")
        parts.append(docket.rstrip() + "\n\n")
        parts.append("=== REVIEW TYPE ===\n")
        parts.append(f"document review - artifact type: {artifact} - mode: {mode}\n")
        parts.append(
            "risk basis: heuristic from stated scope and inspected context (NOT diff-derived)\n"
        )
        parts.append("omitted scope cannot be classified mechanically - hunt for it\n")
        parts.append("no production code may be changed by this review\n\n")
        parts.append("=== REPOSITORIES INSPECTED ===\n")
        if repos:
            for r in repos:
                parts.append(f"{r['label']} @ {r['path']} ({r.get('branch') or 'unknown'})\n")
        else:
            parts.append("(none inspected)\n")
        if scope.get("terms_not_present"):
            parts.append("\n=== TERMS NOT PRESENT IN ARTIFACT (weak signal only) ===\n")
            for t in scope["terms_not_present"]:
                parts.append(f"{t}\n")
        section_map, _ = read_scrubbed(evid / "SECTION_MAP.txt")
        parts.append("\n=== SECTION MAP (line / level / heading) ===\n")
        parts.append((section_map.rstrip() or "(none)") + "\n\n")
        if mode == "review":
            text, n = read_scrubbed(evid / "document.md")
            all_notes.extend(n)
            parts.append(f"=== {str(artifact).upper()} UNDER REVIEW ===\n")
            parts.append(text.rstrip() + "\n\n")
        else:
            parts.append(f"=== {str(artifact).upper()} UNDER REVIEW ===\n")
            parts.append(
                "(create mode - the Chair drafts from source material below; "
                "the draft is reviewed in round 1)\n\n"
            )
        for s in refs.get("sources") or []:
            text, n = read_scrubbed(evid / "sources" / s["name"])
            all_notes.extend(n)
            parts.append(f"=== SOURCE MATERIAL: {s['name']} ===\n")
            parts.append(text.rstrip() + "\n\n")
        if refs.get("recon"):
            text, n = read_scrubbed(evid / "recon.md")
            all_notes.extend(n)
            parts.append("=== RECONNAISSANCE NOTES (paths and symbols already inspected) ===\n")
            parts.append(text.rstrip() + "\n\n")
        meta_extra["artifact_type"] = artifact
        meta_extra["document_mode"] = mode
        meta_extra["repositories_inspected"] = [r["label"] for r in repos]

    packet = "".join(parts)
    packet, dedup_receipt = packet_ops.dedupe_packet(packet)

    if re.search(r"(?i)(?:^|/)\.env(?:\.|\s|$)", packet) and re.search(
        r"(?i)(PASSWORD|SECRET|TOKEN)\s*=\s*\S+", packet
    ):
        raise SystemExit("yonko: packet failed secret scan (possible secrets-file leak)")

    packet_path = session_dir / "packet.md"
    packet_path.write_text(packet, encoding="utf-8")
    digest = hashlib.sha256(packet.encode("utf-8")).hexdigest()

    session = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
    version = int(session.get("packet_version") or 0) + 1
    session["packet_hash"] = digest
    session["packet_version"] = version
    session["status"] = "packet_ready"
    (session_dir / "session.json").write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")

    meta: dict[str, Any] = {
        "packet_hash": digest,
        "packet_version": version,
        "bytes": len(packet.encode("utf-8")),
        "review_type": review_type,
        "scrub_notes": sorted(set(all_notes)),
        "deduplication": dedup_receipt,
    }
    meta.update(meta_extra)
    if linked_plan_handoff:
        meta["linked_plan_handoff"] = linked_plan_handoff
    if review_type == "implementation":
        meta = {
            "packet_hash": digest,
            "packet_version": version,
            "bytes": len(packet.encode("utf-8")),
            "diff_labels": meta_extra["diff_labels"],
            "scrub_notes": sorted(set(all_notes)),
            "review_type": review_type,
            "deduplication": dedup_receipt,
        }
        if linked_plan_handoff:
            meta["linked_plan_handoff"] = linked_plan_handoff

    (session_dir / "packet.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (session_dir / "DOCKET.md").write_text(docket, encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 3:
        print("usage: assemble_packet.py <session_dir> <docket.md> <review_type>", file=sys.stderr)
        return 2
    meta = assemble(pathlib.Path(argv[0]), pathlib.Path(argv[1]), argv[2])
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
