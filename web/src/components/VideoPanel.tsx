import { useRef, useState } from "react";
import type { Role, TimelineEvent } from "../types";
import { classifyFrame } from "../api";
import { captureVideoFrame } from "../lib/capture";
import { newEvent } from "../lib/events";

interface Props {
  onAdd: (event: TimelineEvent) => void;
}

/** Post-hoc mode: load a recorded match, scrub, classify frames or add manually. */
export function VideoPanel({ onAdd }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [src, setSrc] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [last, setLast] = useState<string>("");

  const loadFile = (file: File) => {
    setSrc(URL.createObjectURL(file));
    setLast("");
  };

  const classifyHere = async () => {
    const video = videoRef.current;
    if (!video) return;
    setBusy(true);
    try {
      const blob = await captureVideoFrame(video);
      const r = await classifyFrame(blob);
      setLast(`${r.node_name ?? r.vicos_class} (${(r.confidence * 100).toFixed(0)}%)`);
      onAdd(
        newEvent({
          source: "cv",
          label: r.node_name ?? r.vicos_class,
          type: r.node_type ?? "transition",
          role: r.role as Role,
          start: video.currentTime,
          end: video.currentTime,
          confidence: r.confidence,
        }),
      );
    } catch (err) {
      setLast(`error: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const addManual = () => {
    const t = videoRef.current?.currentTime ?? 0;
    onAdd(newEvent({ source: "manual", start: t, end: t }));
  };

  return (
    <div className="panel">
      <input
        type="file"
        accept="video/*"
        onChange={(e) => e.target.files?.[0] && loadFile(e.target.files[0])}
      />
      {src && (
        <>
          <video ref={videoRef} src={src} controls className="video" />
          <div className="row">
            <button onClick={classifyHere} disabled={busy}>
              {busy ? "Classifying…" : "Classify frame → add"}
            </button>
            <button onClick={addManual}>Add manual at playhead</button>
            {last && <span className="muted">last: {last}</span>}
          </div>
        </>
      )}
    </div>
  );
}
