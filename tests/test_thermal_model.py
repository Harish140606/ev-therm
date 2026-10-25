import math

import pytest

import thermal_model as tm


def params():
    return tm.BatteryParams(capacitance=1e5, resistance=0.1, k_cool=1000.0, ambient=30.0, p_cool_max=2000.0)


def test_step_matches_equation():
    p = params()
    T, I, u, dt = 40.0, 100.0, 0.5, 5.0
    q_gen = I ** 2 * p.resistance                 # 1000 W
    q_cool = p.k_cool * u * (T - p.ambient)       # 5000 W
    expected = T + dt / p.capacitance * (q_gen - q_cool)
    assert tm.step(T, I, u, dt, p) == pytest.approx(expected)
    assert expected == pytest.approx(39.8)


def test_constant_heat_no_cooling_rises_linearly():
    p = params()
    T = 30.0
    for _ in range(100):
        T = tm.step(T, 100.0, 0.0, 1.0, p)
    # dT/dt = 1000 W / 1e5 J/K = 0.01 K/s -> +1 K after 100 s
    assert T == pytest.approx(31.0)


def test_more_heat_means_hotter():
    p = params()
    assert tm.step(35.0, 200.0, 0.3, 5.0, p) > tm.step(35.0, 100.0, 0.3, 5.0, p)


def test_more_cooling_means_cooler():
    p = params()
    temps = []
    for u in (0.0, 0.5, 1.0):
        T = 35.0
        for _ in range(200):
            T = tm.step(T, 150.0, u, 5.0, p)
        temps.append(T)
    assert temps[0] > temps[1] > temps[2]


def test_full_cooling_steady_state():
    p = params()
    T = 40.0
    for _ in range(5000):
        T = tm.step(T, 150.0, 1.0, 5.0, p)
    assert T == pytest.approx(p.ambient + 2250.0 / p.k_cool, abs=1e-3)


@pytest.mark.parametrize("bad", [
    dict(capacitance=0.0), dict(capacitance=-1.0), dict(resistance=-0.1),
    dict(k_cool=-5.0), dict(ambient=math.inf), dict(p_cool_max=math.nan),
])
def test_invalid_params_rejected(bad):
    p = tm.BatteryParams(**bad)
    with pytest.raises(ValueError):
        p.validate(dt=1.0)


def test_unstable_dt_rejected():
    with pytest.raises(ValueError):
        params().validate(dt=200.0)   # dt*K/C = 2 -> unstable Euler


def test_u_out_of_range_rejected():
    with pytest.raises(ValueError):
        tm.step(35.0, 100.0, 1.5, 1.0, params())


def test_cooling_power_cubic():
    p = params()
    assert tm.cooling_power(0.0, p) == 0.0
    assert tm.cooling_power(1.0, p) == pytest.approx(2000.0)
    assert tm.cooling_power(0.5, p) == pytest.approx(250.0)
