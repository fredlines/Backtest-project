"""
Local web UI for the backtest engine.

"""

import base64
import importlib.util
import inspect
import io
import json
import os
import sys
import tempfile
import traceback
from contextlib import redirect_stdout
from http.server import HTTPServer, BaseHTTPRequestHandler

import matplotlib
matplotlib.use('Agg')  # must happen before report.py imports pyplot

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STRATEGIES_DIR = os.path.join(BASE_DIR, 'strategies')
sys.path.insert(0, BASE_DIR)

from engine import BacktestEngine, Config, Strategy  # noqa: E402
from data_utils import load_ohlc_csv, resample_ohlc  # noqa: E402
from report import trades_to_df, compute_stats, print_report, save_charts  # noqa: E402

PORT = 8765


# ------------------------------------------------------- strategy discovery

def discover_strategies() -> dict:
    """Scan strategies/ for Strategy subclasses. Returns
    {name: {'cls': class, 'params': {param: default}, 'timeframes': ..., 'execution_tf': ...}}"""
    found = {}
    for fname in sorted(os.listdir(STRATEGIES_DIR)):
        if not fname.endswith('.py') or fname.startswith('_'):
            continue
        mod_name = f"strategies.{fname[:-3]}"
        path = os.path.join(STRATEGIES_DIR, fname)
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            found[fname[:-3]] = {'error': f"failed to load: {e}"}
            continue
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, Strategy) and obj is not Strategy and obj.__module__ == mod_name:
                params = {}
                sig = inspect.signature(obj.__init__)
                for pname, p in sig.parameters.items():
                    if pname in ('self',):
                        continue
                    if p.default is not inspect.Parameter.empty:
                        params[pname] = p.default
                found[fname[:-3]] = {
                    'cls': obj,
                    'class_name': obj.__name__,
                    'params': params,
                    'timeframes': getattr(obj, 'required_timeframes', {'exec': '15min'}),
                    'execution_tf': getattr(obj, 'execution_tf', 'exec'),
                    'doc': (inspect.getdoc(mod) or inspect.getdoc(obj) or '').strip().split('\n')[0],
                }
    return found


def _jsonable_params(params: dict) -> dict:
    out = {}
    for k, v in params.items():
        if isinstance(v, tuple):
            v = list(v)
        out[k] = v
    return out


# --------------------------------------------------------------- run logic

def run_backtest(csv_text: str, strategy_name: str, params: dict, engine_cfg: dict) -> dict:
    strategies = discover_strategies()
    if strategy_name not in strategies:
        return {'ok': False, 'error': f"strategy '{strategy_name}' not found"}
    spec = strategies[strategy_name]
    if 'error' in spec:
        return {'ok': False, 'error': spec['error']}

    # data -> temp file -> loader (validates columns, sorts, converts tz)
    with tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False) as f:
        f.write(csv_text)
        tmp_csv = f.name
    try:
        base = load_ohlc_csv(tmp_csv)
    except Exception as e:
        os.unlink(tmp_csv)
        return {'ok': False, 'error': f"couldn't parse CSV: {e}. Expected columns: time,open,high,low,close (1-minute bars, GMT/UTC timestamps)."}
    os.unlink(tmp_csv)

    if len(base) < 500:
        return {'ok': False, 'error': f"only {len(base)} rows parsed: that's not enough data to backtest meaningfully. Check the file."}

    data = {key: resample_ohlc(base, rule) for key, rule in spec['timeframes'].items()}

    try:
        strategy = spec['cls'](**params)
    except TypeError as e:
        return {'ok': False, 'error': f"bad params for {strategy_name}: {e}"}

    cfg = Config(
        starting_equity=float(engine_cfg.get('starting_equity', 10000.0)),
        risk_pct_per_trade=float(engine_cfg.get('risk_pct', 1.0)) / 100.0,
        point_value_per_lot=float(engine_cfg.get('point_value', 1.0)),
        spread_points=float(engine_cfg.get('spread_points', 1.5)),
        slippage_points=float(engine_cfg.get('slippage_points', 0.5)),
    )

    try:
        eng = BacktestEngine(data=data, execution_tf=spec['execution_tf'], strategy=strategy, config=cfg)
        equity_df, trades = eng.run()
    except Exception:
        return {'ok': False, 'error': f"strategy crashed during run:\n{traceback.format_exc()}"}

    df = trades_to_df(trades)
    stats = compute_stats(df, equity_df, cfg.starting_equity)

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_report(stats, cfg, eng.counters, df, strategy_name)
    report_text = buf.getvalue()

    charts = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        save_charts(df, equity_df, tmpdir, using_sample=False, strategy_name=strategy_name)
        for chart in ('equity_curve', 'r_multiple_histogram', 'mae_mfe_scatter'):
            p = os.path.join(tmpdir, f'{chart}.png')
            if os.path.exists(p):
                with open(p, 'rb') as f:
                    charts[chart] = base64.b64encode(f.read()).decode()

    trade_log_csv = df.to_csv(index=False) if len(df) else ''

    date_range = f"{base['time'].iloc[0]:%Y-%m-%d} to {base['time'].iloc[-1]:%Y-%m-%d}"
    return {
        'ok': True,
        'report': report_text,
        'charts': charts,
        'trade_log_csv': trade_log_csv,
        'meta': {
            'rows': len(base), 'date_range': date_range,
            'total_trades': stats.get('total_trades', 0),
        },
    }


# ---------------------------------------------------------------- the page

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backtest Workbench</title>
<style>
  :root {
    --bg: #101318; --panel: #171b22; --panel-2: #1d222b; --line: #2a3140;
    --ink: #e8eaed; --muted: #8a93a3; --accent: #e8b44b; --accent-ink: #1a1405;
    --win: #4ade80; --loss: #f87171;
    --mono: "SF Mono", "Cascadia Code", "JetBrains Mono", Consolas, "Liberation Mono", monospace;
    --sans: -apple-system, "Segoe UI", system-ui, sans-serif;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink); font-family: var(--sans); font-size: 14px; }
  header { display: flex; align-items: baseline; gap: 12px; padding: 14px 20px; border-bottom: 1px solid var(--line); }
  header h1 { font-family: var(--mono); font-size: 15px; font-weight: 600; margin: 0; letter-spacing: .04em; }
  header .sub { color: var(--muted); font-size: 12px; }
  .wrap { display: grid; grid-template-columns: 340px 1fr; min-height: calc(100vh - 49px); }
  @media (max-width: 900px) { .wrap { grid-template-columns: 1fr; } }

  .rail { border-right: 1px solid var(--line); padding: 18px; display: flex; flex-direction: column; gap: 18px; }
  .field label { display: block; color: var(--muted); font-size: 11px; letter-spacing: .08em; text-transform: uppercase; margin-bottom: 6px; }
  select, textarea, input[type=number] {
    width: 100%; background: var(--panel); color: var(--ink); border: 1px solid var(--line);
    border-radius: 6px; padding: 8px 10px; font-family: var(--mono); font-size: 13px;
  }
  select:focus, textarea:focus, input:focus, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  textarea { resize: vertical; min-height: 96px; }
  .filebtn {
    display: flex; align-items: center; gap: 10px; width: 100%; background: var(--panel);
    border: 1px dashed var(--line); border-radius: 6px; padding: 10px 12px; color: var(--ink);
    cursor: pointer; font-family: var(--mono); font-size: 13px; text-align: left;
  }
  .filebtn:hover { border-color: var(--accent); }
  .filebtn .name { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .filebtn.loaded .name { color: var(--win); }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .runbtn {
    width: 100%; padding: 12px; border: 0; border-radius: 6px; background: var(--accent);
    color: var(--accent-ink); font-family: var(--mono); font-size: 14px; font-weight: 700;
    letter-spacing: .05em; cursor: pointer;
  }
  .runbtn:disabled { opacity: .45; cursor: not-allowed; }
  .minor { background: none; border: 1px solid var(--line); color: var(--muted); border-radius: 6px;
           padding: 8px 10px; font-family: var(--mono); font-size: 12px; cursor: pointer; width: 100%; }
  .minor:hover { color: var(--ink); border-color: var(--muted); }
  .tfnote { color: var(--muted); font-size: 12px; font-family: var(--mono); margin-top: 6px; }

  .main { padding: 18px 22px; overflow-x: auto; }
  .statusline { font-family: var(--mono); font-size: 12px; color: var(--muted); padding: 8px 12px;
                background: var(--panel); border: 1px solid var(--line); border-radius: 6px; margin-bottom: 16px; }
  .statusline.err { color: var(--loss); border-color: var(--loss); white-space: pre-wrap; }
  .statusline.ok { color: var(--win); }
  pre.report { background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
               padding: 16px; font-family: var(--mono); font-size: 12.5px; line-height: 1.55;
               overflow-x: auto; margin: 0 0 18px; }
  .charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 14px; margin-bottom: 18px; }
  .charts img { width: 100%; border: 1px solid var(--line); border-radius: 6px; background: #fff; }
  .empty { color: var(--muted); font-family: var(--mono); font-size: 13px; padding: 60px 0; text-align: center; }
  a.dl { color: var(--accent); font-family: var(--mono); font-size: 13px; }
  @media (prefers-reduced-motion: no-preference) {
    .runbtn.busy { animation: pulse 1.2s ease-in-out infinite; }
    @keyframes pulse { 50% { opacity: .55; } }
  }
</style>
</head>
<body>
<header>
  <h1>BACKTEST WORKBENCH</h1>
  <span class="sub">local engine &middot; strategies auto-discovered from /strategies</span>
</header>
<div class="wrap">
  <aside class="rail">
    <div class="field">
      <label for="dataFile">1 &middot; Data file</label>
      <button class="filebtn" id="dataBtn" type="button" onclick="document.getElementById('dataFile').click()">
        <span>&#128193;</span><span class="name" id="dataName">Choose CSV (time,open,high,low,close)</span>
      </button>
      <input type="file" id="dataFile" accept=".csv,.txt" hidden>
    </div>

    <div class="field">
      <label for="strategySel">2 &middot; Strategy</label>
      <select id="strategySel" onchange="onStrategyChange()"></select>
      <div class="tfnote" id="tfNote"></div>
      <div style="margin-top:10px">
        <button class="minor" type="button" onclick="document.getElementById('stratFile').click()">Upload new strategy .py&hellip;</button>
        <input type="file" id="stratFile" accept=".py" hidden>
      </div>
    </div>

    <div class="field">
      <label for="paramsBox">3 &middot; Strategy params (JSON)</label>
      <textarea id="paramsBox" spellcheck="false"></textarea>
    </div>

    <div class="field">
      <label>4 &middot; Engine settings</label>
      <div class="grid2">
        <div><label for="eq">Starting equity $</label><input id="eq" type="number" value="10000"></div>
        <div><label for="risk">Risk % / trade</label><input id="risk" type="number" value="1" step="0.1"></div>
        <div><label for="spread">Spread (pts)</label><input id="spread" type="number" value="1.5" step="0.1"></div>
        <div><label for="slip">Slippage (pts)</label><input id="slip" type="number" value="0.5" step="0.1"></div>
        <div><label for="pv">$ / point / lot</label><input id="pv" type="number" value="1" step="0.1"></div>
      </div>
    </div>

    <button class="runbtn" id="runBtn" onclick="run()" disabled>RUN BACKTEST</button>
  </aside>

  <main class="main">
    <div class="statusline" id="status">Load a data CSV to begin. Results will appear here.</div>
    <div id="results"><div class="empty">no run yet</div></div>
  </main>
</div>

<script>
let csvText = null;
let strategies = {};

async function loadStrategies(selectName) {
  const r = await fetch('/api/strategies');
  strategies = await r.json();
  const sel = document.getElementById('strategySel');
  sel.innerHTML = '';
  for (const name of Object.keys(strategies)) {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = strategies[name].error ? (name + ' (load error)') : name;
    sel.appendChild(opt);
  }
  if (selectName && strategies[selectName]) sel.value = selectName;
  onStrategyChange();
}

function onStrategyChange() {
  const name = document.getElementById('strategySel').value;
  const s = strategies[name];
  if (!s) return;
  if (s.error) {
    document.getElementById('paramsBox').value = '';
    document.getElementById('tfNote').textContent = s.error;
    return;
  }
  document.getElementById('paramsBox').value = JSON.stringify(s.params, null, 2);
  const tfs = Object.entries(s.timeframes).map(([k, v]) => k + ':' + v).join('  ');
  document.getElementById('tfNote').textContent = 'timeframes ' + tfs + '  \u00b7  executes on ' + s.execution_tf;
}

document.getElementById('dataFile').addEventListener('change', (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = () => {
    csvText = reader.result;
    const btn = document.getElementById('dataBtn');
    btn.classList.add('loaded');
    document.getElementById('dataName').textContent = f.name + '  (' + (f.size/1048576).toFixed(1) + ' MB)';
    document.getElementById('runBtn').disabled = false;
    setStatus('Data loaded: ' + f.name + '. Pick a strategy and run.');
  };
  reader.readAsText(f);
});

document.getElementById('stratFile').addEventListener('change', async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const code = await f.text();
  const r = await fetch('/api/upload_strategy', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({filename: f.name, code}),
  });
  const j = await r.json();
  if (!j.ok) { setStatus('Strategy upload failed: ' + j.error, 'err'); return; }
  await loadStrategies(j.name);
  setStatus('Strategy "' + j.name + '" added and selected. Only load code you trust: it runs on this machine.');
  e.target.value = '';
});

function setStatus(msg, cls) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.className = 'statusline' + (cls ? ' ' + cls : '');
}

async function run() {
  if (!csvText) { setStatus('No data file loaded.', 'err'); return; }
  let params;
  try { params = JSON.parse(document.getElementById('paramsBox').value || '{}'); }
  catch (err) { setStatus('Params box is not valid JSON: ' + err.message, 'err'); return; }

  const btn = document.getElementById('runBtn');
  btn.disabled = true; btn.classList.add('busy'); btn.textContent = 'RUNNING\u2026';
  setStatus('Running backtest\u2026 large files can take a minute.');

  const body = {
    csv: csvText,
    strategy: document.getElementById('strategySel').value,
    params,
    engine: {
      starting_equity: +document.getElementById('eq').value,
      risk_pct: +document.getElementById('risk').value,
      spread_points: +document.getElementById('spread').value,
      slippage_points: +document.getElementById('slip').value,
      point_value: +document.getElementById('pv').value,
    },
  };

  let j;
  try {
    const r = await fetch('/api/run', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    j = await r.json();
  } catch (err) {
    j = {ok: false, error: 'server error: ' + err.message};
  }
  btn.disabled = false; btn.classList.remove('busy'); btn.textContent = 'RUN BACKTEST';

  if (!j.ok) { setStatus(j.error, 'err'); return; }

  setStatus('Done. ' + j.meta.rows.toLocaleString() + ' rows (' + j.meta.date_range + '), ' + j.meta.total_trades + ' trades.', 'ok');

  let html = '<pre class="report">' + escapeHtml(j.report) + '</pre>';
  html += '<div class="charts">';
  for (const [name, b64] of Object.entries(j.charts)) {
    html += '<img alt="' + name + '" src="data:image/png;base64,' + b64 + '">';
  }
  html += '</div>';
  if (j.trade_log_csv) {
    const blob = new Blob([j.trade_log_csv], {type: 'text/csv'});
    html += '<a class="dl" download="trade_log.csv" href="' + URL.createObjectURL(blob) + '">&#8595; download trade_log.csv</a>';
  }
  document.getElementById('results').innerHTML = html;
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

loadStrategies();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj))

    def do_GET(self):
        if self.path == '/' or self.path.startswith('/index'):
            self._send(200, PAGE, 'text/html; charset=utf-8')
        elif self.path == '/api/strategies':
            out = {}
            for name, spec in discover_strategies().items():
                if 'error' in spec:
                    out[name] = {'error': spec['error']}
                else:
                    out[name] = {
                        'class_name': spec['class_name'],
                        'params': _jsonable_params(spec['params']),
                        'timeframes': spec['timeframes'],
                        'execution_tf': spec['execution_tf'],
                        'doc': spec['doc'],
                    }
            self._json(out)
        else:
            self._json({'ok': False, 'error': 'not found'}, 404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        try:
            payload = json.loads(self.rfile.read(length).decode())
        except Exception:
            self._json({'ok': False, 'error': 'bad request body'}, 400)
            return

        if self.path == '/api/run':
            result = run_backtest(
                csv_text=payload.get('csv', ''),
                strategy_name=payload.get('strategy', ''),
                params=payload.get('params', {}),
                engine_cfg=payload.get('engine', {}),
            )
            self._json(result)
        elif self.path == '/api/upload_strategy':
            fname = os.path.basename(payload.get('filename', ''))
            code = payload.get('code', '')
            if not fname.endswith('.py') or not fname[:-3].replace('_', '').isalnum():
                self._json({'ok': False, 'error': 'filename must be a simple .py name (letters/numbers/underscores)'})
                return
            dest = os.path.join(STRATEGIES_DIR, fname)
            if os.path.exists(dest):
                self._json({'ok': False, 'error': f'{fname} already exists. Rename your file; uploads never overwrite existing strategies.'})
                return
            with open(dest, 'w') as f:
                f.write(code)
            # verify it actually loads and contains a Strategy subclass
            found = discover_strategies()
            name = fname[:-3]
            if name not in found:
                os.unlink(dest)
                self._json({'ok': False, 'error': 'no Strategy subclass found in that file'})
                return
            if 'error' in found[name]:
                os.unlink(dest)
                self._json({'ok': False, 'error': found[name]['error']})
                return
            self._json({'ok': True, 'name': name})
        else:
            self._json({'ok': False, 'error': 'not found'}, 404)

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet


def main():
    server = HTTPServer(('127.0.0.1', PORT), Handler)
    print(f"Backtest Workbench running -> http://localhost:{PORT}")
    print("(Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == '__main__':
    main()
