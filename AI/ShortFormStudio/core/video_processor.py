import os
import subprocess
import tempfile
from typing import Tuple, Optional, Callable, List, Dict, Any
import imageio_ffmpeg
from core.image_processor import create_text_overlay, process_single_image
from core.media_info import check_video_has_audio

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

class VideoProcessor:
    def __init__(self):
        self.current_process: Optional[subprocess.Popen] = None
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True
        if self.current_process and self.current_process.poll() is None:
            try:
                self.current_process.terminate()
            except Exception:
                pass

    def process_video(
        self,
        input_path: str,
        output_path: str,
        target_size: Tuple[int, int] = (1080, 1920),
        resize_mode: str = "blur",  # "blur", "crop", "fit"
        start_time: float = 0.0,
        end_time: Optional[float] = None,
        speed: float = 1.0,
        mute_audio: bool = False,
        rotation: int = 0,
        flip_h: bool = False,
        # Subtitle Parameters (Timeline based or single fallback)
        subtitles: Optional[List[Dict[str, Any]]] = None,
        text: str = "",
        text_position: str = "bottom",
        font_size: int = 56,
        text_color: str = "#FFFFFF",
        bg_style: str = "dark_box",
        # BGM Parameters
        bgm_path: Optional[str] = None,
        bgm_volume: float = 0.5,
        orig_volume: float = 1.0,
        bgm_mode: str = "mix",  # "mix" or "replace"
        has_source_audio: Optional[bool] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> bool:
        self._is_cancelled = False
        tw, th = target_size
        temp_files: List[str] = []

        if has_source_audio is None:
            has_source_audio = check_video_has_audio(input_path)

        try:
            cmd = [FFMPEG_EXE, "-y"]

            # 1. Input 0: Main Video with trimming
            if start_time > 0:
                cmd.extend(["-ss", f"{start_time:.3f}"])
            if end_time is not None and end_time > start_time:
                cmd.extend(["-to", f"{end_time:.3f}"])
                raw_duration = end_time - start_time
            else:
                raw_duration = 10.0

            expected_duration = raw_duration / speed
            cmd.extend(["-i", input_path])
            next_input_idx = 1

            # 2. Timeline Subtitles / Single text overlay PNGs
            sub_overlays = []  # list of (input_idx, t_start, t_end)
            temp_dir = tempfile.gettempdir()

            if subtitles and len(subtitles) > 0:
                for idx, sub in enumerate(subtitles):
                    sub_text = sub.get("text", "").strip()
                    if not sub_text:
                        continue

                    raw_s = sub.get("start_time", 0.0)
                    raw_e = sub.get("end_time", raw_duration)

                    # Map relative to video trim
                    t_start = max(0.0, raw_s - start_time)
                    t_end = raw_e - start_time

                    if t_end <= 0 or t_start >= raw_duration:
                        continue
                    t_end = min(raw_duration, t_end)

                    # Scale by playback speed
                    if speed != 1.0:
                        t_start /= speed
                        t_end /= speed

                    temp_sub_file = os.path.join(temp_dir, f"sf_sub_{os.getpid()}_{idx}.png")
                    overlay_img = create_text_overlay(
                        target_size=target_size,
                        text=sub_text,
                        position=sub.get("position", text_position),
                        font_size=sub.get("font_size", font_size),
                        text_color=sub.get("text_color", text_color),
                        bg_style=sub.get("bg_style", bg_style)
                    )
                    overlay_img.save(temp_sub_file, "PNG")
                    temp_files.append(temp_sub_file)

                    cmd.extend(["-i", temp_sub_file])
                    sub_overlays.append((next_input_idx, t_start, t_end))
                    next_input_idx += 1

            elif text.strip():
                # Single fallback text overlay for the whole video
                temp_sub_file = os.path.join(temp_dir, f"sf_text_{os.getpid()}.png")
                overlay_img = create_text_overlay(
                    target_size=target_size,
                    text=text,
                    position=text_position,
                    font_size=font_size,
                    text_color=text_color,
                    bg_style=bg_style
                )
                overlay_img.save(temp_sub_file, "PNG")
                temp_files.append(temp_sub_file)

                cmd.extend(["-i", temp_sub_file])
                sub_overlays.append((next_input_idx, 0.0, expected_duration))
                next_input_idx += 1

            # 3. Input BGM (Optional)
            bgm_input_idx = None
            has_bgm = bool(bgm_path and os.path.exists(bgm_path))
            if has_bgm:
                cmd.extend(["-stream_loop", "-1", "-i", bgm_path])
                bgm_input_idx = next_input_idx
                next_input_idx += 1

            # 4. Construct Video Filtergraph
            filter_steps = []
            v_in = "[0:v]"

            # Rotation / flip
            rot_filters = []
            if rotation == 90:
                rot_filters.append("transpose=1")
            elif rotation == 180:
                rot_filters.append("transpose=2,transpose=2")
            elif rotation == 270:
                rot_filters.append("transpose=2")
            if flip_h:
                rot_filters.append("hflip")

            if rot_filters:
                rot_str = ",".join(rot_filters)
                filter_steps.append(f"{v_in}{rot_str}[v_rot]")
                v_in = "[v_rot]"

            # 9:16 Resizing modes
            if resize_mode == "crop":
                filter_steps.append(
                    f"{v_in}scale={tw}:{th}:force_original_aspect_ratio=increase,"
                    f"crop={tw}:{th}:(in_w-{tw})/2:(in_h-{th})/2[v_sized]"
                )
            elif resize_mode == "fit":
                filter_steps.append(
                    f"{v_in}scale={tw}:{th}:force_original_aspect_ratio=decrease,"
                    f"pad={tw}:{th}:({tw}-iw)/2:({th}-ih)/2:color=black[v_sized]"
                )
            else:  # "blur"
                filter_steps.append(
                    f"{v_in}split=2[fg_raw][bg_raw];"
                    f"[bg_raw]scale={tw}:{th}:force_original_aspect_ratio=increase,"
                    f"crop={tw}:{th},boxblur=25:5,eq=brightness=-0.2[bg];"
                    f"[fg_raw]scale={tw}:{th}:force_original_aspect_ratio=decrease[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v_sized]"
                )

            v_curr = "[v_sized]"

            # Video Speed
            if speed != 1.0:
                pts_factor = 1.0 / speed
                filter_steps.append(f"{v_curr}setpts={pts_factor:.4f}*PTS[v_speed]")
                v_curr = "[v_speed]"

            # Overlay Subtitles with Timeline Enable
            for s_idx, (in_idx, s_start, s_end) in enumerate(sub_overlays):
                v_next = f"[v_sub_{s_idx}]"
                filter_steps.append(
                    f"{v_curr}[{in_idx}:v]overlay=0:0:enable='between(t,{s_start:.3f},{s_end:.3f})'{v_next}"
                )
                v_curr = v_next

            # 5. Construct Audio Filtergraph
            audio_mapped = False
            a_out_label = None

            if has_bgm:
                fade_start = max(0.1, expected_duration - 1.0)
                bgm_filter = (
                    f"[{bgm_input_idx}:a]volume={bgm_volume:.2f},"
                    f"atrim=0:{expected_duration:.3f},"
                    f"asetpts=PTS-STARTPTS[a_bgm_raw]"
                )
                filter_steps.append(bgm_filter)

                if has_source_audio and not mute_audio and bgm_mode == "mix":
                    orig_filter = f"[0:a]volume={orig_volume:.2f}"
                    if speed != 1.0:
                        orig_filter += f",atempo={speed:.2f}"
                    orig_filter += "[a_orig_raw]"
                    filter_steps.append(orig_filter)

                    filter_steps.append(
                        f"[a_orig_raw][a_bgm_raw]amix=inputs=2:duration=first:dropout_transition=2,"
                        f"afade=t=out:st={fade_start:.3f}:d=1.0[a_mixed]"
                    )
                    a_out_label = "[a_mixed]"
                    audio_mapped = True
                else:
                    filter_steps.append(
                        f"[a_bgm_raw]afade=t=out:st={fade_start:.3f}:d=1.0[a_bgm_out]"
                    )
                    a_out_label = "[a_bgm_out]"
                    audio_mapped = True
            else:
                if not mute_audio and has_source_audio:
                    orig_filter = f"[0:a]volume={orig_volume:.2f}"
                    if speed != 1.0:
                        orig_filter += f",atempo={speed:.2f}"
                    orig_filter += "[a_orig_only]"
                    filter_steps.append(orig_filter)
                    a_out_label = "[a_orig_only]"
                    audio_mapped = True

            # Assemble Filter Complex
            complex_filter = ";".join(filter_steps)
            cmd.extend(["-filter_complex", complex_filter])
            cmd.extend(["-map", v_curr])

            if audio_mapped and a_out_label:
                cmd.extend(["-map", a_out_label, "-c:a", "aac", "-b:a", "192k"])
            else:
                cmd.append("-an")

            # Video Encoding Options
            cmd.extend([
                "-c:v", "libx264",
                "-preset", "faster",
                "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-progress", "pipe:1",
                output_path
            ])

            # Run Process
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0

            self.current_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                universal_newlines=True,
                startupinfo=startupinfo,
                encoding='utf-8',
                errors='replace'
            )

            for line in self.current_process.stdout:
                if self._is_cancelled:
                    self.current_process.kill()
                    return False

                line = line.strip()
                if line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=")[1])
                        curr_sec = us / 1_000_000.0
                        if expected_duration > 0 and progress_callback:
                            percent = min(99.0, (curr_sec / expected_duration) * 100.0)
                            progress_callback(percent, f"{curr_sec:.1f}초 / {expected_duration:.1f}초")
                    except Exception:
                        pass
                elif line == "progress=end":
                    if progress_callback:
                        progress_callback(100.0, "완료")

            ret_code = self.current_process.wait()
            if ret_code == 0 and os.path.exists(output_path):
                if progress_callback:
                    progress_callback(100.0, "완료")
                return True
            return False

        except Exception as e:
            print(f"Error processing video: {e}")
            return False
        finally:
            for f in temp_files:
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception:
                        pass
            self.current_process = None

    def process_image_with_bgm(
        self,
        image_path: str,
        output_path: str,
        bgm_path: str,
        duration: float = 7.0,
        target_size: Tuple[int, int] = (1080, 1920),
        resize_mode: str = "blur",
        rotation: int = 0,
        flip_h: bool = False,
        brightness: float = 1.0,
        contrast: float = 1.0,
        subtitles: Optional[List[Dict[str, Any]]] = None,
        text: str = "",
        text_position: str = "bottom",
        font_size: int = 56,
        text_color: str = "#FFFFFF",
        bg_style: str = "dark_box",
        bgm_volume: float = 0.8,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> bool:
        """Converts a static image + BGM + timeline subtitles into a vertical ShortForm MP4 video."""
        self._is_cancelled = False
        temp_files: List[str] = []

        try:
            # 1. Process base image to 9:16 PNG (without text, subtitles will be overlaid in video)
            temp_dir = tempfile.gettempdir()
            temp_base_img = os.path.join(temp_dir, f"sf_img_base_{os.getpid()}.png")
            img_ok = process_single_image(
                input_path=image_path,
                output_path=temp_base_img,
                target_size=target_size,
                resize_mode=resize_mode,
                rotation=rotation,
                flip_h=flip_h,
                brightness=brightness,
                contrast=contrast,
                text="",  # No static text, timeline subtitles will handle it
                text_position=text_position,
                font_size=font_size,
                text_color=text_color,
                bg_style=bg_style
            )
            if not img_ok or not os.path.exists(temp_base_img):
                return False
            temp_files.append(temp_base_img)

            cmd = [
                FFMPEG_EXE, "-y",
                "-loop", "1", "-t", f"{duration:.3f}", "-i", temp_base_img,
                "-stream_loop", "-1", "-i", bgm_path
            ]
            next_input_idx = 2

            # Subtitle inputs
            sub_overlays = []
            if subtitles and len(subtitles) > 0:
                for idx, sub in enumerate(subtitles):
                    sub_text = sub.get("text", "").strip()
                    if not sub_text:
                        continue
                    t_start = max(0.0, min(duration, sub.get("start_time", 0.0)))
                    t_end = max(0.0, min(duration, sub.get("end_time", duration)))
                    if t_end <= t_start:
                        continue

                    temp_sub_file = os.path.join(temp_dir, f"sf_img_sub_{os.getpid()}_{idx}.png")
                    overlay_img = create_text_overlay(
                        target_size=target_size,
                        text=sub_text,
                        position=sub.get("position", text_position),
                        font_size=sub.get("font_size", font_size),
                        text_color=sub.get("text_color", text_color),
                        bg_style=sub.get("bg_style", bg_style)
                    )
                    overlay_img.save(temp_sub_file, "PNG")
                    temp_files.append(temp_sub_file)

                    cmd.extend(["-i", temp_sub_file])
                    sub_overlays.append((next_input_idx, t_start, t_end))
                    next_input_idx += 1
            elif text.strip():
                temp_sub_file = os.path.join(temp_dir, f"sf_img_text_{os.getpid()}.png")
                overlay_img = create_text_overlay(
                    target_size=target_size,
                    text=text,
                    position=text_position,
                    font_size=font_size,
                    text_color=text_color,
                    bg_style=bg_style
                )
                overlay_img.save(temp_sub_file, "PNG")
                temp_files.append(temp_sub_file)
                cmd.extend(["-i", temp_sub_file])
                sub_overlays.append((next_input_idx, 0.0, duration))
                next_input_idx += 1

            # Construct filter steps
            filter_steps = []
            v_curr = "0:v"
            for s_idx, (in_idx, s_start, s_end) in enumerate(sub_overlays):
                v_next = f"[v_sub_{s_idx}]"
                filter_steps.append(
                    f"[{v_curr}][{in_idx}:v]overlay=0:0:enable='between(t,{s_start:.3f},{s_end:.3f})'{v_next}"
                )
                v_curr = f"v_sub_{s_idx}"

            fade_start = max(0.1, duration - 1.0)
            filter_steps.append(
                f"[1:a]volume={bgm_volume:.2f},atrim=0:{duration:.3f},"
                f"asetpts=PTS-STARTPTS,afade=t=out:st={fade_start:.3f}:d=1.0[a_out]"
            )

            cmd.extend(["-filter_complex", ";".join(filter_steps)])
            if sub_overlays:
                cmd.extend(["-map", f"[{v_curr}]"])
            else:
                cmd.extend(["-map", "0:v"])
            cmd.extend(["-map", "[a_out]"])

            cmd.extend([
                "-c:v", "libx264",
                "-tune", "stillimage",
                "-preset", "faster",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "192k",
                "-progress", "pipe:1",
                output_path
            ])

            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0

            self.current_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                universal_newlines=True,
                startupinfo=startupinfo,
                encoding='utf-8',
                errors='replace'
            )

            for line in self.current_process.stdout:
                if self._is_cancelled:
                    self.current_process.kill()
                    return False
                line = line.strip()
                if line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=")[1])
                        curr_sec = us / 1_000_000.0
                        if duration > 0 and progress_callback:
                            percent = min(99.0, (curr_sec / duration) * 100.0)
                            progress_callback(percent, f"{curr_sec:.1f}초 / {duration:.1f}초")
                    except Exception:
                        pass
                elif line == "progress=end":
                    if progress_callback:
                        progress_callback(100.0, "완료")

            ret_code = self.current_process.wait()
            return ret_code == 0 and os.path.exists(output_path)

        except Exception as e:
            print(f"Error creating image reel with BGM and subtitles: {e}")
            return False
        finally:
            for f in temp_files:
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception:
                        pass
            self.current_process = None
