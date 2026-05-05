"""HTTP API: 10-K filing in (CIK+accession or archive URL), per-item JSON out.

Wires `sec_toolbox.fetch.Fetcher` (rate-limited SEC fetch + on-disk cache) to
`extract_agent.run_loop` (LLM agent that emits the per-item JSON artifact).
The route accepts either:

  POST /extract { "cik": "...", "accession": "..." }
  POST /extract { "url": "https://www.sec.gov/Archives/edgar/data/.../file.htm" }

and returns the 16-record list under `items` plus run stats.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, model_validator

from sec_toolbox.cache import DiskCache
from sec_toolbox.client import SECClient
from sec_toolbox.fetch import Fetcher

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


class ExtractRequest(BaseModel):
    cik: str | None = None
    accession: str | None = None
    url: str | None = None

    @model_validator(mode="after")
    def _exactly_one_mode(self) -> ExtractRequest:
        has_pair = bool(self.cik) and bool(self.accession)
        has_url = bool(self.url)
        if has_url and (self.cik or self.accession):
            raise ValueError("provide either url, or cik+accession — not both")
        if not has_url:
            if not (self.cik or self.accession):
                raise ValueError("provide url, or cik+accession")
            if not has_pair:
                raise ValueError("cik and accession must be given together")
        return self


_ARCHIVE_RE = re.compile(
    r"^https?://www\.sec\.gov/Archives/edgar/data/(\d+)/(\d+)/([^/?#]+)$"
)


def _parse_url(url: str) -> tuple[str, str, str]:
    m = _ARCHIVE_RE.match(url)
    if not m:
        raise HTTPException(status_code=400, detail=f"unrecognized SEC archive URL: {url}")
    cik, acc_no_dashes, filename = m.group(1), m.group(2), m.group(3)
    return cik, acc_no_dashes, filename


def _build_fetcher() -> Fetcher:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    cache = DiskCache(root=DATA_ROOT)
    client = SECClient()
    return Fetcher(client=client, cache=cache)


def _resolve_primary_doc(fetcher: Fetcher, cik: str, accession: str) -> str:
    sub_entry = fetcher.submissions(cik=cik)
    sub = json.loads(sub_entry.path.read_text())
    recent = sub.get("filings", {}).get("recent", {}) or {}
    accs = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    target = accession.replace("-", "")
    for a, d in zip(accs, docs, strict=False):
        if a.replace("-", "") == target:
            return d
    raise HTTPException(
        status_code=404,
        detail=f"accession {accession} not found in submissions for CIK {cik}",
    )


async def _run_agent(html_path: Path, out_path: Path) -> dict:
    from extract_agent.config import Config
    from extract_agent.llm import LLMClient
    from extract_agent.loop import run_loop

    cfg = Config.from_env()
    big = LLMClient(base_url=cfg.base_url, model=cfg.big_model, api_key=cfg.api_key)
    small = LLMClient(base_url=cfg.base_url, model=cfg.small_model, api_key=cfg.api_key)
    try:
        return await run_loop(
            html_path=str(html_path),
            out_path=str(out_path),
            cfg=cfg,
            big=big,
            small=small,
        )
    finally:
        await big.aclose()
        await small.aclose()


app = FastAPI(title="SEC 10-K Item Extraction API", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/extract")
async def extract(req: ExtractRequest) -> dict:
    if req.url:
        cik, accession_no_dashes, filename = _parse_url(req.url)
        accession = accession_no_dashes
    else:
        cik = str(req.cik)
        accession = str(req.accession)
        filename = ""

    fetcher = _build_fetcher()
    if not filename:
        filename = _resolve_primary_doc(fetcher, cik, accession)

    arc = fetcher.archive(cik=cik, accession=accession, filename=filename)

    out_dir = DATA_ROOT / "extracted"
    out_dir.mkdir(parents=True, exist_ok=True)
    acc_no_dashes = accession.replace("-", "")
    out_path = out_dir / f"{cik}-{acc_no_dashes}.json"

    result = await _run_agent(arc.path, out_path)

    state = result["state"]
    stats = {
        "status": result["status"],
        "cost_usd": getattr(state, "cost_usd", None),
        "steps": getattr(state, "steps", None),
    }
    if result["status"] != "done":
        raise HTTPException(status_code=500, detail=stats)

    items = json.loads(out_path.read_text())
    return {
        "cik": cik,
        "accession": accession,
        "filename": filename,
        "items": items,
        "stats": stats,
    }
