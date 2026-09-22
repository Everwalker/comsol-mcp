import importlib.metadata as md, importlib.resources as res, json, pathlib
import comsol_mcp
root = pathlib.Path(comsol_mcp.__file__).resolve().parent
files = sorted(str(p.relative_to(res.files('comsol_mcp'))) for p in res.files('comsol_mcp').rglob('*') if p.is_file())
lower = [f.lower() for f in files]
eps = {e.name: e.value for e in md.entry_points(group='console_scripts') if e.name == 'comsol-mcp'}
print(json.dumps({'module': str(root), 'file_count': len(files),
                  'java_sources': [f for f in files if f.endswith('.java')],
                  'has_action_catalog': any(f.endswith('02_action_catalog.json') for f in lower),
                  'has_schema': any('schema' in f and f.endswith('.json') for f in lower),
                  'console_script': eps.get('comsol-mcp')}))
