import uuid

from tests.conftest import DENIED_ORCID

PERSON_ROW = {
    "ref_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
    "orcid": "0000-0002-1234-5678",
    "name": "Dana Researcher",
    "affiliations": ["University A", "Lab B"],
    "publication_count": 7,
    "funding_count": 2,
    "total": 5,
}


def test_persons_by_orcid_shape_and_binding(client, fake_pool, auth_headers):
    fake_pool.queue([PERSON_ROW])
    resp = client.get(
        "/v1/persons", params={"orcid": "0000-0002-1234-5678"}, headers=auth_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "results": [
            {
                "ref_id": "11111111-1111-1111-1111-111111111111",
                "orcid": "0000-0002-1234-5678",
                "name": "Dana Researcher",
                "affiliations": ["University A", "Lab B"],
                "publication_count": 7,
                "funding_count": 2,
            }
        ],
        "total": 5,
    }
    kind, sql, args = fake_pool.calls[0]
    assert kind == "fetch"
    assert "p.orcid = $1" in sql
    assert args == ("0000-0002-1234-5678",)


def test_persons_counts_come_from_documents_join(client, fake_pool, auth_headers):
    fake_pool.queue([PERSON_ROW])
    client.get("/v1/persons", params={"orcid": PERSON_ROW["orcid"]}, headers=auth_headers)
    _, sql, _ = fake_pool.calls[0]
    assert "LEFT JOIN documents" in sql
    assert "FILTER (WHERE d.type = 'publication')" in sql
    assert "FILTER (WHERE d.type = 'funding_record')" in sql
    assert "COUNT(*) OVER ()" in sql  # pre-limit total


def test_persons_by_name_is_case_insensitive_substring(client, fake_pool, auth_headers):
    fake_pool.queue([])
    resp = client.get("/v1/persons", params={"name": "dana"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"results": [], "total": 0}
    _, sql, args = fake_pool.calls[0]
    assert "p.name ILIKE '%' || $1 || '%'" in sql
    assert args == ("dana",)


def test_persons_limit_is_bound_parameter(client, fake_pool, auth_headers):
    fake_pool.queue([PERSON_ROW])
    client.get(
        "/v1/persons", params={"name": "dana", "limit": 3}, headers=auth_headers
    )
    _, sql, args = fake_pool.calls[0]
    assert sql.endswith("LIMIT $2")
    assert args == ("dana", 3)


def test_denied_orcid_is_403_with_exact_body(client, fake_pool, auth_headers):
    resp = client.get("/v1/persons", params={"orcid": DENIED_ORCID}, headers=auth_headers)
    assert resp.status_code == 403
    assert resp.json() == {
        "error": {"code": "not_authorized", "message": "WDP denies access to this person"}
    }
    assert fake_pool.calls == []  # denied before any SQL


def test_deny_applies_only_to_explicit_orcid_targeting(client, fake_pool, auth_headers):
    # Name search for a denied person is discovery, not targeting; no 403.
    fake_pool.queue([])
    resp = client.get("/v1/persons", params={"name": "Denied Person"}, headers=auth_headers)
    assert resp.status_code == 200


def test_delay_zero_is_accepted(client, fake_pool, auth_headers):
    fake_pool.queue([])
    resp = client.get("/v1/persons", params={"_delay": 0}, headers=auth_headers)
    assert resp.status_code == 200
