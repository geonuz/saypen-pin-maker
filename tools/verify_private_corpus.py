"""Optional local regression; never include input/output media in the repository."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from saypen_pin.catalog import bundled_catalogs
from saypen_pin.engine import build_batch
from saypen_pin.pin_format import sha256

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--template',required=True,type=Path)
    ap.add_argument('--audio-dir',required=True,type=Path)
    ap.add_argument('--expected-dir',required=True,type=Path)
    ap.add_argument('--output-dir',required=True,type=Path)
    args=ap.parse_args();catalog=bundled_catalogs()[0]
    selected={t.key:args.audio_dir/t.suggested_filename for t in catalog.tracks}
    folder,report=build_batch(catalog,args.template,selected,args.output_dir,rights_confirmed=True,
                             progress=lambda text,done,total:print(f'{done}/{total} {text}',flush=True))
    for item in report['files']:
        if sha256(args.expected_dir/item['filename'])!=item['sha256']:
            raise AssertionError(f"Golden output mismatch: {item['filename']}")
    result={'all_24_exact_matches':True,'total_bytes':report['total_bytes'],'output':str(folder)}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    (folder/'regression.json').write_text(json.dumps(result,indent=2),encoding='utf-8')

if __name__=='__main__':main()
