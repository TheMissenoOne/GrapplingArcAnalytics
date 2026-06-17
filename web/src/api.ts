import type {
  ClassifyResponse,
  DetectResponse,
  Detection,
  ExportRequest,
  NodeOption,
  YouSide,
} from "./types";

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

/** Detect athlete positions (with boxes) in a frame — drives the overlay. */
export async function detectFrame(blob: Blob): Promise<DetectResponse> {
  const form = new FormData();
  form.append("file", blob, "frame.jpg");
  return jsonOrThrow<DetectResponse>(
    await fetch(`${BASE}/detect`, { method: "POST", body: form }),
  );
}

/** Persist a hand-labeled frame (bjj3 class + bbox + actor) to the dataset. */
export async function captureFrame(
  blob: Blob,
  opts: {
    detections: Detection[];
    you_side: YouSide;
    image_w: number;
    image_h: number;
    manual_position?: string | null;
    athlete?: string | null;
  },
): Promise<{
  path: string;
  record: unknown;
  you_entry: { label: string; type: string; role: string; actor: string } | null;
}> {
  const form = new FormData();
  form.append("file", blob, "frame.jpg");
  form.append("detections", JSON.stringify(opts.detections));
  form.append("you_side", opts.you_side);
  form.append("image_w", String(opts.image_w));
  form.append("image_h", String(opts.image_h));
  if (opts.manual_position) form.append("manual_position", opts.manual_position);
  if (opts.athlete) form.append("athlete", opts.athlete);
  return jsonOrThrow(await fetch(`${BASE}/capture`, { method: "POST", body: form }));
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
