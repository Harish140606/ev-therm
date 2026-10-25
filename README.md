# EV-Therm — EV Battery Thermal Control Simulator

## 1. Introduction
EV-Therm is a software-only simulator that keeps an electric-vehicle battery near a target
temperature with two cooling controllers, **PID** and **Model Predictive Control (MPC)**,
and compares their temperature tracking and cooling energy. Everything runs locally; no hardware.

## 2. Problem statement
Current flowing through a battery's internal resistance produces heat. If temperature rises too
far, cells age faster or become unsafe; if the cooler runs too hard, it wastes energy that would
otherwise drive the car. The controller must balance the two.

## 3. Objectives
- Model battery temperature with a simple first-order thermal equation.
- Regulate it with a PID controller and with an optimisation-based MPC.
- Run both on the same plant, load and initial condition.
- Compare peak temperature, tracking error and cooling energy.

## 4. Software requirements
Python 3.10+ (tested on 3.14), with: flask, numpy, scipy, plotly, pandas, pytest.
The dashboard is a local web page (HTML/CSS/JS + Plotly.js served from the installed plotly package),
so it works offline. No database, no cloud, no Node.js.

## 5. Installation (Windows PowerShell)
```powershell
cd ev-therm
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```
(macOS/Linux: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`)

## 6. Start the app
```powershell
.venv\Scripts\python app.py
```
The browser opens http://127.0.0.1:8050 automatically. Press Ctrl+C in the terminal to stop.
Tip for demos: http://127.0.0.1:8050/?autorun runs the default simulation on page load.

Dashboard sections: settings panel (left), KPI cards, **Control loop in action** (animated block
diagrams for PID and MPC with play/scrub, plus MPC's predicted horizon at each step), result charts,
metrics table with trade-off notes, CSV/settings download, and the theory section.

Run the tests:
```powershell
.venv\Scripts\python -m pytest -q
```

## 7. Thermal model
Single-node (lumped) model, one temperature T:

    C·dT/dt = Qgen − Qcool
    Qgen    = I²·Rint                 (Joule heating, W)
    Qcool   = Kcool·u·(T − Ta)        (cooling to ambient, W), 0 ≤ u ≤ 1

Discrete form (forward Euler), used by both the plant and MPC's predictor:

    T[k+1] = T[k] + (Δt / C)·(Qgen[k] − Qcool[k])

Units: °C, J/K, W, s. Temperature *differences* in °C equal those in K.
Euler is stable only if Δt·Kcool/C < 1; the app checks this.
With full cooling the hottest steady state is `T = Ta + Qgen/Kcool` — if that is above the
target, **no controller can reach the target** (capacity limit). The app warns about this and
reports "time at full cooling while hot".

Default parameters (illustrative assumptions, not a real battery):

| Parameter | Value |
|---|---|
| C | 1.0×10⁵ J/K |
| Rint | 0.1 Ω |
| Kcool | 1000 W/K |
| Ta | 30 °C |
| Pmax | 2000 W |
| I (base) | 150 A → Qgen = 2250 W |

Load profile: constant, or piecewise-constant steps ×1.0, ×1.6, ×0.6, ×1.0 of the base current
over four equal parts of the run. At ×1.6 (240 A, 5760 W) the battery exceeds the target even
at full cooling — this shows the capacity limit on purpose.

## 8. PID controller
Error `e = T − T_target` (positive = too hot → more cooling).

    u = clip(Kp·e + Ki·Σe·Δt + Kd·(e − e_prev)/Δt, 0, 1)

- **Kp** (1/°C): cooling proportional to how hot it is now.
- **Ki** (1/(°C·s)): builds up over time to remove steady-state error.
- **Kd** (s/°C): reacts to how fast temperature changes; damps overshoot.
- Anti-windup: integral is frozen while the output is saturated and the error pushes further.
- First sample: derivative = 0 (no derivative kick).

Defaults: Kp = 0.3, Ki = 0.002, Kd = 2.0.

## 9. MPC controller
Every time step:
1. Measure current temperature T.
2. Forecast load: current held constant over the horizon (*predicted* load; actual may change).
3. Choose N commands u₀…u_{N−1} minimising

       J = Σ qT·(T̂[k+1] − T_target)² + ru·u_k² + rdu·(u_k − u_{k−1})²

   subject to `0 ≤ u_k ≤ 1` and `|u_k − u_{k−1}| ≤ Δu_max`, with T̂ predicted by the thermal model.
   Solved with `scipy.optimize.minimize(method="SLSQP")`.
4. Apply only u₀; shift the plan to warm-start the next solve.
5. If SLSQP fails, hold the previous command and count the failure (shown in the app).

Defaults: N = 20, qT = 10, ru = 0.1, rdu = 1, Δu_max = 0.2.
MPC never sees future actual temperatures or future actual load.

## 10. Performance metrics (same for both controllers)
- Peak temperature (°C)
- Mean absolute error = mean|T − T_target| (°C)
- RMSE = √mean((T − T_target)²) (°C)
- Cooling energy: `Pcool = Pmax·u³`, `E = ΣPcool·Δt` J, `E_Wh = E/3600`
- Heat removed (thermal, Wh) — not the same as electrical energy
- Maximum cooling command
- Time above temperature limit (s)
- Time at full cooling while above target (s) — capacity limit indicator

The app does **not** pick a winner; it states the energy vs. tracking trade-off.

## 11. Folder structure
```
ev-therm/
├── app.py              Flask server: serves the page, /api/simulate runs both controllers
├── thermal_model.py    battery equations + parameter checks
├── controllers.py      PIDController, MPCController
├── simulation.py       closed-loop engine, load profile, CSV export
├── metrics.py          metrics and trade-off notes
├── static/             index.html, style.css, app.js (dashboard, charts, loop animation)
├── requirements.txt
└── tests/              pytest suite
```

## 12. Assumptions and limitations
- One lumped temperature: no gradients between cells or modules.
- Rint constant (real Rint depends on temperature and state of charge).
- Cooling only to ambient, linear in u and (T − Ta); cannot cool below ambient.
- Cooler power law `Pmax·u³` is an assumption (fan/pump affinity law).
- Parameters are illustrative, not from a specific commercial pack.
- No sensor noise or delay; MPC's internal model equals the plant (perfect model).
- PID has no slew-rate limit; MPC has Δu_max. This is part of the comparison.
- Forward-Euler discretisation; keep Δt·Kcool/C < 1.
