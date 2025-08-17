import os
import json
import time
from typing import List, Dict, Optional


class ChatMemory:
    """
    Persistent chat memory backed by rolling JSONL files per session.

    - Each message is appended as a JSON line: {"role": str, "content": str, "ts": float}
    - Sessions are stored under a directory (default: ".sessions").
    """

    def __init__(self, session_id: Optional[str] = None, storage_dir: str = ".sessions"):
        self.storage_dir = storage_dir
        self.session_id = session_id or self._generate_session_id()
        self.messages: List[Dict[str, str]] = []

        os.makedirs(self.storage_dir, exist_ok=True)
        self._filepath = os.path.join(self.storage_dir, f"{self.session_id}.jsonl")

        # If a file already exists for this session, load it
        if os.path.exists(self._filepath):
            self._load_from_file()

    # ----- Public API -----
    def add(self, role: str, content: str):
        msg = {"role": role, "content": content, "ts": time.time()}
        self.messages.append(msg)
        self._append_line(msg)

    def get(self) -> List[Dict[str, str]]:
        return self.messages

    def save(self):
        """Re-write the JSONL file from in-memory messages (rarely needed)."""
        with open(self._filepath, "w", encoding="utf-8") as f:
            for m in self.messages:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

    @classmethod
    def load(cls, session_id: str, storage_dir: str = ".sessions") -> "ChatMemory":
        """Load an existing session by ID (creates if not exists)."""
        return cls(session_id=session_id, storage_dir=storage_dir)

    @classmethod
    def new_session(cls, session_id: Optional[str] = None, storage_dir: str = ".sessions") -> "ChatMemory":
        """Create a new session with a generated or provided ID."""
        sid = session_id or cls._generate_session_id()
        # Ensure a fresh file (truncate if existed)
        os.makedirs(storage_dir, exist_ok=True)
        filepath = os.path.join(storage_dir, f"{sid}.jsonl")
        with open(filepath, "w", encoding="utf-8") as _:
            pass
        return cls(session_id=sid, storage_dir=storage_dir)

    # ----- Internals -----
    @staticmethod
    def _generate_session_id() -> str:
        # e.g., 20250817-171530
        return time.strftime("%Y%m%d-%H%M%S")

    def _append_line(self, msg: Dict[str, str]):
        try:
            with open(self._filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        except Exception:
            # Fail silently to avoid breaking the REPL; in-memory still works
            pass

    def _load_from_file(self):
        try:
            with open(self._filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        # Backward compat for old schema without ts
                        if "ts" not in obj:
                            obj["ts"] = time.time()
                        self.messages.append(obj)
                    except Exception:
                        continue
        except FileNotFoundError:
            pass