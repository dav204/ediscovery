"""Text normalization v1 — the single source of truth for subject_norm,
body_norm, body_hash, and token_estimate derivation.

Both corpora ingest through here so hashes are comparable across them.
config/pipeline.yaml normalization.version must match NORMALIZATION_VERSION;
bump both together when these rules change (invalidates body_hash).
"""

import hashlib
import re

NORMALIZATION_VERSION = "v1"

_REPLY_PREFIX_RE = re.compile(r"^\s*(re|fw|fwd)\s*:\s*", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def normalize_subject(subject: str | None) -> str | None:
    """Lowercase, strip any stack of reply/forward prefixes, collapse whitespace."""
    if subject is None:
        return None
    s = subject
    while True:
        stripped = _REPLY_PREFIX_RE.sub("", s, count=1)
        if stripped == s:
            break
        s = stripped
    return _WS_RE.sub(" ", s).strip().lower()


def normalize_body(text: str) -> str:
    """Unify line endings, strip per-line trailing whitespace and outer blank lines."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip("\n")


def body_hash(norm: str) -> str:
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    """Chars/4 heuristic — same convention cost.py uses for protocol text."""
    return len(text) // 4
