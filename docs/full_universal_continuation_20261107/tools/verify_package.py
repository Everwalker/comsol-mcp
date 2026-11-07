#!/usr/bin/env python3
"""Verify delivered workpack bytes; no product acceptance is inferred."""
from __future__ import annotations
import hashlib, json, re
from pathlib import Path
from bootstrap import valid_path, safe_ancestors, validate_paths

def verify(root: Path) -> dict:
    errors = []
    seen = set()
    try:
        safe_ancestors(root / 'PACKAGE_MANIFEST.json')
        manifest = json.loads((root/'PACKAGE_MANIFEST.json').read_text(encoding='utf-8'))
        rows = manifest.get('files')
        if not isinstance(rows, list) or not rows:
            raise ValueError('empty or invalid manifest')
        validate_paths(rows)
        for row in rows:
            name = row['path']
            try:
                if name == 'PACKAGE_MANIFEST.json':
                    raise ValueError('manifest cannot include itself')
                seen.add(name)
                expected = row['sha256']
                if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
                    raise ValueError('invalid SHA-256')
                count = row['bytes']
                if type(count) is not int or count < 0:
                    raise ValueError('invalid size')
                path = root.joinpath(*valid_path(name))
                safe_ancestors(path)
                if not path.is_file() or path.stat().st_size != count:
                    raise ValueError('missing file or size mismatch')
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != expected:
                    raise ValueError('SHA-256 mismatch')
            except Exception as exc:
                errors.append({'file': name, 'error': str(exc)})
    except Exception as exc:
        errors.append({'error': str(exc)})
    return {'status': 'WORKPACK_INTEGRITY_PASS' if not errors else 'FAIL',
            'file_count': len(seen), 'errors': errors,
            'product_acceptance': 'NOT_ESTABLISHED'}

if __name__ == '__main__':
    result = verify(Path(__file__).resolve().parents[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if not result['errors'] else 1)

