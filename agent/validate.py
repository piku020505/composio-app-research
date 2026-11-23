#!/usr/bin/env python3
"""Verification loop 1: deterministic checks on agent output.

Runs without network. Catches schema errors and internal contradictions
that an LLM researcher commonly makes, and writes a list of records that
must go back to a re-check agent.

usage: python3 agent/validate.py research/pass1 > verification/loop1_flags.json
"""
import json, sys, glob, os, re
from urllib.parse import urlparse

ALLOWED = {
    "primary_auth": {"OAuth2", "API key", "Bearer token", "Basic", "JWT", "Bot token", "Signature/HMAC", "none", "other"},
    "access": {"self-serve-free", "self-serve-trial", "paid-plan", "admin-approval", "partner-gated", "no-public-api"},
    "api_type": {"REST", "GraphQL", "SOAP", "gRPC", "SDK/CLI only", "none"},
    "api_breadth": {"broad", "moderate", "narrow", "none"},
    "existing_mcp": {"official", "community", "none-found"},
    "buildability": {"ready", "ready-with-friction", "blocked"},
    "confidence": {"high", "medium", "low"},
}
REQUIRED = ["id", "app", "category", "what_it_does", "auth_methods", "primary_auth", "access",
            "api_type", "api_breadth", "existing_mcp", "buildability", "main_blocker",
            "human_needed", "confidence", "evidence"]

# hosts that are third-party aggregators, not primary sources
SECONDARY = ("medium.com", "reddit.com", "stackoverflow.com", "g2.com", "capterra", "zapier.com",
             "rapidapi.com", "apitracker", "pipedream.com", "make.com", "n8n.io", "youtube.com")


def load(folder):
    rows = []
    for f in sorted(glob.glob(os.path.join(folder, "*.json"))):
        rows.extend(json.load(open(f)))
    return rows


def check(r):
    flags = []
    for k in REQUIRED:
        if k not in r or r[k] in (None, ""):
            flags.append(f"missing:{k}")
    for k in ("primary_auth", "access", "api_breadth", "existing_mcp", "buildability", "confidence"):
        if k in r and r[k] not in ALLOWED[k]:
            flags.append(f"bad_value:{k}={r[k]}")
    for t in r.get("api_type", []) or []:
        if t not in ALLOWED["api_type"]:
            flags.append(f"bad_value:api_type={t}")
    if r.get("primary_auth") not in (r.get("auth_methods") or []):
        flags.append("primary_auth_not_in_auth_methods")

    a, b = r.get("access"), r.get("buildability")
    # contradictions
    if a == "no-public-api" and b != "blocked":
        flags.append("contradiction:no-public-api_but_not_blocked")
    if a in ("self-serve-free", "self-serve-trial") and b == "blocked":
        flags.append("contradiction:self-serve_but_blocked")
    if a in ("paid-plan", "admin-approval", "partner-gated") and b == "ready":
        flags.append("contradiction:gated_but_ready")
    if b == "ready" and str(r.get("main_blocker", "")).lower() not in ("none", ""):
        flags.append("contradiction:ready_but_has_blocker")
    if b != "ready" and str(r.get("main_blocker", "")).lower() in ("none", ""):
        flags.append("contradiction:not_ready_but_no_blocker")
    if r.get("existing_mcp") in ("official", "community") and not r.get("mcp_url"):
        flags.append("mcp_claimed_without_url")
    if r.get("api_breadth") == "none" and a != "no-public-api" and "SDK/CLI only" not in (r.get("api_type") or []):
        flags.append("contradiction:no_api_surface_but_access_given")

    ev = r.get("evidence") or {}
    for k in ("auth", "access", "api"):
        u = ev.get(k)
        if not u or not str(u).startswith("http"):
            flags.append(f"no_evidence:{k}")
        elif any(s in urlparse(u).netloc for s in SECONDARY):
            flags.append(f"secondary_source:{k}")
    if r.get("confidence") == "low":
        flags.append("low_confidence")
    return flags


def main():
    rows = load(sys.argv[1] if len(sys.argv) > 1 else "research/pass1")
    ids = sorted(r.get("id") for r in rows)
    out = {"n_records": len(rows), "missing_ids": sorted(set(range(1, 101)) - set(ids)),
           "duplicate_ids": sorted({i for i in ids if ids.count(i) > 1}), "flagged": []}
    for r in sorted(rows, key=lambda x: x.get("id", 0)):
        f = check(r)
        if f:
            out["flagged"].append({"id": r.get("id"), "app": r.get("app"), "flags": f})
    out["n_flagged"] = len(out["flagged"])
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
