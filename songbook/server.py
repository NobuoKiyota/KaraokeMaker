#!/usr/bin/env python
"""songbook のローカルサーバー(編集者用)。

  python songbook/server.py [port]

・web/ を配信する(閲覧画面 index.html、編集者ページ editor.html)
・編集者ページ用のAPI:
    PUT  /api/source/<曲ID>    曲のテキストを songbook/songs/<曲>.cho に保存し、songs.js を作り直す
    POST /api/redraft/<曲ID>   xlsx・歌詞docから下書きを作り直す(今の内容は .cho.bak に退避)
    POST /api/publish          公開サイトに反映する(deploy.py を裏で動かす。1分ほどかかる)
    GET  /api/publish          その進み具合(running / ok / log)
    GET  /api/drive-list/<曲ID>        波形編集用: 曲のテキストの {audio: } のDrive音源(名前・日付・大きさ)
    GET  /api/drive-audio/<曲ID>?i=N   波形編集用: そのDrive音源(このPCのサーバーが、公開サイトと同じ手順で取ってくる)
    GET  /api/audio-list/<曲ID>        波形編集用: その曲のフォルダにある mp3 の名前
    GET  /api/audio/<曲ID>?name=...    波形編集用: その mp3(Rangeに対応)
127.0.0.1 だけで待ち受けるので、編集できるのはこのPCを使っている人だけ。
公開用には web/ から editor.html と editor.js を除いたものを置く(保存APIも持たない静的サイトになる)。
"""
import http.server
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import build_songs

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
MAX_BYTES = 512 * 1024

# 公開(deploy.py)の進み具合。同時に1つだけ動かす
PUBLISH = {"running": False, "ok": None, "log": "", "finished": 0}
PUBLISH_LOCK = threading.Lock()


def _run_publish():
    ok, log = False, ""
    try:
        p = subprocess.run([sys.executable, str(HERE / "deploy.py")], cwd=str(HERE.parent), stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=600, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        log = (p.stdout + p.stderr).decode("utf-8", "replace")
        ok = p.returncode == 0
    except Exception as e:  # noqa: BLE001 - 失敗の理由を画面に返す
        log = f"{type(e).__name__}: {e}"
    with PUBLISH_LOCK:
        PUBLISH.update(running=False, ok=ok, log=log[-1500:], finished=time.time())


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass

    def _host_ok(self):
        # 別サイトのページから叩かれないよう、ホスト名を確認する
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _song_id(self, prefix):
        m = re.fullmatch(prefix + r"(.+)", urlparse(self.path).path)
        if not m:
            return None
        sid = unquote(m.group(1))
        return sid if any(e["id"] == sid for e in build_songs.load_manifest()) else None

    def _drive_tracks(self, sid):
        """曲のテキストの {audio: ラベル=Driveのリンク} を、[{label, id}] にする(公開サイトと同じ音源)"""
        path = build_songs.cho_path(sid)
        if not path.exists():
            return []
        out = []
        for m in re.finditer(r"^\{\s*audio\s*:\s*(.+?)\s*\}", path.read_text(encoding="utf-8"), re.M):
            val, label = m.group(1), ""
            lm = re.match(r"^([^/=:?&#\s]{1,24})\s*=\s*(.+)$", val)
            if lm:
                label, val = lm.group(1), lm.group(2)
            im = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?[^#\s]*?id=)([\w-]{10,})", val) or re.fullmatch(r"([\w-]{25,})", val.strip())
            if im:
                out.append({"label": label, "id": im.group(1)})
        return out

    def _drive_open(self, fid, media):
        """Drive API を呼ぶ。公開サイトと同じAPIキーで、サイトのURLから来た形(Referer)にする(キーは、そのURLからの利用だけを許しているため)"""
        cfg = build_songs.load_json(HERE / "config.json", {})
        key, site = cfg.get("driveApiKey", ""), cfg.get("siteUrl", "")
        if not key or not site:
            raise RuntimeError("config.json に driveApiKey と siteUrl が必要です")
        q = "alt=media" if media else "fields=name,size,modifiedTime"
        req = urllib.request.Request(f"https://www.googleapis.com/drive/v3/files/{fid}?{q}&key={key}",
                                     headers={"Referer": site + "/", "Origin": site})
        return urllib.request.urlopen(req, timeout=60)

    def _audio_files(self, sid):
        ent = next((e for e in build_songs.load_manifest() if e["id"] == sid), None)
        if not ent or not ent.get("xlsx"):
            return []
        folder = (build_songs.ROOT / ent["xlsx"]).parent
        return sorted(folder.glob("*.mp3"), key=lambda p: ("2mix" not in p.name.lower(), p.name))

    def _serve_audio(self, path):
        size = path.stat().st_size
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(65536, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"ok": False, "error": "forbidden"})
        route = urlparse(self.path)
        if route.path.startswith("/api/drive-list/") or route.path.startswith("/api/drive-audio/"):
            listing = route.path.startswith("/api/drive-list/")
            sid = self._song_id(r"/api/drive-list/" if listing else r"/api/drive-audio/")
            tracks = self._drive_tracks(sid) if sid else []
            try:
                if listing:
                    for t in tracks:
                        try:
                            meta = json.load(self._drive_open(t["id"], False))
                            t.update(name=meta.get("name", ""), size=int(meta.get("size") or 0), modified=meta.get("modifiedTime", ""))
                        except Exception:  # noqa: BLE001 - 情報が取れなくても、一覧は返す
                            pass
                    return self._json(200, {"ok": True, "tracks": tracks})
                i = int((parse_qs(route.query).get("i") or ["0"])[0])
                if not (0 <= i < len(tracks)):
                    return self._json(404, {"ok": False, "error": "unknown track"})
                # Driveから取るのは、ファイルが更新されたときだけ(このPCに控えを置く)。
                # 何度も取りに行くと、Googleが「自動の大量アクセス」とみなして、しばらくダウンロードを止めるため
                fid = tracks[i]["id"]
                meta = json.load(self._drive_open(fid, False))
                stamp = re.sub(r"[^0-9A-Za-z]", "", meta.get("modifiedTime", "")) or "0"
                cache = HERE / ".cache" / "drive" / f"{fid}-{stamp}.mp3"
                if not cache.exists():
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    for old in cache.parent.glob(f"{fid}-*.mp3"):
                        old.unlink()
                    tmp = cache.with_suffix(".part")
                    up = self._drive_open(fid, True)
                    with open(tmp, "wb") as f:
                        while True:
                            chunk = up.read(65536)
                            if not chunk:
                                break
                            f.write(chunk)
                    tmp.replace(cache)
                return self._serve_audio(cache)
            except (BrokenPipeError, ConnectionResetError):
                return None
            except urllib.error.HTTPError as e:
                msg = "Googleが一時的にダウンロードを止めています(アクセスが多すぎたため。しばらくすると戻ります)" if e.code == 403 else f"Drive: {e.code}"
                return self._json(502, {"ok": False, "error": msg})
            except Exception as e:  # noqa: BLE001
                return self._json(502, {"ok": False, "error": str(e)})
        if route.path.startswith("/api/audio-list/") or route.path.startswith("/api/audio/"):
            listing = route.path.startswith("/api/audio-list/")
            sid = self._song_id(r"/api/audio-list/" if listing else r"/api/audio/")
            files = self._audio_files(sid) if sid else []
            if listing:
                return self._json(200, {"ok": True, "files": [p.name for p in files],
                                        "info": [{"name": p.name, "mtime": int(p.stat().st_mtime), "size": p.stat().st_size} for p in files]})
            name = (parse_qs(route.query).get("name") or [""])[0]
            hit = next((p for p in files if p.name == name), None)
            if hit is None:
                return self._json(404, {"ok": False, "error": "unknown audio"})
            try:
                return self._serve_audio(hit)
            except (BrokenPipeError, ConnectionResetError):
                return None
        if urlparse(self.path).path == "/api/ping":
            return self._json(200, {"ok": True, "editor": True})
        if urlparse(self.path).path == "/api/publish":
            with PUBLISH_LOCK:
                return self._json(200, dict(PUBLISH))
        return super().do_GET()

    def do_PUT(self):
        if not self._host_ok():
            return self._json(403, {"ok": False, "error": "forbidden"})
        sid = self._song_id(r"/api/source/")
        if sid is None:
            return self._json(404, {"ok": False, "error": "unknown song"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BYTES:
            return self._json(400, {"ok": False, "error": "too large"})
        try:
            text = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError:
            return self._json(400, {"ok": False, "error": "utf-8 expected"})
        text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"
        build_songs.SONGS_DIR.mkdir(exist_ok=True)
        path = build_songs.cho_path(sid)
        if path.exists() and path.read_text(encoding="utf-8") != text:
            # 直前の版を1つだけ残す(うっかり上書きしても戻せる)
            path.with_name(path.name + ".prev").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(text, encoding="utf-8")
        entries = build_songs.main()  # songs.js に反映
        entry = next(e for e in entries if e["id"] == sid)
        return self._json(200, {"ok": True, "docDiff": entry["docDiff"]})

    def do_POST(self):
        if not self._host_ok():
            return self._json(403, {"ok": False, "error": "forbidden"})
        if urlparse(self.path).path == "/api/publish":
            # 別サイトのページから勝手に公開されないよう、専用のヘッダーと送信元を確認する
            origin = self.headers.get("Origin")
            if self.headers.get("X-Songbook") != "1" or (origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost")):
                return self._json(403, {"ok": False, "error": "forbidden"})
            with PUBLISH_LOCK:
                if PUBLISH["running"]:
                    return self._json(409, {"ok": False, "error": "already running"})
                PUBLISH.update(running=True, ok=None, log="", finished=0)
            threading.Thread(target=_run_publish, daemon=True).start()
            return self._json(200, {"ok": True})
        sid = self._song_id(r"/api/redraft/")
        if sid is None:
            return self._json(404, {"ok": False, "error": "unknown song"})
        entry = build_songs.redraft(sid)
        return self._json(200, {"ok": True, "source": entry["source"], "docDiff": entry["docDiff"]})


class Server(http.server.ThreadingHTTPServer):
    # Windowsでは reuse を許すと同じポートに複数のサーバーが居座れてしまい、古い方が応答することがある
    allow_reuse_address = False


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    try:
        srv = Server(("127.0.0.1", port), Handler)
    except OSError:
        sys.exit(f"port {port} is already in use. Close the other songbook window first.")
    print(f"viewer: http://127.0.0.1:{port}/")
    print(f"editor: http://127.0.0.1:{port}/editor.html")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
