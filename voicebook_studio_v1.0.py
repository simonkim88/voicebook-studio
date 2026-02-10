# qwen3_audiobook_app_v2.4.py - 언어 자동 감지 + 수동 선택 버전
# v2.4 - Language Auto-Detection + Manual Selection

import sys
import os
import glob

# 모듈 임포트
from config_manager import load_config, save_config, get_device, DEFAULT_OUTPUT_DIR
from document_parser import DocumentParser, VOICE_OPTIONS
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
    QCheckBox
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QFont, QAction
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI 오디오북 생성기 Pro v2.4 - Language Aware")
        self.setGeometry(100, 100, 1000, 850)
        
        # 상태 변수
        self.current_file = None
        self.output_file = None
        self.current_text = ""
        self.filtered_text = ""
        self.detected_language = None
        self.tts_worker = None
        self.playback_speed = 1.0
        
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
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # 제목
        title = QLabel("📚 AI 오디오북 생성기 Pro v2.4")
        title_font = QFont()
        title_font.setPointSize(24)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        
        subtitle = QLabel("Language Auto-Detection + Smart Content Filter")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("color: #666;")
        layout.addWidget(subtitle)
        
        # 시스템 정보
        info_layout = QHBoxLayout()
        self.folder_label = QLabel(f"📁 저장: {self.output_dir}")
        self.folder_label.setStyleSheet("color: #007AFF; font-size: 12px;")
        info_layout.addWidget(self.folder_label)
        
        self.device_label = QLabel(f"⚙️ 디바이스: {self.device.upper()}")
        self.device_label.setStyleSheet("color: #28a745; font-size: 12px;")
        info_layout.addWidget(self.device_label)
        
        info_layout.addStretch()
        layout.addLayout(info_layout)
        
        # 입력 방식 탭
        input_tabs = QTabWidget()
        
        # 파일 탭
        file_tab = QWidget()
        file_layout = QVBoxLayout(file_tab)
        
        self.drop_area = DropArea()
        self.drop_area.file_dropped.connect(self.on_file_dropped)
        file_layout.addWidget(self.drop_area)
        
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
        self.text_input.setMinimumHeight(200)
        text_layout.addWidget(self.text_input)
        
        input_tabs.addTab(text_tab, "✏️ 직접 입력")
        
        layout.addWidget(input_tabs)
        
        # 스플리터
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # 왼쪽 패널
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        
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
        
        left_layout.addWidget(lang_group)
        
        # 음성 설정
        settings_group = QGroupBox("음성 설정")
        settings_layout = QVBoxLayout(settings_group)
        
        # 목소리
        voice_layout = QHBoxLayout()
        voice_layout.addWidget(QLabel("목소리:"))
        self.voice_combo = QComboBox()
        for voice, language, description in VOICE_OPTIONS:
            self.voice_combo.addItem(f"{voice} ({language})", voice)
        self.voice_combo.setCurrentIndex(8)
        voice_layout.addWidget(self.voice_combo)
        settings_layout.addLayout(voice_layout)
        
        # 톤
        tone_layout = QHBoxLayout()
        tone_layout.addWidget(QLabel("톤/느낌:"))
        self.tone_combo = QComboBox()
        self.tone_combo.addItems(["자연스러운", "차분한", "밝은", "진지한", "감정적인"])
        tone_layout.addWidget(self.tone_combo)
        settings_layout.addLayout(tone_layout)
        
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
        
        left_layout.addWidget(lang_group)
        left_layout.addWidget(settings_group)
        left_layout.addWidget(filter_group)
        
        # 설정 변경 버튼
        change_folder_btn = QPushButton("📁 저장 폴더 변경...")
        change_folder_btn.clicked.connect(self.open_settings)
        left_layout.addWidget(change_folder_btn)
        
        # 변환/중지 버튼 레이아웃
        convert_layout = QHBoxLayout()
        
        # 변환 버튼
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
        
        # 중지 버튼
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
        self.stop_convert_btn.setEnabled(False)  # 초기에는 비활성화
        convert_layout.addWidget(self.stop_convert_btn)
        
        left_layout.addLayout(convert_layout)
        
        # 진행 상황
        self.status_label = QLabel("텍스트를 입력하거나 파일을 선택해주세요")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.status_label)
        
        self.eta_label = QLabel("")
        self.eta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.eta_label.setStyleSheet("color: #28a745; font-size: 13px;")
        left_layout.addWidget(self.eta_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        left_layout.addWidget(self.progress_bar)
        
        left_layout.addStretch()
        
        # 오른쪽 패널
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        preview_group = QGroupBox("텍스트 미리보기")
        preview_layout = QVBoxLayout(preview_group)
        self.text_preview = QTextEdit()
        self.text_preview.setReadOnly(True)
        self.text_preview.setPlaceholderText("여기에 텍스트 내용이 표시됩니다...")
        preview_layout.addWidget(self.text_preview)
        right_layout.addWidget(preview_group)
        
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
        
        self.save_btn = QPushButton("💾 파일로 저장")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_audio)
        player_layout.addWidget(self.save_btn)
        
        right_layout.addWidget(player_group)
        right_layout.addStretch()
        
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([350, 650])
        
        layout.addWidget(splitter)
        
        footer = QLabel("💡 v2.4 Language Aware | 자동 언어 감지 + 스마트 필터")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet("color: #999; font-size: 12px;")
        layout.addWidget(footer)
    
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
                
                # 목소리 언어 확인
                current_voice = self.voice_combo.currentData()
                is_mismatch, _, _ = check_language_mismatch(text, current_voice)
                
                if is_mismatch:
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
            self.folder_label.setText(f"📁 저장: {self.output_dir}")
            self.device_label.setText(f"⚙️ 디바이스: {self.device.upper()}")
            QMessageBox.information(self, "설정 저장", "설정이 저장되었습니다.")
    
    def start_conversion(self):
        if self.filtered_text:
            text = self.filtered_text
            output_name = os.path.splitext(os.path.basename(self.current_file))[0] if self.current_file else "audiobook"
        else:
            text = self.text_input.toPlainText().strip()
            if not text:
                QMessageBox.warning(self, "경고", "텍스트를 입력하거나 파일을 선택해주세요.")
                return
            output_name = "text_input"
            # 직접 입력 시에도 언어 감지
            self.detect_and_set_language(text)
        
        self.output_file = os.path.join(self.output_dir, f"{output_name}_audiobook.wav")
        
        self.convert_btn.setEnabled(False)
        self.convert_btn.setText("변환 중...")
        self.stop_convert_btn.setEnabled(True)  # 중지 버튼 활성화
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.eta_label.setText("시간 계산 중...")
        
        tone_map = {"자연스러운": "natural", "차분한": "calm", "밝은": "happy", "진지한": "serious", "감정적인": "emotional"}
        tone = tone_map.get(self.tone_combo.currentText(), "natural")
        voice = self.voice_combo.currentData()
        
        self.tts_worker = TTSWorker(
            text=text, output_path=self.output_file,
            voice=voice, tone=tone, device=self.device
        )
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
        self.save_btn.setEnabled(True)
        
        self.player.setPlaybackRate(self.playback_speed)
        
        base_path = output_path.replace('.wav', '')
        segment_files = sorted(glob.glob(f"{base_path}_*.wav"))
        
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
        self.save_btn.setEnabled(True)
        
        self.output_file = partial_path
        self.player.setPlaybackRate(self.playback_speed)
        
        QMessageBox.information(self, "중지됨", 
            f"음성 변환이 중지되었습니다.\n\n"
            f"현재까지 진행된 내용이 저장되었습니다:\n"
            f"{partial_path}\n\n"
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
    
    def save_audio(self):
        if not self.output_file:
            return
        save_path, _ = QFileDialog.getSaveFileName(
            self, "오디오 파일 저장", os.path.basename(self.output_file), "Audio Files (*.wav)"
        )
        if save_path:
            import shutil
            shutil.copy(self.output_file, save_path)
            QMessageBox.information(self, "저장 완료", f"파일 저장됨:\n{save_path}")
    
    def closeEvent(self, event):
        if self.tts_worker and self.tts_worker.isRunning():
            self.tts_worker.terminate()
            self.tts_worker.wait()
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
    
    font = QFont("SF Pro" if sys.platform == "darwin" else "Segoe UI", 13)
    if not QFont(font).exactMatch():
        font = QFont("Arial", 13)
    app.setFont(font)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())
