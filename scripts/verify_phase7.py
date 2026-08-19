"""Phase-7 verification against a RUNNING stack (make up + make seed).

Checks the three demo-critical behaviors end to end over the real wire:
  1. tools/list differs by role: analyst-full sees all 7 tools,
     analyst-local sees only the 4 local tools (never x-role wdp_reader).
  2. A forged tools/call to a WDP tool as analyst-local returns the
     structured not_authorized error (the MCP server never trusts the plan).
  3. A legitimate tools/call returns the {data, meta} envelope.
Audit-record presence (one line per tools/call) is checked by the Makefile
target via docker compose logs after this script runs.

Usage: python scripts/verify_phase7.py [--mcp-url http://mcp-server:8001]
Env:   JWT_SECRET (must match the MCP server's), MCP_URL (fallback for the flag)
"""
import argparse
import os
import sys

import httpx

from mint_jwt import mint

LOCAL_TOOLS = {
    "aggregate_assessments",
    "get_proposal",
    "search_personnel",
    "search_proposals",
}
ALL_TOOLS = LOCAL_TOOLS | {
    "retrieve_wdp_documents",
    "search_wdp_entity",
    "search_wdp_person",
}

_failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        _failures.append(label)


def rpc(client: httpx.Client, token: str, method: str, params: dict) -> dict:
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mcp-url", default=os.getenv("MCP_URL", "http://mcp-server:8001")
    )
    args = parser.parse_args()

    full_token = mint("analyst-full")
    local_token = mint("analyst-local")

    with httpx.Client(base_url=args.mcp_url, timeout=15) as client:
        body = rpc(client, full_token, "tools/list", {})
        names = {t["name"] for t in body["result"]["tools"]}
        check("analyst-full sees all 7 tools", names == ALL_TOOLS, f"got {sorted(names)}")

        body = rpc(client, local_token, "tools/list", {})
        tools = body["result"]["tools"]
        names = {t["name"] for t in tools}
        check(
            "analyst-local sees only the 4 local tools",
            names == LOCAL_TOOLS,
            f"got {sorted(names)}",
        )
        check(
            "analyst-local list carries no wdp_reader tool",
            all(t.get("x-role") != "wdp_reader" for t in tools),
        )

        body = rpc(
            client,
            local_token,
            "tools/call",
            {"name": "search_wdp_person", "arguments": {"orcid": "0000-0000-0000-0000"}},
        )
        error = body["result"].get("error") or {}
        check(
            "forged WDP call as analyst-local is not_authorized",
            error.get("code") == "not_authorized",
            f"got {body['result']}",
        )

        body = rpc(
            client,
            local_token,
            "tools/call",
            {"name": "search_proposals", "arguments": {"limit": 1}},
        )
        result = body["result"]
        check(
            "legitimate search_proposals returns the data/meta envelope",
            "data" in result and "meta" in result and result["meta"]["returned"] >= 1,
            f"meta={result.get('meta')}",
        )

    if _failures:
        print(f"\n{len(_failures)} check(s) FAILED")
        return 1
    print("\nall phase-7 checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
