"""EV-Therm web app: Flask serves the dashboard and runs simulations.

Run with:  python app.py      (opens http://127.0.0.1:8050)
"""

import os
import threading
import webbrowser
from dataclasses import fields

import plotly
from flask import Flask, jsonify, request, send_from_directory

import thermal_model as tm
from metrics import metrics_table, trade_off_notes
from simulation import SimConfig, ControllerSettings, run_both, results_to_csv

HERE = os.path.dirname(os.path.abspath(__file__))
PLOTLY_DIR = os.path.join(os.path.dirname(plotly.__file__), "package_data")
PORT = 8050

app = Flask(__name__, static_folder=os.path.join(HERE, "static"), static_url_path="/static")


def _defaults(cls) -> dict:
    obj = cls()
    return {f.name: getattr(obj, f.name) for f in fields(cls) if f.name != "step_multipliers"}


def _build(cls, data: dict):
    """Create a dataclass from JSON, keeping defaults for missing keys and casting numbers."""
    kwargs = {}
    for f in fields(cls):
        if f.name in data:
            default = getattr(cls(), f.name)
            value = data[f.name]
            if isinstance(default, bool) or isinstance(default, (str, tuple)):
                kwargs[f.name] = value
            elif isinstance(default, int):
                kwargs[f.name] = int(value)
            else:
                kwargs[f.name] = float(value)
    return cls(**kwargs)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/vendor/plotly.min.js")
def plotly_js():
    return send_from_directory(PLOTLY_DIR, "plotly.min.js")


@app.get("/api/defaults")
def defaults():
    return jsonify({
        "simulation": _defaults(SimConfig),
        "battery": _defaults(tm.BatteryParams),
        "controllers": _defaults(ControllerSettings),
        "step_multipliers": list(SimConfig().step_multipliers),
    })


@app.post("/api/simulate")
def simulate():
    body = request.get_json(force=True, silent=True) or {}
    try:
        cfg = _build(SimConfig, body.get("simulation", {}))
        params = _build(tm.BatteryParams, body.get("battery", {}))
        settings = _build(ControllerSettings, body.get("controllers", {}))
        res = run_both(cfg, params, settings)
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400

    table = metrics_table(res, cfg.dt, cfg.temp_limit)

    # Capacity check: hottest steady state with full cooling, T = Ta + Qgen / Kcool
    warnings = []
    peak_current = res["PID"]["current_A"].max()
    q_peak = tm.heat_generation(peak_current, params)
    if params.k_cool > 0 and params.ambient + q_peak / params.k_cool > cfg.target_temp:
        warnings.append(
            f"At peak load (Qgen = {q_peak:.0f} W) even full cooling only holds the battery at "
            f"{params.ambient + q_peak / params.k_cool:.1f} °C, above the {cfg.target_temp:.1f} °C target. "
            "This is a cooling-capacity limit, not a controller fault.")
    if cfg.target_temp < params.ambient:
        warnings.append("Target is below ambient; this cooler can only reject heat to ambient air.")
    if res["mpc_failures"]:
        warnings.append(f"MPC optimiser failed on {res['mpc_failures']} steps; it held the previous command there.")

    series = {name: res[name].drop(columns=["solver_ok"]).to_dict(orient="list") for name in ("PID", "MPC")}
    return jsonify({
        "series": series,
        "mpc_plans": {"u": [p[0].tolist() for p in res["mpc_plans"]],
                      "T": [p[1].tolist() for p in res["mpc_plans"]]},
        "metrics": {name: table[name].to_dict() for name in ("PID", "MPC")},
        "notes": trade_off_notes(table),
        "warnings": warnings,
        "mpc_failures": res["mpc_failures"],
        "csv": results_to_csv(res),
        "config": {"simulation": cfg.as_dict(), "battery": params.as_dict(),
                   "controllers": settings.as_dict()},
    })


if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}"
    print(f"EV-Therm running at {url}  (Ctrl+C to stop)")
    if os.environ.get("EVTHERM_NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False)
