"""Threading + inclusive-email detection over exact-canonical messages.

The Enron v2 .eml exports carry NO In-Reply-To/References anywhere (verified:
0 of 101,860), so jwz threading has nothing to work with. Threads are built by
subject: canonical docs sharing a non-empty subject_norm form a chain in
(date_utc, doc_id) order, SPLIT wherever the gap between consecutive messages
exceeds preprocess.thread_max_gap_days — distinct conversations reusing a
generic subject months apart must not weld into one thread (observed: 19% of
multi-doc threads carried >30-day jumps before the split). Each message's
parent is its predecessor in the segment.

Containment is tested on quote-collapsed text: leading '>'/whitespace quote
markers stripped per line, then all whitespace collapsed — Outlook-style
replies re-indent and re-wrap quoted text, and raw substring matching missed
~84% of genuine quotes. Method per node: "containment" when ANY earlier
segment member's collapsed body appears in this one (chain adjacency is not
reply adjacency), else "subject_fallback"; empty-subject docs and one-doc
segments are "singleton".

Inclusive = the thread nodes a reviewer must read to have seen everything:
  terminal_node       no later message in the segment
  unique_content      a later message exists, but none contains this body
  attachment_unique   content is carried forward but an attachment NAME is not
                      (names only — the natives are not compared)
Non-inclusive nodes are fully represented by their descendants.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pyarrow as pa

_DATE_MAX = datetime.max.replace(tzinfo=timezone.utc)


def _chain_order(row: dict):
    return (row["date_utc"] or _DATE_MAX, row["doc_id"])


def collapse_for_containment(body: str) -> str:
    lines = (line.lstrip("> \t") for line in body.split("\n"))
    return " ".join(" ".join(lines).split())


def _contained(needle: str, haystack: str) -> bool:
    return bool(needle) and needle in haystack


def _segments(members: list[dict], max_gap_days: int) -> list[list[dict]]:
    """Split a date-ordered subject group where consecutive dates jump more
    than max_gap_days. Null dates sort last and never trigger a split."""
    gap = timedelta(days=max_gap_days)
    segments: list[list[dict]] = [[members[0]]]
    for prev, cur in zip(members, members[1:]):
        if (prev["date_utc"] is not None and cur["date_utc"] is not None
                and cur["date_utc"] - prev["date_utc"] > gap):
            segments.append([cur])
        else:
            segments[-1].append(cur)
    return segments


def build_threads(rows: list[dict], *, max_gap_days: int = 60) -> list[dict]:
    """rows: canonical docs with doc_id, subject_norm, body_norm, date_utc,
    attachment_names. Returns one dict per doc matching the THREADS schema."""
    groups: dict[str, list[dict]] = defaultdict(list)
    singletons: list[dict] = []
    for row in rows:
        if row["subject_norm"]:
            groups[row["subject_norm"]].append(row)
        else:
            singletons.append(row)

    collapsed = {}
    for row in rows:
        collapsed[row["doc_id"]] = collapse_for_containment(row["body_norm"])

    out: list[dict] = []

    def emit(row, thread_id, parent, depth, method):
        out.append(
            {
                "doc_id": row["doc_id"],
                "thread_id": thread_id,
                "parent_doc_id": parent,
                "depth": depth,
                "method": method,
                "is_inclusive": False,
                "inclusive_reason": None,
            }
        )

    for subject in sorted(groups):
        members = sorted(groups[subject], key=_chain_order)
        for segment in _segments(members, max_gap_days):
            if len(segment) == 1:
                emit(segment[0], f"t-{segment[0]['doc_id']}", None, 0, "singleton")
                continue
            thread_id = f"t-{segment[0]['doc_id']}"
            for i, row in enumerate(segment):
                if i == 0:
                    emit(row, thread_id, None, 0, "subject_fallback")
                    continue
                method = (
                    "containment"
                    if any(
                        _contained(collapsed[e["doc_id"]], collapsed[row["doc_id"]])
                        for e in segment[:i]
                    )
                    else "subject_fallback"
                )
                emit(row, thread_id, segment[i - 1]["doc_id"], i, method)
    for row in sorted(singletons, key=_chain_order):
        emit(row, f"t-{row['doc_id']}", None, 0, "singleton")

    mark_inclusive(out, {r["doc_id"]: r for r in rows}, collapsed)
    return out


def mark_inclusive(thread_rows: list[dict], docs: dict[str, dict],
                   collapsed: dict[str, str]) -> None:
    """Set is_inclusive/inclusive_reason in place. Chains mean descendants of
    node i are simply the later nodes of its thread segment."""
    by_thread: dict[str, list[dict]] = defaultdict(list)
    for t in thread_rows:
        by_thread[t["thread_id"]].append(t)
    for members in by_thread.values():
        members.sort(key=lambda t: t["depth"])
        for i, node in enumerate(members):
            descendants = members[i + 1 :]
            doc = docs[node["doc_id"]]
            if not descendants:
                node["is_inclusive"], node["inclusive_reason"] = True, "terminal_node"
                continue
            if not any(
                _contained(collapsed[node["doc_id"]], collapsed[d["doc_id"]])
                for d in descendants
            ):
                node["is_inclusive"], node["inclusive_reason"] = True, "unique_content"
                continue
            later_attachments = {
                name for d in descendants
                for name in docs[d["doc_id"]]["attachment_names"] or []
            }
            own = set(doc["attachment_names"] or [])
            if own - later_attachments:
                node["is_inclusive"], node["inclusive_reason"] = True, "attachment_unique"


def threads_table(thread_rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(thread_rows)
