#!/usr/bin/env python
"""公開用のフォルダ songbook/dist を作る。

  python songbook/publish.py            パスワードを聞く。入れれば曲データを暗号化、何も入れずに Enter なら暗号化しない
  python songbook/publish.py --plain    聞かずに、暗号化しない(URLを知っていれば誰でも見られる)

・曲データ(songs.js)を作り直してから、web/ の閲覧に必要なファイルだけを dist/ に集める。
・編集者ページ(editor.html / editor.js)と波形編集(waveedit.html / waveedit.js)は含めない。公開版には、曲を書き換える機能も保存APIも無い。
・パスワードを入れたときは、曲データ(歌詞・コード・Drive APIキー)を暗号化して dist/songs.js に入れる。
  パスワードを知らない人は、歌詞を読めない。開くときに、画面でパスワードを入力する(各端末で一度入れれば、覚えておける)。
・パスワードなしのときは、URLを知っている人が、そのまま見られる。
・検索エンジンに載らないよう、robots.txt と noindex を付ける。
パスワードは、次の順で決める(保存はしない。画面にも出さない):
  1. 環境変数 SONGBOOK_PASSWORD
  2. --password-file ファイル名 で指定したファイルの1行目
  3. プロジェクト直下の pass.txt の1行目(あれば)
  4. 画面で入力(何も入れずに Enter なら、暗号化しない)
"""
import base64
import getpass
import hashlib
import re
import urllib.request
import json
import os
import secrets
import shutil
import sys
from pathlib import Path

import build_songs

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
DIST = HERE / "dist"
EXCLUDE = {"editor.html", "editor.js", "waveedit.html", "waveedit.js"}
PASS_FILE = HERE.parent / "pass.txt"  # あれば、1行目をパスワードとして使う(Gitには入れない)
ITERATIONS = 310_000  # パスワードの総当たりを遅くするための繰り返し回数


def encrypt(plain: str, password: str) -> dict:
    """パスワードから鍵を作り(PBKDF2-SHA256)、AES-256-GCM で暗号化する。ブラウザの WebCrypto で復号できる形式。"""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt, iv = secrets.token_bytes(16), secrets.token_bytes(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(password.encode("utf-8"))
    data = AESGCM(key).encrypt(iv, plain.encode("utf-8"), None)  # 末尾に認証タグが付く(WebCryptoと同じ形)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"v": 1, "iter": ITERATIONS, "salt": b64(salt), "iv": b64(iv), "data": b64(data)}


def read_password_file(path: Path) -> str:
    """ファイルの1行目(空行は飛ばす)をパスワードとして読む。文字コードは UTF-8 か Shift_JIS。"""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp932")
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def ask_password() -> str:
    """パスワードを決める。環境変数 -> --password-file -> pass.txt -> 画面で入力、の順。
    何も入れずに Enter なら "" (暗号化しない)。パスワードの中身は、画面に出さない。"""
    pw = os.environ.get("SONGBOOK_PASSWORD")
    if pw is not None:
        return pw
    path = None
    if "--password-file" in sys.argv:
        path = Path(sys.argv[sys.argv.index("--password-file") + 1])
    elif PASS_FILE.exists():
        path = PASS_FILE
    if path is not None:
        pw = read_password_file(path)
        if pw == "":
            print(f"{path.name} が空なので、パスワードなし(暗号化しない)で公開します。")
            return ""
        if len(pw) < 8:
            sys.exit(f"{path} のパスワードが短すぎます(8文字以上にしてください)。")
        print(f"パスワードは {path.name} の1行目({len(pw)}文字)を使います。")
        return pw
    while True:
        pw = getpass.getpass("パスワードをかけるなら入力(8文字以上)。かけないなら、何も入れずに Enter: ")
        if pw == "":
            return ""
        if len(pw) < 8:
            print("  8文字以上にしてください(かけないなら、何も入れずに Enter)。")
            continue
        if getpass.getpass("もう一度入力: ") != pw:
            print("  一致しません。もう一度。")
            continue
        return pw


def drive_meta(fid, cfg):
    """Driveのファイルの名前・大きさ・MD5(中身の指紋)。取れなければ None"""
    key, site = cfg.get("driveApiKey", ""), cfg.get("siteUrl", "")
    if not key or not site:
        return None
    req = urllib.request.Request(f"https://www.googleapis.com/drive/v3/files/{fid}?fields=name,size,md5Checksum,modifiedTime&key={key}",
                                 headers={"Referer": site + "/"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=30))
    except Exception:  # noqa: BLE001
        return None


def collect_audio(entries, cfg):
    """曲のテキストの {audio: ラベル=Driveのリンク} の音源を、公開サイトに一緒に置く(dist/audio/<DriveのファイルID>.mp3)。
    中身は、Excelと同じフォルダの mp3 のうち、Driveのファイルと MD5 が同じもの。同じものが無ければ、名前にラベル(2mix など)を含むものを使い、注意を出す。
    Driveからの配信は、アクセスが多いとGoogleに止められるため。戻り値: {DriveのファイルID: "audio/<ID>.mp3"}"""
    manifest = {e["id"]: e for e in build_songs.load_manifest()}
    out, notes, stems, twomix = {}, [], {}, {}
    (DIST / "audio").mkdir(exist_ok=True)
    for ent in entries:
        m = manifest.get(ent["id"])
        if not m or not m.get("xlsx"):
            continue
        folder = (build_songs.ROOT / m["xlsx"]).parent
        local = {p: None for p in folder.glob("*.mp3")}
        for am in re.finditer(r"^\{\s*audio\s*:\s*(.+?)\s*\}", ent["source"], re.M):
            val, label = am.group(1), ""
            lm = re.match(r"^([^/=:?&#\s]{1,24})\s*=\s*(.+)$", val)
            if lm:
                label, val = lm.group(1), lm.group(2)
            im = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?[^#\s]*?id=)([\w-]{10,})", val)
            if not im:
                continue
            fid = im.group(1)
            meta = drive_meta(fid, cfg) or {}
            hit = None
            if meta.get("md5Checksum"):
                for p in local:
                    if local[p] is None:
                        local[p] = hashlib.md5(p.read_bytes()).hexdigest()
                    if local[p] == meta["md5Checksum"]:
                        hit = p
                        break
            if hit is None:
                cands = [p for p in local if label and label.lower() in p.name.lower()] or [p for p in local if "2mix" in p.name.lower()]
                if not cands:
                    notes.append(f"{ent['id']} {label or ''}: フォルダに mp3 が無いので、Driveから再生します")
                    continue
                hit = cands[0]
                notes.append(f"{ent['id']} {label or ''}: Driveのファイルと中身が違うため、フォルダの {hit.name} を使います"
                             + (f"(Drive: {meta.get('modifiedTime', '')[:10]} 更新)" if meta else "(Driveの情報が取れませんでした)"))
            shutil.copy2(hit, DIST / "audio" / f"{fid}.mp3")
            out[fid] = f"audio/{fid}.mp3"
            if label.lower() == "2mix" or not label:
                twomix[ent["id"]] = (hit, out[fid])
    # 種類ごとの音源(ボーカルなし・ギターなし など)。設定で選ぶと、どの曲もその種類で再生する
    for ent in entries:
        m = manifest.get(ent["id"])
        st = build_songs.local_stems(m) if m else {}
        if not st:
            continue
        key = hashlib.md5(ent["id"].encode("utf-8")).hexdigest()[:10]
        urls = {}
        base = twomix.get(ent["id"])
        for s, p in st.items():
            if s == "2mix" and base:
                urls[s] = base[1]
                continue
            shutil.copy2(p, DIST / "audio" / f"{key}-{s}.mp3")
            urls[s] = f"audio/{key}-{s}.mp3"
        ref = (base[0] if base else st.get("2mix"))
        if ref:
            old = [s for s, p in st.items() if s != "2mix" and ref.stat().st_mtime - p.stat().st_mtime > 86400]
            if old:
                notes.append(f"{ent['id']}: {', '.join(old)} が 2mix より古い書き出しです(曲の中身が違うかもしれません)")
        stems[ent["id"]] = urls
    for n in notes:
        print("  注意:", n)
    return out, stems


def main():
    plain_mode = "--plain" in sys.argv
    entries = build_songs.main()  # songs.js を最新にする
    api_key = build_songs.load_json(HERE / "config.json", {}).get("driveApiKey", "")

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    copied = []
    for f in sorted(WEB.iterdir()):
        if f.is_file() and f.name not in EXCLUDE and not f.name.startswith("_"):
            shutil.copy2(f, DIST / f.name)
            copied.append(f.name)

    audio_files, audio_stems = collect_audio(entries, build_songs.load_json(HERE / "config.json", {}))
    data = {"songs": entries, "driveApiKey": api_key, "audioFiles": audio_files, "audioStems": audio_stems}
    password = "" if plain_mode else ask_password()
    plain_mode = plain_mode or password == ""
    if plain_mode:
        print("曲データは暗号化しません。公開先のURLを知っている人は、誰でも見られます。")
        (DIST / "songs.js").write_text("window.SONGBOOK = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    else:
        payload = json.dumps(data, ensure_ascii=False)
        enc = encrypt(payload, password)
        (DIST / "songs.js").write_text("window.SONGBOOK_ENC = " + json.dumps(enc) + ";\n", encoding="utf-8")

    index = DIST / "index.html"
    html = index.read_text(encoding="utf-8")
    html = html.replace('<meta name="viewport"', '<meta name="robots" content="noindex, nofollow">\n<meta name="viewport"', 1)
    index.write_text(html, encoding="utf-8")

    (DIST / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    # Cloudflare Pages / Workers の指定: 検索に載せない・常に最新を読む(曲を直したらすぐ反映する)
    (DIST / "_headers").write_text("/*\n  X-Robots-Tag: noindex, nofollow\n  Cache-Control: no-cache\n", encoding="utf-8")
    copied += ["robots.txt", "_headers"]

    print(f"{len(entries)}曲を dist に書き出しました: {DIST}")
    print("含めたファイル:", ", ".join(copied))
    print("曲データ:", "暗号化なし(URLを知っていれば誰でも見られる)" if plain_mode else "パスワードで暗号化済み")
    size = sum(p.stat().st_size for p in (DIST / "audio").glob("*.mp3")) / 1048576
    n_audio = len(list((DIST / "audio").glob("*.mp3")))
    print(f"音源: {n_audio} 本をサイトに置きました(合計 {size:.0f} MB)")
    if api_key:
        print("Drive APIキー: あり(公開先のアドレスを、キーのリファラー制限に追加してください)")


if __name__ == "__main__":
    sys.exit(main())
