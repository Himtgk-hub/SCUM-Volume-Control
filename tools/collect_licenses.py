"""Collect installed dependency notices for a local release; no network access."""
from importlib.metadata import distributions
import json
from pathlib import Path
import shutil
import sys


def main():
    target = Path(sys.argv[1])
    target.mkdir(parents=True, exist_ok=True)
    packages = []
    for dist in distributions():
        name = dist.metadata['Name']
        packages.append({'name': name, 'version': dist.version,
                         'license': dist.metadata.get('License'),
                         'homepage': dist.metadata.get('Home-page')})
        for file in dist.files or []:
            parts = [p.lower() for p in file.parts]
            if (any('license' in p or 'licence' in p or p.startswith('notice')
                    or p.startswith('copying') for p in parts)
                    and '..' not in file.parts):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    destination = target / name / str(file)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.exists():
        shutil.copy2(python_license, target / 'Python-LICENSE.txt')
    (target / 'installed-packages.json').write_text(
        json.dumps(sorted(packages, key=lambda p: p['name'].lower()),
                   ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
