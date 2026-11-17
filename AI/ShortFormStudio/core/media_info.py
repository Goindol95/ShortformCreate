import os
import subprocess
import threading
from dataclasses import dataclass
from typing import Optional
from PIL import Image
import cv2
import imageio_ffmpeg

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff', '.gif'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.wma'}

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

@dataclass
class MediaInfo:
    file_path: str
    is_video: bool
    width: int
    height: int
    duration: float  # seconds (0.0 for image)
    fps: float       # 0.0 for image
    total_frames: int
    format_name: str
    file_size_mb: float
    has_audio: bool = False

def check_video_has_audio(file_path: str) -> bool:
    """Checks if video file contains at least one audio stream using FFmpeg."""
    try:
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        res = subprocess.run(
            [FFMPEG_EXE, "-i", file_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            errors='replace',
            startupinfo=startupinfo,
            timeout=5
        )
        for line in res.stderr.splitlines():
            if "Audio:" in line:
                return True
    except Exception:
        pass
    return False

def get_media_info(file_path: str) -> Optional[MediaInfo]:
    if not os.path.exists(file_path):
        return None
    
    ext = os.path.splitext(file_path)[1].lower()
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    
    if ext in IMAGE_EXTENSIONS:
        try:
            with Image.open(file_path) as img:
                w, h = img.size
                return MediaInfo(
                    file_path=file_path,
                    is_video=False,
                    width=w,
                    height=h,
                    duration=0.0,
                    fps=0.0,
                    total_frames=1,
                    format_name=img.format or ext[1:].upper(),
                    file_size_mb=round(file_size_mb, 2),
                    has_audio=False
                )
        except Exception:
            return None
            
    elif ext in VIDEO_EXTENSIONS:
        cap = cv2.VideoCapture(file_path)
        if not cap.isOpened():
            return None
        
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0.0
        cap.release()
        
        has_audio = check_video_has_audio(file_path)
        
        return MediaInfo(
            file_path=file_path,
            is_video=True,
            width=w,
            height=h,
            duration=round(duration, 2),
            fps=round(fps, 2),
            total_frames=total_frames,
            format_name=ext[1:].upper(),
            file_size_mb=round(file_size_mb, 2),
            has_audio=has_audio
        )
        
    return None

class VideoFrameReader:
    """Persistent, thread-safe video frame seeker for smooth timeline playback and scrubbing."""
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.cap = cv2.VideoCapture(file_path)
        self.lock = threading.Lock()

    def get_frame(self, timestamp_sec: float) -> Optional[Image.Image]:
        with self.lock:
            if not self.cap or not self.cap.isOpened():
                return None
            self.cap.set(cv2.CAP_PROP_POS_MSEC, timestamp_sec * 1000.0)
            ret, frame = self.cap.read()
            if not ret or frame is None:
                return None
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            return Image.fromarray(rgb)

    def release(self):
        with self.lock:
            if self.cap and self.cap.isOpened():
                self.cap.release()
                self.cap = None

def extract_video_frame(file_path: str, timestamp_sec: float) -> Optional[Image.Image]:
    """Extracts a frame at timestamp_sec as a PIL RGB Image."""
    cap = cv2.VideoCapture(file_path)
    if not cap.isOpened():
        return None
    
    cap.set(cv2.CAP_PROP_POS_MSEC, timestamp_sec * 1000.0)
    ret, frame = cap.read()
    cap.release()
    
    if not ret or frame is None:
        return None
    
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb_frame)
