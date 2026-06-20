import sounddevice as sd
import soundfile as sf
import threading
import time

class AudioPlayer:
    def __init__(self):
        self.data = None
        self.fs = None
        self.channels = 1
        
        self.current_frame = 0
        self.playing = False
        self.stream = None
        self.lock = threading.RLock()
        
        self._position_changed_callback = None

    def load(self, filepath):
        with self.lock:
            self.stop()
            self.data, self.fs = sf.read(filepath)
            
            # チャンネル数
            if len(self.data.shape) > 1:
                self.channels = self.data.shape[1]
            else:
                self.channels = 1
                # 1次元配列を2次元に変換 (sounddevice用に)
                self.data = self.data.reshape(-1, 1)
                
            self.current_frame = 0

    def set_position_changed_callback(self, callback):
        self._position_changed_callback = callback

    def _callback(self, outdata, frames, time_info, status):
        if status:
            print("Audio error status:", status)
            
        with self.lock:
            if not self.playing:
                outdata.fill(0)
                return
                
            start = self.current_frame
            end = start + frames
            
            if start >= len(self.data):
                # 再生終了
                outdata.fill(0)
                self.playing = False
                self.current_frame = len(self.data)
                return
                
            if end > len(self.data):
                # 最後の部分
                chunk = self.data[start:]
                outdata[:len(chunk)] = chunk
                outdata[len(chunk):].fill(0)
                self.current_frame = len(self.data)
                self.playing = False
            else:
                outdata[:] = self.data[start:end]
                self.current_frame = end
                
            if self._position_changed_callback:
                # コールバック（再生位置秒数）
                self._position_changed_callback(self.get_time())

    def play(self):
        with self.lock:
            if self.data is None:
                return
            if self.playing:
                return
                
            if self.current_frame >= len(self.data):
                # 最初から再生
                self.current_frame = 0
                
            self.playing = True
            
            if self.stream is None:
                self.stream = sd.OutputStream(
                    samplerate=self.fs,
                    channels=self.channels,
                    callback=self._callback,
                    blocksize=1024
                )
                self.stream.start()

    def pause(self):
        with self.lock:
            self.playing = False

    def stop(self):
        with self.lock:
            self.playing = False
            self.current_frame = 0
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
                self.stream = None

    def seek(self, time_sec):
        with self.lock:
            if self.data is None:
                return
            target_frame = int(time_sec * self.fs)
            self.current_frame = max(0, min(target_frame, len(self.data)))

    def get_time(self):
        if self.data is None or self.fs is None:
            return 0.0
        return self.current_frame / self.fs

    def get_duration(self):
        if self.data is None or self.fs is None:
            return 0.0
        return len(self.data) / self.fs

    def is_playing(self):
        return self.playing

if __name__ == "__main__":
    # 簡易動作テスト
    import os
    wav_path = r"Z:\KaraokeMaker\Resources\test.wav"
    if os.path.exists(wav_path):
        player = AudioPlayer()
        player.load(wav_path)
        print("Loaded. Duration:", player.get_duration(), "sec")
        player.play()
        print("Playing...")
        time.sleep(2)
        print("Time now:", player.get_time())
        player.pause()
        print("Paused. Seeking to 5s...")
        player.seek(5.0)
        player.play()
        time.sleep(2)
        player.stop()
        print("Stopped.")
    else:
        print("Sample wav not found.")
