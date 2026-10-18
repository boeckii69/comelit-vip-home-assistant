"""Check release structure, metadata, local docs, artwork and accidental secrets."""
import argparse
import ast
import json
from pathlib import Path
import re
import sys
import yaml
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
IGNORED={'.git','__pycache__','.venv','node_modules','dist'}

def files():
    return [p for p in ROOT.rglob('*') if p.is_file() and not set(p.relative_to(ROOT).parts)&IGNORED]

def check(allow_unconfigured=False):
    errors=[]
    component=ROOT/'custom_components/comelit_native'
    for p in files():
        rel=p.relative_to(ROOT).as_posix()
        try:
            if p.suffix=='.py': ast.parse(p.read_text(), filename=rel)
            elif p.suffix=='.json': json.loads(p.read_text())
            elif p.suffix in ('.yaml','.yml'): yaml.safe_load(p.read_text())
            elif p.suffix=='.png':
                with Image.open(p) as im: im.verify()
        except Exception as exc:
            errors.append(f'{rel}: {exc}')
        if p.suffix in ('.pcap','.pcapng','.exe','.dll','.pem','.key','.zip'):
            errors.append(f'Private/generated binary is not a source artifact: {rel}')
        if p.name in ('settings.json','secrets.yaml','options.json'):
            errors.append(f'Live configuration must not be published: {rel}')
        if p.suffix in ('.py','.md','.json','.yaml','.yml','.txt','.js','.cjs','.sh'):
            text=p.read_text()
            if not allow_unconfigured and not rel.startswith('scripts/') and 'https://github.com/REPLACE_ME/' in text:
                errors.append(f'Unresolved project URL: {rel}')
            if re.search(r'eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}',text):
                errors.append(f'JWT-like secret found: {rel}')
            if re.search(r'(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b',text):
                errors.append(f'Possible identifying MAC address: {rel}')
            if re.search(r'192\.168\.1\.(?:30|62|110|112|199)\b',text):
                errors.append(f'Reference installation address found: {rel}')
        if p.suffix=='.md':
            for target in re.findall(r'\]\(([^)\s]+)\)',p.read_text()):
                if '://' in target or target.startswith('#'): continue
                local=target.split('#')[0]
                if local and not (p.parent/local).exists():
                    errors.append(f'Broken local link in {rel}: {target}')

    manifest=json.loads((component/'manifest.json').read_text())
    for key in ('domain','name','version','documentation','issue_tracker','codeowners'):
        if not manifest.get(key): errors.append('Missing manifest field: '+key)
    if manifest.get('domain')!='comelit_native': errors.append('Existing integration domain changed')
    if manifest['version'] not in (component/'const.py').read_text(): errors.append('Version mismatch in const.py')
    if manifest['version'] not in (component/'www/comelit-call-card.js').read_text(): errors.append('Card version mismatch')
    if not allow_unconfigured and 'REPLACE_ME' in json.dumps(manifest):
        errors.append('Repository identity is not finalized. Run scripts/finalize_repository.py OWNER/REPO.')
    repo=yaml.safe_load((ROOT/'repository.yaml').read_text())
    if repo.get('url')!=manifest['documentation']: errors.append('Repository URLs disagree')
    if manifest['issue_tracker']!=manifest['documentation']+'/issues': errors.append('Issue tracker URL mismatch')
    if len([p for p in (ROOT/'custom_components').iterdir() if p.is_dir() and p.name!='__pycache__'])!=1:
        errors.append('HACS requires one integration directory')
    for name in ('LICENSE','THIRD_PARTY_LICENSE.txt'):
        if (component/name).read_bytes()!=(ROOT/name).read_bytes(): errors.append('Bundled attribution differs: '+name)
    for size,suffix in ((256,''),(512,'@2x')):
        for prefix in ('','dark_'):
            with Image.open(component/f'brand/{prefix}icon{suffix}.png') as im:
                if im.size!=(size,size): errors.append('Unexpected brand icon dimensions')
    def structure(value):
        return {k:structure(v) for k,v in value.items()} if isinstance(value,dict) else None
    strings=json.loads((component/'strings.json').read_text())
    for lang in ('de','en'):
        if structure(json.loads((component/f'translations/{lang}.json').read_text()))!=structure(strings):
            errors.append('Incomplete translation: '+lang)
    addon=json.loads((ROOT/'addons/comelit_external_audio/config.json').read_text())
    if addon['options']['sip_password'] or addon['options']['bridge_key']:
        errors.append('Add-on contains default credentials')
    if addon['options']['ha_ip'] or addon['options']['local_network']:
        errors.append('Add-on must require installation-specific network settings')
    return errors

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--allow-unconfigured',action='store_true')
    args=p.parse_args()
    errors=check(args.allow_unconfigured)
    for error in errors: print('ERROR:',error)
    if errors: return 1
    print('Project checks passed'+(' (local draft; repository identity still to be finalized)' if args.allow_unconfigured else '')+'.')
    return 0

if __name__=='__main__':
    sys.exit(main())
