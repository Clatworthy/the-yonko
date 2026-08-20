#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def require(text: str, phrases: list[str], source: str) -> None:
    normalized = " ".join(text.split())
    missing = [phrase for phrase in phrases if " ".join(phrase.split()) not in normalized]
    assert not missing, f"{source} missing: {missing}"


def test_plan_reviewers_require_terminal_contracts() -> None:
    prompt = read("prompts/plan-reviewers.md")

    require(
        prompt,
        [
            "Leaf-contract closure (mandatory)",
            "terminal persistence or external-effect leaf",
            "exact precondition or ownership predicate",
            "Follow optimistic-lock, conditional-write, and retry branches through exhaustion",
            "present-but-wrong",
            "old-to-new identity",
            "Online path and repair path use one canonical conflict policy",
            "Operational proof: repair / soak / retry-exhausted metrics and completion criteria",
        ],
        "prompts/plan-reviewers.md",
    )


def test_material_plan_revisions_require_hostile_confirmation() -> None:
    skill = read("SKILL.md")
    prompt = read("prompts/plan-reviewers.md")
    adjudicator = read("prompts/adjudicator.md")

    for source, text in (
        ("SKILL.md", skill),
        ("prompts/plan-reviewers.md", prompt),
        ("prompts/adjudicator.md", adjudicator),
    ):
        require(
            text,
            [
                "medium-or-higher",
                "whole revised plan",
                "terminal leaves",
                "hostile case",
            ],
            source,
        )


def test_approved_plan_preserves_leaf_contracts() -> None:
    template = read("templates/plan-review.md")

    require(
        template,
        [
            "Terminal persistence / external-effect leaves opened",
            "Records / keys / external identities",
            "Lifecycle and identity contracts",
            "Mapper authority",
            "Canonical identity propagation",
            "Operational acceptance",
            "retry-exhausted metric/alarm",
        ],
        "templates/plan-review.md",
    )


def test_revision_does_not_count_as_confirmation() -> None:
    record_event = read("scripts/record-event.sh")
    transition = read("scripts/workflow/transition.py")
    guards = read("scripts/workflow/guards.py")

    require(
        record_event,
        ['payload.setdefault("counts_as_confirmation", False)'],
        "scripts/record-event.sh",
    )
    require(
        transition,
        [
            'wf["confirmation_required"] = True',
            'data.get("confirmation_round") is True',
        ],
        "scripts/workflow/transition.py",
    )
    require(
        guards,
        [
            "PLAN_CONFIRMATION_REQUIRED",
            "wf.get(\"confirmation_required\")",
            "conf < 1",
        ],
        "scripts/workflow/guards.py",
    )


def main() -> None:
    test_plan_reviewers_require_terminal_contracts()
    test_material_plan_revisions_require_hostile_confirmation()
    test_approved_plan_preserves_leaf_contracts()
    test_revision_does_not_count_as_confirmation()
    print("All plan leaf-closure smokes passed.")


if __name__ == "__main__":
    main()
