"""Versioned original-Bootstrap four-config continuation, not runtime admission.

Pure template checking is intent only. Native substitutions are obtained solely
from this original Bootstrap's newly created nodes and stopped service. No wire
dict, callback, manifest or parsed profile can become an installation owner.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import ntpath
import re
import struct

from . import _windows_host_bootstrap as b
from . import _windows_scm_config_native as native

SCHEMA = "comsol-mcp.windows-scm-config-recipe/3"
SCHEMA_V4 = "comsol-mcp.windows-scm-config-recipe/4"
STOP_GRANT_SCHEMA = "comsol-mcp.windows-original-managed-stop-grant/1"
CONFIG_PATHS = ("profiles/trusted-profile.json", "profiles/host-provider.json",
                "profiles/memory-resource.json", "profiles/disk-resource.json")
SLOT = "$bootstrap"


def _slot(name):
    return {SLOT: name}


def _replace(value, bindings, trail=()):
    if type(value) is dict:
        if SLOT in value:
            b._fields(value, {SLOT})
            name = value[SLOT]
            if type(name) is not str or name not in bindings:
                b.fail("CONFIG_SLOT", "unknown/unresolved Bootstrap slot")
            return bindings[name]
        return {k: _replace(v, bindings, (*trail, k)) for k, v in value.items()}
    if type(value) is list:
        return [_replace(v, bindings, (*trail, i)) for i, v in enumerate(value)]
    if type(value) not in (str, int, bool, float, type(None)):
        b.fail("CONFIG_TEMPLATE", "JSON templates only; no caller function/object")
    return value


def _planned_sd(directory):
    """Exact initial/exclusive-create DACL, checked against SDK conversion.

    Private startup sources keep this SYSTEM-owned, protected SY/BA policy.
    Other code targets retain service RX after original SCM SID lookup; the
    separate state directory receives its explicit approved Modify template.
    """
    aces = []
    for subject in (b.SY, b.BA):
        sid = b._sid_bytes(subject)
        aces.append(struct.pack('<BBHI', 0, 3 if directory else 0, 8 + len(sid), 0x1F01FF) + sid)
    pack = struct.pack
    acl = pack('<BBHHH', 2, 0, 8 + sum(map(len, aces)), 2, 0) + b''.join(aces)
    owner, group = b._sid_bytes(b.SY), b._sid_bytes(b.BA)
    raw = pack('<BBHIIII', 1, 0, 0x9004, 20, 20 + len(owner), 0, 20 + len(owner) + len(group)) + owner + group + acl
    b.check_security(raw, directory=directory)
    return raw


def _state_policy(policy):
    b._fields(policy, {'service_sid','service_access_mask','client'})
    if (policy['service_sid'] != _slot('service_sid') or type(policy['service_access_mask']) is not int
            or not 0 < policy['service_access_mask'] <= 0x1301BF or policy['service_access_mask'] & ~0x1301BF):
        b.fail('CONFIG_STATE_POLICY', 'explicit original service SID and bounded data Modify required')
    client=policy['client']
    if client is not None:
        from ._windows_sid import is_canonical_sid
        b._fields(client,{'sid','directory_access_mask'})
        if (type(client['sid']) is not str or not is_canonical_sid(client['sid'])
                or not client['sid'].startswith('S-1-5-21-')
                or type(client['directory_access_mask']) is not int or client['directory_access_mask'] != 0x1200A0):
            b.fail('CONFIG_STATE_POLICY', 'one explicit actual User client with non-inheritable directory RX required')


def _state_sd(policy,sid):
    _state_policy(policy)
    if type(sid) is not str or not re.fullmatch(r'S-1-5-80-(\d+-){4}\d+',sid):
        b.fail('CONFIG_STATE_POLICY','original SCM-observed service SID required')
    rows=[(b.SY,0x1F01FF,3),(b.BA,0x1F01FF,3),(sid,policy['service_access_mask'],3)]
    if policy['client'] is not None:
        if policy['client']['sid'] in (b.SY,b.BA,sid):
            b.fail('CONFIG_STATE_POLICY','client is not a service/TCB principal')
        rows.append((policy['client']['sid'],policy['client']['directory_access_mask'],0))
    aces=[]
    for subject,mask,flags in rows:
        binary=b._sid_bytes(subject)
        aces.append(struct.pack('<BBHI',0,flags,8+len(binary),mask)+binary)
    acl=struct.pack('<BBHHH',2,0,8+sum(map(len,aces)),len(aces),0)+b''.join(aces)
    owner,group=b._sid_bytes(b.SY),b._sid_bytes(b.BA)
    return struct.pack('<BBHIIII',1,0,0x9004,20,20+len(owner),0,20+len(owner)+len(group))+owner+group+acl


def _typed_stop_grant(recipe, document, project_id):
    """Closed recipe/4 authoring selector only; no projected scope is minted."""
    if recipe['schema'] != SCHEMA_V4:
        return None
    from . import _host_scoped_provider as p
    b._fields(document, {'schema', 'destinations', 'grants'})
    grants = document['grants']
    if type(grants) is not list or len(grants) > p.MAX_GRANTS:
        b.fail('CONFIG_STOP_GRANT', 'bounded approved grant rows required')
    selected = None
    for index, row in enumerate(grants):
        b._fields(row, {'project_id','action','authorization_ref_sha256','scope_sha256','expires_at_utc'})
        marker = row['scope_sha256']
        if type(marker) is not dict:
            continue  # Existing literal grants keep their existing rules.
        b._fields(marker, {'schema','platform','ownership_mode','source','runtime_id'})
        runtime = marker['runtime_id']
        b._fields(runtime, {'id','class'})
        if (selected is not None or marker['schema'] != STOP_GRANT_SCHEMA
                or marker['platform'] != 'windows' or marker['ownership_mode'] != 'originalManagedLifetime'
                or marker['source'] != 'FINAL_TRUSTED_PROFILE'
                or type(runtime['id']) is not str or not p._IDENTIFIER.fullmatch(runtime['id'])
                or runtime['class'] != 'comsol_mcp._session_context.SessionRuntimeConfig'
                or row['project_id'] != project_id or row['action'] != 'session.stop'):
            b.fail('CONFIG_STOP_GRANT', 'one exact original-managed Stop authoring row required')
        selected = index
    if selected is not None:
        row = grants[selected]
        identity = tuple(row[k] for k in ('project_id','action','authorization_ref_sha256'))
        if any(i != selected and tuple(other[k] for k in ('project_id','action','authorization_ref_sha256')) == identity
               for i, other in enumerate(grants)):
            b.fail('CONFIG_STOP_GRANT', 'typed row conflicts with another approved grant')
    permitted = ('templates', CONFIG_PATHS[1], 'grants', selected, 'scope_sha256')
    def audit(value, trail=()):
        if type(value) is dict:
            schema = value.get('schema')
            if ((type(schema) is str and schema.startswith('comsol-mcp.windows-original-managed-stop-grant'))
                    or value.get('source') == 'FINAL_TRUSTED_PROFILE') and trail != permitted:
                b.fail('CONFIG_STOP_GRANT', 'Stop marker outside its one approved scope position')
            for key, child in value.items(): audit(child, (*trail, key))
        elif type(value) is list:
            for i, child in enumerate(value): audit(child, (*trail, i))
    audit(recipe)
    return selected


def _provider_intent(document, *, typed_stop=None):
    """Filesystem-independent portion only; normal provider load remains required.

    Reuse production action/bound constants. Sink ownership/identity and grant
    expiry are rechecked by the unchanged real loader, never certified here.
    """
    from . import _host_scoped_provider as p
    b._fields(document, {"schema", "destinations", "grants"})
    if document["schema"] != p.CONFIG_SCHEMA:
        b.fail("CONFIG_PROVIDER", "existing provider schema required")
    destinations, grants = document["destinations"], document["grants"]
    if (type(destinations) is not list or len(destinations) > p.MAX_DESTINATIONS
            or type(grants) is not list or len(grants) > p.MAX_GRANTS):
        b.fail("CONFIG_PROVIDER", "bounded approved destinations/grants required")
    seen = set()
    for row in destinations:
        b._fields(row, {"project_id", "destination_ref", "sink_root"})
        if (type(row["project_id"]) is not str or not 0 < len(row["project_id"]) <= 128
                or type(row["destination_ref"]) is not str or not p._IDENTIFIER.fullmatch(row["destination_ref"])):
            b.fail("CONFIG_PROVIDER", "malformed approved destination")
        b._absolute(row["sink_root"])
        key = row["project_id"], row["destination_ref"]
        if key in seen:
            b.fail("CONFIG_PROVIDER", "duplicate destination")
        seen.add(key)
    seen = set()
    for index, row in enumerate(grants):
        b._fields(row, {"project_id", "action", "authorization_ref_sha256", "scope_sha256", "expires_at_utc"})
        if (type(row["project_id"]) is not str or not 0 < len(row["project_id"]) <= 128
                or type(row["action"]) is not str or row["action"] not in p._GRANT_ACTIONS):
            b.fail("CONFIG_PROVIDER", "only published, operator-approved grant actions supported")
        b._hex(row["authorization_ref_sha256"])
        if index != typed_stop:
            b._hex(row["scope_sha256"])
        expiry = row["expires_at_utc"]
        try:
            parsed = datetime.strptime(expiry, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != expiry or (parsed - datetime.now(timezone.utc)).total_seconds() > 366 * 86400:
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise b.BootstrapError("CONFIG_PROVIDER", "canonical bounded approved expiry required") from exc
        # The marker is checked as intent, never replaced by a provisional hash.
        scope_key = b.canonical(row['scope_sha256']) if index == typed_stop else row['scope_sha256']
        key = (row['project_id'], row['action'], row['authorization_ref_sha256'], scope_key)
        if key in seen:
            b.fail("CONFIG_PROVIDER", "duplicate grant")
        seen.add(key)


def _disk_intent(document, *, hostname, root, final):
    from . import _w26_os_resources as r
    from ._windows_disk_resources import _expected_root
    b._fields(document, {"schema", "host_id", "platform", "profiles"})
    if document["schema"] != r.DISK_CONFIG_SCHEMA or document["host_id"] != hostname or document["platform"] != "windows":
        b.fail("CONFIG_DISK", "hostname-bound Windows disk config required")
    rows = document["profiles"]
    # One approved project root; no arbitrary root/profile inventory expansion.
    if type(rows) is not list or len(rows) != 1:
        b.fail("CONFIG_DISK", "one exact newly owned project profile required")
    row = rows[0]
    b._fields(row, {"profile_id", "project_id", "root", "storage_id", "disk_budget", "allowed_operations", "trusted_estimates"})
    b._fields(row["root"], {"path", "volume_id", "file_id", "owner_id"})
    if row["root"] != root or row["storage_id"] != "windows-volume:" + root["volume_id"]:
        b.fail("CONFIG_DISK", "root/storage substitution differs from the original root")
    b._fields(row["disk_budget"], {"mode", "reserve_free_bytes", "max_task_reservation_bytes", "quota_profile_id"})
    if type(row["allowed_operations"]) is not list or type(row["trusted_estimates"]) is not list:
        b.fail("CONFIG_DISK", "explicit operations and estimates required")
    estimates = []
    for item in row["trusted_estimates"]:
        b._fields(item, {"operation_id", "estimate_bytes"})
        estimates.append(r.DiskOperationEstimate(**item))
    typed_root = r.PathIdentity(**root)
    if final:
        _expected_root(typed_root)
    profile = r.DiskResourceProfile(row["profile_id"], row["project_id"], "windows", hostname,
        typed_root, row["storage_id"], r.DiskBudgetPolicy(**row["disk_budget"]),
        tuple(row["allowed_operations"]), tuple(estimates))
    return profile


def _project_root(recipe, prefix, directories):
    """Approved new owned root or exact existing readonly-observed root intent."""
    spec = recipe['project_root']
    if type(spec) is not dict:
        b.fail('CONFIG_ROOT', 'closed explicit project-root recipe required')
    if spec.get('kind') == 'NEW_DIRECTORY':
        b._fields(spec, {'kind','relative'})
        project = b._relative(spec['relative'])
        if project not in directories or project.split('/')[0] in {'profiles','service-state','python312','jdk11','materials','offline'}:
            b.fail('CONFIG_ROOT', 'one disjoint newly owned project directory required')
        return dict(path=ntpath.join(prefix,*project.split('/')), volume_id='UNRESOLVED_ORIGINAL_VOLUME',
                    file_id='UNRESOLVED_ORIGINAL_FILE_ID', owner_id=b.SY)
    if spec.get('kind') == 'EXISTING_DIRECTORY':
        b._fields(spec, {'kind','identity'})
        root = spec['identity']
        b._fields(root, {'path','volume_id','file_id','owner_id'})
        b._absolute(root['path'])
        from ._w26_os_resources import PathIdentity
        from ._windows_disk_resources import _expected_root
        _expected_root(PathIdentity(**root))
        if b._within(root['path'],prefix) or b._within(prefix,root['path']):
            b.fail('CONFIG_ROOT', 'existing project root and new namespace must be disjoint')
        return dict(root)
    b.fail('CONFIG_ROOT', 'unsupported/unconfigured project-root mode')


def _validate_documents(documents, *, recipe, prefix, name, files, argv, root, final, trusted_payload=None):
    from ._windows_t037 import parse_profile_v4_document
    from ._w26_memory_resources import parse_memory_profile_document
    from ._windows_t037_network import derive_session_network_intent
    trusted, provider, memory, disk = (documents[p] for p in CONFIG_PATHS)
    payload = b.canonical(trusted) if trusted_payload is None else trusted_payload
    if trusted_payload is not None:
        trusted = json.loads(payload)  # Parse the exact frozen bytes used for the raw source hash.
    profile = parse_profile_v4_document(trusted, source_sha256=b.digest(payload))
    host = profile.host_resource_profile
    policy = host.windows_isolation
    if (host.platform != "windows" or host.host_id != recipe["host"]["hostname"]
            or policy.host_fingerprint_sha256 != recipe["host"]["machine_fingerprint_sha256"]
            or policy.service_name != name or policy.service_start_name != "LocalSystem"
            or policy.service_logon_sid != b.SY or host.service_account_id != b.SY
            or host.launcher_account_id != b.SY or host.root.as_dict() != root):
        b.fail("CONFIG_BINDING", "host domains/service/root must bind the exact Bootstrap intent")
    derive_session_network_intent(profile)  # no dynamic-port/default fallback
    by_path = {ntpath.normcase(ntpath.join(prefix, *f.path.split('/'))): f for f in files}
    for image in host.launch_images:
        item = by_path.get(ntpath.normcase(image.path))
        if item is not None and item.sha256 != image.image_sha256:
            b.fail("CONFIG_IMAGE", "installed image digest differs from approved material")
        if image.role == "launcher" and (image.path != argv[0] or image.argv_sha256 != b.digest(b.canonical(list(argv)))):
            b.fail("CONFIG_ARGV", "launcher must bind the exact all-four SCM argv")
    protected = {r: t for t in profile.finite_targets.files for r in t.roles}
    if protected["config_rx"].path != ntpath.join(prefix, 'profiles') or protected["config_rx"].content_sha256 is not None:
        b.fail("CONFIG_CYCLE", "config_rx must be the owned directory with null content hash")
    if recipe['project_root']['kind'] == 'NEW_DIRECTORY':
        if protected['project_root'].roles != ('project_root',) or any(
                ntpath.normcase(grant.path) == ntpath.normcase(root['path']) and 'WRITE' in grant.rights
                for grant in (*host.file_grants, *policy.acl_grants)):
            b.fail('CONFIG_ROOT_ROLE', 'readonly root holder requires distinct approved mutable leaves')
    for target in profile.finite_targets.files:
        if b._within(target.path, prefix):
            expected = recipe["labels"]["directory" if target.kind == "directory" else "file"]
            if target.mandatory_label != tuple(expected[k] for k in ("rid", "mask", "flags")):
                b.fail("CONFIG_LABEL", "finite target LABEL differs from approved node LABEL")
    _provider_intent(provider, typed_stop=_typed_stop_grant(recipe, provider, host.project_id))
    parsed_memory = parse_memory_profile_document(memory, actual_host_id=recipe["host"]["machine_fingerprint_sha256"],
        actual_platform="windows", source_sha256=b.digest(b.canonical(memory)))
    parsed_disk = _disk_intent(disk, hostname=recipe["host"]["hostname"], root=root, final=final)
    if (parsed_disk.project_id != host.project_id or parsed_disk.disk_budget != host.disk_budget
            or set(parsed_disk.allowed_operations) != set(host.allowed_operations)
            or not set(parsed_memory.allowed_operations) <= set(host.allowed_operations)):
        b.fail("CONFIG_BUDGET", "approved project/operations/disk budgets must agree")
    return profile


def validate_recipe(recipe, *, prefix, service_name, files, argv):
    """Intent checks only. No native acquisition, source flag, grant or authority.

    Check service-SID-independent schema relations using the *approved existing
    AppContainer SID* as a discarded projection. This is not a service SID
    observation or output. Final compilation reruns every production validator
    with the original LookupAccountName result; no provisional bytes are emitted.
    Root IDs are symbolic strings in this discarded projection, never fake IDs.
    """
    try:
        b._fields(recipe, {"schema", "host", "project_root", "owned_directories", "labels", "templates", "state_policy"})
        if recipe["schema"] not in (SCHEMA, SCHEMA_V4):
            b.fail("CONFIG_RECIPE", "versioned finite recipe required")
        b._fields(recipe["host"], {"hostname", "machine_fingerprint_sha256"})
        hostname = recipe["host"]["hostname"]
        if type(hostname) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", hostname):
            b.fail("CONFIG_HOST", "explicit actual hostname required")
        b._hex(recipe["host"]["machine_fingerprint_sha256"])
        _state_policy(recipe['state_policy'])
        directories = recipe["owned_directories"]
        if type(directories) is not list or not 0 <= len(directories) <= 64:
            b.fail("CONFIG_DIRECTORIES", "bounded explicit new directories required")
        all_dirs = {"", "profiles", "service-state"}
        for item in files:
            parts = item.path.split('/')[:-1]
            all_dirs.update('/'.join(parts[:n]) for n in range(1, len(parts)+1))
        seen = set()
        for p in directories:
            b._relative(p)
            if len(ntpath.join(prefix, *p.split('/'))) > 240:
                b.fail('CONFIG_DIRECTORIES', 'owned directory exceeds the bounded native path')
            if p.casefold() in seen or p.casefold() in {d.casefold() for d in all_dirs} or any(
                    p.casefold() == f.path.casefold() or p.casefold().startswith(f.path.casefold() + '/') for f in files):
                b.fail("CONFIG_DIRECTORIES", "duplicate/file-colliding owned directory")
            seen.add(p.casefold())
        all_dirs.update(directories)
        for p in directories:
            if '/' in p and p.rsplit('/', 1)[0] not in all_dirs:
                b.fail("CONFIG_DIRECTORIES", "every new directory parent must be approved")
        root = _project_root(recipe,prefix,directories)
        b._fields(recipe["labels"], {"directory", "file"})
        for policy in recipe["labels"].values():
            native.label_bytes(policy)
        templates = recipe["templates"]
        b._fields(templates, CONFIG_PATHS)
        # Only these slots and recipe/4's closed Stop marker are sanctioned;
        # no general templating, callback or incoming resolved source facts.
        host = templates[CONFIG_PATHS[0]]["host_resource_profile"]
        policy = host["windows_isolation"]
        required = ((policy, "service_sid", "service_sid"), (host, "root", "project_root"),
                    (templates[CONFIG_PATHS[3]]["profiles"][0], "root", "project_root"),
                    (templates[CONFIG_PATHS[3]]["profiles"][0], "storage_id", "storage_id"))
        allowed = set()
        for container, key, name in required:
            if container[key] != _slot(name):
                b.fail("CONFIG_SLOT", "original service/root slots are mandatory")
            allowed.add(id(container[key]))
        for row in templates[CONFIG_PATHS[0]]["finite_targets"]["files"]:
            b._absolute(row["path"])
            if b._within(row["path"], prefix):
                relative = row["path"][len(prefix)+1:].replace('\\', '/') if row["path"] != prefix else ''
                if row["kind"] == "directory" and relative not in all_dirs or row["kind"] == "file" and relative not in {f.path for f in files}:
                    b.fail("CONFIG_TARGET", "owned finite targets must name approved material/directories")
                if relative == 'profiles':
                    if row['kind'] != 'directory' or row['roles'] != ['config_rx']:
                        b.fail('CONFIG_TARGET', 'private source directory cannot be a mutable/code target')
                    key = 'private_directory_descriptor_hex'
                elif recipe['project_root']['kind'] == 'NEW_DIRECTORY' and relative == recipe['project_root']['relative']:
                    if row['kind'] != 'directory' or row['roles'] != ['project_root']:
                        b.fail('CONFIG_ROOT_ROLE', 'exact new root holder cannot be a mutable/code target')
                    key = 'root_holder_descriptor_hex'
                else:
                    key = 'directory_descriptor_hex' if row["kind"] == "directory" else 'file_descriptor_hex'
                if row["descriptor_hex"] != _slot(key):
                    b.fail("CONFIG_SLOT", "owned finite DACL must come from the original fixed SDK policy")
                allowed.add(id(row["descriptor_hex"]))
        def audit(value):
            if type(value) is dict:
                if SLOT in value and (id(value) not in allowed or set(value) != {SLOT}):
                    b.fail("CONFIG_SLOT", "slot outside a sanctioned literal position")
                for child in value.values(): audit(child)
            elif type(value) is list:
                for child in value: audit(child)
        audit(templates)
        projected = _replace(templates, dict(service_sid=policy["appcontainer_sid"], project_root=root,
            storage_id="windows-volume:"+root["volume_id"], directory_descriptor_hex=_planned_sd(True).hex(),
            file_descriptor_hex=_planned_sd(False).hex(), private_directory_descriptor_hex=_planned_sd(True).hex(),
            root_holder_descriptor_hex=_planned_sd(True).hex()))
        _validate_documents(projected, recipe=recipe, prefix=prefix, name=service_name, files=files,
                            argv=argv, root=root, final=False)
        return b.canonical(recipe)
    except b.BootstrapError:
        raise
    except Exception as exc:
        raise b.BootstrapError("INCOMPLETE_INPUT", "four approved config templates failed closed validation") from exc


@dataclass(frozen=True)
class ResolvedConfiguration:
    """Frozen bytes/derivation diagnostics; never a loader or native capability."""
    recipe_sha256: str
    service_sid: str
    root_identity: tuple
    files: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True)
class _NodeAck:
    node: object
    handle: object
    image: object
    label: bytes
    calls: tuple
    handles: tuple
    label_call: object


class _Configuration:
    def __init__(self, original):
        self.original, self.intent, self.raw = original, original.intent, original.intent.original_raw
        self.owner, self.backend = original.owner, original.backend
        self.recipe = json.loads(original.intent.provisioning)
        self.recipe_raw = original.intent.provisioning
        self.native = native.NativeCompanion(original)
        self.resolved = None
        self._original_resolved = None
        self._resolved_bytes = None
        self._resolved_facts = None
        self.external = []
        self.actor = None
        self.pending_ack = None
        self.loaded = []
        self.loader_calls = []
        self.loader_error = None
        self.directories = tuple(sorted(set(original.intent.directories) | set(self.recipe['owned_directories']),
                                        key=lambda p: (p.count('/'), len(p), p)))
        self.expected_namespace = frozenset((*self.directories, *(f.path for f in original.intent.files), *CONFIG_PATHS))
        self.descriptors = {False: self.backend.sd(False), True: self.backend.sd(True)}
        for directory, raw in self.descriptors.items():
            if raw != _planned_sd(directory):
                b.fail("CONFIG_SD", "actual original SDK policy differs from approved exact DACL bytes")
        self.labels = {k: native.label_bytes(v) for k, v in self.recipe['labels'].items()}
        self._descriptors = tuple(sorted(self.descriptors.items()))
        self._labels = tuple(sorted(self.labels.items()))
        self._directories = self.directories
        self._expected_namespace = self.expected_namespace
        self.service_sid = None
        self.final_descriptors = None
        self.state_descriptor = None
        self._final_security = None
        self.initial_context = self.backend.context()
        self._initial_context_facts = tuple(sorted(self.initial_context.items()))
        self._check_client(self.initial_context)

    def check_original(self, original):
        if (original is not self.original or original._configuration is not self
                or original.intent is not self.intent or original.owner is not self.owner
                or original.backend is not self.backend or self.intent.original_raw != self.raw
                or self.intent.provisioning != self.recipe_raw or b.canonical(self.recipe) != self.recipe_raw
                or b.parse_plan(self.raw) != self.intent or not set(original.nodes) <= self.expected_namespace
                or self.directories != self._directories or self.expected_namespace != self._expected_namespace
                or tuple(sorted(self.descriptors.items())) != self._descriptors
                or tuple(sorted(self.labels.items())) != self._labels
                or tuple(sorted(self.initial_context.items())) != self._initial_context_facts
                or self.resolved is not self._original_resolved
                or self.resolved is not None and (self.resolved.files != self._resolved_bytes
                    or (self.resolved.recipe_sha256, self.resolved.service_sid, self.resolved.root_identity) != self._resolved_facts)):
            b.fail("CONFIG_ORIGIN", "original approved recipe/owner/closed namespace required")
        if self._final_security is not None and (self.service_sid,self.state_descriptor,tuple(sorted(self.final_descriptors.items()))) != self._final_security:
            b.fail('CONFIG_ORIGIN','original resolved service/security facts changed')
        self.native.check()

    def _check_client(self,context):
        client=self.recipe['state_policy']['client']
        if client is not None and client['sid'] != context.get('token_user_sid'):
            b.fail('INCOMPLETE_INPUT','optional client requires this original actual User token; external client identity producer is unavailable')

    def bind_service_security(self):
        if self._final_security is not None:
            b.fail('CONFIG_ORIGIN','service security binding is one-shot')
        self.service_sid=self.original.service_sid
        self.final_descriptors={d:self.backend.sd(d,service_sid=self.service_sid) for d in (False,True)}
        self.state_descriptor=_state_sd(self.recipe['state_policy'],self.service_sid)
        self._final_security=(self.service_sid,self.state_descriptor,tuple(sorted(self.final_descriptors.items())))

    def descriptor_for(self,node):
        if node.relative=='service-state':
            return self.state_descriptor
        if self._private_source_or_root_holder(node):
            return self.descriptors[node.image.directory]
        return self.final_descriptors[node.image.directory]

    def _private_source_or_root_holder(self,node):
        root = self.recipe['project_root']
        return (node.relative == 'profiles' or node.relative in CONFIG_PATHS
            or root['kind'] == 'NEW_DIRECTORY' and node.relative == root['relative'])

    def finish_descriptor(self,node):
        record=self.native.set_descriptor(node,self.descriptor_for(node))
        image=self.backend.image(node.handle,node.path,node.image.directory)
        label=self.native.read_label(node.handle)
        if image != replace(record.before,raw_sd=record.raw) or label != record.before_label:
            b.fail('CONFIG_SET_DRIFT','DACL-only update changed original identity/content/LABEL')
        self._ack_node(node,image,label,record)

    def compare_external(self):
        for path, handle, image, label in self.external:
            if self.backend.image(handle, path, image.directory) != image or self.native.read_label(handle) != label:
                b.fail("CONFIG_EXTERNAL_DRIFT", "original pinned external finite target changed")

    def compare_generated_manifest(self):
        self.check_original(self.original)
        if self.resolved is None or set(self.original.nodes) != self.expected_namespace:
            b.fail('CONFIG_MANIFEST','complete original generated manifest required before pin transition')
        for relative,raw in self.resolved.files:
            node=self.original.nodes[relative]
            self.original._compare_node(node)
            if (node.image.size,node.image.sha256,node.image.raw_sd) != (len(raw),b.digest(raw),self.descriptors[False]):
                b.fail('CONFIG_MANIFEST','four final byte/security snapshots must remain bound')
        for node in self.original.nodes.values():
            if node.image.directory:
                expected={ntpath.basename(n.path) for n in self.original.nodes.values() if ntpath.dirname(n.path)==node.path}
                if self.backend.names(node.path) != expected:
                    b.fail('EXTRA_CHILDREN','closed original namespace changed before pin transition')

    def compare_label(self, node, handle):
        actual = self.native.read_label(handle)
        if node.label is None or actual != node.label:
            b.fail("CONFIG_LABEL_DRIFT", "original explicit node LABEL changed, even if DACL is equal")
        native.explicit_label(actual, expected=self.recipe['labels']['directory' if node.image.directory else 'file'])

    def observe_external(self):
        actual = native.host_domains()
        if actual != self.recipe['host']:
            b.fail("CONFIG_HOST", "actual hostname/fingerprint domains differ from approved host")
        for target in self.recipe['templates'][CONFIG_PATHS[0]]['finite_targets']['files']:
            path, directory = target['path'], target['kind'] == 'directory'
            if b._within(path, self.intent.prefix):
                continue
            handle = self.backend.open_node(path, directory)
            image = self.backend.image(handle, path, directory)
            label = self.native.read_label(handle)
            native.explicit_label(label, expected=target['mandatory_label'])
            if image.raw_sd.hex() != target['descriptor_hex'] or image.sha256 != target['content_sha256']:
                b.fail("CONFIG_EXTERNAL_TARGET", "approved finite external SD/content differs from original read")
            self.external.append((path, handle, image, label))
        if self.recipe['project_root']['kind'] == 'EXISTING_DIRECTORY':
            expected=self.recipe['project_root']['identity']
            rows=[row for row in self.external if row[0]==expected['path'] and row[2].directory]
            if len(rows)!=1:
                b.fail('CONFIG_ROOT', 'existing root requires one original finite readonly pin')
            image=rows[0][2]
            if (str(image.identity[0]),str(image.identity[1]),b.decode_sd(image.raw_sd)['owner']) != tuple(expected[k] for k in ('volume_id','file_id','owner_id')):
                b.fail('CONFIG_ROOT', 'actual existing project root differs from approved full identity')

    def finish_node(self, node, directory, expected=None):
        record = self.native.set_label(node, self.labels['directory' if directory else 'file'])
        image = self.backend.image(node.handle, node.path, directory)
        if image != record.before:
            b.fail('CONFIG_SET_DRIFT', 'LABEL-only operation changed identity/content/OWNER/GROUP/DACL')
        private_source = self._private_source_or_root_holder(node)
        b.check_security(image.raw_sd, directory=directory,service_sid=None if private_source else self.service_sid)
        label = self.native.read_label(node.handle)
        native.explicit_label(label, expected=self.recipe['labels']['directory' if directory else 'file'])
        if expected is not None and (image.size, image.sha256) != expected:
            b.fail("CONFIG_READBACK", "exact original generated/copied bytes differ")
        self._ack_node(node,image,label,record)

    def _ack_node(self,node,image,label,record):
        ack = _NodeAck(node, node.handle, image, label, tuple(self.owner.calls), tuple(self.owner.handles), record)
        self.pending_ack = ack
        self.original._configuration_ack = ack
        self.owner.checkpoint('CONFIG_NODE_POSTIMAGE', ack)
        node.image, node.label = image, label
        self.pending_ack = self.original._configuration_ack = None

    def compile(self, sid):
        self.check_original(self.original)
        if type(sid) is not str or not re.fullmatch(r'S-1-5-80-(\d+-){4}\d+', sid):
            b.fail("CONFIG_SERVICE_SID", "actual original new service SID required")
        if self.recipe['project_root']['kind'] == 'NEW_DIRECTORY':
            node = self.original.nodes[self.recipe['project_root']['relative']]
            self.original._compare_node(node)
            path,image=node.path,node.image
        else:
            expected=self.recipe['project_root']['identity']
            self.compare_external()
            path,handle,image,label=next(row for row in self.external if row[0]==expected['path'])
        root = dict(path=path, volume_id=str(image.identity[0]), file_id=str(image.identity[1]),
                    owner_id=b.decode_sd(image.raw_sd)['owner'])
        documents = _replace(self.recipe['templates'], dict(service_sid=sid, project_root=root,
            storage_id='windows-volume:'+root['volume_id'], directory_descriptor_hex=self.final_descriptors[True].hex(),
            file_descriptor_hex=self.final_descriptors[False].hex(),
            private_directory_descriptor_hex=self.descriptors[True].hex(), root_holder_descriptor_hex=image.raw_sd.hex()))
        if self.recipe['schema'] == SCHEMA_V4:
            # B is frozen once, after the original root/SID/SD observations. R
            # and H derive from these SAME bytes, never the discarded projection.
            trusted_raw = b.canonical(documents[CONFIG_PATHS[0]])
            profile = _validate_documents(documents, recipe=self.recipe, prefix=self.intent.prefix,
                name=self.intent.service_name, files=self.intent.files, argv=self.intent.argv,
                root=root, final=True, trusted_payload=trusted_raw)
            provider = documents[CONFIG_PATHS[1]]
            host = profile.host_resource_profile
            index = _typed_stop_grant(self.recipe, provider, host.project_id)
            if index is not None:
                from . import _host_scoped_provider as p
                row = provider['grants'][index]
                scope = dict(schema=p.MANAGED_STOP_SCOPE_SCHEMA, runtime_id=dict(row['scope_sha256']['runtime_id']),
                    platform='windows', ownership_mode='originalManagedLifetime',
                    host_source=dict(t037_source_sha256=profile.source_sha256,
                        host_resource_profile_sha256=host.sha256, host_id=host.host_id))
                row['scope_sha256'] = p.scope_sha256(host.project_id, 'session.stop', scope)
            _provider_intent(provider)  # Final normal provider schema: literal hashes only.
            frozen = ((CONFIG_PATHS[0], trusted_raw), *(
                (path, b.canonical(documents[path])) for path in CONFIG_PATHS[1:]))
        else:
            _validate_documents(documents, recipe=self.recipe, prefix=self.intent.prefix, name=self.intent.service_name,
                files=self.intent.files, argv=self.intent.argv, root=root, final=True)
            frozen = tuple((p, b.canonical(documents[p])) for p in CONFIG_PATHS)
        if any(not 0 < len(raw) <= b.MAX_PLAN for _, raw in frozen):
            b.fail("CONFIG_BYTES", "final configuration bytes exceed bound")
        return ResolvedConfiguration(b.digest(self.recipe_raw), sid, image.identity, frozen)


def prepare_configuration(original):
    if type(original) is not b.Bootstrap:
        b.fail('CONFIG_ORIGIN', 'exact original Bootstrap required')
    config = _Configuration(original)
    original._configuration = config
    original._original_configuration = config
    config.observe_external()


def _service(original):
    """Same original stopped-service creation/configuration as V1; no start right."""
    api, p = original.backend, original.intent
    manager = api.manager(True)
    original.service = api.create_service(manager, p)
    original.service_image = api.service_image(original.service)
    api.check_initial_service(original.service_image, p)
    for field, value in (("sid_type", 3), ("privileges", tuple(sorted(b.PRIVILEGES))),
                         ("failure_actions", (0, "", "", ())), ("failure_flag", False)):
        before = original.service_image
        api.configure(original.service, field, value)
        actual = api.service_image(original.service)
        if actual != {**before, field: value}:
            b.fail("SCM_READBACK", "original stopped service differs from approved postimage")
        original.service_image = actual
    before = original.service_image
    api.service_security(original.service, api.sd(False, service=True))
    actual = api.service_image(original.service)
    b.check_security(actual['raw_sd'], service=True)
    if {k:v for k,v in actual.items() if k != 'raw_sd'} != {k:v for k,v in before.items() if k != 'raw_sd'}:
        b.fail("SCM_READBACK", "base SCM config changed during DACL setup")
    original.service_image = actual
    original.service_sid = api.lookup_service_sid(p.service_name)


def apply_configuration(original):
    if type(original) is not b.Bootstrap:
        b.fail('CONFIG_ORIGIN', 'exact original Bootstrap required')
    original._guard({'PREPARED_READONLY'})
    config = original._configuration
    if type(config) is not _Configuration:
        b.fail("CONFIG_ORIGIN", "original prepared configuration required")
    config.check_original(original)
    try:
        context = original.backend.context()  # exact same fresh original V1 SDK gate
        if not context.get('elevated_admin') or not context.get('can_assign_SYSTEM_owner'):
            b.fail("SYSTEM_OWNER_CONTEXT_REQUIRED", "already capable elevated actor required; no privilege changes")
        config.actor = ('SYSTEM' if context.get('token_user_sid') == b.SY else 'CAPABLE_ELEVATED_ADMIN', dict(context))
        if context.get('token_user_sid') != config.initial_context.get('token_user_sid'):
            b.fail('CONFIG_ACTOR_DRIFT','original actual installer user changed')
        config._check_client(context)
        original._check_sources_anchors()
        config.compare_external()
        if native.host_domains() != config.recipe['host']:
            b.fail("CONFIG_HOST", "host changed before first effect")
        if not original.backend.absent(original.intent.prefix) or not original.backend.service_absent(original.manager, original.intent.service_name):
            b.fail("NAMESPACE_COLLISION", "new namespace changed since original prepare")
    except BaseException as exc:
        if (original.owner.uncertain or any(c.state in {'UNKNOWN','INVOKING'} for c in original.owner.calls)
                or any(h.state in {'UNKNOWN','PENDING'} for h in original.owner.handles)):
            original.state, original.owner.uncertain = 'UNKNOWN', True
        raise b.BootstrapError(getattr(exc,'code','CONFIG_PRE_EFFECT_REFUSED'), str(exc), owner=original) from exc
    original.apply_started, original.state = True, 'APPLYING'
    try:
        for relative in config.directories:
            node = b._Node(original.intent.destination(relative), relative, None)
            original.nodes[relative] = node  # registered before potentially effective creation
            node.handle = original.backend.create_directory(node.path, config.descriptors[True], None)
            config.finish_node(node, True)
        for item in original.intent.files:
            node = b._Node(original.intent.destination(item.path), item.path, None)
            original.nodes[item.path] = node
            node.handle = original.backend.create_file(node.path, config.descriptors[False], original.sources[item.path][0], item.size)
            config.finish_node(node, False, (item.size, item.sha256))
        _service(original)
        config.bind_service_security()
        for node in original.nodes.values():
            config.finish_descriptor(node)
        config.resolved = config.compile(original.service_sid)  # all four frozen before first config write
        config._original_resolved = config.resolved
        config._resolved_bytes = config.resolved.files
        config._resolved_facts = (config.resolved.recipe_sha256, config.resolved.service_sid, config.resolved.root_identity)
        for relative, raw in config.resolved.files:
            config.check_original(original)
            node = b._Node(original.intent.destination(relative), relative, None)
            original.nodes[relative] = node
            config.native.create_bytes(node, raw, config.descriptors[False])
            config.finish_node(node, False, (len(raw), b.digest(raw)))
        original._compare()
        original.state = 'INSTALLED_STOPPED_START_BLOCKED'
        return {**original.preview(), 'trusted_profile_materialization': 'STATIC_FOUR_FILES_ONLY',
                'static_actor': config.actor[0], 'runtime_qualification': 'NOT_QUALIFIED', 'native_start': 'NOT_RUN'}
    except BaseException as exc:
        if config.pending_ack is not None and original._configuration_ack is config.pending_ack:
            original.state = 'CONFIGURATION_ACK_PENDING'
        else:
            original.state, original.owner.uncertain = 'UNKNOWN', True
        raise b.BootstrapError(getattr(exc, 'code', 'CONFIG_APPLY_UNKNOWN'), str(exc), owner=original) from exc


def acknowledge_configuration(original):
    if type(original) is not b.Bootstrap:
        b.fail('CONFIG_ORIGIN', 'exact original Bootstrap required')
    original._guard({'CONFIGURATION_ACK_PENDING'})
    config = original._configuration
    if type(config) is not _Configuration:
        b.fail('CONFIG_ORIGIN', 'original configuration required')
    config.check_original(original)
    ack = config.pending_ack
    if (type(ack) is not _NodeAck or original._configuration_ack is not ack
            or original.nodes.get(ack.node.relative) is not ack.node or ack.node.handle is not ack.handle
            or tuple(original.owner.calls) != ack.calls or tuple(original.owner.handles) != ack.handles
            or ack.label_call not in config.native.label_calls or ack.label_call.call.state != 'SUCCEEDED'
            or ack.label_call.call.scalar != 0 or ack.label_call.handle is not ack.handle
            or ack.label_call.owner is not original.owner or ack.handle.closed
            or ack.handle.original_value != ack.label_call.value or ack.handle.argument is not ack.label_call.argument
            or ack.label_call.call.fn is not config.native.setter
            or ack.label_call.function is not config.native.setter):
        b.fail('CONFIG_ACK_UNKNOWN', 'only cached original SDK success/postimage metadata may be acknowledged')
    record = ack.label_call
    if bytes(record.storage.raw) != record.raw or record.call.args is not record.arguments or len(record.arguments) != 7:
        b.fail('CONFIG_ACK_UNKNOWN', 'whole original label buffer/arguments/postimage must remain bound')
    base=native.C.addressof(record.storage)
    if record.kind=='LABEL':
        valid=(record.raw == config.labels['directory' if ack.image.directory else 'file']
            and record.call.args[:6] == (record.argument,1,0x10,None,None,None)
            and record.call.args[6].value == base+20 and ack.image == record.before)
    elif record.kind=='DACL':
        offsets=b.decode_sd(record.raw)['offsets']
        valid=(record.raw == config.descriptor_for(ack.node) and record.call.args[:3] == (record.argument,1,0x80000007)
            and tuple(p.value for p in record.call.args[3:6]) == tuple(base+n for n in offsets)
            and record.call.args[6] is None and ack.image == replace(record.before,raw_sd=record.raw)
            and ack.label == record.before_label)
    else:
        valid=False
    if not valid:
        b.fail('CONFIG_ACK_UNKNOWN','only exact cached LABEL/DACL success and postimage are eligible')
    original._original_handle(ack.handle)  # pure original storage/owner validation; no native query
    native.explicit_label(ack.label, expected=config.recipe['labels']['directory' if ack.image.directory else 'file'])
    # No SDK query, setter, free or close. The caller may compare-and-restore,
    # never replay apply/continue a partially executed installation.
    ack.node.image, ack.node.label = ack.image, ack.label
    config.pending_ack = original._configuration_ack = None
    original.state = 'PARTIAL_KNOWN'
    return {'state': original.state, 'repeated_native_calls': 0, 'native_start': 'NOT_RUN'}


def load_configurations(original):
    if type(original) is not b.Bootstrap:
        b.fail('CONFIG_ORIGIN', 'exact original Bootstrap required')
    original._guard({'RUNTIME_READ_PINNED_START_BLOCKED'})
    config = original._configuration
    if type(config) is not _Configuration or config.loaded:
        b.fail('CONFIG_ORIGIN', 'one original four-loader observation only')
    config.check_original(original)
    if original.backend.context().get('token_user_sid') != b.SY:
        b.fail('SYSTEM_LOADER_CONTEXT_REQUIRED', 'Admin static writing is not genuine SYSTEM private-file consumption')
    original._compare()
    from ._windows_t037 import load_trusted_profile
    from ._host_scoped_provider import HostScopedProvider
    from ._w26_memory_resources import TrustedMemoryResourceConfig
    from ._w26_os_resources import TrustedDiskResourceConfig
    try:
        for (relative, raw), loader, attr in zip(config.resolved.files,
                (load_trusted_profile, HostScopedProvider.load, TrustedMemoryResourceConfig.load, TrustedDiskResourceConfig.load),
                ('source_sha256', 'config_sha256', 'source_sha256', 'source_sha256')):
            call = [loader, original.intent.destination(relative), raw, 'INVOKING', None]
            config.loader_calls.append(call)  # original path/function before potential readonly allocation
            result = loader(call[1])
            call[3], call[4] = 'RETURNED', result
            config.loaded.append(result)  # retain originals even if a later loader refuses
            if getattr(result, attr) != b.digest(raw):
                b.fail('CONFIG_LOADER_SOURCE', 'normal loader source digest differs from frozen original bytes')
        original._compare()
    except BaseException as exc:
        config.loader_error = exc
        original.state, original.owner.uncertain = 'UNKNOWN', True
        raise b.BootstrapError(getattr(exc,'code','CONFIG_LOADER_UNKNOWN'), str(exc), owner=original) from exc
    original.loaded_configs = tuple(config.loaded)
    return original.loaded_configs  # no start/RuntimeAck/issuer or cross-process capability
