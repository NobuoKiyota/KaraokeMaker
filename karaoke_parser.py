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

    def parse(self):
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
        tempo = self.metadata["tempo"]
        beat_duration = 60.0 / tempo  # 1拍あたりの秒数
        measure_duration = beat_duration * 4  # 4/4拍子固定と仮定

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
                
                # 表示行 (未指定ならデフォルト1、数字以外なら1)
                try:
                    line_num = int(vals[5]) if vals[5] is not None else 1
                    if line_num not in [1, 2]:
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
        # 最終イベントから数拍後を全体の長さとする
        last_event = raw_rows[-1]
        self.total_duration = last_event["time_sec"] + (beat_duration * 4)

        # 3. コード進行リストの構築
        current_chord = ""
        for ev in raw_rows:
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
        current_section = ""
        for ev in raw_rows:
            if ev["section"] and ev["section"] != current_section:
                current_section = ev["section"]
                self.sections.append({
                    "time_sec": ev["time_sec"],
                    "section": current_section
                })

        # 5. 歌詞フレーズの構築 (表示行 1 と 2 ごと)
        # 表示行ごとに歌詞イベントを抽出し、時間順にソート
        for line_idx in [1, 2]:
            line_events = [ev for ev in raw_rows if ev["line_num"] == line_idx and ev["lyric"] and ev["lyric"].lower() != "none"]
            
            if not line_events:
                continue

            phrases = []
            curr_phrase = None

            for i, ev in enumerate(line_events):
                # 新しいフレーズの開始条件:
                # - 最初の要素である
                # - または、前回の歌詞イベントから時間が大幅に空いている（例：2小節以上 = 8拍分）
                # - または、セクションが変更された
                # - または、直前のイベントの歌詞が空である（ただしline_eventsには歌詞ありのみ抽出しているので時間間隔だけで判断可能）
                
                is_new = False
                if curr_phrase is None:
                    is_new = True
                else:
                    time_diff = ev["time_sec"] - curr_phrase["words"][-1]["end_time"]
                    # 2小節（8拍）以上空いている、またはセクションが変わった場合は別フレーズとする
                    prev_ev = line_events[i-1]
                    if time_diff > (beat_duration * 8) or ev["section"] != prev_ev["section"]:
                        is_new = True

                if is_new:
                    if curr_phrase:
                        # 直前フレーズの終了時間を確定
                        # 最後の単語の終了時間をフレーズ全体の終了時間とする
                        curr_phrase["end_time"] = curr_phrase["words"][-1]["end_time"]
                        phrases.append(curr_phrase)
                    
                    curr_phrase = {
                        "line_num": line_idx,
                        "start_time": ev["time_sec"],
                        "end_time": ev["time_sec"], # 後で更新
                        "section": ev["section"],
                        "words": []
                    }

                # 部分テキストの終了時間を見積もる
                # 次の歌詞イベントの開始時間、または次の拍(1拍分)のどちらか早い方
                if i + 1 < len(line_events):
                    next_ev = line_events[i+1]
                    # 次のイベントが同じフレーズに属しそうならその開始時間まで
                    word_end = next_ev["time_sec"]
                    # ただし、間隔が広すぎる場合は、文字数に応じた長さ（1文字あたり0.3秒など）または1小節分を上限とする
                    if word_end - ev["time_sec"] > (beat_duration * 4):
                        word_end = ev["time_sec"] + (beat_duration * 4)
                else:
                    # 最後の歌詞の場合は1小節分歌うと仮定
                    word_end = ev["time_sec"] + (beat_duration * 4)

                curr_phrase["words"].append({
                    "text": ev["lyric"],
                    "start_time": ev["time_sec"],
                    "end_time": word_end,
                    "measure": ev["measure"],
                    "beat": ev["beat"],
                    "chord": ev["chord"]
                })

            if curr_phrase:
                curr_phrase["end_time"] = curr_phrase["words"][-1]["end_time"]
                phrases.append(curr_phrase)

            # 各フレーズの表示期間 (Display Period) を設定する
            # フレーズが画面に出現する時間と、消える時間を決定する。
            # 通常、表示行1のフレーズNは、次のフレーズN+1が始まるまで表示され続ける（または、歌い終わって数秒後に消える）。
            for i, phrase in enumerate(phrases):
                # 結合された全体のテキスト
                phrase["text"] = "".join([w["text"] for w in phrase["words"]])
                
                # 画面に出現する時間 (基本は歌い始める少し前、または前のフレーズが消えた後)
                if i == 0:
                    phrase["display_start"] = max(0.0, phrase["start_time"] - 2.0)
                else:
                    # 前のフレーズの歌い終わりと、今回の歌い始めの中間点
                    prev_phrase = phrases[i-1]
                    phrase["display_start"] = prev_phrase["end_time"] + (phrase["start_time"] - prev_phrase["end_time"]) * 0.5
                    # ただし、開始の2秒前より早くは出さない
                    phrase["display_start"] = max(phrase["display_start"], phrase["start_time"] - 2.0)

            # 全ての display_start が決定された後に、display_end を確定する
            for i, phrase in enumerate(phrases):
                # 画面から消える時間
                if i + 1 < len(phrases):
                    next_phrase = phrases[i+1]
                    # 次のフレーズが出現するタイミングで消える
                    # 前のフレーズと次のフレーズの表示開始をスムーズにつなぐ
                    phrase["display_end"] = next_phrase["display_start"]
                else:
                    # 最後のフレーズは歌い終わってから3秒表示し続ける
                    phrase["display_end"] = phrase["end_time"] + 3.0

            self.phrases_by_line[line_idx] = phrases

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
