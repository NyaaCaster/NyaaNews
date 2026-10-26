import argparse
from datetime import date, timedelta
import json
import os
import sys
from . import BLESSING, __version__
from .config import Config
from .network import PublicHTTP
from .store import Store
from .feeds import poll
from .util import now

def main():
    parser = argparse.ArgumentParser(prog="nyaanews")
    parser.add_argument("--config", default=os.environ.get("NYAANEWS_CONFIG", ".env/runtime.env"))
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("check")
    discover = subs.add_parser("discover")
    discover.add_argument("--refresh", action="store_true")
    run = subs.add_parser("run")
    run.add_argument("--date", type=date.fromisoformat)
    run.add_argument("--refresh", action="store_true")
    run.add_argument("--no-sync", action="store_true")
    subs.add_parser("sync")
    subs.add_parser("status")
    args = parser.parse_args()
    try:
        cfg = Config.load(args.config)
        cfg.prepare()
        if args.command == "check":
            print(json.dumps({"version": __version__, "blessing": BLESSING,
                              **cfg.public_status()}, ensure_ascii=False))
            return 0
        if args.command == "discover":
            store = Store(cfg.data)
            try:
                report = [poll(s, store, PublicHTTP(), args.refresh)
                          for s in cfg.sources if s.get("enabled", True)]
                print(json.dumps(report, ensure_ascii=False))
                return 0 if all(r["status"] == "succeeded" for r in report) else 2
            finally:
                store.close()
        if args.command == "run":
            from .pipeline import archive_run
            day = args.date or (now().astimezone(cfg.tz).date() - timedelta(days=1))
            report = archive_run(cfg, day, refresh=args.refresh, run_sync=not args.no_sync)
            print(json.dumps(report, ensure_ascii=False))
            return 0 if report["status"] == "completed" else 2
        if args.command in ("sync", "status"):
            from filelock import FileLock
            with FileLock(str(cfg.data/"state/run.lock"),timeout=0):
                store=Store(cfg.data)
                try:
                    if args.command=="sync":
                        from .sync import sync_pending
                        report=sync_pending(cfg,store)
                    else:
                        report={"articles":store.db.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
                                "outbox":dict(store.db.execute(
                                    "SELECT status,COUNT(*) FROM outbox GROUP BY status").fetchall())}
                    print(json.dumps(report,ensure_ascii=False))
                    return 0 if not report.get("unknown") and not report.get("failed") else 2
                finally:
                    store.close()
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__}), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
