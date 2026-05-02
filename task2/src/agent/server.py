from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agent.browser_session import BrowserSession
from agent.config import Config
from agent.llm import LLMClient
from agent.loop import ReactLoop
from agent.notes_store import NotesStore
from agent.summarizer import Summarizer
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
            browser = BrowserSession()
            await browser.start()
            try:
                qc = QuestionChannel()
                agent_llm = LLMClient(
                    cfg.agent_model_base_url,
                    cfg.agent_model_name,
                    transport=llm_transport,
                )
                summ_llm = LLMClient(
                    cfg.summarizer_model_base_url,
                    cfg.summarizer_model_name,
                    transport=llm_transport,
                )
                summarizer = Summarizer(summ_llm, notes)
                reg = ToolRegistry()
                for t in build_browser_tool_list(browser):
                    reg.register(t)
                for t in build_meta_tool_list(
                    notes=notes,
                    current_url=lambda: browser.page.url,
                    question_channel=qc,
                ):
                    reg.register(t)

                orig_write = trace.write

                def write_and_send(ev):
                    orig_write(ev)
                    asyncio.create_task(send_event(ev))

                trace.write = write_and_send  # type: ignore[assignment]

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
                        summarizer=summarizer,
                        trace=trace,
                        browser=browser,
                        question_channel=qc,
                        max_steps=cfg.max_steps,
                    )
                    return await loop.run(goal)
                finally:
                    pump_task.cancel()
            finally:
                await browser.close()

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse("index.html", {"request": request})

    @app.get("/replay", response_class=HTMLResponse)
    async def replay_index(request: Request):
        traces_dir = data_dir / "traces"
        sessions = (
            sorted([p.stem for p in traces_dir.glob("*.jsonl")], reverse=True)
            if traces_dir.exists()
            else []
        )
        return templates.TemplateResponse(
            "replay.html",
            {
                "request": request,
                "sessions": sessions,
                "selected": None,
                "events": [],
            },
        )

    @app.get("/replay/{sid}", response_class=HTMLResponse)
    async def replay_one(request: Request, sid: str):
        traces_dir = data_dir / "traces"
        sessions = sorted([p.stem for p in traces_dir.glob("*.jsonl")], reverse=True)
        events = read_trace(traces_dir / f"{sid}.jsonl")
        return templates.TemplateResponse(
            "replay.html",
            {
                "request": request,
                "sessions": sessions,
                "selected": sid,
                "events": events,
            },
        )

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
