import pytest

from flow_control.star_ingest.final_contract import validate_final_contract_columns


def test_0816_contract_accepts_current_columns():
    assert validate_final_contract_columns(
        ["JET_01", "cmd_massflow_01", "actual_massflow_01", "Fz_Total", "Jet_Reaction_Z"],
        table_kind="actuation",
    ) == []


def test_0816_contract_rejects_underbody_zone_as_action():
    with pytest.warns(UserWarning), pytest.raises(ValueError, match="JET01"):
        validate_final_contract_columns(["JET01"], table_kind="actuation")
