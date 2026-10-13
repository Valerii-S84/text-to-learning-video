
from anna_backend.tts import ANNA_MARIN_PROFILE, SpeechProfile


def test_anna_voice_profile_uses_marin_and_voice_message_direction() -> None:
    profile = ANNA_MARIN_PROFILE
    instructions = " ".join(profile.instructions.split())

    assert profile.model == "gpt-4o-mini-tts"
    assert profile.voice == "marin"
    assert profile.speed == 1.0
    assert "private voice message" in instructions
    assert "slightly lower comfortable register" in instructions
    assert "clean Hochdeutsch" in instructions
    assert "transitions between sentences brief and natural" in instructions
    assert "no extended silence" in instructions
    assert "never uncertainty in German pronunciation" in instructions
    assert "Do not imitate learner errors or a foreign accent" in instructions


def test_accepts_configured_non_default_voice() -> None:
    profile = SpeechProfile(
        model="gpt-4o-mini-tts",
        voice="shimmer",
        instructions="Speak clearly.",
    )

    profile.validate()
