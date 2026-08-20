from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = SKILL_ROOT / "config" / "repository-exploration.json"
REVIEW_MODES = {
    "packet_only",
    "packet_plus_workspace_read",
    "full_agent",
}


def load_exploration_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def risk_band(session_dir: Path) -> str:
    evidence = Path(session_dir) / "evidence"
    for name in ("routing.json", "risk.json", "scope-risk.json"):
        path = evidence / name
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("risk_band")
            if not value:
                value = json.loads(path.read_text(encoding="utf-8")).get("risk")
        except json.JSONDecodeError:
            continue
        if value:
            return str(value)
    return "medium"


def budget_for(session_dir: Path, seat: str) -> dict[str, int]:
    config = load_exploration_config()
    budgets = config.get("budgets") or {}
    band = risk_band(session_dir)
    budget = dict(budgets.get(band) or budgets.get("medium") or {})
    if seat == "luffy" and band in ("low", "medium", "high"):
        higher = {"low": "medium", "medium": "high", "high": "critical"}[band]
        budget = dict(budgets.get(higher) or budget)
    return {str(k): int(v) for k, v in budget.items()}


def resolve_workspace_root(session_dir: Path) -> Path:
    config = load_exploration_config()
    repos_path = Path(session_dir) / "evidence" / "repos.json"
    repos_doc: dict[str, Any] = {}
    if repos_path.is_file():
        try:
            repos_doc = json.loads(repos_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            repos_doc = {}

    explicit = repos_doc.get("workspace_root")
    if explicit:
        root = Path(str(explicit)).expanduser().resolve()
        if root.is_dir():
            return root

    repo_paths = [
        Path(str(row["path"])).expanduser().resolve()
        for row in (repos_doc.get("repos") or [])
        if row.get("path")
    ]
    for raw in config.get("workspace_root_candidates") or []:
        candidate = Path(str(raw)).expanduser().resolve()
        if candidate.is_dir() and all(
            path == candidate or candidate in path.parents for path in repo_paths
        ):
            return candidate

    if repo_paths:
        common = Path(repo_paths[0])
        for path in repo_paths[1:]:
            while common != common.parent and not (
                path == common or common in path.parents
            ):
                common = common.parent
        return common
    return Path(session_dir).resolve()


def resolve_primary_workdir(session_dir: Path) -> Path:
    repos_path = Path(session_dir) / "evidence" / "repos.json"
    if repos_path.is_file():
        try:
            repos = json.loads(repos_path.read_text(encoding="utf-8")).get(
                "repos"
            ) or []
        except json.JSONDecodeError:
            repos = []
        for row in repos:
            raw = row.get("path")
            if raw:
                path = Path(str(raw)).expanduser().resolve()
                if path.is_dir():
                    return path
    return Path(session_dir).resolve()


def review_mode_for(mapping: dict[str, Any], runtime: str) -> str:
    if runtime != "opencode":
        return "packet_only"
    mode = str(
        mapping.get("review_mode")
        or load_exploration_config().get("live_review_mode")
        or "packet_only"
    )
    if mode not in REVIEW_MODES or mode == "full_agent":
        raise ValueError(f"unsupported review mode for reviewer seat: {mode}")
    return mode
