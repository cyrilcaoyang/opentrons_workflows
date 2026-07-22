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


def test_ui_includes_shared_edge_banner():
    """Both the UI source page and the built bundle must load the shared
    signed-in-user banner served by the authenticated Caddy edge."""
    repo_root = UI_DIST_DIR.parent.parent.parent
    source_index = repo_root / "ui" / "index.html"
    assert '/auth/banner.js' in source_index.read_text(encoding="utf-8")
    if UI_BUILT:
        built_index = (UI_DIST_DIR / "index.html").read_text(encoding="utf-8")
        assert '/auth/banner.js' in built_index


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


def test_edge_mode_requires_secret():
    import pytest

    with pytest.raises(RuntimeError):
        create_app(dry_run=True, ui=True, trust_local_ui=False)


def test_edge_mode_gates_ui_and_labware():
    client = _client(ui=True, trust_local_ui=False, edge_secret="s3cret")
    # Direct hits: the UI surface does not exist.
    assert client.get("/ui/").status_code == 404
    assert client.get("/labware").status_code == 404
    assert client.get("/ui/", headers={"X-Edge-Key": "wrong"}).status_code == 404
    # Through the edge: served normally.
    assert client.get("/labware", headers={"X-Edge-Key": "s3cret"}).status_code == 200
    if UI_BUILT:
        resp = client.get("/ui/", headers={"X-Edge-Key": "s3cret"})
        assert resp.status_code == 200
        assert "<div id=\"root\">" in resp.text
    # Spec surfaces stay open to the aggregator.
    assert client.get("/").status_code == 200
    assert client.get("/status").status_code == 200


def test_edge_mode_stamps_claim_owner_from_auth_header(tmp_path, monkeypatch):
    monkeypatch.setenv("OT2_PLATE_STATE_PATH", str(tmp_path / "plate.json"))
    monkeypatch.setenv("OT2_DECK_STATE_PATH", str(tmp_path / "deck.json"))
    monkeypatch.setenv("OT2_TIP_STATE_PATH", str(tmp_path / "tips.json"))
    client = _client(ui=True, trust_local_ui=False, edge_secret="s3cret")

    claim = client.post(
        "/control/claim",
        headers={"X-Edge-Key": "s3cret", "X-Auth-User": "cyril@lab"},
        json={"owner": "ot2-gateway-ui", "session_id": "edge-test", "ttl_s": 30},
    )
    assert claim.status_code == 200
    token = claim.json()["claim_token"]
    status = client.get("/status").json()
    assert status["details"]["claimed_by"]["owner"] == "cyril@lab"
    assert status["details"]["ui_mode"] == "edge"
    assert client.post("/control/release", headers={"X-Claim-Token": token}).status_code == 204


def test_identity_header_ignored_without_edge_key(tmp_path, monkeypatch):
    """X-Auth-User must never be trusted on a request that did not prove it
    came through the edge — otherwise direct curl could forge attribution."""
    monkeypatch.setenv("OT2_PLATE_STATE_PATH", str(tmp_path / "plate.json"))
    monkeypatch.setenv("OT2_DECK_STATE_PATH", str(tmp_path / "deck.json"))
    monkeypatch.setenv("OT2_TIP_STATE_PATH", str(tmp_path / "tips.json"))
    client = _client(ui=True, trust_local_ui=False, edge_secret="s3cret")

    claim = client.post(
        "/control/claim",
        headers={"X-Auth-User": "mallory@lab"},  # no X-Edge-Key
        json={"owner": "honest-owner", "session_id": "spoof-test", "ttl_s": 30},
    )
    assert claim.status_code == 200
    token = claim.json()["claim_token"]
    status = client.get("/status").json()
    assert status["details"]["claimed_by"]["owner"] == "honest-owner"
    assert client.post("/control/release", headers={"X-Claim-Token": token}).status_code == 204


def test_open_mode_reports_itself_on_status():
    client = _client(ui=True, trust_local_ui=True)
    assert client.get("/status").json()["details"]["ui_mode"] == "open"


def test_trust_switch_env_is_read(monkeypatch):
    monkeypatch.setenv("OT2_TRUST_LOCAL_UI", "false")
    monkeypatch.setenv("OT2_EDGE_SECRET", "s3cret")
    client = TestClient(create_app(dry_run=True))
    assert client.get("/labware").status_code == 404
    assert client.get("/labware", headers={"X-Edge-Key": "s3cret"}).status_code == 200


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
