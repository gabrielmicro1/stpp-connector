"""Pure SQL-builder tests: parameter binding, EXISTS semantics, whitelists."""
import pytest

from app.errors import ToolError
from app.rfff_tools import build_aggregate_sql, build_search_proposals_sql


def test_proposal_filter_binds_parameter():
    sql, params = build_search_proposals_sql({"fiscal_year": "2024"}, None, 10)
    assert "p.fiscal_year = $1" in sql
    assert params == ["2024", 10]
    assert "2024" not in sql  # values only ever bind, never interpolate


def test_person_level_filter_is_exists_subquery():
    sql, params = build_search_proposals_sql(
        {"factor1_assessment": "High"}, None, 10
    )
    assert "EXISTS (SELECT 1 FROM proposal_personnel pp" in sql
    assert "pp.factor1_assessment = $1" in sql


def test_keywords_search_title_and_entity_name():
    sql, params = build_search_proposals_sql({}, "quantum", 10)
    assert "p.proposal_title ILIKE $1" in sql
    assert "p.submitting_entity_name ILIKE $1" in sql
    assert params == ["%quantum%", 10]


def test_limit_is_final_parameter():
    sql, params = build_search_proposals_sql({"fiscal_year": "2024"}, "x", 5)
    assert sql.endswith(f"LIMIT ${len(params)}")
    assert params[-1] == 5


def test_unknown_filter_field_rejected_by_whitelist():
    with pytest.raises(ToolError) as exc:
        build_search_proposals_sql({"proposal_number; DROP": "x"}, None, 10)
    assert exc.value.code == "invalid_args"


def test_aggregate_proposal_level_no_join():
    sql, params, overlapping = build_aggregate_sql(["fiscal_year"], {})
    assert "JOIN proposal_personnel" not in sql
    assert "p.fiscal_year AS g0" in sql
    assert "COUNT(DISTINCT p.proposal_number)" in sql
    assert overlapping is False


def test_aggregate_person_level_joins_and_flags_overlap():
    sql, params, overlapping = build_aggregate_sql(
        ["person_overall_assessment"], {"fiscal_year": "2024"}
    )
    assert "JOIN proposal_personnel pp ON pp.proposal_number = p.proposal_number" in sql
    assert "pp.person_overall_assessment AS g0" in sql
    assert overlapping is True
    # Filters must stay on p / EXISTS so they never restrict the grouping join.
    assert "p.fiscal_year = $1" in sql
    assert params == ["2024"]


def test_aggregate_person_filter_stays_exists_even_with_join():
    sql, _, _ = build_aggregate_sql(
        ["person_overall_assessment"], {"factor2_assessment": "Low"}
    )
    assert "EXISTS (SELECT 1 FROM proposal_personnel pp" in sql


def test_aggregate_unknown_group_by():
    with pytest.raises(ToolError) as exc:
        build_aggregate_sql(["proposal_title"], {})
    assert exc.value.code == "invalid_args"
