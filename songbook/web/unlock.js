"use strict";
/* 公開版の入口。曲データがパスワードで暗号化されているとき(window.SONGBOOK_ENC)は、
 * パスワードを入力させて復号してから、画面(app.js)を読み込む。
 * 手元の画面(暗号化されていない songs.js)では、そのまま app.js を読み込む。 */
(function () {
  const enc = window.SONGBOOK_ENC;
  const PW_KEY = "songbook.pw";

  function start() {
    const s = document.createElement("script");
    s.src = "app.js";
    document.body.appendChild(s);
  }
  if (!enc) return start();

  const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

  async function decrypt(pw) {
    const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveKey"]);
    const key = await crypto.subtle.deriveKey(
      { name: "PBKDF2", salt: b64(enc.salt), iterations: enc.iter, hash: "SHA-256" },
      base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64(enc.iv) }, key, b64(enc.data));
    return JSON.parse(new TextDecoder().decode(plain));
  }

  function open(data, pw, remember) {
    window.SONGBOOK = data;
    if (remember) { try { localStorage.setItem(PW_KEY, pw); } catch (e) { /* 保存できない環境 */ } }
    const gate = document.getElementById("gate");
    if (gate) gate.remove();
    start();
  }

  function showGate() {
    const gate = document.createElement("div");
    gate.id = "gate";
    gate.innerHTML =
      '<form class="gate-box" autocomplete="off">' +
      "<h1>コード付き歌詞ブック</h1>" +
      "<p>パスワードを入力してください。</p>" +
      '<input id="gate-pw" type="password" autocomplete="current-password" aria-label="パスワード" autofocus>' +
      '<label class="gate-remember"><input id="gate-remember" type="checkbox" checked> この端末で覚える</label>' +
      '<button type="submit">開く</button>' +
      '<p id="gate-msg" class="gate-msg" role="alert"></p>' +
      "</form>";
    document.body.appendChild(gate);
    const form = gate.querySelector("form");
    const input = gate.querySelector("#gate-pw");
    const msg = gate.querySelector("#gate-msg");
    input.focus();
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      msg.textContent = "確認しています…";
      try {
        const pw = input.value;
        open(await decrypt(pw), pw, gate.querySelector("#gate-remember").checked);
      } catch (err) {
        msg.textContent = "パスワードが違います。";
        input.select();
      }
    });
  }

  if (!window.crypto || !crypto.subtle) {
    document.body.insertAdjacentHTML("beforeend", '<p style="padding:2em">この環境では復号できません(https のアドレスで開いてください)。</p>');
    return;
  }
  // 前に覚えたパスワードがあれば、そのまま開く
  let saved = null;
  try { saved = localStorage.getItem(PW_KEY); } catch (e) { /* 無視 */ }
  if (saved) decrypt(saved).then((d) => open(d, saved, false)).catch(() => { try { localStorage.removeItem(PW_KEY); } catch (e) { /* 無視 */ } showGate(); });
  else showGate();
})();
