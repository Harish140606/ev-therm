// EV-Therm front end: settings form, run, charts, animated control-loop diagrams.
// All numbers shown come from the Python simulation (/api/simulate).

const COLORS = { PID: "#f5a524", MPC: "#22d3ee", plan: "#a78bfa", target: "#9aa5b1", limit: "#f87171" };

// [section, key, label, unit, step]  (unit "select" = dropdown)
const FIELDS = [
  { group: "Scenario", items: [
    ["battery", "ambient", "Ambient temperature", "°C", 0.5],
    ["simulation", "initial_temp", "Initial battery temp", "°C", 0.5],
    ["simulation", "target_temp", "Target temperature", "°C", 0.5],
    ["simulation", "temp_limit", "Temperature limit", "°C", 0.5],
    ["simulation", "current", "Base current", "A", 5],
    ["simulation", "profile", "Load profile", "select"],
    ["simulation", "duration", "Duration", "s", 60],
    ["simulation", "dt", "Time step", "s", 0.5],
  ]},
  { group: "Battery & cooler", items: [
    ["battery", "capacitance", "Thermal capacitance", "J/K", 10000],
    ["battery", "resistance", "Internal resistance", "Ω", 0.01],
    ["battery", "k_cool", "Cooling coefficient", "W/K", 50],
    ["battery", "p_cool_max", "Max cooler power", "W", 100],
  ]},
  { group: "PID gains", collapsible: true, items: [
    ["controllers", "kp", "Kp — proportional", "1/°C", 0.05],
    ["controllers", "ki", "Ki — integral", "1/°C·s", 0.0005],
    ["controllers", "kd", "Kd — derivative", "s/°C", 0.5],
  ]},
  { group: "MPC settings", collapsible: true, items: [
    ["controllers", "horizon", "Horizon N", "steps", 1],
    ["controllers", "q_temp", "qT — temp error", "", 1],
    ["controllers", "r_u", "ru — effort", "", 0.05],
    ["controllers", "r_du", "rdu — change", "", 0.1],
    ["controllers", "du_max", "Δu max / step", "", 0.01],
  ]},
];

const $ = (id) => document.getElementById(id);
const state = { data: null, lastRunSettings: null, k: 0, shown: -1, playing: false, lastChart: 0, flows: {} };

// ---------------- settings form ----------------
function buildForm(defaults) {
  const form = $("settings");
  form.innerHTML = "";
  for (const g of FIELDS) {
    const box = document.createElement(g.collapsible ? "details" : "div");
    box.className = "group";
    const title = document.createElement(g.collapsible ? "summary" : "div");
    if (!g.collapsible) title.className = "group-title";
    title.textContent = g.group;
    box.appendChild(title);
    for (const [section, key, label, unit, step] of g.items) {
      const id = `f-${section}-${key}`;
      const row = document.createElement("div");
      row.className = "field";
      let control;
      if (unit === "select") {
        control = `<select id="${id}"><option value="step" title="×1, ×1.6, ×0.6, ×1 of base current">Steps</option><option value="constant">Constant</option></select>`;
      } else {
        control = `<input id="${id}" type="number" step="${step}" value="${defaults[section][key]}"><span class="unit">${unit}</span>`;
      }
      row.innerHTML = `<label for="${id}">${label}</label><div class="input-wrap">${control}</div>`;
      box.appendChild(row);
    }
    form.appendChild(box);
  }
  $("f-simulation-profile").value = defaults.simulation.profile;
  form.addEventListener("input", markStale);
}

function readForm() {
  const out = { simulation: {}, battery: {}, controllers: {} };
  for (const g of FIELDS) {
    for (const [section, key, , unit] of g.items) {
      const el = $(`f-${section}-${key}`);
      out[section][key] = unit === "select" ? el.value : parseFloat(el.value);
    }
  }
  return out;
}

function markStale() {
  if (!state.data) return;
  $("stale").hidden = JSON.stringify(readForm()) === state.lastRunSettings;
}

// ---------------- run ----------------
async function run() {
  const btn = $("run");
  const settings = readForm();
  for (const sec of Object.values(settings)) {
    for (const [k, v] of Object.entries(sec)) {
      if (typeof v === "number" && !Number.isFinite(v)) return showError(`"${k}" is not a valid number.`);
    }
  }
  btn.disabled = true; btn.classList.add("busy");
  $("run-status").textContent = "Simulating… MPC solves an optimisation every step.";
  const t0 = performance.now();
  try {
    const resp = await fetch("/api/simulate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || `Server error ${resp.status}`);
    state.data = data;
    state.lastRunSettings = JSON.stringify(settings);
    $("error").hidden = true;
    $("intro").hidden = true;
    $("results").hidden = false;
    $("stale").hidden = true;
    render();
    $("run-status").textContent = `Done in ${((performance.now() - t0) / 1000).toFixed(1)} s · ${data.series.PID.time_s.length} steps`;
  } catch (e) {
    showError(e.message);
    $("run-status").textContent = "";
  } finally {
    btn.disabled = false; btn.classList.remove("busy");
  }
}

function showError(msg) {
  const el = $("error");
  el.textContent = `Invalid settings: ${msg}`;
  el.hidden = false;
}

// ---------------- render results ----------------
const fmt = (v, d = 2) => Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

function render() {
  const d = state.data;
  $("warnings").innerHTML = d.warnings.map((w) => `<div class="banner warn">${w}</div>`).join("");
  renderKPIs(d.metrics);
  renderMetrics(d.metrics);
  $("notes").innerHTML = d.notes.map((n) => `<li>${n}</li>`).join("");
  renderCharts(d);
  $("loop-PID").innerHTML = loopSVG("PID");
  $("loop-MPC").innerHTML = loopSVG("MPC");
  const n = d.series.PID.time_s.length;
  $("scrub").max = n - 1;
  pause();
  state.k = 0; state.shown = -1;
  showStep(0, true);
}

function renderKPIs(m) {
  const kpis = [
    ["Peak temperature", "Peak temperature (°C)", "°C", 2],
    ["Tracking RMSE", "RMSE (°C)", "°C", 3],
    ["Cooling energy", "Cooling energy (Wh)", "Wh", 1],
    ["Time above limit", "Time above limit (s)", "s", 0],
  ];
  $("kpis").innerHTML = kpis.map(([label, key, unit, dec]) => {
    const a = m.PID[key], b = m.MPC[key], max = Math.max(Math.abs(a), Math.abs(b), 1e-9);
    const row = (who, v) => `<div class="row"><span class="who ${who.toLowerCase()}">${who}</span>
      <div class="bar"><span style="width:${(100 * Math.abs(v) / max).toFixed(1)}%;background:${COLORS[who]}"></span></div>
      <span class="val">${fmt(v, dec)}<small>${unit}</small></span></div>`;
    return `<div class="kpi"><div class="label">${label}</div>${row("PID", a)}${row("MPC", b)}</div>`;
  }).join("");
}

function renderMetrics(m) {
  const keys = Object.keys(m.PID);
  const dec = (k) => (k.includes("(s)") ? 0 : k.includes("Wh") ? 2 : 3);
  $("metrics").innerHTML = `<thead><tr><th>Metric</th><th class="pid">PID</th><th class="mpc">MPC</th></tr></thead><tbody>${
    keys.map((k) => `<tr><td>${k}</td><td>${fmt(m.PID[k], dec(k))}</td><td>${fmt(m.MPC[k], dec(k))}</td></tr>`).join("")
  }</tbody>`;
}

// ---------------- charts ----------------
function baseLayout(title, ytitle) {
  const axis = { gridcolor: "#16212c", zerolinecolor: "#1d2b3a", linecolor: "#1d2b3a", tickcolor: "#1d2b3a" };
  return {
    title: { text: title, font: { size: 13, color: "#e6edf3" }, x: 0.01, xanchor: "left", y: 0.97 },
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "Inter, Segoe UI, system-ui, sans-serif", color: "#8b9bab", size: 11 },
    margin: { l: 56, r: 18, t: 44, b: 44 },
    xaxis: { ...axis, title: { text: "Time (s)" } },
    yaxis: { ...axis, title: { text: ytitle } },
    legend: { orientation: "h", x: 1, xanchor: "right", y: 1.14, font: { color: "#c9d4de" } },
    hovermode: "x unified",
    hoverlabel: { bgcolor: "#131e2a", bordercolor: "#1d2b3a", font: { color: "#e6edf3" } },
  };
}
const PLOT_CFG = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"] };
const cursor = (x) => ({ type: "line", xref: "x", yref: "paper", x0: x, x1: x, y0: 0, y1: 1, line: { color: "rgba(230,237,243,0.35)", width: 1 } });
const line = (s, y, name, color, extra = {}) => ({ x: s.time_s, y, name, type: "scatter", mode: "lines", line: { color, width: 2, ...extra } });

function renderCharts(d) {
  const P = d.series.PID, M = d.series.MPC, lim = d.config.simulation.temp_limit;

  const lt = baseLayout("Battery temperature", "Temperature (°C)");
  lt.shapes = [cursor(0)];
  Plotly.newPlot("chart-temp", [
    line(P, P.temp_C, "PID", COLORS.PID), line(M, M.temp_C, "MPC", COLORS.MPC),
    line(P, P.target_C, "Target", COLORS.target, { dash: "dash", width: 1.5 }),
    line(P, P.time_s.map(() => lim), "Limit", COLORS.limit, { dash: "dot", width: 1.5 }),
  ], lt, PLOT_CFG);

  const lu = baseLayout("Cooling command", "u (0–1)");
  lu.yaxis.range = [-0.05, 1.05];
  lu.shapes = [cursor(0)];
  Plotly.newPlot("chart-u", [line(P, P.u, "PID", COLORS.PID), line(M, M.u, "MPC", COLORS.MPC)], lu, PLOT_CFG);

  Plotly.newPlot("chart-power", [line(P, P.p_cool_W, "PID", COLORS.PID), line(M, M.p_cool_W, "MPC", COLORS.MPC)],
    baseLayout("Cooler electrical power  (P = Pmax·u³)", "Power (W)"), PLOT_CFG);

  const lh = baseLayout("Heat generation — same load for both", "Qgen (W)");
  lh.yaxis.rangemode = "tozero";
  lh.yaxis2 = { overlaying: "y", side: "right", title: { text: "Current (A)" }, showgrid: false, linecolor: "#1d2b3a",
    rangemode: "tozero", tickmode: "auto", nticks: 6 };
  lh.margin.r = 56;
  Plotly.newPlot("chart-heat", [
    { ...line(P, P.q_gen_W, "Qgen", "#a78bfa"), fill: "tozeroy", fillcolor: "rgba(167,139,250,0.10)" },
    { ...line(P, P.current_A, "Current", "#64748b", { dash: "dot" }), yaxis: "y2" },
  ], lh, PLOT_CFG);

  Plotly.newPlot("chart-energy", [line(P, P.energy_Wh, "PID", COLORS.PID), line(M, M.energy_Wh, "MPC", COLORS.MPC)],
    baseLayout("Cumulative cooling energy", "Energy (Wh)"), PLOT_CFG);

  const M0 = d.series.MPC;
  const lo = Math.min(...M0.temp_C, M0.target_C[0]) - 0.5, hi = Math.max(...M0.temp_C, lim) + 0.5;
  const lz = baseLayout("", "Temperature (°C)");
  lz.yaxis.range = [lo, hi];
  lz.xaxis.range = [0, M0.time_s[M0.time_s.length - 1] + d.config.simulation.dt];
  lz.yaxis2 = { overlaying: "y", side: "right", range: [-0.05, 1.05], tickmode: "linear", tick0: 0, dtick: 0.2,
    title: { text: "u (0–1)" }, showgrid: false, linecolor: "#1d2b3a" };
  lz.margin.r = 56;
  lz.hovermode = "closest";
  Plotly.newPlot("chart-horizon", horizonTraces(0), lz, PLOT_CFG);
}

function horizonTraces(k) {
  const d = state.data, M = d.series.MPC, dt = d.config.simulation.dt;
  const uPlan = d.mpc_plans.u[k], tPred = d.mpc_plans.T[k], now = M.time_s[k];
  const fut = [now, ...tPred.map((_, i) => now + dt * (i + 1))];
  return [
    { x: M.time_s.slice(0, k + 1), y: M.temp_C.slice(0, k + 1), name: "Actual T so far", mode: "lines", line: { color: COLORS.MPC, width: 2 } },
    { x: fut, y: [M.temp_C[k], ...tPred], name: "Predicted T̂ (horizon)", mode: "lines+markers",
      line: { color: COLORS.MPC, width: 2.5, dash: "dot" }, marker: { size: 4 } },
    { x: fut, y: [uPlan[0], ...uPlan], name: "Planned u", mode: "lines", line: { color: COLORS.plan, width: 2, shape: "hv" }, yaxis: "y2" },
    { x: [M.time_s[0], M.time_s[M.time_s.length - 1] + dt], y: [M.target_C[0], M.target_C[0]], name: "Target",
      mode: "lines", line: { color: COLORS.target, dash: "dash", width: 1.5 } },
  ];
}

function updateCharts(k) {
  const d = state.data, t = d.series.PID.time_s[k], M = d.series.MPC, dt = d.config.simulation.dt;
  const N = d.mpc_plans.u[k].length;
  const layout = $("chart-horizon").layout;
  layout.title = { text: `MPC at t = ${fmt(t, 0)} s — predicts ${N} steps ahead, applies only u₀ = ${fmt(d.mpc_plans.u[k][0], 2)}`,
    font: { size: 13, color: "#e6edf3" }, x: 0.01, xanchor: "left", y: 0.97 };
  layout.shapes = [
    { type: "rect", xref: "x", yref: "paper", x0: M.time_s[k], x1: M.time_s[k] + N * dt, y0: 0, y1: 1,
      fillcolor: "rgba(34,211,238,0.07)", line: { width: 0 } },
    cursor(M.time_s[k]),
  ];
  Plotly.react("chart-horizon", horizonTraces(k), layout, PLOT_CFG);
  Plotly.relayout("chart-temp", { "shapes[0].x0": t, "shapes[0].x1": t });
  Plotly.relayout("chart-u", { "shapes[0].x0": t, "shapes[0].x1": t });
}

// ---------------- control-loop diagram ----------------
function loopSVG(name) {
  const c = COLORS[name];
  const law = name === "PID" ? "Kp·e + Ki∫e + Kd·ė" : "min J over N steps";
  const arrows = {
    ref: "M92,95 H118", err: "M160,95 H205", u: "M355,95 H405", qc: "M535,95 H585",
    qg: "M673,10 V53", out: "M769,95 H812 V205 H527", fb: "M423,205 H140 V117",
  };
  const base = Object.entries(arrows).map(([k, d]) =>
    `<path d="${d}" fill="none" stroke="#2a3b4d" stroke-width="2.5" marker-end="url(#ah-${name})"/>`).join("");
  const flows = Object.entries(arrows).map(([k, d]) =>
    `<path id="${name}-flow-${k}" d="${d}" fill="none" stroke="${k === "qg" ? "#f87171" : c}" stroke-width="2.5" stroke-dasharray="5 9" stroke-linecap="round" opacity="0.9"/>`).join("");
  return `
<svg viewBox="0 0 840 245" role="img" aria-label="${name} closed-loop diagram">
  <defs>
    <marker id="ah-${name}" markerWidth="9" markerHeight="9" refX="7" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 z" fill="#3b5068"/></marker>
    <filter id="glow-${name}" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="6"/></filter>
  </defs>
  <text x="14" y="26" fill="${c}" font-size="15" font-weight="700">${name} loop</text>
  ${base}${flows}

  <rect x="10" y="73" width="82" height="44" rx="9" fill="#0e1620" stroke="#2a3b4d"/>
  <text x="51" y="91" fill="#8b9bab" font-size="11" text-anchor="middle">T target</text>
  <text id="${name}-tt" class="num" x="51" y="108" fill="#e6edf3" font-size="13" text-anchor="middle"></text>

  <circle cx="140" cy="95" r="20" fill="#0e1620" stroke="#3b5068" stroke-width="2"/>
  <text x="140" y="101" fill="#e6edf3" font-size="17" text-anchor="middle">Σ</text>
  <text x="114" y="84" fill="#8b9bab" font-size="12">+</text>
  <text x="148" y="133" fill="#8b9bab" font-size="14">−</text>
  <text x="182" y="84" fill="#8b9bab" font-size="11" text-anchor="middle">e (°C)</text>
  <text id="${name}-e" class="num" x="182" y="116" fill="#e6edf3" font-size="11.5" text-anchor="middle"></text>

  <rect x="207" y="60" width="148" height="70" rx="10" fill="#0e1620" stroke="${c}" stroke-width="2"/>
  <text x="281" y="88" fill="${c}" font-size="15" font-weight="700" text-anchor="middle">${name}</text>
  <text x="281" y="110" fill="#8b9bab" font-size="11" text-anchor="middle">${law}</text>
  <text x="380" y="84" fill="#8b9bab" font-size="11" text-anchor="middle">u</text>

  <rect x="407" y="60" width="128" height="70" rx="10" fill="#0e1620" stroke="#3b5068" stroke-width="2"/>
  <g id="${name}-fan">
    <circle cx="436" cy="95" r="17" fill="none" stroke="#2a3b4d" stroke-width="1.5"/>
    <path d="M436,95 C430,84 434,79 441,80 C444,86 440,92 436,95 Z" fill="${c}"/>
    <path d="M436,95 C430,84 434,79 441,80 C444,86 440,92 436,95 Z" fill="${c}" transform="rotate(120 436 95)"/>
    <path d="M436,95 C430,84 434,79 441,80 C444,86 440,92 436,95 Z" fill="${c}" transform="rotate(240 436 95)"/>
    <circle cx="436" cy="95" r="3" fill="#e6edf3"/>
  </g>
  <text x="494" y="80" fill="#8b9bab" font-size="11" text-anchor="middle">Cooler</text>
  <text id="${name}-u" class="num" x="494" y="99" fill="#e6edf3" font-size="14" font-weight="600" text-anchor="middle"></text>
  <text id="${name}-p" class="num" x="494" y="114" fill="#8b9bab" font-size="10.5" text-anchor="middle"></text>
  <rect x="417" y="121" width="108" height="4" rx="2" fill="#1d2b3a"/>
  <rect id="${name}-ubar" x="417" y="121" width="0" height="4" rx="2" fill="${c}"/>
  <text x="560" y="84" fill="#8b9bab" font-size="10.5" text-anchor="middle">Qcool (W)</text>
  <text id="${name}-qc" class="num" x="560" y="116" fill="#e6edf3" font-size="10.5" text-anchor="middle"></text>

  <rect id="${name}-glow" x="595" y="62" width="160" height="66" rx="12" filter="url(#glow-${name})" opacity="0.55"/>
  <rect id="${name}-bat" x="587" y="55" width="174" height="80" rx="12" stroke="#e6edf3" stroke-opacity="0.8" stroke-width="2"/>
  <rect x="761" y="82" width="8" height="26" rx="2" fill="#e6edf3" fill-opacity="0.8"/>
  <text x="674" y="83" fill="#06121a" font-size="12" font-weight="700" text-anchor="middle" opacity="0.8">BATTERY</text>
  <text id="${name}-T" class="num" x="674" y="113" fill="#06121a" font-size="22" font-weight="700" text-anchor="middle"></text>
  <text id="${name}-qg" class="num" x="664" y="22" fill="#fca5a5" font-size="11" text-anchor="end"></text>
  <text x="792" y="86" fill="#e6edf3" font-size="13" font-weight="600">T</text>

  <rect x="425" y="188" width="100" height="34" rx="9" fill="#0e1620" stroke="#3b5068" stroke-width="2"/>
  <text x="475" y="210" fill="#e6edf3" font-size="12.5" text-anchor="middle">Sensor</text>
  <text id="${name}-fb" class="num" x="282" y="225" fill="#8b9bab" font-size="11" text-anchor="middle"></text>
</svg>`;
}

// Hue blend: blue (target − 3) -> green (target) -> red (limit); passes through yellow/orange when warm.
function tempColor(T, target, limit) {
  const hot = Math.max(limit, target + 0.1);
  let hue;
  if (T <= target) hue = 215 - 75 * Math.min(Math.max((T - (target - 3)) / 3, 0), 1);
  else hue = 140 * (1 - Math.min((T - target) / (hot - target), 1));
  return `hsl(${hue.toFixed(0)}, 75%, 55%)`;
}

function updateLoop(name, k) {
  const s = state.data.series[name], cfg = state.data.config;
  const T = s.temp_C[k], Tt = s.target_C[k], u = s.u[k], e = T - Tt;
  const col = tempColor(T, Tt, cfg.simulation.temp_limit);
  $(`${name}-tt`).textContent = `${fmt(Tt, 1)} °C`;
  $(`${name}-e`).textContent = `${e >= 0 ? "+" : ""}${fmt(e, 2)}`;
  $(`${name}-u`).textContent = `u = ${fmt(u, 2)}`;
  $(`${name}-p`).textContent = `${fmt(s.p_cool_W[k], 0)} W`;
  $(`${name}-ubar`).setAttribute("width", (108 * u).toFixed(1));
  $(`${name}-qc`).textContent = Math.round(s.q_cool_W[k]).toString();
  $(`${name}-T`).textContent = `${fmt(T, 2)} °C`;
  $(`${name}-bat`).setAttribute("fill", col);
  $(`${name}-glow`).setAttribute("fill", col);
  $(`${name}-qg`).textContent = `Qgen = I²R = ${fmt(s.q_gen_W[k], 0)} W   (I = ${fmt(s.current_A[k], 0)} A)`;
  $(`${name}-fb`).textContent = `feedback: measured T = ${fmt(T, 2)} °C`;

  // flow speeds (px/s) scale with the signal they carry; fan speed with u
  const qMax = Math.max(1, cfg.battery.k_cool * 15);
  state.flows[name] = {
    speeds: { ref: 12, err: 8 + 40 * Math.min(Math.abs(e), 3), u: 6 + 70 * u, qc: 6 + 80 * Math.min(Math.abs(s.q_cool_W[k]) / qMax, 1),
      qg: 6 + 80 * Math.min(s.q_gen_W[k] / qMax, 1), out: 22, fb: 22 },
    fan: 900 * u,
    offsets: state.flows[name]?.offsets || {},
    angle: state.flows[name]?.angle || 0,
  };
}

function showStep(k, forceCharts = false) {
  const d = state.data;
  if (!d) return;
  state.shown = k;
  updateLoop("PID", k);
  updateLoop("MPC", k);
  $("scrub").value = k;
  $("clock").textContent = `t = ${fmt(d.series.PID.time_s[k], 0)} s`;
  const now = performance.now();
  if (forceCharts || now - state.lastChart > 70) {
    state.lastChart = now;
    updateCharts(k);
  }
}

// ---------------- playback & animation loop ----------------
function play() {
  const n = state.data.series.PID.time_s.length;
  if (state.k >= n - 1) state.k = 0;
  state.playing = true;
  $("play-icon").setAttribute("d", "M7 5h4v14H7zM13 5h4v14h-4z");
}
function pause() {
  state.playing = false;
  $("play-icon").setAttribute("d", "M8 5v14l11-7z");
}

let lastTs = performance.now();
function frame(ts) {
  const dt = Math.min((ts - lastTs) / 1000, 0.1);
  lastTs = ts;
  if (state.data) {
    if (state.playing) {
      const n = state.data.series.PID.time_s.length;
      state.k = Math.min(state.k + parseFloat($("speed").value) * dt, n - 1);
      const k = Math.floor(state.k);
      if (k !== state.shown) showStep(k, k === n - 1);
      if (k >= n - 1) pause();
    }
    for (const [name, f] of Object.entries(state.flows)) {
      for (const [id, v] of Object.entries(f.speeds)) {
        f.offsets[id] = ((f.offsets[id] || 0) - v * dt) % 1000;
        const el = $(`${name}-flow-${id}`);
        if (el) el.setAttribute("stroke-dashoffset", f.offsets[id].toFixed(1));
      }
      f.angle = (f.angle + f.fan * dt) % 360;
      const fan = $(`${name}-fan`);
      if (fan) fan.setAttribute("transform", `rotate(${f.angle.toFixed(1)} 436 95)`);
    }
  }
  requestAnimationFrame(frame);
}

// ---------------- downloads ----------------
function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ---------------- init ----------------
async function init() {
  const defaults = await (await fetch("/api/defaults")).json();
  buildForm(defaults);
  $("run").addEventListener("click", run);
  $("play").addEventListener("click", () => (state.playing ? pause() : play()));
  $("scrub").addEventListener("input", (e) => { pause(); state.k = +e.target.value; showStep(state.k, true); });
  $("dl-csv").addEventListener("click", () => download("ev_therm_results.csv", state.data.csv, "text/csv"));
  $("dl-json").addEventListener("click", () =>
    download("ev_therm_settings.json", JSON.stringify(state.data.config, null, 2), "application/json"));
  requestAnimationFrame(frame);
  if (new URLSearchParams(location.search).has("autorun")) run();   // e.g. /?autorun for demos
}
init();
