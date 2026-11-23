# 100 apps → agent toolkits: what's buildable today

Composio AI Product Ops Intern take-home, by Pravin Kumar Mahato.

**Live page:** https://piku020505.github.io/composio-app-research/
**Data:** [`site/data.json`](site/data.json) · [`site/data.csv`](site/data.csv) · [`site/llms.txt`](site/llms.txt)

For each of 100 apps, an agent pipeline researched:
- auth method(s)
- self-serve vs gated access
- API surface
- existing MCP server
- a buildability verdict

Every answer cites evidence URLs. The pipeline then verified its own output across four loops.

## Pipeline

```
RESEARCH_SPEC.md ──► research_agent.py ──► validate.py ──► research_agent.py --recheck ──► auditor sample ──► human spot-check ──► build.py
 (schema + rules)    (10 parallel agents,   (loop 1:          (loop 2: re-research           (loop 3: independent   (loop 4: manual     (merge, metrics,
                      web search + fetch)    deterministic     every flagged record)          agent re-opens docs    verification of     HTML page)
                                             checks)                                          for 20 Pass-1 apps)    Pass-2 & edge cases)
```

| Step | What it does | Where a human was needed |
|---|---|---|
| Spec | Fixed schema and allowed values. Every field needs a URL, and low confidence beats guessing. | Defining "self-serve", "ready" vs "ready-with-friction" |
| Research | One agent per category. It reads the vendor's auth, credentials and API docs, then searches for MCP servers. | — |
| Loop 1: `validate.py` | Schema, malformed-URL, and allowed-value checks. Flags contradictions such as gated-but-ready, MCP without a URL, missing or secondary evidence, and low confidence. | — |
| Loop 2: gap-fill | Re-researches every flagged record from scratch. | — |
| Loop 3: auditor | A separate skeptical agent checks 20 random Pass-1 apps (seed 2026) field by field against the docs, and wrong fields are corrected. | Reviewing disputed calls (`verification/human_rulings.json`) |
| Loop 4: human spot-check | Manual verification of 12 targeted fields across Pass-2 gap-fills and edge cases against live vendor docs (`verification/human_check.json`). | Catching Pass-2 misses (FanBasis API/SDK, DealCloud/Gladly sales gates, Magento OAuth 1.0a, iPayX API key) |
| `build.py` | Merges the passes, applies verified corrections, computes patterns and accuracy, and renders `site/index.html`. | — |

## Run it

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-...

# 1. research (any id range)
python3 agent/research_agent.py --ids 1-100 --out research/pass1/run.json

# 2. loop 1: deterministic checks
python3 agent/validate.py research/pass1 > verification/loop1_flags_raw.json

# 3. loop 2: re-check every flagged record
python3 agent/research_agent.py --recheck research/pass1 --flags verification/loop1_flags_raw.json --out research/pass2

# 4. build the page + data files
python3 agent/build.py        # -> site/index.html, data.json, data.csv, llms.txt
```

`validate.py` and `build.py` need no network and no API key, so you can re-run them on the committed data:

```bash
python3 agent/validate.py research/pass1_snapshot
python3 agent/validate.py site/data.json
python3 agent/build.py
```

## Honesty notes
- **How this dataset was produced.** The published dataset came from the same spec and loops, run as parallel Claude agents with web search and fetch. `research_agent.py` is the standalone version of that agent.
- **First-pass gaps.** The first pass hit a shared web-search cap, so 24 apps came back "unknown". Loop 1 caught all of them and Loop 2 re-researched them. Both passes are committed (`research/pass1_snapshot`, `research/pass2`) so the improvement can be checked.
- **Pass-2 blind spots.** Loop 3 sampled Pass-1 records, so Loop 4 manually spot-checked Pass-2 and edge-case records against live docs and caught 5 errors (FanBasis public API/SDK, DealCloud & Gladly sales-gated classification, Magento OAuth 1.0a vs OAuth2, and iPayX API-key auth), all recorded in `verification/human_check.json`.
- **Thin evidence.** Obscure or gated apps (`Paygent Connect`, `Waterfall.io`) carry low confidence and explain why.

## Layout
```
agent/          RESEARCH_SPEC.md, research_agent.py, validate.py, build.py
research/       apps.tsv, pass1_snapshot/ (first pass), pass2/ (gap-fill)
verification/   loop1_flags_raw.json, audit_sample.json, human_rulings.json, human_check.json, metrics.json
site/           index.html (the deliverable), data.json, data.csv, llms.txt
```
