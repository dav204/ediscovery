"""Exact and near dedup over the messages table.

Exact key = sha256(subject_norm + "\\x00" + body_norm). Body-only clustering is
wrong for this corpus: 45% of the Enron v2 export shares one 345-char
placeholder body (calendar/notes/discussion-thread exports) whose subjects are
the only distinguishing content. Canonical doc per cluster = earliest
date_utc, doc_id as tie-break (null dates sort last); decisions made on the
canonical propagate to the cluster at evaluation time (P5).

Near dedup: MinHash/LSH candidate generation + greedy STAR clustering in
canonical order — each unassigned doc becomes a cluster center and absorbs
only candidates whose estimated jaccard TO THE CENTER clears the threshold.
Single-link union-find is deliberately not used: transitive chaining produced
clusters whose tail members sat far below threshold relative to the canonical
a P5 decision would propagate from (observed: a 618-member chain with median
0.81 vs t=0.85). The invariant here is jaccard_to_canonical >= threshold for
every emitted member. Three exclusions, all config-gated: bodies appearing in
>= boilerplate_body_min_repeats messages (export boilerplate), bodies shorter
than near_dup_min_body_words (jaccard on tiny sets is noise), and bodies
shared verbatim by MULTIPLE canonical docs (their subjects are the only
distinguishing content — exactly what exact dedup deliberately kept apart;
welding them back at jaccard 1.0 would undo that). Seeded from config
(seeds.minhash); every parameter that changes clustering is baked into
minhash_seed_version on every row.
"""

import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone

import pyarrow as pa

_DATE_MAX = datetime.max.replace(tzinfo=timezone.utc)


def dedup_key(subject_norm: str | None, body_norm: str) -> str:
    return hashlib.sha256(
        f"{subject_norm or ''}\x00{body_norm}".encode("utf-8")
    ).hexdigest()


def _canonical_order(row: dict):
    return (row["date_utc"] or _DATE_MAX, row["doc_id"])


def exact_clusters(rows: list[dict]) -> pa.Table:
    """rows: dicts with doc_id, subject_norm, body_norm, date_utc.
    Every doc gets a row; singletons are their own canonical (dup_rank 0)."""
    clusters: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        clusters[dedup_key(row["subject_norm"], row["body_norm"])].append(row)

    out = {"dedup_key": [], "canonical_doc_id": [], "doc_id": [], "dup_rank": []}
    for key in sorted(clusters):
        members = sorted(clusters[key], key=_canonical_order)
        canonical = members[0]["doc_id"]
        for rank, member in enumerate(members):
            out["dedup_key"].append(key)
            out["canonical_doc_id"].append(canonical)
            out["doc_id"].append(member["doc_id"])
            out["dup_rank"].append(rank)
    return pa.table(out)


def shingles(body_norm: str, width: int) -> set[str]:
    words = body_norm.split()
    if len(words) < width:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + width]) for i in range(len(words) - width + 1)}


def near_clusters(rows: list[dict], *, seed: int, num_perm: int, threshold: float,
                  shingle_words: int, min_body_words: int,
                  boilerplate_min_repeats: int,
                  body_freq: Counter | None = None) -> pa.Table:
    """rows: exact-canonical docs (doc_id, body_norm, date_utc). body_freq:
    body_norm occurrence counts over the FULL messages table (pre-dedup), used
    for the boilerplate exclusion. Returns rows only for docs in a near
    cluster of size >= 2."""
    from datasketch import MinHash, MinHashLSH

    body_freq = body_freq or Counter()
    canonical_body_freq = Counter(r["body_norm"] for r in rows)
    eligible = [
        r for r in rows
        if len(r["body_norm"].split()) >= min_body_words
        and body_freq.get(r["body_norm"], 0) < boilerplate_min_repeats
        and canonical_body_freq[r["body_norm"]] == 1
    ]
    eligible.sort(key=lambda r: r["doc_id"])

    mhs: dict[str, "MinHash"] = {}
    lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    for r in eligible:
        mh = MinHash(num_perm=num_perm, seed=seed)
        for s in shingles(r["body_norm"], shingle_words):
            mh.update(s.encode("utf-8"))
        mhs[r["doc_id"]] = mh
        lsh.insert(r["doc_id"], mh)

    # Greedy star clustering in canonical order: processing earliest-first
    # means every center is the earliest member of its own cluster, and every
    # absorbed member clears the threshold against the center itself.
    assigned: set[str] = set()
    clusters: list[tuple[dict, list[tuple[dict, float]]]] = []
    by_id = {r["doc_id"]: r for r in eligible}
    for center in sorted(eligible, key=_canonical_order):
        if center["doc_id"] in assigned:
            continue
        assigned.add(center["doc_id"])
        members: list[tuple[dict, float]] = []
        for other_id in sorted(lsh.query(mhs[center["doc_id"]])):
            if other_id in assigned:
                continue
            j = mhs[other_id].jaccard(mhs[center["doc_id"]])
            if j >= threshold:
                assigned.add(other_id)
                members.append((by_id[other_id], round(j, 6)))
        if members:
            clusters.append((center, members))

    version = (f"seed{seed}-p{num_perm}-w{shingle_words}-t{threshold}"
               f"-mw{min_body_words}-b{boilerplate_min_repeats}")
    out = {"doc_id": [], "cluster_id": [], "canonical_doc_id": [],
           "jaccard_to_canonical": [], "minhash_seed_version": []}
    for center, members in sorted(clusters, key=lambda c: c[0]["doc_id"]):
        canonical = center["doc_id"]
        for doc_id, jaccard in [(canonical, 1.0)] + sorted(
            ((m["doc_id"], j) for m, j in members)
        ):
            out["doc_id"].append(doc_id)
            out["cluster_id"].append(f"near-{canonical}")
            out["canonical_doc_id"].append(canonical)
            out["jaccard_to_canonical"].append(jaccard)
            out["minhash_seed_version"].append(version)
    return pa.table(out)
