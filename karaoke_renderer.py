from PIL import Image, ImageDraw, ImageFont
import os
import math
import json

class StyleProfile:
    def __init__(self):
        # 歌詞フォント設定
        self.lyric_font_path = "C:\\Windows\\Fonts\\meiryo.ttc"  # メイリオが標準的
        self.lyric_font_size = 48
        self.lyric_idle_color = "#FFFFFF"       # 待機色 (白)
        self.lyric_idle_outline = "#333333"     # 待機アウトライン (暗いグレー)
        self.lyric_active_color = "#FF8C00"     # 歌唱後色 (ダークオレンジ)
        self.lyric_active_outline = "#8B0000"   # 歌唱後アウトライン (ダークレッド)
        self.lyric_outline_width = 4
        
        # コードフォント設定
        self.chord_font_path = "C:\\Windows\\Fonts\\meiryob.ttc" # メイリオ太字
        self.chord_font_size = 42                # 歌詞の真上表示用に適したサイズ
        self.chord_color = "#00FF7F"            # コード色 (スプリンググリーン)
        self.chord_outline = "#000000"
        self.chord_outline_width = 3
        
        # その他UIフォント設定
        self.ui_font_path = "C:\\Windows\\Fonts\\meiryo.ttc"
        self.ui_font_size = 28
        self.ui_color = "#E0E0E0"
        
        # ガイド表示設定
        self.show_metronome = True
        self.show_upcoming_chords = False       # 楽譜風アライメント時は無効化
        self.show_measure_number = True
        self.show_section_name = True

        # === 楽譜風アライメント用追加設定 ===
        self.show_score_layout = True
        self.lyric_y_ratio_1 = 0.50             # 表示ライン①のY座標比率
        self.lyric_y_ratio_2 = 0.80             # 表示ライン②のY座標比率
        self.chord_offset_y = 65                # 歌詞の真上コードオフセット (px)
        self.measure_offset_y = 115             # 歌詞の真上小節オフセット (px)
        self.measure_font_size = 30             # 小節番号のフォントサイズ
        self.measure_color = "#00BFFF"          # 小節番号のカラー (ディープスカイブルー)

        # === 新規: 動画内メタデータ・UI個別レイアウト調整パラメータ ===
        self.show_title_in_video = True         # 動画内タイトル表示
        self.title_font_size = 44               # タイトルフォントサイズ
        self.title_color = "#FFFFFF"            # タイトルカラー
        self.title_margin_x = 60                 # タイトル左余白
        self.title_margin_y = 50                 # タイトル上余白

        self.bpm_key_font_size = 32             # BPM/Keyフォントサイズ
        self.bpm_key_color = "#E0E0E0"          # BPM/Keyカラー
        self.bpm_key_margin_y = 50               # BPM/Key上余白 (右余白はtitle_margin_xと同期)
        
        self.section_font_size = 36             # セクションフォントサイズ
        self.section_margin_x = 60               # セクション左余白
        self.section_margin_y = 130              # セクション上余白

        self.metronome_margin_x = 220            # メトロノーム右余白
        self.metronome_margin_y = 130            # メトロノーム上余白
        self.metronome_dot_size = 14             # メトロノームドット半径
        self.metronome_dot_spacing = 38          # メトロノームドット間隔

    def to_dict(self):
        return self.__dict__.copy()

    def from_dict(self, d):
        for k, v in d.items():
            if hasattr(self, k):
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
                return ImageFont.truetype(path, size)
        except Exception:
            pass
        return ImageFont.load_default()

    def render_frame(self, time_sec, width=1920, height=1080):
        """指定された時間(秒)における1フレームの画像を生成して返す"""
        # 1. 土台の画像を作成 (RGB)
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
        lyric_font = self._load_font(self.style.lyric_font_path, int(self.style.lyric_font_size * scale_factor))
        chord_font = self._load_font(self.style.chord_font_path, int(self.style.chord_font_size * scale_factor))
        ui_font = self._load_font(self.style.ui_font_path, int(self.style.ui_font_size * scale_factor))
        measure_font = self._load_font(self.style.ui_font_path, int(self.style.measure_font_size * scale_factor))
        
        # 新パラメータ用の個別フォント準備
        title_font = self._load_font(self.style.lyric_font_path, int(self.style.title_font_size * scale_factor))
        bpm_key_font = self._load_font(self.style.ui_font_path, int(self.style.bpm_key_font_size * scale_factor))
        section_font = self._load_font(self.style.ui_font_path, int(self.style.section_font_size * scale_factor))

        # 3. 画面情報・メタデータの取得
        beat_info = self.parser.get_current_beat_info(time_sec)
        measure = beat_info["measure"]
        beat = beat_info["beat"]
        beat_ratio = beat_info["beat_ratio"]
        
        # 4. 曲メタデータ (曲タイトル、BPM、Key) の動画内表示
        if self.style.show_title_in_video:
            # A. 曲タイトル (左上)
            title_text = f"曲タイトル: {self.parser.metadata.get('title', 'Unknown')}"
            title_x = int(self.style.title_margin_x * scale_factor)
            title_y = int(self.style.title_margin_y * scale_factor)
            draw.text((title_x, title_y), title_text, fill=self.style.title_color, font=title_font)
            
            # B. BPM / Key (右上、見切れ防止のため右揃え anchor="rt" を適用)
            bpm_key_text = f"BPM: {self.parser.metadata.get('tempo', 120.0)} | Key: {self.parser.metadata.get('key', '--')}"
            bpm_key_x = width - int(self.style.title_margin_x * scale_factor)
            bpm_key_y = int(self.style.bpm_key_margin_y * scale_factor)
            draw.text((bpm_key_x, bpm_key_y), bpm_key_text, fill=self.style.bpm_key_color, font=bpm_key_font, anchor="rt")

        # 5. セクション名 (左下/左中央)
        if self.style.show_section_name:
            section_name = self.parser.get_active_section(time_sec)
            sec_text = f"Section: {section_name}"
            sec_x = int(self.style.section_margin_x * scale_factor)
            sec_y = int(self.style.section_margin_y * scale_factor)
            draw.text((sec_x, sec_y), sec_text, fill=self.style.ui_color, font=section_font)
            
        # 6. 小節番号の右上固定表示 (従来UI)
        if self.style.show_measure_number:
            meas_text = f"Bar: {measure}"
            meas_x = width - int(self.style.title_margin_x * scale_factor)
            meas_y = int(self.style.section_margin_y * scale_factor)
            draw.text((meas_x, meas_y), meas_text, fill=self.style.ui_color, font=section_font, anchor="rt")

        # 7. ビジュアル・ビート・インジケーター (メトロノーム)
        if self.style.show_metronome:
            metro_y = int(self.style.metronome_margin_y * scale_factor)
            metro_x_start = width - int(self.style.metronome_margin_x * scale_factor)
            circle_radius = int(self.style.metronome_dot_size * scale_factor)
            spacing = int(self.style.metronome_dot_spacing * scale_factor)
            
            for b in range(1, 5):
                cx = metro_x_start + (b - 1) * spacing
                cy = metro_y
                is_active = (b == beat)
                if is_active:
                    draw.ellipse([cx - circle_radius, cy - circle_radius, cx + circle_radius, cy + circle_radius], fill="#FF3300", outline="#FFCC00", width=2)
                else:
                    draw.ellipse([cx - circle_radius, cy - circle_radius, cx + circle_radius, cy + circle_radius], fill="#222222", outline="#555555", width=1)

        # 8. コード進行の表示 (show_score_layout が無効の場合のみ、画面中央上部に従来の表示を行う)
        if not self.style.show_score_layout:
            active_chord = self.parser.get_active_chord(time_sec)
            if active_chord:
                chord_y = int(150 * scale_factor)
                self._draw_text_with_outline(
                    draw, active_chord, 
                    (width // 2, chord_y), 
                    self.style.chord_color, self.style.chord_outline, 
                    int(self.style.chord_outline_width * scale_factor), 
                    chord_font, anchor="mm"
                )
                
                if self.style.show_upcoming_chords:
                    upcoming = self.parser.get_next_chords(time_sec, count=3)
                    if upcoming:
                        guide_y = int(270 * scale_factor)
                        guide_text = "Next: " + " -> ".join([c["chord"] if c["chord"] else "N.C." for c in upcoming])
                        draw.text((width // 2, guide_y), guide_text, fill="#AAAAAA", font=ui_font, anchor="mm")

        # 9. 歌詞ワイプ描画 (二行表示) および 楽譜アライメント描画
        line_y_ratios = {1: self.style.lyric_y_ratio_1, 2: self.style.lyric_y_ratio_2}
        
        for line_num in [1, 2]:
            phrase = self.parser.get_active_phrase(line_num, time_sec)
            if not phrase:
                continue
                
            y_pos = int(height * line_y_ratios[line_num])
            words = phrase["words"]
            text_str = phrase["text"]
            
            # テキスト全体の幅を測定し、センタリング時の開始位置を計算
            total_w = self._get_text_width(draw, text_str, lyric_font)
            start_x = (width - total_w) // 2
            
            curr_relative_wipe_x = 0
            accumulated_w = 0
            
            word_positions = []
            
            for word in words:
                w_text = word["text"]
                w_start = word["start_time"]
                w_end = word["end_time"]
                w_chord = word.get("chord", "")
                w_measure = word.get("measure", 1)
                w_beat = word.get("beat", 1)
                
                word_w = self._get_text_width(draw, w_text, lyric_font)
                word_left_x = start_x + accumulated_w
                
                word_positions.append({
                    "text": w_text,
                    "left_x": word_left_x,
                    "width": word_w,
                    "chord": w_chord,
                    "measure": w_measure,
                    "beat": w_beat
                })
                
                if time_sec >= w_end:
                    curr_relative_wipe_x = accumulated_w + word_w
                elif time_sec >= w_start:
                    ratio = (time_sec - w_start) / (w_end - w_start)
                    curr_relative_wipe_x = accumulated_w + int(word_w * ratio)
                
                accumulated_w += word_w

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

            # レンダリング画像への合成
            temp_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            temp_draw = ImageDraw.Draw(temp_img)
            
            # 1. 待機文字を全体に描画
            self._draw_text_with_outline(
                temp_draw, text_str, (start_x, y_pos),
                self.style.lyric_idle_color, self.style.lyric_idle_outline,
                int(self.style.lyric_outline_width * scale_factor),
                lyric_font
            )
            
            # 2. 歌唱後文字のレイヤーを描画
            active_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            active_draw = ImageDraw.Draw(active_layer)
            self._draw_text_with_outline(
                active_draw, text_str, (start_x, y_pos),
                self.style.lyric_active_color, self.style.lyric_active_outline,
                int(self.style.lyric_outline_width * scale_factor),
                lyric_font
            )
            
            # 3. マスク画像の作成 (ワイプXより左側が真っ白、右側が真っ黒のアルファマスク)
            mask_img = Image.new("L", (width, height), 0)
            mask_draw = ImageDraw.Draw(mask_img)
            mask_draw.rectangle([0, 0, absolute_wipe_x, height], fill=255)
            
            # 4. 複合
            temp_img = Image.composite(active_layer, temp_img, mask_img)
            
            # 5. 楽譜風アライメント（コードと小節線）を一時レイヤーに描画して合成
            if self.style.show_score_layout:
                for idx, w_pos in enumerate(word_positions):
                    w_left = w_pos["left_x"]
                    w_chord = w_pos["chord"]
                    w_meas = w_pos["measure"]
                    w_beat = w_pos["beat"]
                    w_text = w_pos["text"]
                    
                    if w_text.lower() == "none":
                        continue

                    # 5-A. コードの描画 (歌詞の真上)
                    if w_chord and w_chord.lower() not in ["none", "null", ""]:
                        chord_y = y_pos - int(self.style.chord_offset_y * scale_factor)
                        self._draw_text_with_outline(
                            temp_draw, w_chord, (w_left, chord_y),
                            self.style.chord_color, self.style.chord_outline,
                            int(self.style.chord_outline_width * scale_factor),
                            chord_font
                        )
                    
                    # 5-B. 小節数の描画 (コードのさらに上)
                    is_new_meas = (idx == 0)
                    if idx > 0 and word_positions[idx-1]["measure"] != w_meas:
                        is_new_meas = True
                        
                    if is_new_meas or w_beat == 1:
                        measure_y = y_pos - int(self.style.measure_offset_y * scale_factor)
                        
                        meas_str = f"{w_meas}"
                        if w_beat > 1:
                            beat_str = f"{int(w_beat)}" if w_beat.is_integer() else f"{w_beat}"
                            meas_str = f"{w_meas}({beat_str})"
                            
                        self._draw_text_with_outline(
                            temp_draw, meas_str, (w_left, measure_y),
                            self.style.measure_color, "#000000",
                            int(1.5 * scale_factor),
                            measure_font
                        )

            # フェードイン・アウト用の透明度調整
            if opacity < 255:
                r, g, b, a = temp_img.split()
                a = a.point(lambda p: int(p * (opacity / 255.0)))
                temp_img = Image.merge("RGBA", (r, g, b, a))

            img.paste(temp_img, (0, 0), temp_img)

        # 10. 小節グリッド・プログレスバー (画面最下部)
        progress_h = int(12 * scale_factor)
        progress_y = height - progress_h
        progress_ratio = min(1.0, time_sec / max(0.1, self.parser.total_duration))
        
        draw.rectangle([0, progress_y, width, height], fill="#222222")
        draw.rectangle([0, progress_y, int(width * progress_ratio), height], fill=self.style.chord_color)
        
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
    img = renderer.render_frame(26.0, 1920, 1080)
    
    output_dir = r"C:\Users\kiyot\.gemini\antigravity-ide\brain\a688f196-f54c-4842-b9dd-e3ce74c446cb\scratch"
    os.makedirs(output_dir, exist_ok=True)
    img_path = os.path.join(output_dir, "preview_frame.png")
    img.save(img_path)
    print("Test frame saved to:", img_path)
