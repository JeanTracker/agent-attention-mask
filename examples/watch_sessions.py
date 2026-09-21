#!/usr/bin/env python3
"""A sample external watcher: poll every amask session and print one line each.

This is the shape a menubar app, a status bar, or a phone-side dashboard takes.
It uses `amask.control` for convenience, but nothing here needs it -- the
protocol is one JSON object per line over a Unix socket, so a Swift or Node
client is the same twenty lines. See the bottom of the file for the raw form.

    python3 examples/watch_sessions.py                 # watch, 1s
    python3 examples/watch_sessions.py --once
    python3 examples/watch_sessions.py --set overlay_delay=8
    python3 examples/watch_sessions.py --skip
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amask import control


def one_line(status):
    """A session as one readable row."""
    where = "덮임" if status["covered"] else "열림"
    state = status["hook_state"] or ("작업" if status["busy"] else "대기")
    if status["skip_turn"]:
        state += "/건너뜀"
    if status["idle_suspected"]:
        state += "/유휴의심"
    held = status["hidden_bytes"]
    prompt = (status["prompt"] or "-")[:44]
    return (f"{status['pid']:>7}  {where:4}  {state:14}  "
            f"{status['busy_seconds']:6.1f}s  {held:>7}B  {prompt}")


def each(command, payload=None):
    """Send one command to every live session, newest last."""
    out = []
    for info in control.sessions():
        body = {"cmd": command}
        if payload:
            body["settings"] = payload
        try:
            out.append((info, control.request(info, body)))
        except (OSError, ValueError) as exc:
            out.append((info, {"ok": False, "reason": str(exc)}))
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="한 번만 출력")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--set", dest="assign", action="append", default=[],
                        metavar="KEY=VALUE", help="세 상수 중 하나를 즉시 변경")
    parser.add_argument("--skip", action="store_true", help="q와 동일")
    parser.add_argument("--wake", action="store_true", help="키 누르기와 동일")
    args = parser.parse_args()

    if args.assign:
        values = dict(item.partition("=")[::2] for item in args.assign)
        for info, reply in each("set", values):
            print(info["pid"], reply)
        return 0
    for command, wanted in (("skip", args.skip), ("wake", args.wake)):
        if wanted:
            for info, reply in each(command):
                print(info["pid"], reply.get("ok"), reply.get("reason", ""))
            return 0

    header = f"{'PID':>7}  {'화면':4}  {'상태':14}  {'작업':>7}  {'숨김':>8}  요청"
    while True:
        rows = []
        for info, reply in each("status"):
            if reply.get("ok"):
                rows.append(one_line(reply["status"]))
            else:
                rows.append(f"{info.get('pid'):>7}  -- {reply.get('reason')}")
        print(header)
        print("\n".join(rows) if rows else "  (세션 없음)")
        if args.once:
            return 0
        time.sleep(args.interval)
        print()


if __name__ == "__main__":
    sys.exit(main())

# The raw protocol, for a client in any language:
#
#   1. read ~/.amask/run/amask-ctl-<pid>.json  -> {"socket": ..., "token": ...}
#   2. connect to that AF_UNIX/SOCK_STREAM path
#   3. send  {"cmd": "status", "token": "<token>"}\n
#   4. read one line of JSON back
#
# Commands: status, get, set (with "settings"), skip, wake.
