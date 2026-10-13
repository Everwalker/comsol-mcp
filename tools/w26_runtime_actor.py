"""Fresh W26 plan/gate using the retained original Popen and UNKNOWN actor."""
from __future__ import annotations
import hashlib
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import re
import stat
import subprocess
import time
import sys
import struct
import uuid
from types import SimpleNamespace

from tools.w26_prepare_current_inputs import _helpers
from tools import _w26_original_process as original
from tools import _w26_original_binary as binary
from tools import _w26_original_tls as tls

SDK_LINK = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk")
CLANG = "/Library/Developer/CommandLineTools/usr/bin/clang"
_RETAINED = {}
_OWNER_TOKEN = object()
FULL_REBUILD='FULL_LOCKED_RUNTIME_REBUILD'
CURRENT43='CURRENT43_DEPENDENCIES'
APP_BUNDLE='CURRENT_APPLICATION_BUNDLE'

def activity_scope(plan):
    kind=plan.get('activity_kind',FULL_REBUILD)
    if kind not in (FULL_REBUILD,CURRENT43,APP_BUNDLE):raise RuntimeError('unknown W26 closed activity kind')
    scope={'activity_kind':kind,'budget_seconds':7200 if kind==FULL_REBUILD else 1800,
           'cleanup_reserve_seconds':600 if kind==FULL_REBUILD else 300,'max_make_parallelism':2 if kind==FULL_REBUILD else 0,
           'sampled_rss_stop_bytes':6*1024**3,'max_output_bytes':12*1024**3}
    if kind==CURRENT43:scope.update(prepare_cap_seconds=600,profile_cap_seconds=180)
    if kind==APP_BUNDLE:scope.update(prepare_cap_seconds=600,build_cap_seconds=600,check_cap_seconds=180)
    for key,value in scope.items():
        if key in plan and (type(plan[key]) is not type(value) or plan[key]!=value):raise RuntimeError('W26 closed activity budget/profile differs')
    return scope

def system_record(path):
    """Separate exact root-owned system reader; ordinary input gate unchanged."""
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        first = os.fstat(fd)
        if first.st_uid != 0 or not stat.S_ISREG(first.st_mode) or first.st_mode & 0o022 or first.st_size > 1073741824:
            raise RuntimeError("toolchain file is not bounded root-owned readonly input")
        digest = hashlib.sha256(); size = 0
        while chunk := os.read(fd, 1048576): digest.update(chunk); size += len(chunk)
        last = os.fstat(fd); named = path.lstat()
        fields = lambda x: (x.st_dev, x.st_ino, x.st_uid, x.st_mode, x.st_nlink, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
        if fields(first) != fields(last) or fields(first) != fields(named) or size != first.st_size:
            raise RuntimeError("system first same-FD identity drift")
        return {"path": str(path), "bytes": size, "sha256": digest.hexdigest(), "identity": list(fields(first))}
    finally: os.close(fd)

def sdk_snapshot():
    sdk = SDK_LINK.resolve(strict=True)
    if sdk.stat().st_uid != 0:
        raise RuntimeError("SDK root ownership differs")
    rows = []
    for path in sorted(sdk.rglob("*")):
        if path.is_symlink():
            info = path.lstat()
            if info.st_uid != 0 or not path.resolve().is_relative_to(sdk):
                raise RuntimeError("SDK symlink leaves current bound SDK")
            rows.append({"path": str(path), "kind": "symlink", "target": os.readlink(path), "identity": [info.st_dev, info.st_ino, info.st_mtime_ns]})
        elif path.is_file(): rows.append({"kind": "regular", **system_record(path)})
    tools = [CLANG, "/usr/bin/make", "/usr/bin/lipo", "/usr/bin/perl", "/bin/sh", "/usr/bin/sandbox-exec",
             "/Library/Developer/CommandLineTools/usr/bin/make", "/Library/Developer/CommandLineTools/usr/bin/lipo",
             "/Library/Developer/CommandLineTools/usr/bin/ar", "/Library/Developer/CommandLineTools/usr/bin/ranlib"]
    aliases=[]
    cxx=Path(CLANG+'++')
    if cxx.is_symlink():
        st=cxx.lstat()
        if st.st_uid!=0:raise RuntimeError('CXX tool alias owner differs')
        aliases.append({'path':str(cxx),'target':os.readlink(cxx),'identity':[st.st_dev,st.st_ino,st.st_mtime_ns],
                        'canonical_record':system_record(cxx.resolve())})
    jdk=Path('/Library/Java/JavaVirtualMachines/amazon-corretto-11.jdk/Contents/Home')
    jdk_records=[]
    for name in ('bin/java','release'):
        path=(jdk/name).resolve()
        if path.exists():
            jdk_records.append(system_record(path) if path.stat().st_uid==0 else _helpers().raw_file(path,maximum=1073741824)[1])
    tool_records=[]
    for requested in tools:
        path=Path(requested)
        if path.is_symlink():
            st=path.lstat()
            if st.st_uid!=0:raise RuntimeError('system tool alias owner differs')
            aliases.append({'path':str(path),'target':os.readlink(path),'identity':[st.st_dev,st.st_ino,st.st_mtime_ns],
                            'canonical_record':system_record(path.resolve())})
            tool_records.append(system_record(path.resolve()))
        else:tool_records.append(system_record(path))
    return {"SDK_link": str(SDK_LINK), "SDK_target": str(sdk), "SDK_link_target": os.readlink(SDK_LINK),
            "rows": rows, "executables":tool_records,"compiler_aliases":aliases,
            "JDK_current_readonly_catalogue":jdk_records,"JDK_use_in_this_unit":"NOT_USED_NO_JVM_START",
            "SDK_bytes": sum(r.get("bytes", 0) for r in rows)}

def verify_toolchain(snapshot, *, full=False):
    if str(SDK_LINK.resolve()) != snapshot["SDK_target"] or os.readlink(SDK_LINK) != snapshot["SDK_link_target"]:
        raise RuntimeError("bound SDK changed")
    members={str(p) for p in Path(snapshot['SDK_target']).rglob('*') if p.is_file() or p.is_symlink()}
    if members!={r['path'] for r in snapshot['rows']}:raise RuntimeError('bound SDK complete member set changed')
    for row in snapshot["executables"]:
        if system_record(row["path"]) != row: raise RuntimeError("bound compiler/system executable changed")
    for row in snapshot.get('compiler_aliases',[]):
        path=Path(row['path']);info=path.lstat()
        if not path.is_symlink() or os.readlink(path)!=row['target'] or [info.st_dev,info.st_ino,info.st_mtime_ns]!=row['identity'] or system_record(path.resolve())!=row['canonical_record']:
            raise RuntimeError('CXX compiler alias/target differs')
    for row in snapshot["rows"]:
        path = Path(row["path"])
        if row["kind"] == "symlink":
            info = path.lstat()
            if not path.is_symlink() or os.readlink(path) != row["target"] or [info.st_dev, info.st_ino, info.st_mtime_ns] != row["identity"]:
                raise RuntimeError("SDK link first record differs")
        else:
            info = path.lstat()
            current = [info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
            if current != row["identity"] or (full and system_record(path) != {k:v for k,v in row.items() if k != "kind"}):
                raise RuntimeError("SDK member first record differs")

def build_plan(root, prepared, sdk):
    root = Path(root); source = root / "sources"
    fat = root / "install/fat"
    stages = []
    base_env = {"PATH": "/Library/Developer/CommandLineTools/usr/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                "DEVELOPER_DIR": "/Library/Developer/CommandLineTools", "SDKROOT": sdk,
                "LANG": "C", "LC_ALL": "C", "MACOSX_DEPLOYMENT_TARGET": "11.0",
                "CONFIG_SITE": "/dev/null", "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(root / "tmp"),
                "CLANG_MODULE_CACHE_PATH": str(root / "tmp/module-cache")}
    def add(label, argv, cwd, env, cap): stages.append({"label": label, "argv": argv, "cwd": str(cwd), "environment": env, "cap": cap})
    for arch in ("arm64", "x86_64"):
        flags = f"-arch {arch} -isysroot {sdk} -mmacosx-version-min=11.0"
        env = dict(base_env, CC=CLANG, CXX=CLANG+"++", CFLAGS=flags, CXXFLAGS=flags, LDFLAGS=flags)
        for name in ("openssl", "xz", "gdbm", "tcl", "tk"):
            prefix = root / "install" / arch / name
            build = root / "build" / arch / name
            top = prepared["sources"][name]["locked"]["top"]
            src = source / name / top
            if name == "openssl":
                argv = ["/usr/bin/perl", "Configure", "darwin64-" + arch + "-cc", "no-shared", "no-tests", "--prefix="+str(prefix)]
                command_env = dict(env, CC=CLANG, CFLAGS=flags, LDFLAGS=flags)
                install_target = "install_sw"
            elif name == "xz":
                argv = ["/bin/sh", str(src / "configure"), "--prefix="+str(prefix), "--build=aarch64-apple-darwin",
                        "--host="+("aarch64" if arch == "arm64" else "x86_64")+"-apple-darwin", "--disable-shared", "--enable-static",
                        "--enable-threads=posix", "--disable-xz", "--disable-xzdec", "--disable-lzmadec", "--disable-lzmainfo",
                        "--disable-lzma-links", "--disable-scripts", "--disable-doc", "--disable-doxygen", "--disable-nls"]
                command_env = env; install_target = "install"
            elif name == "gdbm":
                argv = ["/bin/sh", str(src / "configure"), "--prefix="+str(prefix), "--disable-shared", "--enable-static", "--without-readline",
                        "--build=aarch64-apple-darwin", "--host="+("aarch64" if arch == "arm64" else "x86_64")+"-apple-darwin"]
                command_env = env; install_target = "install"
            else:
                argv = ["/bin/sh", str(src / "unix/configure"), "--prefix="+str(prefix), "--disable-shared", "--enable-threads", "--disable-framework",
                        "--build=aarch64-apple-darwin","--host="+("aarch64" if arch=='arm64' else 'x86_64')+"-apple-darwin"]
                if name == "tk": argv += ["--enable-aqua=yes", "--with-tcl="+str(root / "install" / arch / "tcl/lib")]
                command_env = env; install_target = "install"
            add(name+"-"+arch+"-configure", argv, build, command_env, 300)
            add(name+"-"+arch+"-build", ["/usr/bin/make", "-j2"] + (["-C", "src/liblzma"] if name == "xz" else []), build, command_env, 900)
            targets=(['install-binaries','install-libraries','install-headers','install-msgs','install-tzdata'] if name=='tcl'
                     else ['install-binaries','install-libraries','install-headers'] if name=='tk' else [install_target])
            add(name+"-"+arch+"-install", ["/usr/bin/make"] + (["-C", "src/liblzma"] if name == "xz" else []) + targets, build, command_env, 300)
    for name, lib in [("openssl","libssl.a"),("openssl","libcrypto.a"),("xz","liblzma.a"),("gdbm","libgdbm.a"),("tcl","libtcl8.6.a"),("tk","libtk8.6.a")]:
        add("fat-"+lib, ["/usr/bin/lipo","-create",str(root/"install/arm64"/name/"lib"/lib),
            str(root/"install/x86_64"/name/"lib"/lib),"-output",str(fat/"lib"/lib)], root, base_env, 180)
    cp_source = source / "python" / prepared["sources"]["python"]["locked"]["top"]
    cp_env = dict(base_env, CC=CLANG, CXX=CLANG+"++", CPPFLAGS="-I"+str(fat/"include"),
        LDFLAGS="-L"+str(fat/"lib"), LIBLZMA_CFLAGS="-I"+str(fat/"include"), LIBLZMA_LIBS=str(fat/"lib/liblzma.a"),
        GDBM_CFLAGS="-I"+str(fat/"include"), GDBM_LIBS=str(fat/"lib/libgdbm.a"),
        TCLTK_CFLAGS="-I"+str(fat/"include")+" -DTCL_THREADS", TCLTK_LIBS="<BOUND_GENERATED_TCL_TK_LINK_FLAGS>")
    add("cpython-configure", ["/bin/sh",str(cp_source/"configure"),"--prefix="+str(root/"install/cpython"),
        "--enable-universalsdk="+sdk,"--with-universal-archs=universal2","--with-openssl="+str(fat),
        "--with-openssl-rpath=no","--with-dbmliborder=gdbm","--with-ensurepip=install"], root/"build/cpython", cp_env, 600)
    add("cpython-build", ["/usr/bin/make","-j2"],root/"build/cpython",cp_env,1800)
    add("cpython-install", ["/usr/bin/make","altinstall"],root/"build/cpython",cp_env,600)
    add("runtime-required-profile", [str(root/'install/cpython/bin/python3.12'),'-I','-S','-B',
        str(Path(__file__).with_name('w26_current_runtime_profile.py')),'--root',str(root),'--mode','build-local'],root/'tmp',base_env,180)
    relocation=root/'build/relocation-probe'
    add("runtime-relocation-profile", [str(relocation/'install/cpython/bin/python3.12'),'-I','-S','-B',
        str(Path(__file__).with_name('w26_current_runtime_profile.py')),'--root',str(relocation),'--mode','relocated'],relocation/'tmp',base_env,180)
    return {"stages": stages, "budget_seconds":7200, "cleanup_reserve_seconds":600,
            "max_make_parallelism":2, "sampled_rss_stop_bytes":6*1024**3,"max_output_bytes":12*1024**3,
            "RSS_is_OS_hard_cap":False,"gui_comsol":False,"physical_Intel_acceptance":"NOT_RUN",
            "native_process_umask":"0077"}

def source_fingerprints(kind=FULL_REBUILD):
    base = Path(__file__).parent
    names = ["w26_runtime_actor.py", "w26_rebuild_current_runtime.py", "w26_prepare_current_inputs.py", "_w26_original_process.py", "_w26_original_binary.py", "_w26_original_tls.py", "w26_runtime_sources.lock.json", "w26_hold_exec.py", "w26_current_runtime_profile.py"]
    if kind==CURRENT43:names+=['w26_dependency_unit.py','w26_dependency_stage.py','w26_dependency_profile.py','full_project_release.py']
    if kind==APP_BUNDLE:names+=['w26_dependency_unit.py','w26_dependency_stage.py','w26_dependency_profile.py','full_project_release.py','w26_application_bundle.py','w26_application_stage.py']
    return {str(base/n): _helpers().raw_file(base/n)[1] for n in names}

def prepare_actor(root,*,tag=None):
    root = Path(root); helper = _helpers()
    tag=tag or uuid.uuid4().hex[:12]
    if not re.fullmatch('[a-z0-9-]{1,48}',tag):raise RuntimeError('candidate tag is not a safe unique name')
    raw, prepared_record = helper.raw_file(root/"PREPARED_SOURCES.json")
    prepared = json.loads(raw)
    helper.secure_dir(root,prepared["root_identity"])
    verify_sources(prepared)
    toolchain = sdk_snapshot()
    toolchain_record = helper.publish(root/("CURRENT_TOOLCHAIN_"+tag+".json"),toolchain)
    plan = build_plan(root,prepared,toolchain["SDK_target"])
    admin=Path(sys.executable).resolve()
    admin_record=helper.raw_file(admin,maximum=1073741824)[1]
    plan['administrative_python']={'path':str(admin),'record':admin_record,'product_runtime':False}
    plan_record = helper.publish(root/("CURRENT_BUILD_PLAN_"+tag+".json"),plan)
    fingerprint_record = helper.publish(root/("CURRENT_ACTOR_SOURCE_"+tag+".json"),source_fingerprints())
    return helper.publish(root/("ACTOR_CANDIDATE_"+tag+".json"),{"status":"READY_FOR_MAIN_SOURCE_REVIEW_NOT_EXECUTION_APPROVED",
        "root_identity":prepared["root_identity"],"prepared":prepared_record,"toolchain":toolchain_record,
        "plan":plan_record,"source":fingerprint_record,"native_Popen":0})

def gate(root, binding_path, binding_sha):
    root=Path(root);helper=_helpers()
    if Path(binding_path) != root/"MAIN_ONCE_BINDING.json": raise RuntimeError("fresh exact Main once path required")
    raw,record=helper.raw_file(Path(binding_path),maximum=1048576)
    if record["sha256"]!=binding_sha:raise RuntimeError("Main once bytes differ")
    binding=json.loads(raw);candidate_name=binding.get('candidate_filename','ACTOR_CANDIDATE.json')
    if not isinstance(candidate_name,str) or not re.fullmatch(r'ACTOR_CANDIDATE(?:_[a-z0-9-]{1,48})?\.json',candidate_name):
        raise RuntimeError('Main candidate filename leaves exact current namespace')
    candidate_raw,candidate_record=helper.raw_file(root/candidate_name)
    candidate=json.loads(candidate_raw);helper.secure_dir(root,candidate["root_identity"])
    scope=activity_scope(candidate);kind=scope['activity_kind']
    for key in ("prepared","toolchain","plan","source"):
        helper.raw_file(Path(candidate[key]["path"]),expected=candidate[key])
    source=json.loads(helper.raw_file(Path(candidate["source"]["path"]),expected=candidate["source"])[0])
    for path,row in source.items():helper.raw_file(Path(path),expected=row)
    statuses={FULL_REBUILD:'W26_RUNTIME_REBUILD',CURRENT43:'W26_CURRENT43_DEPENDENCIES',APP_BUNDLE:'W26_APPLICATION_BUNDLE'}
    required={"status":"MAIN_APPROVED_ONCE_"+statuses[kind],
        "candidate_sha256":candidate_record["sha256"],"root":str(root),"root_identity":candidate["root_identity"],
        **{k:v for k,v in scope.items() if k!='activity_kind'},"GUI":False,"COMSOL":False}
    if kind!=FULL_REBUILD:required['activity_kind']=kind
    if any(type(binding.get(k)) is not type(v) or binding.get(k)!=v for k,v in required.items()) or not isinstance(binding.get("nonce"),str) or len(binding["nonce"])<16:
        raise RuntimeError("fresh Main scope/source/root/budget/nonce binding differs")
    review_raw,review=helper.raw_file(root/"MAIN_SOURCE_REVIEW.json")
    review_status='PASS_'+statuses[kind]+'_SOURCE_FOR_EXACT_CANDIDATE'
    if review["sha256"]!=binding.get("review_sha256") or json.loads(review_raw).get("status")!=review_status or \
            json.loads(review_raw).get("candidate_sha256")!=candidate_record["sha256"]:
        raise RuntimeError("fresh Main exact-candidate source review missing")
    if (root/"UNIT_ONCE_GUARD.json").exists() or (root/"UNIT_ONCE_GUARD.json").is_symlink():raise RuntimeError("once already consumed; no replay")
    return candidate,binding,record

def verify_source_tree(root, rows):
    """Close membership, including implicit directories, before any compiler reads."""
    root=Path(root);helper=_helpers();helper.secure_dir(root)
    expected={}
    for row in rows:
        path=Path(row['path'])
        if path.is_absolute() or '..' in path.parts:raise RuntimeError('source tree member path refused')
        expected[str(path)]=row['kind']
        for parent in path.parents:
            if str(parent)!='.':expected.setdefault(str(parent),'directory')
    actual={}
    for path in root.rglob('*'):
        mode=path.lstat().st_mode
        if stat.S_ISDIR(mode):
            helper.secure_dir(path);kind='directory'
        elif stat.S_ISREG(mode):kind='regular'
        else:raise RuntimeError('source tree added link/special member refused')
        actual[str(path.relative_to(root))]=kind
    if actual!=expected:raise RuntimeError('source tree complete membership differs')
    for row in rows:
        if row['kind']=='regular':
            if Path(row['record']['path'])!=root/row['path']:raise RuntimeError('source tree first-record path differs')
            helper.raw_file(root/row['path'],expected=row['record'])

def verify_sources(prepared):
    helper=_helpers()
    lock=json.loads(helper.raw_file(Path(__file__).with_name('w26_runtime_sources.lock.json'))[0])
    if set(prepared['sources'])!=set(lock):raise RuntimeError('six-source closure membership differs')
    for name,result in prepared['sources'].items():
        helper.raw_file(Path(result['acquisition']['actual_record']['path']),expected=result['acquisition']['actual_record'])
        root=Path(result['acquisition']['actual_record']['path']).parent.parent/'sources'/name
        verify_source_tree(root,result['unpacked']['whole_rows'])
    for name,result in prepared['sources'].items():
        current=result['acquisition']['actual_record']
        if current['sha256']!=lock[name]['sha256'] or current['bytes']!=lock[name]['bytes'] or result['locked']['top']!=lock[name]['top']:
            raise RuntimeError('current Main-selected source lock differs from prepared bytes')

def verify_actor(candidate):
    helper=_helpers();raw,_=helper.raw_file(Path(candidate['source']['path']),expected=candidate['source'])
    for path,row in json.loads(raw).items():helper.raw_file(Path(path),expected=row)

def inspect_fat_libraries(root):
    helper=_helpers();root=Path(root);result={}
    for name,lib in [('openssl','libssl.a'),('openssl','libcrypto.a'),('xz','liblzma.a'),('gdbm','libgdbm.a'),('tcl','libtcl8.6.a'),('tk','libtk8.6.a')]:
        inputs={arch:helper.raw_file(root/'install'/arch/name/'lib'/lib,maximum=134217728)[0] for arch in ('arm64','x86_64')}
        raw,record=helper.raw_file(root/'install/fat/lib'/lib,maximum=268435456)
        result[lib]={'first_record':record,'actual_fat_bytes':binary.fat_archive(raw,inputs),'Intel_execution':'NOT_RUN'}
    return result

def inspect_runtime(root):
    root=Path(root);helper=_helpers();install=root/'install/cpython';files=[install/'bin/python3.12']
    dyn=install/'lib/python3.12/lib-dynload'
    for name in ('_ssl','_hashlib','_lzma','_gdbm','_tkinter','_sqlite3','_ctypes','zlib'):
        found=list(dyn.glob(name+'.*.so'))
        if len(found)!=1:raise RuntimeError('required installed extension identity missing/ambiguous '+name)
        files.extend(found)
    results=[]
    for path in files:
        raw,record=helper.raw_file(path,maximum=268435456)
        parsed=binary.fat_binary(raw,(2,8))
        for arch,slice_raw in binary.fat_slices(raw).items():
            count,size=struct.unpack_from('<II',slice_raw,16);offset=32
            for _ in range(count):
                command,length=struct.unpack_from('<II',slice_raw,offset)
                if command==0x8000001c:raise RuntimeError('RPATH present in required runtime; relocation acceptance refused')
                offset+=length
        results.append({'record':record,'fat_MachO':parsed,'RPATH':'ABSENT','physical_Intel':'NOT_RUN'})
    return results

def preserve_notices(root,prepared):
    root=Path(root);helper=_helpers();output=root/'install/notices';output.mkdir(mode=0o700,exist_ok=False);records=[]
    for name,result in prepared['sources'].items():
        source=root/'sources'/name/result['locked']['top']
        found=[p for p in source.iterdir() if p.is_file() and p.name.lower().startswith(('license','copying','copyright'))]
        if not found:raise RuntimeError('original source license/notice not found '+name)
        destination=output/name;destination.mkdir(mode=0o700)
        for p in found:
            raw,record=helper.raw_file(p);helper.copy_blob(destination/p.name,raw);records.append(record)
    return records

def prepared_link_flags(root):
    """Consume actual generated Tcl/Tk configs as data; never source shell code."""
    helper=_helpers();root=Path(root);all_flags=[];records=[]
    for arch in ('arm64','x86_64'):
        for name,variable in [('tcl','TCL_LIBS'),('tk','TK_LIBS')]:
            path=root/'install'/arch/name/'lib'/(name+'Config.sh')
            raw,record=helper.raw_file(path);records.append(record)
            matches=re.findall(r'^'+variable+r"='([^'\n]*)'",raw.decode(),re.M)
            if len(matches)!=1:raise RuntimeError('actual generated Tcl/Tk link interface is not unique')
            tokens=shlex.split(matches[0]);i=0
            while i<len(tokens):
                token=tokens[i]
                if token in ('-framework','-weak_framework'):
                    if i+1>=len(tokens) or not re.fullmatch(r'[A-Za-z0-9_]+',tokens[i+1]):raise RuntimeError('framework link flag refused')
                    framework=SDK_LINK.resolve()/'System/Library/Frameworks'/(tokens[i+1]+'.framework')
                    if not framework.exists():raise RuntimeError('generated framework not in bound SDK')
                    i+=2
                elif token in ('-pthread','-Wl,-search_paths_first') or re.fullmatch(r'-l(z|pthread|m|dl)',token):i+=1
                else:raise RuntimeError('unbound external generated Tcl/Tk dependency '+token)
            if arch=='arm64':all_flags.append(tokens)
            else:
                expected=all_flags[0 if name=='tcl' else 1]
                if tokens!=expected:raise RuntimeError('architecture Tcl/Tk dependency interfaces differ')
    merged=[]
    for tokens in all_flags:
        i=0
        while i<len(tokens):
            chunk=tokens[i:i+2] if tokens[i] in ('-framework','-weak_framework') else tokens[i:i+1]
            if chunk not in merged:merged.append(chunk)
            i+=len(chunk)
    fat=root/'install/fat/lib'
    return ' '.join([str(fat/'libtk8.6.a'),str(fat/'libtcl8.6.a'),*(t for c in merged for t in c)]),records

def copy_fat_headers(root):
    """Require both-slice header bytes, never last-architecture-wins."""
    helper=_helpers();root=Path(root);destination=root/'install/fat/include';destination.mkdir(parents=True,mode=0o700,exist_ok=False)
    records=[]
    for name in ('openssl','xz','gdbm','tcl','tk'):
        a=root/'install/arm64'/name/'include';b=root/'install/x86_64'/name/'include'
        names_a={str(p.relative_to(a)) for p in a.rglob('*') if p.is_file() or p.is_symlink()}
        names_b={str(p.relative_to(b)) for p in b.rglob('*') if p.is_file() or p.is_symlink()}
        if names_a!=names_b:raise RuntimeError('two architecture installed header membership differs '+name)
        for relative in sorted(names_a):
            raw,first=helper.raw_file(a/relative);other,second=helper.raw_file(b/relative)
            if raw!=other:raise RuntimeError('two architecture header bytes differ '+relative)
            target=destination/relative;target.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
            if target.exists():
                if helper.raw_file(target)[0]!=raw:raise RuntimeError('cross-library shared header content differs')
            else:helper.copy_blob(target,raw)
            records.append({'first_architecture':first,'second_architecture':second,'fat_copy':helper.raw_file(target)[1]})
    return records

def copy_owned_tree(source,destination):
    """Exclusive same-FD copies; only relative links confined to the source tree."""
    source=Path(source);destination=Path(destination);helper=_helpers();helper.secure_dir(source)
    destination.mkdir(mode=0o700,exist_ok=False);records=[]
    for path in sorted(source.rglob('*')):
        target=destination/path.relative_to(source);mode=path.lstat().st_mode
        if stat.S_ISDIR(mode):helper.secure_dir(path);target.mkdir(mode=0o700)
        elif stat.S_ISREG(mode):
            raw,record=helper.raw_file(path,maximum=268435456);helper.copy_blob(target,raw)
            if mode&0o111:target.chmod(0o700)
            records.append(record)
        elif stat.S_ISLNK(mode):
            first=path.lstat();link=os.readlink(path)
            if first.st_uid!=os.getuid() or Path(link).is_absolute() or not path.resolve(strict=True).is_relative_to(source):
                raise RuntimeError('installed relative link escapes owned relocation tree')
            target.symlink_to(link)
            if path.lstat()!=first or os.readlink(path)!=link:raise RuntimeError('installed link changed during closed copy')
            records.append({'path':str(path),'relative_link':link})
        else:raise RuntimeError('installed special member refused')
    return records

def privatize_installed_tree(root):
    """Tighten only quiescent task-owned installation outputs, never input gates."""
    root=Path(root);helper=_helpers();helper.secure_dir(root);rows=[];names=[];links=[]
    identity=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    for path in [root,*sorted(root.rglob('*'))]:
        first=path.lstat();mode=first.st_mode
        if first.st_uid!=os.getuid():raise RuntimeError('installed output owner envelope refused')
        if stat.S_ISLNK(mode):
            link=os.readlink(path)
            if Path(link).is_absolute() or not path.resolve(strict=True).is_relative_to(root):raise RuntimeError('installed output link escapes prefix')
            links.append((path,first,link))
            continue
        if mode&0o022 or mode&0o7000:raise RuntimeError('installed output write/privileged-mode envelope refused')
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)) or (stat.S_ISREG(mode) and first.st_nlink!=1):
            raise RuntimeError('installed output special/hardlink refused')
        names.append((path,first))
    for path,first in names:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|(os.O_DIRECTORY if stat.S_ISDIR(first.st_mode) else 0))
        try:
            if identity(os.fstat(fd))!=identity(first) or identity(path.lstat())!=identity(first):raise RuntimeError('installed output first FD/name drift')
            wanted=0o700 if stat.S_ISDIR(first.st_mode) or first.st_mode&0o111 else 0o600
            if stat.S_IMODE(first.st_mode)!=wanted:os.fchmod(fd,wanted)
            after=os.fstat(fd)
            if identity(after)!=identity(path.lstat()) or stat.S_IMODE(after.st_mode)!=wanted:raise RuntimeError('installed output private-mode publication differs')
            rows.append({'path':str(path),'before_identity':list(identity(first)),'after_identity':list(identity(after))})
        finally:os.close(fd)
    if {str(p) for p in root.rglob('*')}!={str(p) for p,_ in names if p!=root}|{str(p) for p,_,_ in links}:
        raise RuntimeError('installed output membership drift during normalization')
    for path,first,link in links:
        if identity(path.lstat())!=identity(first) or os.readlink(path)!=link:raise RuntimeError('installed relative link first record drift')
        rows.append({'path':str(path),'relative_link':link,'unchanged_identity':list(identity(first))})
    return rows

def prepare_public_certificates(root,prepared):
    root=Path(root);helper=_helpers();folder=root/'install/cpython/share/w26-runtime-fixtures'
    folder.mkdir(parents=True,mode=0o700,exist_ok=False);destination=folder/'runtime-certfixtures';destination.mkdir(mode=0o700)
    source=root/'sources/python'/prepared['sources']['python']['locked']['top']/'Lib/test/certdata'
    rows=[]
    for name,pin in {**tls.FIXTURE_BYTES,'keycert.pem':tls.CERT_PIN}.items():
        raw,record=helper.raw_file(source/name)
        if record['bytes']!=pin['bytes'] or record['sha256']!=pin['sha256']:raise RuntimeError('official CPython public certificate pin differs')
        helper.copy_blob(destination/name,raw);rows.append({'source_first_record':record,'new_copy_first_record':helper.raw_file(destination/name)[1]})
    helper.publish(folder/'CERT_FIXTURE_INPUTS.json',tls.FIXTURE_BYTES)
    helper.publish(root/'logs/PUBLIC_CERTIFICATE_COPIES.json',rows)

def script_closure(root,folder):
    helper=_helpers();root=Path(root);folder=Path(folder);helper.secure_dir(folder)
    files={}
    for path in sorted(folder.rglob('*')):
        if path.is_symlink():raise RuntimeError('Tcl/Tk runtime script symlink refused')
        if path.is_dir():helper.secure_dir(path)
        else:files[str(path.relative_to(folder))]=helper.raw_file(path)[1]
    return {'folder':str(folder),'files':files}

def record_tcltk_install_scope(root,prepared,stage):
    helper=_helpers();root=Path(root);name,arch,_=stage['label'].split('-');makefile=Path(stage['cwd'])/'Makefile'
    raw,record=helper.raw_file(makefile)
    selected=stage['argv'][1:]
    if any(not re.search(r'^'+re.escape(t)+r':',raw.decode(),re.M) for t in selected):raise RuntimeError('selected official Tcl/Tk install target not present')
    source=root/'sources'/name/prepared['sources'][name]['locked']['top']
    omitted=['install-doc']+(['install-demos'] if name=='tk' else ['install-packages'])
    packages=sorted(p.name for p in (source/'pkgs').iterdir() if p.is_dir()) if name=='tcl' else []
    return {'generated_Makefile_first_record':record,'selected_official_targets':selected,'omitted_official_targets':omitted,
            'omitted_optional_bundled_product_packages':packages,'retained':'all standard scripts/modules/encoding/assets/tzdata/msgs/native libs/headers',
            'source_notices_and_original_full_install':'PRESERVED_NOT_REMOVED','component':name,'arch':arch}

class RuntimeOwner:
    """Admission/retention only; original actor owns each unchanged Popen object."""
    def __init__(self,root,plan,*,token=None,began=None):
        if token is not _OWNER_TOKEN:raise RuntimeError('internal owner can only be minted after fresh gate; JSON is not authority')
        self.scope=activity_scope(plan);self.root=Path(root);self.plan=plan;self.began=time.monotonic() if began is None else began;self.deadline=self.began+self.scope['budget_seconds']
        self.process=None;self.original_wait=None;self.stop_reason=None;self.observe_only=False;self.first_cause=None
        self.samples=[];self.expected=None;self.diagnostic_failures=[]
    def remaining(self,preferred):
        value=min(float(preferred),self.deadline-time.monotonic()-self.scope['cleanup_reserve_seconds'])
        if self.stop_reason or value<=0:raise TimeoutError('same shared epoch/cleanup reserve stops next effect')
        return value
    def sample(self):
        output=sum(p.lstat().st_size for p in self.root.rglob('*') if p.is_file() and not p.is_symlink())
        rss=0
        if self.process is not None:
            probe=subprocess.run(['/bin/ps','-axo','pid=,ppid=,pgid=,rss='],capture_output=True,text=True,timeout=5,
                                 env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
            if probe.returncode!=0 or probe.stderr.strip():raise RuntimeError('task-owned RSS process observation unavailable')
            rows=[tuple(map(int,line.split())) for line in probe.stdout.splitlines() if line.strip()]
            rss=sum(row[3]*1024 for row in rows if row[2]==self.process.pid or row[0]==os.getpid())
        sample={'elapsed_seconds':time.monotonic()-self.began,'rss_bytes':rss,'output_bytes':output,'hard_OS_cap':False}
        self.samples.append(sample)
        if rss>6*1024**3 or output>12*1024**3:
            self.stop_reason='SAMPLED_RESOURCE_STOP_NO_NEW_EFFECT_OR_UNPROVEN_SIGNAL'
            raise RuntimeError(self.stop_reason)
        return sample
    def popen(self,argv,**kwargs):
        if argv!=self.expected:raise RuntimeError('native argv differs from admitted exact stage')
        self.remaining(1);self.sample()
        process=subprocess.Popen(argv,**kwargs)
        self.process=process # Before PID/birth/log diagnostics can fail.
        self.original_wait=process.wait
        def controlled_wait(timeout=None):
            if self.observe_only:return self.original_wait(timeout=timeout)
            end=time.monotonic()+(self.remaining(1800) if timeout is None else min(timeout,self.remaining(timeout)))
            while True:
                self.sample()
                remaining=min(end-time.monotonic(),self.remaining(1800))
                if remaining<=0:raise subprocess.TimeoutExpired(argv,timeout)
                try:return self.original_wait(timeout=min(15,remaining))
                except subprocess.TimeoutExpired:continue
        process.wait=controlled_wait
        return process
    def diagnostic_run(self,argv,**kwargs):
        if not argv or argv[0]!='/bin/ps':raise RuntimeError('original actor diagnostic command changed')
        kwargs['env']={'PATH':'/usr/bin:/bin','LC_ALL':'C'}
        kwargs['timeout']=min(kwargs.get('timeout',10),self.remaining(10))
        return subprocess.run(argv,**kwargs)

def run_owned_stage(owner,logsfd,index,label,argv,cwd,env,cap,binding,plan,*,sandbox_path):
    """One retained original actor path shared by the two closed W26 activities."""
    root=owner.root;helper=_helpers();owner.remaining(cap);owner.sample()
    record=helper.publish(root/'logs'/('STAGE_'+str(index)+'.json'),{'argv':argv,'cwd':str(cwd),'environment':env,
                         'nonce':binding['nonce'],'parent_pid':os.getpid()})
    go=root/'logs'/('GO_'+str(index)+'.json')
    wrapper=['/usr/bin/sandbox-exec','-f',str(sandbox_path),plan['administrative_python']['path'],'-I','-S','-B',
             str(Path(__file__).with_name('w26_hold_exec.py')),'--stage',record['path'],'--sha',record['sha256'],'--go',str(go)]
    owner.expected=wrapper
    def birth(process,args):
        observed=original.actual_birth(process,args)
        if observed.get('status')=='OBSERVED':helper.publish(go,{'pid':process.pid,'nonce':binding['nonce'],'stage_sha':record['sha256']})
        return observed
    return original.run(logsfd,label,wrapper,root,env,timeout=owner.remaining(cap),birth_reader=birth)

def retain_first_failure(owner,logsfd,exc,completed,*,stem='BUILD'):
    unknown=isinstance(exc,original.StageUnknown)
    owner.first_cause=exc.original_cause if unknown and exc.original_cause is not None else exc
    if unknown:
        owner.observe_only=True;owner.stop_reason=owner.stop_reason or 'UNKNOWN_RETAIN_ORIGINAL_OWNER_NO_NEXT_STAGE'
    def safe_text(error):
        try:return str(error)
        except BaseException as secondary:
            owner.diagnostic_failures.append(secondary)
            return '<'+type(error).__name__+' text unavailable>'
    try:
        data={'type':type(exc).__name__,'error':safe_text(exc),'original_cause':safe_text(owner.first_cause),'completed':completed,'samples':owner.samples}
        if unknown:data.update(token=exc.token,original_process_retained=exc.process is owner.process)
        _helpers().publish(owner.root/(stem+('_FIRST_UNKNOWN.json' if unknown else '_FIRST_FAILURE.json')),data)
    except BaseException as secondary:
        owner.diagnostic_failures.append(secondary)
        try:original.fallback('FIRST_CAUSE retained; secondary report error type: '+type(secondary).__name__)
        except BaseException as fallback_error:owner.diagnostic_failures.append(fallback_error)
    finally:
        if unknown:original.observe_unknown(logsfd,exc,interval=10)

def execute(root,binding_path,binding_sha):
    began=time.monotonic() # Inclusive source/gate/preflight/prepare epoch, never reset.
    helper=_helpers();root=Path(root)
    candidate,binding,binding_record=gate(root,binding_path,binding_sha)
    current_mask=os.umask(0o077)
    os.umask(current_mask)
    if current_mask!=0o077:raise RuntimeError('new unit must enter with private 0077 inherited umask')
    if activity_scope(candidate)['activity_kind']==CURRENT43:
        from tools.w26_dependency_unit import execute_dependencies
        return execute_dependencies(root,candidate,binding,binding_record,began=began,admission_token=_OWNER_TOKEN)
    if activity_scope(candidate)['activity_kind']==APP_BUNDLE:
        from tools.w26_application_bundle import execute_bundle
        return execute_bundle(root,candidate,binding,binding_record,began=began,admission_token=_OWNER_TOKEN)
    plan=json.loads(helper.raw_file(Path(candidate['plan']['path']),expected=candidate['plan'])[0])
    prepared=json.loads(helper.raw_file(root/'PREPARED_SOURCES.json',expected=candidate['prepared'])[0])
    toolchain=json.loads(helper.raw_file(Path(candidate['toolchain']['path']),expected=candidate['toolchain'])[0])
    owner=RuntimeOwner(root,plan,token=_OWNER_TOKEN,began=began);_RETAINED[str(root)]=owner
    verify_sources(prepared);verify_toolchain(toolchain,full=True)
    helper.raw_file(Path(plan['administrative_python']['path']),expected=plan['administrative_python']['record'])
    helper.publish(root/'UNIT_ONCE_GUARD.json',{'binding_first_record':binding_record,'nonce':binding['nonce'],
                   'epoch':owner.began,'budget':7200,'cleanup_reserve':600,'native_started':False})
    for folder in ('logs','tmp','install/fat/lib','build/cpython'):
        (root/folder).mkdir(parents=True,mode=0o700,exist_ok=False)
    for arch in ('arm64','x86_64'):
        for name in ('openssl','xz','gdbm','tcl','tk'):
            work=root/'build'/arch/name
            if name=='openssl':shutil.copytree(root/'sources'/name/prepared['sources'][name]['locked']['top'],work)
            else:work.mkdir(parents=True,mode=0o700)
    allowed=[root/'logs',root/'tmp',root/'build',root/'install']
    profile='(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n'+''.join('(allow file-write* (subpath '+json.dumps(str(p))+'))\n' for p in allowed)+'(allow file-write* (literal "/dev/null"))\n'
    helper.copy_blob(root/'BUILD_SANDBOX.sb',profile.encode())
    original_subprocess=original.subprocess
    original.subprocess=SimpleNamespace(Popen=owner.popen,run=owner.diagnostic_run,TimeoutExpired=subprocess.TimeoutExpired)
    completed=[];logsfd=os.open(root/'logs',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for index,stage in enumerate(plan['stages']):
            verify_sources(prepared);verify_actor(candidate);verify_toolchain(toolchain)
            owner.remaining(stage['cap']);owner.sample()
            argv=list(stage['argv']);env=dict(stage['environment'])
            if stage['label'].startswith('cpython-'):
                if stage['label']=='cpython-configure':
                    fat=inspect_fat_libraries(root);helper.publish(root/'logs/FAT_LIBRARIES_FIRST.json',fat)
                    headers=copy_fat_headers(root);helper.publish(root/'logs/FAT_HEADERS_FIRST.json',headers)
                flags,producer_records=prepared_link_flags(root);env['TCLTK_LIBS']=flags
                helper.publish(root/'logs'/('TCLTK_LINK_'+str(index)+'.json'),{'value':flags,'producer_first_records':producer_records})
            if stage['label']=='runtime-required-profile':
                prepare_public_certificates(root,prepared)
                for name in ('tcl8','tcl8.6','tk8.6'):
                    producer=root/'install/arm64'/('tcl' if name.startswith('tcl') else 'tk')/'lib'/name
                    records=copy_owned_tree(producer,root/'install/cpython/lib'/name)
                    helper.publish(root/'logs'/('SCRIPT_TREE_'+name+'.json'),records)
                script_record=helper.publish(root/'logs/SCRIPT_EXPECTED.json',[
                    script_closure(root,root/'install'/arch/component/'lib'/name)
                    for arch in ('arm64','x86_64') for component,names in [('tcl',('tcl8','tcl8.6')),('tk',('tk8.6',))] for name in names]
                    +[script_closure(root,root/'install/cpython/lib'/name) for name in ('tcl8','tcl8.6','tk8.6')])
            if stage['label']=='runtime-relocation-profile':
                relocation=root/'build/relocation-probe'
                (relocation/'install').mkdir(parents=True,mode=0o700)
                copied=copy_owned_tree(root/'install/cpython',relocation/'install/cpython')
                helper.publish(root/'logs/RELOCATION_COPY_FIRST.json',copied)
                (relocation/'logs').mkdir(mode=0o700);(relocation/'tmp').mkdir(mode=0o700)
                script_record=helper.publish(relocation/'logs/SCRIPT_EXPECTED.json',[
                    script_closure(relocation,relocation/'install/cpython/lib'/name) for name in ('tcl8','tcl8.6','tk8.6')])
            if stage['label'].startswith('runtime-'):
                argv+=['--script-manifest',script_record['path'],'--script-sha',script_record['sha256'],
                       '--tls-helper-sha',source_fingerprints()[str(Path(__file__).with_name('_w26_original_tls.py'))]['sha256']]
            sandbox_path=root/'BUILD_SANDBOX.sb'
            if stage['label']=='runtime-relocation-profile':
                sandbox_path=root/'BUILD_RELOCATION_SANDBOX.sb'
                helper.copy_blob(sandbox_path,(profile+'(deny file-read* (subpath '+json.dumps(str(root/'install'))+'))\n').encode())
            result=run_owned_stage(owner,logsfd,index,stage['label'],argv,stage['cwd'],env,stage['cap'],binding,plan,sandbox_path=sandbox_path)
            completed.append(result)
            helper.publish(root/'logs'/('PASS_'+str(index)+'.json'),result)
            if stage['label'].endswith('-install'):
                records=privatize_installed_tree(root/'install')
                helper.publish(root/'logs'/('PRIVATE_INSTALL_'+str(index)+'.json'),records)
                if stage['label'].startswith(('tcl-','tk-')):
                    helper.publish(root/'logs'/('CORE_INSTALL_SCOPE_'+str(index)+'.json'),record_tcltk_install_scope(root,prepared,stage))
        verify_sources(prepared);verify_actor(candidate);verify_toolchain(toolchain,full=True)
        native_bytes=inspect_runtime(root);helper.publish(root/'logs/RUNTIME_FAT_BYTES_FIRST.json',native_bytes)
        notices=preserve_notices(root,prepared);helper.publish(root/'logs/SOURCE_LICENSE_NOTICES.json',notices)
        runtime_profile=helper.raw_file(root/'logs/RUNTIME_PROFILE_RESULT.json')[1]
        relocation_profile=helper.raw_file(root/'build/relocation-probe/logs/RUNTIME_PROFILE_RESULT.json')[1]
        helper.publish(root/'BUILD_RESULT.json',{'status':'PASS_CURRENT_RUNTIME_BUILD_ARM_PROFILE_FAT_BYTES_SCOPED','commands':completed,
                       'runtime_profile':runtime_profile,'relocation_profile':relocation_profile,'runtime_fat_bytes':native_bytes,
                       'samples':owner.samples,'physical_Intel':'NOT_RUN','full_product_install':'NOT_RUN'})
        return root/'BUILD_RESULT.json'
    except original.StageUnknown as exc:
        retain_first_failure(owner,logsfd,exc,completed)
        raise
    except BaseException as exc:
        retain_first_failure(owner,logsfd,exc,completed)
        raise
    finally:
        original.subprocess=original_subprocess;os.close(logsfd)

if __name__=='__main__':
    # -m must dispatch through this original module, never a second imported owner.
    current_module=sys.modules[__name__];package=sys.modules['tools']
    if sys.modules.get('tools.w26_runtime_actor',current_module) is not current_module or getattr(package,'w26_runtime_actor',current_module) is not current_module:
        raise RuntimeError('CLI canonical actor module identity differs; no dispatch')
    sys.modules['tools.w26_runtime_actor']=current_module
    package.w26_runtime_actor=current_module
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare-actor','execute'])
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--binding',type=Path)
    parser.add_argument('--binding-sha')
    parser.add_argument('--tag')
    args=parser.parse_args()
    if args.action=='prepare-actor':print(prepare_actor(args.root,tag=args.tag))
    else:
        if args.binding is None or args.binding_sha is None:parser.error('execute requires actual fresh Main binding and hash')
        os.umask(0o077) # This new actor process and its children only; no machine setting.
        print(execute(args.root,args.binding,args.binding_sha))
