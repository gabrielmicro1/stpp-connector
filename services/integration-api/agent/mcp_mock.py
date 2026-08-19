"""Demo/phase-6 stand-in for the MCP server, behind the same MCPSession
interface the phase-7 HTTP client implements.

Serves the frozen contracts' tool definitions filtered per user role
(invariant 6's tools/list side), re-checks the role on every call, and
returns deterministic canned results shaped by each tool's outputSchema —
enough for Query-1/Query-2-shaped plans to execute end-to-end. Tests can
override any tool with `fixtures` (a result dict, a callable(args) -> dict,
or an exception to raise).
"""
import json
from contextlib import asynccontextmanager
from pathlib import Path

from agent.errors import MCPToolError
from shared.types import UserContext

# The ORCID every canned proposal includes as its "no WDP records" person
# (demo Query 2's honest-gap beat).
NO_RECORDS_ORCID_SUFFIX = "0002"

_AGG_VALUES = {
    "reviewing_component": ["DARPA", "DTRA", "ONR"],
    "fiscal_year": ["2024", "2025"],
}


def _load_tool_defs(contracts_dir: Path) -> list:
    return [
        json.loads(path.read_text())
        for path in sorted((contracts_dir / "mcp-tools").glob("*.json"))
    ]


def _meta(total: int, returned: int | None = None, truncated: bool = False) -> dict:
    return {"total": total, "returned": returned if returned is not None else total,
            "truncated": truncated}


def _proposal_row(number: str) -> dict:
    return {
        "proposal_number": number,
        "fiscal_year": "2025",
        "ssa": None,
        "opportunity_number": "OPP-25-01",
        "proposal_title": f"Mock proposal {number}",
        "submitting_entity_uei": "UEI0001MOCK",
        "submitting_entity_name": "Mock University",
        "review_type": "Standard",
        "reviewing_component": "DARPA",
        "reviewing_subcomponent": "DARPA-BTO",
        "assessment_state": "Complete",
        "approved_date": "2025-03-01",
        "review_notes": None,
        "mitigation_status": "Complete",
        "mitigation_strategy_proposal": [],
        "mitigation_explanation": None,
        "award_type": "Grant",
        "award_state": "Awarded",
        "fain": None,
        "awarded_date": "2025-04-01",
        "award_pop_start": "2025-05-01",
        "award_pop_end": "2026-05-01",
    }


def _personnel(number: str) -> list:
    return [
        {
            "person_orcid": "0000-0001-2345-0001",
            "first_name": "Avery",
            "middle_name": None,
            "last_name": "Mockperson",
            "proposal_role": "PI",
            "affiliation_name": "Mock University",
            "factor1_assessment": "No Concerns",
            "factor2_assessment": "No Concerns",
            "factor3_assessment": "No Concerns",
            "factor4_assessment": "No Concerns",
            "person_overall_assessment": "Low",
        },
        {
            "person_orcid": f"0000-0001-2345-{NO_RECORDS_ORCID_SUFFIX}",
            "first_name": "Blake",
            "middle_name": None,
            "last_name": "Nowdprecords",
            "proposal_role": "Co-PI",
            "affiliation_name": "Mock Institute",
            "factor1_assessment": "No Concerns",
            "factor2_assessment": "No Concerns",
            "factor3_assessment": "No Concerns",
            "factor4_assessment": "Prohibited Factors",
            "person_overall_assessment": "High",
        },
    ]


class MockMCPSession:
    def __init__(self, user: UserContext, tool_defs: list, fixtures: dict,
                 deny_orcids: frozenset) -> None:
        self._user = user
        self._all_defs = tool_defs
        self._fixtures = fixtures
        self._deny_orcids = deny_orcids

    async def tools_list(self) -> list:
        return [t for t in self._all_defs if t["x-role"] in self._user.roles]

    async def tools_call(self, tool: str, args: dict) -> dict:
        definition = next((t for t in self._all_defs if t["name"] == tool), None)
        if definition is None:
            raise MCPToolError("invalid_args", f"unknown tool {tool!r}")
        # Authorization re-check on EVERY call (invariant 6): the mock never
        # trusts the plan either.
        if definition["x-role"] not in self._user.roles:
            raise MCPToolError(
                "not_authorized",
                f"user {self._user.sub!r} lacks the {definition['x-role']!r} role",
            )
        for required in definition["inputSchema"].get("required", []):
            if required not in args:
                raise MCPToolError(
                    "invalid_args", f"{tool}: missing required argument {required!r}"
                )
        if tool in self._fixtures:
            fixture = self._fixtures[tool]
            if isinstance(fixture, Exception):
                raise fixture
            if callable(fixture):
                result = fixture(args)
                if isinstance(result, Exception):
                    raise result
                return result
            return fixture
        return self._canned(tool, args)

    def _canned(self, tool: str, args: dict) -> dict:
        if tool == "search_proposals":
            rows = [_proposal_row("P-2025-0101"), _proposal_row("P-2025-0102")]
            return {"data": rows, "meta": _meta(2)}
        if tool == "get_proposal":
            number = args["proposal_number"]
            record = _proposal_row(number)
            record["personnel"] = _personnel(number)
            return {"data": record, "meta": _meta(1)}
        if tool == "search_personnel":
            return {
                "data": [
                    {
                        "person_orcid": "0000-0001-2345-0001",
                        "first_name": "Avery",
                        "middle_name": None,
                        "last_name": "Mockperson",
                        "proposals": [
                            {
                                "proposal_number": "P-2025-0101",
                                "proposal_title": "Mock proposal P-2025-0101",
                                "proposal_role": "PI",
                                "person_overall_assessment": "Low",
                            }
                        ],
                    }
                ],
                "meta": _meta(1),
            }
        if tool == "aggregate_assessments":
            fields = args["group_by"]
            values = _AGG_VALUES.get(fields[0], ["Bucket A", "Bucket B", "Bucket C"])
            buckets = [
                {
                    "group": {field: value for field in fields},
                    "count": 10 + 3 * i,
                }
                for i, value in enumerate(values)
            ]
            return {
                "data": buckets,
                "meta": {**_meta(len(buckets)), "overlapping_buckets": False},
            }
        if tool == "search_wdp_person":
            orcid = args.get("orcid")
            if orcid and orcid in self._deny_orcids:
                raise MCPToolError(
                    "not_authorized", f"WDP denies access to records for {orcid}"
                )
            if orcid and orcid.endswith(NO_RECORDS_ORCID_SUFFIX):
                return {"data": [], "meta": _meta(0)}
            key = orcid or (args.get("name") or "unnamed").lower().replace(" ", "-")
            return {
                "data": [
                    {
                        "ref_id": f"wdp-person-{key}",
                        "orcid": orcid,
                        "name": args.get("name") or "Avery Mockperson",
                        "affiliations": ["Mock University", "Prior Institute"],
                        "publication_count": 12,
                        "funding_count": 3,
                    }
                ],
                "meta": _meta(1),
            }
        if tool == "search_wdp_entity":
            uei = args.get("uei") or "UEI0001MOCK"
            return {
                "data": [
                    {
                        "ref_id": f"wdp-entity-{uei}",
                        "uei": uei,
                        "name": args.get("name") or "Mock University",
                        "country": "USA",
                        "record_count": 42,
                    }
                ],
                "meta": _meta(1),
            }
        if tool == "retrieve_wdp_documents":
            ref_id = args["ref_id"]
            if not ref_id.startswith("wdp-"):
                raise MCPToolError("not_found", f"no WDP reference {ref_id!r}")
            return {
                "data": [
                    {
                        "doc_id": f"{ref_id}-doc-1",
                        "type": "publication",
                        "title": "Mock findings in applied research security",
                        "year": 2024,
                        "source": "Journal of Mock Results",
                        "detail": {"abstract": "Deterministic canned abstract."},
                    },
                    {
                        "doc_id": f"{ref_id}-doc-2",
                        "type": "funding_record",
                        "title": "Mock foreign grant",
                        "year": 2023,
                        "source": "Mock Funding Agency",
                        "detail": {"amount": "250000", "currency": "USD"},
                    },
                ],
                "meta": _meta(2),
            }
        raise MCPToolError("invalid_args", f"no canned behavior for tool {tool!r}")


class MockMCPFactory:
    def __init__(self, contracts_dir: Path, *, fixtures: dict | None = None,
                 deny_orcids: frozenset = frozenset()) -> None:
        self._tool_defs = _load_tool_defs(contracts_dir)
        self._fixtures = fixtures or {}
        self._deny_orcids = deny_orcids

    @asynccontextmanager
    async def session(self, user: UserContext):
        yield MockMCPSession(user, self._tool_defs, self._fixtures, self._deny_orcids)
