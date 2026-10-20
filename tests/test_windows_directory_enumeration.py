"""Production directory parser/enumerator controls at the SDK boundary."""
import ctypes as C
from types import SimpleNamespace

import pytest
from comsol_mcp import _windows_parameter_publish as p


def record(name, *, attributes=0x10, file_id=1, reparse_tag=0, last=True):
    text=name.encode('utf-16-le');v=p._DirectoryInfo()
    v.name_length=len(text);v.attributes=attributes;v.file_id[0]=file_id;v.reparse_tag=reparse_tag
    header=p._DirectoryInfo.name.offset;size=(header+len(text)+7)//8*8
    v.next_offset=0 if last else size
    return (bytes(v)[:header]+text).ljust(size,b'\0')


def enumeration(monkeypatch,pages, *, max_entries=40000):
    calls=[];last_error=[0]
    def native(handle,kind,buffer,size):
        assert handle==123 and size==65536;calls.append(kind)
        page=next(pages)
        if isinstance(page,int):last_error[0]=page;return 0
        C.memmove(buffer,page,len(page));return 1
    monkeypatch.setattr(p.C,'get_last_error',lambda:last_error[0],raising=False)
    monkeypatch.setattr(p.C,'WinError',lambda error:OSError(error,'confirmed native error'),raising=False)
    api=p._API.__new__(p._API);api.k=SimpleNamespace(GetFileInformationByHandleEx=native)
    return api.enumerate_directory(123,max_entries=max_entries),calls


@pytest.mark.parametrize('files',[[],['actual-file']])
def test_validated_dot_page_then_real_end(monkeypatch,files):
    dots=record('.',last=False)+record('..')
    pages=[dots]+([record('actual-file',attributes=0)] if files else [])+[18]
    rows,calls=enumeration(monkeypatch,iter(pages))
    assert [r['name'] for r in rows]==files and calls==[20]+[19]*(len(pages)-1)
    assert p.parse_directory_records(dots)==[]  # Existing caller contract retained.


@pytest.mark.parametrize('pages',[
    [record('.'),record('..'),18],
    [record('.',last=False)+record('file',attributes=0),record('.'),18],
    [record('.'),5],
    [b'\0'*96,18],
    [record('.',attributes=0),18],
    [record('.',attributes=0x410),18],
    [record('.',file_id=0),18],
    [record('.',reparse_tag=1),18],
    [record('.',last=False)+record('.'),18],
    [record('file',attributes=0),record('FILE',attributes=0),18],
])
def test_empty_repeat_native_error_malformed_and_duplicate_refuse(monkeypatch,pages):
    with pytest.raises((p.PublishError,OSError)):
        enumeration(monkeypatch,iter(pages))


def test_entry_cap_still_counts_real_entries(monkeypatch):
    with pytest.raises(p.PublishError,match='entry cap'):
        enumeration(monkeypatch,iter([record('.'),record('file',attributes=0),18]),max_entries=0)
