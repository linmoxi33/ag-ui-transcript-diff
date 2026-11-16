#!/usr/bin/env python3
"""10 cases: 4 unit, 6 behavioural subprocess cases."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import agui_transcript_diff as m  # noqa: E402

CLI = os.path.join(ROOT, "agui_transcript_diff.py")
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def run(*args):
    p = subprocess.run([sys.executable, CLI, *args], cwd=ROOT, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def sse(*events):
    return "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()


BASE = [
    {"type": "RUN_STARTED", "threadId": "t", "runId": "r"},
    {"type": "TEXT_MESSAGE_START", "messageId": "m1", "role": "assistant"},
    {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "hi"},
    {"type": "TEXT_MESSAGE_END", "messageId": "m1"},
    {"type": "RUN_FINISHED", "threadId": "t", "runId": "r"},
]


@case
def reduces_text():
    t = m.reduce_events(m.parse_stream(sse(*BASE), "sse"))
    assert t["messages"] == [{"id": "m1", "role": "assistant", "text": "hi"}], t


@case
def outcome_defaults_to_success():
    t = m.reduce_events(m.parse_stream(sse(*BASE), "sse"))
    assert t["run"]["outcome"] == "success"


@case
def state_delta_applies():
    ev = BASE[:1] + [{"type": "STATE_SNAPSHOT", "snapshot": {"a": 1}},
                     {"type": "STATE_DELTA", "delta": [{"op": "replace", "path": "/a", "value": 2}]},
                     BASE[-1]]
    t = m.reduce_events(m.parse_stream(sse(*ev), "sse"))
    assert t["state"] == {"a": 2}, t["state"]


@case
def sha_is_stable():
    a = m.reduce_events(m.parse_stream(sse(*BASE), "sse"))
    b = m.reduce_events(m.parse_stream(sse(*BASE), "sse"))
    assert a["sha256"] == b["sha256"] and len(a["sha256"]) == 64


@case
def cli_single_exit_zero():
    rc, out, _ = run("examples/in/a.sse")
    assert rc == 0 and "sha256" in out, (rc, out)


@case
def cli_equal_streams_exit_zero():
    rc, out, _ = run("examples/in/a.sse", "examples/in/a.sse", "--json")
    r = json.loads(out)
    assert rc == 0 and r["equal"] and not r["differences"], (rc, r)


@case
def cli_state_diff_exit_one():
    rc, out, _ = run("examples/in/a.sse", "examples/in/b.sse", "--json")
    r = json.loads(out)
    assert rc == 1 and r["differences"][0]["code"] == "D030", (rc, r)


@case
def cli_text_diff_detected():
    other = sse(*[dict(e, delta="bye") if e["type"] == "TEXT_MESSAGE_CONTENT" else e for e in BASE])
    with open("/tmp/_td_case.sse", "wb") as fh:
        fh.write(other)
    rc, out, _ = run("examples/in/a.sse", "/tmp/_td_case.sse", "--json")
    r = json.loads(out)
    assert rc == 1 and any(d["code"] == "D011" for d in r["differences"]), r


@case
def cli_missing_file_exit_two():
    rc, _, err = run("/nope/a.sse")
    assert rc == 2 and "ag-ui-transcript-diff" in err, (rc, err)


@case
def cli_bad_json_exit_two():
    with open("/tmp/_td_bad.sse", "w") as fh:
        fh.write("data: {nope}\n\n")
    rc, _, err = run("/tmp/_td_bad.sse")
    assert rc == 2, (rc, err)


def main():
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(CASES)-failed} passed, {failed} failed ({len(CASES)} cases)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())