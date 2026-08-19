import uuid

ENTITY_ROW = {
    "ref_id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
    "uei": "UEI123456789",
    "name": "Acme Research Institute",
    "country": "US",
    "record_count": 4,
    "total": 1,
}


def test_entities_by_uei_shape_and_binding(client, fake_pool, auth_headers):
    fake_pool.queue([ENTITY_ROW])
    resp = client.get(
        "/v1/entities", params={"uei": "UEI123456789"}, headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "results": [
            {
                "ref_id": "22222222-2222-2222-2222-222222222222",
                "uei": "UEI123456789",
                "name": "Acme Research Institute",
                "country": "US",
                "record_count": 4,
            }
        ],
        "total": 1,
    }
    kind, sql, args = fake_pool.calls[0]
    assert kind == "fetch"
    assert "e.uei = $1" in sql
    assert args == ("UEI123456789",)


def test_entities_record_count_from_documents(client, fake_pool, auth_headers):
    fake_pool.queue([ENTITY_ROW])
    client.get("/v1/entities", params={"name": "acme"}, headers=auth_headers)
    _, sql, args = fake_pool.calls[0]
    assert "LEFT JOIN documents" in sql
    assert "COUNT(d.doc_id) AS record_count" in sql
    assert "e.name ILIKE '%' || $1 || '%'" in sql
    assert args == ("acme",)


def test_entities_empty_result(client, fake_pool, auth_headers):
    fake_pool.queue([])
    resp = client.get("/v1/entities", params={"name": "nobody"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"results": [], "total": 0}
