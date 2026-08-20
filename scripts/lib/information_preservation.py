"""Information Preservation checks for Yonko packet optimisations.

Mechanical half of the V4 quality acceptance criteria:

  Yonko may optimise representation, but must never optimise away
  engineering information.

Verbatim forever: DIFF section bodies, fenced code blocks.
Unique prose may become [dedup:ref=N source=…] only when an identical
canonical copy is retained.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HEADER_RE = re.compile(r"(?m)^(=== .+? ===)\s*$")
FENCE_RE = re.compile(r"(?m)^```[^\n]*\n.*?^```\s*$", re.S)
DEDUP_REF_RE = re.compile(r"^\[dedup:ref=(\d+) source=(.+?)\]\s*$")

# Suggested coverage taxonomy for dual-council compare (not exhaustive; Chair may add).
# Optimisation must preserve issue *classes*, not only finding counts.
COVERAGE_CATEGORIES = (
    "correctness",
    "architecture",
    "concurrency",
    "security",
    "performance",
    "compatibility",
    "testing",
    "operability",
    "data-integrity",
    "api-contract",
    "other",
)


def normalize_categories(cats: list[str] | None) -> set[str]:
    if not cats:
        return set()
    return {c.strip().lower().replace(" ", "-").replace("_", "-") for c in cats if c and str(c).strip()}


def _section_name(header: str | None) -> str:
    if not header:
        return "preamble"
    return header.strip("= ").strip()


def split_sections(packet: str) -> list[tuple[str, str]]:
    parts = HEADER_RE.split(packet)
    out: list[tuple[str, str]] = []
    if parts and parts[0].strip():
        out.append(("preamble", parts[0]))
    i = 1
    while i + 1 < len(parts):
        out.append((_section_name(parts[i]), parts[i + 1]))
        i += 2
    return out


def extract_diff_bodies(packet: str) -> dict[str, str]:
    """Map DIFF label -> exact body (must stay byte-identical under optimisation)."""
    out: dict[str, str] = {}
    for name, body in split_sections(packet):
        if name.startswith("DIFF:"):
            label = name[len("DIFF:") :].strip()
            out[label] = body
    return out


def extract_fenced_blocks(packet: str) -> list[str]:
    return FENCE_RE.findall(packet)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class PreservationResult:
    ok: bool
    original_bytes: int
    optimised_bytes: int
    bytes_saved: int
    estimated_token_saved: int
    diff_bodies_identical: bool
    fenced_blocks_identical: bool
    missing_diff_labels: list[str] = field(default_factory=list)
    altered_diff_labels: list[str] = field(default_factory=list)
    fenced_block_count_original: int = 0
    fenced_block_count_optimised: int = 0
    dangling_dedup_refs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _est_tokens(n_bytes: int) -> int:
    return max(0, (n_bytes + 3) // 4)


def validate_dedup_refs_resolvable(optimised: str) -> list[str]:
    """Every [dedup:ref=N source=SEC] must have a prior canonical in that section or earlier."""
    dangling: list[str] = []
    # Build set of refs that were assigned: any non-ref paragraph is canonical;
    # we only check that source section exists in the packet.
    sections = {name for name, _ in split_sections(optimised)}
    for i, line in enumerate(optimised.splitlines(), 1):
        m = DEDUP_REF_RE.match(line.strip())
        if not m:
            continue
        source = m.group(2).strip()
        if source not in sections and source != "preamble":
            dangling.append(f"line {i}: source section {source!r} not in packet")
    return dangling


def compare_packets(original: str, optimised: str) -> PreservationResult:
    """Mechanical information-preservation compare (not a council quality compare)."""
    errors: list[str] = []
    o_diffs = extract_diff_bodies(original)
    n_diffs = extract_diff_bodies(optimised)
    missing = sorted(set(o_diffs) - set(n_diffs))
    altered = sorted(
        label
        for label in set(o_diffs) & set(n_diffs)
        if o_diffs[label] != n_diffs[label]
    )
    # Extra DIFF labels in optimised are unusual but not info loss
    o_fences = extract_fenced_blocks(original)
    n_fences = extract_fenced_blocks(optimised)
    fences_ok = o_fences == n_fences
    if not fences_ok:
        # Order-stable multiset compare
        fences_ok = sorted(o_fences) == sorted(n_fences)
        if not fences_ok:
            errors.append("fenced code blocks differ between original and optimised")

    dangling = validate_dedup_refs_resolvable(optimised)
    if dangling:
        errors.extend(dangling)

    diffs_ok = not missing and not altered
    if missing:
        errors.append(f"missing DIFF labels after optimisation: {missing}")
    if altered:
        errors.append(f"DIFF bodies altered (forbidden): {altered}")

    o_b = len(original.encode("utf-8"))
    n_b = len(optimised.encode("utf-8"))
    saved = max(0, o_b - n_b)
    ok = diffs_ok and fences_ok and not dangling

    return PreservationResult(
        ok=ok,
        original_bytes=o_b,
        optimised_bytes=n_b,
        bytes_saved=saved,
        estimated_token_saved=_est_tokens(saved),
        diff_bodies_identical=diffs_ok,
        fenced_blocks_identical=fences_ok,
        missing_diff_labels=missing,
        altered_diff_labels=altered,
        fenced_block_count_original=len(o_fences),
        fenced_block_count_optimised=len(n_fences),
        dangling_dedup_refs=dangling,
        errors=errors,
    )


@dataclass
class CouncilCompareRecord:
    """Filled by Chair after dual council runs (acceptance criteria)."""

    fixture_id: str
    original_session: str | None = None
    optimised_session: str | None = None
    material_findings_original: int | None = None
    material_findings_optimised: int | None = None
    # Finding coverage: issue *classes*, not just counts.
    # Example failure: original {concurrency, security} vs optimised {concurrency, documentation}
    # still has count=2 but silently lost security coverage.
    finding_categories_original: list[str] = field(default_factory=list)
    finding_categories_optimised: list[str] = field(default_factory=list)
    coverage_lost: list[str] = field(default_factory=list)
    severity_downgrades: list[str] = field(default_factory=list)
    evidence_unresolvable: list[str] = field(default_factory=list)
    verifier_weakened: bool | None = None
    confidence_original: str | None = None
    confidence_optimised: str | None = None
    justified_differences: list[str] = field(default_factory=list)
    unexplained_differences: list[str] = field(default_factory=list)
    pass_: bool | None = None
    notes: str = ""

    def evaluate(self) -> bool:
        """Pass if optimised is not weaker, or every weakness is justified."""
        unexplained: list[str] = []
        mo, mn = self.material_findings_original, self.material_findings_optimised
        if mo is not None and mn is not None and mn < mo:
            gap = mo - mn
            if len(self.justified_differences) < gap:
                unexplained.append(
                    f"fewer material findings ({mo} -> {mn}) without enough justifications"
                )

        # Coverage: categories present in original must remain in optimised
        # unless each lost class is explicitly justified.
        orig_cats = normalize_categories(self.finding_categories_original)
        opt_cats = normalize_categories(self.finding_categories_optimised)
        lost = sorted(orig_cats - opt_cats)
        self.coverage_lost = lost
        if lost:
            justified_blob = " ".join(self.justified_differences).lower()
            unjustified_lost = [
                c for c in lost if c not in justified_blob and f"coverage:{c}" not in justified_blob
            ]
            # Also accept justification mentioning "coverage" + category
            still = []
            for c in unjustified_lost:
                if any(c in j.lower() and "coverage" in j.lower() for j in self.justified_differences):
                    continue
                if any(c in j.lower() for j in self.justified_differences):
                    continue
                still.append(c)
            if still:
                unexplained.append(
                    f"finding coverage lost (same count is not enough): {still}"
                )

        if self.severity_downgrades and not self.justified_differences:
            unexplained.append(f"severity downgrades: {self.severity_downgrades}")
        if self.evidence_unresolvable:
            unexplained.append(f"unresolvable evidence: {self.evidence_unresolvable}")
        if self.verifier_weakened and not self.justified_differences:
            unexplained.append("verifier outcome weakened")
        conf_rank = {"high": 3, "medium": 2, "low": 1}
        if self.confidence_original and self.confidence_optimised:
            if conf_rank.get(self.confidence_optimised.lower(), 0) < conf_rank.get(
                self.confidence_original.lower(), 0
            ):
                if not any("confidence" in j.lower() for j in self.justified_differences):
                    unexplained.append(
                        f"engineering confidence dropped "
                        f"({self.confidence_original} -> {self.confidence_optimised})"
                    )
        self.unexplained_differences = unexplained
        self.pass_ = len(unexplained) == 0
        return self.pass_

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pass"] = d.pop("pass_")
        return d


def write_preservation_report(
    path: Path, mechanical: PreservationResult, council: CouncilCompareRecord | None = None
) -> None:
    payload = {
        "schema_version": "1.0.0",
        "principle": (
            "Yonko may optimise representation, but it must never optimise away "
            "engineering information."
        ),
        "mechanical": mechanical.to_dict(),
        "council_compare": council.to_dict() if council else None,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print(
            "usage: information_preservation.py <original.packet.md> <optimised.packet.md>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    o = Path(sys.argv[1]).read_text(encoding="utf-8")
    n = Path(sys.argv[2]).read_text(encoding="utf-8")
    r = compare_packets(o, n)
    print(json.dumps(r.to_dict(), indent=2))
    raise SystemExit(0 if r.ok else 1)
