# qwen3_audiobook_app_v2.4.py - 언어 자동 감지 + 수동 선택 버전
# v2.4 - Language Auto-Detection + Manual Selection

import sys
import os
import glob

# 모듈 임포트
from config_manager import (
    load_config, save_config, get_device, DEFAULT_OUTPUT_DIR,
    MODEL_SIZE_OPTIONS, TTS_ENGINE_OPTIONS, OUTPUT_FORMAT_OPTIONS,
    OUTPUT_FORMAT_SETTINGS
)
from document_parser import (
    DocumentParser, VOICE_OPTIONS, load_custom_voices, CUSTOM_VOICE_PRESETS,
    get_all_voice_options, KOKORO_VOICE_OPTIONS, KOKORO_VOICES
)
from tts_worker import TTSWorker
from ui_components import DropArea, SettingsDialog, LanguageConfirmDialog
from language_detector import (
    detect_language, get_language_name, get_recommended_voices, 
    check_language_mismatch, MANUAL_LANGUAGE_OPTIONS
)
from content_filter import ContentFilter, BodyConfirmDialog

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QProgressBar, QComboBox, QSlider, QTextEdit,
    QMessageBox, QGroupBox, QSplitter, QTabWidget, QMenuBar, QDialog,
    QCheckBox, QGridLayout, QLineEdit, QRadioButton, QButtonGroup, QSizePolicy
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QFont, QAction, QIcon
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI 오디오북 생성기 Pro v2.5 - Language Aware")
        # 가로로 넓게 3열 배치 → 전체 UI가 세로 스크롤 없이 한눈에 들어온다
        self.setGeometry(80, 60, 1440, 820)
        self.setMinimumSize(1100, 640)
        
        # 상태 변수
        self.current_file = None
        self.output_file = None
        self.current_text = ""
        self.filtered_text = ""
        self.detected_language = None
        self.tts_worker = None
        self.playback_speed = 1.0
        
        # 커스텀 음성 프리셋 로드
        load_custom_voices()

        # 설정 로드
        self.config = load_config()
        self.output_dir = self.config.get("output_directory", DEFAULT_OUTPUT_DIR)
        self.device = get_device(self.config)
        self.manual_lang = self.config.get("manual_language", "auto")
        
        os.makedirs(self.output_dir, exist_ok=True)
        
        # 미디어 플레이어
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        self.player.setAudioOutput(self.audio_output)
        
        self.init_ui()
        self.init_menu()
    
    def init_menu(self):
        menubar = self.menuBar()
        settings_menu = menubar.addMenu("설정")
        
        settings_action = QAction("환경 설정...", self)
        settings_action.triggered.connect(self.open_settings)
        settings_menu.addAction(settings_action)
    
    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        layout.setSpacing(8)
        layout.setContentsMargins(14, 10, 14, 10)

        # ── 헤더: 제목 + 시스템 정보를 한 줄에 (세로 공간 절약) ──
        header_layout = QHBoxLayout()

        title = QLabel("📚 AI 오디오북 생성기 Pro")
        title_font = QFont()
        title_font.setPointSize(17)
        title_font.setBold(True)
        title.setFont(title_font)
        header_layout.addWidget(title)

        subtitle = QLabel("Language Auto-Detection + Smart Content Filter")
        subtitle.setStyleSheet("color: #666; font-size: 11px;")
        header_layout.addWidget(subtitle)

        header_layout.addStretch()

        self.folder_label = QLabel(f"📁 저장: {os.path.normpath(self.output_dir)}")
        self.folder_label.setStyleSheet("color: #007AFF; font-size: 11px;")
        header_layout.addWidget(self.folder_label)

        self.device_label = QLabel(f"⚙️ {self.device.upper()}")
        self.device_label.setStyleSheet("color: #28a745; font-size: 11px;")
        header_layout.addWidget(self.device_label)

        layout.addLayout(header_layout)

        # ── 3열 스플리터: 입력 | 설정 | 실행 ──
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 1열: 입력 & 출력 ──
        input_panel = QWidget()
        input_layout = QVBoxLayout(input_panel)
        input_layout.setContentsMargins(0, 0, 6, 0)
        input_layout.setSpacing(8)

        # 입력 방식 탭
        input_tabs = QTabWidget()
        
        # 파일 탭
        file_tab = QWidget()
        file_layout = QVBoxLayout(file_tab)
        
        self.drop_area = DropArea()
        self.drop_area.file_dropped.connect(self.on_file_dropped)
        # 남는 세로 공간을 드롭 영역이 채우도록 (드롭 타깃도 넓어짐)
        self.drop_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        file_layout.addWidget(self.drop_area, 1)
        
        self.file_label = QLabel("선택된 파일: 없음")
        self.file_label.setWordWrap(True)
        file_layout.addWidget(self.file_label)
        
        # 언어 감지 정보 표시
        self.lang_info = QLabel("")
        self.lang_info.setStyleSheet("color: #007AFF; font-size: 12px; font-weight: bold;")
        file_layout.addWidget(self.lang_info)
        
        # 필터링 정보 표시
        self.filter_info = QLabel("")
        self.filter_info.setStyleSheet("color: #28a745; font-size: 12px;")
        file_layout.addWidget(self.filter_info)
        
        input_tabs.addTab(file_tab, "📁 파일에서 읽기")
        
        # 직접 입력 탭
        text_tab = QWidget()
        text_layout = QVBoxLayout(text_tab)
        
        text_label = QLabel("변환할 텍스트를 입력하세요:")
        text_layout.addWidget(text_label)
        
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("여기에 텍스트를 입력하거나 붙여넣기 하세요...")
        self.text_input.setMinimumHeight(120)
        text_layout.addWidget(self.text_input)

        input_tabs.addTab(text_tab, "✏️ 직접 입력")

        input_layout.addWidget(input_tabs)

        # 출력 파일명
        output_name_layout = QHBoxLayout()
        output_name_layout.addWidget(QLabel("출력 파일명:"))
        self.output_name_input = QLineEdit()
        self.output_name_input.setPlaceholderText("비워두면 원본 파일명 사용")
        output_name_layout.addWidget(self.output_name_input)
        input_layout.addLayout(output_name_layout)

        # 출력 형식 (오디오만 / 오디오 + 자막)
        output_format_group = QGroupBox("출력 형식")
        output_format_layout = QVBoxLayout(output_format_group)

        radio_row = QVBoxLayout()
        self.output_format_buttons = QButtonGroup(self)
        saved_format = self.config.get("output_format", "audio")
        for value, label in OUTPUT_FORMAT_OPTIONS:
            radio = QRadioButton(label)
            radio.setProperty("format_value", value)
            if value == saved_format:
                radio.setChecked(True)
            self.output_format_buttons.addButton(radio)
            radio_row.addWidget(radio)
        radio_row.addStretch()
        output_format_layout.addLayout(radio_row)

        # 저장된 값이 유효하지 않은 경우 첫 번째 옵션으로 폴백
        if self.output_format_buttons.checkedButton() is None:
            self.output_format_buttons.buttons()[0].setChecked(True)

        self.output_format_buttons.buttonClicked.connect(self.on_output_format_changed)

        output_format_desc = QLabel(
            "• 자막(.srt)은 항상 1개로 생성되며, 전체 오디오를 이어지는 하나의\n"
            "  타임라인으로 담습니다 (Simon Reader 오디오-텍스트 매칭용).\n"
            "• '분할 없음'은 오디오도 1개로 만들어 파일 경계 오차가 없으므로\n"
            "  매칭이 가장 정확합니다. 대신 파일 하나가 매우 커집니다."
        )
        output_format_desc.setStyleSheet("color: #666; font-size: 11px;")
        output_format_desc.setWordWrap(True)
        output_format_layout.addWidget(output_format_desc)

        input_layout.addWidget(output_format_group)
        # 입력 탭이 남는 세로 공간을 채우도록 (아래 빈 공간 제거)
        input_layout.setStretchFactor(input_tabs, 1)

        # ── 2열: 설정 ──
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(6, 0, 6, 0)
        left_layout.setSpacing(8)

        # 언어 설정 (NEW!)
        lang_group = QGroupBox("언어 설정")
        lang_layout = QVBoxLayout(lang_group)
        
        # 수동 언어 선택
        manual_lang_layout = QHBoxLayout()
        manual_lang_layout.addWidget(QLabel("문서 언어:"))
        self.lang_combo = QComboBox()
        for lang_code, lang_name in MANUAL_LANGUAGE_OPTIONS:
            self.lang_combo.addItem(lang_name, lang_code)
        
        # 저장된 설정 로드
        saved_lang = self.config.get("manual_language", "auto")
        for i in range(self.lang_combo.count()):
            if self.lang_combo.itemData(i) == saved_lang:
                self.lang_combo.setCurrentIndex(i)
                break
        
        self.lang_combo.currentIndexChanged.connect(self.on_language_changed)
        manual_lang_layout.addWidget(self.lang_combo)
        lang_layout.addLayout(manual_lang_layout)
        
        lang_desc = QLabel("• 자동 감지: 문서 내용을 분석하여 언어 감지\n"
                          "• 수동 선택: 특정 언어로 강제 지정")
        lang_desc.setStyleSheet("color: #666; font-size: 11px;")
        lang_layout.addWidget(lang_desc)

        # 모델 설정
        model_group = QGroupBox("모델 설정")
        model_layout = QVBoxLayout(model_group)

        # TTS 엔진 선택
        engine_layout = QHBoxLayout()
        engine_layout.addWidget(QLabel("TTS 엔진:"))
        self.engine_combo = QComboBox()
        for engine_id, engine_name in TTS_ENGINE_OPTIONS:
            self.engine_combo.addItem(engine_name, engine_id)

        saved_engine = self.config.get("tts_engine", "qwen")
        for i in range(self.engine_combo.count()):
            if self.engine_combo.itemData(i) == saved_engine:
                self.engine_combo.setCurrentIndex(i)
                break

        self.engine_combo.currentIndexChanged.connect(self.on_engine_changed)
        engine_layout.addWidget(self.engine_combo)
        model_layout.addLayout(engine_layout)

        model_size_layout = QHBoxLayout()
        self.model_size_label = QLabel("모델 크기:")
        model_size_layout.addWidget(self.model_size_label)
        self.model_size_combo = QComboBox()
        for size_id, size_name in MODEL_SIZE_OPTIONS:
            self.model_size_combo.addItem(size_name, size_id)

        saved_model_size = self.config.get("model_size", "1.7B")
        for i in range(self.model_size_combo.count()):
            if self.model_size_combo.itemData(i) == saved_model_size:
                self.model_size_combo.setCurrentIndex(i)
                break

        self.model_size_combo.currentIndexChanged.connect(self.on_model_size_changed)
        model_size_layout.addWidget(self.model_size_combo)
        model_layout.addLayout(model_size_layout)

        self.model_desc = QLabel("• 1.7B: 높은 품질, VRAM ~4GB 이상 권장\n"
                            "• 0.6B: 빠른 속도, 낮은 VRAM 사용")
        self.model_desc.setStyleSheet("color: #666; font-size: 11px;")
        model_layout.addWidget(self.model_desc)

        # 음성 설정
        settings_group = QGroupBox("음성 설정")
        settings_layout = QVBoxLayout(settings_group)

        # 목소리
        voice_layout = QHBoxLayout()
        voice_layout.addWidget(QLabel("목소리:"))
        self.voice_combo = QComboBox()
        self._rebuild_voice_combo()  # 엔진에 맞는 음성 목록 채우기
        self.voice_combo.currentIndexChanged.connect(self._on_voice_changed)
        voice_layout.addWidget(self.voice_combo)
        settings_layout.addLayout(voice_layout)

        # 목소리 설명 라벨
        self.voice_desc_label = QLabel()
        self.voice_desc_label.setStyleSheet("color: #666; font-size: 11px; margin-left: 10px;")
        self.voice_desc_label.setWordWrap(True)
        settings_layout.addWidget(self.voice_desc_label)
        self._on_voice_changed()  # 초기값 설정

        # ── 스타일 프리셋 ──
        preset_label = QLabel("── 스타일 프리셋 ──")
        preset_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preset_label.setStyleSheet("color: #555; font-weight: bold; margin-top: 6px;")
        settings_layout.addWidget(preset_label)

        self.preset_tabs = QTabWidget()
        self.preset_tabs.setStyleSheet("QTabBar::tab { padding: 4px 8px; }")

        # --- 기본 톤 탭 ---
        basic_tab = QWidget()
        basic_layout = self._create_flow_layout(basic_tab)
        self.basic_presets = {
            "자연스러운": "자연스럽고 편안한 톤으로 읽어주세요.",
            "차분한": "차분하고 안정된 톤으로 읽어주세요. 감정 기복을 최소화해주세요.",
            "밝은": "밝고 긍정적인 톤으로 읽어주세요. 활기차고 에너지 넘치는 느낌으로.",
            "진지한": "진지하고 무게 있는 톤으로 읽어주세요. 단호하고 분명한 어조로.",
            "감정적인": "감정이 풍부하고 표현력 있는 톤으로 읽어주세요. 문장의 감정을 살려서.",
            "속삭이는": "부드럽고 조용한 속삭이듯한 톤으로 읽어주세요.",
            "힘찬": "에너지 넘치고 힘차며 역동적인 톤으로 읽어주세요.",
            "다정한": "따뜻하고 다정하며 부드러운 톤으로 읽어주세요.",
        }
        for idx, (name, prompt) in enumerate(self.basic_presets.items()):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setStyleSheet(self._preset_btn_style())
            btn.clicked.connect(lambda checked, p=prompt, b=btn: self._on_preset_clicked(p, b))
            basic_layout.addWidget(btn, idx // 4, idx % 4)
        self.preset_tabs.addTab(basic_tab, "기본 톤")

        # --- 장르별 톤 탭 ---
        genre_tab = QWidget()
        genre_layout = self._create_flow_layout(genre_tab)
        self.genre_presets = {
            "역사/평전": "깊고 묵직하며 단호한 톤으로 읽어주세요. 과거의 무게감을 전달하는 느낌으로.",
            "철학/뇌과학": "논리적이고 중립적이며 천천히 읽어주세요. 청자가 생각할 시간을 주는 건조하고 이성적인 톤으로.",
            "자기계발": "자신감 있고 권위 있으며 명확한 톤으로 읽어주세요. 설득력 있는 전문가처럼.",
            "소설/문학": "감정을 살리며 이야기하듯 자연스럽게 읽어주세요. 장면의 분위기에 맞게 톤을 변화시켜주세요.",
            "뉴스/다큐": "차분하고 지적이며 안정된 톤으로 읽어주세요. 다큐멘터리 내레이터처럼 전문적인 나레이션 스타일로.",
            "동화/어린이": "경쾌하고 재미있는 톤으로 읽어주세요. 아이에게 이야기해주듯 다정하고 생동감 있게.",
        }
        for idx, (name, prompt) in enumerate(self.genre_presets.items()):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setStyleSheet(self._preset_btn_style())
            btn.clicked.connect(lambda checked, p=prompt, b=btn: self._on_preset_clicked(p, b))
            genre_layout.addWidget(btn, idx // 3, idx % 3)
        self.preset_tabs.addTab(genre_tab, "장르별 톤")

        # --- 상세 조절 탭 ---
        detail_tab = QWidget()
        detail_layout = QVBoxLayout(detail_tab)
        detail_layout.setContentsMargins(4, 4, 4, 4)
        detail_layout.setSpacing(4)

        # 톤
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("톤:"))
        self.detail_tone = QComboBox()
        self.detail_tone.addItems(["차분한", "자연스러운", "밝은", "진지한", "감정적인", "속삭이는"])
        self.detail_tone.currentIndexChanged.connect(self._update_detail_prompt)
        row1.addWidget(self.detail_tone)
        detail_layout.addLayout(row1)

        # 속도
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("속도:"))
        self.detail_speed = QComboBox()
        self.detail_speed.addItems(["천천히", "보통", "빠르게"])
        self.detail_speed.setCurrentIndex(1)
        self.detail_speed.currentIndexChanged.connect(self._update_detail_prompt)
        row2.addWidget(self.detail_speed)
        detail_layout.addLayout(row2)

        # 감정
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("감정:"))
        self.detail_emotion = QComboBox()
        self.detail_emotion.addItems(["절제된", "보통", "풍부한"])
        self.detail_emotion.setCurrentIndex(1)
        self.detail_emotion.currentIndexChanged.connect(self._update_detail_prompt)
        row3.addWidget(self.detail_emotion)
        detail_layout.addLayout(row3)

        # 음높이
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("음높이:"))
        self.detail_pitch = QComboBox()
        self.detail_pitch.addItems(["낮게", "보통", "높게"])
        self.detail_pitch.setCurrentIndex(1)
        self.detail_pitch.currentIndexChanged.connect(self._update_detail_prompt)
        row4.addWidget(self.detail_pitch)
        detail_layout.addLayout(row4)

        self.preset_tabs.addTab(detail_tab, "상세 조절")
        self.preset_tabs.currentChanged.connect(self._on_preset_tab_changed)

        settings_layout.addWidget(self.preset_tabs)

        # ── 음성 프롬프트 ──
        prompt_label = QLabel("── 음성 프롬프트 ──")
        prompt_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt_label.setStyleSheet("color: #555; font-weight: bold; margin-top: 6px;")
        settings_layout.addWidget(prompt_label)

        self.instruct_edit = QTextEdit()
        self.instruct_edit.setPlaceholderText("음성 스타일 지시를 입력하세요...")
        self.instruct_edit.setMaximumHeight(70)
        self.instruct_edit.setText("자연스럽고 편안한 톤으로 읽어주세요.")
        settings_layout.addWidget(self.instruct_edit)

        instruct_hint = QLabel("위 프리셋을 선택하면 자동으로 채워집니다. 직접 수정도 가능합니다.")
        instruct_hint.setStyleSheet("color: #888; font-size: 10px;")
        instruct_hint.setWordWrap(True)
        settings_layout.addWidget(instruct_hint)

        # 볼륨
        volume_layout = QHBoxLayout()
        volume_layout.addWidget(QLabel("볼륨:"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setMinimum(0)
        self.volume_slider.setMaximum(100)
        self.volume_slider.setValue(70)
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        volume_layout.addWidget(self.volume_slider)
        self.volume_label = QLabel("70%")
        volume_layout.addWidget(self.volume_label)
        settings_layout.addLayout(volume_layout)
        
        # 본문 필터링 옵션
        filter_group = QGroupBox("본문 필터링 옵션")
        filter_layout = QVBoxLayout(filter_group)
        
        self.filter_checkbox = QPushButton("✅ 스마트 필터링 사용")
        self.filter_checkbox.setCheckable(True)
        self.filter_checkbox.setChecked(True)
        self.filter_checkbox.setStyleSheet("""
            QPushButton { text-align: left; padding: 8px; }
            QPushButton:checked { background-color: #28a745; color: white; }
        """)
        filter_layout.addWidget(self.filter_checkbox)
        
        filter_desc = QLabel("페이지 번호, 목차, 각주, 참고문헌 등을 자동으로 제외합니다.")
        filter_desc.setStyleSheet("color: #666; font-size: 11px;")
        filter_desc.setWordWrap(True)
        filter_layout.addWidget(filter_desc)
        
        left_layout.addWidget(model_group)
        left_layout.addWidget(lang_group)
        left_layout.addWidget(settings_group)
        left_layout.addWidget(filter_group)
        left_layout.addStretch()

        # ── 3열: 실행 & 재생 ──
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(8)

        preview_group = QGroupBox("텍스트 미리보기")
        preview_layout = QVBoxLayout(preview_group)
        self.text_preview = QTextEdit()
        self.text_preview.setReadOnly(True)
        self.text_preview.setPlaceholderText("여기에 텍스트 내용이 표시됩니다...")
        self.text_preview.setMinimumHeight(160)
        preview_layout.addWidget(self.text_preview)
        # 미리보기가 남는 세로 공간을 채우도록 (아래 빈 공간 제거)
        right_layout.addWidget(preview_group, 1)

        # 변환/중지 버튼 레이아웃
        convert_layout = QHBoxLayout()

        self.convert_btn = QPushButton("🎙️ 음성 변환 시작")
        self.convert_btn.setStyleSheet("""
            QPushButton {
                background-color: #007AFF; color: white;
                padding: 15px; font-size: 16px; font-weight: bold;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #0056CC; }
            QPushButton:disabled { background-color: #999; }
        """)
        self.convert_btn.clicked.connect(self.start_conversion)
        convert_layout.addWidget(self.convert_btn)

        self.stop_convert_btn = QPushButton("⏹️ 중지")
        self.stop_convert_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc3545; color: white;
                padding: 15px; font-size: 16px; font-weight: bold;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #c82333; }
            QPushButton:disabled { background-color: #999; }
        """)
        self.stop_convert_btn.clicked.connect(self.stop_conversion)
        self.stop_convert_btn.setEnabled(False)
        convert_layout.addWidget(self.stop_convert_btn)

        right_layout.addLayout(convert_layout)

        # 진행 상황
        self.status_label = QLabel("텍스트를 입력하거나 파일을 선택해주세요")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_layout.addWidget(self.status_label)

        self.eta_label = QLabel("")
        self.eta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.eta_label.setStyleSheet("color: #28a745; font-size: 13px;")
        right_layout.addWidget(self.eta_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        right_layout.addWidget(self.progress_bar)

        # 저장 폴더 변경 버튼
        change_folder_btn = QPushButton("📁 저장 폴더 변경...")
        change_folder_btn.clicked.connect(self.open_settings)
        right_layout.addWidget(change_folder_btn)

        player_group = QGroupBox("재생 컨트롤")
        player_layout = QVBoxLayout(player_group)
        
        speed_layout = QHBoxLayout()
        speed_layout.addWidget(QLabel("재생 속도:"))
        
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setMinimum(50)
        self.speed_slider.setMaximum(200)
        self.speed_slider.setValue(100)
        self.speed_slider.valueChanged.connect(self.on_speed_changed)
        speed_layout.addWidget(self.speed_slider)
        
        self.speed_label = QLabel("1.0x")
        self.speed_label.setMinimumWidth(40)
        speed_layout.addWidget(self.speed_label)
        
        player_layout.addLayout(speed_layout)
        
        btn_layout = QHBoxLayout()
        
        self.play_btn = QPushButton("▶️ 재생")
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self.play_audio)
        btn_layout.addWidget(self.play_btn)
        
        self.stop_btn = QPushButton("⏹️ 정지")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_audio)
        btn_layout.addWidget(self.stop_btn)
        
        player_layout.addLayout(btn_layout)
        
        right_layout.addWidget(player_group)

        splitter.addWidget(input_panel)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([460, 470, 470])
        splitter.setChildrenCollapsible(False)

        layout.addWidget(splitter)

        footer = QLabel("💡 v2.5 | 자동 언어 감지 + 스마트 필터 + 자막(.srt) 출력")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet("color: #999; font-size: 11px;")
        layout.addWidget(footer)

        # 저장된 엔진에 맞춰 초기 UI 상태 적용
        self._apply_engine_ui()

    # ── 음성 설정 헬퍼 메서드 ──

    def _preset_btn_style(self):
        return """
            QPushButton { padding: 5px 10px; border: 1px solid #ccc; border-radius: 4px; }
            QPushButton:hover { background-color: #e0e0e0; }
            QPushButton:checked { background-color: #007AFF; color: white; border-color: #007AFF; }
        """

    def _create_flow_layout(self, parent):
        """프리셋 버튼용 그리드 레이아웃"""
        layout = QGridLayout(parent)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        return layout

    def _current_engine(self):
        return self.engine_combo.currentData() if hasattr(self, "engine_combo") else "qwen"

    def _rebuild_voice_combo(self):
        """현재 엔진에 맞는 음성 목록으로 콤보박스를 다시 채운다"""
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()

        if self._current_engine() == "kokoro":
            for voice_id, lang_code, language, description in KOKORO_VOICE_OPTIONS:
                self.voice_combo.addItem(f"{voice_id} ({language})", voice_id)
            self.voice_combo.setCurrentIndex(0)  # af_heart 기본값
        else:
            # Qwen: built-in 음성 + 커스텀 클론 음성
            for voice, language, description in VOICE_OPTIONS:
                self.voice_combo.addItem(f"{voice} ({language})", voice)
            if CUSTOM_VOICE_PRESETS:
                self.voice_combo.insertSeparator(self.voice_combo.count())
                for voice_name, info in CUSTOM_VOICE_PRESETS.items():
                    label = f"[Clone] {voice_name} ({info['language']})"
                    self.voice_combo.addItem(label, voice_name)
            self.voice_combo.setCurrentIndex(8)  # Sohee 기본값

        self.voice_combo.blockSignals(False)
        self._on_voice_changed()

    def _on_voice_changed(self):
        if not hasattr(self, "voice_desc_label"):
            return  # 아직 UI 초기화 중
        voice_name = self.voice_combo.currentData()
        desc = None
        if voice_name and voice_name in KOKORO_VOICES:
            desc = KOKORO_VOICES[voice_name]["description"]
        elif voice_name and voice_name in CUSTOM_VOICE_PRESETS:
            desc = CUSTOM_VOICE_PRESETS[voice_name]["description"]
        else:
            for v, _lang, d in VOICE_OPTIONS:
                if v == voice_name:
                    desc = d
                    break
        if desc:
            self.voice_desc_label.setText(f'  "{desc}"')

    def on_engine_changed(self, index):
        """TTS 엔진 변경 시: 설정 저장 + 음성 목록/UI 갱신"""
        engine = self.engine_combo.currentData()
        self.config["tts_engine"] = engine
        save_config(self.config)
        self._rebuild_voice_combo()
        self._apply_engine_ui()

    def _apply_engine_ui(self):
        """엔진에 따라 관련 UI 요소 활성/비활성 및 안내문 갱신"""
        is_kokoro = self._current_engine() == "kokoro"
        # 모델 크기(1.7B/0.6B)는 Qwen 전용
        self.model_size_combo.setEnabled(not is_kokoro)
        self.model_size_label.setEnabled(not is_kokoro)
        # 스타일 프리셋/프롬프트는 Qwen 전용 (Kokoro는 미지원)
        if hasattr(self, "preset_tabs"):
            self.preset_tabs.setEnabled(not is_kokoro)
        if hasattr(self, "instruct_edit"):
            self.instruct_edit.setEnabled(not is_kokoro)
        if is_kokoro:
            self.model_desc.setText("• Kokoro-82M: 영어 전용 경량 모델 (매우 빠름)\n"
                                    "• 스타일 프롬프트/모델 크기 설정은 적용되지 않습니다")
        else:
            self.model_desc.setText("• 1.7B: 높은 품질, VRAM ~4GB 이상 권장\n"
                                    "• 0.6B: 빠른 속도, 낮은 VRAM 사용")

    def _uncheck_all_presets(self):
        """모든 프리셋 버튼의 체크 해제"""
        for tab_idx in range(self.preset_tabs.count()):
            tab = self.preset_tabs.widget(tab_idx)
            for btn in tab.findChildren(QPushButton):
                if btn.isCheckable():
                    btn.setChecked(False)

    def _on_preset_clicked(self, prompt, clicked_btn):
        self._uncheck_all_presets()
        clicked_btn.setChecked(True)
        self.instruct_edit.setText(prompt)

    def _on_preset_tab_changed(self, index):
        """상세 조절 탭으로 전환 시 프롬프트 자동 생성"""
        if index == 2:  # 상세 조절 탭
            self._update_detail_prompt()

    def _update_detail_prompt(self):
        """상세 조절 드롭다운 조합으로 프롬프트 생성"""
        tone = self.detail_tone.currentText()
        speed = self.detail_speed.currentText()
        emotion = self.detail_emotion.currentText()
        pitch = self.detail_pitch.currentText()

        speed_map = {"천천히": "천천히", "보통": "보통 속도로", "빠르게": "빠르게"}
        emotion_map = {"절제된": "감정을 절제하며", "보통": "적당한 감정으로", "풍부한": "감정을 풍부하게 살려"}
        pitch_map = {"낮게": "약간 낮은 음높이로", "보통": "보통 음높이로", "높게": "약간 높은 음높이로"}

        prompt = f"{tone} 톤으로, {speed_map[speed]}, {emotion_map[emotion]}, {pitch_map[pitch]} 읽어주세요."
        self._uncheck_all_presets()
        self.instruct_edit.setText(prompt)

    def on_model_size_changed(self, index):
        """모델 크기 변경 시 설정 저장"""
        model_size = self.model_size_combo.currentData()
        self.config["model_size"] = model_size
        save_config(self.config)

    def _current_output_format(self):
        """현재 선택된 출력 형식 ('audio' 또는 'audio_srt')"""
        button = self.output_format_buttons.checkedButton()
        if button is None:
            return "audio"
        return button.property("format_value") or "audio"

    def on_output_format_changed(self, button):
        """출력 형식 변경 시 설정 저장"""
        self.config["output_format"] = self._current_output_format()
        save_config(self.config)

    def on_language_changed(self, index):
        """언어 선택 변경 시 설정 저장"""
        lang_code = self.lang_combo.currentData()
        self.config["manual_language"] = lang_code
        save_config(self.config)
    
    def on_volume_changed(self, value):
        self.volume_label.setText(f"{value}%")
        self.audio_output.setVolume(value / 100)
    
    def on_speed_changed(self, value):
        self.playback_speed = value / 100.0
        self.speed_label.setText(f"{self.playback_speed:.1f}x")
        self.player.setPlaybackRate(self.playback_speed)
    
    def detect_and_set_language(self, text):
        """언어 감지 및 목소리 자동 설정"""
        manual_lang = self.lang_combo.currentData()
        
        if manual_lang == "auto":
            # 자동 감지
            detected_lang, confidence = detect_language(text)
            self.detected_language = detected_lang
            
            if detected_lang != "unknown":
                lang_name = get_language_name(detected_lang)
                self.lang_info.setText(f"🌍 감지된 언어: {lang_name} ({int(confidence*100)}%)")
                
                # 목소리 언어 확인 (Kokoro 엔진은 추천 음성이 Qwen용이므로 건너뜀)
                current_voice = self.voice_combo.currentData()
                is_mismatch, _, _ = check_language_mismatch(text, current_voice)

                if is_mismatch and self._current_engine() == "qwen":
                    recommended = get_recommended_voices(detected_lang)
                    if recommended:
                        # 사용자에게 확인
                        dialog = LanguageConfirmDialog(
                            detected_lang, confidence, recommended, 
                            current_voice, self
                        )
                        if dialog.exec() == QDialog.DialogCode.Accepted:
                            new_voice = dialog.get_selected_voice()
                            # 목소리 변경
                            for i in range(self.voice_combo.count()):
                                if self.voice_combo.itemData(i) == new_voice:
                                    self.voice_combo.setCurrentIndex(i)
                                    break
                return detected_lang
            else:
                self.lang_info.setText("⚠️ 언어 감지 실패 - 수동 선택 권장")
                return None
        else:
            # 수동 선택
            self.detected_language = manual_lang
            lang_name = get_language_name(manual_lang)
            self.lang_info.setText(f"📝 수동 선택: {lang_name}")
            return manual_lang
    
    def on_file_dropped(self, file_path):
        try:
            self.current_file = file_path
            filename = os.path.basename(file_path)
            
            # 문서 파싱
            content = DocumentParser.parse(file_path)
            self.current_text = content
            
            # 언어 감지
            self.detect_and_set_language(content)
            
            # 스마트 필터링
            if self.filter_checkbox.isChecked():
                start_index = ContentFilter.detect_body_start(content)
                confidence = 0
                
                if start_index is not None:
                    confidence = ContentFilter.calculate_confidence(content, start_index)
                
                if start_index is None or confidence < 70:
                    suggested_start = start_index if start_index else 0
                    dialog = BodyConfirmDialog(content, suggested_start, confidence, self)
                    
                    if dialog.exec() == QDialog.DialogCode.Accepted:
                        start_index = dialog.get_start_index()
                        self.filtered_text = ContentFilter.extract_body_text(content, start_index)
                        self.filter_info.setText(f"✅ 본문 필터링 적용됨 (시작: {start_index}번째 줄)")
                    else:
                        self.filtered_text = content
                        self.filter_info.setText("⚠️ 필터링 취소됨 - 원본 사용")
                else:
                    self.filtered_text = ContentFilter.extract_body_text(content, start_index)
                    self.filter_info.setText(f"✅ 자동 본문 추출 완료 (신뢰도: {confidence}%)")
            else:
                self.filtered_text = content
                self.filter_info.setText("ℹ️ 필터링 사용 안 함 - 원본 전체")
            
            self.file_label.setText(f"선택된 파일: {filename}")
            self.output_name_input.setText(os.path.splitext(filename)[0])
            self.text_preview.setPlainText(self.filtered_text[:3000] + ("..." if len(self.filtered_text) > 3000 else ""))
            
            # 상태 메시지 업데이트
            original_len = len(content)
            filtered_len = len(self.filtered_text)
            self.status_label.setText(f"파일 로드 완료: 원본 {original_len}자 → 본문 {filtered_len}자")
            
        except Exception as e:
            QMessageBox.critical(self, "오류", f"파일 읽기 실패: {str(e)}")
    
    def open_settings(self):
        dialog = SettingsDialog(self, self.config)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config = dialog.get_settings()
            save_config(self.config)
            self.output_dir = self.config["output_directory"]
            self.device = get_device(self.config)
            os.makedirs(self.output_dir, exist_ok=True)
            self.folder_label.setText(f"📁 저장: {os.path.normpath(self.output_dir)}")
            self.device_label.setText(f"⚙️ 디바이스: {self.device.upper()}")
            QMessageBox.information(self, "설정 저장", "설정이 저장되었습니다.")
    
    def start_conversion(self):
        if self.filtered_text:
            text = self.filtered_text
            default_name = os.path.splitext(os.path.basename(self.current_file))[0] if self.current_file else "audiobook"
        else:
            text = self.text_input.toPlainText().strip()
            if not text:
                QMessageBox.warning(self, "경고", "텍스트를 입력하거나 파일을 선택해주세요.")
                return
            # 첫 문장을 기본 파일명으로 사용
            import re as _re
            first_line = text.split('\n')[0].strip()
            # 파일명에 사용 불가한 문자 제거, 30자 제한
            default_name = _re.sub(r'[\\/*?:"<>|]', '', first_line)[:30].strip() or "text_input"
            # 직접 입력 시에도 언어 감지
            self.detect_and_set_language(text)

        # 사용자 지정 파일명 우선, 없으면 기본값
        user_name = self.output_name_input.text().strip()
        output_name = user_name if user_name else default_name

        self.output_file = os.path.join(self.output_dir, f"{output_name}_audiobook.wav")

        # 이전 세그먼트 파일 → temp 폴더로 이동 (복구 가능)
        base_path = self.output_file.replace('.wav', '')
        old_segments = (glob.glob(f"{base_path}_*.wav") + glob.glob(f"{base_path}_*.mp3")
                        + glob.glob(f"{base_path}_*.srt"))
        if old_segments:
            import shutil
            from datetime import datetime
            temp_dir = os.path.join(self.output_dir, "temp",
                                    datetime.now().strftime("%Y%m%d_%H%M%S"))
            os.makedirs(temp_dir, exist_ok=True)
            for f in old_segments:
                try:
                    shutil.move(f, os.path.join(temp_dir, os.path.basename(f)))
                except OSError:
                    pass
            # 메인 파일도 이동 (wav/mp3/srt 모두)
            for main_ext in ['.wav', '.mp3', '.srt']:
                main_file = base_path + main_ext
                if os.path.exists(main_file):
                    try:
                        shutil.move(main_file, os.path.join(temp_dir, os.path.basename(main_file)))
                    except OSError:
                        pass

        self.convert_btn.setEnabled(False)
        self.convert_btn.setText("변환 중...")
        self.stop_convert_btn.setEnabled(True)  # 중지 버튼 활성화
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.eta_label.setText("시간 계산 중...")
        
        voice = self.voice_combo.currentData()
        instruct_text = self.instruct_edit.toPlainText().strip()
        engine = self._current_engine()

        # 커스텀 음성 여부 판단
        is_custom = voice in CUSTOM_VOICE_PRESETS
        ref_audio_path = None
        ref_text = None
        if is_custom:
            ref_audio_path = CUSTOM_VOICE_PRESETS[voice]["ref_audio_path"]
            ref_text = CUSTOM_VOICE_PRESETS[voice]["ref_text"]

        model_size = self.model_size_combo.currentData()
        srt_mode, split_audio = OUTPUT_FORMAT_SETTINGS.get(
            self._current_output_format(), ("none", True)
        )
        # Kokoro 음성의 언어 코드 (예: af_heart -> 'a')
        kokoro_lang_code = KOKORO_VOICES.get(voice, {}).get("lang_code", "a")

        self.tts_worker = TTSWorker(
            text=text, output_path=self.output_file,
            voice=voice, instruct_text=instruct_text, device=self.device,
            is_custom_voice=is_custom, ref_audio_path=ref_audio_path, ref_text=ref_text,
            model_size=model_size,
            tts_engine=engine, kokoro_lang_code=kokoro_lang_code,
            srt_mode=srt_mode,
        )
        self.tts_worker.split_audio = split_audio
        self.tts_worker.torch_compile = self.config.get("torch_compile")
        self.tts_worker.progress.connect(self.update_progress)
        self.tts_worker.status.connect(self.update_status)
        self.tts_worker.eta.connect(self.update_eta)
        self.tts_worker.finished_signal.connect(self.conversion_finished)
        self.tts_worker.error.connect(self.conversion_error)
        self.tts_worker.stopped.connect(self.conversion_stopped)  # 중지 시그널 연결
        self.tts_worker.start()
    
    def update_progress(self, value):
        self.progress_bar.setValue(value)
    
    def update_status(self, message):
        self.status_label.setText(message)
    
    def update_eta(self, eta_text):
        self.eta_label.setText(eta_text)
    
    def conversion_finished(self, output_path):
        self.status_label.setText("✅ 완료!")
        self.eta_label.setText("")
        self.convert_btn.setEnabled(True)
        self.convert_btn.setText("🎙️ 음성 변환 시작")
        self.stop_convert_btn.setEnabled(False)  # 중지 버튼 비활성화
        self.progress_bar.setVisible(False)
        self.play_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        
        self.player.setPlaybackRate(self.playback_speed)
        
        base_path = output_path.replace('.mp3', '').replace('.wav', '')
        segment_files = sorted(glob.glob(f"{base_path}_*.mp3") + glob.glob(f"{base_path}_*.wav"))
        
        if segment_files:
            num_files = len(segment_files) + 1
            message = f"오디오북 생성 완료!\n\n총 {num_files}개 파일 생성됨:\n"
            message += f"• {output_path}\n"
            for f in segment_files[:5]:
                message += f"• {f}\n"
            if len(segment_files) > 5:
                message += f"• ... 외 {len(segment_files) - 5}개 파일\n"
        else:
            message = f"오디오북 생성 완료!\n\n저장 위치: {output_path}"

        srt_files = getattr(self.tts_worker, "srt_files", []) if self.tts_worker else []
        if srt_files:
            message += (f"\n\n📝 자막 파일:\n• {srt_files[0]}\n\n"
                        f"Simon Reader에 이 .srt를 책으로 등록한 뒤 MP3를 올리면\n"
                        f"오디오-텍스트 매칭이 자동으로 이루어집니다.")

        QMessageBox.information(self, "완료", message)
    
    def conversion_error(self, error_message):
        self.status_label.setText(f"❌ 오류")
        self.eta_label.setText("")
        self.convert_btn.setEnabled(True)
        self.convert_btn.setText("🎙️ 음성 변환 시작")
        self.stop_convert_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        QMessageBox.critical(self, "오류", f"변환 실패:\n{error_message}")
    
    def stop_conversion(self):
        """음성 변환 중지"""
        if self.tts_worker and self.tts_worker.isRunning():
            self.stop_convert_btn.setEnabled(False)
            self.status_label.setText("⏹️ 중지 요청 중...")
            self.tts_worker.stop()
    
    def conversion_stopped(self, partial_path):
        """중지 후 중간 파일 저장 완료"""
        self.status_label.setText("⏹️ 중지됨 - 부분 파일 저장 완료")
        self.eta_label.setText("")
        self.convert_btn.setEnabled(True)
        self.convert_btn.setText("🎙️ 음성 변환 시작")
        self.stop_convert_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.play_btn.setEnabled(True)
        
        self.output_file = partial_path
        self.player.setPlaybackRate(self.playback_speed)
        
        srt_files = getattr(self.tts_worker, "srt_files", []) if self.tts_worker else []
        srt_note = f"\n📝 자막(.srt) {len(srt_files)}개도 함께 저장되었습니다.\n" if srt_files else ""

        QMessageBox.information(self, "중지됨",
            f"음성 변환이 중지되었습니다.\n\n"
            f"현재까지 진행된 내용이 저장되었습니다:\n"
            f"{partial_path}\n"
            f"{srt_note}\n"
            f"※ 파일명에 '_partial'이 포함되어 있습니다.")
    
    def play_audio(self):
        if self.output_file and os.path.exists(self.output_file):
            self.player.setSource(QUrl.fromLocalFile(self.output_file))
            self.player.setPlaybackRate(self.playback_speed)
            self.player.play()
            self.play_btn.setText("⏸️ 일시정지")
            self.play_btn.clicked.disconnect()
            self.play_btn.clicked.connect(self.pause_audio)
    
    def pause_audio(self):
        self.player.pause()
        self.play_btn.setText("▶️ 재생")
        self.play_btn.clicked.disconnect()
        self.play_btn.clicked.connect(self.play_audio)
    
    def stop_audio(self):
        self.player.stop()
        self.play_btn.setText("▶️ 재생")
        try:
            self.play_btn.clicked.disconnect()
        except:
            pass
        self.play_btn.clicked.connect(self.play_audio)
    
    def closeEvent(self, event):
        if self.tts_worker and self.tts_worker.isRunning():
            reply = QMessageBox.question(
                self, "종료 확인",
                "음성 변환이 진행 중입니다.\n\n"
                "• [Yes] 현재 청크까지 저장 후 종료\n"
                "• [No] 즉시 종료 (진행 중인 데이터 손실)\n"
                "• [Cancel] 종료 취소",
                QMessageBox.StandardButton.Yes |
                QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes
            )
            if reply == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            elif reply == QMessageBox.StandardButton.Yes:
                # 우아한 종료: 현재 청크 완료 후 저장
                self.tts_worker.stop()
                self.status_label.setText("종료 중... 현재 청크 저장 대기")
                self.tts_worker.wait(30000)  # 최대 30초 대기
            else:
                # 즉시 종료
                self.tts_worker.terminate()
                self.tts_worker.wait(5000)
        self.player.stop()
        event.accept()


class ContentFilter:
    """간단한 버전 - 실제로는 document_parser.py에서 가져와야 함"""
    @staticmethod
    def detect_body_start(text):
        lines = text.split('\n')
        for i, line in enumerate(lines):
            if len(line.strip()) > 50:  # 긴 문장이 나오면 본문 시작으로 간주
                return i
        return 0
    
    @staticmethod
    def calculate_confidence(text, start_index):
        return 80
    
    @staticmethod
    def extract_body_text(text, start_index):
        lines = text.split('\n')
        return '\n'.join(lines[start_index:])


if __name__ == "__main__":
    if sys.platform == "darwin":
        os.environ["QT_MAC_WANTS_LAYER"] = "1"
    
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Dock/작업표시줄 아이콘 (앱 번들 밖에서 실행될 때도 적용)
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    if sys.platform == "darwin":
        font = QFont("SF Pro", 13)
    else:
        font = QFont("맑은 고딕", 10)
    app.setFont(font)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())
