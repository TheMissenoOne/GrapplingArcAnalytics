import { describe, expect, it } from "vitest";
import { isYou, scaleBox, sideOf } from "./overlay";
import type { Detection } from "../types";

describe("sideOf", () => {
  it("returns left when centerX < half", () => {
    expect(sideOf(40, 100)).toBe("left");
  });

  it("returns right when centerX >= half", () => {
    expect(sideOf(60, 100)).toBe("right");
  });

  it("returns right exactly at midpoint", () => {
    expect(sideOf(50, 100)).toBe("right");
  });
});

describe("isYou", () => {
  it("returns true when side matches youSide", () => {
    expect(isYou(40, 100, "left")).toBe(true);
  });

  it("returns false when side does not match youSide", () => {
    expect(isYou(60, 100, "left")).toBe(false);
  });
});

describe("scaleBox", () => {
  it("converts centre to top-left and scales to canvas", () => {
    const det: Detection = {
      raw_class: "mount_top",
      vicos_class: "mount_top",
      confidence: 0.9,
      x: 50,
      y: 50,
      width: 20,
      height: 20,
    };
    const box = scaleBox(det, 100, 100, 200, 200);
    expect(box).toEqual({ x: 80, y: 80, w: 40, h: 40 });
  });

  it("returns zeros when imageW is 0", () => {
    const det: Detection = {
      raw_class: "mount_top",
      vicos_class: "mount_top",
      confidence: 0.9,
      x: 50,
      y: 50,
      width: 20,
      height: 20,
    };
    const box = scaleBox(det, 0, 100, 200, 200);
    expect(box).toEqual({ x: 0, y: 0, w: 0, h: 0 });
  });

  it("returns zeros when imageH is 0", () => {
    const det: Detection = {
      raw_class: "mount_top",
      vicos_class: "mount_top",
      confidence: 0.9,
      x: 50,
      y: 50,
      width: 20,
      height: 20,
    };
    const box = scaleBox(det, 100, 0, 200, 200);
    expect(box).toEqual({ x: 0, y: 0, w: 0, h: 0 });
  });
});
