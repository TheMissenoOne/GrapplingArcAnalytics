// Athlete graph renderer — dependency-free Fruchterman–Reingold spring layout
// with center gravity, plus mouse pan / wheel zoom / node drag.
// Reads the app-shaped graph JSON from <svg id="athlete-graph" data-graph='...'>:
//   nodes: [{id, label, data:{type, usageCount, computedElo}}]
//   edges: [{source, target, data:{elo}}]
// Nodes are colored by node_type and sized by usage.
(function () {
  "use strict";

  var svg = document.getElementById("athlete-graph");
  if (!svg) return;

  var data;
  try {
    data = JSON.parse(svg.getAttribute("data-graph") || "null");
  } catch (e) {
    data = null;
  }
  if (!data || !data.nodes || !data.nodes.length) return;

  var NS = "http://www.w3.org/2000/svg";
  var W = +svg.getAttribute("width") || 640;
  var H = +svg.getAttribute("height") || 460;

  // node_type → fill (matches the app's type palette loosely).
  var COLORS = {
    guard: "#7c9ef5", pass: "#f5a25d", sweep: "#5dd0c3", submission: "#e2615f",
    takedown: "#b98cf0", control: "#9aa7b5", escape: "#6fbf73",
    transition: "#c7b15d", concept: "#888",
  };
  function colorFor(t) { return COLORS[(t || "").toLowerCase()] || "#9aa7b5"; }

  var nodes = data.nodes.map(function (n) {
    return {
      id: n.id,
      label: n.label || n.id,
      type: (n.data && n.data.type) || "",
      usage: (n.data && n.data.usageCount) || 1,
      x: 0, y: 0, dx: 0, dy: 0,
    };
  });
  var index = {};
  nodes.forEach(function (n, i) { index[n.id] = i; });

  var edges = (data.edges || []).filter(function (e) {
    return index[e.source] !== undefined && index[e.target] !== undefined;
  }).map(function (e) {
    return { s: index[e.source], t: index[e.target] };
  });

  // ── Layout (Fruchterman–Reingold + center gravity) ──────────────────────
  var area = W * H;
  var n = nodes.length;
  var k = Math.sqrt(area / n) * 1.1;        // wider ideal spacing → more spread
  var cx = W / 2, cy = H / 2;
  // Seed on a circle so the sim is deterministic.
  nodes.forEach(function (nd, i) {
    var a = (2 * Math.PI * i) / n;
    nd.x = cx + Math.cos(a) * k;
    nd.y = cy + Math.sin(a) * k;
  });

  var iters = 320;
  var temp = W / 6;
  var gravity = 0.015;                        // weak pull to center → keeps it framed
  for (var it = 0; it < iters; it++) {
    for (var i = 0; i < n; i++) { nodes[i].dx = 0; nodes[i].dy = 0; }
    // Repulsion between every pair (52 nodes → trivial).
    for (var a = 0; a < n; a++) {
      for (var b = a + 1; b < n; b++) {
        var ddx = nodes[a].x - nodes[b].x;
        var ddy = nodes[a].y - nodes[b].y;
        var dist = Math.sqrt(ddx * ddx + ddy * ddy) || 0.01;
        var rep = (k * k) / dist;
        var ux = (ddx / dist) * rep, uy = (ddy / dist) * rep;
        nodes[a].dx += ux; nodes[a].dy += uy;
        nodes[b].dx -= ux; nodes[b].dy -= uy;
      }
    }
    // Attraction along edges.
    edges.forEach(function (e) {
      var s = nodes[e.s], t = nodes[e.t];
      var ddx = s.x - t.x, ddy = s.y - t.y;
      var dist = Math.sqrt(ddx * ddx + ddy * ddy) || 0.01;
      var att = (dist * dist) / k;
      var ux = (ddx / dist) * att, uy = (ddy / dist) * att;
      s.dx -= ux; s.dy -= uy;
      t.dx += ux; t.dy += uy;
    });
    // Center gravity + displace, capped by temperature; cool down.
    for (var c = 0; c < n; c++) {
      var nd = nodes[c];
      nd.dx += (cx - nd.x) * gravity * k;
      nd.dy += (cy - nd.y) * gravity * k;
      var d = Math.sqrt(nd.dx * nd.dx + nd.dy * nd.dy) || 0.01;
      nd.x += (nd.dx / d) * Math.min(d, temp);
      nd.y += (nd.dy / d) * Math.min(d, temp);
    }
    temp *= 0.975;
  }

  // ── Fit to viewBox ──────────────────────────────────────────────────────
  var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  nodes.forEach(function (nd) {
    if (nd.x < minX) minX = nd.x; if (nd.x > maxX) maxX = nd.x;
    if (nd.y < minY) minY = nd.y; if (nd.y > maxY) maxY = nd.y;
  });
  var pad = 40;
  var sx = (W - 2 * pad) / ((maxX - minX) || 1);
  var sy = (H - 2 * pad) / ((maxY - minY) || 1);
  var fit = Math.min(sx, sy);
  nodes.forEach(function (nd) {
    nd.x = pad + (nd.x - minX) * fit;
    nd.y = pad + (nd.y - minY) * fit;
  });

  var maxUsage = nodes.reduce(function (m, nd) { return Math.max(m, nd.usage); }, 1);
  function radius(u) { return 6 + 10 * Math.sqrt(u / maxUsage); }
  // Label only the busier half to cut clutter (others get a hover <title>).
  var usages = nodes.map(function (nd) { return nd.usage; }).sort(function (p, q) { return p - q; });
  var labelMin = usages[Math.floor(usages.length / 2)] || 1;

  // ── Render inside a pannable/zoomable <g> ───────────────────────────────
  var root = document.createElementNS(NS, "g");
  root.setAttribute("id", "graph-root");
  svg.appendChild(root);

  var edgeEls = edges.map(function (e) {
    var s = nodes[e.s], t = nodes[e.t];
    var ln = document.createElementNS(NS, "line");
    ln.setAttribute("x1", s.x); ln.setAttribute("y1", s.y);
    ln.setAttribute("x2", t.x); ln.setAttribute("y2", t.y);
    ln.setAttribute("stroke", "#3a4250");
    ln.setAttribute("stroke-width", "1.5");
    root.appendChild(ln);
    return { el: ln, e: e };
  });

  nodes.forEach(function (nd) {
    var r = radius(nd.usage);
    var c = document.createElementNS(NS, "circle");
    c.setAttribute("cx", nd.x); c.setAttribute("cy", nd.y); c.setAttribute("r", r);
    c.setAttribute("fill", colorFor(nd.type));
    c.setAttribute("stroke", "#11151c");
    c.setAttribute("stroke-width", "1.5");
    c.style.cursor = "grab";
    var title = document.createElementNS(NS, "title");
    title.textContent = nd.label + " · " + (nd.type || "?") + " · used " + nd.usage + "×";
    c.appendChild(title);
    root.appendChild(c);
    nd._c = c; nd._r = r;

    if (nd.usage >= labelMin) {
      var tx = document.createElementNS(NS, "text");
      tx.setAttribute("x", nd.x); tx.setAttribute("y", nd.y - r - 3);
      tx.setAttribute("text-anchor", "middle");
      tx.setAttribute("font-size", "10");
      tx.setAttribute("fill", "#c8d0db");
      tx.style.pointerEvents = "none";
      tx.textContent = nd.label;
      root.appendChild(tx);
      nd._t = tx;
    }
  });

  // ── Pan / zoom / drag ───────────────────────────────────────────────────
  var view = { tx: 0, ty: 0, s: 1 };
  function applyView() {
    root.setAttribute(
      "transform",
      "translate(" + view.tx + "," + view.ty + ") scale(" + view.s + ")"
    );
  }
  function localXY(ev) {
    var r = svg.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }
  function redrawNode(nd) {
    nd._c.setAttribute("cx", nd.x); nd._c.setAttribute("cy", nd.y);
    if (nd._t) {
      nd._t.setAttribute("x", nd.x);
      nd._t.setAttribute("y", nd.y - nd._r - 3);
    }
    edgeEls.forEach(function (ee) {
      var s = nodes[ee.e.s], t = nodes[ee.e.t];
      if (s === nd) { ee.el.setAttribute("x1", nd.x); ee.el.setAttribute("y1", nd.y); }
      if (t === nd) { ee.el.setAttribute("x2", nd.x); ee.el.setAttribute("y2", nd.y); }
    });
  }

  var drag = null;  // {node} or {pan:true, startx, starty, tx0, ty0}

  svg.addEventListener("wheel", function (ev) {
    ev.preventDefault();
    var p = localXY(ev);
    var factor = ev.deltaY < 0 ? 1.1 : 1 / 1.1;
    var ns = Math.max(0.3, Math.min(6, view.s * factor));
    factor = ns / view.s;
    // zoom about the cursor
    view.tx = p.x - (p.x - view.tx) * factor;
    view.ty = p.y - (p.y - view.ty) * factor;
    view.s = ns;
    applyView();
  }, { passive: false });

  svg.addEventListener("mousedown", function (ev) {
    var p = localXY(ev);
    if (ev.target.tagName === "circle") {
      var nd = nodes.filter(function (x) { return x._c === ev.target; })[0];
      if (nd) { drag = { node: nd }; nd._c.style.cursor = "grabbing"; }
    } else {
      drag = { pan: true, sx: p.x, sy: p.y, tx0: view.tx, ty0: view.ty };
      svg.style.cursor = "grabbing";
    }
  });
  window.addEventListener("mousemove", function (ev) {
    if (!drag) return;
    var p = localXY(ev);
    if (drag.pan) {
      view.tx = drag.tx0 + (p.x - drag.sx);
      view.ty = drag.ty0 + (p.y - drag.sy);
      applyView();
    } else {
      // screen → graph coords (undo the view transform)
      drag.node.x = (p.x - view.tx) / view.s;
      drag.node.y = (p.y - view.ty) / view.s;
      redrawNode(drag.node);
    }
  });
  window.addEventListener("mouseup", function () {
    if (drag && drag.node) drag.node._c.style.cursor = "grab";
    svg.style.cursor = "";
    drag = null;
  });
})();
