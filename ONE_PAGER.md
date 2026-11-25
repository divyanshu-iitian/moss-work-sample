# Moss Inference — work sample (Divyanshu Mishra)

Repo with code, scripts and logs: https://github.com/divyanshu-iitian/moss-work-sample · full numbers in RESULTS.md

## 1. Portal
**Liked:** Signing up and getting started was easy. I had a project, a key and a sample index ("product-search-context") with ready-to-run query code within minutes.
**Didn't like:**
- The Playground assistant's answers weren't clean. I asked about "moss" startups in India. It assumed I meant the plant, said it "can't browse the web", and padded the answer with caveats and questions. Inside the Moss portal it doesn't seem to know it is Moss, or to use my indexes or the Tasks browser.
- 2FA by SMS was unreliable; sometimes the code never arrived.
**Would improve:**
- Make the Playground Moss-aware: default it to the project's indexes, show the retrieved hits next to the answer, and offer "run this as a Task" when a question needs the web.
- Add a fallback for 2FA (authenticator app, email code).

## 2. Inference API
**Liked:**
- `moss/fastlane-1` is genuinely fast: p50 162 ms, p95 195 ms for short replies.
- Grounding worked on all three models I tried (Fastlane, Claude Haiku 5.5, Gemini 3.5 Flash-Lite): 12/12 answers correct from an index of invented facts. Each model said "I don't know" when the fact wasn't in the index.
- The stock OpenAI client worked unchanged.
- Error messages tell you what to do next.
**Didn't like:**
- Warm retrieval is 7–9 ms, but the first query after creating an index spent 3.4 s in retrieval. For a voice agent, that's the first caller's first turn.
- For short answers, streaming's first token (~466 ms) arrives after a full non-streamed reply (~375 ms).
- Chat Completions reports `prompt_tokens: 0` while Responses reports input tokens correctly, so cost tracking via Chat Completions under-counts.
- Sending 33 index names returns 403 "outside this project" instead of the documented 400.
- An empty input is accepted with 200.
- A 429 carries `Retry-After: 1` and `reset: 5s` at the same time.
**Would improve:**
- Warm an index as soon as it's created, or expose a load/readiness signal.
- Fix the usage field, and validate request size and shape before ownership.
- Make the rate-limit hints agree.

## 3. Tasks API
**What I tested:** 13 invalid requests, cancel, files, idempotency, five real browser tasks, and four schema-verification probes. Total spend was under $0.25.
**Liked:**
- Validation is excellent: every bad request was rejected before spending anything, with a precise message. This included `file://` and `javascript:` URLs.
- Cancel, idempotency (replay and 422 on reuse), 409 before finish, and per-step screenshots all work as documented.
- A multi-step Wikipedia lookup returned the right number with its source URL in 32 s for $0.07.
- The agent even got through Google's reCAPTCHA demo; the screenshot confirms it.
**Where I broke it:**
- **Schema verification is partial.** `enum` is enforced (`passed:false`), but outputs violating `minimum`, `pattern` and `maxLength` all came back `passed:true`. A task can end "completed, verified" with an answer that breaks the schema.
- `max_cost_usd: 0.01` was overshot 2.4× ($0.024) before `budget_exceeded`. The cap is checked after a model call, not before.
- `cancel_requested` resets to false once a task is cancelled, which loses the audit trail.
**Would improve:**
- Use a full JSON Schema validator, or reject keywords it doesn't enforce.
- Estimate the next call's cost against the remaining budget.
- Keep the cancel flag after cancellation.

Divyanshu Mishra · github.com/divyanshu-iitian
