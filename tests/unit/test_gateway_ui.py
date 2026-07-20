"""Gateway-served UI (/ui) and the labware catalog (GET /labware)."""

from fastapi.testclient import TestClient

from opentrons_server.gateway.api import UI_DIST_DIR, create_app

UI_BUILT = (UI_DIST_DIR / "index.html").is_file()


def _client(**kwargs) -> TestClient:
    return TestClient(create_app(dry_run=True, **kwargs))


def test_ui_serves_index_when_built_and_enabled():
    if not UI_BUILT:
        import pytest

        pytest.skip("ui_dist not built (run `npm run build` in ui/)")
    client = _client(ui=True)
    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<div id=\"root\">" in resp.text


def test_ui_spa_fallback_serves_index_for_unknown_paths():
    if not UI_BUILT:
        import pytest

        pytest.skip("ui_dist not built (run `npm run build` in ui/)")
    client = _client(ui=True)
    resp = client.get("/ui/some/deep/path")
    assert resp.status_code == 200
    assert "<div id=\"root\">" in resp.text


def test_ui_disabled_is_headless():
    client = _client(ui=False)
    assert client.get("/ui").status_code == 404
    assert client.get("/ui/").status_code == 404


def test_ui_flag_reads_env(monkeypatch):
    monkeypatch.setenv("OT2_UI", "off")
    client = TestClient(create_app(dry_run=True))
    assert client.get("/ui/").status_code == 404


def test_labware_endpoint_always_answers():
    client = _client(ui=False)
    resp = client.get("/labware")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["definitions"], list)
    # With opentrons-shared-data installed the catalog is non-empty and each
    # summary carries the fields the UI picker consumes.
    for summary in body["definitions"][:5]:
        assert summary["load_name"]
        assert summary["display_name"]
        assert "rows" in summary and "columns" in summary
        assert summary["source"] == "standard"


def test_ui_claim_sequence_matches_use_claim_hook(tmp_path, monkeypatch):
    """The exact call sequence the UI's useClaim + control client makes:
    tokenless control refused, claim, heartbeat, control with token, release."""
    monkeypatch.setenv("OT2_PLATE_STATE_PATH", str(tmp_path / "plate.json"))
    monkeypatch.setenv("OT2_DECK_STATE_PATH", str(tmp_path / "deck.json"))
    monkeypatch.setenv("OT2_TIP_STATE_PATH", str(tmp_path / "tips.json"))
    client = _client(ui=False)  # claims enforced (default)

    # Tokenless control is refused with the claim-conflict shape.
    resp = client.post("/control/deck/declare", json={"slots": {}})
    assert resp.status_code == 423

    claim = client.post(
        "/control/claim",
        json={"owner": "ot2-gateway-ui", "session_id": "ui-test", "ttl_s": 30},
    )
    assert claim.status_code == 200
    token = claim.json()["claim_token"]
    assert claim.json()["heartbeat_interval_s"] > 0

    assert client.post("/control/heartbeat", headers={"X-Claim-Token": token}).status_code in (
        200,
        204,
    )

    declared = client.post(
        "/control/deck/declare",
        headers={"X-Claim-Token": token},
        json={"slots": {"2": "corning_96_wellplate_360ul_flat"}},
    )
    assert declared.status_code == 200
    assert declared.json()["slots"]["2"]["slot_state"] == "declared"

    assert client.post("/control/release", headers={"X-Claim-Token": token}).status_code == 204
