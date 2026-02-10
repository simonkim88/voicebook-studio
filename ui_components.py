# ui_components.py - UI 위젯 모듈 (언어 선택 포함)
import sys
from PyQt6.QtWidgets import (
    QLabel, QFileDialog, QDialog, QFormLayout, QHBoxLayout,
    QPushButton, QLineEdit, QComboBox, QDialogButtonBox, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

from config_manager import get_available_devices
from language_detector import (
    MANUAL_LANGUAGE_OPTIONS, detect_language, get_language_name,
    get_recommended_voices, check_language_mismatch
)


class DropArea(QLabel):
    """드래그앤드롭 지원 라벨 - 책 테마 배경"""
    file_dropped = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setText("📚\n파일을 여기로 드래그하세요\n또는 클릭해서 파일 선택\n\n(지원: TXT, RTF, PDF, DOCX, EPUB)")
        # 책/오디오북 테마 스타일 - 부드러운 그라데이션과 패턴
        self.setStyleSheet("""
            QLabel {
                border: 3px dashed #6b7280;
                border-radius: 16px;
                padding: 40px;
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #f0f4f8,
                    stop: 0.5 #e8eef5,
                    stop: 1 #dde5f0
                );
                background-image: 
                    repeating-linear-gradient(
                        45deg,
                        transparent,
                        transparent 10px,
                        rgba(107, 114, 128, 0.03) 10px,
                        rgba(107, 114, 128, 0.03) 20px
                    );
                font-size: 15px;
                color: #4b5563;
                font-weight: 500;
            }
            QLabel:hover {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 1,
                    stop: 0 #e8eef5,
                    stop: 0.5 #dde5f0,
                    stop: 1 #d1dce8
                );
                border-color: #4b5563;
                color: #374151;
            }
        """)
        self.setAcceptDrops(True)
        self.setMinimumHeight(180)
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    
    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            self.file_dropped.emit(file_path)
    
    def mousePressEvent(self, event):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "파일 선택", "",
            "Documents (*.txt *.rtf *.pdf *.docx *.epub);;All Files (*)"
        )
        if file_path:
            self.file_dropped.emit(file_path)


class SettingsDialog(QDialog):
    """설정 다이얼로그 (디바이스 선택 포함)"""
    
    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.setWindowTitle("환경 설정")
        self.setGeometry(200, 200, 550, 250)
        self.config = config or {}
        self.init_ui()
    
    def init_ui(self):
        layout = QFormLayout(self)
        
        # 출력 폴�더 설정
        folder_layout = QHBoxLayout()
        self.folder_input = QLineEdit(self.config.get("output_directory", ""))
        self.folder_input.setReadOnly(True)
        folder_layout.addWidget(self.folder_input)
        
        browse_btn = QPushButton("찾아보기...")
        browse_btn.clicked.connect(self.browse_folder)
        folder_layout.addWidget(browse_btn)
        
        layout.addRow("음성 파일 저장 폴�더:", folder_layout)
        
        # 디바이스 선택
        self.device_combo = QComboBox()
        devices = get_available_devices()
        for device_id, device_name in devices:
            self.device_combo.addItem(device_name, device_id)
        
        current_device = self.config.get("device", "auto")
        for i in range(self.device_combo.count()):
            if self.device_combo.itemData(i) == current_device:
                self.device_combo.setCurrentIndex(i)
                break
        
        layout.addRow("처리 디바이스:", self.device_combo)
        
        # 디바이스 설명
        device_desc = QLabel("• 자동 감지: 시스템에 따라 최적의 디바이스 선택\n• CPU: 모든 시스템에서 작동 (느림)\n• CUDA: NVIDIA GPU 필요 (Windows/Linux)\n• MPS: Apple Silicon GPU 필요 (Mac)")
        device_desc.setStyleSheet("color: #666; font-size: 11px;")
        layout.addRow(device_desc)
        
        # 버튼
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
    
    def browse_folder(self):
        """폴�더 선택"""
        folder = QFileDialog.getExistingDirectory(
            self, "저장 폴�더 선택", self.folder_input.text()
        )
        if folder:
            self.folder_input.setText(folder)
    
    def get_settings(self):
        """설정값 반환"""
        return {
            "output_directory": self.folder_input.text(),
            "device": self.device_combo.currentData(),
            "default_voice": self.config.get("default_voice", "Sohee"),
            "default_tone": self.config.get("default_tone", "natural"),
            "default_volume": self.config.get("default_volume", 70)
        }


class LanguageConfirmDialog(QDialog):
    """언어 확인 및 목소리 추천 다이얼로그"""
    
    def __init__(self, detected_lang, confidence, recommended_voices, 
                 current_voice, parent=None):
        super().__init__(parent)
        self.setWindowTitle("언어 확인")
        self.setGeometry(200, 200, 500, 300)
        self.detected_lang = detected_lang
        self.confidence = confidence
        self.recommended_voices = recommended_voices
        self.current_voice = current_voice
        self.selected_voice = current_voice
        
        self.init_ui()
    
    def init_ui(self):
        layout = QFormLayout(self)
        
        # 감지된 언어 표시
        lang_name = get_language_name(self.detected_lang)
        info = QLabel(f"🌍 감지된 언어: {lang_name} (신뢰도: {int(self.confidence*100)}%)")
        info.setStyleSheet("color: #007AFF; font-size: 14px; font-weight: bold;")
        layout.addRow(info)
        
        # 경고 메시지
        warning = QLabel("⚠️ 문서 언어와 선택된 목소리 언어가 다릅니다!\n\n"
                        f"현재 선택: {self.current_voice}\n"
                        f"추천 목소리: {', '.join(self.recommended_voices)}")
        warning.setStyleSheet("color: #dc3545; padding: 10px;")
        warning.setWordWrap(True)
        layout.addRow(warning)
        
        # 추천 목소리 선택
        self.voice_combo = QComboBox()
        for voice in self.recommended_voices:
            self.voice_combo.addItem(voice)
        
        if self.recommended_voices:
            self.voice_combo.setCurrentIndex(0)
        
        layout.addRow("추천 목소리 선택:", self.voice_combo)
        
        # 현재 목소리 유지 옵션
        self.keep_current = QCheckBox("현재 목소리 유지 (권장하지 않음)")
        self.keep_current.stateChanged.connect(self.on_keep_changed)
        layout.addRow(self.keep_current)
        
        # 버튼
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
    
    def on_keep_changed(self, state):
        """현재 목소리 유지 체크박스 상태 변경"""
        if state == Qt.CheckState.Checked.value:
            self.voice_combo.setEnabled(False)
            self.selected_voice = self.current_voice
        else:
            self.voice_combo.setEnabled(True)
            self.selected_voice = self.voice_combo.currentText()
    
    def get_selected_voice(self):
        """선택된 목소리 반환"""
        if self.keep_current.isChecked():
            return self.current_voice
        return self.voice_combo.currentText()
