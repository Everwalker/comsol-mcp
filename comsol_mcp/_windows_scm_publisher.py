"""Original same-process SCM transport publication; never native admission.

SCM Controller integration is a separately reviewed private patch. Public
construction takes no SDK, SID, proof, token or PID supplied by RPC/JSON.
"""
from __future__ import annotations

import base64
import ctypes as C
import json
import io
import hashlib
import os
from pathlib import Path
import re
import secrets
import socket
from http.server import ThreadingHTTPServer

from . import _managed_backend as backend
from . import _windows_scm_publisher_native as n
from ._windows_t037_broker import _require_original_runtime, _require_runtime_identity

_ROOTS = {}
_Lock = backend.ProcessLock
_New, _Init, _Close = _Lock.__new__, _Lock.__init__, _Lock.close
_Server = ThreadingHTTPServer
_ServerInit, _ServerClose = _Server.__init__, _Server.server_close
_Secret, _StageName = secrets.token_urlsafe, secrets.token_hex


def _api():return n._Api()


def _payload_valid(raw):
    doc=n.e._json(raw,16384)
    if type(doc) is not dict or set(doc)!={'port','token','pid','process_start_epoch_ms'}:n.bad('ENDPOINT_SCHEMA')
    for key,upper in (('port',65535),('pid',2**32-1),('process_start_epoch_ms',2**53-1)):
        if type(doc[key]) is not int or not 1<=doc[key]<=upper:n.bad('ENDPOINT_SCHEMA')
    token=doc['token']
    if type(token) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):n.bad('ENDPOINT_SCHEMA')
    if base64.urlsafe_b64encode(base64.b64decode(token+'=',altchars=b'-_',validate=True)).rstrip(b'=').decode()!=token:n.bad('ENDPOINT_SCHEMA')
    return doc


class _Publisher:
    def __repr__(self):return '<original SCM publisher>'

    def _fail(self,error):
        if self._cause is None:self._cause=error
        raise n.PublisherError('ORIGINAL_PUBLISHER_REFUSED',owner=self) from None

    def _scope(self):
        if (_ROOTS.get(id(self)) is not self or self._runtime is not self._binding[0]
                or self._namespace is not self._binding[1] or self._pyhome is not self._binding[2]
                or backend.ProcessLock is not _Lock or _Lock.__new__ is not _New or _Lock.__init__ is not _Init
                or _Lock.close is not _Close or secrets.token_urlsafe is not _Secret or secrets.token_hex is not _StageName):n.bad('PUBLISHER_BINDING')
        _require_runtime_identity(self._runtime)

    def check(self,*,cleanup=False):
        self._scope()
        (_require_runtime_identity(self._runtime) if cleanup else
            _require_original_runtime(self._runtime,running=False))
        if self._held_unknown:n.bad('ORIGINAL_PUBLISHER_UNKNOWN')
        if cleanup and self._withdrawn:
            self._withdraw_completion()
            if self._namespace.owner.retired:self._namespace.api.check()
            else:self._namespace.owner.check()
        else:self._namespace.check()
        if self._lock is not None:
            if type(self._lock) is not _Lock or self._lock.handle is not self._stream:n.bad('SINGLETON_BINDING')
            if self._lock_result is None:n.bad('SINGLETON_ACQUISITION_UNKNOWN')
            if type(self._stream) is not io.TextIOWrapper or self._stream.close!=self._stream_close:n.bad('ORIGINAL_STREAM_CLOSER')

    def _lock_once(self):
        self._scope()
        if self._lock is not None:n.bad('SINGLETON_REPLAY')
        self._lock=_New(_Lock)  # strong root before original constructor effects
        self._lock_args=(self._lock,self._pyhome/'control.lock')
        try:
            self._lock_return=_Init(*self._lock_args)
            if self._lock_return is not None:n.bad('SINGLETON_RESULT_UNKNOWN')
            self._lock_result=(None,)
            self._stream=self._lock.handle
            if type(self._stream) is not io.TextIOWrapper:n.bad('ORIGINAL_STREAM_REQUIRED')
            self._stream_close=self._stream.close
            self._namespace.borrow_stream(self._stream)
        except BaseException:
            self._held_unknown=True
            self._stream=getattr(self._lock,'handle',None)
            raise

    def transport_secret(self):
        try:
            self.check()
            return self._secret
        except BaseException as error:self._fail(error)

    def bind_original(self,daemon,profile_source_sha256):
        try:return self._bind_original(daemon,profile_source_sha256)
        except BaseException as error:self._fail(error)

    def _bind_original(self,daemon,profile_source_sha256):
        self.check()
        from ._control_daemon import ControlDaemon
        from ._windows_t037 import WindowsT037ProfileV2
        if self._daemon is not None or type(daemon) is not ControlDaemon:n.bad('DAEMON_BINDING')
        profile=daemon._windows_t037_profile
        if (type(profile) is not WindowsT037ProfileV2 or profile.service_name!=self._runtime.service_name
                or profile.service_sid!=self._namespace.sid or profile.service_account_sid!=n.e.SY
                or profile.service_start_name.casefold() not in ('localsystem','nt authority\\system')
                or profile.source_sha256!=profile_source_sha256 or daemon.home!=self._pyhome
                or self._namespace.api.path(daemon._windows_t037_profile_path)!=self._namespace._profile_path):n.bad('DAEMON_PROFILE_SOURCE')
        launcher=profile._launch_image('launcher')
        argv_hash=hashlib.sha256(json.dumps(list(self._namespace._argv),ensure_ascii=False,
            sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        if (n.ntpath.normcase(n.e._path(launcher.path))!=n.ntpath.normcase(self._namespace.image)
                or launcher.argv_sha256!=argv_hash):n.bad('DAEMON_LAUNCHER_SOURCE')
        self._daemon=daemon;self._profile=profile;self._profile_source=profile_source_sha256

    def listener(self,handler):
        try:return self._listener(handler)
        except BaseException as error:self._fail(error)

    def _listener(self,handler):
        self.check()
        if self._server is not None or self._daemon is None:n.bad('LISTENER_REPLAY')
        factory=_Server
        if factory.__init__ is not _ServerInit or factory.server_close is not _ServerClose:n.bad('LISTENER_BINDING')
        self._server=factory.__new__(factory)
        self._server_init=(factory,self._server,handler)
        try:
            self._server_return=_ServerInit(self._server,('127.0.0.1',0),handler)
            if self._server_return is not None:n.bad('LISTENER_RESULT_UNKNOWN')
            self._server_created=(None,)
            self._socket=self._server.socket
            address=self._socket.getsockname()
            if (type(address) is not tuple or len(address)!=2 or address[0]!='127.0.0.1'
                    or type(address[1]) is not int or not 1<=address[1]<=65535
                    or self._server.server_port!=address[1] or self._socket.fileno()<0):n.bad('LISTENER_BINDING')
            if self._namespace.api.seal is n._NATIVE and (type(self._socket) is not socket.socket or self._socket.getsockopt(socket.SOL_SOCKET,socket.SO_ACCEPTCONN)!=1):n.bad('LISTENER_BINDING')
        except BaseException:
            self._held_unknown=True
            raise
        return self._server

    def publish(self,server):
        try:
            self.check()
            if server is not self._server or self._server_created is None or self._publication_started:n.bad('PUBLISHER_REPLAY')
            self._publication_started=True
            ns=self._namespace
            ns.actor(self._runtime)  # fresh actual SYSTEM/no-impersonation source, not a caller bit
            if self._daemon._windows_t037_profile is not self._profile:n.bad('DAEMON_PROFILE_SOURCE')
            address=self._socket.getsockname()
            if address!=('127.0.0.1',server.server_port):n.bad('LISTENER_DRIFT')
            self._data=json.dumps(dict(port=server.server_port,token=self._secret,pid=ns.pid,
                process_start_epoch_ms=ns.birth),separators=(',',':')).encode()
            _payload_valid(self._data)
            old=ns.open('control.json')
            self._old=None if old is None else ns.inspect(old)
            if old is not None:_payload_valid(self._old[-1]);ns.release(old)
            self._stage_name='.scm-endpoint-'+_StageName(16)+'.tmp'
            self._stage=ns.create_stage(self._stage_name)
            ns.write(self._stage,self._data)
            self._stage_fact=ns.inspect(self._stage)
            if self._stage_fact[-1]!=self._data or self._stage_fact[3]!=8192:n.bad('STAGE_READBACK')
            expected=[(0,0,0x1F01FF,n.e.SY),(0,0,0x1F01FF,n.e.BA),(0,0,0x1301BF,ns.sid)]
            if ns.reader:expected.append((0,0,0x120089,ns.reader))
            sd=n.e.b.decode_sd(self._stage_fact[1])
            if sd['group']!=n.e.SY or sd['aces']!=tuple(expected):n.bad('STAGE_SECURITY')
            prior=ns.open('control.json')
            current=None if prior is None else ns.inspect(prior)
            if current!=self._old:n.bad('OLD_ENDPOINT_DRIFT')
            if prior is not None:ns.release(prior)
            ns.check()
            self._rename=ns.effect(self._stage,rename=True)
            ns.owner.checkpoint('publisher_rename_ack',self)
            self._complete_publish()
        except BaseException as error:self._fail(error)

    def _complete_publish(self):
        ns=self._namespace
        record=ns._effects.get('rename')
        if record is None or record is not self._rename:n.bad('RENAME_ORIGIN')
        call,handle,io,buffer,kind=record
        if (call.result!=(0,) or call.function is not ns.api.sdk['NtSetInformationFile'] or io.Status!=0
                or io.Information>C.sizeof(buffer) or handle is not self._stage or kind!=10):n.bad('RENAME_COMPLETION_UNKNOWN')
        ns.release(self._stage);ns.close_write_parent()
        if self._final is None:
            self._final=ns.open('control.json')
            if self._final is None:n.bad('FINAL_ENDPOINT_MISSING')
        observed=ns.inspect(self._final)
        if (observed[0][:3]!=self._stage_fact[0][:3] or observed[1:]!=self._stage_fact[1:]):n.bad('FINAL_ENDPOINT_DRIFT')
        self._final_fact=observed;self._ready=True

    def recover_publish_ack(self):
        try:
            self.check()
            if self._ready or self._rename is None:n.bad('ACK_RECOVERY_REFUSED')
            self._complete_publish()
        except BaseException as error:self._fail(error)

    def before_running(self):
        try:return self._before_running()
        except BaseException as error:self._fail(error)

    def _before_running(self):
        self.check()
        if not self._ready or self._stage is None:n.bad('ENDPOINT_NOT_READY')
        for h in (self._stage,self._namespace.writer_parent):
            r=next(r for r in self._namespace.owner.resources if r.storage is h)
            if not r.closed or not r.confirmed or r.close_call is None or r.close_call.result is None or r.close_call.result[0]==0:n.bad('WRITE_HANDLE_NOT_CONFIRMED_CLOSED')
        current=self._namespace.open('control.json')
        if current is None or self._namespace.inspect(current)!=self._final_fact:n.bad('FINAL_ENDPOINT_DRIFT')
        self._namespace.release(current)

    def close_listener_and_withdraw(self):
        try:
            self.close_listener()
            self.check(cleanup=True)
            if self._server is None:n.bad('ORIGINAL_LISTENER_REQUIRED')
            if self._listener_close!=('CONFIRMED',_ServerClose,self._server) or self._socket.fileno()!=-1:n.bad('LISTENER_CLOSE_UNKNOWN')
            if not self._ready:n.bad('ENDPOINT_NOT_READY')
            ns=self._namespace
            if self._withdraw_handle is None:
                self._withdraw_handle=ns.open('control.json',delete=True)
                if self._withdraw_handle is None:n.bad('FINAL_ENDPOINT_MISSING')
            if ns.inspect(self._withdraw_handle)!=self._final_fact:n.bad('FOREIGN_ENDPOINT_PRESERVED')
            ns.release(self._final)
            self._delete=ns.effect(self._withdraw_handle,rename=False)
            ns.owner.checkpoint('publisher_delete_ack',self)
            self._complete_withdraw()
        except BaseException as error:self._fail(error)

    def close_listener(self):
        try:
            self._scope()
            if self._server is None:return
            if self._server_created is None:n.bad('LISTENER_CREATION_UNKNOWN')
            if self._listener_close is None:
                if self._socket.fileno()<0:n.bad('LISTENER_CLOSE_ORIGIN_UNKNOWN')
                self._listener_close=('ENTERED',_ServerClose,self._server)
                result=_ServerClose(self._server)
                if result is not None or self._socket.fileno()!=-1:n.bad('LISTENER_CLOSE_UNKNOWN')
                self._listener_close=('CONFIRMED',_ServerClose,self._server)
            if self._listener_close!=('CONFIRMED',_ServerClose,self._server):n.bad('LISTENER_CLOSE_UNKNOWN')
        except BaseException as error:self._fail(error)

    def _complete_withdraw(self):
        ns=self._namespace;record=ns._effects.get('delete')
        if record is None or record is not self._delete:n.bad('DELETE_ORIGIN')
        call,handle,io,buffer,kind=record
        if call.result!=(0,) or io.Status!=0 or io.Information>C.sizeof(buffer) or handle is not self._withdraw_handle or kind!=13:n.bad('DELETE_COMPLETION_UNKNOWN')
        ns.release(handle)
        if self._withdraw_absence is None:
            absent=ns.open('control.json')
            if absent is not None:n.bad('WITHDRAW_ABSENCE_UNVERIFIED')
            self._withdraw_absence=ns._absence
        self._withdrawn=True

    def recover_withdraw_ack(self):
        """Continue only this original confirmed delete/close bookkeeping."""
        try:
            self.check(cleanup=True)
            if self._withdrawn or self._delete is None:n.bad('ACK_RECOVERY_REFUSED')
            self._complete_withdraw()
        except BaseException as error:self._fail(error)

    def _withdraw_completion(self):
        ns=self._namespace;record=ns._effects.get('delete')
        if record is None or record is not self._delete:n.bad('WITHDRAW_ORIGIN')
        call,handle,io,buffer,kind=record
        if call.result!=(0,) or call.function is not ns.api.sdk['NtSetInformationFile'] or io.Status!=0 or kind!=13 or handle is not self._withdraw_handle:n.bad('WITHDRAW_ORIGIN')
        resource=next((r for r in ns.owner.resources if r.storage is handle and r.owned),None)
        if resource is None or not resource.closed or not resource.confirmed or resource.close_call is None or resource.close_call.result is None or resource.close_call.result[0]==0:n.bad('WITHDRAW_CLOSE_UNKNOWN')
        absent=self._withdraw_absence
        if absent is None or absent[0].result!=(n.NOT_FOUND,) or not absent[1].empty or absent[1].storage.value is not None:n.bad('WITHDRAW_ABSENCE_UNVERIFIED')

    def release_singleton(self):
        try:
            self.check(cleanup=True)
            if not self._withdrawn or self._lock is None:n.bad('WITHDRAW_BEFORE_SINGLETON_RELEASE')
            if not self._namespace.owner.retired:self._namespace.finish()
            self._close_singleton()
            _ROOTS.pop(id(self))
        except BaseException as error:self._fail(error)

    def _close_singleton(self):
        if self._lock_result!=(None,) or self._lock.handle is not self._stream or self._stream.close!=self._stream_close:n.bad('SINGLETON_ORIGIN')
        if self._singleton_close is None:
            self._singleton_close=('ENTERED',_Close,self._lock)
            result=_Close(self._lock)
            if result is not None or not self._stream.closed:n.bad('SINGLETON_CLOSE_UNKNOWN')
            self._singleton_close=('CONFIRMED',_Close,self._lock)
        if self._singleton_close!=('CONFIRMED',_Close,self._lock):n.bad('SINGLETON_CLOSE_UNKNOWN')

    def finish_after_daemon(self):
        if self._namespace._effects.get('rename') is not None:return self.release_singleton()
        return self.abort_before_publication()

    def abort_before_publication(self):
        try:
            self._scope()
            if self._held_unknown or self._namespace._effects.get('rename') is not None:n.bad('PUBLICATION_UNKNOWN')
            self.close_listener()
            ns=self._namespace
            if ns._created_stage is not None:
                stage=ns._created_stage
                record=ns._effects.get('delete')
                if record is None:record=ns.effect(stage,rename=False)
                call,handle,io,_,kind=record
                if (handle is not stage or call.result!=(0,) or io.Status!=0 or kind!=13):n.bad('STAGE_DELETE_UNKNOWN')
                ns.release(stage)
                if ns.open(ns._own_stage_name) is not None:n.bad('STAGE_ABSENCE_UNKNOWN')
            if not ns.owner.retired:ns.finish()
            self._close_singleton()
            _ROOTS.pop(id(self))
        except BaseException as error:self._fail(error)


def prepare_home(home,runtime):
    try:_require_original_runtime(runtime,running=False)
    except BaseException:raise n.PublisherError('ORIGINAL_RUNTIME_REQUIRED') from None
    owner=_Publisher.__new__(_Publisher)
    owner._runtime=runtime;owner._namespace=None;owner._pyhome=Path(home)
    owner._cause=None;owner._held_unknown=False;owner._lock=None;owner._lock_result=None;owner._stream=None;owner._stream_close=None
    owner._daemon=None;owner._server=None;owner._server_created=None;owner._socket=None
    owner._publication_started=False;owner._stage=None;owner._rename=None;owner._delete=None
    owner._final=None;owner._ready=False;owner._withdrawn=False;owner._listener_close=None
    owner._singleton_close=None;owner._withdraw_handle=None;owner._withdraw_absence=None
    _ROOTS[id(owner)]=owner
    try:
        _require_original_runtime(runtime,running=False)
        api=_api();owner._namespace=n._Namespace(api,home,runtime.service_name)
        owner._binding=(runtime,owner._namespace,owner._pyhome)
        owner._namespace.actor(runtime);owner._namespace.prepare_home()
        owner._lock_once()
        owner._secret=_Secret(32)
        return owner
    except BaseException as error:
        # No singleton/native write was entered. Only known readonly ownership
        # may drain; any opaque output/close stays rooted without another call.
        ns=owner._namespace
        if owner._lock is None and ns is not None and not ns.owner.opaque:
            try:
                ns.finish();_ROOTS.pop(id(owner))
            except BaseException:pass
        owner._fail(error)
