"""Text from outside the app made safe to show the model.

`label` is for names (Yahoo league, team and manager names); `untrusted` is for longer free text
(news headlines and summaries). Both are data, never instructions: they can't carry layout,
chat-role markers or hidden characters, and `untrusted` also blanks instruction-shaped sentences.
"""
import re
import unicodedata

MAX_LABEL = 50
MAX_UNTRUSTED = 280
REMOVED = "[text removed]"

_ZERO_WIDTH = re.compile("[­᠎​-‏‪-‮⁠-⁯﻿]")
# Chat-template tokens (<|im_start|>, <|eot_id|>) and role tags (<system>, </tool>).
_SPECIAL_TOKENS = re.compile(r"<\|[^>]{0,40}?\|?>|</?\s*(?:system|assistant|user|developer|tool|im_start|im_end)\b[^>]{0,30}>",
                             re.IGNORECASE)
_ROLE_MARKER = re.compile(r"\b(system|assistant|developer|user|tool)\s*:", re.IGNORECASE)
_MARKUP = re.compile(r"`+|#{2,}")
# Sentences that address the model rather than report news; the rest of the sentence goes too.
_INSTRUCTION = re.compile(
    r"(?:\b(?:ignore|disregard|forget|override)\b[^.!?]{0,40}?\b(?:instructions?|rules|prompts?|messages|context|above)\b"
    r"|\byou\s+are\s+now\b|\bfrom\s+now\s+on\b|\bact\s+as\b|\bpretend\s+(?:to\s+be|you)\b"
    r"|\b(?:system|developer)\s+(?:prompt|message|instructions?)\b|\bnew\s+instructions?\b"
    r"|\b(?:call|use|invoke|run)\s+the\s+\w+\s+tool\b|\bremember\s+(?:that|this)\b"
    r"|\b(?:tell|instruct|inform)\s+the\s+(?:user|assistant|model|ai)\b|\bas\s+an\s+ai\b)"
    r"[^.!?]*[.!?]?", re.IGNORECASE)


def label(text: str | None) -> str:
    """Printable, one line, at most MAX_LABEL characters: a label can't smuggle in instructions or layout."""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in (text or ""))
    return " ".join(cleaned.split())[:MAX_LABEL]


def untrusted(text: str | None, limit: int = MAX_UNTRUSTED) -> str:
    """Outside free text as one clean line of at most `limit` characters (plus an ellipsis when cut)."""
    cleaned = _ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", text or ""))
    cleaned = "".join(ch if ch.isprintable() else " " for ch in cleaned)
    cleaned = _SPECIAL_TOKENS.sub(" ", cleaned)
    cleaned = _INSTRUCTION.sub(f" {REMOVED} ", cleaned)
    cleaned = _MARKUP.sub(" ", _ROLE_MARKER.sub(r"\1 -", cleaned))
    cleaned = " ".join(cleaned.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0] + "…"
