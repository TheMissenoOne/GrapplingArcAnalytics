/**
 * Study orchestrator — calls Edge Function, runs client-side RAG, renders results.
 * Requires: window.GA_CONFIG, window.GA_STUDY_KNOWLEDGE, GAStudyRag, GAGraph
 */
(function () {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));

  let analysis = null;
  let ytId = "";
  let rag = null;

  const formatTime = (sec) => {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    const h = Math.floor(sec / 3600),
      m = Math.floor((sec % 3600) / 60),
      s = sec % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
  };

  const getYoutubeId = (value) => {
    try {
      const u = new URL(value);
      if (u.hostname.includes("youtu.be")) return u.pathname.slice(1).split("/")[0];
      if (u.searchParams.get("v")) return u.searchParams.get("v");
      const m = u.pathname.match(/\/(?:embed|shorts|live)\/([^/?]+)/);
      return m ? m[1] : "";
    } catch {
      return /^[A-Za-z0-9_-]{11}$/.test(value) ? value : "";
    }
  };

  const setStage = (name, state) => {
    const el = $(`.step[data-stage="${name}"]`);
    if (!el) return;
    el.classList.remove("active", "done");
    if (state) el.classList.add(state);
  };

  const resetStages = () => {
    $$(".step").forEach((x) => x.classList.remove("active", "done"));
    $("#progressBar").style.width = "0%";
  };

  const showError = (msg) => {
    $("#errorBox").textContent = msg;
    $("#errorBox").classList.add("show");
  };

  const clearError = () => {
    $("#errorBox").classList.remove("show");
  };

  function renderVideo(sec = 0) {
    const currentSec = Math.max(0, Math.floor(Number(sec) || 0));
    $("#currentTime").textContent = formatTime(currentSec);
    const duration = Number(analysis?.video?.duration) || 0;
    $("#durationTime").textContent = formatTime(duration);
    $("#videoProgress").style.width = duration ? `${Math.min(100, (currentSec / duration) * 100)}%` : "0%";

    const shell = $("#videoShell");
    if (location.protocol === "file:") {
      const thumb = analysis?.video?.thumbnail || `https://i.ytimg.com/vi/${ytId}/hqdefault.jpg`;
      shell.innerHTML = `<div class="video-fallback" style="background-image:url('${thumb}')"><a target="_blank" href="https://www.youtube.com/watch?v=${ytId}&t=${currentSec}s">Open on YouTube at ${formatTime(currentSec)}</a></div>`;
    } else {
      const start = Math.floor(currentSec);
      const origin = encodeURIComponent(location.origin);
      shell.innerHTML = `<iframe src="https://www.youtube.com/embed/${encodeURIComponent(ytId)}?start=${start}&rel=0&playsinline=1&origin=${origin}" title="Study video" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>`;
    }
  }

  function seek(sec) {
    renderVideo(sec);
    window.scrollTo({ top: $("#study").offsetTop - 60, behavior: "smooth" });
  }

  function renderStudy(a) {
    analysis = a;
    ytId = a.video?.id || ytId;

    $("#studyTitle").textContent = a.video?.title || "Study";
    $("#studyLead").textContent = a.summary?.overview || "";
    $("#studyMeta").innerHTML = [
      `<span><strong>${a.video?.channel || "YouTube"}</strong> · source</span>`,
      `<span><strong>${formatTime(a.video?.duration || 0)}</strong> · duration</span>`,
      `<span><strong>${a.snippets?.length || 0}</strong> · captions</span>`,
      `<span><strong>${a.nodes?.length || 0}</strong> · concepts</span>`,
    ].join("");

    renderVideo(a.segments?.[0]?.start || 0);
    renderToc(a);
    renderGraph(a);
    renderSections(a);
    renderTranscript(a);
    renderQuality(a);
  }

  function renderToc(a) {
    const toc = $("#toc");
    toc.innerHTML = "";
    (a.segments || []).forEach((s, i) => {
      const b = document.createElement("button");
      b.innerHTML = `<span class="t">${formatTime(s.start)}</span><span class="label">${s.id}</span>`;
      b.onclick = () => seek(s.start);
      toc.appendChild(b);
    });
  }

  function nodeLevels(nodes, rels) {
    const ids = new Set(nodes.map((n) => n.id));
    const incoming = new Map(nodes.map((n) => [n.id, 0]));
    rels.forEach((r) => {
      if (ids.has(r.source) && ids.has(r.target)) incoming.set(r.target, (incoming.get(r.target) || 0) + 1);
    });
    const level = new Map(nodes.map((n) => [n.id, 0]));
    let changed = true,
      iter = 0;
    while (changed && iter++ < nodes.length) {
      changed = false;
      for (const r of rels) {
        if (!ids.has(r.source) || !ids.has(r.target)) continue;
        const next = Math.min(4, (level.get(r.source) || 0) + 1);
        if (next > (level.get(r.target) || 0)) {
          level.set(r.target, next);
          changed = true;
        }
      }
    }
    return level;
  }

  function renderGraph(a) {
    const inner = $("#graphInner");
    inner.querySelectorAll(".node").forEach((n) => n.remove());
    const svg = $("#edgeSvg");
    svg.innerHTML = "";

    const nodes = (a.nodes || []).slice(0, 18);
    const rels = (a.relationships || [])
      .filter((r) => nodes.some((n) => n.id === r.source) && nodes.some((n) => n.id === r.target))
      .slice(0, 28);

    const levels = nodeLevels(nodes, rels);
    const columns = new Map();
    nodes.forEach((n) => {
      const l = levels.get(n.id) || 0;
      if (!columns.has(l)) columns.set(l, []);
      columns.get(l).push(n);
    });

    const pos = new Map();
    let maxY = 0;
    for (const [l, col] of columns) {
      col.forEach((n, i) => {
        const x = 40 + l * 220,
          y = 34 + i * 100;
        pos.set(n.id, { x, y });
        maxY = Math.max(maxY, y + 90);

        const btn = document.createElement("button");
        btn.className = `node ${n.source || "ontology"}`;
        btn.style.left = x + "px";
        btn.style.top = y + "px";
        btn.dataset.id = n.id;
        btn.innerHTML = `<div class="k">${n.kind || "node"}</div><div class="name">${n.label}</div>`;
        inner.appendChild(btn);
      });
    }

    inner.style.minHeight = Math.max(500, maxY + 60) + "px";
    inner.style.minWidth = Math.max(940, (Math.max(...[...columns.keys(), 0]) + 1) * 220 + 260) + "px";
    svg.setAttribute("viewBox", `0 0 ${parseInt(inner.style.minWidth)} ${parseInt(inner.style.minHeight)}`);

    rels.forEach((r) => {
      const p1 = pos.get(r.source),
        p2 = pos.get(r.target);
      if (!p1 || !p2) return;
      const x1 = p1.x + 180,
        y1 = p1.y + 35,
        x2 = p2.x,
        y2 = p2.y + 35,
        mx = (x1 + x2) / 2;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`);
      svg.appendChild(path);
    });
  }

  function renderSections(a) {
    $("#sectionGrid").innerHTML = (a.segments || [])
      .map(
        (s, i) =>
          `<article class="section-card" id="section-${i}"><div class="num">${String(i + 1).padStart(2, "0")} · ${formatTime(s.start)}</div><h3>${s.id}</h3><p>${(s.text || "").substring(0, 160)}…</p><div class="meta"><button class="timestamp" data-seek="${s.start}">watch ${formatTime(s.start)}</button></div></article>`
      )
      .join("");
    $$("#sectionGrid [data-seek]").forEach((b) => (b.onclick = () => seek(b.dataset.seek)));
  }

  function renderTranscript(a) {
    const rows = (a.snippets || []).slice(0, 120);
    $("#transcriptList").innerHTML = rows
      .map(
        (s, i) =>
          `<div class="transcript-row"><button data-seek="${s.start}">${formatTime(s.start)}</button><div><p>${s.text}</p></div></div>`
      )
      .join("");
    $$("#transcriptList [data-seek]").forEach((b) => (b.onclick = () => seek(b.dataset.seek)));
  }

  function renderQuality(a) {
    const q = a.quality || {};
    const cards = [
      ["Segments", q.segments || (a.snippets?.length || 0)],
      ["Nodes", q.resolvedNodes || (a.nodes?.length || 0)],
      ["Relations", q.relationships || (a.relationships?.length || 0)],
      ["Warnings", (q.warnings || []).length],
    ];
    $("#qualityGrid").innerHTML = cards.map(([k, v]) => `<div class="q"><b>${v}</b><span>${k}</span></div>`).join("");
    $("#warningList").innerHTML = (q.warnings || []).map((w) => `<li>${w}</li>`).join("");
  }

  // Initialize
  if (window.GA_STUDY_KNOWLEDGE) {
    rag = GAStudyRag.create(window.GA_STUDY_KNOWLEDGE);
  }

  $("#analyzeBtn").addEventListener("click", async () => {
    clearError();
    resetStages();
    $("#study").classList.remove("show");

    ytId = getYoutubeId($("#videoUrl").value.trim());
    if (!ytId) {
      showError("Paste a valid YouTube URL.");
      return;
    }

    $("#analyzeBtn").disabled = true;
    const stages = ["metadata", "transcript", "rag", "rendering"];
    for (const s of stages) setStage(s, "active");

    try {
      const endpoint = window.GA_CONFIG?.transcriptEndpoint;
      if (!endpoint) throw new Error("Transcript endpoint not configured");

      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 20000);

      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ url: $("#videoUrl").value.trim(), languages: ["en", "pt-BR", "pt"] }),
        signal: controller.signal,
      });

      clearTimeout(timer);

      if (!response.ok) {
        const err = await response.json().catch(() => ({ error: { message: `HTTP ${response.status}` } }));
        throw new Error(err.error?.message || `HTTP ${response.status}`);
      }

      const payload = await response.json();
      setStage("metadata", "done");
      setStage("transcript", "done");

      // Run RAG
      if (rag) {
        for (const seg of payload.segments) {
          const hits = rag.search(seg.text, { limit: 6 });
          seg.rag = hits;
        }
      }
      setStage("rag", "done");

      renderStudy(payload);
      setStage("rendering", "done");
      $("#study").classList.add("show");
      setTimeout(() => $("#study").scrollIntoView({ behavior: "smooth", block: "start" }), 100);
    } catch (e) {
      showError(e.message || String(e));
    } finally {
      $("#analyzeBtn").disabled = false;
    }
  });
})();
