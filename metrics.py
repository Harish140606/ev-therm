"""Performance metrics. Same definitions for both controllers."""

import numpy as np
import pandas as pd


def compute_metrics(df: pd.DataFrame, dt: float, temp_limit: float) -> dict:
    error = df["temp_C"] - df["target_C"]
    saturated = (df["u"] >= 0.999) & (error > 0)
    return {
        "Peak temperature (°C)": float(df["temp_C"].max()),
        "Mean abs error (°C)": float(np.mean(np.abs(error))),
        "RMSE (°C)": float(np.sqrt(np.mean(error ** 2))),
        "Cooling energy (Wh)": cooling_energy_wh(df["p_cool_W"].to_numpy(), dt),
        "Heat removed (Wh)": float(np.sum(df["q_cool_W"]) * dt / 3600.0),
        "Max cooling command": float(df["u"].max()),
        "Time above limit (s)": float(np.sum(df["temp_C"] > temp_limit) * dt),
        "Time at full cooling while hot (s)": float(np.sum(saturated) * dt),
    }


def cooling_energy_wh(p_cool_w: np.ndarray, dt: float) -> float:
    """E = sum(Pcool * dt) in J, converted to Wh by / 3600."""
    return float(np.sum(p_cool_w) * dt / 3600.0)


def metrics_table(results: dict, dt: float, temp_limit: float) -> pd.DataFrame:
    table = {name: compute_metrics(results[name], dt, temp_limit) for name in ("PID", "MPC")}
    return pd.DataFrame(table)


def trade_off_notes(table: pd.DataFrame) -> list[str]:
    """Plain-language observations. Does not pick a winner."""
    notes = []
    e_pid, e_mpc = table.loc["Cooling energy (Wh)", "PID"], table.loc["Cooling energy (Wh)", "MPC"]
    r_pid, r_mpc = table.loc["RMSE (°C)", "PID"], table.loc["RMSE (°C)", "MPC"]
    lower_e = "PID" if e_pid < e_mpc else "MPC"
    lower_r = "PID" if r_pid < r_mpc else "MPC"
    if abs(e_pid - e_mpc) > 1e-6:
        notes.append(f"{lower_e} used less cooling energy ({min(e_pid, e_mpc):.1f} vs {max(e_pid, e_mpc):.1f} Wh).")
    if abs(r_pid - r_mpc) > 1e-6:
        notes.append(f"{lower_r} tracked the target more closely (RMSE {min(r_pid, r_mpc):.2f} vs {max(r_pid, r_mpc):.2f} °C).")
    if lower_e != lower_r:
        notes.append("Trade-off: the controller that saves energy lets the temperature drift further from the target.")
    for name in ("PID", "MPC"):
        if table.loc["Time at full cooling while hot (s)", name] > 0:
            notes.append(f"{name} hit full cooling (u = 1) while above target for "
                         f"{table.loc['Time at full cooling while hot (s)', name]:.0f} s: "
                         "heat load exceeded cooling capacity at that time.")
    return notes
