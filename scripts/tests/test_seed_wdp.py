"""Pure-stage tests for seed_wdp (no db; load()/read_rfff() are exercised by
`make seed`, not unit tests)."""
import random

import pytest

import seed_wdp as sw


def make_persons(n=40):
    return [
        sw.PersonInput(
            orcid=f"0000-0000-0000-{i:04d}",
            name=f"Person {i}",
            affiliations=[f"University {i % 5}"],
        )
        for i in range(n)
    ]


def make_entities(n=8):
    return [
        sw.EntityInput(uei=f"UEI{i:03d}", name=f"Entity {i}") for i in range(n)
    ]


def orcids_of(persons):
    return sorted(p.orcid for p in persons)


def docs_by_ref(gen):
    grouped = {}
    for doc in gen.documents:
        grouped.setdefault(doc["ref_id"], []).append(doc)
    return grouped


# --- determinism -------------------------------------------------------------


def run_pipeline(deny):
    persons, entities = make_persons(), make_entities()
    orcids = orcids_of(persons)
    gaps = sw.plan_gaps(orcids, deny, random.Random(sw.RNG_SEED))
    gen = sw.generate(persons, entities, gaps, random.Random(sw.RNG_SEED))
    pairs = [
        (f"P{i:03d}", p.orcid) for i, p in enumerate(sorted(persons, key=lambda x: x.orcid))
    ]
    # Make at least one proposal mix a with-records orcid and a gap orcid.
    with_records = next(o for o in orcids if o not in gaps and o not in deny)
    gap_orcid = sorted(gaps)[0]
    pairs += [("PMIX", with_records), ("PMIX", gap_orcid)]
    anchors = sw.select_anchors(
        pairs, {p.orcid: p.name for p in persons}, gaps, deny)
    return gaps, gen, anchors


def test_full_generation_is_deterministic():
    deny = ["0000-0000-0000-0000"]
    gaps1, gen1, anchors1 = run_pipeline(deny)
    gaps2, gen2, anchors2 = run_pipeline(deny)
    assert gaps1 == gaps2
    assert gen1 == gen2  # dataclass equality: uuids, titles, detail, order
    assert anchors1 == anchors2


# --- plan_gaps ---------------------------------------------------------------


def test_plan_gaps_never_selects_deny_orcids():
    persons = make_persons(60)
    orcids = orcids_of(persons)
    deny = orcids[:10]
    gaps = sw.plan_gaps(orcids, deny, random.Random(sw.RNG_SEED))
    assert gaps.isdisjoint(deny)
    assert gaps <= set(orcids)


def test_plan_gaps_hits_the_configured_fraction_of_eligible():
    persons = make_persons(60)
    orcids = orcids_of(persons)
    deny = orcids[:10]
    gaps = sw.plan_gaps(orcids, deny, random.Random(sw.RNG_SEED))
    eligible = len(orcids) - len(deny)
    assert len(gaps) == round(eligible * sw.GAP_FRACTION)


# --- generate ----------------------------------------------------------------


def test_generate_gap_orcids_get_no_persons_row_at_all():
    persons, entities = make_persons(), make_entities()
    orcids = orcids_of(persons)
    gaps = sw.plan_gaps(orcids, [], random.Random(sw.RNG_SEED))
    gen = sw.generate(persons, entities, gaps, random.Random(sw.RNG_SEED))
    seeded = {p["orcid"] for p in gen.persons}
    assert seeded == set(orcids) - gaps
    assert seeded.isdisjoint(gaps)


def test_generate_document_counts_within_bounds():
    persons, entities = make_persons(), make_entities()
    gaps = sw.plan_gaps(orcids_of(persons), [], random.Random(sw.RNG_SEED))
    gen = sw.generate(persons, entities, gaps, random.Random(sw.RNG_SEED))
    grouped = docs_by_ref(gen)
    for person in gen.persons:
        docs = grouped[person["ref_id"]]  # every with-records person has docs
        pubs = [d for d in docs if d["type"] == "publication"]
        funding = [d for d in docs if d["type"] == "funding_record"]
        assert len(pubs) + len(funding) == len(docs)
        assert 2 <= len(pubs) <= 12
        assert 0 <= len(funding) <= 4
    for entity in gen.entities:
        records = grouped[entity["ref_id"]]
        assert all(d["type"] == "entity_record" for d in records)
        assert 1 <= len(records) <= 8


def test_generate_every_document_belongs_to_a_generated_row():
    persons, entities = make_persons(), make_entities()
    gaps = sw.plan_gaps(orcids_of(persons), [], random.Random(sw.RNG_SEED))
    gen = sw.generate(persons, entities, gaps, random.Random(sw.RNG_SEED))
    known = {p["ref_id"] for p in gen.persons}
    known |= {e["ref_id"] for e in gen.entities}
    assert {d["ref_id"] for d in gen.documents} <= known
    assert len(gen.entities) == len(make_entities())  # every entity seeded


# --- verify_deny_orcids --------------------------------------------------------


def test_verify_deny_orcids_rejects_orcid_missing_from_rfff():
    errors = sw.verify_deny_orcids(["9999-x"], ["0000-a", "0000-b"], set())
    assert len(errors) == 1
    assert "9999-x" in errors[0]
    assert "does not exist" in errors[0]


def test_verify_deny_orcids_rejects_orcid_planned_as_gap():
    errors = sw.verify_deny_orcids(
        ["0000-a"], ["0000-a", "0000-b"], {"0000-a"})
    assert len(errors) == 1
    assert "gap" in errors[0]


def test_verify_deny_orcids_passes_on_valid_input():
    assert sw.verify_deny_orcids(
        ["0000-a"], ["0000-a", "0000-b"], {"0000-b"}) == []


# --- select_anchors -------------------------------------------------------------


NAMES = {"0000-a": "Ada Lovelace", "0000-b": "Blaise Pascal",
         "0000-c": "Carl Gauss", "0000-d": "Denied Person"}


def test_select_anchors_picks_first_qualifying_proposal():
    pairs = [
        ("P1", "0000-a"),  # with-records only: does not qualify
        ("P2", "0000-b"), ("P2", "0000-c"),  # records + gap: qualifies
        ("P3", "0000-a"), ("P3", "0000-c"),  # also qualifies, but sorts later
    ]
    anchors = sw.select_anchors(pairs, NAMES, gaps={"0000-c"},
                                deny=["0000-d"])
    assert anchors.query2_proposal_number == "P2"
    assert anchors.query3b_denied_orcid == "0000-d"
    assert anchors.query3b_person_name == "Denied Person"


def test_select_anchors_ignores_denied_orcids_as_the_records_leg():
    # P1's only non-gap member is denied -> must not qualify; P2 does.
    pairs = [
        ("P1", "0000-d"), ("P1", "0000-c"),
        ("P2", "0000-a"), ("P2", "0000-c"),
    ]
    anchors = sw.select_anchors(pairs, NAMES, gaps={"0000-c"},
                                deny=["0000-d"])
    assert anchors.query2_proposal_number == "P2"


def test_select_anchors_errors_when_no_proposal_qualifies():
    pairs = [("P1", "0000-a"), ("P2", "0000-b")]  # nobody pairs with a gap
    with pytest.raises(ValueError, match="no proposal qualifies"):
        sw.select_anchors(pairs, NAMES, gaps={"0000-c"}, deny=["0000-d"])


def test_select_anchors_errors_on_empty_deny_list():
    with pytest.raises(ValueError, match="empty"):
        sw.select_anchors([("P1", "0000-a")], NAMES, gaps=set(), deny=[])


# --- render_report ----------------------------------------------------------------


def test_render_report_mentions_counts_gaps_and_anchors():
    persons, entities = make_persons(), make_entities()
    gaps = sw.plan_gaps(orcids_of(persons), [], random.Random(sw.RNG_SEED))
    gen = sw.generate(persons, entities, gaps, random.Random(sw.RNG_SEED))
    anchors = sw.Anchors(
        query2_proposal_number="P42",
        query3b_person_name="Denied Person",
        query3b_denied_orcid="0000-d",
    )
    report = sw.render_report(
        gen, gaps, anchors,
        {"persons": len(gen.persons), "entities": len(gen.entities),
         "documents": len(gen.documents), "demo_anchors": 3},
    )
    assert f"gap count: {len(gaps)}" in report
    assert sorted(gaps)[0] in report
    assert "P42" in report
    assert "0000-d" in report
    assert "Denied Person" in report
