from PIL import Image, ImageDraw, ImageFont
import os
import math
import json

class StyleProfile:
    def __init__(self):
        # 歌詞フォント設定
        self.lyric_font_path = "C:\\Windows\\Fonts\\meiryo.ttc"  # メイリオが標準的
        self.lyric_idle_color = "#FFFFFF"       # 待機色 (白)
        self.lyric_idle_outline = "#333333"     # 待機アウトライン (暗いグレー)
        self.lyric_active_color = "#FF8C00"     # 歌唱後色 (ダークオレンジ)
        self.lyric_active_outline = "#8B0000"   # 歌唱後アウトライン (ダークレッド)
        self.lyric_outline_width = 4
        
        # コードフォント設定
        self.chord_font_path = "C:\\Windows\\Fonts\\meiryob.ttc" # メイリオ太字
        self.chord_color = "#00FF7F"            # 互換性保持用
        self.chord_idle_color = "#00FF7F"       # 待機コード色 (初期値は旧 chord_color と同じ)
        self.chord_active_color = "#FF8C00"     # 活性コード色
        self.chord_outline = "#000000"
        self.chord_outline_width = 3
        
        # その他UIフォント設定
        self.ui_font_path = "C:\\Windows\\Fonts\\meiryo.ttc"
        self.ui_color = "#E0E0E0"
        
        # 個別フォントカラー設定
        self.title_color = "#FFFFFF"
        self.section_color = "#2222FF"
        self.measure_color = "#E0E0E0"
        self.timecode_color = "#FF2222"
        self.metronome_active_color = "#FF3300"
        self.metronome_inactive_color = "#222222"
        self.metronome_outline_color = "#FFCC00"
        
        # ガイド表示設定
        self.show_metronome = True
        self.show_upcoming_chords = False
        self.show_measure_number = True
        self.show_section_name = True
        self.show_title_in_video = True
        self.show_score_layout = True
        self.show_timecode = True

        # === 新規: 統一されたレイアウト配置パラメータ ===
        # size: フォントサイズ/ドット径, x: X座標/余白, y: Y座標/高さ% or オフセット
        self.layout = {
            "title": {"size": 44, "x": 60, "y": 50},       # 曲名 (左余白X, 上余白Y)
            "bpm": {"size": 32, "x": 260, "y": 50},        # BPM (右余白X, 上余白Y)
            "key": {"size": 32, "x": 60, "y": 50},         # KEY (右余白X, 上余白Y)
            "section": {"size": 36, "x": 60, "y": 130},    # セクション名 (左余白X, 上余白Y)
            "metronome": {"size": 14, "x": 220, "y": 130},  # メトロノーム (球半径, 右余白X, 上余白Y)
            "measure": {"size": 28, "x": 220, "y": 170},   # 小節番号 (サイズ, 右余白X, 上余白Y)
            "timecode": {"size": 28, "x": 220, "y": 210},  # タイムコード (サイズ, 右余白X, 上余白Y)
            "chord1": {"size": 42, "x": 0, "y": 65},       # コード① (X微調整, 歌詞からの上オフセットY)
            "lyric1": {"size": 48, "x": 0, "y": 540},      # 歌詞① (X微調整, 開始高さY px)
            "chord2": {"size": 42, "x": 0, "y": 65},       # コード② (X微調整, 歌詞からの上オフセットY)
            "lyric2": {"size": 48, "x": 0, "y": 864}       # 歌詞② (X微調整, 開始高さY px)
        }

    def to_dict(self):
        d = self.__dict__.copy()
        # 必要であればパスや個別カラーなどの基本設定も辞書化される
        return d

    def from_dict(self, d):
        for k, v in d.items():
            if hasattr(self, k):
                if k == "layout":
                    # 辞書のネストされた値を安全に復元
                    for key_name, param_dict in v.items():
                        if key_name in self.layout:
                            for param_name, param_val in param_dict.items():
                                self.layout[key_name][param_name] = param_val
                else:
                    orig_type = type(getattr(self, k))
                    try:
                        if orig_type == bool:
                            setattr(self, k, bool(v))
                        elif orig_type == int:
                            setattr(self, k, int(v))
                        elif orig_type == float:
                            setattr(self, k, float(v))
                        else:
                            setattr(self, k, v)
                    except Exception:
                        setattr(self, k, v)
        # 互換性維持: 旧設定の chord_color があれば chord_idle_color に適用
        if "chord_color" in d and "chord_idle_color" not in d:
            self.chord_idle_color = d["chord_color"]

class KaraokeRenderer:
    def __init__(self, parser, style=None):
        self.parser = parser
        self.style = style if style else StyleProfile()
        self.bg_image_cache = None
        self.bg_image_path = ""
        
        self._verify_fonts()

    def _verify_fonts(self):
        """指定されたフォントが存在しない場合、存在する日本語フォントにフォールバックする"""
        fallback_fonts = [
            "C:\\Windows\\Fonts\\meiryo.ttc",
            "C:\\Windows\\Fonts\\meiryob.ttc",
            "C:\\Windows\\Fonts\\msgothic.ttc",
            "C:\\Windows\\Fonts\\msyh.ttc",
            "C:\\Windows\\Fonts\\yu.ttc",
            "C:\\Windows\\Fonts\\yugothb.ttc",
            "arial.ttf"
        ]
        
        def check_and_fallback(path):
            if path and os.path.exists(path):
                return path
            for f in fallback_fonts:
                if os.path.exists(f):
                    return f
            return None
            
        self.style.lyric_font_path = check_and_fallback(self.style.lyric_font_path)
        self.style.chord_font_path = check_and_fallback(self.style.chord_font_path)
        self.style.ui_font_path = check_and_fallback(self.style.ui_font_path)

    def _load_font(self, path, size):
        try:
            if path:
                # index=0 (通常はレギュラー) を明示指定して、斜字(イタリック)化バグを完全に防止
                return ImageFont.truetype(path, size, index=0)
        except Exception:
            pass
        return ImageFont.load_default()

    def render_frame(self, time_sec, width=1920, height=1080):
        """指定された時間(秒)における1フレームの画像を生成して返す"""
        # 1. 土台 of 画像を作成 (RGB)
        img = Image.new("RGB", (width, height), self.parser.metadata.get("bg_color", "#000000"))
        draw = ImageDraw.Draw(img)
        
        # 背景画像の描画 (設定されている場合)
        bg_img_path = self.parser.metadata.get("bg_image", "")
        if bg_img_path and bg_img_path.lower() not in ["none", "null", "任意", "なし", ""]:
            full_bg_path = bg_img_path
            if not os.path.isabs(bg_img_path):
                full_bg_path = os.path.join(os.path.dirname(self.parser.excel_path), bg_img_path)
            
            if os.path.exists(full_bg_path):
                try:
                    if self.bg_image_path != full_bg_path or self.bg_image_cache is None:
                        self.bg_image_cache = Image.open(full_bg_path)
                        self.bg_image_path = full_bg_path
                    
                    bg_aspect = self.bg_image_cache.width / self.bg_image_cache.height
                    canvas_aspect = width / height
                    if bg_aspect > canvas_aspect:
                        new_h = height
                        new_w = int(height * bg_aspect)
                    else:
                        new_w = width
                        new_h = int(width / bg_aspect)
                        
                    resized_bg = self.bg_image_cache.resize((new_w, new_h), Image.Resampling.LANCZOS)
                    x = (width - new_w) // 2
                    y = (height - new_h) // 2
                    img.paste(resized_bg, (x, y))
                    draw = ImageDraw.Draw(img)
                except Exception as e:
                    print(f"Failed to draw background image: {e}")

        # 2. フォントの準備 (解像度に応じてフォントサイズをスケーリング)
        scale_factor = height / 1080.0
        
        # 統一レイアウト仕様に基づいてフォントをロード
        lyric1_font = self._load_font(self.style.lyric_font_path, int(self.style.layout["lyric1"]["size"] * scale_factor))
        lyric2_font = self._load_font(self.style.lyric_font_path, int(self.style.layout["lyric2"]["size"] * scale_factor))
        
        chord1_font = self._load_font(self.style.chord_font_path, int(self.style.layout["chord1"]["size"] * scale_factor))
        chord2_font = self._load_font(self.style.chord_font_path, int(self.style.layout["chord2"]["size"] * scale_factor))
        
        title_font = self._load_font(self.style.lyric_font_path, int(self.style.layout["title"]["size"] * scale_factor))
        bpm_font = self._load_font(self.style.ui_font_path, int(self.style.layout["bpm"]["size"] * scale_factor))
        key_font = self._load_font(self.style.ui_font_path, int(self.style.layout["key"]["size"] * scale_factor))
        section_font = self._load_font(self.style.ui_font_path, int(self.style.layout["section"]["size"] * scale_factor))
        measure_font = self._load_font(self.style.ui_font_path, int(self.style.layout["measure"]["size"] * scale_factor))

        # 3. 画面情報・メタデータの取得
        beat_info = self.parser.get_current_beat_info(time_sec)
        measure = beat_info["measure"]
        beat = beat_info["beat"]
        
        # 4. 曲メタデータ (曲タイトル、BPM、Key) の表示
        if self.style.show_title_in_video:
            # A. 曲タイトル (左上)
            title_text = self.parser.metadata.get('title', 'Unknown')
            title_x = int(self.style.layout["title"]["x"] * scale_factor)
            title_y = int(self.style.layout["title"]["y"] * scale_factor)
            draw.text((title_x, title_y), title_text, fill=self.style.title_color, font=title_font)
            
            # B. BPM (右上、右揃え)
            bpm_text = f"BPM: {self.parser.metadata.get('tempo', 120.0)}"
            bpm_x = width - int(self.style.layout["bpm"]["x"] * scale_factor)
            bpm_y = int(self.style.layout["bpm"]["y"] * scale_factor)
            draw.text((bpm_x, bpm_y), bpm_text, fill=self.style.ui_color, font=bpm_font, anchor="rt")

            # C. KEY (右上、右揃え)
            key_text = f"Key: {self.parser.metadata.get('key', '--')}"
            key_x = width - int(self.style.layout["key"]["x"] * scale_factor)
            key_y = int(self.style.layout["key"]["y"] * scale_factor)
            draw.text((key_x, key_y), key_text, fill=self.style.ui_color, font=key_font, anchor="rt")

        # 5. セクション名 (左中央/左余白)
        if self.style.show_section_name:
            section_name = self.parser.get_active_section(time_sec)
            sec_text = f"Section: {section_name}"
            sec_x = int(self.style.layout["section"]["x"] * scale_factor)
            sec_y = int(self.style.layout["section"]["y"] * scale_factor)
            draw.text((sec_x, sec_y), sec_text, fill=self.style.section_color, font=section_font)

        # 6. ビジュアル・ビート・インジケーター (メトロノーム)
        if self.style.show_metronome:
            metro_y = int(self.style.layout["metronome"]["y"] * scale_factor)
            metro_x_start = width - int(self.style.layout["metronome"]["x"] * scale_factor)
            circle_radius = int(self.style.layout["metronome"]["size"] * scale_factor)
            spacing = int(circle_radius * 2.7)
            
            for b in range(1, 5):
                cx = metro_x_start + (b - 1) * spacing
                cy = metro_y
                is_active = (b == beat)
                if is_active:
                    draw.ellipse([cx - circle_radius, cy - circle_radius, cx + circle_radius, cy + circle_radius], fill=self.style.metronome_active_color, outline=self.style.metronome_outline_color, width=2)
                else:
                    draw.ellipse([cx - circle_radius, cy - circle_radius, cx + circle_radius, cy + circle_radius], fill=self.style.metronome_inactive_color, outline="#555555", width=1)

        # 7. 固定小節番号表示 (独立レイアウト化)
        if self.style.show_measure_number:
            meas_text = f"Bar: {measure}"
            meas_x = width - int(self.style.layout["measure"]["x"] * scale_factor)
            meas_y = int(self.style.layout["measure"]["y"] * scale_factor)
            draw.text((meas_x, meas_y), meas_text, fill=self.style.measure_color, font=measure_font, anchor="rt")

        # 8. 歌詞ワイプ描画 (二行表示) および 楽譜アライメント描画
        line_y_ratios = {1: self.style.layout["lyric1"]["y"], 2: self.style.layout["lyric2"]["y"]}
        lyric_fonts = {1: lyric1_font, 2: lyric2_font}
        chord_fonts = {1: chord1_font, 2: chord2_font}
        chord_offsets = {1: self.style.layout["chord1"]["y"], 2: self.style.layout["chord2"]["y"]}
        lyric_adjust_xs = {1: self.style.layout["lyric1"]["x"], 2: self.style.layout["lyric2"]["x"]}
        chord_adjust_xs = {1: self.style.layout["chord1"]["x"], 2: self.style.layout["chord2"]["x"]}
        
        for line_num in [1, 2]:
            phrase = self.parser.get_active_phrase(line_num, time_sec)
            if not phrase:
                continue
                
            y_val = line_y_ratios[line_num]
            y_pos = int(y_val * scale_factor)
            
            l_font = lyric_fonts[line_num]
            c_font = chord_fonts[line_num]
            chord_offset = chord_offsets[line_num]
            
            words = phrase["words"]
            text_str = phrase["text"]
            
            # すべての単語の歌詞が "none" または空文字（＝歌詞なしコードのみ）か判定
            is_lyricless = all(w["text"].lower() == "none" or not w["text"] for w in words)
            
            # テキスト全体の幅を測定し、センタリング時の開始位置を計算
            total_w = self._get_text_width(draw, text_str, l_font)
            start_x = (width - total_w) // 2 + int(lyric_adjust_xs[line_num] * scale_factor)
            
            curr_relative_wipe_x = 0
            accumulated_w = 0
            
            word_positions = []
            phrase_dur = phrase["end_time"] - phrase["start_time"]
            
            for word in words:
                w_text = word["text"]
                w_start = word["start_time"]
                w_end = word["end_time"]
                w_chord = word.get("chord", "")
                w_measure = word.get("measure", 1)
                w_beat = word.get("beat", 1)
                
                if is_lyricless:
                    # 歌詞なしの場合: 画面左右余白15%を除いた領域(中央70%)に、時間比率に応じて配置(重なり防止)
                    margin_x = int(width * 0.15)
                    usable_width = width - margin_x * 2
                    if phrase_dur > 0:
                        ratio = (w_start - phrase["start_time"]) / phrase_dur
                    else:
                        ratio = 0.5
                    word_w = 0
                    word_left_x = margin_x + int(ratio * usable_width) + int(lyric_adjust_xs[line_num] * scale_factor)
                else:
                    # 歌詞がある場合: 従来通り、センタリングされた歌詞にアライメント
                    word_w = self._get_text_width(draw, w_text, l_font) if w_text.lower() != "none" else 0
                    word_left_x = start_x + accumulated_w
                
                word_positions.append({
                    "text": w_text,
                    "left_x": word_left_x,
                    "width": word_w,
                    "chord": w_chord,
                    "measure": w_measure,
                    "beat": w_beat
                })
                
                if not is_lyricless:
                    if time_sec >= w_end:
                        curr_relative_wipe_x = accumulated_w + word_w
                    elif time_sec >= w_start:
                        ratio = (time_sec - w_start) / (w_end - w_start)
                        curr_relative_wipe_x = accumulated_w + int(word_w * ratio)
                    accumulated_w += word_w

            # ワイプ境界Xの計算
            if is_lyricless:
                # 歌詞なしの場合: フレーズ全体の進行度に応じてワイプ境界Xを計算
                margin_x = int(width * 0.15)
                usable_width = width - margin_x * 2
                if phrase_dur > 0:
                    ratio = (time_sec - phrase["start_time"]) / phrase_dur
                else:
                    ratio = 0.0
                ratio = max(0.0, min(1.0, ratio))
                absolute_wipe_x = margin_x + int(ratio * usable_width) + int(lyric_adjust_xs[line_num] * scale_factor)
            else:
                absolute_wipe_x = start_x + curr_relative_wipe_x
            
            # フレーズ全体の不透明度 (フェードイン・アウト)
            opacity = 255
            fade_duration = 0.5
            if time_sec - phrase["display_start"] < fade_duration:
                opacity = int(255 * ((time_sec - phrase["display_start"]) / fade_duration))
            elif phrase["display_end"] - time_sec < fade_duration:
                opacity = int(255 * ((phrase["display_end"] - time_sec) / fade_duration))
            opacity = max(0, min(255, opacity))
            if opacity == 0:
                continue

            # レンダリング画像への合成用レイヤー
            temp_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            temp_draw = ImageDraw.Draw(temp_img)
            
            active_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            active_draw = ImageDraw.Draw(active_layer)

            # 歌詞が有効に存在する場合のみ、歌詞を描画
            if text_str:
                # 1. 待機文字を描画
                self._draw_text_with_outline(
                    temp_draw, text_str, (start_x, y_pos),
                    self.style.lyric_idle_color, self.style.lyric_idle_outline,
                    int(self.style.lyric_outline_width * scale_factor),
                    l_font
                )
                
                # 2. 歌唱後文字を描画
                self._draw_text_with_outline(
                    active_draw, text_str, (start_x, y_pos),
                    self.style.lyric_active_color, self.style.lyric_active_outline,
                    int(self.style.lyric_outline_width * scale_factor),
                    l_font
                )
            
            # 5. 楽譜風アライメント（コード）を描画
            if self.style.show_score_layout:
                for idx, w_pos in enumerate(word_positions):
                    w_left = w_pos["left_x"] + int(chord_adjust_xs[line_num] * scale_factor)
                    w_chord = w_pos["chord"]
                    w_text = w_pos["text"]
                    
                    if w_chord and w_chord.lower() not in ["none", "null", ""]:
                        chord_y = y_pos - int(chord_offset * scale_factor)
                        chord_w = self._get_text_width(draw, w_chord, c_font)
                        
                        # 歌詞が "none" の場合は、コードの中央が w_left に合うよう半幅分左に寄せる
                        draw_x = w_left
                        if w_text.lower() == "none":
                            draw_x = w_left - (chord_w // 2)
                            
                        # 待機コードを描画
                        self._draw_text_with_outline(
                            temp_draw, w_chord, (draw_x, chord_y),
                            self.style.chord_idle_color, self.style.chord_outline,
                            int(self.style.chord_outline_width * scale_factor),
                            c_font
                        )
                        # 活性コードを描画
                        self._draw_text_with_outline(
                            active_draw, w_chord, (draw_x, chord_y),
                            self.style.chord_active_color, self.style.chord_outline,
                            int(self.style.chord_outline_width * scale_factor),
                            c_font
                        )

            # マスク画像の作成 (ワイプXより左側が活性、右側が待機)
            mask_img = Image.new("L", (width, height), 0)
            mask_draw = ImageDraw.Draw(mask_img)
            mask_draw.rectangle([0, 0, absolute_wipe_x, height], fill=255)
            
            # 複合
            temp_img = Image.composite(active_layer, temp_img, mask_img)
            temp_draw = ImageDraw.Draw(temp_img)

            # フェードイン・アウト用の透明度調整
            if opacity < 255:
                r, g, b, a = temp_img.split()
                a = a.point(lambda p: int(p * (opacity / 255.0)))
                temp_img = Image.merge("RGBA", (r, g, b, a))

            img.paste(temp_img, (0, 0), temp_img)

        # 9. 実時間タイムコード表示 (右上端)
        if self.style.show_timecode:
            tc_y = int(self.style.layout["timecode"]["y"] * scale_factor)
            tc_x = width - int(self.style.layout["timecode"]["x"] * scale_factor)
            tc_size = int(self.style.layout["timecode"]["size"] * scale_factor)
            tc_font = self._load_font(self.style.ui_font_path, tc_size)
            
            # MM:SS:mm フォーマット (分:秒:10ミリ秒単位2桁)
            m = int(time_sec // 60)
            s = int(time_sec % 60)
            ms = int((time_sec % 1) * 100)
            tc_text = f"{m:02d}:{s:02d}:{ms:02d}"
            
            self._draw_text_with_outline(
                draw, tc_text, (tc_x, tc_y),
                self.style.timecode_color, "#000000",
                int(2 * scale_factor), tc_font, anchor="rt"
            )

        # 10. 小節グリッド・プログレスバー (画面最下部)
        progress_h = int(12 * scale_factor)
        progress_y = height - progress_h
        progress_ratio = min(1.0, time_sec / max(0.1, self.parser.total_duration))
        
        draw.rectangle([0, progress_y, width, height], fill="#222222")
        draw.rectangle([0, progress_y, int(width * progress_ratio), height], fill=self.style.chord_idle_color)
        
        return img

    def _get_text_width(self, draw, text, font):
        """Pillowでテキストの描画幅を測定する"""
        try:
            left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
            return right - left
        except AttributeError:
            return font.getsize(text)[0]

    def _draw_text_with_outline(self, draw, text, position, text_color, outline_color, outline_width, font, anchor=None):
        """縁取り付きのテキストを描画する"""
        x, y = position
        kwargs = {}
        if anchor:
            kwargs["anchor"] = anchor
            
        if outline_width > 0 and outline_color:
            steps = 8 if outline_width <= 2 else 16
            for i in range(steps):
                angle = i * (2 * math.pi / steps)
                dx = int(math.cos(angle) * outline_width)
                dy = int(math.sin(angle) * outline_width)
                draw.text((x + dx, y + dy), text, fill=outline_color, font=font, **kwargs)
                
        draw.text((x, y), text, fill=text_color, font=font, **kwargs)

if __name__ == "__main__":
    from karaoke_parser import KaraokeParser
    parser = KaraokeParser(r"Z:\KaraokeMaker\Resources\karaokemaker_sample.xlsx")
    renderer = KaraokeRenderer(parser)
    img = renderer.render_frame(17.5, 1920, 1080)
    
    output_dir = r"C:\Users\kiyot\.gemini\antigravity-ide\brain\a688f196-f54c-4842-b9dd-e3ce74c446cb\scratch"
    os.makedirs(output_dir, exist_ok=True)
    img_path = os.path.join(output_dir, "preview_frame.png")
    img.save(img_path)
    print("Test frame saved to:", img_path)
