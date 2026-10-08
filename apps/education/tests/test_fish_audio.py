import pytest
import requests

from services.fish_audio import FISH_TTS_URL, FishAudioClient, FishAudioError

PCM = b"\x01\x00" * 100


class Response:
    def __init__(self, status=200, content=PCM, text=""):
        self.status_code = status
        self.content = content
        self.text = text


class Session:
    """Falsa sesión HTTP: devuelve (o lanza) lo que se le encole, en orden."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def client(session, **kwargs):
    kwargs.setdefault("retry_delay", 0)
    return FishAudioClient("clave-secreta", session=session, **kwargs)


def test_sends_the_documented_request():
    session = Session(Response())
    audio = client(session, model="s2.1-pro", reference_id="voz-123", speed=1.2).synthesize_pcm("Hola.")

    assert audio == PCM
    call = session.calls[0]
    assert call["url"] == FISH_TTS_URL == "https://api.fish.audio/v1/tts"
    assert call["headers"]["Authorization"] == "Bearer clave-secreta"
    assert call["headers"]["model"] == "s2.1-pro"
    assert call["json"] == {
        "text": "Hola.",
        "format": "pcm",
        "sample_rate": 44_100,
        "reference_id": "voz-123",
        "prosody": {"speed": 1.2},
    }
    assert call["timeout"]


def test_optional_fields_are_omitted_when_not_configured():
    session = Session(Response())
    client(session).synthesize_pcm("Hola.")

    body = session.calls[0]["json"]
    assert "reference_id" not in body and "prosody" not in body
    assert session.calls[0]["headers"]["model"] == "s2.1-pro-free"


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_errors_are_retried(status):
    session = Session(Response(status), Response(status), Response())
    assert client(session).synthesize_pcm("Hola.") == PCM
    assert len(session.calls) == 3


def test_connection_errors_are_retried_and_then_reported():
    session = Session(*[requests.ConnectionError("sin red")] * 3)
    with pytest.raises(FishAudioError, match="conectar"):
        client(session).synthesize_pcm("Hola.")
    assert len(session.calls) == 3


def test_gives_up_after_the_last_attempt():
    session = Session(Response(503), Response(503))
    with pytest.raises(FishAudioError, match="503"):
        client(session, max_attempts=2).synthesize_pcm("Hola.")
    assert len(session.calls) == 2


@pytest.mark.parametrize("status, message", [
    (401, "FISH_API_KEY"),
    (402, "FISH_AUDIO_MODEL"),
    (422, "422"),
])
def test_rejections_are_not_retried(status, message):
    session = Session(Response(status, text="detalle"))
    with pytest.raises(FishAudioError, match=message):
        client(session).synthesize_pcm("Hola.")
    assert len(session.calls) == 1


def test_error_messages_never_leak_the_api_key():
    session = Session(Response(401), Response(422, text="x"))
    for _ in range(2):
        with pytest.raises(FishAudioError) as caught:
            client(Session(session.outcomes.pop(0))).synthesize_pcm("Hola.")
        assert "clave-secreta" not in str(caught.value)


def test_empty_audio_is_retried_then_reported():
    session = Session(Response(content=b""), Response(content=b""), Response(content=b""))
    with pytest.raises(FishAudioError, match="vacío"):
        client(session).synthesize_pcm("Hola.")
    assert len(session.calls) == 3


def test_a_trailing_odd_byte_is_dropped_to_keep_16_bit_samples():
    session = Session(Response(content=PCM + b"\x07"))
    assert client(session).synthesize_pcm("Hola.") == PCM


@pytest.mark.parametrize("speed", [0.4, 2.1, -1])
def test_speed_outside_the_documented_range_is_rejected(speed):
    with pytest.raises(ValueError):
        FishAudioClient("clave", speed=speed)


def test_an_api_key_is_required():
    with pytest.raises(ValueError):
        FishAudioClient("")
