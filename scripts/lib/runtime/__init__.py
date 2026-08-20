"""Provider-neutral execution profile and runtime adapters for Yonko seats."""

from __future__ import annotations

FAILURE_CATEGORIES = (
    "runtime_not_installed",
    "authentication_missing",
    "model_unavailable",
    "invalid_profile",
    "invalid_model_mapping",
    "timeout",
    "process_failure",
    "malformed_output",
    "schema_validation_failure",
    "permission_violation",
    "rate_limited",
    "provider_unavailable",
    "unknown_runtime_error",
    "repository_modified",
)

DEFAULT_PROFILE_ID = "cursor-standard"
SUPPORTED_RUNTIMES = ("cursor", "opencode")
REQUIRED_SEATS = ("chair", "shanks", "blackbeard", "buggy", "luffy")
