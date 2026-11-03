#!/usr/bin/env python
"""音源(2mix の mp3)を聴いて、Excelの小節の頭と、実際のビートのずれ(秒)を測り、曲のテキストに {offset: 0.055} と書く。

  python songbook/detect_offsets.py            全曲を測って書く
  python songbook/detect_offsets.py 曲名 ...   指定した曲だけ
  python songbook/detect_offsets.py --dry-run  書かずに、結果だけ見る

区間ループやジャンプは、Excelから出した小節の頭の時刻に、このずれを足した位置を使う。
測り方: 音の立ち上がり(オンセット)を、テンポどおりの拍の並びに重ねて、一番よく合う位置を探す(5ミリ秒きざみ)。
必要なもの: librosa(pip install librosa)と、mp3を読める環境(ffmpeg など)。
"""
import re
import sys
import warnings
from pathlib import Path

import numpy as np

import build_songs

warnings.filterwarnings("ignore")


def measure(mp3, bpm):
    import librosa

    y, sr = librosa.load(str(mp3), sr=22050, mono=True)
    hop = 128
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, aggregate=np.median)
    tf = hop / sr
    beat = 60.0 / bpm
    best, best_ph = -1.0, 0.0
    for ph in np.arange(0, beat, 0.005):
        idx = np.round(np.arange(ph, len(env) * tf, beat) / tf).astype(int)
        idx = idx[idx < len(env)]
        score = env[idx].mean()
        if score > best:
            best, best_ph = score, ph
    return best_ph if best_ph <= beat / 2 else best_ph - beat


def write_offset(path, value):
    text = path.read_text(encoding="utf-8")
    line = "{offset: %g}" % round(value, 3)
    if re.search(r"^\{\s*offset\s*:", text, re.M):
        new = re.sub(r"^\{\s*offset\s*:[^}]*\}", line, text, count=1, flags=re.M)
    else:
        lines = text.split("\n")
        at = 0
        for i, ln in enumerate(lines):  # {audio} / {tempo} / {key} / {title} のあとに入れる
            if re.match(r"^\{\s*(title|key|tempo|audio)\s*:", ln):
                at = i + 1
        lines.insert(at, line)
        new = "\n".join(lines)
    if new != text:
        path.with_name(path.name + ".prev").write_text(text, encoding="utf-8")
        path.write_text(new, encoding="utf-8")
    return new != text


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    for ent in build_songs.load_manifest():
        if args and ent["id"] not in args:
            continue
        if not ent.get("xlsx"):
            continue
        xlsx = build_songs.ROOT / ent["xlsx"]
        meta, _ = build_songs.read_excel(xlsx)
        try:
            bpm = float(meta["tempo"])
        except (KeyError, TypeError, ValueError):
            continue
        mp3s = [p for p in xlsx.parent.glob("*.mp3") if "2mix" in p.name.lower()]
        path = build_songs.cho_path(ent["id"])
        if not mp3s or not path.exists():
            print(f"{ent['id']}: 2mix の mp3 か曲のテキストが見つかりません")
            continue
        off = round(measure(mp3s[0], bpm) * 200) / 200  # 5ミリ秒きざみ
        done = "" if dry else (" (書きました)" if write_offset(path, off) else " (変更なし)")
        print(f"{ent['id']}: offset {off * 1000:+.0f} ms{done}")
    if not dry:
        build_songs.main()


if __name__ == "__main__":
    main()
