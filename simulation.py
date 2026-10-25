"""Common closed-loop simulation engine used by both controllers."""

from dataclasses import dataclass, asdict, field
import math

import numpy as np
import pandas as pd

import thermal_model as tm
from controllers import PIDController, MPCController


@dataclass
class SimConfig:
    duration: float = 1800.0       # s
    dt: float = 5.0                # s
    initial_temp: float = 38.0     # degC
    target_temp: float = 35.0      # degC
    temp_limit: float = 40.0       # degC, used for "time above limit"
    current: float = 150.0         # A, base battery current
    profile: str = "step"          # "constant" or "step"
    # step profile: fraction of duration -> multiplier of base current
    step_multipliers: tuple = (1.0, 1.6, 0.6, 1.0)

    def validate(self) -> None:
        for name in ("duration", "dt", "initial_temp", "target_temp", "temp_limit", "current"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        if self.duration <= 0 or self.dt <= 0:
            raise ValueError("duration and dt must be > 0")
        if self.dt > self.duration:
            raise ValueError("dt must not exceed duration")
        if self.current < 0:
            raise ValueError("current must be >= 0")
        if self.profile not in ("constant", "step"):
            raise ValueError("profile must be 'constant' or 'step'")

    def as_dict(self) -> dict:
        d = asdict(self)
        d["step_multipliers"] = list(self.step_multipliers)
        return d


@dataclass
class ControllerSettings:
    kp: float = 0.3
    ki: float = 0.002
    kd: float = 2.0
    horizon: int = 20
    q_temp: float = 10.0
    r_u: float = 0.1
    r_du: float = 1.0
    du_max: float = 0.2

    def as_dict(self) -> dict:
        return asdict(self)


def current_profile(cfg: SimConfig) -> tuple[np.ndarray, np.ndarray]:
    """Time vector and actual battery current (piecewise constant)."""
    n = int(round(cfg.duration / cfg.dt))
    time = np.arange(n) * cfg.dt
    if cfg.profile == "constant":
        current = np.full(n, cfg.current)
    else:
        segments = len(cfg.step_multipliers)
        idx = np.minimum((time / cfg.duration * segments).astype(int), segments - 1)
        current = cfg.current * np.asarray(cfg.step_multipliers)[idx]
    return time, current


def run_simulation(controller, cfg: SimConfig, params: tm.BatteryParams,
                   time: np.ndarray, current: np.ndarray, plan_log: list | None = None) -> pd.DataFrame:
    """Closed loop: measure T -> controller -> u -> plant update. Returns one row per step.

    If plan_log is a list and the controller is MPC, its plan (u_plan, predicted T) is
    appended each step for visualisation only.
    """
    temp = cfg.initial_temp
    rows = []
    for k in range(len(time)):
        u = controller.compute(temp, current[k])      # controller sees only the present
        if not (0.0 <= u <= 1.0 and math.isfinite(u)):
            raise RuntimeError(f"{controller.name} returned invalid command {u}")
        if plan_log is not None and hasattr(controller, "last_pred"):
            plan_log.append((controller.u_plan.copy(), controller.last_pred.copy()))
        q_gen = tm.heat_generation(current[k], params)
        q_cool = tm.heat_removed(temp, u, params)
        p_cool = tm.cooling_power(u, params)
        rows.append({
            "time_s": time[k],
            "temp_C": temp,
            "target_C": cfg.target_temp,
            "ambient_C": params.ambient,
            "current_A": current[k],
            "q_gen_W": q_gen,
            "u": u,
            "q_cool_W": q_cool,
            "p_cool_W": p_cool,
            "solver_ok": getattr(controller, "last_success", True),
        })
        temp = tm.step(temp, current[k], u, cfg.dt, params)
    df = pd.DataFrame(rows)
    df["energy_Wh"] = (df["p_cool_W"] * cfg.dt).cumsum() / 3600.0
    return df


def run_both(cfg: SimConfig, params: tm.BatteryParams, s: ControllerSettings) -> dict:
    """Run PID and MPC on the same plant, same initial temperature, same load."""
    cfg.validate()
    params.validate(cfg.dt)
    time, current = current_profile(cfg)

    pid = PIDController(s.kp, s.ki, s.kd, cfg.target_temp, cfg.dt)
    mpc = MPCController(params, cfg.target_temp, cfg.dt, horizon=s.horizon,
                        q_temp=s.q_temp, r_u=s.r_u, r_du=s.r_du, du_max=s.du_max)

    mpc_plans = []
    results = {
        "PID": run_simulation(pid, cfg, params, time, current),
        "MPC": run_simulation(mpc, cfg, params, time, current, plan_log=mpc_plans),
    }
    results["mpc_failures"] = mpc.failures
    results["mpc_plans"] = mpc_plans
    return results


def results_to_csv(results: dict) -> str:
    """Wide CSV: shared time/current columns, then PID_* and MPC_* columns."""
    shared = ["time_s", "target_C", "ambient_C", "current_A"]
    pid, mpc = results["PID"], results["MPC"]
    out = pid[shared].copy()
    for name, df in (("PID", pid), ("MPC", mpc)):
        for col in df.columns:
            if col not in shared:
                out[f"{name}_{col}"] = df[col].values
    return out.to_csv(index=False)
