"""Load evaluation / observability config without PyYAML dependency."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


def skill_root() -> Path:
    env = os.environ.get("YONKO_SKILL_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


def sessions_root() -> Path:
    env = os.environ.get("YONKO_SESSIONS_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".cursor" / "yonko-sessions").resolve()


def _scalar(val: str) -> Any:
    v = val.strip()
    if not v:
        return ""
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~", "none"):
        return None
    try:
        if "." in v:
            return float(v)
        return int(v)
    except ValueError:
        return v


def parse_minimal_yaml(text: str) -> Any:
    """Indent-based parser for the flat YAML shapes in this skill."""
    lines: list[str] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        lines.append(raw.rstrip())

    def parse_block(start: int, indent: int) -> tuple[Any, int]:
        if start >= len(lines):
            return {}, start
        first = lines[start]
        cur_indent = len(first) - len(first.lstrip(" "))
        if cur_indent < indent:
            return {}, start
        if first.lstrip().startswith("- "):
            items: list[Any] = []
            i = start
            while i < len(lines):
                line = lines[i]
                ind = len(line) - len(line.lstrip(" "))
                if ind < indent:
                    break
                if ind != indent or not line.lstrip().startswith("- "):
                    break
                body = line.lstrip()[2:]
                if ":" in body and not body.startswith("{"):
                    key, _, val = body.partition(":")
                    nested, i2 = parse_block(i + 1, indent + 2)
                    obj: dict[str, Any] = {key.strip(): _scalar(val.strip())}
                    if isinstance(nested, dict):
                        obj.update(nested)
                    items.append(obj)
                    i = i2
                elif body.endswith(":") and not body.startswith("["):
                    key = body[:-1].strip()
                    nested, i2 = parse_block(i + 1, indent + 2)
                    items.append({key: nested})
                    i = i2
                else:
                    items.append(_scalar(body))
                    i += 1
            return items, i

        mapping: dict[str, Any] = {}
        i = start
        while i < len(lines):
            line = lines[i]
            ind = len(line) - len(line.lstrip(" "))
            if ind < indent:
                break
            if ind > indent:
                break
            if line.lstrip().startswith("- "):
                break
            if ":" not in line:
                i += 1
                continue
            key, _, rest = line.lstrip().partition(":")
            key = key.strip()
            rest = rest.strip()
            if rest == "" or rest == "|" or rest == ">":
                nested, i2 = parse_block(i + 1, indent + 2)
                mapping[key] = nested
                i = i2
            else:
                mapping[key] = _scalar(rest)
                i += 1
        return mapping, i

    data, _ = parse_block(0, 0)
    return data if isinstance(data, dict) else {}


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        data = parse_minimal_yaml(text)
        return data if isinstance(data, dict) else {}


def load_observability_evaluation() -> dict[str, Any]:
    """Sole owner of capture_on_finalize and fail_open."""
    path = skill_root() / "config" / "observability-policy.yaml"
    data = load_yaml_file(path)
    section = data.get("evaluation") if isinstance(data.get("evaluation"), dict) else {}
    return {
        "capture_on_finalize": bool(section.get("capture_on_finalize", True)),
        "fail_open": bool(section.get("fail_open", True)),
    }


def load_evaluation_yaml() -> dict[str, Any]:
    """Paths, min_sample_n, retention, replay defaults only."""
    path = skill_root() / "config" / "evaluation.yaml"
    data = load_yaml_file(path)
    min_n = data.get("min_sample_n", 10)
    try:
        min_n = int(min_n)
    except (TypeError, ValueError):
        min_n = 10
    paths = data.get("paths") if isinstance(data.get("paths"), dict) else {}
    return {
        "min_sample_n": min_n,
        "paths": paths,
        "retention": data.get("retention") if isinstance(data.get("retention"), dict) else {},
        "replay": data.get("replay") if isinstance(data.get("replay"), dict) else {},
        "raw": data,
    }


def expand_sessions_path(raw: str | None, default_rel: str) -> Path:
    if not raw:
        return sessions_root() / default_rel
    raw = raw.strip()
    if raw.startswith("~"):
        return Path(raw).expanduser().resolve()
    p = Path(raw)
    if p.is_absolute():
        return p.resolve()
    return (skill_root() / p).resolve()


def measurement_index_path() -> Path:
    cfg = load_evaluation_yaml()
    raw = (cfg.get("paths") or {}).get("measurement_index")
    return expand_sessions_path(
        raw if isinstance(raw, str) else None,
        "_rollup/measurement-index.jsonl",
    )


SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|password|token|authorization)\s*[:=]\s*['\"]?[A-Za-z0-9/+=_\-]{12,}"),
    re.compile(r"(?i)-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)aws_secret_access_key\s*=\s*\S+"),
    re.compile(r"(?i)GITLAB_PERSONAL_ACCESS_TOKEN\s*=\s*\S+"),
]


def secret_scan_text(text: str) -> list[str]:
    hits: list[str] = []
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            hits.append(pat.pattern)
    return hits
