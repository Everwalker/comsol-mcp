import importlib.resources as res, json, pathlib, sys
import comsol_mcp
root = pathlib.Path(comsol_mcp.__file__).resolve().parent
pkg = res.files('comsol_mcp')
files = sorted(str(p.relative_to(pkg.root)) for p in pkg.rglob('*') if p.is_file())  # type: ignore[attr-defined]
java = [f for f in files if f.endswith('.java')]
print(json.dumps({'module': str(root), 'file_count': len(files), 'java_sources': java,
                  'has_catalog': any('catalog' in f for f in files),
                  'has_schema': any(f.endswith('.json') and 'schema' in f for f in files)}))
