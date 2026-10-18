"""Set the real repository identity locally. Never creates or pushes a repository."""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', help='GitHub OWNER/REPOSITORY')
    parser.add_argument('--maintainer', help='Codeowner username, defaults to repository owner')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]+', args.repository):
        parser.error('Expected a GitHub OWNER/REPOSITORY, not a URL or shell command.')
    owner = args.maintainer or args.repository.split('/')[0]
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', owner):
        parser.error('Invalid maintainer username.')
    manifest = ROOT/'custom_components/comelit_native/manifest.json'
    current = json.loads(manifest.read_text())['documentation']
    template = 'https://github.com/REPLACE_ME/comelit-vip-home-assistant'
    target = 'https://github.com/' + args.repository
    if current not in (template, target):
        parser.error('Already finalized for a different repository; review the intended move explicitly.')
    for p in ROOT.rglob('*'):
        relative = p.relative_to(ROOT)
        if (not p.is_file() or set(relative.parts) & {'.git','__pycache__','dist','scripts'}
                or p.suffix not in ('.md','.json','.py','.yaml','.yml')):
            continue
        text = p.read_text()
        text = text.replace(template, target).replace('"@REPLACE_ME"', json.dumps('@'+owner))
        if relative.as_posix() == 'repository.yaml':
            text = text.replace('maintainer: REPLACE_ME','maintainer: '+owner)
        p.write_text(text)
    print('Finalized local project:', target)
    print('No GitHub repository was created or changed. Run scripts/check_project.py next.')

if __name__ == '__main__':
    main()
