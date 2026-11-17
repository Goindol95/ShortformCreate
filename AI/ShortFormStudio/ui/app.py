import os
import sys
import threading
import time
from typing import Optional, List, Dict, Any, Tuple
from PIL import Image
import customtkinter as ctk
from tkinter import filedialog, messagebox

from core.media_info import MediaInfo, get_media_info, VideoFrameReader
from core.audio_player import AudioPlayer
from core.image_processor import (
    apply_image_adjustments,
    resize_to_shortform,
    create_text_overlay,
    process_single_image
)
from core.video_processor import VideoProcessor

# Configure CustomTkinter Appearance
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class ShortFormStudioApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("ShortForm Studio - 이미지 & 동영상 숏폼(9:16) 편집기")
        self.geometry("1220x920")
        self.minsize(1100, 800)

        # State Variables
        self.current_media: Optional[MediaInfo] = None
        self.current_bgm_path: Optional[str] = None
        self.current_frame_img: Optional[Image.Image] = None
        self.frame_reader: Optional[VideoFrameReader] = None
        self.audio_player = AudioPlayer()
        self.video_processor = VideoProcessor()
        self.is_exporting = False
        self.last_exported_file: Optional[str] = None

        # Playback Engine Variables
        self.is_playing = False
        self.playback_job = None
        self.last_playback_tick = 0.0
        self.current_timestamp = 0.0
        self.active_sub_highlight_idx: Optional[int] = None

        # Editing Parameters
        self.rotation_val = 0
        self.flip_h_val = False

        # Timeline Subtitles State
        self.subtitles: List[Dict[str, Any]] = []
        self.selected_sub_idx: Optional[int] = None
        self.sub_row_widgets: List[Tuple[Dict[str, Any], Any, Any]] = []

        # Build UI
        self._setup_ui()
        self._setup_shortcuts()
        self._set_default_state()

    def destroy(self):
        self._pause_playback()
        self.audio_player.close()
        if self.frame_reader:
            self.frame_reader.release()
            self.frame_reader = None
        super().destroy()

    def _setup_shortcuts(self):
        def handle_space(event):
            focused = self.focus_get()
            if isinstance(focused, (ctk.CTkEntry, ctk.CTkTextbox)):
                return
            self._toggle_playback()

        self.bind("<space>", handle_space)
        self.bind("<Left>", lambda e: self._step_time(-1.0))
        self.bind("<Right>", lambda e: self._step_time(1.0))

    def _setup_ui(self):
        # Top Header Bar
        header_frame = ctk.CTkFrame(self, height=50, corner_radius=0)
        header_frame.pack(side="top", fill="x", padx=0, pady=0)

        title_lbl = ctk.CTkLabel(
            header_frame,
            text="🎬 ShortForm Studio  |  9:16 숏폼 리사이즈 & 실시간 오디오/비디오 미리보기",
            font=ctk.CTkFont(size=17, weight="bold")
        )
        title_lbl.pack(side="left", padx=20, pady=10)

        theme_btn = ctk.CTkSegmentedButton(
            header_frame,
            values=["🌙 다크", "☀️ 라이트"],
            command=self._toggle_theme
        )
        theme_btn.set("🌙 다크")
        theme_btn.pack(side="right", padx=20, pady=10)

        # Main Layout (Split Left: Preview & Playback / Right: Controls)
        main_container = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        main_container.pack(side="top", fill="both", expand=True, padx=15, pady=10)

        # Left Column: Preview & Timeline Playback
        self.left_frame = ctk.CTkFrame(main_container, width=440)
        self.left_frame.pack(side="left", fill="both", expand=False, padx=(0, 10), pady=0)
        self._setup_preview_panel(self.left_frame)

        # Right Column: Controls (Scrollable)
        self.right_frame = ctk.CTkScrollableFrame(main_container, width=720)
        self.right_frame.pack(side="right", fill="both", expand=True, padx=(5, 0), pady=0)
        self._setup_controls_panel(self.right_frame)

    def _toggle_theme(self, choice):
        if "라이트" in choice:
            ctk.set_appearance_mode("Light")
        else:
            ctk.set_appearance_mode("Dark")

    def _setup_preview_panel(self, parent):
        top_prev_frame = ctk.CTkFrame(parent, fg_color="transparent")
        top_prev_frame.pack(fill="x", padx=15, pady=(10, 4))

        prev_title = ctk.CTkLabel(
            top_prev_frame,
            text="📱 실시간 숏폼(9:16) 화면 & 소리 미리보기",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        prev_title.pack(side="left")

        self.ratio_badge = ctk.CTkLabel(
            top_prev_frame,
            text="1080 x 1920",
            font=ctk.CTkFont(size=11),
            fg_color=("gray80", "gray25"),
            corner_radius=6,
            padx=8,
            pady=2
        )
        self.ratio_badge.pack(side="right")

        # 9:16 Preview Box Container (Phone mockup aspect: 324 x 576)
        self.preview_box = ctk.CTkFrame(
            parent,
            width=324,
            height=576,
            corner_radius=12,
            border_width=2,
            border_color=("gray65", "gray35"),
            fg_color=("gray90", "gray15")
        )
        self.preview_box.pack(padx=20, pady=6)
        self.preview_box.pack_propagate(False)

        self.preview_label = ctk.CTkLabel(
            self.preview_box,
            text="미디어 파일을 불러오면\n화면과 소리(음악/오디오)가\n타임라인과 함께 실시간 재생됩니다.",
            font=ctk.CTkFont(size=13),
            text_color=("gray50", "gray50")
        )
        self.preview_label.pack(expand=True, fill="both")

        # Timeline & Playback Controller
        self.timeline_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.timeline_frame.pack(fill="x", padx=15, pady=(4, 4))

        # Row 1: Time display & Loop Checkbox
        time_info_frame = ctk.CTkFrame(self.timeline_frame, fg_color="transparent")
        time_info_frame.pack(fill="x", pady=(0, 2))

        self.time_label = ctk.CTkLabel(
            time_info_frame,
            text="00:00.0 / 00:00.0",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.time_label.pack(side="left")

        self.loop_var = ctk.BooleanVar(value=True)
        self.loop_chk = ctk.CTkCheckBox(
            time_info_frame,
            text="🔁 반복 재생",
            variable=self.loop_var,
            font=ctk.CTkFont(size=11),
            width=70
        )
        self.loop_chk.pack(side="right")

        # Row 2: Timeline Slider
        self.time_slider = ctk.CTkSlider(
            self.timeline_frame,
            from_=0,
            to=100,
            command=self._on_timeline_slider_change
        )
        self.time_slider.pack(fill="x", pady=4)
        self.time_slider.set(0)

        # Row 3: Playback Controls (Jump, Step Back, Play/Pause, Step Fwd)
        play_ctrl_row = ctk.CTkFrame(self.timeline_frame, fg_color="transparent")
        play_ctrl_row.pack(fill="x", pady=2)

        self.jump_start_btn = ctk.CTkButton(
            play_ctrl_row, text="⏮️", width=36, height=32,
            font=ctk.CTkFont(size=12),
            fg_color=("gray75", "gray30"),
            command=self._jump_to_start
        )
        self.jump_start_btn.pack(side="left", padx=2)

        self.step_back_btn = ctk.CTkButton(
            play_ctrl_row, text="⏪ 1초", width=55, height=32,
            font=ctk.CTkFont(size=11),
            fg_color=("gray75", "gray30"),
            command=lambda: self._step_time(-1.0)
        )
        self.step_back_btn.pack(side="left", padx=2)

        self.play_btn = ctk.CTkButton(
            play_ctrl_row, text="▶ 재생 (Space)", height=34,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#1f6aa5",
            hover_color="#144870",
            command=self._toggle_playback
        )
        self.play_btn.pack(side="left", fill="x", expand=True, padx=4)

        self.step_fwd_btn = ctk.CTkButton(
            play_ctrl_row, text="1초 ⏩", width=55, height=32,
            font=ctk.CTkFont(size=11),
            fg_color=("gray75", "gray30"),
            command=lambda: self._step_time(1.0)
        )
        self.step_fwd_btn.pack(side="left", padx=2)

        # Row 4: Quick Timeline Markers
        marker_row = ctk.CTkFrame(self.timeline_frame, fg_color="transparent")
        marker_row.pack(fill="x", pady=(2, 4))

        self.set_start_btn = ctk.CTkButton(
            marker_row,
            text="🚩 영상시작",
            font=ctk.CTkFont(size=11),
            width=75,
            height=26,
            command=self._set_current_as_start
        )
        self.set_start_btn.pack(side="left", padx=2)

        self.set_end_btn = ctk.CTkButton(
            marker_row,
            text="🏁 영상종료",
            font=ctk.CTkFont(size=11),
            width=75,
            height=26,
            command=self._set_current_as_end
        )
        self.set_end_btn.pack(side="left", padx=2)

        self.set_sub_start_btn = ctk.CTkButton(
            marker_row,
            text="⏱️ 자막시작에 반영",
            font=ctk.CTkFont(size=11),
            height=26,
            fg_color="#2b7a78",
            hover_color="#17252a",
            command=self._set_current_as_sub_start
        )
        self.set_sub_start_btn.pack(side="left", fill="x", expand=True, padx=2)

        # Media Info Bar
        self.info_lbl = ctk.CTkLabel(
            parent,
            text="선택된 파일 없음",
            font=ctk.CTkFont(size=11),
            text_color=("gray40", "gray60")
        )
        self.info_lbl.pack(fill="x", padx=15, pady=(4, 8))

    def _setup_controls_panel(self, parent):
        # 1. File Selection Section
        sec1 = self._create_section(parent, "📁 미디어 파일 불러오기")

        file_btn_row = ctk.CTkFrame(sec1, fg_color="transparent")
        file_btn_row.pack(fill="x", padx=12, pady=8)

        open_file_btn = ctk.CTkButton(
            file_btn_row,
            text="📂 이미지 / 동영상 파일 열기",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=36,
            command=self._open_file_dialog
        )
        open_file_btn.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.file_path_lbl = ctk.CTkLabel(
            sec1,
            text="지원 포맷: MP4, MOV, AVI, WEBM, JPG, PNG, WEBP 등",
            font=ctk.CTkFont(size=11),
            text_color=("gray45", "gray55"),
            anchor="w"
        )
        self.file_path_lbl.pack(fill="x", padx=14, pady=(0, 6))

        # 2. Short-form Target & Resize Mode
        sec2 = self._create_section(parent, "📐 숏폼(9:16) 리사이즈 설정")

        res_row = ctk.CTkFrame(sec2, fg_color="transparent")
        res_row.pack(fill="x", padx=12, pady=6)

        ctk.CTkLabel(res_row, text="출력 해상도:", font=ctk.CTkFont(size=12, weight="bold"), width=90, anchor="w").pack(side="left")
        self.res_var = ctk.StringVar(value="1080 x 1920 (FHD)")
        self.res_menu = ctk.CTkOptionMenu(
            res_row,
            values=["1080 x 1920 (FHD - 권장)", "720 x 1280 (HD - 빠른 변환)", "2160 x 3840 (4K - 최고화질)"],
            variable=self.res_var,
            command=self._on_setting_changed
        )
        self.res_menu.pack(side="left", fill="x", expand=True)

        fit_row = ctk.CTkFrame(sec2, fg_color="transparent")
        fit_row.pack(fill="x", padx=12, pady=(4, 10))

        ctk.CTkLabel(fit_row, text="맞춤 모드:", font=ctk.CTkFont(size=12, weight="bold"), width=90, anchor="w").pack(side="left")
        self.mode_var = ctk.StringVar(value="blur")
        self.mode_segmented = ctk.CTkSegmentedButton(
            fit_row,
            values=["🌟 블러 배경 채우기", "✂️ 중앙 크롭", "⬛ 레터박스"],
            command=self._on_mode_changed
        )
        self.mode_segmented.set("🌟 블러 배경 채우기")
        self.mode_segmented.pack(side="left", fill="x", expand=True)

        # 3. Video Editing Section (Trim, Speed, Audio)
        self.video_sec = self._create_section(parent, "🎞️ 동영상 편집 (트리밍 & 재생속도 & 음소거)")

        trim_row = ctk.CTkFrame(self.video_sec, fg_color="transparent")
        trim_row.pack(fill="x", padx=12, pady=6)

        ctk.CTkLabel(trim_row, text="영상 시작(초):", font=ctk.CTkFont(size=12), width=90, anchor="w").pack(side="left")
        self.start_time_entry = ctk.CTkEntry(trim_row, width=80, placeholder_text="0.0")
        self.start_time_entry.insert(0, "0.0")
        self.start_time_entry.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(trim_row, text="영상 종료(초):", font=ctk.CTkFont(size=12), width=90, anchor="w").pack(side="left")
        self.end_time_entry = ctk.CTkEntry(trim_row, width=80, placeholder_text="전체")
        self.end_time_entry.pack(side="left")

        speed_row = ctk.CTkFrame(self.video_sec, fg_color="transparent")
        speed_row.pack(fill="x", padx=12, pady=6)

        ctk.CTkLabel(speed_row, text="재생 속도:", font=ctk.CTkFont(size=12), width=90, anchor="w").pack(side="left")
        self.speed_var = ctk.StringVar(value="1.0x (보통)")
        self.speed_menu = ctk.CTkOptionMenu(
            speed_row,
            values=["0.5x (느리게)", "0.75x", "1.0x (보통)", "1.25x", "1.5x (빠르게)", "2.0x (2배속)"],
            variable=self.speed_var,
            width=130,
            command=self._on_speed_changed
        )
        self.speed_menu.pack(side="left", padx=(0, 20))

        self.mute_var = ctk.BooleanVar(value=False)
        self.mute_chk = ctk.CTkCheckBox(
            speed_row,
            text="🔇 원본 소리 끄기 (음소거)",
            variable=self.mute_var,
            command=self._sync_audio_settings
        )
        self.mute_chk.pack(side="left")

        # 4. Background Music (BGM) Section
        self.bgm_sec = self._create_section(parent, "🎵 백그라운드 음악(BGM) 추가 & 볼륨 믹싱")

        bgm_btn_row = ctk.CTkFrame(self.bgm_sec, fg_color="transparent")
        bgm_btn_row.pack(fill="x", padx=12, pady=6)

        self.choose_bgm_btn = ctk.CTkButton(
            bgm_btn_row,
            text="🎵 BGM 음악 파일 선택 (MP3/WAV/M4A)",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=210,
            command=self._choose_bgm_file
        )
        self.choose_bgm_btn.pack(side="left", padx=(0, 10))

        self.remove_bgm_btn = ctk.CTkButton(
            bgm_btn_row,
            text="✕ 제거",
            width=65,
            fg_color=("gray70", "gray30"),
            state="disabled",
            command=self._remove_bgm_file
        )
        self.remove_bgm_btn.pack(side="left")

        self.bgm_path_lbl = ctk.CTkLabel(
            self.bgm_sec,
            text="선택된 BGM 없음 (BGM을 넣으려면 위 버튼을 클릭하세요)",
            font=ctk.CTkFont(size=11),
            text_color=("gray45", "gray55"),
            anchor="w"
        )
        self.bgm_path_lbl.pack(fill="x", padx=14, pady=(0, 4))

        self.bgm_mode_frame = ctk.CTkFrame(self.bgm_sec, fg_color="transparent")
        self.bgm_mode_frame.pack(fill="x", padx=12, pady=4)

        ctk.CTkLabel(self.bgm_mode_frame, text="믹싱 방식:", font=ctk.CTkFont(size=12), width=75, anchor="w").pack(side="left")
        self.bgm_mode_menu = ctk.CTkSegmentedButton(
            self.bgm_mode_frame,
            values=["🎚️ 원본과 믹싱 (추천)", "🔄 원본 오디오 BGM으로 대체"],
            command=lambda val: self._sync_audio_settings()
        )
        self.bgm_mode_menu.set("🎚️ 원본과 믹싱 (추천)")
        self.bgm_mode_menu.pack(side="left", fill="x", expand=True)

        self.vol_frame = ctk.CTkFrame(self.bgm_sec, fg_color="transparent")
        self.vol_frame.pack(fill="x", padx=12, pady=(4, 6))

        bgm_vol_row = ctk.CTkFrame(self.vol_frame, fg_color="transparent")
        bgm_vol_row.pack(fill="x", pady=2)
        self.bgm_vol_lbl = ctk.CTkLabel(bgm_vol_row, text="BGM 볼륨: 50%", font=ctk.CTkFont(size=11), width=110, anchor="w")
        self.bgm_vol_lbl.pack(side="left")
        self.bgm_vol_slider = ctk.CTkSlider(
            bgm_vol_row, from_=0.0, to=1.5, number_of_steps=30,
            command=self._on_bgm_vol_slider
        )
        self.bgm_vol_slider.set(0.5)
        self.bgm_vol_slider.pack(side="left", fill="x", expand=True)

        self.orig_vol_row = ctk.CTkFrame(self.vol_frame, fg_color="transparent")
        self.orig_vol_row.pack(fill="x", pady=2)
        self.orig_vol_lbl = ctk.CTkLabel(self.orig_vol_row, text="원본 오디오: 100%", font=ctk.CTkFont(size=11), width=110, anchor="w")
        self.orig_vol_lbl.pack(side="left")
        self.orig_vol_slider = ctk.CTkSlider(
            self.orig_vol_row, from_=0.0, to=1.5, number_of_steps=30,
            command=self._on_orig_vol_slider
        )
        self.orig_vol_slider.set(1.0)
        self.orig_vol_slider.pack(side="left", fill="x", expand=True)

        self.img_reel_frame = ctk.CTkFrame(self.bgm_sec, fg_color="transparent")
        self.img_reel_frame.pack(fill="x", padx=12, pady=(4, 6))
        ctk.CTkLabel(self.img_reel_frame, text="📸 이미지 숏폼 영상 길이:", font=ctk.CTkFont(size=12), width=150, anchor="w").pack(side="left")
        self.img_dur_menu = ctk.CTkSegmentedButton(
            self.img_reel_frame,
            values=["5초", "7초 (권장)", "10초", "15초"],
            command=lambda val: self._on_image_duration_changed()
        )
        self.img_dur_menu.set("7초 (권장)")
        self.img_dur_menu.pack(side="left", fill="x", expand=True)
        self.img_reel_frame.pack_forget()

        # 5. Timeline Subtitles Section
        sec5 = self._create_section(parent, "✍️ 타임라인 자막 추가 & 편집 (구간별 자막)")

        sub_text_row = ctk.CTkFrame(sec5, fg_color="transparent")
        sub_text_row.pack(fill="x", padx=12, pady=(6, 4))

        ctk.CTkLabel(sub_text_row, text="자막 내용:", font=ctk.CTkFont(size=12, weight="bold"), width=75, anchor="w").pack(side="left")
        self.sub_text_entry = ctk.CTkEntry(
            sub_text_row,
            placeholder_text="자막 문구를 입력하세요 (예: 오늘의 핵심 요약!)"
        )
        self.sub_text_entry.pack(side="left", fill="x", expand=True)
        self.sub_text_entry.bind("<KeyRelease>", lambda e: self._update_preview())

        time_range_row = ctk.CTkFrame(sec5, fg_color="transparent")
        time_range_row.pack(fill="x", padx=12, pady=4)

        ctk.CTkLabel(time_range_row, text="시작(초):", font=ctk.CTkFont(size=12), width=55, anchor="w").pack(side="left")
        self.sub_start_entry = ctk.CTkEntry(time_range_row, width=65)
        self.sub_start_entry.insert(0, "0.0")
        self.sub_start_entry.pack(side="left", padx=(0, 4))

        sub_start_btn = ctk.CTkButton(
            time_range_row,
            text="⏱️ 현재위치",
            width=70,
            height=26,
            font=ctk.CTkFont(size=11),
            command=self._set_current_as_sub_start
        )
        sub_start_btn.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(time_range_row, text="종료(초):", font=ctk.CTkFont(size=12), width=55, anchor="w").pack(side="left")
        self.sub_end_entry = ctk.CTkEntry(time_range_row, width=65)
        self.sub_end_entry.insert(0, "3.0")
        self.sub_end_entry.pack(side="left", padx=(0, 4))

        sub_end_btn = ctk.CTkButton(
            time_range_row,
            text="⏱️ 현재위치",
            width=70,
            height=26,
            font=ctk.CTkFont(size=11),
            command=self._set_current_as_sub_end
        )
        sub_end_btn.pack(side="left", padx=(0, 10))

        plus2_btn = ctk.CTkButton(
            time_range_row, text="+2초", width=42, height=26, font=ctk.CTkFont(size=11),
            fg_color=("gray75", "gray25"),
            command=lambda: self._quick_add_duration(2.0)
        )
        plus2_btn.pack(side="left", padx=2)

        plus3_btn = ctk.CTkButton(
            time_range_row, text="+3초", width=42, height=26, font=ctk.CTkFont(size=11),
            fg_color=("gray75", "gray25"),
            command=lambda: self._quick_add_duration(3.0)
        )
        plus3_btn.pack(side="left", padx=2)

        sub_style_row = ctk.CTkFrame(sec5, fg_color="transparent")
        sub_style_row.pack(fill="x", padx=12, pady=4)

        ctk.CTkLabel(sub_style_row, text="위치:", font=ctk.CTkFont(size=12), width=40, anchor="w").pack(side="left")
        self.sub_pos_menu = ctk.CTkSegmentedButton(
            sub_style_row,
            values=["상단", "중앙", "하단"],
            command=self._on_setting_changed
        )
        self.sub_pos_menu.set("하단")
        self.sub_pos_menu.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(sub_style_row, text="스타일:", font=ctk.CTkFont(size=12), width=50, anchor="w").pack(side="left")
        self.sub_style_menu = ctk.CTkOptionMenu(
            sub_style_row,
            values=["어두운 박스", "노란색 박스", "외곽선", "배경 없음"],
            command=self._on_setting_changed,
            width=115
        )
        self.sub_style_menu.set("어두운 박스")
        self.sub_style_menu.pack(side="left", padx=(0, 15))

        ctk.CTkLabel(sub_style_row, text="크기:", font=ctk.CTkFont(size=12), width=40, anchor="w").pack(side="left")
        self.sub_size_menu = ctk.CTkSegmentedButton(
            sub_style_row,
            values=["작게", "보통", "크게"],
            command=self._on_setting_changed
        )
        self.sub_size_menu.set("보통")
        self.sub_size_menu.pack(side="left")

        sub_action_row = ctk.CTkFrame(sec5, fg_color="transparent")
        sub_action_row.pack(fill="x", padx=12, pady=(6, 8))

        self.add_sub_btn = ctk.CTkButton(
            sub_action_row,
            text="➕ 타임라인에 자막 추가",
            font=ctk.CTkFont(size=12, weight="bold"),
            height=32,
            fg_color="#2b7a78",
            hover_color="#17252a",
            command=self._save_subtitle
        )
        self.add_sub_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.cancel_sub_edit_btn = ctk.CTkButton(
            sub_action_row,
            text="입력 초기화",
            width=90,
            height=32,
            fg_color=("gray70", "gray30"),
            command=self._clear_sub_inputs
        )
        self.cancel_sub_edit_btn.pack(side="left")

        list_header_row = ctk.CTkFrame(sec5, fg_color="transparent")
        list_header_row.pack(fill="x", padx=12, pady=(4, 2))

        self.sub_count_lbl = ctk.CTkLabel(
            list_header_row,
            text="📋 등록된 자막 목록 (0개)",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.sub_count_lbl.pack(side="left")

        clear_all_subs_btn = ctk.CTkButton(
            list_header_row,
            text="전체 삭제",
            width=70,
            height=22,
            font=ctk.CTkFont(size=11),
            fg_color=("gray75", "gray35"),
            command=self._clear_all_subtitles
        )
        clear_all_subs_btn.pack(side="right")

        self.sub_list_frame = ctk.CTkScrollableFrame(sec5, height=130, corner_radius=6)
        self.sub_list_frame.pack(fill="x", padx=12, pady=(2, 8))

        self._render_subtitle_list()

        # 6. Transform & Adjustments (Rotate, Flip, Brightness, Contrast)
        sec6 = self._create_section(parent, "🎨 화면 보정 & 회전")

        trans_row = ctk.CTkFrame(sec6, fg_color="transparent")
        trans_row.pack(fill="x", padx=12, pady=6)

        rotate_btn = ctk.CTkButton(
            trans_row,
            text="🔄 90° 시계방향 회전",
            width=130,
            command=self._on_rotate_clicked
        )
        rotate_btn.pack(side="left", padx=(0, 10))

        flip_btn = ctk.CTkButton(
            trans_row,
            text="↔️ 좌우 반전",
            width=110,
            command=self._on_flip_clicked
        )
        flip_btn.pack(side="left", padx=(0, 15))

        reset_trans_btn = ctk.CTkButton(
            trans_row,
            text="초기화",
            width=70,
            fg_color=("gray70", "gray30"),
            command=self._reset_transform
        )
        reset_trans_btn.pack(side="left")

        adj_row = ctk.CTkFrame(sec6, fg_color="transparent")
        adj_row.pack(fill="x", padx=12, pady=(4, 10))

        ctk.CTkLabel(adj_row, text="밝기:", font=ctk.CTkFont(size=12), width=45, anchor="w").pack(side="left")
        self.bright_slider = ctk.CTkSlider(adj_row, from_=0.5, to=1.5, number_of_steps=20, command=self._on_setting_changed)
        self.bright_slider.set(1.0)
        self.bright_slider.pack(side="left", fill="x", expand=True, padx=(0, 15))

        ctk.CTkLabel(adj_row, text="대비:", font=ctk.CTkFont(size=12), width=45, anchor="w").pack(side="left")
        self.contrast_slider = ctk.CTkSlider(adj_row, from_=0.5, to=1.5, number_of_steps=20, command=self._on_setting_changed)
        self.contrast_slider.set(1.0)
        self.contrast_slider.pack(side="left", fill="x", expand=True)

        # 7. Export Section
        sec7 = self._create_section(parent, "🚀 저장 및 숏폼 내보내기")

        save_dir_row = ctk.CTkFrame(sec7, fg_color="transparent")
        save_dir_row.pack(fill="x", padx=12, pady=6)

        ctk.CTkLabel(save_dir_row, text="저장 폴더:", font=ctk.CTkFont(size=12, weight="bold"), width=75, anchor="w").pack(side="left")
        default_save_dir = os.path.join(os.path.expanduser("~"), "Desktop")
        self.save_dir_entry = ctk.CTkEntry(save_dir_row)
        self.save_dir_entry.insert(0, default_save_dir)
        self.save_dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        choose_dir_btn = ctk.CTkButton(
            save_dir_row,
            text="폴더 변경",
            width=80,
            command=self._choose_save_dir
        )
        choose_dir_btn.pack(side="left")

        action_row = ctk.CTkFrame(sec7, fg_color="transparent")
        action_row.pack(fill="x", padx=12, pady=(10, 6))

        self.export_btn = ctk.CTkButton(
            action_row,
            text="⚡ 숏폼(9:16) 변환 및 내보내기",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=42,
            fg_color="#1f6aa5",
            hover_color="#144870",
            command=self._start_export
        )
        self.export_btn.pack(side="left", fill="x", expand=True, padx=(0, 10))

        self.cancel_btn = ctk.CTkButton(
            action_row,
            text="취소",
            font=ctk.CTkFont(size=13),
            height=42,
            width=80,
            fg_color=("gray60", "gray35"),
            state="disabled",
            command=self._cancel_export
        )
        self.cancel_btn.pack(side="left")

        self.progress_bar = ctk.CTkProgressBar(sec7)
        self.progress_bar.pack(fill="x", padx=12, pady=6)
        self.progress_bar.set(0)

        prog_info_row = ctk.CTkFrame(sec7, fg_color="transparent")
        prog_info_row.pack(fill="x", padx=12, pady=(0, 8))

        self.progress_status_lbl = ctk.CTkLabel(
            prog_info_row,
            text="대기 중...",
            font=ctk.CTkFont(size=12),
            text_color=("gray50", "gray50")
        )
        self.progress_status_lbl.pack(side="left")

        self.open_result_btn = ctk.CTkButton(
            prog_info_row,
            text="📂 결과물 폴더 열기",
            font=ctk.CTkFont(size=11),
            height=26,
            width=120,
            state="disabled",
            command=self._open_result_folder
        )
        self.open_result_btn.pack(side="right")

    def _create_section(self, parent, title: str) -> ctk.CTkFrame:
        sec = ctk.CTkFrame(parent, corner_radius=8)
        sec.pack(fill="x", padx=5, pady=6)

        hdr = ctk.CTkLabel(
            sec,
            text=title,
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w"
        )
        hdr.pack(fill="x", padx=12, pady=(8, 4))
        return sec

    def _set_default_state(self):
        self.rotation_val = 0
        self.flip_h_val = False

    # Audio Sync Settings Helper
    def _sync_audio_settings(self, *args):
        mode_val = "replace" if "대체" in self.bgm_mode_menu.get() else "mix"
        self.audio_player.update_settings(
            orig_volume=self.orig_vol_slider.get(),
            bgm_volume=self.bgm_vol_slider.get(),
            is_muted=self.mute_var.get(),
            bgm_mode=mode_val
        )

    # Playback Controls Engine with Audio Sync
    def _toggle_playback(self):
        if not self.current_media:
            return
        if self.is_playing:
            self._pause_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        self.is_playing = True
        self.play_btn.configure(text="⏸️ 일시정지 (Space)", fg_color="#d90429", hover_color="#9a031e")
        self.last_playback_tick = time.time()

        # Start synchronized audio
        speed_val = self._get_speed_multiplier()
        self._sync_audio_settings()
        self.audio_player.play(self.current_timestamp, speed=speed_val)

        self._playback_loop()

    def _pause_playback(self):
        self.is_playing = False
        if self.playback_job:
            self.after_cancel(self.playback_job)
            self.playback_job = None
        self.play_btn.configure(text="▶ 재생 (Space)", fg_color="#1f6aa5", hover_color="#144870")
        self.audio_player.pause()

    def _jump_to_start(self):
        self.current_timestamp = 0.0
        max_dur = self._get_max_duration()
        self.time_slider.set(0)
        self._update_time_label(0.0, max_dur)
        self.audio_player.seek(0.0, speed=self._get_speed_multiplier())
        self._render_current_frame()

    def _step_time(self, delta: float):
        max_dur = self._get_max_duration()
        self.current_timestamp = max(0.0, min(max_dur, self.current_timestamp + delta))
        self.time_slider.set(self.current_timestamp)
        self._update_time_label(self.current_timestamp, max_dur)
        self.audio_player.seek(self.current_timestamp, speed=self._get_speed_multiplier())
        self._render_current_frame()

    def _on_speed_changed(self, choice):
        if self.is_playing:
            speed_val = self._get_speed_multiplier()
            self.audio_player.play(self.current_timestamp, speed=speed_val)

    def _get_max_duration(self) -> float:
        if not self.current_media:
            return 1.0
        if self.current_media.is_video:
            return max(0.1, self.current_media.duration)
        else:
            dur_str = self.img_dur_menu.get()
            for d in [5, 7, 10, 15]:
                if f"{d}초" in dur_str:
                    return float(d)
            return 7.0

    def _get_speed_multiplier(self) -> float:
        speed_str = self.speed_var.get()
        for s in [0.5, 0.75, 1.25, 1.5, 2.0]:
            if f"{s}x" in speed_str:
                return s
        return 1.0

    def _playback_loop(self):
        if not self.is_playing:
            return

        now = time.time()
        dt = now - self.last_playback_tick
        self.last_playback_tick = now

        speed = self._get_speed_multiplier()
        self.current_timestamp += dt * speed

        max_dur = self._get_max_duration()
        if self.current_timestamp >= max_dur:
            if self.loop_var.get():
                self.current_timestamp = 0.0
                self.audio_player.play(0.0, speed=speed)
            else:
                self.current_timestamp = max_dur
                self._pause_playback()
                self.time_slider.set(self.current_timestamp)
                self._update_time_label(self.current_timestamp, max_dur)
                self._render_current_frame()
                return

        self.time_slider.set(self.current_timestamp)
        self._update_time_label(self.current_timestamp, max_dur)

        self._render_current_frame()

        # Schedule next tick (~30 FPS = 33ms)
        self.playback_job = self.after(33, self._playback_loop)

    def _render_current_frame(self):
        if not self.current_media:
            return

        if self.current_media.is_video and self.frame_reader:
            frame = self.frame_reader.get_frame(self.current_timestamp)
            if frame:
                self.current_frame_img = frame

        self._update_preview()

    # BGM Handlers
    def _choose_bgm_file(self):
        file_types = [
            ("오디오 음악 파일", "*.mp3;*.wav;*.m4a;*.aac;*.flac;*.ogg;*.wma"),
            ("모든 파일", "*.*")
        ]
        path = filedialog.askopenfilename(filetypes=file_types)
        if path:
            self.current_bgm_path = path
            self.audio_player.load_bgm_audio(path)
            self._sync_audio_settings()

            fname = os.path.basename(path)
            fsize = round(os.path.getsize(path) / (1024 * 1024), 2)
            self.bgm_path_lbl.configure(
                text=f"선택된 BGM: {fname} ({fsize}MB)",
                text_color=("#1f6aa5", "#3a9fdc")
            )
            self.remove_bgm_btn.configure(state="normal")
            self._update_media_ui_modes()

    def _remove_bgm_file(self):
        self.current_bgm_path = None
        self.audio_player.clear_bgm()
        self.bgm_path_lbl.configure(
            text="선택된 BGM 없음 (BGM을 넣으려면 위 버튼을 클릭하세요)",
            text_color=("gray45", "gray55")
        )
        self.remove_bgm_btn.configure(state="disabled")
        self._update_media_ui_modes()

    def _on_bgm_vol_slider(self, val):
        pct = int(float(val) * 100)
        self.bgm_vol_lbl.configure(text=f"BGM 볼륨: {pct}%")
        self._sync_audio_settings()

    def _on_orig_vol_slider(self, val):
        pct = int(float(val) * 100)
        self.orig_vol_lbl.configure(text=f"원본 오디오: {pct}%")
        self._sync_audio_settings()

    def _on_image_duration_changed(self):
        max_dur = self._get_max_duration()
        self.time_slider.configure(from_=0, to=max_dur)
        self.time_slider.set(0)
        self.current_timestamp = 0.0
        self.audio_player.seek(0.0, speed=self._get_speed_multiplier())
        self._update_time_label(0.0, max_dur)
        self._update_preview()

    def _update_media_ui_modes(self):
        if not self.current_media:
            return

        if self.current_media.is_video:
            self.video_sec.pack(fill="x", padx=5, pady=6)
            self.orig_vol_row.pack(fill="x", pady=2)
            self.bgm_mode_frame.pack(fill="x", padx=12, pady=4)
            self.img_reel_frame.pack_forget()
            self.export_btn.configure(text="⚡ 숏폼(9:16) 동영상 변환 및 내보내기")
        else:
            self.video_sec.pack_forget()
            self.orig_vol_row.pack_forget()
            self.bgm_mode_frame.pack_forget()

            if self.current_bgm_path or len(self.subtitles) > 0:
                self.img_reel_frame.pack(fill="x", padx=12, pady=(4, 6))
                self.export_btn.configure(text="🎬 BGM+자막 포함 숏폼 영상(MP4)으로 내보내기")
            else:
                self.img_reel_frame.pack_forget()
                self.export_btn.configure(text="⚡ 숏폼(9:16) 이미지 변환 및 내보내기")

    # File Opening & Handling
    def _open_file_dialog(self):
        file_types = [
            ("모든 미디어 파일", "*.mp4;*.mov;*.avi;*.mkv;*.webm;*.jpg;*.jpeg;*.png;*.webp;*.bmp"),
            ("동영상 파일", "*.mp4;*.mov;*.avi;*.mkv;*.webm;*.flv;*.wmv"),
            ("이미지 파일", "*.jpg;*.jpeg;*.png;*.webp;*.bmp;*.tiff"),
            ("모든 파일", "*.*")
        ]
        path = filedialog.askopenfilename(filetypes=file_types)
        if path:
            self._load_media(path)

    def _load_media(self, path: str):
        info = get_media_info(path)
        if not info:
            messagebox.showerror("오류", "지원하지 않거나 손상된 미디어 파일입니다.")
            return

        self._pause_playback()
        self.audio_player.clear_media()
        if self.frame_reader:
            self.frame_reader.release()
            self.frame_reader = None

        self.current_media = info
        self.current_timestamp = 0.0
        self.rotation_val = 0
        self.flip_h_val = False
        self.bright_slider.set(1.0)
        self.contrast_slider.set(1.0)
        self.subtitles.clear()
        self._clear_sub_inputs()

        media_dir = os.path.dirname(path)
        if os.path.exists(media_dir):
            self.save_dir_entry.delete(0, "end")
            self.save_dir_entry.insert(0, media_dir)

        fname = os.path.basename(path)
        self.file_path_lbl.configure(text=f"선택됨: {fname} ({info.width}x{info.height}, {info.file_size_mb}MB)")

        if info.is_video:
            self.frame_reader = VideoFrameReader(info.file_path)
            self.audio_player.load_media_audio(info.file_path)
            self.time_slider.configure(from_=0, to=max(0.1, info.duration))
            self.time_slider.set(0)
            self._update_time_label(0.0, info.duration)
            self.start_time_entry.delete(0, "end")
            self.start_time_entry.insert(0, "0.0")
            self.end_time_entry.delete(0, "end")
            self.end_time_entry.insert(0, f"{info.duration:.1f}")
            audio_status = "소리 포함" if info.has_audio else "소리 없음"
            self.info_lbl.configure(
                text=f"동영상: {info.width}x{info.height} | {info.fps}fps | 길이: {info.duration:.1f}초 ({audio_status})"
            )
            self.current_frame_img = self.frame_reader.get_frame(0.0)
        else:
            max_dur = self._get_max_duration()
            self.time_slider.configure(from_=0, to=max_dur)
            self.time_slider.set(0)
            self._update_time_label(0.0, max_dur)
            self.info_lbl.configure(text=f"이미지: {info.width}x{info.height} ({info.format_name})")
            try:
                self.current_frame_img = Image.open(info.file_path).convert("RGB")
            except Exception:
                self.current_frame_img = None

        self._sync_audio_settings()
        self._render_subtitle_list()
        self._update_media_ui_modes()
        self._update_preview()

    def _on_timeline_slider_change(self, val):
        self.current_timestamp = float(val)
        max_dur = self._get_max_duration()
        self._update_time_label(self.current_timestamp, max_dur)
        self.audio_player.seek(self.current_timestamp, speed=self._get_speed_multiplier())
        self._render_current_frame()

    def _update_time_label(self, current_sec: float, total_sec: float):
        cur_min, cur_s = divmod(int(current_sec), 60)
        cur_ms = int((current_sec - int(current_sec)) * 10)
        tot_min, tot_s = divmod(int(total_sec), 60)
        tot_ms = int((total_sec - int(total_sec)) * 10)
        self.time_label.configure(
            text=f"{cur_min:02d}:{cur_s:02d}.{cur_ms} / {tot_min:02d}:{tot_s:02d}.{tot_ms}"
        )

    def _set_current_as_start(self):
        self.start_time_entry.delete(0, "end")
        self.start_time_entry.insert(0, f"{self.current_timestamp:.2f}")

    def _set_current_as_end(self):
        self.end_time_entry.delete(0, "end")
        self.end_time_entry.insert(0, f"{self.current_timestamp:.2f}")

    # Subtitle Timeline Handlers
    def _set_current_as_sub_start(self):
        self.sub_start_entry.delete(0, "end")
        self.sub_start_entry.insert(0, f"{self.current_timestamp:.2f}")
        try:
            curr_end = float(self.sub_end_entry.get().strip() or "0")
            if curr_end <= self.current_timestamp:
                self.sub_end_entry.delete(0, "end")
                self.sub_end_entry.insert(0, f"{self.current_timestamp + 2.5:.2f}")
        except ValueError:
            pass

    def _set_current_as_sub_end(self):
        self.sub_end_entry.delete(0, "end")
        self.sub_end_entry.insert(0, f"{self.current_timestamp:.2f}")

    def _quick_add_duration(self, sec: float):
        try:
            s = float(self.sub_start_entry.get().strip() or "0")
            self.sub_end_entry.delete(0, "end")
            self.sub_end_entry.insert(0, f"{s + sec:.2f}")
        except ValueError:
            pass

    def _save_subtitle(self):
        txt = self.sub_text_entry.get().strip()
        if not txt:
            messagebox.showwarning("알림", "자막 내용을 입력해주세요.")
            return

        try:
            s_time = float(self.sub_start_entry.get().strip() or "0")
            e_time = float(self.sub_end_entry.get().strip() or "0")
        except ValueError:
            messagebox.showerror("오류", "시작 및 종료 시간은 올바른 숫자여야 합니다.")
            return

        if e_time <= s_time:
            messagebox.showerror("오류", "종료 시간은 시작 시간보다 커야 합니다.")
            return

        pos_raw = self.sub_pos_menu.get()
        pos_key = "top" if "상단" in pos_raw else ("center" if "중앙" in pos_raw else "bottom")

        style_raw = self.sub_style_menu.get()
        if "노란색" in style_raw:
            style_key = "yellow_box"
        elif "외곽선" in style_raw:
            style_key = "outline"
        elif "배경 없음" in style_raw:
            style_key = "none"
        else:
            style_key = "dark_box"

        size_raw = self.sub_size_menu.get()
        size_val = 42 if "작게" in size_raw else (72 if "크게" in size_raw else 56)

        sub_data = {
            "text": txt,
            "start_time": s_time,
            "end_time": e_time,
            "position": pos_key,
            "style": style_key,
            "font_size": size_val
        }

        if self.selected_sub_idx is not None and 0 <= self.selected_sub_idx < len(self.subtitles):
            self.subtitles[self.selected_sub_idx] = sub_data
        else:
            self.subtitles.append(sub_data)

        self.subtitles.sort(key=lambda x: x["start_time"])

        next_start = e_time
        next_end = next_start + 2.5
        self._clear_sub_inputs()
        self.sub_start_entry.delete(0, "end")
        self.sub_start_entry.insert(0, f"{next_start:.2f}")
        self.sub_end_entry.delete(0, "end")
        self.sub_end_entry.insert(0, f"{next_end:.2f}")

        self._render_subtitle_list()
        self._update_media_ui_modes()
        self._update_preview()

    def _clear_sub_inputs(self):
        self.selected_sub_idx = None
        self.sub_text_entry.delete(0, "end")
        self.add_sub_btn.configure(text="➕ 타임라인에 자막 추가", fg_color="#2b7a78")
        self.cancel_sub_edit_btn.configure(text="입력 초기화")
        self._update_preview()

    def _edit_subtitle(self, idx: int):
        if 0 <= idx < len(self.subtitles):
            sub = self.subtitles[idx]
            self.selected_sub_idx = idx

            self.sub_text_entry.delete(0, "end")
            self.sub_text_entry.insert(0, sub["text"])

            self.sub_start_entry.delete(0, "end")
            self.sub_start_entry.insert(0, f"{sub['start_time']:.2f}")

            self.sub_end_entry.delete(0, "end")
            self.sub_end_entry.insert(0, f"{sub['end_time']:.2f}")

            pos_map = {"top": "상단", "center": "중앙", "bottom": "하단"}
            self.sub_pos_menu.set(pos_map.get(sub.get("position", "bottom"), "하단"))

            style_map = {"dark_box": "어두운 박스", "yellow_box": "노란색 박스", "outline": "외곽선", "none": "배경 없음"}
            self.sub_style_menu.set(style_map.get(sub.get("style", "dark_box"), "어두운 박스"))

            size_map = {42: "작게", 56: "보통", 72: "크게"}
            self.sub_size_menu.set(size_map.get(sub.get("font_size", 56), "보통"))

            self.add_sub_btn.configure(text="✏️ 선택 자막 수정 완료", fg_color="#1f6aa5")
            self.cancel_sub_edit_btn.configure(text="수정 취소")

            self.current_timestamp = sub["start_time"]
            self.time_slider.set(self.current_timestamp)
            self._update_time_label(self.current_timestamp, self._get_max_duration())
            self.audio_player.seek(self.current_timestamp, speed=self._get_speed_multiplier())
            self._render_current_frame()

    def _delete_subtitle(self, idx: int):
        if 0 <= idx < len(self.subtitles):
            del self.subtitles[idx]
            if self.selected_sub_idx == idx:
                self._clear_sub_inputs()
            self._render_subtitle_list()
            self._update_media_ui_modes()
            self._update_preview()

    def _clear_all_subtitles(self):
        if not self.subtitles:
            return
        if messagebox.askyesno("확인", "등록된 모든 자막을 삭제하시겠습니까?"):
            self.subtitles.clear()
            self._clear_sub_inputs()
            self._render_subtitle_list()
            self._update_media_ui_modes()
            self._update_preview()

    def _render_subtitle_list(self):
        for widget in self.sub_list_frame.winfo_children():
            widget.destroy()

        self.sub_row_widgets.clear()
        self.sub_count_lbl.configure(text=f"📋 등록된 자막 목록 ({len(self.subtitles)}개)")

        if not self.subtitles:
            empty_lbl = ctk.CTkLabel(
                self.sub_list_frame,
                text="아직 추가된 타임라인 자막이 없습니다.\n위 입력창에서 자막과 시작/종료 시간을 넣고 '추가'를 눌러주세요.",
                font=ctk.CTkFont(size=11),
                text_color=("gray50", "gray50")
            )
            empty_lbl.pack(pady=15)
            return

        for idx, sub in enumerate(self.subtitles):
            row_frame = ctk.CTkFrame(self.sub_list_frame, fg_color=("gray85", "gray20"), corner_radius=5)
            row_frame.pack(fill="x", pady=2, padx=2)

            time_badge = f"{sub['start_time']:04.1f}s ~ {sub['end_time']:04.1f}s"
            t_lbl = ctk.CTkLabel(
                row_frame,
                text=time_badge,
                font=ctk.CTkFont(size=11, weight="bold"),
                width=90,
                anchor="w"
            )
            t_lbl.pack(side="left", padx=(8, 5), pady=4)

            pos_tag = f"[{sub.get('position', 'bottom')}]"
            short_txt = sub['text'] if len(sub['text']) <= 25 else sub['text'][:24] + "…"
            content_btn = ctk.CTkButton(
                row_frame,
                text=f"{short_txt}  {pos_tag}",
                font=ctk.CTkFont(size=11),
                anchor="w",
                fg_color="transparent",
                hover_color=("gray75", "gray30"),
                text_color=("black", "white"),
                command=lambda i=idx: self._edit_subtitle(i)
            )
            content_btn.pack(side="left", fill="x", expand=True, padx=2)

            del_btn = ctk.CTkButton(
                row_frame,
                text="✕",
                width=24,
                height=22,
                font=ctk.CTkFont(size=11, weight="bold"),
                fg_color=("gray70", "gray35"),
                hover_color="#e63946",
                command=lambda i=idx: self._delete_subtitle(i)
            )
            del_btn.pack(side="right", padx=(2, 6), pady=4)

            self.sub_row_widgets.append((sub, row_frame, content_btn))

    def _highlight_active_subtitle_in_list(self, active_sub_idx: Optional[int]):
        if self.active_sub_highlight_idx == active_sub_idx:
            return

        if self.active_sub_highlight_idx is not None and 0 <= self.active_sub_highlight_idx < len(self.sub_row_widgets):
            try:
                _, prev_frame, _ = self.sub_row_widgets[self.active_sub_highlight_idx]
                prev_frame.configure(fg_color=("gray85", "gray20"))
            except Exception:
                pass

        self.active_sub_highlight_idx = active_sub_idx
        if active_sub_idx is not None and 0 <= active_sub_idx < len(self.sub_row_widgets):
            try:
                _, curr_frame, _ = self.sub_row_widgets[active_sub_idx]
                curr_frame.configure(fg_color=("#1f6aa5", "#144870"))
            except Exception:
                pass

    def _on_setting_changed(self, *args):
        self._update_preview()

    def _on_mode_changed(self, choice):
        self._update_preview()

    def _on_rotate_clicked(self):
        self.rotation_val = (self.rotation_val + 90) % 360
        self._update_preview()

    def _on_flip_clicked(self):
        self.flip_h_val = not self.flip_h_val
        self._update_preview()

    def _reset_transform(self):
        self.rotation_val = 0
        self.flip_h_val = False
        self.bright_slider.set(1.0)
        self.contrast_slider.set(1.0)
        self._update_preview()

    def _get_target_size(self):
        res_text = self.res_var.get()
        if "720" in res_text:
            return (720, 1280)
        elif "2160" in res_text:
            return (2160, 3840)
        return (1080, 1920)

    def _get_fit_mode_key(self):
        mode_text = self.mode_segmented.get()
        if "크롭" in mode_text:
            return "crop"
        elif "레터박스" in mode_text:
            return "fit"
        return "blur"

    # Live Preview Rendering with Timeline & Options (Fast Direct 324x576)
    def _update_preview(self):
        if not self.current_frame_img:
            return

        prev_w, prev_h = 324, 576
        fit_mode = self._get_fit_mode_key()

        target_size = self._get_target_size()
        self.ratio_badge.configure(text=f"{target_size[0]} x {target_size[1]}")

        active_sub = None
        active_idx = None
        if self.subtitles:
            for idx, sub in enumerate(self.subtitles):
                if sub["start_time"] <= self.current_timestamp <= sub["end_time"]:
                    active_sub = sub
                    active_idx = idx
                    break

        self._highlight_active_subtitle_in_list(active_idx)

        draft_txt = self.sub_text_entry.get().strip()
        if not active_sub and draft_txt and not self.subtitles:
            pos_raw = self.sub_pos_menu.get()
            pos_key = "top" if "상단" in pos_raw else ("center" if "중앙" in pos_raw else "bottom")
            style_raw = self.sub_style_menu.get()
            style_key = "yellow_box" if "노란색" in style_raw else ("outline" if "외곽선" in style_raw else ("none" if "배경 없음" in style_raw else "dark_box"))
            size_raw = self.sub_size_menu.get()
            size_val = 42 if "작게" in size_raw else (72 if "크게" in size_raw else 56)

            active_sub = {
                "text": draft_txt,
                "position": pos_key,
                "style": style_key,
                "font_size": size_val
            }

        try:
            adj = apply_image_adjustments(
                self.current_frame_img,
                rotation=self.rotation_val,
                flip_h=self.flip_h_val,
                brightness=self.bright_slider.get(),
                contrast=self.contrast_slider.get()
            )

            short_preview = resize_to_shortform(
                adj,
                target_size=(prev_w, prev_h),
                mode=fit_mode
            )

            if active_sub and active_sub.get("text"):
                overlay = create_text_overlay(
                    target_size=(prev_w, prev_h),
                    text=active_sub["text"],
                    position=active_sub.get("position", "bottom"),
                    font_size=active_sub.get("font_size", 56),
                    text_color="#FFFFFF",
                    bg_style=active_sub.get("style", "dark_box")
                )
                short_preview.paste(overlay, (0, 0), overlay)

            ctk_disp = ctk.CTkImage(light_image=short_preview, dark_image=short_preview, size=(prev_w, prev_h))
            self.preview_label.configure(image=ctk_disp, text="")
            self.preview_label.image = ctk_disp
        except Exception as e:
            print(f"Preview render error: {e}")

    # Export Process
    def _choose_save_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.save_dir_entry.delete(0, "end")
            self.save_dir_entry.insert(0, d)

    def _start_export(self):
        if not self.current_media:
            messagebox.showwarning("알림", "먼저 변환할 이미지나 동영상 파일을 불러와주세요.")
            return

        self._pause_playback()
        self.audio_player.stop()

        save_dir = self.save_dir_entry.get().strip()
        if not os.path.exists(save_dir):
            try:
                os.makedirs(save_dir, exist_ok=True)
            except Exception:
                messagebox.showerror("오류", "유효한 저장 폴더를 지정해주세요.")
                return

        base_name = os.path.splitext(os.path.basename(self.current_media.file_path))[0]
        export_as_video = self.current_media.is_video or bool(self.current_bgm_path) or len(self.subtitles) > 0
        ext = ".mp4" if export_as_video else ".png"

        prefix = "[ShortForm]"
        out_filename = f"{prefix}_{base_name}{ext}"
        out_path = os.path.join(save_dir, out_filename)

        counter = 1
        while os.path.exists(out_path):
            out_filename = f"{prefix}_{base_name}_{counter}{ext}"
            out_path = os.path.join(save_dir, out_filename)
            counter += 1

        self.last_exported_file = out_path

        self.is_exporting = True
        self.export_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.open_result_btn.configure(state="disabled")
        self.progress_bar.set(0)
        self.progress_status_lbl.configure(text="변환을 시작합니다...")

        threading.Thread(target=self._run_export_thread, args=(out_path, export_as_video), daemon=True).start()

    def _cancel_export(self):
        if self.is_exporting:
            self.video_processor.cancel()
            self.progress_status_lbl.configure(text="작업이 취소되었습니다.")
            self._finish_export_ui(success=False, cancelled=True)

    def _run_export_thread(self, out_path: str, export_as_video: bool):
        target_size = self._get_target_size()
        fit_mode = self._get_fit_mode_key()
        bgm_path = self.current_bgm_path
        bgm_vol = self.bgm_vol_slider.get()
        orig_vol = self.orig_vol_slider.get()
        bgm_mode = "replace" if "대체" in self.bgm_mode_menu.get() else "mix"

        sub_list = list(self.subtitles)
        draft_txt = self.sub_text_entry.get().strip()
        if not sub_list and draft_txt:
            pos_raw = self.sub_pos_menu.get()
            pos_key = "top" if "상단" in pos_raw else ("center" if "중앙" in pos_raw else "bottom")
            style_raw = self.sub_style_menu.get()
            style_key = "yellow_box" if "노란색" in style_raw else ("outline" if "외곽선" in style_raw else ("none" if "배경 없음" in style_raw else "dark_box"))
            size_raw = self.sub_size_menu.get()
            size_val = 42 if "작게" in size_raw else (72 if "크게" in size_raw else 56)

            sub_list.append({
                "text": draft_txt,
                "start_time": 0.0,
                "end_time": 999.0,
                "position": pos_key,
                "style": style_key,
                "font_size": size_val
            })

        success = False
        try:
            if not self.current_media.is_video:
                if not export_as_video:
                    self.after(0, lambda: self.progress_status_lbl.configure(text="이미지 리사이즈 및 텍스트 렌더링 중..."))
                    self.after(0, lambda: self.progress_bar.set(0.5))

                    single_text = sub_list[0]["text"] if sub_list else ""
                    single_pos = sub_list[0].get("position", "bottom") if sub_list else "bottom"
                    single_style = sub_list[0].get("style", "dark_box") if sub_list else "dark_box"
                    single_size = sub_list[0].get("font_size", 56) if sub_list else 56

                    success = process_single_image(
                        input_path=self.current_media.file_path,
                        output_path=out_path,
                        target_size=target_size,
                        resize_mode=fit_mode,
                        rotation=self.rotation_val,
                        flip_h=self.flip_h_val,
                        brightness=self.bright_slider.get(),
                        contrast=self.contrast_slider.get(),
                        text=single_text,
                        text_position=single_pos,
                        font_size=single_size,
                        text_color="#FFFFFF",
                        bg_style=single_style
                    )
                    self.after(0, lambda: self.progress_bar.set(1.0))
                else:
                    dur_str = self.img_dur_menu.get()
                    dur_val = 7.0
                    for d in [5, 7, 10, 15]:
                        if f"{d}초" in dur_str:
                            dur_val = float(d)
                            break

                    def progress_cb(percent, status_str):
                        self.after(0, lambda: self.progress_bar.set(percent / 100.0))
                        self.after(0, lambda: self.progress_status_lbl.configure(text=f"숏폼 영상 렌더링 중: {percent:.1f}% ({status_str})"))

                    success = self.video_processor.process_image_with_bgm(
                        image_path=self.current_media.file_path,
                        output_path=out_path,
                        bgm_path=bgm_path or "",
                        duration=dur_val,
                        target_size=target_size,
                        resize_mode=fit_mode,
                        rotation=self.rotation_val,
                        flip_h=self.flip_h_val,
                        brightness=self.bright_slider.get(),
                        contrast=self.contrast_slider.get(),
                        subtitles=sub_list,
                        bgm_volume=bgm_vol,
                        progress_callback=progress_cb
                    )
            else:
                try:
                    start_sec = float(self.start_time_entry.get().strip() or "0")
                except ValueError:
                    start_sec = 0.0

                try:
                    end_sec_str = self.end_time_entry.get().strip()
                    end_sec = float(end_sec_str) if end_sec_str else self.current_media.duration
                except ValueError:
                    end_sec = self.current_media.duration

                speed_val = self._get_speed_multiplier()
                mute = self.mute_var.get()

                def progress_cb(percent, status_str):
                    self.after(0, lambda: self.progress_bar.set(percent / 100.0))
                    self.after(0, lambda: self.progress_status_lbl.configure(text=f"변환 중: {percent:.1f}% ({status_str})"))

                success = self.video_processor.process_video(
                    input_path=self.current_media.file_path,
                    output_path=out_path,
                    target_size=target_size,
                    resize_mode=fit_mode,
                    start_time=start_sec,
                    end_time=end_sec,
                    speed=speed_val,
                    mute_audio=mute,
                    rotation=self.rotation_val,
                    flip_h=self.flip_h_val,
                    subtitles=sub_list,
                    bgm_path=bgm_path,
                    bgm_volume=bgm_vol,
                    orig_volume=orig_vol,
                    bgm_mode=bgm_mode,
                    has_source_audio=self.current_media.has_audio,
                    progress_callback=progress_cb
                )

        except Exception as e:
            print(f"Export thread error: {e}")
            success = False

        self.after(0, lambda: self._finish_export_ui(success))

    def _finish_export_ui(self, success: bool, cancelled: bool = False):
        if not self.is_exporting and not cancelled:
            return
        self.is_exporting = False
        self.export_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")

        if cancelled or self.video_processor._is_cancelled:
            self.progress_status_lbl.configure(text="사용자에 의해 취소되었습니다.")
            messagebox.showinfo("취소", "변환 작업이 취소되었습니다.")
        elif success:
            self.progress_bar.set(1.0)
            self.progress_status_lbl.configure(text="🎉 숏폼 변환 완료!")
            self.open_result_btn.configure(state="normal")
            messagebox.showinfo("완료", f"숏폼 파일이 성공적으로 저장되었습니다!\n\n저장 위치:\n{self.last_exported_file}")
        else:
            self.progress_status_lbl.configure(text="❌ 변환 중 오류가 발생했습니다.")
            messagebox.showerror("오류", "변환 처리에 실패했습니다. 파일 형식과 설정을 확인해주세요.")

    def _open_result_folder(self):
        if self.last_exported_file and os.path.exists(self.last_exported_file):
            os.system(f'explorer /select,"{os.path.abspath(self.last_exported_file)}"')
        else:
            save_dir = self.save_dir_entry.get().strip()
            if os.path.exists(save_dir):
                os.system(f'explorer "{os.path.abspath(save_dir)}"')

def main():
    app = ShortFormStudioApp()
    app.mainloop()

if __name__ == "__main__":
    main()
