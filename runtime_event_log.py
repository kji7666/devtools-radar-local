import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / ".logs" / "runs" / "latest"
EVENTS_PATH = LOG_DIR / "events.jsonl"

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def append_event(source, event_type, title, preview="", level="info", payload=None, status="ok", duration_ms=None):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    event = {
        "id": "evt_" + uuid.uuid4().hex[:12],
        "ts": utc_now(),
        "run_id": "latest",
        "source": source,
        "type": event_type,
        "level": level,
        "title": title,
        "preview": preview,
        "payload": payload or {},
        "status": status,
        "duration_ms": duration_ms,
        "redaction_status": "masked"
    }
    with EVENTS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event

def read_recent_events(limit=200):
    if not EVENTS_PATH.exists():
        return []
    lines = EVENTS_PATH.read_text(encoding="utf-8").splitlines()
    rows = []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows

def clear_events():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    EVENTS_PATH.write_text("", encoding="utf-8")