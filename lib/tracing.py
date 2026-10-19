import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Tracer:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path or os.getenv("TRACE_PATH", "traces/negotiation.jsonl"))
        self.events: list[dict[str, Any]] = []

    def log(self, sender: str, receiver: str, type: str, payload: Any) -> dict[str, Any]:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sender": sender,
            "receiver": receiver,
            "type": type,
            "payload": payload,
        }
        self.events.append(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, default=str) + "\n")
        return event
