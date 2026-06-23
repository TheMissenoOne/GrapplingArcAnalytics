// Live-match builder + convergence sparkline. Dependency-free.
// Tap node chips (with a you/opponent toggle) to build a {label,type,actor}
// sequence; serialized into the existing #sequence-json field on every change.
(function () {
  "use strict";

  // ── Live-match builder ───────────────────────────────────────────────
  var grid = document.getElementById("chip-grid");
  var pillList = document.getElementById("pill-list");
  var jsonField = document.getElementById("sequence-json");
  var youBtn = document.getElementById("actor-you");
  var oppBtn = document.getElementById("actor-opp");

  if (grid && pillList && jsonField) {
    var actor = "you";
    var seq = [];

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
      youBtn.classList.toggle("active-you", actor === "you");
      oppBtn.classList.toggle("active-opp", actor === "opponent");
    }

    if (youBtn) youBtn.addEventListener("click", function () { setActor("you"); });
    if (oppBtn) oppBtn.addEventListener("click", function () { setActor("opponent"); });

    grid.addEventListener("click", function (ev) {
      var chip = ev.target.closest(".chip");
      if (!chip) return;
      seq.push({
        label: chip.getAttribute("data-label") || "",
        type: chip.getAttribute("data-type") || "",
        actor: actor,
      });
      sync();
    });

    // Keep the field current if the user hand-edits the advanced textarea.
    jsonField.addEventListener("input", function () {
      try {
        var p = JSON.parse(jsonField.value || "[]");
        if (Array.isArray(p)) { seq = p; render(); }
      } catch (e) { /* leave seq as-is until valid */ }
    });

    render();
  }

  // ── Opponent picker: clear manual fields when a ranked opponent is chosen ─
  var sel = document.getElementById("opponent-select");
  if (sel) {
    sel.addEventListener("change", function () {
      if (sel.value) {
        var nameInput = document.querySelector('input[name="opponent_name"]');
        var eloInput = document.querySelector('input[name="opponent_elo"]');
        if (nameInput) nameInput.value = "";
        if (eloInput) eloInput.value = "";
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
