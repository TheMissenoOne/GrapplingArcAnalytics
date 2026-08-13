/**
 * Study orchestrator — calls the local Python endpoint, runs client-side RAG, renders results.
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
    $("#progressBar").style.transform = "scaleX(0)";
  };

  const showError = (msg) => {
    $("#errorBox").textContent = msg;
    $("#errorBox").classList.add("show");
  };

  const clearError = () => {
    $("#errorBox").classList.remove("show");
  };

  const urlsFromInput = () => [...new Set($("#videoUrls").value.split(/\r?\n/).map((u) => u.trim()).filter(Boolean))];

  const renderQueue = (items) => {
    const queue = $("#batchQueue");
    queue.replaceChildren();
    items.forEach((item, i) => {
      const row = document.createElement("div");
      row.className = `queue-row ${item.state}`;
      row.innerHTML = `<span class="queue-index"></span><span class="queue-url"></span><span class="queue-state"></span>`;
      row.querySelector(".queue-index").textContent = String(i + 1).padStart(2, "0");
      row.querySelector(".queue-url").textContent = item.title || item.url;
      row.querySelector(".queue-state").textContent = item.stateLabel || item.state;
      if (item.links) {
        const links = document.createElement("span");
        links.className = "queue-links";
        links.innerHTML = `<a target="_blank">HTML</a> <a download>JSON</a>`;
        links.querySelector("a").href = item.links.html;
        links.querySelectorAll("a")[1].href = item.links.json;
        row.append(links);
      }
      queue.append(row);
    });
  };

  const addSavedReport = (report) => {
    const list = $("#reportList");
    list.querySelector(".empty-reports")?.remove();
    const row = document.createElement("div");
    row.className = "report-row";
    row.innerHTML = `<div><strong></strong><span></span></div><div class="report-links"><a target="_blank">Open HTML</a><a download>Download JSON</a></div>`;
    row.querySelector("strong").textContent = report.title;
    row.querySelector("span").textContent = report.video_id;
    row.querySelectorAll("a")[0].href = `/admin/study/reports/${report.id}.html`;
    row.querySelectorAll("a")[1].href = `/admin/study/reports/${report.id}.json`;
    list.prepend(row);
    $("#reportCount").textContent = String(Number($("#reportCount").textContent || 0) + 1);
  };

  const saveReport = async (payload) => {
    const response = await fetch("/admin/study/reports", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error?.message || `Report save failed (HTTP ${response.status})`);
    return result;
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

    const urls = urlsFromInput();
    if (!urls.length) {
      showError("Paste at least one YouTube URL.");
      return;
    }
    const items = urls.map((url) => ({ url, state: "queued", stateLabel: "queued" }));
    renderQueue(items);

    $("#analyzeBtn").disabled = true;
    $("#pipeline").classList.add("show");
    for (let i = 0; i < items.length; i += 1) {
      const item = items[i];
      item.state = "processing"; item.stateLabel = `processing ${i + 1}/${items.length}`; renderQueue(items);
      try {
        ytId = getYoutubeId(item.url);
        if (!ytId) throw new Error("Invalid YouTube URL");
        const endpoint = window.GA_CONFIG?.transcriptEndpoint;
        if (!endpoint) throw new Error("Local transcript endpoint not configured");
        const response = await fetch(endpoint, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: item.url, languages: ["en", "pt-BR", "pt"] }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error?.message || `HTTP ${response.status}`);
        if (rag) payload.segments?.forEach((seg) => { seg.rag = rag.search(seg.text, { limit: 6 }); });
        const report = await saveReport(payload);
        item.title = payload.video?.title || item.url; item.state = "done"; item.stateLabel = "complete";
        item.links = { html: `/admin/study/reports/${report.id}.html`, json: `/admin/study/reports/${report.id}.json` };
        addSavedReport(report); renderStudy(payload); $("#study").classList.add("show");
      } catch (e) {
        item.state = "error"; item.stateLabel = e.message || String(e);
      }
      $("#progressBar").style.transform = `scaleX(${(i + 1) / items.length})`;
      renderQueue(items);
    }
    $("#analyzeBtn").disabled = false;
  });
})();
