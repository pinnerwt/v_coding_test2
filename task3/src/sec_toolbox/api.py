"""FastAPI surface for Task 3.

Single endpoint: ``POST /extract``. Body either supplies CIK + accession +
primary filename (server fetches via the existing :class:`Fetcher`, hitting
the on-disk cache when warm), or a pre-cached HTML byte path is resolved
through the same fetcher. Returns ``{items, verification}`` where ``items``
matches the brief schema and ``verification`` is the schedule-check output.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .cache import DiskCache
from .client import SECClient
from .extract import extract
from .fetch import Fetcher
from .verify import verify_schedule

_DATA_ROOT = Path("data")


class ExtractRequest(BaseModel):
    cik: str = Field(..., description="SEC CIK (with or without leading zeros)")
    accession: str = Field(..., description="Accession number, with or without dashes")
    filename: str = Field(..., description="Primary filename inside the filing")
    fiscal_year: int = Field(..., description="Fiscal year of the filing")


def _fetch_filing_bytes(cik: str, accession: str, filename: str) -> tuple[bytes, str]:
    """Fetch the filing's primary document bytes via the cached Fetcher.

    Indirection point for tests: monkeypatching this swaps the network
    backend without standing up a fake httpx layer.
    """
    cache = DiskCache(root=_DATA_ROOT)
    client = SECClient()
    try:
        fetcher = Fetcher(client=client, cache=cache)
        entry = fetcher.archive(cik=cik, accession=accession, filename=filename)
        return entry.path.read_bytes(), filename
    finally:
        client.close()


def build_app() -> FastAPI:
    app = FastAPI(title="SEC 10-K Item-level Extractor")

    @app.post("/extract")
    def post_extract(req: ExtractRequest) -> dict:
        try:
            html, _ = _fetch_filing_bytes(req.cik, req.accession, req.filename)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"fetch failed: {exc}") from exc
        items = extract(html, fiscal_year=req.fiscal_year)
        verification = [asdict(i) for i in verify_schedule(items, fiscal_year=req.fiscal_year)]
        return {"items": items, "verification": verification}

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    return app


app = build_app()
