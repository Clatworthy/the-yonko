#!/usr/bin/env python3
"""Fail-closed identity-source and reserved-key lifecycle audit.

Generic (domain-agnostic). Scans source trees, diffs, or in-memory snippets for:

1. Principal-scoped id used for ownership/uniqueness/claim without naming the
   resource-owner id source (and without a diverge-principal test).
2. Reserved-key / lease / claim indirection that skips writes when a key exists,
   or transfers onto a destination still in the same delete/archive batch.
3. Eligibility TOCTOU: stale/eligible decided outside the mutate transaction,
   then guard delete/transfer conditioned only on owner id; missing revival and
   destination key-drift adversary tests.

Exit 0 only when no findings. Confirmatory "helper was called" without leaf
identity source + reserved-key lifecycle adversary is why half-built uniqueness
state machines kept shipping past review.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

PRINCIPAL_ID = re.compile(
    r"(?:authResolver|authPrincipleResolver|authPrincipalResolver|principal|"
    r"DefaultAuthPrincipleResolver|securityContext|jwt)\s*\.\s*"
    r"get(?:Customer|Account|Tenant|Organisation|Organization|Owner)Id\s*\(",
    re.I,
)
PRINCIPAL_ID_ALT = re.compile(
    r"get(?:Auth|Current|Caller|Principal)(?:Customer|Account|Tenant|Owner)Id\s*\(",
    re.I,
)
RESOURCE_OWNER_ID = re.compile(
    r"(?:existing\w*|entity|dao|record|row|resource|target|owned)\w*"
    r"\s*\.\s*get(?:Customer|Account|Tenant|Owner)Id\s*\(",
    re.I,
)
OWNERSHIP_LEAF = re.compile(
    r"(?:resolveOwner|addConditionalClaim|conditionalClaim|claimUniqueness|"
    r"claimOwnership|putIfAbsent|createUniqueness|uniquenessGuard|"
    r"ownershipPointer|reserveKey|leaseKey)\s*\(",
    re.I,
)
UNIQUENESS_TOUCH = re.compile(
    r"(?:Uniqueness|OwnershipGuard|OwnershipPointer|ConditionalClaim|"
    r"unique(?:ness)?[Kk]ey|reserved[Kk]ey|lease[Kk]ey)",
    re.I,
)
STALE_SKIP = re.compile(
    r"if\s*\(\s*(?:existing\w*[Kk]ey|existingNewKey|existingGuard|maybeGuard|"
    r"currentOwner|existingClaim)\w*\s*(?:\.\s*isEmpty\s*\(\s*\)|\s*==\s*null|"
    r"\s*\.isPresent\s*\(\s*\)\s*==\s*false)",
    re.I,
)
CLAIM_WRITE = re.compile(
    r"(?:addConditionalClaim|putIfAbsent|claimUniqueness|createUniqueness|"
    r"writeGuard|insertGuard|saveUniqueness|conditionalPut|reserveKey)\s*\(",
    re.I,
)
STALE_REPAIR = re.compile(
    r"(?:transfer(?:Guard|Ownership|Claim|Uniqueness|Key|Owner)|"
    r"addConditionalTransferOwner|replace(?:Guard|Owner)|"
    r"rehome(?:Guard|Ownership)|repair(?:Stale|Guard)|"
    r"delete(?:Uniqueness|Guard|Claim)|clear(?:Guard|Uniqueness|Key)|"
    r"deleteIfOwner(?:WhenStillIneligible)?|putReplacingInvalidOwner|"
    r"addIneligibleOwnerConditionCheck|"
    r"updateConditional|conditionalUpdate|overwriteStale)",
    re.I,
)
OWNER_ONLY_DELETE = re.compile(
    r"deleteIfOwner\s*\(",
    re.I,
)
TXN_TIED_DELETE = re.compile(
    r"(?:deleteIfOwnerWhenStillIneligible|addIneligibleOwnerConditionCheck)\s*\(",
    re.I,
)
TRANSFER_OWNER_MUTATE = re.compile(
    r"(?:addConditionalTransferOwner|putReplacingInvalidOwner|transferGuard)\s*\(",
    re.I,
)
INELIGIBLE_SAME_TXN = re.compile(
    r"(?:addIneligibleOwnerConditionCheck|WhenStillIneligible|ineligibleOwner)\s*\(",
    re.I,
)
ELIGIBLE_DEST_WITH_KEY = re.compile(
    r"addEligibleOwnerConditionCheck\s*\(\s*[^,]+,\s*[^,]+,\s*[^)]+\)",
    re.I | re.S,
)
ELIGIBLE_DEST_WITHOUT_KEY = re.compile(
    r"addEligibleOwnerConditionCheck\s*\(\s*[^,)]+\s*,\s*[^,)]+\s*\)",
    re.I,
)
STALE_ELIGIBILITY_READ = re.compile(
    r"(?:isEligibleUniquenessOwner|liveOwner|clearStale|isStaleOwner|"
    r"filter\s*\(\s*this::isEligible)",
    re.I,
)
BATCH_DOOMED = re.compile(
    r"(?:toBeDeleted|idsToDelete|idsToArchive|toArchive|pendingDelete|"
    r"deleteBatch|archiveBatch|doomedIds|batchDeleteIds)",
    re.I,
)
TRANSFER_CALL = re.compile(
    r"(?:transfer(?:Guard|Ownership|Claim|Uniqueness|Key)|rehome(?:Guard|Ownership)|"
    r"moveOwnership|nextOwner|addConditionalTransferOwner|putReplacingInvalidOwner)\s*\(",
    re.I,
)
EXCLUDE_BATCH = re.compile(
    r"(?:exclude|removeAll|filterNot|!.*contains|noneMatch|"
    r"\.filter\s*\([^)]*(?:!|not))"
    r".{0,120}(?:toBeDeleted|idsToDelete|idsToArchive|toArchive|"
    r"pendingDelete|deleteBatch|doomedIds|batchDeleteIds)",
    re.I | re.S,
)
DIVERGE_TEST = re.compile(
    r"(?:nullCustomer|mismatched(?:Customer|Principal|Owner)|differentCustomer|"
    r"adminPrincipal|internalPrincipal|principal.*null|"
    r"getCustomerId\s*\(\s*\)\s*\)\s*\.\s*thenReturn\s*\(\s*null|"
    r"thenReturn\s*\(\s*(?:null|\"(?:other|admin|internal)\")\s*\))",
    re.I,
)
CONCURRENT_STALE_TEST = re.compile(
    r"(?:concurrent|race|lostRace|secondRacer|ConflictException|"
    r"ConditionalCheckFailed|duplicate.*(?:renam|claim|mutate))",
    re.I,
)
REVIVAL_TEST = re.compile(
    r"(?:reviv|staleOwnerRevives|ownerRevives|whenStaleOwnerRevives|"
    r"beforeClear.*reviv|revivesBeforeClear|concurrent.*eligib)",
    re.I,
)
KEY_DRIFT_TEST = re.compile(
    r"(?:renamedAway|buyerOrderNumberChanged|archive.*rename|rename.*archive|"
    r"siblingRenamed|destination.*(?:number|key).*chang|keyDrift|"
    r"transferTarget.*(?:ineligible|renamed))",
    re.I,
)
BATCH_SIBLING_TEST = re.compile(
    r"(?:sibling|sameRequest|bulkDelete|batchDelete|doomedIds|toBeDeleted|"
    r"duplicate.*batch)",
    re.I,
)


@dataclass
class Finding:
    code: str
    severity: str
    path: str
    detail: str
    attack_card_row: str


def _methods(text: str) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    skip_names = {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "try",
        "else",
        "do",
        "synchronized",
        "new",
    }
    for match in re.finditer(
        r"(?:public|protected|private|static|\s)+\s+[\w<>,\s\[\]]+\s+(\w+)\s*\([^;]*?\)\s*(?:throws [^{]+)?\{",
        text,
    ):
        name = match.group(1)
        if name in skip_names:
            continue
        start = match.end() - 1
        depth = 0
        i = start
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    chunks.append((name, text[start : i + 1]))
                    break
            i += 1
    if not chunks:
        chunks.append(("<snippet>", text))
    return chunks


def _read_texts(paths: list[Path]) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in paths:
        if path.is_file():
            try:
                out[str(path)] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        elif path.is_dir():
            for child in path.rglob("*"):
                if child.suffix.lower() in {
                    ".java",
                    ".kt",
                    ".kts",
                    ".ts",
                    ".js",
                    ".patch",
                    ".diff",
                    ".txt",
                    ".md",
                }:
                    try:
                        out[str(child)] = child.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
    return out


def audit_files(files: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    test_blob = "\n".join(v for k, v in files.items() if "test" in k.lower())
    has_diverge = bool(DIVERGE_TEST.search(test_blob))
    has_concurrent = bool(CONCURRENT_STALE_TEST.search(test_blob))
    has_batch_test = bool(BATCH_SIBLING_TEST.search(test_blob))
    has_revival = bool(REVIVAL_TEST.search(test_blob))
    has_key_drift = bool(KEY_DRIFT_TEST.search(test_blob))
    all_src = "\n".join(v for k, v in files.items() if "test" not in k.lower())
    touches_reserved = bool(UNIQUENESS_TOUCH.search(all_src) or CLAIM_WRITE.search(all_src))

    saw_stale_skip = False
    saw_stale_repair = False
    saw_batch_transfer = False
    saw_eligibility_toctou_path = False

    for path, text in files.items():
        is_test = "test" in path.lower()
        for method, body in _methods(text):
            if not is_test:
                uses_principal = bool(PRINCIPAL_ID.search(body) or PRINCIPAL_ID_ALT.search(body))
                uses_resource = bool(RESOURCE_OWNER_ID.search(body))
                touches_leaf = bool(OWNERSHIP_LEAF.search(body) or UNIQUENESS_TOUCH.search(body))
                if uses_principal and touches_leaf and not uses_resource:
                    findings.append(
                        Finding(
                            code="identity_principal_without_resource",
                            severity="high",
                            path=f"{path}#{method}",
                            detail=(
                                "Ownership/uniqueness/claim leaf scoped with a "
                                "principal id and no resource-owner id in the same "
                                "method. Name principal vs resource source; invent "
                                "a diverge caller (null/different principal). "
                                "Matching principal+resource stubs alone are Fail."
                            ),
                            attack_card_row="Identity sources in diff",
                        )
                    )
                elif uses_principal and touches_leaf and uses_resource and not has_diverge:
                    findings.append(
                        Finding(
                            code="identity_missing_diverge_test",
                            severity="high",
                            path=f"{path}#{method}",
                            detail=(
                                "Principal and resource ids both appear near an "
                                "ownership leaf, but tests never diverge them."
                            ),
                            attack_card_row="Identity sources in diff",
                        )
                    )

                if STALE_SKIP.search(body) and CLAIM_WRITE.search(body):
                    if not STALE_REPAIR.search(body):
                        saw_stale_skip = True
                        findings.append(
                            Finding(
                                code="reserved_key_stale_skip",
                                severity="high",
                                path=f"{path}#{method}",
                                detail=(
                                    "Reserved-key write runs only when absent. "
                                    "When a row exists but the owner is "
                                    "missing/archived/invalid, skipping the write "
                                    "is Fail - repair/transfer in the same "
                                    "transaction. Concurrent double-mutate onto "
                                    "the same stale key: exactly one winner."
                                ),
                                attack_card_row="Reserved-key lifecycle",
                            )
                        )
                    else:
                        saw_stale_repair = True

                if TRANSFER_CALL.search(body) and BATCH_DOOMED.search(body):
                    saw_batch_transfer = True
                    if not (
                        EXCLUDE_BATCH.search(body)
                        or re.search(r"clear(?:Guard|Uniqueness|Key)|delete(?:Uniqueness|Guard)", body, re.I)
                    ):
                        findings.append(
                            Finding(
                                code="reserved_key_batch_doomed_destination",
                                severity="high",
                                path=f"{path}#{method}",
                                detail=(
                                    "Reserved-key transfer in a batch "
                                    "delete/archive path without excluding ids in "
                                    "the doomed set. Exclude batch ids or clear "
                                    "when no live owner remains. Verifying "
                                    "transfer-was-called is Fail."
                                ),
                                attack_card_row="Reserved-key lifecycle",
                            )
                        )

                # Eligibility decided out of band + ownerPoId-only mutate.
                eligibility_read = bool(STALE_ELIGIBILITY_READ.search(body))
                owner_only_delete = bool(OWNER_ONLY_DELETE.search(body)) and not bool(
                    TXN_TIED_DELETE.search(body)
                )
                transfer_mutate = bool(TRANSFER_OWNER_MUTATE.search(body))
                ineligible_tied = bool(INELIGIBLE_SAME_TXN.search(body))
                eligible_with_key = bool(ELIGIBLE_DEST_WITH_KEY.search(body))
                eligible_without_key = bool(ELIGIBLE_DEST_WITHOUT_KEY.search(body)) and not eligible_with_key

                if eligibility_read and owner_only_delete:
                    saw_eligibility_toctou_path = True
                    findings.append(
                        Finding(
                            code="reserved_key_eligibility_toctou_delete",
                            severity="high",
                            path=f"{path}#{method}",
                            detail=(
                                "Stale/eligible owner decided from a read, then "
                                "deleteIfOwner without a same-txn ineligible "
                                "ConditionCheck. Owner can revive before delete. "
                                "Tie ineligibility to the delete transaction."
                            ),
                            attack_card_row="Reserved-key eligibility TOCTOU",
                        )
                    )

                if transfer_mutate and not ineligible_tied:
                    # putReplacingInvalidOwner must embed checks in its own body;
                    # callers are not required to repeat them.
                    steal_path = bool(
                        re.search(
                            r"(?:isStaleOwner|staleOwner|transferStale|clearStale)",
                            body,
                            re.I,
                        )
                    ) and not bool(re.search(r"putReplacingInvalidOwner\s*\(", body, re.I))
                    if steal_path:
                        saw_eligibility_toctou_path = True
                        findings.append(
                            Finding(
                                code="reserved_key_eligibility_toctou_transfer",
                                severity="high",
                                path=f"{path}#{method}",
                                detail=(
                                    "Guard transfer/steal without same-txn "
                                    "ineligible ConditionCheck on the expected "
                                    "owner. Concurrent revival can keep the key "
                                    "while this write steals it."
                                ),
                                attack_card_row="Reserved-key eligibility TOCTOU",
                            )
                        )

                if transfer_mutate and (eligible_without_key or (
                    eligibility_read and not eligible_with_key and "addEligibleOwnerConditionCheck" in body
                )):
                    saw_eligibility_toctou_path = True
                    findings.append(
                        Finding(
                            code="reserved_key_transfer_destination_missing_key",
                            severity="high",
                            path=f"{path}#{method}",
                            detail=(
                                "Transfer destination ConditionCheck does not "
                                "assert the destination still holds the reserved "
                                "business key. Archive-vs-rename can leave the "
                                "guard on an unrelated number."
                            ),
                            attack_card_row="Transfer destination key-drift test",
                        )
                    )

                if transfer_mutate and ineligible_tied:
                    saw_stale_repair = True
                    saw_eligibility_toctou_path = True
                if transfer_mutate and eligible_with_key:
                    saw_eligibility_toctou_path = True
                if TXN_TIED_DELETE.search(body):
                    saw_stale_repair = True
                    saw_eligibility_toctou_path = True

    if touches_reserved and (saw_stale_skip or saw_stale_repair) and not has_concurrent:
        findings.append(
            Finding(
                code="reserved_key_missing_concurrent_stale_test",
                severity="high",
                path="<tests>",
                detail=(
                    "Reserved-key mutate path without a concurrent stale/absent-key "
                    "race test (second racer must Conflict, not both skip)."
                ),
                attack_card_row="Reserved-key lifecycle",
            )
        )
    if saw_batch_transfer and not has_batch_test:
        findings.append(
            Finding(
                code="reserved_key_missing_batch_sibling_test",
                severity="high",
                path="<tests>",
                detail=(
                    "Batch transfer/rehome of a reserved key without a test where "
                    "the destination is also in the delete/archive batch."
                ),
                attack_card_row="Reserved-key lifecycle",
            )
        )
    if saw_eligibility_toctou_path and not has_revival:
        findings.append(
            Finding(
                code="reserved_key_missing_revival_test",
                severity="high",
                path="<tests>",
                detail=(
                    "Reserved-key clear/steal/transfer without a concurrent "
                    "stale-owner revival adversary test."
                ),
                attack_card_row="Concurrent stale-owner revival test",
            )
        )
    if saw_eligibility_toctou_path and not has_key_drift:
        findings.append(
            Finding(
                code="reserved_key_missing_key_drift_test",
                severity="high",
                path="<tests>",
                detail=(
                    "Reserved-key transfer without an archive-vs-rename (or "
                    "equivalent destination key-drift) adversary test."
                ),
                attack_card_row="Transfer destination key-drift test",
            )
        )
    return findings


def audit_snippets(snippets: dict[str, str]) -> list[Finding]:
    """Audit named in-memory snippets (keys used as pseudo-paths)."""
    return audit_files(snippets)


def audit(paths: list[Path]) -> list[Finding]:
    return audit_files(_read_texts(paths))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Files or directories to scan (source + tests)",
    )
    parser.add_argument(
        "--snippet-dir",
        type=Path,
        help="Directory of *.src.txt / *.test.txt pairs (generic fixtures)",
    )
    parser.add_argument("--json", action="store_true", help="Emit findings JSON")
    args = parser.parse_args(argv)

    files: dict[str, str] = {}
    if args.paths:
        files.update(_read_texts(args.paths))
    if args.snippet_dir:
        for child in sorted(args.snippet_dir.rglob("*")):
            if child.is_file() and child.suffix in {".txt", ".diff", ".patch", ".java", ".kt"}:
                files[str(child)] = child.read_text(encoding="utf-8", errors="replace")

    findings = audit_files(files)
    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        if not findings:
            print("identity-reserved-key audit: PASS (0 findings)")
        else:
            print(f"identity-reserved-key audit: FAIL ({len(findings)} findings)")
            for f in findings:
                print(f"- [{f.severity}] {f.code} @ {f.path}: {f.detail}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
