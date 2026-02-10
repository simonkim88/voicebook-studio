# content_filter.py - 본문 추출 필터 모듈
import re
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider, 
    QTextEdit, QDialogButtonBox
)
from PyQt6.QtCore import Qt


class ContentFilter:
    """전자책 콘텐츠 필터링 클래스 - 본문만 추출"""
    
    # 제거할 패턴들
    HEADER_PATTERNS = [
        r'^\s*Chapter\s+\d+.*$',
        r'^\s*CHAPTER\s+\d+.*$',
        r'^\s*제?\s*\d+\s*장[.\s]?.*$',
        r'^\s*Part\s+\d+.*$',
        r'^\s*Section\s+\d+.*$',
    ]
    
    PAGE_NUMBER_PATTERNS = [
        r'^\s*\d+\s*$',
        r'^\s*-\s*\d+\s*-$',
        r'^\s*\[\s*\d+\s*\]\s*$',
    ]
    
    TOC_PATTERNS = [
        r'^\s*Table of Contents\s*$',
        r'^\s*Contents\s*$',
        r'^\s*목차\s*$',
    ]
    
    METADATA_PATTERNS = [
        r'^\s*Copyright\s*[©ⓒ]\s*.*$',
        r'^\s*Published by\s*:?\s*.*$',
        r'^\s*ISBN[\s:-]*\d+.*$',
        r'^\s*All rights reserved.*$',
        r'^\s*Printed in\s*.*$',
        r'^\s*First published\s*.*$',
        r'^\s*출판사\s*:?\s*.*$',
        r'^\s*저자\s*:?\s*.*$',
        r'^\s*역자\s*:?\s*.*$',
    ]
    
    FOOTNOTE_PATTERNS = [
        r'^\s*\d+\s*[).\]]\s*.*$',
        r'^\s*\[\d+\]\s*.*$',
        r'^\s*※\s*.*$',
        r'^\s*\*\s*.*$',
    ]
    
    REFERENCE_PATTERNS = [
        r'^\s*References?\s*$',
        r'^\s*Bibliography\s*$',
        r'^\s*참고문헌\s*$',
        r'^\s*각주\s*$',
    ]
    
    APPENDIX_PATTERNS = [
        r'^\s*Appendix\s*[A-Z]?\s*$',
        r'^\s*부록\s*\d*\s*$',
    ]
    
    TABLE_PATTERNS = [
        r'^\s*Table\s+\d+[.:]?\s*.*$',
        r'^\s*그림\s+\d+[.:]?\s*.*$',
        r'^\s*Figure\s+\d+[.:]?\s*.*$',
        r'^\s*표\s+\d+[.:]?\s*.*$',
    ]
    
    @classmethod
    def is_content_line(cls, line, context=None):
        """해당 라인이 본문인지 판단"""
        line = line.strip()
        if not line:
            return False
        
        if len(line) < 3:
            return False
        
        for pattern in cls.HEADER_PATTERNS:
            if re.match(pattern, line, re.IGNORECASE):
                return False
        
        for pattern in cls.PAGE_NUMBER_PATTERNS:
            if re.match(pattern, line):
                return False
        
        for pattern in cls.TOC_PATTERNS:
            if re.match(pattern, line, re.IGNORECASE):
                return False
        
        for pattern in cls.METADATA_PATTERNS:
            if re.match(pattern, line, re.IGNORECASE):
                return False
        
        for pattern in cls.FOOTNOTE_PATTERNS:
            if re.match(pattern, line):
                return False
        
        for pattern in cls.REFERENCE_PATTERNS:
            if re.match(pattern, line, re.IGNORECASE):
                return False
        
        for pattern in cls.APPENDIX_PATTERNS:
            if re.match(pattern, line, re.IGNORECASE):
                return False
        
        for pattern in cls.TABLE_PATTERNS:
            if re.match(pattern, line, re.IGNORECASE):
                return False
        
        if re.match(r'^\s*https?://.*$', line):
            return False
        
        if re.match(r'^\s*\S+@\S+\.\S+\s*$', line):
            return False
        
        return True
    
    @classmethod
    def extract_body_text(cls, text, start_index=0):
        """본문 텍스트 추출"""
        lines = text.split('\n')
        body_lines = []
        
        for i, line in enumerate(lines[start_index:], start=start_index):
            if cls.is_content_line(line):
                body_lines.append(line)
        
        return '\n'.join(body_lines)
    
    @classmethod
    def detect_body_start(cls, text):
        """본문 시작 위치 자동 감지"""
        lines = text.split('\n')
        content_scores = []
        
        window_size = 10
        for i in range(len(lines) - window_size):
            window = lines[i:i + window_size]
            content_count = sum(1 for line in window if cls.is_content_line(line))
            content_scores.append((i, content_count))
        
        if content_scores:
            best_start = max(content_scores, key=lambda x: x[1])
            if best_start[1] >= window_size * 0.6:
                return best_start[0]
        
        return None
    
    @classmethod
    def calculate_confidence(cls, text, start_index):
        """본문 시작 위치 신뢰도 계산 (0-100%)"""
        lines = text.split('\n')
        if start_index >= len(lines):
            return 0
        
        window_size = min(20, len(lines) - start_index)
        window = lines[start_index:start_index + window_size]
        content_count = sum(1 for line in window if cls.is_content_line(line))
        
        return int((content_count / window_size) * 100)


class BodyConfirmDialog(QDialog):
    """본문 시작 위치 확인 다이얼로그"""
    
    def __init__(self, text, suggested_start=0, confidence=0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("본문 위치 확인")
        self.setGeometry(100, 100, 800, 600)
        self.text = text
        self.lines = text.split('\n')
        self.start_index = suggested_start
        self.confidence = confidence
        
        self.init_ui()
        self.preview_update()
    
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        info = QLabel(f"⚠️ 본문 시작 위치를 확인해주세요 (자동 감지 신뢰도: {self.confidence}%)")
        info.setStyleSheet("color: #007AFF; font-weight: bold; padding: 10px;")
        layout.addWidget(info)
        
        slider_layout = QHBoxLayout()
        slider_layout.addWidget(QLabel("시작 위치:"))
        
        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setMinimum(0)
        self.position_slider.setMaximum(max(0, len(self.lines) - 50))
        self.position_slider.setValue(self.start_index)
        self.position_slider.valueChanged.connect(self.on_position_changed)
        slider_layout.addWidget(self.position_slider)
        
        self.position_label = QLabel(f"{self.start_index}번째 줄")
        slider_layout.addWidget(self.position_label)
        layout.addLayout(slider_layout)
        
        preview_frame = QTextEdit()
        preview_frame.setFrameStyle(QTextEdit.Shape.StyledPanel)
        preview_layout = QVBoxLayout(preview_frame)
        
        preview_label = QLabel("미리보기 (선택된 위치부터 20줄):")
        preview_layout.addWidget(preview_label)
        
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMaximumHeight(300)
        preview_layout.addWidget(self.preview_text)
        
        layout.addWidget(preview_frame)
        
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def on_position_changed(self, value):
        self.start_index = value
        self.position_label.setText(f"{value}번째 줄")
        self.preview_update()
    
    def preview_update(self):
        end_idx = min(self.start_index + 20, len(self.lines))
        preview_lines = self.lines[self.start_index:end_idx]
        preview_text = '\n'.join(preview_lines)
        self.preview_text.setPlainText(preview_text)
    
    def get_start_index(self):
        return self.start_index
