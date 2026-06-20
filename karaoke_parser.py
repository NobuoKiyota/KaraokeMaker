import openpyxl
import os

class KaraokeParser:
    def __init__(self, excel_path):
        self.excel_path = excel_path
        self.metadata = {
            "title": "Unknown Title",
            "tempo": 120.0,
            "key": "",
            "wav_path": "",
            "video_size": (1920, 1080),
            "bg_color": "#000000",
            "bg_image": "",
            "layout_cols": 4
        }
        self.raw_events = []
        self.phrases_by_line = {1: [], 2: []}
        self.chords = []
        self.sections = []
        self.total_duration = 0.0

        if os.path.exists(excel_path):
            self.parse()

    def parse(self, from_raw_events=False):
        tempo = self.metadata["tempo"]
        beat_duration = 60.0 / tempo  # 1拍あたりの秒数
        measure_duration = beat_duration * 4  # 4/4拍子固定と仮定

        if not from_raw_events:
            wb = openpyxl.load_workbook(self.excel_path, data_only=True)
            sheet = wb.active  # 通常は最初のシート

            # 1. メタデータの読み込み (1行目〜7行目)
            for r in range(1, 8):
                row_vals = [cell.value for cell in sheet[r]]
                if not row_vals:
                    continue
                
                # 左側の基本情報
                if len(row_vals) >= 2:
                    key_name = str(row_vals[0]).strip() if row_vals[0] else ""
                    val = row_vals[1]
                    if key_name == "Title":
                        self.metadata["title"] = str(val) if val else "Unknown"
                    elif key_name == "Tempo":
                        try:
                            self.metadata["tempo"] = float(val) if val else 120.0
                        except ValueError:
                            self.metadata["tempo"] = 120.0
                    elif key_name == "Key":
                        self.metadata["key"] = str(val) if val else ""
                    elif key_name == "対象wav":
                        self.metadata["wav_path"] = str(val) if val else ""

                # 右側の描画・動画設定
                if len(row_vals) >= 7:
                    opt_name = str(row_vals[5]).strip() if row_vals[5] else ""
                    opt_val = row_vals[6]
                    if opt_name == "動画サイズ":
                        size_str = str(opt_val) if opt_val else "1920 x 1080"
                        if "x" in size_str:
                            parts = size_str.split("x")
                            try:
                                self.metadata["video_size"] = (int(parts[0].strip()), int(parts[1].strip()))
                            except ValueError:
                                self.metadata["video_size"] = (1920, 1080)
                    elif opt_name == "背景色":
                        self.metadata["bg_color"] = str(opt_val) if opt_val else "#000000"
                    elif opt_name == "背景画像":
                        self.metadata["bg_image"] = str(opt_val) if opt_val else ""
                    elif opt_name == "改行小節数":
                        try:
                            self.metadata["layout_cols"] = int(opt_val) if opt_val else 4
                        except ValueError:
                            self.metadata["layout_cols"] = 4

            # 2. 歌詞・コード・タイミングデータの読み込み (8行目以降)
            # 列インデックス: A: 小節, B: 拍, C: セクション, D: コード, E: 歌詞, F: 表示行
            raw_rows = []
            for r in range(9, sheet.max_row + 1):
                vals = [cell.value for cell in sheet[r]]
                if not vals or vals[0] == "End":
                    break
                if vals[0] is None:
                    continue

                try:
                    measure = int(vals[0])
                    beat = float(vals[1]) if vals[1] is not None else 1.0
                    section = str(vals[2]).strip() if vals[2] is not None else ""
                    chord = str(vals[3]).strip() if vals[3] is not None else ""
                    lyric = str(vals[4]).strip() if vals[4] is not None else ""
                    
                    # 表示行/グループID (未指定ならデフォルト1、数字以外なら1)
                    try:
                        line_num = int(vals[5]) if vals[5] is not None else 1
                        if line_num < 1:
                            line_num = 1
                    except ValueError:
                        line_num = 1

                    # タイムスタンプ算出 (秒)
                    time_sec = ((measure - 1) * 4 + (beat - 1)) * beat_duration
                    
                    raw_rows.append({
                        "measure": measure,
                        "beat": beat,
                        "section": section,
                        "chord": chord,
                        "lyric": lyric,
                        "line_num": line_num,
                        "time_sec": time_sec
                    })
                except Exception as e:
                    print(f"Row {r} parsing error: {e}")
                    continue

            if not raw_rows:
                return

            self.raw_events = raw_rows

        if not self.raw_events:
            return

        # 最終イベントから数拍後を全体の長さとする
        last_event = self.raw_events[-1]
        self.total_duration = last_event["time_sec"] + (beat_duration * 4)

        # 3. コード進行リストの構築
        self.chords = []
        for ev in self.raw_events:
            if ev["chord"]:
                # 'none' や空文字の場合はコードなしとする
                chord_val = "" if ev["chord"].lower() in ["none", "null", ""] else ev["chord"]
                self.chords.append({
                    "time_sec": ev["time_sec"],
                    "chord": chord_val,
                    "measure": ev["measure"],
                    "beat": ev["beat"]
                })

        # 4. セクションリストの構築
        self.sections = []
        current_section = ""
        for ev in self.raw_events:
            if ev["section"] and ev["section"] != current_section:
                current_section = ev["section"]
                self.sections.append({
                    "time_sec": ev["time_sec"],
                    "section": current_section
                })

        # 5. 歌詞フレーズの構築 (グループIDごと)
        # F列の値をグループID (1〜N) として取得し、グループIDごとにフレーズをまとめる
        # 物理的な表示行 (1: 上, 2: 下) は、グループIDが奇数か偶数かで動的に決定する
        group_ids = sorted(list(set([ev["line_num"] for ev in self.raw_events if ev["line_num"] is not None])))
        
        all_phrases = []
        for g_id in group_ids:
            g_events = [
                ev for ev in self.raw_events
                if ev["line_num"] == g_id and (
                    (ev["lyric"] and ev["lyric"].lower() != "none") or
                    (ev["chord"] and ev["chord"].lower() not in ["none", "null", ""])
                )
            ]
            
            if not g_events:
                continue

            # 物理的な表示行を決定 (奇数は1:上、偶数は2:下)
            line_idx = 1 if (g_id % 2 != 0) else 2
            
            phrase = {
                "group_id": g_id,
                "line_num": line_idx,
                "start_time": g_events[0]["time_sec"],
                "end_time": g_events[-1]["time_sec"], # 後で更新
                "section": g_events[0]["section"],
                "words": []
            }

            for i, ev in enumerate(g_events):
                # 部分テキストの終了時間を見積もる
                if i + 1 < len(g_events):
                    next_ev = g_events[i+1]
                    word_end = next_ev["time_sec"]
                    if word_end - ev["time_sec"] > (beat_duration * 4):
                        word_end = ev["time_sec"] + (beat_duration * 4)
                else:
                    word_end = ev["time_sec"] + (beat_duration * 4)

                phrase["words"].append({
                    "text": ev["lyric"],
                    "start_time": ev["time_sec"],
                    "end_time": word_end,
                    "measure": ev["measure"],
                    "beat": ev["beat"],
                    "chord": ev["chord"]
                })

            phrase["end_time"] = phrase["words"][-1]["end_time"]
            phrase["text"] = "".join([w["text"] for w in phrase["words"] if w["text"].lower() != "none"])
            all_phrases.append(phrase)

        # 物理的な表示行 (1:上, 2:下) ごとに、各フレーズの出現・消去タイミングを連結調整
        self.phrases_by_line = {1: [], 2: []}
        for line_idx in [1, 2]:
            line_phrases = [p for p in all_phrases if p["line_num"] == line_idx]
            
            # 出現時間 (display_start) の設定
            for i, phrase in enumerate(line_phrases):
                if i == 0:
                    phrase["display_start"] = max(0.0, phrase["start_time"] - 2.0)
                else:
                    prev_phrase = line_phrases[i-1]
                    phrase["display_start"] = prev_phrase["end_time"] + (phrase["start_time"] - prev_phrase["end_time"]) * 0.5
                    phrase["display_start"] = max(phrase["display_start"], phrase["start_time"] - 2.0)

            # 消去時間 (display_end) の設定
            for i, phrase in enumerate(line_phrases):
                if i + 1 < len(line_phrases):
                    next_phrase = line_phrases[i+1]
                    phrase["display_end"] = next_phrase["display_start"]
                else:
                    phrase["display_end"] = phrase["end_time"] + 3.0

            self.phrases_by_line[line_idx] = line_phrases

    def get_active_phrase(self, line_num, time_sec):
        """指定された時間において、指定された行に表示すべきフレーズを返す"""
        for phrase in self.phrases_by_line.get(line_num, []):
            if phrase["display_start"] <= time_sec <= phrase["display_end"]:
                return phrase
        return None

    def get_active_chord(self, time_sec):
        """指定された時間におけるコードを返す"""
        active_chord = ""
        for c in self.chords:
            if c["time_sec"] <= time_sec:
                active_chord = c["chord"]
            else:
                break
        return active_chord

    def get_next_chords(self, time_sec, count=4):
        """指定された時間以降の直近のコード進行リストを返す(練習ガイド用)"""
        upcoming = []
        for c in self.chords:
            if c["time_sec"] > time_sec:
                upcoming.append(c)
                if len(upcoming) >= count:
                    break
        return upcoming

    def get_active_section(self, time_sec):
        """指定された時間におけるセクション名を返す"""
        active_sec = "Count"
        for s in self.sections:
            if s["time_sec"] <= time_sec:
                active_sec = s["section"]
            else:
                break
        return active_sec

    def get_current_beat_info(self, time_sec):
        """現在時間における小節数、拍数、小節内の進行比率を返す"""
        tempo = self.metadata["tempo"]
        beat_duration = 60.0 / tempo
        
        # 総拍数 (0-indexed)
        total_beats = time_sec / beat_duration
        
        # 4/4拍子と仮定
        measure = int(total_beats // 4) + 1
        beat = int(total_beats % 4) + 1
        
        # 拍の内部の進捗 (0.0〜1.0)
        beat_ratio = (total_beats % 4) - (beat - 1)
        
        return {
            "measure": measure,
            "beat": beat,
            "beat_ratio": beat_ratio,
            "total_beats": total_beats
        }

if __name__ == "__main__":
    parser = KaraokeParser(r"Z:\KaraokeMaker\Resources\karaokemaker_sample.xlsx")
    print("Metadata:", parser.metadata)
    print("Total Duration:", parser.total_duration)
    print("Line 1 Phrases:")
    for p in parser.phrases_by_line[1]:
        print(f"  [{p['display_start']:.2f}s - {p['display_end']:.2f}s] text='{p['text']}' start={p['start_time']:.2f}s end={p['end_time']:.2f}s")
    print("Line 2 Phrases:")
    for p in parser.phrases_by_line[2]:
        print(f"  [{p['display_start']:.2f}s - {p['display_end']:.2f}s] text='{p['text']}' start={p['start_time']:.2f}s end={p['end_time']:.2f}s")
