"""Offline generation using only user-supplied PIN and MP3 inputs.

Outputs preserve the tested special-audio layout and empty ordinary slots.
No source files are edited; each batch gets a new directory.
"""
import hashlib
import json
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from . import __version__
from .pin_format import Pin, KEY, be, audio_probe, sha256

MAX_MP3 = 65535*512
MAX_TEMPLATE = 1024*1024*1024

class UserError(ValueError):
    pass

class Cancelled(UserError):
    pass

def check_cancel(event):
    if event and event.is_set():
        raise Cancelled('작업을 취소했습니다. 원본 파일은 변경되지 않았습니다.')

def _hash(data):
    return hashlib.sha256(data).hexdigest()

def _put(buffer,offset,plain):
    buffer[offset:offset+len(plain)] = bytes(v^KEY[(offset+i)%64] for i,v in enumerate(plain))

@dataclass(frozen=True)
class Prepared:
    source: Path
    source_sha256: str
    base: bytes
    meta_offset: int
    retained_count: int
    retained_references: int

def prepare_template(path,cancel=None):
    check_cancel(cancel)
    path=Path(path)
    if not path.is_file() or path.suffix.lower()!='.pin':
        raise UserError('세이펜의 정상 .pin 파일을 선택하세요.')
    if not 2021888<=path.stat().st_size<=MAX_TEMPLATE:
        raise UserError('지원 범위를 벗어난 PIN 크기입니다. 1GB 이하의 정상 파일을 선택하세요.')
    try:
        pin=Pin(path)
        # Validate the decoded supported layout; do not accept unknown header flags.
        expected=bytearray(64)
        for at,value in [(6,64001),(8,64094),(14,1),(16,63078)]:
            expected[at:at+2]=value.to_bytes(2,'big')
        expected[34:38]=(0x1eda00).to_bytes(4,'big')
        if pin.header!=bytes(expected):
            raise ValueError('Unknown header fields')
        report=pin.validate()
        if report['errors']:
            raise ValueError(report['errors'][0])
        refs=[r for r in pin.references() if r['kind'] in ('special','auxiliary')]
        if not refs: raise ValueError('Special audio missing')
        start=0x1eda00
        if any(r['audio_offset']<start for r in refs): raise ValueError('Invalid audio boundary')
        prefix=bytearray(pin.data[:start])
        prefix[pin.ordinary_offset:pin.sentence_offset]=bytes(pin.sentence_offset-pin.ordinary_offset)
        locations={}
        for offset,length in sorted({(r['audio_offset'],r['allocation']) for r in refs}):
            check_cancel(cancel)
            locations[(offset,length)]=len(prefix)
            prefix.extend(pin.data[offset:offset+length])
            prefix.extend(bytes(2048))
        fields={}
        for r in refs:
            at=r['offset']+((2,7)[r['channel']] if r['kind']=='special' else 5*r['channel'])
            value=locations[(r['audio_offset'],r['allocation'])]//512
            if value>0xffffff or at<64 or at+3>start: raise ValueError('Out of bounds')
            if at in fields and fields[at]!=value: raise ValueError('Conflicting pointer')
            fields[at]=value
            _put(prefix,at,value.to_bytes(3,'big'))
        # Old book pointer is replaced on every build; it is never used as-is.
        return Prepared(path.resolve(),report['sha256'],bytes(prefix),pin.meta_offset,len(locations),len(refs))
    except (ValueError,IndexError,OverflowError) as e:
        raise UserError('지원되는 PIN 형식이 아니거나 파일이 손상되었습니다. 다른 정상 세이펜 PIN을 선택하세요.') from e

def read_mp3(path):
    path=Path(path)
    if not path.is_file() or path.suffix.lower()!='.mp3':
        raise UserError(f'MP3 파일을 선택하세요: {path.name}')
    if not 1<=path.stat().st_size<=MAX_MP3:
        raise UserError(f'음원이 비어 있거나 너무 큽니다 (최대 약 33.5MB): {path.name}')
    data=path.read_bytes()
    probe=audio_probe(data)
    if not probe['valid']:
        raise UserError(f'MP3 구조를 확인할 수 없습니다: {path.name}')
    # Accept ID3v1 and zero padding; reject truncated frames/arbitrary trailing data.
    tail=data[probe['mpeg_end']:]
    if tail.startswith(b'TAG') and len(tail)>=128:
        tail=tail[128:]
    if any(tail):
        raise UserError(f'지원하지 않는 후행 태그 또는 불완전한 MP3입니다: {path.name}')
    return data,probe

def render_pin(prepared,audio):
    count=(len(audio)+511)//512
    start=len(prepared.base)
    if not 1<=count<=65535 or start%512 or start//512>0xffffff:
        raise UserError('음원 크기 또는 정렬을 처리할 수 없습니다.')
    result=bytearray(prepared.base)
    _put(result,prepared.meta_offset+25,(start//512).to_bytes(3,'big')+count.to_bytes(2,'big'))
    result.extend(audio)
    result.extend(bytes(count*512-len(audio)+2048))
    return result

def build_batch(catalog,template,selected,out_parent,*,rights_confirmed=False,progress=None,cancel=None):
    """selected maps track IDs to local MP3 paths. Unselected tracks are skipped."""
    if not rights_confirmed:
        raise UserError('사용할 수 있는 책과 음원인지 확인한 뒤 체크해 주세요.')
    tracks={t.key:t for t in catalog.tracks}
    if not selected or set(selected)-set(tracks):
        raise UserError('만들 음원을 한 개 이상 선택하세요.')
    parent=Path(out_parent)
    if not parent.is_dir(): raise UserError('결과를 저장할 폴더를 선택하세요.')
    def emit(text,done=0,total=len(selected)):
        if progress: progress(text,done,total)
    emit('세이펜 PIN을 확인하고 있습니다…')
    prepared=prepare_template(template,cancel)
    jobs=[]
    for key,path in selected.items():
        check_cancel(cancel)
        data,probe=read_mp3(path)
        jobs.append((tracks[key],Path(path),_hash(data),len(data),probe))
    required=sum(len(prepared.base)+((size+511)//512)*512+2048 for _,_,_,size,_ in jobs)
    if shutil.disk_usage(parent).free<required+20*1024*1024:
        raise UserError('저장 공간이 부족합니다. 다른 폴더를 선택하세요.')
    name=f'PIN_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}'
    staging=parent/(name+'.partial')
    final=parent/name
    staging.mkdir()
    files=staging/'files'
    files.mkdir()
    records=[]
    try:
        for i,(track,path,expected,size,probe) in enumerate(jobs):
            check_cancel(cancel)
            emit(f'{track.book} · {track.mode} 만드는 중…',i)
            audio,_=read_mp3(path)
            if _hash(audio)!=expected: raise UserError(f'작업 중 음원이 변경되었습니다: {path.name}')
            payload=render_pin(prepared,audio)
            target=files/track.pin_filename
            with target.open('xb') as f: f.write(payload)
            pin=Pin(target)
            verification=pin.validate()
            offset=be(pin.meta,25,3)*512
            if verification['errors'] or pin.data[offset:offset+size]!=audio:
                raise UserError('생성 파일 검증에 실패했습니다. 결과를 펜에 넣지 마세요.')
            records.append({'book':track.book,'mode':track.mode,'raw_oid':track.raw_oid,
                            'filename':track.pin_filename,'sha256':verification['sha256'],
                            'size':len(payload),'audio_sha256':expected,'audio_bytes':size,
                            'evidence':track.evidence})
            emit(f'{track.book} · {track.mode} 확인 완료',i+1)
        check_cancel(cancel)
        if sha256(prepared.source)!=prepared.source_sha256:
            raise UserError('작업 중 세이펜 PIN이 변경되었습니다. 다시 시도하세요.')
        report={'app_version':__version__,'catalog':catalog.id,'adapter':'saypen-xor-book-v1',
                'template_sha256':prepared.source_sha256,'input_files_modified':False,
                'retained_special_audio':prepared.retained_count,'total_bytes':sum(r['size'] for r in records),
                'files':records,'status':'structure_and_audio_verified'}
        (staging/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (staging/'사용방법.txt').write_text(
            '완료된 BOOK 폴더의 .pin 파일만 펜 SD카드의 BOOK 폴더 안에 넣으세요.\n'
            '먼저 SD카드를 백업하세요. 같은 이름 파일이 있으면 기존 파일을 별도 보관한 후 교체하세요.\n'
            '파일명은 바꾸지 마세요. 안전하게 분리하고 펜을 완전히 껐다 켜세요.\n'
            '일반 펜 모드에서 선택한 책의 영역을 찍어 확인하세요.\n'
            '결과 PIN에는 사용자가 선택한 MP3와 템플릿의 특수 음원이 포함됩니다. 결과 파일을 공개 배포하지 마세요.\n',encoding='utf-8-sig')
        files.rename(staging/'BOOK')
        staging.rename(final)
        return final,report
    except Exception:
        (staging/'미완료_사용하지마세요.txt').write_text('취소되었거나 생성에 실패한 폴더입니다. 펜에 복사하지 마세요. 원본은 변경되지 않았습니다.',encoding='utf-8-sig')
        raise
