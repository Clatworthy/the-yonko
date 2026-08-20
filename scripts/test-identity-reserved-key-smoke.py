#!/usr/bin/env python3
"""Acceptance: generic identity + reserved-key auditor Fail then Pass.

Fixtures are in-memory generic snippets (no domain service classes, no ticket ids).
"""
from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "lib" / "audit_identity_reserved_key.py"


def load():
    spec = spec_from_file_location("audit_identity_reserved_key", MOD_PATH)
    mod = module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# Generic snippets only: principal vs resource, reserved-key skip/repair, batch doom.

FAIL_IDENTITY_SRC = """
public void mutate(EntityDAO existing, String reservedKey) {
    Optional<String> owner = uniqueness.resolveOwner(authResolver.getCustomerId(), reservedKey);
    if (owner.isEmpty()) {
        uniqueness.addConditionalClaim(authResolver.getCustomerId(), reservedKey, existing.getId());
    }
}
"""

FAIL_IDENTITY_TEST = """
@Test
void mutateWhenPrincipalMatchesResource() {
    when(auth.getCustomerId()).thenReturn("acct-1");
    when(existing.getCustomerId()).thenReturn("acct-1");
}
"""

PASS_IDENTITY_SRC = """
public void mutate(EntityDAO existing, String reservedKey) {
    String resourceOwner = existing.getCustomerId();
    Optional<String> owner = uniqueness.resolveOwner(resourceOwner, reservedKey);
    if (owner.isEmpty()) {
        uniqueness.addConditionalClaim(resourceOwner, reservedKey, existing.getId());
    }
}
"""

PASS_IDENTITY_TEST = """
@Test
void mutateWithMismatchedPrincipalUsesResourceOwner() {
    when(auth.getCustomerId()).thenReturn(null);
    when(existing.getCustomerId()).thenReturn("acct-resource");
}

@Test
void adminPrincipalDifferentFromResourceOwner() {
    when(auth.getCustomerId()).thenReturn("admin-internal");
    when(existing.getCustomerId()).thenReturn("acct-resource");
}
"""

FAIL_RESERVED_SKIP_SRC = """
public void rename(String ownerId, String entityId, String reservedKey) {
    Optional<Guard> existingNewKey = uniqueness.findGuard(ownerId, reservedKey);
    if (existingNewKey.isEmpty()) {
        uniqueness.addConditionalClaim(ownerId, reservedKey, entityId);
    }
}
"""

FAIL_RESERVED_SKIP_TEST = """
@Test
void renameClaimsWhenKeyMissing() {
    when(store.findGuard("o1", "k1")).thenReturn(Optional.empty());
}
"""

PASS_RESERVED_REPAIR_SRC = """
public void rename(String ownerId, String entityId, String reservedKey) {
    Optional<Guard> existingNewKey = uniqueness.findGuard(ownerId, reservedKey);
    if (existingNewKey.isEmpty()) {
        uniqueness.addConditionalClaim(ownerId, reservedKey, entityId);
    } else if (uniqueness.isStaleOwner(existingNewKey.get())) {
        uniqueness.addConditionalTransferOwner(txn, guard, expectedOwner);
        uniqueness.addIneligibleOwnerConditionCheck(txn, expectedOwner, reservedKey);
    } else if (!entityId.equals(existingNewKey.get().getOwnerId())) {
        throw new ConflictException("reserved key owned by another live entity");
    }
}

public void archive(String ownerId, String entityId, String reservedKey, String nextOwnerId) {
    boolean liveOwner = isEligibleUniquenessOwner(load(nextOwnerId));
    uniqueness.addConditionalTransferOwner(txn, guard, entityId);
    uniqueness.addEligibleOwnerConditionCheck(txn, nextOwnerId, reservedKey);
}
"""

PASS_RESERVED_REPAIR_TEST = """
@Test
void renameRepairsStaleGuard() {
    verify(store).addIneligibleOwnerConditionCheck(any(), any(), eq("k1"));
}

@Test
void concurrentStaleRenameSecondRacerGetsConflictException() {
    assertThrows(ConflictException.class, () -> service.rename("o1", "e-loser", "k1"));
}

@Test
void patchWhenStaleOwnerRevivesAbortsTransfer() {
    assertThrows(ConflictException.class, () -> service.rename("o1", "e2", "k1"));
}

@Test
void archivePo_siblingRenamedAwayBeforeTransfer_retriesThenDeletesGuard() {
    verify(store).addEligibleOwnerConditionCheck(any(), eq("sib"), eq("k1"));
}
"""

FAIL_TOCTOU_SRC = """
public void clearStale(String ownerId, String reservedKey, String expectedOwner) {
    boolean liveOwner = isEligibleUniquenessOwner(load(expectedOwner));
    if (!liveOwner) {
        uniqueness.deleteIfOwner(ownerId, reservedKey, expectedOwner);
    }
}

public void renameSteal(String ownerId, String entityId, String reservedKey, String staleOwner) {
    if (!isEligibleUniquenessOwner(load(staleOwner))) {
        uniqueness.addConditionalTransferOwner(txn, guard, staleOwner);
    }
}

public void archiveTransfer(String entityId, String nextOwnerId, String reservedKey) {
    uniqueness.addConditionalTransferOwner(txn, guard, entityId);
    uniqueness.addEligibleOwnerConditionCheck(txn, nextOwnerId);
}
"""

FAIL_TOCTOU_TEST = """
@Test
void clearStaleDeletesWhenOwnerLooksArchived() {
    verify(store).deleteIfOwner(any(), any(), any());
}
"""

FAIL_BATCH_SRC = """
public void deleteBatch(String ownerId, List<String> ids) {
    List<EntityDAO> toBeDeleted = repo.loadActive(ids);
    toBeDeleted.parallelStream().forEach(entity -> {
        EntityDAO nextOwner = repo.findActiveByKey(ownerId, entity.getReservedKey())
                .stream()
                .filter(c -> !c.getId().equals(entity.getId()))
                .findFirst()
                .orElse(null);
        if (nextOwner != null) {
            uniqueness.transferGuard(ownerId, entity.getReservedKey(), nextOwner.getId());
        }
        repo.archive(entity.getId());
    });
}
"""

FAIL_BATCH_TEST = """
@Test
void deleteTransfersGuardWhenAnotherActiveExists() {
    verify(store, atMost(1)).transferGuard(any(), any(), any());
}
"""

PASS_BATCH_SRC = """
public void deleteBatch(String ownerId, List<String> ids) {
    List<EntityDAO> toBeDeleted = repo.loadActive(ids);
    Set<String> doomedIds = toBeDeleted.stream().map(EntityDAO::getId).collect(Collectors.toSet());
    for (EntityDAO entity : toBeDeleted) {
        EntityDAO nextOwner = repo.findActiveByKey(ownerId, entity.getReservedKey())
                .stream()
                .filter(c -> !doomedIds.contains(c.getId()))
                .findFirst()
                .orElse(null);
        if (nextOwner != null) {
            uniqueness.transferGuard(ownerId, entity.getReservedKey(), nextOwner.getId());
        } else {
            uniqueness.clearGuard(ownerId, entity.getReservedKey());
        }
        repo.archive(entity.getId());
    }
}
"""

PASS_BATCH_TEST = """
@Test
void bulkDeleteWithDuplicateSiblingsClearsGuardInsteadOfTransferringOntoDoomedSibling() {
    verify(store, atLeastOnce()).clearGuard("o1", "k1");
    verify(store, never()).transferGuard(eq("o1"), eq("k1"), any());
}
"""


def codes(findings) -> set[str]:
    return {f.code for f in findings}


def expect_fail(mod, name: str, src: str, test: str, required: set[str]) -> None:
    findings = mod.audit_snippets({f"{name}/src.txt": src, f"{name}/test.txt": test})
    got = codes(findings)
    missing = required - got
    assert not missing, f"{name}: expected Fail codes {required}, got {got}; findings={findings}"
    print(f"PASS {name} fails closed ({sorted(got & required)})")


def expect_pass(mod, name: str, src: str, test: str, forbidden: set[str]) -> None:
    findings = mod.audit_snippets({f"{name}/src.txt": src, f"{name}/test.txt": test})
    got = codes(findings)
    bad = got & forbidden
    assert not bad, f"{name}: expected Pass, still has {bad}; findings={findings}"
    print(f"PASS {name} passes ({sorted(got) if got else '0 findings'})")


def main() -> int:
    mod = load()

    expect_fail(
        mod,
        "fail_identity",
        FAIL_IDENTITY_SRC,
        FAIL_IDENTITY_TEST,
        {"identity_principal_without_resource"},
    )
    expect_pass(
        mod,
        "pass_identity",
        PASS_IDENTITY_SRC,
        PASS_IDENTITY_TEST,
        {"identity_principal_without_resource", "identity_missing_diverge_test"},
    )

    expect_fail(
        mod,
        "fail_reserved_skip",
        FAIL_RESERVED_SKIP_SRC,
        FAIL_RESERVED_SKIP_TEST,
        {"reserved_key_stale_skip"},
    )
    expect_pass(
        mod,
        "pass_reserved_repair",
        PASS_RESERVED_REPAIR_SRC,
        PASS_RESERVED_REPAIR_TEST,
        {
            "reserved_key_stale_skip",
            "reserved_key_missing_concurrent_stale_test",
            "reserved_key_eligibility_toctou_delete",
            "reserved_key_eligibility_toctou_transfer",
            "reserved_key_transfer_destination_missing_key",
            "reserved_key_missing_revival_test",
            "reserved_key_missing_key_drift_test",
        },
    )

    expect_fail(
        mod,
        "fail_toctou",
        FAIL_TOCTOU_SRC,
        FAIL_TOCTOU_TEST,
        {
            "reserved_key_eligibility_toctou_delete",
            "reserved_key_eligibility_toctou_transfer",
            "reserved_key_transfer_destination_missing_key",
            "reserved_key_missing_revival_test",
            "reserved_key_missing_key_drift_test",
        },
    )

    expect_fail(
        mod,
        "fail_batch",
        FAIL_BATCH_SRC,
        FAIL_BATCH_TEST,
        {"reserved_key_batch_doomed_destination"},
    )
    expect_pass(
        mod,
        "pass_batch",
        PASS_BATCH_SRC,
        PASS_BATCH_TEST,
        {
            "reserved_key_batch_doomed_destination",
            "reserved_key_missing_batch_sibling_test",
        },
    )

    print("All identity/reserved-key acceptance fixtures passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
