from __future__ import annotations

import os
import re
from typing import Any

from sec_toolbox.client import SECClient


def _pad_cik(cik: str | int) -> str:
    s = str(cik).strip().lstrip("0") or "0"
    return s.zfill(10)


def _strip_dashes(accession: str) -> str:
    return accession.replace("-", "")


def _ext_from_filename(filename: str) -> str:
    _, dot, ext = filename.rpartition(".")
    return ext.lower() if dot else ""


def submissions(client: SECClient, *, cik: str | int) -> tuple[bytes, str, str, str | None]:
    url = f"https://data.sec.gov/submissions/CIK{_pad_cik(cik)}.json"
    resp = client.get(url)
    return resp.content, resp.headers.get("content-type", ""), "json", None


def full_text_search(
    client: SECClient,
    *,
    q: str,
    forms: str | None = None,
    ciks: str | None = None,
    date_range: str | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[bytes, str, str, str | None]:
    params: dict[str, Any] = {"q": q}
    if forms is not None:
        params["forms"] = forms
    if ciks is not None:
        params["ciks"] = ciks
    if date_range is not None:
        params["dateRange"] = date_range
    if extra:
        params.update(extra)
    resp = client.get("https://efts.sec.gov/LATEST/search-index", params=params)
    return resp.content, resp.headers.get("content-type", ""), "json", None


def xbrl_company_facts(client: SECClient, *, cik: str | int) -> tuple[bytes, str, str, str | None]:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{_pad_cik(cik)}.json"
    resp = client.get(url)
    return resp.content, resp.headers.get("content-type", ""), "json", None


_CIK_NUMERIC = re.compile(r"^\d+$")


def archive(
    client: SECClient,
    *,
    cik: str | int,
    accession: str,
    filename: str,
) -> tuple[bytes, str, str, str | None]:
    cik_str = str(cik).lstrip("0") or "0"
    if not _CIK_NUMERIC.match(cik_str):
        raise ValueError(f"non-numeric CIK: {cik!r}")
    acc = _strip_dashes(accession)
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_str}/{acc}/{filename}"
    resp = client.get(url)
    ext = _ext_from_filename(filename)
    relative = os.path.join("raw", "archive", cik_str, acc, filename)
    return resp.content, resp.headers.get("content-type", ""), ext, relative
