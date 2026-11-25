# Moss work sample — Divyanshu Mishra

Hands-on evaluation of **Moss Inference** (Responses / Chat Completions + retrieval) and the **Tasks API** (hosted browser agents).

- **[ONE_PAGER.md](ONE_PAGER.md)** — the write-up: Portal, Inference API, Tasks API.
- **[RESULTS.md](RESULTS.md)** — every finding with numbers, and where it came from.
- **[logs/](logs/)** — raw JSONL of every request I made (credentials never logged; project id redacted).
- **[evidence/](evidence/)** — task screenshots downloaded via `GET /v1/tasks/{id}/files`.

| Script | What it does |
|---|---|
| `common.py` | Credentials, a timed HTTP client that logs every call, helpers |
| `probe_inference.py` | Latency, streaming TTFT, OpenAI-client compatibility, grounding on 3 models, error surface |
| `probe_followups.py` | Re-checks of surprising results (same-prompt streaming, oversize body via curl, paced vs burst requests) |
| `probe_tasks.py` | Validation (13 bad requests), cancel / files / idempotency lifecycle, 5 real browser tasks |
| `probe_verification.py` | Which `output_schema` keywords verification actually enforces |

Setup: `pip install -r requirements.txt`, then put `MOSS_PROJECT_ID` and `MOSS_PROJECT_KEY` in `.env` (see `.env.example`).
