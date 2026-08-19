import datetime
import json

import pytest

from app import rfff_tools
from app.config import Settings
from app.errors import ToolError
from app.rfff_tools import PROPOSAL_COLUMNS

from tests.conftest import FakePool

pytestmark = pytest.mark.anyio


def small_settings(contracts_dir, **over):
    defaults = dict(
        rfff_seed_database_url="postgresql://unused",
        jwt_secret="s",
        wdp_base_url="http://wdp.test",
        wdp_auth_token="t",
        contracts_dir=contracts_dir,
    )
    defaults.update(over)
    return Settings(**defaults)


def proposal_row(**over) -> dict:
    row = {c: None for c in PROPOSAL_COLUMNS}
    row["proposal_number"] = "P-2025-0001"
    row.update(over)
    return row


async def test_search_proposals_envelope(settings):
    pool = FakePool()
    pool.fetch_results.append(
        [
            proposal_row(
                _total=2,
                approved_date=datetime.date(2024, 1, 2),
                mitigation_strategy_proposal=["Training"],
            ),
            proposal_row(_total=2, proposal_number="P-2025-0002"),
        ]
    )
    result = await rfff_tools.search_proposals(pool, {}, settings)
    assert result["meta"] == {"total": 2, "returned": 2, "truncated": False}
    assert result["data"][0]["approved_date"] == "2024-01-02"
    assert result["data"][0]["mitigation_strategy_proposal"] == ["Training"]
    assert result["data"][1]["mitigation_strategy_proposal"] == []


async def test_search_proposals_row_cap_reports_truncation(contracts_dir):
    settings = small_settings(contracts_dir, mcp_max_rows=1)
    pool = FakePool()
    pool.fetch_results.append([proposal_row(_total=5)])
    result = await rfff_tools.search_proposals(pool, {}, settings)
    assert result["meta"] == {"total": 5, "returned": 1, "truncated": True}
    # The effective LIMIT passed to SQL is also capped.
    assert pool.calls[0][2][-1] == 1


async def test_long_text_truncated_honestly(contracts_dir):
    settings = small_settings(contracts_dir, mcp_max_text_chars=10)
    pool = FakePool()
    pool.fetch_results.append([proposal_row(_total=1, review_notes="x" * 50)])
    result = await rfff_tools.search_proposals(pool, {}, settings)
    assert result["data"][0]["review_notes"] == "x" * 10 + "…"
    assert result["meta"]["truncated"] is True


async def test_get_proposal_not_found(settings):
    pool = FakePool()  # fetchrow returns None
    with pytest.raises(ToolError) as exc:
        await rfff_tools.get_proposal(pool, {"proposal_number": "P-NOPE"}, settings)
    assert exc.value.code == "not_found"


async def test_get_proposal_full_record(settings):
    pool = FakePool()
    pool.fetchrow_results.append(proposal_row())
    pool.fetch_results.append(
        [
            {
                "person_orcid": "0000-0001-0000-0001",
                "first_name": "Ada",
                "middle_name": None,
                "last_name": "Lovelace",
                "proposal_role": "PI",
                "affiliation_uei": "UEI1",
                "affiliation_name": "Analytical Engines",
                "factor1_assessment": "Low",
                "factor2_assessment": None,
                "factor3_assessment": None,
                "factor4_assessment": None,
                "person_overall_assessment": "Green",
                "multiple_mitigation": None,
                "mitigation_explanation_person": None,
            }
        ]
    )
    pool.fetch_results.append(
        [
            {
                "scope": "person",
                "person_orcid": "0000-0001-0000-0001",
                "filename": "cv.pdf",
                "metadata": json.dumps({"pages": 3}),
            }
        ]
    )
    result = await rfff_tools.get_proposal(
        pool, {"proposal_number": "P-2025-0001"}, settings
    )
    data = result["data"]
    assert data["personnel"][0]["person_orcid"] == "0000-0001-0000-0001"
    assert data["personnel"][0]["multiple_mitigation"] == []
    assert data["file_refs"][0]["metadata"] == {"pages": 3}
    assert result["meta"] == {"total": 1, "returned": 1, "truncated": False}


async def test_search_personnel_groups_proposals(settings):
    pool = FakePool()
    pool.fetch_results.append(
        [
            {
                "person_orcid": "0000-0001-0000-0001",
                "first_name": "Ada",
                "middle_name": None,
                "last_name": "Lovelace",
                "_total": 1,
            }
        ]
    )
    pool.fetch_results.append(
        [
            {
                "person_orcid": "0000-0001-0000-0001",
                "proposal_number": "P-2025-0001",
                "proposal_title": "Engines",
                "proposal_role": "PI",
                "person_overall_assessment": "Green",
            },
            {
                "person_orcid": "0000-0001-0000-0001",
                "proposal_number": "P-2025-0002",
                "proposal_title": "More Engines",
                "proposal_role": "Key Person",
                "person_overall_assessment": "Green",
            },
        ]
    )
    result = await rfff_tools.search_personnel(pool, {"name": "lovelace"}, settings)
    assert result["meta"]["total"] == 1
    person = result["data"][0]
    assert [p["proposal_number"] for p in person["proposals"]] == [
        "P-2025-0001",
        "P-2025-0002",
    ]


async def test_search_personnel_requires_a_criterion(settings):
    with pytest.raises(ToolError) as exc:
        await rfff_tools.search_personnel(FakePool(), {}, settings)
    assert exc.value.code == "invalid_args"


async def test_aggregate_buckets_and_overlap_flag(settings):
    pool = FakePool()
    pool.fetch_results.append(
        [
            {"g0": "Green", "count": 12},
            {"g0": "Red", "count": 3},
        ]
    )
    result = await rfff_tools.aggregate_assessments(
        pool, {"group_by": ["person_overall_assessment"]}, settings
    )
    assert result["data"] == [
        {"group": {"person_overall_assessment": "Green"}, "count": 12},
        {"group": {"person_overall_assessment": "Red"}, "count": 3},
    ]
    assert result["meta"]["overlapping_buckets"] is True
    assert result["meta"]["total"] == 2
