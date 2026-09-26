"""Build normal wheel from explicit package sources, excluding filesystem metadata.

Staging is disposable and does not modify canonical source. Useful on exFAT,
where AppleDouble sidecars otherwise masquerade as extra dist-info directories.
"""
import argparse, hashlib, json, shutil, subprocess, sys, tempfile
from pathlib import Path

def build(source, destination):
    source=Path(source).resolve();destination=Path(destination).resolve()
    destination.mkdir(parents=True,exist_ok=True)
    files={}
    with tempfile.TemporaryDirectory(prefix='w22-wheel-') as temp:
        stage=Path(temp)
        for f in sorted((source/'comsol_mcp').rglob('*')):
            if not f.is_file() or f.is_symlink() or any(p.startswith('._') or p=='__pycache__' for p in f.parts) or f.suffix in {'.pyc','.pyo'}:continue
            relative=f.relative_to(source);target=stage/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(f,target);files[str(relative)]=hashlib.sha256(f.read_bytes()).hexdigest()
        for name in ['pyproject.toml','README.md','LICENSE']:
            f=source/name
            if f.exists():shutil.copyfile(f,stage/name);files[name]=hashlib.sha256(f.read_bytes()).hexdigest()
        subprocess.run([sys.executable,'-m','pip','wheel',str(stage),'--no-deps','--no-build-isolation','-w',str(destination)],check=True)
    wheels=list(destination.glob('comsol_mcp-*.whl'))
    if len(wheels)!=1:raise ValueError('use a destination containing exactly this candidate wheel')
    receipt={'source_root':str(source),'files':files,'wheel':wheels[0].name,'wheel_sha256':hashlib.sha256(wheels[0].read_bytes()).hexdigest(),'native_status':'NOT_IMPLIED_BY_BUILD'}
    (destination/'build_receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();print(json.dumps(build(a.source,a.out),indent=2))
