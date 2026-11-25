"""Characterise what Tasks `output_schema` verification actually enforces.
Each task asks a question whose honest answer VIOLATES one schema keyword; a strict validator must report passed=false."""
from common import Run, require_creds
from probe_tasks import create, poll
require_creds()
run = Run("verification")
cases = {
    "minimum": ({"type": "object", "properties": {"year": {"type": "integer", "minimum": 3000}}, "required": ["year"]},
                "In what year was the IANA example.com reservation made (RFC 2606)? Reply with the year."),
    "enum": ({"type": "object", "properties": {"colour": {"type": "string", "enum": ["RED", "GREEN"]}}, "required": ["colour"]},
             "What colour is the text of the main paragraph on this page? Use one word in capitals."),
    "pattern": ({"type": "object", "properties": {"title": {"type": "string", "pattern": "^[0-9]+$"}}, "required": ["title"]},
                "What is the exact <title> of this page?"),
    "maxLength": ({"type": "object", "properties": {"title": {"type": "string", "maxLength": 3}}, "required": ["title"]},
                  "What is the exact <title> of this page?"),
}
for name, (schema, goal) in cases.items():
    e = create(run, f"verify.{name}", {"goal": goal, "environment": {"type": "browser", "start_url": "https://example.com"},
                                       "model": "moss-fast", "output_schema": schema, "limits": {"max_runtime_seconds": 180, "max_cost_usd": 0.2}})
    if e.get("status") in (200, 202):
        b = poll(run, e["body"]["id"], f"verify.{name}", timeout_s=300, every=8)
        run.log(label="verify.result", keyword=name, output=(b.get("result") or {}).get("output"), verification=b.get("verification"))
print("done", run.path)
