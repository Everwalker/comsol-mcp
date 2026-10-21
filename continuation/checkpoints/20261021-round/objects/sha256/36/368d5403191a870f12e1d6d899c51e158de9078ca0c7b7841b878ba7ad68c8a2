"""Verbatim original process/UNKNOWN definitions; fixed old entrypoint excluded."""
import json,os,time,subprocess,uuid
from tools.w26_prepare_current_inputs import _helpers
Refusal = _helpers().Refusal

def write_json(fd, name, data):
    f = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(f, "w", closefd=False) as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(f)
    finally:
        os.close(f)
    os.fsync(fd)

class StageUnknown(Refusal):
    def __init__(self, message, *, process=None, token=None, result=None):
        super().__init__(message)
        self.process = process
        self.token = token
        self.original_result = result
        self.original_cause = None
        self.diagnostic_errors = []

class TerminalCommandFailure(Refusal):
    def __init__(self, result):
        super().__init__("owned command ended with a nonzero exit; original raw logs retained")
        self.result = result

def actual_birth(process, argv):
    command = ["/bin/ps", "-p", str(process.pid), "-o", "pid=,ppid=,uid=,lstart=,command="]
    response = subprocess.run(command, capture_output=True, text=True, timeout=10, env=dict(os.environ, LC_ALL="C"))
    raw = {"argv": command, "rc": response.returncode, "stdout": response.stdout, "stderr": response.stderr}
    fields = response.stdout.strip().split(None, 8)
    if response.returncode != 0 or response.stderr.strip() or len(fields) != 9:
        return {"status": "UNKNOWN", "reason": "fresh actual process birth/UID cannot be established", "raw": raw}
    try:
        owned = int(fields[0]) == process.pid and int(fields[1]) == os.getpid() and int(fields[2]) == os.getuid()
    except ValueError:
        owned = False
    if not owned:
        return {"status": "UNKNOWN", "reason": "fresh process ownership differs from the created child", "raw": raw}
    return {"status": "OBSERVED", "pid": process.pid, "ppid": int(fields[1]), "uid": int(fields[2]),
            "birth_observed": " ".join(fields[3:8]), "actual_ps_command": fields[8], "invocation_argv": argv, "raw": raw}

def group_absent(pgid):
    try: os.killpg(pgid, 0)
    except ProcessLookupError: return True
    except PermissionError: return False
    return False

def observe_created_group(process):
    try:
        pgid, sid, parent_sid = os.getpgid(process.pid), os.getsid(process.pid), os.getsid(0)
    except OSError as exc:
        return {"status": "UNKNOWN", "error_type": type(exc).__name__, "error": str(exc)}
    independent = pgid == process.pid and sid == process.pid and sid != parent_sid
    return {"status": "OBSERVED" if independent else "UNKNOWN", "actual_pgid": pgid, "actual_sid": sid,
            "parent_sid": parent_sid, "independent_session_observed": independent, "observed_unix": time.time()}

class DiagnosticErrors(list):
    def __init__(self):
        super().__init__()
        self.causes = []

def failure_detail(phase, exc):
    return {"phase": phase, "error_type": type(exc).__name__, "error": str(exc)}

def fallback(message):
    # Diagnostics must never discard an already-created child handle.
    try: os.write(2, (message + "\n").encode("utf-8", "backslashreplace"))
    except BaseException: pass

def diagnostic_write(fd, name, payload, errors):
    try: write_json(fd, name, payload)
    except BaseException as exc:
        errors.append(failure_detail("write:" + name, exc))
        if hasattr(errors, "causes"): errors.causes.append(exc)
        fallback("UNKNOWN diagnostic failure: " + repr(errors[-1]))

def diagnostic_print(payload, errors):
    try: print(json.dumps(payload), flush=True)
    except BaseException as exc:
        errors.append(failure_detail("stdout", exc))
        if hasattr(errors, "causes"): errors.causes.append(exc)
        fallback(repr(payload))

def release_log(fd, errors):
    for phase, operation in (("fsync", os.fsync), ("close", os.close)):
        try: operation(fd)
        except BaseException as exc:
            errors.append(failure_detail(phase + ":" + str(fd), exc))
            if hasattr(errors, "causes"): errors.causes.append(exc)
            fallback(repr(errors[-1]))

def run(logfd, label, argv, cwd, env, timeout=600, birth_reader=None):
    token = label + "-" + uuid.uuid4().hex
    stdout_name, stderr_name = token + ".stdout", token + ".stderr"
    request = {"argv": argv, "cwd": str(cwd), "environment": env, "timeout_s": timeout,
               "stdout": stdout_name, "stderr": stderr_name, "started_unix": time.time()}
    write_json(logfd, token + ".request.json", request)
    fds, errors, process, primary = [], DiagnosticErrors(), None, None
    result = {"argv": argv, "stdout": stdout_name, "stderr": stderr_name, "status": "UNKNOWN", "rc": None,
              "owned_process_terminal": False, "owned_group_absent": False, "diagnostic_errors": errors}
    try:
        for name in (stdout_name, stderr_name):
            fds.append(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=logfd))
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=fds[0], stderr=fds[1], start_new_session=True)
        result.update(pid=process.pid, requested_new_session=True, uid_at_spawn=os.getuid(), parent_pid=os.getpid())
        write_json(logfd, token + ".spawn.json", {**result, "spawned_unix": time.time()})
        group = observe_created_group(process)
        result["group"] = group
        write_json(logfd, token + ".group.json", group)
        if group.get("status") != "OBSERVED": raise Refusal("new process group observation incomplete")
        birth = (actual_birth if birth_reader is None else birth_reader)(process, argv)
        result["birth"] = birth
        write_json(logfd, token + ".birth.json", birth)
        if birth.get("status") != "OBSERVED": raise Refusal("fresh birth/UID observation incomplete")
        result["rc"] = process.wait(timeout=timeout)
        result["owned_process_terminal"] = True
        result["owned_group_absent"] = group_absent(group["actual_pgid"])
        if not result["owned_group_absent"]: raise Refusal("direct child ended but owned group may remain")
        result["status"] = "PASS" if result["rc"] == 0 else "TERMINAL_FAIL"
        if result["rc"] != 0: primary = TerminalCommandFailure(result)
    except BaseException as exc:
        primary = exc
        result.update(error_type=type(exc).__name__, error=str(exc),
                      status="UNKNOWN_TIMEOUT_ACTIVE_PROCESS" if isinstance(exc, subprocess.TimeoutExpired) else "UNKNOWN_OWNERSHIP_OR_INTERRUPTION")
    finally:
        for fd in fds: release_log(fd, errors)
        try: result["finished_unix"] = time.time()
        except BaseException as exc:
            errors.append(failure_detail("finished_time", exc));errors.causes.append(exc)
            fallback(repr(errors[-1]))
        if errors: result["status"] = "UNKNOWN_DIAGNOSTICS"
        diagnostic_write(logfd, token + ".result.json", result, errors)
    if process is not None and (errors or (primary is not None and not isinstance(primary, TerminalCommandFailure))):
        result["status"] = "UNKNOWN_DIAGNOSTICS" if errors else result["status"]
        unknown = StageUnknown("created command requires same-handle observation; retain stage fence", process=process, token=token, result=result)
        unknown.original_cause = primary or (errors.causes[0] if errors.causes else None)
        unknown.diagnostic_errors = errors
        raise unknown from unknown.original_cause
    if primary is not None: raise primary
    if errors: raise Refusal("pre-spawn diagnostics failed; no child was created")
    diagnostic_print({"command": label, "status": "PASS", "receipt": token + ".result.json"}, errors)
    if errors:
        result["status"] = "UNKNOWN_DIAGNOSTICS"
        unknown = StageUnknown("command output diagnostics failed", process=process, token=token, result=result)
        unknown.diagnostic_errors = errors
        unknown.original_cause = errors.causes[0] if errors.causes else None
        raise unknown from unknown.original_cause
    return {"result_name": token + ".result.json", **result}

def observe_unknown(logfd, error, interval=10):
    """Keep the exact Popen despite any diagnostic failure; never clear its fence."""
    if error.process is None or error.token is None: raise error
    count = 0
    errors = error.diagnostic_errors
    while True:
        count += 1
        try:
            rc = error.process.wait(timeout=interval)
        except subprocess.TimeoutExpired:
            observation = {"status": "SAME_OWNED_PROCESS_STILL_ACTIVE", "pid": error.process.pid,
                           "original_unknown_result": error.token + ".result.json", "observed_unix": time.time()}
            diagnostic_write(logfd, error.token + ".observation." + str(count) + ".json", observation, errors)
            diagnostic_print(observation, errors)
            continue
        except BaseException as exc:
            errors.append(failure_detail("same_handle_wait", exc));fallback(repr(errors[-1]))
            time.sleep(interval)
            continue
        group = error.original_result.get("group", {}) if error.original_result else {}
        birth = error.original_result.get("birth", {}) if error.original_result else {}
        owned = group.get("status") == "OBSERVED" and birth.get("status") == "OBSERVED"
        try: absent = group_absent(group["actual_pgid"]) if owned else None
        except BaseException as exc:
            errors.append(failure_detail("group_absence", exc));fallback(repr(errors[-1]));absent = None
        if owned and absent is not True:
            observation = {"status": "OBSERVED_DIRECT_CHILD_TERMINAL_GROUP_REMAINS_UNKNOWN", "pid": error.process.pid,
                           "rc": rc, "actual_pgid": group["actual_pgid"], "owned_group_absent": absent,
                           "original_unknown_result": error.token + ".result.json", "observed_unix": time.time()}
            diagnostic_write(logfd, error.token + ".observation." + str(count) + ".json", observation, errors)
            diagnostic_print(observation, errors);time.sleep(interval);continue
        observation = {"status": "OBSERVED_DIRECT_CHILD_TERMINAL_NOT_STAGE_ACCEPTED", "pid": error.process.pid,
                       "rc": rc, "owned_group_absent": absent, "group_ownership_confirmed": owned, "observed_unix": time.time(),
                       "original_unknown_result": error.token + ".result.json", "original_unknown_unchanged": True,
                       "active_stage_guard_retained": True, "stage_acceptance": "NOT_RUN"}
        diagnostic_write(logfd, error.token + ".terminal_observation.json", observation, errors)
        diagnostic_print(observation, errors)
        return observation
