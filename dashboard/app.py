from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import threading
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from automation.self_healing import run_self_healing
from core.config import Settings, load_settings
from dashboard.service import artifact_signature, build_snapshot, _read_recent_events


class FailureDrillRequest(BaseModel):
    trigger: str = "dashboard failure drill"


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or load_settings()
    application = FastAPI(title="HAKI Data Control Center", docs_url=None, redoc_url=None)
    application.state.settings = resolved_settings
    application.state.repair_source = "auto"
    application.state.failure_drill_lock = threading.Lock()
    application.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
        name="static",
    )

    @application.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(Path(__file__).resolve().parent / "templates" / "index.html")

    @application.get("/api/dashboard/snapshot")
    async def snapshot() -> dict:
        return build_snapshot(application.state.settings)

    @application.get("/api/dashboard/events")
    async def events(request: Request) -> StreamingResponse:
        event_path = application.state.settings.paths.self_healing_events_jsonl
        settings_for_stream = application.state.settings
        initial_events = _read_recent_events(event_path, limit=500)
        requested_cursor = request.headers.get("last-event-id")
        initial_ids = [event.get("event_id") for event in initial_events]
        if requested_cursor in initial_ids:
            audit_cursor = requested_cursor
        else:
            audit_cursor = initial_ids[-1] if initial_ids else None
        artifact_cursor = artifact_signature(settings_for_stream)

        async def stream():
            nonlocal audit_cursor, artifact_cursor
            heartbeat_at = asyncio.get_running_loop().time()
            while not await request.is_disconnected():
                current_events = _read_recent_events(event_path, limit=500)
                current_ids = [event.get("event_id") for event in current_events]
                if audit_cursor in current_ids:
                    cursor_index = current_ids.index(audit_cursor) + 1
                    new_events = current_events[cursor_index:]
                else:
                    new_events = current_events[-1:] if current_events else []
                for event in new_events:
                    audit_cursor = event.get("event_id")
                    yield (
                        f"id: {audit_cursor}\n"
                        "event: pipeline\n"
                        f"data: {json.dumps(event, ensure_ascii=True)}\n\n"
                    )

                current_signature = artifact_signature(settings_for_stream)
                if current_signature != artifact_cursor:
                    artifact_cursor = current_signature
                    artifact_id = f"artifact-{datetime.now(UTC).timestamp():.6f}"
                    yield (
                        "event: artifact\n"
                        f"data: {json.dumps({'event_id': artifact_id, 'state': 'ARTIFACT_UPDATED', 'timestamp': datetime.now(UTC).isoformat()})}\n\n"
                    )

                loop = asyncio.get_running_loop()
                if loop.time() - heartbeat_at >= 15:
                    heartbeat_at = loop.time()
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.75)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    @application.post("/api/failure-drill", status_code=202)
    async def start_failure_drill(payload: FailureDrillRequest | None = None) -> dict[str, str]:
        lock = application.state.failure_drill_lock
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="A failure drill is already running.")
        run_id = uuid.uuid4().hex

        def worker() -> None:
            try:
                run_self_healing(
                    application.state.settings,
                    trigger=payload.trigger if payload else "dashboard failure drill",
                    repair_source=application.state.repair_source,
                    run_id=run_id,
                )
            finally:
                lock.release()

        try:
            threading.Thread(target=worker, name=f"self-healing-{run_id[:8]}", daemon=True).start()
        except Exception:
            lock.release()
            raise
        return {"run_id": run_id, "state": "CORRUPTING", "message": "Failure drill started."}

    return application


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the HAKI Data Control Center dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: localhost only).")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--repair-source", choices=("auto", "snapshot", "live"), default="auto")
    args = parser.parse_args()
    app.state.repair_source = args.repair_source

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
