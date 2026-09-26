"""Export path normalization must retain data, binding and model settings."""
import json
from comsol_mcp._w21_execution import exported_input_identity

class Function:
    def __init__(self, filename): self.filename = filename
    def getType(self): return 'Interpolation'
    def getString(self, key):
        assert key == 'filename'
        return self.filename

class Model:
    def __init__(self, filename): self.node = Function(filename)
    def func(self, tag):
        assert tag == 'beam'
        return self.node

def export(path, unit='W/m^2'):
    return 'model.func("beam")\n .set("filename", '+json.dumps(str(path))+');\nmodel.func("beam").set("fununit", "'+unit+'");'

def test_import_paths_are_content_bound(tmp_path):
    a=tmp_path/'extract1.txt'; b=tmp_path/'extract2.txt'
    a.write_bytes(b'0 0 1\n');b.write_bytes(a.read_bytes())
    model=Model('original/source.txt')
    original=exported_input_identity(export(a),model)
    assert original==exported_input_identity(export(b),model)
    b.write_bytes(b'0 0 2\n')
    assert original!=exported_input_identity(export(b),model)
    assert original!=exported_input_identity(export(a,'W'),model)
    assert original!=exported_input_identity(export(a),Model('different/source.txt'))

def test_unreadable_import_never_reuses(tmp_path):
    source=export(tmp_path/'missing.txt');model=Model('original.txt')
    a=exported_input_identity(source,model);b=exported_input_identity(source,model)
    assert a[0]==source and b[0]==source
    assert a!=b

def test_other_function_filename_is_preserved(tmp_path):
    model=Model('source.txt')
    model.node.getType=lambda: 'Image'
    source=export(tmp_path/'missing.png')
    assert exported_input_identity(source,model)==(source,[])
