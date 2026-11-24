"""Simulated seasons: the fitted constants files (sim_params.json, sim_injuries.json, sim_next_year.json).

`params()` is sim_params.json as a read-only view (re-exported by sim_world, where the engine imports it).
"""
import json
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

PARAMS_PATH = Path(__file__).with_name("sim_params.json")
PARAMS_FIT = "python -m services.sim_eval --fit"
PARAMS_SECTIONS = ("positions", "league", "market", "adp_stdev", "kdst_adp", "adp_curve", "kick", "dst", "game", "rates")
_overrides: dict[str, dict] = {}         # sim_eval --set, for this process only


def fitted_json(path: Path, command: str, sections: Iterable[str]) -> dict:
    """A fitted constants file, or one clear error naming it and the command that writes it."""
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{path.name} is missing or unreadable ({type(exc).__name__}): run {command}") from exc
    return require(stored, sections, path, command)


def require(found: Mapping, sections: Iterable[str], path: Path, command: str):
    """`found`, or one clear error naming the sections `path` lacks and the command that writes them."""
    missing = [name for name in sections if name not in found]
    if missing:
        raise RuntimeError(f"{path.name} has no {', '.join(missing)}: run {command}")
    return found


@lru_cache(maxsize=1)
def params() -> Mapping:
    """The constants in sim_params.json, read-only: the measured sections, each overlaid with its `tuned` values
    (constants set by matching simulated seasons to real ones, `sim_eval --set ... --save-tuned`) and then any
    `override` of this process."""
    stored = fitted_json(PARAMS_PATH, PARAMS_FIT, ())
    layers = (stored.pop("tuned", {}), _overrides)
    names = {**{name: {} for layer in layers for name in layer}, **stored}
    return require(MappingProxyType({
        name: MappingProxyType({**value, **layers[0].get(name, {}), **layers[1].get(name, {})})
        if isinstance(value, dict) else value for name, value in names.items()}), PARAMS_SECTIONS, PARAMS_PATH, PARAMS_FIT)


def override(section: str, key: str, value) -> None:
    """Set one constant for the rest of this process (sim_eval --set)."""
    _overrides.setdefault(section, {})[key] = value
    params.cache_clear()
