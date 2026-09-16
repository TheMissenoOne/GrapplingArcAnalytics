// Audit pages (Mode A: rounds, Mode B: dictionary). Dependency-free, same style as
// livematch.js. Each app below no-ops if its root element isn't on the page.
(function () {
  "use strict";

  // ── ranked search (same algorithm as livematch.js's chip picker) ─────────
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
  function scoreItem(item, query) {
    var nq = normSearch(query);
    if (!nq) return 1; // empty query -> keep original order, still "match"
    var names = [normSearch(item.label), normSearch(item.pt || ""), normSearch(item.type || "")]
      .filter(Boolean);
    var tokens = nq.split(" ").filter(Boolean);
    var total = 0;
    for (var i = 0; i < tokens.length; i++) {
      var best = 0;
      for (var j = 0; j < names.length; j++) {
        var sc = scoreNameToken(names[j], tokens[i]);
        if (sc > best) best = sc;
      }
      if (best === 0) return 0;
      total += best;
    }
    return total;
  }
  function rankItems(vocab, query, limit) {
    if (!query) return vocab.slice(0, limit);
    return vocab
      .map(function (item) { return { item: item, score: scoreItem(item, query) }; })
      .filter(function (p) { return p.score > 0; })
      .sort(function (a, b) { return b.score - a.score; })
      .slice(0, limit)
      .map(function (p) { return p.item; });
  }

  function fmtTs(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  // ── shared label picker popover ───────────────────────────────────────────
  function LabelPicker(vocab) {
    var el = document.createElement("div");
    el.className = "label-picker";
    el.innerHTML = '<input type="search" placeholder="Search technique…" autocomplete="off">' +
      '<div class="lp-results"></div>';
    document.body.appendChild(el);
    var input = el.querySelector("input");
    var results = el.querySelector(".lp-results");
    var onPick = null;

    function render(query) {
      var picks = rankItems(vocab, query, 30);
      results.innerHTML = "";
      picks.forEach(function (item, i) {
        var row = document.createElement("div");
        row.className = "lp-row" + (i === 0 ? " is-active" : "");
        row.innerHTML = '<span>' + item.label + '</span><span class="lp-type">' +
          (item.type || "") + '</span>';
        row.addEventListener("click", function () { pick(item); });
        results.appendChild(row);
      });
    }
    function pick(item) {
      close();
      if (onPick) onPick(item);
    }
    function activeRow() { return results.querySelector(".lp-row.is-active"); }

    input.addEventListener("input", function () { render(input.value); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { close(); return; }
      if (e.key === "Enter") {
        var a = activeRow();
        if (a) a.click();
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        var rows = Array.prototype.slice.call(results.querySelectorAll(".lp-row"));
        if (!rows.length) return;
        var idx = rows.findIndex(function (r) { return r.classList.contains("is-active"); });
        rows[idx >= 0 ? idx : 0].classList.remove("is-active");
        idx = e.key === "ArrowDown" ? Math.min(idx + 1, rows.length - 1) : Math.max(idx - 1, 0);
        rows[idx].classList.add("is-active");
      }
    });

    function open(anchorRect, pickCallback) {
      onPick = pickCallback;
      var top = Math.min(anchorRect.bottom + 6, window.innerHeight - 320);
      var left = Math.min(anchorRect.left, window.innerWidth - 336);
      el.style.top = Math.max(8, top) + "px";
      el.style.left = Math.max(8, left) + "px";
      el.classList.add("open");
      input.value = "";
      render("");
      input.focus();
    }
    function close() { el.classList.remove("open"); onPick = null; }
    document.addEventListener("click", function (e) {
      if (el.classList.contains("open") && !el.contains(e.target)) close();
    });
    return { open: open, close: close, isOpen: function () { return el.classList.contains("open"); } };
  }

  // ── Mode A: round detail ──────────────────────────────────────────────────
  var roundRoot = document.getElementById("audit-round");
  if (roundRoot) {
    var dataEl = document.getElementById("audit-data");
    var vocabEl = document.getElementById("audit-vocab");
    var data = JSON.parse(dataEl.textContent);
    var vocab = JSON.parse(vocabEl.textContent);
    var slug = data.slug;
    var state = { events: data.events, currentTime: (data.events[0] && (data.events[0].corrected
      ? data.events[0].corrected.ts : (data.events[0].original || {}).ts)) || 0 };
    var selectedIdx = 0;
    var picker = LabelPicker(vocab);

    var video = document.getElementById("audit-video");
    var strip = document.getElementById("frame-strip");
    var list = document.getElementById("audit-events");
    var saveState = document.getElementById("save-state");

    function eventTs(ev) {
      if (ev.corrected && ev.corrected.ts != null) return ev.corrected.ts;
      return ev.original ? ev.original.ts : 0;
    }
    function eventActor(ev) {
      if (ev.corrected && ev.corrected.actor) return ev.corrected.actor;
      return ev.original ? ev.original.actor : "you";
    }
    function eventLabel(ev) {
      if (ev.corrected && ev.corrected.label) return ev.corrected.label;
      return ev.original ? ev.original.label : "";
    }
    function eventType(ev) {
      if (ev.corrected && ev.corrected.type) return ev.corrected.type;
      return ev.original ? ev.original.type : "";
    }

    function renderFrames() {
      strip.innerHTML = "";
      data.frames.forEach(function (f) {
        var img = document.createElement("img");
        img.src = "/admin/audit/rounds/" + slug + "/frame/" + f.file;
        img.loading = "lazy";
        img.dataset.ts = f.ts;
        img.title = fmtTs(f.ts);
        img.addEventListener("click", function () { seekTo(f.ts); });
        strip.appendChild(img);
      });
      markCurrentFrame();
    }
    function markCurrentFrame() {
      var nearest = null, best = Infinity;
      Array.prototype.forEach.call(strip.children, function (img) {
        img.classList.remove("current");
        var d = Math.abs(Number(img.dataset.ts) - state.currentTime);
        if (d < best) { best = d; nearest = img; }
      });
      if (nearest) {
        nearest.classList.add("current");
        nearest.scrollIntoView({ inline: "center", block: "nearest" });
      }
    }
    function seekTo(ts) {
      state.currentTime = ts;
      if (video) video.currentTime = ts;
      markCurrentFrame();
    }

    var highlightsEl = document.getElementById("audit-highlights");
    function renderHighlights() {
      if (!highlightsEl) return;
      (data.highlights || []).forEach(function (hl) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = fmtTs(hl.start) + " " + (hl.label || "highlight");
        // Seeks the ONE main video to the clip's start -- no second player, same source
        // scripts/round_audit.py cut highlights/*.mp4 from.
        btn.addEventListener("click", function () { seekTo(hl.start); });
        highlightsEl.appendChild(btn);
      });
    }

    function renderEvents() {
      list.innerHTML = "";
      state.events.forEach(function (ev, i) {
        var row = document.createElement("div");
        row.className = "audit-event" + (i === selectedIdx ? " selected" : "");
        var actor = eventActor(ev);
        var noteTxt = (ev.original && ev.original.note) || "";
        row.innerHTML =
          '<span class="ts">' + fmtTs(eventTs(ev)) + '</span>' +
          '<span class="actor ' + actor + '">' + actor + '</span>' +
          '<span class="label">' + eventLabel(ev) +
            '<span class="type">' + eventType(ev) + '</span>' +
            (noteTxt ? '<span class="note">' + noteTxt + '</span>' : '') +
          '</span>' +
          (ev.verdict ? '<span class="verdict-badge vb-' + ev.verdict + '">' + ev.verdict.replace(/_/g, " ") + '</span>' : '<span></span>');
        row.addEventListener("click", function () { select(i); seekTo(eventTs(ev)); });
        list.appendChild(row);
      });
      (data.resets || []).forEach(function (ts) {
        var row = document.createElement("div");
        row.className = "audit-event is-reset";
        row.innerHTML = '<span class="ts">' + fmtTs(ts) + '</span><span></span>' +
          '<span class="label">— reset —</span><span></span>';
        list.appendChild(row);
      });
    }

    function select(i) {
      selectedIdx = Math.max(0, Math.min(i, state.events.length - 1));
      renderEvents();
      var row = list.children[selectedIdx];
      if (row) row.scrollIntoView({ block: "nearest" });
    }

    function ensureCorrected(ev) {
      if (!ev.corrected) ev.corrected = { ts: null, actor: null, label: null, type: null, successful: null };
      return ev.corrected;
    }

    function confirmSelected() {
      var ev = state.events[selectedIdx];
      if (!ev) return;
      ev.verdict = "confirmed";
      ev.corrected = null;
      afterChange();
    }
    function swapActorSelected() {
      var ev = state.events[selectedIdx];
      if (!ev) return;
      var c = ensureCorrected(ev);
      c.actor = eventActor(ev) === "you" ? "partner" : "you";
      ev.verdict = "wrong_actor";
      afterChange();
    }
    function openLabelPickerSelected() {
      var ev = state.events[selectedIdx];
      if (!ev) return;
      var row = list.children[selectedIdx];
      var rect = row ? row.getBoundingClientRect() : { bottom: 100, left: 100 };
      picker.open(rect, function (item) {
        var c = ensureCorrected(ev);
        c.label = item.label;
        c.type = item.type;
        ev.verdict = ev.verdict === "added" ? "added" : "wrong_label";
        afterChange();
      });
    }
    function setTimeSelected() {
      var ev = state.events[selectedIdx];
      if (!ev) return;
      var c = ensureCorrected(ev);
      c.ts = state.currentTime;
      ev.verdict = "wrong_time";
      afterChange();
    }
    function deleteSelected() {
      var ev = state.events[selectedIdx];
      if (!ev) return;
      ev.verdict = "not_visible";
      afterChange();
    }
    function addNew() {
      var ev = {
        id: "new-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        original: null, verdict: "added", note: "",
        corrected: { ts: state.currentTime, actor: "you", label: "", type: "", successful: null },
      };
      state.events.push(ev);
      state.events.sort(function (a, b) { return eventTs(a) - eventTs(b); });
      selectedIdx = state.events.indexOf(ev);
      renderEvents();
      openLabelPickerSelected();
    }

    var saveTimer = null;
    function afterChange() {
      renderEvents();
      markCurrentFrame();
      if (saveState) saveState.textContent = "saving…";
      clearTimeout(saveTimer);
      saveTimer = setTimeout(save, 800);
    }
    function save() {
      return fetch("/admin/audit/rounds/" + slug + "/verdicts", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events: state.events }),
      }).then(function (r) {
        if (saveState) saveState.textContent = r.ok ? "saved" : "save failed";
      }).catch(function () { if (saveState) saveState.textContent = "save failed"; });
    }

    var exportBtn = document.getElementById("export-corrected");
    if (exportBtn) {
      exportBtn.addEventListener("click", function () {
        clearTimeout(saveTimer);
        save().then(function () {
          return fetch("/admin/audit/rounds/" + slug + "/export", { method: "POST" });
        }).then(function (r) { return r.json(); }).then(function (doc) {
          var blob = new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" });
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = slug + "_events_corrected.json";
          a.click();
        });
      });
    }

    if (video) {
      video.addEventListener("timeupdate", function () {
        state.currentTime = video.currentTime;
        markCurrentFrame();
      });
    }

    document.addEventListener("keydown", function (e) {
      var tag = (e.target && e.target.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (picker.isOpen()) return;
      switch (e.key) {
        case "j": select(selectedIdx + 1); break;
        case "k": select(selectedIdx - 1); break;
        case "c": confirmSelected(); break;
        case "a": swapActorSelected(); break;
        case "l": openLabelPickerSelected(); break;
        case "t": setTimeSelected(); break;
        case "x": deleteSelected(); break;
        case "n": addNew(); break;
        default: return;
      }
      e.preventDefault();
    });

    renderFrames();
    renderHighlights();
    renderEvents();
  }

  // ── Mode B: dictionary queue ───────────────────────────────────────────────
  var dictRoot = document.getElementById("audit-dictionary");
  if (dictRoot) {
    var itemsEl = document.getElementById("dict-items");
    var dvocabEl = document.getElementById("audit-vocab");
    var items = JSON.parse(itemsEl.textContent);
    var dvocab = JSON.parse(dvocabEl.textContent);
    var dpicker = LabelPicker(dvocab);
    var grid = document.getElementById("dict-grid");
    var progress = document.getElementById("dict-progress");
    var confSelect = document.getElementById("dict-conf-filter");
    var focusIdx = 0;
    var total = items.length;

    function kindLabel(k) {
      return k === "action" ? "ação" : k === "state" ? "estado" : (k || "?");
    }

    function secondLineHtml(item) {
      if (!item.pair_node_key) {
        return '<div class="second">corpus: ' + (item.corpus_label || "—") + ' [' +
          item.agree_near + ']</div>';
      }
      // item 32 (2026-09-16): a `pair` row is a genuine double label -- show both chips with
      // their own kind badge instead of "corpus is only a second opinion".
      return '<div class="pair-chips">' +
        '<span class="chip">' + item.candidate_label + ' (' + kindLabel(item.kind) + ')</span>' +
        '<span class="chip chip-pair">' + (item.pair_label || item.pair_node_key) + ' (' +
          kindLabel(item.pair_kind) + ')</span>' +
        '</div>';
    }

    function cardEl(item, i) {
      var card = document.createElement("div");
      card.className = "dict-card";
      card.dataset.idx = i;
      card.tabIndex = 0;
      card.innerHTML =
        '<img loading="lazy" src="/admin/audit/dictionary/frame/' + encodeURIComponent(item.frame) + '">' +
        '<div class="body">' +
          '<div><span class="candidate">' + item.candidate_label + '</span>' +
          '<span class="conf conf-' + item.confidence + '">' + item.confidence + '</span></div>' +
          secondLineHtml(item) +
          '<div class="meta">' + item.bout + ' · ' + fmtTs(item.ts) + '</div>' +
          '<div class="actions">' +
            '<button class="btn-accept" data-act="accept">Accept</button>' +
            '<button class="btn-relabel" data-act="relabel">Relabel</button>' +
            '<button class="btn-reject" data-act="reject">Reject</button>' +
          '</div>' +
        '</div>';
      card.addEventListener("click", function (e) {
        var act = e.target && e.target.dataset ? e.target.dataset.act : null;
        setFocus(i);
        if (act) act === "relabel" ? relabel(i) : verdict(i, act);
      });
      return card;
    }

    function render() {
      grid.innerHTML = "";
      var floor = confSelect ? confSelect.value : "";
      items.forEach(function (item, i) {
        if (floor && item.confidence !== floor && !(floor === "medium" && item.confidence === "high")) {
          // simple floor filter handled below via visible flag instead
        }
      });
      var order = { low: 0, medium: 1, high: 2 };
      var floorN = floor ? order[floor] : -1;
      items.forEach(function (item, i) {
        if (item._done) return;
        if (floorN >= 0 && order[item.confidence] < floorN) return;
        grid.appendChild(cardEl(item, i));
      });
      updateProgress();
    }
    function updateProgress() {
      var done = items.filter(function (i) { return i._done; }).length;
      if (progress) progress.textContent = done + " / " + total + " reviewed";
    }
    function setFocus(i) {
      focusIdx = i;
      Array.prototype.forEach.call(grid.children, function (c) {
        c.classList.toggle("is-active", Number(c.dataset.idx) === i);
      });
    }
    function verdict(i, kind, target) {
      var item = items[i];
      if (!item || item._done) return;
      var verdictStr = kind === "relabel" ? "relabel:" + target : kind;
      var card = grid.querySelector('[data-idx="' + i + '"]');
      if (card) card.classList.add("is-done");
      var body = {
        node_key: kind === "relabel" ? target : item.node_key,
        bout: item.bout, ts_ms: item.ts_ms, verdict: verdictStr,
      };
      // Relabel only ever replaces the candidate's own claim, never the pair's other half.
      if (kind !== "relabel" && item.pair_node_key) body.pair_node_key = item.pair_node_key;
      fetch("/admin/audit/dictionary/verdict", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (r) {
        if (r.ok) { item._done = true; render(); }
        else if (card) card.classList.remove("is-done");
      });
    }
    function relabel(i) {
      var card = grid.querySelector('[data-idx="' + i + '"]');
      var rect = card ? card.getBoundingClientRect() : { bottom: 100, left: 100 };
      dpicker.open(rect, function (chosen) {
        verdict(i, "relabel", chosen.node_key || chosen.label.toLowerCase());
      });
    }

    if (confSelect) confSelect.addEventListener("change", render);

    document.addEventListener("keydown", function (e) {
      var tag = (e.target && e.target.tagName) || "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (dpicker.isOpen()) return;
      var visible = Array.prototype.slice.call(grid.children);
      var pos = visible.findIndex(function (c) { return Number(c.dataset.idx) === focusIdx; });
      if (e.key === "j") { setFocus(Number((visible[Math.min(pos + 1, visible.length - 1)] || {}).dataset.idx)); }
      else if (e.key === "k") { setFocus(Number((visible[Math.max(pos - 1, 0)] || {}).dataset.idx)); }
      else if (e.key === "1") verdict(focusIdx, "accept");
      else if (e.key === "2") relabel(focusIdx);
      else if (e.key === "3") verdict(focusIdx, "reject");
      else return;
      e.preventDefault();
    });

    render();
  }
})();
