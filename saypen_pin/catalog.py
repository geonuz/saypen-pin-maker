"""Data-only book catalogs. Catalogs never execute code or download files."""
import json
import re
from dataclasses import dataclass
from pathlib import Path

class CatalogError(ValueError):
    pass

@dataclass(frozen=True)
class Track:
    key: str
    book: str
    mode: str
    raw_oid: int
    suggested_filename: str
    evidence: str

    @property
    def pin_filename(self):
        return f'{self.raw_oid:07d}_bk.pin'

@dataclass(frozen=True)
class Catalog:
    id: str
    title: str
    description: str
    tracks: tuple[Track, ...]

def load_catalog(path):
    path = Path(path)
    if path.stat().st_size > 1024*1024:
        raise CatalogError('책 목록 파일이 너무 큽니다. 1MB 이하 JSON을 선택하세요.')
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, UnicodeError) as e:
        raise CatalogError('책 목록 JSON을 읽을 수 없습니다.') from e
    if not isinstance(data,dict) or data.get('schema_version') != 1:
        raise CatalogError('지원하지 않는 책 목록 버전입니다.')
    if data.get('adapter') != 'saypen-xor-book-v1':
        raise CatalogError('이 책 목록은 현재 지원하지 않는 PIN 생성 방식을 사용합니다.')
    def label(value, limit=160):
        if not isinstance(value,str) or not value.strip() or len(value)>limit or any(ord(c)<32 for c in value):
            raise CatalogError('책 목록에 올바르지 않은 이름이 있습니다.')
        return value
    catalog_id=label(data.get('id'),64)
    if not re.fullmatch(r'[a-z0-9_-]+',catalog_id):
        raise CatalogError('책 목록 ID는 영문 소문자, 숫자, 밑줄, 하이픈만 사용할 수 있습니다.')
    books=data.get('books')
    if not isinstance(books,list) or not 1<=len(books)<=200:
        raise CatalogError('책은 1~200권이어야 합니다.')
    tracks=[]; ids=set(); oids=set()
    for book in books:
        if not isinstance(book,dict): raise CatalogError('잘못된 책 항목입니다.')
        title=label(book.get('title'))
        modes=book.get('tracks')
        if not isinstance(modes,list) or not 1<=len(modes)<=20:
            raise CatalogError('각 책에는 1~20개의 음원 항목이 필요합니다.')
        for item in modes:
            if not isinstance(item,dict): raise CatalogError('잘못된 음원 항목입니다.')
            key=label(item.get('id'),80)
            if not re.fullmatch(r'[a-z0-9_-]+',key) or key in ids:
                raise CatalogError('음원 ID가 잘못되었거나 중복됩니다.')
            oid=item.get('raw_oid')
            if type(oid) is not int or not 100000<=oid<=9999999 or oid in oids:
                raise CatalogError('OID는 중복 없는 100000~9999999 정수여야 합니다.')
            filename=item.get('suggested_filename','')
            if not isinstance(filename,str) or any(c in filename for c in '/\\:*?"<>|') or filename in ('.','..'):
                raise CatalogError('추천 파일명에는 경로를 넣을 수 없습니다.')
            ids.add(key);oids.add(oid)
            tracks.append(Track(key,title,label(item.get('mode')),oid,filename,str(item.get('evidence','unverified'))[:200]))
    return Catalog(catalog_id,label(data.get('title')),str(data.get('description',''))[:1000],tuple(tracks))

def bundled_catalogs():
    return [load_catalog(p) for p in sorted((Path(__file__).parent/'profiles').glob('*.json'))]
