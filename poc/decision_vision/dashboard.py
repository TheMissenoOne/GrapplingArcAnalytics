"""Local read-only dashboard for Decision Vision POC progress/results."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

DEFAULT_ROOT = Path("data/cv_decision_poc")
DATA_ROOT = Path(
    os.environ.get("DECISION_VISION_DATA_ROOT", str(DEFAULT_ROOT))
).resolve()

app = FastAPI(
    title="Decision Vision POC Dashboard",
    docs_url=None,
    redoc_url=None,
)


def _safe_child(path: Path) -> Path:
    resolved = path.resolve()
    if DATA_ROOT != resolved and DATA_ROOT not in resolved.parents:
        raise HTTPException(status_code=400, detail="Path outside data root")
    return resolved


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_csv(path: Path, limit: int = 5000) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for index, row in enumerate(reader):
            if index >= limit:
                break
            rows.append(dict(row))
        return rows


def _run_dirs() -> list[Path]:
    candidates: set[Path] = set()
    if (DATA_ROOT / "progress.json").exists():
        candidates.add(DATA_ROOT)

    for path in DATA_ROOT.rglob("progress.json"):
        candidates.add(path.parent)

    for filename in (
        "role_timeline_report.json",
        "report.json",
        "training_report.json",
    ):
        for path in DATA_ROOT.rglob(filename):
            candidates.add(path.parent)

    return sorted(
        candidates,
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )


def _relative(path: Path) -> str:
    value = str(path.relative_to(DATA_ROOT))
    return value if value != "." else "root"


def _pipeline_guess(path: Path) -> str:
    if (path / "role_timeline_report.json").exists():
        return "role_timeline"
    if (path / "report.json").exists():
        return "temporal_probe"
    if (path / "training_report.json").exists():
        return "rgb_baseline"
    return "unknown"


def _summary(path: Path) -> dict[str, Any]:
    progress = _read_json(path / "progress.json")
    return {
        "run": _relative(path),
        "pipeline": progress.get("pipeline") if progress else _pipeline_guess(path),
        "status": progress.get("status") if progress else "completed",
        "phase": progress.get("phase") if progress else "done",
        "percent": progress.get("percent") if progress else 100.0,
        "message": progress.get("message") if progress else "",
        "updated_at": progress.get("updated_at") if progress else None,
        "metrics": progress.get("metrics", {}) if progress else {},
        "role_report": _read_json(path / "role_timeline_report.json"),
        "temporal_report": _read_json(path / "report.json"),
        "training_report": _read_json(path / "training_report.json"),
    }


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return DASHBOARD_HTML


@app.get("/api/runs")
def runs() -> dict[str, Any]:
    return {
        "data_root": str(DATA_ROOT),
        "runs": [_summary(path) for path in _run_dirs()],
    }


@app.get("/api/run/{run_path:path}")
def run_detail(run_path: str) -> dict[str, Any]:
    path = _safe_child(DATA_ROOT / run_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Run not found")

    return {
        **_summary(path),
        "role_samples": _read_csv(path / "role_samples.csv", 10000),
        "top_bottom_segments": _read_csv(path / "top_bottom_segments.csv", 5000),
        "athlete_state_segments": _read_csv(path / "athlete_state_segments.csv", 10000),
        "samples": _read_csv(path / "samples.csv", 5000),
    }


DASHBOARD_HTML = '\n<div class="shell">\n  <header>\n    <div>\n      <h1>Decision Vision POC</h1>\n      <p>Live progress, top/bottom timeline, athlete state and probe results.</p>\n    </div>\n    <div class="actions">\n      <span id="connection">connecting...</span>\n      <button id="refresh" type="button">Refresh</button>\n    </div>\n  </header>\n\n  <div class="layout">\n    <aside>\n      <div class="aside-head"><strong>Runs</strong><span id="run-count"></span></div>\n      <div id="runs"></div>\n    </aside>\n\n    <main>\n      <section class="metrics">\n        <div><small>Status</small><b id="metric-status">-</b></div>\n        <div><small>Progress</small><b id="metric-progress">-</b></div>\n        <div><small>Role resolved</small><b id="metric-role">-</b></div>\n        <div><small>Role switches</small><b id="metric-switches">-</b></div>\n      </section>\n\n      <section class="panel">\n        <div class="section-head">\n          <div><strong>Current progress</strong><p id="phase-message">Select a run.</p></div>\n          <span id="phase">-</span>\n        </div>\n        <div class="bar"><div id="progress-bar"></div></div>\n        <div id="progress-detail" class="muted"></div>\n      </section>\n\n      <section class="panel">\n        <div class="section-head">\n          <div><strong>Top / bottom timeline</strong>\n            <p>Color follows persistent visual identity, not role.</p>\n          </div>\n        </div>\n        <div id="timeline-empty" class="empty">No role timeline available.</div>\n        <div id="timeline-wrap" class="hidden scroll"><div id="timeline"></div></div>\n      </section>\n\n      <section class="two-col">\n        <div class="panel">\n          <strong>Athlete state segments</strong>\n          <div id="athlete-states" class="stack"><div class="empty">No athlete-state data.</div></div>\n        </div>\n        <div class="panel">\n          <strong>Model / probe results</strong>\n          <div id="results" class="stack"><div class="empty">No result report.</div></div>\n        </div>\n      </section>\n    </main>\n  </div>\n</div>\n\n<style>\n:root{\n  color-scheme:dark;\n  --bg:#071018;--panel:#0d1722;--panel2:#101d29;--line:#243344;\n  --text:#e7edf4;--muted:#8fa1b4;--accent:#22d3ee;--green:#34d399;\n  --red:#fb7185;--a:#0e7490;--b:#4f46e5;\n}\n*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,sans-serif}\n.shell{max-width:1280px;margin:auto;padding:24px}\nheader,.actions,.aside-head,.section-head{display:flex;align-items:center;justify-content:space-between;gap:12px}\nh1{margin:0;font-size:24px} p{margin:3px 0;color:var(--muted)}\nbutton{background:var(--panel2);border:1px solid var(--line);color:var(--text);padding:8px 12px;border-radius:8px;cursor:pointer}\n#connection,#phase{background:var(--panel2);padding:5px 9px;border-radius:999px;font-size:12px;color:var(--muted)}\n.layout{display:grid;grid-template-columns:290px 1fr;gap:18px;margin-top:22px}\naside,.panel,.metrics>div{background:var(--panel);border:1px solid var(--line);border-radius:12px}\naside{padding:12px;align-self:start}.aside-head{margin-bottom:10px}\n.run{width:100%;text-align:left;margin:0 0 8px;padding:10px;background:#09131d}.run.active{border-color:var(--accent)}\n.run-top{display:flex;justify-content:space-between;gap:8px}.run small{display:block;color:var(--muted)}\n.mini{height:5px;background:#1d2b39;border-radius:99px;margin-top:8px;overflow:hidden}.mini>i{display:block;height:100%;background:var(--accent)}\nmain{display:flex;flex-direction:column;gap:18px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}\n.metrics>div{padding:14px}.metrics small{display:block;color:var(--muted);text-transform:uppercase;font-size:11px}.metrics b{font-size:20px}\n.panel{padding:15px}.bar{height:10px;background:#1d2b39;border-radius:99px;overflow:hidden;margin-top:12px}.bar>div{height:100%;background:var(--accent);width:0;transition:width .25s}\n.muted,.empty{color:var(--muted);font-size:12px;margin-top:8px}.empty{padding:24px;text-align:center}.hidden{display:none}.scroll{overflow-x:auto}\n.timeline-grid{min-width:720px;display:grid;grid-template-columns:76px 1fr;gap:8px 10px;align-items:center;margin-top:12px}\n.track{position:relative;height:38px;background:#192635;border-radius:8px;overflow:hidden}.seg{position:absolute;top:4px;bottom:4px;border-radius:6px;padding:5px 7px;font-size:11px;white-space:nowrap;overflow:hidden}.seg.a{background:var(--a)}.seg.b{background:var(--b)}.seg.u{background:#475569}\n.axis{position:relative;height:18px}.tick{position:absolute;transform:translateX(-50%);font-size:10px;color:var(--muted)}\n.two-col{display:grid;grid-template-columns:1fr 1fr;gap:18px}.stack{display:flex;flex-direction:column;gap:8px;margin-top:12px}\n.state{background:#09131d;border:1px solid var(--line);border-radius:8px;padding:10px}.state-row{display:flex;justify-content:space-between;gap:10px;font-size:12px;margin-top:4px}\ntable{width:100%;border-collapse:collapse;margin-top:8px;font-size:12px}th,td{text-align:left;padding:7px;border-bottom:1px solid var(--line)}th{color:var(--muted)}\n@media(max-width:850px){.layout{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}.two-col{grid-template-columns:1fr}}\n</style>\n\n<script>\n(() => {\n  const el=id=>document.getElementById(id);\n  let selected=null,runs=[];\n\n  const esc=v=>String(v??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");\n  async function get(url){const r=await fetch(url,{cache:"no-store"});if(!r.ok)throw new Error(r.status);return r.json()}\n  const percent=v=>{const n=Number(v);return Number.isFinite(n)?`${(n<=1?n*100:n).toFixed(1)}%`:"-"};\n\n  function renderRuns(items){\n    runs=items;el("run-count").textContent=items.length;\n    if(!selected||!items.some(x=>x.run===selected)) selected=items[0]?.run||null;\n    el("runs").innerHTML=items.length?items.map(r=>`\n      <button class="run ${r.run===selected?"active":""}" data-run="${esc(r.run)}">\n        <div class="run-top"><span>${esc(r.run)}</span><small>${esc(r.status)}</small></div>\n        <small>${esc(r.pipeline)}</small>\n        <div class="mini"><i style="width:${Number(r.percent||0)}%"></i></div>\n      </button>`).join(""):`<div class="empty">No runs found.</div>`;\n    document.querySelectorAll("[data-run]").forEach(b=>b.addEventListener("click",async()=>{\n      selected=b.dataset.run;renderRuns(runs);await detail();\n    }));\n  }\n\n  function renderProgress(d){\n    const p=Number(d.percent??100);el("metric-status").textContent=d.status||"-";\n    el("metric-progress").textContent=`${p.toFixed(1)}%`;el("phase").textContent=d.phase||"-";\n    el("phase-message").textContent=d.message||d.pipeline||"";el("progress-bar").style.width=`${Math.max(0,Math.min(100,p))}%`;\n    const rr=d.role_report||{};el("metric-role").textContent=rr.role_resolved_rate!=null?percent(rr.role_resolved_rate):"-";\n    el("metric-switches").textContent=rr.role_switch_events??"-";\n    el("progress-detail").textContent=d.updated_at?`updated ${new Date(d.updated_at).toLocaleTimeString()}`:"";\n  }\n\n  function renderTimeline(d){\n    const rows=d.top_bottom_segments||[];\n    if(!rows.length){el("timeline-empty").classList.remove("hidden");el("timeline-wrap").classList.add("hidden");return}\n    el("timeline-empty").classList.add("hidden");el("timeline-wrap").classList.remove("hidden");\n    const t0=Math.min(...rows.map(r=>+r.start)),t1=Math.max(...rows.map(r=>+r.end)),dur=Math.max(1,t1-t0);\n    const lane=which=>rows.map(r=>{\n      const l=100*(+r.start-t0)/dur,w=Math.max(.3,100*(+r.end-+r.start)/dur),track=r[`${which}_track_id`]||"";\n      const cls=track==="track_0"?"a":track==="track_1"?"b":"u",who=r[`${which}_athlete_id`]||track||"unknown";\n      return `<div class="seg ${cls}" style="left:${l}%;width:${w}%" title="${esc(r.position)} ${esc(who)}">${esc(r.position)} · ${esc(who)}</div>`;\n    }).join("");\n    const ticks=[0,.25,.5,.75,1].map(f=>`<span class="tick" style="left:${f*100}%">${(t0+dur*f).toFixed(0)}s</span>`).join("");\n    el("timeline").innerHTML=`<div class="timeline-grid"><div>Top</div><div class="track">${lane("top")}</div><div>Bottom</div><div class="track">${lane("bottom")}</div><div></div><div class="axis">${ticks}</div></div>`;\n  }\n\n  function renderAthletes(d){\n    const rows=d.athlete_state_segments||[];if(!rows.length){el("athlete-states").innerHTML=\'<div class="empty">No athlete-state data.</div>\';return}\n    const groups={};rows.forEach(r=>{const k=r.athlete_id||r.track_id||"unknown";(groups[k]??=[]).push(r)});\n    el("athlete-states").innerHTML=Object.entries(groups).map(([k,segs])=>`<div class="state"><strong>${esc(k)}</strong>${segs.slice(0,18).map(r=>`<div class="state-row"><span>${esc(r.role)} · ${esc(r.position)}</span><span>${(+r.start).toFixed(1)}-${(+r.end).toFixed(1)}s</span></div>`).join("")}</div>`).join("");\n  }\n\n  function renderResults(d){\n    if(d.temporal_report?.experiments){\n      const rows=[];for(const [f,heads] of Object.entries(d.temporal_report.experiments))for(const [h,m] of Object.entries(heads||{}))rows.push([f,h,m.macro_f1,m.accuracy,m.evaluated_samples,m.skipped_unseen_class_samples]);\n      el("results").innerHTML=`<table><thead><tr><th>Features</th><th>Head</th><th>F1</th><th>Acc.</th><th>Coverage</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td><td>${r[2]??"-"}</td><td>${r[3]??"-"}</td><td>${r[4]??"-"} / unseen ${r[5]??0}</td></tr>`).join("")}</tbody></table>`;return;\n    }\n    if(d.training_report){el("results").innerHTML=`<div class="state"><div>Best mean macro-F1 <strong>${d.training_report.best_mean_macro_f1??"-"}</strong></div><div>Matches ${d.training_report.matches??"-"}</div></div>`;return}\n    el("results").innerHTML=\'<div class="empty">No result report.</div>\';\n  }\n\n  async function detail(){if(!selected)return;const d=await get(`/api/run/${encodeURI(selected)}`);renderProgress(d);renderTimeline(d);renderAthletes(d);renderResults(d)}\n  async function refresh(){try{const p=await get("/api/runs");el("connection").textContent="live";renderRuns(p.runs||[]);await detail()}catch(e){el("connection").textContent="offline"}}\n  el("refresh").addEventListener("click",refresh);refresh();setInterval(refresh,1500);\n})();\n</script>\n'  # noqa: E501
