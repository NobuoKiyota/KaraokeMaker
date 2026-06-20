import subprocess
import os
import threading
import imageio_ffmpeg
from PIL import Image

class VideoExporter:
    def __init__(self, renderer):
        self.renderer = renderer
        self._cancel_requested = False
        self._lock = threading.Lock()

    def cancel(self):
        with self._lock:
            self._cancel_requested = True

    def export(self, output_path, audio_path, fps=30, width=1920, height=1080, progress_callback=None):
        """動画をエクスポートする。別スレッドから呼び出すことを想定。
        
        Args:
            output_path: 出力先MP4ファイルパス
            audio_path: 入力WAVファイルパス
            fps: フレームレート
            width: 動画幅
            height: 動画高さ
            progress_callback: 進捗通知用のコールバック関数。引数は (current_frame, total_frames, speed_fps)
        """
        with self._lock:
            self._cancel_requested = False

        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        # 音声の長さを取得して、総フレーム数を算出
        # parserのtotal_durationを基準にする
        duration = self.renderer.parser.total_duration
        total_frames = int(duration * fps)
        
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        
        # 出力ディレクトリの作成
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        # FFmpeg コマンドの設定
        # -y: 上書き許可
        # -f rawvideo: 入力フォーマットは生ビデオデータ
        # -pix_fmt rgb24: RGB 24bit形式
        # -s: 解像度
        # -r: フレームレート
        # -i -: 標準入力から受け取る
        # -i audio_path: 音声ファイルの追加
        # -shortest: 入力のいずれかが終了したら処理を終了する (動画・音声の同期)
        cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}",
            "-r", str(fps),
            "-i", "-",  # 標準入力
            "-i", audio_path,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path
        ]

        # Windows特有の設定: コンソールウィンドウを表示しないようにする
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            try:
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            except AttributeError:
                # 定数が存在しない場合のフォールバック（直接整数値を指定: STARTF_USESHOWWINDOW = 0x00000001）
                startupinfo.dwFlags |= 1
            startupinfo.wShowWindow = 0  # SW_HIDE = 0

        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startupinfo=startupinfo
        )

        import time
        start_time = time.time()
        
        success = False
        error_msg = ""
        
        try:
            for frame_idx in range(total_frames):
                # キャンセルチェック
                with self._lock:
                    if self._cancel_requested:
                        error_msg = "Export cancelled by user."
                        break
                
                time_sec = frame_idx / fps
                
                # レンダリング
                img = self.renderer.render_frame(time_sec, width, height)
                
                # 生のRGB24バイト列に変換
                raw_data = img.tobytes()
                
                # FFmpegの標準入力に流し込み
                try:
                    process.stdin.write(raw_data)
                except BrokenPipeError:
                    error_msg = "FFmpeg process write pipe broken."
                    break
                
                # 進捗通知
                if progress_callback:
                    elapsed = time.time() - start_time
                    speed = (frame_idx + 1) / elapsed if elapsed > 0 else 0
                    progress_callback(frame_idx + 1, total_frames, speed)

            # パイプをクローズしてFFmpegの処理完了を待つ
            try:
                process.stdin.close()
            except Exception:
                pass
                
            stdout, stderr = process.communicate()
            
            if process.returncode == 0 and not error_msg:
                success = True
            else:
                if not error_msg:
                    error_msg = stderr.decode('utf-8', errors='replace')
                    
        except Exception as e:
            error_msg = str(e)
            try:
                process.kill()
            except Exception:
                pass
        
        if not success:
            # 失敗時は部分的なファイルを削除
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
            raise RuntimeError(f"Video export failed: {error_msg}")
            
        return output_path

if __name__ == "__main__":
    # レンダラーおよびエクスポーターの簡易結合テスト
    from karaoke_parser import KaraokeParser
    from karaoke_renderer import KaraokeRenderer
    
    excel_path = r"Z:\KaraokeMaker\Resources\karaokemaker_sample.xlsx"
    wav_path = r"Z:\KaraokeMaker\Resources\test.wav"
    output_mp4 = r"C:\Users\kiyot\.gemini\antigravity-ide\brain\a688f196-f54c-4842-b9dd-e3ce74c446cb\scratch\output_test.mp4"
    
    if os.path.exists(excel_path) and os.path.exists(wav_path):
        print("Starting export test...")
        parser = KaraokeParser(excel_path)
        renderer = KaraokeRenderer(parser)
        exporter = VideoExporter(renderer)
        
        def progress(cur, tot, speed):
            pct = (cur / tot) * 100
            print(f"Progress: {cur}/{tot} ({pct:.1f}%) | Speed: {speed:.1f} fps", end="\r")
            
        try:
            exporter.export(output_mp4, wav_path, fps=30, width=1280, height=720, progress_callback=progress)
            print("\nExport completed successfully! Saved to:", output_mp4)
        except Exception as e:
            print("\nExport failed:", e)
    else:
        print("Required test files not found.")
