import uuid

from tests.conftest import DENIED_ORCID

PERSON_REF = "11111111-1111-1111-1111-111111111111"

DOC_ROW = {
    "doc_id": uuid.UUID("33333333-3333-3333-3333-333333333333"),
    "type": "publication",
    "title": "On Synthetic Data",
    "year": 2023,
    "source": "Journal of Demos",
    # asyncpg returns jsonb as str by default; the app must decode it.
    "detail": '{"doi": "10.1234/demo", "coauthors": 3}',
    "total": 2,
}


def test_documents_shape_detail_decoded_and_ordering(client, fake_pool, auth_headers):
    fake_pool.queue({"orcid": "0000-0002-1234-5678"})  # persons fetchrow
    fake_pool.queue([DOC_ROW])  # documents fetch
    resp = client.get(f"/v1/documents/{PERSON_REF}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {
        "results": [
            {
                "doc_id": "33333333-3333-3333-3333-333333333333",
                "type": "publication",
                "title": "On Synthetic Data",
                "year": 2023,
                "source": "Journal of Demos",
                "detail": {"doi": "10.1234/demo", "coauthors": 3},  # object, not string
            }
        ],
        "total": 2,
    }
    kind, sql, args = fake_pool.calls[-1]
    assert kind == "fetch"
    assert "ORDER BY year DESC NULLS LAST, doc_id" in sql
    assert args == (uuid.UUID(PERSON_REF),)


def test_documents_limit_is_bound_parameter(client, fake_pool, auth_headers):
    fake_pool.queue({"orcid": "0000-0002-1234-5678"})
    fake_pool.queue([DOC_ROW])
    client.get(f"/v1/documents/{PERSON_REF}", params={"limit": 1}, headers=auth_headers)
    _, sql, args = fake_pool.calls[-1]
    assert sql.endswith("LIMIT $2")
    assert args == (uuid.UUID(PERSON_REF), 1)


def test_documents_for_entity_ref(client, fake_pool, auth_headers):
    entity_ref = "22222222-2222-2222-2222-222222222222"
    fake_pool.queue(None)  # not a person
    fake_pool.queue({"?column?": 1})  # entities fetchrow hit
    fake_pool.queue(
        [{**DOC_ROW, "type": "entity_record", "detail": {"already": "decoded"}}]
    )
    resp = client.get(f"/v1/documents/{entity_ref}", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"][0]["type"] == "entity_record"
    assert body["results"][0]["detail"] == {"already": "decoded"}


def test_unknown_ref_id_is_404_with_body(client, fake_pool, auth_headers):
    fake_pool.queue(None)  # not a person
    fake_pool.queue(None)  # not an entity
    resp = client.get(f"/v1/documents/{PERSON_REF}", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "not_found", "message": "unknown ref_id"}}


def test_non_uuid_ref_id_is_404_without_sql(client, fake_pool, auth_headers):
    resp = client.get("/v1/documents/not-a-uuid", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "not_found", "message": "unknown ref_id"}}
    assert fake_pool.calls == []


def test_denied_persons_ref_id_is_403(client, fake_pool, auth_headers):
    fake_pool.queue({"orcid": DENIED_ORCID})
    resp = client.get(f"/v1/documents/{PERSON_REF}", headers=auth_headers)
    assert resp.status_code == 403
    assert resp.json() == {
        "error": {"code": "not_authorized", "message": "WDP denies access to this person"}
    }
    assert len(fake_pool.calls) == 1  # only the persons lookup ran
