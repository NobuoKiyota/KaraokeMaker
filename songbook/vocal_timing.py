"""歌声の出だしを、音源から見つける(歌詞の行の時刻を、実際の歌に合わせるため)。

2mix(歌入りの音源)から Vcless(歌なしの音源)を引くと、歌声だけが残る。その音量の動きから、
「歌い出し」(しばらく無音のあと、声が立ち上がる所)を探して、Excelから出した時刻を、いちばん近い歌い出しに合わせる。
必要なもの: librosa と numpy(pip install librosa)、2mix と Vcless の mp3(Excelと同じフォルダ)。
"""
import glob
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")

HOP = 128
WIN = 512
SR = 22050


class VocalOnsets:
    def __init__(self, folder):
        import librosa

        folder = Path(folder)
        mp3 = [Path(p) for p in glob.glob(str(folder / "*.mp3"))]
        f2 = next((p for p in mp3 if "2mix" in p.name.lower()), None)
        fv = next((p for p in mp3 if "vcless" in p.name.lower()), None)
        if f2 is None or fv is None:
            raise FileNotFoundError("2mix と Vcless の mp3 が、Excelと同じフォルダに必要です")
        y2, _ = librosa.load(str(f2), sr=SR, mono=True)
        yv, _ = librosa.load(str(fv), sr=SR, mono=True)
        n = min(len(y2), len(yv))
        y2, yv = y2[:n], yv[:n]
        # 2つの mp3 の、頭のずれ(エンコーダの遅れ)を、少しだけ探す
        seg = slice(int(SR * 5), int(SR * 25)) if n > SR * 30 else slice(0, n)
        best, lag = 1e9, 0
        for k in range(-48, 49):
            a = np.roll(yv, k)[seg]
            r = float(np.mean((y2[seg] - a) ** 2))
            if r < best:
                best, lag = r, k
        voc = y2 - np.roll(yv, lag)
        frames = (len(voc) - WIN) // HOP
        e = np.empty(frames, dtype=np.float32)
        for i in range(frames):
            seg_i = voc[i * HOP:i * HOP + WIN]
            e[i] = float(np.sqrt(np.mean(seg_i * seg_i)))
        self.dt = HOP / SR
        self.origin = WIN / 2 / SR
        self.env = np.convolve(e, np.ones(3) / 3, mode="same")
        p90 = float(np.percentile(self.env, 90))
        p10 = float(np.percentile(self.env, 10))
        # 歌声がきれいに取れているか: 歌のない所(下位10%)が、歌のある所(上位10%)より、ずっと小さいはず。
        # 2mix と Vcless を書き出した時期が違うなど、合っていないときは、この差が出ない
        self.reliable = p10 < 0.2 * p90
        self.starts = self._phrase_starts(p90) if self.reliable else np.array([])
        self.stats = {"lines": 0, "snapped": 0, "moved": 0.0}

    def _phrase_starts(self, p90):
        env, dt = self.env, self.dt
        hi, lo = 0.30 * p90, 0.08 * p90
        active = env > hi
        gap = max(1, int(0.18 / dt))
        starts = []
        for i in range(1, len(env)):
            if active[i] and not active[i - 1] and not active[max(0, i - gap):i].any():
                j = i
                while j > 0 and i - j < 30 and env[j - 1] > lo:  # 立ち上がりの始まりまで戻る
                    j -= 1
                starts.append(j * dt + self.origin)
        return np.array(starts)

    def refine(self, t_est, before=1.0, after=0.7):
        """見積もりの時刻 t_est(秒)を、近くの歌い出しに合わせる。見つからなければ、そのまま。"""
        self.stats["lines"] += 1
        if not len(self.starts):
            return t_est
        near = self.starts[(self.starts >= t_est - before) & (self.starts <= t_est + after)]
        if not len(near):
            return t_est
        t = float(near[np.argmin(np.abs(near - t_est))])
        self.stats["snapped"] += 1
        self.stats["moved"] += abs(t - t_est)
        return t
