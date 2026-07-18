"""Gmail OAuth connection and public landing-lead delivery."""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field


router = APIRouter(prefix="/integrations/gmail", tags=["gmail"])

GMAIL_PROVIDER = "gmail"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
OAUTH_STATE_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class GmailSettings:
    client_id: str
    client_secret: str
    redirect_uri: str
    connect_token: str
    lead_recipients_csv: str
    state_db_path: Path

    @classmethod
    def from_environment(cls) -> "GmailSettings":
        return cls(
            client_id=os.getenv("GMAIL_CLIENT_ID", ""),
            client_secret=os.getenv("GMAIL_CLIENT_SECRET", ""),
            redirect_uri=os.getenv(
                "GMAIL_REDIRECT_URI", "http://localhost:8010/integrations/gmail/callback"
            ),
            connect_token=os.getenv("GMAIL_CONNECT_TOKEN", ""),
            lead_recipients_csv=os.getenv("LEAD_RECIPIENTS_CSV", "liquor.busy@gmail.com"),
            state_db_path=Path(os.getenv("GMAIL_STATE_DB", "data/gmail-state.sqlite3")),
        )

    @property
    def lead_recipients(self) -> list[str]:
        return [item.strip() for item in self.lead_recipients_csv.split(",") if item.strip()]

    @property
    def oauth_is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri and self.connect_token)


class LandingLead(BaseModel):
    type: Literal["question", "claim"]
    name: str = Field(min_length=1, max_length=160)
    phone: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=320)
    message: str = Field(default="", max_length=2_000)


class GmailConfigurationError(RuntimeError):
    """Gmail OAuth settings are incomplete."""


class GmailDeliveryError(RuntimeError):
    """Gmail rejected a delivery or could not be reached."""


class OAuthStore:
    """Small persistent store for the connected sender and short-lived OAuth states."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def save_refresh_token(self, refresh_token: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO oauth_refresh_tokens (provider, refresh_token, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(provider) DO UPDATE SET
                       refresh_token = excluded.refresh_token,
                       updated_at = excluded.updated_at""",
                (GMAIL_PROVIDER, refresh_token, _now().isoformat()),
            )

    def get_refresh_token(self) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT refresh_token FROM oauth_refresh_tokens WHERE provider = ?", (GMAIL_PROVIDER,)
            ).fetchone()
        return None if row is None else str(row[0])

    def create_connect_state(self, state: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM oauth_connect_states WHERE expires_at <= ?", (_now().isoformat(),)
            )
            connection.execute(
                "INSERT INTO oauth_connect_states (provider, state, expires_at) VALUES (?, ?, ?)",
                (GMAIL_PROVIDER, state, (_now() + OAUTH_STATE_TTL).isoformat()),
            )

    def consume_connect_state(self, state: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT expires_at FROM oauth_connect_states WHERE provider = ? AND state = ?",
                (GMAIL_PROVIDER, state),
            ).fetchone()
            connection.execute("DELETE FROM oauth_connect_states WHERE state = ?", (state,))
        return row is not None and datetime.fromisoformat(str(row[0])) > _now()

    def _connection(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
                provider TEXT PRIMARY KEY,
                refresh_token TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oauth_connect_states (
                provider TEXT NOT NULL,
                state TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL
            );
            """
        )
        return connection


settings = GmailSettings.from_environment()
store = OAuthStore(settings.state_db_path)


@router.get("/connect")
def connect_gmail(connect_token: str = Query(min_length=1)) -> RedirectResponse:
    _require_oauth_configuration()
    if not secrets.compare_digest(connect_token, settings.connect_token):
        raise HTTPException(status_code=404, detail="Integration not found")
    state = secrets.token_urlsafe(32)
    store.create_connect_state(state)
    return RedirectResponse(_authorization_url(state), status_code=303)


@router.get("/callback")
def gmail_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> PlainTextResponse:
    _require_oauth_configuration()
    if error:
        raise HTTPException(status_code=400, detail=f"Google authorization failed: {error}")
    if not code or not state or not store.consume_connect_state(state):
        raise HTTPException(status_code=400, detail="Invalid or expired Gmail authorization state")
    refresh_token = _exchange_authorization_code(code).get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise HTTPException(status_code=400, detail="Google did not return a refresh token")
    store.save_refresh_token(refresh_token)
    return PlainTextResponse("Gmail account connected. You can close this window.")


@router.post("/lead", status_code=202)
def send_landing_lead(lead: LandingLead) -> dict[str, bool]:
    message = lead.message.strip()
    if lead.type == "question" and not message:
        raise HTTPException(status_code=422, detail="A question is required")
    try:
        send_lead(
            context="Ask a question" if lead.type == "question" else "Claim a place",
            name=lead.name.strip(),
            phone=lead.phone.strip(),
            email=lead.email.strip(),
            message=message,
        )
    except (GmailConfigurationError, GmailDeliveryError) as exc:
        logging.exception("Landing Gmail lead delivery failed")
        raise HTTPException(status_code=502, detail="Lead delivery failed") from exc
    return {"accepted": True}


def send_lead(*, context: str, name: str, phone: str, email: str, message: str) -> None:
    _require_delivery_configuration()
    refresh_token = store.get_refresh_token()
    if refresh_token is None:
        raise GmailDeliveryError("No Gmail account is connected")

    email_message = EmailMessage()
    email_message["To"] = ", ".join(settings.lead_recipients)
    email_message["Subject"] = f"[BLB] {context} — {name}"
    email_message.set_content("\n".join([
        f"Context: {context}",
        f"Name: {name}",
        f"Email: {email}",
        f"Phone: {phone}",
        f"Question, if any: {message or '—'}",
    ]))
    email_message["Reply-To"] = email
    _gmail_request(
        {"raw": base64.urlsafe_b64encode(email_message.as_bytes()).decode()},
        _refresh_access_token(refresh_token),
    )


def _authorization_url(state: str) -> str:
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "scope": GMAIL_SEND_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })


def _require_oauth_configuration() -> None:
    if not settings.oauth_is_configured:
        raise GmailConfigurationError("Gmail OAuth settings are incomplete")


def _require_delivery_configuration() -> None:
    _require_oauth_configuration()
    if not settings.lead_recipients:
        raise GmailConfigurationError("LEAD_RECIPIENTS_CSV is required")


def _exchange_authorization_code(code: str) -> dict[str, object]:
    return _token_request({
        "code": code,
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "redirect_uri": settings.redirect_uri,
        "grant_type": "authorization_code",
    })


def _refresh_access_token(refresh_token: str) -> str:
    response = _token_request({
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    access_token = response.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise GmailDeliveryError("Google did not return an access token")
    return access_token


def _token_request(data: dict[str, str]) -> dict[str, object]:
    request = Request(
        "https://oauth2.googleapis.com/token",
        data=urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read())
    except (HTTPError, URLError, json.JSONDecodeError) as exc:
        raise GmailDeliveryError("Google token exchange failed") from exc
    if not isinstance(result, dict):
        raise GmailDeliveryError("Google token exchange returned an invalid response")
    return result


def _gmail_request(payload: dict[str, str], access_token: str) -> None:
    request = Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10):
            pass
    except (HTTPError, URLError) as exc:
        raise GmailDeliveryError("Gmail lead delivery failed") from exc


def _now() -> datetime:
    return datetime.now(UTC)
