import importlib.util,pathlib,shutil,types
base=pathlib.Path('/Volumes/SSD/Comsol-MCP/tmp-migration-20261005-001')
spec=importlib.util.spec_from_file_location('migration',base/'migration.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
owned=pathlib.Path('/private/tmp/comsol-v2-runtime-20261007-new');owned.mkdir(exist_ok=False)
receipts=owned/'migration-receipts';receipts.mkdir()
shutil.copy2(base/'receipts'/'extended-scratch-013.archive-verification.json',receipts)
m.RECEIPTS=receipts;m.LOGS=owned/'migration-logs';m.COMMAND_LOG=m.LOGS/'commands.jsonl'
m.command_restore_full(types.SimpleNamespace(source_id='extended-scratch-013',target_parent=str(owned)))
