# Moss Inference & Tasks — what I ran and what I found

All numbers below come from the logs in [`logs/`](logs/) (one JSON line per request: status, latency, Moss/rate-limit headers, trimmed body). Runs were made on 25 Nov 2026 from India on a new project with the $10 sign-up credit; total spend was well under $1.

Legend: ✅ works as documented · ⚠️ works but surprising · 🐞 bug or doc mismatch

---

## 1. Inference API (`probe_inference.py`, `probe_followups.py`)

### Latency
| Measurement | Result |
|---|---|
| `moss/fastlane-1`, short reply, non-streaming (n=13) | **p50 162 ms, p95 195 ms** ✅ matches the "sub-200 ms" claim |
| First request on a fresh project | 3,096 ms (cold) |
| Streaming, time to first token (n=10, "count 1–20") | p50 511 ms, p95 574 ms |
| Same prompt, streaming vs not (n=6) | non-stream **total** p50 375 ms vs stream **TTFT** p50 466 ms ⚠️ |

⚠️ For short answers the first streamed token arrives ~90 ms *later* than the whole non-streamed answer. Streaming seems to carry a fixed setup cost, which matters for the voice-agent use case Moss targets.

### Grounding (retrieval from my own index)
I created an index with five invented facts (e.g. "Zentrix routers carry a 37-day replacement warranty") so a correct answer can only come from retrieval.

| Model | Grounded questions correct | Without the index | Question not in the index |
|---|---|---|---|
| `moss/fastlane-1` | **4/4** ✅ | "I don't know." | "I do not know." |
| `anthropic/claude-haiku-5.5` | **4/4** ✅ | declined, suggested contacting support | declined |
| `google/gemini-3.5-flash-lite` | **4/4** ✅ | "I don't know." | "I don't know." |

- Index creation took 7.8 s.
- Warm retrieval (`x-moss-retrieve-ms`): **7–9 ms** ✅.
- ⚠️ **Cold retrieval:** the first query after creating the index spent **3,452 ms** in retrieval (4.8 s end to end). The sub-10 ms figure holds only once the index is loaded. A first caller (e.g. the first turn of a voice call) pays seconds.

### OpenAI compatibility ✅
The stock `openai` Python client worked unchanged against `https://inference.moss.dev/v1` for both `responses.create` and `chat.completions.create`.

🐞 **Usage mismatch:** Chat Completions returned `prompt_tokens: 0` for a request with a real prompt, while the Responses API reports input tokens correctly (`input_tokens: 141` for a similar call). Anyone metering cost from Chat Completions usage under-counts.

### Error surface
| Request | Got | Notes |
|---|---|---|
| Wrong key / no key | 401 ✅ | Clear messages ("credentials were rejected" / "Missing bearer token") |
| Unknown model | 400 `model_not_found` ✅ | Message says what to send instead |
| Vendor model without own key | 400 `missing_provider_key` ✅ | Very helpful message |
| **33 index names** | **403 `permission_error`** 🐞 | Docs say >32 names returns **400**. Ownership is checked before the count, so a caller who simply sent too many names is told the indexes are "outside this project". |
| Index from another project | 403 ✅ | |
| `input: ""` (empty) | **200** ⚠️ | Accepted and billed; a 400 would be safer |
| `input: 12345` | 400 ✅ | |
| Malformed JSON | 400 ✅ | |
| Body > 4 MB | 413 ✅ (via curl) | ⚠️ With Python `httpx` the server answers before reading the body and the client sees a connection reset (`WinError 10053`) instead of the 413 JSON |
| `max_output_tokens: -5` | 400 ⚠️ | Message names `max_tokens`, not the Responses field `max_output_tokens` |

### Rate limits
- ✅ Back-to-back requests hit `429` after ~13 calls (burst bucket of 10, documented).
- ✅ The same 14 requests paced 0.6 s apart got no 429s.
- ⚠️ The 429 says `Retry-After: 1`, but `x-ratelimit-reset-requests: 5s` on the same response. Two different hints; a client can't tell which to honour.

---

## 2. Tasks API (`probe_tasks.py`, `probe_verification.py`)

### Real tasks (cloud browser)
| Task | Model | Status / time | Cost | Outcome |
|---|---|---|---|---|
| example.com: title + first `<h1>` with schema | moss-fast | completed, 22 s | $0.0055 | ✅ Correct. Title "Example Domain"; the 2026 page has no `<h1>`, and the agent returned `""` rather than inventing one. |
| Wikipedia: Bilaspur district 2011 population + source URL | moss-balanced | completed, 32 s | $0.068 | ✅ 2,663,629, with the article URL |
| reCAPTCHA demo page: submit and report | moss-fast | completed, 53 s | $0.011 | ⚠️ Got through and reported "Verification Success… Hooray!". The runner screenshot confirms it ([`evidence/captcha`](evidence/captcha)). |
| `max_cost_usd: 0.01` cap | moss-balanced | failed, 12 s | **$0.024** | 🐞 Cap overshot 2.4× before `budget_exceeded` ("Spent $0.02"). The cap is checked after a model call, not before it. |

### Lifecycle ✅
- Cancel: a running task went `running → cancelled` in ~18 s, with usage accounted ($0.008).
- Cancelling a finished task leaves it unchanged.
- ⚠️ `cancel_requested` flips back to `false` once the task is `cancelled`, so the record no longer shows a cancel was requested.
- Files before finish: `409 task_not_finished`, as documented. After finish, per-step screenshots download fine.
- Idempotency:
  - Same key and body returned the same task id, with `idempotent-replayed: true`.
  - Same key with another body returned `422 idempotency_key_reused`.

### Validation ✅ (13 bad requests, all rejected before any spend)
All of these were rejected with precise, actionable messages:
- missing goal; `environment.type: "desktop"`; unknown field; runtime 59 s / 7,201 s; cost $0.001
- unknown model (the message lists the valid ones); `file://` and `javascript:` start URLs
- goal over 20,000 characters; `output_schema` of type array (the message suggests the fix); unknown task id (404)

### 🐞 Output-schema verification is partial
Each task asked a question whose honest answer violates one schema keyword. A strict validator must report `passed: false`.

| Keyword | Agent output | `verification.passed` |
|---|---|---|
| `enum: ["RED","GREEN"]` | `"BLACK"` | **false** ✅ ("not one of the allowed values") |
| `minimum: 3000` | `1999` | **true** 🐞 |
| `pattern: "^[0-9]+$"` | `"Example Domain"` | **true** 🐞 |
| `maxLength: 3` | `"Example Domain"` | **true** 🐞 |

The API accepts these keywords without complaint but does not enforce them. A task can end `completed` with `passed: true` while its output breaks the caller's schema, which is exactly the failure verification exists to catch.

---

## 3. Reproduce
```bash
python -m venv .venv && .venv/Scripts/activate      # or bin/activate
pip install -r requirements.txt
cp .env.example .env                                 # add MOSS_PROJECT_ID and MOSS_PROJECT_KEY
python probe_inference.py      # ~2 min, a few cents
python probe_followups.py      # streaming / oversize / pacing follow-ups
python probe_tasks.py          # ~5 min, ~$0.15 of task credit
python probe_verification.py   # ~3 min, ~$0.05
```
