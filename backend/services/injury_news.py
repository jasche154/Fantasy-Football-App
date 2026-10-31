"""Injury evidence from Sleeper's player news: "out for the season" and return timelines.

Sleeper's player map carries a news timestamp per player. During the hourly status refresh, every
QB/RB/WR/TE on IR, PUP, Out or Doubtful whose timestamp changed has his news re-read, and each
headline (title plus summary) is reduced to a signal:
- season_ending: "out for the season", "season-ending", "done for 2026", "miss the rest of the
  season" and similar, unless negated ("avoids a season-ending injury", "not season-ending");
- weeks: a return timeline, "12-to-16 weeks", "6-10 week timeline", "3-to-4-month absence",
  "out for at least four weeks", in a sentence about missing time (not looking back: "in the past
  five weeks").
A signal only counts when the nearest "Name (injury)" mention before it is the player's own, since
summaries often mention teammates ("joins Ricky Pearsall (knee), who is out for the season").
Only the signals are stored (services/injury_recovery.py reads them); news text is outside data and
never reaches the database or the model as text. Headlines published before the player's last game
are ignored, so an old timeline from an earlier injury doesn't count, and a timeline's return date
is the median of every headline's (later articles often restate the first diagnosis).
"""
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import InjuryNews, Player, PlayerStatus, utc_now
from integrations.sleeper import SleeperError
from integrations.sleeper_news import fetch_news

log = logging.getLogger(__name__)

OUT_TAGS = {"IR", "PUP", "OUT", "DOUBTFUL", "NFI", "IR-R", "PUP-R"}
POSITIONS = ("QB", "RB", "WR", "TE")
MAX_FETCHES = 150             # news calls per refresh (Sleeper asks for under 1,000 a minute)
WEEKS_PER_MONTH = 4.3
MAX_WEEKS = 26
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "eleven": 11, "twelve": 12, "a couple of": 2, "a few": 3, "several": 4}
NUMBER = r"(\d{1,2}|" + "|".join(sorted(WORDS, key=len, reverse=True)) + r")"
SEASON_ENDING = re.compile(
    r"season[- ]ending|out for (?:the )?(?:\d{4} )?(?:season|year)|done for (?:the )?(?:season|year|\d{4})"
    r"|(?:rest|remainder) of (?:the )?(?:\d{4} )?(?:nfl )?(?:season|year)|miss (?:the )?\d{4}(?: nfl)? season")
# Negated, partial ("a significant stretch of the remainder of the season") or speculative ("could miss").
NEGATION = re.compile(r"\b(?:avoid(?:s|ed)?|not|isn't|no|dodge[sd]?|rul(?:es|ed) out a|won't be|fears? of"
                      r"|(?:stretch|part|portion|much|most|chunk|some|majority|bulk) of|could|may|might|possibly"
                      r"|potentially)\b[^.]{0,40}$")
TIMELINE = re.compile(NUMBER + r"(?:[- ]?(?:to|-|–)[- ]?" + NUMBER + r")?[- ](week|month)s?\b")
# "pearsall (knee)", "etienne jr. (hamstring)": how Sleeper names each player in a summary
MENTION = re.compile(r"([a-z'-]+)(?:,? (?:jr|sr|ii|iii|iv|v)\.?)? \([^)]*\)")
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
LOOKING_BACK = re.compile(r"\b(?:past|last|previous|prior|first)\s+$")   # "fourth in the past five weeks"
ABOUT_ABSENCE = re.compile(r"\b(?:out|miss|missing|absence|sideline[ds]?|timeline|return|recover|recovery|shelved|expected)\b")


@dataclass(frozen=True)
class NewsEvidence:
    season_ending: bool
    weeks_left: float | None        # weeks still out from now, per the newest timeline
    published: datetime | None      # of the headline used


def _number(text: str) -> float:
    return float(text) if text.isdigit() else float(WORDS[text])


def last_name(full_name: str) -> str:
    words = [w for w in full_name.lower().replace(".", "").split() if w not in SUFFIXES]
    return words[-1] if words else ""


def _about(prefix: str, surname: str | None) -> bool:
    """Is the text before a match about this player? Sleeper writes every player as "Name (injury)",
    so the nearest such mention before the match must be his ("joins Ricky Pearsall (knee), who is
    out for the season" is about Pearsall)."""
    if not surname:
        return True
    mentions = MENTION.findall(prefix)
    return not mentions or mentions[-1] == surname


def signal(item: Mapping, surname: str | None = None) -> dict | None:
    """{published, season_ending, weeks} for one headline about the player, or None when it says neither."""
    text = " ".join(t for t in (item.get("title"), item.get("summary")) if t).lower()
    ending = False
    for m in SEASON_ENDING.finditer(text):
        sentence = re.split(r"(?<=[.!?])\s", text[:m.start()])[-1]
        if not NEGATION.search(sentence) and _about(sentence, surname):
            ending = True
            break
    weeks = None
    for sentence in re.split(r"(?<=[.!?])\s", text):
        match = TIMELINE.search(sentence)
        if (match and ABOUT_ABSENCE.search(sentence) and not LOOKING_BACK.search(sentence[:match.start()])
                and _about(sentence[:match.start()], surname)):
            low = _number(match.group(1))
            high = _number(match.group(2)) if match.group(2) else low
            scale = WEEKS_PER_MONTH if match.group(3) == "month" else 1.0
            if 0 < low * scale <= MAX_WEEKS:
                weeks = [round(low * scale, 1), round(min(high * scale, MAX_WEEKS), 1)]
                break
    if not ending and weeks is None:
        return None
    return {"published": item.get("published"), "season_ending": ending, "weeks": weeks}


def signals(items: Iterable[Mapping], surname: str | None = None) -> list[dict]:
    return [s for item in items if (s := signal(item, surname))]


def evidence(stored: Iterable[Mapping], since: datetime | None, now: datetime) -> NewsEvidence | None:
    """Evidence published after `since` (his last game): any season-ending headline wins; otherwise
    the median return date implied by the timelines. Later articles often restate the first
    diagnosis ("out 12 weeks" again a month later), so no single headline is trusted to restart it."""
    usable = []
    for s in stored:
        try:
            published = datetime.fromisoformat(s["published"])
        except (KeyError, TypeError, ValueError):
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        if (since is None or published >= since) and published <= now:
            usable.append((published, s))
    usable.sort(key=lambda pair: pair[0], reverse=True)
    for published, s in usable:
        if s.get("season_ending"):
            return NewsEvidence(True, None, published)
    returns = sorted(published + timedelta(weeks=(s["weeks"][0] + s["weeks"][1]) / 2)
                     for published, s in usable if s.get("weeks"))
    if not returns:
        return None
    back = returns[len(returns) // 2] if len(returns) % 2 else returns[len(returns) // 2 - 1]   # median (earlier on ties)
    return NewsEvidence(False, round(max(0.0, (back - now).days / 7), 1), usable[0][0])


def load(session: Session) -> dict[int, list[dict]]:
    out = {}
    for row in session.scalars(select(InjuryNews)):
        try:
            parsed = json.loads(row.signals or "[]")
        except ValueError:
            continue
        if isinstance(parsed, list):
            out[row.player_id] = parsed
    return out


def refresh(session: Session, client: httpx.Client, limit: int = MAX_FETCHES) -> dict:
    """Re-read news for injured skill players whose Sleeper news timestamp changed."""
    rows = session.execute(
        select(PlayerStatus.player_id, PlayerStatus.injury_status, PlayerStatus.news_updated, Player.sleeper_player_id,
               Player.full_name)
        .join(Player, Player.id == PlayerStatus.player_id)
        .where(Player.position.in_(POSITIONS), Player.sleeper_player_id.is_not(None))).all()
    stored = {r.player_id: r for r in session.scalars(select(InjuryNews))}
    due = [r for r in rows if (r.injury_status or "").upper() in OUT_TAGS
           and (r.player_id not in stored or stored[r.player_id].news_updated != r.news_updated)]
    fetched = errors = 0
    for row in due[:limit]:
        try:
            items = fetch_news(row.sleeper_player_id, client)
        except (SleeperError, ValueError) as exc:
            errors += 1
            log.warning("Sleeper news failed for player %s: %s", row.player_id, type(exc).__name__)
            continue
        record = stored.get(row.player_id) or InjuryNews(player_id=row.player_id)
        found = signals(items, last_name(row.full_name))
        record.news_updated, record.signals, record.checked_at = row.news_updated, json.dumps(found), utc_now()
        session.add(record)
        fetched += 1
    session.commit()
    return {"due": len(due), "fetched": fetched, "errors": errors}
