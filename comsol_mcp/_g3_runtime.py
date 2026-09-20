"""G3 runtime operations: ``runtime.license_inspect`` and ``runtime.capabilities``.

Frozen publishing contract (the control plane dispatches through this):

    OPERATIONS: dict[str, Callable[[Any, str, dict], dict]]

Each entry is called as ``fn(worker, model_tag, arguments)``.  It returns the
operation's ``data`` dictionary on success and raises
``comsol_mcp._g2_contract.ExecutionContractError`` for a refusal or a
validation failure.  It never wraps its own result in a ``{"success": ...}``
envelope: the control plane owns that.

What these two operations are for
---------------------------------
``runtime.license_inspect`` answers *"may this runtime run these products?"*
without ever asking a license seat to be checked out, and
``runtime.capabilities`` reports the runtime capability status, its limits and
the evidence actually observed for them.  The W15 T042 acceptance case
(``tools/phase4_run_mcp.py``, ``_case_w15_t042``) consumes both: it needs the
operations to be *executable*, it needs per-product ``hasProduct`` evidence in
``data["products"]``, it needs a product that is not available to be reported
as such (``hasProduct == false``) instead of a fabricated success, it needs an
unresolvable probe to be a *recognizable* refusal (its ``_BLOCKED_CODES``
treats ``BLOCKED_LICENSE`` as BLOCKED, never FAIL), and it needs the payload to
contain no top-level ``checkout``/``seats``/``seat_count``/``checkouts`` key.

op -> COMSOL API -> verification source
---------------------------------------
runtime.license_inspect  ``ModelUtil.hasProduct(String...)`` per requested
                      product, one call per product, through the persistent
                      Worker's own ``modelutil`` command; engine identity from
                      ``ModelUtil.getComsolVersion()``; optional inventory from
                      ``Model.getUsedProducts()``.
                      -> javap of the installed public API jar
                      ``/Applications/COMSOL64/Multiphysics/apiplugins/com.comsol.api_1.0.0.jar``
                      (sha256 9bdc47a9e320be5721956336f44f5afa4cb06a20cfa887bc7d32d1a837483a67)
                      shows ``public static boolean hasProduct(java.lang.String...)``,
                      ``public static boolean checkoutLicense(java.lang.String...)``
                      and ``public abstract java.lang.String[] Model.getUsedProducts()``.
                      -> COMSOL 6.4 Programming Reference Manual, PDF page 42
                      (COMSOL_ProgrammingReferenceManual.pdf, sha256
                      5b7f23ad2eae77f59d71935f4f6c9b6b9ede380dc34da065181841e512bbc105):
                      "ModelUtil.hasProduct(String... product): The hasProduct
                      method checks if the current license allows to run the
                      specified COMSOL products given as the input (as an array
                      of strings)."
                      -> COMSOL 6.4 Application Programming Guide, "License
                      Methods" (application_programming_guide.15.51.html, sha256
                      298165dfbca1e9eeef9ff9edba1917bac6128c934f57113f130e30965253ab03,
                      PDF page 130): hasProduct "Returns true if the COMSOL
                      installation contains the software components required for
                      running the specified products."; the seat-consuming
                      method is ``checkoutLicense`` ("Checks out licenses for
                      all specified products. If not all licenses can be checked
                      out, no licenses are checked out.").  The same page's
                      "License Feature Strings" table is the documented product
                      vocabulary reproduced in ``LICENSE_FEATURE_STRINGS``.
runtime.capabilities     ``ModelUtil.getComsolVersion()`` +
                      ``Model.getUsedProducts()`` + the allow-list/reachability
                      evidence for the license API surface (``LICENSE_API_PROVENANCE``),
                      plus an optional non-secret Worker identity
                      (``PersistentJavaWorker.runtime_metadata()``).

Why the per-product probe can be BLOCKED in this build (reported, not hidden)
-----------------------------------------------------------------------------
The persistent Worker enforces two Java-side allow-lists
(``comsol_mcp/worker_java/PersistentComsolWorker.java``):

* ``MODEL_UTIL`` - the static ``ModelUtil`` surface reachable through the
  Worker's ``modelutil`` command: ``create, load, model, remove, tags,
  uniquetag, modelsUsedByOtherClients, getComsolVersion, hasProduct``.
  ``hasProduct`` was requested by this module and applied by the Worker owner
  (2026-09-20), so the per-product probe now reaches the authoritative call;
  ``hasProductForFile`` is still absent.
* ``METHODS`` - node-handle methods reached through the ``call`` command.
  ``getUsedProducts`` is present, so the model-level inventory is reachable.

This module *attempts* the authoritative call and reports the Worker's own
refusal (``MODEL_UTIL_METHOD_REJECTED``) instead of guessing a value; any
refused allow-list entry is named in the error message and in
``runtime.capabilities``.  If the Worker refuses the probe method, the probe
reports BLOCKED rather than a fabricated boolean.

``ModelUtil.getLicenseNumber()`` is documented in the Application Programming
Guide's License Methods table but is **not declared** by the installed public
API jar (``javap com.comsol.model.util.ModelUtil``), so it is reported as
absent rather than called.  ``ModelUtil.checkoutLicense*`` is deliberately
never called: a license probe must not occupy a seat.

Failure classification (aligned with the T042 driver)
-----------------------------------------------------
* a product whose license state cannot be established -> ``BLOCKED_LICENSE``
  (the driver's ``_BLOCKED_CODES``): a partially resolved probe is refused as a
  whole, because a missing row is indistinguishable from a licensed product for
  a caller that only reads ``hasProduct``;
* the Worker refusing the probe method -> ``BLOCKED_LICENSE`` with the required
  allow-list entry named;
* the engine channel itself failing -> the Worker's own structured code is
  preserved (for example ``ENGINE_UNRESPONSIVE``), never rewritten into a
  success.

``data`` of ``runtime.license_inspect`` carries no top-level ``checkout``,
``seats``, ``seat_count`` or ``checkouts`` key, and its strings avoid the
substring ``server`` together with ``=`` - the two shapes the driver rejects as
a license-server configuration echo.  ``product_probe.methods_attempted`` and
``product_probe.seat_consuming_methods_attempted`` record what the probe really
asked the Worker for, so "the probe occupied no seat" is reported evidence
rather than a claim: the only gate that could hide a checkout is
``_license_api_call``, which refuses every seat-consuming method before
dispatch and records the attempt.

Inventoried API surface
-----------------------
The license API surface reported by ``runtime.capabilities`` is curated from
``javap`` of the installed public API jar: every non-checkout *local*
capability query the jar declares (``ModelUtil.hasProduct``,
``ModelUtil.hasProductForFile``), the engine identity and the model inventory.
The two ``...OnServer`` variants the jar also declares are deliberately out of
scope: they resolve through a license server rather than the installed license,
and their vocabulary is exactly what the acceptance driver treats as a
license-endpoint echo.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Sequence

from ._g2_contract import ExecutionContractError
from ._g3_common import (
    allowlist_rejected,
    bound_model,
    call_probe,
    error_code_of,
    operation_arguments,
    require_bool,
    require_string,
    require_string_array,
)

#: The Worker command that reaches the static ``ModelUtil`` surface (javap +
#: the Worker's ``modelUtil`` command handler).
WORKER_MODELUTIL_COMMAND = "modelutil"

#: Documented COMSOL product token shape.  The corpus publishes no grammar for
#: these identifiers; this pattern can only narrow (letter start, alphanumerics
#: and underscore) what the license system accepts, and every documented
#: feature string in ``LICENSE_FEATURE_STRINGS`` matches it.
PRODUCT_TOKEN_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

#: Upper bound on one probe request: every product costs one Worker round trip
#: and one license query, so a request is bounded instead of unbounded.
MAX_PRODUCTS = 32

#: License feature strings as published by the COMSOL 6.4 Application
#: Programming Guide, "License Methods" -> "License Feature Strings"
#: (application_programming_guide.15.51.html, sha256 298165dfbc...3ab03; the
#: table also spans PDF page 130/131 of the same guide).  The table lists the
#: product strings usable by the license methods; one product may map to more
#: than one feature string ("DESIGN, CADIMPORT").  ``hasProduct`` documents its
#: accepted names as "code completion" only, so a token outside this table is
#: *accepted but flagged* (``documented_feature_string: false``) rather than
#: refused - refusing a legal request would be a guess in the other direction.
LICENSE_FEATURE_STRINGS: dict[str, tuple[str, ...]] = {
    "AC/DC Module": ("ACDC",),
    "Acoustics Module": ("ACOUSTICS",),
    "Battery Design Module": ("BATTERYDESIGN",),
    "CAD Import Module": ("CADIMPORT",),
    "CFD Module": ("CFD",),
    "Chemical Reaction Engineering Module": ("CHEM",),
    "Corrosion Module": ("CORROSION",),
    "Design Module": ("DESIGN", "CADIMPORT"),
    "ECAD Import Module": ("ECADIMPORT",),
    "Electric Discharge Module": ("ELECTRICDISCHARGE",),
    "Electrochemistry Module": ("ELECTROCHEMISTRY",),
    "Electrodeposition Module": ("ELECTRODEPOSITION",),
    "Fatigue Module": ("FATIGUE",),
    "File Import for CATIA V5": ("CATIA5",),
    "Fuel Cell & Electrolyzer Module": ("FUELCELLANDELECTROLYZER",),
    "Geomechanics Module": ("GEOMECHANICS",),
    "Granular Flow Module": ("GRANULARFLOW",),
    "Heat Transfer Module": ("HEATTRANSFER",),
    "Liquid & Gas Properties Module": ("LIQUIDANDGASPROPERTIES",),
    "LiveLink for AutoCAD": ("LLAUTOCAD", "CADIMPORT"),
    "LiveLink for PTC Creo Parametric": ("LLCREOPARAMETRIC", "CADIMPORT"),
    "LiveLink for Excel": ("LLEXCEL",),
    "LiveLink for Inventor": ("LLINVENTOR", "CADIMPORT"),
    "LiveLink for MATLAB": ("LLMATLAB",),
    "LiveLink for Revit": ("LLREVIT", "CADIMPORT"),
    "LiveLink for Simulink": ("LLSIMULINK",),
    "LiveLink for Solid Edge": ("LLSOLIDEDGE", "CADIMPORT"),
    "LiveLink for SOLIDWORKS": ("LLSOLIDWORKS", "CADIMPORT"),
    "Material Library": ("MATLIB",),
    "MEMS Module": ("MEMS",),
    "Metal Processing Module": ("METALPROCESSING",),
    "Microfluidics Module": ("MICROFLUIDICS",),
    "Mixer Module": ("MIXER",),
    "Molecular Flow Module": ("MOLECULARFLOW",),
    "Multibody Dynamics Module": ("MULTIBODYDYNAMICS",),
    "Nonlinear Structural Materials Module": ("NONLINEARSTRUCTMATERIALS",),
    "Optimization Module": ("OPTIMIZATION",),
    "Particle Tracing Module": ("PARTICLETRACING",),
    "Pipe Flow Module": ("PIPEFLOW",),
    "Plasma Module": ("PLASMA",),
    "Polymer Flow Module": ("POLYMERFLOW",),
    "Porous Media Flow Module": ("POROUSMEDIAFLOW",),
    "Ray Optics Module": ("RAYOPTICS",),
    "RF Module": ("RF",),
    "Rotordynamics Module": ("ROTORDYNAMICS",),
    "Semiconductor Module": ("SEMICONDUCTOR",),
    "Structural Mechanics Module": ("STRUCTURALMECHANICS",),
    "Subsurface Flow Module": ("SUBSURFACEFLOW",),
    "Uncertainty Quantification Module": ("UQ",),
    "Wave Optics Module": ("WAVEOPTICS",),
}

#: Product label per documented feature string (a feature string shared by two
#: products resolves to the first product that documents it).
FEATURE_STRING_PRODUCTS: dict[str, str] = {}
for _product, _features in LICENSE_FEATURE_STRINGS.items():
    for _feature in _features:
        FEATURE_STRING_PRODUCTS.setdefault(_feature, _product)

DOCUMENTED_FEATURE_STRINGS = frozenset(FEATURE_STRING_PRODUCTS)

#: The Worker's static ``ModelUtil`` allow-list, read from
#: ``comsol_mcp/worker_java/PersistentComsolWorker.java`` (the ``MODEL_UTIL``
#: set).  Recorded here so a reachability statement can cite what it was read
#: from; the Worker remains the enforcement point.
WORKER_MODELUTIL_ALLOWLIST = frozenset(
    {
        "create", "load", "model", "remove", "tags", "uniquetag",
        "modelsUsedByOtherClients", "getComsolVersion",
        # Requested by ``WORKER_ALLOWLIST_REQUESTS`` and applied by the Worker
        # owner on 2026-09-20 (non-seat-consuming per-product query).
        "hasProduct",
    }
)

#: Node-handle methods the Worker's ``call`` command accepts that this module
#: depends on (``METHODS`` set in the same Java file).
WORKER_NODE_METHODS_ALLOWLIST = frozenset({"getUsedProducts", "getComsolVersion", "tag", "label", "getType"})

#: Methods that check out a license seat.  This module never calls them; the
#: acceptance case asserts the probe does not occupy a seat, so the read-only
#: constraint is part of the contract, not an implementation detail.
SEAT_CONSUMING_METHODS = frozenset(
    {"checkoutLicense", "checkoutLicenseForFile", "checkoutLicenseForFileOnServer"}
)

#: The license-surface APIs relevant to a non-consuming probe, with their
#: offline verification and their reachability through this build's Worker.
LICENSE_API_PROVENANCE: dict[str, dict[str, Any]] = {
    "ModelUtil.hasProduct": {
        "javap_signature": "public static boolean hasProduct(java.lang.String...)",
        "verified_by": "javap com.comsol.model.util.ModelUtil (apiplugins/com.comsol.api_1.0.0.jar)",
        "documented_semantics": (
            "Programming Reference PDF p.42: checks if the current license allows to run the "
            "specified COMSOL products; Application Programming Guide, License Methods: returns "
            "true when the installation contains the software components required to run them"
        ),
        "consumes_license_seat": False,
        "worker_command": WORKER_MODELUTIL_COMMAND,
        "worker_allowlist_entry": "hasProduct",
        "worker_allowlist_present": "hasProduct" in WORKER_MODELUTIL_ALLOWLIST,
        "used_by": ("runtime.license_inspect",),
    },
    "ModelUtil.getComsolVersion": {
        "javap_signature": "public static java.lang.String getComsolVersion()",
        "verified_by": "javap com.comsol.model.util.ModelUtil (apiplugins/com.comsol.api_1.0.0.jar)",
        "documented_semantics": "Application Programming Guide: getComsolVersion returns the current software version as a string",
        "consumes_license_seat": False,
        "worker_command": WORKER_MODELUTIL_COMMAND,
        "worker_allowlist_entry": "getComsolVersion",
        "worker_allowlist_present": "getComsolVersion" in WORKER_MODELUTIL_ALLOWLIST,
        "used_by": ("runtime.license_inspect", "runtime.capabilities"),
    },
    "Model.getUsedProducts": {
        "javap_signature": "public abstract java.lang.String[] getUsedProducts()",
        "verified_by": "javap com.comsol.model.Model (apiplugins/com.comsol.api_1.0.0.jar)",
        "documented_semantics": "Programming Reference PDF p.45: returns the products that this model uses (an inventory, not a license decision)",
        "consumes_license_seat": False,
        "worker_command": "call",
        "worker_allowlist_entry": "getUsedProducts",
        "worker_allowlist_present": "getUsedProducts" in WORKER_NODE_METHODS_ALLOWLIST,
        "used_by": ("runtime.license_inspect", "runtime.capabilities"),
    },
    "ModelUtil.hasProductForFile": {
        "javap_signature": (
            "public static boolean hasProductForFile(java.lang.String) throws java.io.IOException"
        ),
        "verified_by": "javap com.comsol.model.util.ModelUtil (apiplugins/com.comsol.api_1.0.0.jar)",
        "documented_semantics": (
            "Programming Reference PDF p.42: checks if the current license allows the COMSOL products "
            "needed to use that COMSOL MPH file; a non-checkout, file-level capability query"
        ),
        "consumes_license_seat": False,
        "worker_command": "modelutil",
        "worker_allowlist_entry": "hasProductForFile",
        "worker_allowlist_present": False,
        "used_by": [],
    },
    "ModelUtil.getLicenseNumber": {
        "javap_signature": None,
        "verified_by": (
            "documented (Application Programming Guide, License Methods: returns the license "
            "number of the current session) but NOT declared by the installed public API jar "
            "(javap com.comsol.model.util.ModelUtil lists no getLicenseNumber)"
        ),
        "documented_semantics": "session license number string",
        "consumes_license_seat": False,
        "worker_command": None,
        "worker_allowlist_entry": None,
        "worker_allowlist_present": False,
        "used_by": (),
    },
    "ModelUtil.checkoutLicense": {
        "javap_signature": "public static boolean checkoutLicense(java.lang.String...)",
        "verified_by": "javap com.comsol.model.util.ModelUtil (apiplugins/com.comsol.api_1.0.0.jar)",
        "documented_semantics": "Application Programming Guide: checks out licenses for all specified products; if not all can be checked out, none are",
        "consumes_license_seat": True,
        "worker_command": None,
        "worker_allowlist_entry": None,
        "worker_allowlist_present": False,
        "used_by": (),
    },
}

#: Extensions this module needs from the Worker owner, reported instead of
#: worked around (the Java allow-lists are the enforcement point and are not
#: edited by this layer).
#: Allow-list entries this module needs and that the Worker did not yet carry
#: when the module landed.  ``hasProduct`` was applied by the Worker owner on
#: 2026-09-20, so nothing is pending; a future entry lands here until applied.
WORKER_ALLOWLIST_REQUESTS: tuple[dict[str, str], ...] = ()

_ALLOWLIST_REFUSAL_CODES = frozenset({"METHOD_REJECTED", "MODEL_UTIL_METHOD_REJECTED", "METHOD_NOT_ALLOWED"})

#: Argument names the two operations accept, beyond the control-plane envelope.
_LICENSE_INSPECT_FIELDS = ("runtime_id", "products")
_CAPABILITIES_FIELDS = ("runtime_id", "refresh")


# ---------------------------------------------------------------------------
# Worker channel
# ---------------------------------------------------------------------------


def _is_allowlist_refusal(code: Any, message: Any) -> bool:
    """True when a Worker refusal is its allow-list talking, not COMSOL.

    The Java Worker reports a refused method as ``SecurityException:
    MODEL_UTIL_METHOD_REJECTED`` inside a generic ``ENGINE_CALL_FAILED``
    failure, so the code *and* the message are inspected - never only one.
    """
    if isinstance(code, str) and code in _ALLOWLIST_REFUSAL_CODES:
        return True
    text = f"{code or ''} {message or ''}"
    return "METHOD_REJECTED" in text or "METHOD_NOT_ALLOWED" in text


def _refusal(code: Any, message: Any, method: str, *, channel: str, allowlist: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "value": None,
        "code": str(code) if isinstance(code, str) and code else "ENGINE_CALL_FAILED",
        "message": str(message) if message else "the Worker refused the call",
        "allowlist_entry_required": method if allowlist else None,
        "channel": channel,
        "method": method,
    }


class _CallLog:
    """Evidence of which license-surface methods this operation actually asked for.

    The operations report the log instead of *claiming* that no seat-consuming
    method was used: the claim is then re-derivable from the recorded attempt
    list, and an edit that started checking out seats would show up in it.
    """

    def __init__(self) -> None:
        self.methods: list[str] = []

    def record(self, method: str) -> None:
        self.methods.append(method)

    @property
    def attempted(self) -> list[str]:
        return sorted(set(self.methods))

    @property
    def seat_consuming(self) -> list[str]:
        return sorted(set(self.methods) & SEAT_CONSUMING_METHODS)


def _license_api_call(worker: Any, method: str, arguments: Sequence[Any],
                      *, log: "_CallLog | None" = None) -> dict[str, Any]:
    """Call one license-surface API through the persistent Worker, once.

    The Worker's own command channel is preferred because that is where the
    Java allow-list lives: a refused method is then observed
    (``MODEL_UTIL_METHOD_REJECTED``) instead of being assumed from a source
    read.  A Python client facade that exposes the same method is used only
    when the Worker has no command channel at all.  No retry happens here: a
    license answer is never approximated.
    """
    if log is not None:
        log.record(method)
    if method in SEAT_CONSUMING_METHODS:
        raise ExecutionContractError(
            "PERMISSION_DENIED",
            f"{method} checks out a license seat and is never called by a license probe",
        )
    submit = getattr(worker, "submit", None)
    if callable(submit):
        try:
            reply = submit(
                WORKER_MODELUTIL_COMMAND,
                {"method": method, "args": [argument for argument in arguments]},
            )
        except Exception as exc:  # noqa: BLE001 - a transport/driver failure is reported, never guessed
            return _refusal(
                error_code_of(exc),
                f"Worker {method} call failed: {type(exc).__name__}",
                method,
                channel=f"{WORKER_MODELUTIL_COMMAND} command",
                allowlist=allowlist_rejected(exc) or _is_allowlist_refusal(error_code_of(exc), exc),
            )
        if not isinstance(reply, Mapping):
            return _refusal(
                "EXECUTION_STATE_UNKNOWN",
                f"Worker {method} reply is not an object",
                method,
                channel=f"{WORKER_MODELUTIL_COMMAND} command",
                allowlist=False,
            )
        if reply.get("ok") is True:
            return {
                "ok": True,
                "value": reply.get("result"),
                "code": None,
                "message": None,
                "allowlist_entry_required": None,
                "channel": f"{WORKER_MODELUTIL_COMMAND} command",
                "method": method,
            }
        raw_failure = reply.get("failure")
        failure: Mapping[str, Any] = raw_failure if isinstance(raw_failure, Mapping) else {}
        code = failure.get("code")
        message = failure.get("message")
        return _refusal(
            code,
            message,
            method,
            channel=f"{WORKER_MODELUTIL_COMMAND} command",
            allowlist=_is_allowlist_refusal(code, message),
        )
    client = None
    try:
        client = worker.client()
    except Exception:  # noqa: BLE001 - an unusable worker is reported by the caller
        client = None
    facade = getattr(client, method, None) if client is not None else None
    if callable(facade):
        try:
            return {
                "ok": True,
                "value": facade(*arguments),
                "code": None,
                "message": None,
                "allowlist_entry_required": None,
                "channel": "client facade",
                "method": method,
            }
        except Exception as exc:  # noqa: BLE001 - probe must not raise
            return _refusal(
                error_code_of(exc),
                f"client facade {method} call failed: {type(exc).__name__}",
                method,
                channel="client facade",
                allowlist=allowlist_rejected(exc),
            )
    return _refusal(
        "ENGINE_UNRESPONSIVE",
        f"the bound Worker exposes neither a {WORKER_MODELUTIL_COMMAND} command channel nor a "
        f"{method} client facade",
        method,
        channel="unavailable",
        allowlist=False,
    )


def _channel_failure(outcome: Mapping[str, Any], label: str) -> ExecutionContractError:
    """Turn an unreachable engine channel into a refusal that keeps its own code."""
    code = outcome.get("code")
    resolved = str(code) if isinstance(code, str) and code else "ENGINE_UNRESPONSIVE"
    return ExecutionContractError(resolved, f"{label}: {resolved} ({outcome.get('message')})")


def _engine_identity(worker: Any, log: "_CallLog | None" = None) -> dict[str, Any]:
    """``ModelUtil.getComsolVersion()`` - the version of the connected runtime."""
    outcome = _license_api_call(worker, "getComsolVersion", [], log=log)
    if not outcome["ok"]:
        raise _channel_failure(outcome, "the runtime did not report its COMSOL version")
    value = outcome["value"]
    if not isinstance(value, str) or not value.strip():
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            "ModelUtil.getComsolVersion() did not return a version string",
        )
    identity: dict[str, Any] = {
        "comsol_version": value,
        "source": "ModelUtil.getComsolVersion()",
        "channel": outcome["channel"],
    }
    generation = getattr(worker, "generation", None)
    if isinstance(generation, int) and not isinstance(generation, bool):
        identity["worker_generation"] = int(generation)
    return identity


def _worker_identity(worker: Any) -> dict[str, Any]:
    """Optional, non-secret Worker identity; a failure is reported, not raised.

    Endpoint host/port are deliberately dropped from ``runtime_metadata()``:
    they identify the connection, not the runtime capability this operation
    reports.
    """
    probe = getattr(worker, "runtime_metadata", None)
    if not callable(probe):
        return {"available": False, "reason": "this Worker does not publish runtime_metadata()", "error": None}
    try:
        raw = probe()
    except Exception as exc:  # noqa: BLE001 - optional evidence
        return {"available": False, "reason": "runtime_metadata() failed", "error": error_code_of(exc)}
    if not isinstance(raw, Mapping):
        return {"available": False, "reason": "runtime_metadata() did not return an object", "error": None}
    return {
        "available": True,
        "connected": raw.get("connected") if isinstance(raw.get("connected"), bool) else None,
        "generation": raw.get("generation") if isinstance(raw.get("generation"), int) else None,
        "instance_id": raw.get("instance_id") if isinstance(raw.get("instance_id"), str) else None,
        "source": "PersistentJavaWorker.runtime_metadata()",
        "error": None,
    }


def _bound_model_used_products(worker: Any, model_tag: str, log: "_CallLog | None" = None) -> dict[str, Any]:
    """``model.getUsedProducts()`` inventory for the bound model, best effort.

    This is an inventory of what the bound model uses, *not* a license answer:
    the result is labelled as such so no caller mistakes it for one.
    """
    if log is not None:
        log.record("getUsedProducts")
    try:
        model = bound_model(worker, model_tag)
    except Exception as exc:  # noqa: BLE001 - the probe must not depend on a model
        return {"model_tag": model_tag, "used_products": None,
                "source": "Model.getUsedProducts()",
                "error": {"code": error_code_of(exc),
                          "message": f"the bound model {model_tag!r} is unavailable",
                          "allowlist_entry_required": None},
                "provenance": "inventory only; never a license decision"}
    probe = call_probe(model, "getUsedProducts")
    if not probe["ok"]:
        return {"model_tag": model_tag, "used_products": None,
                "source": "Model.getUsedProducts()", "error": probe["error"],
                "provenance": "inventory only; never a license decision"}
    value = probe["value"]
    if value is None or not isinstance(value, (list, tuple)):
        return {"model_tag": model_tag, "used_products": None,
                "source": "Model.getUsedProducts()",
                "error": {"code": "EXECUTION_STATE_UNKNOWN",
                          "message": "getUsedProducts() did not return a string list",
                          "allowlist_entry_required": None},
                "provenance": "inventory only; never a license decision"}
    products: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return {"model_tag": model_tag, "used_products": None,
                    "source": "Model.getUsedProducts()",
                    "error": {"code": "EXECUTION_STATE_UNKNOWN",
                              "message": "getUsedProducts() returned a non-string entry",
                              "allowlist_entry_required": None},
                    "provenance": "inventory only; never a license decision"}
        products.append(item)
    return {"model_tag": model_tag, "used_products": products,
            "source": "Model.getUsedProducts()", "error": None,
            "provenance": "inventory only; never a license decision"}


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def _runtime_id(payload: Mapping[str, Any]) -> str:
    """``runtime_id`` is a catalog string with no published grammar.

    Only the documented shape constraint (a non-empty string) is enforced; a
    stricter pattern would be invented rather than verified.
    """
    return require_string(payload.get("runtime_id"), "runtime_id", max_length=256)


def _requested_products(payload: Mapping[str, Any]) -> list[str]:
    """Validate the optional ``products`` array of documented-feature tokens."""
    if "products" not in payload or payload["products"] is None:
        return []
    products = require_string_array(
        payload["products"], "products", allow_empty=True, item_pattern=PRODUCT_TOKEN_PATTERN
    )
    if len(products) > MAX_PRODUCTS:
        raise ExecutionContractError(
            "INVALID_REQUEST", f"products must not contain more than {MAX_PRODUCTS} entries"
        )
    return products


def _product_rows(worker: Any, products: Sequence[str],
                  log: "_CallLog | None" = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One non-checkout license query per product; unresolved rows are returned.

    A row carries a value only when the engine returned a real boolean.  A row
    the probe could not resolve is returned separately so the caller can refuse
    the whole request instead of reporting a partially answered license state.
    """
    rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for product in products:
        outcome = _license_api_call(worker, "hasProduct", [product], log=log)
        if not outcome["ok"]:
            unresolved.append(
                {
                    "product": product,
                    "probe_error": {
                        "code": outcome["code"],
                        "message": outcome["message"],
                        "allowlist_entry_required": outcome["allowlist_entry_required"],
                    },
                }
            )
            continue
        if type(outcome["value"]) is not bool:
            unresolved.append(
                {
                    "product": product,
                    "probe_error": {
                        "code": "EXECUTION_STATE_UNKNOWN",
                        "message": "ModelUtil.hasProduct() did not return a boolean",
                        "allowlist_entry_required": None,
                    },
                }
            )
            continue
        rows.append(
            {
                "product": product,
                "hasProduct": outcome["value"],
                "product_label": FEATURE_STRING_PRODUCTS.get(product),
                "documented_feature_string": product in DOCUMENTED_FEATURE_STRINGS,
                "source": "ModelUtil.hasProduct(String)",
                "channel": outcome["channel"],
            }
        )
    return rows, unresolved


def _unresolved_refusal(unresolved: Sequence[Mapping[str, Any]], requested: int) -> ExecutionContractError:
    """A license probe that cannot see a product must not report success."""
    first = dict(unresolved[0].get("probe_error") or {})
    entry = first.get("allowlist_entry_required")
    hint = (
        f"; the Worker needs the {WORKER_MODELUTIL_COMMAND} allow-list entry {entry!r}"
        if entry
        else ""
    )
    detail = ", ".join(str(row.get("product")) for row in unresolved)
    return ExecutionContractError(
        "BLOCKED_LICENSE",
        f"no non-checkout license answer was available for {len(unresolved)} of {requested} requested "
        f"product(s): {detail} (probe ModelUtil.hasProduct refused with {first.get('code')}: "
        f"{first.get('message')}{hint}). No seat was requested and no hasProduct value is reported for "
        f"an unresolved product",
    )


# ---------------------------------------------------------------------------
# runtime.license_inspect
# ---------------------------------------------------------------------------


def license_inspect(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Non-checkout per-product license probe for one bound runtime.

    ``products`` omitted or empty is an *inventory* read: the engine identity
    and the bound model's product inventory are reported and
    ``product_probe.status`` says that nothing was probed.  With products, a
    boolean ``hasProduct`` is reported for every requested product or the
    operation refuses as a whole.
    """
    payload = operation_arguments(arguments, _LICENSE_INSPECT_FIELDS, required=("runtime_id",))
    runtime_id = _runtime_id(payload)
    products = _requested_products(payload)

    log = _CallLog()
    engine = _engine_identity(worker, log)
    inventory = _bound_model_used_products(worker, model_tag, log)

    data: dict[str, Any] = {
        "runtime_id": runtime_id,
        "engine": engine,
        "bound_model": inventory,
        "requested_products": list(products),
        "products": [],
        "missing_products": [],
        "product_probe": {
            "status": "NOT_REQUESTED",
            "method": "ModelUtil.hasProduct",
            "channel": f"{WORKER_MODELUTIL_COMMAND} command",
            "consumes_license_seat": False,
            "license_checkout_attempted": False,
            "seat_consuming_method_family": "ModelUtil.checkoutLicense*",
            "seat_consuming_methods_attempted": log.seat_consuming,
            "methods_attempted": log.attempted,
            "checked_count": 0,
            "unresolved_count": 0,
        },
    }
    if not products:
        data["verified"] = [
            "the runtime reported its COMSOL version through the Worker's controlled channel",
            "the probe did not request a license seat and no product was probed",
        ]
        data["unverified"] = [
            "no product was requested, so no hasProduct evidence exists for this call",
        ]
        data["notes"] = [
            "pass products to receive per-product hasProduct evidence; an empty request is an inventory read",
        ]
        return data

    rows, unresolved = _product_rows(worker, products, log)
    if unresolved:
        raise _unresolved_refusal(unresolved, len(products))

    missing = sorted(str(row["product"]) for row in rows if row["hasProduct"] is False)
    undoc = sorted(str(row["product"]) for row in rows if not row["documented_feature_string"])
    data["products"] = rows
    data["missing_products"] = missing
    data["product_probe"] = {
        "status": "COMPLETE",
        "method": "ModelUtil.hasProduct",
        "channel": f"{WORKER_MODELUTIL_COMMAND} command",
        "consumes_license_seat": False,
        "license_checkout_attempted": False,
        "seat_consuming_method_family": "ModelUtil.checkoutLicense*",
        "seat_consuming_methods_attempted": log.seat_consuming,
        "methods_attempted": log.attempted,
        "checked_count": len(rows),
        "unresolved_count": 0,
        "license_allows_all_requested": not missing,
    }
    data["verified"] = [
        "ModelUtil.hasProduct(String) returned a boolean for every requested product",
        "the probe is a license query, not a checkout: no seat was requested",
    ]
    data["unverified"] = [
        "hasProduct reports the license capability of this runtime; a floating license can still be "
        "exhausted when the product is actually used",
    ]
    if undoc:
        data["unverified"].append(
            f"these identifiers are not in the published License Feature Strings table: {undoc}"
        )
    if missing:
        data["unverified"].append(
            "the runtime reports no license for: " + ", ".join(missing)
        )
    data["notes"] = [
        "hasProduct is a non-checkout query (Application Programming Guide, License Methods); the "
        "seat-consuming ModelUtil.checkoutLicense family is never called by this operation",
        "missing_products is a convenience mirror of the rows with hasProduct false",
    ]
    return data


# ---------------------------------------------------------------------------
# runtime.capabilities
# ---------------------------------------------------------------------------


def _capability_rows(worker: Any, *, refresh: bool, inventory: Mapping[str, Any],
                     log: "_CallLog | None" = None) -> dict[str, Any]:
    """The verified license API surface with its reachability and live evidence."""
    rows: dict[str, Any] = {}
    for api, provenance in LICENSE_API_PROVENANCE.items():
        row = dict(provenance)
        row["live_probe"] = {"status": "NOT_RUN", "reason": "refresh=false"}
        rows[api] = row

    rows["ModelUtil.getComsolVersion"]["live_probe"] = {
        "status": "OK",
        "reason": None,
        "value_source": "engine.comsol_version",
    }
    seat_apis = [api for api, provenance in rows.items() if provenance["consumes_license_seat"]]
    for api in seat_apis:
        rows[api]["live_probe"] = {
            "status": "NOT_CALLED_BY_DESIGN",
            "reason": "a license probe must not occupy a seat",
        }
    rows["ModelUtil.hasProductForFile"]["live_probe"] = {
        "status": "NOT_CALLED_BY_DESIGN",
        "reason": "this probe is product-based; the file-level query is inventoried but unused",
    }
    rows["ModelUtil.getLicenseNumber"]["live_probe"] = {
        "status": "NOT_CALLED_BY_DESIGN",
        "reason": "the installed public API jar does not declare this method",
    }

    used_probe = rows["Model.getUsedProducts"]["live_probe"]
    inventory_error = inventory.get("error")
    if isinstance(inventory_error, Mapping):
        used_probe.update({"status": "UNAVAILABLE", "reason": inventory_error.get("code")})
    elif inventory_error:
        used_probe.update({"status": "UNAVAILABLE", "reason": str(inventory_error)})
    elif inventory.get("used_products") is None:
        used_probe.update({"status": "UNAVAILABLE", "reason": "no bound model was resolvable"})
    else:
        used_probe.update({"status": "OK", "reason": None})

    if not refresh:
        return rows

    outcome = _license_api_call(worker, "hasProduct", ["ACDC"], log=log)
    if outcome["ok"]:
        rows["ModelUtil.hasProduct"]["live_probe"] = {
            "status": "OK",
            "reason": None,
            "value": outcome["value"],
            "probe_product": "ACDC",
        }
    else:
        rows["ModelUtil.hasProduct"]["live_probe"] = {
            "status": "REFUSED",
            "reason": outcome["code"],
            "allowlist_entry_required": outcome["allowlist_entry_required"],
            "probe_product": "ACDC",
            "message": outcome["message"],
        }
    return rows


def capabilities(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Report runtime capability status, limits and the evidence for them.

    ``refresh=false`` (the default) reports the source-read reachability of the
    license API surface; ``refresh=true`` additionally *attempts* the
    allow-list-gated probe once and records the observed outcome, so a limit is
    evidenced rather than assumed.  Nothing here is cached and nothing is
    checked out.
    """
    payload = operation_arguments(arguments, _CAPABILITIES_FIELDS)
    runtime_id = _runtime_id(payload) if payload.get("runtime_id") is not None else None
    refresh = require_bool(payload["refresh"], "refresh") if payload.get("refresh") is not None else False

    log = _CallLog()
    engine = _engine_identity(worker, log)
    inventory = _bound_model_used_products(worker, model_tag, log)
    rows = _capability_rows(worker, refresh=refresh, inventory=inventory, log=log)

    limits: list[dict[str, Any]] = []
    for api, row in rows.items():
        probe = row["live_probe"]
        if probe["status"] in {"REFUSED", "UNAVAILABLE", "NOT_CALLED_BY_DESIGN"}:
            limits.append(
                {
                    "api": api,
                    "state": probe["status"],
                    "reason": probe.get("reason"),
                    "allowlist_entry_required": probe.get("allowlist_entry_required"),
                }
            )
    return {
        "runtime_id": runtime_id,
        "engine": engine,
        "worker": _worker_identity(worker),
        "bound_model": inventory,
        "refresh_requested": refresh,
        "cache": {"used": False, "reason": "every field below is read live from the Worker"},
        "probes": rows,
        "limits": limits,
        "worker_allowlist_requests": [dict(item) for item in WORKER_ALLOWLIST_REQUESTS],
        "methods_attempted": log.attempted,
        "seat_consuming_methods_attempted": log.seat_consuming,
        "seat_consuming_method_family": "ModelUtil.checkoutLicense*",
        "verified": [
            "the runtime reported its COMSOL version through the Worker's controlled channel",
            "reachability of each license API is stated from the Worker allow-lists and, when "
            "refresh=true, from an observed probe outcome",
        ],
        "unverified": [
            "installation discovery, JDK and architecture checks belong to runtime.discover/doctor "
            "and are not reported here",
            "no license seat state (free seats, checkout) is read: this operation never checks out",
        ],
        "notes": [
            "a limit is reported as a limit, not as a failed operation: this operation succeeds while "
            "the license probe capability may be BLOCKED in this build",
        ],
    }


# ---------------------------------------------------------------------------
# publishing table
# ---------------------------------------------------------------------------

OPERATIONS: dict[str, Callable[[Any, str, dict], dict]] = {
    "runtime.capabilities": capabilities,
    "runtime.license_inspect": license_inspect,
}

__all__ = ["OPERATIONS"]
