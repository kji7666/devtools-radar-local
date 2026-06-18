import json
import shutil
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RUNS_DIR = BASE_DIR / ".logs" / "runs"

LATEST_RUN_ID = "latest"
LATEST_DIR = RUNS_DIR / LATEST_RUN_ID
LATEST_EVENTS_PATH = LATEST_DIR / "events.jsonl"
LATEST_META_PATH = LATEST_DIR / "meta.json"

CURRENT_RUN_ID: ContextVar[str] = ContextVar("CURRENT_RUN_ID", default=LATEST_RUN_ID)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def make_run_id():
    now = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    return f"{now}-{suffix}"


def get_current_run_id():
    return CURRENT_RUN_ID.get() or LATEST_RUN_ID


def set_current_run_id(run_id: str):
    return CURRENT_RUN_ID.set(run_id or LATEST_RUN_ID)


def reset_current_run_id(token):
    try:
        CURRENT_RUN_ID.reset(token)
    except Exception:
        pass


def run_dir(run_id: str | None = None) -> Path:
    return RUNS_DIR / (run_id or get_current_run_id())


def events_path(run_id: str | None = None) -> Path:
    return run_dir(run_id) / "events.jsonl"


def meta_path(run_id: str | None = None) -> Path:
    return run_dir(run_id) / "meta.json"


def write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def start_run(summary: dict | None = None) -> str:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    run_id = make_run_id()
    now = utc_now()

    meta = {
        "run_id": run_id,
        "started_at": now,
        "updated_at": now,
        "status": "running",
        "summary": summary or {},
    }

    run_dir(run_id).mkdir(parents=True, exist_ok=True)
    events_path(run_id).write_text("", encoding="utf-8")
    write_json(meta_path(run_id), meta)

    LATEST_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_EVENTS_PATH.write_text("", encoding="utf-8")
    write_json(
        LATEST_META_PATH,
        {
            **meta,
            "run_id": LATEST_RUN_ID,
            "latest_alias_for": run_id,
        },
    )

    return run_id


def update_run_meta(run_id: str, **updates):
    path = meta_path(run_id)

    meta = read_json(path)
    if not meta:
        meta = {
            "run_id": run_id,
            "started_at": utc_now(),
        }

    meta.update(updates)
    meta["updated_at"] = utc_now()

    write_json(path, meta)

    latest_meta = read_json(LATEST_META_PATH)
    if latest_meta.get("latest_alias_for") == run_id:
        write_json(
            LATEST_META_PATH,
            {
                **meta,
                "run_id": LATEST_RUN_ID,
                "latest_alias_for": run_id,
            },
        )


def append_event(
    source,
    event_type,
    title,
    preview="",
    level="info",
    payload=None,
    status="ok",
    duration_ms=None,
    run_id=None,
):
    actual_run_id = run_id or get_current_run_id()
    now = utc_now()

    event = {
        "id": "evt_" + uuid.uuid4().hex[:12],
        "ts": now,
        "run_id": actual_run_id,
        "source": source,
        "type": event_type,
        "level": level,
        "title": title,
        "preview": preview,
        "payload": payload or {},
        "status": status,
        "duration_ms": duration_ms,
        "redaction_status": "debug_unmasked",
    }

    target_path = events_path(actual_run_id)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    with target_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

    update_run_meta(
        actual_run_id,
        status="error" if level == "error" or status == "error" else "running",
        last_event_at=now,
    )

    if actual_run_id != LATEST_RUN_ID:
        LATEST_DIR.mkdir(parents=True, exist_ok=True)

        latest_event = {
            **event,
            "latest_alias_for": actual_run_id,
        }

        with LATEST_EVENTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(latest_event, ensure_ascii=False) + "\n")

    return event


def read_events_from_path(path: Path, limit=200):
    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8").splitlines()
    rows = []

    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return rows


def read_recent_events(limit=200):
    return read_events_from_path(LATEST_EVENTS_PATH, limit=limit)


def read_run_events(run_id: str, limit=500):
    if run_id == LATEST_RUN_ID:
        return read_recent_events(limit)

    return read_events_from_path(events_path(run_id), limit=limit)


def export_events_text(run_id: str = LATEST_RUN_ID):
    path = LATEST_EVENTS_PATH if run_id == LATEST_RUN_ID else events_path(run_id)

    if not path.exists():
        return ""

    return path.read_text(encoding="utf-8")


def clear_events(run_id: str = LATEST_RUN_ID):
    if run_id == LATEST_RUN_ID:
        LATEST_DIR.mkdir(parents=True, exist_ok=True)
        LATEST_EVENTS_PATH.write_text("", encoding="utf-8")
        write_json(
            LATEST_META_PATH,
            {
                "run_id": LATEST_RUN_ID,
                "started_at": utc_now(),
                "updated_at": utc_now(),
                "status": "cleared",
                "summary": {},
            },
        )
        return

    target = events_path(run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")
    update_run_meta(run_id, status="cleared")


def get_event_stats(run_id: str = LATEST_RUN_ID):
    events = read_run_events(run_id, limit=100000)

    by_level = {}
    by_type = {}
    by_source = {}

    for event in events:
        level = event.get("level") or "unknown"
        event_type = event.get("type") or "unknown"
        source = event.get("source") or "unknown"

        by_level[level] = by_level.get(level, 0) + 1
        by_type[event_type] = by_type.get(event_type, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1

    return {
        "run_id": run_id,
        "total": len(events),
        "errors": by_level.get("error", 0),
        "model_events": sum(
            count for event_type, count in by_type.items()
            if "model" in event_type
        ),
        "tool_events": sum(
            count for event_type, count in by_type.items()
            if "tool" in event_type or "mcp" in event_type
        ),
        "by_level": by_level,
        "by_type": by_type,
        "by_source": by_source,
    }


def finish_run(run_id: str, status: str = "completed", duration_ms: int | None = None):
    update_run_meta(
        run_id,
        status=status,
        finished_at=utc_now(),
        duration_ms=duration_ms,
        stats=get_event_stats(run_id),
    )


def list_runs(limit=50):
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    rows = []

    for child in RUNS_DIR.iterdir():
        if not child.is_dir():
            continue

        if child.name == LATEST_RUN_ID:
            continue

        meta = read_json(child / "meta.json")
        if not meta:
            continue

        stats = get_event_stats(child.name)

        rows.append(
            {
                "run_id": child.name,
                "started_at": meta.get("started_at", ""),
                "updated_at": meta.get("updated_at", ""),
                "finished_at": meta.get("finished_at", ""),
                "status": meta.get("status", "unknown"),
                "duration_ms": meta.get("duration_ms"),
                "summary": meta.get("summary", {}),
                "stats": stats,
            }
        )

    rows.sort(key=lambda item: item.get("started_at", ""), reverse=True)

    return rows[:limit]


def delete_run(run_id: str):
    if run_id == LATEST_RUN_ID:
        clear_events(LATEST_RUN_ID)
        return

    target = run_dir(run_id)
    if target.exists():
        shutil.rmtree(target)