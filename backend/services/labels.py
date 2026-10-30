"""Names from outside the app (Yahoo league, team and manager names) made safe to show the model."""

MAX_LABEL = 50


def label(text: str | None) -> str:
    """Printable, one line, at most MAX_LABEL characters: a label can't smuggle in instructions or layout."""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in (text or ""))
    return " ".join(cleaned.split())[:MAX_LABEL]
