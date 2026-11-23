#!/usr/bin/env python3
"""Toolkit-research agent: app name in -> verified JSON record out.

Pipeline (same loop that produced the published dataset):
  1. RESEARCH  - an LLM agent with web_search + web_fetch tools reads the
                 vendor's own docs and fills the schema in RESEARCH_SPEC.md,
                 citing a URL for every field.
  2. VALIDATE  - validate.py runs deterministic checks (schema, allowed values,
                 contradictions such as "gated but ready", missing evidence).
  3. RE-CHECK  - every flagged or low-confidence record goes to a second,
                 adversarial agent that must re-open the cited URLs and either
                 confirm or correct each field.
  4. HUMAN     - a random sample across passes is checked by hand against the
                 docs; results go into verification/human_check.json.

Usage:
  export ANTHROPIC_API_KEY=...
  python3 agent/research_agent.py --apps research/apps.tsv --ids 1-10 --out research/pass1/custom.json
  python3 agent/research_agent.py --recheck research/pass1 --flags verification/loop1_flags_raw.json --out research/pass2

Requires: pip install anthropic
"""
import argparse, csv, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor

try:
    import anthropic
except ImportError:
    sys.exit("pip install anthropic")

MODEL = os.environ.get("RESEARCH_MODEL", "claude-sonnet-4-5-20250929")
HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = open(os.path.join(HERE, "RESEARCH_SPEC.md"), encoding="utf-8").read()
TOOLS = [
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 8},
    {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 8},
]
client = anthropic.Anthropic()


def _json_from(text):
    m = re.search(r"```json\s*(.*?)```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    return json.loads(m.group(1))


def _run(prompt):
    for attempt in range(3):
        try:
            msg = client.messages.create(
                model=MODEL, max_tokens=4000, tools=TOOLS,
                system=SPEC + "\n\nReturn ONE JSON object for the app, inside a ```json block.",
                messages=[{"role": "user", "content": prompt}],
                extra_headers={"anthropic-beta": "web-fetch-2025-09-10"},
            )
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            return _json_from(text)
        except Exception as e:  # retry on rate limits / bad JSON
            print(f"  retry {attempt+1}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    return None


def research(row):
    p = (f"Research app #{row['id']}: {row['app']} (hint: {row['hint']}), category '{row['category']}'. "
         "Open the official auth docs, the page explaining how a developer obtains credentials, and the API reference. "
         "Search '<app> MCP server'. Cite a URL for every field.")
    rec = _run(p)
    if rec:
        rec.update({"id": int(row["id"]), "app": row["app"], "category": row["category"]})
    return rec


def recheck(rec, flags):
    p = ("You are a skeptical reviewer. A first-pass agent produced the record below. "
         f"Automated checks raised these flags: {flags}.\n"
         "Re-open every evidence URL (and search again where evidence is missing or secondary). "
         "For each field, confirm it or correct it. Prefer the vendor's own docs. "
         "Add a key 'changes' listing every field you changed and why (empty list if none).\n\n"
         + json.dumps(rec, indent=1))
    return _run(p)


def parse_ids(s):
    out = set()
    for part in s.split(","):
        a, _, b = part.partition("-")
        out |= set(range(int(a), int(b or a) + 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apps", default=os.path.join(HERE, "..", "research", "apps.tsv"))
    ap.add_argument("--ids", default="1-100")
    ap.add_argument("--out", required=True)
    ap.add_argument("--recheck", help="folder of pass-1 JSON files")
    ap.add_argument("--flags", help="validate.py output")
    ap.add_argument("--workers", type=int, default=5)
    a = ap.parse_args()

    if a.recheck:
        rows = {}
        for f in sorted(os.listdir(a.recheck)):
            if not f.endswith(".json"):
                continue
            for r in json.load(open(os.path.join(a.recheck, f), encoding="utf-8")):
                rows[r["id"]] = r
        flagged = {x["id"]: x["flags"] for x in json.load(open(a.flags, encoding="utf-8"))["flagged"]}
        with ThreadPoolExecutor(a.workers) as ex:
            fixed = list(ex.map(lambda i: recheck(rows[i], flagged[i]), flagged))
        os.makedirs(a.out, exist_ok=True)
        json.dump([r for r in fixed if r], open(os.path.join(a.out, "rechecked.json"), "w", encoding="utf-8"), indent=1)
        return

    want = parse_ids(a.ids)
    rows = [r for r in csv.DictReader(open(a.apps, encoding="utf-8"), delimiter="\t") if int(r["id"]) in want]
    with ThreadPoolExecutor(a.workers) as ex:
        recs = list(ex.map(research, rows))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    json.dump([r for r in recs if r], open(a.out, "w", encoding="utf-8"), indent=1)
    print(f"wrote {sum(1 for r in recs if r)}/{len(rows)} records -> {a.out}")


if __name__ == "__main__":
    main()
