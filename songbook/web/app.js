"use strict";

const SONGS = loadSongs();

/* 音源の種類(2mix・ボーカルなし など)。曲のフォルダにある mp3 から作る。設定で選んだ種類を、どの曲でも使う(無い曲は 2mix) */
const STEM_NAMES = { "2mix": "2mix", Vcless: "ボーカルなし", Gtrless: "ギターなし", Bsless: "ベースなし", Drless: "ドラムなし" };
(function mergeStems() {
  const all = (window.SONGBOOK && window.SONGBOOK.audioStems) || {};
  for (const s of SONGS) {
    const st = all[s.id];
    if (!st) continue;
    const list = Object.keys(STEM_NAMES).filter((k) => st[k]).map((k) => ({ label: k, id: "", url: st[k] }));
    const extra = s.audios.filter((a) => a.label && !st[a.label] && a.label.toLowerCase() !== "2mix");
    if (list.length) s.audios = list.concat(extra);
  }
})();
const STORE_KEY = "songbook.settings.v3";

const DEFAULTS = {
  song: null, // 表示している曲のid。null = 先頭の曲
  theme: "auto",
  chords: "above",
  transpose: 0,
  accidental: "auto",
  chordColor: "color",
  chordScale: 80,
  barLines: false,
  maxBars: "auto",
  sectionColors: true,
  customColors: {},
  lyricSize: 14,
  metaSize: 100,
  lineGap: 6,
  font: "gothic",
  paper: "A4",
  orient: "portrait",
  columns: 1,
  margin: 12,
  showMeta: true,
  showSections: true,
  showInst: true,
  autoSpeed: 1,
  playlist: { ids: [], repeat: "off" }, // プレイリスト(曲の並びと、リピート: off / all / one)
  loopMode: "normal", // 区間ループの方式: normal(音源をそのまま再生。つなぎ目に少し隙間) / precise(読み込んで、切れ目なく)
  trackPref: "2mix", // 再生する音源の種類(全曲共通): 2mix / Vcless / Gtrless / Bsless / Drless
  autoOn: true, // 自動スクロールを、開いたときからオンにしておく(⬇ で切り替えると、その端末では覚える)
  autoOffset: 0, // 自動スクロールを音源に対して遅らせる秒数(マイナスで早める)
  autoHighlight: "line", // いま歌う行に色をつける: off / line(行) / wipe(行 + 文字が左から染まる)
  autoColor: "#f07000",
};

const PAPER_MM = { A4: [210, 297], B5: [182, 257], A5: [148, 210] };
const UNITS = { chordScale: "%", lyricSize: "pt", metaSize: "%", lineGap: "px", margin: "mm", autoOffset: "秒" };
const DECIMALS = { lyricSize: 1, autoOffset: 1 }; // 整数と小数で表示がそろわないので、いつも小数点以下を出す(14.0pt、0.0秒)
const showVal = (key, v) => (key in DECIMALS ? Number(v).toFixed(DECIMALS[key]) : v) + (UNITS[key] || "");

let settings = loadSettings();
// 印刷中は常に標準色・作成者どおりの改行で組み、ページに分ける。 ?print=1 を付けると、画面で印刷の見た目を確認できる
let printing = new URLSearchParams(location.search).get("print") === "1";
let savedTitle = document.title;
if (printing) document.body.classList.add("print-preview"); // 確認用: 画面の枠を隠して、印刷されるページだけを見せる

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
    return Object.assign({}, DEFAULTS, saved);
  } catch (e) {
    return Object.assign({}, DEFAULTS);
  }
}

function saveSettings() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(settings));
  } catch (e) { /* 保存できない環境でも動作に影響なし */ }
}

/* ---------------- テーマ ---------------- */
const darkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

function effectiveTheme() {
  if (printing) return "light";
  if (settings.theme === "dark") return "dark";
  if (settings.theme === "light") return "light";
  return darkQuery && darkQuery.matches ? "dark" : "light";
}

// コードだけの行(イントロ・間奏)などを、画面の幅に合わせて何小節ずつで折り返すか(0=そのまま)
function effectiveBarCap() {
  const v = settings.maxBars;
  if (v === "auto") return printing || window.innerWidth >= 560 ? 0 : 4;
  return Number(v) || 0;
}

/* ---------------- 描画(1曲だけ表示する。曲名を押して切り替える) ---------------- */
const songById = (id) => SONGS.find((s) => s.id === id);
const currentSong = () => songById(settings.song) || SONGS[0] || null;

function render() {
  const theme = effectiveTheme();
  document.documentElement.dataset.theme = theme;

  const sheet = document.getElementById("sheet");
  const [pw, ph] = PAPER_MM[settings.paper];
  const w = settings.orient === "landscape" ? ph : pw;

  sheet.className = "sheet" +
    (settings.font === "mincho" ? " mincho" : "") +
    (settings.chordColor === "mono" ? " mono-chords" : "") +
    (settings.chords === "none" ? " no-chords" : "") +
    (settings.barLines ? "" : " no-barlines") +
    (settings.sectionColors ? "" : " no-colors") +
    (settings.showSections ? "" : " no-labels");
  sheet.style.setProperty("--lyric", settings.lyricSize + "pt");
  sheet.style.setProperty("--meta-scale", settings.metaSize / 100);
  sheet.style.setProperty("--chord-scale", settings.chordScale / 100);
  sheet.style.setProperty("--gap", settings.lineGap + "px");
  sheet.style.setProperty("--margin", settings.margin + "mm");
  sheet.style.setProperty("--cols", settings.columns);
  sheet.style.setProperty("--paper-w", w + "mm");

  // 余白は、自前のページ(.print-page)の内側につける。@page の余白を0にすると、ブラウザの日付・URLなどが出ない
  document.getElementById("page-style").textContent =
    `@page { size: ${settings.paper} ${settings.orient}; margin: 0; }`;

  const opts = {
    transpose: settings.transpose,
    accidental: settings.accidental,
    showSections: settings.showSections,
    showInst: settings.showInst,
    showMeta: settings.showMeta,
    barCap: effectiveBarCap(),
    colors: resolveColors(theme, settings.customColors),
    audio: true,
    songs: SONGS.map((s) => ({ id: s.id, title: s.title })),
  };
  const song = currentSong();
  document.body.appendChild(pl.el("player")); // 描き直しで消えないよう、いったん外に出す
  sheet.innerHTML = song ? renderSong(song, opts) : '<p class="empty">曲がありません</p>';
  if (printing && song) {
    sheet.classList.add("print-mode");
    buildPrintPages(sheet, song);
  }
  syncPlayers();
  auto.tl = null; // 描き直したので、自動スクロールの時間表も作り直す
  auto.nowEl = null;
  applyLoopMarks();
  labelHints();
  const edit = document.getElementById("to-editor");
  if (song) edit.href = "editor.html?song=" + encodeURIComponent(song.id);
}


/* ---------------- 印刷: 自前でページに分ける ----------------
 * ブラウザの印刷は、余白に日付・タイトル・URLを入れてしまう。@page の余白を0にするとそれが消えるが、
 * 2ページ目以降の余白が無くなり、ページ番号も付けられない。そこで、セクション単位で高さを測って、
 * 用紙ごとの .print-page に自分で詰める(余白とページ番号は .print-page の中に作る)。 */
function mmToPx() {
  const d = document.createElement("div");
  d.style.cssText = "position:absolute;visibility:hidden;width:100mm;height:0";
  document.body.appendChild(d);
  const px = d.getBoundingClientRect().width / 100;
  d.remove();
  return px;
}

function buildPrintPages(sheet, song) {
  const [pw0, ph0] = PAPER_MM[settings.paper];
  const landscape = settings.orient === "landscape";
  const W = landscape ? ph0 : pw0, H = landscape ? pw0 : ph0;
  const m = settings.margin, cols = Number(settings.columns) || 1, gapMm = 8;
  const k = mmToPx();
  const pageH = (H - 0.5) * k;                                   // 用紙より少し低くして、白紙のページが出るのを防ぐ
  const contentW = (W - 2 * m) * k, contentH = pageH - 2 * m * k;
  const colW = (contentW - gapMm * k * (cols - 1)) / cols;

  const article = sheet.querySelector(".song");
  const head = article.querySelector(".song-head");
  const secs = [...article.querySelectorAll(":scope > .sec")];

  // 高さを測るための、見えない作業場所(画面と同じ文字の設定を使う)
  const meas = document.createElement("div");
  meas.className = sheet.className.replace("print-mode", "").trim();
  meas.style.cssText = sheet.style.cssText + `;position:absolute;left:-99999px;top:0;visibility:hidden;padding:0;box-shadow:none;border:0;min-height:0;width:${colW}px`;
  document.body.appendChild(meas);
  const heightOf = (node, width) => {
    meas.style.width = width + "px";
    const clone = node.cloneNode(true);
    meas.appendChild(clone);
    const r = clone.getBoundingClientRect().height + (parseFloat(getComputedStyle(clone).marginBottom) || 0);
    clone.remove();
    return r;
  };

  const headH = head ? heightOf(head, contentW) : 0;

  // 1つのセクションが1段より高いときだけ、行の区切りで分ける
  const items = [];
  const cap0 = contentH - headH, capN = contentH;
  for (const sec of secs) {
    const h = heightOf(sec, colW);
    if (h <= Math.min(cap0, capN)) { items.push({ el: sec, h }); continue; }
    const body = sec.querySelector(".sec-body");
    const kids = [...body.children];
    let part = null, used = 0;
    const flush = () => { if (part) { items.push({ el: part, h: used }); part = null; used = 0; } };
    kids.forEach((kid, idx) => {
      const kh = heightOf(kid, colW - 28);
      if (!part || used + kh > capN) {
        flush();
        part = sec.cloneNode(true);
        const pb = part.querySelector(".sec-body");
        [...pb.children].forEach((x) => x.remove());
        if (idx > 0) { const lab = part.querySelector(".sec-label"); if (lab) lab.className = "sec-label bare"; if (lab) lab.textContent = ""; }
      }
      part.querySelector(".sec-body").appendChild(kid.cloneNode(true));
      used += kh;
    });
    flush();
  }
  meas.remove();

  // ページと段に、上から順に詰める
  const pages = [];
  const newPage = () => { const pg = { cols: Array.from({ length: cols }, () => []), used: new Array(cols).fill(0), cap: pages.length ? capN : cap0 }; pages.push(pg); return pg; };
  let page = newPage(), c = 0;
  for (const it of items) {
    if (page.used[c] + it.h > page.cap && page.used[c] > 0) {
      c += 1;
      if (c >= cols) { page = newPage(); c = 0; }
    }
    page.cols[c].push(it.el);
    page.used[c] += it.h;
  }

  sheet.innerHTML = "";
  pages.forEach((pg, i) => {
    const el = document.createElement("div");
    el.className = "print-page";
    el.style.cssText = `width:${W}mm;height:${H - 0.5}mm;padding:${m}mm`;
    const num = document.createElement("div");
    num.className = "pp-num";
    num.textContent = `${i + 1} / ${pages.length}`;
    el.appendChild(num);
    if (i === 0 && head) el.appendChild(head);
    const grid = document.createElement("div");
    grid.className = "pp-cols";
    grid.style.cssText = `grid-template-columns:repeat(${cols},minmax(0,1fr));column-gap:${gapMm}mm`;
    pg.cols.forEach((list) => {
      const col = document.createElement("div");
      col.className = "pp-col";
      list.forEach((n) => col.appendChild(n));
      grid.appendChild(col);
    });
    el.appendChild(grid);
    sheet.appendChild(el);
  });
}

function startPrint() {
  window.print(); // beforeprint で、印刷用のページ組みに切り替わる
}

function openPrintHelp() {
  let skip = false;
  try { skip = localStorage.getItem("songbook.printHelpSkip") === "1"; } catch (e) { /* 無視 */ }
  const dlg = document.getElementById("print-help");
  if (skip || !dlg.showModal) return startPrint();
  dlg.showModal();
}

function switchSong(id) {
  if (!songById(id)) return;
  settings.song = id;
  saveSettings();
  auto.vt = 0;
  auto.running = false;
  resetPlayerFor(songById(id));
  render();
  window.scrollTo(0, 0);
}

/* ---------------- 設定パネル ---------------- */
function setPanel(open) {
  const panel = document.getElementById("panel");
  const btn = document.getElementById("toggle-panel");
  panel.classList.toggle("open", open);
  btn.setAttribute("aria-expanded", String(open));
}

function zoom(delta) {
  settings.lyricSize = Math.max(8, Math.min(28, settings.lyricSize + delta));
  saveSettings();
  syncControls();
  render();
}

function bindControls() {
  for (const key of Object.keys(DEFAULTS)) {
    const el = document.getElementById(key);
    if (!el) continue;
    el.addEventListener("input", () => {
      if (el.type === "checkbox") settings[key] = el.checked;
      else if (typeof DEFAULTS[key] === "number") settings[key] = Number(el.value);
      else settings[key] = el.value;
      const out = document.getElementById(key + "-out");
      if (out) out.textContent = showVal(key, settings[key]);
      saveSettings();
      if (key === "autoOffset" || key === "autoHighlight" || key === "autoColor") { applyHighlight(); return; } // 描き直しは不要
      if (key === "trackPref") { applyTrackPref(); return; } // 描き直しは不要
      if (key === "loopMode") { if (settings.loopMode !== "precise") engineExit(true); else if (loop.on) prepareEngine(); return; }
      render();
      if (key === "theme") renderColorGrid();
    });
  }

  document.querySelectorAll("[data-step]").forEach((b) =>
    b.addEventListener("click", () => {
      settings.transpose = Math.max(-11, Math.min(11, settings.transpose + Number(b.dataset.step)));
      syncControls();
      saveSettings();
      render();
    })
  );

  document.getElementById("sheet").addEventListener("change", (e) => {
    if (e.target.classList.contains("title-select")) { switchSong(e.target.value); plsOnManualSwitch(e.target.value); }
  });
  document.getElementById("size-select").addEventListener("change", (e) => {
    settings.lyricSize = Number(e.target.value);
    saveSettings();
    syncControls();
    render();
  });
  document.getElementById("print").addEventListener("click", openPrintHelp);
  const dlg = document.getElementById("print-help");
  document.getElementById("print-help-cancel").addEventListener("click", () => dlg.close());
  document.getElementById("print-help-go").addEventListener("click", () => {
    if (document.getElementById("print-help-skip").checked) {
      try { localStorage.setItem("songbook.printHelpSkip", "1"); } catch (e) { /* 無視 */ }
    }
    dlg.close();
    startPrint();
  });
  document.getElementById("reset").addEventListener("click", () => {
    const keep = settings.song;
    settings = Object.assign({}, DEFAULTS, { customColors: {}, song: keep });
    saveSettings();
    syncControls();
    render();
  });
  document.getElementById("reset-colors").addEventListener("click", () => {
    settings.customColors = {};
    saveSettings();
    renderColorGrid();
    render();
  });
  document.getElementById("toggle-panel").addEventListener("click", () =>
    setPanel(!document.getElementById("panel").classList.contains("open"))
  );

  // 端末の表示設定・画面幅の変化、印刷の前後
  if (darkQuery) darkQuery.addEventListener("change", () => { if (settings.theme === "auto") { render(); renderColorGrid(); } });
  let lastCap = effectiveBarCap();
  window.addEventListener("resize", () => {
    const cap = effectiveBarCap();
    if (cap !== lastCap) {
      lastCap = cap;
      render();
    }
  });
  window.addEventListener("beforeprint", () => {
    if (printing) return; // ?print=1 のときは、すでに印刷用
    savedTitle = document.title;
    const song = currentSong();
    if (song) document.title = song.title; // PDFの保存名になる
    printing = true;
    render();
  });
  window.addEventListener("afterprint", () => {
    if (new URLSearchParams(location.search).get("print") === "1") return;
    printing = false;
    document.title = savedTitle;
    render();
  });
}

function syncControls() {
  for (const key of Object.keys(DEFAULTS)) {
    const el = document.getElementById(key);
    if (!el) continue;
    if (el.type === "checkbox") el.checked = !!settings[key];
    else el.value = settings[key];
    const out = document.getElementById(key + "-out");
    if (out) out.textContent = showVal(key, settings[key]);
  }
  const t = settings.transpose;
  document.getElementById("transpose-out").textContent = (t > 0 ? "+" : "") + t;
  applyHighlight();
  syncSizeSelect();
  renderColorGrid();
}

/** 表示サイズの選択肢(極小〜極大)のうち、いまの文字の大きさに一番近いものを選んでおく */
function syncSizeSelect() {
  const sel = document.getElementById("size-select");
  let best = sel.options[0], bd = 1e9;
  for (const o of sel.options) { const d = Math.abs(Number(o.value) - settings.lyricSize); if (d < bd) { bd = d; best = o; } }
  sel.value = best.value;
  document.getElementById("size-btn").title = "表示サイズ: " + best.textContent + (bd > 0.01 ? "(細かい調整中)" : "");
}

function renderColorGrid() {
  const theme = effectiveTheme();
  const colors = resolveColors(theme, settings.customColors);
  const grid = document.getElementById("color-grid");
  grid.innerHTML = FAMILIES.map((f) =>
    `<label class="color-item"><input type="color" data-fam="${f.id}" value="${colors[f.id]}">${f.name}</label>`
  ).join("");
  grid.querySelectorAll("input").forEach((inp) =>
    inp.addEventListener("input", () => {
      settings.customColors = Object.assign({}, settings.customColors, { [inp.dataset.fam]: inp.value });
      saveSettings();
      render();
    })
  );
}

/* ---------------- 音源の再生(Google Drive の mp3) ----------------
 * 曲テキストの {audio: ...} に書いた音源を、曲名の下の再生バーで再生する(音源がある曲だけ)。
 * Driveのファイルは「リンクを知っている全員が閲覧可」にしておく。
 * Drive APIキーが無い、または直接再生できない環境では、Driveの埋め込みプレーヤーに切り替える。 */
const audio = new Audio();
audio.preload = "none";
const pl = {
  cur: null, // {id, track}
  fallback: false, // Driveの埋め込みプレーヤーで再生しているか
  el: (id) => document.getElementById(id),
};

// Google Driveのmp3は、ほかのサイトのページから直接は読み込めない(Google側が拒否する)ため、
// Drive APIキー(config.json の driveApiKey)があるときはAPI経由で再生し、無いときはDriveの埋め込みプレーヤーを使う。
const DRIVE_KEY = (window.SONGBOOK && window.SONGBOOK.driveApiKey) || "";
// 公開サイトでは、音源をサイト自体に置いている(audio/<DriveのファイルID>.mp3)。Driveからの配信は、アクセスが多いと止められるため
const AUDIO_FILES = (window.SONGBOOK && window.SONGBOOK.audioFiles) || {};
const driveStream = (id) => `https://www.googleapis.com/drive/v3/files/${encodeURIComponent(id)}?alt=media&key=${encodeURIComponent(DRIVE_KEY)}`;
const fmtTime = (t) => (isFinite(t) ? `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}` : "0:00");

/** 曲を切り替えたとき: 再生を止めて、新しい曲の再生バーを「待機」の状態にする */
function resetPlayerFor(song) {
  clearLoop(false);
  pendingSeek = null;
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  pl.fallback = false;
  pl.el("pl-frame").src = "about:blank";
  pl.el("pl-fallback").hidden = true;
  pl.el("pl-msg").hidden = true;
  pl.el("player").classList.remove("collapsed");
  pl.cur = song && song.audios.length ? { id: song.id, track: prefTrackIndex(song) } : null;
  setupTrackSelect();
  pl.el("pl-toggle").hidden = false;
  const seek = pl.el("pl-seek");
  seek.hidden = false;
  seek.disabled = true;
  seek.value = 0;
  pl.el("pl-time").textContent = "0:00 / 0:00";
}

/** 設定で選んだ種類の音源が、その曲の何番目か。無ければ 2mix、それも無ければ最初の音源 */
function prefTrackIndex(song) {
  if (!song || !song.audios.length) return 0;
  const want = String(settings.trackPref || "2mix").toLowerCase();
  let i = song.audios.findIndex((a) => (a.label || "").toLowerCase() === want);
  if (i < 0) i = song.audios.findIndex((a) => (a.label || "").toLowerCase() === "2mix");
  return i < 0 ? 0 : i;
}

/** 音源の種類を変えたとき: いまの曲も、同じ位置から、その種類に切り替える(区間ループ中なら、ループも残す) */
function applyTrackPref() {
  const song = currentSong();
  if (!pl.cur || !song || pl.cur.id !== song.id) return;
  const idx = prefTrackIndex(song);
  if (idx === pl.cur.track) return;
  const loaded = !!audio.getAttribute("src") || eng.active;
  const pos = tpTime(), playing = tpPlaying(), keep = loop.on ? loop.idx.slice() : null;
  if (!loaded) { pl.cur.track = idx; setupTrackSelect(); return; }
  startTrack(song.id, idx);
  pendingSeek = pos;
  if (!playing) audio.addEventListener("playing", () => audio.pause(), { once: true });
  if (keep) {
    const { list, off } = sectionInfos();
    const ks = keep.map((i) => list.findIndex((x) => x.i === i)).filter((k) => k >= 0);
    if (ks.length) setLoopRange(list, Math.min(...ks), Math.max(...ks), off);
  }
}

function setupTrackSelect() {
  const song = pl.cur && songById(pl.cur.id);
  const sel = pl.el("pl-track");
  if (!song) return;
  pl.el("pl-title").textContent = song.title;
  sel.hidden = song.audios.length < 2;
  sel.innerHTML = song.audios.map((x, i) => `<option value="${i}"${i === pl.cur.track ? " selected" : ""}>${esc(x.label || "音源" + (i + 1))}</option>`).join("");
}

function startTrack(id, track) {
  const song = songById(id);
  const a = song && song.audios[track];
  if (!a) return;
  if (!(pl.cur && pl.cur.id === id && pl.cur.track === track)) clearLoop(false);
  pl.cur = { id, track };
  pl.fallback = false;
  pl.el("player").classList.remove("collapsed");
  setupTrackSelect();
  pl.el("pl-msg").hidden = true;
  pl.el("pl-fallback").hidden = true;
  pl.el("pl-frame").src = "about:blank";
  pl.el("pl-toggle").hidden = false;
  pl.el("pl-seek").hidden = false;
  pl.el("pl-seek").disabled = false;
  audio.loop = pls.active && settings.playlist.repeat === "one" ? true : pl.el("pl-loop").checked;
  const url = a.id ? (AUDIO_FILES[a.id] || (DRIVE_KEY ? driveStream(a.id) : "")) : a.url;
  if (!url) { // APIキーが無いDrive音源: Driveのプレーヤーで再生する
    audio.pause();
    audio.removeAttribute("src");
    showFallback();
    syncPlayers();
    return;
  }
  audio.src = url;
  audio.play().catch(() => { /* 失敗は error イベントで扱う */ });
  syncPlayers();
}

function togglePlay() {
  if (!pl.cur) return;
  if (eng.active) {
    eng.paused = !eng.paused;
    if (eng.paused) eng.ctx.suspend().catch(() => {}); else eng.ctx.resume().catch(() => {});
    syncPlayers();
  } else if (audio.getAttribute("src")) {
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  } else {
    startTrack(pl.cur.id, pl.cur.track);
  }
}

function showFallback() {
  pl.fallback = true;
  const song = pl.cur && songById(pl.cur.id);
  const a = song && song.audios[pl.cur.track];
  pl.el("pl-toggle").hidden = true;
  pl.el("pl-seek").hidden = true;
  if (a && a.id) {
    pl.el("pl-frame").src = `https://drive.google.com/file/d/${encodeURIComponent(a.id)}/preview`;
    pl.el("pl-fallback-note").textContent = DRIVE_KEY ? "直接再生できなかったので、Driveのプレーヤーを表示しています" : "";
    pl.el("pl-fallback-note").hidden = !DRIVE_KEY;
    pl.el("pl-fallback").hidden = false;
  } else {
    pl.el("pl-msg").textContent = "再生できませんでした。音源のURLと、共有設定(リンクを知っている全員が閲覧可)を確認してください。";
    pl.el("pl-msg").hidden = false;
  }
}

/**
 * プレーヤーの置き場所。自前のバー(Drive APIキーで直接再生するとき)は、曲名の下にいつも置く。
 * Driveの埋め込みプレーヤーは、描き直しで再読み込みされると止まってしまうので、画面下に固定する。
 */
function placePlayer() {
  const el = pl.el("player");
  const inline = !!pl.cur && !pl.fallback;
  const slot = inline ? document.getElementById("player-top") : null;
  if (slot) {
    if (el.parentNode !== slot) slot.appendChild(el);
  } else if (el.parentNode !== document.body) {
    document.body.appendChild(el);
  }
  el.classList.toggle("inline", !!slot);
  el.hidden = !pl.cur;
  // 曲名の下に置くときは、曲名の表示を省く。音源が複数あるときだけ、選ぶ欄を出す
  const song = pl.cur && songById(pl.cur.id);
  const info = el.querySelector(".pl-info");
  if (info) info.hidden = !!slot && !(song && song.audios.length > 1);
}

/** 再生バーの表示を、今の再生状態に合わせる */
function syncPlayers() {
  placePlayer();
  const playing = tpPlaying();
  pl.el("pl-toggle").textContent = playing ? "⏸" : "▶";
  pl.el("pl-toggle").setAttribute("aria-label", playing ? "一時停止" : "再生");
  pl.el("pl-prev").hidden = pl.el("pl-next").hidden = !pls.active;
  document.body.classList.toggle("pls-on", pls.active);
  const pb = pl.el("pls-btn");
  pb.classList.toggle("on", pls.active);
  pb.title = pls.active ? `プレイリスト再生中(${pls.idx + 1} / ${plsIds().length})` : "プレイリスト";
}

function bindPlayer() {
  pl.el("pl-toggle").addEventListener("click", togglePlay);
  pl.el("pl-track").addEventListener("change", (e) => pl.cur && startTrack(pl.cur.id, Number(e.target.value)));
  pl.el("pl-loop").addEventListener("change", (e) => { audio.loop = e.target.checked; });
  pl.el("pl-min").addEventListener("click", () => pl.el("player").classList.toggle("collapsed"));
  pl.el("pl-close").addEventListener("click", () => resetPlayerFor(currentSong()) || syncPlayers());
  const seek = pl.el("pl-seek");
  seek.addEventListener("input", () => {
    seeking = true;
    const d = tpDuration();
    if (!d) return;
    const t = (seek.value / 1000) * d;
    if (loop.on && !(t >= loop.a && t < Math.min(loop.b, d))) clearLoop(); // ループの外へ動かしたら、ループをやめる
    if (eng.active) engineStartSource(t); else audio.currentTime = t;
  });
  seek.addEventListener("change", () => { seeking = false; });
  audio.addEventListener("timeupdate", () => { if (!eng.active) updatePlayerUi(); });
  audio.addEventListener("loadedmetadata", () => {
    if (pendingSeek != null) { audio.currentTime = pendingSeek; pendingSeek = null; }
    if (loop.on && loop.toEnd && !isFinite(loop.b)) loop.b = audio.duration + songOffset(currentSong()) - LOOP_PREROLL;
  });
  for (const ev of ["play", "pause"]) audio.addEventListener(ev, syncPlayers);
  audio.addEventListener("ended", () => {
    if (loop.on && !eng.active) { audio.currentTime = loop.a; audio.play().catch(() => {}); return; }
    syncPlayers();
    if (pls.active) plsNext(1, true); // プレイリスト: 次の曲へ
  });
  audio.addEventListener("error", () => { if (audio.getAttribute("src")) { showFallback(); syncPlayers(); } });
}

/* ---------------- 再生位置の窓口・ジャンプ・区間ループ ----------------
 * ふだんは audio 要素で再生する。区間ループ中だけ、Web Audio で音源を読み込み(デコードして)、
 * サンプル単位で切れ目なく繰り返す(audio 要素のシークだと、つなぎ目に隙間ができるため)。
 * 読み込めない環境では、audio 要素を細かい間隔で見張って戻す方法で続ける。 */
const DEFAULT_AUDIO_OFFSET = 0.05; // 全曲を測ったところ、音源のビートは、Excelの小節の頭より約0.05秒あとにある。曲ごとに {offset: 秒} で変えられる
const LOOP_PREROLL = 0.01;         // 区間の頭と終わりを、拍の少し手前にそろえる(秒)
const eng = { ctx: null, buf: null, key: "", loading: "", src: null, active: false, paused: false, pos0: 0, ct0: 0, a: 0, b: 0, ui: 0 };
const loop = { on: false, a: 0, b: 0, toEnd: false, idx: [], label: "", timer: 0 };
let pendingSeek = null; // 再生を始めてから合わせる位置(秒)
let seeking = false;
const songOffset = (song) => (song && typeof song.offset === "number" && isFinite(song.offset) ? song.offset : DEFAULT_AUDIO_OFFSET);
const trackKey = () => (pl.cur ? pl.cur.id + ":" + pl.cur.track : "");

function enginePos() {
  let p = eng.pos0 + (eng.ctx.currentTime - eng.ct0);
  if (p >= eng.b) p = eng.a + ((p - eng.a) % (eng.b - eng.a));
  return p;
}
const tpPlaying = () => !!pl.cur && (eng.active ? !eng.paused : !!audio.getAttribute("src") && !audio.paused && !audio.ended);
const tpTime = () => (eng.active ? enginePos() : audio.currentTime);
function tpDuration() {
  if (isFinite(audio.duration) && audio.duration > 0) return audio.duration;
  return eng.buf && eng.key === trackKey() ? eng.buf.duration : 0;
}
function knownDuration() {
  const song = currentSong();
  return tpDuration() || (song && song.duration) || Infinity;
}

function updatePlayerUi() {
  const d = tpDuration(), t = tpTime();
  pl.el("pl-time").textContent = `${fmtTime(t)} / ${fmtTime(d)}`;
  if (!seeking && d) pl.el("pl-seek").value = Math.round((t / d) * 1000);
}

function engineStartSource(pos) {
  if (eng.src) { try { eng.src.stop(); } catch (e) { /* 無視 */ } eng.src.disconnect(); }
  const sr = eng.buf.sampleRate;
  eng.a = Math.round(loop.a * sr) / sr; // 区間の両端を、サンプルの境目にそろえる(半端な位置だと、つなぎ目で波形がずれる)
  eng.b = Math.round(Math.min(loop.b, eng.buf.duration) * sr) / sr;
  const s = eng.ctx.createBufferSource();
  s.buffer = eng.buf;
  s.loop = true;
  s.loopStart = eng.a;
  s.loopEnd = eng.b;
  s.connect(eng.ctx.destination);
  const at0 = pos >= eng.a - 30 && pos < eng.b ? pos : eng.a; // ループの頭より前(助走)から始めても、そのままループに入る
  const at = Math.min(Math.round(at0 * sr) / sr, eng.b); // 開始位置も、サンプルの境目に
  s.start(0, at);
  eng.src = s;
  eng.pos0 = at;
  eng.ct0 = eng.ctx.currentTime;
}

/** audio 要素から、切れ目のない Web Audio のループへ切り替える(再生中に、音源の読み込みが終わったとき) */
function engineEnter() {
  if (eng.active || !loop.on || settings.loopMode !== "precise" || !eng.buf || eng.key !== trackKey() || !eng.ctx || eng.ctx.state !== "running") return;
  const pos = audio.currentTime;
  eng.active = true;
  eng.paused = false;
  engineStartSource(pos);
  audio.pause();
  syncPlayers();
}

function engineExit(resume) {
  if (!eng.active) return;
  const pos = enginePos(), wasPlaying = !eng.paused;
  if (eng.src) { try { eng.src.stop(); } catch (e) { /* 無視 */ } eng.src.disconnect(); eng.src = null; }
  eng.active = false;
  if (eng.ctx.state !== "running") eng.ctx.resume().catch(() => {});
  if (resume) {
    audio.currentTime = pos;
    if (wasPlaying) audio.play().catch(() => {});
  }
  syncPlayers();
}

/** 音源を読み込んでデコードする(区間ループを始めたとき、1曲につき1回)。失敗しても、見張る方法で続く */
async function prepareEngine() {
  const key = trackKey();
  const song = currentSong();
  const a = song && pl.cur && song.audios[pl.cur.track];
  if (!a || pl.fallback || settings.loopMode !== "precise") return;
  const url = a.id ? (AUDIO_FILES[a.id] || (DRIVE_KEY ? driveStream(a.id) : "")) : a.url;
  if (!url) return;
  try {
    // latencyHint "playback": 音の切れ(プチプチ)が出にくい、大きめのバッファで再生する
    if (!eng.ctx) eng.ctx = new (window.AudioContext || window.webkitAudioContext)({ latencyHint: "playback" });
    const ready = eng.ctx.state === "running" ? Promise.resolve() : eng.ctx.resume(); // ボタンを押した操作の中で呼ぶ(自動再生の制限のため)
    if (eng.key === key && eng.buf) return;
    if (eng.loading === key) return;
    eng.loading = key;
    updateLoopChip();
    await ready;
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.status);
    const buf = await eng.ctx.decodeAudioData(await res.arrayBuffer());
    if (trackKey() === key) { eng.buf = buf; eng.key = key; }
  } catch (e) { /* 失敗しても、audio 要素を見張る方法で続ける */ } finally {
    if (eng.loading === key) eng.loading = "";
    updateLoopChip();
  }
}

function loopTick() {
  if (!loop.on) return;
  if (eng.active) {
    const now = performance.now();
    if (now - eng.ui > 100) { eng.ui = now; updatePlayerUi(); }
    return;
  }
  if (tpPlaying() && eng.buf && eng.key === trackKey() && eng.ctx && eng.ctx.state === "running") { engineEnter(); return; }
  if (audio.getAttribute("src") && !audio.paused) {
    const b = isFinite(loop.b) ? loop.b : tpDuration();
    if (b && audio.currentTime >= b - 0.015) audio.currentTime = loop.a;
  }
}

function applyLoopMarks() {
  document.querySelectorAll("#sheet .sec").forEach((el, i) => el.classList.toggle("looping", loop.on && loop.idx.includes(i)));
  updateLoopChip();
}

function updateLoopChip() {
  const chip = document.getElementById("loop-chip");
  chip.hidden = !loop.on;
  if (loop.on) document.getElementById("loop-label").textContent = "ループ: " + loop.label + (eng.loading ? "(準備中)" : "");
}

function clearLoop(resume = true) {
  if (!loop.on) return;
  loop.on = false;
  clearInterval(loop.timer);
  loop.timer = 0;
  engineExit(resume);
  applyLoopMarks();
}

/** 時刻(秒)のついたセクションの一覧。終わりは、次のセクションの始まり(最後は曲の終わり) */
function sectionInfos() {
  const list = [];
  document.querySelectorAll("#sheet .sec").forEach((el, i) => {
    if (el.dataset.t === undefined || el.dataset.t === "") return;
    const lab = el.querySelector(".sec-label");
    list.push({ el, i, t: Number(el.dataset.t), label: (lab && lab.textContent.trim()) || "#" + (i + 1), end: null });
  });
  list.forEach((x, k) => {
    const nx = list.slice(k + 1).find((y) => y.t > x.t);
    x.end = nx ? nx.t : null;
  });
  return { list, off: songOffset(currentSong()) };
}

function seekTo(t, play) {
  if (!pl.cur || pl.fallback) return;
  if (eng.active) { engineStartSource(t); return; }
  if (!audio.getAttribute("src")) { pendingSeek = t; startTrack(pl.cur.id, pl.cur.track); return; }
  audio.currentTime = t;
  if (play && audio.paused) audio.play().catch(() => {});
}

function jumpToSection(secEl) {
  const { list, off } = sectionInfos();
  const it = list.find((x) => x.el === secEl);
  if (!it || !pl.cur || pl.fallback) return;
  const t = Math.max(0, it.t + off - LOOP_PREROLL);
  if (loop.on && !(t >= loop.a - 0.05 && t < loop.b)) clearLoop(); // ループの外へ出るときは、ループをやめる
  seekTo(t, true);
  auto.pauseUntil = 0;
}

function setLoopRange(list, lo, hi, off) {
  const endT = list[hi].end != null ? list[hi].end : knownDuration();
  loop.toEnd = list[hi].end == null;
  loop.a = Math.max(0, list[lo].t + off - LOOP_PREROLL);
  loop.b = isFinite(endT) ? endT + off - LOOP_PREROLL : Infinity;
  loop.idx = list.slice(lo, hi + 1).map((x) => x.i);
  loop.label = list[lo].label + (hi > lo ? "〜" + list[hi].label : "");
  loop.on = true;
  if (!loop.timer) loop.timer = setInterval(loopTick, 8);
  applyLoopMarks();
  prepareEngine();
  const pos = tpTime();
  const inside = pos >= loop.a && pos < loop.b;
  if (eng.active) engineStartSource(inside ? pos : loop.a);
  else if (!inside || !tpPlaying()) seekTo(loop.a, true);
}

/** 区間のタグをダブルタップ: その区間をループ。もう一度で解除。ループ中に別の区間なら、範囲を広げる */
function loopSection(secEl) {
  const { list, off } = sectionInfos();
  const k = list.findIndex((x) => x.el === secEl);
  if (k < 0 || !pl.cur || pl.fallback) return;
  let lo = k, hi = k;
  if (loop.on) {
    const inLoop = loop.idx.includes(list[k].i);
    if (inLoop) { clearLoop(); return; } // ループ中の範囲のボタンを押したら、解除
    if (!inLoop) {
      const have = loop.idx.map((i) => list.findIndex((x) => x.i === i)).filter((i) => i >= 0);
      lo = Math.min(k, ...have);
      hi = Math.max(k, ...have);
    }
  }
  setLoopRange(list, lo, hi, off);
}

function labelHints() {
  document.querySelectorAll("#sheet .sec[data-t] .sec-label").forEach((l) => { l.title = "タップ: ここへジャンプ"; });
  // 各セクションの右隅(用紙の余白)に、ループのボタン。歌詞にも、左の色の帯にも重ならない
  document.querySelectorAll("#sheet .sec[data-t]").forEach((sec) => {
    if (sec.querySelector(".sec-loop")) return;
    const b = document.createElement("button");
    b.type = "button";
    b.className = "sec-loop";
    b.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12V9a3 3 0 0 1 3-3h13M17 3l3 3-3 3"/><path d="M20 12v3a3 3 0 0 1-3 3H4M7 21l-3-3 3-3"/></svg>';
    b.setAttribute("aria-label", "このセクションをループ");
    b.title = "ループ(もう一度で解除。ループ中にほかのセクションを押すと、範囲を広げる)";
    sec.appendChild(b);
  });
}

function bindSections() {
  document.getElementById("sheet").addEventListener("click", (e) => {
    if (printing) return;
    const lb = e.target.closest(".sec-loop");
    if (lb) { loopSection(lb.closest(".sec")); return; }
    const lab = e.target.closest(".sec-label");
    if (!lab) return;
    const sec = lab.closest(".sec");
    if (!sec || sec.dataset.t === undefined || sec.dataset.t === "") return;
    jumpToSection(sec); // タグはジャンプだけ(ループは右隅のボタン。2回タップの判定はしない)
  });
  document.getElementById("loop-clear").addEventListener("click", () => clearLoop());
  // 固定のヘッダーの高さ(ループの表示を、その下に置く)
  const hd = document.querySelector(".bar-top");
  const setHead = () => document.documentElement.style.setProperty("--head-h", hd.offsetHeight + "px");
  setHead();
  if (window.ResizeObserver) new ResizeObserver(setHead).observe(hd);
  window.addEventListener("resize", setHead);
}

/* ---------------- プレイリスト ----------------
 * 曲を選んで順番に並べ、続けて再生する。全曲リピート / 1曲リピート。名前を付けて保存・読み込み(この端末のブラウザに保存)。 */
const pls = { active: false, idx: 0 };
const PLAYLISTS_KEY = "songbook.playlists.v1";
const plsIds = () => settings.playlist.ids.filter((id) => songById(id));
const hasAudio = (id) => { const s = songById(id); return !!(s && s.audios && s.audios.length); };
function loadPlaylists() { try { return JSON.parse(localStorage.getItem(PLAYLISTS_KEY) || "{}") || {}; } catch (e) { return {}; } }
function storePlaylists(o) { try { localStorage.setItem(PLAYLISTS_KEY, JSON.stringify(o)); } catch (e) { /* 保存できない環境 */ } }
function savePls() { saveSettings(); renderPlaylist(); }

/** from から dir の向きに、音源のある曲を探す。wrap=false なら、端で止める */
function nextPlayable(ids, from, dir, wrap) {
  for (let n = 0; n < ids.length; n++) {
    let k = from + dir * n;
    if (k < 0 || k >= ids.length) {
      if (!wrap) return -1;
      k = ((k % ids.length) + ids.length) % ids.length;
    }
    if (hasAudio(ids[k])) return k;
  }
  return -1;
}

function playListItem(k) {
  const id = plsIds()[k];
  pls.idx = k;
  switchSong(id);
  startTrack(id, prefTrackIndex(songById(id)));
  renderPlaylist();
}

function plsStart(i) {
  const ids = plsIds();
  const msg = document.getElementById("pls-msg");
  const k = ids.length ? nextPlayable(ids, i, 1, true) : -1;
  if (k < 0) { msg.textContent = ids.length ? "音源のある曲がありません" : "曲を追加してください"; return; }
  msg.textContent = "";
  pls.active = true;
  setPanel(false);
  playListItem(k);
}

function plsNext(dir, auto) {
  const ids = plsIds();
  const k = nextPlayable(ids, pls.idx + dir, dir, settings.playlist.repeat === "all" || !auto);
  if (k < 0) { pls.active = false; syncPlayers(); renderPlaylist(); return; }
  playListItem(k);
}

function plsOnManualSwitch(id) {
  const i = plsIds().indexOf(id);
  if (i >= 0) pls.idx = i; else pls.active = false;
  syncPlayers();
  renderPlaylist();
}

function renderPlsPop() {
  const ids = plsIds();
  const rep = { off: "リピートなし", all: "全曲リピート", one: "1曲リピート" }[settings.playlist.repeat];
  document.getElementById("pls-pop-state").textContent = pls.active ? `再生中 ${pls.idx + 1} / ${ids.length} ・ ${rep}` : ids.length ? `停止中 ・ ${rep}` : "";
  document.getElementById("pls-pop-list").innerHTML = ids.length
    ? ids.map((id, i) => `<li class="${pls.active && i === pls.idx ? "cur" : ""}" data-i="${i}"><button type="button" class="pls-name" data-act="play">${pls.active && i === pls.idx ? "▶ " : ""}${i + 1}. ${esc(songById(id).title)}${hasAudio(id) ? "" : "(音源なし)"}</button></li>`).join("")
    : '<li class="empty">曲が入っていません。「編集(⚙)」で追加できます</li>';
  document.getElementById("pls-pop-end").hidden = !pls.active;
}

function renderPlaylist() {
  renderPlsPop();
  const ids = plsIds();
  const li = ids.map((id, i) => {
    const s = songById(id);
    const cur = pls.active && i === pls.idx;
    return `<li class="${cur ? "cur" : ""}" data-i="${i}"><button type="button" class="pls-name" data-act="play">${i + 1}. ${esc(s.title)}${hasAudio(id) ? "" : "(音源なし)"}</button>` +
      `<button type="button" data-act="up" aria-label="上へ">↑</button><button type="button" data-act="down" aria-label="下へ">↓</button><button type="button" data-act="del" aria-label="外す">✕</button></li>`;
  }).join("");
  document.getElementById("pls-list").innerHTML = li || '<li class="empty">曲を追加してください</li>';
  document.getElementById("pls-repeat").value = settings.playlist.repeat;
  const add = document.getElementById("pls-add");
  add.innerHTML = '<option value="">曲を追加…</option>' + SONGS.map((s) => `<option value="${esc(s.id)}">${esc(s.title)}</option>`).join("");
  const saved = loadPlaylists();
  const sv = document.getElementById("pls-saved");
  const prev = sv.value;
  sv.innerHTML = Object.keys(saved).length ? Object.keys(saved).map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("") : '<option value="">(保存したものはありません)</option>';
  if (prev && saved[prev]) sv.value = prev;
}

function bindPlaylist() {
  settings.playlist = Object.assign({ ids: [], repeat: "off" }, settings.playlist);
  const $ = (id) => document.getElementById(id);
  const msg = (t) => { $("pls-msg").textContent = t; };
  $("pls-play").addEventListener("click", () => plsStart(0));
  $("pls-repeat").addEventListener("change", (e) => {
    settings.playlist.repeat = e.target.value;
    if (pls.active) audio.loop = e.target.value === "one";
    saveSettings();
  });
  $("pls-add").addEventListener("change", (e) => {
    if (e.target.value) { settings.playlist.ids.push(e.target.value); msg(""); savePls(); }
  });
  $("pls-all").addEventListener("click", () => { settings.playlist.ids = SONGS.map((s) => s.id); savePls(); });
  $("pls-clear").addEventListener("click", () => { settings.playlist.ids = []; pls.active = false; syncPlayers(); savePls(); });
  $("pls-list").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]");
    if (!b) return;
    const i = Number(b.closest("li").dataset.i), ids = settings.playlist.ids, act = b.dataset.act;
    if (act === "play") return plsStart(i);
    if (act === "up" && i > 0) [ids[i - 1], ids[i]] = [ids[i], ids[i - 1]];
    else if (act === "down" && i < ids.length - 1) [ids[i + 1], ids[i]] = [ids[i], ids[i + 1]];
    else if (act === "del") ids.splice(i, 1);
    pls.active = false;
    savePls();
  });
  $("pls-save").addEventListener("click", () => {
    const name = $("pls-name").value.trim();
    if (!name) return msg("名前を入れてください");
    if (!plsIds().length) return msg("曲を追加してください");
    const all = loadPlaylists();
    all[name] = plsIds();
    storePlaylists(all);
    msg("「" + name + "」を保存しました");
    renderPlaylist();
    $("pls-saved").value = name;
  });
  $("pls-load").addEventListener("click", () => {
    const all = loadPlaylists(), name = $("pls-saved").value;
    if (!name || !all[name]) return msg("読み込むものがありません");
    settings.playlist.ids = all[name].filter((id) => songById(id));
    pls.active = false;
    $("pls-name").value = name;
    msg("「" + name + "」を読み込みました");
    savePls();
  });
  $("pls-del").addEventListener("click", () => {
    const all = loadPlaylists(), name = $("pls-saved").value;
    if (!name || !all[name] || !confirm("「" + name + "」を削除しますか?")) return;
    delete all[name];
    storePlaylists(all);
    msg("削除しました");
    renderPlaylist();
  });
  const pop = $("pls-pop"), pbtn = $("pls-btn");
  const setPop = (open) => { pop.hidden = !open; pbtn.setAttribute("aria-expanded", String(open)); if (open) renderPlsPop(); };
  pbtn.addEventListener("click", (e) => { e.stopPropagation(); setPop(pop.hidden); });
  $("pls-pop-close").addEventListener("click", () => setPop(false));
  $("pls-pop-list").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act]");
    if (b) { setPop(false); plsStart(Number(b.closest("li").dataset.i)); }
  });
  $("pls-pop-play").addEventListener("click", () => { setPop(false); plsStart(0); });
  $("pls-pop-end").addEventListener("click", () => { pls.active = false; syncPlayers(); renderPlaylist(); });
  $("pls-pop-edit").addEventListener("click", () => { setPop(false); setPanel(true); $("pls-section").scrollIntoView({ block: "start" }); });
  document.addEventListener("click", (e) => { if (!pop.hidden && !pop.contains(e.target)) setPop(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) setPop(false); });
  $("pl-next").addEventListener("click", () => plsNext(1, false));
  $("pl-prev").addEventListener("click", () => { if (tpTime() > 3) seekTo(0, true); else plsNext(-1, false); });
}


/* ---------------- 自動スクロール ----------------
 * 曲の各行に「何時何分に歌う/弾くか」を割り当てて、その時刻の行が画面の上から3割あたりに来るように動かす。
 *  ・割り当ての元は、セクション見出しの @時間(Excelのテンポと小節番号から自動で付く)。
 *    見出しと見出しの間は、行の小節数の比で割る。見出しが無い曲は、曲の長さ(音源の長さ)を行数で割る。
 *  ・時刻の出どころ: 音源を再生中なら音源の位置(シークにも追従)。再生していなければ、画面の中のタイマー。
 *  ・自分でスクロールしたら、数秒だけ止まってから、また追従する。 */
const auto = { on: false, running: false, vt: 0, last: 0, tl: null, pauseUntil: 0, timer: 0, nowEl: null, nowCells: null, nowIdx: -1 };
const auEl = (id) => document.getElementById(id);

function currentAudioDuration() {
  const song = currentSong();
  return pl.cur && song && pl.cur.id === song.id ? tpDuration() : 0;
}

/** 行ごとの開始・終了の秒数を作る */
function buildTimeline() {
  const song = currentSong();
  if (!song) return null;
  const spb = Number(song.tempo) > 0 ? 240 / Number(song.tempo) : 1.5; // 1小節の秒数(4/4拍子)
  const off = songOffset(song); // 見出しの時刻は「小節の頭」。音源のビートは、その分だけ遅れている(行の時刻は、はじめから音源の秒)
  const items = [];
  document.querySelectorAll("#sheet .sec").forEach((sec) => {
    const secHard = sec.dataset.t !== undefined && sec.dataset.t !== "" ? Number(sec.dataset.t) + off : null;
    let first = true;
    sec.querySelectorAll("[data-w]").forEach((el) => {
      const w = Number(el.dataset.w);
      if (w > 0) {
        // 行に書いた時刻(@0:21)があれば、それを使う。なければ、セクションの先頭だけ見出しの時刻
        const own = el.dataset.t !== undefined && el.dataset.t !== "" ? Number(el.dataset.t) : null;
        items.push({ el, est: w * spb, hard: own != null ? own : first ? secHard : null });
        first = false;
      }
    });
  });
  if (!items.length) return null;
  if (items[0].hard == null) items[0].hard = 0;
  // 時刻が前の行より戻っているものは、手がかりから外す(行の途中で、時刻が逆戻りしないように)
  let lastHard = -1;
  items.forEach((it) => {
    if (it.hard == null) return;
    if (it.hard <= lastHard) it.hard = null; else lastHard = it.hard;
  });
  const anchors = [];
  items.forEach((it, i) => { if (it.hard != null) anchors.push(i); });
  const endHint = currentAudioDuration() || song.duration || 0;
  anchors.forEach((a, k) => {
    const b = k + 1 < anchors.length ? anchors[k + 1] : items.length;
    const t0 = items[a].hard;
    const sumEst = items.slice(a, b).reduce((sum, it) => sum + it.est, 0);
    const t1 = k + 1 < anchors.length ? items[b].hard : (endHint > t0 ? endHint : t0 + sumEst);
    const scale = t1 > t0 && sumEst > 0 ? (t1 - t0) / sumEst : 1;
    let t = t0;
    for (let i = a; i < b; i++) {
      items[i].t = t;
      t += items[i].est * scale;
      items[i].t1 = t;
    }
  });
  return items;
}

/** 今の時刻と、その出どころ(audio / timer / idle) */
function autoClock(now) {
  const song = currentSong();
  const audioOn = pl.cur && song && pl.cur.id === song.id && tpPlaying();
  if (audioOn) {
    const t = tpTime();
    auto.vt = t;
    auto.last = now;
    return { t, src: "audio" };
  }
  if (auto.running) auto.vt += ((now - auto.last) / 1000) * settings.autoSpeed;
  auto.last = now;
  return { t: auto.vt, src: auto.running ? "timer" : "idle" };
}

/** 時刻 T にあたる行と、その行の中での進み具合(0〜1) */
function itemAt(T) {
  const tl = auto.tl;
  if (!tl || !tl.length) return null;
  let lo = 0, hi = tl.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (tl[mid].t <= T) lo = mid; else hi = mid - 1;
  }
  const it = tl[lo];
  return { it, p: Math.max(0, Math.min(1, (T - it.t) / Math.max(0.001, it.t1 - it.t))) };
}

function scrollToTime(T) {
  const f = itemAt(T);
  if (!f) return;
  const { it, p } = f;
  const r = it.el.getBoundingClientRect();
  const head = document.querySelector(".bar-top");
  const hb = head ? head.getBoundingClientRect().bottom : 0; // 固定されたヘッダーの下端(この下が、歌詞の見える範囲)
  const target = r.top + window.scrollY + p * r.height - (hb + (window.innerHeight - hb) * 0.18);
  const cur = window.scrollY;
  if (Math.abs(target - cur) > 1) window.scrollTo(0, Math.max(0, cur + (target - cur) * 0.2));
}

/* ---- いま歌う行に色をつける ---- */
function applyHighlight() {
  document.documentElement.style.setProperty("--hl-color", settings.autoColor);
  clearNow();
}

function clearNow() {
  if (auto.tl) auto.tl.forEach((it) => it.el.classList.remove("past"));
  auto.nowIdx = -1;
  if (auto.nowEl) {
    auto.nowEl.classList.remove("now", "wipe");
    auto.nowEl.querySelectorAll(".t").forEach((t) => t.style.removeProperty("--q"));
  }
  auto.nowEl = null;
  auto.nowCells = null;
}

/** 行の中の歌詞を、小節(なければ行全体)ごとに等分し、さらに文字数で割って、染まる順に並べる */
function wipeCells(el) {
  const bars = el.querySelectorAll(":scope > .bar");
  const groups = bars.length ? [...bars].map((b) => [...b.querySelectorAll(".t")]) : [[...el.querySelectorAll(".t")]];
  const cells = [];
  groups.forEach((g, gi) => {
    const total = g.reduce((s, t) => s + [...t.textContent].length, 0);
    if (!total) return;
    const g0 = gi / groups.length, g1 = (gi + 1) / groups.length;
    let cum = 0;
    for (const t of g) {
      const n = [...t.textContent].length;
      if (!n) continue;
      cells.push({ t, s: g0 + (cum / total) * (g1 - g0), e: g0 + ((cum + n) / total) * (g1 - g0) });
      cum += n;
    }
  });
  return cells;
}

function markNow(T) {
  const mode = settings.autoHighlight;
  if (mode === "off") { clearNow(); return; }
  const f = itemAt(T);
  if (!f) return;
  if (auto.nowEl !== f.it.el) {
    clearNow();
    auto.nowEl = f.it.el;
    auto.nowEl.classList.add("now");
    auto.nowCells = null;
    // 過ぎた行は、薄くする
    const idx = auto.tl.indexOf(f.it);
    auto.tl.forEach((it, i) => it.el.classList.toggle("past", i < idx));
  }
  auto.nowEl.classList.toggle("wipe", mode === "wipe");
  if (mode === "wipe") {
    if (!auto.nowCells) auto.nowCells = wipeCells(auto.nowEl);
    for (const c of auto.nowCells) {
      const q = Math.max(0, Math.min(1, (f.p - c.s) / Math.max(0.0001, c.e - c.s)));
      c.t.style.setProperty("--q", (q * 100).toFixed(1) + "%");
    }
  }
}

/** 音源のある曲は、上の再生バーに合わせて動く。テンポで動かすボタンは、音源のない曲だけに出す */
function updateAutoBar() {
  const song = currentSong();
  const noAudio = !(song && song.audios && song.audios.length);
  auEl("auto-bar").hidden = !auto.on || !noAudio;
  auEl("auto-run").classList.toggle("on", auto.running);
}

function autoFrame(now) {
  if (!auto.on) return;
  const clk = autoClock(now);
  const paused = now < auto.pauseUntil;
  if (!printing && clk.src !== "idle" && !paused) {
    if (!auto.tl) auto.tl = buildTimeline();
    scrollToTime(Math.max(0, clk.t - settings.autoOffset));
  }
  if (!printing && clk.src !== "idle") markNow(Math.max(0, clk.t - settings.autoOffset));
  updateAutoBar(clk, paused);
}

function setAuto(on) {
  auto.on = on;
  auEl("auto-toggle").setAttribute("aria-pressed", String(on));
  auEl("auto-bar").hidden = true; // 出すかどうかは、updateAutoBar が曲に合わせて決める
  clearInterval(auto.timer);
  if (!on) clearNow();
  if (on) {
    auto.tl = null;
    auto.last = performance.now();
    auto.timer = setInterval(() => autoFrame(performance.now()), 33); // 約30回/秒。画面が見えていないときは、ブラウザが間引く
  }
}

function bindAuto() {
  auEl("auto-toggle").addEventListener("click", () => {
    setAuto(!auto.on);
    settings.autoOn = auto.on;
    saveSettings();
  });
  auEl("auto-run").addEventListener("click", () => {
    auto.running = !auto.running;
    auto.last = performance.now();
  });
  auEl("auto-top").addEventListener("click", () => {
    auto.vt = 0;
    clearLoop();
    if (audio.getAttribute("src") && pl.cur && pl.cur.id === (currentSong() || {}).id) audio.currentTime = 0;
    window.scrollTo({ top: 0 });
  });
  // 自分でスクロールしたら、数秒だけ止める
  const hold = () => { if (auto.on) auto.pauseUntil = performance.now() + 4000; };
  for (const ev of ["wheel", "touchstart", "touchmove", "mousedown"]) window.addEventListener(ev, hold, { passive: true });
  window.addEventListener("keydown", (e) => { if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(e.key)) hold(); });
  // 音源の長さが分かったら、時間表を作り直す
  for (const ev of ["loadedmetadata", "durationchange"]) audio.addEventListener(ev, () => { auto.tl = null; });
}

bindControls();
bindPlayer();
bindAuto();
bindSections();
bindPlaylist();
renderPlaylist();
resetPlayerFor(currentSong());
syncControls();
setPanel(false);
render();
if (settings.autoOn) setAuto(true);

// 編集者用サーバーで開いているときだけ、編集へのリンクを出す(公開用の静的サイトには出ない)
fetch("/api/ping", { cache: "no-store" })
  .then((r) => (r.ok ? r.json() : null))
  .then((j) => {
    if (j && j.editor) document.getElementById("to-editor").hidden = false;
  })
  .catch(() => {});
