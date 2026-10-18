"""Create a source release after project validation; never publishes it."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile
from check_project import ROOT, check, files

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-unconfigured',action='store_true')
    args=parser.parse_args()
    errors=check(args.allow_unconfigured)
    if errors:
        print('\n'.join(errors))
        return 1
    version=json.loads((ROOT/'custom_components/comelit_native/manifest.json').read_text())['version']
    target=ROOT/'dist'/f'Comelit_ViP_Native_{version}_{"GitHub_Draft" if args.allow_unconfigured else "Source"}.zip'
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(files()):
            if p.suffix=='.pyc': continue
            z.write(p,'comelit-vip-home-assistant/'+p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(target) as z:
        if z.testzip(): raise RuntimeError('ZIP CRC check failed')
    print(target)
    print('SHA256',hashlib.sha256(target.read_bytes()).hexdigest())
    return 0

if __name__=='__main__':
    sys.exit(main())
