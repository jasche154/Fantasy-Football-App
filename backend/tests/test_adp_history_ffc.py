"""parse_ffc against real Fantasy Football Calculator payloads (2023, 12 teams, trimmed to 12 players each,
in tests/data/ffc_2023): every format's meta type matches FFC_FORMATS, and a list FFC may have answered
for another format is refused."""
import json
from pathlib import Path

import pytest

from integrations import adp_history

FIXTURES = Path(__file__).parent / "data" / "ffc_2023"


def real(adp_format: str) -> dict:
    return json.loads((FIXTURES / f"{adp_format}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("adp_format", list(adp_history.FFC_FORMATS))
def test_each_real_ffc_payload_parses_under_its_own_format(adp_format):
    payload = real(adp_format)

    entries = adp_history.parse_ffc(payload, adp_format)

    assert payload["meta"]["type"] == adp_history.FFC_FORMATS[adp_format][1]
    assert len(entries) == 12 and entries == sorted(entries, key=lambda e: e.adp)


@pytest.mark.parametrize("adp_format", ["half", "std", "2qb"])
def test_a_payload_without_meta_is_refused_for_a_non_ppr_format(adp_format):
    payload = {key: value for key, value in real("ppr").items() if key != "meta"}    # FFC's default list

    with pytest.raises(adp_history.HistoryError, match="another format"):
        adp_history.parse_ffc(payload, adp_format)


def test_a_payload_without_meta_still_parses_as_ppr():
    payload = {key: value for key, value in real("ppr").items() if key != "meta"}

    assert len(adp_history.parse_ffc(payload, "ppr")) == 12


@pytest.mark.parametrize("adp_format", ["ppr", "half", "std"])
def test_another_formats_real_list_is_refused(adp_format):
    with pytest.raises(adp_history.HistoryError, match="another format"):
        adp_history.parse_ffc(real("2qb"), adp_format)


@pytest.mark.parametrize("adp_format", ["half", "std", "2qb"])
@pytest.mark.parametrize("payload", [{"status": "Success", "players": []}, {"status": "Success"},
                                     {"status": "Success", "players": None},
                                     {"status": "Error", "errors": "No ADP data found."}])
def test_ffcs_own_no_data_answers_are_no_data_in_every_format(payload, adp_format):
    with pytest.raises(adp_history.FfcNoDataError):
        adp_history.parse_ffc(payload, adp_format)
