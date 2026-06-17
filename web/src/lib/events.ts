import type { ExportRequest, NodeOption, Role, TimelineEvent } from "../types";

let _seq = 0;

/** Create a TimelineEvent with a unique id and sensible defaults. */
export function newEvent(partial: Partial<TimelineEvent> = {}): TimelineEvent {
  _seq += 1;
  return {
    id: `e${Date.now()}_${_seq}`,
    label: "",
    type: "control",
    role: "",
    start: 0,
    end: 0,
    successful: true,
    source: "manual",
    ...partial,
  };
}

/** Case-insensitive search over node name + English translation. */
export function filterNodes(nodes: NodeOption[], query: string): NodeOption[] {
  const q = query.trim().toLowerCase();
  if (!q) return nodes;
  return nodes.filter(
    (n) =>
      n.name.toLowerCase().includes(q) || (n.en ?? "").toLowerCase().includes(q),
  );
}

/** Shape the timeline (sorted by start) into the backend /export request body. */
export function buildExportRequest(
  events: TimelineEvent[],
  opts: {
    youRole: Role;
    difficulty?: number;
    intensity?: number;
    notes?: string;
    outcome?: string | null;
  },
): ExportRequest {
  const sorted = [...events].sort((a, b) => a.start - b.start);
  return {
    events: sorted
      .filter((e) => e.label.trim().length > 0)
      .map((e) => ({
        label: e.label,
        type: e.type,
        role: e.role,
        successful: e.successful,
      })),
    you_role: opts.youRole || "top",
    difficulty: opts.difficulty ?? 3,
    intensity: opts.intensity ?? 3,
    notes: opts.notes ?? "",
    outcome: opts.outcome ?? null,
  };
}

/** Resolve a ViCoS role to the app actor, given which role you played. */
export function roleToActor(role: Role, youRole: Role): "you" | "partner" {
  if (!role) return "you";
  return role === youRole ? "you" : "partner";
}
