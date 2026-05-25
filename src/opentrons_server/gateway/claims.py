"""In-memory cooperative claim manager for gateway control endpoints."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import ClaimedBy, ClaimRequest, ClaimResponse


class ClaimConflict(Exception):
    def __init__(self, claimed_by: ClaimedBy, retry_after_s: float) -> None:
        super().__init__("device is already claimed")
        self.claimed_by = claimed_by
        self.retry_after_s = retry_after_s


class UnknownClaim(Exception):
    pass


class ClaimManager:
    """Small single-device claim store.

    Claims are cooperative coordination, not authentication. A process restart
    clears the claim, which is acceptable for the current in-memory gateway.
    """

    def __init__(self, heartbeat_fraction: float = 0.5) -> None:
        self._token: Optional[str] = None
        self._owner: Optional[str] = None
        self._session_id: Optional[str] = None
        self._expires_at: Optional[datetime] = None
        self._ttl_s: float = 30.0
        self._heartbeat_fraction = heartbeat_fraction

    def _expired(self) -> bool:
        return self._expires_at is not None and datetime.now(timezone.utc) >= self._expires_at

    def _clear_if_expired(self) -> None:
        if self._expired():
            self._token = None
            self._owner = None
            self._session_id = None
            self._expires_at = None

    def current(self) -> Optional[ClaimedBy]:
        self._clear_if_expired()
        if not self._session_id or not self._owner or not self._expires_at:
            return None
        return ClaimedBy(
            session_id=self._session_id,
            owner=self._owner,
            expires_at=self._expires_at,
        )

    def acquire(self, request: ClaimRequest) -> ClaimResponse:
        self._clear_if_expired()
        current = self.current()
        if current is not None and current.session_id != request.session_id:
            retry_after_s = max((current.expires_at - datetime.now(timezone.utc)).total_seconds(), 0.0)
            raise ClaimConflict(current, retry_after_s)

        self._token = self._token if current else secrets.token_urlsafe(24)
        self._owner = request.owner
        self._session_id = request.session_id
        self._ttl_s = max(request.ttl_s, 5.0)
        self._expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._ttl_s)
        return self._response()

    def heartbeat(self, token: Optional[str]) -> ClaimResponse:
        if not self.validate(token):
            raise UnknownClaim("claim token is unknown or expired")
        self._expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._ttl_s)
        return self._response()

    def release(self, token: Optional[str]) -> None:
        if token is None or token == self._token:
            self._token = None
            self._owner = None
            self._session_id = None
            self._expires_at = None

    def validate(self, token: Optional[str]) -> bool:
        self._clear_if_expired()
        return bool(token and self._token and token == self._token)

    def force_clear(self) -> None:
        self.release(self._token)

    def _response(self) -> ClaimResponse:
        if self._token is None or self._expires_at is None:
            raise UnknownClaim("no active claim")
        return ClaimResponse(
            claim_token=self._token,
            heartbeat_interval_s=max(self._ttl_s * self._heartbeat_fraction, 1.0),
            expires_at=self._expires_at,
        )
