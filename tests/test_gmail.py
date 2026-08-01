import base64
from email import policy
from email.parser import BytesParser
from pathlib import Path

import channels.gmail as gmail


def _configure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(gmail, "settings", gmail.GmailSettings(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="http://localhost:8010/integrations/gmail/callback",
        connect_token="connect-token",
        lead_recipients_csv="admin@example.test",
        state_db_path=tmp_path / "state.sqlite3",
    ))
    monkeypatch.setattr(gmail, "store", gmail.OAuthStore(tmp_path / "state.sqlite3"))


def test_landing_question_sends_a_gmail_lead(monkeypatch, tmp_path: Path) -> None:
    _configure(monkeypatch, tmp_path)
    gmail.store.save_refresh_token("refresh-token")
    monkeypatch.setattr(gmail, "_refresh_access_token", lambda token: "access-token")
    sent = []
    monkeypatch.setattr(gmail, "_gmail_request", lambda payload, token: sent.append((payload, token)))

    response = gmail.send_landing_lead(gmail.LandingLead(
        type="question",
        name="Ada Lovelace",
        phone="+351000000000",
        email="ada@example.test",
        message="When is the next course?",
        source="Areola & Skin Defect Camouflage | https://example.test/courses/areola/",
    ))

    assert response == {"accepted": True}
    assert sent[0][1] == "access-token"
    assert "raw" in sent[0][0]
    message = BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(sent[0][0]["raw"])
    )
    assert message["Subject"] == "[BLB] New request about Areola & Skin Defect Camouflage - Ada Lovelace"
    assert "New request about: Areola & Skin Defect Camouflage" in message.get_content()
    assert "Course page: https://example.test/courses/areola/" in message.get_content()
    assert "Request type: Ask a question" in message.get_content()
    assert "Message: When is the next course?" in message.get_content()
    assert gmail.delivery_status() == {
        "oauth_configured": True,
        "recipient_configured": True,
        "sender_connected": True,
    }
