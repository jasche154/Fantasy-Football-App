"""Replay League: the chat line bank (services/replay_chat_lines.json) is complete, clean and well-formed."""
import re
import string

import pytest

from services import replay_chat as chat, replay_mood, replay_personas

BANNED = re.compile(
    r"\b(damn|hell|crap|shit\w*|fuck\w*|ass|asses|bitch\w*|bastard|piss\w*|dick|sucks?|kill\w*|die|died|dead|death"
    r"|injur\w*|hurt\w*|torn|concussion|surgery|hospital|retard\w*|stupid|idiot|slur)\b", re.IGNORECASE)
BUCKETS = [(kind, voice) for kind in chat.KINDS for voice in chat.VOICES if voice not in chat.QUIET.get(kind, ())]
NEW_VOICES = ("fan", "stasher", "haggler", "contender", "dealer", "tilted")
NEW_KINDS = ("offer_buy_low", "offer_haggle", "offer_haggle_counter", "decline_rival", "decline_room",
             "decline_attached", "decline_need", "decline_value", "decline_close", "decline_rule_traded",
             "decline_rule_cap", "decline_rule_ir", "decline_rule_lineup")
NEW_LINES = 5           # the newer styles and kinds have five lines a bucket (the user's choice)


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
    least = (NEW_LINES if voice in NEW_VOICES or kind in NEW_KINDS
             else chat.MIN_LINES_COMMON if kind in chat.COMMON else chat.MIN_LINES)

    assert len(lines) >= least
    assert len({_squashed(line) for line in lines}) == len(lines), "near-duplicate lines"
    for line in lines:
        assert len(line) <= chat.MAX_CHARS, line
        assert line.isascii() and "--" not in line, line
        assert _fields(line) <= set(chat.PLACEHOLDERS[kind]), line
        assert not BANNED.search(line), line
        assert line.format_map({name: "X" for name in chat.PLACEHOLDERS[kind]})


@pytest.mark.parametrize("kind", ["boom", "bust", "shop", "offer_worried", "offer_high", "offer_buy_low",
                                  "offer_haggle", "offer_haggle_counter", "decline_attached", "decline_rival",
                                  "decline_close", "decline_value", "decline_rule_traded"])
def test_a_line_about_one_player_names_him(kind):
    # The chat shows only the text: "5.2. A bad game is one game." leaves the league guessing who.
    unnamed = [line for lines in chat.bank()[kind].values() for line in lines if "{player}" not in line]
    assert unnamed == []


def test_a_need_offer_never_says_the_player_it_asks_for_sits_on_the_bench():
    # offer_need names the best player he asks for, usually one of the user's starters.
    benched = [line for lines in chat.bank()["offer_need"].values() for line in lines if "bench" in line.lower()]
    assert benched == []


def test_quiet_voices_have_no_bucket_and_never_post_it():
    for kind, voices in chat.QUIET.items():
        assert not set(voices) & set(chat.bank()[kind])
    assert all(replay_mood.SHOP_RATE[voice] == 0.0 for voice in chat.QUIET["shop"])
    assert all(replay_mood.SHOP_RATE[voice] > 0.0 for voice in chat.VOICES if voice not in chat.QUIET["shop"])


def test_the_one_style_kinds_belong_to_their_style_only():
    assert {kind: set(chat.bank()[kind]) for kind in chat.ONLY} == {kind: {voice} for kind, voice in chat.ONLY.items()}


def test_a_haggler_says_his_first_offer_is_an_opening_one():
    assert all(re.search(r"open|first|start", line, re.IGNORECASE) for line in chat.bank()["offer_haggle"]["haggler"])


@pytest.mark.parametrize("kind", ["decline_need", "decline_rule_lineup"])
def test_a_line_about_a_position_names_it(kind):
    assert all("{position}" in line for lines in chat.bank()[kind].values() for line in lines)


def test_a_close_value_reply_never_calls_it_a_lowball_and_a_far_one_never_calls_it_close():
    close = re.compile(r"\b(almost|nearly|a little|a bit|slightly|a touch|so close|in range|we.re near)\b", re.IGNORECASE)
    far = re.compile(r"\b(lowball|nowhere|far|miles|joke|long way|not even)\b", re.IGNORECASE)
    assert not [line for lines in chat.bank()["decline_value"].values() for line in lines if close.search(line)]
    assert not [line for lines in chat.bank()["decline_close"].values() for line in lines if far.search(line)]


def test_the_bank_is_big():
    assert sum(len(lines) for voices in chat.bank().values() for lines in voices.values()) >= 1000
