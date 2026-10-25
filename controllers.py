"""Cooling controllers: PID and optimisation-based MPC.

Both controllers expose the same method:
    compute(temp_measured, current_now) -> u in [0, 1]
They only see the *current* measured temperature, never future temperatures.
"""

import numpy as np
from scipy.optimize import minimize

import thermal_model as tm


class PIDController:
    """Discrete PID on error = T_measured - T_target.

    Kp: proportional gain, 1/degC   -> reacts to how hot the battery is now.
    Ki: integral gain, 1/(degC*s)   -> removes steady-state error over time.
    Kd: derivative gain, s/degC     -> reacts to how fast temperature is rising.
    Positive error (too hot) gives more cooling. Output is clamped to [0, 1].
    """

    name = "PID"

    def __init__(self, kp: float, ki: float, kd: float, target: float, dt: float):
        if dt <= 0:
            raise ValueError("dt must be > 0")
        self.kp, self.ki, self.kd = kp, ki, kd
        self.target = target
        self.dt = dt
        self.integral = 0.0
        self.prev_error = None   # None until first sample -> derivative = 0 on first call

    def compute(self, temp_measured: float, current_now: float = 0.0) -> float:
        error = temp_measured - self.target

        if self.prev_error is None:
            derivative = 0.0     # no history yet, avoid derivative kick
        else:
            derivative = (error - self.prev_error) / self.dt
        self.prev_error = error

        candidate_integral = self.integral + error * self.dt
        u_raw = self.kp * error + self.ki * candidate_integral + self.kd * derivative
        u = float(np.clip(u_raw, 0.0, 1.0))

        # Anti-windup (conditional integration): only keep the new integral if the
        # output is not saturated, or if the error is pulling it out of saturation.
        saturated_high = u_raw > 1.0 and error > 0
        saturated_low = u_raw < 0.0 and error < 0
        if not (saturated_high or saturated_low):
            self.integral = candidate_integral
        return u


class MPCController:
    """Finite-horizon Model Predictive Controller solved with scipy.optimize.minimize.

    At each step it chooses N future commands u_0..u_{N-1} that minimise

        J = sum_k  qT*(T_k+1 - T_target)^2 + ru*u_k^2 + rdu*(u_k - u_{k-1})^2

    subject to 0 <= u_k <= 1 and |u_k - u_{k-1}| <= du_max,
    where T_k+1 is predicted with the same thermal model as the plant.
    The future load is forecast by holding the current current constant.
    Only u_0 is applied; the rest is used to warm-start the next solve.
    """

    name = "MPC"

    def __init__(self, params: tm.BatteryParams, target: float, dt: float,
                 horizon: int = 20, q_temp: float = 10.0, r_u: float = 0.1,
                 r_du: float = 1.0, du_max: float = 0.2, max_iter: int = 100):
        if horizon < 1:
            raise ValueError("horizon must be >= 1")
        self.p = params
        self.target = target
        self.dt = dt
        self.horizon = int(horizon)
        self.q_temp, self.r_u, self.r_du = q_temp, r_u, r_du
        self.du_max = du_max
        self.max_iter = max_iter
        self.u_prev = 0.0
        self.u_plan = np.zeros(self.horizon)   # warm start
        self.last_success = True
        self.failures = 0

    def predict(self, temp0: float, u_seq: np.ndarray, current_forecast: np.ndarray) -> np.ndarray:
        """Predicted temperatures T_1..T_N for a candidate command sequence."""
        temps = np.empty(len(u_seq))
        t = temp0
        for k, u in enumerate(u_seq):
            q_gen = tm.heat_generation(current_forecast[k], self.p)
            q_cool = tm.heat_removed(t, u, self.p)
            t = t + (self.dt / self.p.capacitance) * (q_gen - q_cool)
            temps[k] = t
        return temps

    def cost(self, u_seq: np.ndarray, temp0: float, current_forecast: np.ndarray) -> float:
        temps = self.predict(temp0, u_seq, current_forecast)
        du = np.diff(np.concatenate(([self.u_prev], u_seq)))
        return float(self.q_temp * np.sum((temps - self.target) ** 2)
                     + self.r_u * np.sum(u_seq ** 2)
                     + self.r_du * np.sum(du ** 2))

    def _slew_constraints(self):
        """Linear constraints du_max - |u_k - u_{k-1}| >= 0, written as two inequalities each."""
        if self.du_max is None:
            return []
        n = self.horizon
        # D @ u - d0 = [u0 - u_prev, u1 - u0, ...]
        D = np.eye(n) - np.eye(n, k=-1)

        def rate(u):
            r = D @ u
            r[0] -= self.u_prev
            return r

        return [
            {"type": "ineq", "fun": lambda u: self.du_max - rate(u), "jac": lambda u: -D},
            {"type": "ineq", "fun": lambda u: self.du_max + rate(u), "jac": lambda u: D},
        ]

    def compute(self, temp_measured: float, current_now: float) -> float:
        forecast = np.full(self.horizon, current_now)   # predicted load (held constant)
        x0 = np.clip(np.append(self.u_plan[1:], self.u_plan[-1]), 0.0, 1.0)  # shifted warm start

        result = minimize(
            self.cost, x0, args=(temp_measured, forecast),
            method="SLSQP", bounds=[(0.0, 1.0)] * self.horizon,
            constraints=self._slew_constraints(),
            options={"maxiter": self.max_iter},
        )

        if result.success and np.all(np.isfinite(result.x)):
            self.last_success = True
            self.u_plan = np.clip(result.x, 0.0, 1.0)
            u = float(self.u_plan[0])
        else:
            # Fallback: hold previous command (deterministic, bounded). Reported, not hidden.
            self.last_success = False
            self.failures += 1
            u = float(np.clip(self.u_prev, 0.0, 1.0))
            self.u_plan = np.full(self.horizon, u)

        # kept only so the app can draw what MPC "was thinking" at this step
        self.last_pred = self.predict(temp_measured, self.u_plan, forecast)
        self.u_prev = u
        return u
