"""Follow-up checks on findings from probe_inference.py:
1. Streaming vs non-streaming with the SAME prompt (is the TTFT gap real?).
2. Oversize body with a plain HTTP client (does the server send 413, or drop the connection?).
3. Paced requests (0.6 s apart) vs back-to-back: when does the burst limit bite?"""
import json, os, subprocess, time
import httpx
from common import BASE, Run, auth, require_creds
require_creds()
run = Run("followups")
P = "List the first 12 prime numbers, comma separated."
for i in range(6):
    t0 = time.perf_counter(); e = run.call("same.nonstream", "POST", "/responses", body={"model": "moss/fastlane-1", "input": P + f" ({i})"})
    t0 = time.perf_counter(); first = None
    with httpx.stream("POST", BASE + "/responses", headers={"Content-Type": "application/json", **auth()},
                      json={"model": "moss/fastlane-1", "input": P + f" ({i})", "stream": True}, timeout=60) as r:
        for line in r.iter_lines():
            if line.startswith("data:") and '"response.output_text.delta"' in line and first is None:
                first = (time.perf_counter() - t0) * 1000
    total = (time.perf_counter() - t0) * 1000
    run.log(label="same.stream", ttft_ms=round(first or -1, 1), total_ms=round(total, 1))
    time.sleep(1.2)
# 2. oversize via curl (separate HTTP stack)
big = os.path.join("logs", "_big.json")
open(big, "w").write(json.dumps({"model": "moss/fastlane-1", "input": "x" * (4 * 1024 * 1024 + 10)}))
h = auth()["Authorization"]
out = subprocess.run(["curl", "-s", "-o", "-", "-w", "\nHTTP %{http_code}", "-X", "POST", BASE + "/responses",
                      "-H", f"Authorization: {h}", "-H", "Content-Type: application/json", "--data-binary", "@" + big],
                     capture_output=True, text=True, timeout=120)
run.log(label="oversize.curl", tail=(out.stdout or "")[-400:], stderr=(out.stderr or "")[-200:])
os.remove(big)
# 3. paced requests
time.sleep(6)
codes = []
for i in range(14):
    e = run.call("paced.0.6s", "POST", "/responses", body={"model": "moss/fastlane-1", "input": "OK"})
    codes.append(e.get("status")); time.sleep(0.6)
run.log(label="paced.summary", codes=codes)
print("done", run.path)
