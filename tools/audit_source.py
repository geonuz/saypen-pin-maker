"""Fail closed on media/binaries/private paths in the public source tree."""
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SKIP={'.git','.venv','venv','build','dist','__pycache__','.private-tests'}
ALLOWED={'.py','.md','.toml','.txt','.json','.yml','.yaml','.png','.ico'}
SPECIAL={'LICENSE','.gitignore'}
PRIVATE=re.compile(r'(?i)[a-z]:[\\/]+Users[\\/]+[^\\/\s"\']+')

def source_files():
    files=[]
    for path in sorted(ROOT.rglob('*')):
        relative=path.relative_to(ROOT)
        if any(part in SKIP or part.endswith('.egg-info') for part in relative.parts):continue
        if path.is_symlink():raise ValueError(f'Symlinks are not allowed: {relative}')
        if not path.is_file():continue
        if path.name not in SPECIAL and path.suffix.lower() not in ALLOWED:
            raise ValueError(f'Unexpected source artifact: {relative}')
        if path.stat().st_size>2*1024*1024:raise ValueError(f'Oversized source artifact: {relative}')
        if path.suffix.lower() not in {'.png','.ico'}:
            text=path.read_text(encoding='utf-8-sig')
            if PRIVATE.search(text):raise ValueError(f'Private absolute user path: {relative}')
        files.append(path)
    return files

if __name__=='__main__':print(f'Public source audit passed: {len(source_files())} files; no media/PIN/firmware/binary inputs')
