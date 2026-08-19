import datetime

import pytest

import seed_rfff as sr

# Trimmed data dictionary covering the shapes profile() must handle: Choice
# with a list, Choice without one (ssa), Multiple Values, and Calculated.
SCHEMA_CSV = """Field Name,Description,Requirement,Data Type,Possible Values
fiscal_year,Fiscal Year,Required,Choice,"2026, 2025, 2024, 2023"
ssa,Source Selection Auth,Required,Choice,Not specified in Lists tab
person_orcid,ORCIDs,Required,Free-Text,N/A
proposal_role,Role in Proposal,Required,Choice,"PI, Co-PI, Collaborator, Student"
person_overall_assessment,Personnel Assessment,Calculated,Calculated,N/A
multiple_mitigation,Multiple Mitigation Strategies,Optional,Multiple Values,N/A
mitigation_strategy_proposal,Mitigation Strategy for Proposal,Conditional,Choice,"Cessation, Clarification, Threat Briefs"
assessment_state,Assessment State,Required,Choice,"Draft, In Progress, Completed"
mitigation_status,Mitigation Status on Proposal,Required,Choice,"Not Needed, Not Started, In Progress, Implemented"
award_state,Award State,Optional,Choice,"In Assessment, In Negotiation, Awarded, No Award"
"""


def dictionary():
    return sr.parse_dictionary(SCHEMA_CSV)


def make_row(line=2, **over):
    data = {
        "fiscal_year": "2023",
        "ssa": "SSA-innovation",
        "person_orcid": "0000-0000-0000-0001",
        "first_name": "Ada",
        "middle_name": "",
        "last_name": "Lovelace",
        "proposal_role": "PI",
        "affiliation_uei": "UEI1",
        "affiliation_name": "Uni",
        "factor1_assessment": "No Mitigation Needed",
        "factor2_assessment": "No Mitigation Needed",
        "factor3_assessment": "No Mitigation Needed",
        "factor4_assessment": "No Mitigation Needed",
        "person_overall_assessment": "No Mitigation Needed",
        "multiple_mitigation": "",
        "mitigation_explanation_person": "",
        "mitigation_strategy_proposal": "",
        "mitigation_explanation_proposal": "",
        "proposal_number": "P1",
        "opportunity_number": "OPP-1",
        "proposal_title": "Title One",
        "submitting_entity_uei": "SUEI",
        "submitting_entity_name": "Globex",
        "review_type": "Initial",
        "reviewing_component": "DARPA",
        "reviewing_subcomponent": "DSO",
        "assessment_state": "Completed",
        "approved_date": "5/3/2024 14:47",
        "review_notes": "",
        "mitigation_status": "Not Needed",
        "award_type": "Grant",
        "award_state": "Awarded",
        "fain": "FAIN1",
        "awarded_date": "6/7/2024 5:20",
        "award_pop_start": "1/1/2024 0:00",
        "award_pop_end": "12/31/2025 0:00",
        "proposal_files": "proposal_P1.pdf",
        "person_files": "person_Lovelace.pdf",
        "review_files": "review_P1.docx",
    }
    data.update(over)
    return sr.Row(line=line, data=data)


def observed(profile, field):
    return {o.value: o for o in profile.observed[field]}


def rules(profile):
    return [q.rule for q in profile.quarantine]


# --- parsing ---------------------------------------------------------------


def test_parse_dictionary_splits_value_lists():
    d = dictionary()
    assert d["proposal_role"].values == ["PI", "Co-PI", "Collaborator", "Student"]
    assert d["proposal_role"].data_type == "Choice"
    assert d["fiscal_year"].description == "Fiscal Year"


def test_parse_dictionary_treats_na_and_unspecified_as_no_list():
    d = dictionary()
    assert d["ssa"].values is None
    assert d["person_orcid"].values is None
    assert d["multiple_mitigation"].values is None


def test_parse_rows_keeps_csv_line_numbers():
    rows = sr.parse_rows("a,b\nx,y\nz,w\n")
    assert [(r.line, r.data["a"]) for r in rows] == [(2, "x"), (3, "z")]


def test_parse_date_formats():
    assert sr.parse_date("5/3/2021 14:47") == datetime.date(2021, 5, 3)
    assert sr.parse_date("") is None
    with pytest.raises(ValueError):
        sr.parse_date("not a date")


# --- profiler: observed enums ----------------------------------------------


def test_observed_enums_flag_undocumented_values():
    rows = [
        make_row(line=2),
        make_row(line=3, proposal_number="P2", proposal_role="Consultant"),
        make_row(line=4, proposal_number="P3", proposal_role="Consultant"),
    ]
    prof = sr.profile(rows, dictionary())
    role = observed(prof, "proposal_role")
    assert role["PI"].in_dictionary is True
    assert role["PI"].row_count == 1
    assert role["Consultant"].in_dictionary is False
    assert role["Consultant"].row_count == 2


def test_multi_value_fields_are_split_before_counting():
    rows = [make_row(mitigation_strategy_proposal="Cessation, Threat Briefs")]
    prof = sr.profile(rows, dictionary())
    strat = observed(prof, "mitigation_strategy_proposal")
    assert set(strat) == {"Cessation", "Threat Briefs"}
    assert all(o.in_dictionary for o in strat.values())


def test_choice_field_with_comma_joined_members_flagged_multi_valued():
    rows = [make_row(mitigation_strategy_proposal="Cessation, Threat Briefs")]
    prof = sr.profile(rows, dictionary())
    assert "mitigation_strategy_proposal" in prof.multi_valued_choice_fields


def test_fields_without_documented_list_are_profiled_but_marked():
    rows = [make_row()]
    prof = sr.profile(rows, dictionary())
    assert "ssa" in prof.fields_without_list
    assert observed(prof, "ssa")["SSA-innovation"].in_dictionary is False
    # Calculated field is profiled too (planner needs its observed values).
    assert "person_overall_assessment" in prof.fields_without_list


# --- profiler: referential checks -------------------------------------------


def test_duplicate_proposal_person_key_quarantined():
    rows = [make_row(line=2), make_row(line=3)]
    prof = sr.profile(rows, dictionary())
    assert rules(prof) == ["duplicate_key"]
    assert "3" in prof.quarantine[0].row_ref


def test_conflicting_proposal_level_field_quarantined():
    rows = [
        make_row(line=2),
        make_row(line=3, person_orcid="0000-0000-0000-0002",
                 person_files="person_Two.pdf", proposal_title="Different"),
    ]
    prof = sr.profile(rows, dictionary())
    assert rules(prof) == ["proposal_field_conflict"]
    assert "proposal_title" in prof.quarantine[0].detail


def test_conflicting_person_name_quarantined():
    rows = [
        make_row(line=2),
        make_row(line=3, proposal_number="P2", last_name="Byron"),
    ]
    prof = sr.profile(rows, dictionary())
    assert rules(prof) == ["person_name_conflict"]


def test_middle_name_variance_is_counted_not_quarantined():
    # The mock generator jitters the Optional middle_name; first+last are the
    # identity. Variance is a profile stat, not 400+ quarantine rows.
    rows = [
        make_row(line=2),
        make_row(line=3, proposal_number="P2", middle_name="Q"),
    ]
    prof = sr.profile(rows, dictionary())
    assert rules(prof) == []
    assert prof.personnel_with_variant_middle_name == 1


def test_pi_coverage_stats():
    rows = [
        make_row(line=2, proposal_number="P1", proposal_role="Collaborator"),
        make_row(line=3, proposal_number="P2"),
        make_row(line=4, proposal_number="P2", person_orcid="0000-0000-0000-0002",
                 person_files="person_Two.pdf"),
    ]
    prof = sr.profile(rows, dictionary())
    assert prof.proposals_without_pi == 1
    assert prof.proposals_with_multiple_pi == 1


# --- profiler: date sanity ---------------------------------------------------


def test_pop_end_before_pop_start_quarantined():
    rows = [make_row(award_pop_start="6/1/2025 0:00", award_pop_end="1/1/2024 0:00")]
    prof = sr.profile(rows, dictionary())
    assert rules(prof) == ["pop_end_before_pop_start"]


def test_awarded_date_without_awarded_state_quarantined():
    rows = [make_row(award_state="In Negotiation")]
    prof = sr.profile(rows, dictionary())
    assert rules(prof) == ["awarded_date_without_awarded_state"]


def test_approved_date_year_outside_fiscal_year_span_quarantined():
    rows = [make_row(approved_date="5/3/2020 14:47")]
    prof = sr.profile(rows, dictionary())
    assert rules(prof) == ["approved_date_year_implausible"]


def test_unparseable_date_quarantined():
    rows = [make_row(awarded_date="garbage")]
    prof = sr.profile(rows, dictionary())
    assert rules(prof) == ["unparseable_date"]


def test_clean_rows_produce_no_quarantine():
    rows = [make_row()]
    prof = sr.profile(rows, dictionary())
    assert prof.quarantine == []


# --- normalizer ---------------------------------------------------------------


def by_key(records, key):
    return {r[key]: r for r in records}


def test_normalize_splits_multi_values_and_maps_explanation_column():
    rows = [make_row(
        mitigation_strategy_proposal="Cessation, Threat Briefs",
        multiple_mitigation="Cessation, Increased Reporting",
        mitigation_explanation_proposal="prop expl",
        mitigation_explanation_person="pers expl",
    )]
    norm = sr.normalize(rows)
    prop = norm.proposals[0]
    assert prop["mitigation_strategy_proposal"] == ["Cessation", "Threat Briefs"]
    assert prop["mitigation_explanation"] == "prop expl"
    assert "mitigation_explanation_proposal" not in prop
    pp = norm.proposal_personnel[0]
    assert pp["multiple_mitigation"] == ["Cessation", "Increased Reporting"]
    assert pp["mitigation_explanation_person"] == "pers expl"


def test_normalize_empty_multi_value_is_null_not_empty_list():
    norm = sr.normalize([make_row(mitigation_strategy_proposal="",
                                  multiple_mitigation="")])
    assert norm.proposals[0]["mitigation_strategy_proposal"] is None
    assert norm.proposal_personnel[0]["multiple_mitigation"] is None


def test_normalize_parses_dates_and_nulls_bad_or_empty():
    norm = sr.normalize([make_row(approved_date="5/3/2024 14:47",
                                  awarded_date="", award_pop_start="garbage")])
    prop = norm.proposals[0]
    assert prop["approved_date"] == datetime.date(2024, 5, 3)
    assert prop["awarded_date"] is None
    assert prop["award_pop_start"] is None  # quarantined by profile, not here


def test_normalize_first_occurrence_wins():
    rows = [
        make_row(line=2),
        make_row(line=3, proposal_title="Different"),  # dup key + conflict
        make_row(line=4, proposal_number="P2",
                 person_orcid="0000-0000-0000-0002", last_name="Byron"),
    ]
    norm = sr.normalize(rows)
    assert len(norm.proposals) == 2
    assert by_key(norm.proposals, "proposal_number")["P1"]["proposal_title"] == "Title One"
    assert len(norm.personnel) == 2
    assert len(norm.proposal_personnel) == 2  # (P1,o1) dup collapses
    assert norm.proposal_personnel[0]["person_overall_assessment"] == "No Mitigation Needed"


def test_normalize_empty_strings_become_null():
    norm = sr.normalize([make_row(middle_name="", review_notes="", fain="")])
    assert norm.personnel[0]["middle_name"] is None
    assert norm.proposals[0]["review_notes"] is None
    assert norm.proposals[0]["fain"] is None


def test_normalize_file_refs_scopes_and_proposal_level_dedup():
    rows = [
        make_row(line=2),
        make_row(line=3, person_orcid="0000-0000-0000-0002",
                 person_files="person_Two.pdf"),
    ]
    norm = sr.normalize(rows)
    refs = [(r["scope"], r["proposal_number"], r["person_orcid"], r["filename"])
            for r in norm.file_refs]
    assert refs.count(("proposal", "P1", None, "proposal_P1.pdf")) == 1
    assert refs.count(("review", "P1", None, "review_P1.docx")) == 1
    assert ("person", "P1", "0000-0000-0000-0001", "person_Lovelace.pdf") in refs
    assert ("person", "P1", "0000-0000-0000-0002", "person_Two.pdf") in refs
    assert len(refs) == 4
    assert norm.file_refs[0]["metadata"] == {"source_column": "proposal_files"}


# --- planner context & report ---------------------------------------------


def sample_profile():
    rows = [
        make_row(line=2, proposal_role="Consultant",
                 mitigation_strategy_proposal="Cessation, Threat Briefs",
                 approved_date="5/3/2020 14:47"),
        make_row(line=3, proposal_number="P2"),
    ]
    return sr.profile(rows, dictionary())


def test_planner_context_merges_dictionary_and_observed():
    ctx = sr.build_planner_context(dictionary(), sample_profile())
    role = ctx["fields"]["proposal_role"]
    assert role["description"] == "Role in Proposal"
    assert role["dictionary_values"] == ["PI", "Co-PI", "Collaborator", "Student"]
    observed = {o["value"]: o for o in role["observed_values"]}
    assert observed["Consultant"]["in_dictionary"] is False
    assert observed["PI"]["in_dictionary"] is True
    # Non-enum fields still carry their dictionary description.
    assert ctx["fields"]["person_orcid"]["description"] == "ORCIDs"
    assert ctx["fields"]["person_orcid"]["observed_values"] is None


def test_planner_context_includes_generated_caveats():
    ctx = sr.build_planner_context(dictionary(), sample_profile())
    text = " ".join(ctx["caveats"])
    assert "proposal_role" in text and "Consultant" in text
    assert "person_overall_assessment" in text  # opaque, never derived
    assert "date" in text.lower()  # do not build reasoning on dates
    assert "ssa" in text  # dictionary silent; open question for STPP
    assert "mitigation_strategy_proposal" in text  # secretly multi-valued
    assert ctx["profile"]["row_count"] == 2
    assert ctx["profile"]["quarantine_by_rule"] == {"approved_date_year_implausible": 1}


def test_render_report_marks_undocumented_values():
    report = sr.render_report(sample_profile(), dictionary(),
                              {"proposals": 2, "personnel": 1})
    assert "UNDOCUMENTED" in report
    assert "Consultant" in report
    assert "proposals" in report and "2" in report
    # Fields with no dictionary list must not read as data errors.
    assert "no value list" in report


def test_real_mock_csvs_flag_the_spec_undocumented_values():
    import pathlib

    data_dir = pathlib.Path(__file__).resolve().parents[2] / "data" / "mock"
    dct = sr.parse_dictionary(
        (data_dir / "proposal-assessment-schema.csv").read_text())
    rows = sr.parse_rows(
        (data_dir / "proposal-assessment-mock.csv").read_text())
    prof = sr.profile(rows, dct)

    def undocumented(field):
        return {o.value for o in prof.observed[field]
                if not o.in_dictionary}

    assert undocumented("proposal_role") == {"Consultant"}
    assert undocumented("assessment_state") == {"Canceled"}
    assert undocumented("mitigation_status") == {"Complete", "Pending"}
    assert undocumented("award_state") == {"Declined", "Pending"}
    assert "mitigation_strategy_proposal" in prof.multi_valued_choice_fields
    assert prof.row_count == 1000
