"""Build the Windows app and audited source archive without user content."""
import hashlib
import importlib.metadata
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from audit_source import source_files,ROOT
sys.path.insert(0,str(ROOT))
from saypen_pin import __version__

def main():
    if sys.platform!='win32':raise SystemExit('Build Windows releases on Windows.')
    files=source_files()
    dist=ROOT/'dist';dist.mkdir(exist_ok=True)
    name=f'SaypenPINMaker-{__version__}'
    windows=dist/(name+'-windows-x64.zip');source=dist/(name+'-source.zip')
    exe=dist/(name+'.exe')
    sums=dist/(f'SHA256SUMS-{__version__}.txt')
    if any(p.exists() for p in (exe,windows,source,sums)):
        raise FileExistsError('Release archives already exist. Use a fresh checkout/build directory.')
    with tempfile.TemporaryDirectory(prefix='saypen-pin-maker-build-') as temporary:
        build_dir=Path(temporary)
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onefile','--windowed',
                        '--name',name,'--distpath',str(dist),
                        '--specpath',str(build_dir),'--workpath',str(build_dir),'--paths',str(ROOT),
                        '--add-data',f'{ROOT / "saypen_pin/profiles"};saypen_pin/profiles',
                        str(ROOT/'run_app.py')],cwd=ROOT,check=True)
    # Verify bundled archive names: no templates, media or firmware.
    from PyInstaller.archive.readers import CArchiveReader
    forbidden={'.pin','.mp3','.upd','.bmk','.smf','.wav'}
    archive=CArchiveReader(str(exe))
    bad=[n for n in archive.toc if Path(n).suffix.lower() in forbidden]
    if bad:raise RuntimeError(f'Forbidden content in executable: {bad}')
    licenses={}
    python_license=Path(sys.base_prefix)/'LICENSE.txt'
    if not python_license.is_file():raise FileNotFoundError('Python license missing')
    licenses['Python-LICENSE.txt']=python_license
    tk=Path(os.environ.get('TK_LIBRARY',str(Path(sys.base_prefix)/'tcl/tk8.6')))
    tcl=Path(os.environ.get('TCL_LIBRARY',str(Path(sys.base_prefix)/'tcl/tcl8.6')))
    for title,directory in [('Tk',tk),('Tcl',tcl)]:
        candidates=[directory/'license.terms',directory.parent/'license.terms']
        license_path=next((p for p in candidates if p.is_file()),None)
        if license_path is None:
            # The development runtime omits the Tcl script license file.
            # Include its upstream Tcl 8.6.12 license, never substitute Tk's.
            license_path=ROOT/'licenses/Tcl-8.6.12-license.txt' if title=='Tcl' else tk/'license.terms'
        if not license_path.is_file():raise FileNotFoundError(f'{title} license missing')
        licenses[f'{title}-license.terms']=license_path
    package=importlib.metadata.distribution('pyinstaller')
    for item in package.files or []:
        if str(item).endswith('licenses/COPYING.txt'):
            licenses['PyInstaller-COPYING.txt']=Path(package.locate_file(item))
    if 'PyInstaller-COPYING.txt' not in licenses:raise FileNotFoundError('PyInstaller license missing')
    with zipfile.ZipFile(windows,'x',zipfile.ZIP_DEFLATED) as z:
        z.write(exe,'SaypenPINMaker.exe')
        for filename in ['README.md','LICENSE','NOTICE.md','시작하기.txt']:
            z.write(ROOT/filename,filename)
        for document in sorted((ROOT/'docs').rglob('*')):
            if document.is_file():z.write(document,document.relative_to(ROOT).as_posix())
        for label,path in licenses.items():z.write(path,'third_party_licenses/'+label)
    with zipfile.ZipFile(source,'x',zipfile.ZIP_DEFLATED) as z:
        for path in files:z.write(path,name+'/'+path.relative_to(ROOT).as_posix())
    with sums.open('x',encoding='ascii') as f:
        for path in [windows,source]:
            f.write(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name+'\n')
    (dist/'bundle-audit.txt').write_text('No .pin/.mp3/.upd/.bmk/.smf/.wav entries in executable.\n'+
                                      '\n'.join(sorted(archive.toc)),encoding='utf-8')
    print('Created:',windows,source,sums,sep='\n')

if __name__=='__main__':main()
