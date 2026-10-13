from flow_control.config import load_config_with_system_defaults, merge_config


def test_merge_config_recursively_preserves_local_overrides():
    merged = merge_config(
        {
            "system": {"random_seed": 1, "name": "base"},
            "actuation": {"random_seed": 2, "mode": "prbs_demo"},
        },
        {
            "actuation": {"mode": "pulse_singlejet"},
        },
    )

    assert merged["system"]["random_seed"] == 1
    assert merged["actuation"]["random_seed"] == 2
    assert merged["actuation"]["mode"] == "pulse_singlejet"


def test_load_config_with_system_defaults_uses_env_override(tmp_path, monkeypatch):
    system_config = tmp_path / "system.yaml"
    local_config = tmp_path / "pulse.yaml"
    system_config.write_text(
        "system:\n"
        "  random_seed: 2468\n",
        encoding="utf-8",
    )
    local_config.write_text(
        "actuation:\n"
        "  mode: pulse_singlejet\n"
        "  total_windows: 4\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLOW_CONTROL_SYSTEM_CONFIG", str(system_config))

    merged = load_config_with_system_defaults(local_config)

    assert merged["system"]["random_seed"] == 2468
    assert merged["actuation"]["mode"] == "pulse_singlejet"
