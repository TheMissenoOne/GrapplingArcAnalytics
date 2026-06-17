import { describe, expect, it } from "vitest";
import { buildExportRequest, filterNodes, newEvent, roleToActor } from "./events";
import type { NodeOption, TimelineEvent } from "../types";

describe("newEvent", () => {
  it("creates unique ids and applies defaults + overrides", () => {
    const a = newEvent();
    const b = newEvent({ label: "Montada", type: "control" });
    expect(a.id).not.toEqual(b.id);
    expect(a.successful).toBe(true);
    expect(b.label).toBe("Montada");
  });
});

describe("filterNodes", () => {
  const nodes: NodeOption[] = [
    { name: "Montada", type: "control", en: "Mount" },
    { name: "Guarda Fechada", type: "guard", en: "Closed Guard" },
  ];
  it("matches name or english, case-insensitive", () => {
    expect(filterNodes(nodes, "mount").map((n) => n.name)).toEqual(["Montada"]);
    expect(filterNodes(nodes, "guarda").map((n) => n.name)).toEqual(["Guarda Fechada"]);
    expect(filterNodes(nodes, "")).toHaveLength(2);
  });
});

describe("buildExportRequest", () => {
  const evs: TimelineEvent[] = [
    newEvent({ label: "Armlock", type: "submission", role: "top", start: 30 }),
    newEvent({ label: "Montada", type: "control", role: "top", start: 10 }),
    newEvent({ label: "  ", type: "control", role: "top", start: 5 }), // blank -> dropped
  ];
  it("sorts by start, drops blanks, carries you_role", () => {
    const req = buildExportRequest(evs, { youRole: "top", difficulty: 4 });
    expect(req.events.map((e) => e.label)).toEqual(["Montada", "Armlock"]);
    expect(req.you_role).toBe("top");
    expect(req.difficulty).toBe(4);
  });
});

describe("roleToActor", () => {
  it("maps relative to you_role", () => {
    expect(roleToActor("top", "top")).toBe("you");
    expect(roleToActor("bottom", "top")).toBe("partner");
    expect(roleToActor("", "top")).toBe("you");
  });
});
