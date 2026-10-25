import io

import numpy as np
import pandas as pd
import pytest

import thermal_model as tm
from metrics import compute_metrics, cooling_energy_wh, metrics_table
from simulation import SimConfig, ControllerSettings, run_both, current_profile, results_to_csv


def short_cfg(**kw):
    base = dict(duration=600.0, dt=5.0, initial_temp=38.0, target_temp=35.0, current=150.0, profile="step")
    base.update(kw)
    return SimConfig(**base)


@pytest.fixture(scope="module")
def results():
    return run_both(short_cfg(), tm.BatteryParams(), ControllerSettings())


def test_lengths_and_finite(results):
    n = int(600 / 5)
    for name in ("PID", "MPC"):
        df = results[name]
        assert len(df) == n
        numeric = df.select_dtypes("number").to_numpy()
        assert np.all(np.isfinite(numeric))
        assert df["u"].between(0.0, 1.0).all()
        assert (df["p_cool_W"] >= 0).all()


def test_same_initial_conditions_and_load(results):
    pid, mpc = results["PID"], results["MPC"]
    assert pid["temp_C"].iloc[0] == mpc["temp_C"].iloc[0] == 38.0
    assert np.array_equal(pid["current_A"], mpc["current_A"])
    assert np.array_equal(pid["q_gen_W"], mpc["q_gen_W"])


def test_closed_loop_uses_controller_output(results):
    """Re-simulate the plant from the recorded u; must reproduce the recorded temperatures."""
    p = tm.BatteryParams()
    for name in ("PID", "MPC"):
        df = results[name]
        T = df["temp_C"].iloc[0]
        for k in range(len(df) - 1):
            T = tm.step(T, df["current_A"].iloc[k], df["u"].iloc[k], 5.0, p)
            assert T == pytest.approx(df["temp_C"].iloc[k + 1])


def test_controllers_regulate_toward_target(results):
    # constant moderate load: both should end close to target
    res = run_both(short_cfg(profile="constant", duration=1800.0), tm.BatteryParams(), ControllerSettings())
    for name in ("PID", "MPC"):
        assert abs(res[name]["temp_C"].iloc[-1] - 35.0) < 0.5


def test_cooling_lowers_temperature_vs_no_cooling(results):
    p = tm.BatteryParams()
    _, current = current_profile(short_cfg())
    T = 38.0
    for I in current:
        T = tm.step(T, I, 0.0, 5.0, p)
    for name in ("PID", "MPC"):
        assert results[name]["temp_C"].iloc[-1] < T


def test_deterministic(results):
    again = run_both(short_cfg(), tm.BatteryParams(), ControllerSettings())
    for name in ("PID", "MPC"):
        pd.testing.assert_frame_equal(results[name], again[name])


def test_energy_calculation():
    # 1000 W for 3600 s = 1 Wh * 1000
    assert cooling_energy_wh(np.full(720, 1000.0), 5.0) == pytest.approx(1000.0)
    df = pd.DataFrame({"temp_C": [36.0, 34.0], "target_C": [35.0, 35.0], "u": [1.0, 0.5],
                       "p_cool_W": [2000.0, 250.0], "q_cool_W": [1000.0, 500.0]})
    m = compute_metrics(df, dt=3600.0, temp_limit=35.5)
    assert m["Cooling energy (Wh)"] == pytest.approx(2250.0)
    assert m["Mean abs error (°C)"] == pytest.approx(1.0)
    assert m["RMSE (°C)"] == pytest.approx(1.0)
    assert m["Peak temperature (°C)"] == 36.0
    assert m["Time above limit (s)"] == 3600.0
    assert m["Time at full cooling while hot (s)"] == 3600.0


def test_energy_matches_series(results):
    table = metrics_table(results, 5.0, 40.0)
    for name in ("PID", "MPC"):
        assert table.loc["Cooling energy (Wh)", name] == pytest.approx(results[name]["energy_Wh"].iloc[-1])
        assert table.loc["Cooling energy (Wh)", name] >= 0


def test_csv_contains_real_results(results):
    df = pd.read_csv(io.StringIO(results_to_csv(results)))
    assert len(df) == len(results["PID"])
    assert np.allclose(df["PID_temp_C"], results["PID"]["temp_C"])
    assert np.allclose(df["MPC_u"], results["MPC"]["u"])


def test_overload_is_reported():
    # 300 A -> 9000 W; full cooling holds only Ta + 9 = 39 C > 35 C target
    res = run_both(short_cfg(profile="constant", current=300.0), tm.BatteryParams(), ControllerSettings())
    table = metrics_table(res, 5.0, 40.0)
    for name in ("PID", "MPC"):
        assert table.loc["Time at full cooling while hot (s)", name] > 0
        assert res[name]["temp_C"].min() > 35.0


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        run_both(short_cfg(dt=0.0), tm.BatteryParams(), ControllerSettings())
    with pytest.raises(ValueError):
        run_both(short_cfg(current=-1.0), tm.BatteryParams(), ControllerSettings())
