/* Plate Stream front end. No dependencies, no network calls except to this server. */
(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const pct = (v) => `${Math.round(v * 100)}%`;
  const fmtTime = (s) => {
    const m = Math.floor(s / 60);
    return `${String(m).padStart(2, "0")}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
  };
  const fmtClock = (s) => {
    s = Math.max(0, Math.round(s));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  };
  // Confidence bands describe how well the OCR readings agree; the number is always shown too.
  const band = (c) => (c >= 0.6 ? "high" : c >= 0.4 ? "check" : "low");
  const BAND_LABEL = { high: "✓ High", check: "◐ Check", low: "▲ Low" };
  const charQuality = (p) => (p >= 0.8 ? "ok" : p >= 0.5 ? "mid" : "low");
  const emptyItem = document.getElementById("plates-empty");
  const BUDGET_MB = 512;
  const CHUNK = 4 * 1024 * 1024;
  const EVENT_TYPES = ["start", "frame", "progress", "reading", "plates", "stats", "done", "error", "cancelled"];

  const state = {
    jobId: null, source: null, abort: null, finished: true,
    duration: 0, startedAt: 0, lastProgress: 0,
    records: [], evidence: new Map(), labelByTrack: new Map(),
    img: null, imgToken: 0, boxes: [], seenBoxes: new Set(), flashUntil: 0,
    colors: {}, uploadDone: false,
  };

  // ---------------------------------------------------------------- helpers
  function readColors() {
    const css = getComputedStyle(document.documentElement);
    for (const name of ["ok", "warn", "bad", "accent", "text"]) {
      state.colors[name] = css.getPropertyValue(`--${name}`).trim() || "#fff";
    }
  }

  function setStatus(kind, text) {
    $("#status").dataset.state = kind;
    $("#status-text").textContent = text;
  }

  function setMode(mode) {
    $("#stage").dataset.mode = mode;
  }

  function showBanner(kind, message) {
    const banner = $("#banner");
    banner.dataset.kind = kind;
    banner.textContent = message;
    banner.hidden = false;
  }

  function hideBanner() {
    $("#banner").hidden = true;
  }

  function show(selector, visible) {
    $(selector).hidden = !visible;
  }

  async function api(path, options) {
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const err = new Error(data.error || `Request failed (${response.status})`);
      err.status = response.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // ---------------------------------------------------------------- reset
  function resetUI() {
    state.records = [];
    state.evidence.clear();
    state.labelByTrack.clear();
    state.seenBoxes.clear();
    state.boxes = [];
    state.img = null;
    state.duration = 0;
    state.lastProgress = 0;
    state.uploadDone = false;
    $("#plate-list").replaceChildren(emptyItem);
    emptyItem.hidden = false;
    $("#feed").replaceChildren();
    $("#pins").replaceChildren();
    $("#count").textContent = "0";
    $("#plates-note").textContent = "";
    $("#up-fill").style.width = "0";
    $("#an-fill").style.width = "0";
    $("#up-val").textContent = "waiting for a file";
    $("#an-val").textContent = "waiting for a file";
    $("#summary").hidden = true;
    $("#summary").replaceChildren();
    for (const id of ["#cancel", "#dl-json", "#dl-csv", "#again"]) show(id, false);
    for (const li of document.querySelectorAll("#lat li")) {
      li.querySelector(".val").textContent = "-";
      li.querySelector("i").style.width = "0";
    }
    for (const id of ["#t-rtf", "#t-proc", "#t-skip", "#t-elapsed"]) $(id).textContent = "-";
    hideBanner();
    draw();
  }

  // ---------------------------------------------------------------- start a run
  async function startFile(file) {
    if (!file || !state.finished) return;
    resetUI();
    setStatus("uploading", "Uploading");
    $("#up-val").textContent = `0 / ${(file.size / 1048576).toFixed(0)} MB`;
    try {
      const job = await api("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: file.name, size: file.size, preset: $("#preset").value }),
      });
      begin(job.id);
      upload(file, job.id);
    } catch (err) {
      fail(err.message);
    }
  }

  async function startSample(name) {
    if (!state.finished) return;
    resetUI();
    setStatus("analyzing", "Starting");
    try {
      const job = await api("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sample: name, preset: $("#preset").value }),
      });
      $("#up-fill").style.width = "100%";
      $("#up-val").textContent = "read from disk";
      state.uploadDone = true;
      begin(job.id);
    } catch (err) {
      fail(err.message);
    }
  }

  function begin(jobId) {
    state.jobId = jobId;
    state.finished = false;
    show("#cancel", true);
    connect(jobId);
  }

  // Sends one chunk. A network error or a mismatch is retried after asking the server how many
  // bytes it already has, so a hiccup never loses or duplicates data.
  async function sendChunk(file, jobId, offset) {
    const attempts = 5;
    for (let attempt = 1; ; attempt++) {
      const end = Math.min(offset + CHUNK, file.size);
      try {
        const reply = await api(`/api/jobs/${jobId}/data?offset=${offset}`, {
          method: "PUT",
          body: file.slice(offset, end),
          signal: state.abort.signal,
        });
        return reply.received;
      } catch (err) {
        if (err.name === "AbortError" || state.finished) throw err;
        if (err.status === 410 || err.status === 404 || attempt === attempts) throw err;
        await sleep(300 * attempt);
        if (err.data && typeof err.data.received === "number") return err.data.received; // resync
        // Ask how much the server already has: part of this chunk may have arrived.
        const known = await api(`/api/jobs/${jobId}/progress`).catch(() => null);
        if (known && known.received > offset) return known.received;
      }
    }
  }

  async function upload(file, jobId) {
    state.abort = new AbortController();
    const started = performance.now();
    let offset = 0;
    try {
      while (offset < file.size) {
        offset = await sendChunk(file, jobId, offset);
        const seconds = (performance.now() - started) / 1000;
        $("#up-fill").style.width = `${(offset / file.size) * 100}%`;
        $("#up-val").textContent =
          `${(offset / 1048576).toFixed(0)} / ${(file.size / 1048576).toFixed(0)} MB` +
          (seconds > 0.5 ? ` · ${(offset / 1048576 / seconds).toFixed(0)} MB/s` : "");
      }
      state.uploadDone = true;
    } catch (err) {
      if (err.name === "AbortError" || state.finished) return;
      // Give up cleanly: free the server's slot, then say exactly what went wrong.
      fetch(`/api/jobs/${jobId}/cancel`, { method: "POST" }).catch(() => {});
      const detail = err.message === "Failed to fetch" ? "the connection to the server was lost" : err.message;
      fail(`Upload failed at ${Math.round((offset / file.size) * 100)}%: ${detail}.`);
    }
  }

  // ---------------------------------------------------------------- event stream
  function connect(jobId) {
    state.source?.close();
    const source = new EventSource(`/api/jobs/${jobId}/events`);
    state.source = source;
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, (e) => {
        // The browser also fires a data-less "error" event when the connection closes.
        if (typeof e.data === "string") handle(type, JSON.parse(e.data));
      });
    }
    source.onopen = () => {
      if ($("#banner").dataset.kind === "warn") hideBanner();
    };
    source.onerror = () => {
      if (!state.finished) showBanner("warn", "Connection lost. Reconnecting...");
    };
  }

  function handle(type, data) {
    switch (type) {
      case "start": return onStart(data);
      case "frame": return onFrame(data);
      case "progress": return onProgress(data);
      case "reading": return onReading(data);
      case "plates": return onPlates(data);
      case "stats": return onStats(data);
      case "done": return onDone(data);
      case "error": return fail(data.message);
      case "cancelled": return onCancelled(data);
    }
  }

  function endRun(statusKind, statusText) {
    state.finished = true;
    state.source?.close();
    state.abort?.abort();
    setStatus(statusKind, statusText);
    show("#cancel", false);
    show("#again", true);
  }

  function fail(message) {
    endRun("error", "Failed");
    setMode(state.records.length ? "done" : "idle");
    showBanner("error", message || "Something went wrong.");
  }

  function onCancelled(data) {
    endRun("cancelled", "Stopped");
    setMode(state.records.length ? "done" : "idle");
    showBanner("warn", data.message || "Stopped early.");
  }

  function onStart(data) {
    state.duration = data.video.duration_s || 0;
    state.startedAt = performance.now();
    setStatus("analyzing", "Analysing");
    setMode("live");
    $("#an-val").textContent = "starting";
    $("#plates-note").textContent = `${data.video.width}×${data.video.height} · ${data.video.codec}`;
  }

  // ---------------------------------------------------------------- frames and canvas
  function onFrame(data) {
    updateProgress(data.progress, data.t);
    $("#hud-time").textContent = fmtTime(data.t);
    $("#hud-kind").textContent = data.kind === "tiled" ? "deep scan (3 tiles)" : "quick scan";
    state.boxes = data.boxes;
    for (const b of data.boxes) {
      if (!state.seenBoxes.has(b.id)) {
        state.seenBoxes.add(b.id);
        state.flashUntil = performance.now() + 700;
      }
    }
    const token = ++state.imgToken;
    const img = new Image();
    img.onload = () => {
      if (token === state.imgToken) {
        state.img = img;
        animate();
      }
    };
    img.src = `data:image/jpeg;base64,${data.jpeg}`;
  }

  function onProgress(data) {
    updateProgress(data.progress, data.t);
    $("#hud-time").textContent = fmtTime(data.t);
    $("#hud-kind").textContent = "unchanged scene, skipped";
  }

  function updateProgress(progress, t) {
    state.lastProgress = progress;
    $("#an-fill").style.width = `${progress * 100}%`;
    const elapsed = (performance.now() - state.startedAt) / 1000;
    const eta = progress > 0.04 ? elapsed / progress - elapsed : null;
    $("#an-val").textContent =
      `${fmtTime(t)} / ${fmtTime(state.duration)} · ${pct(progress)}` +
      (eta !== null ? ` · ETA ${fmtClock(eta)}` : "");
  }

  function animate() {
    draw();
    if (performance.now() < state.flashUntil && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      requestAnimationFrame(animate);
    }
  }

  function draw() {
    const canvas = $("#view");
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (!w || !h) return;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const g = canvas.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    if (!state.img) return;
    const iw = state.img.naturalWidth;
    const ih = state.img.naturalHeight;
    const scale = Math.min(w / iw, h / ih);
    const dw = iw * scale;
    const dh = ih * scale;
    const ox = (w - dw) / 2;
    const oy = (h - dh) / 2;
    g.drawImage(state.img, ox, oy, dw, dh);

    const flashing = performance.now() < state.flashUntil;
    g.font = "600 12px ui-monospace, Consolas, monospace";
    g.textBaseline = "middle";
    for (const b of state.boxes) {
      const colorName = { high: "ok", check: "warn", low: "bad" }[band(b.conf)];
      const color = state.colors[colorName];
      const x = ox + b.x * dw;
      const y = oy + b.y * dh;
      const bw = b.w * dw;
      const bh = b.h * dh;
      g.lineWidth = flashing ? 3.5 : 2;
      g.strokeStyle = color;
      g.strokeRect(x, y, bw, bh);
      const voted = state.labelByTrack.get(b.id);
      const label = `${voted || b.text}${voted ? "" : " ?"}`;
      const padX = 6;
      const tw = g.measureText(label).width + padX * 2;
      const ly = Math.max(oy + 2, y - 20);
      g.fillStyle = "rgba(5, 8, 12, 0.82)";
      g.fillRect(x, ly, tw, 18);
      g.fillStyle = color;
      g.fillText(label, x + padX, ly + 9.5);
    }
  }

  // ---------------------------------------------------------------- OCR stream
  function onReading(data) {
    const list = state.evidence.get(data.id) || [];
    list.push({ t: data.t, text: data.text, conf: data.conf, crop: data.crop });
    list.sort((a, b) => b.conf - a.conf);
    state.evidence.set(data.id, list.slice(0, 4));

    const row = make("li");
    row.dataset.band = band(data.conf);
    const img = make("img");
    img.alt = "";
    img.src = `data:image/jpeg;base64,${data.crop}`;
    row.append(img, make("span", "t num", fmtTime(data.t)), make("span", "txt mono", data.text), make("span", "c num", pct(data.conf)));
    const feed = $("#feed");
    feed.prepend(row);
    while (feed.children.length > 60) feed.lastChild.remove();
  }

  // ---------------------------------------------------------------- plates
  function onPlates(data) {
    const plates = data.plates;
    const keep = new Set();
    for (const p of plates) {
      let rec = state.records.find((r) => p.ids.some((id) => r.ids.has(id)));
      if (!rec) {
        rec = createRecord(p);
        state.records.push(rec);
        $("#live").textContent = `New plate ${p.text.split("").join(" ")}, confidence ${pct(p.confidence)}`;
      }
      rec.ids = new Set(p.ids);
      updateRecord(rec, p);
      keep.add(rec);
    }
    for (const rec of [...state.records]) {
      if (!keep.has(rec)) {
        rec.el.remove();
        rec.pin.remove();
        state.records.splice(state.records.indexOf(rec), 1);
      }
    }
    state.records.sort((a, b) => a.first - b.first);
    const list = $("#plate-list");
    state.records.forEach((rec, index) => {
      if (list.children[index] !== rec.el) list.insertBefore(rec.el, list.children[index] || null);
    });
    state.labelByTrack.clear();
    for (const rec of state.records) for (const id of rec.ids) state.labelByTrack.set(id, rec.text);
    emptyItem.hidden = state.records.length > 0;
    $("#count").textContent = String(state.records.length);
    draw();
  }

  function createRecord(p) {
    const el = make("li", "card fresh");
    el.tabIndex = 0;
    el.setAttribute("role", "button");
    el.setAttribute("aria-expanded", "false");
    const thumb = make("img", "thumb");
    thumb.alt = "";
    const main = make("div", "card-main");
    const plate = make("div", "plate");
    const meta = make("div", "meta");
    const conf = make("span", "conf");
    const bar = make("span", "conf-bar");
    bar.append(make("b"));
    conf.append(bar, make("strong", "num"), make("span", "band"));
    meta.append(conf, make("span", "readings num"), make("span", "span num"), make("span", "fmt"));
    main.append(plate, meta);
    const evidence = make("div", "evidence");
    evidence.hidden = true;
    el.append(thumb, main, evidence);

    const pin = make("button", "pin");
    pin.type = "button";
    $("#pins").append(pin);
    const rec = { el, pin, thumb, plate, meta, evidence, ids: new Set(p.ids), text: "", first: p.first_seen, expanded: false };
    const toggle = () => {
      rec.expanded = !rec.expanded;
      el.setAttribute("aria-expanded", String(rec.expanded));
      evidence.hidden = !rec.expanded;
      if (rec.expanded) renderEvidence(rec);
    };
    el.addEventListener("click", toggle);
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggle();
      }
    });
    pin.addEventListener("click", () => {
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
      el.focus();
    });
    setTimeout(() => el.classList.remove("fresh"), 1600);
    return rec;
  }

  function updateRecord(rec, p) {
    rec.first = p.first_seen;
    const b = band(p.confidence);
    rec.el.dataset.band = b;
    // plate characters, with a short flash on any character the vote has just changed
    const chars = p.text.split("");
    if (rec.plate.children.length !== chars.length) {
      rec.plate.replaceChildren(...chars.map(() => make("span", "ch")));
    }
    chars.forEach((c, i) => {
      const span = rec.plate.children[i];
      const old = span.textContent;
      span.textContent = c;
      const share = p.chars[i] ?? 1;
      span.style.setProperty("--p", share.toFixed(2));
      span.dataset.q = charQuality(share);
      if (old && old !== c) {
        span.classList.remove("changed");
        void span.offsetWidth;
        span.classList.add("changed");
      }
    });
    rec.plate.setAttribute("aria-label", `Plate ${chars.join(" ")}`);
    rec.text = p.text;
    $(".conf-bar b", rec.meta).style.width = pct(p.confidence);
    $("strong", rec.meta).textContent = pct(p.confidence);
    $(".band", rec.meta).textContent = BAND_LABEL[b];
    $(".readings", rec.meta).textContent = `${p.readings} reading${p.readings === 1 ? "" : "s"}`;
    $(".span", rec.meta).textContent = `${fmtTime(p.first_seen)} – ${fmtTime(p.last_seen)}`;
    $(".fmt", rec.meta).textContent = p.format ? "CA format" : "other format";
    // pin on the timeline
    const left = state.duration ? clamp(p.first_seen / state.duration, 0, 1) * 100 : 0;
    rec.pin.style.left = `${left}%`;
    const label = `${p.text} first seen at ${fmtTime(p.first_seen)}`;
    rec.pin.title = label;
    rec.pin.setAttribute("aria-label", label);
    // best crop as the thumbnail
    const best = bestEvidence(rec);
    if (best && rec.thumb.dataset.src !== best.crop) {
      rec.thumb.dataset.src = best.crop;
      rec.thumb.src = `data:image/jpeg;base64,${best.crop}`;
    }
    if (rec.expanded) renderEvidence(rec);
  }

  function evidenceFor(rec) {
    const all = [];
    for (const id of rec.ids) all.push(...(state.evidence.get(id) || []));
    return all.sort((a, b) => b.conf - a.conf);
  }

  function bestEvidence(rec) {
    return evidenceFor(rec)[0];
  }

  function renderEvidence(rec) {
    const items = evidenceFor(rec).slice(0, 4);
    rec.evidence.replaceChildren(
      ...items.map((item) => {
        const fig = make("figure");
        const img = make("img");
        img.alt = `Crop read as ${item.text}`;
        img.src = `data:image/jpeg;base64,${item.crop}`;
        const cap = make("figcaption");
        cap.append(make("span", "mono", item.text), make("span", "num", pct(item.conf)));
        fig.append(img, cap);
        return fig;
      }),
    );
  }

  // ---------------------------------------------------------------- stats
  function onStats(data) {
    const scale = 400;
    for (const li of document.querySelectorAll("#lat li")) {
      const ms = data.latency_ms[li.dataset.key];
      if (ms === undefined) continue;
      li.querySelector(".val").textContent = `${ms.toFixed(1)} ms`;
      li.querySelector("i").style.width = `${clamp((ms / scale) * 100, ms > 0 ? 1.5 : 0, 100)}%`;
    }
    const mem = data.memory_mb;
    $("#mem-fill").style.width = `${clamp((mem / BUDGET_MB) * 100, 0, 100)}%`;
    $("#mem-fill").dataset.hot = mem > BUDGET_MB * 0.85 ? "1" : "0";
    $("#mem-text").textContent = `${mem.toFixed(0)} / ${BUDGET_MB} MB`;
    $("#t-rtf").textContent = data.rtf ? `${data.rtf.toFixed(2)}x` : "-";
    $("#t-proc").textContent = String(data.frames.processed);
    $("#t-skip").textContent = String(data.frames.skipped);
    $("#t-elapsed").textContent = fmtClock(data.elapsed_s);
  }

  // ---------------------------------------------------------------- finish
  function onDone(data) {
    endRun("done", "Finished");
    setMode("done");
    $("#an-fill").style.width = "100%";
    $("#an-val").textContent = `${fmtTime(state.duration)} / ${fmtTime(state.duration)} · 100%`;
    for (const rec of state.records) rec.el.classList.remove("fresh");
    const run = data.run;
    const summary = $("#summary");
    summary.hidden = false;
    const total = data.plates.length;
    summary.replaceChildren(
      make("strong", "", `${total} plate${total === 1 ? "" : "s"} found`),
      make(
        "span",
        "sub num",
        `${run.timing_s.total.toFixed(1)} s · ${run.throughput.real_time_factor}x real time · peak ${run.memory.peak_rss_mb} MB of ${BUDGET_MB} MB`,
      ),
    );
    $("#dl-json").href = `/api/jobs/${state.jobId}/result.json`;
    $("#dl-csv").href = `/api/jobs/${state.jobId}/result.csv`;
    show("#dl-json", true);
    show("#dl-csv", true);
    $("#mem-text").textContent = `${run.memory.peak_rss_mb} / ${BUDGET_MB} MB peak`;
    if (total === 0) showBanner("warn", "No readable plates were found in this clip.");
    $("#live").textContent = `Finished. ${total} plates found.`;
  }

  // ---------------------------------------------------------------- init and events
  function backToIdle() {
    state.finished = true;
    resetUI();
    setMode("idle");
    setStatus("idle", "Ready");
  }

  async function init() {
    readColors();
    try {
      const info = await api("/api/info");
      const select = $("#preset");
      for (const name of info.presets) select.append(new Option(name, name, false, name === "balanced"));
      if (info.samples.length) {
        show("#samples", true);
        for (const s of info.samples) {
          const chip = make("button", "sample", `${s.name.replace(/\.[^.]+$/, "")} · ${s.size_mb.toFixed(0)} MB`);
          chip.type = "button";
          chip.addEventListener("click", () => startSample(s.name));
          $("#sample-chips").append(chip);
        }
      }
    } catch (err) {
      showBanner("error", `Cannot reach the server: ${err.message}`);
    }

    $("#choose").addEventListener("click", () => $("#file").click());
    $("#file").addEventListener("change", (e) => {
      startFile(e.target.files[0]);
      e.target.value = "";
    });
    $("#cancel").addEventListener("click", () => {
      state.abort?.abort();
      if (state.jobId) fetch(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
    });
    $("#again").addEventListener("click", backToIdle);
    // Closing or reloading the tab mid-run must free the server for the next clip.
    window.addEventListener("pagehide", () => {
      if (!state.finished && state.jobId) {
        navigator.sendBeacon(`/api/jobs/${state.jobId}/cancel`);
      }
    });

    const drop = $("#drop");
    const stage = $("#stage");
    for (const type of ["dragenter", "dragover"]) {
      stage.addEventListener(type, (e) => {
        e.preventDefault();
        if (state.finished) drop.dataset.over = "1";
      });
    }
    stage.addEventListener("dragleave", () => { drop.dataset.over = "0"; });
    stage.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.dataset.over = "0";
      if (state.finished && e.dataTransfer.files.length) {
        if ($("#stage").dataset.mode !== "idle") backToIdle();
        startFile(e.dataTransfer.files[0]);
      }
    });
    for (const type of ["dragover", "drop"]) window.addEventListener(type, (e) => e.preventDefault());

    $("#theme").addEventListener("click", () => {
      const root = document.documentElement;
      const dark = root.dataset.theme ? root.dataset.theme === "dark" : !matchMedia("(prefers-color-scheme: light)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("theme", root.dataset.theme); } catch (_) { /* private mode */ }
      readColors();
      draw();
    });
    try {
      const saved = localStorage.getItem("theme");
      if (saved) document.documentElement.dataset.theme = saved;
    } catch (_) { /* storage blocked */ }
    readColors();
    new ResizeObserver(draw).observe($("#stage"));
    $("#stage").style.setProperty("--stage-h", `${$("#stage").clientHeight}px`);
    new ResizeObserver(() => $("#stage").style.setProperty("--stage-h", `${$("#stage").clientHeight}px`)).observe($("#stage"));
    setMode("idle");
  }

  init();
})();
