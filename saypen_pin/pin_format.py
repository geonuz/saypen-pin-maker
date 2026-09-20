"""Read-only parser for the XOR PIN variant observed on RTR-4000BS V0042.

Only structurally established fields are named. Audio length is an allocated
sector count, not the exact MP3 byte length. No third-party dependencies.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

KEY = bytes.fromhex('5432035683804435742379643423911234242578321235670728013675a243b212312f129112aab40c9932192e918093fd24147432911481120131ffa2013398')

def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(4*1024*1024), b''):
            h.update(b)
    return h.hexdigest()

def xor_block(raw, offset):
    if not any(raw):
        return raw
    return bytes(v ^ KEY[(offset+i) % 64] for i, v in enumerate(raw))

def be(b, start, size):
    return int.from_bytes(b[start:start+size], 'big')

def frame_size(b):
    if len(b) < 4:
        return 0
    h = int.from_bytes(b[:4], 'big')
    if h >> 21 != 0x7ff:
        return 0
    ver, layer, rate, freq = (h>>19)&3, (h>>17)&3, (h>>12)&15, (h>>10)&3
    if ver == 1 or layer != 1 or rate in (0,15) or freq == 3:
        return 0  # Layer III only; free-format intentionally unsupported.
    rates = [0,32,40,48,56,64,80,96,112,128,160,192,224,256,320] if ver == 3 else [0,8,16,24,32,40,48,56,64,80,96,112,128,144,160]
    sample = [44100,48000,32000][freq] // (1 if ver == 3 else 2 if ver == 2 else 4)
    return (144000 if ver == 3 else 72000)*rates[rate]//sample + ((h>>9)&1)

def audio_probe(b):
    p = 0
    kind = 'MPEG'
    if b.startswith(b'ID3'):
        if len(b) < 10 or any(x & 128 for x in b[6:10]):
            return {'valid': False, 'reason': 'invalid ID3 size'}
        p = 10 + sum(v << shift for v,shift in zip(b[6:10], (21,14,7,0)))
        if b[3] == 4 and b[5] & 16:
            p += 10
        kind = 'ID3+MPEG'
    start = p
    frames = 0
    while p+4 <= len(b):
        n = frame_size(b[p:p+4])
        if not n or p+n > len(b):
            break
        frames += 1
        p += n
    return {'valid': frames >= 3, 'kind': kind, 'mpeg_offset': start,
            'frames': frames, 'mpeg_end': p, 'trailing_bytes': len(b)-p}

class Pin:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        if len(self.data) < 64 or self.data[60:64] != KEY[60:64]:
            raise ValueError('Unsupported header: expected observed XOR PIN signature')
        self.header = xor_block(self.data[:64], 0)
        self.ranges = {name: (be(self.header,o,2), be(self.header,o+2,2))
                       for name,o in [('special',6),('ordinary',14),('sentence',18),('page',38)]}
        def count(name):
            a,z = self.ranges[name]
            if z < a:
                raise ValueError('Reversed index range')
            return z-a+1 if z != a else 0
        self.counts = {k: count(k) for k in self.ranges}
        self.meta_offset = 64 + self.counts['special']*16
        self.ordinary_offset = self.meta_offset+32
        self.sentence_offset = self.ordinary_offset+self.counts['ordinary']*32
        self.page_offset = self.sentence_offset+self.counts['sentence']*48
        self.index_end = self.page_offset+self.counts['page']*16
        if self.index_end > len(self.data):
            raise ValueError('Index exceeds file')
        if self.counts['sentence'] or self.counts['page']:
            raise ValueError('Sentence/page records not validated in supplied corpus')
        self.meta = self.decode(self.meta_offset,32)
        if be(self.meta,0,2) != 64101:
            raise ValueError('Unexpected book-entry ID')

    def decode(self, offset, size):
        if offset < 0 or offset+size > len(self.data):
            raise ValueError('Record out of bounds')
        return xor_block(self.data[offset:offset+size],offset)

    def records(self):
        yield {'kind':'book_entry','offset':self.meta_offset,'slot':64101,'decoded':self.meta}
        for name,size,base in [('special',16,64),('ordinary',32,self.ordinary_offset)]:
            for i in range(self.counts[name]):
                off = base+i*size
                if not any(self.data[off:off+size]):
                    continue
                yield {'kind':name,'offset':off,'slot':self.ranges[name][0]+i,'decoded':self.decode(off,size)}

    def references(self):
        for r in self.records():
            b = r['decoded']
            layouts = [(25,28)] if r['kind']=='book_entry' else [(2,5),(7,10)] if r['kind']=='special' else [(3,6),(8,11)]
            for ch,(s,n) in enumerate(layouts):
                sector,length = be(b,s,3),be(b,n,2)
                if sector or length:
                    yield {k:v for k,v in r.items() if k!='decoded'} | {'channel':ch,'sector':sector,'sectors':length,'audio_offset':sector*512,'allocation':length*512}
            if r['kind']=='special':
                ptr = be(b,12,4)
                if ptr:
                    if ptr < self.index_end or ptr+16 > len(self.data) or ptr%16:
                        raise ValueError(f'Invalid auxiliary pointer {ptr:#x}')
                    aux = self.decode(ptr,16)
                    for ch in range(3):
                        sector,length=be(aux,ch*5,3),be(aux,ch*5+3,2)
                        if sector or length:
                            yield {'kind':'auxiliary','offset':ptr,'slot':r['slot'],'channel':ch,'sector':sector,'sectors':length,'audio_offset':sector*512,'allocation':length*512}

    def validate(self):
        errors=[]
        records=list(self.records())
        for r in records:
            if be(r['decoded'],0,2) != r['slot']:
                errors.append(f"ID mismatch at {r['offset']:#x}")
        refs=list(self.references())
        unique={}
        for r in refs:
            key=(r['audio_offset'],r['allocation'])
            if key not in unique:
                off,n=key
                p = audio_probe(self.data[off:off+n]) if off>=self.index_end and n>0 and off+n<=len(self.data) else {'valid':False,'reason':'bounds'}
                unique[key]=p
                if not p['valid']:
                    errors.append(f'Invalid audio {off:#x} length {n}: {p}')
        sorted_audio=sorted(unique)
        gaps={}
        for (off,n),(nextoff,_) in zip(sorted_audio,sorted_audio[1:]):
            gap=nextoff-off-n
            gaps[str(gap)]=gaps.get(str(gap),0)+1
        return {'file':str(self.path),'size':len(self.data),'sha256':hashlib.sha256(self.data).hexdigest(),
                'header_hex':self.header.hex(),'ranges':self.ranges,'counts':self.counts,
                'meta_offset':self.meta_offset,'ordinary_offset':self.ordinary_offset,'index_end':self.index_end,
                'occupied_records':len(records),'audio_references':len(refs),'unique_audio':len(unique),
                'gap_bytes_histogram':gaps,'errors':errors}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('pin',type=Path)
    ap.add_argument('--csv',type=Path,help='Export populated records (exclusive creation)')
    ap.add_argument('--audio-csv',type=Path)
    ap.add_argument('--json',type=Path)
    a=ap.parse_args()
    pin=Pin(a.pin)
    report=pin.validate()
    if a.csv:
        with a.csv.open('x',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=['kind','offset','slot','decoded_hex'])
            w.writeheader()
            for r in pin.records():
                w.writerow({k:v for k,v in r.items() if k!='decoded'} | {'decoded_hex':r['decoded'].hex()})
    if a.audio_csv:
        rows=list(pin.references())
        with a.audio_csv.open('x',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    out=json.dumps(report,indent=2,ensure_ascii=False)
    if a.json:
        with a.json.open('x',encoding='utf-8') as f: f.write(out+'\n')
    print(out)
    raise SystemExit(bool(report['errors']))

if __name__=='__main__': main()
