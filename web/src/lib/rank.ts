import type { NodeOption } from "../types";

/** Lowercase, strip diacritics, collapse whitespace — mirrors the app's normalizeForSearch. */
export function normalize(s: string): string {
  return s
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** Score a (normalized) name against a (normalized) token. Prefix beats substring. */
function scoreToken(name: string, token: string): number {
  if (!name || !token) return 0;
  if (name === token) return 100;
  if (name.startsWith(token)) return 70;
  if (name.split(" ").some((w) => w.startsWith(token))) return 50;
  if (name.includes(token)) return 25;
  return 0;
}

function scoreNode(node: NodeOption, query: string): number {
  const fields = [normalize(node.name), normalize(node.en ?? "")];
  const tokens = normalize(query).split(" ").filter(Boolean);
  if (tokens.length === 0) return 0;
  let total = 0;
  for (const token of tokens) {
    const best = Math.max(...fields.map((f) => scoreToken(f, token)));
    if (best === 0) return 0; // AND semantics: every token must match somewhere
    total += best;
  }
  return total;
}

/** Rank nodes by relevance to `query` (name + English), best first. */
export function rankNodes(nodes: NodeOption[], query: string, limit?: number): NodeOption[] {
  const q = query.trim();
  if (!q) return limit !== undefined ? nodes.slice(0, limit) : nodes;
  const ranked = nodes
    .map((node, i) => ({ node, i, score: scoreNode(node, q) }))
    .filter((s) => s.score > 0)
    .sort((a, b) => b.score - a.score || a.i - b.i)
    .map((s) => s.node);
  return limit !== undefined ? ranked.slice(0, limit) : ranked;
}
