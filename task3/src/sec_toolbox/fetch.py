from __future__ import annotations

from typing import Any

from sec_toolbox import endpoints
from sec_toolbox.cache import CacheEntry, DiskCache
from sec_toolbox.client import SECClient


class Fetcher:
    def __init__(self, *, client: SECClient, cache: DiskCache) -> None:
        self.client = client
        self.cache = cache

    def _go(
        self,
        endpoint: str,
        args: dict[str, Any],
        fetch_callable,
        *,
        no_cache: bool = False,
        refresh: bool = False,
    ) -> CacheEntry:
        if not refresh and not no_cache:
            hit = self.cache.read(endpoint, args)
            if hit is not None:
                return hit
        body, content_type, ext, relative = fetch_callable()
        return self.cache.write(
            endpoint=endpoint,
            args=args,
            ext=ext,
            body=body,
            status=200,
            content_type=content_type,
            relative_path=relative,
        )

    def submissions(
        self, *, cik: str | int, no_cache: bool = False, refresh: bool = False
    ) -> CacheEntry:
        args = {"cik": str(cik)}
        return self._go(
            "submissions",
            args,
            lambda: endpoints.submissions(self.client, cik=cik),
            no_cache=no_cache,
            refresh=refresh,
        )

    def search(
        self,
        *,
        q: str,
        forms: str | None = None,
        ciks: str | None = None,
        date_range: str | None = None,
        no_cache: bool = False,
        refresh: bool = False,
    ) -> CacheEntry:
        args = {"q": q, "forms": forms, "ciks": ciks, "dateRange": date_range}
        return self._go(
            "search",
            args,
            lambda: endpoints.full_text_search(
                self.client, q=q, forms=forms, ciks=ciks, date_range=date_range
            ),
            no_cache=no_cache,
            refresh=refresh,
        )

    def xbrl(self, *, cik: str | int, no_cache: bool = False, refresh: bool = False) -> CacheEntry:
        args = {"cik": str(cik)}
        return self._go(
            "xbrl",
            args,
            lambda: endpoints.xbrl_company_facts(self.client, cik=cik),
            no_cache=no_cache,
            refresh=refresh,
        )

    def archive(
        self,
        *,
        cik: str | int,
        accession: str,
        filename: str,
        no_cache: bool = False,
        refresh: bool = False,
    ) -> CacheEntry:
        args = {"cik": str(cik), "accession": accession, "filename": filename}
        return self._go(
            "archive",
            args,
            lambda: endpoints.archive(self.client, cik=cik, accession=accession, filename=filename),
            no_cache=no_cache,
            refresh=refresh,
        )
