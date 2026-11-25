"""Tasks API probe: real browser tasks, schema verification, cancellation, idempotency, limits, validation.

    python probe_tasks.py          # runs the suite (each real task is capped by limits.max_cost_usd)

Results go to logs/tasks_<ts>.jsonl; summarise with `python report.py`.
"""
from __future__ import annotations

import time
import uuid

from common import Run, require_creds

TERMINAL = {"completed", "failed", "cancelled"}


def poll(run: Run, tid: str, label: str, timeout_s: int = 900, every: int = 10) -> dict:
    t0 = time.time()
    last = None
    seen = []
    while time.time() - t0 < timeout_s:
        e = run.call(f"{label}.poll", "GET", f"/tasks/{tid}")
        b = e.get("body") if isinstance(e.get("body"), dict) else {}
        st = b.get("status")
        if st != last:
            seen.append((round(time.time() - t0), st))
            last = st
        if st in TERMINAL:
            run.log(label=f"{label}.final", task_id=tid, status=st, transitions=seen, result=b.get("result"),
                    verification=b.get("verification"), usage=b.get("usage"), error=b.get("error"))
            return b
        time.sleep(every)
    run.log(label=f"{label}.timeout", task_id=tid, transitions=seen)
    return {}


def create(run: Run, label: str, payload: dict, headers: dict | None = None) -> dict:
    return run.call(label, "POST", "/tasks", body=payload, headers=headers)


def real_tasks(run: Run) -> None:
    # 1. Simple, verifiable goal with an output schema (should pass verification).
    schema = {"type": "object", "properties": {"title": {"type": "string"}, "first_heading": {"type": "string"}},
              "required": ["title", "first_heading"]}
    e = create(run, "task.simple_schema", {
        "goal": "Open the page and report its <title> and its first <h1> text exactly.",
        "environment": {"type": "browser", "start_url": "https://example.com"},
        "model": "moss-fast", "output_schema": schema,
        "limits": {"max_runtime_seconds": 300, "max_cost_usd": 0.5}})
    if e.get("status") in (200, 202):
        b = poll(run, e["body"]["id"], "task.simple_schema")
        out = (b.get("result") or {}).get("output") or {}
        run.log(label="task.simple_schema.check", correct=(out.get("first_heading", "").strip() == "Example Domain"), output=out)
        # files only exist after the task ends: list them now
        run.call("task.files", "GET", f"/tasks/{e['body']['id']}/files")

    # 2. Multi-step navigation with structured output (Wikipedia: stable, public, no login).
    schema2 = {"type": "object", "properties": {"population_2011": {"type": "string"}, "source_url": {"type": "string"}},
               "required": ["population_2011", "source_url"]}
    e = create(run, "task.multistep", {
        "goal": "Find the population of Bilaspur district, Chhattisgarh, in the 2011 census, and give the page you read it on.",
        "environment": {"type": "browser", "start_url": "https://en.wikipedia.org"},
        "model": "moss-balanced", "output_schema": schema2,
        "limits": {"max_runtime_seconds": 600, "max_cost_usd": 1}})
    if e.get("status") in (200, 202):
        poll(run, e["body"]["id"], "task.multistep")

    # 3. Impossible schema: the answer cannot satisfy it. Docs say the task still ends `completed` with
    #    verification.passed=false. Does it, and is the failure visible enough to a caller?
    impossible = {"type": "object", "properties": {"year": {"type": "integer", "minimum": 3000}}, "required": ["year"]}
    e = create(run, "task.impossible_schema", {
        "goal": "What year was example.com's IANA reservation made? Answer with the year.",
        "environment": {"type": "browser", "start_url": "https://example.com"},
        "model": "moss-fast", "output_schema": impossible,
        "limits": {"max_runtime_seconds": 240, "max_cost_usd": 0.3}})
    if e.get("status") in (200, 202):
        poll(run, e["body"]["id"], "task.impossible_schema")

    # 4. A page that blocks bots (CAPTCHA demo). How does the agent fail: honestly, or with a made-up answer?
    e = create(run, "task.captcha", {
        "goal": "Submit the form on this page and report the confirmation message shown after submitting.",
        "environment": {"type": "browser", "start_url": "https://www.google.com/recaptcha/api2/demo"},
        "model": "moss-fast", "limits": {"max_runtime_seconds": 180, "max_cost_usd": 0.3}})
    if e.get("status") in (200, 202):
        poll(run, e["body"]["id"], "task.captcha")

    # 5. Tiny cost cap: is max_cost_usd enforced, and how does the task end when it is hit?
    e = create(run, "task.tiny_budget", {
        "goal": "Read the Wikipedia article on India and summarise its history section in 200 words.",
        "environment": {"type": "browser", "start_url": "https://en.wikipedia.org/wiki/India"},
        "model": "moss-balanced", "limits": {"max_runtime_seconds": 300, "max_cost_usd": 0.01}})
    if e.get("status") in (200, 202):
        poll(run, e["body"]["id"], "task.tiny_budget")


def lifecycle(run: Run) -> None:
    # Cancellation of a running task.
    e = create(run, "task.cancel_target", {
        "goal": "Browse Wikipedia's featured articles and list ten of them with one-line summaries.",
        "environment": {"type": "browser", "start_url": "https://en.wikipedia.org"},
        "model": "moss-fast", "limits": {"max_runtime_seconds": 600, "max_cost_usd": 0.5}})
    if e.get("status") in (200, 202):
        tid = e["body"]["id"]
        time.sleep(20)
        run.call("task.files_before_finish", "GET", f"/tasks/{tid}/files")     # expect 409 task_not_finished
        run.call("task.cancel", "POST", f"/tasks/{tid}/cancel")
        b = poll(run, tid, "task.cancel_target", timeout_s=300, every=5)
        run.call("task.cancel_again", "POST", f"/tasks/{tid}/cancel")          # finished task: unchanged?
        run.log(label="task.cancel.check", final_status=b.get("status"), usage=b.get("usage"))

    # Idempotency: same key + same body -> replay; same key + different body -> 422.
    key = "ws-" + uuid.uuid4().hex
    payload = {"goal": "Report the title of https://example.com.", "environment": {"type": "browser", "start_url": "https://example.com"},
               "model": "moss-fast", "limits": {"max_runtime_seconds": 120, "max_cost_usd": 0.2}}
    a = create(run, "idem.first", payload, headers={"Idempotency-Key": key})
    b = create(run, "idem.replay", payload, headers={"Idempotency-Key": key})
    c = create(run, "idem.reuse_other_body", {**payload, "goal": "Something else."}, headers={"Idempotency-Key": key})
    ida = (a.get("body") or {}).get("id") if isinstance(a.get("body"), dict) else None
    idb = (b.get("body") or {}).get("id") if isinstance(b.get("body"), dict) else None
    run.log(label="idem.check", same_task=(ida is not None and ida == idb), replay_header=b.get("headers"),
            reuse_status=c.get("status"))
    if ida:
        run.call("idem.cleanup_cancel", "POST", f"/tasks/{ida}/cancel")


def validation(run: Run) -> None:
    ok_env = {"type": "browser"}
    run.call("val.missing_goal", "POST", "/tasks", body={"environment": ok_env})
    run.call("val.bad_env_type", "POST", "/tasks", body={"goal": "x", "environment": {"type": "desktop"}})
    run.call("val.unknown_field", "POST", "/tasks", body={"goal": "x", "environment": ok_env, "colour": "blue"})
    run.call("val.runtime_too_low", "POST", "/tasks", body={"goal": "x", "environment": ok_env, "limits": {"max_runtime_seconds": 59}})
    run.call("val.runtime_too_high", "POST", "/tasks", body={"goal": "x", "environment": ok_env, "limits": {"max_runtime_seconds": 7201}})
    run.call("val.cost_too_low", "POST", "/tasks", body={"goal": "x", "environment": ok_env, "limits": {"max_cost_usd": 0.001}})
    run.call("val.bad_model", "POST", "/tasks", body={"goal": "x", "environment": ok_env, "model": "moss-ultra"})
    run.call("val.file_url", "POST", "/tasks", body={"goal": "x", "environment": {"type": "browser", "start_url": "file:///etc/passwd"}})
    run.call("val.js_url", "POST", "/tasks", body={"goal": "x", "environment": {"type": "browser", "start_url": "javascript:alert(1)"}})
    run.call("val.goal_too_long", "POST", "/tasks", body={"goal": "x" * 20001, "environment": ok_env})
    run.call("val.schema_not_object", "POST", "/tasks", body={"goal": "x", "environment": ok_env, "output_schema": {"type": "array"}})
    run.call("val.unknown_task", "GET", "/tasks/task_" + "0" * 32)
    run.call("val.malformed_task_id", "GET", "/tasks/../../models")


def main() -> None:
    require_creds()
    run = Run("tasks")
    print("log:", run.path)
    validation(run)          # free: all rejected before any work
    lifecycle(run)
    real_tasks(run)
    print("done:", run.path)


if __name__ == "__main__":
    main()
