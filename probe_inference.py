"""Inference API probe: latency, streaming, grounding, OpenAI compatibility, models and the error surface.

    python probe_inference.py            # everything (about $0.10-0.50 of credits)
    python probe_inference.py --quick    # fewer repetitions

Results go to logs/inference_<ts>.jsonl; summarise with `python report.py`.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time

import httpx

from common import BASE, PROJECT_ID, PROJECT_KEY, Run, auth, output_text, require_creds

QUICK = "--quick" in sys.argv
N_LAT = 5 if QUICK else 15
N_STREAM = 4 if QUICK else 10
INDEX = "ws_probe_kb"

# A tiny knowledge base with made-up facts, so a correct answer can only come from retrieval.
DOCS = [
    ("d1", "Zentrix routers carry a 37-day replacement warranty, counted from the delivery date."),
    ("d2", "Zentrix support is open 07:00 to 15:00 IST, Monday to Friday. There is no weekend support."),
    ("d3", "Orders to Bhutan ship from the Siliguri warehouse and take 4 business days."),
    ("d4", "The Zentrix Pro firmware version is 9.4.2, released on 2026-10-30."),
    ("d5", "Refunds are issued to the original payment method within 6 business days of approval."),
]
GROUNDED_QS = [
    ("How long is the Zentrix replacement warranty?", "37"),
    ("When is Zentrix support open?", "07:00"),
    ("How long do orders to Bhutan take?", "4 business days"),
    ("What is the latest Zentrix Pro firmware version?", "9.4.2"),
]
UNANSWERABLE_Q = "What is the Zentrix CEO's phone number?"   # not in the index: does it admit it or invent one?


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else None


def body(model="moss/fastlane-1", **kw):
    b = {"model": model, "instructions": "You are a concise support agent. Answer only from the provided context; "
                                          "if the answer is not there, say you don't know.",
         "input": "Do you ship to Canada?"}
    b.update(kw)
    return b


def latency(run: Run) -> None:
    lat = []
    for i in range(N_LAT):
        e = run.call("latency.fastlane", "POST", "/responses", body=body(input=f"Reply with the single word OK. ({i})"))
        if e.get("status") == 200:
            lat.append(e["ms"])
    run.log(label="summary.latency.fastlane", n=len(lat), p50=pct(lat, 50), p95=pct(lat, 95),
            mean=round(statistics.mean(lat), 1) if lat else None)


def streaming(run: Run) -> None:
    """Time-to-first-token vs total time, over raw SSE."""
    ttft, total = [], []
    for i in range(N_STREAM):
        h = {"Content-Type": "application/json", **auth()}
        t0 = time.perf_counter()
        first = None
        events, text = 0, []
        try:
            with httpx.stream("POST", BASE + "/responses", headers=h,
                              json=body(stream=True, input=f"Count from 1 to 20, comma separated. ({i})"), timeout=120) as r:
                status = r.status_code
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    events += 1
                    try:
                        ev = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    if ev.get("type") == "response.output_text.delta":
                        if first is None:
                            first = (time.perf_counter() - t0) * 1000
                        text.append(ev.get("delta", ""))
            dt = (time.perf_counter() - t0) * 1000
            run.log(label="stream.fastlane", status=status, ttft_ms=round(first, 1) if first else None,
                    total_ms=round(dt, 1), events=events, text="".join(text)[:200])
            if first:
                ttft.append(first); total.append(dt)
        except Exception as ex:
            run.log(label="stream.fastlane", error=repr(ex)[:300])
    run.log(label="summary.stream", n=len(ttft), ttft_p50=pct(ttft, 50), ttft_p95=pct(ttft, 95),
            total_p50=pct(total, 50))


async def make_index(run: Run) -> bool:
    try:
        from moss import DocumentInfo, MossClient
        c = MossClient(PROJECT_ID, PROJECT_KEY)
        t0 = time.perf_counter()
        try:
            await c.delete_index(INDEX)
        except Exception:
            pass
        await c.create_index(INDEX, [DocumentInfo(id=i, text=t) for i, t in DOCS], "moss-minilm")
        run.log(label="index.create", ok=True, ms=round((time.perf_counter() - t0) * 1000, 1), docs=len(DOCS))
        return True
    except Exception as ex:
        run.log(label="index.create", ok=False, error=repr(ex)[:500])
        return False


def grounding(run: Run, models=("moss/fastlane-1", "anthropic/claude-haiku-5.5", "google/gemini-3.5-flash-lite")) -> None:
    for m in models:
        for q, must in GROUNDED_QS:
            e = run.call(f"ground.{m}", "POST", "/responses", headers={"X-Moss-Indexes": INDEX}, body=body(model=m, input=q))
            ans = output_text(e.get("body"))
            run.log(label="ground.check", model=m, q=q, expected=must, correct=must.lower() in ans.lower(),
                    ms=e.get("ms"), status=e.get("status"), answer=ans[:300])
        # same question WITHOUT the index: proves the fact comes from retrieval
        e = run.call(f"ungrounded.{m}", "POST", "/responses", body=body(model=m, input=GROUNDED_QS[0][0]))
        run.log(label="ungrounded.check", model=m, answer=output_text(e.get("body"))[:300], status=e.get("status"))
        e = run.call(f"unanswerable.{m}", "POST", "/responses", headers={"X-Moss-Indexes": INDEX},
                     body=body(model=m, input=UNANSWERABLE_Q))
        run.log(label="unanswerable.check", model=m, answer=output_text(e.get("body"))[:300], status=e.get("status"))


def openai_compat(run: Run) -> None:
    try:
        from openai import OpenAI
        cl = OpenAI(base_url=BASE, api_key=f"{PROJECT_ID}:{PROJECT_KEY}")
        t0 = time.perf_counter()
        r = cl.responses.create(model="moss/fastlane-1", input="Say hi in five words.")
        run.log(label="openai.responses", ok=True, ms=round((time.perf_counter() - t0) * 1000, 1), text=r.output_text[:200])
        t0 = time.perf_counter()
        c = cl.chat.completions.create(model="moss/fastlane-1", messages=[{"role": "user", "content": "Say hi in five words."}])
        run.log(label="openai.chat", ok=True, ms=round((time.perf_counter() - t0) * 1000, 1),
                text=(c.choices[0].message.content or "")[:200], usage=getattr(c, "usage", None))
    except Exception as ex:
        run.log(label="openai.compat", ok=False, error=repr(ex)[:500])


def error_surface(run: Run) -> None:
    """Deliberately bad requests: does each fail fast, with the documented status and a useful message?"""
    run.call("err.bad_auth", "POST", "/responses", body=body(), raw_auth={"Authorization": "Bearer nope:nope"})
    run.call("err.no_auth", "POST", "/responses", body=body(), use_auth=False)
    run.call("err.unknown_model", "POST", "/responses", body=body(model="moss/does-not-exist"))
    run.call("err.vendor_model_no_key", "POST", "/responses", body=body(model="gpt-6-luna"))
    run.call("err.33_indexes", "POST", "/responses", headers={"X-Moss-Indexes": ",".join(f"i{i}" for i in range(33))}, body=body())
    run.call("err.foreign_index", "POST", "/responses", headers={"X-Moss-Indexes": "someone_elses_index_xyz"}, body=body())
    run.call("err.empty_input", "POST", "/responses", body=body(input=""))
    run.call("err.wrong_type_input", "POST", "/responses", body=body(input=12345))
    run.call("err.malformed_json", "POST", "/responses", content=b'{"model": "moss/fastlane-1", "input": ')
    run.call("err.oversize_body", "POST", "/responses", content=json.dumps(body(input="x" * (4 * 1024 * 1024 + 10))).encode())
    run.call("err.negative_max_tokens", "POST", "/responses", body=body(max_output_tokens=-5))
    run.call("models.list", "GET", "/models")


def main() -> None:
    require_creds()
    run = Run("inference")
    print("log:", run.path)
    run.call("smoke", "POST", "/responses", body=body(input="Reply OK."))
    latency(run)
    streaming(run)
    openai_compat(run)
    if asyncio.run(make_index(run)):
        grounding(run)
    error_surface(run)
    print("done:", run.path)


if __name__ == "__main__":
    main()
