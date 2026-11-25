"""Replay League chat about trades with the user: a CPU offer's one line (`label_offers`) and a CPU's reply when it
turns the user's proposal down (`decline_reply`). Re-exported by replay_chat; lines come from its bank.

- `label_offers`: every new CPU offer to the user gets his one-line reason (Offer.message), the first that fits:
  worried about a player he sends (one he is shopping, or one he feels worried about who nobody could think was
  hurt); a counter (a haggler answering the user declining his own opening offer: offer_haggle_counter, else
  offer_counter); a haggler's opening offer (offer_haggle: it says it is an opening offer); high on a player
  he asks for; buying low (offer_buy_low: a player he asks for is slumping, or out with a return date); short at
  a position (offer_need: he asks for a player at a position he needs, `replay_styles.position_needs`, the one
  definition the trade value uses too); else a plain value pitch.
- `decline_reply`: the line for `Verdict.decline` (replay_trades.DECLINE_KINDS), posted to the user and stored on
  the declined Offer (`Offer.reply`), so the trade history shows it. Neither shows in the league chat feed.
Feelings are read with the persona his prices follow now (`replay_personas.now`). Pure.
"""
from dataclasses import replace

from services import replay_fans, replay_mood, replay_personas, replay_state as rs, replay_styles
from services.replay_chat import post
from services.replay_mood import might_be_hurt
from services.replay_pool import SeasonData
from services.replay_state import Offer, Settings, State
from services.replay_trades import Verdict
from services.replay_values import Knowledge, ReplayValue


def _best(know: Knowledge, player_ids: tuple[int, ...]) -> int:
    return max(player_ids, key=lambda pid: (know.by_id[pid].chart, -pid))


def buying_low(value: ReplayValue) -> bool:
    """Slumping (form below his rate and a last game short of it) or out with a return date."""
    slumping = value.form_chart < 0 and bool(value.recent_points) and value.recent_points[0] < value.ros_ppg
    return slumping or replay_personas.stashable(value)


def _offer_kind(state: State, settings: Settings, know: Knowledge, played: SeasonData,
                offer: Offer) -> tuple[str, int | None]:
    """Why this CPU (offer.from_team) makes the offer: (kind, the player the line names). A shopped player was
    checked for `might_be_hurt` when his shop opened or renewed this week; any other worry is checked here."""
    cpu = rs.team(state, offer.from_team)
    persona = replay_personas.now(state, cpu)
    shopped = rs.markdowns(state, cpu.id)
    worried = [pid for pid in offer.give if pid in shopped or (
        (felt := replay_mood.feeling(know.by_id[pid], persona, cpu.hype)) and felt.kind == "worried"
        and not might_be_hurt(know, played, pid, state.week - 1))]
    if worried:     # the player he is shopping first, so the offer names the one his shop message did
        return "offer_worried", min(worried, key=lambda pid: pid not in shopped)
    if offer.counter_to is not None:
        answered = next((o for o in state.offers if o.id == offer.counter_to), None)
        haggled = cpu.persona == "haggler" and answered is not None and answered.from_team == cpu.id
        return ("offer_haggle_counter", _best(know, offer.get)) if haggled else ("offer_counter", None)
    if cpu.persona == "haggler":
        return "offer_haggle", _best(know, offer.get)
    loved = [pid for pid in offer.get if (felt := replay_mood.feeling(know.by_id[pid], persona, cpu.hype))
             and felt.kind == "high"]
    if loved:
        return "offer_high", loved[0]
    cheap = [pid for pid in offer.get if buying_low(know.by_id[pid])]
    if cheap:
        return "offer_buy_low", _best(know, tuple(cheap))
    needs = replay_styles.position_needs(state, settings, know)[cpu.id][0]
    wanted = [pid for pid in offer.get if know.by_id[pid].position in needs]
    if wanted:
        return "offer_need", _best(know, tuple(wanted))
    return "offer_value", _best(know, offer.get)


def label_offers(state: State, know: Knowledge, seed: int, played: SeasonData, settings: Settings) -> State:
    """Every pending CPU offer to the user without a message gets one in its sender's voice (not in the feed).
    `played` is the season as known now (data.as_of(state.week)), for `might_be_hurt` on last week's game."""
    user = rs.user_team(state)
    for offer in state.offers:
        if offer.to_team != user.id or offer.status != "pending" or offer.message:
            continue
        cpu = rs.team(state, offer.from_team)
        kind, pid = _offer_kind(state, settings, know, played, offer)
        named = know.by_id[pid] if pid is not None else None
        values = {"me": cpu.name, "you": user.name, "player": named.name if named else "",
                  "position": named.position if named else ""}
        posted = post(state, cpu, kind, values, seed, to_team=user.id, player_ids=() if pid is None else (pid,))
        text = posted.chat[-1].text
        # the line is used up in the bucket (kept in chat history as kind offer_*) so offers don't repeat either
        state = replace(posted, offers=tuple(replace(o, message=text) if o.id == offer.id else o for o in posted.offers))
    return state


def decline_reply(state: State, know: Knowledge, seed: int, offer_id: int, verdict: Verdict) -> State:
    """The state with the CPU's reply to the user's declined proposal `offer_id` posted to the user and stored on
    that Offer (`Offer.reply`); unchanged when the verdict has no CPU reason (`Verdict.decline`) or the offer
    already has a reply."""
    offer = next((o for o in state.offers if o.id == offer_id), None)
    if offer is None or offer.reply is not None or verdict.accepted or verdict.decline is None:
        return state
    cpu, user = rs.team(state, offer.to_team), rs.user_team(state)
    pid = verdict.decline_player
    values = {"me": cpu.name, "you": user.name, "player": know.by_id[pid].name if pid is not None else "",
              "position": verdict.decline_position or "",
              "team": replay_fans.nickname(replay_fans.rival(cpu))}
    posted = post(state, cpu, verdict.decline, values, seed, to_team=user.id, player_ids=() if pid is None else (pid,))
    text = posted.chat[-1].text
    return replace(posted, offers=tuple(replace(o, reply=text) if o.id == offer_id else o for o in posted.offers))
