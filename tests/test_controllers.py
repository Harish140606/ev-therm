import numpy as np
import pytest

import controllers
import thermal_model as tm
from controllers import PIDController, MPCController


def params():
    return tm.BatteryParams()


def test_pid_output_bounded():
    pid = PIDController(kp=5.0, ki=1.0, kd=50.0, target=35.0, dt=1.0)
    for T in [100.0, -50.0, 35.0, 80.0, 0.0, 1e4]:
        u = pid.compute(T)
        assert 0.0 <= u <= 1.0


def test_pid_direction():
    hot = PIDController(0.3, 0.0, 0.0, target=35.0, dt=1.0).compute(38.0)
    cold = PIDController(0.3, 0.0, 0.0, target=35.0, dt=1.0).compute(33.0)
    assert hot > 0.0
    assert cold == 0.0
    hotter = PIDController(0.3, 0.0, 0.0, target=35.0, dt=1.0).compute(37.0)
    assert hot > hotter


def test_pid_first_derivative_is_zero():
    pid = PIDController(kp=0.0, ki=0.0, kd=10.0, target=35.0, dt=1.0)
    assert pid.compute(40.0) == 0.0          # no derivative kick on first call
    assert pid.compute(40.1) == pytest.approx(1.0)   # 10 * 0.1 / 1 = 1.0


def test_pid_anti_windup():
    pid = PIDController(kp=1.0, ki=0.1, kd=0.0, target=35.0, dt=1.0)
    for _ in range(1000):
        pid.compute(45.0)        # saturated high for a long time
    assert pid.integral < 20.0   # integral frozen near saturation, not 10 000
    # once below target the output drops quickly instead of staying stuck at 1
    assert pid.compute(34.0) < 1.0


def test_mpc_output_bounded_and_slew_limited():
    mpc = MPCController(params(), target=35.0, dt=5.0, horizon=10, du_max=0.2)
    u_prev = 0.0
    for T in [45.0, 45.0, 30.0, 50.0, 20.0]:
        u = mpc.compute(T, 150.0)
        assert 0.0 <= u <= 1.0
        assert abs(u - u_prev) <= 0.2 + 1e-6
        u_prev = u


def test_mpc_cools_when_hot_not_when_cold():
    hot = MPCController(params(), target=35.0, dt=5.0, horizon=10).compute(40.0, 150.0)
    cold = MPCController(params(), target=35.0, dt=5.0, horizon=10).compute(31.0, 0.0)
    assert hot > 0.1
    assert cold < 0.01


def test_mpc_actually_optimises():
    """The chosen plan must have lower cost than simple alternatives."""
    mpc = MPCController(params(), target=35.0, dt=5.0, horizon=10, du_max=None)
    mpc.compute(36.0, 150.0)
    forecast = np.full(10, 150.0)
    mpc.u_prev = 0.0
    best = mpc.cost(mpc.u_plan, 36.0, forecast)
    assert 0.0 < mpc.u_plan[0] < 1.0          # interior optimum, not just a bound
    for const in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0):
        # SLSQP default ftol is 1e-6, so allow that relative slack
        assert best <= mpc.cost(np.full(10, const), 36.0, forecast) * (1 + 1e-6)


def test_mpc_prediction_uses_model():
    p = params()
    mpc = MPCController(p, target=35.0, dt=5.0, horizon=3)
    pred = mpc.predict(40.0, np.array([0.5, 0.5, 0.5]), np.full(3, 150.0))
    T = 40.0
    for k in range(3):
        T = tm.step(T, 150.0, 0.5, 5.0, p)
        assert pred[k] == pytest.approx(T)


class _FailedResult:
    success = False
    x = np.full(10, 0.9)
    message = "forced failure"


def test_mpc_failure_falls_back(monkeypatch):
    mpc = MPCController(params(), target=35.0, dt=5.0, horizon=10)
    u_ok = mpc.compute(40.0, 150.0)
    assert mpc.last_success

    monkeypatch.setattr(controllers, "minimize", lambda *a, **k: _FailedResult())
    u_fail = mpc.compute(40.0, 150.0)
    assert not mpc.last_success
    assert mpc.failures == 1
    assert u_fail == pytest.approx(u_ok)     # held previous command, not the bad solution
    assert 0.0 <= u_fail <= 1.0
