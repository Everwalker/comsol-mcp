"""Exact four-site official ASN1 accessor backport; never a fuzzy patch."""
import hashlib,os,stat
from pathlib import Path
from acquire_exact_archives import Refusal
BASE_SHA='19beed620c8b9eaec82e21235cf83f505e279ad8ede63652616d3fedea4a1710'
AFTER_SHA='6576392227c33a4bf4ba9f2ce2b0b600e353edfb759d1ec12c9743a9390cc8c7'
REPLACEMENTS=((b'                if (name->d.ip->length == 4) {\n                    unsigned char *p = name->d.ip->data;\n', b'                if (ASN1_STRING_length(name->d.ip) == 4) {\n                    const unsigned char *p = ASN1_STRING_get0_data(name->d.ip);\n'), (b'                } else if (name->d.ip->length == 16) {\n                    unsigned char *p = name->d.ip->data;\n', b'                } else if (ASN1_STRING_length(name->d.ip) == 16) {\n                    const unsigned char *p = ASN1_STRING_get0_data(name->d.ip);\n'), (b'        ostr = PyUnicode_FromStringAndSize((char *)uri->data,\n                                           uri->length);\n', b'        ostr = PyUnicode_FromStringAndSize(\n                   (const char *)ASN1_STRING_get0_data(uri),\n                   ASN1_STRING_length(uri));\n'), (b'            ouri = PyUnicode_FromStringAndSize((char *)uri->data,\n                                               uri->length);\n', b'            ouri = PyUnicode_FromStringAndSize(\n                       (const char *)ASN1_STRING_get0_data(uri),\n                       ASN1_STRING_length(uri));\n'))

def transform(raw):
    if hashlib.sha256(raw).hexdigest()!=BASE_SHA:raise Refusal('exact official CPython3.12.13 _ssl before SHA differs')
    positions=[]
    for before,after in REPLACEMENTS:
        if raw.count(before)!=1:raise Refusal('official accessor site occurrence count differs')
        start=raw.index(before);positions.append((start,start+len(before),after))
    positions.sort();output=bytearray();end=0
    for start,stop,replacement in positions:
        if start<end:raise Refusal('accessor replacement sites overlap')
        output.extend(raw[end:start]);output.extend(replacement);end=stop
    output.extend(raw[end:]);result=bytes(output)
    if hashlib.sha256(result).hexdigest()!=AFTER_SHA:raise Refusal('exact local accessor backport after SHA differs')
    for token in (b'name->d.ip->length',b'name->d.ip->data',b'uri->length',b'uri->data'):
        if token in result:raise Refusal('one of original eight direct field accesses remains')
    return result,{'base':'CPython3.12.13','software_variant':'CPython3.12.13+comsol-asn1-accessor-backport1','before_sha256':BASE_SHA,'after_sha256':AFTER_SHA,'site_blocks':4,'deleted_direct_field_lines':8,'added_accessor_lines':10,'other_bytes_unchanged':True,'upstream_PR':'https://github.com/python/cpython/pull/141848','upstream_patch_sha256':'3dff28a4a2ead3d5c446c3d6094b9695efdfdb0d3582e6e4161825136cd432ae','upstream_commit':'3f598bf8492b14ed3775cf2290053e2e2c555a29','main_commit':'c41fce08a5d7aae8fd9e3fa25e0ba521600e097c','license':'PSF-2.0; retain original CPython LICENSE and source copyright; local backport, not official unmodified3.12.13'}

def apply_fresh(source,evidence):
    fd=os.open(source,os.O_RDWR|os.O_NOFOLLOW)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1 or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size!=191102:raise Refusal('fresh _ssl source owner/mode/link/size mismatch')
        raw=bytearray()
        while data:=os.read(fd,1048576):
            raw.extend(data)
            if len(raw)>262144:raise Refusal('_ssl source exceeds bound')
        result,receipt=transform(bytes(raw))
        original=os.open(evidence/'SSL_ORIGINAL_BEFORE_BACKPORT.c',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            view=memoryview(raw)
            while view:view=view[os.write(original,view):]
            os.fsync(original)
        finally:os.close(original)
        os.lseek(fd,0,os.SEEK_SET);view=memoryview(result)
        while view:view=view[os.write(fd,view):]
        os.ftruncate(fd,len(result));os.fsync(fd)
        current=os.fstat(fd)
        if (current.st_dev,current.st_ino,current.st_uid,current.st_nlink,current.st_mode)!=(st.st_dev,st.st_ino,st.st_uid,st.st_nlink,st.st_mode):raise Refusal('fresh source ownership/identity changed during backport')
        os.lseek(fd,0,os.SEEK_SET);actual=bytearray()
        while data:=os.read(fd,1048576):actual.extend(data)
        if bytes(actual)!=result:raise Refusal('actual written backport bytes differ')
        receipt.update(actual_source=str(source),identity={'device':st.st_dev,'inode':st.st_ino},after_bytes=len(result))
        return receipt
    finally:os.close(fd)
