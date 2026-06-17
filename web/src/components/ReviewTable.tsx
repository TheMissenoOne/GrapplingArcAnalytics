import type { NodeOption, Role, TimelineEvent } from "../types";
import { roleToActor } from "../lib/events";
import { NodePicker } from "./NodePicker";

interface Props {
  events: TimelineEvent[];
  nodes: NodeOption[];
  youRole: Role;
  onChange: (events: TimelineEvent[]) => void;
}

const ROLES: Role[] = ["", "top", "bottom"];

/** Editable timeline: relabel, set role, toggle landed, reorder by time, delete. */
export function ReviewTable({ events, nodes, youRole, onChange }: Props) {
  const sorted = [...events].sort((a, b) => a.start - b.start);

  const update = (id: string, patch: Partial<TimelineEvent>) =>
    onChange(events.map((e) => (e.id === id ? { ...e, ...patch } : e)));
  const remove = (id: string) => onChange(events.filter((e) => e.id !== id));

  if (sorted.length === 0) {
    return <p className="muted">No events yet. Classify a frame or add one manually.</p>;
  }

  return (
    <table className="review-table">
      <thead>
        <tr>
          <th>t (s)</th>
          <th>Technique</th>
          <th>Role → actor</th>
          <th>Landed</th>
          <th>Source</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {sorted.map((e) => (
          <tr key={e.id}>
            <td>
              <input
                type="number"
                step="0.1"
                value={e.start}
                onChange={(ev) => update(e.id, { start: Number(ev.target.value) })}
              />
            </td>
            <td>
              <NodePicker
                nodes={nodes}
                value={e.label}
                onPick={(n) => update(e.id, { label: n.name, type: n.type })}
              />
              <small className="muted">{e.type}</small>
            </td>
            <td>
              <select
                value={e.role}
                onChange={(ev) => update(e.id, { role: ev.target.value as Role })}
              >
                {ROLES.map((r) => (
                  <option key={r || "none"} value={r}>
                    {r || "—"}
                  </option>
                ))}
              </select>
              <small className="muted"> {roleToActor(e.role, youRole)}</small>
            </td>
            <td>
              <input
                type="checkbox"
                checked={e.successful}
                onChange={(ev) => update(e.id, { successful: ev.target.checked })}
              />
            </td>
            <td>
              <span className={`tag tag-${e.source}`}>{e.source}</span>
              {e.confidence != null && <small className="muted"> {(e.confidence * 100).toFixed(0)}%</small>}
            </td>
            <td>
              <button className="link danger" onClick={() => remove(e.id)}>
                ✕
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
