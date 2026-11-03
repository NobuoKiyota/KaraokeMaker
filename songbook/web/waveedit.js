"use strict";
/* 波形編集(編集者のPCだけ): 音源の波形を見ながら、セクションの頭(ループ・ジャンプの位置)を細かく合わせて、曲のテキストに保存する。
 * 位置は「音源の秒」で扱う。曲のテキストには、そこから {offset}(ビートの遅れ)を引いた「小節の頭の時刻」を書く。 */

const SONGS = loadSongs();
const PRE = 0.01;                 // 閲覧画面のループ・ジャンプと同じ「拍の少し手前」(app.js の LOOP_PREROLL)
const $ = (id) => document.getElementById(id);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

const st = {
  id: null, entry: null, lines: [], heads: [], bpm: 120, offset: 0.05, hasOffsetLine: false,
  buf: null, mono: null, mipMin: null, mipMax: null, env: null, envDt: 0, sr: 48000, dur: 0,
  pps: 20, cursor: 0, dirty: false, drag: -1,
  ctx: null, src: null, playing: false, pos0: 0, ct0: 0, loop: null, // loop: {idx, a, b}
  undoStack: [], redoStack: [], lastOp: { key: "", at: 0 }, savedKey: "",
};
const MIP = 64;

/* ---------------- 時刻の表記 ---------------- */
function fmt3(t) {
  t = Math.max(0, t);
  let ms = Math.round(t * 1000);
  const m = Math.floor(ms / 60000); ms -= m * 60000;
  const s = Math.floor(ms / 1000); ms -= s * 1000;
  return `${m}:${String(s).padStart(2, "0")}` + (ms ? "." + String(ms).padStart(3, "0").replace(/0+$/, "") : "");
}
const fmtClock = (t) => { const ms = Math.round(Math.max(0, t) * 1000); return `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}.${String(ms % 1000).padStart(3, "0")}`; };

/* ---------------- 曲のテキスト ---------------- */
function parseSource(text) {
  st.lines = text.replace(/\r\n?/g, "\n").split("\n");
  st.heads = [];
  st.lines.forEach((ln, i) => {
    if (!ln.trim().startsWith("#")) return;
    const m = /^(#+\s*)(.*?)\s*(?:@\s*((?:\d+:)?\d+(?:\.\d+)?))?\s*$/.exec(ln.trim());
    if (m) st.heads.push({ line: i, prefix: m[1], label: m[2], t: m[3] !== undefined ? parseTime(m[3]) : null });
  });
  const tempo = /^\{\s*tempo\s*:\s*([\d.]+)\s*\}/m.exec(text);
  st.bpm = tempo ? Number(tempo[1]) : 120;
  const off = /^\{\s*offset\s*:\s*(-?[\d.]+)\s*\}/m.exec(text);
  st.hasOffsetLine = !!off;
  st.offset = off ? Number(off[1]) : 0.05;
  $("bpm").textContent = st.bpm;
  $("offset").value = st.offset.toFixed(3);
}

function buildSource() {
  const lines = st.lines.slice();
  st.heads.forEach((h) => { if (h.t != null) lines[h.line] = `${h.prefix}${h.label} @${fmt3(h.t)}`; });
  let text = lines.join("\n");
  const dir = `{offset: ${Math.round(st.offset * 1000) / 1000}}`;
  if (/^\{\s*offset\s*:[^}]*\}/m.test(text)) text = text.replace(/^\{\s*offset\s*:[^}]*\}/m, dir);
  else {
    const ls = text.split("\n");
    let at = 0;
    ls.forEach((ln, i) => { if (/^\{\s*(title|key|tempo|audio)\s*:/.test(ln)) at = i + 1; });
    ls.splice(at, 0, dir);
    text = ls.join("\n");
  }
  return text;
}

const pos = (h) => h.t + st.offset;                        // 音源の秒(小節の頭にあたる位置)
const timed = () => st.heads.map((h, i) => ({ h, i })).filter((x) => x.h.t != null).sort((a, b) => a.h.t - b.h.t);

function setDirty(v) {
  st.dirty = v;
  $("save").disabled = !v;
  $("msg").textContent = v ? "未保存の変更があります" : $("msg").dataset.keep || "";
  $("msg").classList.toggle("dirty", v);
}
function say(t) { $("msg").dataset.keep = t; $("msg").textContent = t; $("msg").classList.remove("dirty"); }

/* ---------------- 音源の読み込み ---------------- */
function ensureCtx() {
  if (!st.ctx) st.ctx = new (window.AudioContext || window.webkitAudioContext)({ latencyHint: "playback" });
  if (st.ctx.state !== "running") st.ctx.resume();
  return st.ctx;
}

/** spec: "drive:N"(曲のテキストのN番目のDrive音源 = 公開サイトと同じ) / "file:名前"(フォルダの mp3) / それ以外は、file(選んだファイル)の名前 */
async function loadAudio(spec, file) {
  stop(true);
  say("音源を読み込んでいます…");
  const name = (st.trackNames && st.trackNames[spec]) || spec;
  try {
    let data;
    if (file) data = await file.arrayBuffer();
    else {
      // キャッシュは使わない(入れ替えたファイルも、読み込み直す)
      const url = spec.startsWith("drive:")
        ? `/api/drive-audio/${encodeURIComponent(st.id)}?i=${Number(spec.slice(6))}&_=${Date.now()}`
        : `/api/audio/${encodeURIComponent(st.id)}?name=${encodeURIComponent(spec.replace(/^file:/, ""))}&_=${Date.now()}`;
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) { let m = res.status; try { m = (await res.json()).error || m; } catch (e) { /* 無視 */ } throw new Error(m); }
      data = await res.arrayBuffer();
    }
    const buf = await ensureCtx().decodeAudioData(data);
    st.buf = buf; st.sr = buf.sampleRate; st.dur = buf.duration;
    const n = buf.length, ch = buf.numberOfChannels, mono = new Float32Array(n);
    for (let c = 0; c < ch; c++) { const d = buf.getChannelData(c); for (let i = 0; i < n; i++) mono[i] += d[i] / ch; }
    st.mono = mono;
    const nb = Math.ceil(n / MIP);
    st.mipMin = new Float32Array(nb); st.mipMax = new Float32Array(nb);
    for (let b = 0; b < nb; b++) {
      let lo = 1, hi = -1;
      const e = Math.min(n, (b + 1) * MIP);
      for (let i = b * MIP; i < e; i++) { const v = mono[i]; if (v < lo) lo = v; if (v > hi) hi = v; }
      st.mipMin[b] = lo; st.mipMax[b] = hi;
    }
    buildEnvelope();
    st.cursor = 0;
    fit();
    say(`${file ? "選んだファイル: " : spec.startsWith("drive:") ? "Drive(公開サイトと同じ): " : "フォルダの "}${name}(${fmtClock(st.dur)} / ${st.sr} Hz)`);
    refreshLoop();
    draw();
    return true;
  } catch (e) {
    say("音源を読み込めませんでした: " + e.message);
    return false;
  }
}

/** 音の立ち上がりの強さ(エネルギーの増え方)。ビートの位置合わせと、立ち上がりへの吸着に使う */
function buildEnvelope() {
  const hop = 128, win = 512, n = st.mono.length, frames = Math.floor((n - win) / hop);
  const e = new Float32Array(frames);
  for (let f = 0; f < frames; f++) { let s = 0; const o = f * hop; for (let i = 0; i < win; i++) { const v = st.mono[o + i]; s += v * v; } e[f] = Math.sqrt(s / win); }
  const env = new Float32Array(frames);
  for (let f = 1; f < frames; f++) env[f] = Math.max(0, e[f] - e[f - 1]);
  st.env = env;
  st.envDt = hop / st.sr;
  st.envOrigin = win / 2 / st.sr; // 窓の真ん中の時刻
}

/** Excelの小節の頭と、実際のビートのずれ(秒)。テンポどおりの拍の並びに、立ち上がりが一番よく重なる位置を探す */
function detectOffset() {
  if (!st.env) return;
  pushUndo();
  const beat = 60 / st.bpm, step = 0.005;
  let best = -1, bestPh = 0;
  for (let ph = 0; ph < beat; ph += step) {
    let s = 0, c = 0;
    for (let t = ph; t < st.dur; t += beat) { const i = Math.round((t - st.envOrigin) / st.envDt); if (i >= 0 && i < st.env.length) { s += st.env[i]; c++; } }
    if (c && s / c > best) { best = s / c; bestPh = ph; }
  }
  const off = bestPh <= beat / 2 ? bestPh : bestPh - beat;
  st.offset = Math.round(off * 200) / 200;
  $("offset").value = st.offset.toFixed(3);
  setDirty(true); refreshLoop(); renderTable(); draw();
  say(`ずれを ${Math.round(st.offset * 1000)} ミリ秒と測りました`);
}

/* ---------------- 吸着 ---------------- */
function snap(t) {
  const mode = $("snap").value, bar = 240 / st.bpm, beat = 60 / st.bpm;
  if (mode === "bar") return Math.round((t - st.offset) / bar) * bar + st.offset;
  if (mode === "beat") return Math.round((t - st.offset) / beat) * beat + st.offset;
  if (mode === "onset" && st.env) {
    const lo = Math.max(0, Math.round((t - 0.05 - st.envOrigin) / st.envDt)), hi = Math.min(st.env.length - 1, Math.round((t + 0.05 - st.envOrigin) / st.envDt));
    let bi = -1, bv = 0;
    for (let i = lo; i <= hi; i++) if (st.env[i] > bv) { bv = st.env[i]; bi = i; }
    return bi >= 0 ? bi * st.envDt + st.envOrigin : t;
  }
  if (mode === "zero" && st.mono) {
    const c = Math.round(t * st.sr), w = Math.round(0.005 * st.sr);
    let bestI = -1, bestD = 1e9;
    for (let i = Math.max(1, c - w); i < Math.min(st.mono.length - 1, c + w); i++) {
      if ((st.mono[i - 1] <= 0 && st.mono[i] > 0) && Math.abs(i - c) < bestD) { bestD = Math.abs(i - c); bestI = i; }
    }
    return bestI >= 0 ? bestI / st.sr : t;
  }
  return t;
}

/* ---------------- 波形の描画 ---------------- */
const cv = $("wf"), wrap = $("wf-scroll"), spacer = $("wf-spacer");
const H = 210;
const t0 = () => wrap.scrollLeft / st.pps;
const timeToX = (t) => (t - t0()) * st.pps;

function peaks(s0, s1) {
  let lo = 1, hi = -1;
  if (s1 - s0 >= MIP * 2) {
    const b0 = Math.floor(s0 / MIP), b1 = Math.min(st.mipMin.length - 1, Math.floor(s1 / MIP));
    for (let b = b0; b <= b1; b++) { if (st.mipMin[b] < lo) lo = st.mipMin[b]; if (st.mipMax[b] > hi) hi = st.mipMax[b]; }
  } else {
    for (let i = s0; i <= s1 && i < st.mono.length; i++) { const v = st.mono[i]; if (v < lo) lo = v; if (v > hi) hi = v; }
  }
  return [lo, hi];
}

function draw() {
  const w = wrap.clientWidth, dpr = window.devicePixelRatio || 1;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(H * dpr); }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const css = getComputedStyle(document.documentElement);
  const accent = css.getPropertyValue("--ui-accent").trim() || "#2f5fd0", ink = css.getPropertyValue("--ink").trim() || "#111", soft = css.getPropertyValue("--ink-soft").trim() || "#777";
  g.clearRect(0, 0, w, H);
  const mid = (H - 22) / 2 + 4, A = (H - 30) / 2 * 0.95;
  g.fillStyle = soft; g.globalAlpha = 0.35; g.fillRect(0, mid, w, 1); g.globalAlpha = 1;
  if (!st.mono) { g.fillStyle = soft; g.fillText("音源を読み込んでいます…", 12, 24); return; }
  const left = t0(), right = left + w / st.pps;

  // ループの範囲
  if (st.loop) {
    g.fillStyle = accent; g.globalAlpha = 0.12;
    g.fillRect(timeToX(st.loop.a), 0, (st.loop.b - st.loop.a) * st.pps, H - 18); g.globalAlpha = 1;
  }
  // 波形
  g.fillStyle = ink; g.globalAlpha = 0.75;
  const spp = st.sr / st.pps;
  for (let x = 0; x < w; x++) {
    const s0 = Math.floor((left + x / st.pps) * st.sr);
    if (s0 < 0 || s0 >= st.mono.length) continue;
    const [lo, hi] = peaks(s0, Math.max(s0, Math.floor(s0 + spp)));
    const y1 = mid - hi * A, y2 = mid - lo * A;
    g.fillRect(x, y1, 1, Math.max(1, y2 - y1));
  }
  g.globalAlpha = 1;

  // 小節・拍の格子(ずれを足した位置)
  const bar = 240 / st.bpm, beat = 60 / st.bpm;
  if (beat * st.pps >= 14) {
    g.fillStyle = soft; g.globalAlpha = 0.18;
    for (let k = Math.floor((left - st.offset) / beat); ; k++) { const t = k * beat + st.offset; if (t > right) break; if (t >= left && k % 4) g.fillRect(timeToX(t), 0, 1, H - 18); }
    g.globalAlpha = 1;
  }
  if (bar * st.pps >= 6) {
    g.fillStyle = soft; g.globalAlpha = 0.45; g.font = "10px sans-serif";
    const every = bar * st.pps >= 36 ? 1 : bar * st.pps >= 18 ? 2 : 4;
    for (let k = Math.floor((left - st.offset) / bar); ; k++) {
      const t = k * bar + st.offset; if (t > right) break; if (t < left) continue;
      const x = timeToX(t); g.fillRect(x, 0, 1, H - 18);
      if (k % every === 0) g.fillText(String(k + 1), x + 2, H - 22);
    }
    g.globalAlpha = 1;
  }
  // 目盛り(秒)
  g.fillStyle = soft; g.font = "11px sans-serif";
  const stepS = st.pps >= 400 ? 0.1 : st.pps >= 80 ? 0.5 : st.pps >= 30 ? 1 : st.pps >= 12 ? 5 : 10;
  for (let t = Math.ceil(left / stepS) * stepS; t < right; t += stepS) { const x = timeToX(t); g.fillRect(x, H - 16, 1, 5); g.fillText(fmtClock(t).replace(/\.0+$/, ""), x + 3, H - 4); }

  // セクションの頭
  st.heads.forEach((h, i) => {
    if (h.t == null) return;
    const x = timeToX(pos(h));
    if (x < -80 || x > w + 8) return;
    const on = st.drag === i || (st.loop && (st.loop.idx === i || st.loop.next === i));
    g.fillStyle = accent; g.fillRect(x - 1, 0, on ? 3 : 2, H - 18);
    const label = h.label || "#";
    g.font = "bold 11px sans-serif";
    const tw = g.measureText(label).width + 8;
    g.fillRect(x, 2, tw, 16);
    g.fillStyle = "#fff"; g.fillText(label, x + 4, 14);
  });
  // 再生位置
  const cx = timeToX(st.cursor);
  g.fillStyle = "#e5484d"; g.fillRect(cx - 1, 0, 2, H - 18);
}

function fit() {
  st.pps = clamp((wrap.clientWidth - 8) / Math.max(1, st.dur), 4, 4000);
  applyZoom(0);
}
function applyZoom(centerTime) {
  spacer.style.width = Math.ceil(st.dur * st.pps) + "px";
  if (centerTime) wrap.scrollLeft = centerTime * st.pps - wrap.clientWidth / 2;
  draw();
}
function zoomBy(f, centerTime) {
  const c = centerTime != null ? centerTime : t0() + wrap.clientWidth / 2 / st.pps;
  st.pps = clamp(st.pps * f, 4, 4000);
  applyZoom(c);
}

/* ---------------- 再生とループ ---------------- */
function snapSample(t) { return Math.round(t * st.sr) / st.sr; }

function startSource(from) {
  if (!st.buf) return;
  const ctx = ensureCtx();
  killSource();
  const s = ctx.createBufferSource();
  s.buffer = st.buf;
  let at = from;
  if (st.loop) {
    s.loop = true; s.loopStart = snapSample(st.loop.a); s.loopEnd = snapSample(Math.min(st.loop.b, st.dur));
    if (at >= s.loopEnd || at < s.loopStart - 30) at = s.loopStart; // ループの頭より前(助走)から始めても、そのままループに入る
  }
  at = Math.min(snapSample(at), st.dur - 0.001);
  s.connect(ctx.destination);
  s.onended = () => { if (st.src === s && !st.loop) { st.src = null; st.playing = false; st.cursor = st.dur; updateButtons(); } };
  s.start(0, at);
  st.src = s; st.pos0 = at; st.ct0 = ctx.currentTime; st.playing = true;
  updateButtons();
}
function killSource() {
  if (st.src) { const s = st.src; st.src = null; s.onended = null; try { s.stop(); } catch (e) { /* 無視 */ } s.disconnect(); }
}
function curPos() {
  if (!st.playing) return st.cursor;
  let p = st.pos0 + (st.ctx.currentTime - st.ct0);
  if (st.loop) { const a = snapSample(st.loop.a), b = snapSample(Math.min(st.loop.b, st.dur)); if (p >= b) p = a + ((p - a) % (b - a)); }
  return Math.min(p, st.dur);
}
function play() { if (!st.buf) return; startSource(st.cursor >= st.dur - 0.01 ? 0 : st.cursor); }
function pause() { if (!st.playing) return; st.cursor = curPos(); killSource(); st.playing = false; updateButtons(); draw(); }
function stop(toStart) { killSource(); st.playing = false; if (toStart) st.cursor = 0; updateButtons(); draw(); }
function seek(t) { st.cursor = clamp(t, 0, st.dur); if (st.playing) startSource(st.cursor); draw(); }
function updateButtons() { $("play").textContent = st.playing ? "⏸ 一時停止" : "▶ 再生"; }

/** ループの範囲を、いまのセクションの位置から作り直す(閲覧画面と同じ。頭 − PRE から、次のセクションの頭 − PRE まで) */
function refreshLoop() {
  if (!st.loop) { drawSeam(); return; }
  const list = timed(), k = list.findIndex((x) => x.i === st.loop.idx);
  if (k < 0) { st.loop = null; drawSeam(); return; }
  const next = list[k + 1];
  st.loop.a = Math.max(0, pos(list[k].h) - PRE);
  st.loop.b = (next ? pos(next.h) : st.dur) - PRE;
  st.loop.next = next ? next.i : -1;
  if (st.playing) { const p = curPos(); startSource(p); }
  drawSeam();
}
function toggleLoop(idx) {
  if (st.loop && st.loop.idx === idx) { st.loop = null; const p = curPos(); st.cursor = p; if (st.playing) startSource(p); drawSeam(); renderTable(); draw(); return; }
  st.loop = { idx, a: 0, b: 0, next: -1 };
  refreshLoop();
  const wasPlaying = st.playing;
  st.cursor = st.loop.a;
  startSource(st.loop.a);
  if (!wasPlaying) { /* 再生を始める */ }
  renderTable(); draw();
}

const lead = () => clamp(Number($("lead").value) || 2, 0.5, 30);
const leadText = () => lead().toFixed(1); // 2 と 2.5 で表示がそろわないので、いつも 2.0 の形で出す

/** そのセクションのループの「終わりの N 秒前」から再生する。終わり → 頭のつなぎ目を、すぐ確かめられる。
 *  すでにそのセクションをループ中なら、ループはそのまま(解除しない)。別のセクションをループ中なら、このセクションに切り替える。 */
function seamCheck(i) {
  if (!(st.loop && st.loop.idx === i)) { st.loop = { idx: i, a: 0, b: 0, next: -1 }; refreshLoop(); }
  const t = Math.max(st.loop.a, st.loop.b - lead());
  st.cursor = t;
  startSource(t);
  renderTable();
  draw();
  if ($("follow").checked) wrap.scrollLeft = Math.max(0, t * st.pps - wrap.clientWidth * 0.3);
}

/** つなぎ目: ループの終わり(左)と頭(右)の波形を並べて、つながりを見る */
function drawSeam() {
  const c = $("seam"), g = c.getContext("2d"), w = c.width, h = c.height;
  g.clearRect(0, 0, w, h);
  const info = $("seam-info");
  if (!st.loop || !st.mono) { info.textContent = "表のセクションの「⟲」を押すと、そのセクションをループして、つなぎ目の波形が出ます。"; return; }
  const css = getComputedStyle(document.documentElement);
  const ink = css.getPropertyValue("--ink").trim() || "#111", soft = css.getPropertyValue("--ink-soft").trim() || "#777", accent = css.getPropertyValue("--ui-accent").trim() || "#2f5fd0";
  const half = Math.round(0.04 * st.sr);                // 左右それぞれ 0.04 秒(約2000サンプル)
  const bi = Math.round(snapSample(Math.min(st.loop.b, st.dur)) * st.sr), ai = Math.round(snapSample(st.loop.a) * st.sr);
  const arr = new Float32Array(half * 2);
  for (let i = 0; i < half; i++) { arr[i] = st.mono[bi - half + i] || 0; arr[half + i] = st.mono[ai + i] || 0; }
  const mid = h / 2, A = h / 2 * 0.9;
  g.fillStyle = soft; g.globalAlpha = 0.4; g.fillRect(0, mid, w, 1); g.globalAlpha = 1;
  g.strokeStyle = ink; g.lineWidth = 1.2; g.beginPath();
  for (let i = 0; i < arr.length; i++) { const x = (i / (arr.length - 1)) * w, y = mid - arr[i] * A; if (i) g.lineTo(x, y); else g.moveTo(x, y); }
  g.stroke();
  g.fillStyle = accent; g.fillRect(w / 2 - 1, 0, 2, h);
  const step = Math.abs(arr[half - 1] - arr[half]);
  const slope = Math.abs(arr[half - 1] - arr[half - 2]);
  const ok = step < 0.05 + slope;
  info.innerHTML = `左: ループの終わり ／ 右: ループの頭(青い線がつなぎ目)。つなぎ目の段差: <b class="${ok ? "ok" : "warn"}">${step.toFixed(3)}</b> ` +
    (ok ? "(なめらか)" : "(段差があります。「吸着」を「ゼロ交差」や「音の立ち上がり」にして、位置を少し動かしてみてください)");
}

/* ---------------- 表 ---------------- */
function structCells(i) {
  return `<td><button type="button" data-act="merge" ${i === 0 ? "disabled" : ""} title="この見出しを外して、前のセクションにつなげる(本文はそのまま残ります)">前に結合</button></td>` +
    `<td><button type="button" data-act="del" title="この見出しと、その本文を削除する">削除</button></td>`;
}

function renderTable() {
  const rows = st.heads.map((h, i) => {
    const has = h.t != null, looping = st.loop && st.loop.idx === i;
    if (!has) return `<tr class="notime" data-i="${i}"><td></td><td></td><td></td><td>${esc(h.label || "(名前なし)")}</td><td colspan="10">時刻がありません <button type="button" data-act="setcur">カーソル位置に設定</button></td>${structCells(i)}</tr>`;
    return `<tr data-i="${i}" class="${looping ? "loop" : ""}"><td><button type="button" data-act="jump" title="ここから再生">▶</button></td>` +
      `<td><button type="button" data-act="lead" title="このセクションをループにして、ループの終わりの${leadText()}秒前から再生(つなぎ目を、すぐ確かめる)">⏪末尾${leadText()}秒</button></td>` +
      `<td><button type="button" data-act="loop" title="このセクションをループ">${looping ? "■" : "⟲"}</button></td><td>${esc(h.label || "(名前なし)")}</td>` +
      `<td><input class="t" type="text" value="${fmtClock(pos(h))}" data-act="time" aria-label="位置"></td>` +
      `<td class="dev">${devText(h)}</td>` +
      `<td><button type="button" data-act="bar" data-d="-1" title="1小節前へ(テンポから出した長さ)">−1小節</button></td>` +
      [-10, -1, 1, 10].map((d) => `<td><button type="button" data-act="nudge" data-d="${d}">${d > 0 ? "+" : ""}${d}ms</button></td>`).join("") +
      `<td><button type="button" data-act="bar" data-d="1" title="1小節後ろへ(テンポから出した長さ)">+1小節</button></td>` +
      `<td><button type="button" data-act="setcur" title="カーソル位置に動かす">カーソルへ</button></td>` +
      `<td><button type="button" data-act="quant" title="格子に合わせる">そろえる</button></td>${structCells(i)}</tr>`;
  });
  $("secs").innerHTML = rows.join("");
}

/* ---------------- クオンタイズ ---------------- */
const qStep = () => ({ bar: 240 / st.bpm, beat: 60 / st.bpm, half: 30 / st.bpm })[$("q-unit").value];

/** 小節の頭の時刻(t)と、一番近い格子(テンポから出した、小節・拍の位置)とのずれ(秒) */
function gridDev(h) { const s = qStep(); return h.t - Math.round(h.t / s) * s; }
function devText(h) {
  const d = Math.round(gridDev(h) * 1000);
  return `<span style="color:${Math.abs(d) <= 3 ? "var(--ui-muted)" : Math.abs(d) <= 30 ? "#d97706" : "#e5484d"}">${d > 0 ? "+" : ""}${d} ms</span>`;
}

/** 表の頭の時刻を、テンポから出した格子に合わせる。idxList: 動かす見出しの番号(省略で、時刻のある全部) */
function quantize(idxList) {
  const list = idxList || st.heads.map((h, i) => (h.t != null ? i : -1)).filter((i) => i >= 0);
  const s = qStep(), tol = (Number($("q-tol").value) || 0) / 1000;
  const before = snapshot();
  let moved = 0, skipped = 0, maxd = 0;
  for (const i of list) {
    const h = st.heads[i];
    if (h.t == null) continue;
    const q = Math.max(0, Math.round(h.t / s) * s), d = q - h.t;
    if (Math.abs(d) > tol) { skipped++; continue; }
    if (Math.abs(d) > 0.0004) { moved++; maxd = Math.max(maxd, Math.abs(d)); }
    h.t = Math.round(q * 1e6) / 1e6;
  }
  if (moved) { commitUndo(before); setDirty(true); refreshLoop(); }
  say(moved || skipped ? `${moved}か所を格子に合わせました(最大 ${Math.round(maxd * 1000)} ms 動きました)` + (skipped ? `。許容より離れていて動かさなかったもの: ${skipped}か所` : "") : "すでに格子に合っています");
  renderTable(); draw();
}

/* ---------------- 元に戻す・やり直し ---------------- */
/** 編集中の内容(本文の行・見出し・ずれ)が、最後に保存した内容と同じかを調べるための印 */
function snapKey() {
  const heading = new Set(st.heads.map((h) => h.line));
  const lines = [];
  st.lines.forEach((ln, i) => { if (!/^\{\s*offset\s*:/.test(ln)) lines.push(heading.has(i) ? "#" : ln); });
  return JSON.stringify([lines, st.heads.map((h) => [h.prefix, h.label, h.t == null ? null : Math.round(h.t * 1e6)]), Math.round(st.offset * 1e6)]);
}
function snapshot() { return { lines: st.lines.slice(), heads: st.heads.map((h) => ({ ...h })), offset: st.offset, loopIdx: st.loop ? st.loop.idx : null }; }
function commitUndo(before) { st.undoStack.push(before); if (st.undoStack.length > 200) st.undoStack.shift(); st.redoStack = []; updateUndoButtons(); }
/** 変える前に呼ぶ。key が同じ操作が続くとき(ミリ秒ずつ動かす、など)は、まとめて1回にする */
function pushUndo(key) {
  const now = performance.now();
  if (key && st.lastOp.key === key && now - st.lastOp.at < 1500) { st.lastOp.at = now; return; }
  st.lastOp = { key: key || "", at: now };
  commitUndo(snapshot());
}
function updateUndoButtons() { $("undo").disabled = !st.undoStack.length; $("redo").disabled = !st.redoStack.length; }
function restore(s) {
  const hadLoop = !!st.loop;
  st.lines = s.lines.slice();
  st.heads = s.heads.map((h) => ({ ...h }));
  st.offset = s.offset;
  $("offset").value = st.offset.toFixed(3);
  st.loop = s.loopIdx != null && st.heads[s.loopIdx] && st.heads[s.loopIdx].t != null ? { idx: s.loopIdx, a: 0, b: 0, next: -1 } : null;
  if (st.loop) refreshLoop(); else { if (hadLoop && st.playing) startSource(curPos()); drawSeam(); }
  setDirty(snapKey() !== st.savedKey);
  renderTable();
  draw();
}
function undo() {
  if (!st.undoStack.length) return;
  st.redoStack.push(snapshot());
  restore(st.undoStack.pop());
  st.lastOp = { key: "", at: 0 };
  updateUndoButtons();
  say("元に戻しました");
}
function redo() {
  if (!st.redoStack.length) return;
  st.undoStack.push(snapshot());
  restore(st.redoStack.pop());
  st.lastOp = { key: "", at: 0 };
  updateUndoButtons();
  say("やり直しました");
}

/* ---------------- セクションの結合・削除 ----------------
 * 曲のテキストの見出しの行(# 名前 @時刻)を、外す・消す。行の番号、ループの範囲、表は、自動で付け直す。
 * ・前に結合: この見出しだけを外す。本文は前のセクションにつながる(前のセクションの終わりは、次の見出しの時刻になる)
 * ・削除: 見出しと、その本文(次の見出しの前まで)を消す */
function afterStructureChange(removedIdx) {
  if (st.loop) {
    if (st.loop.idx === removedIdx) { st.loop = null; if (st.playing) startSource(curPos()); }
    else if (st.loop.idx > removedIdx) st.loop.idx -= 1;
  }
  setDirty(snapKey() !== st.savedKey);
  refreshLoop();
  renderTable();
  draw();
}
function mergeUp(i) {
  if (i <= 0) return;
  const h = st.heads[i];
  pushUndo();
  st.lines.splice(h.line, 1);
  st.heads.splice(i, 1);
  for (let j = i; j < st.heads.length; j++) st.heads[j].line -= 1;
  afterStructureChange(i);
  say(`「${h.label || "(名前なし)"}」を前のセクションに結合しました`);
}
function deleteSection(i) {
  const h = st.heads[i], end = i + 1 < st.heads.length ? st.heads[i + 1].line : st.lines.length, n = end - h.line;
  if (!confirm(`「${h.label || "(名前なし)"}」の見出しと、その本文(${n}行)を削除します。\n「元に戻す」(Ctrl+Z)で戻せます。よろしいですか?`)) return;
  pushUndo();
  st.lines.splice(h.line, n);
  st.heads.splice(i, 1);
  for (let j = i; j < st.heads.length; j++) st.heads[j].line -= n;
  afterStructureChange(i);
  say(`「${h.label || "(名前なし)"}」を削除しました`);
}

function setPos(i, p) {          // p: 音源の秒
  st.heads[i].t = Math.max(0, p - st.offset);
  setDirty(true);
  refreshLoop();
}

function bindTable() {
  $("secs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const i = Number(b.closest("tr").dataset.i), h = st.heads[i], act = b.dataset.act;
    if (act === "nudge") pushUndo("nudge" + i); else if (act === "bar" || act === "setcur") pushUndo();
    if (act === "merge") { mergeUp(i); return; }
    if (act === "del") { deleteSection(i); return; }
    if (act === "jump") { st.cursor = Math.max(0, pos(h) - PRE); startSource(st.cursor); }
    else if (act === "loop") toggleLoop(i);
    else if (act === "lead") seamCheck(i);
    else if (act === "nudge") setPos(i, pos(h) + Number(b.dataset.d) / 1000);
    else if (act === "bar") setPos(i, pos(h) + Number(b.dataset.d) * 240 / st.bpm);
    else if (act === "setcur") setPos(i, curPos());
    else if (act === "quant") quantize([i]);
    renderTable(); draw();
  });
  $("secs").addEventListener("change", (e) => {
    if (e.target.dataset.act !== "time") return;
    const v = parseTime(e.target.value);
    if (v == null) { renderTable(); return; }
    pushUndo();
    setPos(Number(e.target.closest("tr").dataset.i), v);
    renderTable(); draw();
  });
}

/* ---------------- 波形の操作 ---------------- */
function bindWave() {
  const xOf = (e) => e.clientX - cv.getBoundingClientRect().left;
  const hit = (x) => { let best = -1, bd = 8; st.heads.forEach((h, i) => { if (h.t == null) return; const d = Math.abs(timeToX(pos(h)) - x); if (d < bd) { bd = d; best = i; } }); return best; };
  cv.addEventListener("pointerdown", (e) => {
    if (!st.mono) return;
    const x = xOf(e), i = hit(x);
    cv.setPointerCapture(e.pointerId);
    if (i >= 0) { pushUndo(); st.drag = i; } else { seek(t0() + x / st.pps); }
  });
  cv.addEventListener("pointermove", (e) => {
    const x = xOf(e);
    if (st.drag < 0) { cv.style.cursor = hit(x) >= 0 ? "ew-resize" : "crosshair"; return; }
    setPos(st.drag, snap(t0() + x / st.pps));
    renderTableLight();
    draw();
  });
  const up = () => { if (st.drag >= 0) { st.drag = -1; renderTable(); draw(); } };
  cv.addEventListener("pointerup", up);
  cv.addEventListener("pointercancel", up);
  wrap.addEventListener("scroll", () => draw());
  wrap.addEventListener("wheel", (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    zoomBy(e.deltaY < 0 ? 1.25 : 0.8, t0() + (e.clientX - wrap.getBoundingClientRect().left) / st.pps);
  }, { passive: false });
  window.addEventListener("resize", () => draw());
}
function renderTableLight() {   // ドラッグ中は、表の時刻の欄だけ更新する(入力欄を作り直さない)
  document.querySelectorAll("#secs tr[data-i]").forEach((tr) => {
    const h = st.heads[Number(tr.dataset.i)], inp = tr.querySelector("input.t");
    if (inp && h.t != null && document.activeElement !== inp) inp.value = fmtClock(pos(h));
    const dv = tr.querySelector("td.dev");
    if (dv && h.t != null) dv.innerHTML = devText(h);
  });
}

/* ---------------- 曲の選択・保存 ---------------- */
async function openSong(id) {
  if (st.dirty && !confirm("保存していない変更があります。破棄して、別の曲を開きますか?")) { $("song").value = st.id; return; }
  stop(true);
  st.loop = null;
  st.id = id;
  st.entry = SONGS.find((s) => s.id === id);
  parseSource(st.entry.source);
  st.undoStack = []; st.redoStack = []; st.lastOp = { key: "", at: 0 };
  st.savedKey = snapKey();
  updateUndoButtons();
  setDirty(false);
  renderTable();
  st.mono = null; st.buf = null;
  draw();
  try {
    const get = (u) => fetch(u).then((r) => r.json()).catch(() => ({}));
    const [dr, fr] = await Promise.all([get("/api/drive-list/" + encodeURIComponent(id)), get("/api/audio-list/" + encodeURIComponent(id))]);
    const p2 = (n) => String(n).padStart(2, "0");
    const when = (d, size) => { if (!d) return ""; return `(${d.getMonth() + 1}/${d.getDate()} ${p2(d.getHours())}:${p2(d.getMinutes())}${size ? " ・ " + (size / 1048576).toFixed(1) + "MB" : ""})`; };
    const opts = [];
    st.trackNames = {};
    (dr.tracks || []).forEach((t, i) => {
      const spec = "drive:" + i, nm = t.label || "音源" + (i + 1);
      st.trackNames[spec] = (t.name || nm);
      opts.push(`<option value="${spec}">Drive: ${esc(nm)} ${when(t.modified ? new Date(t.modified) : null, t.size)} ← 公開サイトと同じ</option>`);
    });
    const info = Object.fromEntries((fr.info || []).map((x) => [x.name, x]));
    (fr.files || []).forEach((f) => {
      st.trackNames["file:" + f] = f;
      opts.push(`<option value="file:${esc(f)}">フォルダ: ${esc(f)} ${when(info[f] ? new Date(info[f].mtime * 1000) : null, info[f] && info[f].size)}</option>`);
    });
    $("track").innerHTML = opts.length ? opts.join("") : "<option value=''>(音源がありません)</option>";
    if (!opts.length) { say("この曲に、音源(Driveのリンクもフォルダの mp3 も)がありません"); return; }
    const ok = await loadAudio($("track").value);
    // Driveから取れないとき(Googleが一時的に止めているときなど)は、フォルダの mp3 で開く
    if (!ok && $("track").value.startsWith("drive:") && (fr.files || []).length) {
      const why = $("msg").textContent;
      $("track").value = "file:" + fr.files[0];
      if (await loadAudio($("track").value)) say(`Driveから取れなかったので、フォルダの mp3 で開きました(${why.replace(/^音源を読み込めませんでした: /, "")})`);
    }
  } catch (e) { say("音源の一覧を取れませんでした(songbook.bat で起動していますか?)"); }
}

async function save() {
  const text = buildSource();
  say("保存中…");
  try {
    const res = await fetch("/api/source/" + encodeURIComponent(st.id), { method: "PUT", headers: { "Content-Type": "text/plain; charset=utf-8" }, body: text });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || res.status);
    st.entry.source = text;
    st.lines = text.replace(/\r\n?/g, "\n").split("\n");
    st.heads.forEach((h) => { /* 見出しの行番号は変わらない(offset の行が増えたときだけずれる) */ });
    parseSource(text);
    st.savedKey = snapKey();
    setDirty(false);
    renderTable();
    say("保存しました(閲覧画面にも反映されます)");
  } catch (e) { say("保存に失敗しました: " + e.message); }
}

let publishing = false;
async function publish() {
  if (publishing) return;
  if (st.dirty && !confirm("未保存の変更があります。保存していない内容は、公開されません。\nこのまま公開しますか?")) return;
  if (!confirm("公開サイトに反映します(全曲)。\nよろしいですか?")) return;
  publishing = true; $("publish").disabled = true;
  say("公開中…(1分ほどかかります)");
  try {
    const res = await fetch("/api/publish", { method: "POST", headers: { "X-Songbook": "1" } });
    if (!res.ok) throw new Error(res.status === 409 ? "すでに公開中です" : res.status);
    for (;;) {
      await new Promise((r) => setTimeout(r, 2000));
      const s = await (await fetch("/api/publish", { cache: "no-store" })).json();
      if (s.running) continue;
      if (s.ok) say("公開しました(開いたままの画面は、再読み込みで新しくなります)");
      else { say("公開に失敗しました"); alert("公開に失敗しました。\n\n" + (s.log || "").split("\n").slice(-12).join("\n")); }
      break;
    }
  } catch (e) { say("公開に失敗しました: " + e.message); }
  publishing = false; $("publish").disabled = false;
}

/* ---------------- 起動 ---------------- */
function frame() {
  if (st.playing) {
    st.cursor = curPos();
    $("clock").textContent = fmtClock(st.cursor);
    if ($("follow").checked && st.drag < 0) {
      const x = timeToX(st.cursor), w = wrap.clientWidth;
      if (x < 0 || x > w * 0.85) wrap.scrollLeft = st.cursor * st.pps - w * 0.15;
    }
    draw();
  }
  requestAnimationFrame(frame);
}

function init() {
  const theme = (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light";
  document.documentElement.dataset.theme = theme;
  $("song").innerHTML = SONGS.map((s) => `<option value="${esc(s.id)}">${esc(s.title)}</option>`).join("");
  const want = new URLSearchParams(location.search).get("song");
  const first = (SONGS.find((s) => s.id === want) || SONGS[0] || {}).id;
  $("song").value = first;
  $("lead").value = leadText();
  $("song").addEventListener("change", (e) => openSong(e.target.value));
  $("track").addEventListener("change", (e) => e.target.value && loadAudio(e.target.value));
  $("pick").addEventListener("change", (e) => { const f = e.target.files[0]; if (f) loadAudio(f.name, f); e.target.value = ""; });
  $("offset").addEventListener("change", (e) => { pushUndo("offset"); st.offset = Number(e.target.value) || 0; e.target.value = st.offset.toFixed(3); setDirty(true); refreshLoop(); renderTable(); draw(); });
  $("detect").addEventListener("click", detectOffset);
  $("q-all").addEventListener("click", () => quantize());
  $("undo").addEventListener("click", undo);
  $("redo").addEventListener("click", redo);
  $("q-unit").addEventListener("change", renderTable);
  const shiftAll = (d) => {
    const bar = 240 / st.bpm;
    pushUndo();
    st.heads.forEach((h) => { if (h.t != null) h.t = Math.max(0, Math.round((h.t + d * bar) * 1e6) / 1e6); });
    setDirty(true); refreshLoop(); renderTable(); draw();
    say(`全セクションの頭を、${d > 0 ? "1小節後ろ" : "1小節前"}へ動かしました(${(bar * 1000).toFixed(0)} ms)`);
  };
  $("bar-all-minus").addEventListener("click", () => shiftAll(-1));
  $("bar-all-plus").addEventListener("click", () => shiftAll(1));
  $("lead").addEventListener("change", () => { $("lead").value = leadText(); renderTable(); });
  $("play").addEventListener("click", () => (st.playing ? pause() : play()));
  $("stop").addEventListener("click", () => { stop(true); seek(0); });
  $("zoom-in").addEventListener("click", () => zoomBy(1.5));
  $("zoom-out").addEventListener("click", () => zoomBy(1 / 1.5));
  $("zoom-fit").addEventListener("click", fit);
  $("save").addEventListener("click", save);
  $("publish").addEventListener("click", publish);
  window.addEventListener("keydown", (e) => {
    const ae = document.activeElement, typing = (ae.tagName === "INPUT" && /^(text|number)$/.test(ae.type)) || ae.tagName === "TEXTAREA";
    if ((e.ctrlKey || e.metaKey) && !typing && /^[zy]$/i.test(e.key)) {   // 入力欄の中では、ブラウザ標準の取り消しに任せる
      e.preventDefault();
      if (e.key.toLowerCase() === "y" || e.shiftKey) redo(); else undo();
      return;
    }
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName) && e.key !== "s") return;
    if (e.code === "Space") { e.preventDefault(); st.playing ? pause() : play(); }
    if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); if (!$("save").disabled) save(); }
  });
  window.addEventListener("beforeunload", (e) => { if (st.dirty) { e.preventDefault(); e.returnValue = ""; } });
  bindWave();
  bindTable();
  requestAnimationFrame(frame);
  if (first) openSong(first);
}
init();
