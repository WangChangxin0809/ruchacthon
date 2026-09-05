"""Write-only secret store: a 0600 JSON file next to the database, keyed by
name. The UI can set, replace and delete a secret and can ask whether one is
set; nothing ever hands a value back out. Runs resolve a profile's
`credential_ref` here first, then in the server's environment -- the
environment stays the way to inject secrets without touching the UI.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from .db import DATA_DIR

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


class SecretStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else DATA_DIR / "secrets.json"
        self._lock = threading.Lock()

    def _load(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        try:
            return json.loads(self.path.read_text()) or {}
        except ValueError:
            return {}

    def _save(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def set(self, name: str, value: str) -> None:
        if not NAME_RE.match(name):
            raise ValueError("secret name must be letters/digits/_ . - and start with a letter")
        if not value:
            raise ValueError("empty secret")
        with self._lock:
            data = self._load()
            data[name] = value
            self._save(data)

    def delete(self, name: str) -> bool:
        with self._lock:
            data = self._load()
            if name not in data:
                return False
            del data[name]
            self._save(data)
            return True

    def get(self, name: str | None) -> str | None:
        """Store first, then the process environment."""
        if not name:
            return None
        v = self._load().get(name)
        return v if v else os.environ.get(name) or None

    def is_set(self, name: str | None) -> bool:
        return bool(self.get(name))

    def names(self) -> list[dict]:
        with self._lock:
            return [{"name": k, "source": "store"} for k in sorted(self._load())]
