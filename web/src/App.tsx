import { useEffect, useState } from "react";
import type { NodeOption, Role, TimelineEvent } from "./types";
import { exportSession, getNodes } from "./api";
import { buildExportRequest } from "./lib/events";
import { Studio } from "./components/Studio";
import { ReviewTable } from "./components/ReviewTable";

export default function App() {
  const [nodes, setNodes] = useState<NodeOption[]>([]);
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [athlete, setAthlete] = useState("");
  const [youRole, setYouRole] = useState<Role>("top");
  const [difficulty, setDifficulty] = useState(3);
  const [intensity, setIntensity] = useState(3);
  const [notes, setNotes] = useState("");
  const [banner, setBanner] = useState<string>("");

  useEffect(() => {
    getNodes()
      .then(setNodes)
      .catch((err) =>
        setBanner(`Backend unreachable (${(err as Error).message}). Manual picker disabled.`),
      );
  }, []);

  const addEvent = (e: TimelineEvent) => setEvents((prev) => [...prev, e]);

  const doExport = async () => {
    const req = buildExportRequest(events, { youRole, difficulty, intensity, notes });
    try {
      const payload = await exportSession(req);
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "session.json";
      a.click();
      // Defer revoke: revoking synchronously after click can abort the download
      // before the browser fetches the blob: URL (Firefox/Safari, intermittent Chrome).
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (err) {
      setBanner(`Export failed: ${(err as Error).message}`);
    }
  };

  return (
    <div className="app">
      <header>
        <h1>GrapplingArc Vision</h1>
        <p className="muted">CV-assisted + manual match annotation → app session</p>
      </header>

      {banner && <div className="banner">{banner}</div>}

      <Studio nodes={nodes} athlete={athlete} onAdd={addEvent} />

      <section className="session-meta">
        <label className="grow">
          Athlete
          <input
            value={athlete}
            placeholder="name (builds graph)"
            onChange={(e) => setAthlete(e.target.value)}
          />
        </label>
        <label>
          You are
          <select value={youRole} onChange={(e) => setYouRole(e.target.value as Role)}>
            <option value="top">top</option>
            <option value="bottom">bottom</option>
          </select>
        </label>
        <label>
          Difficulty
          <input
            type="number"
            min={1}
            max={5}
            value={difficulty}
            onChange={(e) => setDifficulty(Number(e.target.value))}
          />
        </label>
        <label>
          Intensity
          <input
            type="number"
            min={1}
            max={5}
            value={intensity}
            onChange={(e) => setIntensity(Number(e.target.value))}
          />
        </label>
        <label className="grow">
          Notes
          <input value={notes} onChange={(e) => setNotes(e.target.value)} />
        </label>
      </section>

      <h2>Timeline ({events.length})</h2>
      <ReviewTable events={events} nodes={nodes} youRole={youRole} onChange={setEvents} />

      <div className="row">
        <button className="primary" disabled={events.length === 0} onClick={() => void doExport()}>
          Export SessionPayload JSON
        </button>
        <button className="link" disabled={events.length === 0} onClick={() => setEvents([])}>
          Clear
        </button>
      </div>
    </div>
  );
}
