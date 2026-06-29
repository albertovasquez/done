"""`dn cron …` subcommands: install / uninstall / status of the OS service.

Thin layer over harness/jobs/service.py — argument routing + human output only.
Invoked from harness/tui_main.py when argv[1] == "cron".
"""
from __future__ import annotations

import argparse

from harness.jobs import service
from harness.jobs.service import ServiceResult


def print_result(res: ServiceResult) -> None:
    mark = "✓" if res.ok else "✗"
    print(f"{mark} [{res.backend}] {res.state}: {res.detail}")


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="dn cron",
                                     description="Manage the DoneDone cron autostart service.")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("install", help="Register + start the OS autostart service.")
    sub.add_parser("uninstall", help="Stop + deregister the OS autostart service.")
    sub.add_parser("status", help="Show whether the OS autostart service is installed.")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    if args.action == "install":
        res = service.install()
    elif args.action == "uninstall":
        res = service.uninstall()
    else:
        res = service.service_status()

    print_result(res)
    return 0 if res.ok else 1
