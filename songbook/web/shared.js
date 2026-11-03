"use strict";
/* 閲覧画面(app.js)と編集者ページ(editor.js)で共有する、曲テキスト(.cho)の読み込みと描画 */

/* ---------------- セクションの種類と色 ---------------- */
const FAMILIES = [
  { id: "intro", name: "イントロ" },
  { id: "a", name: "Aメロ" },
  { id: "b", name: "Bメロ" },
  { id: "c", name: "Cメロ" },
  { id: "d", name: "Dメロ" },
  { id: "chorus", name: "サビ" },
  { id: "bridge", name: "ブリッジ" },
  { id: "solo", name: "ソロ・間奏" },
  { id: "outro", name: "アウトロ" },
  { id: "other", name: "その他" },
];

const DEFAULT_COLORS = {
  light: { intro: "#64748b", a: "#2563eb", b: "#16a34a", c: "#9333ea", d: "#0891b2",
           chorus: "#db2777", bridge: "#0d9488", solo: "#ca8a04", outro: "#475569", other: "#6b7280" },
  dark:  { intro: "#94a3b8", a: "#60a5fa", b: "#4ade80", c: "#c084fc", d: "#22d3ee",
           chorus: "#f472b6", bridge: "#2dd4bf", solo: "#facc15", outro: "#cbd5e1", other: "#9ca3af" },
};

/** セクション名(A・1B・サビ2・Intro2・Gsolo など)を種類に分類する */
function classifyLabel(label) {
  const t = String(label || "").toLowerCase().replace(/[\d\s"”'’()（）]/g, "");
  if (/サビ|chorus|^s$|hook/.test(t)) return "chorus";
  if (/イントロ|intro/.test(t)) return "intro";
  if (/アウトロ|outro|ending|coda/.test(t)) return "outro";
  if (/ソロ|solo|間奏|inter/.test(t)) return "solo";
  if (/ブリッジ|bridge/.test(t)) return "bridge";
  const m = /^([a-d])(メロ)?$/.exec(t) || /^([a-d])メロ/.exec(t);
  return m ? m[1] : "other";
}

function readableOn(hex) {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? "#111111" : "#ffffff";
}

/** 既定色にユーザーが変えた色を重ねる */
function resolveColors(theme, custom) {
  return Object.assign({}, DEFAULT_COLORS[theme === "dark" ? "dark" : "light"], custom || {});
}

/* ---------------- コードの移調 ---------------- */
const SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
const BASE = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

function noteIndex(n) {
  let v = BASE[n[0]];
  for (const a of n.slice(1)) v += a === "#" ? 1 : -1;
  return ((v % 12) + 12) % 12;
}

// 移調後のキーが♭系(F, B♭, E♭, A♭, D♭, G♭ / Dm, Gm, Cm, Fm, B♭m, E♭m)なら♭表記にする
function keyUsesFlats(key, semis) {
  const m = /^([A-G][#b]?)(m?)/.exec(key || "");
  if (!m) return false;
  const idx = (noteIndex(m[1]) + semis + 12) % 12;
  return (m[2] ? [2, 7, 0, 5, 10, 3] : [5, 10, 3, 8, 1, 6]).includes(idx);
}

function transposeChord(chord, semis, useFlats) {
  if (!chord || semis === 0) return chord;
  const m = /^([A-G][#b]?)(.*?)(?:\/([A-G][#b]?))?$/.exec(chord);
  if (!m) return chord;
  const names = useFlats ? FLAT : SHARP;
  const root = names[(noteIndex(m[1]) + semis + 12) % 12];
  const bass = m[3] ? "/" + names[(noteIndex(m[3]) + semis + 12) % 12] : "";
  return root + m[2] + bass;
}

/* ---------------- 曲テキスト(.cho)の読み込み ----------------
 *   {title: 曲名} {key: Bm} {tempo: 190}     曲の情報
 *   # A                                      セクション見出し(ChordProの {sov: A} {soc: サビ} {sob: Bridge} も可)
 *   [Bm]届かない[G]響かない                   歌詞はそのまま書き、コードは乗せたい文字の直前に [コード]
 *   | Bm D | G F#7 | %% | / |               コードだけの行。| で小節を区切る
 *                                             / = 前のコードを続ける  % = 前の1小節と同じ  %% = 前の2小節と同じ  %%%% = 前の4小節と同じ
 *   |: ... :|                                繰り返し記号(歌詞の行にも付けられる)
 *   {c: メモ}                                 注釈の行
 */
/** "3:27" / "1:05.5" / "207" -> 秒数。読めなければ null */
function parseTime(str) {
  const m = /^(?:(\d+):)?(\d+(?:\.\d+)?)$/.exec(String(str || "").trim());
  return m ? (m[1] ? Number(m[1]) * 60 : 0) + Number(m[2]) : null;
}

function parseSong(source, fallbackTitle) {
  const song = { title: fallbackTitle || "", key: "", tempo: "", duration: null, sections: [], warnings: [], hasChords: false, audios: [] };
  let sec = null;
  const startSection = (label, ln) => { sec = { label, lines: [], ln }; song.sections.push(sec); };
  const part = { sov: "Verse", start_of_verse: "Verse", soc: "Chorus", start_of_chorus: "Chorus",
                 sob: "Bridge", start_of_bridge: "Bridge", start_of_part: "" };

  String(source || "").replace(/\r\n?/g, "\n").split("\n").forEach((raw, ln) => {
    const s = raw.trim();
    if (!s) return;
    const d = /^\{\s*([A-Za-z_]+)\s*(?::\s*(.*?))?\s*\}$/.exec(s);
    if (d) {
      const name = d[1].toLowerCase(), val = d[2] || "";
      if (name === "title" || name === "t") song.title = val || song.title;
      else if (name === "key") song.key = val;
      else if (name === "tempo") song.tempo = val;
      else if (name === "duration") song.duration = parseTime(val);
      else if (name === "offset") song.offset = Number(val); // 音源のビートが、Excelの小節の頭よりどれだけ遅れているか(秒)
      else if (name === "audio") {
        const a = parseAudio(val);
        if (a) song.audios.push(a); else song.warnings.push({ ln, text: "{audio: } の中身が読み取れません(Google Driveのリンクか、mp3のURLを書いてください)" });
      }
      else if (name === "comment" || name === "c" || name === "ci" || name === "cb") {
        if (!sec) startSection("", ln);
        sec.lines.push({ type: "note", text: val, ln });
      } else if (name in part) startSection(val || part[name], ln);
      else if (!/^end_of_|^(eov|eoc|eob)$/.test(name)) song.warnings.push({ ln, text: `未対応の指定です: {${name}}` });
      return;
    }
    if (s.startsWith("#")) {
      // 見出しの末尾の @0:21 は、その曲の音源での開始時刻(自動スクロール用)
      let label = s.replace(/^#+\s*/, "");
      const tm = /^(.*?)\s*@\s*((?:\d+:)?\d+(?:\.\d+)?)\s*$/.exec(label);
      if (tm) label = tm[1];
      startSection(label, ln);
      sec.time = tm ? parseTime(tm[2]) : null;
      return;
    }
    if (!sec) startSection("", ln);
    // 行の先頭の @0:21 は、その行を歌う(弾く)時刻。自動スクロールと、いまの行の色づけが、この時刻に合う
    const lm = /^@\s*((?:\d+:)?\d+(?:\.\d+)?)\s*(.*)$/.exec(s);
    const body = lm ? lm[2] : s;
    const line = body.startsWith("|") && !body.includes("[") ? parseChordLine(body, ln, song) : parseLyricLine(body, ln, song);
    if (lm) line.time = parseTime(lm[1]);
    sec.lines.push(line);
  });
  return song;
}

/**
 * {audio: 2mix=https://drive.google.com/file/d/ファイルID/view} の中身を読む。
 * 「ラベル=」は省略できる。Google DriveのリンクかファイルID、または mp3 のURL(相対パスも可)。
 * Driveのファイルは「リンクを知っている全員が閲覧可」にしておくこと。
 */
function parseAudio(val) {
  let label = "", target = String(val || "").trim();
  const m = /^([^\/=:?&#\s]{1,24})\s*=\s*(.+)$/.exec(target);
  if (m) { label = m[1]; target = m[2].trim(); }
  if (!target) return null;
  const idm = /drive\.google\.com\/(?:file\/d\/|open\?id=|uc\?[^#\s]*?id=)([\w-]{10,})/.exec(target);
  if (idm) return { label, id: idm[1], url: target };
  if (/^[\w-]{25,}$/.test(target)) return { label, id: target, url: "" };
  if (/^(https?:\/\/|\.{0,2}\/|[\w.-]+\/)/.test(target) || /\.(mp3|m4a|wav|ogg)$/i.test(target)) return { label, id: "", url: target };
  return null;
}

/** コードだけの行: | Bm D | G F#7 | %% | 。各小節 {kind: chords|hold|r1|r2|r4, chords, rs, re} */
function parseChordLine(s, ln, song) {
  const parts = s.split("|");
  const bars = [];
  for (let k = 1; k < parts.length; k++) {
    let txt = parts[k].trim();
    if (k === parts.length - 1 && txt === "") continue; // 行末の |
    let rs = false, re = false;
    if (txt.startsWith(":")) { rs = true; txt = txt.slice(1).trim(); }
    if (txt.endsWith(":")) { re = true; txt = txt.slice(0, -1).trim(); }
    const toks = txt.split(/\s+/).filter((t) => t && t !== "/");
    let bar;
    if (!toks.length) bar = { kind: "hold" };
    else if (toks.length === 1 && toks[0] === "%") bar = { kind: "r1" };
    else if (toks.length === 1 && toks[0] === "%%") bar = { kind: "r2" };
    else if (toks.length === 1 && toks[0] === "%%%%") bar = { kind: "r4" };
    else { bar = { kind: "chords", chords: toks }; song.hasChords = true; }
    bar.rs = rs; bar.re = re;
    bars.push(bar);
  }
  return { type: "chords", bars, ln };
}

/** 歌詞の行: [Bm]届かない[G]響かない 。| があれば小節として区切る */
function parseLyricLine(s, ln, song) {
  const bars = [{ segs: [], rs: false, re: false }];
  let barred = false;
  let cur = { c: "", t: "" };
  const flush = () => { if (cur.c || cur.t) bars[bars.length - 1].segs.push(cur); cur = { c: "", t: "" }; };
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (ch === "[") {
      const j = s.indexOf("]", i);
      if (j < 0) { song.warnings.push({ ln, text: "[ に対応する ] がありません" }); cur.t += ch; continue; }
      flush();
      cur = { c: s.slice(i + 1, j).trim(), t: "" };
      song.hasChords = true;
      i = j;
    } else if (ch === ":" && s[i + 1] === "|") {
      bars[bars.length - 1].re = true;
    } else if (ch === "|") {
      flush();
      barred = true;
      const last = bars[bars.length - 1];
      if (last.segs.length || bars.length > 1 || last.re) bars.push({ segs: [], rs: false, re: false });
      if (s[i + 1] === ":") { bars[bars.length - 1].rs = true; i++; }
    } else {
      cur.t += ch;
    }
  }
  flush();
  if (barred && bars.length > 1 && !bars[bars.length - 1].segs.length) bars.pop(); // 行末の |
  return { type: "lyric", barred, bars, ln };
}

/** songs.js の1曲分(id, source, ...)から、描画できる曲データを作る */
function loadSongs() {
  return ((window.SONGBOOK && window.SONGBOOK.songs) || []).map((e) =>
    Object.assign(parseSong(e.source, e.id), { id: e.id, hasLyricsDoc: e.hasLyricsDoc, docDiff: e.docDiff, source: e.source }));
}

/* ---------------- 描画 ---------------- */
/** 「前の1・2・4小節と同じ」を表す記号(・/・ ・//・ ・////・)。フォントに頼らずSVGで描く */
const SIMILE_SVG = {
  r1: '<svg class="sim-svg" viewBox="0 0 28 24" aria-label="前の小節と同じ"><path d="M9 20 L19 4" stroke-width="2.6" fill="none" stroke-linecap="round"/><circle cx="5" cy="8" r="2.2"/><circle cx="23" cy="16" r="2.2"/></svg>',
  r2: '<svg class="sim-svg" viewBox="0 0 34 24" aria-label="前の2小節と同じ"><path d="M10 20 L17 4 M18 20 L25 4" stroke-width="2.6" fill="none" stroke-linecap="round"/><circle cx="5" cy="8" r="2.2"/><circle cx="29" cy="16" r="2.2"/></svg>',
  r4: '<svg class="sim-svg" viewBox="0 0 50 24" aria-label="前の4小節と同じ"><path d="M10 20 L17 4 M18 20 L25 4 M26 20 L33 4 M34 20 L41 4" stroke-width="2.6" fill="none" stroke-linecap="round"/><circle cx="5" cy="8" r="2.2"/><circle cx="45" cy="16" r="2.2"/></svg>',
};

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function flatsFor(song, semis, accidental) {
  if (accidental === "flat") return true;
  if (accidental === "sharp") return false;
  return keyUsesFlats(song.key, semis);
}

/** 小節の並びを、1行あたりの幅(r2は2小節分、r4は4小節分)が cap を超えないように分ける。cap=0 は分けない */
function chunkBars(bars, cap) {
  if (!cap) return [bars];
  const out = [];
  let cur = [], w = 0;
  for (const b of bars) {
    const bw = b.kind === "r4" ? 4 : b.kind === "r2" ? 2 : 1;
    if (cur.length && w + bw > cap) { out.push(cur); cur = []; w = 0; }
    cur.push(b);
    w += bw;
  }
  if (cur.length) out.push(cur);
  return out;
}

/**
 * 1曲分のHTMLを作る。
 * o: transpose, accidental, showSections, showInst, showMeta, colors, barCap,
 *    songs(曲名から曲を選べるようにする一覧), audio(再生バーの置き場所を作る)
 */
function renderSong(song, o) {
  const semis = o.transpose || 0;
  const flats = flatsFor(song, semis, o.accidental);
  const tc = (c) => esc(transposeChord(c, semis, flats));
  const colors = o.colors || resolveColors("light");
  const parts = [];

  const keyText = song.key
    ? transposeChord(song.key.replace(/m$/, ""), semis, flats) + (song.key.endsWith("m") ? "m" : "")
    : "";
  const meta = [];
  if (keyText) meta.push(`<span class="mi"><i>Key</i>${esc(keyText)}</span>`);
  if (song.tempo) meta.push(`<span class="mi"><i>BPM</i>${esc(song.tempo)}</span>`);
  // 閲覧画面: 曲名を押すと曲を選べる(o.songs)。音源があれば、曲名の下に再生バーの置き場所を作る(o.audio)
  const audios = o.audio && song.audios ? song.audios : [];
  const titleHtml = o.songs
    ? `<h2 class="title-pick"><span class="tp-text">${esc(song.title)}</span><span class="tp-caret" aria-hidden="true">▾</span>` +
      `<select class="title-select" aria-label="曲を選ぶ">` +
      o.songs.map((x) => `<option value="${esc(x.id)}"${x.id === song.id ? " selected" : ""}>${esc(x.title)}</option>`).join("") +
      `</select></h2>`
    : `<h2>${esc(song.title)}</h2>`;
  parts.push(
    `<div class="song-head">${titleHtml}` +
      (o.showMeta && meta.length ? `<span class="meta">${meta.join("")}</span>` : "") +
      `</div>`
  );

  const segHtml = (segs) => segs
    .map((c) => `<span class="cell"><span class="c">${c.c ? tc(c.c) : ""}</span><span class="t">${esc(c.t)}</span></span>`)
    .join("");

  const timeAttr = (l) => (typeof l.time === "number" ? ` data-t="${l.time}"` : "");
  const barClass = (b, extra) => ["bar", extra, b.rs ? "rep-start" : "", b.re ? "rep-end" : ""].filter(Boolean).join(" ");

  for (const sec of song.sections) {
    const fam = classifyLabel(sec.label);
    const color = colors[fam];
    const style = `--sec:${color};--sec-fg:${readableOn(color)}`;
    // セクション名は、左の色の帯の中に縦書きで置く(短い名前は文字を立てて、長い名前は横向きに倒す)
    const label = !o.showSections ? ""
      : sec.label ? `<div class="sec-label ${[...sec.label].length <= 3 ? "up" : "side"}">${esc(sec.label)}</div>`
      : '<div class="sec-label bare"></div>';
    const lines = sec.lines.filter((l) => o.showInst || l.type !== "chords");
    if (!lines.length) continue;

    const html = lines.map((l, li) => {
      const last = li === lines.length - 1;
      if (l.type === "note") return `<div class="note" data-ln="${l.ln}">${esc(l.text)}</div>`;
      if (l.type === "lyric" && !l.barred) {
        return `<div class="line" data-ln="${l.ln}" data-w="4"${timeAttr(l)}>${segHtml(l.bars[0].segs)}</div>`;
      }
      // 1小節に複数のコードがある行は、狭い画面でコードが小さくなりすぎないよう、折り返しを2小節までにする
      const cap = o.barCap && l.bars.some((b) => b.chords && b.chords.length > 1) ? Math.min(o.barCap, 2) : o.barCap;
      const rows = chunkBars(l.bars, cap);
      return rows.map((row, ri) => {
        const endRow = last && ri === rows.length - 1;
        const bars = row.map((b, bi) => {
          const cls = [];
          if (endRow && bi === row.length - 1) cls.push("sec-end");
          if (l.type === "lyric") {
            return `<div class="${barClass(b, "lyric " + cls.join(" "))}"><div class="cells">${segHtml(b.segs)}</div></div>`;
          }
          if (b.kind === "r1") {
            return `<div class="${barClass(b, "inst sim sim-1 " + cls.join(" "))}"><div class="cells"><span class="simbox">${SIMILE_SVG.r1}</span></div></div>`;
          }
          if (b.kind === "r2" || b.kind === "r4") {
            // 2小節・4小節の繰り返し記号は、小節線を残したまま小節線の上に重ねて置く
            const n = b.kind === "r2" ? 2 : 4;
            const parts = Array.from({ length: n }, (_, pi) => {
              const pc = ["inst sim-part"];
              if (pi === n / 2) pc.push("sim-center"); // 記号の真下の小節線は、記号を避けて切る
              if (pi === 0) { if (b.rs) pc.push("rep-start"); }
              if (pi === n - 1) { if (b.re) pc.push("rep-end"); pc.push(...cls); }
              return `<div class="bar ${pc.join(" ")}"><div class="cells"></div></div>`;
            }).join("");
            return `<div class="simgroup" style="flex-grow:${n}">${parts}<span class="simmark">${SIMILE_SVG[b.kind]}</span></div>`;
          }
          const inner = b.kind === "hold"
            ? '<span class="hold">／</span>'
            : b.chords.map((c) => `<span class="cell"><span class="c">${tc(c)}</span><span class="t"></span></span>`).join("");
          // 1小節に複数のコードがあるときは、等分して1行に収める(文字の大きさは、CSS側で小節の幅に合わせて縮める)
          const shown = b.kind === "hold" ? [] : b.chords.map((c) => transposeChord(c, semis, flats));
          const vars = shown.length ? ` style="--n:${shown.length};--len:${Math.max(...shown.map((x) => [...x].length))}"` : "";
          return `<div class="${barClass(b, "inst " + cls.join(" "))}"${vars}><div class="cells">${inner}</div></div>`;
        }).join("");
        // data-w: 自動スクロールで、この行に使う時間の重み(小節数)
        const weight = row.reduce((sum, b) => sum + (b.kind === "r4" ? 4 : b.kind === "r2" ? 2 : 1), 0);
        return `<div class="mline" data-ln="${l.ln}" data-w="${weight}"${ri === 0 ? timeAttr(l) : ""}>${bars}</div>`;
      }).join("");
    }).join("");
    const tAttr = typeof sec.time === "number" ? ` data-t="${sec.time}"` : "";
    parts.push(`<section class="sec fam-${fam}" style="${style}"${tAttr}>${label}<div class="sec-body">${html}</div></section>`);
  }
  return `<article class="song${o.pageBreak ? " page-break" : ""}">${parts.join("")}</article>`;
}
