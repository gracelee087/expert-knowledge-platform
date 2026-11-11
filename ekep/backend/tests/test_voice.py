"""Voice-Pfad und Fehlerbehandlung."""
from conftest import EXPERT, wait_idle


def test_audio_answer_transcribed_and_processed(client, started):
    _, iv = started
    r = client.post(f"/api/interviews/{iv['id']}/answer/audio", headers=EXPERT,
                    files={"audio": ("a.webm", b"\x1aE\xdf\xa3fakeaudio", "audio/webm")}, data={"duration_s": "7.5"})
    assert r.status_code == 200, r.text
    turn = r.json()["expert_turn"]
    assert turn["input_mode"] == "voice"
    assert abs(turn["t_start"] - max(0.0, turn["t_end"] - 7.5)) < 0.2  # Zeitstempel aus Audiodauer
    wait_idle(client, iv["id"])


def test_stt_failure_returns_502_and_keeps_state(client, started, fakes):
    _, speech = fakes
    _, iv = started
    speech.fail = True
    r = client.post(f"/api/interviews/{iv['id']}/answer/audio", headers=EXPERT,
                    files={"audio": ("a.webm", b"data", "audio/webm")})
    assert r.status_code == 502
    st = client.get(f"/api/interviews/{iv['id']}", headers=EXPERT).json()
    assert st["status"] == "in_progress" and len(st["turns"]) == 1


def test_silence_returns_422(client, started, fakes):
    _, speech = fakes
    _, iv = started
    speech.next_text = " "
    r = client.post(f"/api/interviews/{iv['id']}/answer/audio", headers=EXPERT,
                    files={"audio": ("a.webm", b"data", "audio/webm")})
    assert r.status_code == 422


def test_empty_audio_rejected(client, started):
    _, iv = started
    r = client.post(f"/api/interviews/{iv['id']}/answer/audio", headers=EXPERT, files={"audio": ("a.webm", b"", "audio/webm")})
    assert r.status_code == 400


def test_pii_in_spoken_answer_is_redacted(client, started, fakes):
    _, speech = fakes
    _, iv = started
    speech.next_text = "Der Kunde hat die IBAN DE89 3704 0044 0532 0130 00 und ruft oft an."
    r = client.post(f"/api/interviews/{iv['id']}/answer/audio", headers=EXPERT, files={"audio": ("a.webm", b"x", "audio/webm")})
    t = r.json()["expert_turn"]
    assert "DE89" not in t["text"] and t["redactions"] == 1


def test_tts(client):
    r = client.post("/api/tts", headers=EXPERT, json={"text": "Guten Tag"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"


def test_browser_transcribed_answer_keeps_voice_mode_and_duration(client, started):
    """VOICE_ENGINE=browser: Text kommt aus der Web Speech API, Zeitstempel aus der Sprechdauer."""
    _, iv = started
    r = client.post(f"/api/interviews/{iv['id']}/answer", headers=EXPERT,
                    json={"text": "Bei Classic 2003 prüfe ich zuerst das Tarifkennzeichen.", "mode": "voice", "duration_s": 6.0})
    assert r.status_code == 200
    t = r.json()["expert_turn"]
    assert t["input_mode"] == "voice"
    assert abs(t["t_start"] - max(0.0, t["t_end"] - 6.0)) < 0.2
    wait_idle(client, iv["id"])


def test_public_config_exposes_voice_engine(client):
    assert client.get("/api/config/public").json()["voice_engine"] in ("browser", "openai")
