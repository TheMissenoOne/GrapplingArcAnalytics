import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Detection, NodeOption, YouSide } from "../types";
import { captureFrame, detectFrame } from "../api";
import { captureVideoFrame } from "../lib/capture";
import { rankNodes } from "../lib/rank";

interface Props {
  nodes: NodeOption[];
}

interface Captured {
  id: string;
  summary: string;
}

const YOU = "#3b82f6"; // blue
const OPP = "#ef4444"; // red

/** Which side of the frame a detection's centre falls on. */
function sideOf(det: Detection, imageW: number): "left" | "right" {
  return det.x < imageW / 2 ? "left" : "right";
}

/**
 * Keyboard-driven manual annotation studio.
 *   Space — play/pause; on pause, classify the current frame (draw boxes)
 *   ← / →  — which side is You (left / right); overlay recolors
 *   Enter  — commit the labeled frame to the dataset
 * On a no-detection frame, a manual position input activates.
 */
export function AnnotatePanel({ nodes }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const manualInputRef = useRef<HTMLInputElement>(null);

  const [src, setSrc] = useState<string | null>(null);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [dims, setDims] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  const [youSide, setYouSide] = useState<YouSide>("left");
  const [manual, setManual] = useState(false);
  const [manualQuery, setManualQuery] = useState("");
  const [manualPosition, setManualPosition] = useState<string | null>(null);
  const [captured, setCaptured] = useState<Captured[]>([]);
  const [flash, setFlash] = useState("");

  useEffect(() => {
    if (!src) return;
    return () => URL.revokeObjectURL(src);
  }, [src]);

  const loadFile = (file: File) => {
    setSrc(URL.createObjectURL(file));
    setDetections([]);
    setManual(false);
    setManualPosition(null);
  };

  // ── Draw the detection overlay (boxes + YOU/OPP labels) ──
  const draw = useCallback(() => {
    const video = videoRef.current;
    const canvas = canvasRef.current;
    if (!video || !canvas) return;
    canvas.width = video.clientWidth;
    canvas.height = video.clientHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!dims.w || !dims.h) return;
    const sx = canvas.width / dims.w;
    const sy = canvas.height / dims.h;
    ctx.lineWidth = 3;
    ctx.font = "bold 14px system-ui";
    for (const det of detections) {
      const isYou = sideOf(det, dims.w) === youSide;
      const color = isYou ? YOU : OPP;
      const x = (det.x - det.width / 2) * sx;
      const y = (det.y - det.height / 2) * sy;
      const w = det.width * sx;
      const h = det.height * sy;
      ctx.strokeStyle = color;
      ctx.strokeRect(x, y, w, h);
      const label = `${isYou ? "YOU" : "OPP"} · ${det.vicos_class}`;
      ctx.fillStyle = color;
      ctx.fillRect(x, y - 18, ctx.measureText(label).width + 10, 18);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, x + 5, y - 5);
    }
  }, [detections, dims, youSide]);

  useEffect(() => {
    draw();
  }, [draw]);

  // ── Actions ──
  const classifyCurrent = useCallback(async () => {
    const video = videoRef.current;
    if (!video) return;
    try {
      const blob = await captureVideoFrame(video);
      const res = await detectFrame(blob);
      setDetections(res.detections);
      setDims({ w: res.image_w, h: res.image_h });
      if (res.detections.length === 0) {
        setManual(true);
        setFlash("No detection — type the position");
        setTimeout(() => manualInputRef.current?.focus(), 0);
      } else {
        setManual(false);
        setFlash(`${res.detections.length} detection(s)`);
      }
    } catch (err) {
      setFlash(`detect error: ${(err as Error).message}`);
    }
  }, []);

  const commit = useCallback(async () => {
    const video = videoRef.current;
    if (!video) return;
    if (detections.length === 0 && !manualPosition) {
      setFlash("Nothing to capture — classify or pick a position first");
      return;
    }
    try {
      const blob = await captureVideoFrame(video);
      const image_w = dims.w || video.videoWidth;
      const image_h = dims.h || video.videoHeight;
      await captureFrame(blob, {
        detections,
        you_side: youSide,
        image_w,
        image_h,
        manual_position: manualPosition,
      });
      const summary = manualPosition
        ? `${manualPosition} (manual)`
        : detections
            .map((d) => `${d.vicos_class}/${sideOf(d, dims.w) === youSide ? "YOU" : "OPP"}`)
            .join(" + ");
      setCaptured((prev) => [{ id: `${Date.now()}`, summary }, ...prev]);
      setManualPosition(null);
      setManual(false);
      setFlash("✓ captured");
    } catch (err) {
      setFlash(`capture error: ${(err as Error).message}`);
    }
  }, [detections, dims, youSide, manualPosition]);

  const togglePlay = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      void video.play();
    } else {
      video.pause();
      void classifyCurrent();
    }
  }, [classifyCurrent]);

  // ── Keyboard ──
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Don't hijack typing in the manual input.
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === " ") {
        e.preventDefault();
        togglePlay();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        setYouSide("left");
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        setYouSide("right");
      } else if (e.key === "Enter") {
        e.preventDefault();
        void commit();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [togglePlay, commit]);

  const manualResults = useMemo(() => rankNodes(nodes, manualQuery, 8), [nodes, manualQuery]);

  return (
    <div className="panel">
      <input
        type="file"
        accept="video/*"
        onChange={(e) => e.target.files?.[0] && loadFile(e.target.files[0])}
      />
      <p className="muted">
        <b>Space</b> classify · <b>←</b> you=left · <b>→</b> you=right · <b>Enter</b> capture ·
        you-side: <b style={{ color: YOU }}>{youSide}</b>
        {flash && ` · ${flash}`}
      </p>

      {src && (
        <div className="live-stage">
          <video ref={videoRef} src={src} controls className="video" onLoadedData={draw} />
          <canvas ref={canvasRef} className="overlay-canvas" />
        </div>
      )}

      {manual && (
        <div className="manual-box">
          <span className="muted">Type position:</span>
          <div className="node-picker">
            <input
              ref={manualInputRef}
              value={manualQuery}
              placeholder="search position…"
              onChange={(e) => setManualQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && manualResults[0]) {
                  setManualPosition(manualResults[0].name);
                  setManualQuery(manualResults[0].name);
                }
              }}
            />
            {manualQuery && !manualPosition && manualResults.length > 0 && (
              <ul className="node-picker-list">
                {manualResults.map((n) => (
                  <li
                    key={n.name}
                    onMouseDown={() => {
                      setManualPosition(n.name);
                      setManualQuery(n.name);
                    }}
                  >
                    <span>{n.name}</span>
                    <small>{n.en ?? n.type}</small>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {manualPosition && <span className="tag tag-cv">{manualPosition} — Enter to capture</span>}
        </div>
      )}

      <h2>Captured ({captured.length})</h2>
      {captured.length === 0 ? (
        <p className="muted">Nothing captured yet.</p>
      ) : (
        <ul className="capture-list">
          {captured.map((c, i) => (
            <li key={c.id}>
              <span className="idx">{captured.length - i}</span>
              {c.summary}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
