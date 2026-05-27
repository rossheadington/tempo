"""Tests for ``tempo.bot.transcribe`` (Phase 10 / VOICE-04..06).

Covers:

* :func:`get_model` before :func:`warm_model` raises ``RuntimeError``.
* :func:`warm_model` is idempotent: a second call does NOT reconstruct
  :class:`WhisperModel`.
* :func:`warm_model` passes the three ``whisper_*`` settings + ``cpu_threads=4``
  through to the :class:`WhisperModel` constructor.
* env-var overrides flow end-to-end: ``WHISPER_MODEL_NAME=base.en`` etc.
  surface as constructor args.
* :func:`transcribe_file` iterates the segments generator eagerly (the
  faster-whisper foot-gun) and returns the joined transcript.
* Integration: actually load ``small.en`` and transcribe the committed
  ``tests/fixtures/voice/sample.ogg`` -- the only test that touches the
  real model. Marked ``slow`` (no marker convention yet; see docstring).

Every test calls :func:`tempo.bot.transcribe._reset_for_tests` via an autouse
fixture so the module-level ``_MODEL`` singleton does not leak between tests
(``monkeypatch`` alone won't undo a real ``WhisperModel`` rebind).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tempo.bot import transcribe
from tempo.config import Settings

FIXTURE_OGG = Path(__file__).parent / "fixtures" / "voice" / "sample.ogg"


def _clear_whisper_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop every WHISPER_* env var so a developer's real ``.env`` cannot leak."""
    for key in (
        "WHISPER_MODEL_NAME",
        "WHISPER_COMPUTE_TYPE",
        "WHISPER_DEVICE",
        "TEMPO_WHISPER_MODEL_NAME",
        "TEMPO_WHISPER_COMPUTE_TYPE",
        "TEMPO_WHISPER_DEVICE",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _reset_transcribe_singleton() -> None:
    """Hard-reset the module-level singleton around every test in this file."""
    transcribe._reset_for_tests()
    yield
    transcribe._reset_for_tests()


# ---------------------------------------------------------------------------
# get_model() before warm_model() raises
# ---------------------------------------------------------------------------


def test_get_model_before_warm_raises() -> None:
    # Autouse fixture has already _reset_for_tests; double-check the invariant.
    assert transcribe._MODEL is None
    with pytest.raises(RuntimeError, match="not warmed"):
        transcribe.get_model()


# ---------------------------------------------------------------------------
# warm_model() idempotency + value passthrough
# ---------------------------------------------------------------------------


def test_warm_model_idempotent(tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_whisper_env(monkeypatch)
    sentinel = object()
    constructor = MagicMock(return_value=sentinel)
    monkeypatch.setattr(transcribe, "WhisperModel", constructor)

    settings = Settings(_env_file=None)
    first = transcribe.warm_model(settings)
    second = transcribe.warm_model(settings)

    assert first is sentinel
    assert second is sentinel
    assert constructor.call_count == 1


def test_warm_model_uses_settings_values(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_whisper_env(monkeypatch)
    constructor = MagicMock(return_value=object())
    monkeypatch.setattr(transcribe, "WhisperModel", constructor)

    settings = Settings(_env_file=None)
    transcribe.warm_model(settings)

    constructor.assert_called_once_with(
        "small.en",
        device="cpu",
        compute_type="int8",
        cpu_threads=4,
    )


def test_warm_model_overridden_by_env(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_whisper_env(monkeypatch)
    monkeypatch.setenv("WHISPER_MODEL_NAME", "base.en")
    monkeypatch.setenv("WHISPER_COMPUTE_TYPE", "int8_float16")
    monkeypatch.setenv("WHISPER_DEVICE", "cuda")
    constructor = MagicMock(return_value=object())
    monkeypatch.setattr(transcribe, "WhisperModel", constructor)

    settings = Settings(_env_file=None)
    transcribe.warm_model(settings)

    constructor.assert_called_once_with(
        "base.en",
        device="cuda",
        compute_type="int8_float16",
        cpu_threads=4,
    )


# ---------------------------------------------------------------------------
# transcribe_file: eager generator iteration + parameter passthrough
# ---------------------------------------------------------------------------


def test_transcribe_file_iterates_segments_generator_eagerly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model = MagicMock()
    # segments is a one-shot generator; if transcribe_file fails to iterate it,
    # the returned string would be empty -- the exact foot-gun the test guards.
    fake_model.transcribe.return_value = (
        iter(
            [
                SimpleNamespace(text=" hi "),
                SimpleNamespace(text=" world "),
            ]
        ),
        SimpleNamespace(duration=1.5),
    )
    monkeypatch.setattr(transcribe, "_MODEL", fake_model)

    result = transcribe.transcribe_file(Path("dummy.ogg"))

    assert result == "hi world"
    # The exact contract Plan 10-02 will rely on:
    fake_model.transcribe.assert_called_once()
    args, kwargs = fake_model.transcribe.call_args
    assert args == ("dummy.ogg",)
    assert kwargs == {"language": "en", "beam_size": 5, "vad_filter": True}


def test_transcribe_file_empty_segments_returns_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Phase 10 contract: VAD silence -> empty string, NOT an error.
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (
        iter([]),
        SimpleNamespace(duration=0.5),
    )
    monkeypatch.setattr(transcribe, "_MODEL", fake_model)

    assert transcribe.transcribe_file(Path("silent.ogg")) == ""


# ---------------------------------------------------------------------------
# Real-model integration: load small.en and transcribe the committed fixture
# ---------------------------------------------------------------------------


def test_transcribe_file_real_fixture_returns_nonempty(
    tempo_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actually load ``small.en`` and transcribe the committed fixture.

    First-run downloads ~480 MB from HF Hub (one-time, cached under
    ``~/.cache/huggingface/hub``). Subsequent runs reuse the cache and
    transcription completes in <10s on M-series CPU. Left unmarked because
    pyproject.toml does not yet define a ``slow`` marker; document the
    wall-time expectation in the SUMMARY so we can add a marker later if
    needed.
    """
    _clear_whisper_env(monkeypatch)
    assert FIXTURE_OGG.exists(), f"missing fixture: {FIXTURE_OGG}"

    settings = Settings(_env_file=None)
    transcribe.warm_model(settings)
    text = transcribe.transcribe_file(FIXTURE_OGG)

    # Substring assertions are intentionally loose -- Whisper output is not
    # bit-exact and varies tiny amounts across runs; the fixture says
    # "hello world this is a test of tempo transcription" so we assert
    # presence of a stable subset of words.
    assert isinstance(text, str)
    assert len(text.strip()) > 3
    lower = text.lower()
    # At least one of these words MUST survive transcription; any zero-match
    # outcome is a real regression worth investigating.
    assert any(w in lower for w in ("hello", "world", "test", "tempo", "transcription"))
