#!/usr/bin/env python
"""公開用の dist を Cloudflare(Workers の静的アセット)にアップロードする。

  python songbook/deploy.py              dist を作り直して(publish.py と同じ)、アップロードする
  python songbook/deploy.py --no-build   今ある dist をそのままアップロードする
  python songbook/deploy.py --dry-run    アップロードはせず、手順だけ確認する

必要なもの:
  ・プロジェクト直下の cf_token.txt … CloudflareのAPIトークン(1行目)。Gitには入れない。画面にも出さない。
  ・songbook/config.json の "cloudflare": {"accountId": "...", "workerName": "..."}
同じ Worker 名で上げるので、公開のアドレスは変わらない(上書き更新)。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import build_songs
import publish

HERE = Path(__file__).resolve().parent
TOKEN_FILE = HERE.parent / "cf_token.txt"
COMPAT_DATE = "2025-10-01"


def read_token() -> str:
    raw = TOKEN_FILE.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp932")
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def main():
    cfg = build_songs.load_json(HERE / "config.json", {}).get("cloudflare", {})
    account, worker = cfg.get("accountId", ""), cfg.get("workerName", "")
    if not account or not worker:
        sys.exit('songbook/config.json に "cloudflare": {"accountId": "...", "workerName": "..."} を書いてください。')
    if not TOKEN_FILE.exists() or not read_token():
        sys.exit(f"{TOKEN_FILE} がありません(CloudflareのAPIトークンを、1行で書いたファイルです)。")
    npx = shutil.which("npx")
    if not npx:
        sys.exit("npx が見つかりません(Node.js を入れてください)。")

    if "--no-build" not in sys.argv:
        publish.main()  # パスワード(pass.txt)で暗号化した dist を作り直す
    if not (publish.DIST / "index.html").exists():
        sys.exit("dist がありません。--no-build を付けずに実行してください。")

    # 音源の「範囲指定の読み込み」に答えるため、小さな Worker(worker.js)を、/audio/ にだけ通す
    toml = [
        f'name = "{worker}"',
        'main = "worker.js"',
        f'compatibility_date = "{COMPAT_DATE}"',
        "",
        "[assets]",
        'directory = "dist"',
        'binding = "ASSETS"',
        'run_worker_first = ["/audio/*"]',
    ]
    (HERE / "wrangler.toml").write_text("\n".join(toml) + "\n", encoding="utf-8")
    cmd = [npx, "--yes", "wrangler@4", "deploy", "--config", str(HERE / "wrangler.toml")]
    if "--dry-run" in sys.argv:
        cmd.append("--dry-run")
    env = dict(os.environ, CLOUDFLARE_API_TOKEN=read_token(), CLOUDFLARE_ACCOUNT_ID=account)
    print(f"Worker「{worker}」へアップロードします…" + ("(確認だけ)" if "--dry-run" in sys.argv else ""))
    return subprocess.call(cmd, env=env, cwd=str(HERE))


if __name__ == "__main__":
    sys.exit(main())
