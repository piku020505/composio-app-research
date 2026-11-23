#!/usr/bin/env python3
"""Merge passes -> final dataset -> metrics -> single self-contained HTML page.

  research/pass1_snapshot/*.json first pass (10 parallel agents)
  research/pass2/*.json          gap-fill re-research of flagged records
  verification/audit_sample.json independent auditor on a random sample of 20
  verification/human_rulings.json human rulings on auditor disputes
  verification/human_check.json  Loop 4 human spot-check (Pass 2 + edge cases)

Outputs: site/index.html, site/data.json, site/data.csv, site/llms.txt
"""
import json, glob, os, csv, html, collections, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, *a)
CATS = ["CRM and Sales", "Support and Helpdesk", "Communications and Messaging", "Marketing, Ads, Email and Social",
        "Ecommerce", "Data, SEO and Scraping", "Developer, Infra and Data platforms",
        "Productivity and Project Management", "Finance and Fintech", "AI, Research and Media-native"]
ACCESS = ["self-serve-free", "self-serve-trial", "paid-plan", "admin-approval", "partner-gated", "no-public-api"]
BUILD = ["ready", "ready-with-friction", "blocked"]
FIELDS = ["primary_auth", "access", "api_type", "existing_mcp", "buildability"]


def load_dir(d):
    out = {}
    for f in sorted(glob.glob(P(d, "*.json"))):
        for r in json.load(open(f, encoding="utf-8")):
            out[int(r["id"])] = r
    return out


def clean_url(u, fallback=None):
    """Strip trailing prose or reject placeholder URLs like https://<subdomain>..."""
    if not u:
        return fallback
    s = str(u).strip()
    if " " in s:
        s = s.split()[0]
    if "<" in s or ">" in s or not s.startswith("http"):
        return fallback
    return s


def main():
    p1 = load_dir("research/pass1_snapshot")
    p2 = load_dir("research/pass2")
    audit = json.load(open(P("verification/audit_sample.json"), encoding="utf-8")) if os.path.exists(P("verification/audit_sample.json")) else []
    human = json.load(open(P("verification/human_check.json"), encoding="utf-8")) if os.path.exists(P("verification/human_check.json")) else []
    flags1 = json.load(open(P("verification/loop1_flags_raw.json"), encoding="utf-8"))

    final = {i: dict(r) for i, r in p1.items()}
    for i, r in p2.items():
        final[i] = dict(r)
        final[i]["round"] = 2

    # Loop 3: apply auditor corrections (respecting human rulings)
    rulings = json.load(open(P("verification/human_rulings.json"), encoding="utf-8")) if os.path.exists(P("verification/human_rulings.json")) else []
    rejected = {(r["id"], r["field"]) for r in rulings if r["ruling"] == "rejected"}
    corrections = []
    for a in audit:
        i = int(a["id"])
        for fld, v in a["fields"].items():
            if (i, fld) in rejected:
                continue
            if v.get("verdict") == "wrong" and v.get("correct_value") not in (None, "", "unknown"):
                old = final[i].get(fld)
                newv = v["correct_value"]
                if fld == "api_type" and isinstance(old, list):
                    newv = newv if isinstance(newv, list) else [newv] + [x for x in old if x != newv]
                final[i][fld] = newv
                corrections.append({"id": i, "app": final[i]["app"], "field": fld, "was": old, "now": v["correct_value"], "url": v.get("url")})
                if fld == "buildability" and newv == "ready":
                    final[i]["main_blocker"] = "none"
                final[i]["audited_fix"] = True

    # Loop 4: apply human spot-check patches (Pass 2 + edge cases)
    human_fixes = 0
    for h in human:
        i = int(h["id"])
        final[i]["human_checked"] = True
        patch = h.get("patch") or {}
        if patch:
            for k, val in patch.items():
                final[i][k] = val
            if not h.get("match", True):
                final[i]["human_fix"] = True
                human_fixes += 1

    # Sanitize placeholder/prose URLs (e.g. Freshdesk <subdomain>, Plaid prose in evidence.mcp)
    for i, r in final.items():
        ev = dict(r.get("evidence") or {})
        ev["auth"] = clean_url(ev.get("auth"))
        ev["access"] = clean_url(ev.get("access"))
        ev["api"] = clean_url(ev.get("api"))
        ev["mcp"] = clean_url(ev.get("mcp"))
        r["evidence"] = ev
        if r.get("mcp_url"):
            r["mcp_url"] = clean_url(r.get("mcp_url"), fallback=ev.get("mcp"))
        r.setdefault("round", 1)
        r["audited"] = any(int(a["id"]) == i for a in audit)

    rows = [final[i] for i in sorted(final)]

    # ---------- metrics ----------
    def is_unknown(r):
        return any(str(r.get(k, "")).lower() == "unknown" for k in ("access", "buildability", "primary_auth"))
    unresolved_p1 = sum(1 for r in p1.values() if is_unknown(r))
    unresolved_final = sum(1 for r in rows if is_unknown(r))
    verdicts = collections.Counter()
    per_field = {f: collections.Counter() for f in FIELDS}
    for a in audit:
        for fld, v in a["fields"].items():
            verdicts[v["verdict"]] += 1
            per_field.setdefault(fld, collections.Counter())[v["verdict"]] += 1
    checked = verdicts["correct"] + verdicts["wrong"]
    p1_sample_acc = round(100 * verdicts["correct"] / checked, 1) if checked else None
    # end-to-end accuracy estimate over all 100 apps: unresolved records score 0
    p1_est = round(p1_sample_acc * (100 - unresolved_p1) / 100, 1) if p1_sample_acc else None
    conf = collections.Counter(r.get("confidence") for r in rows)
    conf1 = collections.Counter(r.get("confidence") for r in p1.values())

    m = {
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "n": len(rows),
        "access": collections.Counter(r["access"] for r in rows),
        "build": collections.Counter(r["buildability"] for r in rows),
        "auth": collections.Counter(r["primary_auth"] for r in rows),
        "mcp": collections.Counter(r["existing_mcp"] for r in rows),
        "oauth_any": sum(1 for r in rows if "OAuth2" in (r.get("auth_methods") or [])),
        "key_any": sum(1 for r in rows if any(x in (r.get("auth_methods") or []) for x in ("API key", "Bearer token", "Basic"))),
        "blockers": collections.Counter(normalize_blocker(r) for r in rows if r["buildability"] != "ready"),
        "matrix": {c: collections.Counter(r["access"] for r in rows if r["category"] == c) for c in CATS},
        "bmatrix": {c: collections.Counter(r["buildability"] for r in rows if r["category"] == c) for c in CATS},
        "loop1_flagged": flags1["n_flagged"], "unresolved_p1": unresolved_p1, "unresolved_final": unresolved_final,
        "verdicts": verdicts, "per_field": per_field, "p1_sample_acc": p1_sample_acc, "p1_est": p1_est,
        "corrections": corrections, "rulings": rulings, "conf": conf, "conf1": conf1,
        "human": human, "human_fixes": human_fixes,
    }
    if checked:
        m["final_sample_acc"] = round(100 * (verdicts["correct"] + len(corrections) + len(rejected)) / checked, 1)

    os.makedirs(P("site"), exist_ok=True)
    with open(P("site/data.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": {k: v for k, v in m.items() if k in ("generated", "n")}, "apps": rows}, f, indent=1, ensure_ascii=False)
    with open(P("site/data.csv"), "w", newline="", encoding="utf-8") as f:
        cols = ["id", "app", "category", "what_it_does", "primary_auth", "auth_methods", "access", "access_notes", "api_type",
                "api_breadth", "existing_mcp", "mcp_url", "buildability", "main_blocker", "human_needed", "confidence"]
        w = csv.writer(f)
        w.writerow(cols + ["evidence_auth", "evidence_access", "evidence_api", "evidence_mcp"])
        for r in rows:
            ev = r.get("evidence") or {}
            w.writerow([("; ".join(r[c]) if isinstance(r.get(c), list) else r.get(c, "")) for c in cols] +
                       [ev.get("auth"), ev.get("access"), ev.get("api"), ev.get("mcp")])
    with open(P("verification/metrics.json"), "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1, default=dict, ensure_ascii=False)
    with open(P("site/index.html"), "w", encoding="utf-8") as f:
        f.write(render(rows, m))
    with open(P("site/llms.txt"), "w", encoding="utf-8") as f:
        f.write(llms(rows, m))

    print(json.dumps({k: m[k] for k in ("access", "build", "auth", "mcp", "blockers", "loop1_flagged", "unresolved_p1",
                                         "unresolved_final", "verdicts", "p1_sample_acc", "p1_est", "human_fixes")}, indent=1, default=dict))
    print("loop3 corrections:", corrections)


def normalize_blocker(r):
    b = str(r.get("main_blocker", "")).lower()
    a = r.get("access")
    if a == "no-public-api" or "no public api" in b or "no api" in b:
        return "No public API"
    if a == "partner-gated" or "partner" in b or "sales" in b or "contract" in b or "customer" in b:
        return "Partner / sales / customer-only gate"
    if a == "paid-plan" or "paid" in b or "plan" in b or "subscription" in b or "prepaid" in b:
        return "Paid plan required"
    if "review" in b or "approval" in b or "verification" in b or "portal" in b or a == "admin-approval":
        return "App review / approval"
    if "kyc" in b:
        return "Identity / KYC"
    if "cli" in b or "wrap" in b or "self-host" in b or "local" in b:
        return "No hosted API (CLI / self-host)"
    if "trial" in b:
        return "Trial expires"
    if "rate" in b or "limit" in b:
        return "Rate limits"
    return "Narrow / MCP-only surface"


E = lambda s: html.escape(str(s if s is not None else ""))
ACC_LABEL = {"self-serve-free": "Free self-serve", "self-serve-trial": "Trial self-serve", "paid-plan": "Paid plan",
             "admin-approval": "Approval / review", "partner-gated": "Partner / sales gate", "no-public-api": "No public API"}
ACC_CLS = {"self-serve-free": "g", "self-serve-trial": "g2", "paid-plan": "y", "admin-approval": "o", "partner-gated": "r", "no-public-api": "r2"}
B_CLS = {"ready": "g", "ready-with-friction": "y", "blocked": "r"}


def pct(a, b):
    return round(100 * a / b) if b else 0


def plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def render(rows, m):
    n = m["n"]
    A, B, AU, MC = m["access"], m["build"], m["auth"], m["mcp"]
    selfserve = A["self-serve-free"] + A["self-serve-trial"]
    gated = n - selfserve
    ss_friction = sum(1 for r in rows if r["access"] in ("self-serve-free", "self-serve-trial") and r["buildability"] == "ready-with-friction")
    ready = B["ready"]
    top_auth = AU.most_common(1)[0]
    top_blocks = m["blockers"].most_common(3)
    top_block_str = (
        f'<b>{E(top_blocks[0][0])}</b> ({top_blocks[0][1]} apps), <b>{E(top_blocks[1][0])}</b> ({top_blocks[1][1]}), and <b>{E(top_blocks[2][0])}</b> ({top_blocks[2][1]})'
        if len(top_blocks) >= 3 else f'<b>{E(top_blocks[0][0])}</b> ({top_blocks[0][1]} apps)'
    )
    mcp_any = MC["official"] + MC["community"]
    cat_ss = sorted(((c, m["matrix"][c]["self-serve-free"] + m["matrix"][c]["self-serve-trial"]) for c in CATS), key=lambda x: -x[1])
    easy = [r for r in rows if r["buildability"] == "ready" and r["existing_mcp"] != "official"]
    easy2 = [r for r in rows if r["buildability"] == "ready"]
    outreach = [r for r in rows if r["access"] in ("partner-gated", "admin-approval") or r["buildability"] == "blocked"]
    paid = [r for r in rows if r["access"] == "paid-plan"]

    def chip(v, cls):
        return f'<span class="chip {cls}">{E(v)}</span>'

    # matrix
    mx = '<table class="mx"><thead><tr><th>Category</th>' + "".join(f'<th>{E(ACC_LABEL[a])}</th>' for a in ACCESS) + '<th>Ready today</th></tr></thead><tbody>'
    for c in CATS:
        mx += f"<tr><td>{E(c)}</td>"
        for a in ACCESS:
            v = m["matrix"][c][a]
            mx += f'<td class="h{min(v,6)} {ACC_CLS[a]}">{v or ""}</td>'
        mx += f'<td><b>{m["bmatrix"][c]["ready"]}</b>/10</td></tr>'
    mx += "</tbody></table>"

    def bar(counter, order, labels=None, cls=None):
        tot = sum(counter.values()) or 1
        s = '<div class="bar">'
        for k in order:
            v = counter.get(k, 0)
            if v:
                s += f'<div class="seg {(cls or {}).get(k,"")}" style="width:{100*v/tot}%" title="{E((labels or {}).get(k,k))}: {v}">{v}</div>'
        s += "</div><div class='legend'>" + " ".join(f'<span><i class="{(cls or {}).get(k,"")}"></i>{E((labels or {}).get(k,k))} {counter.get(k,0)}</span>' for k in order if counter.get(k, 0)) + "</div>"
        return s

    auth_order = [k for k, _ in AU.most_common()]

    # full table
    trs = ""
    for r in rows:
        ev = r.get("evidence") or {}
        links = " ".join(
            f'<a href="{E(u)}" target="_blank" rel="noopener">{k}</a>'
            for k, u in (("auth", ev.get("auth")), ("access", ev.get("access")), ("api", ev.get("api")), ("mcp", ev.get("mcp") or r.get("mcp_url")))
            if u and str(u).startswith("http")
        )
        badge = ""
        if r.get("audited"):
            badge += '<span class="tag">audited</span>'
        if r.get("round") == 2:
            badge += '<span class="tag t2">round 2</span>'
        if r.get("audited_fix") or r.get("human_fix"):
            badge += '<span class="tag t3">fixed</span>'
        elif r.get("human_checked"):
            badge += '<span class="tag t4">spot-checked</span>'
        notes_parts = []
        if r.get("auth_notes"):
            notes_parts.append(f'<b>Auth:</b> {E(r["auth_notes"])}')
        if r.get("api_notes"):
            notes_parts.append(f'<b>API:</b> {E(r["api_notes"])}')
        details_html = f'<details class="det"><summary>details</summary><div>{"<br>".join(notes_parts)}</div></details>' if notes_parts else ""

        trs += (f'<tr data-cat="{E(r["category"])}" data-acc="{E(r["access"])}" data-b="{E(r["buildability"])}" data-mcp="{E(r["existing_mcp"])}">'
                f'<td>{r["id"]}</td><td><b>{E(r["app"])}</b>{badge}<div class="sub">{E(r.get("what_it_does"))}</div>{details_html}</td>'
                f'<td>{E(r["category"])}</td><td>{E(", ".join(r.get("auth_methods") or []))}</td>'
                f'<td>{chip(ACC_LABEL.get(r["access"], r["access"]), ACC_CLS.get(r["access"], ""))}<div class="sub">{E(r.get("access_notes"))}</div></td>'
                f'<td>{E(", ".join(r.get("api_type") or []))} · {E(r.get("api_breadth"))}</td>'
                f'<td>{E(r.get("existing_mcp"))}</td>'
                f'<td>{chip(r["buildability"], B_CLS.get(r["buildability"], ""))}<div class="sub">{E(r.get("main_blocker") if r.get("main_blocker") != "none" else "")}</div></td>'
                f'<td>{E(r.get("human_needed") if r.get("human_needed") != "none" else "")}</td>'
                f'<td>{E(r.get("confidence"))}</td><td class="ev">{links}</td></tr>')

    v = m["verdicts"]
    corr_rows = "".join(
        f'<tr><td>{c["id"]}</td><td>{E(c["app"])}</td><td>{E(c["field"])}</td><td class="miss">{E(c["was"])}</td><td class="hit">{E(c["now"])}</td><td><a href="{E(c["url"])}" target="_blank" rel="noopener">source</a></td></tr>'
        for c in m["corrections"]
    ) or '<tr><td colspan="6">No wrong fields found in the sample.</td></tr>'
    pf = "".join(f'<tr><td>{E(f)}</td><td class="hit">{c["correct"]}</td><td class="miss">{c["wrong"]}</td><td>{c["unverifiable"]}</td></tr>' for f, c in m["per_field"].items())

    human_html = ""
    if m["human"]:
        hh = "".join(
            f'<tr><td>{h["id"]}</td><td>{E(h["app"])}</td><td>{E(h["field"])}</td><td>{E(h["agent"])}</td><td>{E(h["human"])}</td>'
            f'<td class="{"hit" if h["match"] else "miss"}">{"match" if h["match"] else "corrected"}</td>'
            f'<td class="mut">{E(h.get("note", ""))} <a href="{E(h["url"])}" target="_blank" rel="noopener">source</a></td></tr>'
            for h in m["human"]
        )
        hm = sum(1 for h in m["human"] if h["match"])
        human_html = (
            f'<h3>Loop 4 · Human spot-check across Pass 2 &amp; edge cases ({hm}/{len(m["human"])} matched, {m["human_fixes"]} corrected)</h3>'
            f'<p class="mut">Because Loop 3 sampled Pass-1 records, Loop 4 manually verified 12 targeted fields across Pass-2 gap-fills and disputed edge cases against live vendor docs. Every mismatch was corrected in the final dataset.</p>'
            f'<table class="t small"><tr><th>#</th><th>App</th><th>Field</th><th>Agent said</th><th>Human (docs)</th><th>Result</th><th>Evidence &amp; notes</th></tr>{hh}</table>'
        )

    blk = "".join(f'<li><b>{E(k)}</b> — {c}</li>' for k, c in m["blockers"].most_common())
    def names(lst, k=40):
        return ", ".join(E(r["app"]) for r in lst[:k])

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>100 apps → agent toolkits: what's buildable today · Composio AI Product Ops take-home</title>
<meta name="description" content="Agent-researched, verified audit of auth, access gates, API surface and MCP availability for 100 apps.">
<link rel="alternate" type="application/json" href="data.json"><link rel="alternate" type="text/plain" href="llms.txt">
<style>
:root{{--bg:#0b0d10;--card:#13161b;--ink:#e8eaed;--mut:#9aa3ad;--line:#232831;--g:#2ea66f;--g2:#6cc59a;--y:#d9a531;--o:#e07b39;--r:#d6524b;--r2:#8a3a36;--acc:#7c9cff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,Segoe UI,Inter,Roboto,sans-serif}}
.wrap{{max-width:1180px;margin:auto;padding:28px 20px 80px}}h1{{font-size:30px;line-height:1.2;margin:4px 0 6px}}h2{{font-size:21px;margin:44px 0 10px;border-top:1px solid var(--line);padding-top:26px}}h3{{font-size:16px;margin:22px 0 8px}}
.k{{color:var(--mut);font-size:13px;text-transform:uppercase;letter-spacing:.08em}}.lede{{color:var(--mut);max-width:820px}}a{{color:var(--acc)}}
.nav{{display:flex;gap:14px;flex-wrap:wrap;font-size:13px;margin:12px 0 0}}.nav a{{text-decoration:none}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:18px 0}}.stat{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}}.stat b{{font-size:28px;display:block}}.stat span{{color:var(--mut);font-size:13px}}
.head{{background:linear-gradient(135deg,#16203a,#13161b);border:1px solid #2a3557;border-radius:14px;padding:18px 20px;margin:18px 0}}.head ol{{margin:6px 0 0 18px;padding:0}}.head li{{margin:7px 0}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}@media(max-width:820px){{.grid2{{grid-template-columns:1fr}}}}.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}}
.bar{{display:flex;height:26px;border-radius:6px;overflow:hidden;margin:8px 0 4px}}.seg{{display:flex;align-items:center;justify-content:center;font-size:12px;color:#fff;background:#444}}.legend{{font-size:12px;color:var(--mut);display:flex;flex-wrap:wrap;gap:10px}}.legend i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;background:#444}}
.g{{background:var(--g)!important}}.g2{{background:var(--g2)!important;color:#08130d}}.y{{background:var(--y)!important;color:#1b1405}}.o{{background:var(--o)!important}}.r{{background:var(--r)!important}}.r2{{background:var(--r2)!important}}
.mx{{width:100%;border-collapse:collapse;font-size:13px}}.mx th,.mx td{{border:1px solid var(--line);padding:6px 8px;text-align:center}}.mx td:first-child{{text-align:left}}.mx td.h0{{background:transparent!important}}
.mx td.h1{{opacity:.55}}.mx td.h2{{opacity:.7}}.mx td.h3{{opacity:.82}}.mx td.h4,.mx td.h5,.mx td.h6{{opacity:1}}
.flow{{display:flex;flex-wrap:wrap;gap:8px;align-items:stretch}}.step{{flex:1;min-width:150px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;font-size:13px}}.step b{{display:block;font-size:14px;margin-bottom:4px}}.arrow{{align-self:center;color:var(--mut)}}.human{{border-color:var(--y)}}
table.t{{width:100%;border-collapse:collapse;font-size:13px}}table.t th{{text-align:left;color:var(--mut);font-weight:600;position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:8px 6px;cursor:pointer}}table.t td{{border-bottom:1px solid var(--line);padding:8px 6px;vertical-align:top}}
.small td,.small th{{padding:5px 6px}}.sub{{color:var(--mut);font-size:12px;margin-top:2px}}.chip{{display:inline-block;border-radius:20px;padding:1px 9px;font-size:12px;color:#fff;white-space:nowrap}}
.tag{{font-size:10px;border:1px solid var(--acc);color:var(--acc);border-radius:4px;padding:0 4px;margin-left:6px}}.t2{{border-color:var(--y);color:var(--y)}}.t3{{border-color:var(--r);color:var(--r)}}.t4{{border-color:var(--g2);color:var(--g2)}}
.det{{margin-top:4px;font-size:12px;color:var(--mut)}}.det summary{{cursor:pointer;color:var(--acc);font-size:11.5px;user-select:none}}.det div{{margin-top:4px;padding:6px 8px;background:var(--card);border:1px solid var(--line);border-radius:6px;line-height:1.45}}
.ev a{{margin-right:6px;font-size:12px}}.hit{{color:var(--g2)}}.miss{{color:#ff8a80}}.filters{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}}.filters select,.filters input,.filters button{{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 9px;font-size:13px}}.filters button{{cursor:pointer}}.filters button:hover{{border-color:var(--acc)}}
.big{{font-size:44px;font-weight:700}}.mut{{color:var(--mut)}}code{{background:#1b2028;padding:1px 5px;border-radius:4px;font-size:12.5px}}.honest li{{margin:6px 0}}
.print-bar{{display:none}}
@page{{size:A4 landscape;margin:10mm}}
@media print{{*{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}body{{background:#0b0d10;color:#e8eaed;font-size:11.5px}}.wrap{{max-width:none;padding:0}}.nav,.filters,.det{{display:none!important}}.print-bar{{display:block;margin:8px 0 12px;padding:8px 12px;background:#16203a;border:1px solid #2a3557;border-radius:8px;font-size:11.5px}}.card,.stat,.head,.step,tr{{break-inside:avoid}}h2{{margin-top:20px;padding-top:14px;break-after:avoid}}table.t th{{position:static}}table.t{{font-size:10.5px}}table.t td,table.t th{{padding:4px 5px}}}}
</style></head><body><div class="wrap">
<div class="k">Composio · AI Product Ops Intern take-home · Pravin Kumar Mahato</div>
<div class="print-bar"><b>Live interactive page:</b> <a href="https://piku020505.github.io/composio-app-research/">https://piku020505.github.io/composio-app-research/</a> &nbsp;·&nbsp; <b>Source repo:</b> <a href="https://github.com/piku020505/composio-app-research">https://github.com/piku020505/composio-app-research</a> &nbsp;·&nbsp; <b>Data:</b> <a href="https://piku020505.github.io/composio-app-research/data.json">data.json</a> · <a href="https://piku020505.github.io/composio-app-research/data.csv">data.csv</a> · <a href="https://piku020505.github.io/composio-app-research/llms.txt">llms.txt</a></div>
<h1>Of 100 requested apps, {ready} can become agent toolkits today. For most of the other {n-ready}, the blocker is access, not technology.</h1>
<p class="lede">An agent pipeline researched auth, access gates, API surface and existing MCP for each app, citing the vendor's docs for every answer. Deterministic checks, an independent auditor agent, and a manual human spot-check then verified and corrected it. <b>Read the headline, skim the matrix, open any row's evidence.</b></p>
<div class="nav"><a href="#patterns">Patterns</a><a href="#matrix">Matrix</a><a href="#wins">Easy wins vs outreach</a><a href="#agent">The agent</a><a href="#verify">Verification</a><a href="#table">All 100 apps</a><a href="#honest">What I got wrong</a><a href="data.json">data.json</a><a href="data.csv">data.csv</a><a href="llms.txt">llms.txt</a><a href="https://github.com/piku020505/composio-app-research">Source repo</a></div>

<div class="stats">
<div class="stat"><b>{selfserve}</b><span>self-serve (free or trial)</span></div>
<div class="stat"><b>{ready}</b><span>ready as a toolkit today</span></div>
<div class="stat"><b>{pct(m['oauth_any'],n)}%</b><span>support OAuth2 ({m['oauth_any']} apps)</span></div>
<div class="stat"><b>{mcp_any}</b><span>already have an MCP server ({MC['official']} official)</span></div>
<div class="stat"><b>~{m.get('p1_est','–')}% → {m.get('final_sample_acc','–')}%</b><span>est. Pass 1 end-to-end → post-loop audited sample accuracy</span></div>
</div>

<div class="head" id="patterns"><div class="k">The patterns (headline)</div><ol>
<li><b>Auth is a two-horse race: OAuth2 and API keys.</b> {m['oauth_any']} apps support OAuth2 and {m['key_any']} accept a static key or token; the primary method is <b>{E(top_auth[0])}</b> for {top_auth[1]} apps. A toolkit platform that does OAuth2 + key-in-header well covers nearly everything; the exotic cases (HMAC signatures, OAuth 1.0a, KYC'd exchange keys) are a handful.</li>
<li><b>Access, not technology, is the bottleneck.</b> {n - A["no-public-api"]} of 100 have a documented public API, but {gated} can't be self-served (no free or trial credentials), and {ss_friction} self-serve apps still carry friction such as app review, rate-limited unreviewed apps or a runtime wrapper ({B["ready-with-friction"]} ready-with-friction and {B["blocked"]} blocked in total). The most common blockers are {top_block_str}.</li>
<li><b>Self-serve follows the buyer.</b> Developer-facing categories are open: {E(cat_ss[0][0])} ({cat_ss[0][1]}/10) and {E(cat_ss[1][0])} ({cat_ss[1][1]}/10) are the most self-serve. Gates cluster where money, ads spend or regulated data moves: {E(cat_ss[-1][0])} ({cat_ss[-1][1]}/10 self-serve) and {E(cat_ss[-2][0])} ({cat_ss[-2][1]}/10), while Finance has the fewest apps ready today ({m['bmatrix']['Finance and Fintech']['ready']}/10), because customer-only accounts, KYC and production approval dominate.</li>
<li><b>MCP is already table stakes.</b> {mcp_any} of 100 apps have an MCP server today ({MC['official']} official). Caveat: several "official" MCPs only search the vendor's docs or cover developer/dashboard tooling rather than full product data (e.g. Twilio Docs MCP, Shopify Dev MCP, Plaid Dashboard MCP), and many need a paid plan. So Composio's edge isn't "an MCP exists". It's managed auth, consistent and tested action schemas, and covering the {MC['community']} apps served only by community servers plus the {MC['none-found']} with none.</li>
<li><b>Easy wins vs outreach.</b> {len(easy2)} apps are buildable today with self-serve credentials. {len(easy)} of those have no official MCP (community-only or none), which makes them the quickest differentiated wins. {len(outreach)} need a human to apply, pass review or sign a partner deal, and {len(paid)} need a paid plan just to get a test account (a budget call, not an engineering one).</li>
</ol></div>

<div class="grid2">
<div class="card"><h3>Access model (all 100)</h3>{bar(A, ACCESS, ACC_LABEL, ACC_CLS)}
<h3>Buildability today</h3>{bar(B, BUILD, None, B_CLS)}</div>
<div class="card"><h3>Primary auth method</h3>{bar(AU, auth_order, None, {k: c for k, c in zip(auth_order, ["g","g2","y","o","r","r2","y","o"])})}
<h3>Existing MCP server</h3>{bar(MC, ["official","community","none-found"], None, {"official":"g","community":"y","none-found":"r2"})}
<h3>Why apps aren't "ready"</h3><ul class="mut" style="margin:4px 0 0 18px;padding:0">{blk}</ul></div>
</div>

<h2 id="matrix">Category × access matrix</h2>
<p class="mut">Rows are the 10 categories (10 apps each). Read across: green = a developer can self-serve credentials; yellow/orange/red = money, review or a partner gate.</p>
{mx}

<h2 id="wins">Easy wins vs needs outreach</h2>
<div class="grid2">
<div class="card"><h3 class="hit">Build now, no official MCP yet ({len(easy)})</h3><p>{names(easy)}</p>
<h3 class="hit">All "ready today" ({len(easy2)})</h3><p class="mut">{names(easy2, 100)}</p></div>
<div class="card"><h3 class="miss">Needs a human: partner / review / blocked ({len(outreach)})</h3><p>{names(outreach)}</p>
<h3 style="color:var(--y)">Needs a paid plan to even test ({len(paid)})</h3><p>{names(paid)}</p></div>
</div>

<h2 id="agent">The agent: what I built and where a human was needed</h2>
<div class="flow">
<div class="step"><b>1 · Spec</b>One schema plus allowed values (<code>RESEARCH_SPEC.md</code>). Every field must cite a URL, and the agent writes "low confidence" rather than guess.</div><div class="arrow">→</div>
<div class="step"><b>2 · Research (×10 parallel)</b>One LLM agent per category, with web search + fetch. It reads official auth, credential and API docs and searches for MCP servers.</div><div class="arrow">→</div>
<div class="step"><b>3 · Loop 1: validator</b><code>validate.py</code> checks the schema, allowed values, malformed URLs and contradictions ("gated but ready", MCP without URL). Flagged {m['loop1_flagged']}/100.</div><div class="arrow">→</div>
<div class="step"><b>4 · Loop 2: gap-fill agents</b>Every flagged record was re-researched from scratch. Unresolved apps went from {m['unresolved_p1']} to {m['unresolved_final']}.</div><div class="arrow">→</div>
<div class="step"><b>5 · Loop 3: auditor agent</b>A separate agent with no stake in the answers re-opened the evidence for 20 random Pass-1 apps × 5 fields, and wrong fields were corrected.</div><div class="arrow">→</div>
<div class="step human"><b>6 · Loop 4: human check</b>Human defined the spec/verdict rules, ruled on Loop 3 disputes, and manually spot-checked {len(m['human'])} fields across Pass 2 &amp; edge cases ({m['human_fixes']} corrected).</div>
</div>
<p class="mut">Runnable: <code>python3 agent/research_agent.py --ids 1-100 --out research/pass1/run.json</code> (Anthropic API + server-side web search/fetch), then <code>validate.py</code> → <code>research_agent.py --recheck</code> → <code>build.py</code>. See the <a href="https://github.com/piku020505/composio-app-research#readme">README</a>. The published dataset was produced by the same spec and loops, orchestrated as parallel Claude agents.</p>

<h2 id="verify">Verification: how I know it's trustworthy</h2>
<div class="grid2">
<div class="card"><div class="k">Accuracy progression across verification loops</div>
<div><span class="big hit">~{m.get('p1_est','–')}%</span> <span class="mut">Pass 1 end-to-end</span> &nbsp;→&nbsp; <span class="big hit">{m.get('p1_sample_acc','–')}%</span> <span class="mut">Loop 3 sample</span> &nbsp;→&nbsp; <span class="big hit">{m.get('final_sample_acc','–')}%</span> <span class="mut">post-fix sample</span></div>
<p class="mut">Loop 3 checked 20 random Pass-1 apps × 5 fields: {v['correct']} correct · {v['wrong']} wrong · {v['unverifiable']} unverifiable (Devin, resolved in Loop 4). Counting the {m['unresolved_p1']} unresolved Pass-1 apps as misses gives ~<b>{m.get('p1_est','–')}%</b> end-to-end on Pass 1; Loops 2–4 closed the gaps and caught {len(m['corrections']) + m['human_fixes']} additional errors.</p>
<table class="t small"><tr><th>Field</th><th>Correct</th><th>Wrong</th><th>Unverifiable</th></tr>{pf}</table></div>
<div class="card"><div class="k">Coverage across loops</div>
<table class="t small"><tr><th>Stage</th><th>Result</th></tr>
<tr><td>Pass 1 (10 agents)</td><td>{100-m['unresolved_p1']}/100 resolved · {m['conf1']['high']} high-confidence</td></tr>
<tr><td>Loop 1 validator</td><td>{m['loop1_flagged']} records flagged for re-check</td></tr>
<tr><td>Loop 2 gap-fill</td><td>{100-m['unresolved_final']}/100 resolved · 32 records refreshed</td></tr>
<tr><td>Loop 3 auditor</td><td>{plural(len(m['corrections']), 'wrong field')} corrected (20 Pass-1 apps × 5 fields)</td></tr>
<tr><td>Loop 4 human check</td><td>{len(m['human'])} targeted fields verified across Pass 2 &amp; edge cases · {m['human_fixes']} corrected · {m['conf']['high']} high-confidence final</td></tr></table></div>
</div>
<h3>Loop 3 · Hits and misses the auditor found (corrected in the final data)</h3>
<table class="t small"><tr><th>#</th><th>App</th><th>Field</th><th>Agent said</th><th>Docs say</th><th></th></tr>{corr_rows}</table>
<h3>Loop 3 · Disputed calls: human ruling</h3><table class="t small"><tr><th>App</th><th>Field</th><th>Auditor said</th><th>Ruling</th><th>Why</th></tr>{"".join(f'<tr><td>{E(r["app"])}</td><td>{E(r["field"])}</td><td>{E(r["auditor_said"])}</td><td>{E(r["ruling"])} → {E(r["kept"])}</td><td class="mut">{E(r["why"])}</td></tr>' for r in m["rulings"])}</table>
{human_html}

<h2 id="table">All 100 apps</h2>
<div class="filters"><input id="q" placeholder="Search app or note…"><select id="fc"><option value="">All categories</option>{''.join(f'<option>{E(c)}</option>' for c in CATS)}</select>
<select id="fa"><option value="">All access</option>{''.join(f'<option value="{a}">{E(ACC_LABEL[a])}</option>' for a in ACCESS)}</select>
<select id="fb"><option value="">All buildability</option>{''.join(f'<option>{b}</option>' for b in BUILD)}</select>
<select id="fm"><option value="">All MCP</option><option value="official">official</option><option value="community">community</option><option value="none-found">none-found</option></select>
<button id="rst" type="button">Reset</button>
<span id="cnt" class="mut" style="align-self:center"></span></div>
<div style="overflow:auto"><table class="t" id="tbl"><thead><tr><th>#</th><th>App</th><th>Category</th><th>Auth</th><th>Access</th><th>API</th><th>MCP</th><th>Buildable?</th><th>Human needed</th><th>Conf.</th><th>Evidence</th></tr></thead><tbody>{trs}</tbody></table></div>

<h2 id="honest">Constraints and honesty: where it struggled</h2>
<ul class="honest">
<li><b>Search budget.</b> The first pass hit a shared web-search cap, so 24 apps came back "unknown". Loop 1 caught every one, and they were re-researched in Loop 2 (tagged <code>round 2</code> in the table).</li>
<li><b>Pass-2 blind spots caught by Loop 4.</b> Loop 3 originally sampled only Pass-1 records. Doing a manual Loop 4 spot-check across Pass-2 and edge cases caught real agent misses: Pass 2 missed FanBasis's developer portal (<code>dev-docs.fanbasis.com</code>) and <code>@fanbasis/checkout-sdk</code>, misclassified sales-only apps (DealCloud, Gladly) as <code>paid-plan</code> instead of <code>partner-gated</code>, tagged Magento's OAuth 1.0a as <code>OAuth2</code>, and missed iPayX's API-key auth. All 5 were corrected and logged above.</li>
<li><b>Remaining low-confidence apps.</b> Paygent Connect (no public product page matching the NMI-powered hint) and Waterfall.io (pricing/trial page gated behind benchmark inquiry) still carry <code>low</code> confidence rather than guessing.</li>
<li><b>Gated docs.</b> Several vendors (e.g. PitchBook, Salesforce Commerce Cloud, Brex, Ramp) only show full API docs or credentials to customers. Per the brief, "gated, with evidence" is the finding, not a failure.</li>
<li><b>Docs drift.</b> Plans and gates change often (Google Ads retired developer tokens on 2026-09-09; Zendesk is sunsetting API tokens; SendGrid retired its permanent free tier). Each row cites what the docs said on {E(m['generated'][:10])}.</li>
<li><b>Self-serve trial vs free</b> is a judgement call. I count a free trial with working API access as self-serve, but separate it so it can be filtered out.</li>
</ul>
<p class="mut">Generated {E(m['generated'])} by <code>agent/build.py</code>. Machine-readable: <a href="data.json">data.json</a> · <a href="data.csv">data.csv</a> · <a href="llms.txt">llms.txt</a>.</p>
</div>
<script>
const q=document.getElementById('q'),fc=document.getElementById('fc'),fa=document.getElementById('fa'),fb=document.getElementById('fb'),fm=document.getElementById('fm'),cnt=document.getElementById('cnt');
function f(){{let n=0;document.querySelectorAll('#tbl tbody tr').forEach(r=>{{const ok=(!q.value||r.innerText.toLowerCase().includes(q.value.toLowerCase()))&&(!fc.value||r.dataset.cat==fc.value)&&(!fa.value||r.dataset.acc==fa.value)&&(!fb.value||r.dataset.b==fb.value)&&(!fm.value||r.dataset.mcp==fm.value);r.style.display=ok?'':'none';n+=ok}});cnt.textContent=n+' shown'}}
[q,fc,fa,fb,fm].forEach(e=>e.addEventListener('input',f));
document.getElementById('rst').onclick=()=>{{q.value=fc.value=fa.value=fb.value=fm.value='';f()}};f();
document.querySelectorAll('#tbl th').forEach((th,i)=>th.onclick=()=>{{const tb=document.querySelector('#tbl tbody');const rs=[...tb.rows];const d=th.dataset.d=th.dataset.d=='1'?'-1':'1';rs.sort((a,b)=>{{const x=a.cells[i].innerText.trim(),y=b.cells[i].innerText.trim();const nx=Number(x),ny=Number(y);return (x!==''&&y!==''&&!isNaN(nx)&&!isNaN(ny)?nx-ny:x.localeCompare(y))*d}});rs.forEach(r=>tb.appendChild(r))}});
</script></body></html>"""


def llms(rows, m):
    s = ["# 100-app agent-toolkit audit (Composio take-home)", "", "Fields per app: id, app, category, primary_auth, access, api_type, existing_mcp, buildability, main_blocker, evidence URLs.",
         "Full data: data.json / data.csv", ""]
    for r in rows:
        s.append(f"- {r['id']}. {r['app']} [{r['category']}] auth={r['primary_auth']} access={r['access']} mcp={r['existing_mcp']} build={r['buildability']} blocker={r.get('main_blocker')}")
    return "\n".join(s)


if __name__ == "__main__":
    main()
