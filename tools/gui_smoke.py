"""Exercise the real GUI event loop with synthetic inputs; optional own-window screenshot."""
import argparse
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from test_engine import synthetic_template,synthetic_mp3
from saypen_pin.app import App

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--screenshot',type=Path);args=ap.parse_args()
    app=App();app.update()
    try:
        assert len(app.catalog.tracks)==24 and len(app.tree.get_children())==8
        app.geometry('880x700');app.update()
        assert app.status_label.winfo_rooty()+app.status_label.winfo_height()<=app.winfo_rooty()+app.winfo_height()
        assert app.create.winfo_rootx()+app.create.winfo_width()<=app.winfo_rootx()+app.winfo_width()
        app.geometry('1080x800');app.update()
        if args.screenshot:
            # Render only our application window; never capture the desktop.
            import ctypes
            from ctypes import wintypes
            from PIL import Image
            app.update();time.sleep(.2)
            u=ctypes.windll.user32;g=ctypes.windll.gdi32
            u.GetDC.restype=wintypes.HDC;u.GetDC.argtypes=[wintypes.HWND]
            g.CreateCompatibleDC.restype=wintypes.HDC;g.CreateCompatibleDC.argtypes=[wintypes.HDC]
            g.CreateCompatibleBitmap.restype=wintypes.HBITMAP;g.CreateCompatibleBitmap.argtypes=[wintypes.HDC,ctypes.c_int,ctypes.c_int]
            g.SelectObject.restype=wintypes.HANDLE;g.SelectObject.argtypes=[wintypes.HDC,wintypes.HANDLE]
            u.PrintWindow.argtypes=[wintypes.HWND,wintypes.HDC,wintypes.UINT]
            g.GetDIBits.argtypes=[wintypes.HDC,wintypes.HBITMAP,wintypes.UINT,wintypes.UINT,ctypes.c_void_p,ctypes.c_void_p,wintypes.UINT]
            g.DeleteObject.argtypes=[wintypes.HANDLE];g.DeleteDC.argtypes=[wintypes.HDC]
            u.ReleaseDC.argtypes=[wintypes.HWND,wintypes.HDC]
            w,h=app.winfo_width(),app.winfo_height();hwnd=app.winfo_id()
            dc=u.GetDC(hwnd);memory=g.CreateCompatibleDC(dc);bitmap=g.CreateCompatibleBitmap(dc,w,h)
            previous=g.SelectObject(memory,bitmap)
            try:
                if not u.PrintWindow(hwnd,memory,3):raise RuntimeError('Application rendering failed')
                import struct
                header=ctypes.create_string_buffer(struct.pack('<IiiHHIIiiII',40,w,-h,1,32,0,w*h*4,0,0,0,0))
                pixels=ctypes.create_string_buffer(w*h*4)
                if not g.GetDIBits(memory,bitmap,0,h,pixels,header,0):raise RuntimeError('Application bitmap failed')
                Image.frombytes('RGB',(w,h),pixels.raw,'raw','BGRX').save(args.screenshot)
            finally:
                g.SelectObject(memory,previous);g.DeleteObject(bitmap);g.DeleteDC(memory);u.ReleaseDC(hwnd,dc)
        app.withdraw()
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);template=tmp/'sample.pin';synthetic_template(template)
            audio=tmp/'sample.mp3';audio.write_bytes(synthetic_mp3())
            app.template.set(str(template));app.folder.set(str(tmp));app.rights.set(True)
            track=app.catalog.tracks[0]
            with patch('saypen_pin.app.filedialog.askopenfilename',return_value=str(audio)):
                app._pick_audio(track)
            app._clear_audio(track);assert track.key not in app.selected
            app.selected[track.key]=str(audio);app._refresh_count();app._show_book()
            with patch('saypen_pin.app.messagebox.showinfo'),patch('saypen_pin.app.messagebox.showerror') as error:
                app._start();deadline=time.monotonic()+30
                while app.busy and time.monotonic()<deadline:
                    app.update();time.sleep(.02)
                assert not app.busy and app.output, 'GUI worker failed to finish'
                assert not error.called, 'GUI displayed an error'
                assert (app.output/'BOOK/0299000_bk.pin').is_file()
        print('GUI selection, clearing, worker, progress and completion: PASS')
    finally:app.destroy()

if __name__=='__main__':main()
