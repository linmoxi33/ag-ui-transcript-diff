#!/usr/bin/env python3
"""Reduce recorded AG-UI streams to a canonical transcript, then diff two of them.

Why: two SDK versions (or two transports) can both pass schema validation and
still hand the app a different conversation — different text, tool arguments,
or final state. This tool projects the stream onto what the application
actually consumes, hashes it, and diffs two projections field by field.
Zero dependencies, no network. Exit: 0 equal, 1 different, 2 usage/input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys


def parse_stream(raw: bytes, fmt: str) -> list[dict]:
    text = raw.decode("utf-8", errors="replace")
    events: list[dict] = []
    if fmt == "jsonl" or (fmt == "auto" and text.lstrip()[:1] in ("{", "[")):
        for line in text.splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events
    data: list[str] = []
    for line in text.splitlines():
        if line == "":
            if data:
                events.append(json.loads("\n".join(data)))
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        events.append(json.loads("\n".join(data)))
    return events


def _apply_patch(doc, patch: list) -> None:
    """Minimal RFC 6902: add/replace/remove on top-level-ish paths."""
    for op in patch:
        if not isinstance(op, dict):
            continue
        path = str(op.get("path", ""))
        parts = [p.replace("~1", "/").replace("~0", "~") for p in path.split("/")[1:]]
        if not parts:
            continue
        cur = doc
        for part in parts[:-1]:
            if isinstance(cur, dict):
                cur = cur.setdefault(part, {})
            elif isinstance(cur, list) and part.isdigit():
                idx = int(part)
                while len(cur) <= idx:
                    cur.append(None)
                cur = cur[idx]
            else:
                break
        last = parts[-1]
        kind = op.get("op")
        if isinstance(cur, dict):
            if kind in ("add", "replace"):
                cur[last] = op.get("value")
            elif kind == "remove":
                cur.pop(last, None)
        elif isinstance(cur, list) and last.isdigit():
            idx = int(last)
            if kind == "remove" and idx < len(cur):
                cur.pop(idx)
            elif kind in ("add", "replace"):
                while len(cur) <= idx:
                    cur.append(None)
                cur[idx] = op.get("value")


def reduce_events(events: list[dict]) -> dict:
    run = {"runId": None, "threadId": None, "outcome": None, "error": None}
    messages: dict[str, dict] = {}
    tools: dict[str, dict] = {}
    order: list[str] = []
    state = {}
    steps: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        if t == "RUN_STARTED":
            run["runId"], run["threadId"] = ev.get("runId"), ev.get("threadId")
            run["outcome"] = None
        elif t == "RUN_FINISHED":
            outcome = ev.get("outcome")
            run["outcome"] = outcome.get("type") if isinstance(outcome, dict) else "success"
            if "result" in ev:
                run["result"] = ev["result"]
        elif t == "RUN_ERROR":
            run["outcome"] = "error"
            run["error"] = ev.get("message")
        elif t == "TEXT_MESSAGE_START":
            mid = str(ev.get("messageId"))
            messages.setdefault(mid, {"id": mid, "role": ev.get("role"), "text": ""})
            if mid not in order:
                order.append(mid)
        elif t == "TEXT_MESSAGE_CONTENT":
            mid = str(ev.get("messageId"))
            messages.setdefault(mid, {"id": mid, "role": None, "text": ""})
            messages[mid]["text"] += str(ev.get("delta", ""))
        elif t == "TEXT_MESSAGE_END":
            mid = str(ev.get("messageId"))
            messages.setdefault(mid, {"id": mid, "role": None, "text": ""})
        elif t == "TOOL_CALL_START":
            tid = str(ev.get("toolCallId"))
            tools[tid] = {"id": tid, "name": ev.get("toolCallName"), "args": ""}
        elif t == "TOOL_CALL_ARGS":
            tid = str(ev.get("toolCallId"))
            tools.setdefault(tid, {"id": tid, "name": None, "args": ""})["args"] += str(ev.get("delta", ""))
        elif t == "TOOL_CALL_END":
            tid = str(ev.get("toolCallId"))
            tools.setdefault(tid, {"id": tid, "name": None, "args": ""})
        elif t == "TOOL_CALL_RESULT":
            tid = str(ev.get("toolCallId"))
            tools.setdefault(tid, {"id": tid, "name": None, "args": ""})["result"] = ev.get("content")
        elif t == "STATE_SNAPSHOT":
            snap = ev.get("snapshot")
            state = snap if isinstance(snap, dict) else {}
        elif t == "STATE_DELTA":
            patch = ev.get("delta")
            if isinstance(patch, list):
                _apply_patch(state, patch)
        elif t == "STEP_STARTED":
            steps.append(str(ev.get("stepName")))
    transcript = {
        "run": run,
        "messages": [messages[mid] for mid in order],
        "toolCalls": list(tools.values()),
        "state": state,
        "steps": steps,
    }
    blob = json.dumps(transcript, sort_keys=True, ensure_ascii=False).encode()
    transcript["sha256"] = hashlib.sha256(blob).hexdigest()
    return transcript


def diff_transcripts(a: dict, b: dict) -> list[dict]:
    out: list[dict] = []
    if a["run"].get("outcome") != b["run"].get("outcome"):
        out.append({"code": "D001", "path": "run.outcome",
                    "left": a["run"].get("outcome"), "right": b["run"].get("outcome")})
    am = {m["id"]: m for m in a["messages"]}
    bm = {m["id"]: m for m in b["messages"]}
    for mid in sorted(set(am) | set(bm)):
        if mid not in am or mid not in bm:
            out.append({"code": "D010", "path": f"messages[{mid}]",
                        "left": mid in am, "right": mid in bm})
        elif am[mid]["text"] != bm[mid]["text"]:
            out.append({"code": "D011", "path": f"messages[{mid}].text",
                        "left": am[mid]["text"], "right": bm[mid]["text"]})
    at = {t["id"]: t for t in a["toolCalls"]}
    bt = {t["id"]: t for t in b["toolCalls"]}
    for tid in sorted(set(at) | set(bt)):
        if tid not in at or tid not in bt:
            out.append({"code": "D020", "path": f"toolCalls[{tid}]",
                        "left": tid in at, "right": tid in bt})
            continue
        for field in ("name", "args", "result"):
            if at[tid].get(field) != bt[tid].get(field):
                out.append({"code": "D021", "path": f"toolCalls[{tid}].{field}",
                            "left": at[tid].get(field), "right": bt[tid].get(field)})
    if a["state"] != b["state"]:
        out.append({"code": "D030", "path": "state", "left": a["state"], "right": b["state"]})
    if a["steps"] != b["steps"]:
        out.append({"code": "D031", "path": "steps", "left": a["steps"], "right": b["steps"]})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ag-ui-transcript-diff")
    ap.add_argument("left")
    ap.add_argument("right", nargs="?")
    ap.add_argument("--format", choices=("auto", "sse", "jsonl"), default="auto")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)
    fmt = args.format
    if fmt == "auto":
        fmt = "sse"  # per-file auto re-detected in parse_stream
    try:
        la = reduce_events(parse_stream(open(args.left, "rb").read(), fmt))
        lb = reduce_events(parse_stream(open(args.right, "rb").read(), fmt)) if args.right else None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ag-ui-transcript-diff: {exc}", file=sys.stderr)
        return 2
    if lb is None:
        print(json.dumps(la, ensure_ascii=False, indent=2) if args.as_json
              else f"{la['run'].get('runId')}: {len(la['messages'])} messages, "
                   f"{len(la['toolCalls'])} tool calls, sha256 {la['sha256']}")
        return 0
    diffs = diff_transcripts(la, lb)
    report = {"equal": not diffs, "left_sha256": la["sha256"], "right_sha256": lb["sha256"],
              "differences": diffs}
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for d in diffs:
            print(f"{d['code']} {d['path']}: {d['left']!r} -> {d['right']!r}")
        print(f"{len(diffs)} difference(s)")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())