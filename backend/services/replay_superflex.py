"""Replay League superflex values: QBs placed among the non-QBs as the real 2-QB ADP list places them, everyone
else exactly as in a regular league of the same scoring.

- `one_qb(data)`: the season on the league-scoring (1-QB) ADP list. A superflex league's SeasonData carries the
  2-QB list in `players` and the league-scoring list in `scoring_adp` (replay_pool); this puts every player on
  `scoring_adp`. A season saved before `scoring_adp` existed (an old simulated league, or a simulated market
  that priced only a 2-QB board) converts its non-QBs: the k-th non-QB of the 2-QB list (its order kept) goes
  at pick k + the QBs ahead of the k-th non-QB in an average 1-QB list (`fitted()[scoring]`). Data on a 1-QB
  list is returned as is.
- `view(data)`: `one_qb(data)` with each QB moved: count the non-QBs ahead of QB n in the 2-QB list
  (fractional: k ahead, and how far he went between the k-th and (k+1)-th non-QB by ADP), and give him the
  pick where that (fractional) non-QB slot went on the league-scoring list, so his chart is worth what that
  non-QB's is (replay_chart.slot_curve -> pick_value). A season without a 2-QB list uses `fitted()["2qb"]`, the
  average slot of QB n over the seasons that have both lists, replacing the old flat QB ADP x 0.4. The CPU draft,
  pre_rank and the chart all read it, so QBs go where real superflex drafts take them and RBs, WRs and TEs
  keep their regular-league values and order.

`fitted()` is measured by `python -m services.replay_superflex --fit` from every stored season with both lists
(replay_superflex.json). Pure apart from that CLI: no database.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import json
import logging
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from functools import lru_cache
from types import MappingProxyType

from services.replay_adp_format import PRESETS, TWO_QB, scoring_format
from services.replay_pool import PoolPlayer, SeasonData

log = logging.getLogger(__name__)

FIT_PATH = Path(__file__).with_name("replay_superflex.json")


def qb_slots(board: Iterable[tuple[str, float]]) -> tuple[float, ...]:
    """For (position, ADP) pairs: the fractional non-QB slot of each QB, best QB first. A QB with k non-QBs
    ahead sits at k + how far his ADP went from the k-th non-QB's toward the (k+1)-th's (0 before the first)."""
    ordered = sorted(board, key=lambda pair: pair[1])
    others = [adp for position, adp in ordered if position != "QB"]
    slots = []
    for position, adp in ordered:
        if position != "QB":
            continue
        k = bisect_left(others, adp)
        low = others[k - 1] if k else 0.0
        high = others[k] if k < len(others) else low + 1.0
        slots.append(k + (adp - low) / (high - low) if high > low else float(k))
    return tuple(slots)


@lru_cache(maxsize=1)
def fitted() -> Mapping[str, tuple[float, ...]]:
    """{list: the average non-QB slot of QB n} for "2qb" and each 1-QB preset (replay_superflex.json)."""
    stored = json.loads(FIT_PATH.read_text(encoding="utf-8"))
    return MappingProxyType({name: tuple(stored[name]) for name in (TWO_QB, *PRESETS)})


def _board(data: SeasonData, adp: Mapping[int, float]) -> list[tuple[str, float]]:
    return [(data.players[pid].position, value) for pid, value in adp.items()]


def _extend(slots: Sequence[float], own: Sequence[float], count: int) -> list[float]:
    """`slots` for `count` QBs: past its end, QB n moves on from the last as `own` (his list's slots) does."""
    found = list(slots[:count])
    while len(found) < count:
        n = len(found)
        step = own[n] - own[n - 1] if 0 < n < len(own) else 1.0
        found.append((found[-1] if found else 0.0) + step)
    return found


def _converted(data: SeasonData) -> dict[int, float]:
    """{non-QB: ADP on an average 1-QB list} for a season that carries only the 2-QB list."""
    one_qb = fitted()[scoring_format(data.scoring, data.rules)]
    others = sorted((p.adp, pid) for pid, p in data.players.items() if p.position != "QB" and p.adp is not None)
    return {pid: float(k + bisect_left(one_qb, k)) for k, (_, pid) in enumerate(others, start=1)}


def _with_adp(data: SeasonData, adp: Mapping[int, float | None]) -> SeasonData:
    def moved(p: PoolPlayer) -> PoolPlayer:
        new = adp.get(p.player_id)
        if new == p.adp:
            return p
        stdev = p.adp_stdev * new / p.adp if p.adp_stdev is not None and p.adp and new is not None else None
        return replace(p, adp=new, adp_stdev=stdev if new is not None else None)
    return replace(data, players=MappingProxyType({pid: moved(p) for pid, p in data.players.items()}))


@lru_cache(maxsize=64)
def one_qb(data: SeasonData) -> SeasonData:
    """The season on the league-scoring 1-QB list (see the module docstring); cached per season object."""
    if data.adp_format != TWO_QB:
        return data
    converted = _converted(data)                    # non-QBs; a QB keeps his: view() moves him
    if data.scoring_adp:                            # a non-QB the 1-QB list lacks: his converted pick
        return _with_adp(data, {pid: data.scoring_adp.get(pid, converted.get(pid)) for pid in data.players})
    qbs = {pid: p.adp for pid, p in data.players.items() if p.position == "QB"}
    return _with_adp(data, {**qbs, **converted})


def pick_of_slot(picks: Sequence[float], slot: float) -> float:
    """The pick of a fractional non-QB slot: between the k-th and (k+1)-th non-QB's picks (pick 1 before the
    first; one pick per slot past the last)."""
    k = int(slot)
    low = picks[k - 1] if 0 < k <= len(picks) else 1.0
    if k >= len(picks):
        return (picks[-1] if picks else 1.0) + (slot - len(picks))
    return low + (slot - k) * (picks[k] - low)


@lru_cache(maxsize=64)
def view(data: SeasonData) -> SeasonData:
    """The season as a superflex league values it (module docstring); cached, so curves built on it are too."""
    regular = one_qb(data)
    others = sorted(p.adp for p in regular.players.values() if p.position != "QB" and p.adp is not None)
    qbs = sorted((p.adp, pid) for pid, p in data.players.items() if p.position == "QB" and p.adp is not None)
    own = qb_slots((p.position, p.adp) for p in data.players.values() if p.adp is not None)
    slots = own if data.adp_format == TWO_QB else _extend(fitted()[TWO_QB], own, len(qbs))
    moved = {pid: pick_of_slot(others, slot) for (_, pid), slot in zip(qbs, slots)}
    return _with_adp(regular, {**{pid: p.adp for pid, p in regular.players.items()}, **moved})


def borrowed_qb_adp(adp: float, listed: str = "ppr") -> float:
    """A QB's ADP on a 1-QB list (`listed`: ppr, half or std) moved to where the average 2-QB list takes the same
    QB (replay_data: the 2-QB list borrowing a QB it leaves out); never before pick 1. QB n goes at about pick
    slot + n on either list."""
    pairs = list(zip(fitted()[listed], fitted()[TWO_QB]))          # the QBs both curves list
    one = [1.0, *(a + n for n, (a, _) in enumerate(pairs, start=1))]
    two = [1.0, *(b + n for n, (_, b) in enumerate(pairs, start=1))]
    if adp >= one[-1]:
        return two[-1] + adp - one[-1]
    k = max(1, bisect_left(one, adp))
    return max(1.0, two[k - 1] + (adp - one[k - 1]) / (one[k] - one[k - 1]) * (two[k] - two[k - 1]))


def as_two_qb(listed: str, pairs: Sequence[tuple]) -> list[tuple]:
    """A 1-QB list's (AdpEntry, player id) pairs lent to the 2-QB list: each QB at `borrowed_qb_adp`, his spread
    moved in proportion; everyone else as is."""
    def moved(entry):
        if entry.position != "QB" or entry.adp <= 0:
            return entry
        adp = borrowed_qb_adp(entry.adp, listed)
        return replace(entry, adp=adp, stdev=entry.stdev * adp / entry.adp if entry.stdev is not None else None)
    return [(moved(entry), player_id) for entry, player_id in pairs]


def clear_cache() -> None:
    one_qb.cache_clear()
    view.cache_clear()


# ---------------- fit ----------------

def fit(session, seasons: Sequence[int] | None = None) -> dict:
    """The average QB slots per list over the stored seasons that have the 2-QB list and that 1-QB list
    (every QB n that all of them list), read from FFC's own rows only: a borrowed row was placed by this fit
    (`as_two_qb`) and MFL's tail is not on FFC's scale. ValueError when a list is stored for no season:
    `fitted()` needs all four."""
    from sqlalchemy import select

    from database.models import Player, ReplayAdp

    rows = session.execute(select(ReplayAdp.season, ReplayAdp.format, ReplayAdp.adp, Player.position)
                           .join(Player, Player.id == ReplayAdp.player_id).where(ReplayAdp.source == "ffc"))
    boards: dict[tuple[int, str], list[tuple[str, float]]] = {}
    for season, fmt, adp, position in rows:
        if seasons is None or season in seasons:
            boards.setdefault((season, fmt), []).append((position, adp))
    both = sorted({s for s, f in boards if f == TWO_QB and any((s, p) in boards for p in PRESETS)})
    out: dict = {"seasons": both}
    for fmt in (TWO_QB, *PRESETS):
        curves = [qb_slots(boards[(s, fmt)]) for s in both if (s, fmt) in boards]
        if not curves:
            raise ValueError(f"No season stores both the 2-QB list and the {fmt} list: nothing written.")
        length = min(len(c) for c in curves)
        out[fmt] = [round(sum(c[n] for c in curves) / len(curves), 2) for n in range(length)]
    return out


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fit", action="store_true", help="measure replay_superflex.json from the stored lists")
    args = parser.parse_args(argv)
    if not args.fit:
        log.info("%s", json.dumps(dict(fitted()), indent=1))
        return
    from database.db import SessionLocal
    with SessionLocal() as session:
        try:
            found = fit(session)
        except ValueError as exc:
            log.error("%s", exc)
            raise SystemExit(1) from exc
    FIT_PATH.write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8")
    fitted.cache_clear()
    log.info("Wrote %s from seasons %s", FIT_PATH.name, found["seasons"])


if __name__ == "__main__":
    main()
