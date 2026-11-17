import os
import subprocess
import threading
from typing import Optional
import numpy as np
import imageio_ffmpeg

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import pygame

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
SAMPLE_RATE = 44100

class AudioPlayer:
    """
    High-performance audio player for video timeline preview.
    Supports real-time multi-track audio playback (video original audio + BGM),
    instantaneous zero-latency seeking, speed scaling, and independent volume controls.
    """
    def __init__(self):
        self._ensure_mixer()

        # In-memory decoded PCM arrays: shape (N, 2), dtype int16
        self.orig_audio: Optional[np.ndarray] = None
        self.bgm_audio: Optional[np.ndarray] = None

        self.orig_channel = pygame.mixer.Channel(0)
        self.bgm_channel = pygame.mixer.Channel(1)

        # Control states
        self.orig_volume: float = 1.0
        self.bgm_volume: float = 0.5
        self.is_muted: bool = False
        self.bgm_mode: str = "mix"  # "mix" or "replace"
        self.speed: float = 1.0
        self.is_playing: bool = False

        self._lock = threading.Lock()

    def _ensure_mixer(self):
        if not pygame.mixer.get_init():
            try:
                # 44.1kHz stereo, 16-bit signed, small buffer (512) for instant response
                pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=2, buffer=512)
            except Exception as e:
                print(f"Mixer initialization error: {e}")

    def load_media_audio(self, file_path: str):
        """Loads and decodes audio from a video or audio file into memory."""
        def _decode():
            arr = self._decode_to_pcm(file_path)
            with self._lock:
                self.orig_audio = arr

        threading.Thread(target=_decode, daemon=True).start()

    def load_bgm_audio(self, file_path: str):
        """Loads and decodes background music into memory."""
        def _decode():
            arr = self._decode_to_pcm(file_path)
            with self._lock:
                self.bgm_audio = arr

        threading.Thread(target=_decode, daemon=True).start()

    def clear_bgm(self):
        with self._lock:
            self.bgm_audio = None
            self.bgm_channel.stop()

    def clear_media(self):
        with self._lock:
            self.orig_audio = None
            self.orig_channel.stop()

    def _decode_to_pcm(self, file_path: str) -> Optional[np.ndarray]:
        if not file_path or not os.path.exists(file_path):
            return None
        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0

            cmd = [
                FFMPEG_EXE, "-i", file_path,
                "-vn", "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", str(SAMPLE_RATE), "-ac", "2", "pipe:1"
            ]
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo
            )
            raw_bytes = res.stdout
            if not raw_bytes or len(raw_bytes) < 4:
                return None

            arr = np.frombuffer(raw_bytes, dtype=np.int16).reshape(-1, 2)
            return arr
        except Exception as e:
            print(f"Audio decode error for {file_path}: {e}")
            return None

    def play(self, start_time_sec: float = 0.0, speed: float = 1.0):
        with self._lock:
            self.is_playing = True
            self.speed = speed

            self.orig_channel.stop()
            self.bgm_channel.stop()

            start_sample = int(max(0.0, start_time_sec) * SAMPLE_RATE)

            # 1. Original Video Audio
            should_play_orig = (
                self.orig_audio is not None and
                not self.is_muted and
                self.bgm_mode != "replace"
            )
            if should_play_orig:
                if start_sample < len(self.orig_audio):
                    slice_data = self.orig_audio[start_sample:]
                    if speed != 1.0 and speed > 0:
                        indices = np.round(np.arange(0, len(slice_data), speed)).astype(np.int64)
                        indices = indices[indices < len(slice_data)]
                        slice_data = slice_data[indices]

                    if len(slice_data) > 0:
                        try:
                            sound = pygame.sndarray.make_sound(np.ascontiguousarray(slice_data))
                            vol = max(0.0, min(1.0, self.orig_volume))
                            self.orig_channel.set_volume(vol)
                            self.orig_channel.play(sound)
                        except Exception as e:
                            print(f"Error playing orig audio: {e}")

            # 2. BGM Audio
            if self.bgm_audio is not None and len(self.bgm_audio) > 0:
                bgm_sample = start_sample % len(self.bgm_audio)
                slice_data = self.bgm_audio[bgm_sample:]
                if speed != 1.0 and speed > 0:
                    indices = np.round(np.arange(0, len(slice_data), speed)).astype(np.int64)
                    indices = indices[indices < len(slice_data)]
                    slice_data = slice_data[indices]

                if len(slice_data) > 0:
                    try:
                        sound = pygame.sndarray.make_sound(np.ascontiguousarray(slice_data))
                        vol = max(0.0, min(1.0, self.bgm_volume))
                        self.bgm_channel.set_volume(vol)
                        self.bgm_channel.play(sound, loops=-1)
                    except Exception as e:
                        print(f"Error playing BGM: {e}")

    def pause(self):
        with self._lock:
            self.is_playing = False
            self.orig_channel.pause()
            self.bgm_channel.pause()

    def stop(self):
        with self._lock:
            self.is_playing = False
            self.orig_channel.stop()
            self.bgm_channel.stop()

    def seek(self, timestamp_sec: float, speed: float = 1.0):
        if self.is_playing:
            self.play(timestamp_sec, speed=speed)
        else:
            self.stop()

    def update_settings(self, orig_volume: float, bgm_volume: float, is_muted: bool, bgm_mode: str):
        with self._lock:
            self.orig_volume = orig_volume
            self.bgm_volume = bgm_volume
            self.is_muted = is_muted
            self.bgm_mode = bgm_mode

            eff_orig_vol = 0.0 if (self.is_muted or self.bgm_mode == "replace") else max(0.0, min(1.0, self.orig_volume))
            self.orig_channel.set_volume(eff_orig_vol)

            eff_bgm_vol = max(0.0, min(1.0, self.bgm_volume))
            self.bgm_channel.set_volume(eff_bgm_vol)

    def close(self):
        self.stop()
        if pygame.mixer.get_init():
            try:
                pygame.mixer.quit()
            except Exception:
                pass
