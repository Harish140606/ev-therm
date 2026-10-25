import io

import pandas as pd

from app import app


def client():
    app.config["TESTING"] = True
    return app.test_client()


def test_pages_served():
    c = client()
    assert c.get("/").status_code == 200
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/vendor/plotly.min.js").status_code == 200


def test_simulate_returns_real_results():
    c = client()
    defaults = c.get("/api/defaults").get_json()
    body = {"simulation": dict(defaults["simulation"], duration=300.0),
            "battery": defaults["battery"], "controllers": defaults["controllers"]}
    r = c.post("/api/simulate", json=body)
    assert r.status_code == 200
    d = r.get_json()
    n = 300 // 5
    assert len(d["series"]["PID"]["temp_C"]) == n == len(d["series"]["MPC"]["temp_C"])
    assert len(d["mpc_plans"]["u"]) == n
    assert len(d["mpc_plans"]["u"][0]) == defaults["controllers"]["horizon"]
    csv = pd.read_csv(io.StringIO(d["csv"]))
    assert list(csv["MPC_u"].round(9)) == [round(x, 9) for x in d["series"]["MPC"]["u"]]


def test_simulate_rejects_bad_input():
    r = client().post("/api/simulate", json={"simulation": {"dt": 0}})
    assert r.status_code == 400
    assert "error" in r.get_json()
