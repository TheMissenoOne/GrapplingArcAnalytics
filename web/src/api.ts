import type { ClassifyResponse, ExportRequest, NodeOption } from "./types";

const BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

export async function getNodes(): Promise<NodeOption[]> {
  return jsonOrThrow<NodeOption[]>(await fetch(`${BASE}/nodes`));
}

/** Classify a single frame (PNG/JPEG blob) → detected position. */
export async function classifyFrame(blob: Blob): Promise<ClassifyResponse> {
  const form = new FormData();
  form.append("file", blob, "frame.png");
  return jsonOrThrow<ClassifyResponse>(
    await fetch(`${BASE}/classify`, { method: "POST", body: form }),
  );
}

/** Build a GrapplingArc SessionPayload from the reviewed timeline. */
export async function exportSession(req: ExportRequest): Promise<unknown> {
  return jsonOrThrow<unknown>(
    await fetch(`${BASE}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  );
}

export { BASE as API_BASE };
