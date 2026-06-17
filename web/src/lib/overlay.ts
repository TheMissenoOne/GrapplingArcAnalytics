import type { Detection, YouSide } from "../types";

export function sideOf(centerX: number, imageW: number): "left" | "right" {
  return centerX < imageW / 2 ? "left" : "right";
}

export function isYou(centerX: number, imageW: number, youSide: YouSide): boolean {
  return sideOf(centerX, imageW) === youSide;
}

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function scaleBox(
  det: Detection,
  imageW: number,
  imageH: number,
  canvasW: number,
  canvasH: number,
): Box {
  if (imageW === 0 || imageH === 0) {
    return { x: 0, y: 0, w: 0, h: 0 };
  }
  const sx = canvasW / imageW;
  const sy = canvasH / imageH;
  return {
    x: (det.x - det.width / 2) * sx,
    y: (det.y - det.height / 2) * sy,
    w: det.width * sx,
    h: det.height * sy,
  };
}
