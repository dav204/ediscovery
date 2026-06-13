import hashlib

import pytest

from pipeline.acquire import _verify_entry, sha256_file

pytestmark = pytest.mark.phase0


def test_sha256_file(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello corpus")
    assert sha256_file(p) == hashlib.sha256(b"hello corpus").hexdigest()


def test_verify_missing(tmp_path):
    assert _verify_entry(tmp_path / "absent.zip", None)["status"] == "missing"


def test_verify_unpinned_records_digest(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"data")
    record = _verify_entry(p, None)
    assert record["status"] == "present_unpinned"
    assert record["sha256"] == hashlib.sha256(b"data").hexdigest()


def test_verify_match_and_mismatch(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"data")
    good = hashlib.sha256(b"data").hexdigest()
    assert _verify_entry(p, good)["status"] == "verified"
    assert _verify_entry(p, "0" * 64)["status"] == "checksum_mismatch"


def test_verify_directory_aggregate(tmp_path):
    d = tmp_path / "collection"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_bytes(b"one")
    (d / "sub" / "b.txt").write_bytes(b"two")
    first = _verify_entry(d, None)
    assert first["status"] == "present_unpinned"
    # Aggregate digest is order-stable and content-sensitive.
    assert _verify_entry(d, first["sha256"])["status"] == "verified"
    (d / "a.txt").write_bytes(b"changed")
    assert _verify_entry(d, first["sha256"])["status"] == "checksum_mismatch"


def test_verify_empty_directory_is_missing(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert _verify_entry(d, None)["status"] == "missing"
