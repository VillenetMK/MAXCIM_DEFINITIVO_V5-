"""Cliente mínimo de Fish Audio (texto a voz).

Documentación: https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech

Se pide el audio en PCM crudo (16 bits, mono) en vez de WAV: la respuesta llega
en streaming y no se documenta que el encabezado WAV traiga el tamaño correcto,
así que el WAV lo arma la app con la frecuencia que ella misma pidió.
"""
from __future__ import annotations

import time

import requests

FISH_TTS_URL = "https://api.fish.audio/v1/tts"
DEFAULT_MODEL = "s2.1-pro-free"
DEFAULT_SAMPLE_RATE = 44_100
MIN_SPEED, MAX_SPEED = 0.5, 2.0

# Fallos pasajeros del servicio (sobrecarga, límite de peticiones): se reintenta.
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_REJECTION_MESSAGES = {
    401: "Fish Audio rechazó la clave (revisa FISH_API_KEY).",
    402: "La cuenta de Fish Audio no tiene saldo o plan para este modelo (revisa FISH_AUDIO_MODEL).",
}


class FishAudioError(RuntimeError):
    """No se pudo generar el audio con Fish Audio. El mensaje nunca incluye la clave."""


class FishAudioClient:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        reference_id: str = "",
        speed: float = 1.0,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        timeout: tuple[float, float] = (10, 180),
        max_attempts: int = 3,
        retry_delay: float = 1.0,
        session: requests.Session | None = None,
    ):
        if not api_key:
            raise ValueError("Falta la clave de Fish Audio.")
        if not MIN_SPEED <= speed <= MAX_SPEED:
            raise ValueError(
                f"La velocidad de Fish Audio debe estar entre {MIN_SPEED} y {MAX_SPEED}."
            )
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.reference_id = reference_id
        self.speed = speed
        self.sample_rate = sample_rate
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self.retry_delay = retry_delay
        self._session = session or requests.Session()

    def synthesize_pcm(self, text: str) -> bytes:
        """Narra `text` y devuelve PCM de 16 bits, mono, a `self.sample_rate`."""
        payload: dict[str, object] = {
            "text": text,
            "format": "pcm",
            "sample_rate": self.sample_rate,
        }
        if self.reference_id:
            payload["reference_id"] = self.reference_id
        if self.speed != 1.0:
            payload["prosody"] = {"speed": self.speed}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "model": self.model,
        }

        last_error = FishAudioError("No se pudo generar el audio con Fish Audio.")
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._session.post(
                    FISH_TTS_URL, json=payload, headers=headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = FishAudioError(
                    f"No se pudo conectar con Fish Audio ({type(exc).__name__})."
                )
            else:
                if response.status_code == 200:
                    audio = response.content
                    if len(audio) >= 2:
                        return audio[: len(audio) - len(audio) % 2]
                    last_error = FishAudioError("Fish Audio devolvió un audio vacío.")
                elif response.status_code in _RETRYABLE_STATUSES:
                    last_error = FishAudioError(
                        f"Fish Audio no está disponible ahora (HTTP {response.status_code})."
                    )
                else:
                    raise FishAudioError(
                        _REJECTION_MESSAGES.get(response.status_code)
                        or f"Fish Audio rechazó la solicitud (HTTP {response.status_code}): "
                           f"{response.text[:200]}"
                    )
            if attempt < self.max_attempts:
                time.sleep(self.retry_delay * attempt)
        raise last_error
