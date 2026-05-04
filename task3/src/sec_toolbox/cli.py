from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sec_toolbox.cache import DiskCache
from sec_toolbox.client import SECClient
from sec_toolbox.fetch import Fetcher


def _build_client(args: argparse.Namespace) -> SECClient:
    return SECClient()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sec_toolbox")
    p.add_argument("--data-dir", default="data", help="cache root (default: data/)")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("-o", "--output", default=None, help="override output path (archive only)")

    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submissions")
    s.add_argument("--cik", required=True)

    s = sub.add_parser("search")
    s.add_argument("--q", required=True)
    s.add_argument("--forms", default=None)
    s.add_argument("--ciks", default=None)
    s.add_argument("--dateRange", dest="date_range", default=None)

    s = sub.add_parser("xbrl")
    s.add_argument("--cik", required=True)

    s = sub.add_parser("archive")
    s.add_argument("--cik", required=True)
    s.add_argument("--accession", required=True)
    s.add_argument("--filename", required=True)

    s = sub.add_parser("survey")
    s.add_argument("--out", default="data/survey")

    s = sub.add_parser("eval")
    s.add_argument("--out", default="data/eval")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if e.code is not None else 2

    cache = DiskCache(root=Path(args.data_dir))
    client = _build_client(args)
    fetcher = Fetcher(client=client, cache=cache)

    try:
        if args.cmd == "submissions":
            entry = fetcher.submissions(cik=args.cik, no_cache=args.no_cache, refresh=args.refresh)
            sys.stdout.write(entry.path.read_text())
        elif args.cmd == "search":
            entry = fetcher.search(
                q=args.q,
                forms=args.forms,
                ciks=args.ciks,
                date_range=args.date_range,
                no_cache=args.no_cache,
                refresh=args.refresh,
            )
            sys.stdout.write(entry.path.read_text())
        elif args.cmd == "xbrl":
            entry = fetcher.xbrl(cik=args.cik, no_cache=args.no_cache, refresh=args.refresh)
            sys.stdout.write(entry.path.read_text())
        elif args.cmd == "archive":
            entry = fetcher.archive(
                cik=args.cik,
                accession=args.accession,
                filename=args.filename,
                no_cache=args.no_cache,
                refresh=args.refresh,
            )
            if args.output:
                Path(args.output).write_bytes(entry.path.read_bytes())
                print(args.output)
            else:
                print(str(entry.path))
        elif args.cmd == "survey":
            from sec_toolbox.survey import run_survey

            run_survey(fetcher=fetcher, out_dir=Path(args.out))
        elif args.cmd == "eval":
            from sec_toolbox.eval import EvalSpec, run_eval
            from sec_toolbox.survey import SLATE, pick_filing

            specs: list[EvalSpec] = []
            for s_entry in SLATE:
                sub_entry = fetcher.submissions(cik=s_entry.cik)
                sub_json = json.loads(sub_entry.path.read_text())
                pick = pick_filing(sub_json, s_entry.target)
                if pick is None:
                    continue
                arc = fetcher.archive(
                    cik=s_entry.cik,
                    accession=pick["accession"],
                    filename=pick["primary_doc"],
                )
                year = int(pick["date"][:4])
                slug = s_entry.name.lower().replace(" ", "_")
                specs.append(
                    EvalSpec(
                        name=f"{s_entry.cik}_{slug}_{year}",
                        html_path=arc.path,
                        fiscal_year=year,
                    )
                )
            run_eval(specs, out_dir=Path(args.out))
        else:
            parser.error(f"unknown subcommand {args.cmd!r}")
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
