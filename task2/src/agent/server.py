from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agent.browser_session import BrowserSession
from agent.config import Config
from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.notes_store import NotesStore
from agent.tools.browser import build_browser_tool_list
from agent.tools.meta import QuestionChannel, build_meta_tool_list
from agent.tools.registry import ToolRegistry
from agent.trace import TraceWriter, read_trace


def build_app(*, cfg: Config, data_dir: Path, llm_transport: Any = None) -> FastAPI:
    app = FastAPI()
    here = Path(__file__).parent
    templates = Jinja2Templates(directory=str(here / "templates"))
    app.mount("/static", StaticFiles(directory=str(here / "static")), name="static")
    sem = asyncio.Semaphore(1)
    notes = NotesStore(data_dir / "url_notes.db", query_strip=cfg.url_note_query_strip)

    async def run_loop(goal: str, send_event, ask_user) -> dict:
        async with sem:
            session_id = uuid.uuid4().hex
            (data_dir / "traces").mkdir(parents=True, exist_ok=True)
            trace = TraceWriter(data_dir / "traces" / f"{session_id}.jsonl")

            orig_write = trace.write

            def write_and_send(ev):
                orig_write(ev)
                asyncio.create_task(send_event(ev))

            trace.write = write_and_send  # type: ignore[assignment]

            trace.write(
                {
                    "type": "session_started",
                    "payload": {
                        "sid": session_id,
                        "goal": goal,
                        "started_at": datetime.now(UTC).isoformat(),
                    },
                }
            )

            browser = BrowserSession()
            await browser.start()
            try:
                qc = QuestionChannel()
                agent_llm = LLMClient(
                    cfg.agent_model_base_url,
                    cfg.agent_model_name,
                    api_key=cfg.agent_api_key,
                    transport=llm_transport,
                )
                reg = ToolRegistry()
                loop_holder: list = []
                reason_log: list[str] = []

                def allowlist_sources():
                    if not loop_holder:
                        return [goal]
                    return (
                        [goal]
                        + [step.get("obs", "") for step in loop_holder[0].tape]
                        + [browser.page.url or ""]
                    )

                for t in build_browser_tool_list(
                    browser,
                    restrict_goto=cfg.restrict_goto,
                    allowlist_sources=allowlist_sources,
                ):
                    reg.register(t)
                for t in build_meta_tool_list(
                    question_channel=qc,
                    reason_log=reason_log,
                ):
                    reg.register(t)

                async def question_pump():
                    while True:
                        await asyncio.sleep(0.05)
                        q = qc.pending()
                        if q:
                            await send_event({"type": "question", "payload": {"question": q}})
                            ans = await ask_user()
                            qc.answer(ans)

                pump_task = asyncio.create_task(question_pump())
                try:
                    loop = ReactLoop(
                        llm=agent_llm,
                        registry=reg,
                        notes=notes,
                        trace=trace,
                        browser=browser,
                        question_channel=qc,
                        max_steps=cfg.max_steps,
                        send_transient=send_event,
                        small_diff_threshold=cfg.small_diff_threshold,
                        max_auto_advance_hops=cfg.max_auto_advance_hops,
                        diff_inject_max_lines=cfg.diff_inject_max_lines,
                        reason_log=reason_log,
                    )
                    loop_holder.append(loop)
                    return await loop.run(goal)
                finally:
                    pump_task.cancel()
            finally:
                await browser.close()

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html")

    @app.get("/api/sessions")
    async def list_sessions():
        traces_dir = data_dir / "traces"
        if not traces_dir.exists():
            return []
        out = []
        for p in traces_dir.glob("*.jsonl"):
            sid = p.stem
            goal = ""
            started_at = ""
            status = "running"
            for line in p.read_text().splitlines():
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("type") == "session_started":
                    goal = ev["payload"].get("goal", "")
                    started_at = ev["payload"].get("started_at", "")
                elif ev.get("type") == "done":
                    status = ev["payload"].get("status", status)
            out.append({"sid": sid, "goal": goal, "started_at": started_at, "status": status})
        out.sort(key=lambda s: s["started_at"] or "", reverse=True)
        return out

    @app.get("/api/llm_health")
    async def llm_health():
        url = cfg.agent_model_base_url.rstrip("/") + "/models"
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=2.0, transport=llm_transport) as c:
                r = await c.get(url)
                r.raise_for_status()
            return {"llm": "up", "latency_ms": int((time.perf_counter() - t0) * 1000)}
        except Exception:
            return {"llm": "down", "latency_ms": int((time.perf_counter() - t0) * 1000)}

    @app.get("/api/trace/{sid}")
    async def get_trace(sid: str):
        p = data_dir / "traces" / f"{sid}.jsonl"
        if not p.exists():
            raise HTTPException(status_code=404, detail="not found")
        return read_trace(p)

    @app.post("/api/run_sync")
    async def run_sync(payload: dict):
        async def send_event(ev):
            return None

        async def ask_user():
            return ""

        return await run_loop(payload["goal"], send_event, ask_user)

    @app.websocket("/ws")
    async def ws(ws: WebSocket):
        await ws.accept()
        try:
            first = await ws.receive_json()
            assert first["type"] == "goal"
            answer_q: asyncio.Queue[str] = asyncio.Queue()

            async def send_event(ev):
                await ws.send_json(ev)

            async def ask_user():
                return await answer_q.get()

            run_task = asyncio.create_task(run_loop(first["goal"], send_event, ask_user))
            while not run_task.done():
                try:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=0.1)
                    if msg.get("type") == "answer":
                        await answer_q.put(msg.get("text", ""))
                except TimeoutError:
                    pass
                except WebSocketDisconnect:
                    run_task.cancel()
                    break
            result = await run_task
            await ws.send_json({"type": "result", "payload": result})
        except WebSocketDisconnect:
            pass

    return app


def app_factory() -> FastAPI:
    return build_app(
        cfg=Config.from_env(),
        data_dir=Path(os.getenv("DATA_DIR", "data")),
    )
