"""Generate verbatim v1 review protocols from official topic text.

A v1 protocol is the official production request pasted word for word, wrapped
in fixed boilerplate that is identical for every topic (reviewer role, how to
read the document, when to use borderline, the JSON output contract). It adds
no topic-specific interpretation, which keeps v1 comparable to the 2023
GPT-3.5 baseline and keeps the model from being tuned toward the qrels.

v2+ rewrites informed by dev-set results need Dan's sign-off. A signed-off
rewrite whose wording is topic-independent lives here as a template too (the
Bush breadth v2, approved 2026-10-23 from docs/drafts/athome-v2-breadth.md),
so every topic's file is the same verbatim request pasted into approved
boilerplate. Nothing here ever overwrites an existing protocol file.
"""

import sys
from pathlib import Path

from ..config import CONFIG_DIR, REPO_ROOT, _load_yaml, load_pipeline_config
from ..qrels.parse import TOPICS_FILE

GENERATED_MARKER = "<!-- generated: verbatim v1 from official topic text (pipeline protocol) -->"

V1_TEMPLATE = """\
{marker}
# {corpus_label} topic {topic}: {title}

You are a document reviewer in a civil litigation document review. Decide whether
the document below is responsive to this production request:

"{request}"

## How to decide

- Judge the document against the production request exactly as written.
- Review the email metadata and body. Attachments are listed by name only; do not
  infer their contents beyond what the name and the email text say.
- Answer "responsive" if the document falls within the request, "not_responsive" if
  it does not, and "borderline" only if you genuinely cannot decide from the request
  as written.
- Give a confidence between 0 and 1 for your decision.

## Output

Reply with a single JSON object and nothing else:
{{"decision": "responsive" | "not_responsive" | "borderline", "confidence": <number from 0 to 1>, "rationale": "<at most two sentences in your own words; never quote the document>"}}
"""

V2_BREADTH_MARKER = ("<!-- generated: v2 breadth template, Dan approved 2026-10-23 "
                     "(docs/drafts/athome-v2-breadth.md) -->")

V2_BREADTH_TEMPLATE = """\
{marker}
# {corpus_label} topic {topic}: {title}

You are a first-pass document reviewer in a civil litigation document review. Decide
whether the document below is responsive to this production request:

"{request}"

## How to decide

- Read "concerning" broadly. A document is responsive if any part of it relates to the
  subject of the request in a substantive way, even if that is not its main topic.
  Scheduling, logistics, press, constituent views, and internal discussion all count
  when they are about the subject.
- Any part of the document counts: the body, the subject line, quoted or forwarded
  earlier messages, and attachments listed by name.
- Not responsive: no connection to the subject, or a word in common with no
  substantive connection.
- The production standard favors inclusion. If you are unsure whether the document
  concerns the subject, answer "responsive".
- Answer "borderline" only if even this broad reading genuinely cannot decide.
- Give a confidence between 0 and 1 for your decision.

## Output

Reply with a single JSON object and nothing else:
{{"decision": "responsive" | "not_responsive" | "borderline", "confidence": <number from 0 to 1>, "rationale": "<at most two sentences in your own words; never quote the document>"}}
"""

CORPUS_LABELS = {"bush": "Jeb Bush email (TREC 2016 Total Recall athome4)",
                 "enron": "Enron (TREC 2010 Legal Learning task)"}


def protocol_stem(corpus: str, topic: str) -> str:
    """Must match review.run.resolve_protocol's lookup."""
    return f"athome{topic}" if corpus == "bush" else f"topic{topic}"


def render_v1(corpus: str, topic: str, title: str, request: str) -> str:
    request = " ".join(request.split())
    if not request:
        raise ValueError(f"{corpus}/{topic}: empty request text")
    return V1_TEMPLATE.format(
        marker=GENERATED_MARKER, corpus_label=CORPUS_LABELS[corpus],
        topic=topic, title=title.strip(), request=request,
    )


def render_v2_breadth(corpus: str, topic: str, title: str, request: str) -> str:
    request = " ".join(request.split())
    if not request:
        raise ValueError(f"{corpus}/{topic}: empty request text")
    return V2_BREADTH_TEMPLATE.format(
        marker=V2_BREADTH_MARKER, corpus_label=CORPUS_LABELS[corpus],
        topic=topic, title=title.strip(), request=request,
    )


def write_v2_breadth(protocols_root: Path, corpus: str, topic: str, title: str,
                     request: str) -> Path | None:
    """Write <stem>.v2.md next to an existing v1; None (untouched) when v1 is
    missing or any v2+ already exists."""
    stem = protocol_stem(corpus, topic)
    corpus_dir = Path(protocols_root) / corpus
    if not (corpus_dir / f"{stem}.v1.md").exists():
        return None
    existing = [p for p in corpus_dir.glob(f"{stem}.v*.md") if not p.name.endswith(".v1.md")]
    if existing:
        return None
    path = corpus_dir / f"{stem}.v2.md"
    path.write_text(render_v2_breadth(corpus, topic, title, request))
    return path


def read_bush_topics(path: Path) -> dict[str, tuple[str, str]]:
    """topic -> (title, request) from athome4.topics.sample
    (`<topic> <n> <title> -- <request>`)."""
    topics = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        topic, _n, rest = line.split(maxsplit=2)
        title, sep, request = rest.partition(" -- ")
        if not sep:
            raise ValueError(f"{path}: no ' -- ' separator on topic {topic}")
        topics[topic] = (title.strip(), request.strip())
    if not topics:
        raise ValueError(f"{path}: no topics found")
    return topics


def read_enron_topics(config_dir: Path = CONFIG_DIR) -> dict[str, tuple[str, str]]:
    """topic -> (title, request) from config/corpora/enron.yaml `topic_statements`
    (verbatim TREC 2009 Legal wording; see the comment there for provenance)."""
    raw = _load_yaml(Path(config_dir) / "corpora" / "enron.yaml")
    statements = raw.get("topic_statements") or {}
    if not statements:
        raise ValueError("enron.yaml has no topic_statements")
    return {str(t): (s["title"], s["request"]) for t, s in statements.items()}


def write_v1(protocols_root: Path, corpus: str, topic: str, title: str,
             request: str) -> Path | None:
    """Write protocols/<corpus>/<stem>.v1.md; returns None (untouched) if any
    version of this topic's protocol already exists."""
    stem = protocol_stem(corpus, topic)
    corpus_dir = Path(protocols_root) / corpus
    if any(corpus_dir.glob(f"{stem}.v*.md")):
        return None
    corpus_dir.mkdir(parents=True, exist_ok=True)
    path = corpus_dir / f"{stem}.v1.md"
    path.write_text(render_v1(corpus, topic, title, request))
    return path


def main(args) -> int:
    if args.corpus == "bush":
        cfg = load_pipeline_config()
        topics = read_bush_topics(cfg.paths["raw"] / "bush" / TOPICS_FILE)
    else:
        topics = read_enron_topics()
    wanted = [args.topic] if args.topic else sorted(topics)
    missing = [t for t in wanted if t not in topics]
    if missing:
        print(f"protocol: no official topic text for {args.corpus} topics {missing}",
              file=sys.stderr)
        return 2
    for topic in wanted:
        title, request = topics[topic]
        writer = write_v2_breadth if getattr(args, "v2_breadth", False) else write_v1
        path = writer(REPO_ROOT / "protocols", args.corpus, topic, title, request)
        print(f"  {topic}: {'wrote ' + str(path.relative_to(REPO_ROOT)) if path else 'exists, skipped'}")
    return 0
