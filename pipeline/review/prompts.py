"""Protocol loading and prompt rendering.

Protocols are human-edited markdown files named `<name>.v<N>.md` (see
protocols/README.md). The pipeline treats them as opaque instructions and
records both the version (from the file name) and a content hash with every
decision, so a content edit without a version bump is detectable.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_VERSION_RE = re.compile(r"^(?P<stem>.+)\.v(?P<num>\d+)$")


@dataclass(frozen=True)
class Protocol:
    name: str  # e.g. "topic201"
    version: str  # e.g. "topic201.v1"
    content: str
    content_hash: str  # sha256[:12] of content


def load_protocol(path: Path) -> Protocol:
    path = Path(path)
    m = _VERSION_RE.match(path.stem)
    if not m:
        raise ValueError(f"protocol file {path.name!r} must be named <name>.v<N>.md")
    content = path.read_text()
    if not content.strip():
        raise ValueError(f"protocol {path} is empty")
    return Protocol(
        name=m.group("stem"),
        version=path.stem,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest()[:12],
    )


DOC_TEMPLATE = """\
<document>
<metadata>
custodian: {custodian}
from: {from_addr}
to: {to}
cc: {cc}
date: {date_raw}
subject: {subject}
attachments: {attachment_names}
</metadata>
<body>
{body_text}
</body>
</document>"""


def render_document(doc: dict) -> str:
    return DOC_TEMPLATE.format(
        custodian=doc.get("custodian", ""),
        from_addr=doc.get("from_addr", ""),
        to="; ".join(doc.get("to") or []),
        cc="; ".join(doc.get("cc") or []),
        date_raw=doc.get("date_raw", ""),
        subject=doc.get("subject", ""),
        attachment_names="; ".join(doc.get("attachment_names") or []),
        body_text=doc.get("body_text", ""),
    )


def render_messages(protocol: Protocol, doc: dict) -> list[dict]:
    """Anthropic messages payload: protocol as system-like first turn, doc after.

    The protocol text is identical across all docs in a run, which makes it a
    prompt-caching prefix candidate when batches are built.
    """
    return [{"role": "user", "content": f"{protocol.content}\n\n{render_document(doc)}"}]
