import pytest

from app import wdp_tools
from app.config import Settings
from app.errors import ToolError
from app.wdpclient import WDPError

from tests.conftest import StubWDP

pytestmark = pytest.mark.anyio


async def test_results_total_mapped_to_envelope(settings):
    wdp = StubWDP()
    wdp.results.append(
        {
            "results": [
                {
                    "ref_id": "abc",
                    "orcid": "0000-0001-0000-0001",
                    "name": "Ada",
                    "affiliations": [],
                    "publication_count": 4,
                    "funding_count": 1,
                }
            ],
            "total": 1,
        }
    )
    result = await wdp_tools.search_wdp_person(wdp, {"orcid": "0000-0001-0000-0001"}, settings)
    assert result["data"][0]["ref_id"] == "abc"
    assert result["meta"] == {"total": 1, "returned": 1, "truncated": False}


async def test_limit_clamped_to_max_rows(contracts_dir):
    settings = Settings(
        rfff_seed_database_url="x",
        jwt_secret="s",
        wdp_base_url="http://wdp.test",
        wdp_auth_token="t",
        contracts_dir=contracts_dir,
        mcp_max_rows=5,
    )
    wdp = StubWDP()
    wdp.results.append({"results": [], "total": 0})
    await wdp_tools.search_wdp_entity(wdp, {"uei": "U1", "limit": 50}, settings)
    assert wdp.calls[0][1]["limit"] == 5


async def test_missing_limit_defaults_to_max_rows(settings):
    wdp = StubWDP()
    wdp.results.append({"results": [], "total": 0})
    await wdp_tools.retrieve_wdp_documents(wdp, {"ref_id": "abc"}, settings)
    assert wdp.calls[0][1]["limit"] == settings.mcp_max_rows


async def test_wdp_error_becomes_tool_error(settings):
    wdp = StubWDP()
    wdp.results.append(WDPError("not_authorized", "WDP denies access to this person"))
    with pytest.raises(ToolError) as exc:
        await wdp_tools.search_wdp_person(wdp, {"orcid": "denied"}, settings)
    assert exc.value.code == "not_authorized"
    assert exc.value.message == "WDP denies access to this person"


async def test_truncated_true_when_upstream_has_more(settings):
    wdp = StubWDP()
    wdp.results.append({"results": [{"ref_id": "a"}], "total": 9})
    result = await wdp_tools.search_wdp_person(wdp, {"name": "smith"}, settings)
    assert result["meta"] == {"total": 9, "returned": 1, "truncated": True}
