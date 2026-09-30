"""Generate verbatim v1 review protocols from official topic text.

A v1 protocol is the official production request pasted word for word, wrapped
in fixed boilerplate that is identical for every topic (reviewer role, how to
read the document, when to use borderline, the JSON output contract). It adds
no topic-specific interpretation, which keeps v1 comparable to the 2023
GPT-3.5 baseline and keeps the model from being tuned toward the qrels.

Only v1 is generated. v2+ rewrites informed by dev-set results are
human-approved (protocols/README.md), so this module refuses to overwrite any
existing protocol file and never writes a version other than v1.
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
        path = write_v1(REPO_ROOT / "protocols", args.corpus, topic, title, request)
        print(f"  {topic}: {'wrote ' + str(path.relative_to(REPO_ROOT)) if path else 'exists, skipped'}")
    return 0
