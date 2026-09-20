import json
import tempfile
import threading
import unittest
from pathlib import Path
from saypen_pin.catalog import Catalog, Track, load_catalog, CatalogError, bundled_catalogs
from saypen_pin.engine import prepare_template, render_pin, build_batch, read_mp3, UserError, Cancelled
from saypen_pin.pin_format import KEY, Pin, be, sha256

def encode(plain,offset):
    return bytes(v^KEY[(offset+i)%64] for i,v in enumerate(plain))

def synthetic_mp3():
    # Generated MPEG-shaped frames, no recorded or copyrighted audio fixtures.
    return (b'\xff\xfb\x90\x64'+bytes(413))*3

def synthetic_template(path):
    start=0x1eda00
    data=bytearray(start)
    header=bytearray(64)
    for at,value in [(6,64001),(8,64094),(14,1),(16,63078)]:header[at:at+2]=value.to_bytes(2,'big')
    header[34:38]=start.to_bytes(4,'big');data[:64]=encode(header,0)
    special=bytearray(16);special[:2]=(64001).to_bytes(2,'big')
    special[2:5]=(start//512).to_bytes(3,'big');special[5:7]=(3).to_bytes(2,'big')
    data[64:80]=encode(special,64)
    meta=bytearray(32);meta[:2]=(64101).to_bytes(2,'big')
    meta[25:28]=(start//512).to_bytes(3,'big');meta[28:30]=(3).to_bytes(2,'big')
    data[0x620:0x640]=encode(meta,0x620)
    audio=synthetic_mp3();data.extend(audio);data.extend(bytes(1536-len(audio)+2048))
    path.write_bytes(data)

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.template=self.root/'내 템플릿.pin';synthetic_template(self.template)
        self.mp3=self.root/'내 음원.mp3';self.mp3.write_bytes(synthetic_mp3())
        self.track=Track('read','테스트 책','Read',299000,'299000.mp3','synthetic test')
        self.catalog=Catalog('test','테스트','',(self.track,))
    def tearDown(self):self.tmp.cleanup()
    def test_compaction_and_audio(self):
        prepared=prepare_template(self.template)
        result=render_pin(prepared,self.mp3.read_bytes())
        path=self.root/'결과.pin';path.write_bytes(result);pin=Pin(path)
        self.assertEqual(pin.validate()['errors'],[])
        self.assertEqual(pin.data[0x640:0x1ed300],bytes(0x1ed300-0x640))
        start=be(pin.meta,25,3)*512
        self.assertEqual(pin.data[start:start+1251],self.mp3.read_bytes())
        self.assertEqual(pin.data[:64],self.template.read_bytes()[:64])
    def test_batch_and_no_overwrite(self):
        original=sha256(self.template);audio_hash=sha256(self.mp3)
        a,report=build_batch(self.catalog,self.template,{'read':self.mp3},self.root,rights_confirmed=True)
        b,_=build_batch(self.catalog,self.template,{'read':self.mp3},self.root,rights_confirmed=True)
        self.assertNotEqual(a,b)
        self.assertTrue((a/'BOOK/0299000_bk.pin').is_file())
        self.assertEqual(report['files'][0]['audio_sha256'],audio_hash)
        self.assertEqual(sha256(self.template),original);self.assertEqual(sha256(self.mp3),audio_hash)
        self.assertNotIn(str(self.root),(a/'manifest.json').read_text(encoding='utf-8'))
    def test_rights_required(self):
        with self.assertRaises(UserError):build_batch(self.catalog,self.template,{'read':self.mp3},self.root)
    def test_preflight_failure_no_output(self):
        before=set(self.root.iterdir());self.mp3.write_bytes(b'not mp3')
        with self.assertRaises(UserError):build_batch(self.catalog,self.template,{'read':self.mp3},self.root,rights_confirmed=True)
        self.assertEqual(before,set(self.root.iterdir()))
    def test_cancel_before_start(self):
        event=threading.Event();event.set()
        with self.assertRaises(Cancelled):build_batch(self.catalog,self.template,{'read':self.mp3},self.root,rights_confirmed=True,cancel=event)
        self.assertFalse(list(self.root.glob('PIN_*')))
    def test_cancel_after_write_keeps_incomplete_separate(self):
        event=threading.Event()
        def progress(text,done,total):
            if done==1:event.set()
        with self.assertRaises(Cancelled):build_batch(self.catalog,self.template,{'read':self.mp3},self.root,rights_confirmed=True,cancel=event,progress=progress)
        folders=list(self.root.glob('*.partial'));self.assertEqual(len(folders),1)
        self.assertFalse((folders[0]/'BOOK').exists())
        self.assertTrue((folders[0]/'미완료_사용하지마세요.txt').exists())
    def test_rejects_header_variation(self):
        data=bytearray(self.template.read_bytes());data[4]^=1;self.template.write_bytes(data)
        with self.assertRaises(UserError):prepare_template(self.template)
    def test_rejects_audio_pointer_out_of_bounds(self):
        data=bytearray(self.template.read_bytes());data[66:69]=encode(b'\xff\xff\xff',66);self.template.write_bytes(data)
        with self.assertRaises(UserError):prepare_template(self.template)
    def test_rejects_truncated_final_frame(self):
        self.mp3.write_bytes(synthetic_mp3()+b'\xff\xfb\x90\x64')
        with self.assertRaises(UserError):read_mp3(self.mp3)
    def test_rejects_empty_mp3(self):
        self.mp3.write_bytes(b'')
        with self.assertRaises(UserError):read_mp3(self.mp3)
    def test_unknown_selection(self):
        with self.assertRaises(UserError):build_batch(self.catalog,self.template,{'not-a-track':self.mp3},self.root,rights_confirmed=True)

class CatalogTests(unittest.TestCase):
    def test_builtin(self):
        catalog=bundled_catalogs()[0]
        self.assertEqual(len(catalog.tracks),24)
        self.assertEqual(catalog.tracks[0].pin_filename,'0299000_bk.pin')
    def test_bad_catalogs(self):
        base={'schema_version':1,'id':'test','title':'Test','adapter':'saypen-xor-book-v1','books':[{'title':'Book','tracks':[{'id':'read','mode':'Read','raw_oid':299000}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'catalog.json'
            for change in ['duplicate','path','boolean','adapter','id']:
                data=json.loads(json.dumps(base))
                track=data['books'][0]['tracks'][0]
                if change=='duplicate':data['books'][0]['tracks'].append(dict(track))
                elif change=='path':track['suggested_filename']='../audio.mp3'
                elif change=='boolean':track['raw_oid']=True
                elif change=='adapter':data['adapter']='execute-python'
                else:track['id']='../../unsafe'
                p.write_text(json.dumps(data),encoding='utf-8')
                with self.assertRaises(CatalogError):load_catalog(p)

if __name__=='__main__':unittest.main()
