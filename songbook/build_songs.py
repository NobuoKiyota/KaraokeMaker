#!/usr/bin/env python
"""KaraokeMaker用Excel(コード+歌詞断片)と歌詞docxを照合し、songbook用のデータを生成する。

使い方:  python songbook/build_songs.py [songs_manifest.json]

曲ごとのテキストファイル songs/<曲>.cho が元データ。無い曲だけ、下記の処理で下書きを作る。
できあがった .cho は上書きしない(歌詞やコード位置を人が直すため)。

Excelの歌詞は歌うタイミング単位に細切れ(例: 「閉ざす」「心を閉」「ざす」)で、
表記もdocxと微妙に違う(吸いこんで/吸い込んで)。そこで両方の文字列を正規化して
文字単位でアラインメント(Needleman-Wunsch)し、Excelのコード位置を
整った歌詞(docx)の文字位置へ写し替える。歌詞の本文は常にdocxが正。
docxが無い曲はExcelの歌詞をそのまま使う。
"""
import copy
import json
import math
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import docx
import openpyxl

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = Path(__file__).resolve().parent / "web"

DASHES = set("－―—‐−-ｰ")
NO_CHORD = {"", "none", "null"}


# ---------------------------------------------------------------- 読み込み
def read_excel(path):
    ws = openpyxl.load_workbook(path, data_only=True).active
    meta, header_row = {}, None
    for r in range(1, ws.max_row + 1):
        a = ws.cell(r, 1).value
        if a is None:
            continue
        a = str(a).strip()
        if a == "Section":
            header_row = r
            break
        if a in ("Title", "Tempo", "Key"):
            meta[a.lower()] = ws.cell(r, 2).value
    if header_row is None:
        raise ValueError(f"{path}: 'Section' 見出し行が見つかりません")

    rows, last_group = [], 1
    for r in range(header_row + 1, ws.max_row + 1):
        v = [c.value for c in ws[r][:6]]
        if v[0] is not None and str(v[0]).strip() == "End":
            break
        if v[2] is None:
            continue
        try:
            group = int(v[1]) if v[1] is not None else last_group
        except ValueError:
            group = last_group
        last_group = group
        chord = str(v[4]).strip() if v[4] is not None else ""
        frag = str(v[5]).strip() if v[5] is not None else ""
        if chord == "None":  # 大文字の None は「コードなし(N.C)」、小文字の none は空欄の目印
            chord = "N.C"
        rows.append({
            "section": str(v[0]).strip() if v[0] is not None else "",
            "group": group,
            "bar": int(v[2]),
            "beat": float(v[3]) if v[3] is not None else 1.0,
            "chord": "" if chord.lower() in NO_CHORD else chord,
            "frag": "" if frag.lower() in NO_CHORD else frag,
        })
    return meta, rows


HEADER_RE = re.compile(r"[\[［【](.+?)[\]］】](?:\s*[（(].*)?")
BARE_HEADER_RE = re.compile(
    r"(?:[A-ZＡ-Ｚ]\d{0,2}|[A-D]メロ\d{0,2}|サビ\d{0,2}|ラスサビ|ブリッジ|イントロ|アウトロ|間奏|Intro|Outro|Bridge|Chorus|Verse\d?)")
FULL_LYRICS_RE = re.compile(r"フル歌詞")


def read_lyrics(path):
    """docx -> (title, [{'section':str,'text':str,'sec_id':int}])

    書式は曲ごとにまちまちなので、次の揺れを吸収する:
      ・見出しは [A] 【Aメロ01】 または単独の A / サビ
      ・「曲名：『…』」の行と、その直後の解説ブロック
      ・メモ行(※…、(…)だけの行)
      ・【フル歌詞】があれば、それより前(エピソード等の解説)は読み飛ばす
    """
    paras = [ln.strip() for p in docx.Document(path).paragraphs for ln in p.text.split("\n")]
    start = 0
    for n, t in enumerate(paras):
        m = HEADER_RE.fullmatch(t)
        if m and FULL_LYRICS_RE.search(m.group(1)):
            start = n + 1
            break
    paras = paras[start:]

    title, section, sec_id, lines = None, "", 0, []
    in_note_block = bool(paras) and paras[0].startswith("曲名") and start == 0
    for t in paras:
        if in_note_block:  # 「曲名：」の直後〜最初の空行は曲の解説とみなす
            if t.startswith("曲名"):
                title = t.split("：", 1)[-1].strip("『』「」 ")
            elif not t:
                in_note_block = False
            continue
        if not t:
            continue
        m = HEADER_RE.fullmatch(t)
        if m or BARE_HEADER_RE.fullmatch(t):
            section, sec_id = (m.group(1) if m else t).strip(), sec_id + 1
            continue
        if t.startswith("※") or re.fullmatch(r"[（(].*[）)]", t):
            continue
        if title is None and not lines and re.fullmatch(r"[『「].*[』」]", t):
            title = t.strip("『』「」")
            continue
        lines.append({"section": section, "text": t, "sec_id": sec_id})
    return title, lines


def lyrics_from_excel(rows):
    """docxが無い曲: Excelの歌詞をGroup単位でつなげて歌詞行にする"""
    lines, prev_sec, sec_id = [], None, 0
    by_group = defaultdict(list)
    order = []
    for r in rows:
        key = (r["section"], r["group"])
        if key not in by_group:
            order.append(key)
        by_group[key].append(r)
    for key in order:
        text = "".join(r["frag"] for r in by_group[key])
        if not text:
            continue
        sec = key[0]
        if sec != prev_sec:
            sec_id += 1
        lines.append({"section": sec, "text": text, "sec_id": sec_id})
        prev_sec = sec
    return lines


# ---------------------------------------------------------------- 正規化・照合
def norm_text(s):
    """[(正規化した1文字, 元の文字位置)]。空白・記号は除外、カナはひらがなへ。"""
    out = []
    for i, ch in enumerate(s):
        if ch in DASHES:
            out.append(("ー", i))
            continue
        for c in unicodedata.normalize("NFKC", ch):
            if c.isspace() or unicodedata.category(c)[0] in "PSZC":
                continue
            if "ァ" <= c <= "ヶ":
                c = chr(ord(c) - 0x60)
            out.append((c.lower(), i))
    return out


def _is_kana(c):
    return "ぁ" <= c <= "ゖ" or c == "ー"


def _sub_cost(x, y):
    """置換コスト。かな<->漢字(吸いこんで/吸い込んで 等の表記ゆれ)だけ安く、それ以外は高くして
    無関係な文字同士が偶然対応づくのを防ぐ。"""
    if x == y:
        return 0.0
    return 1.2 if _is_kana(x) != _is_kana(y) else 2.5


def align(a, b, gap=1.0):
    """文字列a, bの大域アラインメント。対応した添字ペア[(i,j)]を返す。"""
    n, m = len(a), len(b)
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i * gap
    for j in range(1, m + 1):
        dp[0][j] = j * gap
    for i in range(1, n + 1):
        ai, row, prev = a[i - 1], dp[i], dp[i - 1]
        for j in range(1, m + 1):
            diag = prev[j - 1] + _sub_cost(ai, b[j - 1])
            row[j] = min(diag, prev[j] + gap, row[j - 1] + gap)
    pairs, i, j = [], n, m
    while i > 0 and j > 0:
        if abs(dp[i][j] - (dp[i - 1][j - 1] + _sub_cost(a[i - 1], b[j - 1]))) < 1e-9:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif abs(dp[i][j] - (dp[i - 1][j] + gap)) < 1e-9:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def drop_isolated_mismatches(pairs, a, b):
    """表記ゆれ(不一致ペア)は、隣の文字が完全一致している場合だけ採用する。
    周りも噛み合っていない不一致は偶然の対応とみなして捨てる。"""
    exact = {(i, j) for i, j in pairs if a[i] == b[j]}
    keep = []
    for i, j in pairs:
        if (i, j) in exact or (i - 1, j - 1) in exact or (i + 1, j + 1) in exact:
            keep.append((i, j))
    return keep


# ---------------------------------------------------------------- 組み立て
def squeeze_gap(gap, prev_ch, next_ch):
    """文字の間にある空白・記号を整える。空白は捨て、英単語どうしの間だけ半角空白を1つ残す。"""
    keep = "".join(ch for ch in gap if not ch.isspace())
    latin = lambda ch: bool(ch) and ch.isascii() and ch.isalnum()
    if any(ch.isspace() for ch in gap) and latin(prev_ch) and latin(next_ch):
        return " " + keep
    return keep



def make_segs(text, chords):
    """歌詞テキストとコード位置[(offset, chord)]から [{'c':コード,'t':歌詞}] を作る"""
    chords = sorted(chords, key=lambda x: x[0])
    if not chords:
        return [{"c": "", "t": text}]
    segs = []
    if chords[0][0] > 0:
        segs.append({"c": "", "t": text[:chords[0][0]]})
    for idx, (off, ch) in enumerate(chords):
        nxt = chords[idx + 1][0] if idx + 1 < len(chords) else len(text)
        segs.append({"c": ch, "t": text[off:nxt]})
    return segs


def build_lyrics_only(sid, title, dx_lines):
    """Excelが無い曲: 歌詞docだけの行(コードなし)を出す。小節が無いので text を持つセクションにする"""
    sections, last = [], None
    for dl in dx_lines:
        if dl["sec_id"] != last:
            sections.append({"label": dl["section"], "text": []})
            last = dl["sec_id"]
        sections[-1]["text"].append(dl["text"])
    return {"id": sid, "title": title, "key": "", "tempo": None, "hasLyricsDoc": True,
            "hasChords": False, "sections": sections, "layout": {}, "align": None}


def normalize_key(k):
    if not k:
        return ""
    m = re.match(r"^([A-Ga-g][#b♯♭]?)(.*)$", str(k).strip())
    if not m:
        return str(k)
    root, rest = m.group(1)[0].upper() + m.group(1)[1:], m.group(2).strip().lower()
    if rest in ("m", "min", "minor", "miner"):
        return root + "m"
    if rest in ("", "maj", "major"):
        return root
    return str(k)


def build_song(sid, meta, rows, title_doc, dx_lines, has_docx):
    ex_chars, ex_row, ex_orig = [], [], []
    for ri, r in enumerate(rows):
        for c, oi in norm_text(r["frag"]):
            ex_chars.append(c)
            ex_row.append(ri)
            ex_orig.append(oi)
    dx_chars, dx_line, dx_off = [], [], []
    for k, line in enumerate(dx_lines):
        for c, off in norm_text(line["text"]):
            dx_chars.append(c)
            dx_line.append(k)
            dx_off.append(off)

    pairs = drop_isolated_mismatches(align(ex_chars, dx_chars), ex_chars, dx_chars)
    matched_ex = {i for i, _ in pairs}
    matched_dx = {j for _, j in pairs}
    dx_of = dict(pairs)

    # 歌詞docの行ごとに「Excelとほぼ全文一致し、位置もまとまっているか」を見て、信頼できる行だけ使う
    line_total = defaultdict(int)
    for k in dx_line:
        line_total[k] += 1
    by_line = defaultdict(list)
    for i, j in pairs:
        by_line[dx_line[j]].append(i)
    trusted = {k for k, idx in by_line.items()
               if len(idx) / line_total[k] >= 0.9 and len(idx) / (max(idx) - min(idx) + 1) >= 0.8}
    dx_of = {i: j for i, j in pairs if dx_line[j] in trusted}

    row_chars = defaultdict(dict)  # 行 -> {frag内の文字位置: 正規化ストリームの添字}
    for i in range(len(ex_chars)):
        row_chars[ex_row[i]].setdefault(ex_orig[i], i)
    # 行ごとの描き方: doc主導(歌詞docの表記・改行・セクション名を借りる) / Excel主導(Excelの文字をそのまま出す)
    row_ext, doc_driven = {}, {}
    for ri, ch in row_chars.items():
        idx = list(ch.values())
        words = [i for i in idx if ex_chars[i] != "ー"]
        aligned_words = sum(i in dx_of for i in words)
        any_aligned = any(i in dx_of for i in idx)
        row_ext[ri] = not any_aligned and not words
        doc_driven[ri] = any_aligned and len(words) - aligned_words <= 1

    # --- 小節単位に組み立てる。Excelの並び・コード・小節番号が正。歌詞docは表記と区切りを貸す補足。
    bars, bar_by_n = [], {}
    last_o = {}                   # docx行 -> 出力済みの最後の文字位置
    last_k, last_cell = None, None  # 直前に出した歌詞docの行・セル

    def pos_of(r):
        return r["bar"] + (r["beat"] - 1) / 4  # 小節内の位置(4/4拍子前提)

    def get_bar(r):
        n = r["bar"]
        if n not in bar_by_n:
            bar = {"n": n, "esec": r["section"], "cells": [], "dl": defaultdict(int), "ext": False}
            bar_by_n[n] = bar
            bars.append(bar)
        return bar_by_n[n]

    for ri, r in enumerate(rows):
        if r["section"].lower() == "count":  # カウント行は楽譜に載せない
            continue
        bar = get_bar(r)
        cells = bar["cells"]
        frag, chars = r["frag"], row_chars.get(ri, {})
        if not chars or row_ext[ri]:  # 歌詞なし・伸ばし棒だけ: コードがあれば歌詞なしのセルにする
            if chars:
                bar["ext"] = True  # 歌い伸ばし(ー)の小節
            if r["chord"]:
                cells.append({"c": r["chord"], "t": "", "pos": pos_of(r)})
            continue
        if not doc_driven[ri]:  # docに対応が無いExcel上の歌詞: Excelの文字をそのまま出して要確認にする
            if r["chord"] or not cells:
                cells.append({"c": r["chord"], "t": frag, "x": 1, "pos": pos_of(r), "ks": [[0, -r["group"], pos_of(r)]]})
            else:
                cells[-1]["t"] += frag
                cells[-1]["x"] = 1
                if not cells[-1].get("ks"):
                    cells[-1]["ks"] = [[0, -r["group"], pos_of(r)]]
            last_k = None
            continue
        pending_chord = r["chord"]
        for oi in range(len(frag)):
            i = chars.get(oi)
            j = dx_of.get(i)
            if j is None:  # doc主導の行で対応づかなかった文字(表記ゆれ)は捨てる
                continue
            k, o = dx_line[j], dx_off[j]
            text = dx_lines[k]["text"]
            last = last_o.get(k, -1)
            if o <= last:
                continue
            pre = text[last + 1:o]
            if pre and norm_text(pre):
                pre = ""
            prev_ch = last_cell["t"][-1:] if last_cell is not None else ""
            pre = squeeze_gap(pre, prev_ch, text[o])  # 歌詞docの空白は和文の間では捨てる(小節線が区切りになる)
            if pre and last_k == k and last_cell is not None:  # 文字間の記号は直前の文字の後ろへ
                last_cell["t"] += pre
                pre = ""
            piece = pre + text[o]
            if pending_chord or not cells:
                cell = {"c": pending_chord, "t": piece, "pos": pos_of(r), "ks": [[0, k, pos_of(r)]]}
                cells.append(cell)
                pending_chord = ""
            else:
                cell = cells[-1]
                if last_k is not None and last_k != k:  # 歌詞docで行が変わった所(英語の行どうしだけ空白を入れる)
                    piece = squeeze_gap(" ", cell["t"][-1:], piece[:1]) + piece
                if not cell.get("ks") or cell["ks"][-1][1] != k:  # 1つのセルの中で歌詞docの行が変わる位置を覚えておく
                    cell.setdefault("ks", []).append([len(cell["t"]) + len(piece) - 1, k, pos_of(r)])
                cell["t"] += piece
            rest = text[o + 1:]
            if rest and not norm_text(rest):  # 歌詞docの行末の記号(！など)
                cell["t"] += "".join(ch for ch in rest if not ch.isspace())
                o = len(text) - 1
            last_o[k] = o
            last_k, last_cell = k, cell
            bar["dl"][(dx_lines[k]["sec_id"], dx_lines[k]["section"])] += 1
        if pending_chord:  # 文字が1つも出なかった行のコード
            cells.append({"c": pending_chord, "t": "", "pos": pos_of(r)})

    # 抜けている小節番号は空の小節で埋める
    filled = []
    for b in bars:
        while filled and b["n"] > filled[-1]["n"] + 1:
            filled.append({"n": filled[-1]["n"] + 1, "esec": filled[-1]["esec"], "cells": [], "dl": {}, "ext": False})
        filled.append(b)
    bars = filled
    for b in bars:
        b["lyric"] = any(c["t"].strip() for c in b["cells"])

    # --- セクション分け。歌詞のある小節は歌詞docの見出し(A・サビ…)でまとめ、歌詞のない小節はExcelのセクション名
    #     (Intro・Gsolo…)でまとめる。歌詞docの塊が楽譜上のセクションになる。
    n_bars = len(bars)
    # 歌い伸ばし(ー)の小節は、歌詞のあとに同じExcelセクションが続く間だけ「伸ばし」とみなす
    for i, b in enumerate(bars):
        b["ext"] = b["ext"] and i > 0 and bars[i - 1]["esec"] == b["esec"]
    # 歌詞のはみ出し: ほとんどが間奏のExcelセクションの先頭にだけ歌詞が残っている小節(例: Intro2の最初の1小節)
    a0 = 0
    for i in range(1, n_bars + 1):
        if i == n_bars or bars[i]["esec"] != bars[a0]["esec"]:
            lyric_n = sum(bars[t]["lyric"] for t in range(a0, i))
            for t in range(a0, i):
                bars[t]["spill"] = bars[t]["lyric"] and i - a0 >= 3 and lyric_n <= 2 and lyric_n * 2 < i - a0
            a0 = i

    def plain(name):
        return re.sub(r"[\d\s\"”'’]+", "", name or "").lower()

    keys, labels, rest_bars = [None] * n_bars, {}, set()  # rest_bars: 歌詞行の中の休み小節
    for i, b in enumerate(bars):
        if not b["lyric"]:
            continue
        dsec = max(b["dl"].items(), key=lambda kv: kv[1])[0] if b["dl"] else None
        if dsec:
            keys[i] = ("d", dsec[0])
            labels[keys[i]] = dsec[1] or b["esec"]
        elif i > 0 and bars[i - 1]["lyric"]:
            keys[i] = keys[i - 1]  # docに無い歌詞(Excelのみ)は直前の歌詞セクションの続きとして扱う
        else:
            keys[i] = ("e", b["esec"], i)
            labels[keys[i]] = b["esec"]
    i = 0
    while i < n_bars:
        if bars[i]["lyric"]:
            i += 1
            continue
        j = i
        while j < n_bars and not bars[j]["lyric"]:
            j += 1
        prev_key = keys[i - 1] if i > 0 else None
        next_key = keys[j] if j < n_bars else None
        if prev_key is not None and prev_key == next_key and j - i <= 2:
            for t in range(i, j):  # 歌詞の途中の休み小節
                keys[t] = prev_key
                rest_bars.add(t)
        else:
            # 歌詞のない小節は、Excelのセクション名ごとのまとまりにする。
            # 歌い伸ばしだけの短いまとまり(5小節以下)は、続く間奏にまとめる(例: ソロ前の伸ばし -> Gsolo)
            segs = []
            for t in range(i, j):
                if segs and bars[segs[-1][0]]["esec"] == bars[t]["esec"]:
                    segs[-1].append(t)
                else:
                    segs.append([t])
            for si, seg in enumerate(segs):
                # 歌詞セクションと同じ名前で短い(4小節以下)後奏は、そのセクションの最後の行にする
                if si == 0 and prev_key is not None and len(seg) <= 4 and plain(bars[seg[0]]["esec"]) == plain(labels[prev_key]):
                    for t in seg:
                        keys[t] = prev_key
                    continue
                if si + 1 < len(segs) and len(seg) <= 5 and all(bars[t]["ext"] for t in seg):
                    segs[si + 1] = seg + segs[si + 1]
                    continue
                name = bars[seg[-1]]["esec"]
                for t in seg:
                    keys[t] = ("i", name, seg[0])
                labels[("i", name, seg[0])] = name
        i = j

    out_sections, prev = [], None
    for t, (b, key) in enumerate(zip(bars, keys)):
        if key != prev:
            out_sections.append({"label": labels[key], "bars": []})
            prev = key
        kind = "l" if b["lyric"] or t in rest_bars else "i"
        out_sections[-1]["bars"].append({"n": b["n"], "k": kind, "cells": b["cells"],
                                         "ext": b["ext"], "spill": b.get("spill", False)})

    def runs(idx_list, chars, label_of):
        out, cur = [], []
        for i in idx_list:
            if cur and i != cur[-1] + 1:
                out.append(cur)
                cur = []
            cur.append(i)
        if cur:
            out.append(cur)
        return [{"text": "".join(chars[i] for i in run), "at": label_of(run[0])} for run in out]

    ex_only = runs([i for i in range(len(ex_chars)) if i not in matched_ex], ex_chars,
                   lambda i: f"{rows[ex_row[i]]['bar']}小節{rows[ex_row[i]]['beat']:g}拍")
    dx_only = runs([j for j in range(len(dx_chars)) if j not in matched_dx], dx_chars,
                   lambda j: dx_lines[dx_line[j]]["text"])
    diffs = [(ex_chars[i], dx_chars[j], dx_lines[dx_line[j]]["text"])
             for i, j in pairs if ex_chars[i] != dx_chars[j]]

    song = {
        "id": sid,
        "title": str(meta.get("title") or title_doc or sid),
        "key": normalize_key(meta.get("key")),
        "tempo": meta.get("tempo"),
        "hasLyricsDoc": has_docx,
        "hasChords": True,
        "sections": out_sections,
        "layout": {},
        "align": {
            "excelChars": len(ex_chars), "docxChars": len(dx_chars),
            "matched": len(pairs), "exact": len(pairs) - len(diffs),
            "excelOnly": ex_only, "docxOnly": dx_only,
            "differs": [{"excel": a, "docx": b, "line": ln} for a, b, ln in diffs],
        },
    }
    return song


def report(song):
    a = song["align"]
    out = [f"■ {song['title']}  key={song['key']} tempo={song['tempo']}  歌詞doc={'あり' if song['hasLyricsDoc'] else 'なし'}"]
    out.append(f"  Excel歌詞 {a['excelChars']}字 / docx歌詞 {a['docxChars']}字 / 対応 {a['matched']}字(完全一致 {a['exact']})")
    out.append(f"  Excelのみ(docxに無い) {len(a['excelOnly'])}箇所:")
    out += [f"    『{x['text']}』 @{x['at']}" for x in a["excelOnly"]]
    out.append(f"  docxのみ(Excelに無い) {len(a['docxOnly'])}箇所:")
    out += [f"    『{x['text']}』 in 「{x['at']}」" for x in a["docxOnly"]]
    out.append(f"  表記違いで対応づけた文字 {len(a['differs'])}件:")
    out += [f"    Excel『{x['excel']}』 = docx『{x['docx']}』 in 「{x['line']}」" for x in a["differs"]]
    flagged = [b["n"] for sec in song["sections"] for b in sec["bars"] if any(c.get("x") for c in b["cells"])]
    out.append(f"  要確認の小節(歌詞docと不一致のExcel歌詞を含む) {len(flagged)}小節: {flagged}")
    return "\n".join(out)


def safe_name(sid):
    return re.sub(r'[\\/:*?"<>|]', "_", sid)


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


HERE = Path(__file__).resolve().parent
SONGS_DIR = HERE / "songs"


# ---------------------------------------------------------------- 曲ファイル(.cho)
# 曲ごとのテキストファイルが元データ。書式(ChordProに近い):
#   {title: 曲名} {key: Bm} {tempo: 190}      曲の情報
#   # A                                        セクション見出し
#   [Bm]届かない響かない耳を[G]閉ざす          歌詞はそのまま書き、コードは乗せたい文字の直前に [コード]
#   | Bm D | G F#7 | %% | / |                  コードだけの行は小節を | で区切る(/=前のコードを続ける %=前の1小節と同じ %%=前の2小節と同じ)
# xlsx/歌詞docから作るのは最初の下書きだけ。できあがった .cho は上書きしない。
def inst_chunk_size(n):
    """歌詞のない行の1行あたり小節数: 基本8、6の倍数なら6"""
    if n % 8 == 0:
        return 8
    if n % 6 == 0:
        return 6
    return 8


def chord_bar_text(bar):
    chords = [c["c"] for c in bar["cells"] if c["c"]]
    return " ".join(chords) if chords else "/"


def compress_bars(texts):
    """コードだけの行を、繰り返し記号で短くする。直前の4小節と同じなら %%%%、2小節なら %%、
    1小節(コードの入った小節)なら %。比べるのは元のコードで、行の中だけで数える。
    %%%% は4小節、%% は2小節の区切りの位置でだけ使う。"""
    n, out, i = len(texts), [], 0
    while i < n:
        if i >= 4 and i % 4 == 0 and n - i >= 4 and texts[i:i + 4] == texts[i - 4:i]:
            out.append("%%%%")
            i += 4
        elif i >= 2 and i % 2 == 0 and n - i >= 2 and texts[i:i + 2] == texts[i - 2:i]:
            out.append("%%")
            i += 2
        elif i >= 1 and texts[i] == texts[i - 1] and texts[i] != "/":
            out.append("%")
            i += 1
        else:
            out.append(texts[i])
            i += 1
    return out


def inst_lines(bars):
    """歌詞のない小節の並びを、コードだけの行(| 区切り)にする"""
    lead = 0
    while lead < len(bars) and bars[lead].get("ext"):  # 先頭の歌い伸ばし(ー)の小節
        lead += 1
    chunks = []
    if lead >= 4 and len(bars) > lead:  # 伸ばしが4小節以上あれば、独立した行にする
        chunks.append(bars[:lead])
        bars = bars[lead:]
    size = inst_chunk_size(len(bars))
    chunks += [bars[i:i + size] for i in range(0, len(bars), size)]
    if len(chunks) >= 2 and len(chunks[-1]) <= 2 and not (lead >= 4 and len(chunks) == 2):  # 1〜2小節だけ余る行は前の行に含める
        last = chunks.pop()
        chunks[-1] = chunks[-1] + last
    return ["| " + " | ".join(compress_bars([chord_bar_text(b) for b in chunk])) + " |" for chunk in chunks]


def lyric_tokens(bars):
    """小節の並びを、歌詞doc(または歌詞の塊)の行ごとに区切った [コード, 歌詞, 行, 位置] の列にする"""
    toks, prev_k = [], None
    for b in bars:
        bar_chord = ""
        for c in b["cells"]:
            pos = c.get("pos", b["n"])
            chord = c["c"]
            if chord and chord == bar_chord:  # 同じ小節の中で同じコードが続くときは、2回目を省く
                chord = ""
            bar_chord = c["c"] or bar_chord
            if not c["t"]:  # コードだけ: 直前の歌詞の続きとして扱う
                if chord:
                    toks.append([chord, "", prev_k, pos])
                continue
            ks = c.get("ks") or [[0, None, pos]]
            for idx, (off, k, kpos) in enumerate(ks):
                end = ks[idx + 1][0] if idx + 1 < len(ks) else len(c["t"])
                toks.append([chord if idx == 0 else "", c["t"][off:end], k, kpos])
                prev_k = k
    return toks


def lyric_lines(bars, merge_tail=True):
    """歌詞のある小節の並びを、歌詞の行にする。
    歌詞docの1行(フレーズ)は途中で切らず、フレーズをまとめて約4小節の行にする。
    最後に残った短い行は、前の行にくっつける(歌詞docがある曲だけ。docが無い曲は、Excelの表示行がそのまま1行)。"""
    toks = lyric_tokens(bars)
    phrases = []
    for t in toks:
        if not phrases or (t[2] is not None and t[2] != phrases[-1]["k"] and t[3] is not None and t[1]):
            phrases.append({"k": t[2], "toks": [], "start": t[3], "end": t[3]})
        phrases[-1]["toks"].append(t)
        if t[1]:
            phrases[-1]["end"] = t[3]
    lines, cur = [], []
    for i, ph in enumerate(phrases):
        cur.append(ph)
        nxt = phrases[i + 1]["start"] if i + 1 < len(phrases) else ph["end"] + 0.5
        if nxt - cur[0]["start"] >= 3.5:
            lines.append(cur)
            cur = []
    if cur:
        length = cur[-1]["end"] + 0.5 - cur[0]["start"]
        if merge_tail and lines and (len(cur) == 1 or length < 3):  # 短い残りは前の行にくっつける
            lines[-1] += cur
        else:
            lines.append(cur)
    def join(line):
        out = ""
        for ph in line:
            seg = "".join((f"[{t[0]}]" if t[0] else "") + t[1] for t in ph["toks"])
            head = next((t[1][:1] for t in ph["toks"] if t[1]), "")
            plain_out = TAG_RE.sub("", out)
            if plain_out and head and plain_out[-1].isascii() and plain_out[-1].isalnum() and head.isascii() and head.isalnum():
                out += " "  # 英語の行どうしは単語が続かないように空白を入れる
            out += seg
        return out

    return [join(line) for line in lines]


def prepare_sections(song):
    """下書きの前処理: 間奏の直前の「歌詞のはみ出し」小節のコードを間奏側に移し、同じ見出しが続くときは 1, 2 を付ける"""
    sections = copy.deepcopy(song["sections"])
    for si in range(len(sections) - 1):
        bars, nxt = sections[si].get("bars"), sections[si + 1].get("bars")
        if bars and nxt and bars[-1].get("spill") and nxt[0]["k"] == "i":
            last = bars[-1]
            chords = [c["c"] for c in last["cells"] if c["c"]]
            if chords:
                nxt.insert(0, {"n": last["n"], "k": "i", "ext": True,
                               "cells": [{"c": ch, "t": ""} for ch in chords]})
                last["cells"] = [c for c in last["cells"] if c["t"]]
                for c in last["cells"]:
                    c["c"] = ""

    # 同じ見出しが続くとき(歌詞のあとの間奏など)は 1, 2 を付けて区別する
    i = 0
    while i < len(sections):
        j = i
        while j < len(sections) and sections[j].get("label") and sections[j]["label"] == sections[i].get("label"):
            j += 1
        if j - i >= 2:
            for n, sec in enumerate(sections[i:j], 1):
                sec["label"] = f"{sec['label']}{n}"
        i = max(j, i + 1)
    return sections


def fmt_time(sec):
    sec = max(0, int(round(sec)))
    return f"{sec // 60}:{sec % 60:02d}"


def fmt_time2(sec):
    """小数2桁までの時刻(0:59.04)。区間ループなど、秒単位より細かい位置が要るところに使う。端数が無ければ 0:59"""
    sec = max(0.0, float(sec))
    whole = int(sec)
    frac = round(sec - whole, 2)
    if frac >= 1:
        whole, frac = whole + 1, 0.0
    s = f"{whole // 60}:{whole % 60:02d}"
    return s + (f"{frac:.2f}".rstrip("0")[1:] if frac else "")


def section_time(sec, tempo):
    """セクションの開始秒(音源の先頭からの秒数)。小節番号とテンポから出す(4/4拍子、音源は1小節目から始まる前提)。"""
    try:
        bpm = float(tempo)
    except (TypeError, ValueError):
        return None
    bars = sec.get("bars")
    if not bars or bpm <= 0:
        return None
    return (bars[0]["n"] - 1) * 240.0 / bpm


def draft_text(song):
    """小節単位のデータから、.cho の下書きを作る。
    歌詞のある行は約4小節(歌詞の1行を途中で切らない)、歌詞のない行は8小節(6の倍数なら6)で、
    繰り返しは %% や %%%% で短くする。お手本(シンゾウハウゴイテルの清書)の書き方に合わせている。
    セクションの見出しには、自動スクロール用の開始時刻(@0:21)をテンポと小節番号から付ける。"""
    lines = [f"{{title: {song['title']}}}"]
    if song.get("key"):
        lines.append(f"{{key: {song['key']}}}")
    tempo = song.get("tempo")
    if tempo:
        try:
            tempo = int(float(tempo)) if float(tempo).is_integer() else tempo
        except (TypeError, ValueError):
            pass
        lines.append(f"{{tempo: {tempo}}}")
    for a in load_json(HERE / "config.json", {}).get("audio", {}).get(song["id"], []):
        lines.append(f"{{audio: {a}}}")  # 音源(Google Drive)。config.json の audio に曲ごとに登録しておく
    lines.append("")

    sections = prepare_sections(song)
    for sec in sections:
        if "text" not in sec and not any(b["cells"] for b in sec["bars"]):
            continue  # 中身が無いセクションは出さない
        if sec.get("label"):
            t = section_time(sec, song.get("tempo"))
            lines.append("# " + sec["label"] + (f" @{fmt_time2(t)}" if t is not None else ""))
        if "text" in sec:  # 歌詞だけの曲
            lines += sec["text"]
        else:
            runs = []
            for b in sec["bars"]:
                if runs and runs[-1][0] == b["k"]:
                    runs[-1][1].append(b)
                else:
                    runs.append((b["k"], [b]))
            for ri, (kind, bars) in enumerate(runs):
                if kind == "l" and any(c["t"].strip() for b in bars for c in b["cells"]):
                    lines += lyric_lines(bars, merge_tail=bool(song.get("hasLyricsDoc")))
                else:
                    if sec is sections[-1] and ri == len(runs) - 1:  # 曲の最後の伸ばしは1小節にまとめる
                        while len(bars) >= 2 and chord_bar_text(bars[-1]) == "/" and chord_bar_text(bars[-2]) == "/":
                            bars = bars[:-1]
                    lines += inst_lines(bars)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def add_section_times(ent):
    """できあがっている .cho の見出しに、Excelから出した開始時刻(@0:21)を書き足す。
    見出しは、数字・空白を除いた名前で、前から順に対応づける(Intro / Intro1 / Intro2 は同じ「Intro」)。
    すでに @時間 がある見出しは触らない。Excelに対応が無い見出しも、そのまま。本文は一切変えない。"""
    path = cho_path(ent["id"])
    if not ent.get("xlsx") or not path.exists():
        return 0
    norm = lambda name: re.sub(r"[\d\s\"”'’]+", "", name or "").lower()
    song = intermediate(ent)
    sections = [s for s in prepare_sections(song) if s.get("bars") and s.get("label")]
    text = path.read_text(encoding="utf-8")
    out, j, added = [], 0, 0
    for line in text.split("\n"):
        m = re.match(r"^(#+\s*)(.+?)\s*$", line)
        if m:
            has_time = bool(re.search(r"@\s*[\d:.]+\s*$", m.group(2)))
            label = re.sub(r"\s*@\s*[\d:.]+\s*$", "", m.group(2))
            for k in range(j, len(sections)):
                if norm(sections[k]["label"]) == norm(label):
                    t = section_time(sections[k], song.get("tempo"))
                    if t is not None and not has_time:
                        line = f"{m.group(1)}{label} @{fmt_time2(t)}"
                        added += 1
                    j = k + 1
                    break
        out.append(line)
    if added:
        path.with_name(path.name + ".prev").write_text(text, encoding="utf-8")  # 変更前を1つ残す
        path.write_text("\n".join(out), encoding="utf-8")
    return added


TAG_RE = re.compile(r"\[[^\]]*\]")
BAR_RE = re.compile(r"\|:|:\||\|")
LINE_TIME_RE = re.compile(r"^@\s*((?:\d+:)?\d+(?:\.\d+)?)\s*")  # 行の先頭の @0:21


def lyric_stream(song):
    """歌詞の文字を、曲の順に (正規化した文字, 位置) で並べる。位置は「小節番号 + 拍/4」(1小節目の頭が1.0)。
    セルの中の文字は、次のセルの位置までを等分する(最大2小節)。"""
    cells = [c for sec in song["sections"] for b in sec["bars"] for c in b["cells"]]
    chars, poss = [], []
    for i, c in enumerate(cells):
        if not c["t"].strip():
            continue
        nxt = cells[i + 1]["pos"] if i + 1 < len(cells) else c["pos"] + 1
        span = min(max(nxt - c["pos"], 0.25), 2.0)
        for ch, oi in norm_text(c["t"]):
            chars.append(ch)
            poss.append(c["pos"] + span * oi / max(len(c["t"]), 1))
    return chars, poss


def add_line_times(ent, redo=False, vocal=None):
    """できあがっている .cho の各行の頭に、Excelから出した時刻(@0:21 )を書き足す。
    歌詞の行は、歌詞の文字をExcelの歌詞と照らして位置を探す(前から順に。サビなど同じ歌詞があっても、直前の続きから探す)。
    コードだけの行は、直前までの小節数から数える。見つからない行・すでに @時刻 がある行は触らない。
    見出しに @時刻 が無ければ、先頭の行の時刻を見出しにも書く。
    redo=True なら、行の時刻をいったん外して付け直し、見出しの時刻も、先頭の歌詞の行と1.5秒以上ずれていれば直す。
    vocal(vocal_timing.VocalOnsets)を渡すと、歌詞の行の時刻を、音源の「歌い出し」に合わせる。
    行の時刻は「音源の秒」(Excelの小節の頭 + {offset})。小数2桁まで書く。
    本文は変えない。書き足した行数(見出しを直した数を含む)を返す。"""
    path = cho_path(ent["id"])
    if not ent.get("xlsx") or not path.exists():
        return 0
    song = intermediate(ent)
    try:
        bpm = float(song.get("tempo"))
    except (TypeError, ValueError):
        return 0
    if bpm <= 0:
        return 0
    chars, poss = lyric_stream(song)
    joined = "".join(chars)
    to_sec = lambda pos: (pos - 1) * 240.0 / bpm
    time_re = re.compile(r"^(?:(\d+):)?(\d+(?:\.\d+)?)$")
    text = path.read_text(encoding="utf-8")
    om = re.search(r"^\{\s*offset\s*:\s*(-?[\d.]+)\s*\}", text, re.M)
    off = float(om.group(1)) if om else 0.05   # 音源のビートの遅れ(秒)。閲覧画面の初期値と同じ
    out, added = [], 0
    cursor, cur_bar, last_t = 0, None, -1.0
    head = None  # 直前の見出し: {"i": 出力の位置, "label": 名前, "t": 見出しの時刻 or None}
    for line in text.split("\n"):
        if redo:
            line = LINE_TIME_RE.sub("", line)
        s = line.strip()
        if not s or s.startswith("{"):
            out.append(line)
            continue
        if s.startswith("#"):
            m = re.match(r"^(#+\s*)(.*?)\s*(?:@\s*((?:\d+:)?\d+(?:\.\d+)?))?\s*$", s)
            tm = time_re.match(m.group(3)) if m and m.group(3) else None
            ht = (int(tm.group(1) or 0) * 60 + float(tm.group(2))) if tm else None
            head = {"i": len(out), "prefix": m.group(1), "label": m.group(2), "t": ht}
            if ht is not None:
                cur_bar = ht * bpm / 240.0 + 1
                last_t = ht + off
            out.append(line)
            continue
        if LINE_TIME_RE.match(s):
            out.append(line)
            head = None
            continue
        lyric = not (s.startswith("|") and "[" not in s)
        t = t_bar = None
        if not lyric:  # コードだけの行
            if cur_bar is not None:
                t = to_sec(cur_bar)
                cur_bar += sum(4 if c == "%%%%" else 2 if c == "%%" else 1
                               for c in (x.strip() for x in re.split(r"\|:|:\||\|", s)) if c)
        else:
            key = "".join(ch for ch, _ in norm_text(BAR_RE.sub("", TAG_RE.sub("", s))))
            if key:
                idx = joined.find(key, cursor, cursor + len(key) + 400)
                if idx < 0 and len(key) > 6:
                    idx = joined.find(key[:6], cursor, cursor + 160)
                if idx >= 0:
                    t = to_sec(poss[idx])
                    t_bar = to_sec(math.floor(poss[idx] + 1e-6))  # その歌詞が始まる小節の頭(見出しの時刻に使う)
                    last = min(idx + len(key), len(poss)) - 1
                    cursor = idx + max(len(key), 1)
                    cur_bar = int(poss[last]) + 1  # 次に来るものは、この歌詞の最後の小節の次から
        t_line = None  # この行の時刻(音源の秒)
        if t is not None:
            t_line = t + off
            if vocal is not None and lyric:
                t_line = vocal.refine(t_line)
        if head is not None:  # セクションの最初の行: 見出しの時刻を確かめる
            head_t = head["t"]
            if t is not None and (head["t"] is None or (redo and lyric and abs(head["t"] - t) > 1.5)):
                out[head["i"]] = f"{head['prefix']}{head['label']} @{fmt_time2(t_bar if t_bar is not None else t)}"
                added += 1
                last_t = t + off
                head_t = t
            elif head["t"] is not None and not lyric:
                t = head["t"]  # コードだけで始まる見出しは、見出しの時刻を信じる
                t_line = t + off
            first_in_sec = True
        else:
            first_in_sec = False
        # 見出しの時刻とほぼ同じ先頭行・直前より戻る時刻は書かない(歌い出しに合わせるときは、0.12秒以上ずれていれば書く)
        same = first_in_sec and head_t is not None and t_line is not None and abs(t_line - (head_t + off)) < (0.12 if vocal is not None else 1.0)
        if t_line is not None and not same and t_line > last_t + 0.02:  # 前の行より後でなければ書かない(逆戻りを防ぐ)
            line = f"@{fmt_time2(t_line)} {line}"
            added += 1
        if t_line is not None and t_line > last_t:
            last_t = t_line
        head = None
        out.append(line)
    if added:
        path.with_name(path.name + ".prev").write_text(text, encoding="utf-8")  # 変更前を1つ残す
        path.write_text("\n".join(out), encoding="utf-8")
    return added


def precise_section_times(ent, tol=1.0):
    """見出しの時刻(秒単位に丸められている)を、Excelの小節の頭にそろえて、小数2桁にする。
    歌詞で始まる見出しは、その歌詞が始まる小節の頭。コードだけで始まる見出しは、いまの時刻に一番近い小節の頭。
    いまの時刻と tol 秒(コードだけの見出しは0.75秒)より離れているものは、手で直した値とみなして触らない。
    見出しにまだ時刻が無いものも触らない。本文・行の時刻は変えない。直した見出しの数を返す。"""
    path = cho_path(ent["id"])
    if not ent.get("xlsx") or not path.exists():
        return 0
    song = intermediate(ent)
    try:
        bpm = float(song.get("tempo"))
    except (TypeError, ValueError):
        return 0
    if bpm <= 0:
        return 0
    chars, poss = lyric_stream(song)
    joined = "".join(chars)
    bar_sec = 240.0 / bpm
    time_re = re.compile(r"^(?:(\d+):)?(\d+(?:\.\d+)?)$")
    text = path.read_text(encoding="utf-8")
    out, changed, cursor, head = [], 0, 0, None
    for line in text.split("\n"):
        s = LINE_TIME_RE.sub("", line.strip())
        if s.startswith("#"):
            m = re.match(r"^(#+\s*)(.*?)\s*(?:@\s*((?:\d+:)?\d+(?:\.\d+)?))?\s*$", s)
            tm = time_re.match(m.group(3)) if m and m.group(3) else None
            head = {"i": len(out), "prefix": m.group(1), "label": m.group(2),
                    "t": (int(tm.group(1) or 0) * 60 + float(tm.group(2))) if tm else None}
            out.append(line)
            continue
        if not s or s.startswith("{"):
            out.append(line)
            continue
        lyric = not (s.startswith("|") and "[" not in s)
        exact = None
        if lyric:
            key = "".join(ch for ch, _ in norm_text(BAR_RE.sub("", TAG_RE.sub("", s))))
            if key:
                idx = joined.find(key, cursor, cursor + len(key) + 400)
                if idx < 0 and len(key) > 6:
                    idx = joined.find(key[:6], cursor, cursor + 160)
                if idx >= 0:
                    cursor = idx + max(len(key), 1)
                    exact = (math.floor(poss[idx] + 1e-6) - 1) * bar_sec
                    if head is not None and head["t"] is not None and abs(exact - head["t"]) > tol:
                        exact = None
        elif head is not None and head["t"] is not None:
            k = round(head["t"] / bar_sec)
            exact = k * bar_sec
            if abs(exact - head["t"]) > 0.75:
                exact = None
        if head is not None and exact is not None and head["t"] is not None:
            new = f"{head['prefix']}{head['label']} @{fmt_time2(exact)}"
            if new != out[head["i"]].strip():
                out[head["i"]] = new
                changed += 1
        if lyric or exact is not None or head is not None:
            head = None
        out.append(line)
    if changed:
        path.with_name(path.name + ".prev").write_text(text, encoding="utf-8")  # 変更前を1つ残す
        path.write_text("\n".join(out), encoding="utf-8")
    return changed


def plain_lyric_lines(source):
    """.cho から歌詞の行だけを取り出す(コード・小節線・見出し・指定は除く)"""
    out = []
    for ln in source.splitlines():
        s = LINE_TIME_RE.sub("", ln.strip())
        if not s or s.startswith(("{", "#")):
            continue
        if s.startswith("|") and "[" not in s:  # コードだけの行
            continue
        out.append(BAR_RE.sub("", TAG_RE.sub("", s)))
    return out


def group_runs(idx_list):
    runs, cur = [], []
    for i in idx_list:
        if cur and i != cur[-1] + 1:
            runs.append(cur)
            cur = []
        cur.append(i)
    if cur:
        runs.append(cur)
    return runs


def doc_diff(source, dx_lines):
    """.cho の歌詞と、歌詞docの歌詞の違い(歌詞を直したとき、docと食い違っていないかの確認用)"""
    cho = plain_lyric_lines(source)
    a, a_line, b, b_line = [], [], [], []
    for k, t in enumerate(cho):
        for c, _ in norm_text(t):
            a.append(c)
            a_line.append(k)
    for k, line in enumerate(dx_lines):
        for c, _ in norm_text(line["text"]):
            b.append(c)
            b_line.append(k)
    pairs = drop_isolated_mismatches(align(a, b), a, b)
    ma, mb = {i for i, _ in pairs}, {j for _, j in pairs}
    diffs = [(a[i], b[j], cho[a_line[i]]) for i, j in pairs if a[i] != b[j]]
    cho_only = [{"text": "".join(a[i] for i in r), "line": cho[a_line[r[0]]]}
                for r in group_runs([i for i in range(len(a)) if i not in ma])]
    doc_only = [{"text": "".join(b[j] for j in r), "line": dx_lines[b_line[r[0]]]["text"]}
                for r in group_runs([j for j in range(len(b)) if j not in mb])]
    return {
        "choChars": len(a), "docChars": len(b), "matched": len(pairs), "exact": len(pairs) - len(diffs),
        "choOnly": cho_only[:60], "docOnly": doc_only[:60],
        "differs": [{"cho": x, "doc": y, "line": ln} for x, y, ln in diffs[:60]],
        "same": not cho_only and not doc_only and not diffs,
    }


def diff_report(title, d):
    out = [f"■ {title}  歌詞docとの照合: cho {d['choChars']}字 / doc {d['docChars']}字 / 一致 {d['exact']}字"]
    out.append(f"  choにだけある歌詞 {len(d['choOnly'])}箇所:")
    out += [f"    『{x['text']}』 in 「{x['line']}」" for x in d["choOnly"]]
    out.append(f"  docにだけある歌詞 {len(d['docOnly'])}箇所:")
    out += [f"    『{x['text']}』 in 「{x['line']}」" for x in d["docOnly"]]
    out.append(f"  表記が違う文字 {len(d['differs'])}件:")
    out += [f"    cho『{x['cho']}』 doc『{x['doc']}』 in 「{x['line']}」" for x in d["differs"]]
    return "\n".join(out)


# ---------------------------------------------------------------- 取り込みの入口
def intermediate(ent):
    """manifestの1曲から、小節単位のデータ(下書き用)を作る"""
    if not ent.get("xlsx"):
        title_doc, dx_lines = read_lyrics(ROOT / ent["lyrics"])
        return build_lyrics_only(ent["id"], ent.get("title") or title_doc, dx_lines)
    meta, rows = read_excel(ROOT / ent["xlsx"])
    if ent.get("lyrics"):
        title_doc, dx_lines = read_lyrics(ROOT / ent["lyrics"])
        has_docx = True
    else:
        title_doc, dx_lines, has_docx = None, lyrics_from_excel(rows), False
    return build_song(ent["id"], meta, rows, title_doc, dx_lines, has_docx)


def cho_path(sid):
    return SONGS_DIR / (safe_name(sid) + ".cho")


def load_manifest(manifest_path=None):
    manifest = json.loads(Path(manifest_path or HERE / "songs_manifest.json").read_text(encoding="utf-8"))
    exclude = set(load_json(HERE / "config.json", {}).get("exclude", []))
    return [e for e in manifest if e["id"] not in exclude and e.get("title") not in exclude]


def make_entry(ent):
    source = cho_path(ent["id"]).read_text(encoding="utf-8")
    diff = None
    if ent.get("lyrics") and (ROOT / ent["lyrics"]).exists():  # 歌詞docが手元に無いPCでは、照合を飛ばす
        _, dx_lines = read_lyrics(ROOT / ent["lyrics"])
        diff = doc_diff(source, dx_lines)
    return {"id": ent["id"], "hasLyricsDoc": bool(ent.get("lyrics")), "source": source, "docDiff": diff}


ORDER_FILE = HERE / "songs_order.txt"


def apply_order(entries):
    """曲の並び(プルダウンの順)。songs_order.txt に曲名を1行ずつ書いた順にする。
    書いていない曲は、その後ろに、もとの順で並ぶ。空行と # で始まる行は無視する。"""
    if not ORDER_FILE.exists():
        return entries
    names = [ln.strip() for ln in ORDER_FILE.read_text(encoding="utf-8-sig").splitlines()]
    rank = {n: i for i, n in enumerate(n for n in names if n and not n.startswith("#"))}
    return sorted(entries, key=lambda e: rank.get(e["id"], len(rank)))  # sorted は安定: 書いていない曲はもとの順


STEMS = ["2mix", "Vcless", "Gtrless", "Bsless", "Drless"]  # 歌入り / ボーカルなし / ギターなし / ベースなし / ドラムなし


def local_stems(ent):
    """Excelと同じフォルダの mp3 を、種類(2mix・Vcless など)ごとに。ファイル名に種類の名前が入っているものを使う"""
    if not ent.get("xlsx"):
        return {}
    folder = (ROOT / ent["xlsx"]).parent
    out = {}
    for p in sorted(folder.glob("*.mp3")):
        for s in STEMS:
            if s.lower() in p.name.lower() and s not in out:
                out[s] = p
    return out


def main(manifest_path=None):
    """songs/*.cho を読んで web/songs.js を作る。.cho が無い曲だけ、xlsx/歌詞docから下書きを作る。"""
    SONGS_DIR.mkdir(exist_ok=True)
    entries, reports = [], []
    for ent in load_manifest(manifest_path):
        path = cho_path(ent["id"])
        if not path.exists():
            song = intermediate(ent)
            path.write_text(draft_text(song), encoding="utf-8")
            add_line_times(ent)
            reports.append((report(song) if song.get("align") else f"■ {song['title']}  Excelなし(歌詞のみ)")
                           + f"\n  → 下書きを新規作成: songs/{path.name}")
        entry = make_entry(ent)
        entries.append(entry)
        if entry["docDiff"]:
            reports.append(diff_report(ent["id"], entry["docDiff"]))

    entries = apply_order(entries)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    api_key = load_json(HERE / "config.json", {}).get("driveApiKey", "")  # Google Drive APIキー(音源の再生用、任意)
    (OUT_DIR / "songs.js").write_text(
        "window.SONGBOOK = " + json.dumps({"songs": entries, "driveApiKey": api_key, "audioStems": local_stem_urls(manifest_path)},
                                          ensure_ascii=False) + ";\n",
        encoding="utf-8")
    (HERE / "align_report.txt").write_text("\n\n".join(reports), encoding="utf-8")
    print(f"{len(entries)} songs written -> {OUT_DIR}")
    return entries


def local_stem_urls(manifest_path=None):
    """手元の画面(編集者用サーバー)で使う、音源の種類ごとのURL。サーバーがフォルダの mp3 を返す"""
    from urllib.parse import quote
    out = {}
    for e in load_manifest(manifest_path):
        st = local_stems(e)
        if st:
            out[e["id"]] = {s: f"/api/audio/{quote(e['id'])}?name={quote(p.name)}" for s, p in st.items()}
    return out


def redraft(sid):
    """xlsx/歌詞docから下書きを作り直す(今の .cho は .bak に退避)。更新された曲のエントリを返す。"""
    ent = next((e for e in load_manifest() if e["id"] == sid), None)
    if ent is None:
        raise KeyError(sid)
    SONGS_DIR.mkdir(exist_ok=True)
    path = cho_path(sid)
    if path.exists():
        path.with_name(path.name + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(draft_text(intermediate(ent)), encoding="utf-8")
    add_line_times(ent)
    return next(e for e in main() if e["id"] == sid)


def redraft_all(keep=()):
    """全曲の下書きを作り直す。keep に挙げた曲(清書済み)は触らない。今の .cho は .bak に退避する。"""
    done = []
    for ent in load_manifest():
        if ent["id"] in keep:
            continue
        SONGS_DIR.mkdir(exist_ok=True)
        path = cho_path(ent["id"])
        if path.exists():
            path.with_name(path.name + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(draft_text(intermediate(ent)), encoding="utf-8")
        add_line_times(ent)
        done.append(ent["id"])
    main()
    return done


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--add-times":
        for e in load_manifest():
            n = add_section_times(e)
            if n:
                print(f"{e['id']}: 見出し {n} 個に開始時刻を書き足しました")
        main()
    elif len(sys.argv) > 1 and sys.argv[1] == "--detect-line-times":
        # 例: python songbook/build_songs.py --detect-line-times [曲名 ...]
        # 歌入りの音源から歌なしの音源を引いて歌声を取り出し、歌詞の各行の時刻を「歌い出し」に合わせて付け直す
        import vocal_timing
        only = [a for a in sys.argv[2:] if not a.startswith("--")]
        for e in load_manifest():
            if only and e["id"] not in only:
                continue
            if not e.get("xlsx"):
                continue
            try:
                v = vocal_timing.VocalOnsets((ROOT / e["xlsx"]).parent)
            except Exception as ex:  # noqa: BLE001
                print(f"{e['id']}: 歌声を取り出せませんでした({ex})")
                continue
            if not v.reliable:
                print(f"{e['id']}: 歌声をきれいに取り出せないため、Excelからの時刻のままにします(歌入りと歌なしの音源が、同じ時期に書き出したものか確かめてください)")
            n = add_line_times(e, redo=True, vocal=v if v.reliable else None)
            st = v.stats
            mv = st["moved"] / st["snapped"] if st["snapped"] else 0
            print(f"{e['id']}: {n} 行の時刻を付け直しました(歌い出しに合わせた歌詞の行: {st['snapped']} / {st['lines']}、平均 {mv:.2f} 秒動かした)")
        main()
    elif len(sys.argv) > 1 and sys.argv[1] == "--precise-times":
        # 例: python songbook/build_songs.py --precise-times [曲名 ...]   見出しの時刻を、小節の頭にそろえて小数にする
        only = [a for a in sys.argv[2:] if not a.startswith("--")]
        for e in load_manifest():
            if only and e["id"] not in only:
                continue
            n = precise_section_times(e)
            if n:
                print(f"{e['id']}: 見出し {n} 個の時刻を、小節の頭にそろえました")
        main()
    elif len(sys.argv) > 1 and sys.argv[1] in ("--add-line-times", "--redo-line-times"):
        # 例: python songbook/build_songs.py --add-line-times [曲名 ...]   (--redo-line-times は付け直し)
        only = [a for a in sys.argv[2:] if not a.startswith("--")]
        for e in load_manifest():
            if only and e["id"] not in only:
                continue
            n = add_line_times(e, redo=sys.argv[1] == "--redo-line-times")
            if n:
                print(f"{e['id']}: {n} 行に時刻を書き足しました")
        main()
    elif len(sys.argv) > 1 and sys.argv[1] == "--redraft-all":
        # 例: python songbook/build_songs.py --redraft-all --keep シンゾウハウゴイテル
        keep = sys.argv[sys.argv.index("--keep") + 1:] if "--keep" in sys.argv else []
        print("redrafted:", ", ".join(redraft_all(keep)))
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else None)
