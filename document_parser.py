# document_parser.py - 문서 파싱 모듈
import os
import re

# 문서 파서 임포트
try:
    import fitz
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

try:
    import ebooklib
    from ebooklib import epub
    EPUB_AVAILABLE = True
except ImportError:
    EPUB_AVAILABLE = False


class DocumentParser:
    """문서 파싱 클래스"""
    
    @staticmethod
    def parse_txt(filepath):
        """텍스트 파일 파싱"""
        encodings = ['utf-8', 'cp949', 'euc-kr', 'latin-1']
        for encoding in encodings:
            try:
                with open(filepath, 'r', encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        raise Exception("파일 인코딩을 감지할 수 없습니다.")
    
    @staticmethod
    def parse_rtf(filepath):
        """리치텍스트 파일 파싱 - 테이블 제외"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # RTF 테이블 제외 (\trowd ~ \row)
            content = re.sub(r'\\trowd.*?\\row', '', content, flags=re.DOTALL)
            
            # 기존 정리
            content = re.sub(r'\\[a-z]+\d*\s?', '', content)
            content = re.sub(r'[{}]', '', content)
            content = re.sub(r'\\\*?\\[a-z]+\s?', '', content)
            content = re.sub(r'\\\d+\s?', '', content)
            return content.strip()
        except Exception as e:
            raise Exception(f"RTF 파싱 오류: {str(e)}")
    
    @staticmethod
    def parse_pdf(filepath):
        """PDF 파일 파싱 - 테이블 제외 시도"""
        if not PDF_AVAILABLE:
            raise Exception("PyMuPDF가 설치되어 있지 않습니다. 'pip install pymupdf'를 실행하세요.")
        try:
            doc = fitz.open(filepath)
            text = ""
            
            for page in doc:
                # 테이블 감지 및 제외 (PyMuPDF 1.23.5+)
                try:
                    tables = page.find_tables()
                    if tables and len(tables.tables) > 0:
                        # 테이블 영역을 제외한 텍스트 추출
                        table_bboxes = [table.bbox for table in tables.tables]
                        blocks = page.get_text("blocks")
                        for block in blocks:
                            block_bbox = fitz.Rect(block[:4])
                            # 테이블 영역과 겹치지 않는 블록만 포함
                            is_in_table = False
                            for table_bbox in table_bboxes:
                                if block_bbox.intersects(table_bbox):
                                    is_in_table = True
                                    break
                            if not is_in_table:
                                text += block[4] + "\n"
                    else:
                        text += page.get_text()
                except:
                    # 테이블 감지 실패 시 일반 텍스트 추출
                    text += page.get_text()
            
            doc.close()
            return text
        except Exception as e:
            raise Exception(f"PDF 파싱 오류: {str(e)}")
    
    @staticmethod
    def parse_docx(filepath):
        """Word 문서 파싱 - 테이블 제외"""
        if not DOCX_AVAILABLE:
            raise Exception("python-docx가 설치되어 있지 않습니다. 'pip install python-docx'를 실행하세요.")
        try:
            doc = Document(filepath)
            paragraphs = []
            
            for para in doc.paragraphs:
                # 테이블 안에 있는 단락은 제외
                if para._element.getparent().tag.endswith('tbl'):
                    continue
                if para.text.strip():
                    paragraphs.append(para.text)
            
            return "\n\n".join(paragraphs)
        except Exception as e:
            raise Exception(f"DOCX 파싱 오류: {str(e)}")
    
    @staticmethod
    def parse_epub(filepath):
        """EPUB 전자책 파싱 - 테이블 제외"""
        if not EPUB_AVAILABLE:
            raise Exception("ebooklib이 설치되어 있지 않습니다. 'pip install ebooklib'를 실행하세요.")
        try:
            book = epub.read_epub(filepath)
            texts = []
            for item in book.get_items():
                if item.get_type() == ebooklib.ITEM_DOCUMENT:
                    try:
                        content = item.get_content().decode('utf-8')
                        
                        # HTML 테이블 제외 (<table> ~ </table>)
                        content = re.sub(r'<table[^>]*>.*?</table>', ' ', content, flags=re.DOTALL | re.IGNORECASE)
                        
                        # 나머지 HTML 태그 제거
                        content = re.sub(r'<[^>]+>', ' ', content)
                        content = re.sub(r'\s+', ' ', content).strip()
                        if content:
                            texts.append(content)
                    except:
                        continue
            return "\n\n".join(texts)
        except Exception as e:
            raise Exception(f"EPUB 파싱 오류: {str(e)}")
    
    @classmethod
    def parse(cls, filepath):
        """파일 확장자에 따라 자동 파싱"""
        ext = os.path.splitext(filepath)[1].lower()
        parsers = {
            '.txt': cls.parse_txt,
            '.rtf': cls.parse_rtf,
            '.pdf': cls.parse_pdf,
            '.docx': cls.parse_docx,
            '.epub': cls.parse_epub
        }
        if ext in parsers:
            return parsers[ext](filepath)
        return cls.parse_txt(filepath)
    
    @classmethod
    def get_supported_extensions(cls):
        """지원하는 파일 확장자 목록"""
        exts = ['.txt']
        if PDF_AVAILABLE:
            exts.append('.pdf')
        if DOCX_AVAILABLE:
            exts.append('.docx')
        if EPUB_AVAILABLE:
            exts.append('.epub')
        exts.append('.rtf')
        return exts


# 상수
VOICE_OPTIONS = [
    ("Vivian", "Chinese", "밝고 약간 날카로운 젊은 여성 목소리"),
    ("Serena", "Chinese", "따뜻하고 부드러운 젊은 여성 목소리"),
    ("Uncle_Fu", "Chinese", "풍부한 저음의 중년 남성 목소리"),
    ("Dylan", "Chinese (Beijing Dialect)", "명랑하고 자연스러운 북경 사투리 남성 목소리"),
    ("Eric", "Chinese (Sichuan Dialect)", "쾌활하고 약간 쉰 듯한 청두 사투리 남성 목소리"),
    ("Ryan", "English", "강한 리듬감이 있는 역동적인 남성 목소리"),
    ("Aiden", "English", "맑은 중음역의 밝은 미국식 남성 목소리"),
    ("Ono_Anna", "Japanese", "경쾌하고 재치 있는 일본 여성 목소리"),
    ("Sohee", "Korean", "풍부한 감정의 따뜻한 한국 여성 목소리")
]
