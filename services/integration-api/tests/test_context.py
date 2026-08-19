"""Pure parts of the planner-input assembly (the asyncpg provider itself is
thin and verified live, like PostgresJobStore)."""
from agent.context import (
    build_observed_enums,
    extract_terms,
    person_match,
    proposal_match,
)


def test_extract_terms_keeps_proposal_numbers_whole():
    terms = extract_terms(
        "Give me research background on the personnel of proposal P-2025-0042."
    )
    assert "p-2025-0042" in terms
    assert "research" in terms
    assert "personnel" in terms


def test_extract_terms_drops_stopwords_and_short_tokens():
    terms = extract_terms("How many proposals had Prohibited Factors on factor 4?")
    assert "how" not in terms
    assert "many" not in terms
    assert "had" not in terms
    assert "on" not in terms  # too short
    assert "prohibited" in terms
    assert "factors" in terms


def test_extract_terms_dedups_preserving_order():
    assert extract_terms("DARPA darpa proposals DARPA") == ["darpa", "proposals"]


def test_extract_terms_empty_query():
    assert extract_terms("??") == []


def test_build_observed_enums_groups_by_field():
    rows = [
        {"field_name": "fiscal_year", "value": "2024"},
        {"field_name": "fiscal_year", "value": "2025"},
        {"field_name": "award_state", "value": "Awarded"},
    ]
    assert build_observed_enums(rows) == {
        "fiscal_year": {"2024", "2025"},
        "award_state": {"Awarded"},
    }


def test_match_row_shapes():
    p = proposal_match(
        {
            "proposal_number": "P-1",
            "proposal_title": "T",
            "submitting_entity_name": "E",
            "fiscal_year": "2025",
        }
    )
    assert p["type"] == "proposal" and p["proposal_number"] == "P-1"
    person = person_match(
        {
            "person_orcid": "0000-0001-2345-0001",
            "first_name": "Avery",
            "last_name": "Mockperson",
            "proposal_numbers": ["P-1", "P-2"],
        }
    )
    assert person["type"] == "person"
    assert person["name"] == "Avery Mockperson"
    assert person["proposal_numbers"] == ["P-1", "P-2"]
