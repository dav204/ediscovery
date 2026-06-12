"""Shared fixtures. Hard rule: no network in tests, ever."""

import json
import socket

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is forbidden in tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)


class MockBatchClient:
    """Offline stand-in for the Anthropic Message Batches API.

    Behaviour is scripted per custom_id via `script`: each value is either a
    dict {"decision", "confidence", "rationale"} (returned as JSON text), a
    raw string (returned verbatim, e.g. to test unparseable output), or one of
    the sentinel strings "ERRORED"/"EXPIRED".
    Status returns "in_progress" for `pending_polls` calls before "ended".
    """

    def __init__(self, script: dict | None = None, pending_polls: int = 0,
                 tokens=(100, 20)):
        self.script = script or {}
        self.pending_polls = pending_polls
        self.tokens = tokens
        self.created_batches: list[list[dict]] = []
        self._polls: dict[str, int] = {}

    def create(self, requests):
        self.created_batches.append(requests)
        return f"batch_{len(self.created_batches):03d}"

    def status(self, batch_id):
        self._polls[batch_id] = self._polls.get(batch_id, 0) + 1
        return "ended" if self._polls[batch_id] > self.pending_polls else "in_progress"

    def results(self, batch_id):
        index = int(batch_id.split("_")[1]) - 1
        for req in self.created_batches[index]:
            custom_id = req["custom_id"]
            action = self.script.get(
                custom_id,
                {"decision": "not_responsive", "confidence": 0.9, "rationale": "default"},
            )
            if action == "ERRORED":
                yield {"custom_id": custom_id, "type": "errored"}
            elif action == "EXPIRED":
                yield {"custom_id": custom_id, "type": "expired"}
            else:
                text = action if isinstance(action, str) else json.dumps(action)
                yield {
                    "custom_id": custom_id,
                    "type": "succeeded",
                    "text": text,
                    "usage": {
                        "input_tokens": self.tokens[0],
                        "output_tokens": self.tokens[1],
                    },
                }


@pytest.fixture
def mock_client_factory():
    return MockBatchClient
