# language_detector.py - 언어 감지 모듈

import re
from typing import Tuple, Optional

# 언어별 특성 패턴
LANGUAGE_PATTERNS = {
    'ko': {
        'name': 'Korean',
        'name_ko': '한국어',
        'pattern': r'[\uAC00-\uD7AF]',  # 한글 음절
        'sample_chars': '가나다라마바사',
        'recommended_voices': ['Sohee']
    },
    'en': {
        'name': 'English',
        'name_ko': '영어',
        'pattern': r'[a-zA-Z]',
        'sample_chars': 'abcdefghijklmnopqrstuvwxyz',
        'recommended_voices': ['Ryan', 'Aiden']
    },
    'zh': {
        'name': 'Chinese',
        'name_ko': '중국어',
        'pattern': r'[\u4E00-\u9FFF]',  # CJK 통합 한자
        'sample_chars': '的一是不了',
        'recommended_voices': ['Vivian', 'Serena', 'Uncle_Fu', 'Dylan', 'Eric']
    },
    'ja': {
        'name': 'Japanese',
        'name_ko': '일본어',
        'pattern': r'[\u3040-\u309F\u30A0-\u30FF]',  # 히라가나 + 가타칸나
        'sample_chars': 'あいうえお',
        'recommended_voices': ['Ono_Anna']
    },
}


def detect_language(text: str, sample_size: int = 1000) -> Tuple[str, float]:
    """
    텍스트의 언어를 감지합니다.
    
    Returns:
        (language_code, confidence_score)
        language_code: 'ko', 'en', 'zh', 'ja', 'unknown'
        confidence_score: 0.0 ~ 1.0
    """
    if not text or not text.strip():
        return 'unknown', 0.0
    
    # 샘플링 (너무 긴 텍스트는 앞부분만 분석)
    sample_text = text[:sample_size]
    total_chars = len(sample_text.replace(' ', '').replace('\n', ''))
    
    if total_chars == 0:
        return 'unknown', 0.0
    
    # 각 언어별 문자 수 계산
    lang_scores = {}
    for lang_code, lang_info in LANGUAGE_PATTERNS.items():
        matches = re.findall(lang_info['pattern'], sample_text)
        char_count = len(matches)
        ratio = char_count / total_chars if total_chars > 0 else 0
        lang_scores[lang_code] = ratio
    
    # 가장 높은 점수의 언어 선택
    if not lang_scores:
        return 'unknown', 0.0
    
    best_lang = max(lang_scores, key=lang_scores.get)
    best_score = lang_scores[best_lang]
    
    # 임계값 체크 (30% 이상이 해당 언어 문자여야 함)
    if best_score < 0.3:
        # 영어 알파벳이 대부분인 경우 영어로 판단
        if lang_scores.get('en', 0) > 0.5:
            return 'en', lang_scores['en']
        return 'unknown', best_score
    
    return best_lang, best_score


def get_language_name(lang_code: str, korean: bool = True) -> str:
    """언어 코드를 이름으로 변환"""
    if lang_code in LANGUAGE_PATTERNS:
        if korean:
            return LANGUAGE_PATTERNS[lang_code]['name_ko']
        return LANGUAGE_PATTERNS[lang_code]['name']
    return '알 수 없음' if korean else 'Unknown'


def get_recommended_voices(lang_code: str) -> list:
    """해당 언어에 추천되는 목소리 목록 반환"""
    if lang_code in LANGUAGE_PATTERNS:
        return LANGUAGE_PATTERNS[lang_code]['recommended_voices']
    return []


def get_voice_language(voice_name: str) -> str:
    """목소리 이름으로 해당 언어 반환"""
    voice_to_lang = {
        'Sohee': 'ko',
        'Ryan': 'en',
        'Aiden': 'en',
        'Vivian': 'zh',
        'Serena': 'zh',
        'Uncle_Fu': 'zh',
        'Dylan': 'zh',
        'Eric': 'zh',
        'Ono_Anna': 'ja',
    }
    return voice_to_lang.get(voice_name, 'unknown')


def check_language_mismatch(text: str, selected_voice: str) -> Tuple[bool, str, str]:
    """
    문서 언어와 선택된 목소리 언어가 일치하는지 확인
    
    Returns:
        (is_mismatch, detected_lang, voice_lang)
        is_mismatch: True if mismatch detected
        detected_lang: detected language code
        voice_lang: voice language code
    """
    detected_lang, confidence = detect_language(text)
    voice_lang = get_voice_language(selected_voice)
    
    if detected_lang == 'unknown' or confidence < 0.3:
        return False, detected_lang, voice_lang
    
    is_mismatch = detected_lang != voice_lang
    return is_mismatch, detected_lang, voice_lang


# 수동 언어 선택 옵션
MANUAL_LANGUAGE_OPTIONS = [
    ('auto', '자동 감지 (권장)'),
    ('ko', '한국어 (Korean)'),
    ('en', '영어 (English)'),
    ('zh', '중국어 (Chinese)'),
    ('ja', '일본어 (Japanese)'),
]
