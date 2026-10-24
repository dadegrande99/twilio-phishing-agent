import json
import os
import time

import pytest
import structlog

from app.whitelist import AllowedUser, Whitelist, WhitelistError, normalize_number


def write(path, numbers):
    path.write_text(json.dumps({"allowed_numbers": numbers}))


def touch_later(path):
    future = time.time() + 5
    os.utime(path, (future, future))


@pytest.fixture
def wl_path(tmp_path):
    path = tmp_path / "w.json"
    write(path, [{"number": "+393331234567", "name": "Davide"}])
    return path


def test_lookup_strips_whatsapp_prefix(wl_path):
    assert Whitelist(wl_path).lookup("whatsapp:+393331234567") == AllowedUser(
        "+393331234567", "Davide"
    )


def test_unknown_number(wl_path):
    assert Whitelist(wl_path).lookup("+391111111111") is None


def test_garbage_number_returns_none(wl_path):
    assert Whitelist(wl_path).lookup("whatsapp:abc") is None


def test_entry_with_spaces_normalized(tmp_path):
    path = tmp_path / "w.json"
    write(path, [{"number": "+39 333 123-4567", "name": "A"}])
    assert Whitelist(path).lookup("+393331234567").name == "A"


def test_len(wl_path):
    assert len(Whitelist(wl_path)) == 1


@pytest.mark.parametrize("raw", ["333", "393331234567", "+39abc", ""])
def test_normalize_rejects_invalid(raw):
    with pytest.raises(ValueError):
        normalize_number(raw)


def test_missing_file_fails_closed(tmp_path):
    with pytest.raises(WhitelistError):
        Whitelist(tmp_path / "nope.json")


def test_invalid_json_fails_closed(tmp_path):
    path = tmp_path / "w.json"
    path.write_text("{ broken")
    with pytest.raises(WhitelistError):
        Whitelist(path)


def test_missing_key_fails_closed(tmp_path):
    path = tmp_path / "w.json"
    path.write_text(json.dumps({"numbers": []}))
    with pytest.raises(WhitelistError):
        Whitelist(path)


def test_invalid_number_in_file_fails(tmp_path):
    path = tmp_path / "w.json"
    write(path, [{"number": "333", "name": "x"}])
    with pytest.raises(WhitelistError):
        Whitelist(path)


def test_hot_reload(wl_path):
    wl = Whitelist(wl_path)
    write(wl_path, [{"number": "+393409876543", "name": "B"}])
    touch_later(wl_path)
    with structlog.testing.capture_logs() as logs:
        assert wl.lookup("+393409876543").name == "B"
    assert wl.lookup("+393331234567") is None
    assert any(e["event"] == "whitelist.reloaded" and e["count"] == 1 for e in logs)


def test_broken_reload_keeps_previous_list(wl_path):
    wl = Whitelist(wl_path)
    wl_path.write_text("{ broken")
    touch_later(wl_path)
    with structlog.testing.capture_logs() as logs:
        assert wl.lookup("+393331234567").name == "Davide"
    assert any(e["event"] == "whitelist.reload_failed" for e in logs)


def test_deleted_file_keeps_previous_list(wl_path):
    wl = Whitelist(wl_path)
    wl_path.unlink()
    assert wl.lookup("+393331234567").name == "Davide"


def test_non_string_number_fails_closed(tmp_path):
    path = tmp_path / "w.json"
    write(path, [{"number": 393331234567, "name": "x"}])
    with pytest.raises(WhitelistError):
        Whitelist(path)


def test_non_string_number_on_reload_logs_failure(wl_path):
    wl = Whitelist(wl_path)
    write(wl_path, [{"number": 393409876543, "name": "B"}])
    touch_later(wl_path)
    with structlog.testing.capture_logs() as logs:
        assert wl.lookup("+393331234567").name == "Davide"
    assert any(e["event"] == "whitelist.reload_failed" for e in logs)


def test_reload_failure_does_not_log_raw_number(wl_path):
    wl = Whitelist(wl_path)
    write(wl_path, [{"number": "+39 333 12345x", "name": "B"}])
    touch_later(wl_path)
    with structlog.testing.capture_logs() as logs:
        wl.lookup("+393331234567")
    assert "12345x" not in str(logs)
