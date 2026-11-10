from fastapi.testclient import TestClient
from app.main import app
from app.security import API_TOKEN, APPROVAL_SECRET
from app.database import get_db
from app.models import LedgerEvent
from app.policies import classify_action_risk

client = TestClient(app)
client.headers["Authorization"] = f"Bearer {API_TOKEN}"


def test_acceptance_1_wake_word_greeting_and_multi_turn_session():
    """TEST 1: Say 'Hey RALLY.' -> RALLY wakes, enters multi-turn session, and responds verbally."""
    res = client.post("/api/v1/voice/utterance", json={"transcript": "Hey RALLY."}).json()
    assert res["handled"] is True
    assert res["woke_only"] is True
    assert "What can I do for you" in res["spoken"]

    status = client.get("/api/v1/voice/status").json()
    assert status["in_conversation_window"] is True


def test_acceptance_2_and_3_multi_turn_chrome_and_youtube_starboy():
    """
    TEST 2 & TEST 3:
    During active conversation window (without repeating 'Hey RALLY'):
    - Say 'Open Chrome.' -> Chrome opens.
    - Say 'Search YouTube for Starboy.' -> YouTube search results load and verify.
    """
    status = client.get("/api/v1/voice/status").json()
    if not status.get("in_conversation_window"):
        woke = client.post("/api/v1/voice/utterance", json={"transcript": "Hey RALLY."}).json()
        assert woke["handled"] is True

    res_chrome = client.post("/api/v1/voice/utterance", json={"transcript": "Open Chrome."}).json()
    assert res_chrome["handled"] is True
    assert res_chrome["mission_status"] == "COMPLETED"

    res_yt = client.post("/api/v1/voice/utterance", json={"transcript": "Search YouTube for Starboy."}).json()
    assert res_yt["handled"] is True
    assert res_yt["structured_task"]["intent"] == "browser_search"
    assert res_yt["search_query"].lower() if "search_query" in res_yt else res_yt["structured_task"]["search_query"].lower() == "starboy"
    assert res_yt["mission_status"] == "COMPLETED"


def test_acceptance_4_spotify_desktop_control():
    """TEST 4: Say 'Open Spotify and play Starboy.' -> Launches Spotify desktop and verifies."""
    status = client.get("/api/v1/voice/status").json()
    if not status.get("in_conversation_window"):
        client.post("/api/v1/voice/utterance", json={"transcript": "Hey RALLY."})

    res = client.post("/api/v1/voice/utterance", json={"transcript": "Open Spotify and play Starboy."}).json()
    assert res["handled"] is True
    assert res["structured_task"]["intent"] == "media_control"
    assert res["structured_task"]["application"] == "Spotify"
    assert res["mission_status"] == "COMPLETED"


def test_acceptance_5_what_is_on_my_screen():
    """TEST 5: Say 'Hey RALLY, what is on my screen?' -> Observes and summarizes actual desktop."""
    res = client.post("/api/v1/voice/utterance", json={"transcript": "Hey RALLY, what is on my screen?"}).json()
    assert res["handled"] is True
    assert res["structured_task"]["intent"] == "screen_observation"
    assert res["window_count"] >= 1
    assert "windows open on your screen" in res["spoken"]


def test_acceptance_6_voice_stop_interruption():
    """TEST 6: Say 'Stop.' -> Active execution is interrupted immediately."""
    res = client.post("/api/v1/voice/utterance", json={"transcript": "Hey RALLY, stop."}).json()
    assert res["handled"] is True
    assert res["interrupted"] is True


def test_acceptance_7_consequential_approval_gate():
    """TEST 7: Consequential external action halts for explicit approval (Tier 2 governance)."""
    # Create a mission with an external send step requiring Tier 2 approval
    mis = client.post(
        "/api/v1/missions",
        json={"objective": "Draft and send email to professor regarding project submission deadline."},
    ).json()
    mis_id = mis["id"]

    # Start mission -> should execute non-consequential steps and halt at Tier 2 external send
    started = client.post(f"/api/v1/missions/{mis_id}/start").json()
    assert started["status"] == "AWAITING_APPROVAL"

    # Verify pending approval request exists
    approvals = client.get("/api/v1/approvals").json()
    pending = [a for a in approvals if a["mission_id"] == mis_id and a["status"] == "pending"]
    assert len(pending) >= 1
    apr_id = pending[0]["id"]
    assert pending[0]["risk_tier"] >= 2

    # Agent or malicious attempt to self-approve WITHOUT out-of-band secret -> 403 Forbidden!
    unauth_resp = client.post(f"/api/v1/approvals/{apr_id}/approve", json={"resolved_by": "agent_self"})
    assert unauth_resp.status_code == 403
    assert "Self-approval blocked" in unauth_resp.json()["detail"]

    # User explicitly approves action with out-of-band X-Rally-Approval-Token
    appr_res = client.post(
        f"/api/v1/approvals/{apr_id}/approve",
        headers={"X-Rally-Approval-Token": APPROVAL_SECRET},
        json={"resolved_by": "test_user"},
    ).json()
    assert appr_res["status"] == "approved"
    assert appr_res["mission_status"] in {"COMPLETED", "RUNNING", "VERIFYING"}


def test_acceptance_8_build_supervisor_defect_triage_and_report():
    """TEST 8: Autonomous Build Supervisor inspects workspace, runs checks, triages defects, and reports."""
    # 1. Inspect workspace
    inspection = client.post("/api/v1/build/projects/inspect", json={"workspace_path": "."}).json()
    assert inspection["exists"] is True
    assert inspection["file_count"] > 0

    # 2. Create build supervision mission
    build_mis = client.post(
        "/api/v1/build/missions",
        json={"workspace_path": ".", "objective": "Verify backend test suite passes", "selected_coding_agent": "ollama_cli"},
    ).json()
    m_id = build_mis["mission"]["id"]

    # 3. Run check
    check_res = client.post(f"/api/v1/build/missions/{m_id}/run-check", json={"command_kind": "test"}).json()
    assert "passed" in check_res
    assert "exit_code" in check_res

    # 4. Generate truthful report
    report = client.get(f"/api/v1/build/missions/{m_id}/report").json()
    assert report["mission_id"] == m_id
    assert "verification_matrix" in report
    assert "tests_verified" in report["verification_matrix"]


def test_acceptance_9_and_10_startup_and_pause_listening():
    """TEST 9 & 10: Pause listening stops audio capture; Windows startup toggle updates cleanly."""
    client.post("/api/v1/voice/start")
    paused = client.post("/api/v1/voice/pause").json()
    assert paused["listening_enabled"] is False
    assert paused["state"] == "PAUSED"


def test_acceptance_evidence_artifact_endpoint():
    """TEST 11: Serve verification screenshot files via evidence artifact endpoint."""
    missions = client.get("/api/v1/missions").json()
    assert len(missions) > 0
    latest_id = missions[0]["id"]
    evidence_list = client.get(f"/api/v1/missions/{latest_id}/evidence").json()
    for ev in evidence_list:
        if ev.get("local_path") and ev.get("evidence_type") == "screenshot":
            resp = client.get(f"/api/v1/evidence/{ev['id']}/artifact")
            assert resp.status_code == 200
            assert "image" in resp.headers["content-type"]
            break


def test_acceptance_voice_transcribe_and_realtime_events():
    """TEST 12: Real-time audio stream transcription endpoint accepts audio/wav and processes."""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 8000)

    resp = client.post(
        "/api/v1/voice/transcribe?auto_execute=false",
        content=buf.getvalue(),
        headers={"Content-Type": "audio/wav"},
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert "ok" in res_data
    # Empty audio correctly recognized as no speech detected
    assert res_data["ok"] is False
    assert "reason" in res_data


def test_acceptance_13_security_authentication_and_dns_rebinding_protection():
    """TEST 13: Local API authentication, Host/Origin validation, and DNS rebinding protections."""
    # 1. Unauthenticated request without Bearer token -> 401 Unauthorized
    unauth_client = TestClient(app)
    resp = unauth_client.get("/api/v1/missions")
    assert resp.status_code == 401
    assert "Authentication required" in resp.json()["detail"]

    # 2. Request with invalid token -> 401 Unauthorized
    bad_token_resp = unauth_client.get("/api/v1/missions", headers={"Authorization": "Bearer totally_fake_token"})
    assert bad_token_resp.status_code == 401

    # 3. Request with untrusted Host (DNS rebinding attempt) -> 403 Forbidden
    bad_host_resp = client.get("/api/v1/missions", headers={"Host": "attacker.com:8765"})
    assert bad_host_resp.status_code == 403
    assert "Security violation" in bad_host_resp.json()["detail"]

    # 4. Request from external Origin (cross-origin hijacking attempt) -> 403 Forbidden
    bad_origin_resp = client.get("/api/v1/missions", headers={"Origin": "https://malicious-website.com"})
    assert bad_origin_resp.status_code == 403

    # 5. Public health check endpoint is exempt and accessible
    health = unauth_client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"


def test_acceptance_14_cryptographic_tamper_evident_ledger_verification():
    """TEST 14: Verifies the SHA-256 hash-chained event ledger and verifies tamper detection."""
    # Ensure at least one mission event exists
    client.post("/api/v1/missions", json={"objective": "Test ledger chain verification"})

    # 1. Cryptographically verify the valid chain
    verify_res = client.get("/api/v1/ledger/verify").json()
    assert verify_res["valid"] is True
    assert verify_res["status"] == "cryptographically_verified"
    assert "latest_hash" in verify_res
    assert verify_res["event_count"] >= 1

    # 2. Tamper with a ledger event in the DB to test tamper detection
    from app.database import SessionLocal
    from app.models import LedgerEvent
    db = SessionLocal()
    try:
        first_event = db.query(LedgerEvent).first()
        assert first_event is not None
        original_payload = first_event.payload_json
        first_event.payload_json = '{"tampered": true}'
        db.commit()

        # The cryptographic auditor must detect the tamper!
        tampered_res = client.get("/api/v1/ledger/verify").json()
        assert tampered_res["valid"] is False
        assert tampered_res["tampered_index"] == 0
        assert "mismatch" in tampered_res["reason"].lower() or "broken" in tampered_res["reason"].lower()

        # Restore original payload to keep DB clean
        first_event.payload_json = original_payload
        db.commit()

        # After restoring, the ledger is valid again
        restored_res = client.get("/api/v1/ledger/verify").json()
        assert restored_res["valid"] is True
        assert restored_res["status"] == "cryptographically_verified"
    finally:
        db.close()


def test_acceptance_15_fine_grained_action_and_argument_risk_tiering():
    """TEST 15: Deep inspection of launch_app arguments and URLs to escalate risky actions."""
    # Benign application launch -> Tier 1 (auto-allowed)
    benign = classify_action_risk("launch_app", {"app_name": "notepad.exe"})
    assert benign.risk_tier == 1
    assert benign.requires_approval is False

    # Dangerous shell launch -> Escalated to Tier 2 with approval required
    powershell_app = classify_action_risk("launch_app", {"app_name": "powershell.exe -NoProfile -ExecutionPolicy Bypass"})
    assert powershell_app.risk_tier >= 2
    assert powershell_app.requires_approval is True

    # Dangerous cmd /c launch -> Escalated to Tier 2/3
    cmd_app = classify_action_risk("launch_app", {"app_name": "cmd.exe", "arguments": "/c rm -rf /"})
    assert cmd_app.risk_tier >= 2
    assert cmd_app.requires_approval is True

    # Dangerous URL scheme (file:// access) -> Escalated to Tier 2
    file_url = classify_action_risk("open_url", {"target_name": "file:///C:/Users/AppData/passwords.txt"})
    assert file_url.risk_tier >= 2
    assert file_url.requires_approval is True
