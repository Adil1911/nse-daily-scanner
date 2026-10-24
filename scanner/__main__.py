"""Command line: python -m scanner run --out OUT_DIR [--prev PREV_DIR]"""

from __future__ import annotations

import argparse
import sys

from .data import YahooProvider
from .market import now_ist
from .run import load_config, run_scan


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scanner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="scan the universe and write latest.json")
    r.add_argument("--out", required=True, help="folder for latest.json and the cached lists")
    r.add_argument("--prev", help="previous run's folder (cached lists, picks log)")
    r.add_argument("--config", help="config.json path (default: repo config.json)")
    r.add_argument("--now", help='pin the clock, India time, e.g. "2026-10-26T07:00"')
    r.add_argument("--symbols", help="comma-separated NSE symbols to scan instead of the whole list")
    r.add_argument("--limit", type=int, help="scan only the first N stocks (for a quick test)")
    r.add_argument("--no-extras", action="store_true", help="skip the F&O ban list and results calendar")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    provider = YahooProvider(cfg["data"]["chunk_size"], cfg["data"]["pause_seconds"])
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
    try:
        run_scan(
            cfg,
            provider,
            args.out,
            prev_dir=args.prev,
            now=now_ist(args.now),
            symbols=symbols,
            limit=args.limit,
            extras=not args.no_extras,
        )
    except RuntimeError as exc:
        print(f"SCAN FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
