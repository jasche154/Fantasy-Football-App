"""Replay League: the chat line bank (services/replay_chat_lines.json) is complete, clean and well-formed."""
import re
import string

import pytest

from services import replay_chat as chat, replay_mood, replay_personas

BANNED = re.compile(
    r"\b(damn|hell|crap|shit\w*|fuck\w*|ass|asses|bitch\w*|bastard|piss\w*|dick|sucks?|kill\w*|die|died|dead|death"
    r"|injur\w*|hurt\w*|torn|concussion|surgery|hospital|retard\w*|stupid|idiot|slur)\b", re.IGNORECASE)
BUCKETS = [(kind, voice) for kind in chat.KINDS for voice in chat.VOICES if voice not in chat.QUIET.get(kind, ())]


def _fields(line: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(line) if name is not None}


def _squashed(line: str) -> str:
    return re.sub(r"[^a-z]", "", line.lower())


def test_voices_are_the_personas_and_every_kind_is_in_the_bank():
    assert set(chat.VOICES) == set(replay_personas.PERSONAS)
    assert set(chat.bank()) == set(chat.KINDS)


@pytest.mark.parametrize(("kind", "voice"), BUCKETS)
def test_every_bucket_has_enough_distinct_clean_lines(kind, voice):
    lines = chat.bank()[kind][voice]
    least = chat.MIN_LINES_COMMON if kind in chat.COMMON else chat.MIN_LINES

    assert len(lines) >= least
    assert len({_squashed(line) for line in lines}) == len(lines), "near-duplicate lines"
    for line in lines:
        assert len(line) <= chat.MAX_CHARS, line
        assert line.isascii() and "--" not in line, line
        assert _fields(line) <= set(chat.PLACEHOLDERS[kind]), line
        assert not BANNED.search(line), line
        assert line.format_map({name: "X" for name in chat.PLACEHOLDERS[kind]})


def test_quiet_voices_have_no_bucket_and_never_post_it():
    for kind, voices in chat.QUIET.items():
        assert not set(voices) & set(chat.bank()[kind])
    assert all(replay_mood.SHOP_RATE[voice] == 0.0 for voice in chat.QUIET["shop"])


def test_the_bank_is_big():
    assert sum(len(lines) for voices in chat.bank().values() for lines in voices.values()) >= 1000
