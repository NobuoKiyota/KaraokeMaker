#!/usr/bin/env python
"""Resources 内のExcelと Resources/Lyrics 内の歌詞docxを曲名の類似度で紐付け、
songs_manifest.json を作る。結果は人が確認できるよう match_score と一緒に書き出す。

使い方:  python songbook/make_manifest.py
"""
import difflib
import json
import re
import unicodedata
from pathlib import Path

import docx
import openpyxl

ROOT = Path(__file__).resolve().parent.parent
RESOURCES = ROOT / "Resources"
LYRICS_DIR = RESOURCES / "Lyrics"
THRESHOLD = 0.6


def norm(s):
    s = unicodedata.normalize("NFKC", s)
    s = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)
    return re.sub(r"[\W_]+", "", s.lower())


def similarity(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return max(0.9, difflib.SequenceMatcher(None, a, b).ratio())
    return difflib.SequenceMatcher(None, a, b).ratio()


def excel_title(path):
    """KaraokeMaker形式(Section見出しあり)のExcelなら曲名を返す。違う形式ならNone。"""
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
    title, is_km = path.stem, False
    for row in ws.iter_rows(min_row=1, max_row=12, max_col=2, values_only=True):
        if not row[0]:
            continue
        key = str(row[0]).strip()
        if key == "Title" and row[1]:
            title = str(row[1]).strip()
        elif key == "Section":
            is_km = True
    return title if is_km else None


def docx_title(path):
    """ファイル名(先頭の番号と末尾の「歌詞」を除く)から曲名を得る。"""
    name = re.sub(r"^[\d\-_]+", "", path.stem)
    name = re.sub(r"歌詞$", "", name)
    return name or path.stem


def main():
    # --- Excel: 同じ曲名が複数あれば _parsed/コピー でないものを優先、次に新しいもの
    groups = {}
    for p in sorted(RESOURCES.rglob("*.xlsx")):
        if p.name.startswith((".~lock", "~$")) or p.name == "karaokemaker_template.xlsx":
            continue
        title = excel_title(p)
        if title is None:
            print(f"スキップ(KaraokeMaker形式ではない): {p.relative_to(ROOT)}")
            continue
        groups.setdefault(norm(title), []).append((title, p))
    excels = []
    for cands in groups.values():
        cands.sort(key=lambda t: ("_parsed" in t[1].stem or "コピー" in t[1].stem, -t[1].stat().st_mtime))
        title, path = cands[0]
        excels.append({"title": title, "path": path, "skipped": [str(c[1].relative_to(ROOT)) for c in cands[1:]]})

    # --- docx
    docs = [{"title": docx_title(p), "path": p} for p in sorted(LYRICS_DIR.glob("*.docx")) if not p.name.startswith("~$")]

    # --- 紐付け(類似度の高い組から確定)
    pairs = sorted(
        ((similarity(e["title"], d["title"]), ei, di) for ei, e in enumerate(excels) for di, d in enumerate(docs)),
        reverse=True,
    )
    used_e, used_d, link = set(), set(), {}
    for score, ei, di in pairs:
        if score < THRESHOLD:
            break
        if ei in used_e or di in used_d:
            continue
        used_e.add(ei)
        used_d.add(di)
        link[ei] = (di, score)

    manifest = []
    for ei, e in enumerate(excels):
        ent = {"id": e["title"], "title": e["title"], "xlsx": str(e["path"].relative_to(ROOT)).replace("\\", "/")}
        if ei in link:
            di, score = link[ei]
            ent["lyrics"] = str(docs[di]["path"].relative_to(ROOT)).replace("\\", "/")
            ent["match_score"] = round(score, 2)
        if e["skipped"]:
            ent["ignored_duplicates"] = e["skipped"]
        manifest.append(ent)
    for di, d in enumerate(docs):
        if di not in used_d:
            manifest.append({"id": d["title"], "title": d["title"],
                             "lyrics": str(d["path"].relative_to(ROOT)).replace("\\", "/")})

    manifest.sort(key=lambda m: (m.get("lyrics") or m["xlsx"]))
    out = Path(__file__).with_name("songs_manifest.json")
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(manifest)}曲 -> {out}")


if __name__ == "__main__":
    main()
