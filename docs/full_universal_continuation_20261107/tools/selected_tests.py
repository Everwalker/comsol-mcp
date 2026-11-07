#!/usr/bin/env python3
"""Print (default) or run a finite list of software pytest nodes."""
from __future__ import annotations
import argparse, json, os, shlex, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def selected_nodes():
    data = json.loads((ROOT/'config/test_selection.json').read_text(encoding='utf-8'))
    nodes = data['nodes']
    if not isinstance(nodes, list) or not nodes or len(nodes) != len(set(nodes)):
        raise ValueError('invalid or duplicate test selection')
    for node in nodes:
        if not isinstance(node, str) or node.count('::') != 1:
            raise ValueError('only exact module::test nodes are permitted')
        path, name = node.split('::')
        if (not path.startswith('tests/') or not path.endswith('.py')
                or '..' in path.split('/') or '\\' in path
                or not name.startswith('test_') or not name.isidentifier()):
            raise ValueError('unsafe test node')
    return nodes

def command(nodes, *, collect=False, junit=None):
    argv = [sys.executable, '-m', 'pytest', '-q', '-o', 'addopts=']
    if collect:
        argv.append('--collect-only')
    if junit is not None:
        argv.append('--junitxml='+str(junit))
    return argv + nodes

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--collect-only', action='store_true')
    args = parser.parse_args(argv)
    nodes = selected_nodes()
    repo = args.repo.resolve()
    if not (repo/'pyproject.toml').is_file():
        parser.error('run from the restored repository root or supply --repo')
    for node in nodes:
        if not (repo/node.split('::')[0]).is_file():
            parser.error('missing selected test file: '+node.split('::')[0])
    output = repo/'_state/full_universal_continuation_20261107'
    junit = None if args.collect_only else output/'selected-software-junit.xml'
    cmd = command(nodes, collect=args.collect_only, junit=junit)
    print('Software selection: '+str(len(nodes))+' exact nodes; collected case count may differ.')
    print('Compiler harness / COMSOL native are not selected. Skips are not PASS.')
    print('Command: '+(subprocess.list2cmdline(cmd) if os.name=='nt' else shlex.join(cmd)), flush=True)
    if not args.run:
        print('Not executed. Inspect selected test changes, then add --run.')
        return 0
    if junit is not None:
        output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # Environment additions can append whole modules or load arbitrary plugins,
    # defeating the explicit node selection even when ini addopts is cleared.
    env.pop('PYTEST_ADDOPTS', None)
    env.pop('PYTEST_PLUGINS', None)
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    return subprocess.run(cmd, cwd=repo, env=env, check=False).returncode

if __name__ == '__main__':
    raise SystemExit(main())

