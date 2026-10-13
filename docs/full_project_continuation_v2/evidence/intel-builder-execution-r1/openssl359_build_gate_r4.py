"""Fixed official CPython build views; never follow a symbolic link for bytes."""
import hashlib,os,re,stat,json
from pathlib import Path
from acquire_exact_archives import Refusal
from resume_exact_archives_r1 import checked_regular
from tls_binary_r1 import fat_binary

def fields(st):return (st.st_dev,st.st_ino,st.st_uid,st.st_mode,st.st_nlink,st.st_size)

def regular_leaf(directory,name,logical_path,maximum=134217728):
    if Path(name).name!=name or name in ('','.','..'):raise Refusal('not fixed normal physical basename')
    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
    try:
        st=checked_regular(fd)
        if stat.S_IMODE(st.st_mode)&0o022 or st.st_size>maximum:raise Refusal('owned physical leaf mode/bound differs')
        raw=bytearray()
        while block:=os.read(fd,1048576):
            raw.extend(block)
            if len(raw)>maximum:raise Refusal('owned physical leaf grew beyond bound')
        final=checked_regular(fd);named=os.stat(name,dir_fd=directory,follow_symlinks=False)
        if fields(final)!=fields(st) or fields(named)!=fields(st) or len(raw)!=st.st_size:raise Refusal('owned physical leaf identity changed during sameFD read')
        raw=bytes(raw)
        row={'path':str(logical_path),'kind':'regular','identity':{'device':st.st_dev,'inode':st.st_ino},'uid':st.st_uid,'nlink':st.st_nlink,'mode':stat.S_IMODE(st.st_mode),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        return raw,row
    finally:os.close(fd)

def owned_directory(parent,name,device):
    fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
    try:
        st=os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.getuid() or st.st_dev!=device or stat.S_IMODE(st.st_mode)&0o022:raise Refusal('fixed build directory ownership/type/mount/mode differs')
        return fd,st
    except BaseException:os.close(fd);raise

def module_leaf(modules,view,name,build):
    if name not in ('_ssl','_hashlib'):raise Refusal('only two exact required normal module names allowed')
    basename=name+'.cpython-312-darwin.so';literal='../../Modules/'+basename
    before=os.stat(basename,dir_fd=view,follow_symlinks=False)
    if not stat.S_ISLNK(before.st_mode) or before.st_uid!=os.getuid() or before.st_nlink!=1:raise Refusal('official build view is not one owned symbolic link')
    first=os.readlink(basename,dir_fd=view)
    if first!=literal:raise Refusal('official build view literal target differs')
    raw,row=regular_leaf(modules,basename,build/'Modules'/basename)
    after=os.stat(basename,dir_fd=view,follow_symlinks=False)
    if fields(before)!=fields(after) or os.readlink(basename,dir_fd=view)!=first:raise Refusal('official build view identity/text changed during physical read')
    return raw,row,{'path':str(build/'build/lib.macosx-11.0-universal2-3.12'/basename),'kind':'symlink','uid':before.st_uid,'nlink':before.st_nlink,'identity':{'device':before.st_dev,'inode':before.st_ino},'mode':stat.S_IMODE(before.st_mode),'literal_target':first,'followed_for_bytes':False}

def pinned_regular_leaf(directory,name,path,first,maximum=134217728):
    raw,current=regular_leaf(directory,name,path,maximum)
    if current!=first:raise Refusal('first accepted regular identity/UID/nlink/mode/bytes/SHA drifted')
    return raw

def dir_record(st):return {'identity':{'device':st.st_dev,'inode':st.st_ino},'uid':st.st_uid,'mode':stat.S_IMODE(st.st_mode)}

def recheck_regulars(root,modules,build,records):
    if set(records)!={'Makefile','python','_ssl','_hashlib'}:raise Refusal('four original accepted regular members required')
    make_raw=pinned_regular_leaf(root,'Makefile',build/'Makefile',records['Makefile'],1048576)
    from openssl359_successor_r2 import build_python_basename
    name=build_python_basename(make_raw)
    pinned_regular_leaf(root,name,build/name,records['python'])
    for name in ('_ssl','_hashlib'):
        basename=name+'.cpython-312-darwin.so';pinned_regular_leaf(modules,basename,build/'Modules'/basename,records[name])

def recheck_original_acceptance(x,report):
    build=x['build'];root=os.open(build,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);opened=[]
    try:
        st=os.fstat(root);first=report['original_directories']
        if dir_record(st)!=first['root']:raise Refusal('accepted build root changed beforeinstall')
        modules,ms=owned_directory(root,'Modules',st.st_dev);opened.append(modules)
        intermediate,bs=owned_directory(root,'build',st.st_dev);opened.append(intermediate)
        view,vs=owned_directory(intermediate,'lib.macosx-11.0-universal2-3.12',st.st_dev);opened.append(view)
        if any(dir_record(current)!=first[name] for name,current in [('Modules',ms),('build',bs),('view',vs)]):raise Refusal('accepted fixed directory changed beforeinstall')
        recheck_regulars(root,modules,build,report['first_regular_records'])
        for row in report['official_exact_symbolic_views']:
            name=Path(row['path']).name;current=os.stat(name,dir_fd=view,follow_symlinks=False)
            if not stat.S_ISLNK(current.st_mode) or dir_record(current)!={'identity':row['identity'],'uid':row['uid'],'mode':row['mode']} or current.st_nlink!=row['nlink'] or os.readlink(name,dir_fd=view)!=row['literal_target']:raise Refusal('original accepted view changed beforeinstall')
        for parent,name,initial in ((root,'Modules',ms),(root,'build',bs),(intermediate,'lib.macosx-11.0-universal2-3.12',vs)):
            if dir_record(os.stat(name,dir_fd=parent,follow_symlinks=False))!=dir_record(initial):raise Refusal('accepted parent directory swapped beforeinstall')
        if dir_record(build.lstat())!=first['root']:raise Refusal('accepted build root swapped beforeinstall')
        return {'all_four_original_first_records_unchanged':True,'install_dispatch':'NOT_DONE_BY_VERIFIER'}
    finally:
        for fd in reversed(opened):os.close(fd)
        os.close(root)

def capture_build_checkpoint(path,report):
    raw=(json.dumps(report,sort_keys=True,indent=2)+'\n').encode()
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        first=checked_regular(fd);view=memoryview(raw)
        while view:view=view[os.write(fd,view):]
        os.fsync(fd);os.lseek(fd,0,os.SEEK_SET);observed=bytearray()
        while block:=os.read(fd,1048576):observed.extend(block)
        final=checked_regular(fd);named=os.stat(path,follow_symlinks=False)
        if (final.st_dev,final.st_ino,final.st_uid,final.st_mode,final.st_nlink)!=(first.st_dev,first.st_ino,first.st_uid,first.st_mode,first.st_nlink) or fields(final)!=fields(named) or bytes(observed)!=raw or final.st_size!=len(raw):raise Refusal('original build checkpoint write/identity/raw drifted')
        record={'path':str(path),'kind':'regular','mode':stat.S_IMODE(first.st_mode),'identity':{'device':first.st_dev,'inode':first.st_ino},'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        # Both tuple entries are immutable bytes retained in the actor. No
        # metadata-file replacement or subsequent dictionary adoption is used.
        return (json.dumps(record,sort_keys=True).encode(),raw)
    finally:os.close(fd)

def verify_preinstall_checkpoint(x,accepted):
    from intel_crypto_successor_r1 import checkpoint_bytes
    if not isinstance(accepted,tuple) or len(accepted)!=2 or any(not isinstance(value,bytes) for value in accepted):raise Refusal('immutable actor build acceptance missing')
    record=json.loads(accepted[0]);path=x['root']/'REQUIRED_BUILD_OUTPUTS_VERIFIED.json'
    if record['path']!=str(path):raise Refusal('original build checkpoint path changed')
    raw,_=checkpoint_bytes(path,record)
    if raw!=accepted[1]:raise Refusal('original build checkpoint raw changed')
    return recheck_original_acceptance(x,json.loads(accepted[1]))

def required_build_outputs(x,command):
    from openssl359_successor_r2 import record,checkpoint_bytes,build_python_basename
    if command['argv']!=['/usr/bin/make','-j2'] or command['rc']!=0 or not command['owned_process_terminal'] or not command['owned_group_absent']:raise Refusal('exact actual terminal make-j2 required')
    logs=[]
    for suffix in ('stdout','stderr'):
        name=command[suffix]
        if Path(name).name!=name:raise Refusal('build raw log escaped owned basename')
        row=record(x['logs']/name,maximum=16777216);raw,_=checkpoint_bytes(Path(row['path']),row)
        if re.search(rb'^\[ERROR\] (?:_ssl|_hashlib)\b',raw,re.MULTILINE):raise Refusal('actual build helper required module ERROR; stop beforeinstall')
        logs.append(row)
    build=x['build'];root=os.open(build,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);opened=[]
    try:
        st=os.fstat(root)
        if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)&0o022:raise Refusal('owned build root mode/UID differs')
        modules,ms=owned_directory(root,'Modules',st.st_dev);opened.append(modules)
        intermediate,bs=owned_directory(root,'build',st.st_dev);opened.append(intermediate)
        view,vs=owned_directory(intermediate,'lib.macosx-11.0-universal2-3.12',st.st_dev);opened.append(view)
        make_raw,make_record=regular_leaf(root,'Makefile',build/'Makefile',1048576)
        main_raw,main_record=regular_leaf(root,build_python_basename(make_raw),build/build_python_basename(make_raw))
        artifacts={'python':{'file':main_record,'slices':fat_binary(main_raw,(2,))}};links=[]
        for name in ('_ssl','_hashlib'):
            raw,row,link=module_leaf(modules,view,name,build);links.append(link)
            artifacts[name]={'file':row,'slices':fat_binary(raw,(6,8))}
        first_records={'Makefile':make_record,**{name:row['file'] for name,row in artifacts.items()}}
        recheck_regulars(root,modules,build,first_records)
        # FDs anchor every read. A parent name swapped while they were open
        # cannot turn an old descriptor into an accepted new current build tree.
        for parent,name,fd,initial in ((root,'Modules',modules,ms),(root,'build',intermediate,bs),(intermediate,'lib.macosx-11.0-universal2-3.12',view,vs)):
            current=os.stat(name,dir_fd=parent,follow_symlinks=False)
            if (current.st_dev,current.st_ino,current.st_uid,current.st_mode)!=(initial.st_dev,initial.st_ino,initial.st_uid,initial.st_mode):raise Refusal('fixed build parent directory swapped during physical gate')
        for row in links:
            basename=Path(row['path']).name;current_link=os.stat(basename,dir_fd=view,follow_symlinks=False)
            if {'device':current_link.st_dev,'inode':current_link.st_ino}!=row['identity'] or current_link.st_uid!=row['uid'] or current_link.st_nlink!=row['nlink'] or stat.S_IMODE(current_link.st_mode)!=row['mode'] or not stat.S_ISLNK(current_link.st_mode) or os.readlink(basename,dir_fd=view)!=row['literal_target']:raise Refusal('official symbolic view changed before whole gate completed')
        current=build.lstat()
        if (current.st_dev,current.st_ino,current.st_uid,current.st_mode)!=(st.st_dev,st.st_ino,st.st_uid,st.st_mode):raise Refusal('build root swapped during physical gate')
        return {'first_regular_records':first_records,'original_directories':{'root':dir_record(st),'Modules':dir_record(ms),'build':dir_record(bs),'view':dir_record(vs)},'required_build_binaries':artifacts,'actual_build_Makefile':make_record,'official_exact_symbolic_views':links,'actual_helper_logs':logs,'required_module_ERROR_absent':True,'general_symlink_follow':False,'runtime_acceptance':'NOT_RUN'}
    finally:
        for fd in reversed(opened):os.close(fd)
        os.close(root)
