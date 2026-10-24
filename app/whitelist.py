"""Read-only whitelist of WhatsApp numbers the agent answers to.

The JSON file is managed by hand and never written by the app. It is reloaded
when its modification time changes; a broken edit keeps the last valid list
(fail closed: never crash, never open up to everyone).
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from app.logging import get_logger, mask_number

log = get_logger(__name__)

_E164 = re.compile(r"^\+\d{8,15}$")


class WhitelistError(Exception):
    pass


@dataclass(frozen=True)
class AllowedUser:
    number: str
    name: str


def normalize_number(raw: str) -> str:
    if not isinstance(raw, str):
        raise TypeError(f"number must be a string, got {type(raw).__name__}")
    number = re.sub(r"[\s.\-]", "", raw.removeprefix("whatsapp:"))
    if not _E164.match(number):
        raise ValueError(f"not an E.164 number: {mask_number(number)}")
    return number


def _load(path: Path) -> dict[str, AllowedUser]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data["allowed_numbers"]
        users = {}
        for entry in entries:
            number = normalize_number(entry["number"])
            users[number] = AllowedUser(number=number, name=entry.get("name") or "?")
        return users
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise WhitelistError(f"invalid whitelist {path}: {e}") from e


class Whitelist:
    def __init__(self, path: Path):
        self._path = path
        self._users = _load(path)
        self._mtime = self._current_mtime()
        log.info("whitelist.loaded", path=str(path), count=len(self._users))

    def _current_mtime(self) -> float | None:
        try:
            return self._path.stat().st_mtime
        except OSError:
            return None

    def _maybe_reload(self) -> None:
        mtime = self._current_mtime()
        if mtime is None or mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            self._users = _load(self._path)
        except WhitelistError as e:
            log.error("whitelist.reload_failed", error=str(e), kept=len(self._users))
            return
        log.info("whitelist.reloaded", count=len(self._users))

    def lookup(self, raw_number: str) -> AllowedUser | None:
        self._maybe_reload()
        try:
            return self._users.get(normalize_number(raw_number))
        except (ValueError, TypeError):
            return None

    def __len__(self) -> int:
        return len(self._users)
