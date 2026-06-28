// Live-match builder + convergence sparkline. Dependency-free.
// Tap node chips (with a you/opponent toggle) to build a {label,type,actor}
// sequence; serialized into the existing #sequence-json field on every change.
(function () {
  "use strict";

  // ── Live-match builder ───────────────────────────────────────────────
  var search = document.getElementById("chip-search");
  var results = document.getElementById("chip-results");
  var vocabEl = document.getElementById("node-vocab");
  var pillList = document.getElementById("pill-list");
  var jsonField = document.getElementById("sequence-json");
  var youBtn = document.getElementById("actor-you");
  var oppBtn = document.getElementById("actor-opp");

  // node_type → dot color (mirrors graphview.js).
  var TYPE_COLORS = {
    guard: "#7c9ef5", pass: "#f5a25d", sweep: "#5dd0c3", submission: "#e2615f",
    takedown: "#b98cf0", control: "#9aa7b5", escape: "#6fbf73",
    transition: "#c7b15d", concept: "#888",
  };

  // ── Ranked search (JS port of nodeSearchUtils.scoreNode/rankNodesBySearch) ─
  function normSearch(s) {
    if (!s) return "";
    try {
      return s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
        .replace(/\s+/g, " ").trim();
    } catch (e) { return s.toLowerCase().trim(); }
  }
  function scoreNameToken(name, token) {
    if (!name || !token) return 0;
    if (name === token) return 100;
    if (name.indexOf(token) === 0) return 70;
    if (name.split(" ").some(function (w) { return w.indexOf(token) === 0; })) return 50;
    if (name.indexOf(token) !== -1) return 25;
    return 0;
  }
  function scoreNode(node, query) {
    var nq = normSearch(query);
    if (!nq) return 0;
    var names = [normSearch(node.name), normSearch(node.type)].filter(Boolean);
    var tokens = nq.split(" ").filter(Boolean);
    var total = 0;
    for (var i = 0; i < tokens.length; i++) {
      var best = 0;
      for (var j = 0; j < names.length; j++) {
        var sc = scoreNameToken(names[j], tokens[i]);
        if (sc > best) best = sc;
      }
      if (best === 0) return 0;             // every token must match (AND)
      total += best;
    }
    for (var m = 0; m < names.length; m++) {
      if (names[m] === nq) { total += 40; break; }
      if (names[m].indexOf(nq) === 0) total = Math.max(total, total + 20);
    }
    return total;
  }
  function rankNodes(vocab, query, limit) {
    var q = (query || "").trim();
    if (!q) return vocab.slice(0, limit);
    return vocab
      .map(function (node, i) { return { node: node, i: i, s: scoreNode(node, q) }; })
      .filter(function (r) { return r.s > 0; })
      .sort(function (a, b) { return (b.s - a.s) || (a.i - b.i); })
      .slice(0, limit)
      .map(function (r) { return r.node; });
  }

  if (results && search && pillList && jsonField) {
    var vocab = [];
    try { vocab = JSON.parse((vocabEl && vocabEl.textContent) || "[]"); } catch (e) { vocab = []; }
    var actor = "you";
    var seq = [];
    var shown = [];   // currently rendered result nodes
    var active = 0;   // highlighted result index

    // Seed from any existing JSON (e.g. the advanced textarea).
    try {
      var parsed = JSON.parse(jsonField.value || "[]");
      if (Array.isArray(parsed)) seq = parsed;
    } catch (e) {
      seq = [];
    }

    function sync() {
      jsonField.value = JSON.stringify(seq);
      render();
    }

    function render() {
      pillList.innerHTML = "";
      seq.forEach(function (item, idx) {
        var pill = document.createElement("span");
        pill.className = "pill " + (item.actor === "you" ? "you" : "opp");
        var text = document.createElement("span");
        text.textContent = item.label;
        text.style.cursor = "pointer";
        text.title = "Click to flip you/opponent";
        text.addEventListener("click", function () {
          seq[idx].actor = seq[idx].actor === "you" ? "opponent" : "you";
          sync();
        });
        var x = document.createElement("span");
        x.className = "x";
        x.textContent = "×";
        x.setAttribute("role", "button");
        x.setAttribute("aria-label", "Remove " + item.label);
        x.addEventListener("click", function () {
          seq.splice(idx, 1);
          sync();
        });
        pill.appendChild(text);
        pill.appendChild(x);
        pillList.appendChild(pill);
      });
    }

    function setActor(next) {
      actor = next;
      if (youBtn) youBtn.classList.toggle("active-you", actor === "you");
      if (oppBtn) oppBtn.classList.toggle("active-opp", actor === "opponent");
      search.placeholder = "Search techniques — adding as " +
        (actor === "you" ? "YOU" : "OPPONENT") + " (` to flip)";
    }

    function addNode(node) {
      if (!node) return;
      seq.push({ label: node.name, type: node.type, actor: actor });
      sync();
      active = 0;
      renderResults();   // reset highlight; keep search text
    }

    function renderResults() {
      shown = rankNodes(vocab, search.value, 50);
      if (active >= shown.length) active = Math.max(0, shown.length - 1);
      results.innerHTML = "";
      shown.forEach(function (node, i) {
        var row = document.createElement("div");
        row.className = "result-row" + (i === active ? " is-active" : "");
        var dot = document.createElement("span");
        dot.className = "dot";
        dot.style.background = TYPE_COLORS[(node.type || "").toLowerCase()] || "#9aa7b5";
        var lab = document.createElement("span");
        lab.className = "r-label";
        lab.textContent = node.name;
        var ty = document.createElement("span");
        ty.className = "r-type";
        ty.textContent = node.type || "";
        row.appendChild(dot); row.appendChild(lab); row.appendChild(ty);
        row.addEventListener("mousedown", function (ev) { ev.preventDefault(); addNode(node); });
        results.appendChild(row);
      });
    }

    function moveActive(delta) {
      if (!shown.length) return;
      active = (active + delta + shown.length) % shown.length;
      renderResults();
      var el = results.children[active];
      if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
    }

    if (youBtn) youBtn.addEventListener("click", function () { setActor("you"); });
    if (oppBtn) oppBtn.addEventListener("click", function () { setActor("opponent"); });

    search.addEventListener("input", function () { active = 0; renderResults(); });
    search.addEventListener("keydown", function (ev) {
      if (ev.key === "ArrowDown") { ev.preventDefault(); moveActive(1); }
      else if (ev.key === "ArrowUp") { ev.preventDefault(); moveActive(-1); }
      else if (ev.key === "Enter") { ev.preventDefault(); addNode(shown[active]); }
      else if (ev.key === "`") { ev.preventDefault(); setActor(actor === "you" ? "opponent" : "you"); }
      else if (ev.key === "Backspace" && !search.value && seq.length) {
        ev.preventDefault(); seq.pop(); sync();
      }
    });

    setActor("you");
    renderResults();

    // Keep the field current if the user hand-edits the advanced textarea.
    jsonField.addEventListener("input", function () {
      try {
        var p = JSON.parse(jsonField.value || "[]");
        if (Array.isArray(p)) { seq = p; render(); }
      } catch (e) { /* leave seq as-is until valid */ }
    });

    // ── Paste a full match JSON object to fill the whole form ──────────────
    var pasteBox = document.getElementById("paste-json");
    var pasteBtn = document.getElementById("paste-fill");

    function setVal(name, value) {
      var el = document.querySelector('#match-form [name="' + name + '"]');
      if (el && value !== undefined && value !== null) el.value = value;
    }

    function fillFromMatchJson(text) {
      var obj;
      try { obj = JSON.parse(text); } catch (e) { window.alert("Invalid JSON"); return; }
      if (Array.isArray(obj)) obj = { sequence: obj };  // bare sequence → metadata-less

      // Match a ranked opponent by name; else fall back to the manual field.
      var oppName = obj.opponent || obj.opponent_name || "";
      var oppSel = document.getElementById("opponent-select");
      var matchedId = "";
      if (oppSel && oppName) {
        Array.prototype.forEach.call(oppSel.options, function (opt) {
          if ((opt.getAttribute("data-name") || "").toLowerCase() === oppName.toLowerCase()) {
            matchedId = opt.value;
          }
        });
      }
      if (matchedId) {
        oppSel.value = matchedId;
        setVal("opponent_name", "");
      } else {
        if (oppSel) oppSel.value = "";
        setVal("opponent_name", oppName);
      }

      setVal("event", obj.event);
      setVal("year", obj.year);
      setVal("weight_class", obj.weight_class);
      setVal("win_type", obj.win_type);
      setVal("stage", obj.stage);
      setVal("submission", obj.submission);
      var lost = obj.won === false || obj.result === "L" || obj.result === "D";
      setVal("won", lost ? "false" : "true");

      if (Array.isArray(obj.sequence)) { seq = obj.sequence; sync(); }
    }

    if (pasteBtn && pasteBox) {
      pasteBtn.addEventListener("click", function () { fillFromMatchJson(pasteBox.value); });
    }

    render();
  }

  // ── Opponent picker: clear the new-name field when an athlete is chosen ─
  var sel = document.getElementById("opponent-select");
  if (sel) {
    sel.addEventListener("change", function () {
      if (sel.value) {
        var nameInput = document.querySelector('input[name="opponent_name"]');
        if (nameInput) nameInput.value = "";
      }
    });
  }

  // ── Convergence sparkline ────────────────────────────────────────────
  var svg = document.getElementById("converge-spark");
  if (svg) {
    var series;
    try {
      series = JSON.parse(svg.getAttribute("data-series") || "[]");
    } catch (e) {
      series = [];
    }
    if (Array.isArray(series) && series.length > 1) {
      var w = +svg.getAttribute("width") || 320;
      var h = +svg.getAttribute("height") || 60;
      var pad = 4;
      var targetAttr = svg.getAttribute("data-target");
      var vals = series.slice();
      if (targetAttr !== null) vals.push(+targetAttr);
      var min = Math.min.apply(null, vals);
      var max = Math.max.apply(null, vals);
      var span = max - min || 1;
      var ns = "http://www.w3.org/2000/svg";

      function y(v) { return pad + (h - 2 * pad) * (1 - (v - min) / span); }
      function x(i) { return pad + (w - 2 * pad) * (i / (series.length - 1)); }

      if (targetAttr !== null) {
        var line = document.createElementNS(ns, "line");
        line.setAttribute("x1", pad);
        line.setAttribute("x2", w - pad);
        line.setAttribute("y1", y(+targetAttr));
        line.setAttribute("y2", y(+targetAttr));
        line.setAttribute("stroke", "#c62828");
        line.setAttribute("stroke-dasharray", "3 3");
        svg.appendChild(line);
      }

      var pts = series.map(function (v, i) { return x(i) + "," + y(v); }).join(" ");
      var poly = document.createElementNS(ns, "polyline");
      poly.setAttribute("points", pts);
      poly.setAttribute("fill", "none");
      poly.setAttribute("stroke", "#7c9ef5");
      poly.setAttribute("stroke-width", "2");
      svg.appendChild(poly);
    }
  }
})();
