import { describe, expect, it } from "vitest";
import { normalize, rankNodes } from "./rank";
import type { NodeOption } from "../types";

const NODES: NodeOption[] = [
  { name: "Montada", type: "control", en: "Mount" },
  { name: "Controle Lateral", type: "control", en: "Side Control" },
  { name: "Guarda Fechada", type: "guard", en: "Closed Guard" },
  { name: "Costas", type: "control", en: "Back Control" },
];

describe("normalize", () => {
  it("lowercases, strips diacritics, collapses whitespace", () => {
    expect(normalize("  Guarda   Fechada ")).toBe("guarda fechada");
    expect(normalize("Posição")).toBe("posicao");
  });
});

describe("rankNodes", () => {
  it("matches English translation", () => {
    expect(rankNodes(NODES, "mount")[0].name).toBe("Montada");
  });

  it("prefix beats substring", () => {
    // 'co' prefixes Controle/Costas (70) and is a substring of others; prefix wins.
    const top = rankNodes(NODES, "co").map((n) => n.name);
    expect(top.slice(0, 2).sort()).toEqual(["Controle Lateral", "Costas"]);
  });

  it("multi-token AND", () => {
    expect(rankNodes(NODES, "side control")[0].name).toBe("Controle Lateral");
    expect(rankNodes(NODES, "side zzz")).toHaveLength(0); // 'zzz' matches nothing
  });

  it("empty query returns all (optionally limited)", () => {
    expect(rankNodes(NODES, "")).toHaveLength(4);
    expect(rankNodes(NODES, "", 2)).toHaveLength(2);
  });
});
