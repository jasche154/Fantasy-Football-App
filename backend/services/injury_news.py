"""Injury evidence from Sleeper's player news: "out for the season" and return timelines.

Sleeper's player map carries a news timestamp per player. During the hourly status refresh, every
QB/RB/WR/TE tagged IR, PUP, suspended, Out or Doubtful whose timestamp changed has his news re-read,
and each headline (title plus summary) is reduced to a signal:
- season_ending: "out for the season", "season-ending", "done for 2026", "miss the rest of the
  (regular) season", "ending his season" and similar. Not when negated right before it ("avoids a
  season-ending injury", "not season-ending"), partial ("a significant stretch of the remainder of
  the season") or speculative ("could miss"), and not "out for the season opener".
- weeks: a return timeline, "12-to-16 weeks", "6-10 week timeline", "3-to-4-month absence",
  "out for at least four weeks", in a sentence about missing time. Absences already served don't
  count: "after a three-week absence", "after missing three weeks", "injured two weeks ago",
  "his fourth missed game in five weeks".
A signal only counts when it's about the player: the nearest "Name (injury)" mention before it (how
Sleeper writes every player) must be his, and a ", who ..." clause must follow his name ("joins
Ricky Pearsall (knee), who is out for the season" is about Pearsall).
Only the signals are stored (services/injury_recovery.py reads them); news text is outside data and
never reaches the database or the model as text. Each refetch merges new signals into the stored
ones (Sleeper returns only the newest headlines, and the first diagnosis matters). Headlines
published before the player's last kickoff are ignored, so an old timeline from an earlier injury
doesn't count, and a timeline's return date is the median of every headline's (later articles
often restate the first diagnosis).
"""
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import islice

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import InjuryNews, Player, PlayerStatus, utc_now
from integrations.sleeper import SleeperError
from integrations.sleeper_news import fetch_news
from services.ros_consensus import IR_STATUSES, SHORT_OUT

log = logging.getLogger(__name__)

OUT_TAGS = IR_STATUSES | SHORT_OUT            # the tags injury_recovery treats as "out now"
POSITIONS = ("QB", "RB", "WR", "TE")
MAX_FETCHES = 150             # news calls per refresh (Sleeper asks for under 1,000 a minute)
MAX_FAILURES_IN_ROW = 5       # then Sleeper is probably down: stop instead of waiting out 150 timeouts
MAX_TEXT = 1500               # characters of one headline parsed (bounds the regex work)
MAX_CANDIDATES = 5            # timeline matches tried per sentence
KEEP_DAYS = 240               # stored signals older than this are dropped (a season and its offseason)
WEEKS_PER_MONTH = 4.3
MAX_WEEKS = 26
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "a couple of": 2, "a few": 3, "several": 4}
NUMBER = r"(\d{1,2}|" + "|".join(sorted(WORDS, key=len, reverse=True)) + r")(?!\w)"   # not "four" in "fourth"
SEASON_ENDING = re.compile(
    r"season[- ]ending|out for (?:the )?(?:\d{4} )?(?:season|year)(?![- ]?(?:opener|finale|debut|'s))"
    r"|done for (?:the )?(?:season|year|\d{4})|ending his (?:\d{4} )?season|season is over"
    r"|(?:rest|remainder) of (?:the )?(?:\d{4} )?(?:nfl )?(?:regular )?(?:season|year)(?![- ]?(?:opener|finale|'s))"
    r"|miss (?:the )?\d{4}(?: nfl)? season")
# Negated, partial ("a significant stretch of the remainder of the season") or speculative ("could miss"),
# close before the match: "has not practiced since and will miss the rest of the season" still counts.
NEGATION = re.compile(r"\b(?:avoid(?:s|ed)?|not|isn't|no|dodge[sd]?|rul(?:es|ed) out a|won't be|fears? of"
                      r"|(?:stretch|part|portion|much|most|chunk|some|majority|bulk) of|could|may|might|possibly"
                      r"|potentially)\b[^.]{0,20}$")
# The first number starts a word: not "26" in "2026", not "ten" in "often".
TIMELINE = re.compile(r"(?<![\w-])" + NUMBER + r"(?:[- ]?(?:to|-|–)[- ]?" + NUMBER + r")?[- ](week|month)s?\b")
# "pearsall (knee)", "etienne jr. (hamstring)": how Sleeper names each player in a summary
MENTION = re.compile(r"([a-z'-]+)(?:,? (?:jr|sr|ii|iii|iv|v)\.?)? \([^)]*\)")
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
# A count of time already missed: "after a three-week absence", "after missing three weeks",
# "fourth game in the past five weeks", "fourth missed game in five weeks"
LOOKING_BACK = re.compile(r"(?:\b(?:after|following|since|missing|missed|sat out)\b(?:\W+\w+){0,2}\W+"
                          r"|\b(?:past|last|previous|prior|first)\s+|\bgames? in\s+)$")
AGO = re.compile(r"^\s+ago\b")
ABOUT_ABSENCE = re.compile(r"\b(?:out|miss|missing|absence|sideline[ds]?|timeline|return|recover|recovery|shelved"
                           r"|expected)\b")
SENTENCE = re.compile(r"(?<=[.!?])\s")


@dataclass(frozen=True)
class NewsEvidence:
    season_ending: bool
    weeks_left: float | None        # calendar weeks still out from now, per the median return date
    published: datetime | None      # newest headline used


def _number(text: str) -> float:
    return float(text) if text.isdigit() else float(WORDS[text])


def last_name(full_name: str) -> str:
    words = [w for w in full_name.lower().replace(".", "").split() if w not in SUFFIXES]
    return words[-1] if words else ""


def _about(prefix: str, surname: str | None) -> bool:
    """Is the sentence up to a match about this player? The nearest "Name (injury)" mention must be
    his, and a ", who" clause must hang off his name (not "backup Jones, who tore his ACL")."""
    if not surname:
        return True
    mentions = MENTION.findall(prefix)
    if mentions and mentions[-1] != surname:
        return False
    who = prefix.rfind(", who ")
    return who < 0 or bool(re.search(re.escape(surname) + r"(?:,? (?:jr|sr|ii|iii|iv|v)\.?)?(?: \([^)]*\))?$",
                                     prefix[:who]))


def _timeline(sentence: str, surname: str | None) -> list[float] | None:
    for match in islice(TIMELINE.finditer(sentence), MAX_CANDIDATES):
        before, after = sentence[:match.start()], sentence[match.end():]
        if LOOKING_BACK.search(before) or AGO.match(after) or not _about(before, surname):
            continue
        low = _number(match.group(1))
        high = _number(match.group(2)) if match.group(2) else low
        scale = WEEKS_PER_MONTH if match.group(3) == "month" else 1.0
        if 0 < low * scale <= MAX_WEEKS:
            return [round(low * scale, 1), round(min(high * scale, MAX_WEEKS), 1)]
    return None


def signal(item: Mapping, surname: str | None = None) -> dict | None:
    """{published, season_ending, weeks} for one headline about the player, or None when it says neither."""
    text = " ".join(t for t in (item.get("title"), item.get("summary")) if t).lower()[:MAX_TEXT]
    ending = False
    for m in SEASON_ENDING.finditer(text):
        sentence = SENTENCE.split(text[:m.start()])[-1]
        if not NEGATION.search(sentence) and _about(sentence, surname):
            ending = True
            break
    weeks = None
    for sentence in SENTENCE.split(text):
        if ABOUT_ABSENCE.search(sentence) and (weeks := _timeline(sentence, surname)):
            break
    if not ending and weeks is None:
        return None
    return {"published": item.get("published"), "season_ending": ending, "weeks": weeks}


def signals(items: Iterable[Mapping], surname: str | None = None) -> list[dict]:
    return [s for item in items if (s := signal(item, surname))]


def _published(s: Mapping) -> datetime | None:
    try:
        published = datetime.fromisoformat(s["published"])
    except (KeyError, TypeError, ValueError):
        return None
    return published if published.tzinfo else published.replace(tzinfo=timezone.utc)


def merge(old: Iterable[Mapping], new: Iterable[Mapping], now: datetime) -> list[dict]:
    """Stored signals plus new ones, de-duplicated, newest first, without ones older than KEEP_DAYS."""
    out: dict[str, dict] = {}
    for s in (*old, *new):
        published = _published(s)
        if published is not None and now - published <= timedelta(days=KEEP_DAYS):
            out[json.dumps(s, sort_keys=True)] = dict(s)
    return sorted(out.values(), key=lambda s: s["published"], reverse=True)


def evidence(stored: Iterable[Mapping], since: datetime | None, now: datetime) -> NewsEvidence | None:
    """Evidence published after `since` (his last kickoff): any season-ending headline wins; otherwise
    the median return date implied by the timelines. Later articles often restate the first
    diagnosis ("out 12 weeks" again a month later), so no single headline is trusted to restart it."""
    since = max(since, now - timedelta(days=KEEP_DAYS)) if since else now - timedelta(days=KEEP_DAYS)
    usable = [(p, s) for s in stored if (p := _published(s)) and since <= p <= now]
    usable.sort(key=lambda pair: pair[0], reverse=True)
    for published, s in usable:
        if s.get("season_ending"):
            return NewsEvidence(True, None, published)
    returns = sorted(published + timedelta(weeks=(s["weeks"][0] + s["weeks"][1]) / 2)
                     for published, s in usable if s.get("weeks"))
    if not returns:
        return None
    back = returns[(len(returns) - 1) // 2]                      # median (the earlier one on ties)
    weeks_left = max(0.0, (back - now).total_seconds() / timedelta(weeks=1).total_seconds())
    return NewsEvidence(False, round(weeks_left, 1), usable[0][0])


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
    """Re-read news for injured skill players whose Sleeper news timestamp changed.

    Each player is committed on his own, so one failure (network, an unreadable response, a parsing
    bug) is counted and skipped instead of dropping everyone's update.
    """
    rows = session.execute(
        select(PlayerStatus.player_id, PlayerStatus.injury_status, PlayerStatus.news_updated, Player.sleeper_player_id,
               Player.full_name)
        .join(Player, Player.id == PlayerStatus.player_id)
        .where(Player.position.in_(POSITIONS), Player.sleeper_player_id.is_not(None))).all()
    stored = {r.player_id: r for r in session.scalars(select(InjuryNews))}
    due = [r for r in rows if (r.injury_status or "").upper() in OUT_TAGS
           and (r.player_id not in stored or stored[r.player_id].news_updated != r.news_updated)]
    # Least recently checked first, so players whose news keeps failing can't starve the rest.
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    due.sort(key=lambda r: _aware(stored[r.player_id].checked_at) if r.player_id in stored else oldest)
    fetched = errors = failures_in_row = 0
    for row in due[:limit]:
        if failures_in_row >= MAX_FAILURES_IN_ROW:
            log.warning("Sleeper news looks down; stopped after %s failures in a row", failures_in_row)
            break
        try:
            items = fetch_news(row.sleeper_player_id, client, fresh=True)
            record = stored.get(row.player_id) or InjuryNews(player_id=row.player_id)
            now = utc_now()
            old = json.loads(record.signals or "[]") if record.signals else []
            found = merge(old if isinstance(old, list) else [], signals(items, last_name(row.full_name)), now)
            record.news_updated, record.signals, record.checked_at = row.news_updated, json.dumps(found), now
            session.add(record)
            session.commit()
            fetched += 1
            failures_in_row = 0
        except Exception as exc:  # noqa: BLE001 - one player's news must never stop the others
            session.rollback()
            errors += 1
            failures_in_row += 1
            log.warning("Sleeper news failed for player %s: %s", row.player_id, type(exc).__name__)
            _mark_checked(session, row.player_id)
    return {"due": len(due), "fetched": fetched, "errors": errors}


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _mark_checked(session: Session, player_id: int) -> None:
    """A failed player moves to the back of the queue (his news stamp stays old, so he's retried)."""
    try:
        record = session.get(InjuryNews, player_id) or InjuryNews(player_id=player_id, signals="[]")
        record.checked_at = utc_now()
        session.add(record)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        log.warning("Couldn't record the failed news check: %s", type(exc).__name__)
