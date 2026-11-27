"""Fit sim_injuries.json from the replay data, and check the fit by simulating seasons through sim_injuries.

    python -m services.sim_injuries --fit --seasons 2018-2025 [--check]

Who counts: each season's top TOP[position] players by preseason ADP (the fantasy-relevant ones). For each,
his team's games are walked in order (byes skipped):
- exposure: a game he played that has a later team game (an injury after the last game is never seen);
- absence: he played a game and missed the next with an injury tag (Q/D/O/IR/PUP, not a suspension or a
  non-injury note); its length is the tagged games he missed in a row. One that ends with a missed game
  carrying no injury tag is right-censored (real tags hold to the return: IR and PUP come from the weekly
  roster every week, over byes too, so an untagged miss is a gap, a cut or a scratch, and in 2018-2025 he
  missed more games after it in nearly every case). One that runs out the season goes on into the next: the
  games his team played before his first game there are added (still out, or retired, is right-censored).
  Lengths are a Kaplan-Meier fit, so censored ones count as "at least";
- banged up: he played, his snaps were under EXIT_CUTOFF of his season median, and he was listed Q/D for the
  next game and played it;
- exit share: that game's snaps over his season median (1.0 when he did not leave early);
- phantom: a Q/D tag on a game he played that is not part of an injury above;
- preseason: missing week 1 with an injury tag, last season's carried-over absences excluded (rate by
  position, length pooled: there are only a few dozen);
- the per-game hazard (absences plus banged-up games over exposure) on seasons since HAZARD_SINCE only: the
  same count gives quarterbacks 0.023-0.026 new injuries a game in 2018-2020 and 0.043-0.067 in 2021-2025
  (sim_metrics.injuries; running backs, receivers and tight ends show no such step), so a pooled 2018-2025
  hazard (QB 0.050) left simulated QBs below the target sim_eval holds them to (0.75 x the 2021-2025 rate);
  lengths, exits and the rest still use every season;
- IR policy (seasons since IR_RULES_SINCE, today's rules): the absence length from which IR is the usual
  outcome (fewest misclassified absences), the median missed game it starts at and its usual stint (the rule's
  minimum).
"""
import argparse
import json
import logging
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np

from services import sim_injuries as si
from services.replay_injuries import NOT_INJURY
from services.replay_pool import SeasonData

log = logging.getLogger(__name__)

TOP = {"QB": 32, "RB": 48, "WR": 60, "TE": 24, "K": 20}
EXIT_CUTOFF = 0.70
HAZARD_SINCE = 2021               # per-game hazard: 2021 on (see `recent_hazards`); lengths use every season
IR_RULES_SINCE = 2022             # IR minimum of four games, two-game designations (2020-21 were COVID years)
MIN_SPELLS = 60                   # below this a position borrows the pooled length fit
EXIT_LEVELS = 11
NOT_BODY = ("illness", "covid", "rest", "personal", "coach", "not injury", "suspension", "reserve")
SIDES = ("left ", "right ")
SIM_POOL = {"QB": 44, "RB": 65, "WR": 82, "TE": 32, "K": 27}     # 250 players, the relevant mix
SIM_GAMES = 17
HIST_EDGES = (1, 2, 3, 4, 6, 9)  # 1, 2, 3, 4-5, 6-8, 9+


@dataclass
class Spell:
    position: str
    length: int                   # games missed in this season (extended across the offseason later)
    censored: bool                # ran to the season's end, or an untagged miss ended it unseen
    tags: list[str]
    exit_share: float | None = None
    q_back: bool | None = None
    back_practice: str | None = None
    body: str | None = None
    preseason: bool = False
    pid: int = 0
    in_season: int = 0            # games missed in the season it started
    season_end: bool = False      # ran to the season's end (carried into the next by `extend`)


@dataclass
class Tally:
    exposure: Counter = field(default_factory=Counter)
    banged: Counter = field(default_factory=Counter)
    q_tags: Counter = field(default_factory=Counter)
    q_played: Counter = field(default_factory=Counter)
    played: Counter = field(default_factory=Counter)
    phantoms: dict = field(default_factory=lambda: defaultdict(list))   # position -> [(tag, practice, body)]
    bodies: dict = field(default_factory=lambda: defaultdict(Counter))
    banged_exits: dict = field(default_factory=lambda: defaultdict(list))
    players: Counter = field(default_factory=Counter)                   # relevant player-seasons
    fresh_players: Counter = field(default_factory=Counter)             # ... in seasons with a season before
    spells: list = field(default_factory=list)


def body_part(text: str | None) -> str | None:
    """'right Shoulder' -> 'Shoulder'; None for a note that names no body part."""
    text = (text or "").strip()
    if not text or text.lower().startswith(NOT_INJURY) or any(w in text.lower() for w in NOT_BODY):
        return None
    for side in SIDES:
        if text.lower().startswith(side):
            text = text[len(side):]
    text = text.split(",")[0].split("/")[0].strip()
    return (text[:1].upper() + text[1:])[:si.MAX_BODY] or None


def relevant(data: SeasonData) -> dict[int, str]:
    out = {}
    for position, top in TOP.items():
        ranked = sorted((p for p in data.players.values() if p.position == position and p.adp is not None),
                        key=lambda p: p.adp)
        out.update({p.player_id: position for p in ranked[:top]})
    return out


def _hurt(injury) -> bool:
    return (injury is not None and injury.tag not in ("SUS", "EXE")
            and not (injury.injury or "").lower().startswith(NOT_INJURY))


def _team_games(data: SeasonData, pid: int) -> list[int]:
    return [w for w in range(1, data.last_week + 1) if data.schedule.opponent(data.team_at(pid, w), w)]


def _snap_shares(data: SeasonData, pid: int, weeks: list[int]) -> list[float | None]:
    """Each game's snap share against his median (capped at 1); None where unknown."""
    snaps = [(data.usage.get((pid, w)).snap_pct if (pid, w) in data.usage else None) for w in weeks]
    known = [s for s in snaps if s]
    normal = float(np.median(known)) if known else None
    return [min(1.0, s / normal) if normal and s is not None else None for s in snaps]


def _count_tags(tally: Tally, position: str, tags: list, played: list[bool], claimed: set[int]) -> None:
    """Q tags and whether he played through them, games played, and phantoms: a Q/D tag on a game he played that
    no injury claimed."""
    for i, injury in enumerate(tags):
        if injury and injury.tag == "Q":
            tally.q_tags[position] += 1
            tally.q_played[position] += played[i]
        if played[i]:
            tally.played[position] += 1
            if i not in claimed and injury and injury.tag in ("Q", "D") and _hurt(injury):
                tally.phantoms[position].append((injury.tag, injury.practice or "DNP", body_part(injury.injury)))


def walk(data: SeasonData, pid: int, position: str, tally: Tally, fresh: bool) -> tuple[list[int], list]:
    """Adds one player-season to the tally; returns (his team's game weeks, which of them he played)."""
    weeks = _team_games(data, pid)
    played = [(pid, w) in data.actual for w in weeks]
    tags = [data.injuries.get((pid, w)) for w in weeks]
    shares = _snap_shares(data, pid, weeks)
    tally.players[position] += 1
    tally.fresh_players[position] += fresh
    claimed: set[int] = set()                         # tagged games that belong to an injury (not phantoms)

    def run(start: int) -> tuple[int, bool]:
        j = start
        while j < len(weeks) and not played[j] and _hurt(tags[j]):
            j += 1
        return j - start, j == len(weeks)

    def add(start: int, exit_share: float | None, preseason: bool) -> None:
        length, season_end = run(start)
        censored = season_end or not played[start + length]          # ended by a missed game with no injury tag
        end = start + length
        back = end < len(weeks) and played[end]
        texts = [t.injury for t in tags[start:end] if t] + ([tags[end].injury] if back and tags[end] else [])
        body = next((b for b in map(body_part, texts) if b), None)
        q_back = bool(back and tags[end] and tags[end].tag in ("Q", "D")) if back else None
        if q_back:
            claimed.add(end)
        tally.spells.append(Spell(position, length, censored, [t.tag for t in tags[start:end]], exit_share, q_back,
                                  tags[end].practice if q_back else None, body, preseason, pid, length, season_end))
        if body:
            tally.bodies[position][body] += 1

    if weeks and not played[0] and _hurt(tags[0]) and fresh:
        add(0, None, True)
    for i, (now, after) in enumerate(zip(played, played[1:])):
        if not now:
            continue
        tally.exposure[position] += 1
        if not after and _hurt(tags[i + 1]):
            add(i + 1, shares[i], False)
        elif after and tags[i + 1] and tags[i + 1].tag in ("Q", "D") and _hurt(tags[i + 1]) \
                and (shares[i] or 1.0) < EXIT_CUTOFF:
            tally.banged[position] += 1
            tally.banged_exits[position].append(shares[i])
            claimed.add(i + 1)
            if body_part(tags[i + 1].injury):
                tally.bodies[position][body_part(tags[i + 1].injury)] += 1
    _count_tags(tally, position, tags, played, claimed)
    return weeks, played


def kaplan_meier(lengths: Iterable[tuple[int, bool]]) -> list[list[float]]:
    """[[value, probability]] from (length, censored) pairs; the mass left after the last event goes to the
    longest length seen (no invented value)."""
    pairs = list(lengths)
    if not pairs:
        return [[0, 1.0]]
    survive, pmf = 1.0, []
    for value in sorted({n for n, censored in pairs if not censored}):
        at_risk = sum(n >= value for n, _ in pairs)
        events = sum(n == value and not censored for n, censored in pairs)
        mass = survive * events / at_risk
        survive -= mass
        pmf.append([value, mass])
    longest = max(n for n, _ in pairs)
    if survive > 1e-9:
        pmf = [p for p in pmf if p[0] != longest] + [[longest, survive + sum(p[1] for p in pmf if p[0] == longest)]]
    return [[int(v), round(p, 6)] for v, p in pmf if p > 0]


def _bucket(n: int, edges: tuple[int, ...]) -> int:
    return max(e for e in edges if e <= max(edges[0], n))


def _ir_policy(spells: list[Spell]) -> dict:
    """The IR rule from absences under today's rules (Spell.tags of the season it started)."""
    rows = [(s.length, "IR" in s.tags or "PUP" in s.tags,
             next((i for i, t in enumerate(s.tags) if t in ("IR", "PUP")), None), s.censored) for s in spells]
    best = min(range(1, 19), key=lambda t: sum((n >= t) != ir for n, ir, _, _ in rows))
    starts = [start for n, ir, start, _ in rows if ir and n >= best]
    stints = Counter(n - start for n, ir, start, censored in rows if ir and not censored and n >= best)
    return {"min_games": best, "from": int(np.median(starts)) if starts else 0,
            "min_stint": stints.most_common(1)[0][0] if stints else best,      # the usual (rule) stint
            "ir_share": {str(n): round(float(np.mean([ir for m, ir, _, _ in rows if _bucket(m, HIST_EDGES) == n])), 3)
                         for n in HIST_EDGES if any(_bucket(m, HIST_EDGES) == n for m, *_ in rows)}}


def _mode(values: Iterable[str | None], fallback: str) -> str:
    found = Counter(v for v in values if v)
    return found.most_common(1)[0][0] if found else fallback


def _remaining(spells: list[Spell]) -> dict[str, list]:
    """{"group|weeks out bucket": [(games still to miss, censored)]} at each game missed so far, by IR/O and pooled."""
    remaining: dict[str, list] = defaultdict(list)
    for s in spells:
        for m in range(1, s.in_season + 1):
            group = "IR" if s.tags[m - 1] in ("IR", "PUP") else "O"
            bucket = _bucket(m, si.WEEKS_OUT_EDGES)
            remaining[f"{group}|{bucket}"].append((s.length - m, s.censored))
            remaining[f"{si.POOLED}|{bucket}"].append((s.length - m, s.censored))
    return remaining


def summarize(tally: Tally, recent: list[Spell], o_practices: list[str | None]) -> dict:
    """The JSON model from the tally (`recent`: spells since IR_RULES_SINCE, for the IR policy)."""
    spells = tally.spells
    positions, pooled_len = {}, kaplan_meier((s.length, s.censored) for s in spells if not s.preseason)
    all_bodies = sum(tally.bodies.values(), Counter())
    pooled_pre = kaplan_meier((s.length, s.censored) for s in spells if s.preseason)    # too few per position
    for position in TOP:
        mine = [s for s in spells if s.position == position and not s.preseason]
        pre = [s for s in spells if s.position == position and s.preseason]
        exits = [s.exit_share for s in mine if s.exit_share is not None]
        exit_shares = [x for x in exits if x < EXIT_CUTOFF] + [x for x in tally.banged_exits[position] if x]
        banged, starts = tally.banged[position], len(mine) + tally.banged[position]
        phantoms = tally.phantoms[position]
        practice = defaultdict(Counter)
        for label, prac, _ in phantoms:
            practice[label][prac] += 1
        bodies = tally.bodies[position] if sum(tally.bodies[position].values()) >= MIN_SPELLS else all_bodies
        returns = [s.q_back for s in mine if s.q_back is not None]
        positions[position] = {
            "player_seasons": tally.players[position], "games": tally.exposure[position], "absences": len(mine),
            "banged": banged, "hazard": round(starts / max(1, tally.exposure[position]), 5),
            "banged_share": round(banged / max(1, starts), 4),
            "length_pmf": kaplan_meier((s.length, s.censored) for s in mine) if len(mine) >= MIN_SPELLS else pooled_len,
            "exit_rate": round(len([x for x in exits if x < EXIT_CUTOFF]) / max(1, len(exits)), 4),
            "exit_shares": [round(float(q), 3) for q in np.quantile(exit_shares, np.linspace(0, 1, EXIT_LEVELS))]
            if exit_shares else [1.0] * EXIT_LEVELS,
            "q_back": round(float(np.mean(returns)), 4) if returns else 0.0,
            "q_play": round(tally.q_played[position] / max(1, tally.q_tags[position]), 4),
            "phantom_rate": round(len(phantoms) / max(1, tally.played[position]), 5),
            "phantom_tags": dict(Counter(label for label, _, _ in phantoms)) or {"Q": 1},
            "phantom_practice": {label: dict(c) for label, c in practice.items()} or {"Q": {"LP": 1}},
            "body": [[b, n] for b, n in bodies.most_common() if n >= 2],
            "preseason_rate": round(len(pre) / max(1, tally.fresh_players[position]), 5),
            "preseason_pmf": pooled_pre,
            "preseason_n": len(pre)}
    for fit in positions.values():                   # every tag drawn must have a practice mix
        fit["phantom_practice"] = {label: fit["phantom_practice"].get(label, {"LP": 1}) for label in fit["phantom_tags"]}
    remaining = _remaining(spells)
    ir = _ir_policy(recent)
    ir["o_practice"] = _mode(o_practices, "DNP")
    ir["back_practice"] = _mode((s.back_practice for s in spells), "LP")
    return {"positions": positions, "ir": ir,
            "remaining": {key: kaplan_meier(pairs) for key, pairs in sorted(remaining.items()) if len(pairs) >= 20},
            "remaining_n": {key: len(pairs) for key, pairs in sorted(remaining.items())},
            "real": real_summary(tally)}


def real_summary(tally: Tally) -> dict:
    """What really happened, in the units `check` reports: in-season games missed per player-season and the
    in-season absence-length histogram (censored at the season's end, as a simulated season is)."""
    missed = defaultdict(int)
    hist: dict[str, Counter] = defaultdict(Counter)
    for s in tally.spells:
        missed[s.position] += s.in_season
        if not s.preseason:
            hist[s.position][_bucket(s.in_season, HIST_EDGES)] += 1
    return {"missed_per_player": {p: round(missed[p] / max(1, tally.players[p]), 3) for p in TOP},
            "length_hist": {p: {str(k): round(hist[p][k] / max(1, sum(hist[p].values())), 3) for k in HIST_EDGES}
                            for p in TOP}}


def extend(spells: list[Spell], nxt: dict[int, tuple[list[int], list[bool], list]]) -> None:
    """Carry a season-ending absence into the next season (its team games before his first game there)."""
    for s in spells:
        if not s.season_end or s.pid not in nxt:
            continue
        _, played, tags = nxt[s.pid]
        if any(played):
            s.length += played.index(True)
            s.censored = s.season_end = False
        elif played and all(_hurt(t) for t in tags):
            s.length += len(played)


def fit(session, seasons: list[int]) -> dict:
    from services.replay_pool import load_season_data

    tally, by_season, o_practices = Tally(), {}, []
    recent: dict[str, Counter] = {"games": Counter(), "starts": Counter()}
    for season in seasons:
        data = load_season_data(session, season, "half")
        first = len(tally.spells)
        exposure, banged = Counter(tally.exposure), Counter(tally.banged)
        seen = {}
        prior = by_season.get(season - 1, {})
        for pid, position in relevant(data).items():
            fresh = season - 1 in by_season and not any(s.pid == pid and s.season_end for s in prior.get("spells", []))
            weeks, played = walk(data, pid, position, tally, fresh)
            seen[pid] = (weeks, played, [data.injuries.get((pid, w)) for w in weeks])
        for pid in {pid for pid, _ in data.actual} | {pid for pid, _ in data.injuries}:
            if pid not in seen and pid in data.players:
                weeks = _team_games(data, pid)
                seen[pid] = (weeks, [(pid, w) in data.actual for w in weeks], [data.injuries.get((pid, w)) for w in weeks])
        o_practices += [i.practice for (_, w), i in data.injuries.items() if i.tag == "O"]
        mine = tally.spells[first:]
        if season >= HAZARD_SINCE:
            recent["games"] += tally.exposure - exposure
            recent["starts"] += (tally.banged - banged) + Counter(s.position for s in mine if not s.preseason)
        if season - 1 in by_season:
            extend(by_season[season - 1]["spells"], seen)
        by_season[season] = {"spells": mine}
        log.info("%s: %d relevant players, %d spells", season, len(relevant(data)), len(mine))
    ir_spells = [s for season, row in by_season.items() if season >= IR_RULES_SINCE for s in row["spells"]]
    model = summarize(tally, ir_spells, o_practices)
    for position, fit in model["positions"].items():         # the per-game hazard on the recent seasons only
        if recent["games"][position]:
            fit["hazard"] = round(recent["starts"][position] / recent["games"][position], 5)
    model["hazard_seasons"] = f"{max(HAZARD_SINCE, seasons[0])}-{seasons[-1]}"
    model["seasons"] = f"{seasons[0]}-{seasons[-1]}"
    model["top"] = TOP
    return model


def check(seasons: int = 1000, seed: int = 0) -> dict:
    """Simulate `seasons` seasons of a SIM_POOL of players through sim_injuries; same units as real_summary."""
    dice = np.random.default_rng(seed)
    missed, hist = Counter(), defaultdict(Counter)
    for position, count in SIM_POOL.items():
        hz = si.hazard(position)
        for _ in range(seasons * count):
            out = 0
            pre = si.preseason(dice, position)
            if pre is not None:
                out = min(pre.games_out, SIM_GAMES)
                missed[position] += out
            for game in range(out, SIM_GAMES - 1):
                if game < out:
                    continue
                if dice.random() < hz:
                    spell = si.draw(dice, position)
                    if spell.games_out:
                        n = min(spell.games_out, SIM_GAMES - 1 - game)
                        missed[position] += n
                        hist[position][_bucket(n, HIST_EDGES)] += 1
                        out = game + 1 + n
    return {"missed_per_player": {p: round(missed[p] / (seasons * n), 3) for p, n in SIM_POOL.items()},
            "length_hist": {p: {str(k): round(hist[p][k] / max(1, sum(hist[p].values())), 3) for k in HIST_EDGES}
                            for p in SIM_POOL}}


def main(argv: list[str] | None = None) -> None:
    from services.replay_data import parse_seasons     # the CLI's only use of the importer

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fit", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--seasons", default="2018-2025")
    args = parser.parse_args(argv)
    if args.fit:
        from database.db import SessionLocal

        with SessionLocal() as session:
            model = fit(session, parse_seasons(args.seasons))
        si.PATH.write_text(json.dumps(model, indent=1), encoding="utf-8")
        si.stored.cache_clear()
        log.info("Wrote %s", si.PATH)
    if args.check:
        sim, real = check(), si.stored()["real"]
        for position in TOP:
            log.info("%s missed/player-season real %.2f target %.2f sim %.2f | lengths real %s sim %s", position,
                     real["missed_per_player"][position], real["missed_per_player"][position] * si.INJURY_SCALE,
                     sim["missed_per_player"][position],
                     real["length_hist"][position], sim["length_hist"][position])
