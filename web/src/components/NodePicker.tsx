import { useMemo, useState } from "react";
import type { NodeOption } from "../types";
import { rankNodes } from "../lib/rank";

interface Props {
  nodes: NodeOption[];
  value: string;
  onPick: (node: NodeOption) => void;
  placeholder?: string;
}

/** Searchable technique-node selector backed by the app's 137-node vocab. */
export function NodePicker({ nodes, value, onPick, placeholder }: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const results = useMemo(() => rankNodes(nodes, query, 12), [nodes, query]);

  return (
    <div className="node-picker">
      <input
        value={open ? query : value}
        placeholder={placeholder ?? "Search technique…"}
        onFocus={() => {
          setOpen(true);
          setQuery("");
        }}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onChange={(e) => setQuery(e.target.value)}
      />
      {open && results.length > 0 && (
        <ul className="node-picker-list">
          {results.map((n) => (
            <li
              key={n.name}
              onMouseDown={() => {
                onPick(n);
                setOpen(false);
              }}
            >
              <span>{n.name}</span>
              <small>
                {n.type}
                {n.en ? ` · ${n.en}` : ""}
              </small>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
