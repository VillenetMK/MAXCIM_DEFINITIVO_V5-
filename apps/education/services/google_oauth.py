from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token


GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleOIDCError(RuntimeError):
    """Safe, user-facing Google authentication failure."""


class GoogleOIDCConfigurationError(GoogleOIDCError):
    pass


class GoogleOIDCAuthenticationError(GoogleOIDCError):
    pass


@dataclass(frozen=True)
class GoogleIdentity:
    id_token: str
    subject: str
    email: str
    display_name: str
    hosted_domain: str


class GoogleOIDCClient:
    """Minimal Google OpenID Connect authorization-code client.

    It requests only ``openid email profile``, verifies the signed ID token,
    enforces nonce and Workspace-domain claims, and never retains Google access
    or refresh tokens.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        allowed_domains: tuple[str, ...],
        timeout_seconds: float = 8.0,
    ):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.allowed_domains = tuple(
            sorted({domain.strip().lower() for domain in allowed_domains if domain.strip()})
        )
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "GoogleOIDCClient":
        raw_domains = str(config.get("GOOGLE_OAUTH_ALLOWED_DOMAINS") or "")
        return cls(
            client_id=str(config.get("GOOGLE_OAUTH_CLIENT_ID") or ""),
            client_secret=str(config.get("GOOGLE_OAUTH_CLIENT_SECRET") or ""),
            allowed_domains=tuple(raw_domains.split(",")),
            timeout_seconds=float(config.get("GOOGLE_OAUTH_TIMEOUT_SECONDS") or 8),
        )

    @property
    def ready(self) -> bool:
        return bool(self.client_id and self.client_secret and self.allowed_domains)

    @staticmethod
    def create_pkce_pair() -> tuple[str, str]:
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        return verifier, challenge

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        if not self.ready:
            raise GoogleOIDCConfigurationError(
                "El acceso institucional con Google todavía no está configurado."
            )
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        if len(self.allowed_domains) == 1:
            # This improves account selection; the signed ``hd`` claim is still
            # validated after the callback because the hint is not access control.
            params["hd"] = self.allowed_domains[0]
        return f"{GOOGLE_AUTHORIZATION_URL}?{urlencode(params)}"

    def exchange_and_verify(
        self,
        *,
        code: str,
        redirect_uri: str,
        nonce: str,
        code_verifier: str,
    ) -> GoogleIdentity:
        if not self.ready:
            raise GoogleOIDCConfigurationError(
                "El acceso institucional con Google todavía no está configurado."
            )
        try:
            response = requests.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise GoogleOIDCAuthenticationError(
                "No se pudo contactar el servicio de acceso de Google."
            ) from exc
        if response.status_code >= 400:
            raise GoogleOIDCAuthenticationError(
                "Google no pudo completar el inicio de sesión. Inténtalo nuevamente."
            )
        try:
            token_payload = response.json()
        except ValueError as exc:
            raise GoogleOIDCAuthenticationError(
                "Google devolvió una respuesta de acceso no válida."
            ) from exc

        raw_id_token = str(token_payload.get("id_token") or "").strip()
        if not raw_id_token:
            raise GoogleOIDCAuthenticationError(
                "Google no devolvió la identidad verificada de la cuenta."
            )
        try:
            claims = google_id_token.verify_oauth2_token(
                raw_id_token,
                GoogleAuthRequest(),
                audience=self.client_id,
            )
        except (ValueError, TypeError) as exc:
            raise GoogleOIDCAuthenticationError(
                "La identidad devuelta por Google no superó la validación."
            ) from exc

        received_nonce = str(claims.get("nonce") or "")
        if not received_nonce or not hmac.compare_digest(received_nonce, nonce):
            raise GoogleOIDCAuthenticationError(
                "La respuesta de Google no corresponde a esta sesión de acceso."
            )

        subject = str(claims.get("sub") or "").strip()
        email = str(claims.get("email") or "").strip().lower()
        hosted_domain = str(claims.get("hd") or "").strip().lower()
        email_domain = email.rpartition("@")[2]
        if claims.get("email_verified") is not True or not subject or not email:
            raise GoogleOIDCAuthenticationError(
                "Google no confirmó el correo institucional de la cuenta."
            )
        if (
            hosted_domain not in self.allowed_domains
            or email_domain not in self.allowed_domains
        ):
            raise GoogleOIDCAuthenticationError(
                "Utiliza una cuenta institucional autorizada del colegio."
            )

        return GoogleIdentity(
            id_token=raw_id_token,
            subject=subject,
            email=email,
            display_name=str(claims.get("name") or email).strip(),
            hosted_domain=hosted_domain,
        )
