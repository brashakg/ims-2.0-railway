async def test_bulk_send_skips_opted_out_customers(monkeypatch):
    """POST /marketing/notifications/send-bulk must SKIP customers whose
    marketing_consent is False (consent/DLT compliance), send to consented +
    None/missing (defaults to consented), and report a skipped count.
    send_notification is stubbed so no real provider is hit."""
    docs = {
        "customers": [
            {"customer_id": "C_OPTED_IN", "marketing_consent": True},
            {"customer_id": "C_OPTED_OUT", "marketing_consent": False},
            {"customer_id": "C_NO_PREF"},  # missing -> defaults consented
        ]
    }
    client, _db = _client(monkeypatch, docs)

    async def _fake_send(**kwargs):
        return {"status": "queued"}

    monkeypatch.setattr(marketing_router, "send_notification", _fake_send)

    tok = _token(["STORE_MANAGER"])
    r = client.post(
        "/api/v1/marketing/notifications/send-bulk",
        json={
            "template_id": "promo",
            "channel": "WHATSAPP",
            "recipients": [
                {"customer_id": "C_OPTED_IN", "phone": "9000000001"},
                {"customer_id": "C_OPTED_OUT", "phone": "9000000002"},
                {"customer_id": "C_NO_PREF", "phone": "9000000003"},
                {"phone": "9000000004"},  # ad-hoc, no customer_id -> sent
            ],
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skipped"] == 1
    statuses = {x["phone"]: x["status"] for x in body["results"]}
    assert statuses["9000000002"] == "skipped"  # opted out
    assert statuses["9000000001"] == "queued"  # opted in
    assert statuses["9000000003"] == "queued"  # no pref -> consented
    assert statuses["9000000004"] == "queued"  # ad-hoc phone
