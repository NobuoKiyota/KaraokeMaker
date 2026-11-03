"use strict";
/* 編集者ページ: 曲のテキスト(.cho)を直接編集し、右のプレビューで確認して保存する */

const SONGS = loadSongs();
const VIEWER_KEY = "songbook.settings.v3"; // 閲覧画面の設定(色)をそのまま使う
const THEME_KEY = "songbook.editor.theme";

const wantedSong = new URLSearchParams(location.search).get("song"); // 閲覧画面の「編集」リンクから開いたとき
const state = {
  id: (SONGS.find((s) => s.id === wantedSong) || SONGS[0] || {}).id || null,
  saved: "",          // 最後に保存した(または読み込んだ)テキスト
  server: null,       // 保存APIが使えるか(null=確認中)
};

const $ = (id) => document.getElementById(id);
const src = () => $("src");
const darkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
const entry = () => SONGS.find((s) => s.id === state.id);
const isDirty = () => src().value !== state.saved;
let publishing = false;

function viewerSettings() {
  try { return JSON.parse(localStorage.getItem(VIEWER_KEY) || "{}"); } catch (e) { return {}; }
}

function effectiveTheme() {
  const v = $("theme").value;
  if (v === "dark" || v === "light") return v;
  return darkQuery && darkQuery.matches ? "dark" : "light";
}

function updateStatus(msg) {
  const el = $("status");
  const dirty = isDirty();
  el.textContent = msg || (state.server === false ? "保存できません(songbook.bat で起動してください)" : dirty ? "未保存の変更があります" : "");
  el.classList.toggle("dirty", dirty && !msg);
  $("save").disabled = !dirty || state.server === false;
  $("publish").disabled = state.server === false || publishing;
}

/* ---------------- プレビュー ---------------- */
let renderTimer = null;
function scheduleRender() {
  updateStatus();
  clearTimeout(renderTimer);
  renderTimer = setTimeout(render, 120);
}

function render() {
  $("to-wave").href = "waveedit.html?song=" + encodeURIComponent(state.id || "");
  const theme = effectiveTheme();
  document.documentElement.dataset.theme = theme;
  const parsed = parseSong(src().value, state.id);
  const sheet = $("sheet");
  sheet.className = "sheet";
  sheet.style.setProperty("--lyric", "14pt");
  sheet.style.setProperty("--chord-scale", ".8");
  sheet.style.setProperty("--gap", "6px");
  sheet.style.setProperty("--margin", "10mm");
  sheet.style.setProperty("--paper-w", "210mm");
  sheet.innerHTML = renderSong(parsed, {
    transpose: 0, showSections: true, showInst: true, showMeta: true, barCap: 0,
    colors: resolveColors(theme, viewerSettings().customColors),
  });
  $("warnings").innerHTML = parsed.warnings.map((w) => `${w.ln + 1}行目: ${esc(w.text)}`).join("<br>");
  renderChips(parsed);
  highlight(false);
}

function renderChips(parsed) {
  const count = new Map();
  const add = (c) => count.set(c, (count.get(c) || 0) + 1);
  for (const sec of parsed.sections) for (const l of sec.lines) {
    if (l.type === "lyric") l.bars.forEach((b) => b.segs.forEach((g) => g.c && add(g.c)));
    else if (l.type === "chords") l.bars.forEach((b) => (b.chords || []).forEach(add));
  }
  const chords = [...count.entries()].sort((a, b) => b[1] - a[1]).map(([c]) => c);
  $("chips").innerHTML = '<span class="lbl">コード:</span>' +
    chords.map((c) => `<button type="button" data-chord="${esc(c)}">${esc(c)}</button>`).join("");
}

/** カーソルのある行を、プレビュー側でも強調して表示位置に出す */
function highlight(scroll) {
  const ta = src();
  const ln = ta.value.slice(0, ta.selectionStart).split("\n").length - 1;
  const sheet = $("sheet");
  sheet.querySelectorAll(".hl").forEach((e) => e.classList.remove("hl"));
  let target = null;
  for (let n = ln; n >= 0 && !target; n--) target = sheet.querySelector(`[data-ln="${n}"]`);
  if (!target) return;
  sheet.querySelectorAll(`[data-ln="${target.dataset.ln}"]`).forEach((e) => e.classList.add("hl"));
  if (scroll) {
    const r = target.getBoundingClientRect();
    if (r.top < 70 || r.bottom > window.innerHeight - 20) target.scrollIntoView({ block: "center", behavior: "smooth" });
  }
}

/* ---------------- 編集操作 ---------------- */
function insertAtCursor(text) {
  const ta = src();
  ta.focus();
  // execCommand は元に戻す(Ctrl+Z)の履歴に残る。使えない環境では値を直接書き換える
  if (!document.execCommand || !document.execCommand("insertText", false, text)) {
    ta.setRangeText(text, ta.selectionStart, ta.selectionEnd, "end");
  }
  scheduleRender();
}

function loadSong(id) {
  state.id = id;
  const e = entry();
  src().value = e ? e.source : "";
  state.saved = src().value;
  src().scrollTop = 0;
  src().setSelectionRange(0, 0);
  renderDiff(e && e.docDiff);
  render();
  updateStatus();
}

function renderDiff(d) {
  const box = $("diff-box");
  if (!d) {
    box.style.display = "none";
    return;
  }
  box.style.display = "";
  if (d.same) {
    $("diff-sum").innerHTML = '歌詞docとの違い: <span class="diff-ok">なし(一致)</span>';
    $("diff-body").innerHTML = "";
    return;
  }
  const n = d.choOnly.length + d.docOnly.length + d.differs.length;
  $("diff-sum").textContent = `歌詞docとの違い: ${n}箇所(確認してください)`;
  const li = (t) => `<li>${t}</li>`;
  $("diff-body").innerHTML =
    (d.choOnly.length ? `<b>このテキストにだけある歌詞</b><ul class="diff-list">${d.choOnly.map((x) => li(`『${esc(x.text)}』 … ${esc(x.line)}`)).join("")}</ul>` : "") +
    (d.docOnly.length ? `<b>歌詞docにだけある歌詞</b><ul class="diff-list">${d.docOnly.map((x) => li(`『${esc(x.text)}』 … ${esc(x.line)}`)).join("")}</ul>` : "") +
    (d.differs.length ? `<b>表記が違う文字</b><ul class="diff-list">${d.differs.map((x) => li(`テキスト『${esc(x.cho)}』 doc『${esc(x.doc)}』 … ${esc(x.line)}`)).join("")}</ul>` : "");
}

/* ---------------- 保存・下書き ---------------- */
async function save() {
  const e = entry();
  // 保存先の取り違え防止(ブラウザが、再読み込みのときに古い入力を戻してしまうことがある)
  if ($("song").value !== state.id) {
    updateStatus("保存しませんでした");
    alert("画面の曲の選択と、編集中の曲が食い違っています。保存を止めました。\nページを開き直してから、もう一度編集してください。");
    return;
  }
  const m = /^\{\s*title\s*:\s*(.*?)\s*\}/m.exec(src().value);
  if (m && m[1] && m[1] !== e.title &&
      !confirm(`テキストの曲名は『${m[1]}』ですが、保存先は『${e.title}』です。\nこのまま保存すると、『${e.title}』の内容が置き換わります。\n保存しますか?`)) {
    updateStatus("保存をやめました");
    return;
  }
  updateStatus("保存中…");
  try {
    const res = await fetch("/api/source/" + encodeURIComponent(e.id), {
      method: "PUT",
      headers: { "Content-Type": "text/plain; charset=utf-8" },
      body: src().value,
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || res.status);
    e.source = src().value;
    e.docDiff = data.docDiff;
    state.saved = src().value;
    renderDiff(e.docDiff);
    updateStatus("保存しました(閲覧画面にも反映されます)");
  } catch (err) {
    updateStatus("保存に失敗しました: " + err.message);
  }
}

/** 保存した内容を、公開サイトに反映する(サーバーが deploy.py を裏で動かす。1分ほどかかる) */
async function publish() {
  if (isDirty() && !confirm("未保存の変更があります。保存していない内容は、公開されません。\nこのまま公開しますか?")) return;
  if (!confirm("公開サイトに反映します(全曲)。\nよろしいですか?")) return;
  publishing = true;
  updateStatus("公開中…(1分ほどかかります)");
  try {
    const res = await fetch("/api/publish", { method: "POST", headers: { "X-Songbook": "1" } });
    if (!res.ok) throw new Error(res.status === 409 ? "すでに公開中です" : res.status);
    for (;;) {
      await new Promise((r) => setTimeout(r, 2000));
      const st = await (await fetch("/api/publish", { cache: "no-store" })).json();
      if (st.running) continue;
      publishing = false;
      if (st.ok) {
        updateStatus("公開しました(開いたままの画面は、再読み込みで新しくなります)");
      } else {
        updateStatus("公開に失敗しました");
        alert("公開に失敗しました。\n\n" + (st.log || "").split("\n").slice(-12).join("\n"));
      }
      return;
    }
  } catch (err) {
    publishing = false;
    updateStatus("公開に失敗しました: " + err.message);
  }
}

async function redraft() {
  const e = entry();
  if (!confirm("xlsx・歌詞docから下書きを作り直します。\nこの曲で直した内容は新しい下書きに置き換わります(今の内容は .cho.bak に退避)。\nよろしいですか?")) return;
  updateStatus("作り直し中…");
  try {
    const res = await fetch("/api/redraft/" + encodeURIComponent(e.id), { method: "POST" });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || res.status);
    e.source = data.source;
    e.docDiff = data.docDiff;
    state.saved = data.source;
    src().value = data.source;
    renderDiff(e.docDiff);
    render();
    updateStatus("下書きを作り直しました");
  } catch (err) {
    updateStatus("作り直しに失敗しました: " + err.message);
  }
}

async function checkServer() {
  try {
    const res = await fetch("/api/ping", { cache: "no-store" });
    state.server = res.ok && (await res.json()).editor === true;
  } catch (e) {
    state.server = false;
  }
  updateStatus();
}

/* ---------------- 初期化 ---------------- */
function init() {
  $("song").innerHTML = SONGS
    .map((s) => `<option value="${esc(s.id)}">${esc(s.title)}${s.hasChords ? "" : "(コード未作成)"}</option>`)
    .join("");

  let savedTheme = null;
  try { savedTheme = localStorage.getItem(THEME_KEY); } catch (e) { /* 保存できない環境 */ }
  $("theme").value = savedTheme || viewerSettings().theme || "auto";
  $("theme").addEventListener("input", () => {
    try { localStorage.setItem(THEME_KEY, $("theme").value); } catch (e) { /* 無視 */ }
    render();
  });
  if (darkQuery) darkQuery.addEventListener("change", () => $("theme").value === "auto" && render());

  $("song").addEventListener("change", () => {
    if (isDirty() && !confirm("保存していない変更があります。破棄して曲を切り替えますか?")) {
      $("song").value = state.id;
      return;
    }
    loadSong($("song").value);
  });

  const ta = src();
  ta.addEventListener("input", scheduleRender);
  for (const ev of ["keyup", "click", "focus"]) ta.addEventListener(ev, () => highlight(true));
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Tab") { e.preventDefault(); insertAtCursor("  "); }
  });

  $("chips").addEventListener("click", (e) => {
    const b = e.target.closest("[data-chord]");
    if (b) insertAtCursor(`[${b.dataset.chord}]`);
  });
  document.querySelector(".symbols").addEventListener("click", (e) => {
    const b = e.target.closest("[data-ins]");
    if (b) insertAtCursor(b.dataset.ins);
  });

  $("save").addEventListener("click", save);
  $("publish").addEventListener("click", publish);
  $("redraft").addEventListener("click", redraft);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      if (!$("save").disabled) save();
    }
  });
  window.addEventListener("beforeunload", (e) => {
    if (isDirty()) { e.preventDefault(); e.returnValue = ""; }
  });

  if (state.id) {
    $("song").value = state.id;
    loadSong(state.id);
  } else {
    render();
  }
  checkServer();
}

init();
