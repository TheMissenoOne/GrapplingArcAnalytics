export type Role = "" | "top" | "bottom";
export type Actor = "you" | "partner";

export interface NodeOption {
  name: string;
  type: string;
  en?: string | null;
}

export interface ClassifyResponse {
  vicos_class: string;
  confidence: number;
  role: string;
  node_name: string | null;
  node_type: string | null;
  ok: boolean;
}

/** One reviewed event on the match timeline (CV-detected or manual). */
export interface TimelineEvent {
  id: string;
  label: string;
  type: string;
  role: Role;
  start: number; // seconds
  end: number; // seconds
  successful: boolean;
  source: "cv" | "manual";
  confidence?: number;
}

export interface ExportEvent {
  label: string;
  type: string;
  role: string;
  successful: boolean;
  setup?: string | null;
}

export interface ExportRequest {
  events: ExportEvent[];
  you_role: string;
  difficulty: number;
  intensity: number;
  notes: string;
  outcome?: string | null;
}
