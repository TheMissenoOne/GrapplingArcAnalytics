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
  const inFlight = useRef(false);
  const [running, setRunning] = useState(false);
  const [current, setCurrent] = useState<{ label: string; role: Role; conf: number } | null>(null);
  const [error, setError] = useState<string>("");
  const startedAt = useRef<number>(0);

  const start = async () => {
    setError("");
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      startedAt.current = Date.now();
      setRunning(true);
    } catch (err) {
      // User cancelled the share dialog or denied permission, etc.
      setError(`Could not start capture: ${(err as Error).message}`);
    }
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
      // Skip if a request is still running so slow responses can't pile up or land
      // out of order (an older frame overwriting a newer detection).
      if (!video || video.videoWidth === 0 || inFlight.current) return;
      inFlight.current = true;
      try {
        const r = await classifyFrame(await captureVideoFrame(video));
        setCurrent({ label: r.node_name ?? r.vicos_class, role: r.role as Role, conf: r.confidence });
      } catch {
        /* transient; keep polling */
      } finally {
        inFlight.current = false;
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
      {error && <div className="banner">{error}</div>}
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
