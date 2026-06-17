import { useEffect, useRef, useState } from "react";
import type { Role, TimelineEvent } from "../types";
import { classifyFrame } from "../api";
import { captureVideoFrame } from "../lib/capture";
import { newEvent } from "../lib/events";

interface Props {
  onAdd: (event: TimelineEvent) => void;
}

const POLL_MS = 1500;

/** Live mode: capture the screen, poll-classify, overlay the current position. */
export function LivePanel({ onAdd }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [running, setRunning] = useState(false);
  const [current, setCurrent] = useState<{ label: string; role: Role; conf: number } | null>(null);
  const startedAt = useRef<number>(0);

  const start = async () => {
    const stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
    streamRef.current = stream;
    if (videoRef.current) {
      videoRef.current.srcObject = stream;
      await videoRef.current.play();
    }
    startedAt.current = Date.now();
    setRunning(true);
  };

  const stop = () => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setRunning(false);
    setCurrent(null);
  };

  useEffect(() => () => stop(), []);

  useEffect(() => {
    if (!running) return;
    const id = setInterval(async () => {
      const video = videoRef.current;
      if (!video || video.videoWidth === 0) return;
      try {
        const r = await classifyFrame(await captureVideoFrame(video));
        setCurrent({ label: r.node_name ?? r.vicos_class, role: r.role as Role, conf: r.confidence });
      } catch {
        /* transient; keep polling */
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [running]);

  const logCurrent = () => {
    if (!current) return;
    const t = (Date.now() - startedAt.current) / 1000;
    onAdd(
      newEvent({
        source: "cv",
        label: current.label,
        role: current.role,
        start: t,
        end: t,
        confidence: current.conf,
      }),
    );
  };

  return (
    <div className="panel">
      <div className="row">
        {!running ? (
          <button onClick={() => void start()}>Start screen capture</button>
        ) : (
          <button className="danger" onClick={stop}>
            Stop
          </button>
        )}
        <button onClick={logCurrent} disabled={!current}>
          Log current → timeline
        </button>
      </div>
      <div className="live-stage">
        <video ref={videoRef} muted className="video" />
        {current && (
          <div className="overlay">
            {current.label} · {current.role || "—"} · {(current.conf * 100).toFixed(0)}%
          </div>
        )}
      </div>
    </div>
  );
}
