import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Detection, NodeOption, TimelineEvent, YouSide } from "../types";
import { captureFrame, detectFrame } from "../api";
import { captureVideoFrame } from "../lib/capture";
import { newEvent } from "../lib/events";
import { isYou, scaleBox } from "../lib/overlay";
import { rankNodes } from "../lib/rank";

interface Props {
  nodes: NodeOption[];
  athlete: string;
  onAdd: (event: TimelineEvent) => void;
}

type Source = "video" | "screen";

const YOU = "#3b82f6"; // blue
const OPP = "#ef4444"; // red

/**
 * Unified annotation studio: upload a video OR share the screen, then annotate by keyboard.
 *   Space — play/pause; on pause, classify the current frame (draw boxes)
 *   ← / →  — which side is You (left / right); overlay recolors
 *   Enter  — commit: save a dataset frame AND add an editable timeline row
 * No-detection / detect failure → type the position (free text), Enter captures.
 */
export function Studio({ nodes, athlete, onAdd }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const manualInputRef = useRef<HTMLInputElement>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const [source, setSource] = useState<Source>("video");
  const [src, setSrc] = useState<string | null>(null);
  const [hasScreen, setHasScreen] = useState(false);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [dims, setDims] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  const [youSide, setYouSide] = useState<YouSide>("left");
  const [manual, setManual] = useState(false);
  const [manualQuery, setManualQuery] = useState("");
  const [flash, setFlash] = useState("");

  const stopStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    setHasScreen(false);
  }, []);

  useEffect(() => () => stopStream(), [stopStream]);
  useEffect(() => {
    if (!src) return;
    return () => URL.revokeObjectURL(src);
  }, [src]);

  const reset = () => {
    setDetections([]);
    setManual(false);
    setManualQuery("");
  };

  const loadFile = (file: File) => {
    stopStream();
    setSource("video");
    if (videoRef.current) videoRef.current.srcObject = null;
    setSrc(URL.createObjectURL(file));
    reset();
  };

  const shareScreen = async () => {
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
      streamRef.current = stream;
      setSource("screen");
      setSrc(null);
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setHasScreen(true);
      reset();
    } catch (err) {
      setFlash(`screen share cancelled: ${(err as Error).message}`);
    }
  };

  // ── Overlay ──
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
    ctx.lineWidth = 3;
    ctx.font = "bold 14px system-ui";
    for (const det of detections) {
      const mine = isYou(det.x, dims.w, youSide);
      const color = mine ? YOU : OPP;
      const b = scaleBox(det, dims.w, dims.h, canvas.width, canvas.height);
      ctx.strokeStyle = color;
      ctx.strokeRect(b.x, b.y, b.w, b.h);
      const label = `${mine ? "YOU" : "OPP"} · ${det.vicos_class}`;
      ctx.fillStyle = color;
      ctx.fillRect(b.x, b.y - 18, ctx.measureText(label).width + 10, 18);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, b.x + 5, b.y - 5);
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
      const res = await detectFrame(await captureVideoFrame(video));
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
      setDetections([]);
      setManual(true);
      setFlash(`detect failed (${(err as Error).message}) — type the position`);
      setTimeout(() => manualInputRef.current?.focus(), 0);
    }
  }, []);

  const commit = useCallback(
    async (positionOverride?: string) => {
      const video = videoRef.current;
      if (!video) return;
      const pos = positionOverride ?? null;
      if (detections.length === 0 && !pos) {
        setFlash("Nothing to capture — classify or type a position first");
        return;
      }
      try {
        const blob = await captureVideoFrame(video);
        const image_w = dims.w || video.videoWidth;
        const image_h = dims.h || video.videoHeight;
        const res = await captureFrame(blob, {
          detections,
          you_side: youSide,
          image_w,
          image_h,
          manual_position: pos,
          athlete: athlete.trim() || undefined,
        });
        if (res.you_entry) {
          onAdd(
            newEvent({
              source: "cv",
              label: res.you_entry.label,
              type: res.you_entry.type,
              role: (res.you_entry.role as TimelineEvent["role"]) || "",
              start: video.currentTime || 0,
            }),
          );
        }
        setManualQuery("");
        setManual(false);
        setFlash(res.graph_node ? `✓ ${res.graph_node}` : "✓ captured");
      } catch (err) {
        setFlash(`capture error: ${(err as Error).message}`);
      }
    },
    [detections, dims, youSide, athlete, onAdd],
  );

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
  const ready = source === "video" ? !!src : hasScreen;

  return (
    <div className="panel">
      <div className="row">
        <button className={source === "video" ? "active" : ""} onClick={() => setSource("video")}>
          Upload video
        </button>
        <button onClick={() => void shareScreen()}>Share screen</button>
        {source === "video" && (
          <input
            type="file"
            accept="video/*"
            onChange={(e) => e.target.files?.[0] && loadFile(e.target.files[0])}
          />
        )}
      </div>

      <p className="muted">
        <b>Space</b> classify · <b>←</b> you=left · <b>→</b> you=right · <b>Enter</b> capture ·
        you-side: <b style={{ color: YOU }}>{youSide}</b>
        {flash && ` · ${flash}`}
      </p>

      {ready && (
        <div className="live-stage">
          <video
            ref={videoRef}
            src={src ?? undefined}
            controls={source === "video"}
            muted
            className="video"
            onLoadedData={draw}
          />
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
                if (e.key === "Enter") {
                  e.preventDefault();
                  const chosen = manualResults[0]?.name ?? manualQuery.trim();
                  if (chosen) void commit(chosen);
                }
              }}
            />
            {manualQuery && manualResults.length > 0 && (
              <ul className="node-picker-list">
                {manualResults.map((n) => (
                  <li key={n.name} onMouseDown={() => void commit(n.name)}>
                    <span>{n.name}</span>
                    <small>{n.en ?? n.type}</small>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <span className="muted">type + Enter to capture (free text ok)</span>
        </div>
      )}
    </div>
  );
}
