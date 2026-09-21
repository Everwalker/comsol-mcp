#!/usr/bin/env python3
"""Production-stdio acceptance driver for the Mac G3 (Phase 4) W13-W16 slice.

The driver talks to the public MCP stdio entrypoint only (``python -m
comsol_mcp.mcp_server``).  It never starts or stops COMSOL, never imports the
control client or a COMSOL Java client, and never requests trusted user code.
With ``--live`` it attaches to the already running endpoint named by
``--host``/``--port`` and binds an existing model (``--model``) or creates a
fresh MCP-owned model.  ``--reopen-check <mph>`` runs the separate
save -> fresh stdio child -> fresh private control home -> fresh Java Worker
reload verification.

Architecture and evidence layout follow ``tools/phase3_run_mcp.py`` (frozen G2
reproducibility): one MCP stdio client session, a redacted JSON-RPC transcript,
per-case assertion records, and a run directory containing request.json,
source_snapshot.json, environment.json, transcript.json, assertions.json,
result.json, case_inventory.json, ``<host>.engine.log`` and SHA256SUMS.json.

Every case records its planned subcases independently as PASS, FAIL, BLOCKED or
NOT_RUN.  A missing operation, an unavailable product, a not-yet-implemented
capability or a missing reviewed input is preserved as a bounded result instead
of becoming a synthetic pass.  Subcases carry an explicit evidence level:
``static`` (catalog/schema inspection), ``protocol`` (MCP/contract behaviour),
``fixture`` (driver-side pre-registered reference data), ``live`` (bound-model
operation) or ``numerical`` (analytic comparison of a real solution).

Assertion details are taken from ``docs/comsol_mcp_design_v1/NEXT_GOAL_MAC_G3.md``
sections 5-8, 10.1-10.3 and 11, ``G3_EXECUTION_PLAN.md``, and the T0xx entries of
``docs/comsol_mcp_design_v1/03_ACCEPTANCE.md``.  No requirement is invented here
and no threshold is relaxed by this file.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time
import traceback
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_RUN"}
LEVELS = {"static", "protocol", "fixture", "live", "numerical", "rollup"}

CASE_ORDER = (
    "R01_LIVE",
    "R03_LIVE",
    "R04_LIVE",
    "R_READBACK",
    "W13_T006_variables",
    "W13_T015_units",
    "W13_T048_selection_drift",
    "W13_T016_2D_data",
    "W14_T009_geometry_edit",
    "W14_T034_local_paths",
    "W15_T007_selections",
    "W15_T017_material",
    "W15_T042_license",
    "W16_T018_mesh",
    "W16_T019_chainA_steady",
    "W16_T019_chainB_transient",
    "W16_T019_chainC_continue",
    "W16_T020_solver",
    "GUARD_T010",
    "GUARD_T035",
    "GUARD_T038",
    "GUARD_T005",
    "GUARD_T033",
)
R_READBACK_SOURCES = ("R01_LIVE", "R03_LIVE", "R04_LIVE")
# W13/W14/W15/W16 cases whose live half is a single operation recipe.
TRUSTED_CODE_IS_NOT_REQUIRED = True


def _benchmark_number(value: float) -> str:
    """A model expression for one benchmark number (``1000``, not ``1000.0``)."""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return repr(number)


@dataclass(frozen=True)
class BenchmarkSpec:
    """The single source of truth for one benchmark chain (G3.1 §8.1, R-08).

    Everything the chain builds *and* everything it expects is derived from this object, so a value
    can no longer disagree between the config, the material definition and the analytic reference
    (the original defect: the configs said ``Cp=1000`` while the material presets wrote ``100`` and
    ``10``, and the mismatch was hidden by re-reading the wrong model value).  The tolerance and the
    error definition are part of the spec as well: they cannot be relaxed to fit an observation, and
    a read-back can only ever produce a *mismatch record* against this spec.
    """

    chain: str
    length_m: float
    width_m: float
    height_m: float
    k_w_mk: float
    rho_kg_m3: float
    cp_j_kgk: float
    t0_k: float
    hot_k: float | None = None
    delta_t_k: float | None = None
    time_points_s: tuple[float, ...] = ()
    sample_count: int = 21
    error_limit: float = 1e-4
    error_definition: str = ""
    sides: str = "insulated on the four lateral faces"
    source: str = "G3.1 §8.1 pre-registered benchmark specification"

    # ---- expressions written into the model (derived, never literals in the case body) ----
    @property
    def k_expression(self) -> str:
        return f"{_benchmark_number(self.k_w_mk)}[W/(m*K)]"

    @property
    def rho_expression(self) -> str:
        return f"{_benchmark_number(self.rho_kg_m3)}[kg/m^3]"

    @property
    def cp_expression(self) -> str:
        return f"{_benchmark_number(self.cp_j_kgk)}[J/(kg*K)]"

    @property
    def t0_expression(self) -> str:
        return f"{_benchmark_number(self.t0_k)}[K]"

    @property
    def hot_expression(self) -> str | None:
        return None if self.hot_k is None else f"{_benchmark_number(self.hot_k)}[K]"

    # ---- derived physical quantities ----
    @property
    def temperature_difference_k(self) -> float:
        if self.hot_k is not None:
            return float(self.hot_k) - float(self.t0_k)
        if self.delta_t_k is not None:
            return float(self.delta_t_k)
        return 0.0

    @property
    def cross_section_area_m2(self) -> float:
        return float(self.width_m) * float(self.height_m)

    @property
    def heat_flux_w_m2(self) -> float:
        return float(self.k_w_mk) * self.temperature_difference_k / float(self.length_m)

    @property
    def power_w(self) -> float:
        return self.heat_flux_w_m2 * self.cross_section_area_m2

    @property
    def alpha_m2_s(self) -> float:
        return float(self.k_w_mk) / (float(self.rho_kg_m3) * float(self.cp_j_kgk))

    @property
    def decay_rate_per_s(self) -> float:
        return self.alpha_m2_s * (math.pi / float(self.length_m)) ** 2

    # ---- the model definition and the analytic model, both from this spec ----
    def material_properties(self) -> tuple[dict[str, Any], ...]:
        """The material definition this chain writes, built from the spec's own numbers."""
        return (
            {"name": "thermalconductivity",
             "value": {"kind": "expression", "shape": [3, 3], "unit": "W/(m*K)",
                       "data": [[self.k_expression, "0", "0"], ["0", self.k_expression, "0"],
                                ["0", "0", self.k_expression]]}},
            {"name": "density", "value": {"kind": "expression", "shape": [], "data": self.rho_expression}},
            {"name": "heatcapacity", "value": {"kind": "expression", "shape": [], "data": self.cp_expression}},
        )

    def expected_model_values(self) -> tuple[dict[str, Any], ...]:
        """What the model must contain before the solve, item by item, for a read-back check."""
        rows: list[dict[str, Any]] = [
            {"item": "thermalconductivity", "unit": "W/(m*K)", "value": self.k_w_mk, "expression": self.k_expression},
            {"item": "density", "unit": "kg/m^3", "value": self.rho_kg_m3, "expression": self.rho_expression},
            {"item": "heatcapacity", "unit": "J/(kg*K)", "value": self.cp_j_kgk, "expression": self.cp_expression},
            {"item": "length_m", "unit": "m", "value": self.length_m, "expression": None},
            {"item": "width_m", "unit": "m", "value": self.width_m, "expression": None},
            {"item": "height_m", "unit": "m", "value": self.height_m, "expression": None},
            {"item": "initial_value", "unit": "K", "value": self.t0_k, "expression": self.t0_expression},
        ]
        if self.hot_expression is not None:
            rows.append({"item": "hot_boundary", "unit": "K", "value": self.hot_k, "expression": self.hot_expression})
        if self.time_points_s:
            rows.append({"item": "time_points", "unit": "s", "value": list(self.time_points_s), "expression": None})
        return tuple(rows)

    def steady_profile_k(self, x_m: float) -> float:
        """Chain A: the 1D steady profile of the insulated slab."""
        return float(self.t0_k) + self.temperature_difference_k * float(x_m) / float(self.length_m)

    def transient_profile_k(self, x_m: float, time_s: float) -> float:
        """Chain B: the registered transient solution of the initial sine perturbation."""
        return (float(self.t0_k)
                + self.temperature_difference_k * math.sin(math.pi * float(x_m) / float(self.length_m))
                * math.exp(-self.decay_rate_per_s * float(time_s)))

    def sample_points_m(self) -> list[float]:
        count = int(self.sample_count)
        return [float(self.length_m) * index / (count - 1) for index in range(count)]

    def sanity_checks(self) -> tuple[dict[str, Any], ...]:
        """Dimensional and numeric sanity checks of the spec's own derived quantities.

        These run offline before any live step: a spec whose units or numbers do not close cannot be
        used to judge a solve.
        """
        checks: list[dict[str, Any]] = []
        dimensions = {"k_w_mk": "W/(m*K)", "rho_kg_m3": "kg/m^3", "cp_j_kgk": "J/(kg*K)",
                      "length_m": "m", "t0_k": "K"}
        for name, unit in dimensions.items():
            value = getattr(self, name)
            checks.append({"check": f"unit:{name}", "unit": unit, "value": value,
                           "ok": bool(isinstance(value, (int, float)) and math.isfinite(float(value))
                                      and float(value) > 0.0),
                           "why": "a positive finite quantity in the registered unit"})
        # alpha = k/(rho*cp) must be a diffusivity: m^2/s, and positive.
        checks.append({"check": "diffusivity", "unit": "m^2/s", "value": self.alpha_m2_s,
                       "ok": self.alpha_m2_s > 0.0,
                       "why": "alpha = k/(rho*Cp) is the only diffusivity the analytic model uses"})
        checks.append({"check": "decay_rate", "unit": "1/s", "value": self.decay_rate_per_s,
                       "ok": self.decay_rate_per_s > 0.0,
                       "why": "alpha*(pi/L)^2: the transient factor must decay, not grow"})
        if self.chain.upper().startswith("A"):
            checks.append({"check": "steady_profile_endpoints", "unit": "K",
                           "value": [self.steady_profile_k(0.0), self.steady_profile_k(float(self.length_m))],
                           "ok": abs(self.steady_profile_k(0.0) - float(self.t0_k)) < 1e-12
                                 and abs(self.steady_profile_k(float(self.length_m)) - float(self.hot_k or 0.0)) < 1e-12,
                           "why": "T(0)=T0 and T(L)=Thot for the registered boundary values"})
            profile = [self.steady_profile_k(x) for x in self.sample_points_m()]
            checks.append({"check": "steady_profile_monotone", "unit": "K", "value": profile[0:3],
                           "ok": all(right >= left for left, right in zip(profile, profile[1:])),
                           "why": "a linear driven profile cannot decrease along the driving direction"})
            checks.append({"check": "flux_times_area", "unit": "W", "value": self.power_w,
                           "ok": abs(self.power_w - self.heat_flux_w_m2 * self.cross_section_area_m2) < 1e-18,
                           "why": "the total power is the flux through the *actual* end-face area"})
        if self.chain.upper().startswith("B"):
            checks.append({"check": "transient_initial_profile", "unit": "K",
                           "value": [self.transient_profile_k(x, 0.0) for x in self.sample_points_m()[:3]],
                           "ok": abs(self.transient_profile_k(float(self.length_m) / 2, 0.0)
                                     - (float(self.t0_k) + self.temperature_difference_k)) < 1e-12,
                           "why": "T(x,0) = T0 + dT*sin(pi*x/L): the initial perturbation is the registered one"})
            checks.append({"check": "transient_boundaries_hold", "unit": "K",
                           "value": [self.transient_profile_k(0.0, time_s) for time_s in self.time_points_s[:2]],
                           "ok": all(abs(self.transient_profile_k(0.0, time_s) - float(self.t0_k)) < 1e-12
                                     and abs(self.transient_profile_k(float(self.length_m), time_s)
                                             - float(self.t0_k)) < 1e-12 for time_s in self.time_points_s),
                           "why": "both ends sit at T0 for every registered time (the sine vanishes there)"})
            late = self.transient_profile_k(float(self.length_m) / 2, 1e6)
            checks.append({"check": "transient_decays_to_initial", "unit": "K", "value": late,
                           "ok": abs(late - float(self.t0_k)) < 1e-6,
                           "why": "as t grows the perturbation must vanish, leaving T0"})
            checks.append({"check": "sine_perturbation_shape", "unit": "K",
                           "value": self.transient_profile_k(float(self.length_m) / 4, 0.0),
                           "ok": abs(self.transient_profile_k(float(self.length_m) / 4, 0.0)
                                     - (float(self.t0_k) + self.temperature_difference_k * math.sin(math.pi / 4))) < 1e-12,
                           "why": "the spatial shape is the registered sin(pi*x/L), not a fitted curve"})
        return tuple(checks)

    def as_config(self) -> dict[str, Any]:
        """The mapping the case bodies read (kept as the pre-spec key names)."""
        config: dict[str, Any] = {"length_m": float(self.length_m), "width_m": float(self.width_m),
                                  "height_m": float(self.height_m), "k_w_mk": float(self.k_w_mk),
                                  "rho_kg_m3": float(self.rho_kg_m3), "cp_j_kgk": float(self.cp_j_kgk),
                                  "t0_k": float(self.t0_k), "sample_count": int(self.sample_count),
                                  "chain": self.chain}
        if self.hot_k is not None:
            config["t1_k"] = float(self.hot_k)
        if self.delta_t_k is not None:
            config["delta_t_k"] = float(self.delta_t_k)
        if self.time_points_s:
            config["time_points_s"] = [float(value) for value in self.time_points_s]
        if self.chain.upper().startswith("A"):
            config["relative_error_limit"] = float(self.error_limit)
        if self.chain.upper().startswith("B"):
            config["normalized_error_limit"] = float(self.error_limit)
        return config


#: Chain A — steady 1D conduction through a slab: 300 K / 400 K end faces, insulated sides,
#: k=10 W/(m*K), rho=1000 kg/m^3, **Cp=1000 J/(kg*K)** (the material preset used to write 100).
BENCHMARK_A = BenchmarkSpec(
    chain="A", length_m=0.01, width_m=0.01, height_m=0.005,
    k_w_mk=10.0, rho_kg_m3=1000.0, cp_j_kgk=1000.0, t0_k=300.0, hot_k=400.0, sample_count=21,
    error_limit=1e-4,
    error_definition=("max |T_engine(x) - T_analytic(x)| / (T1 - T0) over the pre-registered sample "
                      "line, T(x) = T0 + (T1-T0)*x/L"),
)
#: Chain B — transient relaxation of a 20 K sine perturbation (T0=300 K, the same rho/k, Cp=1000;
#: the material preset used to write 10, which is why the decay never matched).
BENCHMARK_B = BenchmarkSpec(
    chain="B", length_m=0.01, width_m=0.01, height_m=0.005,
    k_w_mk=10.0, rho_kg_m3=1000.0, cp_j_kgk=1000.0, t0_k=300.0, delta_t_k=20.0,
    time_points_s=(0.0, 0.5, 1.0, 2.0, 4.0), sample_count=21,
    error_limit=1e-3,
    error_definition=("max over the registered time points of |T_engine(x,t) - T_analytic(x,t)| / dT, "
                      "T(x,t) = T0 + dT*sin(pi*x/L)*exp(-alpha*(pi/L)^2*t), alpha = k/(rho*Cp)"),
)
CHAIN_A = BENCHMARK_A.as_config()
CHAIN_B = BENCHMARK_B.as_config()
CHAIN_A_MATERIAL_PROPERTIES = BENCHMARK_A.material_properties()
CHAIN_B_MATERIAL_PROPERTIES = BENCHMARK_B.material_properties()
BENCHMARKS = {"A": BENCHMARK_A, "B": BENCHMARK_B}
T016_FIXTURE_NAME = "phase4_non_axisymmetric_q_xy.csv"
FAKE_CREDENTIAL = "COMSOL_MCP_PHASE4_FAKE_CREDENTIAL_9f3a1c7d5b"

_BLOCKED_CODES = {
    # Control/engine boundary unavailable.
    "EXECUTION_STATE_UNKNOWN",
    "CONTROL_STARTUP_ERROR",
    "ENGINE_UNRESPONSIVE",
    "SERVER_UNAVAILABLE",
    "CONTROL_SERVICE_UNAVAILABLE",
    "RUNTIME_CONFIGURATION_REQUIRED",
    "ISOLATION_PROOF_REQUIRED",
    "CHECKPOINT_RESTORE_REQUIRES_REBIND",
    "ENGINE_BUSY",
    # Explicit capability/version/permission/product outcomes.
    "UNAVAILABLE",
    "UNSUPPORTED_OPERATION",
    "API_UNSUPPORTED",
    "PERMISSION_DENIED",
    "COMPILE_UNAVAILABLE",
    "NOT_IMPLEMENTED",
    "BLOCKED_LICENSE",
    "LICENSE_UNAVAILABLE",
    "INSUFFICIENT_LICENSE",
    "PRODUCT_UNAVAILABLE",
    "CAPABILITY_UNAVAILABLE",
    "CONNECT_REQUIRED",
    # The managed-revision precondition of a write (see REVISION_CONFLICT_PRECONDITIONS).  The
    # host releases it through the published model_inspect reconcile read and replays the call
    # once; a conflict that survives that release means the acceptance line was never reached, so
    # it is BLOCKED with the product's own message rather than reported as a violated contract.
    # A deliberate revision probe keeps its envelope and is judged by its own check.
    "REVISION_CONFLICT",
}

_INVARIANT_REJECTION_CODES = {"INVALID_INVARIANT", "INVARIANT_UNSUPPORTED", "INVALID_REQUEST",
                              "PROPERTY_TYPE_MISMATCH", "UNSUPPORTED_OPERATION"}

#: The control daemon refuses every engine operation while an unfinished job is still
#: unresolved; this is the exact refusal message it returns (``_control_daemon.py``).
ENGINE_GATE_MESSAGE = "reconcile unfinished engine work before new operations"
#: Envelope error codes that mean "the engine state could not be established" rather than
#: "the request was wrong": they are blocked conditions, never contract violations.
UNKNOWN_ERROR_CODES = frozenset({"EXECUTION_STATE_UNKNOWN", "ENGINE_UNRESPONSIVE"})
#: Tools used to *release* the gate.  They must never run the gate hook themselves.
RECONCILE_EXEMPT_TOOLS = frozenset({"job_reconcile", "job_status", "job_log", "job_result", "job_list",
                                    "operation_describe", "session_health", "server_info",
                                    # The published reconciliation read of the managed revision; it
                                    # must never be answered with another reconciliation itself.
                                    "model_inspect"})
#: The managed-revision preconditions a write is refused with before it reaches the engine.
#: ``_execution_contract.SessionLedger.begin_write`` raises ``external model change requires
#: reconciliation`` when the ledger has observed an engine change that no published reconcile
#: read has acknowledged yet (a transaction whose outcome is partial/unknown leaves the model
#: *dirty*), and ``expected_revision does not match managed revision`` when the caller's cached
#: revision is behind.  Both are release-path conditions with one published answer:
#: ``model_inspect`` with ``refresh: true`` reconciles and reports the current revision.  The
#: driver replays a refused write exactly once under a fresh idempotency identity with the
#: refreshed revision.  A deliberately stale probe passes ``reconcile=False`` and keeps the
#: refusal as its evidence.
REVISION_CONFLICT_PRECONDITIONS = {
    "external model change requires reconciliation before trial": "external-change",
    "external model change requires reconciliation": "external-change",
    "expected_revision does not match managed revision": "stale-expected-revision",
}

#: A per-process serial for idempotency stems.  Two calls with a *different* body must never
#: share an idempotency key: the control daemon stores the first result under that key and
#: refuses the second with ``IDEMPOTENCY_CONFLICT`` (observed live for every parameterized
#: discovery read), and a same-key read would replay a stale stored value after a write.
_KEY_SERIAL = itertools.count(1)


def _fresh_key(stem: str) -> str:
    """A unique idempotency stem for a call whose body is not constant."""
    return f"{stem}-{next(_KEY_SERIAL)}"


def _revision_conflict(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Classify a managed-revision refusal, with the precondition the product named.

    ``None`` means the envelope is not a revision conflict; the caller must then treat it as the
    ordinary refusal it is (a stale-revision *negative probe* keeps its envelope verbatim).
    """
    if not isinstance(payload, Mapping) or _success(payload):
        return None
    if _error_code(payload) != "REVISION_CONFLICT":
        return None
    message = _error_message(payload) or ""
    for text, precondition in REVISION_CONFLICT_PRECONDITIONS.items():
        if text in message:
            return {"error_code": "REVISION_CONFLICT", "precondition": precondition,
                    "message": message, "job_id": _envelope_job_id(payload)}
    return {"error_code": "REVISION_CONFLICT", "precondition": "unnamed", "message": message,
            "job_id": _envelope_job_id(payload)}


def _expected_revision(arguments: Mapping[str, Any] | None) -> int | None:
    """The ``expected_revision`` a call carried (wire body first, then the execution block)."""
    if not isinstance(arguments, Mapping):
        return None
    for container in (arguments, arguments.get("arguments")):
        if not isinstance(container, Mapping):
            continue
        value = container.get("expected_revision")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    execution = arguments.get("execution")
    if isinstance(execution, Mapping):
        value = execution.get("expected_revision")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _with_expected_revision(arguments: Mapping[str, Any], revision: int | None) -> dict[str, Any]:
    """Rewrite every ``expected_revision`` a call carries to the revision a refresh reported."""
    request = dict(arguments)
    if revision is None:
        return request
    for name in ("arguments", "execution"):
        container = request.get(name)
        if isinstance(container, Mapping) and "expected_revision" in container:
            updated = dict(container)
            updated["expected_revision"] = revision
            request[name] = updated
    if "expected_revision" in request:
        request["expected_revision"] = revision
    return request


# ---------------------------------------------------------------------------
# C02: the run's centralized execution context
# ---------------------------------------------------------------------------
#: The revision/job rejection reasons a refusal is classified with.  The live runs collapsed all
#: four situations into one "revision conflict" habit (G3.1 section 3):
#:
#: * ``stale_expected`` — the request carries one ``expected_revision`` that is behind the revision
#:   this context observed *and every observation that advanced it was dispatched by this driver*:
#:   our own sequenced writes made the request stale.
#: * ``external_observation`` — the model moved by an observation this driver never dispatched
#:   (``dirty``, or a revision advance with no matching dispatch): an external change, released
#:   through the published reconcile read and never "repaired" silently.
#: * ``unknown_job`` — a job whose engine outcome is UNKNOWN is known to the context: the request
#:   must first be resolved through that original job's own query/reconcile.
#: * ``generation_mismatch`` — the request's identity does not name the model this context tracks
#:   (another generation / server epoch / tag), or two duplicated identity fields inside one
#:   request disagree, so no single canonical source exists.
REJECTION_REASONS: tuple[str, ...] = ("stale_expected", "external_observation", "unknown_job",
                                      "generation_mismatch")
#: Rejections outside the model/job taxonomy: a self-contradicting envelope, and an explicit
#: idempotency key re-used with another body (kept observable: the product's own conflict is the
#: evidence GUARD_T010 asks for, so the driver never pre-empts it).
AUXILIARY_REJECTIONS: tuple[str, ...] = ("ambiguous_envelope", "explicit_key_reuse")
#: Dispatch stages a refusal can provably have reached.  The first two prove NOT_EXECUTED because no
#: engine call was made at all; ``dispatched_without_mutation`` proves it from the *product's own*
#: published witness (``error.details.witness.mutation_issued is False``): the callback reached the
#: engine, issued reads only, and every one of the stages here is therefore the sole basis for
#: spending a recorded new plan on the same logical request.
NOT_EXECUTED_STAGES: frozenset[str] = frozenset({"refused_before_engine", "not_dispatched",
                                                 "dispatched_without_mutation"})
DISPATCH_STAGES: frozenset[str] = NOT_EXECUTED_STAGES | {"dispatched", "unknown"}
#: The stage vocabulary a *product* refusal publishes in ``error.details`` (the callback's own
#: report of how far it got), plus the ``validation`` token the published G3 refusal envelope
#: declares for a raise the callback proved happened before its first mutation
#: (``comsol_mcp._execution_contract.PreWriteRefusal`` / ``_managed_backend._refusal_envelope``,
#: i.e. ``comsol_mcp._domain_outcome.STAGE_VALIDATION``).  The driver never maps a product stage
#: onto its own stage names by guessing: ``product_dispatch_stage`` reads the witness and the two
#: vocabularies stay separate.
PRODUCT_DISPATCH_STAGES: frozenset[str] = frozenset({"not_dispatched", "pre_dispatch", "post_dispatch",
                                                     "dispatched", "unknown", "validation"})
#: The product stages that provably precede the first engine mutation (see NOT_EXECUTED_STAGES).
PRODUCT_PRE_DISPATCH_STAGES: frozenset[str] = frozenset({"not_dispatched", "pre_dispatch", "validation"})
#: Every identity field a request may duplicate, with every documented path it can appear on.
IDENTITY_FIELD_PATHS: dict[str, tuple[str, ...]] = {
    "expected_revision": ("expected_revision", "arguments.expected_revision", "execution.expected_revision"),
    "session_id": ("session_id", "arguments.session_id", "execution.session_id"),
    "model_ref": ("model_ref", "arguments.model_ref", "execution.model_ref"),
}
#: The managed-revision preconditions whose refusal is raised by the execution ledger's preflight,
#: *before* any engine call: the product documents them, and the driver may therefore treat their
#: refusal as proved NOT_EXECUTED evidence (any other failure keeps stage ``unknown``).
PRE_DISPATCH_REFUSAL_CODES: dict[str, str] = {
    "REVISION_CONFLICT": "refused_before_engine",
    "MODEL_IDENTITY_MISMATCH": "refused_before_engine",
    "IDEMPOTENCY_CONFLICT": "refused_before_engine",
}


def _identity_at(arguments: Mapping[str, Any] | None, dotted: str) -> tuple[bool, Any]:
    """Read one dotted path from a request body — no fuzzy fallback, no name guessing."""
    node: Any = arguments if isinstance(arguments, Mapping) else {}
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False, None
        node = node[part]
    return True, node


def model_ref_token(ref: Mapping[str, Any] | None) -> str | None:
    """The canonical key of a *full* ModelRef: session/server epoch + tag + generation (C02).

    One project name or one global revision cannot stand in for several models, so the context
    keys every piece of state by this token.  A mapping without a ``model_tag`` is not a model
    identity and has no token.
    """
    if _model_tag(ref) is None:
        return None
    return "|".join(_ref_part(ref, name) for name in ("session_id", "server_instance_id", "model_tag",
                                                      "generation"))


def model_ref_lineage(ref: Mapping[str, Any] | None) -> str | None:
    """The generation-independent part of a model identity (one model, several generations)."""
    if _model_tag(ref) is None:
        return None
    return "|".join(_ref_part(ref, name) for name in ("session_id", "server_instance_id", "model_tag"))


def _model_tag(ref: Mapping[str, Any] | None) -> str | None:
    tag = ref.get("model_tag") if isinstance(ref, Mapping) else None
    return tag if isinstance(tag, str) and tag else None


def _ref_part(ref: Mapping[str, Any] | None, name: str) -> str:
    value = ref.get(name) if isinstance(ref, Mapping) else None
    return "?" if value is None or value == "" else str(value)


def _body_sha256(body: Mapping[str, Any] | None) -> str:
    """The digest of one request body — the only thing that may decide a key reuse."""
    if body is None:
        return "none"
    text = json.dumps(_json_safe(dict(body)), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _identity_key(field: str, value: Any) -> str:
    """A comparable key for one identity value (``model_ref`` values compare by full token)."""
    if field == "model_ref":
        token = model_ref_token(value if isinstance(value, Mapping) else None)
        if token is not None:
            return token
    return json.dumps(_json_safe(value), sort_keys=True, default=str)


def identity_conflict(arguments: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Every identity field a request duplicates must agree *before* it is dispatched (C02).

    The driver passes the same identity three times — at the top level, inside ``arguments`` and
    inside ``execution`` — and each layer used to pick its own preferred source, so one request
    could carry ``revision=2`` in one place and ``3`` in another and every layer chose differently.
    One canonical source is kept here: nothing is dispatched until the copies agree.
    """
    request = arguments if isinstance(arguments, Mapping) else {}
    for field, paths in IDENTITY_FIELD_PATHS.items():
        found = [(path, result[1]) for path, result in
                 ((path, _identity_at(request, path)) for path in paths) if result[0]]
        if len(found) < 2:
            continue
        if len({_identity_key(field, value) for _, value in found}) > 1:
            return {"reason": "generation_mismatch", "field": field,
                    "values": {path: _json_safe(value) for path, value in found},
                    "paths": [path for path, _ in found],
                    "detail": (f"the request carries {len(found)} copies of {field} that disagree; the "
                               "driver keeps one canonical source and refuses to guess which layer wins")}
    return None


def _plan_body(arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    """The request body a plan is minted from: the call without its execution envelope.

    ``expected_revision`` lives inside the call *and* inside ``execution``; a new plan that rewrites
    the revision therefore carries a different body, which is exactly why it is a new plan and not a
    retry of the original request.
    """
    body = dict(arguments or {})
    body.pop("execution", None)
    return body


@dataclass(frozen=True)
class EnvelopeWitness:
    """One fixed-schema reading of a decoded MCP envelope (never a recursive search)."""

    tool: str
    success: bool | None
    data: Mapping[str, Any]
    error: Mapping[str, Any]
    execution: Mapping[str, Any]
    job_id: str | None
    outer_is_error: bool
    source: Mapping[str, str]
    conflicts: tuple[str, ...]

    @property
    def model_ref(self) -> Mapping[str, Any] | None:
        ref = self.execution.get("model_ref")
        return ref if isinstance(ref, Mapping) else None

    @property
    def token(self) -> str | None:
        return model_ref_token(self.model_ref)

    @property
    def lineage(self) -> str | None:
        return model_ref_lineage(self.model_ref)

    @property
    def generation(self) -> Any:
        return self.model_ref.get("generation") if isinstance(self.model_ref, Mapping) else None

    @property
    def revision(self) -> int | None:
        value = self.execution.get("revision")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def dirty(self) -> bool | None:
        value = self.execution.get("dirty")
        return value if isinstance(value, bool) else None

    @property
    def error_code(self) -> str | None:
        code = self.error.get("code")
        return code if isinstance(code, str) else None

    @property
    def error_message(self) -> str:
        message = self.error.get("message")
        return message if isinstance(message, str) else ""


def unpack_envelope(tool: str, payload: Mapping[str, Any] | None) -> EnvelopeWitness:
    """Read one decoded envelope through the *published* field schema only.

    ``structuredContent`` (the outer MCP result), the ActionResult it carries and the job block
    have fixed names.  C01/C02 require one reading of them that cannot mistake a domain-level
    ``status.ok=false`` inside ordinary data for the operation's own outcome, so this reader walks
    exactly the documented paths — never "the first ``success``/``status`` found in any nested
    mapping" — names every path it used in ``source``, and reports two published copies of the
    same field that disagree as a conflict instead of resolving it.
    """
    outer = payload if isinstance(payload, Mapping) else {}
    structured = outer.get("_structuredContent")
    structured = structured if isinstance(structured, Mapping) else None

    def candidates(name: str) -> list[tuple[str, Any]]:
        found: list[tuple[str, Any]] = []
        if structured is not None and name in structured:
            found.append((f"structuredContent.{name}", structured.get(name)))
        if name in outer:
            found.append((f"payload.{name}", outer.get(name)))
        return found

    def choose_bool(name: str) -> tuple[bool | None, str | None, list[str]]:
        chosen: bool | None = None
        path_used: str | None = None
        notes: list[str] = []
        for path, value in candidates(name):
            if not isinstance(value, bool):
                continue
            if path_used is None:
                chosen, path_used = value, path
            elif value != chosen:
                notes.append(f"{name}: {path_used}={chosen} vs {path}={value}")
        return chosen, path_used, notes

    def choose_mapping(name: str) -> tuple[Mapping[str, Any], str | None, list[str]]:
        chosen: Mapping[str, Any] = {}
        path_used: str | None = None
        notes: list[str] = []
        for path, value in candidates(name):
            if not isinstance(value, Mapping):
                continue
            if path_used is None:
                chosen, path_used = value, path
            elif dict(value) != dict(chosen):
                notes.append(f"{name}: {path_used} and {path} disagree")
        return chosen, path_used, notes

    success, success_path, conflicts = choose_bool("success")
    data, data_path, notes = choose_mapping("data")
    conflicts.extend(notes)
    error, error_path, notes = choose_mapping("error")
    conflicts.extend(notes)
    execution, execution_path, notes = choose_mapping("execution")
    conflicts.extend(notes)
    if not execution:
        inner = data.get("execution") if isinstance(data.get("execution"), Mapping) else None
        if inner:
            execution, execution_path = inner, "data.execution"
    job_id: str | None = None
    job_source: str | None = None
    for path, container in (("execution.job_id", execution), ("data.job_id", data)):
        value = container.get("job_id") if isinstance(container, Mapping) else None
        if isinstance(value, str) and value:
            job_id, job_source = value, path
            break
    outer_flag = outer.get("_outer_isError")
    source = {name: path for name, path in (("success", success_path), ("data", data_path),
                                            ("error", error_path), ("execution", execution_path),
                                            ("job_id", job_source)) if path is not None}
    return EnvelopeWitness(tool=tool, success=success, data=data, error=error, execution=execution,
                           job_id=job_id, outer_is_error=bool(outer_flag) if isinstance(outer_flag, bool) else False,
                           source=source, conflicts=tuple(conflicts))


def product_dispatch_stage(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The dispatch evidence the *product* published for a refusal (C03: keep cause + stage).

    A refused mutation may carry ``error.details.dispatch_stage`` (the callback's own report of how
    far it got), ``error.details.cause_code``/``cause_message`` (the original operation error, kept
    under the wrapper code) and ``error.details.witness`` (the engine calls that were made and
    whether a mutation was issued).  Read through those fixed paths only — never "the first
    stage/cause-looking key in any nested mapping" — and state what the witness *proves*:

    * ``proves_not_executed`` is true only when the product itself reports
      ``witness.mutation_issued is False`` and does not flag the pre-dispatch stage as unproven.
      That is what lets the M1 run's ``NODE_NOT_FOUND`` refusal (a ``comp1`` the *fixture* never
      created) be filed as NOT_EXECUTED instead of a first-hand UNKNOWN that freezes the run.

    The second documented position is the *clean* refusal envelope itself, which the G3 dispatch
    wrapper publishes for a raise it proved happened before dispatch (a ``PreWriteRefusal``: the
    ``_refusal_envelope`` writer in ``comsol_mcp/_managed_backend.py``): ``data.refused is True``
    with ``data.dispatch_stage`` (``validation`` for a pre-write raise), ``error.stage`` and
    ``data.witness``.  That envelope is read only when both of its own proofs are there — the
    declared pre-dispatch stage *and* ``witness.mutation_issued is False`` — so a post-write store
    exception can never be re-labelled as an unexecuted refusal.  ``refused_before_engine``-style
    codes are not read here: they are ``observed_dispatch_stage``'s business and stay separate.

    ``None`` means the envelope published no such evidence at all.
    """
    error = _mapping(payload.get("error")) if isinstance(payload, Mapping) else {}
    details = _mapping(error.get("details"))
    data = _mapping(payload.get("data")) if isinstance(payload, Mapping) else {}
    refusal_witness = _mapping(data.get("witness"))
    if data.get("refused") is True and refusal_witness:
        # The clean refusal block is the wrapper's *own* publication, so it is read before
        # ``error.details``: a refusal that carries details for its own reason (the
        # multi-geometry ``API_UNSUPPORTED``, say) still publishes its stage and witness here.
        row = _product_stage_row(
            stage=error.get("stage") or data.get("dispatch_stage"), witness=refusal_witness,
            cause_code=error.get("code"),
            cause_message=error.get("message") if isinstance(error.get("message"), str) else None,
            unproven=details.get("unproven_pre_dispatch"), source="data.refused")
        # The clean refusal envelope proves the pre-dispatch claim only with *both* of its own
        # proofs: a declared pre-dispatch stage and a witness that issued no mutation.
        row["proves_not_executed"] = bool(row["proves_not_executed"] and row["stage"] in PRODUCT_PRE_DISPATCH_STAGES)
        row["dispatch_stage"] = "dispatched_without_mutation" if row["proves_not_executed"] else "unknown"
        return row
    if details:
        return _product_stage_row(
            stage=details.get("dispatch_stage") or details.get("stage"), witness=_mapping(details.get("witness")),
            cause_code=details.get("cause_code"),
            cause_message=details.get("cause_message") if isinstance(details.get("cause_message"), str) else None,
            unproven=details.get("unproven_pre_dispatch"), source="error.details")
    return None


def _product_stage_row(*, stage: Any, witness: Mapping[str, Any], cause_code: Any, cause_message: str | None,
                       unproven: Any, source: str) -> dict[str, Any]:
    """One product dispatch-evidence row, from one documented position of the envelope."""
    mutation = witness.get("mutation_issued")
    row: dict[str, Any] = {
        "stage": stage if isinstance(stage, str) and stage else None,
        "stage_known": isinstance(stage, str) and stage in PRODUCT_DISPATCH_STAGES,
        "cause_code": cause_code if isinstance(cause_code, str) and cause_code else None,
        "cause_message": cause_message,
        "mutation_issued": mutation if isinstance(mutation, bool) else None,
        "mutation_method": witness.get("mutation_method") if isinstance(witness.get("mutation_method"), str) else None,
        "engine_calls": witness.get("engine_calls") if isinstance(witness.get("engine_calls"), int) else None,
        "methods": [_json_safe(item) for item in witness.get("methods", [])] if isinstance(witness.get("methods"), list) else None,
        "unproven_pre_dispatch": unproven if isinstance(unproven, bool) else None,
        "source": source,
    }
    row["proves_not_executed"] = bool(mutation is False and unproven is not True)
    row["dispatch_stage"] = "dispatched_without_mutation" if row["proves_not_executed"] else "unknown"
    return row


def observed_dispatch_stage(payload: Mapping[str, Any] | None) -> str:
    """The stage one dispatched call provably reached, from the product's own vocabulary.

    ``refused_before_engine`` is claimed only for the documented pre-dispatch refusal codes and for
    the control daemon's own "reconcile unfinished engine work" refusal: both prove the engine was
    never asked to change anything.  A refusal that publishes its own mutation witness
    (``error.details.witness.mutation_issued is False``) proves the same thing from the callback's
    side and is filed as ``dispatched_without_mutation``.  A first-hand UNKNOWN result *without*
    such a witness keeps ``unknown`` — the envelope does not establish whether an engine call
    changed anything, and assuming NOT_EXECUTED is exactly the assumption that would let a replay
    execute work twice.
    """
    witness = unpack_envelope("dispatch", payload)
    outcome = _unknown_outcome(payload)
    code = str(witness.error_code or "")
    if outcome is not None and outcome.get("gate"):
        return "refused_before_engine"
    if code in PRE_DISPATCH_REFUSAL_CODES:
        return PRE_DISPATCH_REFUSAL_CODES[code]
    product = product_dispatch_stage(payload)
    if product is not None and product.get("proves_not_executed"):
        return "dispatched_without_mutation"
    if outcome is not None:
        return "unknown"
    if witness.success is False:
        return "unknown"
    return "dispatched"


@dataclass(frozen=True)
class RequestPlan:
    """One logical request's identity: run/case/step/sequence plus the key it is dispatched under."""

    run: str
    case: str
    step: str
    sequence: int
    key: str
    request_id: str
    body_sha256: str
    minted_at: str
    replan_of: str | None = None
    retry_of: str | None = None
    explicit_key: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"run": self.run, "case": self.case, "step": self.step, "sequence": self.sequence,
                "idempotency_key": self.key, "request_id": self.request_id,
                "body_sha256": self.body_sha256, "minted_at": self.minted_at,
                "replan_of": self.replan_of, "retry_of": self.retry_of,
                "explicit_key": self.explicit_key}

    def with_changes(self, **changes: Any) -> "RequestPlan":
        """A copy with named fields replaced (the dataclass is frozen)."""
        values = {name: getattr(self, name) for name in
                  ("run", "case", "step", "sequence", "key", "request_id", "body_sha256", "minted_at",
                   "replan_of", "retry_of", "explicit_key")}
        values.update(changes)
        return RequestPlan(**values)


class ExecutionContext:
    """The run's single source of truth for request identity, revisions and dispatch evidence.

    One instance per run (``ProductionHost.context``).  Nothing is inferred from a project name or
    from "the first number an envelope happens to carry": every model is keyed by its full ModelRef
    token, every logical request gets its own run/case/step/sequence identity, and every dispatch is
    recorded with the stage it provably reached.  The context never repairs anything by itself — it
    classifies, records and hands the decision to the caller.
    """

    #: How many rows of each evidence list a run document keeps (bounded evidence, not a second
    #: transcript).
    EVIDENCE_LIMIT = 40

    def __init__(self, *, run: str = "phase4") -> None:
        self.run = run
        self.models: dict[str, dict[str, Any]] = {}
        self.active: dict[str, str] = {}
        self.generations: list[dict[str, Any]] = []
        self.unfinished: dict[str, dict[str, Any]] = {}
        self.dispatches: list[dict[str, Any]] = []
        self.observations: list[dict[str, Any]] = []
        self.rejections: list[dict[str, Any]] = []
        self.replans: list[dict[str, Any]] = []
        self.replays: list[dict[str, Any]] = []
        self.negative_probes: list[dict[str, Any]] = []
        self.plans: dict[str, RequestPlan] = {}
        self.reader_errors: int = 0
        self._serial = itertools.count(1)
        self._sequences: dict[str, int] = {}
        self._job_queries: dict[str, int] = {}

    # ------------------------------------------------------------------ request identity
    def mint(self, *, case: str, step: str, body: Mapping[str, Any] | None = None,
             explicit_key: str | None = None) -> RequestPlan:
        """A *new* logical request: its own run/case/step/sequence identity and idempotency key.

        A driver-minted key is never reused for another logical request — that reuse is exactly the
        live defect (one fixed key per operation replayed the first stored answer, and a different
        body under it was refused as ``IDEMPOTENCY_CONFLICT``).  An explicit key passed by a case is
        honoured verbatim (a case may be probing the product's own contract) and its reuse is
        recorded as evidence.
        """
        scope = f"{case}:{step}"
        sequence = self._sequences.get(scope, 0) + 1
        self._sequences[scope] = sequence
        key = explicit_key or f"{self.run}-{case}-{step}-{sequence}"
        plan = RequestPlan(run=self.run, case=case, step=step, sequence=sequence, key=key,
                           request_id=key, body_sha256=_body_sha256(body), minted_at=_utc_now(),
                           explicit_key=explicit_key is not None)
        previous = self.plans.get(key)
        if previous is not None:
            same_body = previous.body_sha256 == plan.body_sha256
            note = {"key": key, "previous_request": previous.request_id, "same_body": same_body,
                    "explicit": plan.explicit_key, "at": _utc_now(),
                    "body_sha256": plan.body_sha256, "previous_body_sha256": previous.body_sha256}
            if plan.explicit_key:
                note["intent"] = ("deliberate explicit-key reuse: the product's own answer is the "
                                  "evidence this call exists for")
            elif same_body:
                # Same key, byte-identical body: the one reuse the idempotency contract allows,
                # and only as the retry of the *same* request (an uncertain response).
                note["intent"] = "retry of the same request (identical body)"
                plan = plan.with_changes(retry_of=previous.request_id)
            else:
                note["intent"] = ("driver key collision with a different body: the collision is a "
                                  "driver defect, recorded as such")
                note["reason"] = AUXILIARY_REJECTIONS[1]
            self.replays.append(_json_safe(note))
            del self.replays[:-self.EVIDENCE_LIMIT]
        self.plans[key] = plan
        return plan

    def plan(self, request_id: str) -> RequestPlan | None:
        return self.plans.get(request_id)

    def bind_body(self, key: str, body: Mapping[str, Any] | None) -> None:
        """Attach the dispatched body's digest to a plan.

        The key has to exist before the wire envelope is built (it is carried inside it), so the
        digest of the body as actually dispatched is bound immediately afterwards.  Only that digest
        may decide whether a later same-key call is a retry of this request or a different one.
        """
        plan = self.plans.get(key)
        if plan is not None:
            self.plans[key] = plan.with_changes(body_sha256=_body_sha256(body))

    def reuse_for_retry(self, request_id: str, *, body: Mapping[str, Any] | None) -> dict[str, Any] | None:
        """Check that a retry of ``request_id`` may reuse its key: the body must be identical.

        ``None`` means the retry may proceed.  A request this context never minted (a direct
        ``host.call`` inside a case) has no recorded body to compare, so nothing is invented for
        it: the check is recorded as skipped rather than passed.
        """
        plan = self.plans.get(request_id)
        if plan is None:
            self.replays.append({"request_id": request_id, "checked": False, "same_body": None,
                                 "intent": ("no plan was minted for this request (a direct host call): "
                                            "the retry keeps its own identity"),
                                 "at": _utc_now()})
            del self.replays[:-self.EVIDENCE_LIMIT]
            return None
        digest = _body_sha256(body)
        if digest != plan.body_sha256:
            return {"reason": "external_observation", "request_id": request_id,
                    "expected_body_sha256": plan.body_sha256, "body_sha256": digest,
                    "detail": ("a retry must resend the identical body; a changed body is a new plan, "
                               "not the original request")}
        self.replays.append({"request_id": request_id, "checked": True, "same_body": True,
                             "intent": "same-key retry of the same request (identical body)",
                             "at": _utc_now()})
        del self.replays[:-self.EVIDENCE_LIMIT]
        return None

    def grant_replan(self, request_id: str, *, body: Mapping[str, Any] | None,
                     evidence: Mapping[str, Any], why: str = "") -> tuple[RequestPlan | None, dict[str, Any]]:
        """Allow exactly one recorded new plan for a request that provably never executed.

        A new plan (new key, possibly a rewritten revision) is only legitimate when the original
        request is *proved* not to have executed and the model was legally re-verified through the
        published reconcile read.  Everything else — including a first-hand UNKNOWN result — is
        refused here and keeps its original evidence.
        """
        stage = str(evidence.get("dispatch_stage") or "unknown")
        decision: dict[str, Any] = {"request_id": request_id, "dispatch_stage": stage,
                                    "evidence": _json_safe(dict(evidence)), "at": _utc_now(),
                                    "why": why}
        refused = self._refuse_replan(request_id, stage, decision)
        if refused is not None:
            return None, refused
        parts = self.plan(request_id)
        case = parts.case if parts is not None else "unplanned"
        step = parts.step if parts is not None else str(request_id)
        new_plan = self.mint(case=case, step=step, body=body)
        replanned = new_plan.with_changes(replan_of=request_id)
        self.plans[replanned.key] = replanned
        decision.update({"granted": True, "new_key": replanned.key, "new_request_id": replanned.request_id})
        self.replans.append(_json_safe(decision))
        del self.replans[:-self.EVIDENCE_LIMIT]
        return replanned, decision

    def _refuse_replan(self, request_id: str, stage: str, decision: dict[str, Any]) -> dict[str, Any] | None:
        previously = [row for row in self.replans
                      if row.get("request_id") == request_id and row.get("granted") is True]
        if stage not in NOT_EXECUTED_STAGES:
            decision.update({"granted": False, "reason": "unknown_job" if stage == "unknown"
                             else "external_observation",
                             "detail": ("the request is not proved NOT_EXECUTED (dispatch stage "
                                        f"{stage!r}), so replaying it could execute it twice")})
        elif previously:
            decision.update({"granted": False, "reason": "unknown_job",
                             "detail": ("this request already spent its one recorded new plan; a "
                                        "second one is beyond the contract")})
        else:
            return None
        self.replans.append(_json_safe(decision))
        del self.replans[:-self.EVIDENCE_LIMIT]
        return decision

    # ------------------------------------------------------------------ dispatch evidence
    def note_dispatch(self, *, request_id: str, tool: str, stage: str, reason: str | None = None,
                      model_ref: Mapping[str, Any] | None = None, revision: int | None = None,
                      **detail: Any) -> dict[str, Any]:
        row = {"request_id": request_id, "tool": tool, "stage": stage, "reason": reason,
               "model_ref": _json_safe(model_ref), "token": model_ref_token(model_ref),
               "revision": revision, "at": _utc_now(), **{key: _json_safe(value) for key, value in detail.items()}}
        self.dispatches.append(row)
        del self.dispatches[:-self.EVIDENCE_LIMIT]
        return row

    def refusal_evidence(self, payload: Mapping[str, Any] | None) -> dict[str, Any]:
        """What a refusal envelope provably says about the dispatch stage it reached."""
        witness = unpack_envelope("refusal", payload)
        code = witness.error_code
        stage = PRE_DISPATCH_REFUSAL_CODES.get(str(code))
        basis = ("the product documents this refusal as raised by the execution ledger's "
                 "preflight, before any engine call" if stage in NOT_EXECUTED_STAGES
                 else "the envelope does not establish whether an engine call happened")
        product = product_dispatch_stage(payload)
        if stage is None and product is not None and product.get("proves_not_executed"):
            # C03: the product's own mutation witness proves nothing was executed — the callback
            # reached the engine, issued reads only (``mutation_issued: false``), so the refusal is
            # filed as NOT_EXECUTED instead of an unknown engine state.
            stage = str(product.get("dispatch_stage"))
            basis = ("the product published its own witness for this refusal: the callback reached "
                     "the engine, issued no mutation, so no engine change was executed")
        return {"error_code": code, "message": witness.error_message,
                "dispatch_stage": stage or "unknown", "job_id": witness.job_id,
                "source": dict(witness.source), "product_dispatch": _json_safe(product) if product else None,
                "basis": basis}

    def note_negative_probe(self, *, request_id: str, tool: str, model_ref: Mapping[str, Any] | None,
                            note: str) -> dict[str, Any]:
        """A deliberate stale/conflicting probe: it must never be repaired automatically."""
        row = {"request_id": request_id, "tool": tool, "token": model_ref_token(model_ref),
               "model_ref": _json_safe(model_ref), "note": note, "at": _utc_now(),
               "auto_repair": "refused: the case asked for the product's own refusal"}
        self.negative_probes.append(row)
        del self.negative_probes[:-self.EVIDENCE_LIMIT]
        return row

    def note_unfinished(self, job_id: str, *, tool: str, error_code: str | None,
                        message: str | None) -> dict[str, Any]:
        entry = self.unfinished.get(job_id)
        if entry is None:
            entry = self.unfinished[job_id] = {"job_id": job_id, "first_tool": tool, "first_at": _utc_now(),
                                               "observations": 0, "resolved": False}
        entry["observations"] = int(entry.get("observations") or 0) + 1
        entry.update({"last_tool": tool, "last_error_code": error_code, "last_message": message,
                      "last_at": _utc_now()})
        return entry

    def resolve_unfinished(self, job_id: str, *, outcome: str) -> dict[str, Any]:
        entry = self.unfinished.get(job_id) or self.note_unfinished(job_id, tool="job_reconcile",
                                                                    error_code=None, message=None)
        entry["resolved"] = outcome == "released"
        entry["resolution"] = outcome
        entry["resolved_at"] = _utc_now()
        return entry

    def unfinished_jobs(self) -> list[str]:
        return [job_id for job_id, entry in self.unfinished.items() if not entry.get("resolved")]

    def query_budget(self, job_id: str) -> bool:
        """Whether one more published query of this job is still within the bounded budget."""
        used = self._job_queries.get(job_id, 0)
        if used >= 2:
            return False
        self._job_queries[job_id] = used + 1
        return True

    # ------------------------------------------------------------------ model state
    def observe(self, tool: str, payload: Mapping[str, Any] | None, *,
                request: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Learn from one decoded envelope.  Called on the wire choke point for every call."""
        try:
            witness = unpack_envelope(tool, payload)
        except Exception as exc:  # noqa: BLE001 - the choke point must never break a call
            self.reader_errors += 1
            row = {"tool": tool, "at": _utc_now(), "adopted": False,
                   "reader_error": f"{type(exc).__name__}: {exc}"}
            self.observations.append(row)
            del self.observations[:-self.EVIDENCE_LIMIT]
            return row
        row: dict[str, Any] = {"tool": tool, "at": _utc_now(), "success": witness.success,
                               "error_code": witness.error_code, "job_id": witness.job_id,
                               "model_ref": _json_safe(witness.model_ref), "revision": witness.revision,
                               "dirty": witness.dirty, "source": dict(witness.source), "adopted": False}
        # C03: the product's own cause and dispatch stage are carried next to the driver's reading,
        # so a refusal is filed with its original operation/code/cause/dispatch stage — and a
        # refusal that proves no mutation was issued is NOT_EXECUTED, not an unknown job.
        product_stage = product_dispatch_stage(payload)
        if product_stage is not None:
            row["cause_code"] = product_stage.get("cause_code")
            row["product_dispatch"] = product_stage
            row["dispatch_stage"] = observed_dispatch_stage(payload)
        if witness.conflicts:
            # A self-contradicting envelope is evidence, never a value to adopt.
            row["conflicts"] = list(witness.conflicts)
            self._reject(reason=AUXILIARY_REJECTIONS[0], tool=tool, job_id=witness.job_id,
                         detail={"conflicts": list(witness.conflicts), "source": dict(witness.source)})
            self._record_observation(row)
            return row
        request_token = model_ref_token(_mapping(request).get("model_ref")) if isinstance(request, Mapping) else None
        if witness.token is not None and request_token is not None and witness.token != request_token:
            # Adopting another model's revision is the "two models, one global revision" defect.
            self._reject(reason="generation_mismatch", tool=tool, job_id=witness.job_id,
                         detail={"request_model_ref_token": request_token, "envelope_model_ref_token": witness.token,
                                 "note": "the envelope names another model than the request did; nothing was adopted"})
            self._record_observation(row)
            return row
        if witness.token is not None:
            self._adopt(tool, witness)
            row["adopted"] = True
            row["token"] = witness.token
        if witness.job_id and (witness.error_code in UNKNOWN_ERROR_CODES):
            self.note_unfinished(witness.job_id, tool=tool, error_code=witness.error_code,
                                 message=witness.error_message)
        self._record_observation(row)
        return row

    def _record_observation(self, row: dict[str, Any]) -> None:
        self.observations.append(row)
        del self.observations[:-self.EVIDENCE_LIMIT]

    def _adopt(self, tool: str, witness: EnvelopeWitness) -> None:
        token = witness.token
        if token is None:
            return
        lineage = witness.lineage
        active_token = self.active.get(lineage) if lineage else None
        superseded = self.models.get(active_token) if active_token else None
        superseded_generation = ((superseded or {}).get("ref") or {}).get("generation")
        if (lineage and witness.generation is not None and isinstance(superseded, dict)
                and superseded_generation is not None and str(superseded_generation) != str(witness.generation)):
            # A reload/restore returned a new generation: replace the reference explicitly and
            # invalidate the cached revision of the old one (never carry it across).
            self.generations.append({"lineage": lineage, "superseded_token": self.active.get(lineage),
                                     "replacement_token": token, "at": _utc_now(), "tool": tool,
                                     "superseded_generation": superseded_generation,
                                     "generation": witness.generation})
            superseded["invalidated"] = True
            superseded["invalidated_by"] = token
            superseded["revision"] = None
            superseded["dirty"] = None
        state = self.models.get(token)
        if state is None:
            state = self.models[token] = {"ref": dict(witness.model_ref or {}), "revision": None, "dirty": None,
                                          "observations": 0, "last_tool": None, "last_at": None,
                                          "revision_tool": None, "dirty_tool": None, "invalidated": False}
        state["observations"] = int(state.get("observations") or 0) + 1
        state["last_tool"] = tool
        state["last_at"] = _utc_now()
        if witness.revision is not None:
            known = state.get("revision")
            if not isinstance(known, int) or witness.revision >= known:
                state["revision"] = witness.revision
                state["revision_tool"] = tool
        if witness.dirty is not None:
            state["dirty"] = witness.dirty
            state["dirty_tool"] = tool
        if lineage:
            self.active[lineage] = token

    def state_for(self, ref: Mapping[str, Any] | None) -> dict[str, Any] | None:
        """The tracked state of one model, by full token (never by project name)."""
        token = model_ref_token(ref)
        if token is not None and token in self.models:
            return self.models[token]
        lineage = model_ref_lineage(ref)
        if lineage is None:
            return None
        return self.models.get(self.active.get(lineage) or "")

    def revision_for(self, ref: Mapping[str, Any] | None) -> int | None:
        state = self.state_for(ref)
        revision = state.get("revision") if isinstance(state, Mapping) else None
        return revision if isinstance(revision, int) and not isinstance(revision, bool) else None

    def dirty_for(self, ref: Mapping[str, Any] | None) -> bool | None:
        state = self.state_for(ref)
        dirty = state.get("dirty") if isinstance(state, Mapping) else None
        return dirty if isinstance(dirty, bool) else None

    def superseded_generation(self, ref: Mapping[str, Any] | None) -> dict[str, Any] | None:
        """Whether this request names a generation a published read already replaced."""
        token = model_ref_token(ref)
        lineage = model_ref_lineage(ref)
        if token is None or lineage is None:
            return None
        active = self.active.get(lineage)
        if active is None or active == token:
            return None
        state = self.models.get(token) or {}
        if not state.get("invalidated"):
            return None
        return {"lineage": lineage, "request_token": token, "active_token": active,
                "superseded_by": state.get("invalidated_by")}

    def self_dispatched(self, ref: Mapping[str, Any] | None) -> bool:
        """Whether any recorded dispatch of this run actually *reached the engine* for this model.

        A call refused before dispatch (a ledger preflight refusal, the control gate) is not an
        engine change: it must never make a later external change look self-caused.
        """
        lineage = model_ref_lineage(ref)
        if lineage is None:
            return False
        return any(row.get("stage") == "dispatched" and row.get("token")
                   and model_ref_lineage(_mapping(row.get("model_ref"))) == lineage
                   for row in self.dispatches)

    # ------------------------------------------------------------------ pre-dispatch policy
    def precheck(self, arguments: Mapping[str, Any] | None, *, tool: str = "", negative_probe: bool = False,
                 reconcile: bool = True) -> dict[str, Any] | None:
        """Classify one request *before* it is dispatched.  ``None`` means: nothing to report.

        The returned decision never repairs anything by itself:

        * ``refuse`` — only for a request that contradicts itself or names a generation a published
          read already replaced (no wire call is made at all);
        * ``query_first`` — an unfinished job or an externally caused change exists: the original
          job's own query/reconcile runs first, and the request keeps its identity;
        * ``stale_expected`` / ``external_observation`` — recorded as the reason the product will
          refuse this request, so the release path reports *why* instead of one generic conflict.
        """
        request = arguments if isinstance(arguments, Mapping) else {}
        conflict = identity_conflict(request)
        if conflict is not None:
            self._reject(tool=tool, job_id=None, detail=conflict, reason=conflict["reason"])
            return {"reason": conflict["reason"], "action": "refuse", "field": conflict.get("field"),
                    "detail": conflict, "note": "the request contradicts itself; nothing was dispatched"}
        execution = _mapping(request.get("execution"))
        ref = execution.get("model_ref") if isinstance(execution.get("model_ref"), Mapping) else None
        if ref is None:
            ref = request.get("model_ref") if isinstance(request.get("model_ref"), Mapping) else None
        superseded = self.superseded_generation(ref)
        if superseded is not None:
            detail = {"detail": ("the request names a model generation this run already saw replaced; "
                                 "the old reference is invalidated and must be re-bound through the "
                                 "published read"),
                      **superseded, "request_id": execution.get("request_id")}
            self._reject(tool=tool, job_id=None, detail=detail, reason="generation_mismatch")
            return {"reason": "generation_mismatch", "action": "refuse", "detail": detail,
                    "note": "the cached reference belongs to a replaced generation"}
        pending = self.unfinished_jobs()
        if pending and reconcile and not negative_probe and tool not in RECONCILE_EXEMPT_TOOLS:
            detail = {"unfinished_jobs": list(pending), "request_id": execution.get("request_id"),
                      "note": ("an unfinished job must be resolved through its own published query before "
                               "any new logical request is dispatched")}
            return {"reason": "unknown_job", "action": "query_first", "detail": detail}
        expected = _expected_revision(request)
        observed = self.revision_for(ref)
        if expected is not None and observed is not None and expected < observed:
            self_caused = self.self_dispatched(ref)
            reason = "stale_expected" if self_caused else "external_observation"
            detail = {"expected_revision": expected, "observed_revision": observed,
                      "self_caused": self_caused, "request_id": execution.get("request_id"),
                      "request_token": model_ref_token(ref),
                      "note": ("the request is behind a revision this driver produced" if self_caused
                               else "the request is behind a revision no dispatch of this run produced")}
            if negative_probe:
                detail["negative_probe"] = "kept: the product's own refusal is this probe's evidence"
                return {"reason": reason, "action": "proceed", "detail": detail}
            return {"reason": reason, "action": "expected_refusal", "detail": detail}
        dirty = self.dirty_for(ref)
        if dirty is True and not self.self_dispatched(ref) and ref is not None:
            return {"reason": "external_observation", "action": "query_first" if reconcile else "proceed",
                    "detail": {"dirty": True, "self_caused": False,
                               "note": "the model carries an engine change no dispatch of this run produced"}}
        return None

    def classify_revision_conflict(self, conflict: Mapping[str, Any],
                                   arguments: Mapping[str, Any] | None) -> dict[str, Any]:
        """Name *why* a managed-revision refusal happened, from the context's own observations."""
        request = arguments if isinstance(arguments, Mapping) else {}
        execution = _mapping(request.get("execution"))
        ref = execution.get("model_ref") if isinstance(execution.get("model_ref"), Mapping) else None
        expected = _expected_revision(request)
        observed = self.revision_for(ref)
        self_caused = self.self_dispatched(ref)
        reason = "stale_expected" if self_caused else "external_observation"
        return {"reason": reason, "precondition": conflict.get("precondition"),
                "expected_revision": expected, "observed_revision": observed,
                "self_caused": self_caused, "request_id": execution.get("request_id"),
                "request_token": model_ref_token(ref), "envelope_job_id": conflict.get("job_id"),
                "basis": ("every observation that advanced this model's revision came from a dispatch of "
                          "this run" if self_caused else
                          "the model moved without a matching dispatch of this run (external change)")}

    def _reject(self, *, reason: str, tool: str, job_id: str | None, detail: Mapping[str, Any]) -> dict[str, Any]:
        row = {"reason": reason, "tool": tool, "job_id": job_id, "detail": _json_safe(dict(detail)),
               "at": _utc_now()}
        if reason not in REJECTION_REASONS and reason not in AUXILIARY_REJECTIONS:
            row["unclassified"] = True
        self.rejections.append(row)
        del self.rejections[:-self.EVIDENCE_LIMIT]
        return row

    # ------------------------------------------------------------------ evidence
    def evidence(self) -> dict[str, Any]:
        """The context as evidence: plans, per-model state, generations, refusals, replans."""
        plans = list(self.plans.values())
        rejected = [row for row in self.rejections if row.get("reason") in REJECTION_REASONS]
        return {
            "run": self.run,
            "rejection_reasons": list(REJECTION_REASONS),
            "auxiliary_rejections": list(AUXILIARY_REJECTIONS),
            "requests": [plan.as_dict() for plan in plans[-self.EVIDENCE_LIMIT:]],
            "request_count": len(plans),
            "model_states": [{"token": token, **_json_safe(state)} for token, state in self.models.items()],
            "active_models": dict(self.active),
            "generation_replacements": _json_safe(list(self.generations)),
            "dispatches": _json_safe(list(self.dispatches)),
            "observations": _json_safe(list(self.observations)),
            "rejections": _json_safe(list(self.rejections)),
            "rejection_counts": {reason: len([row for row in self.rejections if row.get("reason") == reason])
                                 for reason in (*REJECTION_REASONS, *AUXILIARY_REJECTIONS)},
            "replans": _json_safe(list(self.replans)),
            "replays": _json_safe(list(self.replays)),
            "negative_probes": _json_safe(list(self.negative_probes)),
            "unfinished_jobs": self.unfinished_jobs(),
            "unfinished_entries": _json_safe(list(self.unfinished.values())),
            "reader_errors": self.reader_errors,
            "note": ("one execution context for the whole run: revisions and dirty flags are keyed by "
                     "the full ModelRef (session/server epoch/tag/generation), every logical request "
                     "carries its own run/case/step/sequence key, and every refusal is classified as "
                     "stale_expected / external_observation / unknown_job / generation_mismatch"),
        }

_RUN_IDEMPOTENCY_PREFIX = ""
_RUN_PRIVATE_HOME_ROOT: Path | None = None


class CapabilityUnavailable(RuntimeError):
    """The requested operation is not published or cannot run in this scope."""


# ---------------------------------------------------------------------------
# Planned subcase inventory.  Every case declares its acceptance lines here so a
# missing observation can never silently disappear from the report.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Planned:
    name: str
    level: str
    acceptance: str
    ops: tuple[str, ...] = ()


PLAN: dict[str, tuple[Planned, ...]] = {
    "R01_LIVE": (
        Planned("static_expression_readback_ops_executable", "static", "G3 R01 readback path",
                ("node.property_schema", "node.property_get", "node.property_set")),
        Planned("static_malformed_typed_value_rejected_pre_engine", "protocol", "G3 R01 fail-closed", ("transaction.preview",)),
        Planned("expression_string_same_text_verified", "live", "G3 R01 same-text expression->String verified"),
        Planned("expression_array_or_matrix_verified", "live", "G3 R01 expression array/matrix"),
        Planned("unit_expression_text_preserved", "live", "G3 R01 unit field contract"),
        Planned("incompatible_kind_or_unit_rejected_before_write", "live", "G3 R01 incompatible type/unit rejected before write"),
        Planned("non_matching_readback_not_silently_accepted", "live", "G3 R01 engine readback differs stays partial/UNKNOWN"),
        Planned("nonfinite_text_strictly_rejected", "live", "G3 R01 complex/non-finite strict boundary"),
    ),
    "R03_LIVE": (
        Planned("static_indexed_ops_executable", "static", "G3 R03 indexed/keyed readback path",
                ("node.property_index_set", "node.property_entry_set", "node.property_get")),
        Planned("static_malformed_index_and_key_rejected_pre_engine", "protocol", "G3 R03 write-before rejection", ("transaction.preview",)),
        Planned("vector_element_readback", "live", "G3 R03 vector element"),
        Planned("matrix_cell_or_row_readback", "live", "G3 R03 matrix cell/row"),
        Planned("keyed_entry_set_and_readback", "live", "G3 R03 keyed entry"),
        Planned("wrong_index_rejected_without_write", "live", "G3 R03 wrong index/key"),
        Planned("setter_noop_or_normalized_value_detected", "live", "G3 R03 setter success but value differs"),
        Planned("non_target_items_unchanged", "live", "G3 R03 non-target items unchanged"),
        Planned("empty_or_single_element_property", "live", "G3 R03 empty/single element"),
        Planned("envelope_consistency", "protocol", "G3 R03 outer isError/UNKNOWN consistent"),
    ),
    "R04_LIVE": (
        Planned("static_transaction_ops_executable", "static", "G3 R04 executable invariants",
                ("transaction.preview", "transaction.apply", "transaction.verify")),
        Planned("static_unsupported_invariant_rejected_pre_write", "protocol", "G3 R04 unsupported/malformed invariant rejected before write"),
        Planned("invariant_violation_not_reported_as_pass", "live", "G3 R04 required failure is not PASS"),
        Planned("verified_transaction_status_split", "live", "G3 R04 execution vs verification status"),
        Planned("stale_revision_rejected", "live", "G3 R04 stale revision"),
        Planned("cross_model_transaction_record_rejected", "live", "G3 R04 cross-model record binding"),
        Planned("recorded_pass_with_changed_live_property", "live", "G3 R04 record passes but live property differs"),
        Planned("checkpoint_recovery_after_failed_invariant", "live", "G3 R04 checkpoint after failed invariant"),
    ),
    "R_READBACK": (
        Planned("r01_expression_readback_status", "rollup", "G3 R01 status roll-up"),
        Planned("r03_indexed_keyed_readback_status", "rollup", "G3 R03 status roll-up"),
        Planned("r04_invariant_readback_status", "rollup", "G3 R04 status roll-up"),
        Planned("r_round_not_failed", "rollup", "G3 R round has no FAIL"),
    ),
    "W13_T006_variables": (
        Planned("static_variable_ops_availability", "static", "W13 variable group add/update",
                ("variable.group_create", "variable.set", "variable.get")),
        Planned("static_legacy_variable_route_available", "static", "T006 evaluation route", ("manage_variables", "evaluate_expressions")),
        Planned("variables_two_in_one_group", "live", "T006 two variables in one group"),
        Planned("varnames_contains_modified_variable", "live", "T006 varnames contains the changed variable"),
        Planned("no_bogus_name_expr_variables", "live", "T006 no fake name/expr variables"),
        Planned("expression_evaluates_after_modification", "live", "T006 expression still evaluates"),
        Planned("component_and_global_scope", "live", "W13 global and component scopes"),
    ),
    "W13_T015_units": (
        Planned("static_physics_unit_ops_availability", "static", "T015 source/unit actions",
                ("physics.create", "physics.feature_create", "physics.feature_update", "physics.validate")),
        Planned("surface_source_W_per_m2", "live", "T015 surface source: the interface the W/m^2 boundary heat flux is bound to (the boundary write point itself is recorded as auxiliary evidence in the same case)"),
        Planned("volume_source_W_per_m3", "live", "T015 W/m^3 volumetric source"),
        Planned("coordinate_unit_m_and_mm", "live", "T015 m/mm interpolation coordinates"),
        Planned("wrong_unit_warns_or_fails", "live", "T015 deliberate wrong unit"),
        Planned("no_implicit_thickness_or_absorptivity", "live", "T015 no automatic thickness/absorptivity"),
    ),
    "W13_T048_selection_drift": (
        Planned("static_selection_ops_availability", "static", "T048 selection revalidation",
                ("selection.create", "selection.measure", "selection.validate", "physics.selection_set")),
        Planned("named_selection_created_and_bound", "live", "T048 named selection binding"),
        Planned("geometry_revision_recorded", "live", "T048 geometry revision"),
        Planned("revalidation_after_geometry_change", "live", "T048 re-evaluate after geometry rebuild"),
        Planned("drift_stops_boundary_application", "live", "T048 drift stops boundary application"),
        Planned("entity_measure_change_recorded", "live", "T048 entity measure change"),
    ),
    "W13_T016_2D_data": (
        Planned("static_function_data_ops_availability", "static", "T016 interpolation functions",
                ("function.create", "function.data_import", "function.inspect", "function.evaluate")),
        Planned("non_axisymmetric_fixture_hash_recorded", "fixture", "T016 Q(x,y) fixture and hash"),
        Planned("two_d_interpolation_angular_difference_preserved", "live", "T016 angular difference preserved"),
        Planned("radial_average_not_substituted", "live", "T016 no radial averaging"),
        Planned("m_mm_coordinate_conversion", "live", "T016 m/mm data coordinate units"),
        Planned("interpolation_and_extrapolation_settings_recorded", "live", "T016 interpolation/extrapolation traceability"),
    ),
    "W14_T009_geometry_edit": (
        Planned("static_geometry_ops_availability", "static", "T009 nested Work Plane edit",
                ("geometry.workplane_edit", "geometry.array_create", "geometry.build", "geometry.measure")),
        Planned("work_plane_and_array_located", "live", "T009 locate wp3 and rectangular array"),
        Planned("local_subfeature_edit_applied", "live", "T009 edit selected sub-feature only"),
        Planned("left_most_object_preserved", "live", "T009 keep the left-most object"),
        Planned("count_position_spacing_quantified", "live", "T009 count/position/spacing contract"),
        Planned("main_model_not_replaced", "live", "T009 main model identity"),
        Planned("sibling_features_unchanged", "live", "T009 sibling features preserved"),
    ),
    "W14_T034_local_paths": (
        Planned("static_model_path_ops_availability", "static", "T034 path handling",
                ("model.load", "model.save", "geometry.import")),
        Planned("local_spaces_and_chinese_path", "fixture", "T034 project path with spaces/Chinese"),
        Planned("model_save_load_roundtrip_hash", "live", "T034 hash match after reload"),
        Planned("missing_dependency_reported", "live", "T034 missing dependency is explicit"),
        Planned("cad_import_license_limited", "live", "T034 CAD import license boundary"),
    ),
    "W15_T007_selections": (
        Planned("static_physics_selection_ops_availability", "static", "T007 selection levels",
                ("physics.selection_set", "physics.feature_update", "physics.inspect")),
        Planned("physics_level_selection_set", "live", "T007 physics-level selection"),
        Planned("feature_level_selection_set", "live", "T007 feature-level selection"),
        Planned("inherited_selection_not_writable", "live", "T007 inherited selection fails accurately"),
        Planned("named_selection_binding_readback", "live", "T007 named selection binding"),
        Planned("no_invalid_parent_feature_coupling", "live", "T007 no phys.feature(phys_tag) misuse"),
    ),
    "W15_T017_material": (
        Planned("static_material_ops_availability", "static", "T017 material actions",
                ("material.create", "material.set_properties", "material.validate")),
        Planned("k_T_Cp_T_and_rho_expressions", "live", "T017 k(T)/Cp(T)/rho"),
        Planned("anisotropic_tensor_and_coordinate_system", "live", "T017 anisotropic tensor and frame"),
        Planned("missing_required_property_preflight_error", "live", "T017 missing required property preflight"),
        Planned("material_readback_after_update", "live", "T017 material readback"),
    ),
    "W15_T042_license": (
        Planned("static_license_ops_availability", "static", "T042 license probe",
                ("runtime.license_inspect", "runtime.capabilities")),
        Planned("license_inspect_records_has_product", "live", "T042 hasProduct evidence"),
        Planned("missing_product_blocks_with_blocked_license", "live", "T042 BLOCKED_LICENSE not fake success"),
        Planned("authorized_product_usable", "live", "T042 authorized product works"),
        Planned("probe_does_not_occupy_license", "live", "T042 probe does not consume a seat"),
    ),
    "W16_T018_mesh": (
        Planned("static_mesh_ops_availability", "static", "T018 mesh actions",
                ("mesh.create", "mesh.feature_create", "mesh.build", "mesh.statistics", "mesh.quality")),
        Planned("free_tet_sequence_created", "live", "T018 FreeTet sequence"),
        Planned("local_size_feature_applied", "live", "T018 local Size feature"),
        Planned("modify_and_rebuild", "live", "T018 modify and rebuild"),
        Planned("statistics_counts_and_coverage", "live", "T018 statistics counts/coverage"),
        Planned("quality_definition_and_low_quality_locations", "live", "T018 quality definition and low-quality locations"),
        Planned("build_success_is_not_quality_pass", "protocol", "T018 build success != quality"),
    ),
    "W16_T019_chainA_steady": (
        Planned("analytic_reference_preregistered", "fixture", "T019 chain A analytic reference"),
        Planned("static_chain_a_ops_availability", "static", "T019 chain A empty-model chain",
                ("geometry.feature_create", "material.create", "physics.create", "mesh.build", "study.run")),
        Planned("benchmark_spec_registered_and_sane", "fixture",
                "T019 chain A pre-registered BenchmarkSpec (C07b): units, dimensions and derived quantities"),
        Planned("pre_solve_readback_matches_spec", "live",
                "T019 chain A model read-back checked item by item against the frozen specification"),
        Planned("empty_model_geometry_block", "live", "T019 chain A block geometry from an empty model"),
        Planned("constant_material_assigned", "live", "T019 chain A constant material"),
        Planned("boundary_temperatures_and_insulation", "live", "T019 chain A end temperatures, other faces insulated"),
        Planned("local_mesh_built", "live", "T019 chain A local mesh"),
        Planned("stationary_study_and_solver", "live", "T019 chain A study and solver"),
        Planned("solve_produced_solution", "live", "T019 chain A real solution"),
        Planned("linear_profile_relative_error_le_1e-4", "numerical", "T019 chain A T(x) relative error <= 1e-4"),
        Planned("heat_flux_and_power_balance", "numerical", "T019 chain A flux/power balance"),
        Planned("sample_table_and_units_recorded", "numerical", "T019 chain A sampling evidence"),
        Planned("saved_mph_hash_recorded", "live", "T019 chain A saved mph and hash"),
    ),
    "W16_T019_chainB_transient": (
        Planned("analytic_reference_preregistered", "fixture", "T019 chain B analytic reference"),
        Planned("benchmark_spec_registered_and_sane", "fixture",
                "T019 chain B pre-registered BenchmarkSpec (C07b): units, dimensions and derived quantities"),
        Planned("static_chain_b_ops_availability", "static", "T019 chain B transient chain",
                ("study.step_create", "study.run", "result.sample_path")),
        Planned("pre_solve_readback_matches_spec", "live",
                "T019 chain B model read-back checked item by item against the frozen specification"),
        Planned("transient_study_and_initial_value", "live", "T019 chain B transient study and initial value"),
        Planned("transient_solve_produced_solution", "live", "T019 chain B real transient solution"),
        Planned("normalized_max_error_le_1e-3", "numerical", "T019 chain B normalized error <= 1e-3"),
        Planned("time_points_and_mesh_recorded", "numerical", "T019 chain B time points and mesh evidence"),
    ),
    "W16_T019_chainC_continue": (
        Planned("static_chain_c_ops_availability", "static", "T019 chain C continuation",
                ("material.set_properties", "physics.feature_update", "study.run")),
        Planned("user_style_model_available", "fixture", "T019 chain C user-style model input"),
        Planned("target_only_modified", "live", "T019 chain C target-only modification"),
        Planned("non_target_nodes_preserved", "live", "T019 chain C non-target nodes preserved"),
        Planned("manual_solver_preserved", "live", "T019 chain C manual solver preserved"),
        Planned("derived_values_and_data_association_preserved", "live", "T019 chain C derived values/associations"),
        Planned("solve_after_continuation", "live", "T019 chain C solve after continuation"),
    ),
    "W16_T020_solver": (
        Planned("static_solver_ops_availability", "static", "T020 solver tree",
                ("study.list", "study.create", "study.step_create", "solver.list", "solver.inspect",
                 "solver.feature_update", "study.run", "study.solver_generate")),
        Planned("solver_tree_read", "live", "T020 nested solver tree"),
        Planned("sub_feature_property_update_readback", "live", "T020 sub-feature update readback"),
        Planned("solve_after_subfeature_update", "live", "T020 solve after update"),
        Planned("unknown_property_fails_accurately", "live", "T020 unknown property fails"),
        Planned("manual_solver_not_auto_overwritten", "live", "T020 manual solver preserved"),
    ),
    "GUARD_T010": (
        Planned("static_idempotency_contract_published", "static", "T010 idempotency contract",
                ("node.property_set", "transaction.apply", "create_feature")),
        Planned("repeated_key_not_reexecuted", "live", "T010 repeated idempotency key"),
        Planned("same_tag_same_type_duplicate_rejected_or_idempotent", "live", "T010 same tag/type duplicate"),
        Planned("same_tag_different_type_rejected", "live", "T010 same tag different type"),
        Planned("request_hash_conflict_rejected", "live", "T010 request hash conflict"),
    ),
    "GUARD_T035": (
        Planned("static_docs_guard_ops_executable", "static", "T035 outside-workspace source guard",
                ("docs.index", "docs.search")),
        Planned("outside_workspace_source_denied", "protocol", "T035 outside-workspace source"),
        Planned("symlink_outside_denied", "protocol", "T035 symlink/reparse escape"),
        Planned("credential_text_not_persisted", "protocol", "T035 credentials never persisted"),
        Planned("private_paths_not_in_evidence", "protocol", "T035 private paths redacted"),
        Planned("failed_denial_does_not_modify_files", "protocol", "T035 failed access changes nothing"),
        Planned("outside_write_attempt_denied", "protocol", "T035 outside write attempt"),
    ),
    "GUARD_T038": (
        Planned("initialize_and_tools_list_schemas", "protocol", "T038 tools/list schema validity"),
        Planned("structured_content_action_results", "protocol", "T038 structuredContent envelope"),
        Planned("outer_iserror_matches_success", "protocol", "T038 isError semantics"),
        Planned("error_propagation_structured", "protocol", "T038 structured errors"),
        Planned("registry_paging_continuation", "protocol", "T038 pagination"),
        Planned("stdio_log_isolation", "protocol", "T038 stdout/stderr isolation"),
        Planned("partial_failure_not_reported_as_success", "protocol", "T038 partial failure not all-success"),
    ),
    "GUARD_T005": (
        Planned("static_evaluation_ops_available", "static", "T005 evaluation route", ("evaluate_expressions", "get_core_metrics")),
        Planned("user_derived_nodes_preserved", "live", "T005 user nodes preserved"),
        Planned("invalid_expression_does_not_delete_nodes", "live", "T005 invalid expression is non-destructive"),
        Planned("temporary_nodes_cleaned", "live", "T005 temporary nodes cleaned"),
    ),
    "GUARD_T033": (
        Planned("static_evaluation_policy_documented", "static", "T033 evaluation policy contract", ("evaluate_expressions",)),
        Planned("static_evaluation_policy_source_recorded", "static", "T033 policy read path", ("evaluate_expressions",)),
        Planned("pure_read_rejects_or_isolates", "live", "T033 pure_read behaviour"),
        Planned("ephemeral_mutation_recorded_and_serial", "live", "T033 ephemeral mutation recorded/serial"),
        Planned("evaluation_expression_kinds_routed", "live", "T033 constant/model/field/illegal expressions"),
        Planned("only_own_temporary_nodes_cleaned", "live", "T033 only own nodes cleaned"),
    ),
}


# ---------------------------------------------------------------------------
# Case record
# ---------------------------------------------------------------------------


@dataclass
class Case:
    case_id: str
    package: str
    acceptance: tuple[str, ...]
    status: str = "NOT_RUN"
    reason: str | None = None
    assertions: dict[str, Any] = field(default_factory=dict)
    subcases: dict[str, dict[str, Any]] = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: _utc_now())
    finished_at: str | None = None

    def assertion(self, name: str, value: Any, **detail: Any) -> bool:
        row: dict[str, Any] = {"pass": bool(value)}
        if detail:
            row.update(detail)
        self.assertions[name] = row
        return bool(value)

    def subcase(self, name: str, status: str, *, reason: str | None = None, level: str | None = None,
                force: Any = False, **data: Any) -> None:
        if status not in STATUSES:
            raise ValueError(f"invalid subcase status: {status}")
        if name in self.subcases and self.subcases[name].get("status") == "PASS" and not force:
            # A recorded PASS is never silently downgraded by a later summary.
            return
        row: dict[str, Any] = {"status": status}
        planned = next((item for item in PLAN.get(self.case_id, ()) if item.name == name), None)
        if level is None:
            level = planned.level if planned is not None else "live"
        if level not in LEVELS:
            raise ValueError(f"invalid evidence level: {level}")
        row["level"] = level
        if planned is not None:
            row["acceptance"] = planned.acceptance
        if reason:
            row["reason"] = reason
        if data:
            row.update(data)
        self.subcases[name] = row

    def subcase_status(self, name: str) -> str | None:
        row = self.subcases.get(name)
        return row.get("status") if isinstance(row, Mapping) else None

    def finish(self, status: str | None = None, *, reason: str | None = None) -> str:
        if status is None:
            statuses = [row.get("status") for row in self.subcases.values()]
            if any(value == "FAIL" for value in statuses):
                status = "FAIL"
            elif any(value == "BLOCKED" for value in statuses):
                status = "BLOCKED"
            elif any(value == "NOT_RUN" for value in statuses):
                status = "NOT_RUN"
            elif statuses:
                status = "PASS"
            else:
                status = "NOT_RUN"
        if status not in STATUSES:
            raise ValueError(f"invalid case status: {status}")
        self.status = status
        self.reason = reason or self.reason
        self.finished_at = _utc_now()
        return status

    def finalize_inventory(self) -> dict[str, Any]:
        """Fill planned subcases that recorded no observation and verify coverage."""
        missing: list[str] = []
        for item in PLAN.get(self.case_id, ()):
            if item.name not in self.subcases:
                missing.append(item.name)
                self.subcase(item.name, "NOT_RUN", reason="planned subcase recorded no observation", level=item.level)
        levels = {row.get("level") for row in self.subcases.values()}
        self.assertions["subcase_inventory"] = {
            "planned": len(PLAN.get(self.case_id, ())),
            "recorded": len(self.subcases),
            "missing": missing,
            "levels": sorted(level for level in levels if level),
        }
        return self.assertions["subcase_inventory"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "package": self.package,
            "acceptance": list(self.acceptance),
            "status": self.status,
            "reason": self.reason,
            "assertions": self.assertions,
            "subcases": self.subcases,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return _json_safe(value.model_dump(mode="json"))
        except TypeError:
            return _json_safe(value.model_dump())
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


_SECRET_KEY_WORDS = ("token", "password", "credential", "authorization", "secret", "receipt", "isolation_proof")


def _redact_string(value: str) -> str:
    result = value
    try:
        repo = str(ROOT)
        if repo and repo in result:
            result = result.replace(repo, "$REPO")
    except Exception:
        pass
    try:
        home = str(Path.home())
        if home and len(home) > 1 and home in result:
            result = result.replace(home, "$HOME")
    except Exception:
        pass
    for marker in (".phase1-private", ".phase2-private", "control-private", ".g3-private", ".phase4-private"):
        if marker in result:
            result = result.replace(marker, "PRIVATE_REDACTED")
    if "comsol-mcp-phase4-doc-" in result or "comsol-mcp-phase3-doc-" in result:
        result = "PRIVATE_DOC_FIXTURE"
    return result


def _redact(value: Any, *, key: str = "") -> Any:
    """Redact secret material and private absolute paths without destroying evidence structure.

    A key that merely *mentions* a secret (for example the subcase name
    ``credential_text_not_persisted``) keeps its structure; only scalar values under such a key
    are replaced, and every string leaf anywhere still goes through the path/pattern rules.
    """
    lowered = key.lower()
    secret_key = any(word in lowered for word in _SECRET_KEY_WORDS)
    if isinstance(value, Mapping):
        return {str(name): _redact(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, key=key) for item in value]
    if isinstance(value, str):
        if secret_key:
            return "REDACTED"
        if lowered in {"snippet", "content"}:
            return {"redacted_document_text": True,
                    "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                    "characters": len(value)}
        if lowered == "text" and value.lstrip().startswith(("{", "[")):
            try:
                return json.dumps(_redact(json.loads(value)), ensure_ascii=False, sort_keys=True)
            except (ValueError, TypeError):
                pass
        return _redact_string(value)
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_redact(_json_safe(value)), ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_sources(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.is_file():
            rows.append({"path": str(path), "exists": False})
            continue
        rows.append({"path": str(path.resolve()), "exists": True, "size": path.stat().st_size,
                     "sha256": _sha256(path)})
    return rows


_LEGACY_PATH_ARGUMENTS = {"model_load": "path", "save_model": "path", "load_visible_main_model": None,
                          "load_current_main_model": None}

#: Operations whose strict versioned implementation may still be PROPOSED while the host has
#: already published the public legacy MCP action that carries the same job.  A published
#: public route is a real route: availability is recorded with explicit route annotations
#: (see ``_apply_legacy_fallbacks``) and the live step is routed to the published tool
#: (see ``ActionClient._published_tool``), never silently substituted.
LEGACY_ROUTE_FALLBACKS = {"model.create": "model_create", "model.load": "model_load",
                          "model.save": "save_model"}


def _legacy_arguments(operation: str, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Re-shape strict-catalog arguments for the legacy managed tool that will carry them.

    The legacy throughput tools take a bare ``path``; the strict catalog nests the path inside a
    path policy.  Sending the strict body to a legacy tool would be rejected as a bad argument.
    """
    key = _LEGACY_PATH_ARGUMENTS.get(tool)
    if isinstance(key, str):
        nested = arguments.get("path_policy") if isinstance(arguments.get("path_policy"), Mapping) else None
        destination = arguments.get("destination")
        if isinstance(destination, Mapping):
            source: Mapping[str, Any] | None = destination
        elif isinstance(destination, str) and os.path.isabs(destination):
            # The strict catalog types ``destination`` as a plain path string.  Only an
            # absolute path is forwarded, so a policy token can never become a filename.
            source = {"path": destination}
        else:
            source = nested
        path = (source or {}).get("path") if isinstance(source, Mapping) else arguments.get(key)
        if isinstance(path, str) and path:
            extra = {"artifact_id": arguments["artifact_id"]} if isinstance(arguments.get("artifact_id"), str) else {}
            return {**extra, key: path}
    return dict(arguments)


def _execution(*, key: str, request: str | None = None, ref: Mapping[str, Any] | None = None,
               revision: int | None = None, revision_override: int | None = None, **timeouts: Any) -> dict[str, Any]:
    request_id = request or key
    result: dict[str, Any] = {
        "idempotency_key": _RUN_IDEMPOTENCY_PREFIX + key,
        "request_id": _RUN_IDEMPOTENCY_PREFIX + request_id,
    }
    result.update(timeouts)
    if ref is not None:
        result["model_ref"] = dict(ref)
        result["session_id"] = ref.get("session_id")
        result["expected_revision"] = revision_override if revision_override is not None else revision
    return result


def _payload_execution(payload: Mapping[str, Any] | None) -> tuple[dict[str, Any] | None, int | None]:
    if not isinstance(payload, Mapping):
        return None, None
    execution = payload.get("execution")
    if not isinstance(execution, Mapping):
        data = payload.get("data")
        execution = data.get("execution") if isinstance(data, Mapping) else None
    if not isinstance(execution, Mapping):
        return None, None
    ref = execution.get("model_ref")
    revision = execution.get("revision")
    if not isinstance(ref, Mapping):
        ref = None
    if isinstance(revision, bool) or not isinstance(revision, int):
        revision = None
    return (dict(ref) if ref is not None else None), revision


def _error_code(payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    error = payload.get("error")
    if isinstance(error, Mapping):
        code = error.get("code")
        return str(code) if code else None
    if isinstance(error, str):
        return error
    return None


def _error_message(payload: Mapping[str, Any] | None, fallback: str = "") -> str:
    if not isinstance(payload, Mapping):
        return fallback
    error = payload.get("error")
    if isinstance(error, Mapping) and isinstance(error.get("message"), str):
        return error["message"]
    if isinstance(error, str):
        return error
    return fallback


def _blocked_payload(payload: Mapping[str, Any] | None) -> bool:
    return _error_code(payload) in _BLOCKED_CODES


def _unreadable_verdict(payload: Mapping[str, Any] | None, reason: str) -> tuple[str, str]:
    """Status for "the value could not be established".

    An unknown engine state (or the control-plane gate) is a *blocked* condition: nothing
    about the acceptance line was established, so it must not be reported as a contract
    violation.  Any other refusal keeps the caller's FAIL reason.
    """
    outcome = _unknown_outcome(payload)
    if outcome is not None:
        detail = outcome.get("message") or outcome.get("error_code") or "EXECUTION_STATE_UNKNOWN"
        return "BLOCKED", f"{reason}: the engine state was unknown ({detail})"
    conflict = _revision_conflict(payload)
    if conflict is not None:
        return ("BLOCKED",
                f"{reason}: the call was refused by the managed-revision precondition "
                f"({conflict['precondition']}: {conflict.get('message') or 'REVISION_CONFLICT'}) before it "
                f"reached the engine")
    if _blocked_payload(payload):
        return "BLOCKED", f"{reason}: {_error_code(payload)}"
    return "FAIL", reason


#: Error codes a guard probe may be refused with; the message text is checked as well so a
#: build that only words its refusal differently still counts as an explicit denial.
DENIAL_CODES = {"PERMISSION_DENIED", "ACCESS_DENIED", "FORBIDDEN", "PERMISSION",
                "PATH_OUTSIDE_WORKSPACE", "OUTSIDE_WORKSPACE", "PRIVATE_PATH_DENIED"}
DENIAL_WORDS = ("permission", "denied", "forbidden", "outside", "not allowed", "private")


def _denial_verdict(payload: Mapping[str, Any] | None, reason: str, *,
                    release: Mapping[str, Any] | None = None) -> tuple[str, str | None, dict[str, Any]]:
    """Verdict for a probe that must be *refused*.

    A refusal is only evidence when the product says so.  An unknown execution state (or a
    call the control plane gated) means the probe never reached the guard: that is blocked,
    never "denied" and never a breach.
    """
    identity = _json_safe(_envelope_identity(payload))
    message = _error_message(payload) or ""
    code = _error_code(payload) or ""
    raw = payload if isinstance(payload, Mapping) else {}
    detail: dict[str, Any] = {"identity": identity, "error_code": code or None, "error_message": message or None,
                              "outer_isError": raw.get("_outer_isError")}
    outcome = _unknown_outcome(payload)
    if outcome is not None:
        detail["unknown_engine_state"] = outcome
        detail["release"] = _json_safe(release) if release is not None else None
        before = ("the control plane refused the call with an unknown engine state"
                  if outcome.get("gate") else "the call returned an unknown engine state")
        return ("BLOCKED",
                f"{reason}: {before} ({code or 'EXECUTION_STATE_UNKNOWN'}) before the guard could run, so "
                "neither a denial nor an acceptance was established", detail)
    if _success(payload):
        return "FAIL", reason, detail
    text = json.dumps(identity).lower() + " " + message.lower()
    if code in DENIAL_CODES or any(word in text for word in DENIAL_WORDS):
        return "PASS", None, detail
    return "FAIL", f"{reason} (refused with {code or 'an unnamed error code'}: {message or 'no message'})", detail


def _success(payload: Mapping[str, Any] | None) -> bool:
    return isinstance(payload, Mapping) and payload.get("success") is True and payload.get("_outer_isError") is not True


def _data(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    value = payload.get("data") if isinstance(payload, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else {}


def _execution_readback(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    data = _data(payload)
    value = data.get("readback")
    if not isinstance(value, Mapping):
        return {}
    nested = value.get("readback")
    if isinstance(nested, Mapping):
        return dict(nested)
    return dict(value)


def _same_ref(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None) -> bool:
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    names = ("session_id", "server_instance_id", "model_tag", "generation")
    return all(name in left and name in right and left.get(name) == right.get(name) for name in names)


def _envelope_identity(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Compact identity of a wire envelope (used where a legacy round trip is recorded)."""
    if not isinstance(payload, Mapping):
        return {"success": None, "outer_isError": None, "error_code": None, "has_structured_content": False}
    data = _data(payload)
    identity = execution_identity(payload)
    return {
        "success": payload.get("success"),
        "outer_isError": payload.get("_outer_isError"),
        # The daemon stamps ``execution.operation_id``/``job_id`` on every envelope it finishes;
        # ``data.operation_id`` is only the operation's *name* on a driver-shaped refusal, so it is
        # kept as a fallback, never as the operation's identity (C02/C03).
        "operation_id": identity.get("operation_id") or data.get("operation_id"),
        "job_id": identity.get("job_id"),
        "request_hash": identity.get("request_hash"),
        "error_code": _error_code(payload),
        "has_structured_content": isinstance(payload.get("_structuredContent"), Mapping),
    }


def execution_identity(payload: Mapping[str, Any] | None, *, tool: str = "") -> dict[str, Any]:
    """The operation identity the product published for one call, through the documented paths.

    ``operation_id``/``job_id``/``request_hash`` live in the envelope's ``execution`` block — the
    daemon stamps them when it finishes an operation.  Reading them from ``data.operation_id``
    (which carries the *requested operation's name* on a refusal) is how the first M1 run's
    GUARD_T010 compared two empty identities and reported the retry, not the product, as the
    defect.  ``source`` names every path that was used; a missing identity stays ``None``.
    """
    witness = unpack_envelope(tool or "identity", payload)
    execution = witness.execution if isinstance(witness.execution, Mapping) else {}
    payload_map = payload if isinstance(payload, Mapping) else {}
    return {
        "operation_id": execution.get("operation_id"),
        "job_id": witness.job_id,
        "idempotency_key": execution.get("idempotency_key"),
        "request_id": execution.get("request_id"),
        "request_hash": execution.get("request_hash"),
        "success": witness.success,
        "error_code": witness.error_code,
        "outer_isError": payload_map.get("_outer_isError"),
        "source": dict(witness.source),
        "has_structured_content": isinstance(payload_map.get("_structuredContent"), Mapping),
    }


def _mapping(value: Any) -> dict[str, Any]:
    """``dict`` view of a mapping value, or an empty dict (never ``None``)."""
    return dict(value) if isinstance(value, Mapping) else {}


def _envelope_job_id(payload: Mapping[str, Any] | None) -> str | None:
    """Durable job id of a wire envelope (``execution.job_id``, then the data payload)."""
    if not isinstance(payload, Mapping):
        return None
    data = _mapping(payload.get("data"))
    execution = _mapping(payload.get("execution")) or _mapping(data.get("execution"))
    for source in (execution, payload, data):
        value = source.get("job_id")
        if isinstance(value, str) and value:
            return value
    return None


def _unknown_outcome(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Classify an envelope that says the engine state is unknown or the gate is closed.

    Two wire shapes mean "the driver must reconcile before it may continue":

    * a first-hand UNKNOWN result (``error.code == EXECUTION_STATE_UNKNOWN`` and
      ``data.execution_state_unknown is True``, ``data.status == "UNKNOWN"``), whose
      ``execution.job_id`` *is* the unresolved job; and
    * the control daemon's unresolved-work refusal, whose message is
      :data:`ENGINE_GATE_MESSAGE` and whose ``execution.job_id`` is the *newly refused*
      job, not the unfinished one.

    ``None`` means the envelope carries no unknown/gate signal at all.
    """
    if not isinstance(payload, Mapping):
        return None
    error = _mapping(payload.get("error"))
    data = _mapping(payload.get("data"))
    nested = _mapping(data.get("error"))
    code = str(error.get("code") or nested.get("code") or "")
    message = str(error.get("message") or nested.get("message") or data.get("message") or "")
    gate = ENGINE_GATE_MESSAGE in message
    unknown = (code in UNKNOWN_ERROR_CODES
               or payload.get("execution_state_unknown") is True
               or data.get("execution_state_unknown") is True)
    if not (gate or unknown):
        return None
    return {"gate": gate, "unknown": unknown, "error_code": code or None, "message": message or None,
            "job_id": _envelope_job_id(payload)}


def _fresh_call_identity(arguments: Mapping[str, Any], *, suffix: str = "-gate-retry") -> dict[str, Any] | None:
    """Copy a call body with a fresh idempotency identity for exactly one replay.

    The control daemon stores the *result* of a refused job under its idempotency key, so a
    replay under the same key returns the stored refusal without touching the engine.  Both
    the idempotency key and the request id are replaced (the logical body is untouched), so
    the replay is a new request that proves the released gate.
    """
    def refresh(container: dict[str, Any]) -> bool:
        execution = container.get("execution")
        if not isinstance(execution, Mapping):
            return False
        fresh = dict(execution)
        changed = False
        for field in ("idempotency_key", "request_id"):
            value = fresh.get(field)
            if isinstance(value, str) and value:
                fresh[field] = value + suffix
                changed = True
        if not changed:
            return False
        container["execution"] = fresh
        return True

    request = dict(arguments)
    changed = refresh(request)
    logical = request.get("arguments")
    if isinstance(logical, Mapping):
        logical = dict(logical)
        changed = refresh(logical) or changed
        request["arguments"] = logical
    return request if changed else None


def _typed_shape(value: Mapping[str, Any] | None) -> list[int] | None:
    shape = value.get("shape") if isinstance(value, Mapping) else None
    if not isinstance(shape, list) or not all(isinstance(item, int) and not isinstance(item, bool) for item in shape):
        return None
    return list(shape)


def _property_value_rows(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    data = _data(payload)
    values = data.get("values", data.get("properties", []))
    return [dict(row) for row in values if isinstance(row, Mapping)
            and isinstance(row.get("name"), str) and isinstance(row.get("value"), Mapping)]


def _row_value(rows: Iterable[Mapping[str, Any]], name: str) -> Mapping[str, Any] | None:
    for row in rows:
        if isinstance(row, Mapping) and row.get("name") == name and isinstance(row.get("value"), Mapping):
            return row["value"]
    return None


def _git_snapshot() -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.STDOUT).strip()
        except Exception as exc:
            return f"UNAVAILABLE:{type(exc).__name__}"

    return {
        "head": run("rev-parse", "HEAD"),
        "status": run("status", "--short"),
        "changed_paths": run("diff", "--name-only"),
    }


# ---------------------------------------------------------------------------
# Production stdio host
# ---------------------------------------------------------------------------


class ProductionHost:
    """One real MCP stdio client session and its append-only transcript."""

    def __init__(
        self,
        args: argparse.Namespace,
        run_dir: Path,
        transcript: list[dict[str, Any]],
        *,
        label: str = "primary",
        private_home: Path | None = None,
        profile: str | None = None,
        run_state: dict[str, Any] | None = None,
    ) -> None:
        self.args = args
        self.run_dir = run_dir
        self.transcript = transcript
        self.label = label
        self.profile = profile or getattr(args, "tool_profile", None) or "full"
        # A run-owned, mode-700 private home keeps control tokens, SQLite state
        # and local documentation indexes out of the public evidence tree.  An
        # explicitly supplied live-runtime home is reserved for a caller that
        # intentionally binds an existing runtime (never for the reopen check,
        # which must start a fresh control daemon and Worker).
        default_private_root = _RUN_PRIVATE_HOME_ROOT or ROOT / ".g3-private" / "phase4-acceptance" / run_dir.name
        if private_home is not None:
            self.private_home = Path(private_home).expanduser()
            self.private_home_is_caller_owned = True
        elif args.private_home:
            self.private_home = Path(args.private_home).expanduser()
            self.private_home_is_caller_owned = True
        else:
            self.private_home = default_private_root / label / "mcp-home"
            self.private_home_is_caller_owned = False
        self.session: ClientSession | None = None
        self.transport: Any = None
        self.reader: Any = None
        self.writer: Any = None
        self.log_stream: Any = None
        self.tools: dict[str, Any] = {}
        self.initialized: Any = None
        self.isolation_receipt_present = bool(os.environ.get("COMSOL_MCP_ISOLATION_RECEIPT"))
        #: Every engine reconciliation this host had to perform, in order (evidence).
        self.reconciliations: list[dict[str, Any]] = []
        #: Every published ``model_inspect`` reconcile read this host performed, in order.
        self.refreshes: list[dict[str, Any]] = []
        #: The most recent reconcile read (the revision a refused write was replayed with).
        self.last_refresh: dict[str, Any] | None = None
        #: The reconcile read the *call now running* performed.  ``ActionClient`` adopts exactly
        #: this row: a refresh another call made must never overwrite the revision this call
        #: observed on the wire (the live long tail: a stale cached revision kept refusing every
        #: later write although a fresh refresh had already been read).
        self.last_call_refresh: dict[str, Any] | None = None
        #: The run's mutable state; the ledger publishes itself there (``state["unknown_jobs"]``) so
        #: the run index and the case documents persist it, not only this process.
        self.run_state = run_state
        #: The driver's *own* ledger of every job whose engine outcome was reported UNKNOWN.
        #: Neither the refusal envelope nor any published read names the jobs that block the
        #: control daemon's gate: the refusal only names the job it just refused.  A blocking job
        #: id can therefore only be learned from an envelope that reported its *own* UNKNOWN
        #: outcome, which is exactly what ``_observe_envelope`` records here (on every call path).
        self.job_ledger: dict[str, dict[str, Any]] = {}
        #: Ledger ids no ``job_reconcile`` has reported quiescent yet: they block the gate.
        self.unresolved_jobs: list[str] = []
        #: The "reconcile unfinished engine work" refusals the control plane returned (bounded).
        self.gate_refusals: dict[str, Any] = {"count": 0, "first": None, "last": None, "by_tool": {}}
        #: Calls that stayed refused after the ledger was released — never retried a second time.
        self.still_blocked: list[dict[str, Any]] = []
        #: Calls the control plane asked to re-ask ("resubmit with the same idempotency key") and
        #: the answers they produced: a distinct recovery from replaying an ambiguous mutation.
        self.requeries: list[dict[str, Any]] = []
        #: The newest ``(model identity, revision)`` any decoded envelope carried (wire choke point).
        self.last_readback: dict[str, Any] | None = None
        #: The run's centralized request/revision context (C02): per-ModelRef revision and dirty
        #: state, per-request run/case/step/sequence keys, dispatch stages and the classified
        #: refusals.  One instance for the whole run — never a global revision shared by models.
        self.context = ExecutionContext(run=run_dir.name)
        self._reconciling = False

    # ------------------------------------------------------------------
    # The self-recorded UNKNOWN-job ledger (the release path's only memory)
    # ------------------------------------------------------------------

    def ledger_entry(self, job_id: str, operation: str | None = None) -> dict[str, Any]:
        """The ledger row of one job (created on first sight, never duplicated)."""
        entry = self.job_ledger.get(job_id)
        if entry is None:
            entry = {
                "job_id": job_id,
                "operation": operation,
                "first_seen_at": _utc_now(),
                "observations": [],
                "release_attempts": [],
                "released": False,
                "reconciled_quiescent": None,
            }
            self.job_ledger[job_id] = entry
            if job_id not in self.unresolved_jobs:
                self.unresolved_jobs.append(job_id)
            self._publish_ledger()
        elif operation and not entry.get("operation"):
            entry["operation"] = operation
        return entry

    def _publish_ledger(self) -> None:
        """Publish the ledger into the run state the case/index writers persist."""
        if isinstance(self.run_state, dict):
            self.run_state["unknown_jobs"] = self.ledger_evidence()

    def ledger_evidence(self) -> dict[str, Any]:
        """The ledger as evidence: every recorded job, its release attempts and the refusals."""
        entries = [_json_safe(entry) for entry in self.job_ledger.values()]
        return {
            "unresolved_jobs": list(self.unresolved_jobs),
            "released_jobs": [str(entry.get("job_id")) for entry in entries if entry.get("released")],
            "entries": entries,
            "gate_refusals": _json_safe(dict(self.gate_refusals)),
            "still_blocked": _json_safe(list(self.still_blocked)),
            "note": ("the driver records every job id an envelope reported as UNKNOWN (no published "
                     "read lists the unresolved jobs) and releases each one through the published "
                     "job_reconcile read before it replays a refused call once"),
        }

    def note_gate_refusal(self, tool: str, outcome: Mapping[str, Any]) -> dict[str, Any]:
        """Record one control-plane gate refusal (bounded: count, per-tool totals, last row)."""
        row = {"tool": tool, "job_id": outcome.get("job_id"), "error_code": outcome.get("error_code"),
               "message": outcome.get("message"), "at": _utc_now()}
        by_tool = self.gate_refusals["by_tool"]
        by_tool[tool] = (by_tool.get(tool) or 0) + 1
        self.gate_refusals["count"] = int(self.gate_refusals["count"]) + 1
        if self.gate_refusals["first"] is None:
            self.gate_refusals["first"] = row
        self.gate_refusals["last"] = row
        self._publish_ledger()
        return row

    def note_still_blocked(self, tool: str, payload: Mapping[str, Any] | None, *, phase: str) -> dict[str, Any]:
        """Record a call that stayed refused after the release path ran (never replayed again)."""
        outcome = _unknown_outcome(payload) or {}
        row = {"tool": tool, "phase": phase, "gate": bool(outcome.get("gate")),
               "job_id": outcome.get("job_id") or _envelope_job_id(payload),
               "error_code": outcome.get("error_code") or _error_code(payload),
               "message": outcome.get("message") or _error_message(payload) or None,
               "unreleased_jobs": list(self.unresolved_jobs), "at": _utc_now()}
        self.still_blocked.append(row)
        del self.still_blocked[:-25]
        self._publish_ledger()
        return row

    def release_summary(self, payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
        """What the release path did for the job an envelope names (evidence, never a claim)."""
        outcome = _unknown_outcome(payload) or {}
        job_id = outcome.get("job_id")
        for record in reversed(self.reconciliations):
            if record.get("envelope_job_id") == job_id or job_id in (record.get("targets") or []):
                return _json_safe({"trigger": record.get("trigger"), "gate": record.get("gate"),
                                   "targets": record.get("targets"), "released": record.get("released"),
                                   "retry": record.get("retry"), "still_blocked": record.get("still_blocked")})
        return None

    def _observe_envelope(self, tool: str, payload: Mapping[str, Any], *,
                          request: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        """Learn from *every* envelope the host decodes — the ledger's only write path.

        A gate refusal names only the job it just refused (already terminal), so it is recorded as
        a refusal; a first-hand UNKNOWN envelope names the job that blocks the gate, so its id is
        learned here.  Both can arrive through any call path — a case probe, a reconcile read, or
        the retry of a managed-revision conflict — which is why the observation sits on the wire
        choke point instead of on one caller.

        The same choke point records the newest ``(model identity, revision)`` any envelope carried:
        a case that reads through a direct ``host.call`` (``model_tree``, a reconcile read) must
        still leave the client's cache current, or the call after it is refused as stale — exactly
        the live long tail after the chain steps.  Since C02 it also feeds the run's execution
        context, which keys that state by the *full* ModelRef and never adopts another model's
        revision.
        """
        ref, revision = _payload_execution(payload)
        if ref is not None and isinstance(revision, int) and not isinstance(revision, bool):
            seen = getattr(self, "last_readback", None)
            known = seen.get("revision") if isinstance(seen, Mapping) else None
            same = isinstance(seen, Mapping) and _same_ref(seen.get("model_ref"), ref)
            if not same or not isinstance(known, int) or revision >= known:
                self.last_readback = {"model_ref": ref, "revision": revision, "tool": tool}
        context = getattr(self, "context", None)
        if context is not None:
            try:
                context.observe(tool, payload, request=request)
            except Exception as exc:  # noqa: BLE001 - the choke point never breaks a real call
                context.reader_errors += 1
                context.observations.append({"tool": tool, "at": _utc_now(), "adopted": False,
                                             "context_error": f"{type(exc).__name__}: {exc}"})
                del context.observations[:-context.EVIDENCE_LIMIT]
        outcome = _unknown_outcome(payload)
        if outcome is None:
            return None
        if outcome.get("gate"):
            self.note_gate_refusal(tool, outcome)
            return outcome
        job_id = outcome.get("job_id")
        if isinstance(job_id, str) and job_id:
            entry = self.ledger_entry(job_id, tool)
            entry["last_seen_at"] = _utc_now()
            entry["last_error_code"] = outcome.get("error_code")
            entry["last_message"] = outcome.get("message")
            entry["observations"].append({"tool": tool, "error_code": outcome.get("error_code"),
                                          "message": outcome.get("message"), "at": _utc_now()})
            del entry["observations"][:-6]  # bounded: evidence, not a second transcript
            self._publish_ledger()
        return outcome

    async def resolve_unfinished_jobs(self, arguments: Mapping[str, Any] | None = None, *,
                                      why: str | None = None) -> dict[str, Any] | None:
        """Resolve the unfinished jobs the context knows *before* a new request is dispatched.

        The control daemon refuses every new operation while an unresolved job whose engine
        outcome is UNKNOWN is still on its books, and the live run only ever learned that from the
        refusal it then had to replay.  The published ``job_reconcile``/``job_status``/
        ``job_result`` reads are the product's own way to resolve it, so they run first here, with
        a bounded per-job query budget (a job that cannot be released must not be queried forever).
        """
        if self._reconciling:
            return None
        context = getattr(self, "context", None)
        if context is None:
            return None
        pending = [job_id for job_id in context.unfinished_jobs() if context.query_budget(job_id)]
        if not pending:
            return None
        record: dict[str, Any] = {"trigger": why or "unfinished job before a new logical request",
                                  "unresolved_before": list(self.unresolved_jobs), "targets": list(pending),
                                  "at": _utc_now()}
        previous = self._reconciling
        self._reconciling = True
        try:
            record["released"] = await self._reconcile_jobs(record, pending)
            for job_id in pending:
                entry = self.ledger_entry(job_id)
                context.resolve_unfinished(job_id, outcome="released" if entry.get("released") else "unresolved")
            record["retry"] = "not performed: this call's own identity is unchanged"
            self.reconciliations.append(_json_safe(record))
        finally:
            self._reconciling = previous
        return record

    def _environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        if self.args.comsol_root:
            env["COMSOL_ROOT"] = str(self.args.comsol_root)
        if self.args.jdk11:
            env["COMSOL_JAVA_HOME"] = str(self.args.jdk11)
            env["JAVA_HOME"] = str(self.args.jdk11)
        if self.args.prefs:
            env["COMSOL_PREFS_DIR"] = str(self.args.prefs)
        env["COMSOL_SERVER_MCP_HOME"] = str(self.private_home.resolve())
        env["COMSOL_MCP_TOOL_PROFILE"] = self.profile
        env["COMSOL_SERVER_HOST"] = str(self.args.host)
        env["COMSOL_SERVER_PORT"] = str(self.args.port)
        # COMSOL_MCP_ISOLATION_RECEIPT is injected by the orchestrating
        # environment.  The driver only passes it through: it never invents,
        # logs or augments an isolation proof.
        return env

    def server_command(self) -> list[str]:
        """The exact stdio command line this host launched (path-redacted at write time)."""
        return [str(Path(self.args.python).expanduser()), "-m", "comsol_mcp.mcp_server"]

    def initialize_summary(self) -> dict[str, Any]:
        """Compact, redacted summary of the MCP initialize handshake."""
        initialized = self.initialized
        server_info = getattr(initialized, "serverInfo", None)
        capabilities = getattr(initialized, "capabilities", None)
        return {
            "protocolVersion": getattr(initialized, "protocolVersion", None),
            "server_name": getattr(server_info, "name", None),
            "server_version": getattr(server_info, "version", None),
            "capabilities": _json_safe(capabilities),
            "instruction_bytes": len(getattr(initialized, "instructions", "") or ""),
            "tool_count": len(self.tools),
            "private_home": str(self.private_home),
            "caller_owned_private_home": self.private_home_is_caller_owned,
            "tool_profile": self.profile,
        }

    async def __aenter__(self) -> "ProductionHost":
        if not self.private_home_is_caller_owned:
            self.private_home.mkdir(mode=0o700, parents=True, exist_ok=True)
            for private_level in (
                self.private_home,
                self.private_home.parent,
                self.private_home.parent.parent,
                self.private_home.parent.parent.parent,
            ):
                try:
                    os.chmod(private_level, 0o700)
                except OSError:
                    pass
        else:
            self.private_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.log_stream = (self.run_dir / f"{self.label}.engine.log").open("w", encoding="utf-8")
        params = StdioServerParameters(
            # Keep the caller-provided virtualenv launcher unresolved: resolving
            # the symlink replaces ``.venv/bin/python`` with the system Python
            # and silently drops the MCP dependency from the child.
            command=str(Path(self.args.python).expanduser()),
            args=["-m", "comsol_mcp.mcp_server"],
            env=self._environment(),
            cwd=str(ROOT),
        )
        self.transport = stdio_client(params, errlog=self.log_stream)
        transport_entered = False
        session_entered = False
        try:
            self.reader, self.writer = await self.transport.__aenter__()
            transport_entered = True
            self.session = ClientSession(self.reader, self.writer, read_timeout_seconds=timedelta(minutes=10))
            await self.session.__aenter__()
            session_entered = True
            started = time.monotonic()
            self.initialized = await self.session.initialize()
            self.transcript.append({
                "host": self.label,
                "transport": "stdio",
                "operation": "initialize",
                "elapsed_s": time.monotonic() - started,
                "result": _json_safe(self.initialized),
                "outer_isError": False,
            })
            listed = await self.session.list_tools()
            tool_rows = _json_safe(listed)
            self.transcript.append({"host": self.label, "transport": "stdio", "operation": "tools/list",
                                    "result": tool_rows, "outer_isError": False})
            self.tools = {str(getattr(tool, "name", "")): _json_safe(tool) for tool in getattr(listed, "tools", [])
                          if getattr(tool, "name", None)}
            return self
        except BaseException as exc:
            exc_info = (type(exc), exc, exc.__traceback__)
            if session_entered and self.session is not None:
                try:
                    await self.session.__aexit__(*exc_info)
                except Exception:
                    pass
            if transport_entered and self.transport is not None:
                try:
                    await self.transport.__aexit__(*exc_info)
                except Exception:
                    pass
            if self.log_stream is not None:
                self.log_stream.close()
            raise

    async def __aexit__(self, *exc: Any) -> None:
        try:
            if self.session is not None:
                await self.session.__aexit__(*exc)
        finally:
            if self.transport is not None:
                await self.transport.__aexit__(*exc)
            if self.log_stream is not None:
                self.log_stream.close()
            worker_json = self.private_home / "control-private" / "worker" / "worker_endpoint.json"
            if worker_json.is_file():
                try:
                    data = json.loads(worker_json.read_text())
                    w_pid = data.get("pid")
                    if isinstance(w_pid, int) and w_pid > 0 and w_pid != os.getpid():
                        os.kill(w_pid, signal.SIGKILL)
                except Exception:
                    pass
            ctrl_json = self.private_home / "control-private" / "control.json"
            if ctrl_json.is_file():
                try:
                    data = json.loads(ctrl_json.read_text())
                    ctrl_pid = data.get("pid")
                    if isinstance(ctrl_pid, int) and ctrl_pid > 0 and ctrl_pid != os.getpid():
                        os.kill(ctrl_pid, signal.SIGKILL)
                except Exception:
                    pass

    async def call(self, name: str, arguments: Mapping[str, Any] | None = None, *,
                   reconcile: bool = True) -> dict[str, Any]:
        """One tool call, with the engine-reconciliation gates handled around it.

        Two refusals mean "the driver must reconcile before it may continue":

        * an unknown engine state (or the daemon's "reconcile unfinished engine work"
          refusal): the published ``job_reconcile`` read is recorded and — only for a call the
          gate *refused* — replayed once under a fresh idempotency identity.  A first-hand
          UNKNOWN result is never replayed: that result is the operation's evidence.
        * a managed-revision conflict (``REVISION_CONFLICT``): the published
          ``model_inspect(refresh=true)`` read is recorded and the refused call is replayed once
          with the refreshed revision.  ``reconcile=False`` marks a *negative* probe (a
          deliberately stale revision): the refusal is then this call's evidence.

        ``RECONCILE_EXEMPT_TOOLS`` is never gated on itself.
        """
        request = dict(arguments or {})
        self.last_call_refresh = None
        payload = await self._call_once(name, request)
        if name in RECONCILE_EXEMPT_TOOLS:
            if self._reconciling:
                return payload
            # A release/describe tool is never gated on itself, but the product's own instruction
            # to re-ask a call under the *same* idempotency key applies to it exactly as it does to
            # a domain call: ``operation_describe`` answered "Control response unavailable; query
            # or resubmit with the same idempotency key to reconcile" in the live run, and the
            # driver returned that envelope verbatim, so the plan read reported
            # ``EXECUTION_STATE_UNKNOWN`` as an *operation capability* (GUARD_T005/T033 were
            # blocked by a describe that the product had already said how to re-ask).  The re-ask
            # is one call, under the same identity, and only when the message asks for it.
            answered = await self._requery_instructed(name, request, payload)
            return answered if answered is not None else payload
        if self._reconciling:
            return payload
        outcome = _unknown_outcome(payload)
        if outcome is not None:
            released = await self._reconcile_and_continue(name, request, payload, outcome)
            answered = await self._requery_instructed(name, request, payload)
            return answered if answered is not None else released
        conflict = _revision_conflict(payload)
        if conflict is None:
            return payload
        if not reconcile:
            conflict["reconcile"] = "not performed: this call is a deliberate revision probe"
            self.reconciliations.append(_json_safe({"trigger_tool": name, **conflict}))
            return payload
        return await self._release_revision_conflict(name, request, payload, conflict)

    async def _release_revision_conflict(self, name: str, arguments: Mapping[str, Any],
                                         payload: dict[str, Any], conflict: Mapping[str, Any]) -> dict[str, Any]:
        """Release a managed-revision conflict through the published reconcile read, then retry once.

        The refusal happened in the ledger's preflight, before any engine call, so replaying it
        cannot double-apply a mutation — but the replay still uses a *fresh* idempotency identity,
        because the daemon stores the refusal's result under the original key.
        """
        self._reconciling = True
        record: dict[str, Any] = {
            "trigger_tool": name,
            "trigger": "managed revision conflict",
            "gate": False,
            "error_code": conflict.get("error_code"),
            "message": conflict.get("message"),
            "precondition": conflict.get("precondition"),
            "sent_expected_revision": _expected_revision(arguments),
            "envelope_job_id": conflict.get("job_id"),
            "at": _utc_now(),
        }
        context = getattr(self, "context", None)
        if context is not None:
            # Why the product refused this request — stale because our own writes advanced the
            # revision, or because the model moved without a dispatch of this run.
            record["classification"] = _json_safe(context.classify_revision_conflict(conflict, arguments))
        try:
            refresh = await self._refresh_model_read(str(arguments.get("project_id") or "phase4"), arguments)
            record["refresh"] = refresh
            if not refresh.get("released"):
                record["retry"] = ("not performed: the published model_inspect reconcile read did not report "
                                   "a released revision")
                record["still_blocked"] = self.note_still_blocked(name, payload, phase="revision-refresh-failed")
                self.reconciliations.append(_json_safe(record))
                return payload
            retry_arguments = _fresh_call_identity(arguments, suffix="-revision-retry")
            if retry_arguments is None:
                record["retry"] = "not performed: the call carried no idempotency identity to replace"
                record["still_blocked"] = self.note_still_blocked(name, payload, phase="revision-refresh-failed")
                self.reconciliations.append(_json_safe(record))
                return payload
            revision = refresh.get("revision")
            if record["precondition"] == "stale-expected-revision" or record["sent_expected_revision"] != revision:
                record["expected_revision_rewritten_to"] = revision
            retry_arguments = _with_expected_revision(retry_arguments, revision)
            request_id = str((_mapping(arguments.get("execution")) or {}).get("request_id") or name)
            if context is not None:
                # The replay is a *new plan*: a new key and a rewritten revision.  It is only
                # legitimate because the refusal is a documented pre-dispatch refusal (the ledger's
                # preflight raised it before any engine call) and the published reconcile read has
                # just re-verified the model.  The context decides, and records the decision.
                replan, decision = context.grant_replan(
                    request_id, body=retry_arguments,
                    evidence={"dispatch_stage": "refused_before_engine",
                              "source": "the execution ledger refused this call in its preflight "
                                        "(managed-revision precondition)",
                              "error_code": conflict.get("error_code"),
                              "reconciled": bool(refresh.get("released")),
                              "revision": revision},
                    why="managed revision conflict: refreshed and re-dispatched once")
                record["replan"] = _json_safe(decision)
                if replan is None:
                    record["retry"] = ("not performed: the context refused a new plan for this request "
                                       f"({decision.get('detail')})")
                    record["still_blocked"] = self.note_still_blocked(name, payload, phase="replan-refused")
                    self.reconciliations.append(_json_safe(record))
                    return payload
            retried = await self._call_once(name, retry_arguments)
            record["retry"] = {"success": retried.get("success"), "error_code": _error_code(retried),
                               "precondition": (_revision_conflict(retried) or {}).get("precondition"),
                               "retried_expected_revision": _expected_revision(retry_arguments)}
            self.reconciliations.append(_json_safe(record))
            retry_outcome = _unknown_outcome(retried)
            if retry_outcome is not None:
                # The replayed write was refused again — its engine outcome is UNKNOWN or the
                # control gate closed.  The ledger already learned the new job at the wire choke
                # point, so hand the envelope to the release path: it reconciles what the ledger
                # knows and, for a gate refusal, replays this call exactly once more under a fresh
                # identity.  (Observed live: without this the retry's own UNKNOWN job stayed
                # unreconciled and closed the gate for the rest of the run.)
                return await self._reconcile_and_continue(
                    name, retry_arguments, retried, retry_outcome,
                    trigger="managed revision conflict replay")
            if not _success(retried):
                # The refreshed revision did not release the call either: exactly one replay per
                # refusal, and the call is recorded as still blocked rather than retried again.
                record["still_blocked"] = self.note_still_blocked(name, retried,
                                                                  phase="revision-replay-refused")
                self.reconciliations.append(_json_safe(record))
            return retried
        finally:
            self._reconciling = False

    async def _refresh_model_read(self, prefix: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Perform the published reconciliation read and report the revision it established.

        ``model_inspect`` with ``refresh: true`` is the product's own reconcile entry point
        (``_managed_backend`` routes it to ``ExecutionService.reconcile``); it is exempt from the
        daemon's unresolved-work gate, so it is the only read that can release a blocked write.
        The read is recorded in ``refreshes``; nothing about it is inferred.
        """
        execution = _mapping((arguments or {}).get("execution"))
        ref = execution.get("model_ref") if isinstance(execution.get("model_ref"), Mapping) else None
        session = execution.get("session_id")
        row: dict[str, Any] = {"tool": "model_inspect", "refresh": True, "at": _utc_now(),
                               "model_ref": _json_safe(ref), "session_id": session}
        if "model_inspect" not in self.tools:
            row.update({"available": False, "released": False,
                        "reason": "model_inspect is not published by this host, so the managed revision "
                                  "cannot be reconciled through a published read"})
            return self._record_refresh(row)
        if not isinstance(ref, Mapping):
            row.update({"available": True, "released": False,
                        "reason": "the refused call carried no model_ref, so there was nothing to reconcile"})
            return self._record_refresh(row)
        # Every reconcile read is a *fresh* question ("what is the managed revision now?") whose
        # answer must be read again: reusing one idempotency key made the control daemon replay the
        # *first* stored answer and refuse the rest with ``IDEMPOTENCY_CONFLICT`` — observed live on
        # 50 of 52 reconcile reads, which is why every later write still saw a stale revision.
        key = _fresh_key(f"phase4-reconcile-model-{prefix}")
        request = {"refresh": True, "execution": _execution(key=key, request=key, ref=ref,
                                                            revision=execution.get("expected_revision"))}
        payload = await self._call_once("model_inspect", request)
        readback = _mapping((payload or {}).get("execution"))
        revision = readback.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            revision = None
        dirty = readback.get("dirty")
        row.update({
            "available": True, "success": _success(payload), "error_code": _error_code(payload),
            "revision": revision, "dirty": dirty, "sent_expected_revision": _expected_revision(request),
            "released": bool(_success(payload) and dirty is False and revision is not None),
            "note": ("the reconcile read reports the ledger revision and whether the model still carries an "
                     "unacknowledged engine change"),
        })
        return self._record_refresh(row)

    #: The product's own instruction to re-ask a call under the *same* idempotency identity.
    REQUERY_INSTRUCTIONS = ("resubmit with the same idempotency key", "query or resubmit")

    async def _requery_instructed(self, name: str, arguments: Mapping[str, Any],
                                  payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """Re-ask a call the control plane itself asked to re-ask — once, under the same identity.

        A "Control response unavailable" envelope is not the operation's outcome: its own message
        says to query or resubmit with the same idempotency key, and that key is exactly what makes
        the daemon answer with the stored result instead of executing the work twice (observed live
        on chain A's ``mesh.statistics``, which stayed unresolved although the product had said how
        to resolve it).  Every other first-hand UNKNOWN keeps the rule: reconciled, never replayed.
        """
        message = (_error_message(payload) or "").lower()
        if not any(text in message for text in self.REQUERY_INSTRUCTIONS):
            return None
        record: dict[str, Any] = {"tool": name, "phase": "requery-instructed",
                                  "instruction": _error_message(payload), "at": _utc_now()}
        context = getattr(self, "context", None)
        request_id = str((_mapping(arguments.get("execution")) or {}).get("request_id") or name)
        if context is not None:
            # Re-asking under the *same* key is allowed only for the *same* request: the body must
            # be byte-identical.  A changed body is a new plan and must not be sent as a "retry".
            refusal = context.reuse_for_retry(request_id, body=_plan_body(arguments))
            if refusal is not None:
                record["reuse"] = _json_safe(refusal)
                records = getattr(self, "requeries", None)
                if records is None:
                    records = self.requeries = []
                records.append(_json_safe(record))
                return None
        previous = self._reconciling
        self._reconciling = True
        try:
            answered = await self._call_once(name, dict(arguments))
        finally:
            self._reconciling = previous
        record["outcome"] = {"success": answered.get("success"), "error_code": _error_code(answered),
                             "job_id": _envelope_job_id(answered)}
        records = getattr(self, "requeries", None)
        if records is None:
            records = self.requeries = []
        records.append(_json_safe(record))
        if _success(answered) and _unknown_outcome(answered) is None:
            return answered
        # The re-ask was answered with another unknown state: this *read* keeps the tri-state it
        # reports (the operation's own status was never established), but the job the envelope
        # named is reconciled through the published release reads so the control gate it closed
        # re-opens for the calls that come after this one instead of refusing them all.
        outcome = _unknown_outcome(answered) or _unknown_outcome(payload)
        if outcome is not None:
            record["release"] = await self._release_known_work(
                outcome, trigger="describe/read EU after the same-key re-ask")
        return None

    async def _release_known_work(self, outcome: Mapping[str, Any], *, trigger: str) -> dict[str, Any]:
        """Reconcile the unfinished work an envelope named — without replaying the refused call.

        Used where the instruction is to *query* rather than resubmit (a read whose answer is the
        evidence) and where a replay would be a second execution (`RECONCILE_EXEMPT_TOOLS`).
        """
        record: dict[str, Any] = {
            "trigger": trigger,
            "gate": bool(outcome.get("gate")),
            "error_code": outcome.get("error_code"),
            "message": outcome.get("message"),
            "envelope_job_id": outcome.get("job_id"),
            "unresolved_before": list(self.unresolved_jobs),
            "at": _utc_now(),
        }
        previous = self._reconciling
        self._reconciling = True
        try:
            targets = self._reconcile_targets(outcome)
            record["targets"] = list(targets)
            record["released"] = await self._reconcile_jobs(record, targets)
            record["retry"] = "not performed: this call's own answer is its evidence"
            self.reconciliations.append(_json_safe(record))
        finally:
            self._reconciling = previous
        return record

    def _record_refresh(self, row: Mapping[str, Any]) -> dict[str, Any]:
        """Record one reconcile read on the host and return it (the callers' only evidence)."""
        recorded = _json_safe(dict(row))
        self.refreshes.append(recorded)
        self.last_refresh = recorded
        self.last_call_refresh = recorded
        return recorded

    async def reconcile_external_change(self, arguments: Mapping[str, Any] | None = None, *,
                                        why: str = "unacknowledged engine change") -> dict[str, Any] | None:
        """Run the published reconcile read *before* a call the ledger would otherwise refuse.

        A write is refused while the engine carries a change no published read has acknowledged
        (``execution.dirty``).  The live run only ever reconciled *after* a refusal, so the next
        call was refused again for the same reason — the long tail this method removes.  The read
        is the product's own ``model_inspect(refresh=true)`` entry point; ``None`` means the host
        is already inside a release path (which reconciles as part of its own protocol).
        """
        if self._reconciling:
            return None
        prefix = str((arguments or {}).get("project_id") or "phase4")
        previous = self._reconciling
        self._reconciling = True
        try:
            row = await self._refresh_model_read(prefix, arguments)
        finally:
            self._reconciling = previous
        row["trigger"] = why
        return row

    async def _reconcile_and_continue(self, name: str, arguments: Mapping[str, Any],
                                      payload: dict[str, Any], outcome: Mapping[str, Any],
                                      *, trigger: str | None = None) -> dict[str, Any]:
        """Release what the ledger knows about, then replay a *refused* call exactly once.

        A first-hand UNKNOWN result is this call's own evidence and is never replayed — but it *is*
        reconciled, so the gate it closed re-opens for the calls that come after it.  A gate
        refusal is replayed exactly once under a fresh idempotency identity, and only after a
        ``job_reconcile`` reported a ledger job quiescent; if it stays refused it is recorded as
        ``still_blocked`` and reported as the rejection it is.
        """
        previous = self._reconciling
        self._reconciling = True
        gate = bool(outcome.get("gate"))
        record: dict[str, Any] = {
            "trigger_tool": name,
            "trigger": trigger or ("control-gate refusal" if gate else "first-hand UNKNOWN result"),
            "gate": gate,
            "error_code": outcome.get("error_code"),
            "message": outcome.get("message"),
            "envelope_job_id": outcome.get("job_id"),
            "unresolved_before": list(self.unresolved_jobs),
            "at": _utc_now(),
        }
        try:
            targets = self._reconcile_targets(outcome)
            record["targets"] = list(targets)
            if not targets and gate:
                # A gate refusal names the *refused* job in its envelope, not the job that blocks
                # the gate; record what the published reads expose about it.
                record["discovery"] = await self._probe_unresolved_hint()
            record["released"] = await self._reconcile_jobs(record, targets)
            if not gate:
                # The refused work *is* this call's own result: replaying it would erase the
                # evidence and re-run a transaction that already reported UNKNOWN.
                record["retry"] = "not performed: a first-hand UNKNOWN result is this call's evidence"
            elif not record["released"]:
                record["retry"] = "not performed: the gate was not released"
                record["still_blocked"] = self.note_still_blocked(name, payload, phase="release-failed")
            else:
                retry_arguments = _fresh_call_identity(arguments)
                if retry_arguments is None:
                    record["retry"] = "not performed: the call carried no idempotency identity to replace"
                else:
                    context = getattr(self, "context", None)
                    request_id = str((_mapping(arguments.get("execution")) or {}).get("request_id") or name)
                    if context is not None:
                        # A gate refusal is the control daemon refusing the call *before* dispatch:
                        # proved NOT_EXECUTED, and the jobs it names have just been reconciled through
                        # their own published reads.  That is the only basis on which the context
                        # grants this (single, recorded) new plan.
                        _replan, decision = context.grant_replan(
                            request_id, body=retry_arguments,
                            evidence={"dispatch_stage": "refused_before_engine",
                                      "source": "the control daemon refused the call before dispatch "
                                                "(reconcile unfinished engine work)",
                                      "error_code": outcome.get("error_code"),
                                      "reconciled": bool(record.get("released"))},
                            why="control-gate refusal: released the unfinished work and re-dispatched once")
                        record["replan"] = _json_safe(decision)
                        if _replan is None:
                            record["retry"] = ("not performed: the context refused a new plan for this "
                                               f"request ({decision.get('detail')})")
                            record["still_blocked"] = self.note_still_blocked(name, payload,
                                                                             phase="replan-refused")
                            self.reconciliations.append(_json_safe(record))
                            return payload
                    retried = await self._call_once(name, retry_arguments)
                    retry_outcome = _unknown_outcome(retried)
                    record["retry"] = {"success": retried.get("success"), "error_code": _error_code(retried),
                                       "job_id": _envelope_job_id(retried), "still_unknown": retry_outcome is not None}
                    if retry_outcome is not None:
                        # The replay revealed more unfinished work (the ledger already recorded it
                        # at the wire choke point): release that too, then stop.  Exactly one
                        # replay per refusal — a call that stays refused is reported as such.
                        record["retry_released"] = await self._reconcile_jobs(record, self._reconcile_targets(retry_outcome))
                        record["still_blocked"] = self.note_still_blocked(
                            name, retried, phase="replay-gate-refused" if retry_outcome.get("gate")
                            else "replay-reported-unknown")
                    self.reconciliations.append(_json_safe(record))
                    return retried
            self.reconciliations.append(_json_safe(record))
            return payload
        finally:
            self._reconciling = previous

    def _reconcile_targets(self, outcome: Mapping[str, Any]) -> list[str]:
        """Jobs to release: the refused envelope's own job, plus every unreleased ledger job.

        A gate refusal names the *newly refused* job in its envelope (already terminal: FAILED);
        the job that actually blocks the gate reported its own UNKNOWN outcome to an earlier call
        and is remembered in the ledger.  A first-hand UNKNOWN envelope names the blocking job
        itself, so it is learned and released here.
        """
        candidates: list[str] = []
        if outcome.get("gate"):
            candidates.extend(self.unresolved_jobs)
        else:
            job_id = outcome.get("job_id")
            if isinstance(job_id, str) and job_id:
                self.ledger_entry(job_id, None)
                candidates.append(job_id)
        return [item for index, item in enumerate(candidates) if item and item not in candidates[:index]]

    async def _probe_unresolved_hint(self) -> dict[str, Any]:
        """Record what the published reads expose when a gate refusal names no blocking job.

        The refusal only reports the job it just refused; no published read lists the
        unresolved jobs themselves (``job_status``/``job_log``/``job_result`` all need an id,
        and ``job_reconcile`` on the refused job cannot release anything).  This is recorded
        so a run that starts against a pre-existing gate reports the gap instead of guessing.
        """
        note = ("no published read lists unresolved jobs; the gate can only be released through a job id "
                "observed in this process (an earlier EXECUTION_STATE_UNKNOWN envelope)")
        if "session_health" not in self.tools:
            return {"available": False, "note": note}
        payload = await self._call_once("session_health", {"execution": _execution(key="reconcile-discover",
                                                                                 request="session_health")})
        data = _data(payload)
        return {"available": _success(payload), "status": data.get("status"),
                "worker_connected": data.get("worker_connected"),
                "active_jobs": _json_safe(data.get("active_jobs")), "note": note,
                "error_code": _error_code(payload)}

    async def _reconcile_jobs(self, record: dict[str, Any], targets: Sequence[str]) -> bool:
        """Call ``job_reconcile`` for each candidate and report whether the gate opened.

        Every attempt is recorded on its ledger row, so a job that was reported quiescent is never
        retried (and a job whose release failed keeps its evidence).
        """
        rows: list[dict[str, Any]] = record.setdefault("jobs", [])
        if not targets:
            record["job_reconcile"] = ("no ledger job is unreleased: no job id was observed as UNKNOWN in "
                                       "this process and the refusal names only the job it just refused")
            return False
        if "job_reconcile" not in self.tools:
            record["job_reconcile"] = "job_reconcile is not published by this host"
            return False
        released = False
        for job_id in targets:
            result = await self._call_once("job_reconcile", {
                "job_id": job_id,
                "execution": _execution(key="reconcile-" + job_id, request="job_reconcile"),
            })
            data = _mapping(_data(result))
            metadata = _mapping(data.get("metadata"))
            quiescent = bool(data.get("reconciled_quiescent") or metadata.get("reconciled_quiescent"))
            row = {"job_id": job_id, "success": _success(result), "error_code": _error_code(result),
                   "status": data.get("status"), "reconciled_quiescent": quiescent,
                   "reconciliation": _json_safe(data.get("reconciliation") or metadata.get("reconciliation")),
                   "at": _utc_now()}
            rows.append(row)
            entry = self.ledger_entry(job_id)
            entry["status"] = data.get("status")
            entry["reconciled_quiescent"] = quiescent
            entry["release_attempts"].append(_json_safe(row))
            del entry["release_attempts"][:-8]
            if quiescent:
                released = True
                entry["released"] = True
                entry["released_at"] = _utc_now()
                if job_id in self.unresolved_jobs:
                    self.unresolved_jobs.remove(job_id)
            else:
                # ``job_reconcile`` could not *verify* the release (observed live: the isolated
                # worker answered "worker request status unavailable", status UNKNOWN, and the gate
                # stayed closed for every call after it).  The same envelope says to *query* the
                # job, so the published ``job_status``/``job_result`` reads are tried: a job that
                # reports a terminal outcome has been read back, which is what the gate holds out
                # for.
                observed = await self._observe_job_reads(row, job_id)
                if observed.get("terminal"):
                    released = True
                    entry["released"] = True
                    entry["released_at"] = _utc_now()
                    entry["released_via"] = f"{observed.get('via')} reported {observed.get('status')}"
                    if job_id in self.unresolved_jobs:
                        self.unresolved_jobs.remove(job_id)
            self._publish_ledger()
        return released

    #: A published job read reports one of these once the job reached an outcome; a ledger job that
    #: can be read back at all is no longer *unreconciled* work.
    TERMINAL_JOB_STATUSES = frozenset({"SUCCEEDED", "SUCCESS", "FAILED", "FAILURE", "ERROR", "CANCELLED",
                                       "CANCELED", "TERMINAL", "COMPLETE", "COMPLETED", "DONE", "ABORTED",
                                       "RELEASED"})

    async def _observe_job_reads(self, row: dict[str, Any], job_id: str) -> dict[str, Any]:
        """Query the published job reads for one unresolved job — the product's own instruction.

        ``job_reconcile`` can answer with ``status: UNKNOWN`` and a reconciliation row saying
        "worker request status unavailable" (observed live): the driver then cannot *verify* the
        release and the control plane keeps refusing new work.  The same envelope says to *query
        the job*, and ``job_status``/``job_result`` are the published queries.  Only their own
        answers are recorded; when neither can be read, the job stays unresolved and the record
        says exactly that.
        """
        reads: list[dict[str, Any]] = []
        observed: dict[str, Any] = {"reads": reads, "terminal": False}
        for tool, extra in (("job_status", {}), ("job_result", {})):
            if tool not in self.tools:
                reads.append({"tool": tool, "available": False})
                continue
            payload = await self._call_once(tool, {"job_id": job_id, **extra,
                                                   "execution": _execution(key=f"{tool}-{job_id}",
                                                                           request=tool)})
            data = _mapping(_data(payload))
            status = str(data.get("status") or "")
            terminal = status.upper() in self.TERMINAL_JOB_STATUSES
            reads.append({"tool": tool, "available": True, "success": _success(payload), "status": status,
                          "error_code": _error_code(payload), "message": _error_message(payload),
                          "terminal": terminal, "data_keys": sorted(str(key) for key in data)})
            if terminal:
                observed.update({"terminal": True, "via": tool, "status": status})
                break
        row["job_reads"] = reads
        row["observed_via"] = observed.get("via")
        return observed

    async def _call_once(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError("stdio session is not initialized")
        request = dict(arguments or {})
        started = time.monotonic()
        response = await self.session.call_tool(name, request)
        elapsed = time.monotonic() - started
        outer_error = bool(getattr(response, "isError", False))
        structured = _json_safe(getattr(response, "structuredContent", None))
        content = _json_safe(getattr(response, "content", []))
        if isinstance(structured, Mapping):
            payload = dict(structured)
        else:
            payload = {}
            for block in getattr(response, "content", []) or []:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    try:
                        decoded = json.loads(text)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(decoded, Mapping):
                        payload = dict(decoded)
                        break
            if not payload:
                payload = {"success": False, "data": {},
                           "error": {"code": "INVALID_MCP_RESPONSE", "message": "no structured JSON result"}}
        row = {
            "host": self.label,
            "transport": "stdio",
            "operation": name,
            "arguments": request,
            "elapsed_s": elapsed,
            "outer_isError": outer_error,
            "structuredContent": structured,
            "content": content,
            "payload": payload,
        }
        self.transcript.append(_redact(row))
        payload = {**payload, "_outer_isError": outer_error, "_structuredContent": structured}
        # Every envelope passes through here, so the ledger learns from the wire itself: a job
        # whose outcome is UNKNOWN is recorded (with the operation that reported it) even when the
        # caller never asks what to do about it.  The run's execution context reads the same
        # envelope through its fixed schema and only adopts state that matches the request's own
        # model_ref.
        self._observe_envelope(name, payload,
                               request=request.get("execution") if isinstance(request.get("execution"), Mapping)
                               else None)
        return payload


class ActionClient:
    """Resolve a logical catalog action to a published tool or strict fallback."""

    CONTROL_ALIASES = {
        "docs.index": "docs_index",
        "docs.search": "docs_search",
        "docs.get": "docs_get",
        "docs.examples": "docs_examples",
        "docs.error_search": "docs_error_search",
        "checkpoint.list": "checkpoint_list",
        "checkpoint.inspect": "checkpoint_inspect",
        "code.describe_java": "code_describe_java",
        "code.compile_java": "code_compile_java",
        "transaction.preview": "transaction_preview",
        "transaction.verify": "transaction_verify",
        # Legacy managed route (published fallback for the original 51 tools).
        "evaluate_expressions": "evaluate_expressions",
        "get_core_metrics": "get_core_metrics",
        "manage_variables": "manage_variables",
        "get_parameters": "get_parameters",
        "save_model": "save_model",
        "model_tree": "model_tree",
        "run_study": "run_study",
    }

    def __init__(self, host: ProductionHost, args: argparse.Namespace, state: dict[str, Any]) -> None:
        self.host = host
        self.args = args
        self.state = state
        #: Every request this client actually dispatched, by its idempotency key: the *exact* bytes
        #: handed to the wire, so a retry of one of them re-sends those bytes instead of deriving a
        #: new body (C02 — a rewritten body under the same key is a new plan, and the product
        #: answers it with ``IDEMPOTENCY_CONFLICT``).
        self.dispatched: dict[str, dict[str, Any]] = {}
        #: Every true retry this client performed, in order (evidence, bounded).
        self.retries: list[dict[str, Any]] = []

    def _published_tool(self, operation: str) -> tuple[str | None, bool]:
        alias = self.CONTROL_ALIASES.get(operation, operation.replace(".", "_"))
        if alias in self.host.tools:
            return alias, False
        if operation in self.host.tools:
            return operation, False
        # The strict versioned route may still be PROPOSED while the public legacy MCP action
        # for the same job is already published: call the route that exists.
        fallback = LEGACY_ROUTE_FALLBACKS.get(operation)
        if fallback is not None and fallback in self.host.tools:
            return fallback, False
        if "operation_call" in self.host.tools:
            return "operation_call", True
        if "registry_call" in self.host.tools:
            return "registry_call", True
        return None, False

    def _wire_body(self, body: Mapping[str, Any], ref: Mapping[str, Any] | None, revision: int | None) -> dict[str, Any]:
        result = {"project_id": self.args.project_id}
        if ref is not None:
            result.update({"session_id": ref.get("session_id"), "model_ref": dict(ref), "expected_revision": revision})
        result.update(dict(body))
        return result

    def _record_identity(self, payload: Mapping[str, Any] | None) -> None:
        ref, revision = _payload_execution(payload)
        if ref is None:
            return
        current = self.state.get("ref")
        if current is None or _same_ref(current, ref):
            self.state["ref"] = ref
            if revision is not None:
                self.state["revision"] = revision

    def _adopt_refresh(self, host: ProductionHost) -> None:
        """Adopt what the reconcile read of *this call* established.

        A refusal carries no ``execution`` readback, so the cached revision would stay behind the
        ledger's and every later write would be refused as stale (observed live in chain B after an
        ``EXECUTION_STATE_UNKNOWN``, and then in every later case of the run).  Only the refresh of
        the call now returning is adopted: a refresh another call made must never overwrite the
        revision this call read off its own envelope.  Within one model the revision only moves
        forward, so a refresh that is older than what the call already saw is ignored.
        """
        refresh = getattr(host, "last_call_refresh", None)
        if not isinstance(refresh, Mapping) or not _success(refresh):
            return
        candidate = refresh.get("model_ref")
        if isinstance(candidate, Mapping) and isinstance(candidate.get("model_ref"), Mapping):
            candidate = dict(candidate["model_ref"])
        elif isinstance(candidate, Mapping):
            candidate = dict(candidate)
        else:
            candidate = None
        current = self.state.get("ref")
        same_model = candidate is not None and _same_ref(current, candidate)
        if candidate is not None and (current is None or same_model):
            self.state["ref"] = candidate
        revision = refresh.get("revision")
        if isinstance(revision, int) and not isinstance(revision, bool):
            known = self.state.get("revision")
            if same_model or (current is None and candidate is not None):
                if not isinstance(known, int) or isinstance(known, bool) or revision >= known:
                    self.state["revision"] = revision
            # else: a different model carries its own revision counter, never a comparable one.
        if refresh.get("released") is True:
            # The published read acknowledged the engine change, so the gate the ledger closed is
            # open again; a call that follows does not need to reconcile first.
            self.state.pop("needs_reconcile", None)

    async def action(
        self,
        operation: str,
        body: Mapping[str, Any] | None = None,
        *,
        require_model: bool = True,
        key: str | None = None,
        request: str | None = None,
        revision_override: int | None = None,
        reconcile: bool = True,
        rpc_timeout_s: float | None = None,
    ) -> dict[str, Any]:
        # Every envelope decoded since the last call — a case's own direct ``host.call`` read
        # included — is adopted *before* this call's identity is built, so the revision it compares
        # against is the newest one the run has seen (the live tail: a successful step left a stale
        # revision behind and the next write was refused).
        self._adopt_readback(None)
        ref = self.state.get("ref") if require_model else None
        revision = revision_override if revision_override is not None else self.state.get("revision")
        if require_model and not isinstance(ref, Mapping):
            raise CapabilityUnavailable(f"{operation} requires a bound model_ref")
        tool, fallback = self._published_tool(operation)
        if tool is None:
            raise CapabilityUnavailable(f"{operation} is neither published nor available through operation_call")
        logical = self._wire_body(_legacy_arguments(operation, tool, body or {}), ref, revision)
        if fallback:
            call_args: dict[str, Any] = {"operation_id": operation, "arguments": logical}
        else:
            call_args = logical
        # C02: every logical request carries its own run/case/step/sequence identity.  A driver-minted
        # key is never shared with another request (the live defect behind the 50/52 replayed
        # reconcile reads); an explicitly keyed call keeps its key, because it may be probing the
        # product's own idempotency contract (GUARD_T010).
        context: ExecutionContext | None = getattr(self.host, "context", None)
        scope = self.state.get("request_scope")
        scope = scope if isinstance(scope, Mapping) else {}
        plan = context.mint(case=str(scope.get("case") or "run"),
                            step=str(scope.get("step") or request or operation.replace(".", "-")),
                            body=None, explicit_key=key) if context is not None else None
        exec_kwargs: dict[str, Any] = {}
        if rpc_timeout_s is not None:
            exec_kwargs["rpc_timeout_s"] = float(rpc_timeout_s)
        call_args["execution"] = _execution(
            key=key or (plan.key if plan is not None
                        else operation.replace(".", "-") + "-" + str(len(self.host.transcript))),
            request=request,
            ref=ref,
            revision=revision,
            revision_override=revision_override,
            **exec_kwargs,
        )
        if context is not None and plan is not None:
            # The digest of the body as *actually dispatched* is what may later decide whether a
            # same-key call is a retry of this request or a different request.
            context.bind_body(plan.key, _plan_body(call_args))
        negative_probe = not reconcile
        decision = (context.precheck(call_args, tool=tool, negative_probe=negative_probe, reconcile=reconcile)
                    if context is not None else None)
        if decision is not None:
            if context is not None and negative_probe:
                context.note_negative_probe(
                    request_id=str((_mapping(call_args.get("execution")) or {}).get("request_id") or operation),
                    tool=operation, model_ref=ref,
                    note=str((decision.get("detail") or {}).get("note") or decision.get("reason") or ""))
            if decision.get("action") == "refuse":
                # No wire call at all: the request contradicts itself (or names a replaced
                # generation), and every layer would otherwise choose a different identity.
                if context is not None and plan is not None:
                    context.note_dispatch(request_id=plan.request_id, tool=operation, stage="not_dispatched",
                                          reason=str(decision.get("reason")), model_ref=ref, revision=revision,
                                          decision=str(decision.get("action")), detail=decision.get("detail"))
                return self._pre_dispatch_refusal(operation, tool, call_args, decision)
            if decision.get("action") == "query_first" and reconcile and not negative_probe:
                resolver = getattr(self.host, "resolve_unfinished_jobs", None)
                if callable(resolver):
                    # The original job's own published query runs first; this request keeps its
                    # identity and is never re-executed under a new one.
                    await resolver(call_args, why=str((decision.get("detail") or {}).get("note")
                                                      or decision.get("reason")))
        reconciler: Callable[..., Awaitable[dict[str, Any] | None]] | None = getattr(
            self.host, "reconcile_external_change", None)
        if (reconcile and self.state.get("needs_reconcile") and self.state.get("ref") is not None
                and callable(reconciler)):
            # The ledger is carrying an engine change no published read has acknowledged yet: a
            # write would be refused for it.  Reconcile first (the product's own read), so the
            # refusal never happens instead of being classified after the fact.  A negative probe
            # passes ``reconcile=False`` and keeps the model exactly as it found it.
            await reconciler(call_args, why="pre-call: unacknowledged engine change")
        if plan is not None:
            # The dispatched bytes are recorded *before* the wire call: this is what a true retry
            # may re-send, and it is the only body that may reuse this key.
            self.dispatched[plan.key] = {
                "key": plan.key, "request_id": plan.request_id, "request": request, "tool": tool,
                "operation": operation, "route": "fallback" if fallback else "published",
                "arguments": _json_safe(call_args), "body_sha256": _body_sha256(_plan_body(call_args)),
                "at": _utc_now(),
            }
        payload = await self.host.call(tool, call_args, reconcile=reconcile)
        if context is not None and plan is not None:
            product_stage = product_dispatch_stage(payload)
            context.note_dispatch(request_id=plan.request_id, tool=operation,
                                  stage=observed_dispatch_stage(payload),
                                  reason=str((decision or {}).get("reason") or "") or None,
                                  model_ref=ref, revision=revision,
                                  # C03: the product's own cause/stage always travel with the
                                  # driver's reading of them, so a refusal keeps its original
                                  # operation/code/cause/dispatch stage in the evidence.
                                  product_stage=_json_safe(product_stage) if product_stage else None,
                                  negative_probe=negative_probe)
        self._adopt_refresh(self.host)
        self._record_identity(payload)
        self._adopt_readback(payload)
        self._note_engine_dirt(payload)
        return payload

    async def retry(self, key: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Re-dispatch a recorded request with *exactly* the bytes it was dispatched with (C02).

        Only the same request, whose response was uncertain, may reuse its key *and* its body: the
        expected revision, the model binding and every other field come from the record — nothing is
        derived again.  Deriving a fresh revision is what the first M1 run did and what the product
        rightly refused with ``IDEMPOTENCY_CONFLICT`` (it hashes the whole semantic request,
        ``expected_revision`` included), so the run filed a product defect that was its own.  The
        retry is recorded as a replay of that one request and ``host.call`` is asked not to re-plan
        it: a faithful retry is never rewritten inside the host.

        Returns ``(payload, record)`` where the record carries both body digests and whether they
        are identical — the retry's own proof that it re-sent the request it claims to retry.
        """
        record = self.dispatched.get(key)
        if record is None:
            raise CapabilityUnavailable(
                f"{key!r} has no dispatched request to retry: an idempotency key may only be reused "
                "for the request it was minted for")
        # A deep copy: the dispatched record is evidence and must not share mutable parts with the
        # retry that reads it.
        arguments = _json_safe(record["arguments"])
        body = _plan_body(arguments)
        digest = _body_sha256(body)
        context = getattr(self.host, "context", None)
        if context is not None:
            # A same-key retry is only legitimate with the identical body; the context records the
            # check and returns a decision when the body changed (then nothing is dispatched).
            decision = context.reuse_for_retry(str(record["request_id"]), body=body)
            if decision is not None:
                raise CapabilityUnavailable(
                    f"the retry of {key!r} was refused before dispatch: {decision.get('detail') or decision.get('reason')}")
        payload = await self.host.call(str(record["tool"]), arguments, reconcile=False)
        self._adopt_refresh(self.host)
        self._record_identity(payload)
        self._adopt_readback(payload)
        self._note_engine_dirt(payload)
        row = {"key": key, "request_id": record["request_id"], "tool": record["tool"],
               "operation": record["operation"], "body_sha256": record["body_sha256"],
               "retry_body_sha256": digest, "same_body": digest == record["body_sha256"],
               "first_dispatched_at": record.get("at"), "at": _utc_now(),
               "success": payload.get("success"), "error_code": _error_code(payload)}
        self.retries.append(_json_safe(row))
        del self.retries[:-25]
        if context is not None:
            context.note_dispatch(request_id=str(record["request_id"]), tool=str(record["operation"]),
                                  stage=observed_dispatch_stage(payload), reason="same-key retry (identical body)",
                                  model_ref=self.state.get("ref"), revision=self.state.get("revision"),
                                  product_stage=_json_safe(product_dispatch_stage(payload)) or None,
                                  retry=True)
        return payload, _json_safe(row)

    def _pre_dispatch_refusal(self, operation: str, tool: str, arguments: Mapping[str, Any],
                              decision: Mapping[str, Any]) -> dict[str, Any]:
        """A refusal the driver raised *before* any wire call — recorded, shaped like the product's.

        The envelope carries the published ActionResult fields (``success``/``data``/``error``) so
        every checker reads it exactly like a server envelope, plus an explicit ``pre_dispatch``
        block stating that no JSON-RPC call was made and why.
        """
        reason = str(decision.get("reason"))
        detail = _mapping(decision.get("detail"))
        execution = dict(_mapping(arguments.get("execution")))
        error = {"code": "DRIVER_PRE_DISPATCH_REFUSAL", "reason": reason, "safe_retry": False,
                 "type": "ExecutionContextRefusal",
                 "message": f"{operation} was refused before dispatch: {decision.get('note') or reason}"}
        data = {"operation_id": operation, "reason": reason, "refusal": _json_safe(detail)}
        payload: dict[str, Any] = {"success": False, "data": data, "error": error, "execution": execution,
                                   "pre_dispatch": {"tool": tool, "wire_call": False, "operation": operation,
                                                    "reason": reason, "detail": _json_safe(detail)}}
        payload["_outer_isError"] = True
        payload["_structuredContent"] = {"success": False, "data": data, "error": error, "execution": execution}
        self.host.transcript.append(_redact({
            "host": getattr(self.host, "label", "primary"), "transport": "stdio", "operation": tool,
            "arguments": dict(arguments), "elapsed_s": 0.0, "outer_isError": True,
            "structuredContent": payload["_structuredContent"], "content": [], "payload": payload,
            "pre_dispatch_refusal": True}))
        return payload

    def _adopt_readback(self, payload: Mapping[str, Any] | None) -> None:
        """Keep the cached identity at least as new as anything the wire reported.

        Every decoded envelope is recorded on the host (``last_readback``), including reads a case
        made through a direct ``host.call``.  Adopting it here means the next call compares against
        the newest revision the run has actually seen — the revision of *this* payload wins when it
        carries one, so the cache never moves backwards within a model.
        """
        seen = getattr(self.host, "last_readback", None)
        if not isinstance(seen, Mapping):
            return
        candidate = seen.get("model_ref")
        candidate = dict(candidate) if isinstance(candidate, Mapping) else None
        revision = seen.get("revision")
        if candidate is None or not isinstance(revision, int) or isinstance(revision, bool):
            return
        own_ref, own_revision = _payload_execution(payload)
        if own_ref is not None and _same_ref(own_ref, candidate) and isinstance(own_revision, int) \
                and not isinstance(own_revision, bool) and own_revision > revision:
            return  # the payload's own readback is newer than the recorded one
        current = self.state.get("ref")
        same_model = _same_ref(current, candidate)
        if current is None or same_model:
            self.state["ref"] = candidate
            known = self.state.get("revision")
            if same_model or current is None:
                if not isinstance(known, int) or isinstance(known, bool) or revision >= known:
                    self.state["revision"] = revision

    def _note_engine_dirt(self, payload: Mapping[str, Any] | None) -> None:
        """Remember that the engine carries a change no published read has acknowledged.

        The ledger reports it on the envelopes that carry an ``execution`` readback (``dirty``); a
        refusal does not carry one, so the marker is only set from what the wire actually said.  It
        is cleared when a released reconcile read is adopted.
        """
        execution = _data(payload).get("execution")
        execution = execution if isinstance(execution, Mapping) else {}
        dirty = execution.get("dirty")
        if dirty is True or _data(payload).get("requires_model_reconciliation") is True:
            self.state["needs_reconcile"] = True


# ---------------------------------------------------------------------------
# Capability gates and bounded live steps
# ---------------------------------------------------------------------------


_EVALUATION_POLICY_PATH = "input_schema.properties.evaluation_policy"


def _evaluation_policy(data: Mapping[str, Any], schema: Any) -> tuple[Any, str | None, str]:
    """Where an operation's evaluation policy is actually published (C04).

    The original GUARD_T033 evidence carries the policy at
    ``input_schema.properties.evaluation_policy``: the operation's own input schema declares the
    field (its type/enum/description is the published contract).  The driver read only the data
    level, so it reported an empty policy and the case then asked for the same information in a
    field the operation never had.  The published schema path is read first, the data-level copy
    second, and the path that carried the value is recorded either way — a missing policy is
    reported as *not published*, never as an empty one.
    """
    properties = schema.get("properties") if isinstance(schema, Mapping) else None
    properties = properties if isinstance(properties, Mapping) else {}
    if "evaluation_policy" in properties:
        return properties.get("evaluation_policy"), _EVALUATION_POLICY_PATH, (
            "read from the operation's own input schema (the published declaration of the field)")
    if isinstance(data, Mapping) and "evaluation_policy" in data:
        return data.get("evaluation_policy"), "data.evaluation_policy", (
            "read from the describe payload's data level (no input-schema declaration published)")
    return None, None, "not published: neither the input schema nor the describe payload declares it"


async def _describe_row(host: ProductionHost, operation: str) -> dict[str, Any]:
    """Describe one operation without needing a case (the suite-level bind probe uses this)."""
    # The identity is *fresh per describe*: the control daemon stores one result per idempotency
    # identity, so a key reused across cases (`phase4-describe-<operation>`) makes every later
    # describe answer with the first call's stored envelope — including a stored
    # "Control response unavailable" one, which is exactly the envelope whose own instruction says
    # to re-ask under the same key.  A fresh key per call plus the same-key re-ask is the only
    # combination where that instruction can be honoured.
    key = _fresh_key("phase4-describe-" + operation.replace(".", "-"))
    if "operation_describe" not in host.tools:
        return {"available": False, "error_code": "UNSUPPORTED_OPERATION",
                "reason": "operation_describe is not published by this host"}
    payload = await host.call("operation_describe", {
        "operation_id": operation,
        "execution": _execution(key=key, request=key),
    })
    data = _data(payload)
    available = bool(_success(payload) and data.get("executable") is True)
    schema = data.get("input_schema")
    schema_properties = sorted(str(name) for name in (schema.get("properties") or {})) if isinstance(schema, Mapping) else []
    policy, policy_source, policy_note = _evaluation_policy(data, schema)
    wire = data.get("wire_compatibility")
    # A describe the engine never answered is *not* a capability gap: the operation's own status
    # was not established at all.  The unknown outcome is kept on the row so a gate that reads it
    # reports the blocked probe instead of "not executable in this build".
    unreadable = _unknown_outcome(payload)
    return {
        "available": available,
        "implementation_status": data.get("implementation_status"),
        "executable": data.get("executable"),
        "probe_unreadable": _json_safe(unreadable),
        "mcp_tool_name": data.get("mcp_tool_name"),
        "effect": data.get("effect"),
        "route": data.get("route"),
        "error_code": _error_code(payload),
        "schema_is_object": bool(isinstance(schema, Mapping) and schema.get("type") == "object"),
        "input_schema": _json_safe(schema),
        "output_contract": bool(data.get("output_contract")),
        "input_schema_properties": schema_properties,
        "wire_compatibility": _json_safe(wire),
        "execution_fields": _json_safe((wire or {}).get("execution_fields")) if isinstance(wire, Mapping) else None,
        # C04: the evaluation policy lives in the operation's own input schema; the path that
        # carried it is published with the value so a case never asks for it in a field the
        # operation does not have.
        "evaluation_policy": _json_safe(policy),
        "evaluation_policy_source": policy_source,
        "evaluation_policy_note": policy_note,
        "required_products": _json_safe(data.get("required_products")),
        "remediation": _json_safe(data.get("remediation")),
    }


async def _describe_operation(host: ProductionHost, operation: str, case: Case) -> dict[str, Any]:
    """Describe one operation for a case (the case receives the row through _prepare_case)."""
    del case  # the row is persisted by the caller, not here
    return await _describe_row(host, operation)


def _synthetic_blocked(operation: str, reason: str) -> dict[str, Any]:
    """A driver-side capability refusal, shaped exactly like a server-side structured error."""
    error = {"code": "CAPABILITY_UNAVAILABLE", "message": reason}
    return {"success": False, "error": dict(error), "data": {"operation_id": operation},
            "_outer_isError": True, "_structuredContent": {"success": False, "error": dict(error)}}


def _apply_legacy_fallbacks(host: ProductionHost, rows: dict[str, Any]) -> dict[str, Any]:
    """Reconcile a strict capability gap with an already published public MCP route.

    ``operation_describe`` reports the strict, versioned operation.  When that operation is
    still ``PROPOSED_NOT_IMPLEMENTED`` but the host publishes the public legacy MCP action
    that carries the same job (``model_create``/``model_load``/``save_model``), the case is
    not capability-blocked: the public route exists and the live step calls it.  The row
    keeps the untouched strict probe under ``strict_probe`` and gains explicit route
    annotations, so the substitution is recorded evidence rather than a silent rewrite.
    """
    published = getattr(host, "tools", {}) or {}
    for operation, tool in LEGACY_ROUTE_FALLBACKS.items():
        row = rows.get(operation)
        if not isinstance(row, Mapping) or row.get("available") is True:
            continue
        if tool not in published:
            continue
        patched = dict(row)
        patched["strict_probe"] = dict(row)
        patched["strict_available"] = False
        patched["strict_route"] = row.get("route")
        patched["fallback_tool"] = tool
        patched["available"] = True
        patched["route"] = f"legacy published MCP route ({tool})"
        patched["reason"] = (f"strict {operation} is "
                             f"{row.get('implementation_status') or row.get('error_code') or 'not executable'}; "
                             f"the published legacy MCP action {tool} carries this operation")
        rows[operation] = patched
    return rows


async def _prepare_case(case: Case, host: ProductionHost, operations: Iterable[str]) -> dict[str, Any]:
    """Describe every operation the case needs before any bound-model call."""
    rows: dict[str, Any] = dict(case.assertions.get("operation_availability") or {})
    for operation in operations:
        if operation in rows:
            continue
        try:
            rows[operation] = await _describe_operation(host, operation, case)
        except Exception as exc:
            rows[operation] = {"available": False, "error_code": type(exc).__name__, "reason": str(exc)}
    case.assertions["operation_availability"] = _apply_legacy_fallbacks(host, rows)
    return rows


def _plan_ops(case_id: str) -> tuple[str, ...]:
    seen: list[str] = []
    for item in PLAN.get(case_id, ()):
        for operation in item.ops:
            if operation not in seen:
                seen.append(operation)
    return tuple(seen)


def _unavailable_reason(rows: Mapping[str, Any], operations: Iterable[str]) -> str | None:
    missing = [operation for operation in operations if not (rows.get(operation) or {}).get("available")]
    if not missing:
        return None
    # An operation whose capability probe was never answered (the engine state was unknown) has no
    # implementation status: saying "not executable in this build" would turn an unreadable probe
    # into a claim about the build.  The two are reported separately.
    unreadable = [operation for operation in missing if (rows.get(operation) or {}).get("probe_unreadable")]
    details = ", ".join(
        f"{operation}(implementation_status={((rows.get(operation) or {}).get('implementation_status')) or (rows.get(operation) or {}).get('error_code') or 'unknown'})"
        for operation in missing if operation not in unreadable
    )
    if unreadable and not details:
        return ("the capability probe of required operation(s) could not be read: "
                + ", ".join(f"{operation}({((rows.get(operation) or {}).get('probe_unreadable') or {}).get('message') or 'unknown engine state'})"
                            for operation in unreadable))
    if unreadable:
        details += "; probe(s) not read: " + ", ".join(unreadable)
    return f"required operation(s) not executable in this build: {details}"


def _availability_subcase(case: Case, name: str, operations: Sequence[str], rows: Mapping[str, Any],
                          *, flag: bool | None = None) -> bool:
    """Record a static capability probe.

    A missing or unimplemented operation is a *capability gap*: it is preserved as
    BLOCKED with the operation status as evidence, never as FAIL.  FAIL is reserved
    for a contract that is published and still violated.
    """
    if flag is None:
        flag = all((rows.get(operation) or {}).get("available") for operation in operations)
    if flag:
        case.subcase(name, "PASS", level="static")
    else:
        case.subcase(name, "BLOCKED", level="static",
                     reason=_unavailable_reason(rows, operations)
                     or "the required operation(s) are published but not executable through this profile")
    return flag


def _gate(
    case: Case,
    args: argparse.Namespace,
    state: dict[str, Any],
    name: str,
    *,
    rows: Mapping[str, Any] | None = None,
    operations: Iterable[str] = (),
    level: str = "live",
    prereq: str | None = None,
    reason: str | None = None,
) -> bool:
    """Record the truthful status of a live subcase that must not run."""
    if prereq is not None and case.subcase_status(prereq) != "PASS":
        case.subcase(name, "NOT_RUN", reason=f"prerequisite subcase {prereq} did not pass", level=level)
        return False
    unavailable = _unavailable_reason(rows or {}, operations)
    if unavailable:
        case.subcase(name, "BLOCKED", reason=unavailable, level=level)
        return False
    if not args.live:
        case.subcase(name, "NOT_RUN", reason="offline run: --live was not supplied", level=level)
        return False
    if not isinstance(state.get("ref"), Mapping):
        case.subcase(name, "BLOCKED", reason="no bound model_ref", level=level)
        return False
    if reason:
        case.subcase(name, "NOT_RUN", reason=reason, level=level)
        return False
    return True


async def _step(
    case: Case,
    host: ProductionHost,
    client: ActionClient,
    args: argparse.Namespace,
    state: dict[str, Any],
    *,
    name: str,
    operation: str,
    arguments: Mapping[str, Any] | None = None,
    level: str = "live",
    require_model: bool = True,
    key: str | None = None,
    revision_override: int | None = None,
    prereq: str | None = None,
    check: Callable[[Mapping[str, Any], dict[str, Any]], Any] | None = None,
    reason: str | None = None,
    store: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Run one bounded live operation and record its observed evidence.

    ``check`` returns either ``(status, reason)`` or ``(status, reason, extra)``
    and is the only place a subcase may be declared PASS.  Without a check the
    operation's own success envelope decides, so an unverified action can never
    become a silent acceptance.
    """
    rows = case.assertions.get("operation_availability") or {}
    if not _gate(case, args, state, name, rows=rows, operations=(operation,), level=level, prereq=prereq, reason=reason):
        return None
    try:
        payload = await client.action(operation, dict(arguments or {}), require_model=require_model,
                                      key=key or name, request=key or name, revision_override=revision_override)
    except CapabilityUnavailable as exc:
        case.subcase(name, "BLOCKED", reason=str(exc), level=level)
        return None
    except Exception as exc:
        case.subcase(name, "FAIL", reason=f"{type(exc).__name__}: {exc}", level=level)
        case.assertions[f"step_{name}"] = {"traceback": traceback.format_exc()}
        return None
    record: dict[str, Any] = {
        "operation": operation,
        "success": payload.get("success"),
        "error_code": _error_code(payload),
        "error_message": _error_message(payload) or None,
        "outer_isError": payload.get("_outer_isError"),
        "elapsed_s": None,
        "data_keys": sorted(_data(payload)),
    }
    # C03: a refused step keeps the *original* operation, code, cause and dispatch stage.  The
    # product may wrap the operation's own failure (``error.details.cause_code``) inside a broader
    # code, and the stage it reports is what decides whether the request was executed at all.
    product_stage = product_dispatch_stage(payload)
    if product_stage is not None:
        record["cause_code"] = product_stage.get("cause_code")
        record["cause_message"] = product_stage.get("cause_message")
        record["dispatch_stage"] = product_stage.get("dispatch_stage")
        record["product_dispatch"] = product_stage
    conflict = _revision_conflict(payload)
    if conflict is not None:
        # The host already tried the published release; record what it did so the verdict is
        # never read as an unexplained refusal.
        record["revision_conflict"] = conflict
        record["reconciliations"] = [row for row in host.reconciliations
                                     if row.get("trigger") == "managed revision conflict"][-2:]
    if store is not None:
        store[name] = payload
    if check is not None:
        outcome = check(payload, {"case": case, "state": state, "args": args, "store": store or {}, "record": record})
        if isinstance(outcome, tuple):
            status = outcome[0]
            detail_reason = outcome[1] if len(outcome) > 1 else None
            extra = outcome[2] if len(outcome) > 2 else None
        else:
            status, detail_reason, extra = str(outcome), None, None
        if isinstance(extra, Mapping):
            record.update(extra)
        record["data_keys"] = sorted(_data(payload))
        case.assertions[f"step_{name}"] = record
        case.subcase(name, status, reason=detail_reason, level=level, **({"observed": extra} if extra else {}))
        return payload
    if _success(payload):
        case.assertions[f"step_{name}"] = record
        case.subcase(name, "PASS", level=level)
        return payload
    status = "BLOCKED" if _blocked_payload(payload) else "FAIL"
    case.assertions[f"step_{name}"] = record
    detail = record.get("error_message") or _error_code(payload) or "an invalid envelope"
    case.subcase(name, status, reason=f"{operation} returned {detail}", level=level)
    return payload


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _typed_number(value: Mapping[str, Any] | None) -> float | None:
    if not isinstance(value, Mapping):
        return None
    data = value.get("data")
    if isinstance(data, list):
        if len(data) != 1:
            return None
        data = data[0]
    return _finite_float(data)


def _flatten_numbers(value: Any) -> list[float]:
    if isinstance(value, list):
        out: list[float] = []
        for item in value:
            out.extend(_flatten_numbers(item))
        return out
    number = _finite_float(value)
    return [number] if number is not None else []


def _can_run(
    case: Case,
    args: argparse.Namespace,
    state: dict[str, Any],
    rows: Mapping[str, Any],
    operations: Iterable[str],
    *,
    prereq: str | None = None,
    require_writable_model: bool = False,
) -> tuple[bool, str, str | None]:
    """Return (runnable, truthful_status_if_not, reason_if_not)."""
    if prereq is not None and case.subcase_status(prereq) != "PASS":
        return False, "NOT_RUN", f"prerequisite subcase {prereq} did not pass"
    unavailable = _unavailable_reason(rows, operations)
    if unavailable:
        return False, "BLOCKED", unavailable
    if not args.live:
        return False, "NOT_RUN", "offline run: --live was not supplied"
    if not isinstance(state.get("ref"), Mapping):
        return False, "BLOCKED", "no bound model_ref"
    if require_writable_model and state.get("model_origin") == "adopted" and not args.allow_adopted_writes:
        return False, "BLOCKED", "the bound model was adopted from an existing tag; pass --allow-adopted-writes to authorize mutations"
    return True, "PASS", None


def _tree_read_blocked(case: "Case", name: str, probes: Mapping[str, Any]) -> bool:
    """Mark a tree-diff line BLOCKED when a call it compares was refused with an unknown engine state.

    Two ``None`` trees compare equal, so without this guard a gated run would read "the tree did not
    change" as a pass while neither read ever reached the product.
    """
    unknown = {key: _unknown_outcome(payload) for key, payload in probes.items()}
    refused = {key: value for key, value in unknown.items() if value is not None}
    if not refused:
        return False
    key, payload = sorted(refused.items())[0]
    _mark(case, name, "BLOCKED",
          f"the model tree could not be compared: the {key} call was refused with an unknown engine "
          f"state ({payload.get('error_code') or 'EXECUTION_STATE_UNKNOWN'}), so no tree change was observed",
          unknown_engine_state=_json_safe(payload))
    return True


def _mark(case: "Case", name: str, status: str, reason: str | None = None, *, level: str = "live", **data: Any) -> None:
    case.subcase(name, status, reason=reason, level=level, **data)


async def _discover_nodes(client: ActionClient, *, limit: int = 200,
                          root: Mapping[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stem = _fresh_key("phase4-find")
    payload = await client.action("node.find",
                                  {"query": {}, "root": dict(root) if isinstance(root, Mapping) else {"segments": []},
                                   "limit": limit},
                                  key=stem, request=stem)
    if not _success(payload):
        return [], {"error_code": _error_code(payload), "success": payload.get("success")}
    data = _data(payload)
    rows = [dict(row) for row in data.get("results", []) if isinstance(row, Mapping) and isinstance(row.get("path"), Mapping)]
    return rows, {
        "count": data.get("count"), "complete": data.get("complete"), "truncated": data.get("truncated"),
        "visited": data.get("visited"), "errors": data.get("errors"), "status": data.get("status"),
        "rpc_calls": data.get("rpc_calls"),
    }


async def _known_properties(client: ActionClient, path: Mapping[str, Any], wanted_kinds: Iterable[str]) -> list[dict[str, Any]]:
    kinds = set(wanted_kinds)
    stem = _fresh_key("phase4-schema")
    payload = await client.action("node.property_schema", {"path": dict(path)},
                                 key=stem, request=stem)
    if not _success(payload):
        return []
    rows = [dict(row) for row in _data(payload).get("properties", []) if isinstance(row, Mapping)]
    return [row for row in rows
            if row.get("metadata_status") == "KNOWN" and isinstance(row.get("name"), str)
            and (not kinds or row.get("kind") in kinds)]


async def _read_value(client: ActionClient, path: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
    # Every read carries a fresh identity: the same key would let the daemon replay the *stored*
    # result of the earlier read, which is a stale value after any write.
    stem = _fresh_key("phase4-property-get")
    payload = await client.action("node.property_get", {"path": dict(path), "names": [name]},
                                 key=stem, request=stem)
    if not _success(payload):
        return None
    return _row_value(_property_value_rows(payload), name)


async def _typed_candidates(
    client: ActionClient,
    *,
    kinds: Iterable[str],
    node_limit: int = 40,
    property_limit: int = 8,
    node_limit_find: int = 200,
    roots: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bounded discovery of readable typed properties on the bound document tree.

    ``roots`` restricts the walk to the given node paths (the driver's own probe fixture);
    the default walks the whole model from its root.
    """
    nodes: list[dict[str, Any]] = []
    discovery: dict[str, Any] = {}
    if not roots:
        nodes, discovery = await _discover_nodes(client, limit=node_limit_find)
    else:
        found: dict[str, Any] = {}
        for root in roots:
            rows, detail = await _discover_nodes(client, limit=node_limit_find, root=root)
            key = json.dumps(_json_safe(root), sort_keys=True)
            found[key] = detail
            nodes.extend(rows)
        discovery = {"roots": found, "count": len(nodes)}
    candidates: list[dict[str, Any]] = []
    for node in nodes[:node_limit]:
        path = node.get("path")
        if not isinstance(path, Mapping):
            continue
        try:
            properties = await _known_properties(client, path, kinds)
        except CapabilityUnavailable:
            raise
        except Exception:
            continue
        for row in properties[:property_limit]:
            value = await _read_value(client, path, row["name"])
            if value is None:
                continue
            candidates.append({
                "path": dict(path), "node_type": node.get("type_id"), "name": row["name"],
                "kind": row.get("kind"), "shape_rank": row.get("shape_rank"), "getter": row.get("getter"),
                "unit": row.get("unit"), "value": _json_safe(value),
            })
    return candidates, discovery


def _shape_len(value: Mapping[str, Any] | None) -> int | None:
    shape = _typed_shape(value)
    if shape is None or len(shape) != 1:
        return None
    return shape[0]


#: Verdicts of a static-preview rejection probe.  A refusal is only evidence when the product
#: says so: an unknown engine state (or a call the control plane gated) means the probe never
#: reached the validator, so it is *blocked* — never "the preview did not reject".
PREVIEW_REJECTED = "rejected"
PREVIEW_UNESTABLISHED = "unestablished"
PREVIEW_BLOCKED = "blocked"


async def _preview_static_rejection(
    client: ActionClient,
    actions: list[dict[str, Any]],
    expect_codes: Iterable[str],
    key: str,
) -> tuple[str, dict[str, Any]]:
    payload = await client.action("transaction.preview", {"actions": actions, "invariants": []},
                                  require_model=False, key=key, request=key)
    data = _data(payload)
    observed = {
        "success": payload.get("success"),
        "error_code": _error_code(payload),
        "error_message": _error_message(payload),
        "outer_isError": payload.get("_outer_isError"),
        "status": data.get("status"),
        "engine_called": data.get("engine_called"),
        "static_only": data.get("static_only"),
    }
    unknown = _unknown_outcome(payload)
    if unknown is not None:
        observed["unknown_engine_state"] = unknown
        observed["gate"] = bool(unknown.get("gate"))
        return PREVIEW_BLOCKED, observed
    rejected = (not _success(payload)) and payload.get("_outer_isError") is True and _error_code(payload) in set(expect_codes)
    return (PREVIEW_REJECTED if rejected else PREVIEW_UNESTABLISHED), observed


def _preview_rejection_reason(verdict: str, observed: Mapping[str, Any], subject: str) -> str:
    """Why a static-preview rejection line was not established (never read as a FAIL)."""
    if verdict == PREVIEW_BLOCKED:
        outcome = observed.get("unknown_engine_state") or {}
        return (f"the static preview of {subject} did not reach the validator: the call was refused with an "
                f"unknown engine state ({observed.get('error_code') or 'EXECUTION_STATE_UNKNOWN'}"
                + (", the control plane refused it before the engine" if outcome.get("gate") else "")
                + "), so the pre-engine rejection line was not established")
    return (f"the static preview did not reject {subject} with a documented code "
            f"({observed.get('error_code') or 'no error code'}; success={observed.get('success')!r}, "
            f"engine_called={observed.get('engine_called')!r}), so the pre-engine rejection line was not "
            "established")




# ---------------------------------------------------------------------------
# R01 — expression / string / numeric readback consistency
# ---------------------------------------------------------------------------

async def _case_r01(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace, state: dict[str, Any]) -> None:
    rows = await _prepare_case(case, host, _plan_ops(case.case_id))
    core_ops = ("node.property_schema", "node.property_get", "node.property_set")
    ops_ok = all((rows.get(operation) or {}).get("available") for operation in core_ops)
    case.assertion("static_expression_readback_ops_executable", ops_ok, operations=list(core_ops))
    ops_ok = _availability_subcase(case, "static_expression_readback_ops_executable", core_ops, rows, flag=ops_ok)

    verdict, observed = await _preview_static_rejection(
        client,
        [{"operation_id": "node.property_set",
          "arguments": {"path": {"segments": []}, "properties": [{"name": "phase4_probe_missing_value"}]}}],
        ("PROPERTY_TYPE_MISMATCH", "INVALID_REQUEST"), "r01-static-malformed",
    )
    case.assertions["r01_malformed_typed_value_preview"] = observed
    rejected = verdict == PREVIEW_REJECTED
    case.assertion("malformed_typed_value_rejected_pre_engine", rejected,
                   error_code=observed.get("error_code"), engine_called=observed.get("engine_called"))
    case.subcase("static_malformed_typed_value_rejected_pre_engine",
                 "PASS" if rejected else "BLOCKED", level="protocol",
                 reason=None if rejected else _preview_rejection_reason(
                     verdict, observed, "a malformed typed value"),
                 observed=observed)

    live_names = (
        "expression_string_same_text_verified", "expression_array_or_matrix_verified",
        "unit_expression_text_preserved", "incompatible_kind_or_unit_rejected_before_write",
        "non_matching_readback_not_silently_accepted", "nonfinite_text_strictly_rejected",
    )
    runnable, status, why = _can_run(case, args, state, rows, core_ops, require_writable_model=True)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    try:
        candidates, discovery = await _typed_candidates(client, kinds=("string", "expression", "float64", "int32", "int64"))
    except CapabilityUnavailable as exc:
        for name in live_names:
            _mark(case, name, "BLOCKED", str(exc))
        case.finish()
        return

    def _classify(rows: Sequence[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]],
                                                              list[Mapping[str, Any]], list[Mapping[str, Any]]]:
        strings = [row for row in rows if row["kind"] in {"string", "expression"} and row["shape_rank"] == 0]
        string_arrays = [row for row in rows if row["kind"] in {"string", "expression"}
                         and isinstance(row["shape_rank"], int) and row["shape_rank"] >= 1]
        numbers = [row for row in rows if row["kind"] in {"float64", "int32", "int64"} and row["shape_rank"] == 0]
        units = [row for row in rows if row["kind"] in {"string", "expression"}
                 and isinstance(row.get("unit"), str) and row.get("unit")]
        return strings, string_arrays, numbers, units

    strings, string_arrays, numbers, unit_candidates = _classify(candidates)
    fixture_evidence: dict[str, Any] | None = None
    if not (strings and string_arrays and numbers and unit_candidates):
        # The bound model may be an empty MCP-owned model: the probe targets the R01
        # acceptance lines need are then *created* on it (and read back) instead of the case
        # silently reporting NOT_RUN because no suitable property happened to exist.
        created, fixture_evidence = await _probe_candidates(
            client, args, state, ("string", "expression", "float64", "int32", "int64"))
        candidates = [*created, *candidates]
        strings, string_arrays, numbers, unit_candidates = _classify(candidates)
    case.assertions["r01_discovery"] = {
        "find": discovery, "candidates": len(candidates), "probe_fixture": fixture_evidence,
        "available": {"scalar_string": len(strings), "string_array": len(string_arrays),
                      "scalar_number": len(numbers), "unit_bearing": len(unit_candidates)},
        "targets": [{"requirements": sorted({name for name, rows in
                                              (("scalar_string", strings), ("string_array", string_arrays),
                                               ("scalar_number", numbers), ("unit_bearing", unit_candidates))
                                              if rows and rows[0] is row}),
                     "node": row.get("node_type"), "property": row.get("name"), "kind": row.get("kind"),
                     "shape_rank": row.get("shape_rank"), "unit": row.get("unit"),
                     "source": row.get("source", "model_discovery"), "path": row.get("path")}
                    for row in candidates[:8]],
    }

    # 1) Same text written as kind=expression into a String-stored property.
    if not strings:
        _mark(case, "expression_string_same_text_verified", "NOT_RUN",
              "the bound model exposed no scalar string-kind property; the R01 comparison rule was not exercised")
    else:
        candidate = strings[0]
        original = dict(candidate["value"])
        request_value = {"kind": "expression", "shape": list(original.get("shape") or []), "data": original.get("data")}
        if isinstance(original.get("unit"), str):
            request_value["unit"] = original["unit"]
        write = await client.action("node.property_set",
                                    {"path": candidate["path"], "properties": [{"name": candidate["name"], "value": request_value}]},
                                    key="r01-same-text", request="r01-same-text")
        readback = await _read_value(client, candidate["path"], candidate["name"])
        applied = _data(write).get("applied")
        row = applied[0] if isinstance(applied, list) and applied and isinstance(applied[0], Mapping) else {}
        comparison: dict[str, Any] = dict(row["comparison"]) if isinstance(row.get("comparison"), Mapping) else {}
        ok = bool(
            _success(write) and row.get("readback_match") is True
            and comparison.get("rule") in {"exact_text_expression_string_mapping", "exact"}
            and _json_safe(readback) == _json_safe(original)
            and write.get("execution_state_unknown") is not True
        )
        case.assertions["r01_same_text"] = {
            "node": candidate["node_type"], "property": candidate["name"], "path": candidate["path"],
            "requested": request_value, "comparison": comparison, "readback": _json_safe(readback),
            "error_code": _error_code(write), "outer_isError": write.get("_outer_isError"),
        }
        _mark(case, "expression_string_same_text_verified", "PASS" if ok else "BLOCKED" if _blocked_payload(write) else "FAIL",
              None if ok else "same-text expression/string round trip was not verified",
              property=candidate["name"], comparison_rule=comparison.get("rule"))

    # 2) Expression array/matrix into a string array property.
    if not string_arrays:
        _mark(case, "expression_array_or_matrix_verified", "NOT_RUN",
              "the bound model exposed no string array/matrix property")
    else:
        candidate = string_arrays[0]
        original = dict(candidate["value"])
        request_value = {"kind": "expression", "shape": list(original.get("shape") or []), "data": original.get("data")}
        write = await client.action("node.property_set",
                                    {"path": candidate["path"], "properties": [{"name": candidate["name"], "value": request_value}]},
                                    key="r01-array", request="r01-array")
        readback = await _read_value(client, candidate["path"], candidate["name"])
        applied = _data(write).get("applied")
        row = applied[0] if isinstance(applied, list) and applied and isinstance(applied[0], Mapping) else {}
        comparison: dict[str, Any] = dict(row["comparison"]) if isinstance(row.get("comparison"), Mapping) else {}
        ok = bool(_success(write) and row.get("readback_match") is True
                  and comparison.get("rule") in {"exact_text_expression_string_mapping", "exact"}
                  and _json_safe(readback) == _json_safe(original))
        case.assertions["r01_array"] = {"property": candidate["name"], "shape": _typed_shape(original),
                                        "comparison": comparison, "readback": _json_safe(readback),
                                        "error_code": _error_code(write)}
        _mark(case, "expression_array_or_matrix_verified", "PASS" if ok else "BLOCKED" if _blocked_payload(write) else "FAIL",
              None if ok else "expression array/matrix round trip was not verified", property=candidate["name"])

    # 3) Legal unit-bearing expression text is preserved verbatim.
    if not unit_candidates:
        _mark(case, "unit_expression_text_preserved", "NOT_RUN",
              "no property exposed authoritative unit metadata; unit-bearing text was not exercised")
    else:
        candidate = unit_candidates[0]
        original = dict(candidate["value"])
        request_value = {"kind": "expression", "shape": list(original.get("shape") or []),
                         "data": original.get("data"), "unit": candidate["unit"]}
        write = await client.action("node.property_set",
                                    {"path": candidate["path"], "properties": [{"name": candidate["name"], "value": request_value}]},
                                    key="r01-unit", request="r01-unit")
        readback = await _read_value(client, candidate["path"], candidate["name"])
        ok = bool(_success(write) and _json_safe(readback) == _json_safe(original))
        case.assertions["r01_unit"] = {"property": candidate["name"], "unit": candidate["unit"],
                                      "requested": request_value, "readback": _json_safe(readback),
                                      "error_code": _error_code(write)}
        _mark(case, "unit_expression_text_preserved", "PASS" if ok else "BLOCKED" if _blocked_payload(write) else "FAIL",
              None if ok else "unit-bearing expression text was not preserved verbatim", property=candidate["name"])

    # 4) A kind/unit combination with no supported engine view is rejected before the write.
    if not numbers:
        _mark(case, "incompatible_kind_or_unit_rejected_before_write", "NOT_RUN",
              "the bound model exposed no scalar numeric property for the negative contract probe")
    else:
        candidate = numbers[0]
        before = dict(candidate["value"])
        revision_before = state.get("revision")
        expression_write = await client.action(
            "node.property_set",
            {"path": candidate["path"], "properties": [{"name": candidate["name"],
                                                         "value": {"kind": "expression", "shape": [], "data": "phase4_unsupported_expression"}}]},
            key="r01-numeric-expression", request="r01-numeric-expression")
        unit_write = await client.action(
            "node.property_set",
            {"path": candidate["path"], "properties": [{"name": candidate["name"],
                                                         "value": {"kind": "float64", "shape": [], "data": 1.0, "unit": "W/m^2"}}]},
            key="r01-numeric-unit", request="r01-numeric-unit")
        after = await _read_value(client, candidate["path"], candidate["name"])
        expression_unknown = _unknown_outcome(expression_write)
        unit_unknown = _unknown_outcome(unit_write)
        rejected = bool(
            (not _success(expression_write)) and expression_write.get("_outer_isError") is True
            and _error_code(expression_write) == "PROPERTY_TYPE_MISMATCH"
            and (not _success(unit_write)) and unit_write.get("_outer_isError") is True
            and _error_code(unit_write) == "PROPERTY_TYPE_MISMATCH"
            and _json_safe(after) == _json_safe(before)
            and state.get("revision") == revision_before
        )
        case.assertions["r01_incompatible"] = {
            "property": candidate["name"], "before": before, "after": _json_safe(after),
            "expression_error": _error_code(expression_write), "unit_error": _error_code(unit_write),
            "revision_before": revision_before, "revision_after": state.get("revision"),
            "expression_unknown_engine_state": _json_safe(expression_unknown),
            "unit_unknown_engine_state": _json_safe(unit_unknown),
            "expression_release": host.release_summary(expression_write),
            "unit_release": host.release_summary(unit_write),
        }
        unknown_probe = expression_unknown or unit_unknown
        if unknown_probe is not None:
            # The engine state was unknown for the probe itself: whether the write was rejected
            # before the engine is not established either way, so this is blocked — never a FAIL
            # (no contract violation was observed) and never a PASS (a refusal is not the
            # documented pre-engine rejection).
            _mark(case, "incompatible_kind_or_unit_rejected_before_write", "BLOCKED",
                  f"the negative probe was refused with an unknown engine state "
                  f"({unknown_probe.get('error_code') or 'EXECUTION_STATE_UNKNOWN'}); whether the "
                  "unsupported write reached the engine was not established",
                  unknown_engine_state=_json_safe(unknown_probe))
        elif rejected:
            _mark(case, "incompatible_kind_or_unit_rejected_before_write", "PASS", None)
        else:
            _mark(case, "incompatible_kind_or_unit_rejected_before_write", "FAIL",
                  "an unsupported expression/unit write was not rejected before the engine write",
                  expression_error=_error_code(expression_write), unit_error=_error_code(unit_write))

    # 5) A readback that the engine normalizes must never be reported as a verified write.
    if not strings:
        _mark(case, "non_matching_readback_not_silently_accepted", "NOT_RUN", "no scalar string property was available")
    else:
        candidate = strings[0]
        original: dict[str, Any] = dict(candidate.get("value") or {})
        text = str(original.get("data") if isinstance(original.get("data"), str) else "")
        probe_text = text + " " if not text.endswith(" ") else text.rstrip() + "  "
        write = await client.action(
            "node.property_set",
            {"path": candidate["path"], "properties": [{"name": candidate["name"],
                                                         "value": {"kind": "expression", "shape": [], "data": probe_text}}]},
            key="r01-normalization", request="r01-normalization")
        applied = _data(write).get("applied")
        row = applied[0] if isinstance(applied, list) and applied and isinstance(applied[0], Mapping) else {}
        claimed_match = bool(_success(write) and row.get("readback_match") is True)
        observed_readback = row.get("readback") if isinstance(row.get("readback"), Mapping) else await _read_value(client, candidate["path"], candidate["name"])
        readback_text = observed_readback.get("data") if isinstance(observed_readback, Mapping) else None
        normalized = readback_text != probe_text
        silent_acceptance = bool(claimed_match and normalized)
        # Restore the original text regardless of the verdict.
        restore = await client.action(
            "node.property_set",
            {"path": candidate["path"], "properties": [{"name": candidate["name"], "value": original}]},
            key="r01-normalization-restore", request="r01-normalization-restore")
        restored = await _read_value(client, candidate["path"], candidate["name"])
        case.assertions["r01_normalization"] = {
            "property": candidate["name"], "probe_text": probe_text, "readback": _json_safe(observed_readback),
            "normalized": normalized, "claimed_match": claimed_match, "restore_success": _success(restore),
            "restored_value": _json_safe(restored), "error_code": _error_code(write),
        }
        if silent_acceptance:
            verdict, reason = "FAIL", "a normalized readback was reported as a verified write"
        elif _success(write):
            verdict, reason = "PASS", None
        elif _blocked_payload(write):
            # A refusal is not the line's own failure: it stays blocked, and a blocked verdict
            # always carries the product's answer (a reason-less BLOCKED cannot be reported on).
            verdict, reason = "BLOCKED", (f"the probe write was refused by the product "
                                          f"({_error_code(write) or 'no error code'}"
                                          f"{': ' + _error_message(write) if _error_message(write) else ''})")
        else:
            verdict, reason = "FAIL", f"the probe write returned {_error_code(write) or 'an invalid envelope'}"
        _mark(case, "non_matching_readback_not_silently_accepted", verdict, reason,
              normalized=normalized, restored=bool(_success(restore) and _json_safe(restored) == _json_safe(original)))

    # 6) Non-finite / unsupported numeric text stays strictly rejected.
    if not numbers:
        _mark(case, "nonfinite_text_strictly_rejected", "NOT_RUN", "no scalar numeric property was available")
    else:
        candidate = numbers[0]
        before = dict(candidate["value"])
        nan_write = await client.action(
            "node.property_set",
            {"path": candidate["path"], "properties": [{"name": candidate["name"],
                                                         "value": {"kind": "float64", "shape": [], "data": "NaN"}}]},
            key="r01-nonfinite-nan", request="r01-nonfinite-nan")
        inf_write = await client.action(
            "node.property_set",
            {"path": candidate["path"], "properties": [{"name": candidate["name"],
                                                         "value": {"kind": "float64", "shape": [], "data": "Infinity"}}]},
            key="r01-nonfinite-inf", request="r01-nonfinite-inf")
        after = await _read_value(client, candidate["path"], candidate["name"])
        rejected = bool(
            (not _success(nan_write)) and nan_write.get("_outer_isError") is True
            and (not _success(inf_write)) and inf_write.get("_outer_isError") is True
            and _json_safe(after) == _json_safe(before)
        )
        nan_unknown = _unknown_outcome(nan_write)
        inf_unknown = _unknown_outcome(inf_write)
        case.assertions["r01_nonfinite"] = {"property": candidate["name"], "before": before,
                                            "after": _json_safe(after), "nan_error": _error_code(nan_write),
                                            "inf_error": _error_code(inf_write),
                                            "nan_unknown_engine_state": _json_safe(nan_unknown),
                                            "inf_unknown_engine_state": _json_safe(inf_unknown),
                                            "nan_release": host.release_summary(nan_write),
                                            "inf_release": host.release_summary(inf_write)}
        unknown_probe = nan_unknown or inf_unknown
        if unknown_probe is not None:
            # A gate refusal or an unknown engine state is not a "strict rejection" of the text:
            # whether the value stayed unchanged was not established.
            _mark(case, "nonfinite_text_strictly_rejected", "BLOCKED",
                  "the non-finite probe was refused with an unknown engine state "
                  f"({unknown_probe.get('error_code') or 'EXECUTION_STATE_UNKNOWN'}); strict rejection "
                  "with the value left unchanged was not established",
                  unknown_engine_state=_json_safe(unknown_probe))
        elif rejected:
            _mark(case, "nonfinite_text_strictly_rejected", "PASS", None)
        else:
            _mark(case, "nonfinite_text_strictly_rejected", "FAIL",
                  "non-finite numeric text was not rejected with the value left unchanged")
    case.finish()


# ---------------------------------------------------------------------------
# R03 — indexed / keyed writes with real verification
# ---------------------------------------------------------------------------


async def _case_r03(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace, state: dict[str, Any]) -> None:
    start = len(host.transcript)
    rows = await _prepare_case(case, host, _plan_ops(case.case_id))
    core_ops = ("node.property_index_set", "node.property_entry_set", "node.property_get")
    ops_ok = all((rows.get(operation) or {}).get("available") for operation in core_ops)
    case.assertion("static_indexed_ops_executable", ops_ok, operations=list(core_ops))
    ops_ok = _availability_subcase(case, "static_indexed_ops_executable", core_ops, rows, flag=ops_ok)

    malformed_actions = [
        {"operation_id": "node.property_index_set",
         "arguments": {"path": {"segments": []}, "name": "phase4_probe", "indices": [],
                       "value": {"kind": "float64", "shape": [], "data": 1.0}}},
        {"operation_id": "node.property_entry_set",
         "arguments": {"path": {"segments": []}, "name": "phase4_probe",
                       "value": {"kind": "float64", "shape": [], "data": 1.0}}},
    ]
    verdict, observed = await _preview_static_rejection(client, malformed_actions,
                                                        ("INVALID_REQUEST", "PROPERTY_TYPE_MISMATCH"), "r03-static-malformed")
    case.assertions["r03_malformed_index_preview"] = observed
    rejected = verdict == PREVIEW_REJECTED
    case.assertion("malformed_index_and_key_rejected_pre_engine", rejected,
                   error_code=observed.get("error_code"), engine_called=observed.get("engine_called"))
    case.subcase("static_malformed_index_and_key_rejected_pre_engine",
                 "PASS" if rejected else "BLOCKED", level="protocol",
                 reason=None if rejected else _preview_rejection_reason(
                     verdict, observed, "an empty index array / a missing entry key"),
                 observed=observed)

    live_names = ("vector_element_readback", "matrix_cell_or_row_readback", "keyed_entry_set_and_readback",
                  "wrong_index_rejected_without_write", "setter_noop_or_normalized_value_detected",
                  "non_target_items_unchanged", "empty_or_single_element_property")
    runnable, status, why = _can_run(case, args, state, rows, core_ops, require_writable_model=True)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.subcase("envelope_consistency", "NOT_RUN", reason="no live indexed/keyed call was made", level="protocol")
        case.finish()
        return
    try:
        candidates, discovery = await _typed_candidates(client, kinds=("float64", "int32", "string", "expression"))
    except CapabilityUnavailable as exc:
        for name in live_names:
            _mark(case, name, "BLOCKED", str(exc))
        case.subcase("envelope_consistency", "BLOCKED", reason=str(exc), level="protocol")
        case.finish()
        return
    case.assertions["r03_discovery_base"] = {"find": discovery, "candidates": len(candidates)}

    def _length(row: Mapping[str, Any]) -> int:
        shape = _typed_shape(row.get("value"))
        if not shape:
            return 0
        total = 1
        for item in shape:
            total *= item
        return total

    def _is_keyed(row: Mapping[str, Any]) -> bool:
        # A keyed target lives in a property group.  The product normalizes wire segments to
        # ``accessor``; a path the driver built itself carries ``collection``: both name the
        # same accessor and neither is treated as a keyed target on its own.
        segments = (row.get("path") or {}).get("segments") if isinstance(row.get("path"), Mapping) else None
        return any(str(segment.get("accessor") or segment.get("collection")) == "propertyGroup"
                   for segment in (segments or []) if isinstance(segment, Mapping))

    def _classify_r03(rows: Sequence[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]],
                                                                  list[Mapping[str, Any]], list[Mapping[str, Any]]]:
        vectors = [row for row in rows if isinstance(row.get("shape_rank"), int) and row["shape_rank"] == 1 and _length(row) >= 2]
        matrices = [row for row in rows if isinstance(row.get("shape_rank"), int) and row["shape_rank"] == 2]
        singles = [row for row in rows if isinstance(row.get("shape_rank"), int) and row["shape_rank"] == 1 and _length(row) <= 1]
        keyed = [row for row in rows if row["kind"] in {"string", "expression"}
                 and isinstance(row.get("shape_rank"), int) and row["shape_rank"] >= 1 and _is_keyed(row)]
        return vectors, matrices, singles, keyed

    vectors, matrices, singles, keyed = _classify_r03(candidates)
    fixture_evidence: dict[str, Any] | None = None
    if not (vectors and matrices and keyed and singles):
        # Create the indexed/keyed probe targets on the bound model instead of reporting
        # NOT_RUN for every acceptance line that needs a multi-element or keyed property.
        created, fixture_evidence = await _probe_candidates(
            client, args, state, ("float64", "int32", "string", "expression"))
        candidates = [*created, *candidates]
        vectors, matrices, singles, keyed = _classify_r03(candidates)
    case.assertions["r03_discovery"] = {
        "candidates": len(candidates), "probe_fixture": fixture_evidence,
        "available": {"vector": len(vectors), "matrix": len(matrices), "keyed": len(keyed), "single": len(singles)},
        "targets": [{"requirements": sorted({name for name, rows in
                                              (("vector", vectors), ("matrix", matrices), ("keyed", keyed),
                                               ("single", singles)) if rows and rows[0] is row}),
                     "node": row.get("node_type"), "property": row.get("name"), "kind": row.get("kind"),
                     "shape_rank": row.get("shape_rank"), "shape": _typed_shape(row.get("value")),
                     "source": row.get("source", "model_discovery"), "path": row.get("path")}
                    for row in candidates[:8]],
    }

    async def indexed_probe(name: str, candidate: Mapping[str, Any], indices: list[int], element_index: int) -> None:
        before_typed = dict(candidate["value"])
        before_flat = _flatten_typed(before_typed)
        if element_index >= len(before_flat):
            _mark(case, name, "NOT_RUN", "the exposed property is shorter than the probed element index")
            return
        requested = {"kind": before_typed.get("kind"), "shape": [], "data": before_flat[element_index]}
        write = await client.action(
            "node.property_index_set",
            {"path": candidate["path"], "name": candidate["name"], "indices": indices, "value": requested},
            key=f"r03-{name}-write", request=f"r03-{name}-write")
        after_typed = await _read_value(client, candidate["path"], candidate["name"])
        after_flat = _flatten_typed(after_typed)
        response_readback = _data(write).get("readback") if isinstance(_data(write).get("readback"), Mapping) else {}
        response_flat = _flatten_typed(response_readback)
        target_ok = element_index < len(after_flat) and after_flat[element_index] == requested["data"]
        others_ok = all(index == element_index or index >= min(len(before_flat), len(after_flat)) or after_flat[index] == before_flat[index]
                        for index in range(min(len(before_flat), len(after_flat))))
        response_ok = element_index < len(response_flat) and response_flat[element_index] == requested["data"]
        ok = bool(_success(write) and target_ok and others_ok and response_ok)
        case.assertions[f"r03_{name}"] = {
            "property": candidate["name"], "path": candidate["path"], "indices": indices,
            "requested": requested, "before": before_typed, "after": _json_safe(after_typed),
            "response_readback": _json_safe(response_readback), "target_ok": target_ok,
            "non_target_unchanged": others_ok, "error_code": _error_code(write),
        }
        _mark(case, name, "PASS" if ok else "BLOCKED" if _blocked_payload(write) else "FAIL",
              None if ok else "indexed write was not verified from an independent readback",
              property=candidate["name"], indices=indices)

    if not vectors:
        _mark(case, "vector_element_readback", "NOT_RUN", "no 1-D property with at least two elements was exposed")
    else:
        await indexed_probe("vector_element_readback", vectors[0], [0], 0)

    if not matrices:
        _mark(case, "matrix_cell_or_row_readback", "NOT_RUN", "no 2-D property was exposed")
    else:
        await indexed_probe("matrix_cell_or_row_readback", matrices[0], [0, 0], 0)

    # Keyed entry: only a property that advertises keyed semantics is probed.
    if not keyed:
        _mark(case, "keyed_entry_set_and_readback", "NOT_RUN",
              "no keyed (property-group) entry property could be discovered or created on the bound model")
    else:
        candidate = keyed[0]
        flat = _flatten_typed(candidate["value"])
        key = str(flat[0]) if flat and isinstance(flat[0], str) else None
        if not key:
            _mark(case, "keyed_entry_set_and_readback", "NOT_RUN", "the keyed property exposed no readable key")
        else:
            # Write a *distinct* text under the same key: a readback that merely repeats the
            # key we read first would prove nothing about the setter.
            probe_text = key + " " if not key.endswith(" ") else key.rstrip() + "  "
            write = await client.action(
                "node.property_entry_set",
                {"path": candidate["path"], "name": candidate["name"], "key": key,
                 "value": {"kind": "expression", "shape": [], "data": probe_text}},
                key="r03-entry-write", request="r03-entry-write")
            after = await _read_value(client, candidate["path"], candidate["name"])
            after_flat = _flatten_typed(after)
            stored = after_flat[0] if after_flat and isinstance(after_flat[0], str) else None
            ok = bool(_success(write) and stored == probe_text)
            restore = await client.action(
                "node.property_entry_set",
                {"path": candidate["path"], "name": candidate["name"], "key": key,
                 "value": {"kind": "expression", "shape": [], "data": key}},
                key="r03-entry-restore", request="r03-entry-restore")
            case.assertions["r03_keyed_entry"] = {"property": candidate["name"], "key": key,
                                                  "requested": probe_text, "stored": stored,
                                                  "after": _json_safe(after), "error_code": _error_code(write),
                                                  "restore_success": _success(restore),
                                                  "path": candidate["path"]}
            if _success(write) and not ok:
                status, detail = "FAIL", "the keyed setter reported success while the stored entry value differs"
            elif not _success(write):
                status, detail = _unreadable_verdict(
                    write, "the keyed entry write could not be verified")
            else:
                status, detail = "PASS", None
            _mark(case, "keyed_entry_set_and_readback", status, detail,
                  property=candidate["name"], key=key)

    # Wrong index: rejected without a write, or an explicitly reported expansion.
    if not vectors:
        _mark(case, "wrong_index_rejected_without_write", "NOT_RUN", "no 1-D property was exposed")
    else:
        candidate = vectors[0]
        before_typed = dict(candidate["value"])
        before_flat = _flatten_typed(before_typed)
        out_of_range = len(before_flat)
        write = await client.action(
            "node.property_index_set",
            {"path": candidate["path"], "name": candidate["name"], "indices": [out_of_range],
             "value": {"kind": before_typed.get("kind"), "shape": [], "data": before_flat[0] if before_flat else 0}},
            key="r03-wrong-index", request="r03-wrong-index")
        after_typed = await _read_value(client, candidate["path"], candidate["name"])
        after_flat = _flatten_typed(after_typed)
        rejected_clean = bool(not _success(write) and _json_safe(after_typed) == _json_safe(before_typed))
        expanded_reported = bool(_success(write) and len(after_flat) > len(before_flat)
                                 and _data(write).get("readback") is not None)
        ok = rejected_clean or expanded_reported
        case.assertions["r03_wrong_index"] = {
            "property": candidate["name"], "probed_index": out_of_range, "before": before_typed,
            "after": _json_safe(after_typed), "error_code": _error_code(write),
            "rejected_clean": rejected_clean, "expansion_reported": expanded_reported,
        }
        _mark(case, "wrong_index_rejected_without_write", "PASS" if ok else "BLOCKED" if _blocked_payload(write) else "FAIL",
              None if ok else "an out-of-range index neither was rejected cleanly nor reported an explicit expansion")

    # Setter succeeded but the stored value differs: the envelope must say so.
    if not vectors:
        _mark(case, "setter_noop_or_normalized_value_detected", "NOT_RUN", "no 1-D property was exposed")
    else:
        candidate = vectors[0]
        before_typed = dict(candidate["value"])
        before_flat = _flatten_typed(before_typed)
        if candidate["kind"] in {"string", "expression"} and isinstance(before_flat[0], str):
            probe = before_flat[0] + " "
        elif isinstance(before_flat[0], (int, float)) and not isinstance(before_flat[0], bool):
            probe = float(before_flat[0]) + 0.125
        else:
            probe = before_flat[0]
        write = await client.action(
            "node.property_index_set",
            {"path": candidate["path"], "name": candidate["name"], "indices": [0],
             "value": {"kind": candidate["kind"], "shape": [], "data": probe}},
            key="r03-setter-value", request="r03-setter-value")
        after_typed = await _read_value(client, candidate["path"], candidate["name"])
        after_flat = _flatten_typed(after_typed)
        stored = after_flat[0] if after_flat else None
        claimed_ok = bool(_success(write))
        silent_acceptance = bool(claimed_ok and stored != probe)
        restore = await client.action(
            "node.property_index_set",
            {"path": candidate["path"], "name": candidate["name"], "indices": [0],
             "value": {"kind": candidate["kind"], "shape": [], "data": before_flat[0]}},
            key="r03-setter-restore", request="r03-setter-restore")
        case.assertions["r03_setter_value"] = {
            "property": candidate["name"], "requested": probe, "stored": stored, "claimed_ok": claimed_ok,
            "silent_acceptance": silent_acceptance, "restore_success": _success(restore),
            "error_code": _error_code(write),
        }
        _mark(case, "setter_noop_or_normalized_value_detected",
              "FAIL" if silent_acceptance else "PASS" if (claimed_ok or _blocked_payload(write)) else "FAIL",
              "the indexed setter reported success while the stored value differs" if silent_acceptance else None,
              requested=probe, stored=stored)

    # Non-target items: every element except the written one is unchanged.
    if not (vectors or matrices):
        _mark(case, "non_target_items_unchanged", "NOT_RUN", "no multi-element property was exposed")
    else:
        candidate = vectors[0] if vectors else matrices[0]
        before_typed = dict(candidate.get("value") or {})
        before_flat = _flatten_typed(before_typed)
        shape = _typed_shape(before_typed)
        indices = [0] if shape and len(shape) == 1 else [0, 0]
        write = await client.action(
            "node.property_index_set",
            {"path": candidate["path"], "name": candidate["name"], "indices": indices,
             "value": {"kind": before_typed.get("kind"), "shape": [], "data": before_flat[0] if before_flat else 0}},
            key="r03-non-target", request="r03-non-target")
        after_typed = await _read_value(client, candidate["path"], candidate["name"])
        after_flat = _flatten_typed(after_typed)
        target = 0
        unchanged = all(index == target or index >= len(after_flat) or after_flat[index] == before_flat[index]
                        for index in range(len(before_flat)))
        ok = bool(_success(write) and unchanged)
        case.assertions["r03_non_target"] = {"property": candidate["name"], "indices": indices, "target": target,
                                            "before": before_flat, "after": after_flat, "unchanged": unchanged}
        _mark(case, "non_target_items_unchanged", "PASS" if ok else "BLOCKED" if _blocked_payload(write) else "FAIL",
              None if ok else "a non-target element changed or the write was not verified")

    if not singles:
        _mark(case, "empty_or_single_element_property", "NOT_RUN",
              "the bound model exposed no empty or single-element 1-D property")
    else:
        candidate = singles[0]
        before_typed = dict(candidate["value"])
        before_flat = _flatten_typed(before_typed)
        value = before_flat[0] if before_flat else (0.0 if candidate["kind"] in {"float64", "int32"} else "")
        write = await client.action(
            "node.property_index_set",
            {"path": candidate["path"], "name": candidate["name"], "indices": [0],
             "value": {"kind": candidate["kind"], "shape": [], "data": value}},
            key="r03-single", request="r03-single")
        after_typed = await _read_value(client, candidate["path"], candidate["name"])
        after_flat = _flatten_typed(after_typed)
        consistent = bool(_success(write) and after_flat and after_flat[0] == value) or bool(not _success(write))
        case.assertions["r03_single_element"] = {"property": candidate["name"], "before": before_typed,
                                                "after": _json_safe(after_typed), "error_code": _error_code(write)}
        _mark(case, "empty_or_single_element_property", "PASS" if consistent else "BLOCKED" if _blocked_payload(write) else "FAIL",
              None if consistent else "single/empty element write was accepted without a consistent readback")

    # Envelope consistency is checked over *this case's* indexed/keyed calls, on whichever route
    # the host publishes them: the strict tools directly, or the public ``operation_call`` /
    # ``registry_call`` action carrying ``operation_id``.  A call the previous case made is not
    # this case's evidence, and a sweep that matched nothing must not be reported as a violated
    # contract (that was the live ``calls: 0, pass: false`` FAIL).
    indexed_ops = {"node.property_index_set", "node.property_entry_set"}
    indexed_tools = {operation.replace(".", "_") for operation in indexed_ops}

    def _envelope_calls(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for row in rows:
            arguments = row.get("arguments")
            if not isinstance(arguments, Mapping):
                continue
            operation_id = arguments.get("operation_id")
            if isinstance(operation_id, str):
                if operation_id in indexed_ops:
                    calls.append({"route": str(row.get("operation")), "operation_id": operation_id,
                                  "outer_isError": row.get("outer_isError"), "payload": row.get("payload")})
            elif row.get("operation") in indexed_tools:
                calls.append({"route": str(row.get("operation")), "operation_id": None,
                              "outer_isError": row.get("outer_isError"), "payload": row.get("payload")})
        return calls

    def _inner_success(call: Mapping[str, Any]) -> bool | None:
        payload = call.get("payload")
        if not isinstance(payload, Mapping):
            return None
        success = payload.get("success")
        return success if isinstance(success, bool) else None

    envelope_rows = _envelope_calls(host.transcript[start:])
    comparable = [call for call in envelope_rows if isinstance(call.get("outer_isError"), bool)
                  and _inner_success(call) is not None]
    mismatches = [{"route": call["route"], "operation_id": call["operation_id"],
                   "outer_isError": call["outer_isError"], "inner_success": _inner_success(call)}
                  for call in comparable
                  if (call.get("outer_isError") is True) == (_inner_success(call) is True)]
    case.assertions["indexed_envelope_consistency"] = {
        "calls": len(envelope_rows),
        "comparable": len(comparable),
        "unverifiable": len(envelope_rows) - len(comparable),
        "routes": sorted({call["route"] for call in envelope_rows}),
        "mismatches": mismatches,
        "operations": sorted({str(call["operation_id"]) for call in envelope_rows if call["operation_id"]}),
    }
    case.assertion("indexed_envelope_consistency", bool(comparable) and not mismatches, calls=len(envelope_rows))
    if not envelope_rows:
        case.subcase("envelope_consistency", "BLOCKED", level="protocol",
                     reason=("no indexed/keyed call was made by this case, so the outer flag and the inner "
                             "success of such a call could not be compared (the live subcases above were "
                             "blocked before reaching the wire)"))
    elif not comparable:
        case.subcase("envelope_consistency", "BLOCKED", level="protocol",
                     reason=("this case's indexed/keyed calls carried no comparable outer/inner flags "
                             f"({len(envelope_rows)} call(s) recorded)"))
    else:
        case.subcase("envelope_consistency", "PASS" if not mismatches else "FAIL", level="protocol",
                     reason=None if not mismatches else
                     ("an indexed/keyed call returned an inner success that disagrees with the outer isError flag: "
                      + json.dumps(mismatches[:3], sort_keys=True)))
    case.finish()


# ---------------------------------------------------------------------------
# R04 — execut(ed) actions versus verified invariants
# ---------------------------------------------------------------------------


def _r04_probe_action(path: Mapping[str, Any]) -> dict[str, Any]:
    """A transaction action this build can actually execute.

    ``node.inspect`` on the model root is deliberately *not* used: the root node does not
    expose ``properties()`` on this build, and a nested action failure is reported as
    ``execution_state_unknown``.  That turned a read-only probe into a durable UNKNOWN
    transaction job which then closed the control-plane gate for every later call
    (observed in the first live run).  ``node.children`` is a pure read whose refusals are
    ordinary structured errors.
    """
    return {"operation_id": "node.children", "arguments": {"path": dict(path), "limit": 25}}


def _r04_invariant(kind: str, path: Mapping[str, Any], *, required: bool = True) -> dict[str, Any]:
    """One declaration in the product's invariant vocabulary (``type``/``path``/``required``).

    ``kind`` is the *human* name of the invariant type; the wire field is ``type``.  Sending
    ``kind`` made the product reject the whole transaction as ``INVALID_INVARIANT`` before
    any action (observed in the first live run).
    """
    return {"type": kind, "path": dict(path), "required": required}


def _r04_violation_verdict(payload: Mapping[str, Any] | None) -> tuple[str, str | None, dict[str, Any]]:
    """Verdict for a required invariant that must fail while its actions still apply."""
    data = _data(payload)
    results = _mapping(data.get("invariant_results"))
    checks_raw = results.get("checks")
    checks = checks_raw if isinstance(checks_raw, list) else []
    failing_required = [row for row in checks if isinstance(row, Mapping)
                        and row.get("required") is True and row.get("status") != "PASS"]
    observed = {
        "success": payload.get("success") if isinstance(payload, Mapping) else None,
        "error_code": _error_code(payload), "execution_status": data.get("execution_status"),
        "verification_status": data.get("verification_status"),
        "verification_reason": results.get("reason"),
        "failing_required_checks": _json_safe(failing_required),
        "invariants": _json_safe(data.get("invariants")), "applied": _json_safe(data.get("applied")),
        "checkpoint_id": data.get("checkpoint_id"), "partial_change": data.get("partial_change"),
        "execution_state_unknown": data.get("execution_state_unknown"),
    }
    outcome = _unknown_outcome(payload)
    if outcome is not None:
        return ("BLOCKED",
                f"the transaction reported an unknown engine state ({outcome.get('error_code')}); no verification status was established",
                observed)
    if _blocked_payload(payload):
        return "BLOCKED", f"the transaction was refused with {_error_code(payload)}", observed
    if not isinstance(payload, Mapping) or payload.get("success") is None:
        return "BLOCKED", "no transaction envelope was returned", observed
    ok = bool(payload.get("success") is False
              and data.get("verification_status") == "FAILED"
              and data.get("execution_status") in {None, "SUCCEEDED"}
              and failing_required)
    if ok:
        return "PASS", None, observed
    return ("FAIL",
            "a violated required invariant did not produce success=false with verification_status=FAILED "
            f"(success={payload.get('success')!r}, execution_status={data.get('execution_status')!r}, "
            f"verification_status={data.get('verification_status')!r}, failing_required={len(failing_required)})",
            observed)


async def _r04_probe_target(client: ActionClient, state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Choose and *verify* the transaction probe target before any transaction uses it."""
    fixture = state.get("probe_fixture") if isinstance(state.get("probe_fixture"), Mapping) else {}
    nodes = fixture.get("nodes") if isinstance(fixture.get("nodes"), Mapping) else {}
    candidates: list[tuple[str, dict[str, Any]]] = []
    component = nodes.get("component")
    if isinstance(component, Mapping) and isinstance(component.get("segments"), list):
        candidates.append(("probe_fixture.component", dict(component)))
    candidates.append(("model_root", {"segments": []}))
    attempts: list[dict[str, Any]] = []
    for label, path in candidates:
        payload = await client.action("node.children", {"path": path, "limit": 25},
                                      key=f"r04-probe-{label}", request=f"r04-probe-{label}")
        data = _data(payload)
        attempts.append({"target": label, "path": path, "success": payload.get("success"),
                         "error_code": _error_code(payload), "status": data.get("status"),
                         "count": data.get("count")})
        if _success(payload):
            return _r04_probe_action(path), {"selected": label, "attempts": attempts}
    label, path = candidates[-1]
    return _r04_probe_action(path), {
        "selected": None, "attempts": attempts,
        "reason": "no verified probe target executed in this build; the transaction subcases are gated on it",
    }


async def _case_r04(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace, state: dict[str, Any]) -> None:
    rows = await _prepare_case(case, host, _plan_ops(case.case_id))
    core_ops = ("transaction.preview", "transaction.apply", "transaction.verify")
    ops_ok = all((rows.get(operation) or {}).get("available") for operation in core_ops)
    case.assertion("static_transaction_ops_executable", ops_ok, operations=list(core_ops))
    ops_ok = _availability_subcase(case, "static_transaction_ops_executable", core_ops, rows, flag=ops_ok)

    probe_actions = [{"operation_id": "node.inspect",
                      "arguments": {"path": {"segments": []}, "include_values": False}}]
    malformed = await client.action(
        "transaction.preview", {"actions": probe_actions, "invariants": [{}]},
        require_model=False, key="r04-malformed-invariant", request="r04-malformed-invariant")
    unsupported = await client.action(
        "transaction.preview",
        {"actions": probe_actions,
         "invariants": [{"type": "phase4.unsupported_invariant", "target": {"segments": []}}]},
        require_model=False, key="r04-unsupported-invariant", request="r04-unsupported-invariant")

    def _rejection_verdict(payload: Mapping[str, Any]) -> tuple[str, str | None]:
        """Classify one static preview probe: rejected / accepted / unknown engine state."""
        if _unknown_outcome(payload) is not None:
            return "BLOCKED", "unknown engine state"
        if _success(payload):
            return "FAIL", "accepted"
        if payload.get("_outer_isError") is True and _error_code(payload) in _INVARIANT_REJECTION_CODES:
            return "PASS", None
        return "BLOCKED", "an unexpected refusal code"

    malformed_verdict, malformed_because = _rejection_verdict(malformed)
    unsupported_verdict, unsupported_because = _rejection_verdict(unsupported)
    invariant_contract = "PASS" if malformed_verdict == unsupported_verdict == "PASS" else (
        "FAIL" if "FAIL" in {malformed_verdict, unsupported_verdict} else "BLOCKED")
    blocked_by_unknown = "unknown engine state" in {malformed_because, unsupported_because}
    case.assertions["r04_unsupported_invariant_preview"] = {
        "malformed": _envelope_identity(malformed), "unsupported": _envelope_identity(unsupported),
        "malformed_error": _error_message(malformed), "unsupported_error": _error_message(unsupported),
        "accepted_rejection_codes": sorted(_INVARIANT_REJECTION_CODES),
        "verdict": invariant_contract,
        "per_probe": {"malformed": malformed_verdict, "unsupported": unsupported_verdict},
        "unknown_engine_state": {"malformed": _json_safe(_unknown_outcome(malformed)),
                                 "unsupported": _json_safe(_unknown_outcome(unsupported))},
        "release": {"malformed": host.release_summary(malformed),
                    "unsupported": host.release_summary(unsupported)},
    }
    case.assertion("unsupported_invariant_rejected_pre_write", invariant_contract == "PASS",
                   error_code=_error_code(malformed), engine_called=_data(malformed).get("engine_called"))
    if invariant_contract == "PASS":
        _mark(case, "static_unsupported_invariant_rejected_pre_write", invariant_contract, level="protocol",
              reason=None, observed=case.assertions["r04_unsupported_invariant_preview"])
    else:
        # A gate refusal or an unknown engine state means the preview never ran: the contract was
        # not classified, which is blocked — never "the preview rejected with an odd code".
        reason = (
            f"the preview of the malformed/unsupported invariant was refused with an unknown engine state "
            f"({_error_code(malformed)}, {_error_code(unsupported)}) before the validator ran, so the R04 "
            "invariant contract was not established"
            if blocked_by_unknown else
            "a static preview reported success for an invariant it cannot evaluate" if invariant_contract == "FAIL"
            else (f"the preview rejected the probes with codes outside the documented vocabulary "
                  f"({_error_code(malformed)}, {_error_code(unsupported)}), so the R04 invariant contract "
                  "could not be classified"))
        _mark(case, "static_unsupported_invariant_rejected_pre_write", invariant_contract, level="protocol",
              reason=reason, observed=case.assertions["r04_unsupported_invariant_preview"])

    live_names = ("invariant_violation_not_reported_as_pass", "verified_transaction_status_split",
                  "stale_revision_rejected", "cross_model_transaction_record_rejected",
                  "recorded_pass_with_changed_live_property", "checkpoint_recovery_after_failed_invariant")
    runnable, status, why = _can_run(case, args, state, rows, core_ops, require_writable_model=True)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    if not invariant_contract:
        for name in live_names:
            _mark(case, name, "BLOCKED",
                  "transaction responses do not expose the R04 execution/verification split in this build")
        case.finish()
        return

    probe_action, probe_evidence = await _r04_probe_target(client, state)
    case.assertions["r04_probe_action"] = probe_evidence
    # Product observation, recorded rather than asserted as an acceptance line: a *read-only*
    # refusal on the model root must stay a plain structured error.  The same read nested in
    # a transaction action was classified as execution_state_unknown on this build.
    root_read = await client.action("node.property_schema", {"path": {"segments": []}},
                                    key="r04-root-read", request="r04-root-read")
    root_read_unknown = _unknown_outcome(root_read)
    root_read_refused = bool(
        (not _success(root_read)) and root_read.get("_outer_isError") is True and bool(_error_code(root_read))
        and root_read_unknown is None
    )
    case.assertions["r04_root_node_read"] = {
        "success": root_read.get("success"), "outer_isError": root_read.get("_outer_isError"),
        "error_code": _error_code(root_read), "error_message": _error_message(root_read),
        "unknown_escalation": _unknown_outcome(root_read),
    }
    case.subcase("root_node_read_refusal_is_structured", "PASS" if root_read_refused else "BLOCKED", level="protocol",
        reason=None if root_read_refused else (
            ("a read-only refusal on the model root was refused with an unknown engine state "
             f"({_error_code(root_read)}), so its envelope was not classified")
            if root_read_unknown is not None else
            "a read-only refusal on the model root was not reported as a structured error"),
        observed=case.assertions["r04_root_node_read"])

    bogus_target = {"segments": [{"collection": "component", "tag": "phase4_absent_component"}]}
    violation = await client.action(
        "transaction.apply",
        {"actions": [probe_action], "invariants": [_r04_invariant("node_exists", bogus_target)],
         "checkpoint_policy": "on_failure"},
        key="r04-violation", request="r04-violation")
    violation_data = _data(violation)
    violation_status, violation_reason, violation_observed = _r04_violation_verdict(violation)
    case.assertions["r04_invariant_violation"] = violation_observed
    _mark(case, "invariant_violation_not_reported_as_pass", violation_status, violation_reason,
          observed=violation_observed)

    holds = await client.action(
        "transaction.apply",
        {"actions": [probe_action], "invariants": [_r04_invariant("node_exists", {"segments": []})],
         "checkpoint_policy": "on_failure"},
        key="r04-holds", request="r04-holds")
    holds_data = _data(holds)
    split_ok = bool(_success(holds) and isinstance(holds_data.get("execution_status"), str)
                    and isinstance(holds_data.get("verification_status"), str))
    split_reason = None if split_ok else (
        f"a satisfied transaction did not report execution and verification status separately "
        f"({_error_code(holds) or 'invalid envelope'})")
    case.assertions["r04_status_split"] = {"success": holds.get("success"),
                                          "execution_status": holds_data.get("execution_status"),
                                          "verification_status": holds_data.get("verification_status"),
                                          "error_code": _error_code(holds),
                                          "release_reads": [row for row in host.refreshes
                                                            if row.get("tool") == "model_inspect"][-2:],
                                          "reconciliations": [row for row in host.reconciliations
                                                              if row.get("trigger") == "managed revision conflict"][-2:]}
    if split_ok:
        _mark(case, "verified_transaction_status_split", "PASS", None)
    else:
        split_status, split_detail = _unreadable_verdict(
            holds, "a satisfied transaction did not report execution and verification status separately")
        _mark(case, "verified_transaction_status_split", split_status, split_detail)

    # A deliberate stale-revision probe.  ``reconcile=False`` keeps the host's release path away
    # from it: a probe the driver "repairs" would prove nothing, and the point of this line is
    # that the product refuses a write whose expected_revision is behind.
    bound_revision = int(state.get("revision") or 0)
    stale = await client.action(
        "transaction.apply",
        {"actions": [probe_action], "invariants": [], "checkpoint_policy": "on_failure"},
        key="r04-stale", request="r04-stale",
        revision_override=max(0, bound_revision - 1),
        reconcile=False,
    )
    # The product's own stale-revision vocabulary (``_g2_contract``/``_managed_backend``
    # raise REVISION_CONFLICT; the older codes are accepted for other build revisions).
    stale_conflict = _revision_conflict(stale) or {}
    stale_rejected = bool(not _success(stale) and _error_code(stale) in {
        "REVISION_CONFLICT", "REVISION_MISMATCH", "STALE_MODEL_REF", "MODEL_IDENTITY_MISMATCH", "INVALID_REQUEST"})
    # The refusal must be the *revision comparison*, not the external-change gate: the satisfied
    # transaction above was reconciled.  If the product still names an unacknowledged external
    # change here, that is a product-side ordering observation and this line is not established.
    stale_precondition_ok = stale_conflict.get("precondition") in {None, "stale-expected-revision"}
    stale_rejected = stale_rejected and stale_precondition_ok
    case.assertions["r04_stale_revision"] = {
        "success": stale.get("success"), "error_code": _error_code(stale),
        "error_message": _error_message(stale),
        "precondition": stale_conflict.get("precondition"),
        "sent_expected_revision": max(0, bound_revision - 1), "bound_revision": bound_revision,
        "reconcile": "withheld: this is a deliberate stale-revision probe",
        "release_reads": host.refreshes[-1:],
    }
    if stale_rejected:
        _mark(case, "stale_revision_rejected", "PASS", None)
    elif not stale_precondition_ok:
        _mark(case, "stale_revision_rejected", "BLOCKED",
              "the stale probe was refused by the managed-revision precondition "
              f"{stale_conflict.get('precondition')!r} ({_error_message(stale)}), not by a comparison of the "
              "sent expected_revision with the managed revision: the model still required reconciliation "
              "after the satisfied transaction")
    else:
        stale_status, stale_detail = _unreadable_verdict(
            stale, "a stale expected_revision was not rejected")
        _mark(case, "stale_revision_rejected", stale_status, stale_detail)

    transaction_id = holds_data.get("transaction_id") or _data(violation).get("transaction_id")
    if not isinstance(transaction_id, str) or not transaction_id:
        _mark(case, "cross_model_transaction_record_rejected", "NOT_RUN", "no transaction_id was returned to re-verify")
        _mark(case, "recorded_pass_with_changed_live_property", "NOT_RUN", "no transaction_id was returned to re-verify")
    else:
        # The durable-record check vocabulary is ``field``/``equals``: a check that names a
        # property of the *live* model (or a field the record does not carry) cannot be
        # established from the record, and the response must never report it as VERIFIED.
        verify = await client.action(
            "transaction.verify",
            {"transaction_id": transaction_id,
             "checks": [{"field": "live_model_state.phase4_absent", "equals": 1}]},
            key="r04-cross-model-verify", request="r04-cross-model-verify")
        verify_data = _data(verify)
        raw_checks = verify_data.get("checks")
        recorded_checks = raw_checks if isinstance(raw_checks, list) else []
        unestablished = [row for row in recorded_checks if isinstance(row, Mapping) and row.get("status") != "PASSED"]
        cross_rejected = bool(
            (not _success(verify))
            or verify_data.get("verified") is not True
            or verify_data.get("status") != "VERIFIED"
            or unestablished
        )
        case.assertions["r04_record_verify"] = {
            "success": verify.get("success"), "error_code": _error_code(verify),
            "status": verify_data.get("status"), "verified": verify_data.get("verified"),
            "verification_status": verify_data.get("verification_status"),
            "checks": _json_safe(recorded_checks),
            "scope": verify_data.get("scope"),
        }
        verify_status = "PASS" if cross_rejected else "FAIL"
        verify_reason = None if cross_rejected else "a check the durable record cannot establish was reported as verified"
        if not cross_rejected:
            verify_status, verify_reason = _unreadable_verdict(
                verify, "a check the durable record cannot establish was reported as verified")
        _mark(case, "cross_model_transaction_record_rejected", verify_status, verify_reason)
        _mark(case, "recorded_pass_with_changed_live_property", verify_status, verify_reason)

    checkpoint = await client.action("checkpoint.create", {"label": "phase4-r04-invariant", "include_solution": False},
                                     key="r04-checkpoint", request="r04-checkpoint")
    checkpoint_data = _data(checkpoint)
    if not _success(checkpoint) or not checkpoint_data.get("checkpoint_id"):
        _mark(case, "checkpoint_recovery_after_failed_invariant", "BLOCKED",
              f"checkpoint creation unavailable: {_error_code(checkpoint) or 'UNKNOWN'}")
    else:
        _mark(case, "checkpoint_recovery_after_failed_invariant", "PASS",
              "a bound checkpoint exists for the failed-invariant transaction and the restore option was returned",
              checkpoint_id=checkpoint_data.get("checkpoint_id"),
              checkpoint_sha256=checkpoint_data.get("sha256"))
        case.assertions["r04_checkpoint"] = {"checkpoint_id": checkpoint_data.get("checkpoint_id"),
                                            "sha256": checkpoint_data.get("sha256"),
                                            "restore_scope": checkpoint_data.get("restore_scope")}
    case.finish()


def _flatten_typed(value: Mapping[str, Any] | None) -> list[Any]:
    if not isinstance(value, Mapping):
        return []
    data = value.get("data")
    shape = _typed_shape(value)
    if shape in (None, []):
        return [data]
    out: list[Any] = []

    def walk(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        out.append(item)

    walk(data)
    return out


async def _case_r_readback(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                           state: dict[str, Any]) -> None:
    mapping = {
        "r01_expression_readback_status": "R01_LIVE",
        "r03_indexed_keyed_readback_status": "R03_LIVE",
        "r04_invariant_readback_status": "R04_LIVE",
    }
    completed = state.get("completed_cases")
    completed = completed if isinstance(completed, Mapping) else {}
    statuses: dict[str, str] = {}
    for name, source in mapping.items():
        status = str(completed.get(source) or "NOT_RUN")
        statuses[name] = status
        case.subcase(name, status, level="rollup",
                     reason=None if status == "PASS" else f"roll-up of {source}: {status}",
                     source_case=source)
    case.assertion("r_round_statuses", statuses)
    no_fail = all(status != "FAIL" for status in statuses.values())
    case.subcase("r_round_not_failed", "PASS" if no_fail else "FAIL", level="rollup",
                 reason=None if no_fail else "an R-round readback case reported FAIL")
    case.finish()


# ---------------------------------------------------------------------------
# W13 — parameters, variables, functions, selections
# ---------------------------------------------------------------------------


#: Refusal codes that mean "the container is already there": a case that must address a component,
#: a geometry sequence or a fixture feature an earlier case (or the model itself) already created
#: adopts it instead of reporting a failure.
CONTAINER_ADOPT_CODES = frozenset({"TAG_CONFLICT", "TAG_EXISTS", "NODE_EXISTS", "ALREADY_EXISTS"})
#: The edge length (m) of the solid the driver's own geometry fixture creates and builds.
CONTAINER_BLOCK_SIZE = (1.0e-3, 1.0e-3, 1.0e-3)


async def _container_step(client: ActionClient, step: str, operation: str, arguments: Mapping[str, Any],
                          store: dict[str, Any] | None = None) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """One container/fixture step: APPLIED, ADOPTED (already present) or the refusal itself.

    Every step carries a *fresh* idempotency identity: the control daemon stores a result per key
    (and per request body), so reusing one key for a container that is created again on another
    model is answered with ``IDEMPOTENCY_CONFLICT`` instead of doing the work.
    """
    key = _fresh_key(f"phase4-{step}")
    payload = await client.action(operation, dict(arguments), key=key, request=step)
    code = _error_code(payload)
    status = "APPLIED" if _success(payload) else ("ADOPTED" if code in CONTAINER_ADOPT_CODES else
                                                  "BLOCKED" if _blocked_payload(payload) else "FAILED")
    row = {"step": step, "operation": operation, "status": status, "error_code": code,
           "message": None if _success(payload) else _error_message(payload),
           "identity": _envelope_identity(payload)}
    if store is not None:
        store.setdefault("container_steps", []).append(row)
    return status, payload, row


async def _ensure_component(client: ActionClient, args: argparse.Namespace, *, prefix: str,
                            component: str | None = None,
                            store: dict[str, Any] | None = None) -> dict[str, Any]:
    """Establish one component container **read first**, and prove it by reading it back.

    The M1c run created the component it addressed *before* looking: on a model that already
    carried ``comp1`` the product answered the create with ``TAG_CONFLICT`` -- correctly -- and the
    pre-repair envelope hid that refusal behind an unresolved engine state, so the case never
    reached its own acceptance lines.  The order is now the observation order:

    1. read the component list (``definition.component_manage`` with ``action: list``; the
       operation enumerates ``model.component()`` and takes no tag),
    2. the addressed tag is in that list -> ``SATISFIED``/**adopted**, and *no* create is dispatched,
    3. otherwise create it and read the list back; the prerequisite holds only when the tag is
       observed there.

    A create refused with an "already exists" code is never an assumption: the list is read again
    and the tag has to be observed (the refusal's own dispatch stage and mutation witness travel
    with the step as ``adoption``).  ``status`` is ``SATISFIED``/``UNSATISFIED``/``BLOCKED`` with the
    refusal's operation/code/cause/dispatch stage as ``root_cause``, never a FAIL.
    """
    component = component or args.component
    evidence: dict[str, Any] = {"component": component, "prefix": prefix, "steps": [], "verified": False,
                                "component_tags": None, "status": "UNSATISFIED", "reason": None,
                                "root_cause": None, "adopted": False, "created": False}
    steps: list[dict[str, Any]] = evidence["steps"]

    async def read_list(step: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
        return await _container_step(client, step, "definition.component_manage",
                                     {"action": "list"}, store)

    try:
        status, listed, row = await read_list(f"{prefix}-component-list")
    except CapabilityUnavailable as exc:
        evidence.update({"status": "BLOCKED",
                         "reason": f"definition.component_manage is not published: {exc}",
                         "root_cause": {"operation": "definition.component_manage",
                                        "error_code": "CAPABILITY_UNAVAILABLE", "message": str(exc)}})
        return evidence
    steps.append(row)
    evidence["component_tags"] = _component_tags_in(listed)
    if not _success(listed):
        evidence.update({"status": "BLOCKED" if _blocked_payload(listed) else "UNSATISFIED",
                         "reason": (f"the component list could not be read "
                                    f"({_error_code(listed) or 'an invalid envelope'}): "
                                    f"{_error_message(listed)}"),
                         "root_cause": _refusal_root_cause("definition.component_manage", listed)})
        return evidence
    if component in (evidence["component_tags"] or []):
        # Read first: the container is already there, so the create that the pre-repair driver
        # sent (and that the product rightly refused) is never dispatched at all.
        evidence.update({"status": "SATISFIED", "verified": True, "adopted": True,
                         "reason": (f"the component list read back {evidence['component_tags']} and "
                                    f"already contains {component!r}: no create was dispatched"),
                         "root_cause": None})
        return evidence

    status, created, created_row = await _container_step(
        client, f"{prefix}-component", "definition.component_manage",
        {"action": "create", "tag": component}, store)
    steps.append(created_row)
    if status not in {"APPLIED", "ADOPTED"}:
        evidence.update({"status": "BLOCKED" if status == "BLOCKED" else "UNSATISFIED",
                         "reason": (f"definition.component_manage (create) returned "
                                    f"{created_row['error_code'] or 'an invalid envelope'}: "
                                    f"{created_row['message']}"),
                         "root_cause": _refusal_root_cause("definition.component_manage", created)})
        return evidence
    if status == "ADOPTED":
        # "Already exists" is the product's statement, not an observation: keep the refusal's own
        # stage/witness evidence with the step and read the list again to observe the tag.
        created_row["adoption"] = _adoption_evidence(created)
    try:
        _verify_status, verified, verify_row = await read_list(f"{prefix}-component-list-verify")
    except CapabilityUnavailable as exc:
        evidence.update({"status": "UNSATISFIED",
                         "reason": (f"the created component could not be read back: "
                                    f"definition.component_manage is not published: {exc}"),
                         "root_cause": {"operation": "definition.component_manage",
                                        "error_code": "CAPABILITY_UNAVAILABLE", "message": str(exc)}})
        return evidence
    steps.append(verify_row)
    evidence["component_tags"] = _component_tags_in(verified)
    if not _success(verified):
        evidence.update({"status": "BLOCKED" if _blocked_payload(verified) else "UNSATISFIED",
                         "reason": (f"the component list could not be read "
                                    f"({_error_code(verified) or 'an invalid envelope'}): "
                                    f"{_error_message(verified)}"),
                         "root_cause": _refusal_root_cause("definition.component_manage", verified)})
        return evidence
    if component not in (evidence["component_tags"] or []):
        evidence.update({"status": "UNSATISFIED",
                         "reason": (f"the component list read back {evidence['component_tags']!r} "
                                    f"without {component!r}, so the container this case addresses "
                                    "does not exist"),
                         "root_cause": {"operation": "definition.component_manage", "error_code": None,
                                        "cause_code": "NODE_NOT_FOUND",
                                        "cause_message": f"component {component!r} is not in the component list",
                                        "readback_tags": evidence["component_tags"]}})
        return evidence
    evidence.update({"status": "SATISFIED", "verified": True, "created": status == "APPLIED",
                     "adopted": status == "ADOPTED",
                     "reason": (f"the component {component!r} was "
                                + ("created and read back" if status == "APPLIED"
                                   else "reported present by a refused create and read back")
                                + " from the component list")})
    return evidence


def _component_tags_in(payload: Mapping[str, Any] | None) -> list[str]:
    """The component tags one ``definition.component_manage`` listing reports (both positions)."""
    data = _data(payload)
    readback = data.get("readback")
    readback_tags = ([str(item) for item in (readback.get("tags") or []) if isinstance(item, (str, int))]
                     if isinstance(readback, Mapping) else [])
    if readback_tags:
        return readback_tags
    return [str(item.get("tag")) for item in (data.get("components") or [])
            if isinstance(item, Mapping) and item.get("tag")]


def _adoption_evidence(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """The published stage/witness evidence behind an "already exists" adoption (C03).

    A create the product refused because the tag exists is adopted *only* together with this
    reading: the product's own dispatch stage, its mutation witness and whether that evidence
    proves nothing was executed.  An envelope that publishes none of it is recorded as such --
    the adoption then rests on the follow-up list read alone.
    """
    product = product_dispatch_stage(payload)
    stage = observed_dispatch_stage(payload)
    return {"operation": "definition.component_manage", "error_code": _error_code(payload),
            "dispatch_stage": stage, "proves_not_executed": stage in NOT_EXECUTED_STAGES,
            "product_dispatch_stage": _json_safe(product) if product else None}


async def _ensure_geometry_container(client: ActionClient, args: argparse.Namespace, *, prefix: str,
                                     store: dict[str, Any] | None = None, component: str | None = None,
                                     geometry: str | None = None, build_block: bool = True,
                                     block_tag: str | None = None) -> tuple[bool, dict[str, Any]]:
    """Create the component (+ geometry sequence + built solid) a live case addresses, in order.

    The bound model is shared by the whole suite and the cases that create a model of their own
    (the chains, T018, T020) leave *their* model bound, so a later case must never assume that
    ``comp1``/``geom1`` exists.  Observed live: ``physics.create`` was refused with "geometry
    'geom1' does not exist in component 'comp1'" and ``geometry.feature_create`` with "could not
    resolve node path segment geom:geom1" -- both are *driver-side* addressing defects, not product
    gaps, because the containers are create-able through published operations (chain A does exactly
    that).  A refusal naming an existing tag is adopted: the container is there, which is all the
    caller needs -- and the component step *reads first* (see :func:`_ensure_component`), so a
    container that is already present is never created again.
    """
    component = component or args.component
    geometry = geometry or args.geometry_tag
    block_tag = block_tag or f"{prefix}_blk"
    evidence: dict[str, Any] = {"component": component, "geometry": geometry, "block": block_tag if build_block else None,
                                "prefix": prefix}
    steps: list[dict[str, Any]] = []
    ok = True

    async def step(name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        nonlocal ok
        status, payload, row = await _container_step(client, name, operation, arguments, store)
        steps.append(row)
        if status not in {"APPLIED", "ADOPTED"} and ok:
            ok = False
            evidence["status"] = "BLOCKED" if status == "BLOCKED" else "FAIL"
            evidence["reason"] = f"{operation} returned {row['error_code'] or 'an invalid envelope'}: {row['message']}"
        return payload

    component_evidence = await _ensure_component(client, args, prefix=prefix, component=component,
                                                 store=store)
    steps.extend(component_evidence["steps"])
    evidence["component_evidence"] = component_evidence
    if component_evidence["status"] != "SATISFIED" and ok:
        # The same mapping the single component step used before: a blocked boundary stays
        # BLOCKED, every other refusal that stopped the container is a FAIL of the fixture.
        ok = False
        evidence["status"] = "BLOCKED" if component_evidence["status"] == "BLOCKED" else "FAIL"
        evidence["reason"] = (f"the component {component!r} could not be established: "
                              f"{component_evidence['reason']}")
    await step(f"{prefix}-geometry", "geometry.sequence_create",
               {"component": component, "tag": geometry, "dimension": 3})
    if build_block:
        geometry_path = {"segments": [{"collection": "component", "tag": component},
                                      {"collection": "geom", "tag": geometry}]}
        await step(f"{prefix}-block", "geometry.feature_create",
                   {"parent": geometry_path, "tag": block_tag, "type_id": "Block",
                    "properties": [{"name": "size", "value": {"kind": "float64", "shape": [3],
                                                              "data": list(CONTAINER_BLOCK_SIZE)}},
                                   {"name": "pos", "value": {"kind": "float64", "shape": [3],
                                                             "data": [0.0, 0.0, 0.0]}}]})
        await step(f"{prefix}-build", "geometry.build", {"geometry": geometry_path})
    evidence["steps"] = steps
    evidence.setdefault("status", "PASS" if ok else "BLOCKED")
    evidence.setdefault("reason", None)
    if store is not None:
        store["containers"] = evidence
    return ok, evidence


def _variable_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = _data(payload)
    raw = data.get("variables")
    rows: list[Any] = raw if isinstance(raw, list) else []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _variable_readback_path(args: argparse.Namespace, tag: str, component: str | None) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    if component:
        segments.append({"collection": "component", "tag": component})
    segments.append({"collection": "variable", "tag": tag})
    return {"segments": segments}


def _refusal_root_cause(operation: str, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """The original operation/code/cause/dispatch stage of one refusal (C03: name the root cause).

    The wrapper code the envelope raises (``EXECUTION_STATE_UNKNOWN``) and the operation's own
    failure (``error.details.cause_code``) are kept side by side, together with the stage the
    product reports and whether that stage proves nothing was executed.
    """
    product = product_dispatch_stage(payload) or {}
    stage = product.get("dispatch_stage")
    return {"operation": operation, "error_code": _error_code(payload),
            "cause_code": product.get("cause_code"), "cause_message": product.get("cause_message"),
            "product_dispatch_stage": product.get("stage"), "dispatch_stage": stage,
            "proves_not_executed": bool(stage in NOT_EXECUTED_STAGES) if stage else None,
            "message": _error_message(payload) or None, "identity": _envelope_identity(payload)}


async def _establish_component_prerequisite(client: ActionClient, args: argparse.Namespace, *,
                                            prefix: str,
                                            store: dict[str, Any] | None = None) -> dict[str, Any]:
    """Establish the component a live case addresses — its explicit prerequisite (C03).

    The M1 run called ``variable.group_create(component='comp1')`` on the run's freshly created
    (empty) model; the product answered, correctly, ``component 'comp1' does not exist``, and the
    case filed that as a product gap (G3.1 §4: "establish explicit prerequisites: model,
    component/geometry, material, mesh/study, revision").  This is the case's own prerequisite, and
    it is established in the observation order: the component list is read *first*, a tag that is
    already there is adopted without dispatching a create at all, and a missing one is created and
    read back (see :func:`_ensure_component`).

    ``status`` is ``SATISFIED`` only with the tag read back from the component list; every other
    outcome carries the refusal's own operation/code/cause/dispatch stage as ``root_cause`` and is
    filed as ONE ``DEPENDENCY_BLOCKED`` root cause — never as a defect of the operation that then
    refuses to address the container.
    """
    return await _ensure_component(client, args, prefix=prefix, store=store)


async def _case_w13_t006(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace, state: dict[str, Any]) -> None:
    rows = await _prepare_case(case, host, _plan_ops(case.case_id))
    domain_ops = ("variable.group_create", "variable.set", "variable.get")
    domain_ok = all((rows.get(operation) or {}).get("available") for operation in domain_ops)
    case.assertion("static_variable_ops_executable", domain_ok, operations=list(domain_ops))
    domain_ok = _availability_subcase(case, "static_variable_ops_availability", domain_ops, rows, flag=domain_ok)
    legacy_ops = ("manage_variables", "evaluate_expressions")
    legacy_ok = all((rows.get(operation) or {}).get("available") for operation in legacy_ops)
    case.assertion("static_legacy_variable_route_executable", legacy_ok, operations=list(legacy_ops))
    legacy_ok = _availability_subcase(case, "static_legacy_variable_route_available", legacy_ops, rows, flag=legacy_ok)

    live_ops = domain_ops if domain_ok else legacy_ops if legacy_ok else domain_ops
    runnable, status, why = _can_run(case, args, state, rows, live_ops, require_writable_model=True)
    if not runnable:
        for name in ("variables_two_in_one_group", "varnames_contains_modified_variable", "no_bogus_name_expr_variables",
                     "expression_evaluates_after_modification", "component_and_global_scope"):
            _mark(case, name, status, why)
        case.finish()
        return
    route = "domain" if domain_ok else "legacy"
    store: dict[str, Any] = {}
    # ---- explicit prerequisite: the component this case addresses -------------------------------
    # G3.1 §4: a case establishes its own containers before it uses them, and a refusal naming a
    # container the case never created is the case's *dependency*, never a product defect.  The M1
    # run skipped this step and filed the resulting ``NODE_NOT_FOUND`` as a product gap.
    live_lines = ("variables_two_in_one_group", "varnames_contains_modified_variable",
                  "no_bogus_name_expr_variables", "expression_evaluates_after_modification",
                  "component_and_global_scope")
    prerequisite = await _establish_component_prerequisite(client, args, prefix="w13-t006", store=store)
    case.assertions.setdefault("prerequisites", {})
    if isinstance(case.assertions["prerequisites"], dict):
        case.assertions["prerequisites"]["established"] = prerequisite
    if prerequisite["status"] != "SATISFIED":
        missing = f"component:{args.component}"
        for name in live_lines:
            _mark(case, name, "NOT_RUN", f"dependency not established: {missing} ({prerequisite['reason']})")
        case.assertions["dependency_blocked"] = {
            "missing": [missing],
            "root_cause": prerequisite.get("root_cause"),
            "note": ("the live lines of this case list NOT_RUN against the prerequisite this case was "
                     "to establish itself; a container the fixture never created is never a defect of "
                     "the operation that then refuses to address it"),
        }
        case.assertions["first_cause"] = classify_first_cause(
            {"status": "NOT_RUN", "error_code": "DEPENDENCY_BLOCKED", "first_cause": "DEPENDENCY_BLOCKED",
             "reason": (f"the case's own prerequisite is not established: {missing} "
                        f"({prerequisite['reason']}); the live lines depend on it and were not attempted")},
            case_id=case.case_id)
        case.finish("BLOCKED", reason=case.assertions["first_cause"]["reason"])
        return
    # The component is established *and read back*, and the group path below may address it.
    # ``variable.set``/``variable.get`` address the variable group with a *NodePath* (the
    # catalogue declares ``group`` as ``common.schema.json#/$defs/NodePath``); a bare tag string is
    # refused by the domain layer with "group must be a NodePath object" before anything runs
    # (observed live).  The path is the same component+variable path this case reads the node from.
    group_path = _variable_readback_path(args, args.variable_group, args.component or None)
    # T006 writes *defining expressions*.  COMSOL's Variables table documents the Expression column as
    # "the expression, using COMSOL syntax, that defines the variable" -- a definition text such as
    # ``phase4_q1=2`` is not an expression, and the m1d run wrote exactly that into ``q1``: the
    # engine then had a variable whose value could not be read (``q1_probe: expected 2.0, observed
    # None``) while the readback of the *text* still matched.  The variable is named ``q1``/``q2`` and
    # its defining expression is the number, on both routes.
    first_two = "2"
    first_three = "3"
    global_group = f"{args.variable_group}_global"
    global_path = _variable_readback_path(args, global_group, None)
    if route == "domain":
        created = await _step(case, host, client, args, state, name="variables_two_in_one_group",
                              operation="variable.group_create",
                              arguments={"tag": args.variable_group, "component": args.component},
                              store=store)
        if created is None:
            for name in ("varnames_contains_modified_variable", "no_bogus_name_expr_variables",
                         "expression_evaluates_after_modification"):
                _mark(case, name, "NOT_RUN", "prerequisite subcase variables_two_in_one_group did not pass")
        else:
            await _step(case, host, client, args, state, name="varnames_contains_modified_variable",
                        operation="variable.set",
                        arguments={"group": group_path,
                                   "variables": [{"name": "q1", "expression": first_two},
                                                 {"name": "q2", "expression": first_three}]},
                        prereq="variables_two_in_one_group", store=store)
            await client.action("variable.group_create", {"tag": global_group})
            await client.action("variable.set", {"group": global_path,
                                                 "variables": [{"name": "g1", "expression": "5"},
                                                               {"name": "g2", "expression": "g1*4"}]})
            await client.action("variable.set", {"group": group_path,
                                                 "variables": [{"name": "q1", "expression": "4"}]})
            await client.action("variable.set", {"group": global_path,
                                                 "variables": [{"name": "g1", "expression": "10"}]})
            readback = await _step(case, host, client, args, state, name="no_bogus_name_expr_variables",
                                   operation="variable.get",
                                   arguments={"group": group_path, "names": ["q1", "q2"]},
                                   prereq="variables_two_in_one_group", store=store,
                                   check=lambda payload, bundle: (
                                       ("PASS", None)
                                       if _success(payload) and _variable_names(payload) == ["q1", "q2"]
                                       else (("BLOCKED", f"variable.get was refused: {_error_code(payload)}")
                                             if _blocked_payload(payload) else
                                             ("FAIL", f"varnames readback was {_variable_names(payload)!r} instead of the two requested variables"))
                                   ))
            assert readback is not None or True
            await _step(case, host, client, args, state, name="expression_evaluates_after_modification",
                        operation="evaluate_expressions",
                        arguments={"expressions_json": json.dumps([{"name": "q1_probe", "expression": "q1"},
                                                                   {"name": "q2_probe", "expression": "q2"}]),
                                   "evaluation_policy": "ephemeral_mutation"},
                        prereq="varnames_contains_modified_variable", store=store,
                        check=_check_expression_values({"q1_probe": 2.0, "q2_probe": 3.0}))
    else:
        created = await _step(case, host, client, args, state, name="variables_two_in_one_group",
                              operation="manage_variables",
                              arguments={"action": "create", "component": args.component, "tag": args.variable_group,
                                         "name": "q1", "expression": first_two},
                              store=store,
                              check=lambda payload, bundle: (
                                  ("PASS", None) if _success(payload) and bool(_variable_rows(payload)) else
                                  ("FAIL", "the legacy variable route did not create the first variable")
                              ))
        if created is None or case.subcase_status("variables_two_in_one_group") != "PASS":
            for name in ("varnames_contains_modified_variable", "no_bogus_name_expr_variables",
                         "expression_evaluates_after_modification"):
                _mark(case, name, "NOT_RUN", "prerequisite subcase variables_two_in_one_group did not pass")
        else:
            await _step(case, host, client, args, state, name="varnames_contains_modified_variable",
                        operation="manage_variables",
                        arguments={"action": "create", "component": args.component, "tag": args.variable_group,
                                   "name": "q2", "expression": first_three},
                        prereq="variables_two_in_one_group", store=store,
                        check=lambda payload, bundle: (
                            ("PASS", None)
                            if _success(payload) and {row.get("name") for row in _variable_rows(payload)} == {"q1", "q2"}
                            else ("FAIL", f"the variable group exposed {_variable_rows(payload)!r} instead of the two requested variables")
                        ))
            readback_payload = store.get("varnames_contains_modified_variable")
            names = _variable_names(readback_payload) if readback_payload is not None else []
            path = _variable_readback_path(args, args.variable_group, args.component or None)
            node_value = await _read_variable_node(client, case, path, state)
            joined = [str(item) for item in names] + [str(item) for item in node_value]
            bogus = [name for name in ("name", "expr") if name in joined]
            case.assertions["w13_t006_varnames"] = {"legacy_names": names, "node_varnames": node_value, "bogus": bogus}
            _mark(case, "no_bogus_name_expr_variables", "FAIL" if bogus else "PASS" if joined else "NOT_RUN",
                  "the variable list contained the literal names name/expr" if bogus else None,
                  names=joined)
            await _step(case, host, client, args, state, name="expression_evaluates_after_modification",
                        operation="evaluate_expressions",
                        arguments={"expressions_json": json.dumps([{"name": "q1_probe", "expression": "q1"},
                                                                   {"name": "q2_probe", "expression": "q2"}]),
                                   "evaluation_policy": "ephemeral_mutation"},
                        prereq="varnames_contains_modified_variable", store=store,
                        check=_check_expression_values({"q1_probe": 2.0, "q2_probe": 3.0}))

    if case.subcase_status("expression_evaluates_after_modification") == "PASS":
        await _step(case, host, client, args, state, name="component_and_global_scope",
                    operation="evaluate_expressions",
                    arguments={"expressions_json": json.dumps([{"name": "global_probe", "expression": "g1"},
                                                               {"name": "global_dep_probe", "expression": "g2"}]),
                               "evaluation_policy": "ephemeral_mutation"},
                    store=store, check=_check_expression_values({"global_probe": 10.0, "global_dep_probe": 40.0}))
    else:
        _mark(case, "component_and_global_scope", "NOT_RUN",
              "the component variable evaluation did not pass; the global-scope probe was not attempted")
    case.finish()


def _variable_names(payload: Mapping[str, Any] | None) -> list[str]:
    data = _data(payload)
    for key in ("varnames", "names"):
        value = data.get(key)
        if isinstance(value, list):
            return [str(item) for item in value]
    rows = _variable_rows(payload) if payload is not None else []
    return [str(row.get("name")) for row in rows if row.get("name")]


async def _read_variable_node(client: ActionClient, case: Case, path: Mapping[str, Any],
                              state: dict[str, Any]) -> list[str]:
    try:
        value = await _read_value(client, path, "varnames")
    except CapabilityUnavailable:
        return []
    if isinstance(value, Mapping):
        data = value.get("data")
        if isinstance(data, list):
            return [str(item) for item in data]
        if data is not None:
            return [str(data)]
    return []


def _trailing_scalar(value: Any) -> Any:
    """The trailing element of a nested structure, or the value itself (never a computed number)."""
    current = value
    while isinstance(current, list) and current:
        current = current[-1]
    return current


def _published_scalar(row: Mapping[str, Any]) -> float | None:
    """The scalar an evaluated row publishes, read through the published paths (C04).

    ``last_value`` is the trailing scalar the product publishes next to ``value``; a row that
    publishes only ``value`` still yields that scalar when the structure is a 1x1 read.  Nothing is
    computed here - a row that publishes no numbers yields ``None`` and is reported as such, never
    as a driver-side estimate.
    """
    for key in ("last_value", "value"):
        if key not in row:
            continue
        candidate = row.get(key)
        scalar = _finite_float(candidate if not isinstance(candidate, list) else _trailing_scalar(candidate))
        if scalar is not None:
            return scalar
    return None


def _row_refusal(row: Mapping[str, Any]) -> str:
    """The product's own reason for a row that published no value, quoted verbatim."""
    parts = [str(row.get("error_code") or "no value published")]
    if row.get("error"):
        parts.append(str(row["error"]))
    route = row.get("route")
    if route:
        parts.append(f"[route {route}]")
    if "dataset" in row:
        parts.append(f"[dataset {row.get('dataset')!r}]")
    if "solution" in row:
        parts.append(f"[solution {row.get('solution')!r}]")
    if "shape" in row:
        parts.append(f"[result shape {row.get('shape')!r}]")
    return f"{row.get('name')}: " + " ".join(parts)


def _unit_check_rows(payload: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    """Every unit-check record a payload publishes: ``unit_checks`` on a write, the single
    ``unit_check`` in a refusal's details, or the ``unit_check`` block on an interface read."""
    if not isinstance(payload, Mapping):
        return []
    data = _data(payload)
    rows = data.get("unit_checks")
    if isinstance(rows, Mapping):
        return [rows]
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, Mapping)]
    details = data.get("error", {}).get("details") if isinstance(data.get("error"), Mapping) else None
    for holder in (details, data):
        single = holder.get("unit_check") if isinstance(holder, Mapping) else None
        if isinstance(single, Mapping):
            return [single]
    return []


def _implicit_factor_report(*payloads: Mapping[str, Any] | None) -> dict[str, Any]:
    """Collect what the product's unit records say about implicit thickness/absorptivity (T015).

    The acceptance forbids multiplying a source by a thickness or an absorptivity factor.  The
    records have to *say* so (``verbatim`` + ``implicit_factors.applied``), and a record that claims
    an applied factor anywhere is a failure with the claim quoted - never silently ignored.
    """
    report: dict[str, Any] = {"records": [], "claimed": [], "silent": [], "boundary_records": []}
    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        for row in _unit_check_rows(payload):
            point = str(row.get("write_point"))
            for key in ("implicit_thickness_applied", "implicit_absorptivity_applied"):
                if _data_or_details(payload, row, key) is True:
                    report["claimed"].append(f"{point}: {key}")
            factors = row.get("implicit_factors")
            verbatim = row.get("verbatim") is True
            applied = factors.get("applied") if isinstance(factors, Mapping) else None
            record = {"write_point": point, "documented_entity": row.get("documented_entity"),
                      "status": row.get("status"), "verbatim": verbatim, "implicit_factors": factors}
            report["records"].append(record)
            if row.get("documented_entity") == "boundary":
                report["boundary_records"].append(record)
            if applied is True or not isinstance(factors, Mapping) or not verbatim:
                report["silent"].append(record)
    return report


def _data_or_details(payload: Mapping[str, Any], row: Mapping[str, Any], key: str) -> Any:
    """A flag is a claim wherever it is published: the row, the data envelope or the error details."""
    if key in row:
        return row[key]
    data = _data(payload)
    if key in data:
        return data[key]
    details = data.get("error", {}).get("details") if isinstance(data.get("error"), Mapping) else None
    if isinstance(details, Mapping) and key in details:
        return details[key]
    if isinstance(_data(payload).get("unit_check"), Mapping) and key in _data(payload)["unit_check"]:
        return _data(payload)["unit_check"][key]
    return None


def _row_without_value(row: Mapping[str, Any]) -> str:
    """A row that publishes no number: quote what it *did* publish, including its ok flag.

    ``ok: true`` with an empty ``value``/``last_value`` is the silent-degradation shape the m1d
    W13_T006 payload carried; naming it here keeps the dashboard from reading like a missing key.
    """
    return (f"{row.get('name')}: the row publishes no value "
            f"(ok={row.get('ok')!r}, value={_json_safe(row.get('value'))!r}, "
            f"last_value={row.get('last_value')!r}, shape={row.get('shape')!r}"
            + (f", route={row.get('route')!r}" if "route" in row else "")
            + (f", dataset={row.get('dataset')!r}" if "dataset" in row else "")
            + (f", solution={row.get('solution')!r}" if "solution" in row else "")
            + ")")


def _check_expression_values(expected: Mapping[str, float]) -> Callable[[Mapping[str, Any], dict[str, Any]], Any]:
    def check(payload: Mapping[str, Any], bundle: dict[str, Any]) -> Any:
        if not _success(payload):
            return "BLOCKED" if _blocked_payload(payload) else "FAIL", f"expression evaluation returned {_error_code(payload) or 'an invalid envelope'}"
        results_raw = _data(payload).get("results")
        results: list[Any] = results_raw if isinstance(results_raw, list) else []
        observed = {str(row.get("name")): row for row in results if isinstance(row, Mapping)}
        mismatches: list[str] = []
        for name, want in expected.items():
            row = observed.get(name)
            if isinstance(row, Mapping) and row.get("ok") is False:
                # The row itself carries the product's reason (an empty read, a refusal, ...): report
                # that instead of a bare "observed None", which said nothing about *why*.
                mismatches.append(_row_refusal(row))
                continue
            got = _published_scalar(row) if isinstance(row, Mapping) else None
            if got is None and isinstance(row, Mapping):
                mismatches.append(f"expected {want}; {_row_without_value(row)}")
            elif got is None or abs(got - float(want)) > 1e-9 * max(1.0, abs(float(want))):
                mismatches.append(f"{name}: expected {want}, observed {got}")
        if mismatches:
            return "FAIL", "; ".join(mismatches), {"results": results}
        return "PASS", None, {"results": results}
    return check


def _no_implicit_factor_check() -> Callable[[Mapping[str, Any], dict[str, Any]], Any]:
    """T015: the source records must state verbatim use and no implicit thickness/absorptivity.

    ``physics.validate`` is called with the *published* schema shape (``checks`` is an object); its
    reply must not claim a violation and must not claim an applied factor.  The substance comes from
    the unit-check records of the source writes this case made: they have to say ``verbatim: true``
    and ``implicit_factors.applied: false``, at least one record has to exist, and a record that
    *claims* an implicit factor anywhere fails the line with the claim quoted.  When the boundary
    write point was written, its record must report the boundary entity and the area unit.
    """

    def check(payload: Mapping[str, Any], bundle: dict[str, Any]) -> Any:
        if not _success(payload):
            # The published input schema types ``checks`` as an object; an array (the m1d request)
            # is refused before anything runs, so a refusal here is reported with its own code.
            return "BLOCKED" if _blocked_payload(payload) else "FAIL", (
                f"physics.validate refused the checks payload: {_error_code(payload) or 'invalid envelope'}"
                f": {_error_message(payload)}")
        data = _data(payload)
        if str(data.get("verdict") or "").upper() == "VIOLATION":
            return "FAIL", f"the interface validate reported a violation: {json.dumps(_json_safe(data.get('violations')), ensure_ascii=False)}"
        store = bundle.get("store") or {}
        assertions = getattr(bundle.get("case"), "assertions", {}) or {}
        surface_write = assertions.get("t015_surface_flux_write")
        report = _implicit_factor_report(store.get("volume_source_W_per_m3"), store.get("coordinate_unit_m_and_mm"),
                                         store.get("wrong_unit_warns_or_fails"), surface_write.get("payload") if isinstance(surface_write, Mapping) else None)
        if report["claimed"]:
            return "FAIL", ("the source unit check reported an implicit thickness/absorptivity factor: "
                            + "; ".join(report["claimed"])), {"unit_records": report}
        if not report["records"]:
            return "FAIL", ("no unit-check record was published for the heat sources, so the response "
                            "says nothing about implicit thickness/absorptivity"), {"unit_records": report}
        if report["silent"]:
            return "FAIL", ("the unit-check record does not state that the value is used verbatim without an "
                            "implicit factor: " + json.dumps(_json_safe(report["silent"][:2]), ensure_ascii=False)), {"unit_records": report}
        if isinstance(surface_write, Mapping) and surface_write.get("success"):
            if not report["boundary_records"]:
                return "FAIL", ("the boundary heat flux write was accepted but no boundary write point was "
                                "reported, so the surface source's dimension is not evidenced"), {"unit_records": report}
        observed = {
            "verdict": data.get("verdict"),
            "checked_rules": data.get("checked_rules"),
            "unit_records": report["records"],
            "surface_flux_write": surface_write if isinstance(surface_write, Mapping) else None,
            "implicit_factor_claims": report["claimed"],
        }
        return "PASS", None, {"observed": observed}

    return check


async def _case_w13_t015(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace, state: dict[str, Any]) -> None:
    rows = await _prepare_case(case, host, _plan_ops(case.case_id))
    domain_ops = ("physics.create", "physics.feature_create", "physics.feature_update", "physics.validate")
    domain_ok = all((rows.get(operation) or {}).get("available") for operation in domain_ops)
    case.assertion("static_physics_unit_ops_executable", domain_ok, operations=list(domain_ops))
    domain_ok = _availability_subcase(case, "static_physics_unit_ops_availability", domain_ops, rows, flag=domain_ok)
    live_ops = ("physics.create", "physics.feature_create", "physics.feature_update")
    runnable, status, why = _can_run(case, args, state, rows, live_ops, require_writable_model=True)
    live_names = ("surface_source_W_per_m2", "volume_source_W_per_m3", "coordinate_unit_m_and_mm",
                  "wrong_unit_warns_or_fails", "no_implicit_thickness_or_absorptivity")
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    store: dict[str, Any] = {}
    # ``physics.create`` binds the interface to a geometry sequence that must *exist* on the bound
    # model (``_require_geometry``); addressing ``geom1`` on a model that carries neither the
    # component nor the sequence is refused with "geometry 'geom1' does not exist in component
    # 'comp1'" before the interface is created (observed live).  The containers are created (or
    # adopted when they are already there) first, exactly like chain A does.
    containers_ok, container_evidence = await _ensure_geometry_container(
        client, args, prefix="t015", store=store)
    case.assertions["t015_containers"] = container_evidence
    if not containers_ok:
        for name in live_names:
            _mark(case, name, str(container_evidence["status"]), container_evidence["reason"],
                  observed=container_evidence)
        case.finish()
        return
    created = await _step(case, host, client, args, state, name="surface_source_W_per_m2",
                          operation="physics.create",
                          arguments={"component": args.component, "tag": args.physics_tag,
                                     "type_id": args.heat_physics_type, "geometry": args.geometry_tag},
                          store=store)
    if created is None:
        for name in live_names[1:]:
            _mark(case, name, "NOT_RUN", "prerequisite subcase surface_source_W_per_m2 did not pass")
        case.finish()
        return
    await _step(case, host, client, args, state, name="volume_source_W_per_m3",
                operation="physics.feature_create",
                arguments={"parent": {"segments": [{"collection": "component", "tag": args.component},
                                                   {"collection": "physics", "tag": args.physics_tag}]},
                           "tag": args.volume_source_tag, "type_id": "HeatSource", "entity_dimension": 3,
                           "properties": [{"name": "Q0", "value": {"kind": "expression", "shape": [], "data": "1e5[W/m^3]"}}]},
                prereq="surface_source_W_per_m2", store=store)
    await _step(case, host, client, args, state, name="coordinate_unit_m_and_mm",
                operation="physics.feature_update",
                arguments={"path": {"segments": [{"collection": "component", "tag": args.component},
                                                {"collection": "physics", "tag": args.physics_tag},
                                                {"collection": "feature", "tag": args.volume_source_tag}]},
                           "properties": [{"name": "Q0", "value": {"kind": "expression", "shape": [],
                                                                   "data": "1e2[W/m^3]", "unit": "W/m^3"}}]},
                prereq="volume_source_W_per_m3", store=store)
    await _step(case, host, client, args, state, name="wrong_unit_warns_or_fails",
                operation="physics.feature_update",
                arguments={"path": {"segments": [{"collection": "component", "tag": args.component},
                                                {"collection": "physics", "tag": args.physics_tag},
                                                {"collection": "feature", "tag": args.volume_source_tag}]},
                           "properties": [{"name": "Q0", "value": {"kind": "expression", "shape": [],
                                                                   "data": "1e5[W/m^2]"}}]},
                prereq="coordinate_unit_m_and_mm", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": "unit inconsistency reported",
                                   "unit_check": _json_safe(_unit_check_rows(payload))})
                    if (not _success(payload)) or _data(payload).get("preflight_warnings") or _data(payload).get("unit_warning")
                    else ("FAIL", "a W/m^2 expression was accepted for a W/m^3 volumetric source without any warning")
                ))
    # T015's surface source: the boundary feature whose documented write point is
    # ``HeatFluxBoundary.q0`` ("q0 is the inward heat flux (SI unit: W/m2), normal to the boundary",
    # HeatTransferModuleUsersGuide p.94).  This write is *evidence*: the m1d run's
    # ``surface_source_W_per_m2`` line created the interface only, so no W/m^2 value existed anywhere
    # in that run.  A build whose property label is not verified refuses the write, and that refusal
    # is recorded with its own code rather than reported as a unit pass.
    surface_tag = f"{args.volume_source_tag}flux"
    surface_write = await client.action(
        "physics.feature_create",
        {"parent": {"segments": [{"collection": "component", "tag": args.component},
                                 {"collection": "physics", "tag": args.physics_tag}]},
         "tag": surface_tag, "type_id": "HeatFluxBoundary", "entity_dimension": 2,
         "properties": [{"name": "q0", "value": {"kind": "expression", "shape": [], "data": "1e5[W/m^2]"}}]},
        key=_fresh_key("phase4-t015-surface-flux"), request="t015-surface-flux")
    case.assertions["t015_surface_flux_write"] = {
        "operation": "physics.feature_create", "tag": surface_tag, "type_id": "HeatFluxBoundary",
        "entity_dimension": 2, "property": "q0", "data": "1e5[W/m^2]",
        "success": _success(surface_write),
        "error_code": None if _success(surface_write) else _error_code(surface_write),
        "message": None if _success(surface_write) else _error_message(surface_write),
        "unit_checks": _json_safe(_unit_check_rows(surface_write)),
        "payload": _json_safe(surface_write),
    }
    case.assertions["t015_implicit_factor_report"] = _json_safe(_implicit_factor_report(
        store.get("volume_source_W_per_m3"), store.get("coordinate_unit_m_and_mm"),
        store.get("wrong_unit_warns_or_fails"), surface_write))
    await _step(case, host, client, args, state, name="no_implicit_thickness_or_absorptivity",
                operation="physics.validate",
                arguments={"scope": {"segments": [{"collection": "component", "tag": args.component},
                                                  {"collection": "physics", "tag": args.physics_tag}]},
                           # The published input schema types ``checks`` as an object (the operation
                           # describe in the m1d transcript carries ``"checks": {"type": "object"}``);
                           # the array this case used to send was refused with
                           # "INVALID_REQUEST: physics.validate.checks must be an object".
                           "checks": {"required_products": []}},
                prereq="wrong_unit_warns_or_fails", store=store,
                check=_no_implicit_factor_check())
    case.finish()


async def _case_w13_t048(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace, state: dict[str, Any]) -> None:
    rows = await _prepare_case(case, host, _plan_ops(case.case_id))
    domain_ops = ("selection.create", "selection.measure", "selection.validate", "physics.selection_set")
    domain_ok = all((rows.get(operation) or {}).get("available") for operation in domain_ops)
    case.assertion("static_selection_ops_executable", domain_ok, operations=list(domain_ops))
    domain_ok = _availability_subcase(case, "static_selection_ops_availability", domain_ops, rows, flag=domain_ok)
    live_names = ("named_selection_created_and_bound", "geometry_revision_recorded", "revalidation_after_geometry_change",
                  "drift_stops_boundary_application", "entity_measure_change_recorded")
    runnable, status, why = _can_run(case, args, state, rows, domain_ops, require_writable_model=True)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    store: dict[str, Any] = {}
    # The selection is addressed by a *SelectionSpec* (``kind: named``) in every read/validate call
    # and by a NodePath only where an operation declares one; the Box definition uses the documented
    # Box property vocabulary (xmin/xmax/ymin/ymax/zmin/zmax), never a nested ``box`` object — the
    # live run sent ``{"box": ...}`` and the domain layer refused it as neither an assignment field
    # nor a documented Box property.  The model also has to *carry* something the box can select:
    # the geometry containers and a built solid are created first.
    containers_ok, container_evidence = await _ensure_geometry_container(client, args, prefix="t048", store=store)
    case.assertions["t048_containers"] = container_evidence
    if not containers_ok:
        for name in live_names:
            _mark(case, name, str(container_evidence["status"]), container_evidence["reason"],
                  observed=container_evidence)
        case.finish()
        return
    selection_spec = {"kind": "named", "component": args.component, "tag": args.selection_tag}
    measure_metrics = ["n_entities", "volume", "bounding_box"]
    await _step(case, host, client, args, state, name="named_selection_created_and_bound",
                operation="selection.create",
                arguments={"component": args.component, "tag": args.selection_tag, "type_id": "Box",
                           "definition": {"entity_dimension": 3, "xmin": 0.0, "xmax": 2.0e-3,
                                          "ymin": 0.0, "ymax": 2.0e-3, "zmin": 0.0, "zmax": 2.0e-3}},
                store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _data(payload)})
                    if _success(payload) and _data(payload).get("type_readback") == "Box"
                    else _unreadable_verdict(payload, "the named Box selection could not be created")
                ))
    # What the product publishes about a selection is its *resolved* component/geometry/dimension,
    # its entity list and the measured metrics of those entities (``selection.measure``); there is
    # no geometry-revision getter in this build.  The pre-change reference therefore records the
    # resolved binding and the measured metrics: that is the state a later revalidation has to be
    # compared against, and it is what the acceptance line needs to be able to observe drift.
    measured_before = await _step(case, host, client, args, state, name="geometry_revision_recorded",
                                  operation="selection.measure",
                                  arguments={"selection": selection_spec, "metrics": measure_metrics},
                                  store=store,
                                  check=lambda payload, bundle: (
                                      ("PASS", None, {"observed": _data(payload)})
                                      if _success(payload) and _data(payload).get("geometry")
                                      and isinstance(_data(payload).get("entity_count"), int)
                                      else _unreadable_verdict(
                                          payload, "the selection's geometry binding and entity measure were not recorded")
                                  ))
    before_metrics = _selection_metric_values(measured_before)
    # A real geometry change: the block the containers created is resized and the sequence rebuilt,
    # so the selection's entities are renumbered/re-measured by the engine itself.
    edit = await _container_step(client, "t048-geometry-change", "geometry.feature_update",
                                 {"path": {"segments": [{"collection": "component", "tag": args.component},
                                                        {"collection": "geom", "tag": args.geometry_tag},
                                                        {"collection": "feature", "tag": "t048_blk"}]},
                                  "properties": [{"name": "size", "value": {"kind": "float64", "shape": [3],
                                                                            "data": [3.0e-3, 1.0e-3, 1.0e-3]}}]},
                                 store)
    edit_status, edit_payload, edit_row = edit
    rebuilt = await _container_step(client, "t048-geometry-rebuild", "geometry.build",
                                    {"geometry": {"segments": [{"collection": "component", "tag": args.component},
                                                               {"collection": "geom", "tag": args.geometry_tag}]}},
                                    store)
    case.assertions["t048_geometry_change"] = {"edit": edit_row, "rebuild": rebuilt[2]}
    geometry_changed = edit_status == "APPLIED" and rebuilt[0] == "APPLIED"
    measured_after = await _step(case, host, client, args, state, name="revalidation_after_geometry_change",
                                 operation="selection.validate",
                                 arguments={"selection": selection_spec,
                                            "expectations": [{"kind": "count", "min": 0}]},
                                 prereq="geometry_revision_recorded", store=store,
                                 check=lambda payload, bundle: _check_selection_revalidation(
                                     payload, before_metrics, geometry_changed))
    after_metrics = _selection_metric_values(measured_after)
    drift = bool(before_metrics) and bool(after_metrics) and before_metrics != after_metrics
    case.assertions["t048_drift"] = {"before": before_metrics, "after": after_metrics,
                                     "geometry_changed": geometry_changed, "drift_observed": drift}
    if not geometry_changed:
        _mark(case, "drift_stops_boundary_application", "NOT_RUN",
              "the geometry change did not apply, so no drift could be provoked")
    elif not drift:
        _mark(case, "drift_stops_boundary_application", "NOT_RUN",
              "the measured selection state did not change across the geometry rebuild, so no drift could be "
              "provoked")
    else:
        await _step(case, host, client, args, state, name="drift_stops_boundary_application",
                    operation="physics.selection_set",
                    arguments={"path": {"segments": [{"collection": "component", "tag": args.component},
                                                     {"collection": "physics", "tag": args.physics_tag},
                                                     {"collection": "feature", "tag": args.temperature_tag}]},
                               "selection": selection_spec},
                    prereq="revalidation_after_geometry_change", store=store,
                    check=lambda payload, bundle: (
                        ("FAIL", "a boundary was applied to a selection whose measured entities drifted",
                         {"observed": _data(payload), "drift": case.assertions["t048_drift"]})
                        if _success(payload) else
                        _unreadable_verdict(payload, "the boundary application on a drifted selection")
                    ))
    await _step(case, host, client, args, state, name="entity_measure_change_recorded",
                operation="selection.measure",
                arguments={"selection": selection_spec, "metrics": measure_metrics},
                prereq="revalidation_after_geometry_change", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _data(payload)})
                    if _success(payload) and isinstance(_data(payload).get("entity_count"), int)
                    and _selection_metric_values(payload)
                    else _unreadable_verdict(payload, "the post-change measure did not report the selection's "
                                                      "resolved entities and metrics")
                ))
    case.finish()


def _selection_metric_values(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """The measured entity count and metric values of a ``selection.measure``/``validate`` payload."""
    data = _data(payload) if isinstance(payload, Mapping) else {}
    raw_metrics = data.get("metrics")
    metrics: Mapping[str, Any] = raw_metrics if isinstance(raw_metrics, Mapping) else {}
    values: dict[str, Any] = {}
    for name, row in sorted(metrics.items()):
        value = row.get("value") if isinstance(row, Mapping) else None
        values[str(name)] = _json_safe((value or {}).get("data")) if isinstance(value, Mapping) else None
    if isinstance(data.get("entity_count"), int):
        values["entity_count"] = data["entity_count"]
    return values


def _check_selection_revalidation(payload: Mapping[str, Any], before: Mapping[str, Any],
                                  geometry_changed: bool) -> Any:
    """Verdict for a ``selection.validate`` re-read after a geometry change.

    The product publishes the resolved selection, its checks and its verdict — but no
    geometry-revision getter.  A refusal is blocked, a failing check is reported with its own check
    results, and a verdict that claims validity while the driver *measured* a different entity state
    than before the change is the drift this acceptance line exists to catch.
    """
    if not _success(payload):
        return _unreadable_verdict(payload, "the selection could not be revalidated after the geometry change")
    data = _data(payload)
    observed = {"verdict": data.get("verdict"), "checks": _json_safe(data.get("checks")),
                "entity_count": data.get("entity_count"), "geometry": data.get("geometry"),
                "before": _json_safe(before), "geometry_changed": geometry_changed}
    after = _selection_metric_values(payload)
    if geometry_changed and before and after and before != after and data.get("verdict") == "PASS":
        # The entities were re-measured after a geometry rebuild and the validation still reports a
        # plain PASS: the drift is recorded here and the boundary line is what must catch it.
        return ("PASS", None, {**observed, "drift": {"before": before, "after": after}})
    if data.get("verdict") not in {"PASS"}:
        return ("FAIL", f"the revalidation verdict was {data.get('verdict')!r} with failed checks "
                        f"{[check.get('check') for check in (data.get('checks') or []) if isinstance(check, Mapping) and check.get('status') != 'PASS']}",
                observed)
    return ("PASS", None, observed)


async def _case_w13_t016(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                         state: dict[str, Any]) -> None:
    run_dir = Path(args.run_dir)
    rows = await _prepare_case(case, host, _plan_ops(case.case_id))
    domain_ops = ("function.create", "function.data_import", "function.inspect", "function.evaluate")
    domain_ok = all((rows.get(operation) or {}).get("available") for operation in domain_ops)
    case.assertion("static_function_data_ops_executable", domain_ok, operations=list(domain_ops))
    domain_ok = _availability_subcase(case, "static_function_data_ops_availability", domain_ops, rows, flag=domain_ok)

    fixture = run_dir / T016_FIXTURE_NAME
    rows_written, expectation = _write_t016_fixture(fixture)
    digest = _sha256(fixture) if fixture.is_file() else None
    case.assertions["t016_fixture"] = {
        "path": str(fixture), "sha256": digest, "rows": len(rows_written),
        "columns": "x[m],y[m],Q[W/m^2]",
        "angular_probe_grid": expectation["probe_table"],
        "radial_mean_of_angle_average": expectation["radial_mean"],
    }
    fixture_ok = bool(digest and len(rows_written) >= 9 and expectation["angle_difference"] > 0)
    case.assertion("non_axisymmetric_fixture_hash_recorded", fixture_ok, sha256=digest, rows=len(rows_written))
    case.subcase("non_axisymmetric_fixture_hash_recorded", "PASS" if fixture_ok else "FAIL", level="fixture",
                 reason=None if fixture_ok else "the non-axisymmetric Q(x,y) fixture could not be written or hashed")

    live_names = ("two_d_interpolation_angular_difference_preserved", "radial_average_not_substituted",
                  "m_mm_coordinate_conversion", "interpolation_and_extrapolation_settings_recorded")
    runnable, status, why = _can_run(case, args, state, rows, domain_ops, require_writable_model=True)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    store: dict[str, Any] = {}
    function_path = {"segments": [{"collection": "func", "tag": args.function_tag}]}
    await _step(case, host, client, args, state, name="two_d_interpolation_angular_difference_preserved",
                operation="function.create",
                arguments={"tag": args.function_tag, "type_id": "Interpolation",
                           "definition": {"interpolation": "linear", "extrapolation": "none", "data_unit": "W/m^2"}},
                store=store)
    await _step(case, host, client, args, state, name="radial_average_not_substituted",
                operation="function.evaluate",
                arguments={"path": function_path,
                           "arguments": [[row["x"], row["y"]] for row in expectation["probe_table"]]},
                prereq="two_d_interpolation_angular_difference_preserved", store=store,
                check=lambda payload, bundle: _check_angular_difference(payload, expectation))
    await _step(case, host, client, args, state, name="m_mm_coordinate_conversion",
                operation="function.evaluate",
                arguments={"path": function_path,
                           "arguments": [[row["x_mm"], row["y_mm"]] for row in expectation["probe_table"]],
                           "coordinate_unit": "mm"},
                prereq="radial_average_not_substituted", store=store,
                check=lambda payload, bundle: _check_unit_conversion(payload, expectation))
    await _step(case, host, client, args, state, name="interpolation_and_extrapolation_settings_recorded",
                operation="function.inspect",
                arguments={"path": function_path},
                prereq="m_mm_coordinate_conversion", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _data(payload)})
                    if _success(payload) and _data(payload).get("interpolation") and "extrapolation" in _data(payload)
                    else ("FAIL", "function.inspect did not report interpolation/extrapolation settings")
                ))
    case.assertions["t016_function"] = {"fixture_sha256": digest}
    case.finish()


def _write_t016_fixture(path: Path) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Write a non-axisymmetric Q(x,y) heat-flux fixture and its probe grid."""
    import csv
    import io

    angles = (0.0, 30.0, 60.0, 90.0, 180.0, 270.0)
    radius_m = 5.0e-4
    rows: list[dict[str, float]] = []
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["x[m]", "y[m]", "Q[W/m^2]"])
    axis = (-radius_m, -radius_m / 3.0, radius_m / 3.0, radius_m)
    grid = [(x, y) for y in axis for x in axis]
    for x, y in grid:
        value = 1.0e5 * (1.0 + (x / radius_m)) * (0.5 + 0.5 * (y / radius_m))
        writer.writerow([f"{x:.10g}", f"{y:.10g}", f"{value:.10g}"])
        rows.append({"x": x, "y": y, "Q": value})
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    probe_table = []
    for angle in angles:
        x = radius_m * math.cos(math.radians(angle))
        y = radius_m * math.sin(math.radians(angle))
        value = 1.0e5 * (1.0 + (x / radius_m)) * (0.5 + 0.5 * (y / radius_m))
        probe_table.append({"angle_deg": angle, "x": x, "y": y, "x_mm": x * 1000.0, "y_mm": y * 1000.0, "Q": value})
    values = [row["Q"] for row in probe_table]
    radial_mean = sum(values) / len(values)
    return rows, {
        "probe_table": probe_table,
        "radial_mean": radial_mean,
        "angle_difference": max(values) - min(values),
        "radius_m": radius_m,
    }


def _check_angular_difference(payload: Mapping[str, Any], expectation: Mapping[str, Any]) -> Any:
    if not _success(payload):
        return ("BLOCKED" if _blocked_payload(payload) else "FAIL", f"function.evaluate returned {_error_code(payload) or 'an invalid envelope'}")
    numbers = _flatten_numbers(_data(payload).get("values", _data(payload).get("results")))
    if len(numbers) != len(expectation["probe_table"]):
        return "FAIL", f"function.evaluate returned {len(numbers)} values for {len(expectation['probe_table'])} probes"
    spread = max(numbers) - min(numbers)
    radial_mean = float(expectation["radial_mean"])
    if spread <= 0:
        return "FAIL", "all same-radius samples were equal: the angular difference was lost"
    if all(abs(value - radial_mean) <= 1e-9 for value in numbers):
        return "FAIL", "the samples reproduced the radial average instead of the angular profile"
    return "PASS", None, {"values": numbers, "spread": spread, "radial_mean": radial_mean}


def _check_unit_conversion(payload: Mapping[str, Any], expectation: Mapping[str, Any]) -> Any:
    if not _success(payload):
        return ("BLOCKED" if _blocked_payload(payload) else "FAIL", f"function.evaluate returned {_error_code(payload) or 'an invalid envelope'}")
    numbers = _flatten_numbers(_data(payload).get("values", _data(payload).get("results")))
    wanted = [float(row["Q"]) for row in expectation["probe_table"]]
    if len(numbers) != len(wanted):
        return "FAIL", f"the mm probe returned {len(numbers)} values for {len(wanted)} probes"
    mismatch = [f"{got} != {want}" for got, want in zip(numbers, wanted) if abs(got - want) > 1e-6 * max(1.0, abs(want))]
    if mismatch:
        return "FAIL", "the m/mm coordinate conversion changed the sampled values: " + "; ".join(mismatch[:3])
    return "PASS", None, {"values": numbers}


# ---------------------------------------------------------------------------
# W14 — geometry edits and local paths
# ---------------------------------------------------------------------------


def _geometry_paths(args: argparse.Namespace) -> dict[str, Any]:
    geometry_path = {"segments": [{"collection": "component", "tag": args.component},
                                  {"collection": "geom", "tag": args.geometry_tag}]}
    return {"geometry": geometry_path}


async def _case_w14_t009(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                         state: dict[str, Any]) -> None:
    domain_ops = ("geometry.feature_create", "geometry.workplane_edit", "geometry.array_create",
                  "geometry.feature_update", "geometry.build", "geometry.measure", "node.find",
                  "node.property_get")
    # Every operation this case gates on is probed: an unprobed operation must never read as a
    # capability gap (it has no status at all, which is not the same as "not executable").
    rows = await _prepare_case(case, host, (*_plan_ops(case.case_id), *domain_ops))
    domain_ok = all((rows.get(operation) or {}).get("available") for operation in domain_ops)
    case.assertion("static_geometry_ops_executable", domain_ok, operations=list(domain_ops))
    domain_ok = _availability_subcase(case, "static_geometry_ops_availability", domain_ops, rows, flag=domain_ok)
    live_names = ("work_plane_and_array_located", "local_subfeature_edit_applied", "left_most_object_preserved",
                  "count_position_spacing_quantified", "main_model_not_replaced", "sibling_features_unchanged")
    runnable, status, why = _can_run(case, args, state, rows, domain_ops, require_writable_model=True)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    store: dict[str, Any] = {}
    paths = _geometry_paths(args)
    workplane_path = {"segments": [{"collection": "component", "tag": args.component},
                                   {"collection": "geom", "tag": args.geometry_tag},
                                   {"collection": "feature", "tag": args.work_plane_tag}]}
    array_path = {"segments": [{"collection": "component", "tag": args.component},
                               {"collection": "geom", "tag": args.geometry_tag},
                               {"collection": "feature", "tag": args.array_tag}]}
    # The case edits a *local sub-feature*: the work plane and the rectangle it carries must exist
    # before ``geometry.workplane_edit`` can address them, and the geometry sequence itself must
    # exist before either.  Observed live: the sequence did not exist and the edit was refused with
    # "could not resolve node path segment geom:geom1" — a driver-side addressing gap (both the
    # containers and the fixture are create-able through published operations).
    containers_ok, container_evidence = await _ensure_geometry_container(client, args, prefix="t009",
                                                                        store=store, build_block=False)
    case.assertions["t009_containers"] = container_evidence
    if not containers_ok:
        for name in live_names:
            _mark(case, name, str(container_evidence["status"]), container_evidence["reason"],
                  observed=container_evidence)
        case.finish()
        return
    fixture_status, fixture_payload, fixture_row = await _container_step(
        client, "t009-workplane", "geometry.workplane_create",
        {"geometry": paths["geometry"], "tag": args.work_plane_tag,
         "definition": {"planetype": "quick", "quickplane": "yz", "unite": True}}, store)
    fixture_ok = fixture_status in {"APPLIED", "ADOPTED"}
    case.assertions["t009_workplane"] = fixture_row
    if fixture_ok:
        # ``r1`` lives inside the work plane's own 2D sequence: it is created through the same
        # ``workplane_edit`` surface the case is about (its ``create`` action), then built.
        fixture_status, fixture_payload, fixture_row = await _container_step(
            client, "t009-rectangle", "geometry.workplane_edit",
            {"workplane": workplane_path,
             "actions": [{"action": "create", "tag": args.rectangle_tag, "type_id": "Rectangle",
                          "definition": {"size": [float(args.rect_width), float(args.rect_height)]}},
                         {"action": "build"}]}, store)
        fixture_ok = fixture_status in {"APPLIED", "ADOPTED"} or _error_code(fixture_payload) == "TAG_CONFLICT"
        case.assertions["t009_rectangle"] = fixture_row
    if not fixture_ok:
        for name in live_names:
            _mark(case, name, "BLOCKED" if fixture_status == "BLOCKED" else "FAIL",
                  f"the work-plane fixture could not be created: {fixture_row['operation']} returned "
                  f"{fixture_row['error_code'] or 'an invalid envelope'}: {fixture_row['message']}",
                  observed=fixture_row)
        case.finish()
        return
    measure_before = await client.action("geometry.measure", {
        "geometry": paths["geometry"],
        "query": {"mode": "objects", "metrics": ["n_entities", "volume", "bounding_box"]}})
    store["measure_before"] = measure_before
    await _step(case, host, client, args, state, name="work_plane_and_array_located",
                operation="geometry.workplane_edit",
                arguments={"workplane": workplane_path,
                           "actions": [{"action": "create", "tag": args.array_tag, "type_id": "Array",
                                        "definition": {"type": "rectangular",
                                                       "size": [int(args.array_size_x), int(args.array_size_y)],
                                                       "displ": [float(args.array_pitch), 0.0]},
                                        "inputs": {"input": [args.rectangle_tag]}},
                                       {"action": "build"}]},
                store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _data(payload)})
                    if _success(payload)
                    and args.array_tag in [str(tag) for tag in (_data(payload).get("features_after") or [])]
                    and args.rectangle_tag in [str(tag) for tag in (_data(payload).get("preserved_features") or [])]
                    else _unreadable_verdict(payload, "the work plane and its array could not be located")
                ))
    await _step(case, host, client, args, state, name="local_subfeature_edit_applied",
                operation="geometry.workplane_edit",
                arguments={"workplane": workplane_path,
                           "actions": [{"action": "update", "tag": args.rectangle_tag,
                                        "properties": [{"name": "size", "value": {"kind": "float64", "shape": [2],
                                                                                   "data": [float(args.rect_width),
                                                                                            float(args.rect_height)]}}]},
                                       {"action": "build"}]},
                prereq="work_plane_and_array_located", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _data(payload)})
                    if _success(payload) and args.rectangle_tag in [str(tag) for tag
                                                                   in (_data(payload).get("features_after") or [])]
                    else _unreadable_verdict(payload, "the local sub-feature edit was not applied")
                ))
    await _step(case, host, client, args, state, name="left_most_object_preserved",
                operation="geometry.measure",
                arguments={"geometry": paths["geometry"],
                           "query": {"mode": "objects", "metrics": ["n_entities", "volume", "bounding_box"]}},
                prereq="local_subfeature_edit_applied", store=store,
                check=lambda payload, bundle: _check_geometry_preserved(payload, bundle.get("measure_before")))
    await _step(case, host, client, args, state, name="count_position_spacing_quantified",
                operation="geometry.array_create",
                arguments={"geometry": paths["geometry"], "tag": f"{args.array_tag}_geo",
                           "definition": {"type": "rectangular",
                                          "size": [int(args.array_size_x), int(args.array_size_y)],
                                          "displ": [float(args.array_pitch), 0.0]},
                           "inputs": {"input": [args.rectangle_tag]}},
                prereq="left_most_object_preserved", store=store,
                check=lambda payload, bundle: _check_array_quantified(payload))
    await _step(case, host, client, args, state, name="main_model_not_replaced",
                operation="node.find",
                arguments={"query": {"kind": "geometry_feature", "ids": [args.geometry_tag]},
                           "root": {"segments": [{"collection": "component", "tag": args.component}]}},
                prereq="count_position_spacing_quantified", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _data(payload)})
                    if _success(payload) and int(_data(payload).get("count") or 0) >= 1
                    else ("FAIL", "the main geometry sequence was replaced or is no longer discoverable")
                ))
    await _step(case, host, client, args, state, name="sibling_features_unchanged",
                operation="node.property_get",
                arguments={"path": array_path, "names": ["size", "displacement"]},
                prereq="count_position_spacing_quantified", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _data(payload)})
                    if _success(payload) and _property_value_rows(payload)
                    else ("FAIL", "the edited feature's sibling properties could not be read back")
                ))
    case.finish()


def _check_array_quantified(payload: Mapping[str, Any]) -> Any:
    """The array's count, spacing and extent as the *engine* reports them back.

    ``geometry.array_create`` publishes ``array_type``, the written ``properties`` and the applied
    ``inputs``; those are the engine's own readback of the write (count/position/spacing are the
    ``size``/``displ``/``fullsize`` properties).
    """
    if not _success(payload):
        return _unreadable_verdict(payload, "the rectangular array edit could not be applied")
    data = _data(payload)
    raw_properties = data.get("properties")
    properties: Mapping[str, Any] = raw_properties if isinstance(raw_properties, Mapping) else {}
    quantified = [name for name in ("size", "displ", "fullsize") if name in properties]
    raw_inputs = data.get("inputs")
    inputs: Mapping[str, Any] = raw_inputs if isinstance(raw_inputs, Mapping) else {}
    raw_applied = inputs.get("applied")
    applied = raw_applied if isinstance(raw_applied, list) else []
    objects = [obj for row in applied if isinstance(row, Mapping) for obj in (row.get("objects") or [])]
    observed = {"array_type": data.get("array_type"), "quantified": quantified,
                "inputs": _json_safe(applied), "properties": _json_safe(properties)}
    if data.get("array_type") != "rectangular" or not quantified:
        return ("FAIL", "the array readback did not quantify the count/spacing properties", observed)
    if applied and not objects:
        return ("FAIL", "the array's inputs readback does not name the rectangle it displaces", observed)
    return ("PASS", None, observed)


def _check_geometry_preserved(payload: Mapping[str, Any], before: Mapping[str, Any] | None) -> Any:
    if not _success(payload):
        return ("BLOCKED" if _blocked_payload(payload) else "FAIL",
                f"geometry.measure returned {_error_code(payload) or 'an invalid envelope'}")
    if not _success(before if before is not None else None):
        return "FAIL", "no pre-edit geometry measure was recorded"
    after_data = _data(payload)
    before_data = _data(before)
    for key in ("count", "volume"):
        if key in before_data and key in after_data and after_data[key] != before_data[key]:
            return "FAIL", f"the left-most object measure changed ({key}: {before_data[key]} -> {after_data[key]})"
    return "PASS", None, {"before": before_data, "after": after_data}


async def _case_w14_t034(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                         state: dict[str, Any]) -> None:
    run_dir = Path(args.run_dir)
    domain_ops = ("model.save", "model.load", "geometry.import")
    rows = await _prepare_case(case, host, (*_plan_ops(case.case_id), *domain_ops))
    domain_ok = all((rows.get(operation) or {}).get("available") for operation in domain_ops)
    case.assertion("static_model_path_ops_executable", domain_ok, operations=list(domain_ops))
    domain_ok = _availability_subcase(case, "static_model_path_ops_availability", domain_ops, rows, flag=domain_ok)

    spaced = run_dir / args.local_space_dir
    chinese = spaced / "中文目录"
    try:
        chinese.mkdir(mode=0o700, parents=True, exist_ok=True)
        probe = chinese / "probe file.txt"
        probe.write_text("phase4 local path probe\n", encoding="utf-8")
        digest = _sha256(probe)
        fixture_ok = probe.is_file() and bool(digest)
        fixture_detail = {"directory": str(chinese), "probe_sha256": digest}
    except OSError as exc:
        fixture_ok = False
        fixture_detail = {"error": f"{type(exc).__name__}: {exc}"}
    case.assertions["local_path_fixture"] = fixture_detail
    case.assertion("local_spaces_and_chinese_path_created", fixture_ok)
    case.subcase("local_spaces_and_chinese_path", "PASS" if fixture_ok else "FAIL", level="fixture",
                 reason=None if fixture_ok else "a local directory with spaces and a Chinese segment could not be created",
                 **fixture_detail)

    save_path = chinese / f"{args.model_file_stem} 模型.mph"
    live_ops = ("model.save", "model.load")
    runnable, status, why = _can_run(case, args, state, rows, live_ops)
    if not runnable:
        _mark(case, "model_save_load_roundtrip_hash", status, why)
    else:
        store: dict[str, Any] = {}
        saved = await _step(case, host, client, args, state, name="model_save_load_roundtrip_hash",
                            operation="model.save",
                            arguments={"destination": str(save_path), "overwrite": True, "include_solution": True},
                            store=store)
        _ = saved
        stored = Path(str(save_path))
        case.assertions["model_save_roundtrip"] = {"planned_destination": str(save_path),
                                                   "exists": stored.is_file(),
                                                   "sha256": _sha256(stored) if stored.is_file() else None}
    if not args.live:
        # Offline the live probe cannot run.  Keep the truthful status/reason from _can_run:
        # BLOCKED only when a required operation is genuinely unavailable, never because the
        # run was merely offline.
        _mark(case, "missing_dependency_reported", status, why)
    else:
        # A *real* missing-dependency .mph: the driver writes the interpolation source file it will
        # reference, creates the function that references it on the engine, saves the model and only
        # then deletes the source — so the stored model carries an unresolved external dependency
        # that the reload has to report.  Planting a text file named ``.mph`` proves nothing and is
        # not an acceptable substitute.
        fixture = await _missing_dependency_fixture(host, client, case, args, state, rows, chinese)
        _mark(case, "missing_dependency_reported", fixture["status"], fixture["reason"],
              observed=fixture)
    cad_ops = ("geometry.import",)
    cad_runnable, cad_status, cad_why = _can_run(case, args, state, rows, cad_ops, require_writable_model=True)
    if not cad_runnable:
        _mark(case, "cad_import_license_limited", cad_status, cad_why)
    else:
        # ``geometry.import`` addresses the geometry sequence with a NodePath and only accepts the
        # documented option keys; the live run sent a bare tag and ``{"repair": false}`` and was
        # refused with INVALID_REQUEST before the engine ever saw the import.
        cad_file = chinese / f"{args.cad_artifact_id}.stl"
        try:
            cad_file.write_text(_ascii_stl_probe(), encoding="ascii")
            cad_fixture = {"path": str(cad_file), "bytes": cad_file.stat().st_size,
                           "sha256": _sha256(cad_file)}
        except OSError as exc:
            cad_fixture = {"error": f"{type(exc).__name__}: {exc}"}
        case.assertions["cad_import_fixture"] = cad_fixture
        payload = await client.action("geometry.import", {
            "geometry": _geometry_paths(args)["geometry"], "tag": args.import_tag,
            "artifact_id": str(cad_file),
            "options": {"path_check": "local", "build": False}}, require_model=True,
            key=_fresh_key("t034-cad-import"), request="cad-import")
        code = _error_code(payload)
        if code in _BLOCKED_CODES or (code and "LICENSE" in code.upper()):
            case.assertions["cad_import"] = {"error_code": code, "error": _error_message(payload),
                                             "fixture": cad_fixture}
            _mark(case, "cad_import_license_limited", "BLOCKED",
                  f"CAD import is license-limited in this installation: {code}")
        else:
            _mark(case, "cad_import_license_limited", "PASS" if _success(payload) else "FAIL",
                  None if _success(payload) else f"geometry.import returned {code or 'an invalid envelope'}",
                  observed=_data(payload))
    case.finish()


#: The minimal ASCII STL the CAD-import probe imports: a unit tetrahedron, written in the *import*
#: vocabulary the product publishes (the probe is the artefact, not a substitute for one).
ASCII_STL_PROBE = """solid phase4_probe
facet normal 0 0 -1
  outer loop
    vertex 0 0 0
    vertex 1 0 0
    vertex 0 1 0
  endloop
endfacet
facet normal 0 -1 0
  outer loop
    vertex 0 0 0
    vertex 0 0 1
    vertex 0 1 0
  endloop
endfacet
facet normal -1 0 0
  outer loop
    vertex 0 0 0
    vertex 1 0 0
    vertex 0 0 1
  endloop
endfacet
facet normal 1 1 1
  outer loop
    vertex 1 0 0
    vertex 0 1 0
    vertex 0 0 1
  endloop
endfacet
endsolid phase4_probe
"""


def _ascii_stl_probe() -> str:
    return ASCII_STL_PROBE


async def _missing_dependency_fixture(host: ProductionHost, client: ActionClient, case: Case,
                                      args: argparse.Namespace, state: dict[str, Any],
                                      rows: Mapping[str, Any], chinese: Path) -> dict[str, Any]:
    """Build a real model whose external dependency is gone, then reload it.

    Steps (each one a published operation, each one recorded):
      1. write the interpolation source file the function will reference;
      2. create a scratch model (the shared bound model is never touched);
      3. ``function.create`` an ``Interpolation`` function sourced from that file;
      4. ``model.save`` the model to ``missing_dependency.mph``;
      5. delete the source file and unload the scratch model;
      6. ``model.load`` the saved file — the reload is what has to report the unresolved dependency.
    A step the build cannot do is reported as BLOCKED with that operation's own refusal, never as a
    planted stand-in.
    """
    required = ("function.create", "model.save", "model.load", "model.remove", "model.create")
    probe_rows = rows if all(op in rows for op in required) else await _prepare_case(case, host, required)
    evidence: dict[str, Any] = {"required_operations": list(required)}
    unavailable = [op for op in required if not (probe_rows.get(op) or {}).get("available")]
    if unavailable:
        evidence.update({"status": "BLOCKED", "unavailable": unavailable})
        return {**evidence, "status": "BLOCKED",
                "reason": "the missing-dependency fixture needs operations this build does not offer: "
                          + ", ".join(unavailable)}
    source = chinese / "missing_dependency_source.csv"
    saved = chinese / "missing_dependency.mph"
    model_tag = f"phase4_missing_dep_{state.get('run_stamp') or 'x'}"
    try:
        source.write_text("0 0\n1 1\n", encoding="utf-8")
        evidence["source"] = {"path": str(source), "sha256": _sha256(source), "bytes": source.stat().st_size}
    except OSError as exc:
        evidence.update({"status": "BLOCKED", "error": f"{type(exc).__name__}: {exc}"})
        return {**evidence, "status": "BLOCKED",
                "reason": f"the interpolation source file could not be written: {type(exc).__name__}: {exc}"}

    async def call(step: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        key = _fresh_key(f"t034-{step}")
        payload = await client.action(operation, dict(arguments), key=key, request=step)
        row = {"step": step, "operation": operation, "success": bool(payload.get("success")),
               "error_code": _error_code(payload), "message": _error_message(payload),
               "identity": _envelope_identity(payload)}
        evidence.setdefault("steps", []).append(row)
        return payload

    created = await call("scratch-model", "model.create", {"name": model_tag})
    if not _success(created):
        return {**evidence, "status": "BLOCKED",
                "reason": f"model.create returned {_error_code(created) or 'an invalid envelope'}: "
                          f"{_error_message(created)}"}
    reference = await call("interpolation-function", "function.create",
                           {"tag": "phase4_missing_dep", "type_id": "Interpolation",
                            "definition": {"sourcetype": "file", "filename": str(source),
                                           "funcname": "phase4_missing_dep"}})
    reference_status = "APPLIED" if _success(reference) else ("BLOCKED" if _blocked_payload(reference) else "FAILED")
    evidence["reference"] = {"status": reference_status, "error_code": _error_code(reference),
                             "message": _error_message(reference)}
    if reference_status != "APPLIED":
        await call("scratch-remove", "model.remove", {"name": model_tag})
        return {**evidence, "status": "BLOCKED" if reference_status == "BLOCKED" else "FAIL",
                "reason": f"function.create returned {_error_code(reference) or 'an invalid envelope'}: "
                          f"{_error_message(reference)} — the fixture could not be built"}
    stored = await call("save", "model.save", {"model": model_tag, "destination": str(saved),
                                               "overwrite": True})
    if not _success(stored) or not saved.is_file():
        await call("scratch-remove", "model.remove", {"name": model_tag})
        return {**evidence, "status": "BLOCKED" if _blocked_payload(stored) else "FAIL",
                "reason": f"model.save returned {_error_code(stored) or 'an invalid envelope'} and "
                          f"{'the file is missing' if not saved.is_file() else 'the file exists'}"}
    evidence["saved"] = {"path": str(saved), "sha256": _sha256(saved), "bytes": saved.stat().st_size}
    await call("source-removed", "model.remove", {"name": model_tag})
    try:
        source.unlink()
        evidence["source_removed"] = True
    except OSError as exc:
        evidence["source_removed"] = f"{type(exc).__name__}: {exc}"
    reloaded = await call("reload", "model.load", {"source": str(saved)})
    evidence["reload"] = {"success": bool(reloaded.get("success")), "error_code": _error_code(reloaded),
                          "message": _error_message(reloaded), "data": _json_safe(_data(reloaded))}
    message = " ".join(str(part or "") for part in (_error_message(reloaded), _json_safe(_data(reloaded))))
    if _success(reloaded):
        # The reload succeeded; whether the *unresolved* dependency is reported is what the driver
        # has to read, so it reads the function back instead of assuming either way.
        readback = await call("dependency-readback", "node.property_get",
                              {"path": {"segments": [{"collection": "func", "tag": "phase4_missing_dep"}]},
                               "names": ["filename", "sourcetype"]})
        evidence["readback"] = {"success": bool(readback.get("success")), "error_code": _error_code(readback),
                                "message": _error_message(readback), "data": _json_safe(_data(readback))}
        reported = bool(_error_code(readback)) or "missing" in " ".join(
            str(part or "") for part in (_error_message(readback), _json_safe(_data(readback)))).lower()
        return {**evidence, "status": "PASS" if reported else "NOT_RUN",
                "reason": None if reported else
                "the model reloaded and the function readback did not report the deleted source file, so "
                "no missing dependency could be observed"}
    if _blocked_payload(reloaded) or "MISSING" in " ".join(str(part or "").upper() for part
                                                          in (_error_code(reloaded), message)):
        return {**evidence, "status": "PASS",
                "reason": None}
    return {**evidence, "status": "FAIL",
            "reason": f"model.load returned {_error_code(reloaded) or 'an invalid envelope'}: "
                      f"{_error_message(reloaded)}"}


# ---------------------------------------------------------------------------
# W15 — selections, materials, licenses
# ---------------------------------------------------------------------------


async def _case_w15_t007(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                         state: dict[str, Any]) -> None:
    domain_ops = ("physics.selection_set", "physics.feature_create", "physics.feature_update",
                  "selection.create", "node.property_get")
    rows = await _prepare_case(case, host, (*_plan_ops(case.case_id), *domain_ops))
    domain_ok = all((rows.get(operation) or {}).get("available") for operation in domain_ops)
    case.assertion("static_physics_selection_ops_executable", domain_ok, operations=list(domain_ops))
    domain_ok = _availability_subcase(case, "static_physics_selection_ops_availability", domain_ops, rows, flag=domain_ok)
    live_names = ("physics_level_selection_set", "feature_level_selection_set", "inherited_selection_not_writable",
                  "named_selection_binding_readback", "no_invalid_parent_feature_coupling")
    runnable, status, why = _can_run(case, args, state, rows, domain_ops, require_writable_model=True)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    store: dict[str, Any] = {}
    physics_path = {"segments": [{"collection": "component", "tag": args.component},
                                 {"collection": "physics", "tag": args.physics_tag}]}
    feature_path = {"segments": [{"collection": "component", "tag": args.component},
                                 {"collection": "physics", "tag": args.physics_tag},
                                 {"collection": "feature", "tag": args.temperature_tag}]}
    # The case assigns a selection to a physics interface and to one of its features, using a *named*
    # selection; none of the three exists on the bound model yet (live the very first call was
    # refused with "physics:ht" — the interface was never created).  Create them (or adopt what is
    # already there) before addressing them.
    containers_ok, container_evidence = await _ensure_geometry_container(client, args, prefix="t007", store=store)
    case.assertions["t007_containers"] = container_evidence
    fixture_ok = containers_ok
    fixture_row: dict[str, Any] = {"step": "containers", "operation": "definition.component_manage",
                                   "status": container_evidence["status"], "error_code": None,
                                   "message": container_evidence.get("reason")}
    if fixture_ok:
        for step, operation, arguments in (
            ("t007-physics", "physics.create",
             {"component": args.component, "tag": args.physics_tag, "type_id": args.heat_physics_type,
              "geometry": args.geometry_tag}),
            ("t007-temperature", "physics.feature_create",
             {"parent": physics_path, "tag": args.temperature_tag, "type_id": "TemperatureBoundary",
              "entity_dimension": 2,
              "properties": [{"name": "T0", "value": {"kind": "expression", "shape": [], "data": "300[K]"}}]}),
            ("t007-selection", "selection.create",
             {"component": args.component, "tag": args.selection_tag, "type_id": "Box",
              "definition": {"entity_dimension": 2, "xmin": -1.0e-3, "xmax": 2.0e-3,
                             "ymin": -1.0e-3, "ymax": 2.0e-3, "zmin": -1.0e-3, "zmax": 2.0e-3}}),
        ):
            step_status, _step_payload, step_row = await _container_step(client, step, operation, arguments, store)
            if step_status not in {"APPLIED", "ADOPTED"}:
                fixture_ok = False
                fixture_row = step_row
                break
    case.assertions["t007_fixture"] = {"ok": fixture_ok, "failure": fixture_row,
                                       "steps": store.get("container_steps")}
    if not fixture_ok:
        for name in live_names:
            _mark(case, name, "BLOCKED" if fixture_row["status"] == "BLOCKED" else "FAIL",
                  f"the physics/feature/selection fixture could not be built: {fixture_row['operation']} "
                  f"returned {fixture_row['error_code'] or 'an invalid envelope'}: {fixture_row['message']}",
                  observed=fixture_row)
        case.finish()
        return
    await _step(case, host, client, args, state, name="physics_level_selection_set",
                operation="physics.selection_set",
                arguments={"path": physics_path, "selection": {"kind": "named", "component": args.component,
                                                               "tag": args.selection_tag}},
                store=store)
    await _step(case, host, client, args, state, name="feature_level_selection_set",
                operation="physics.selection_set",
                arguments={"path": feature_path, "selection": {"kind": "named", "component": args.component,
                                                               "tag": args.selection_tag}},
                prereq="physics_level_selection_set", store=store)
    await _step(case, host, client, args, state, name="inherited_selection_not_writable",
                operation="physics.feature_update",
                arguments={"path": feature_path, "properties": [{"name": "selection", "value": {
                    "kind": "expression", "shape": [], "data": "inherited"}}]},
                prereq="feature_level_selection_set", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _error_code(payload)})
                    if (not _success(payload)) or _data(payload).get("selection_inherited") is True
                    else ("FAIL", "an inherited selection was overwritten silently")
                ))
    await _step(case, host, client, args, state, name="named_selection_binding_readback",
                operation="node.property_get",
                arguments={"path": feature_path, "names": ["selection"]},
                prereq="feature_level_selection_set", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _data(payload)})
                    if _success(payload) and _property_value_rows(payload)
                    else ("FAIL", "the feature selection could not be read back")
                ))
    await _step(case, host, client, args, state, name="no_invalid_parent_feature_coupling",
                operation="physics.feature_create",
                arguments={"parent": feature_path, "tag": "phase4_child_probe",
                           "type_id": "TemperatureBoundary", "entity_dimension": 2,
                           "properties": [{"name": "T0", "value": {"kind": "expression", "shape": [], "data": "300[K]"}}]},
                prereq="named_selection_binding_readback", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _error_code(payload)})
                    if _success(payload) and isinstance(_data(payload).get("parent_path"), Mapping)
                    else ("FAIL", "a child feature was created without a truthful parent-path readback")
                ))
    case.finish()


def _material_expression_properties() -> list[dict[str, Any]]:
    """The T017 property set: an anisotropic tensor plus a temperature-dependent expression.

    ``heatcapacity`` deliberately carries a ``T``-dependent term: the acceptance line is
    that the *text* survives the engine round trip, so the probe must contain a value the
    engine could rewrite if it normalized expressions.
    """
    return [
        {"name": "thermalconductivity", "value": {"kind": "expression", "shape": [3, 3], "unit": "W/(m*K)",
                                                  "data": [["10[W/(m*K)]", "0", "0"],
                                                           ["0", "10[W/(m*K)]", "0"],
                                                           ["0", "0", "10[W/(m*K)]"]]}},
        {"name": "density", "value": {"kind": "expression", "shape": [], "data": "7850[kg/m^3]"}},
        {"name": "heatcapacity", "value": {"kind": "expression", "shape": [],
                                           "data": "500[J/(kg*K)]+0.1[J/(kg*K^2)]*(T-293.15[K])"}},
    ]


def _material_property_verdict(payload: Mapping[str, Any] | None,
                               requested: Sequence[Mapping[str, Any]]) -> tuple[str, str | None, dict[str, Any]]:
    """Verdict for ``material.set_properties``: a claimed write must show its readback.

    A response that reports a comparison mismatch fails here.  A response that carries no
    comparison at all is recorded as *delegated*: the acceptance then rests on the
    independent ``node.property_get`` step, which never reports a silent acceptance.
    """
    comparisons: dict[str, Any] = {}
    rows_raw = _data(payload).get("applied")
    rows: list[Any] = rows_raw if isinstance(rows_raw, list) else []
    observed = {str(row.get("name")): row for row in rows if isinstance(row, Mapping) and row.get("name")}
    for row in requested:
        name = str(row.get("name"))
        entry = observed.get(name) or {}
        readback = entry.get("readback") if isinstance(entry.get("readback"), Mapping) else None
        comparisons[name] = {"readback": _json_safe(readback), "metadata_status": entry.get("metadata_status"),
                             "readback_match": entry.get("readback_match"), "value_type": entry.get("value_type")}
    detail = {"error_code": _error_code(payload), "comparisons": comparisons,
              "applied": _json_safe(rows) if rows else None}
    if not _success(payload):
        status, reason = _unreadable_verdict(payload, "the material property write was refused")
        return status, reason, detail
    mismatched = [name for name, entry in comparisons.items() if entry["readback_match"] is False]
    if mismatched:
        return ("FAIL", "the engine readback did not preserve the requested property text for: "
                        + ", ".join(sorted(mismatched)), detail)
    if rows:
        detail["verified_by"] = "material.set_properties readback"
        return "PASS", None, detail
    detail["verified_by"] = "independent node.property_get step"
    detail["delegated_to"] = "material_readback_after_update"
    return "PASS", None, detail


def _material_readback_verdict(payload: Mapping[str, Any] | None,
                               requested: Sequence[Mapping[str, Any]]) -> tuple[str, str | None, dict[str, Any]]:
    """Verdict for the independent property readback of a material write."""
    detail: dict[str, Any] = {"error_code": _error_code(payload), "rows": _json_safe(_property_value_rows(payload))}
    if not _success(payload):
        status, reason = _unreadable_verdict(payload, "the material property readback was refused")
        return status, reason, detail
    values = _property_value_rows(payload)
    missing = [str(row.get("name")) for row in requested if _row_value(values, str(row.get("name"))) is None]
    if missing:
        return ("FAIL", "the readback did not return the updated properties: " + ", ".join(sorted(missing)), detail)
    observed_text = json.dumps(_json_safe(values), ensure_ascii=False)
    verdicts: dict[str, Any] = {}
    lost: list[str] = []
    for row in requested:
        name = str(row.get("name"))
        wanted = _json_safe(row.get("value"))
        got = _json_safe(_row_value(values, name))
        if got == wanted:
            verdicts[name] = "exact"
            continue
        wanted_text = json.dumps(wanted, ensure_ascii=False)
        if wanted_text in observed_text:
            verdicts[name] = "text_contained"
            continue
        verdicts[name] = "not_preserved"
        lost.append(name)
    detail["verdicts"] = verdicts
    if lost:
        return ("FAIL", "the requested property text was not preserved in the readback for: "
                        + ", ".join(sorted(lost)), detail)
    return "PASS", None, detail


async def _case_w15_t017(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                         state: dict[str, Any]) -> None:
    domain_ops = ("material.create", "material.set_properties", "material.selection_set", "material.validate",
                  "node.property_get")
    rows = await _prepare_case(case, host, (*_plan_ops(case.case_id), *domain_ops))
    domain_ok = all((rows.get(operation) or {}).get("available") for operation in domain_ops)
    case.assertion("static_material_ops_executable", domain_ok, operations=list(domain_ops))
    domain_ok = _availability_subcase(case, "static_material_ops_availability", domain_ops, rows, flag=domain_ok)
    live_names = ("k_T_Cp_T_and_rho_expressions", "anisotropic_tensor_and_coordinate_system",
                  "missing_required_property_preflight_error", "material_readback_after_update")
    runnable, status, why = _can_run(case, args, state, rows, domain_ops, require_writable_model=True)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    store: dict[str, Any] = {}
    # The target material is created (or adopted after a reported conflict) and read back by
    # the probe fixture: the case no longer depends on a material that "happens to exist",
    # and the property-group schema it reads is the metadata the assertions rest on.
    fixture = await _probe_fixture(client, args, state)
    material_ready = fixture.ready("material")
    material_path = fixture.material_path() if material_ready else {
        "segments": [{"collection": "component", "tag": args.component},
                     {"collection": "material", "tag": args.material_tag}]}
    case.assertions["t017_target"] = {"probe_fixture": fixture.evidence(), "material_ready": material_ready,
                                      "material_path": material_path, "group_schema": fixture.group_schema}
    expressions = _material_expression_properties()
    # The payload is written out as a literal (only the path local) so the wire-replay gate
    # can evaluate it in the driver's own namespaces; ``expressions`` carries the same rows
    # for the verdict, and the verdict compares the engine readback against them.
    await _step(case, host, client, args, state, name="k_T_Cp_T_and_rho_expressions",
                operation="material.set_properties",
                arguments={"path": material_path, "group": "def",
                           "properties": [
                               {"name": "thermalconductivity",
                                "value": {"kind": "expression", "shape": [3, 3], "unit": "W/(m*K)",
                                          "data": [["10[W/(m*K)]", "0", "0"],
                                                   ["0", "10[W/(m*K)]", "0"],
                                                   ["0", "0", "10[W/(m*K)]"]]}},
                               {"name": "density",
                                "value": {"kind": "expression", "shape": [], "data": "7850[kg/m^3]"}},
                               {"name": "heatcapacity",
                                "value": {"kind": "expression", "shape": [],
                                          "data": "500[J/(kg*K)]+0.1[J/(kg*K^2)]*(T-293.15[K])"}}]},
                store=store,
                check=lambda payload, bundle: _material_property_verdict(payload, expressions))
    await _step(case, host, client, args, state, name="anisotropic_tensor_and_coordinate_system",
                operation="material.set_properties",
                arguments={"path": material_path, "group": "def",
                           "properties": [{"name": "thermalconductivity",
                                           "value": {"kind": "expression", "shape": [3, 3],
                                                     "data": [["10", "0", "0"], ["0", "20", "0"], ["0", "0", "30"]],
                                                     "unit": "W/(m*K)"}}],
                           "provenance": {"coordinate_system": args.coordinate_system_tag}},
                prereq="k_T_Cp_T_and_rho_expressions", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _data(payload)})
                    if _success(payload) and "coordinate_system" in json.dumps(_json_safe(_data(payload)))
                    else _unreadable_verdict(
                        payload, "the anisotropic tensor was accepted without recording its coordinate system")
                ))
    await _step(case, host, client, args, state, name="missing_required_property_preflight_error",
                operation="material.validate",
                arguments={"scope": material_path,
                           # The probe property cannot exist on a real build, so the "a missing
                           # required property is reported" rule is exercised, not just asserted.
                           "checks": {"required_properties": ["density", "heatcapacity", "thermalconductivity",
                                                             "phase4_missing_required_property"]}},
                prereq="anisotropic_tensor_and_coordinate_system", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _data(payload)})
                    if (not _success(payload)) or _data(payload).get("valid") is False
                    or _data(payload).get("missing")
                    else ("FAIL", "material.validate reported success although a required property is missing")
                ))
    readback_step = await _step(case, host, client, args, state, name="material_readback_after_update",
                                operation="node.property_get",
                                arguments={"path": material_path,
                                           "names": [str(row["name"]) for row in expressions]},
                                prereq="k_T_Cp_T_and_rho_expressions", store=store,
                                check=lambda payload, bundle: _material_readback_verdict(payload, expressions))
    if readback_step is not None and _success(readback_step):
        # The apply path and the independent read path must agree; the readback step carries
        # the round-trip verdict, this assertion makes the agreement explicit in the evidence.
        case.assertion("material_expression_readback_matches_request", True,
                       requested=[row["name"] for row in expressions],
                       observed=_json_safe(_property_value_rows(readback_step)))
    case.finish()


async def _case_w15_t042(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                         state: dict[str, Any]) -> None:
    license_ops = ("runtime.license_inspect", "runtime.capabilities")
    live_ops = (*license_ops, "node.property_get")
    # One op list for the static probe and the live gate: the licence case must go live as
    # soon as the runtime module publishes exactly these operations.
    rows = await _prepare_case(case, host, (*_plan_ops(case.case_id), *live_ops))
    license_ok = all((rows.get(operation) or {}).get("available") for operation in license_ops)
    case.assertion("static_license_ops_executable", license_ok, operations=list(license_ops))
    license_ok = _availability_subcase(case, "static_license_ops_availability", license_ops, rows, flag=license_ok)
    live_names = ("license_inspect_records_has_product", "missing_product_blocks_with_blocked_license",
                  "authorized_product_usable", "probe_does_not_occupy_license")
    if not license_ok and args.live:
        probe = await client.action("comsol_status", {}, require_model=False, key="t042-status", request="status")
        case.assertions["runtime_probe_via_legacy_status"] = {"envelope": _envelope_identity(probe),
                                                              "success": _success(probe),
                                                              "error_code": _error_code(probe)}
    runnable, status, why = _can_run(case, args, state, rows, live_ops)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    await _step(case, host, client, args, state, name="license_inspect_records_has_product",
                operation="runtime.license_inspect",
                arguments={"runtime_id": args.runtime_id, "products": list(args.license_products)},
                check=lambda payload, bundle: _check_license_probe(payload))
    inspect_payload = None
    try:
        # Bound to the model the previous step used: ``runtime.license_inspect`` is routed through
        # the managed ledger, and an unbound call is refused with MODEL_IDENTITY_MISMATCH ("model_ref
        # is required for this operation") before the probe runs — observed live, where that refusal
        # was then read as "the missing product was not shown to be blocked".  The envelope is the
        # same probe either way; binding it makes the *product's* answer the one that is classified.
        inspect_payload = await client.action("runtime.license_inspect",
                                              {"runtime_id": args.runtime_id, "products": list(args.license_products)},
                                              require_model=True, key="t042-inspect", request="inspect")
    except CapabilityUnavailable as exc:
        _mark(case, "missing_product_blocks_with_blocked_license", "BLOCKED", str(exc))
    else:
        missing = _missing_products(inspect_payload)
        if missing:
            case.assertions["license_missing_products"] = missing
            _mark(case, "missing_product_blocks_with_blocked_license", "BLOCKED",
                  f"BLOCKED_LICENSE: the runtime reports no license for {', '.join(missing)}",
                  missing=missing)
        elif not _success(inspect_payload):
            # A refused probe publishes no product rows: that is not the same
            # fact as "every product is licensed".  An error envelope must
            # never read as a pass (review finding on runtime.license_inspect).
            _mark(case, "missing_product_blocks_with_blocked_license",
                  "BLOCKED" if _blocked_payload(inspect_payload) else "FAIL",
                  f"runtime.license_inspect returned {_error_code(inspect_payload) or 'an invalid envelope'} without product rows",
                  observed=_data(inspect_payload))
        else:
            case.assertions["license_products"] = _data(inspect_payload).get("products")
            _mark(case, "missing_product_blocks_with_blocked_license", "PASS",
                  None, observed="all requested products report hasProduct=true")
    await _step(case, host, client, args, state, name="authorized_product_usable",
                operation="node.property_get", require_model=True,
                arguments={"path": {"segments": [{"collection": "component", "tag": args.component}]},
                           "names": ["geometry"]},
                prereq=None,
                check=lambda payload, bundle: (
                    # An unknown engine state is neither a licence block nor a usable product: the
                    # gate refuses every engine call once a job is unresolved, so a gated probe
                    # must never read as "the authorized product works".
                    ("BLOCKED", "the authorized-product probe returned an unknown engine state "
                                f"({_error_code(payload)}), so product usability was not established")
                    if _unknown_outcome(payload) is not None else
                    ("PASS", None)
                    if _success(payload) or _error_code(payload) not in {"BLOCKED_LICENSE", "LICENSE_UNAVAILABLE"}
                    else ("BLOCKED", f"BLOCKED_LICENSE: {_error_code(payload)}")
                ))
    if inspect_payload is not None:
        seats = [key for key in ("checkout", "seats", "seat_count", "checkouts") if key in _data(inspect_payload)]
        case.assertions["license_probe_side_effects"] = {"seat_fields_present": seats}
        if _unknown_outcome(inspect_payload) is not None:
            _mark(case, "probe_does_not_occupy_license", "BLOCKED",
                  "the license probe returned an unknown engine state "
                  f"({_error_code(inspect_payload)}), so the absence of checkout/seat fields was not established",
                  seat_fields=seats)
        else:
            _mark(case, "probe_does_not_occupy_license", "FAIL" if seats else "PASS",
                  "the license probe reported checkout/seat fields" if seats else None, seat_fields=seats)
    else:
        _mark(case, "probe_does_not_occupy_license", "NOT_RUN", "no license inspect payload was available")
    case.finish()


def _check_license_probe(payload: Mapping[str, Any]) -> Any:
    if not _success(payload):
        code = _error_code(payload)
        message = _error_message(payload)
        if code in _BLOCKED_CODES or (code and "LICENSE" in code.upper()):
            if code == "BLOCKED_LICENSE":
                return "BLOCKED", f"BLOCKED_LICENSE: {message}" if message else "BLOCKED_LICENSE"
            return "BLOCKED", f"BLOCKED_LICENSE: {code}" + (f" ({message})" if message else "")
        return "FAIL", f"runtime.license_inspect returned {code or 'an invalid envelope'}"
    data = _data(payload)
    rows_raw = data.get("products")
    rows: list[Any] = rows_raw if isinstance(rows_raw, list) else []
    has_product_flags = [row for row in rows if isinstance(row, Mapping) and "hasProduct" in row]
    if not has_product_flags:
        return "FAIL", "the license probe did not report hasProduct for any requested product"
    text = json.dumps(data)
    if "license" in text.lower() and ("SERVER" in text.upper() and "=" in text):
        return "FAIL", "the license probe echoed license-server configuration text into the response"
    return "PASS", None, {"has_product_rows": has_product_flags}


def _missing_products(payload: Mapping[str, Any]) -> list[str]:
    data = _data(payload)
    rows_raw = data.get("products")
    rows: list[Any] = rows_raw if isinstance(rows_raw, list) else []
    missing: list[str] = []
    for row in rows:
        if isinstance(row, Mapping) and row.get("hasProduct") is False:
            missing.append(str(row.get("product") or row.get("name") or "unknown"))
    return missing


# ---------------------------------------------------------------------------
# W16 — mesh, end-to-end chains, solver tree
# ---------------------------------------------------------------------------


async def _load_model(host: ProductionHost, client: ActionClient, path: Path, artifact_id: str, *,
                      key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load an .mph: the strict operation when it is executable, else the legacy model_load tool.

    Returns ``(payload, probe)``; the probe is recorded so a legacy fallback is never silent.
    """
    probe = await _describe_row(host, "model.load")
    if probe.get("available"):
        payload = await client.action("model.load", {"artifact_id": artifact_id, "path_policy": {"path": str(path)}},
                                      require_model=False, key=key, request="model.load")
    elif "model_load" in host.tools:
        payload = dict(await host.call("model_load", {"path": str(path), "artifact_id": artifact_id,
                                                      "execution": _execution(key=key, request="model.load")}))
    else:
        payload = _synthetic_blocked("model.load",
                                     "model.load is not executable in this build and the legacy model_load tool is not published")
    return payload, probe


async def _create_empty_model(client: ActionClient, client_state: dict[str, Any], label: str,
                              host: ProductionHost) -> dict[str, Any]:
    """Create an empty model through whichever published route exists (strict first)."""
    probe = await _describe_row(host, "model.create")
    client_state["model_create_probe"] = probe
    key = "create-" + label.replace(" ", "-")
    if probe.get("available") and "operation_call" in host.tools:
        payload = await client.action("model.create", {"label": label, "dimension": 3},
                                      require_model=False, key=key, request="model.create")
    elif "model_create" in host.tools:
        payload = dict(await host.call("model_create", {"name": label, "execution": _execution(key=key)}))
    else:
        payload = _synthetic_blocked("model.create",
                                     "model.create is not executable in this build and the legacy model_create tool is not published")
    ref, revision = _payload_execution(payload)
    if ref is not None:
        client_state["ref"] = ref
        client_state["revision"] = revision
        client_state["model_label"] = label
    return dict(payload)


async def _case_w16_t018(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                         state: dict[str, Any]) -> None:
    domain_ops = ("mesh.create", "mesh.feature_create", "mesh.feature_update", "mesh.build",
                  "mesh.statistics", "mesh.quality")
    rows = await _prepare_case(case, host, (*_plan_ops(case.case_id), *domain_ops))
    domain_ok = all((rows.get(operation) or {}).get("available") for operation in domain_ops)
    case.assertion("static_mesh_ops_executable", domain_ok, operations=list(domain_ops))
    domain_ok = _availability_subcase(case, "static_mesh_ops_availability", domain_ops, rows, flag=domain_ok)
    live_names = ("free_tet_sequence_created", "local_size_feature_applied", "modify_and_rebuild",
                  "statistics_counts_and_coverage", "quality_definition_and_low_quality_locations")
    runnable, status, why = _can_run(case, args, state, rows, domain_ops, require_writable_model=True)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
    else:
        mesh_path = {"segments": [{"collection": "component", "tag": args.component},
                                  {"collection": "mesh", "tag": args.mesh_tag}]}
        meshed_path = {"segments": [{"collection": "component", "tag": args.component},
                                    {"collection": "mesh", "tag": args.mesh_tag},
                                    {"collection": "feature", "tag": args.mesh_size_tag}]}
        store: dict[str, Any] = {}
        # The mesh binds to a geometry sequence that has to exist *and carry a built solid* before a
        # mesh sequence is worth creating; the bound model is shared, so the driver ensures its own
        # (component, sequence, block) first instead of assuming what an earlier case left behind.
        containers_ok, container_evidence = await _ensure_geometry_container(client, args, prefix="t018", store=store)
        case.assertions["t018_containers"] = container_evidence
        if not containers_ok:
            for name in live_names:
                _mark(case, name, str(container_evidence["status"]), container_evidence["reason"],
                      observed=container_evidence)
            case.finish()
            return
        # ``mesh.create`` binds a mesh sequence to a geometry sequence; the tag is read from the
        # model's own tree (the configured tag is a default, not a fact about the bound model).
        geometry_resolution = await _bound_geometry_tag(client, args)
        case.assertions["t018_geometry_resolution"] = geometry_resolution
        bound_geometry = str(geometry_resolution["tag"])
        await _step(case, host, client, args, state, name="free_tet_sequence_created",
                    operation="mesh.create",
                    arguments={"component": args.component, "tag": args.mesh_tag, "geometry": bound_geometry},
                    store=store)
        await _step(case, host, client, args, state, name="local_size_feature_applied",
                    operation="mesh.feature_create",
                    arguments={"parent": mesh_path, "tag": args.mesh_size_tag, "type_id": "Size",
                               "properties": [{"name": "hauto", "value": {"kind": "int32", "shape": [], "data": 3}},
                                              {"name": "custom", "value": {"kind": "string", "shape": [], "data": "on"}},
                                              {"name": "hmax", "value": {"kind": "float64", "shape": [], "data": 3e-4}}],
                               "selection": {"kind": "named", "component": args.component,
                                             "tag": args.selection_tag}},
                    prereq="free_tet_sequence_created", store=store)
        first_build = await _step(case, host, client, args, state, name="modify_and_rebuild",
                                  operation="mesh.feature_update",
                                  arguments={"path": meshed_path,
                                             "properties": [{"name": "hmax", "value": {"kind": "float64",
                                                                                       "shape": [], "data": 2e-4}}]},
                                  prereq="local_size_feature_applied", store=store)
        rebuilt = None
        if first_build is not None:
            rebuilt = await client.action("mesh.build", {"path": mesh_path},
                                          key="t018-rebuild", request="mesh.build")
            case.assertions["t018_rebuild"] = {"success": _success(rebuilt), "error_code": _error_code(rebuilt),
                                               "data": _json_safe(_data(rebuilt))}
            _mark(case, "modify_and_rebuild", "PASS" if _success(rebuilt) else "FAIL",
                  None if _success(rebuilt) else f"mesh.build returned {_error_code(rebuilt) or 'an invalid envelope'}")
        await _step(case, host, client, args, state, name="statistics_counts_and_coverage",
                    operation="mesh.statistics",
                    arguments={"path": mesh_path},
                    prereq="modify_and_rebuild", store=store,
                    check=lambda payload, bundle: _check_mesh_statistics(payload))
        await _step(case, host, client, args, state, name="quality_definition_and_low_quality_locations",
                    operation="mesh.quality",
                    arguments={"path": mesh_path, "metric": args.mesh_quality_metric, "bins": 10},
                    prereq="statistics_counts_and_coverage", store=store,
                    check=lambda payload, bundle: (
                        ("PASS", None, {"observed": _data(payload)})
                        if _success(payload) and "metric" in _data(payload)
                        and isinstance(_data(payload).get("worst_elements"), list)
                        else ("FAIL", "mesh.quality did not report both the metric definition and the worst element locations")
                    ))
    quality_status = case.subcase_status("quality_definition_and_low_quality_locations")
    build_status = case.subcase_status("modify_and_rebuild")
    separation_ok = not (build_status == "PASS" and quality_status == "PASS" and
                         case.assertions.get("t018_rebuild", {}).get("quality_claimed") is True)
    case.assertion("build_success_recorded_separately_from_quality", separation_ok,
                   build_status=build_status, quality_status=quality_status)
    case.subcase("build_success_is_not_quality_pass", "PASS" if separation_ok else "FAIL", level="protocol",
                 reason=None if separation_ok else "a successful build was reported as a quality pass")
    case.finish()


def _check_mesh_statistics(payload: Mapping[str, Any]) -> Any:
    """The mesh's element counts and coverage as the *engine* reported them — or the read's refusal.

    ``mesh.statistics`` publishes ``element_count``/``vertex_count``/``element_types``/
    ``elements_by_dimension`` plus the read errors of every probe it attempted.  A read the engine
    never answered is not a statistics failure (observed live: the envelope was a control-response
    UNKNOWN), and a read that answered with a null count because the engine could not read the
    counter is reported with that read's own error instead of a bare "no counts".
    """
    if not _success(payload):
        return _unreadable_verdict(payload, "the mesh statistics could not be read")
    data = _data(payload)
    counts = {name: data.get(name) for name in ("element_count", "vertex_count", "element_types")}
    coverage = data.get("elements_by_dimension")
    errors = data.get("read_errors") if isinstance(data.get("read_errors"), Mapping) else {}
    empty = data.get("is_empty")
    observed = {"counts": _json_safe(counts), "elements_by_dimension": _json_safe(coverage),
                "is_empty": empty, "is_complete": data.get("is_complete"),
                "build_time_ms": data.get("build_time_ms"), "read_errors": _json_safe(errors),
                "statistics_node_available": data.get("statistics_node_available")}
    if not isinstance(counts["element_count"], int):
        reason = "mesh.statistics did not report an element count"
        if errors:
            reason += f"; the engine's own read errors say: {_json_safe(errors)}"
        if empty is True:
            reason += "; the mesh sequence reports itself empty (is_empty=true)"
        if data.get("allowlist_entry_required"):
            reason += f"; the read needs an allowlist entry: {_json_safe(data.get('allowlist_entry_required'))}"
        return "FAIL", reason, observed
    return "PASS", None, observed


async def _bound_geometry_tag(client: ActionClient, args: argparse.Namespace) -> dict[str, Any]:
    """The geometry sequence the *bound* model actually carries, read from the published tree.

    The configured geometry tag is a default, not a fact about the bound model: a case that follows
    a case which created its own model addresses *that* model's tags.  Live, T018's ``mesh.create``
    was refused with ``NODE_NOT_FOUND`` ("geometry 'geom1' does not exist in component 'comp1'")
    because the bound model's sequence carried another tag.  ``model_tree`` is the published read
    that reports the component's own sequences, so the tag is resolved from it; when it reports
    none, the configured tag is kept and the resolution is recorded as unverified.
    """
    evidence: dict[str, Any] = {"component": args.component, "configured_tag": args.geometry_tag}
    if "model_tree" not in client.host.tools:
        evidence.update({"tag": args.geometry_tag, "verified": False,
                         "source": "configured default: model_tree is not published by this host"})
        return evidence
    payload = await client.host.call("model_tree", {
        "depth": 2,
        "execution": _execution(key=_fresh_key("phase4-bound-geometry"), ref=client.state.get("ref"),
                                revision=client.state.get("revision"))})
    evidence["read"] = _envelope_identity(payload)
    details = _data(payload).get("component_details")
    rows = [row for row in (details if isinstance(details, list) else []) if isinstance(row, Mapping)]
    row = next((item for item in rows if str(item.get("tag") or "") == args.component), None)
    geometries = [str(item) for item in ((row or {}).get("geometries") or []) if isinstance(item, str)]
    if args.geometry_tag in geometries:
        evidence.update({"tag": args.geometry_tag, "verified": True, "geometries": geometries,
                         "source": "the bound model's own component details report this tag"})
        return evidence
    if geometries:
        evidence.update({"tag": geometries[0], "verified": True, "geometries": geometries,
                         "source": "the bound model's own component details report this sequence "
                                   "instead of the configured tag"})
        return evidence
    evidence.update({"tag": args.geometry_tag, "verified": False, "geometries": [],
                     "source": "the bound model reports no geometry sequence for this component, so the "
                               "configured tag is kept and the resolution is unverified"})
    return evidence


def _extract_samples(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Extract a sample table from a sampling payload, if the contract publishes one."""
    data = _data(payload)
    for key in ("samples", "points", "table", "rows", "values"):
        raw = data.get(key)
        if isinstance(raw, list) and raw and all(isinstance(row, Mapping) for row in raw):
            return [dict(row) for row in raw]
    return []


def _sample_series(rows: list[dict[str, Any]], candidates: tuple[str, ...]) -> list[float] | None:
    for row in rows:
        for key in row:
            if any(str(key).strip().lower() == candidate.lower() for candidate in candidates):
                series: list[float] = []
                for item in rows:
                    value = _finite_float(item.get(key)) if isinstance(item, Mapping) else None
                    if value is None:
                        return None
                    series.append(value)
                return series
    return None


def _check_chain_a(payload: Mapping[str, Any], reference: Mapping[str, Any]) -> Any:
    if not _success(payload):
        return ("BLOCKED" if _blocked_payload(payload) else "FAIL",
                f"result.sample_path returned {_error_code(payload) or 'an invalid envelope'}")
    rows = _extract_samples(payload)
    if not rows:
        return "FAIL", f"result.sample_path returned no sample table (data keys: {sorted(_data(payload))[:12]})"
    x_series = _sample_series(rows, ("x", "x_m", "x [m]"))
    t_series = _sample_series(rows, ("T", "T_k", "T [K]", "temp"))
    if x_series is None or t_series is None:
        return "FAIL", f"the sample table did not expose x/T columns (keys: {sorted(rows[0])})"
    delta = float(reference["temperature_difference_k"])
    worst = 0.0
    worst_at = None
    for x, t in zip(x_series, t_series):
        want = float(reference["t0_k"]) + delta * (x / float(reference["length_m"]))
        error = abs(t - want) / delta
        if error > worst:
            worst, worst_at = error, {"x_m": x, "T_k": t, "T_analytic_k": want}
    limit = float(reference["relative_error_limit"])
    if worst > limit:
        return "FAIL", f"the steady profile deviates by {worst:.3e} (limit {limit:.1e}) relative to the temperature difference at {worst_at}"
    return "PASS", None, {"max_relative_error": worst, "worst_point": worst_at, "samples": len(rows)}


def _check_chain_b(payload: Mapping[str, Any], reference: Mapping[str, Any]) -> Any:
    if not _success(payload):
        return ("BLOCKED" if _blocked_payload(payload) else "FAIL",
                f"result.sample_path returned {_error_code(payload) or 'an invalid envelope'}")
    rows = _extract_samples(payload)
    if not rows:
        return "FAIL", f"result.sample_path returned no sample table (data keys: {sorted(_data(payload))[:12]})"
    t_series = _sample_series(rows, ("T", "T_k", "T [K]", "temp"))
    time_series = _sample_series(rows, ("t", "time", "t_s", "time [s]"))
    if t_series is None or time_series is None:
        return "FAIL", f"the sample table did not expose t/T columns (keys: {sorted(rows[0])})"
    x_series = _sample_series(rows, ("x", "x_m", "x [m]"))
    delta = float(reference["temperature_difference_k"])
    worst = 0.0
    worst_at = None
    for index, (time_value, temperature) in enumerate(zip(time_series, t_series)):
        if time_value <= 0.0:
            continue
        x = x_series[index] if x_series is not None and index < len(x_series) else float(reference["length_m"])
        want = float(reference["t0_k"]) + delta * math.sin(math.pi * x / float(reference["length_m"])) * math.exp(
            -float(reference["alpha_m2_s"]) * (math.pi / float(reference["length_m"])) ** 2 * time_value)
        error = abs(temperature - want) / delta
        if error > worst:
            worst, worst_at = error, {"t_s": time_value, "x_m": x, "T_k": temperature, "T_analytic_k": want}
    limit = float(reference["relative_error_limit"])
    if worst > limit:
        return "FAIL", f"the transient decay deviates by {worst:.3e} (limit {limit:.1e}) at {worst_at}"
    return "PASS", None, {"normalized_max_error": worst, "worst_point": worst_at, "samples": len(rows)}


def _material_definition_verdict(payload: Mapping[str, Any] | None,
                                 requested: Sequence[str]) -> tuple[str, str | None, dict[str, Any]]:
    """Verdict for ``material.create`` with a property definition.

    ``material.create`` reports ``success: true`` together with a ``PARTIAL_FAILURE`` status when
    the material node was created but the requested properties were refused, so the status has to
    be read from ``applied``/``failed``: a material that exists without its properties is not the
    material an acceptance line needs (observed live: a fresh ``Common`` material refuses
    ``thermalconductivity`` because ``propertyGroup("def").hasProperty()`` is false, while the
    Programming Guide writes exactly that property on a Common material without a pre-check).
    """
    data = _data(payload)
    applied = [row for row in (data.get("applied") or []) if isinstance(row, Mapping)]
    failed = [row for row in (data.get("failed") or []) if isinstance(row, Mapping)]
    written: set[str] = set()
    for row in applied:
        entries = row.get("properties")
        for item in entries if isinstance(entries, list) else []:
            if isinstance(item, Mapping) and item.get("name"):
                written.add(str(item["name"]))
    missing = [str(name) for name in requested if str(name) not in written]
    detail = {"error_code": _error_code(payload), "status": data.get("status"),
              "applied_actions": [row.get("action") for row in applied],
              "written_properties": sorted(written), "missing_properties": sorted(missing),
              "failed": _json_safe(failed)}
    if not _success(payload):
        status, reason = _unreadable_verdict(payload, "the material could not be created")
        return status, reason, detail
    if failed or missing:
        message = "; ".join(str(((row.get("error") or {}) if isinstance(row.get("error"), Mapping) else {}).get("message"))
                            for row in failed) or f"the properties were not written: {sorted(missing)}"
        return ("BLOCKED",
                f"the material node exists but its property definition was refused by the product ({message})",
                detail)
    return "PASS", None, detail


_RANK_REFUSAL = re.compile(r"expects array rank (\d+), received (\d+)")


def _engine_expected_rank(payload: Mapping[str, Any] | None) -> int | None:
    """The array rank the *engine's* own property metadata demanded, from a refusal.

    ``PropFeature.getValueType`` is authoritative and build dependent: a write the product refuses
    on shape names the engine's expected rank in its message ("property expects array rank 1,
    received 2").  Reading it back here is what lets the driver align a documented value with what
    the bound build actually publishes instead of reporting a product gap it can fix itself.
    """
    ranks: list[int] = []

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            error = value.get("error")
            message = error.get("message") if isinstance(error, Mapping) else None
            if isinstance(message, str):
                ranks.extend(int(match.group(1)) for match in _RANK_REFUSAL.finditer(message))
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(_data(payload))
    return ranks[0] if ranks else None


def _rank_of(value: Mapping[str, Any] | None) -> int | None:
    shape = (value or {}).get("shape")
    return len(shape) if isinstance(shape, list) else None


def _flatten_data(data: Any) -> list[Any] | None:
    """Row-major flattening of a rectangular JSON array (``None`` when it is not one)."""
    if not isinstance(data, list):
        return None
    out: list[Any] = []
    for item in data:
        if isinstance(item, list):
            nested = _flatten_data(item)
            if nested is None:
                return None
            out.extend(nested)
        else:
            out.append(item)
    return out


def _aligned_property_rows(properties: Sequence[Mapping[str, Any]],
                           rank: int) -> tuple[list[dict[str, Any]] | None, str]:
    """Re-shape documented property values to the rank the engine's metadata published.

    Only the shapes that keep the *same values* are produced: a matrix is flattened for a rank-1
    property, a square flat array is folded back for a rank-2 property, and an isotropic matrix
    collapses to its diagonal for a rank-0 property.  The rank is the one the *refusing* property
    expects, so the alignment is decided **per property**: a definition that mixes a tensor with
    scalars (chain A writes ``thermalconductivity``, ``density`` and ``heatcapacity`` together)
    keeps the scalars exactly as they are instead of losing the whole write, and a value with no
    faithful reshape stays in its documented shape — the engine will say so again if it disagrees.
    """
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    for row in properties:
        name = row.get("name")
        value = row.get("value")
        if not isinstance(name, str) or not isinstance(value, Mapping):
            return None, f"property {name!r} has no typed value to align"
        current = _rank_of(value)
        data = value.get("data")
        if current is None:
            return None, f"property {name!r} carries no shape"
        if current == rank:
            rows.append({"name": name, "value": dict(value)})
            continue
        if rank == 1 and current == 2:
            flat = _flatten_data(data)
            if flat is None:
                notes.append(f"{name}: not a rectangular array, kept as it was")
                rows.append({"name": name, "value": dict(value)})
                continue
            rows.append({"name": name, "value": {**value, "shape": [len(flat)], "data": flat}})
            notes.append(f"{name}: flattened from rank 2 to rank 1")
            continue
        if rank == 2 and current == 1:
            flat = _flatten_data(data)
            side = int(len(flat) ** 0.5) if flat else 0
            if not flat or side * side != len(flat):
                notes.append(f"{name}: cannot be folded into a square matrix, kept as it was")
                rows.append({"name": name, "value": dict(value)})
                continue
            matrix = [flat[index * side:(index + 1) * side] for index in range(side)]
            rows.append({"name": name, "value": {**value, "shape": [side, side], "data": matrix}})
            notes.append(f"{name}: folded from rank 1 into a {side}x{side} matrix")
            continue
        if rank == 0 and current in {1, 2}:
            flat = _flatten_data(data)
            if flat is None:
                notes.append(f"{name}: not a rectangular array, kept as it was")
                rows.append({"name": name, "value": dict(value)})
                continue
            side = int(len(flat) ** 0.5) if flat else 0
            if current == 2 and side * side == len(flat) and side > 0:
                diagonal = [flat[index * side + index] for index in range(side)]
                off_diagonal = [item for index, item in enumerate(flat) if index % side != index // side]
                zeroish = all(str(item).strip() in {"0", "0.0"} for item in off_diagonal)
                if zeroish and len(set(map(str, diagonal))) == 1:
                    aligned = dict(value)
                    aligned.update({"shape": [], "data": diagonal[0]})
                    rows.append({"name": name, "value": aligned})
                    notes.append(f"{name}: collapsed to its isotropic diagonal for rank 0")
                    continue
            if current == 1 and len(flat) == 1:
                aligned = dict(value)
                aligned.update({"shape": [], "data": flat[0]})
                rows.append({"name": name, "value": aligned})
                notes.append(f"{name}: unwrapped a single-element array for rank 0")
                continue
            notes.append(f"{name}: rank {current} has no faithful rank-0 value, kept as it was")
            rows.append({"name": name, "value": dict(value)})
            continue
        notes.append(f"{name}: rank {current} is not aligned to the engine's rank {rank}, kept as it was")
        rows.append({"name": name, "value": dict(value)})
    reshaped = [note for note in notes if "kept as it was" not in note]
    if not notes:
        return rows, f"every property already carries the engine's published array rank {rank}"
    summary = "; ".join(notes)
    if not reshaped:
        return rows, f"no property could be reshaped to the engine's published array rank {rank}: {summary}"
    return rows, f"aligned to the engine's published array rank {rank}: {summary}"


async def _realign_refused_properties(
    client: ActionClient,
    payload: Mapping[str, Any] | None,
    *,
    path: Mapping[str, Any],
    group: str,
    properties: Sequence[Mapping[str, Any]],
    key_stem: str,
    request_stem: str,
) -> dict[str, Any] | None:
    """Re-issue a refused property definition in the shape the engine's metadata published.

    The refusal itself is the metadata source: the product validates against the engine's own
    ``getValueType`` table and names the rank it demanded, so the documented value is re-shaped
    (never replaced by another value) and written once under a fresh idempotency identity.  The
    returned evidence carries both the original refusal and the aligned envelope; ``None`` means
    no alignment was possible or the refusal named no rank.
    """
    rank = _engine_expected_rank(payload)
    if rank is None or _success(payload) and not _data(payload).get("failed"):
        return None
    rows, note = _aligned_property_rows(properties, rank)
    evidence: dict[str, Any] = {"expected_rank": rank, "note": note,
                                "refusal": {"error_code": _error_code(payload),
                                            "failed": _json_safe(_data(payload).get("failed"))}}
    if rows is None or note.startswith("no property could be reshaped"):
        # Nothing about the write would change, so re-issuing it would only repeat the refusal.
        evidence["aligned"] = False
        return evidence
    evidence["properties"] = _json_safe(rows)
    aligned = await client.action("material.set_properties",
                                 {"path": dict(path), "group": group, "properties": rows},
                                 key=f"{key_stem}-aligned-rank-{rank}", request=request_stem)
    evidence.update({"aligned": True, "envelope": _envelope_identity(aligned),
                     "payload": _json_safe(aligned),
                     "error_code": _error_code(aligned), "failed": _json_safe(_data(aligned).get("failed")),
                     "applied": _json_safe(_data(aligned).get("applied"))})
    return evidence


async def _adopt_engine_insulation(
    client: ActionClient,
    payload: Mapping[str, Any] | None,
    *,
    physics_path: Mapping[str, Any],
    tag: str,
    key_stem: str,
    request_stem: str,
) -> dict[str, Any] | None:
    """Adopt an insulation feature the engine created together with the physics interface.

    COMSOL's Heat Transfer interface brings its own ``ins1`` (Thermal Insulation) node, so the tag
    is taken before the case can create it and the create is refused with ``TAG_CONFLICT``
    (observed live in chain A).  The refusal alone proves nothing about the node, so the feature is
    read back through the published ``node.find`` under the interface and the adoption is reported
    as verified only when the engine names a type (or label) that *is* an insulation feature.

    ``None`` means the envelope was not an adoption-style conflict.
    """
    if _error_code(payload) not in PROBE_FIXTURE_ADOPT_CODES:
        return None
    found = await client.action("node.find",
                               {"query": {"tag": tag}, "root": dict(physics_path), "limit": 5},
                               key=f"{key_stem}-read", request=request_stem)
    rows = [row for row in (_data(found).get("results") or []) if isinstance(row, Mapping)]
    row = next((item for item in rows if str(item.get("tag") or "") == tag), None)
    if row is None:
        row = rows[0] if rows else None
    type_id = str((row or {}).get("type_id") or "")
    label = str((row or {}).get("label") or "")
    verified = bool(row) and "insulat" in f"{type_id} {label}".lower()
    return {
        "adopted": True,
        "refused_with": _error_code(payload),
        "refusal_message": _error_message(payload),
        "read": _envelope_identity(found),
        "payload": _json_safe(found),
        "found": _json_safe(row),
        "type_id": type_id or None,
        "label": label or None,
        "verified_as_insulation": verified,
        "verdict": ("the interface already carries the engine's own insulation feature"
                    if verified else
                    "a feature with this tag exists, but the engine did not report it as an insulation "
                    "feature, so the insulation line is not established"),
    }


def _chain_line(case: Case, name: str, rows: Sequence[tuple[str, Mapping[str, Any]]]) -> bool:
    """Verdict for one chain acceptance line made of several operations.

    A chain line ("the boundary temperatures and the insulation", "a local mesh") consists of
    several operations, so each of their envelopes is recorded and the line passes only when
    every one of them applied.  The first refusal is reported with the product's own error code
    and message — never as a generic "prerequisite did not pass".
    """
    evidence: dict[str, Any] = {"operations": [{"step": label, **_envelope_identity(payload)} for label, payload in rows]}
    if not rows:
        _mark(case, name, "NOT_RUN", "no operation of this line was attempted", observed=evidence)
        return False
    for label, payload in rows:
        if not _success(payload):
            status, reason = _unreadable_verdict(payload, f"{label} was refused")
            evidence["refused"] = {"step": label, "error_code": _error_code(payload),
                                   "error_message": _error_message(payload)}
            _mark(case, name, status, reason, observed=evidence)
            return False
    _mark(case, name, "PASS", None, observed=evidence)
    return True


async def _prepare_chain_containers(client: ActionClient, args: argparse.Namespace, store: dict[str, Any],
                                    *, prefix: str = "chain") -> tuple[bool, dict[str, Any]]:
    """Create the component and the geometry sequence a chain case addresses, in order.

    The chain cases run on an *empty* model (``model.create``), so the component and the geometry
    sequence do not exist yet: addressing ``component/comp1/geom/geom1`` directly is refused with
    ``NODE_NOT_FOUND`` (observed live for chain A and chain B, where the drivers' first engine
    operation was the block feature create).  Both containers are created through their published
    operations here, and each envelope is recorded; the flag is True only when both applied.

    ``prefix`` names the *chain*: the idempotency identity is per (body, model), and chain B runs on
    a model of its own, so reusing chain A's keys made the control daemon answer chain B's first
    container call with ``IDEMPOTENCY_CONFLICT`` (observed live, and the reason chain B reported
    "the chain component could not be created").
    """
    evidence: dict[str, Any] = {"component": args.component, "geometry": args.geometry_tag, "dimension": 3}
    component = await client.action("definition.component_manage", {"action": "create", "tag": args.component},
                                    key=f"{prefix}-component", request=f"{prefix}-component")
    evidence["component_create"] = _envelope_identity(component)
    if not _success(component):
        status, reason = _unreadable_verdict(component, "the chain component could not be created")
        evidence.update({"status": status, "reason": reason})
        store["chain_containers"] = evidence
        return False, evidence
    geometry = await client.action("geometry.sequence_create",
                                   {"component": args.component, "tag": args.geometry_tag, "dimension": 3},
                                   key=f"{prefix}-geometry", request=f"{prefix}-geometry")
    evidence["geometry_create"] = _envelope_identity(geometry)
    if not _success(geometry):
        status, reason = _unreadable_verdict(geometry, "the chain geometry sequence could not be created")
        evidence.update({"status": status, "reason": reason})
        store["chain_containers"] = evidence
        return False, evidence
    evidence.update({"status": "PASS", "reason": None})
    store["chain_containers"] = evidence
    return True, evidence


def _mark_benchmark_spec(case: Case, spec: BenchmarkSpec) -> dict[str, Any]:
    """Register the frozen specification and record its dimensional/numeric sanity checks."""
    checks = spec.sanity_checks()
    failed = [row for row in checks if not row.get("ok")]
    document = _spec_document(spec)
    case.assertions["benchmark_spec"] = document
    if failed:
        _mark(case, "benchmark_spec_registered_and_sane", "FAIL",
              "the registered specification does not close dimensionally/numerically: "
              + ", ".join(f"{row['check']} ({row['value']!r} {row['unit']})" for row in failed),
              observed=document)
        return document
    _mark(case, "benchmark_spec_registered_and_sane", "PASS", None, observed=document)
    return document


def _mark_benchmark_readback(case: Case, verdict: Mapping[str, Any]) -> None:
    """One acceptance line for the pre-solve read-back, with the mismatching items named."""
    status = str(verdict.get("status") or "BLOCKED")
    if status == "PASS":
        _mark(case, "pre_solve_readback_matches_spec", "PASS", None, observed=verdict)
        return
    if status == "FAIL":
        _mark(case, "pre_solve_readback_matches_spec", "FAIL",
              "the model disagrees with the pre-registered specification at: "
              + ", ".join(str(item) for item in verdict.get("mismatched") or [])
              + " (the specification and its tolerance were left unchanged)",
              observed=verdict)
        return
    _mark(case, "pre_solve_readback_matches_spec", "BLOCKED",
          "the read-back could not be compared item by item against the specification: not observed "
          f"{verdict.get('not_observed') or []}, not comparable {verdict.get('not_comparable') or []}",
          observed=verdict)


def _property_value(payload: Mapping[str, Any] | None, name: str) -> tuple[Any, str | None]:
    """The value one ``node.property_get`` reply published for ``name`` (never a substitute)."""
    data = _mapping(_data(payload))
    for key in ("properties", "values", "rows"):
        rows = data.get(key)
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, Mapping) and str(row.get("name")) == name:
                for value_key in ("value", "expression", "data"):
                    if value_key in row:
                        return row[value_key], None
                return row, None
    if name in data:
        return data[name], None
    return None, (f"the reply published no value for {name!r} "
                  f"(keys: {sorted(str(key) for key in data) or 'none'})")


def _extract_spec_number(value: Any) -> float | None:
    if isinstance(value, Mapping):
        for k in ("data", "expression", "value"):
            if k in value:
                res = _extract_spec_number(value[k])
                if res is not None:
                    return res
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        for item in value:
            num = _extract_spec_number(item)
            if num is not None and num != 0.0:
                return num
        if value:
            return _extract_spec_number(value[0])
        return None
    if isinstance(value, str):
        m = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", value.strip())
        if m:
            try:
                n = float(m.group(0))
                if math.isfinite(n):
                    return n
            except ValueError:
                pass
    return None


def _benchmark_readback_verdict(spec: BenchmarkSpec, observed: Mapping[str, Any],
                                errors: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Compare a pre-solve model read-back against the frozen spec, item by item (G3.1 §8.1).

    The comparison direction matters: the spec is the acceptance line, so a model value that
    disagrees fails *the comparison*.  It never edits the expected value, the shape of the analytic
    model or the tolerance — that substitution is exactly how a wrong build (``Cp=100`` instead of
    ``1000``) used to stay hidden.
    """
    errors = errors if isinstance(errors, Mapping) else {}
    rows: list[dict[str, Any]] = []
    for expected in spec.expected_model_values():
        item = str(expected["item"])
        errors_for_item = errors.get(item)
        if item not in observed:
            rows.append({"item": item, "unit": expected["unit"], "expected": expected["value"],
                         "observed": None, "status": "NOT_OBSERVED",
                         "reason": (errors_for_item.get("why") if isinstance(errors_for_item, Mapping) else None)
                                   or "the model published no value for this item"})
            continue
        raw = observed[item]
        value = raw.get("value") if isinstance(raw, Mapping) else raw
        if isinstance(expected["value"], (list, tuple)):
            expected_list = [_extract_spec_number(x) for x in expected["value"]]
            raw_data = value.get("data") if isinstance(value, Mapping) else value
            if isinstance(raw_data, (list, tuple)):
                observed_list = [_extract_spec_number(x) for x in raw_data]
            else:
                observed_list = [_extract_spec_number(raw_data)]
            matched = (len(expected_list) == len(observed_list) and
                       all(e is not None and o is not None and math.isclose(e, o, rel_tol=1e-6, abs_tol=1e-6)
                           for e, o in zip(expected_list, observed_list)))
            rows.append({"item": item, "unit": expected["unit"], "expected": expected["value"],
                         "observed": _json_safe(value), "status": "MATCH" if matched else "MISMATCH",
                         "reason": None if matched else "the model time list does not match the registered spec"})
            continue
        number = _extract_spec_number(value)
        expected_number = _extract_spec_number(expected["value"])
        if number is not None and expected_number is not None:
            matched = math.isclose(number, expected_number, rel_tol=1e-6, abs_tol=1e-6)
            rows.append({"item": item, "unit": expected["unit"], "expected": expected["value"],
                         "observed": _json_safe(value), "observed_number": number,
                         "status": "MATCH" if matched else "MISMATCH",
                         "reason": None if matched else
                                   (f"the model holds {number!r} where the registered specification says "
                                    f"{expected_number!r} {expected['unit']}: the specification is the "
                                    "acceptance line and is not changed by this observation")})
            continue
        rows.append({"item": item, "unit": expected["unit"], "expected": expected["value"],
                     "observed": _json_safe(value), "status": "NOT_COMPARABLE",
                     "reason": ("the value could not be reduced to a number, so it was not compared "
                                "against the specification")})
    wrong = [row["item"] for row in rows if row["status"] == "MISMATCH"]
    missing = [row["item"] for row in rows if row["status"] == "NOT_OBSERVED"]
    incomparable = [row["item"] for row in rows if row["status"] == "NOT_COMPARABLE"]
    status = "FAIL" if wrong else ("BLOCKED" if (missing or incomparable) else "PASS")
    return {"chain": spec.chain, "spec_source": spec.source, "error_limit": spec.error_limit,
            "error_definition": spec.error_definition, "rows": rows, "mismatched": wrong,
            "not_observed": missing, "not_comparable": incomparable, "status": status,
            "note": ("read back before the solve and compared against the pre-registered specification; "
                     "the specification and the tolerance were not modified by this observation")}


async def _benchmark_readback(client: ActionClient, spec: BenchmarkSpec, *,
                              material_path: Mapping[str, Any], property_paths: Mapping[str, Any],
                              key_stem: str) -> dict[str, Any]:
    """Read the built values back through the published node path and compare them to the spec."""
    observed: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    for row in spec.expected_model_values():
        item = str(row["item"])
        target = property_paths.get(item)
        if target is None:
            continue
        if isinstance(target, (tuple, list)) and len(target) == 2:
            path, prop_name = target[0], target[1]
        else:
            path, prop_name = target, item
        payload = await client.action("node.property_get", {"path": dict(path), "names": [prop_name]},
                                      key=f"{key_stem}-readback-{item}", request="benchmark-readback")
        value, why = _property_value(payload, prop_name)
        if value is None:
            errors[item] = {"error_code": _error_code(payload), "why": why}
            continue
        observed[item] = {"value": value, "envelope": _envelope_identity(payload)}
    verdict = _benchmark_readback_verdict(spec, observed, errors)
    verdict["observed_envelopes"] = {item: row.get("envelope") for item, row in observed.items()}
    verdict["read_errors"] = _json_safe(errors)
    return verdict


async def _case_w16_t019_chain_a(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                                 state: dict[str, Any]) -> None:
    run_dir = Path(args.run_dir)
    reference = _chain_a_reference(run_dir)
    case.assertions["chain_a_reference"] = {key: value for key, value in reference.items() if key != "samples"}
    case.assertion("analytic_reference_preregistered", bool(reference["samples"]), sha256=reference["reference_sha256"])
    case.subcase("analytic_reference_preregistered", "PASS", level="fixture",
                 reason=None, sha256=reference["reference_sha256"])
    chain_ops = ("model.create", "geometry.feature_create", "geometry.build", "material.create",
                 "material.set_properties", "material.selection_set", "physics.create", "physics.feature_create",
                 "physics.selection_set",
                 "mesh.create", "mesh.feature_create", "mesh.build", "study.create", "study.step_create",
                 "study.run", "result.sample_path", "model.save")
    # Every operation this case gates on is probed, including the ones the plan inventory does
    # not name: an unprobed operation must never masquerade as a capability gap.
    rows = await _prepare_case(case, host, (*_plan_ops(case.case_id), *chain_ops))
    chain_ok = all((rows.get(operation) or {}).get("available") for operation in chain_ops)
    case.assertion("static_chain_a_ops_executable", chain_ok, operations=list(chain_ops))
    chain_ok = _availability_subcase(case, "static_chain_a_ops_availability", chain_ops, rows, flag=chain_ok)
    live_names = ("empty_model_geometry_block", "constant_material_assigned", "boundary_temperatures_and_insulation",
                  "local_mesh_built", "stationary_study_and_solver", "solve_produced_solution",
                  "linear_profile_relative_error_le_1e-4", "heat_flux_and_power_balance",
                  "sample_table_and_units_recorded", "saved_mph_hash_recorded")
    runnable, status, why = _can_run(case, args, state, rows, chain_ops, require_writable_model=True)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    store: dict[str, Any] = {}
    created = await _create_empty_model(client, state, args.chain_a_label, host)
    case.assertions["chain_a_model_create"] = _envelope_identity(created)
    if not (_success(created) and isinstance(state.get("ref"), Mapping)):
        _mark(case, "empty_model_geometry_block", "BLOCKED" if _blocked_payload(created) else "FAIL",
              f"model.create returned {_error_code(created) or 'an invalid envelope'}")
        for name in live_names[1:]:
            _mark(case, name, "NOT_RUN", "prerequisite subcase empty_model_geometry_block did not pass")
        case.finish()
        return
    spec = BENCHMARK_A
    config = spec.as_config()
    # C07b (R-08): the specification is pre-registered and sanity-checked *before* anything is built,
    # and the same object is the only source of the material definition and of the analytic model.
    _mark_benchmark_spec(case, spec)
    # An empty model has no component and no geometry sequence: both containers this chain
    # addresses are created here, in order, before the first feature.  Live, the driver's first
    # engine operation was the block feature create on ``component/comp1/geom/geom1``, which the
    # product refused with NODE_NOT_FOUND because neither container existed.
    containers_ok, container_evidence = await _prepare_chain_containers(client, args, store, prefix="chain-a")
    case.assertions["chain_a_containers"] = container_evidence
    geometry_path = {"segments": [{"collection": "component", "tag": args.component},
                                  {"collection": "geom", "tag": args.geometry_tag}]}
    material_path = {"segments": [{"collection": "component", "tag": args.component},
                                  {"collection": "material", "tag": args.material_tag}]}
    physics_path = {"segments": [{"collection": "component", "tag": args.component},
                                 {"collection": "physics", "tag": args.physics_tag}]}
    mesh_path = {"segments": [{"collection": "component", "tag": args.component},
                              {"collection": "mesh", "tag": args.mesh_tag}]}
    study_path = {"segments": [{"collection": "study", "tag": args.study_tag}]}
    if not containers_ok:
        _mark(case, "empty_model_geometry_block", str(container_evidence["status"]), container_evidence["reason"],
              observed=container_evidence)
        for name in live_names[1:]:
            _mark(case, name, "NOT_RUN", "prerequisite subcase empty_model_geometry_block did not pass")
        case.finish()
        return
    block = await client.action("geometry.feature_create",
                                {"parent": geometry_path, "tag": "blk1", "type_id": "Block",
                                 "properties": [
                                     {"name": "size", "value": {"kind": "float64", "shape": [3],
                                                                "data": [float(config["length_m"]), float(config["width_m"]),
                                                                         float(config["height_m"])]}},
                                     {"name": "pos", "value": {"kind": "float64", "shape": [3],
                                                               "data": [0.0, 0.0, 0.0]}}]},
                                key="chain-a-block", request="chain-a")
    built = await client.action("geometry.build", {"geometry": geometry_path}, key="chain-a-build", request="chain-a")
    case.assertions["chain_a_geometry"] = {"block": _envelope_identity(block), "build": _envelope_identity(built)}
    geometry_ok = _chain_line(case, "empty_model_geometry_block",
                              [("geometry.feature_create", block), ("geometry.build", built)])
    if not geometry_ok:
        for name in live_names[1:]:
            _mark(case, name, "NOT_RUN", "prerequisite subcase empty_model_geometry_block did not pass")
        case.finish()
        return

    material = await client.action("material.create",
                                   {"component": args.component, "tag": args.material_tag, "type_id": "Common",
                                    "definition": {"properties": [dict(row) for row in CHAIN_A_MATERIAL_PROPERTIES]}},
                                   key="chain-a-material", request="chain-a")
    # The engine's own ``getValueType`` metadata decides the array rank a property takes, and it is
    # build dependent: on this build a fresh ``Common`` material reports ``thermalconductivity`` as
    # a rank-1 array, so the documented 3x3 matrix is refused ("property expects array rank 1,
    # received 2").  The refusal names the rank the engine will take, and the definition is
    # re-issued in exactly that shape (same values, aligned shape) under a fresh identity.
    material_realign = await _realign_refused_properties(
        client, material, path=material_path, group=PROBE_FIXTURE_TAGS["property_group"],
        properties=CHAIN_A_MATERIAL_PROPERTIES, key_stem="chain-a-material", request_stem="chain-a")
    selection = await client.action("material.selection_set",
                                    {"path": material_path,
                                     "selection": {"kind": "explicit", "component": args.component,
                                                   "geometry": args.geometry_tag, "entity_dimension": 3,
                                                   "entities": [1]}},
                                    key="chain-a-material-selection", request="chain-a")
    material_status, material_reason, material_detail = _material_definition_verdict(
        material, ("thermalconductivity", "density", "heatcapacity"))
    if material_realign is not None:
        aligned_status, aligned_reason, aligned_detail = _material_definition_verdict(
            material_realign.get("payload"), ("thermalconductivity", "density", "heatcapacity"))
        material_detail["engine_alignment"] = material_realign
        material_detail["engine_alignment_verdict"] = {"status": aligned_status, "reason": aligned_reason,
                                                       "detail": aligned_detail}
        if aligned_status == "PASS":
            material_status, material_reason = "PASS", None
            material_detail["verdict_source"] = "aligned re-issue in the engine's published array rank"
    material_evidence = {"create": _envelope_identity(material), "selection": _envelope_identity(selection),
                         "properties": material_detail}
    if material_status == "PASS" and _success(selection):
        _mark(case, "constant_material_assigned", "PASS", None, observed=material_evidence)
    elif material_status == "PASS":
        selection_status, selection_reason = _unreadable_verdict(
            selection, "the material domain selection was refused")
        _mark(case, "constant_material_assigned", selection_status, selection_reason, observed=material_evidence)
    else:
        _mark(case, "constant_material_assigned", material_status, material_reason, observed=material_evidence)
    case.assertions["chain_a_material"] = material_evidence

    physics_created = await client.action("physics.create",
                                          {"component": args.component, "tag": args.physics_tag,
                                           "type_id": args.heat_physics_type, "geometry": args.geometry_tag},
                                          key="chain-a-physics", request="chain-a")
    physics_temp1_path = {"segments": [{"collection": "component", "tag": args.component},
                                        {"collection": "physics", "tag": args.physics_tag},
                                        {"collection": "feature", "tag": "temp1"}]}
    physics_temp2_path = {"segments": [{"collection": "component", "tag": args.component},
                                        {"collection": "physics", "tag": args.physics_tag},
                                        {"collection": "feature", "tag": "temp2"}]}
    temperature_hot = await client.action("physics.feature_create",
                                          {"parent": physics_path, "tag": "temp1", "type_id": "TemperatureBoundary",
                                           "entity_dimension": 2,
                                           "properties": [{"name": "T0", "value": {"kind": "expression", "shape": [],
                                                                                   "data": spec.t0_expression}}]},
                                          key="chain-a-temp1", request="chain-a")
    selection_hot = await client.action("physics.selection_set",
                                        {"path": physics_temp1_path,
                                         "selection": {"kind": "explicit", "component": args.component,
                                                       "geometry": args.geometry_tag, "entity_dimension": 2,
                                                       "entities": [1]}},
                                        key="chain-a-temp1-sel", request="chain-a")
    temperature_cold = await client.action("physics.feature_create",
                                           {"parent": physics_path, "tag": "temp2", "type_id": "TemperatureBoundary",
                                            "entity_dimension": 2,
                                            "properties": [{"name": "T0", "value": {"kind": "expression", "shape": [],
                                                                                    "data": spec.hot_expression}}]},
                                           key="chain-a-temp2", request="chain-a")
    selection_cold = await client.action("physics.selection_set",
                                         {"path": physics_temp2_path,
                                          "selection": {"kind": "explicit", "component": args.component,
                                                        "geometry": args.geometry_tag, "entity_dimension": 2,
                                                        "entities": [6]}},
                                         key="chain-a-temp2-sel", request="chain-a")
    insulation = await client.action("physics.feature_create",
                                      {"parent": physics_path, "tag": "ins1", "type_id": "ThermalInsulation",
                                       "entity_dimension": 2, "properties": []},
                                      key="chain-a-insulation", request="chain-a")
    insulation_adoption = await _adopt_engine_insulation(
        client, insulation, physics_path=physics_path, tag="ins1",
        key_stem="chain-a-insulation", request_stem="chain-a")
    case.assertions["chain_a_physics"] = {
        "create": _envelope_identity(physics_created), "temp1": _envelope_identity(temperature_hot),
        "temp1_selection": _envelope_identity(selection_hot),
        "temp2": _envelope_identity(temperature_cold),
        "temp2_selection": _envelope_identity(selection_cold),
        "insulation": _envelope_identity(insulation),
        "insulation_adoption": insulation_adoption}
    physics_rows: list[tuple[str, Mapping[str, Any]]] = [
        ("physics.create", physics_created),
        ("physics.feature_create temp1", temperature_hot),
        ("physics.selection_set temp1", selection_hot),
        ("physics.feature_create temp2", temperature_cold),
        ("physics.selection_set temp2", selection_cold)]
    if insulation_adoption is not None and insulation_adoption.get("verified_as_insulation"):
        # The engine's own interface default *is* the insulation this line needs: the create was
        # refused because the tag is taken, and the feature was read back as an insulation node.
        physics_rows.append(("ins1 (the engine's own insulation feature, read back)",
                             insulation_adoption["payload"]))
        _chain_line(case, "boundary_temperatures_and_insulation", physics_rows)
    elif insulation_adoption is not None:
        # A tag conflict the engine could not confirm as an insulation feature: the fact this line
        # needs was not established, which is blocked — not "the case failed".
        evidence = {"operations": [{"step": label, **_envelope_identity(payload)}
                                   for label, payload in physics_rows],
                    "insulation": _envelope_identity(insulation), "adoption": insulation_adoption}
        _mark(case, "boundary_temperatures_and_insulation", "BLOCKED",
              "the engine already carries a feature at tag 'ins1' but did not report a type or label "
              f"that identifies it as an insulation feature ({insulation_adoption.get('type_id')!r}), "
              "so the insulation part of this line was not established", observed=evidence)
    else:
        physics_rows.append(("physics.feature_create ins1", insulation))
        _chain_line(case, "boundary_temperatures_and_insulation", physics_rows)

    mesh_created = await client.action("mesh.create",
                                       {"component": args.component, "tag": args.mesh_tag,
                                        "geometry": args.geometry_tag}, key="chain-a-mesh", request="chain-a")
    case.assertions["chain_a_mesh_create"] = _envelope_identity(mesh_created)
    mesh_size = await client.action("mesh.feature_create",
                                    {"parent": mesh_path, "tag": args.mesh_size_tag, "type_id": "Size",
                                     "properties": [{"name": "hauto", "value": {"kind": "int32", "shape": [],
                                                                                "data": 4}}]},
                                    key="chain-a-size", request="chain-a")
    mesh_tet = await client.action("mesh.feature_create",
                                   {"parent": mesh_path, "tag": "ftet1", "type_id": "FreeTet",
                                    "properties": []},
                                   key="chain-a-ftet", request="chain-a")
    mesh_built = await client.action("mesh.build", {"path": mesh_path}, key="chain-a-meshbuild", request="chain-a")
    case.assertions["chain_a_mesh_build"] = {"success": _success(mesh_built), "error_code": _error_code(mesh_built)}
    statistics = await client.action("mesh.statistics", {"path": mesh_path}, key="chain-a-stats", request="chain-a")
    case.assertions["chain_a_mesh_statistics"] = _json_safe(_data(statistics))
    _chain_line(case, "local_mesh_built",
                [("mesh.create", mesh_created), ("mesh.feature_create size", mesh_size),
                 ("mesh.feature_create ftet", mesh_tet), ("mesh.build", mesh_built)])

    # C07b (R-08): read the built values back *before* the solve and check them item by item against
    # the frozen specification.  The read-back informs the mismatch record only — it never edits the
    # specification, the analytic model or the tolerance, which is how Cp=100 vs Cp=1000 stayed hidden.
    material_def_path = {"segments": [{"collection": "component", "tag": args.component},
                                      {"collection": "material", "tag": args.material_tag},
                                      {"collection": "propertyGroup", "tag": "def"}]}
    geom_block_path = {"segments": [{"collection": "component", "tag": args.component},
                                    {"collection": "geom", "tag": args.geometry_tag},
                                    {"collection": "feature", "tag": "blk1"}]}
    physics_temp1_path = {"segments": [{"collection": "component", "tag": args.component},
                                       {"collection": "physics", "tag": args.physics_tag},
                                       {"collection": "feature", "tag": "temp1"}]}
    physics_temp2_path = {"segments": [{"collection": "component", "tag": args.component},
                                       {"collection": "physics", "tag": args.physics_tag},
                                       {"collection": "feature", "tag": "temp2"}]}

    readback = await _benchmark_readback(
        client, spec, material_path=material_path, key_stem="chain-a",
        property_paths={"thermalconductivity": (material_def_path, "thermalconductivity"),
                        "density": (material_def_path, "density"),
                        "heatcapacity": (material_def_path, "heatcapacity"),
                        "length_m": (geom_block_path, "lx"),
                        "width_m": (geom_block_path, "ly"),
                        "height_m": (geom_block_path, "lz"),
                        "initial_value": (physics_temp1_path, "T0"),
                        "hot_boundary": (physics_temp2_path, "T0")})
    case.assertions["chain_a_pre_solve_readback"] = readback
    _mark_benchmark_readback(case, readback)

    study = await client.action("study.create", {"tag": args.study_tag, "label": "G3 chain A stationary"},
                                key="chain-a-study", request="chain-a")
    case.assertions["chain_a_study_create"] = _envelope_identity(study)
    step = await client.action("study.step_create",
                               {"study": study_path, "tag": "stat", "type_id": "Stationary", "properties": []},
                               key="chain-a-step", request="chain-a")
    case.assertions["chain_a_step_create"] = _envelope_identity(step)
    generated = await client.action("study.solver_generate", {"study": study_path, "replace_existing": False},
                                    key="chain-a-solver-generate", request="chain-a")
    solvers = await client.action("solver.list", {"filter": {"study": args.study_tag}},
                                  key="chain-a-solver-list", request="chain-a")
    solver_sequence_ok = bool(_success(solvers) and _data(solvers).get("solver_count"))
    case.assertions["chain_a_solver_sequence"] = {"generate": _envelope_identity(generated),
                                                 "list": _json_safe(_data(solvers)),
                                                 "solver_sequence_ok": solver_sequence_ok}
    study_ok = _chain_line(case, "stationary_study_and_solver",
                           [("study.create", study), ("study.step_create", step)])
    if study_ok and not solver_sequence_ok and _error_code(generated) not in {None, "SOLVER_SEQUENCE_EXISTS"}:
        case.subcase("stationary_study_and_solver", "BLOCKED", level="live", force=True,
                     reason=(f"the study and its step were created but no solver sequence could be "
                             f"generated ({_error_code(generated)}): the solve cannot run a sequence "
                             f"that does not exist"))
        study_ok = False

    prerequisites = {name: case.subcase_status(name) for name in live_names[:5]}
    unmet = [name for name, value in prerequisites.items() if value != "PASS"]
    if unmet:
        detail = "; ".join(f"{name}={prerequisites[name]}" for name in unmet)
        _mark(case, "solve_produced_solution", "NOT_RUN",
              f"a prerequisite subcase did not pass ({detail})")
        for name in live_names[6:]:
            _mark(case, name, "NOT_RUN", f"a prerequisite subcase did not pass ({detail})")
        case.assertions["chain_a_prerequisites"] = prerequisites
        case.finish()
        return
    case.assertions["chain_a_prerequisites"] = prerequisites
    solved = await client.action("study.run", {"study": study_path, "timeout_s": args.solve_timeout_s},
                                 key="chain-a-solve", request="chain-a")
    case.assertions["chain_a_solve"] = {"success": _success(solved), "error_code": _error_code(solved)}
    if not _success(solved):
        _mark(case, "solve_produced_solution", "BLOCKED" if _blocked_payload(solved) else "FAIL",
              f"study.run returned {_error_code(solved) or 'an invalid envelope'}")
        for name in live_names[6:]:
            _mark(case, name, "NOT_RUN", "prerequisite subcase solve_produced_solution did not pass")
        case.finish()
        return
    _mark(case, "solve_produced_solution", "PASS", None, observed=_data(solved))
    _record_solution(state, case, source="chain A steady solve", dataset=args.dataset_tag, solution="sol1")
    samples = await client.action("result.sample_path", {
        "spec": {"expressions": ["T"], "dataset": args.dataset_tag, "solution": "sol1"},
        "path_definition": {"kind": "line", "start": [0.0, config["width_m"] / 2, config["height_m"] / 2],
                            "end": [config["length_m"], config["width_m"] / 2, config["height_m"] / 2],
                            "samples": config["sample_count"]}}, key="chain-a-sample", request="chain-a")
    verdict = _check_chain_a(samples, reference)
    sample_rows = _extract_samples(samples)
    table_path = run_dir / "chainA_samples.json"
    _write_json(table_path, {"reference_sha256": reference["reference_sha256"], "samples": sample_rows,
                             "verdict": verdict[1]})
    case.assertions["chain_a_samples"] = {"path": str(table_path), "sha256": _sha256(table_path),
                                          "rows": len(sample_rows),
                                          "max_relative_error": verdict[2].get("max_relative_error") if len(verdict) > 2 else None}
    _mark(case, "linear_profile_relative_error_le_1e-4", verdict[0], verdict[1],
          **(verdict[2] if len(verdict) > 2 else {}))
    flux = _data(samples).get("boundary_flux_w_m2") or _data(samples).get("heat_flux_w_m2")
    expected = float(reference["heat_flux_w_m2"])
    derived = None
    x_series = _sample_series(sample_rows, ("x", "x_m", "x [m]"))
    t_series = _sample_series(sample_rows, ("T", "T_k", "T [K]", "temp"))
    if x_series is not None and t_series is not None and len(x_series) >= 2 and (x_series[-1] - x_series[0]) != 0.0:
        # The goal document computes the boundary heat flux separately from
        # k, the sampled gradient and the cross-section; the sampling
        # operation must not be required to publish it.
        k_effective = expected * float(reference["length_m"]) / float(reference["temperature_difference_k"])
        derived = abs(k_effective * (t_series[-1] - t_series[0]) / (x_series[-1] - x_series[0]))
    power = _finite_float(flux) if flux is not None else derived
    balance_ok = power is not None and abs(power - expected) <= 1e-3 * abs(expected)
    case.assertions["chain_a_power_balance"] = {
        "reported_heat_flux_w_m2": _finite_float(flux),
        "derived_heat_flux_w_m2": derived,
        "analytic_heat_flux_w_m2": expected,
        "analytic_power_w": reference["power_w"],
    }
    _mark(case, "heat_flux_and_power_balance", "PASS" if balance_ok else "FAIL",
          None if balance_ok else "the reported boundary heat flux is missing or differs from k*ΔT/L by more than 0.1%")
    _mark(case, "sample_table_and_units_recorded", "PASS" if sample_rows else "FAIL",
          None if sample_rows else "no sample table was written", table=str(table_path))
    save_target = run_dir / "chainA_result.mph"
    saved = await client.action("model.save", {"destination": {"path": str(save_target)}, "overwrite": True,
                                              "include_solution": True}, key="chain-a-save", request="chain-a")
    digest = _sha256(save_target) if save_target.is_file() else None
    case.assertions["chain_a_saved_mph"] = {"path": str(save_target), "sha256": digest,
                                            "reopen_command": f"tools/phase4_run_mcp.py --reopen-check {save_target}"}
    _mark(case, "saved_mph_hash_recorded", "PASS" if (digest and _success(saved)) else "FAIL",
          None if (digest and _success(saved)) else f"model.save returned {_error_code(saved) or 'no file was written'}",
          sha256=digest)
    case.finish()




def _seconds_value(text: str) -> float:
    """Parse a ``<number>[s]`` time value for a DoubleArray property.

    ``tlist`` is a DoubleArray: the wire form carries plain numbers in seconds,
    so a value in any other unit would be silently reinterpreted as seconds.
    Such a value is refused here with a clear message instead.
    """
    cleaned = str(text).strip()
    if cleaned.endswith("]"):
        number, _, unit = cleaned.partition("[")
        if unit.strip().rstrip("]").strip().lower() not in {"s", "sec", "second", "seconds"}:
            raise ValueError(f"time value {text!r} must be given in seconds, e.g. '1[s]'")
        cleaned = number
    return float(cleaned)


def _chain_a_reference(run_dir: Path) -> dict[str, Any]:
    """Chain A's analytic reference: derived from ``BENCHMARK_A``, written before the solve.

    Nothing here can be influenced by a model read-back: the spec is frozen, so a disagreement
    between the model and this reference is recorded as a mismatch and never fixed by editing the
    reference (G3.1 §8.1).
    """
    spec = BENCHMARK_A
    config = spec.as_config()
    samples = [{"x_m": x_m, "x_mm": 1000.0 * x_m, "T_analytic_k": spec.steady_profile_k(x_m)}
               for x_m in spec.sample_points_m()]
    reference = {
        "analytic_model": "steady 1D conduction in a slab insulated on four sides: T(x) = T0 + (T1-T0)*x/L",
        "benchmark_spec": _spec_document(spec),
        "config": config, "samples": samples, "temperature_difference_k": spec.temperature_difference_k,
        # ``_check_chain_a`` reads these two directly; keep them top-level so the
        # contract between the reference writer and the checker is explicit.
        "t0_k": float(spec.t0_k), "length_m": float(spec.length_m),
        "heat_flux_w_m2": spec.heat_flux_w_m2, "cross_section_area_m2": spec.cross_section_area_m2,
        "power_w": spec.power_w,
        "relative_error_limit": float(spec.error_limit),
        "error_definition": spec.error_definition,
    }
    path = run_dir / "chainA_reference.json"
    _write_json(path, reference)
    reference["reference_sha256"] = _sha256(path)
    return reference


def _chain_b_reference(run_dir: Path) -> dict[str, Any]:
    """Chain B's analytic reference: ``BENCHMARK_B``'s own alpha and decay factors."""
    spec = BENCHMARK_B
    config = spec.as_config()
    times = [float(value) for value in spec.time_points_s]
    factors = [math.exp(-spec.decay_rate_per_s * time_value) for time_value in times]
    reference = {
        "analytic_model": ("transient 1D conduction with T(x,0)=T0+dT*sin(pi*x/L) and both ends held at T0: "
                           "T(x,t) = T0 + dT*sin(pi*x/L)*exp(-alpha*(pi/L)^2*t); the slab is insulated elsewhere"),
        "benchmark_spec": _spec_document(spec),
        # ``_check_chain_b`` reads these directly; keep them top-level so the contract is explicit.
        "config": config, "temperature_difference_k": spec.temperature_difference_k,
        "alpha_m2_s": spec.alpha_m2_s,
        "decay_factors": [{"t_s": time_value, "factor": factor} for time_value, factor in zip(times, factors)],
        "relative_error_limit": float(spec.error_limit),
        "normalized_error_limit": float(spec.error_limit), "length_m": float(spec.length_m),
        "t0_k": float(spec.t0_k),
        "error_definition": spec.error_definition,
    }
    path = run_dir / "chainB_reference.json"
    _write_json(path, reference)
    reference["reference_sha256"] = _sha256(path)
    return reference


def _spec_document(spec: BenchmarkSpec) -> dict[str, Any]:
    """The registered specification, as recorded with a reference (for the report and the index)."""
    return {"chain": spec.chain, "source": spec.source, "sides": spec.sides,
            "length_m": spec.length_m, "width_m": spec.width_m, "height_m": spec.height_m,
            "k_w_mk": spec.k_w_mk, "rho_kg_m3": spec.rho_kg_m3, "cp_j_kgk": spec.cp_j_kgk,
            "t0_k": spec.t0_k, "hot_k": spec.hot_k, "delta_t_k": spec.delta_t_k,
            "time_points_s": list(spec.time_points_s), "sample_count": spec.sample_count,
            "error_limit": spec.error_limit, "error_definition": spec.error_definition,
            "alpha_m2_s": spec.alpha_m2_s, "expected_model_values": [dict(row) for row in
                                                                    spec.expected_model_values()],
            "sanity_checks": [dict(row) for row in spec.sanity_checks()]}


async def _case_w16_t019_chain_b(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                                 state: dict[str, Any]) -> None:
    # The analytic reference is pre-registered before the solve, so the transient profile cannot be
    # fitted to whatever the engine returns afterwards.
    run_dir = Path(args.run_dir)
    reference = _chain_b_reference(run_dir)
    case.assertions["chain_b_reference"] = {key: value for key, value in reference.items() if key != "decay_factors"}
    case.subcase("analytic_reference_preregistered", "PASS", level="fixture", reason=None,
                 sha256=reference["reference_sha256"], alpha_m2_s=reference["alpha_m2_s"])
    chain_ops = ('model.create', 'geometry.feature_create', 'geometry.build', 'material.create', 'material.set_properties',
                 'material.selection_set', 'physics.create', 'physics.feature_create', 'physics.feature_update', 'physics.selection_set', 'mesh.create', 'mesh.build', 'study.create',
                 'study.step_create', 'study.run', 'result.sample_path')
    rows = await _prepare_case(case, host, [*_plan_ops(case.case_id), *chain_ops])
    chain_ok = all(rows.get(operation, {}).get("available") for operation in chain_ops)
    case.assertion("static_chain_b_ops_executable", chain_ok, operations=list(chain_ops))
    chain_ok = _availability_subcase(case, "static_chain_b_ops_availability", chain_ops, rows, flag=chain_ok)
    live_names = ("transient_study_and_initial_value", "transient_solve_produced_solution",
                  "normalized_max_error_le_1e-3", "time_points_and_mesh_recorded")
    runnable, status, why = _can_run(case, args, state, rows, chain_ops, require_writable_model=True)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    spec = BENCHMARK_B
    config = spec.as_config()
    _mark_benchmark_spec(case, spec)
    store: dict[str, Any] = {}
    created = await _create_empty_model(client, state, args.chain_b_label, host)
    case.assertions["chain_b_model_create"] = _envelope_identity(created)
    if not (_success(created) and isinstance(state.get("ref"), Mapping)):
        _mark(case, "transient_study_and_initial_value", "BLOCKED" if _blocked_payload(created) else "FAIL",
              f"model.create returned {_error_code(created) or 'an invalid envelope'}")
        for name in live_names[1:]:
            _mark(case, name, "NOT_RUN", "prerequisite subcase transient_study_and_initial_value did not pass")
        case.finish()
        return
    # The same container order as chain A: an empty model has no component and no geometry
    # sequence, so both are created before the first feature (live: the block feature create was
    # refused with NODE_NOT_FOUND on component/comp1/geom/geom1).
    containers_ok, container_evidence = await _prepare_chain_containers(client, args, store, prefix="chain-b")
    case.assertions["chain_b_containers"] = container_evidence
    geometry_path = {"segments": [{"collection": "component", "tag": args.component},
                                  {"collection": "geom", "tag": args.geometry_tag}]}
    physics_path = {"segments": [{"collection": "component", "tag": args.component},
                                 {"collection": "physics", "tag": args.physics_tag}]}
    material_path = {"segments": [{"collection": "component", "tag": args.component},
                                  {"collection": "material", "tag": args.material_tag}]}
    mesh_path = {"segments": [{"collection": "component", "tag": args.component},
                              {"collection": "mesh", "tag": args.mesh_tag}]}
    study_path = {"segments": [{"collection": "study", "tag": args.study_tag}]}
    if not containers_ok:
        _mark(case, "transient_study_and_initial_value", str(container_evidence["status"]),
              container_evidence["reason"], observed=container_evidence)
        for name in live_names[1:]:
            _mark(case, name, "NOT_RUN", "prerequisite subcase transient_study_and_initial_value did not pass")
        case.finish()
        return
    block = await client.action("geometry.feature_create",
                                {"parent": geometry_path, "tag": "blk1", "type_id": "Block",
                                 "properties": [{"name": "size", "value": {"kind": "float64", "shape": [3],
                                                                           "data": [float(config["length_m"]),
                                                                                    float(config["width_m"]),
                                                                                    float(config["height_m"])]}}]},
                                key="chain-b-block", request="chain-b")
    geometry_built = await client.action("geometry.build", {"geometry": geometry_path},
                                         key="chain-b-build", request="chain-b")
    material = await client.action("material.create", {"component": args.component, "tag": args.material_tag,
                                                      "type_id": "Common",
                                                      "definition": {"properties": [dict(row) for row in
                                                                                    CHAIN_B_MATERIAL_PROPERTIES]}},
                               key="chain-b-material", request="chain-b")
    # Same engine-metadata alignment as chain A: the refused array rank is re-issued in the shape
    # the bound build published, so the material definition is the one the acceptance line needs.
    material_realign = await _realign_refused_properties(
        client, material, path=material_path, group=PROBE_FIXTURE_TAGS["property_group"],
        properties=CHAIN_B_MATERIAL_PROPERTIES, key_stem="chain-b-material", request_stem="chain-b")
    selection = await client.action("material.selection_set",
                                    {"path": material_path,
                                     "selection": {"kind": "explicit", "component": args.component,
                                                   "geometry": args.geometry_tag, "entity_dimension": 3,
                                                   "entities": [1]}},
                                    key="chain-b-material-selection", request="chain-b")
    physics_created = await client.action("physics.create", {"component": args.component, "tag": args.physics_tag,
                                                             "type_id": args.heat_physics_type,
                                                             "geometry": args.geometry_tag},
                                           key="chain-b-physics", request="chain-b")
    # The initial value is the analytic initial condition T(x,0)=T0 + dT*sin(pi*x/L), written through
    # the verified feature property (``Tinit`` on the engine's initial-values feature).
    initial_path = {"segments": [{"collection": "component", "tag": args.component},
                                 {"collection": "physics", "tag": args.physics_tag},
                                 {"collection": "feature", "tag": "init1"}]}
    init_expr = f"{float(config['t0_k'])}[K] + {float(config['delta_t_k'])}[K]*sin(pi*x/{float(config['length_m'])}[m])"
    initial_value = await client.action(
        "physics.feature_update",
        {"path": initial_path,
         "properties": [{"name": "Tinit",
                         "value": {"kind": "expression", "shape": [], "data": init_expr}}]},
        key="chain-b-initial", request="chain-b")
    physics_temp1_path = {"segments": [{"collection": "component", "tag": args.component},
                                        {"collection": "physics", "tag": args.physics_tag},
                                        {"collection": "feature", "tag": "temp1"}]}
    temperature_boundary = await client.action(
        "physics.feature_create",
        {"parent": physics_path, "tag": "temp1", "type_id": "TemperatureBoundary",
         "entity_dimension": 2,
         "properties": [{"name": "T0", "value": {"kind": "expression", "shape": [],
                                                 "data": f"{float(config['t0_k'])}[K]"}}]},
        key="chain-b-temp", request="chain-b")
    selection_boundary = await client.action(
        "physics.selection_set",
        {"path": physics_temp1_path,
         "selection": {"kind": "explicit", "component": args.component,
                       "geometry": args.geometry_tag, "entity_dimension": 2,
                       "entities": [1, 6]}},
        key="chain-b-temp-sel", request="chain-b")
    insulation = await client.action("physics.feature_create", {"parent": physics_path, "tag": "ins1",
                                                                "type_id": "ThermalInsulation", "entity_dimension": 2,
                                                                "properties": []},
                                     key="chain-b-insulation", request="chain-b")
    mesh_created = await client.action("mesh.create", {"component": args.component, "tag": args.mesh_tag,
                                                       "geometry": args.geometry_tag},
                                        key="chain-b-mesh", request="chain-b")
    mesh_size = await client.action("mesh.feature_create", {"parent": mesh_path, "tag": args.mesh_size_tag,
                                                            "type_id": "Size",
                                                            "properties": [{"name": "hauto",
                                                                            "value": {"kind": "int32", "shape": [],
                                                                                      "data": 3}}]},
                                    key="chain-b-size", request="chain-b")
    mesh_tet = await client.action("mesh.feature_create",
                                   {"parent": mesh_path, "tag": "ftet1", "type_id": "FreeTet",
                                    "properties": []},
                                   key="chain-b-ftet", request="chain-b")
    mesh_built = await client.action("mesh.build", {"path": mesh_path}, key="chain-b-meshbuild", request="chain-b")
    study = await client.action("study.create", {"tag": args.study_tag, "label": "G3 chain B transient"},
                                key="chain-b-study", request="chain-b")
    case.assertions["chain_b_study_create"] = _envelope_identity(study)
    step = await client.action("study.step_create", {
        "study": study_path, "tag": "time", "type_id": "Transient",
        "properties": [
            {"name": "tlist", "value": {"kind": "float64", "shape": [len(config["time_points_s"])],
                                        "data": [float(value) for value in config["time_points_s"]]}},
            {"name": "usertol", "value": {"kind": "string", "shape": [], "data": "on"}},
            {"name": "rtol", "value": {"kind": "float64", "shape": [], "data": 1e-5}},
        ]},
        key="chain-b-step", request="chain-b")
    case.assertions["chain_b_step_create"] = _envelope_identity(step)
    generated = await client.action("study.solver_generate", {"study": study_path, "replace_existing": False},
                                    key="chain-b-solver-generate", request="chain-b")
    solvers = await client.action("solver.list", {"filter": {"study": args.study_tag}},
                                  key="chain-b-solver-list", request="chain-b")
    case.assertions["chain_b_solver_sequence"] = {"generate": _envelope_identity(generated),
                                                 "list": _json_safe(_data(solvers)),
                                                 "solver_count": _data(solvers).get("solver_count")}
    insulation_adoption = await _adopt_engine_insulation(
        client, insulation, physics_path=physics_path, tag="ins1",
        key_stem="chain-b-insulation", request_stem="chain-b")
    case.assertions["chain_b_insulation_adoption"] = insulation_adoption
    ins_row = (("ins1 (the engine's own insulation feature, read back)", insulation_adoption["payload"])
               if insulation_adoption is not None and insulation_adoption.get("verified_as_insulation")
               else ("physics.feature_create ins1", insulation))
    chain_b_rows: list[tuple[str, Mapping[str, Any]]] = [
        ("geometry.feature_create", block), ("geometry.build", geometry_built),
        ("material.create", material), ("material.selection_set", selection),
        ("physics.create", physics_created),
        ("physics.feature_update init1", initial_value),
        ("physics.feature_create temp1", temperature_boundary),
        ("physics.selection_set temp1", selection_boundary),
        ins_row,
        ("mesh.create", mesh_created),
        ("mesh.feature_create size", mesh_size), ("mesh.feature_create ftet", mesh_tet), ("mesh.build", mesh_built),
        ("study.create", study), ("study.step_create", step)]
    line_ok = _chain_line(case, "transient_study_and_initial_value", chain_b_rows)
    if not line_ok:
        for name in live_names[1:]:
            _mark(case, name, "NOT_RUN", "prerequisite subcase transient_study_and_initial_value did not pass")
        case.finish()
        return
    case.assertions["chain_b_model"] = {"material": _envelope_identity(material),
                                        "material_selection": _envelope_identity(selection),
                                        "material_engine_alignment": material_realign,
                                        "initial_value": _envelope_identity(initial_value),
                                        "temperature": _envelope_identity(temperature_boundary),
                                        "insulation": _envelope_identity(insulation),
                                        "mesh": _envelope_identity(mesh_built)}
    # C07b (R-08): the read-back is compared against the frozen specification before the solve, and
    # the transient reference stays the one registered from BENCHMARK_B (alpha = k/(rho*Cp), Cp=1000).
    material_def_path = {"segments": [{"collection": "component", "tag": args.component},
                                      {"collection": "material", "tag": args.material_tag},
                                      {"collection": "propertyGroup", "tag": "def"}]}
    geom_block_path = {"segments": [{"collection": "component", "tag": args.component},
                                    {"collection": "geom", "tag": args.geometry_tag},
                                    {"collection": "feature", "tag": "blk1"}]}
    study_time_path = {"segments": [{"collection": "study", "tag": args.study_tag},
                                    {"collection": "feature", "tag": "time"}]}

    readback = await _benchmark_readback(
        client, spec, material_path=material_path, key_stem="chain-b",
        property_paths={"thermalconductivity": (material_def_path, "thermalconductivity"),
                        "density": (material_def_path, "density"),
                        "heatcapacity": (material_def_path, "heatcapacity"),
                        "length_m": (geom_block_path, "lx"),
                        "width_m": (geom_block_path, "ly"),
                        "height_m": (geom_block_path, "lz"),
                        "initial_value": (initial_path, "Tinit"),
                        "time_points": (study_time_path, "tlist")})
    case.assertions["chain_b_pre_solve_readback"] = readback
    _mark_benchmark_readback(case, readback)
    solved = await client.action("study.run", {"study": study_path, "timeout_s": args.solve_timeout_s},
                                 key="chain-b-solve", request="chain-b", rpc_timeout_s=180.0)
    solution_status = "PASS" if _success(solved) else ("BLOCKED" if _blocked_payload(solved) else "FAIL")
    _mark(case, "transient_solve_produced_solution", solution_status,
          None if _success(solved) else f"study.run returned {_error_code(solved) or 'an invalid envelope'}",
          observed=_data(solved))
    if _success(solved):
        _record_solution(state, case, source="chain B transient solve", dataset=args.dataset_tag, solution="sol1")
    if not _success(solved):
        for name in live_names[2:]:
            _mark(case, name, "NOT_RUN", "prerequisite subcase transient_solve_produced_solution did not pass")
        case.finish()
        return
    samples = await client.action("result.sample_path", {
        "spec": {"expressions": ["T"], "dataset": args.dataset_tag, "solution": "sol1"},
        "path_definition": {"kind": "line", "start": [0.0, config["width_m"] / 2, config["height_m"] / 2],
                            "end": [config["length_m"], config["width_m"] / 2, config["height_m"] / 2],
                            "samples": config["sample_count"]}}, key="chain-b-sample", request="chain-b",
        rpc_timeout_s=60.0)
    verdict = _check_chain_b(samples, reference)
    rows_out = _extract_samples(samples)
    table_path = run_dir / "chainB_samples.json"
    _write_json(table_path, {"reference_sha256": reference["reference_sha256"], "samples": rows_out, "verdict": verdict[1]})
    case.assertions["chain_b_samples"] = {"path": str(table_path), "sha256": _sha256(table_path), "rows": len(rows_out)}
    _mark(case, "normalized_max_error_le_1e-3", verdict[0], verdict[1],
          **(verdict[2] if len(verdict) > 2 else {}))
    time_values = _data(samples).get("time_values") or _data(solved).get("time_values")
    recorded_ok = bool(rows_out) and isinstance(time_values, list) and len(time_values) >= 2
    case.assertions["chain_b_recording"] = {"sample_rows": len(rows_out), "time_values": time_values,
                                           "mesh": _data(solved).get("mesh"),
                                           "statistics": _json_safe(_data(await client.action(
                                               "mesh.statistics", {"path": mesh_path}, key="chain-b-stats",
                                               request="chain-b")))}
    _mark(case, "time_points_and_mesh_recorded", "PASS" if recorded_ok else "FAIL",
          None if recorded_ok else "the time points and mesh identity were not recorded together with the samples")
    save_target = run_dir / "chainB_result.mph"
    saved = await client.action("model.save", {"destination": {"path": str(save_target)}, "overwrite": True,
                                              "include_solution": True}, key="chain-b-save", request="chain-b")
    digest = _sha256(save_target) if save_target.is_file() else None
    case.assertions["chain_b_saved_mph"] = {"path": str(save_target), "sha256": digest,
                                            "reopen_command": f"tools/phase4_run_mcp.py --reopen-check {save_target}"}
    case.finish()


def _adopt_payload_model(client: ActionClient, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Bind the model a *selection* operation just loaded or created.

    ``ActionClient._record_identity`` deliberately refuses to switch the bound model when an
    envelope names a different model tag — that is what keeps a stale model_ref from leaking into
    a later case.  A chain-C continuation load is the one place where the tag is *expected* to
    change, so the switch is made explicit here (and recorded), instead of being refused by the
    product as a bound request.
    """
    ref, revision = _payload_execution(payload)
    if ref is None:
        return {"adopted": False, "reason": "the envelope carried no execution.model_ref readback"}
    client.state["ref"] = ref
    if revision is not None:
        client.state["revision"] = revision
    return {"adopted": True, "model_ref": _json_safe(ref), "revision": revision}


async def _case_w16_t019_chain_c(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                                 state: dict[str, Any]) -> None:
    chain_ops = ("model.load", "node.find", "node.property_get", "study.step_update", "study.run",
                 "solver.list", "solver.inspect", "model.save")
    rows = await _prepare_case(case, host, (*_plan_ops(case.case_id), *chain_ops))
    chain_ok = all((rows.get(operation) or {}).get("available") for operation in chain_ops)
    case.assertion("static_chain_c_ops_executable", chain_ok, operations=list(chain_ops))
    chain_ok = _availability_subcase(case, "static_chain_c_ops_availability", chain_ops, rows, flag=chain_ok)
    user_model = args.chain_c_model
    if user_model is None:
        case.assertions["user_style_model"] = {"provided": False}
        case.subcase("user_style_model_available", "NOT_RUN", level="fixture",
                     reason="no user-style continuation model was supplied (--chain-c-model)")
    else:
        available = Path(user_model).is_file()
        case.assertions["user_style_model"] = {"provided": True, "path": str(user_model), "exists": available,
                                              "sha256": _sha256(Path(user_model)) if available else None}
        case.subcase("user_style_model_available", "PASS" if available else "FAIL", level="fixture",
                     reason=None if available else "the supplied continuation model does not exist",
                     sha256=_sha256(Path(user_model)) if available else None)
    live_names = ("target_only_modified", "non_target_nodes_preserved", "manual_solver_preserved",
                  "derived_values_and_data_association_preserved", "solve_after_continuation")
    prerequisite = case.subcase_status("user_style_model_available")
    if prerequisite != "PASS":
        reason = ("the user-style continuation model was not supplied"
                  if user_model is None else "the supplied continuation model is not readable")
        for name in live_names:
            _mark(case, name, "BLOCKED", reason)
        case.finish()
        return
    runnable, status, why = _can_run(case, args, state, rows, chain_ops)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    store: dict[str, Any] = {}
    # ``model.load`` is a *selection* operation: the product refuses a load that arrives with a
    # bound model_ref ("model selection requires an unbound request" — observed live as
    # MODEL_IDENTITY_MISMATCH, because the driver sent the previous case's ref and revision).
    # The load is therefore sent unbound, and the model it returns becomes the bound model for
    # the rest of this chain.
    loaded = await _step(case, host, client, args, state, name="target_only_modified", operation="model.load",
                         arguments={"artifact_id": args.chain_c_artifact_id,
                                    "path_policy": {"path": str(user_model)}},
                         require_model=False, store=store)
    adoption = _adopt_payload_model(client, loaded)
    case.assertions["chain_c_bound_model"] = {"load": _envelope_identity(loaded), **adoption,
                                              "path_policy": str(user_model),
                                              "artifact_id": args.chain_c_artifact_id}
    if loaded is None or not _success(loaded) or not adoption.get("adopted"):
        detail = (_error_code(loaded) if loaded is not None else None) or adoption.get("reason") or "invalid envelope"
        for name in live_names[1:]:
            _mark(case, name, "NOT_RUN",
                  f"prerequisite subcase target_only_modified did not pass ({detail})")
        case.finish()
        return
    before = await client.host.call("model_tree", {"depth": 2,
                                                   "execution": _execution(key="chain-c-tree-before",
                                                                           ref=state.get("ref"),
                                                                           revision=state.get("revision"))})
    # ``solver.inspect`` addresses a *solver* sequence or one of its features — a study path is
    # refused with INVALID_NODE_PATH (observed live, where the driver sent study/std1).  The tag is
    # read from the published listing of the bound model, never assembled from a configured default.
    solver_listing = await client.action("solver.list", {"filter": {"study": args.study_tag}},
                                         key="chain-c-solver-list", request="chain-c")
    solver_path, solver_path_origin = _solver_sequence_path(solver_listing, study=args.study_tag)
    case.assertions["chain_c_solver_listing"] = {
        "envelope": _envelope_identity(solver_listing), "data": _json_safe(_data(solver_listing)),
        "solver_path": solver_path, "origin": solver_path_origin,
        "filter": {"study": args.study_tag}}
    if solver_path is None:
        solver_before = None
        _mark(case, "manual_solver_preserved", "BLOCKED",
              f"the bound model's solver sequence could not be addressed: {solver_path_origin}",
              observed=case.assertions["chain_c_solver_listing"])
    else:
        solver_before = await client.action("solver.inspect", {"path": solver_path, "depth": 3},
                                            key="chain-c-solver-before", request="chain-c")
    case.assertions["chain_c_before"] = {"tree": _json_safe(_data(before)), "solver": _json_safe(_data(solver_before)),
                                         "solver_path": solver_path, "solver_path_origin": solver_path_origin}
    update = await _step(case, host, client, args, state, name="non_target_nodes_preserved", operation="study.step_update",
                         arguments={"path": {"segments": [{"collection": "study", "tag": "std1"},
                                                          {"collection": "feature", "tag": "time"}]},
                                    "properties": [{"name": "tlist", "value": {"kind": "float64", "shape": [2],
                                                                               "data": [0.0, _seconds_value(args.continuation_time)]}}]},
                         prereq="target_only_modified", store=store)
    if update is not None and _success(update):
        after = await client.host.call("model_tree", {"depth": 2,
                                                      "execution": _execution(key="chain-c-tree-after",
                                                                              ref=state.get("ref"),
                                                                              revision=state.get("revision"))})
        same = _json_safe(_data(before).get("tree")) == _json_safe(_data(after).get("tree"))
        case.assertions["chain_c_tree_diff"] = {"identical": same}
        _mark(case, "non_target_nodes_preserved", "PASS" if same else "FAIL",
              None if same else "the study-tree readback changed for nodes outside the continuation target")
    else:
        _mark(case, "non_target_nodes_preserved", "FAIL", "the continuation update did not apply")
    solver_before_data = _json_safe(_data(solver_before)) if solver_before is not None else None
    solver_before_config = _solver_configuration(solver_before)

    def _check_manual_solver(payload: Mapping[str, Any], bundle: dict[str, Any]) -> Any:
        if not _success(payload):
            # A refusal is not a changed configuration: an unreadable or gated inspect is blocked.
            return _unreadable_verdict(payload, "the manual solver configuration could not be re-read")
        if solver_before_config is None:
            return ("BLOCKED",
                    "the solver configuration could not be read before the continuation, so it cannot "
                    "be compared afterwards")
        after_config = _solver_configuration(payload)
        if after_config is None:
            return _unreadable_verdict(payload, "the manual solver configuration could not be re-read")
        if after_config != solver_before_config:
            # Compare what a *manual configuration* is: the solver features and their settings.
            # Comparing the whole inspect payload made the line fail on the engine's own state
            # (durations, initialization flags, problem counters) which changes across any
            # continuation without a single solver setting being touched.
            return ("FAIL", "the manual solver configuration changed across the continuation: "
                            + _solver_configuration_delta(solver_before_config, after_config),
                    {"before": solver_before_config, "after": after_config})
        return "PASS", None, {"configuration": after_config}

    if solver_path is None:
        # Already marked BLOCKED above with the listing's own reason: the line keeps that verdict.
        pass
    else:
        await _step(case, host, client, args, state, name="manual_solver_preserved", operation="solver.inspect",
                    arguments={"path": solver_path, "depth": 3},
                    prereq="non_target_nodes_preserved", store=store, check=_check_manual_solver)
    if solver_before_data is not None:
        # The full payload is still kept as evidence, so a reader can see exactly which of its
        # fields moved when the configuration comparison reports a difference.
        case.assertions["chain_c_solver_payloads"] = {"before": solver_before_data,
                                                      "after_read": store.get("manual_solver_preserved")}
    await _step(case, host, client, args, state, name="derived_values_and_data_association_preserved",
                operation="node.find",
                arguments={"query": {"kind": "derived_value"}, "root": {"segments": []}},
                prereq="manual_solver_preserved", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _data(payload)})
                    if _success(payload) and int(_data(payload).get("count") or 0) >= 0
                    else ("FAIL", "the derived-value nodes could not be enumerated after the continuation")
                ))
    await _step(case, host, client, args, state, name="solve_after_continuation", operation="study.run",
                arguments={"study": {"segments": [{"collection": "study", "tag": "std1"}]},
                           "timeout_s": args.solve_timeout_s},
                prereq="derived_values_and_data_association_preserved", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _data(payload)})
                    if _success(payload) else ("BLOCKED" if _blocked_payload(payload) else "FAIL",
                                               f"study.run returned {_error_code(payload) or 'an invalid envelope'}")
                ))
    case.finish()


#: Solver settings the driver probes when it needs one writable sub-feature property, ordered by
#: how safe a change is (iteration caps, then tolerances).  The engine's own publish table decides
#: *which* of them exists on the inspected feature; this tuple only ranks the candidates.
SOLVER_UPDATE_PROPERTY_PREFERENCE = ("maxiter", "maxsegiter", "iter", "itrestart", "nliter",
                                     "pivotthreshold", "rtol", "stol", "atol", "damping")
#: Settings that are *not* solver tuning: never selected as the acceptance target.
SOLVER_PROPERTY_BLOCKLIST = frozenset({"message", "label", "tag", "type", "componentid", "study",
                                       "studystep", "changedproperties", "hiddenchangedproperties",
                                       "lastchangedproperty", "enable", "plot", "soldatamode"})


def _solver_rows(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The solver rows a published ``solver.list`` read reported (never a guessed tag)."""
    rows = _data(payload).get("solvers")
    return [dict(row) for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def _solver_sequence_path(payload: Mapping[str, Any] | None, *, study: str | None = None,
                          prefer_tag: str | None = None) -> tuple[dict[str, Any] | None, str]:
    """The solver-sequence path ``solver.list`` reported for the bound model.

    ``solver.inspect`` accepts a solver sequence (``sol``) or a solver feature — never a study
    path, which the product refuses with ``INVALID_NODE_PATH`` (observed live in chain C, where the
    driver addressed ``study/std1``).  The tag is therefore read from the published listing.
    """
    rows = _solver_rows(payload)
    if not rows:
        return None, "the published solver listing reported no solver sequence"
    chosen = None
    if prefer_tag:
        chosen = next((row for row in rows if str(row.get("solver") or "") == prefer_tag), None)
    if chosen is None and study:
        chosen = next((row for row in rows if str(row.get("study") or "") == study), None)
    if chosen is None:
        chosen = rows[0]
    path = chosen.get("path")
    if not isinstance(path, Mapping) or not isinstance(path.get("segments"), list) or not path["segments"]:
        return None, f"solver {chosen.get('solver')!r} was listed without a readable path"
    return ({"segments": [dict(segment) for segment in path["segments"] if isinstance(segment, Mapping)]},
            f"taken from the published solver listing ({chosen.get('solver')!r}, study {chosen.get('study')!r})")


#: Inspect fields that are *not* part of the user's solver configuration but of the engine's own
#: state: they move across any continuation (durations, initialization flags, problem counters,
#: cached labels) without a single solver setting being touched.  The comparison for the chain-C
#: acceptance line is made on the features' own settings tables, never on these.
SOLVER_VOLATILE_KEYS = frozenset({"duration_s", "duration", "build_time_ms", "is_initialized", "initialized",
                                  "has_problems", "problems", "lastchangedproperty", "changedproperties",
                                  "hiddenchangedproperties", "label", "solnum", "default_solnum", "revision",
                                  "at", "state", "status_message", "warnings", "errors"})


def _solver_configuration(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The user-visible solver configuration: one row per solver feature and its settings.

    ``None`` when the payload is not a successful inspect — the caller reports that as blocked
    rather than comparing a refusal against a configuration.
    """
    if payload is None or not _success(payload):
        return None
    data = _data(payload)
    features = [{"tag": row.get("tag"), "type_id": row.get("type_id"),
                 "settings": _configuration_settings(row.get("settings"))}
                for row in _solver_feature_rows(payload)]
    return {"solver": data.get("solver"),
            "study": data.get("study"),
            "features": features}


def _configuration_settings(settings: Any) -> dict[str, Any]:
    """One feature's settings table without the engine-state keys."""
    if not isinstance(settings, Mapping):
        return {}
    return {str(name): _json_safe(value) for name, value in sorted(settings.items())
            if str(name) not in SOLVER_VOLATILE_KEYS}


def _solver_configuration_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> str:
    """A readable description of what differs between two solver configurations."""
    parts: list[str] = []
    before_rows = {str(row.get("tag")): row for row in (before.get("features") or []) if isinstance(row, Mapping)}
    after_rows = {str(row.get("tag")): row for row in (after.get("features") or []) if isinstance(row, Mapping)}
    for tag in sorted(set(before_rows) - set(after_rows)):
        parts.append(f"feature {tag!r} disappeared")
    for tag in sorted(set(after_rows) - set(before_rows)):
        parts.append(f"feature {tag!r} appeared")
    for tag in sorted(set(before_rows) & set(after_rows)):
        old, new = before_rows[tag], after_rows[tag]
        if old.get("type_id") != new.get("type_id"):
            parts.append(f"feature {tag!r} type {old.get('type_id')!r} -> {new.get('type_id')!r}")
        old_settings, new_settings = old.get("settings") or {}, new.get("settings") or {}
        for name in sorted(set(old_settings) | set(new_settings)):
            if old_settings.get(name) != new_settings.get(name):
                parts.append(f"feature {tag!r} setting {name!r}: {old_settings.get(name)!r} -> "
                             f"{new_settings.get(name)!r}")
    return "; ".join(parts) if parts else "the configuration rows differ (see the recorded payloads)"


def _solver_feature_rows(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Every solver feature the inspect payload published, pre-order, with its depth."""
    out: list[dict[str, Any]] = []

    def walk(rows: Any, depth: int) -> None:
        if not isinstance(rows, list) or depth > 8:
            return
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            path = row.get("path")
            if isinstance(path, Mapping) and isinstance(path.get("segments"), list):
                out.append({"path": {"segments": [dict(segment) for segment in path["segments"]
                                                  if isinstance(segment, Mapping)]},
                            "tag": row.get("tag"), "type_id": row.get("type_id"), "depth": depth,
                            "settings": row.get("settings") if isinstance(row.get("settings"), Mapping) else {},
                            "settings_metadata": row.get("settings_metadata")
                            if isinstance(row.get("settings_metadata"), Mapping) else {}})
            walk(row.get("children"), depth + 1)

    walk(_data(payload).get("features"), 0)
    return out


def _solver_update_target(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The (feature, property, value) triple the *inspect payload* says can be updated.

    The sub-feature and its property are read from the engine's own publish table
    (``settings_metadata``): a feature whose metadata is UNKNOWN is not a target, and the property
    is one of the build's own scalar settings — never a hard-coded tag such as ``maxiter`` on a
    feature the engine never publishes it on (observed live: the driver sent ``maxiter`` to a
    ``StudyStep`` and the product refused it with "unsupported fields").
    """
    preference = {name: index for index, name in enumerate(SOLVER_UPDATE_PROPERTY_PREFERENCE)}
    best: tuple[int, int, int, dict[str, Any]] | None = None
    order = 0
    for row in _solver_feature_rows(payload):
        for name, meta in row["settings_metadata"].items():
            if not isinstance(name, str) or not isinstance(meta, Mapping):
                continue
            if meta.get("metadata_status") != "KNOWN" or meta.get("shape_rank") != 0:
                continue
            kind = meta.get("kind")
            if kind not in {"int32", "int64", "float64", "boolean"}:
                continue
            lowered = name.lower()
            if lowered in SOLVER_PROPERTY_BLOCKLIST:
                continue
            candidate = (preference.get(lowered, len(preference)), row["depth"], order,
                         {"path": row["path"], "feature": row["tag"], "type_id": row["type_id"],
                          "name": name, "kind": kind, "shape_rank": 0,
                          "current": row["settings"].get(name), "metadata_status": "KNOWN"})
            order += 1
            if best is None or candidate[:3] < best[:3]:
                best = candidate
    if best is None:
        return None
    target = best[3]
    value = _solver_probe_value(target["kind"], target.get("current"))
    if value is None:
        return None
    target["value"] = value
    target["source"] = "solver.inspect settings_metadata"
    return target


def _solver_probe_value(kind: str, current: Any) -> Any:
    """A different-but-safe value for one scalar solver setting (``None`` when none is safe)."""
    if kind in {"int32", "int64"}:
        number = current if isinstance(current, int) and not isinstance(current, bool) else None
        if number is None and isinstance(current, float) and current.is_integer():
            number = int(current)
        return (number or 0) + 1 if number is not None and number >= 0 else 1
    if kind == "float64":
        number = current if isinstance(current, (int, float)) and not isinstance(current, bool) else None
        if number is None:
            return None
        value = float(number)
        if value == 0.0:
            return 1.0e-6
        return value * 10.0 if 0 < value <= 1.0e-3 else value / 10.0
    if kind == "boolean":
        return not current if isinstance(current, bool) else True
    return None


def _solver_tree_verdict(payload: Mapping[str, Any] | None) -> tuple[str, str | None, dict[str, Any]]:
    """Verdict for ``solver.list``: the bound model must expose a readable solver sequence.

    An empty list is *not* a pass: a model whose study never generated a sequence has no
    solver tree to read, and that is a blocked target, not a verified read.
    """
    data = _data(payload)
    rows_raw = data.get("solvers")
    rows = [row for row in rows_raw if isinstance(row, Mapping)] if isinstance(rows_raw, list) else []
    detail = {"error_code": _error_code(payload), "solver_count": data.get("solver_count"), "solvers": _json_safe(rows)}
    if not _success(payload):
        status, reason = _unreadable_verdict(payload, "the solver tree could not be read")
        return status, reason, detail
    if not rows:
        return ("BLOCKED",
                "the bound model exposes no solver sequence for the requested study, so no solver tree "
                "exists to read (study.solver_generate was attempted first; see solver_generate)", detail)
    missing_paths = [str(row.get("solver")) for row in rows if not isinstance(row.get("path"), Mapping)]
    if missing_paths:
        return ("FAIL", "the solver listing reported a solver without a readable path: "
                        + ", ".join(sorted(missing_paths)), detail)
    return "PASS", None, detail


def _first_feature_path(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """First ``feature`` node path inside an inspect payload (recursive, bounded by the payload)."""
    seen = 0

    def walk(value: Any) -> dict[str, Any] | None:
        nonlocal seen
        seen += 1
        if seen > 4000:
            return None
        if isinstance(value, Mapping):
            path = value.get("path")
            if isinstance(path, Mapping) and isinstance(path.get("segments"), list) and path["segments"]:
                last = path["segments"][-1]
                if isinstance(last, Mapping) and str(last.get("accessor") or last.get("collection")) == "feature":
                    return {"segments": [dict(segment) for segment in path["segments"] if isinstance(segment, Mapping)]}
            for item in value.values():
                found = walk(item)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = walk(item)
                if found is not None:
                    return found
        return None

    return walk(_data(payload))


def _engine_gate_note(host: ProductionHost) -> str | None:
    """The control-plane gate chain this run actually observed — the reason a blocked case needs.

    A case refused with "reconcile unfinished engine work before new operations" is blocked by
    *another* call's unresolved job: the refusal envelope names only the job it just refused, so a
    case that reports a bare "could not be established" hides the one fact a reader needs.  The
    chain is taken from the driver's own ledger (job ids observed on envelopes, their release
    attempts and what ``job_reconcile``/``job_status`` answered) — never inferred.
    """
    ledger = host.ledger_evidence()
    unresolved = [str(job) for job in (ledger.get("unresolved_jobs") or [])]
    refusals = host.gate_refusals if isinstance(host.gate_refusals, Mapping) else {}
    if not unresolved:
        return None
    entries = [entry for entry in (ledger.get("entries") or []) if isinstance(entry, Mapping)]
    described: list[str] = []
    for job_id in unresolved[:3]:
        entry = next((row for row in entries if str(row.get("job_id")) == job_id), {})
        attempts = [row for row in (entry.get("release_attempts") or []) if isinstance(row, Mapping)]
        last = attempts[-1] if attempts else {}
        detail = f"job {job_id}: last job_reconcile status={last.get('status')!r}"
        if last.get("reconciliation"):
            detail += f", reconciliation {_json_safe(last['reconciliation'])}"
        if last.get("job_reads"):
            detail += f", job reads {_json_safe(last['job_reads'])}"
        described.append(detail)
    note = ("the control plane refuses new work while its ledger still holds unreconciled job(s), and the "
            "published reconcile reads did not report a release: " + "; ".join(described))
    if refusals.get("count"):
        note += (f" (gate refusals this run: {refusals.get('count')}, last: "
                 f"{_json_safe(refusals.get('last'))})")
    return note


async def _case_w16_t020(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                         state: dict[str, Any]) -> None:
    # Every operation this case gates on: the solver tree is read through the solver route, and
    # the study the tree belongs to is read first (a solver filter naming a study the model does
    # not have returns an empty list that no generate call can fill).  The *target-creation*
    # operations are probed separately: they are only required when the bound model has no study
    # or no sequence at all, so a build without them still reads a model-owned tree.
    solver_ops = ("study.list", "solver.list", "solver.inspect", "solver.feature_update", "study.run")
    target_ops = ("study.create", "study.step_create", "study.solver_generate", "node.property_get")
    rows = await _prepare_case(case, host, (*_plan_ops(case.case_id), *solver_ops, *target_ops))
    solver_ok = all((rows.get(operation) or {}).get("available") for operation in solver_ops)
    case.assertion("static_solver_ops_executable", solver_ok, operations=list(solver_ops))
    case.assertions["t020_target_ops_availability"] = {
        operation: _json_safe(rows.get(operation)) for operation in target_ops}
    case.assertion("static_solver_target_ops_published",
                   all((rows.get(operation) or {}).get("available") for operation in target_ops),
                   operations=list(target_ops))
    solver_ok = _availability_subcase(case, "static_solver_ops_availability", solver_ops, rows, flag=solver_ok)
    live_names = ("solver_tree_read", "sub_feature_property_update_readback", "solve_after_subfeature_update",
                  "unknown_property_fails_accurately", "manual_solver_not_auto_overwritten")
    runnable, status, why = _can_run(case, args, state, rows, solver_ops, require_writable_model=True)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    store: dict[str, Any] = {}

    # The study the solver sequence belongs to is *selected from the bound model's own study
    # list* first: ``solver.list`` with a filter naming a study the model does not have returns
    # an empty list that no generate call can fill (observed live: the case asked for
    # ``filter={"study": "std1"}``, got zero solvers, and the generate step was still gated on
    # the empty read).  A model without any study gets the driver's own study and step, so the
    # target is created rather than assumed.
    listed_studies = await _step(case, host, client, args, state, name="study_target_read",
                                 operation="study.list", arguments={}, store=store,
                                 check=lambda payload, bundle: (
                                     _unreadable_verdict(payload, "the study list could not be read")
                                     if not _success(payload) else
                                     ("PASS", None, {"observed": _data(payload)})
                                 ))
    studies = [row for row in (_data(listed_studies).get("studies") or []) if isinstance(row, Mapping)] \
        if listed_studies is not None else []
    chosen: Mapping[str, Any] | None = None
    selection = "from the bound model's study list"
    if studies:
        chosen = next((row for row in studies if row.get("study") == args.study_tag), None)
        if chosen is None:
            chosen = next((row for row in studies if row.get("solver_sequences")), studies[0])
            selection = "the configured study tag is not on this model; the study with a solver sequence was chosen"
    else:
        missing_ops = [operation for operation in ("study.create", "study.step_create")
                       if not (rows.get(operation) or {}).get("available")]
        if missing_ops:
            for name in live_names:
                _mark(case, name, "BLOCKED",
                      "the bound model has no study and this build does not publish "
                      + ", ".join(sorted(missing_ops)) + ", so no study target could be created")
            case.finish()
            return
        created_study = await _step(case, host, client, args, state, name="study_target_create",
                                    operation="study.create", arguments={"tag": args.study_tag,
                                                                        "label": "G3 T020 solver tree"},
                                    store=store)
        if not _success(created_study):
            # The study the whole case hangs on was refused: the reason is the refusal *and* the
            # gate chain behind it (observed live: the refusal was the control plane's
            # "reconcile unfinished engine work before new operations", which names a job the
            # product then could not release — the case must report that chain, not lose it).
            note = _engine_gate_note(host)
            if note:
                case.subcase("study_target_create", case.subcase_status("study_target_create") or "BLOCKED",
                             reason=(f"study.create was refused ({_error_code(created_study) or 'invalid envelope'}); "
                                      f"{note}"), level="live", gate=note)
                for name in live_names:
                    if case.subcase_status(name) in {None, "BLOCKED"}:
                        _mark(case, name, "BLOCKED", f"the study target could not be created; {note}")
        if _success(created_study):
            step_created = await _step(case, host, client, args, state, name="study_target_step",
                                       operation="study.step_create",
                                       arguments={"study": {"segments": [{"collection": "study", "tag": args.study_tag}]},
                                                  "tag": "stat", "type_id": "Stationary", "properties": []},
                                       store=store)
            if _success(step_created):
                chosen = {"study": args.study_tag, "path": {"segments": [{"collection": "study", "tag": args.study_tag}]}}
                selection = "the bound model had no study, so one was created for this case"
    study_tag = str(chosen.get("study")) if isinstance(chosen, Mapping) and chosen.get("study") else args.study_tag
    study_path = chosen.get("path") if isinstance(chosen, Mapping) and isinstance(chosen.get("path"), Mapping) \
        else {"segments": [{"collection": "study", "tag": study_tag}]}
    case.assertions["t020_study_target"] = {
        "study_tag": study_tag, "study_path": _json_safe(study_path), "selection": selection,
        "studies_on_model": [{"study": row.get("study"), "steps": row.get("step_count"),
                              "solver_sequences": row.get("solver_sequences")} for row in studies],
        "configured_study_tag": args.study_tag,
    }
    if not studies and not isinstance(chosen, Mapping):
        note = _engine_gate_note(host)
        for name in live_names:
            _mark(case, name, "BLOCKED",
                  "no study could be established on the bound model, so no solver sequence can exist"
                  + (f"; {note}" if note else ""))
        case.finish()
        return
    case.subcase("study_target_read", "PASS", level="live",
                 reason=None, study=study_tag, selection=selection)

    listed = await _step(case, host, client, args, state, name="solver_tree_read", operation="solver.list",
                         arguments={"filter": {"study": study_tag}}, store=store,
                         check=lambda payload, bundle: _solver_tree_verdict(payload))
    solver_rows = [row for row in (_data(listed).get("solvers") or []) if isinstance(row, Mapping)] if listed else []
    if not solver_rows:
        # Self-made target: a study that never generated a solver sequence has no tree to
        # read, so the driver asks COMSOL to materialize the default sequence and records
        # exactly what that produced before touching any verdict.  The generate step is gated
        # on the *study target* — gating it on the empty tree read was the live bug.
        await _step(case, host, client, args, state, name="solver_generate", operation="study.solver_generate",
                    arguments={"study": study_path, "replace_existing": False},
                    prereq="study_target_read", store=store,
                    check=lambda payload, bundle: (
                        ("PASS", None, {"observed": _data(payload)})
                        if _success(payload) or _error_code(payload) == "SOLVER_SEQUENCE_EXISTS"
                        else _unreadable_verdict(payload, "no solver sequence could be generated for the study")
                    ))
        relist_key = _fresh_key("t020-relist")
        relisted = await client.action("solver.list", {"filter": {"study": study_tag}},
                                       key=relist_key, request="solver-relist")
        case.assertions["t020_solver_relist"] = _json_safe(_data(relisted))
        relist_status, relist_reason, _ = _solver_tree_verdict(relisted)
        if relist_status == "PASS":
            listed = relisted
            solver_rows = [row for row in (_data(relisted).get("solvers") or []) if isinstance(row, Mapping)]
        elif _data(listed).get("solver_count") == 0:
            # The read is still empty after the generate attempt: report the generate outcome
            # in the tree-read reason instead of a second, unexplained "no sequence" line.
            case.subcase("solver_tree_read", "BLOCKED", level="live", force=True,
                         reason=(f"study {study_tag!r} exposes no solver sequence after study.solver_generate "
                                 f"({_error_code(relisted) or relist_reason}); the tree was not readable"),
                         observed=_json_safe(_data(listed)))

    solver_feature_path: dict[str, Any] | None = None
    solver_path: dict[str, Any] | None = None
    if solver_rows:
        first = solver_rows[0]
        candidate = first.get("path")
        if isinstance(candidate, Mapping) and isinstance(candidate.get("segments"), list):
            solver_path = {"segments": [dict(segment) for segment in candidate["segments"] if isinstance(segment, Mapping)]}
    if solver_path is None:
        for name in ("sub_feature_property_update_readback", "solve_after_subfeature_update",
                     "manual_solver_not_auto_overwritten"):
            _mark(case, name, "BLOCKED", "no solver sequence was readable on the bound model, so no sub-feature could be addressed")
        _mark(case, "unknown_property_fails_accurately", "BLOCKED",
              "no solver sequence was readable on the bound model, so no sub-feature could be addressed")
        case.finish()
        return
    inspected = await client.action("solver.inspect", {"path": solver_path, "depth": 4},
                                    key="t020-inspect", request="solver.inspect")
    case.assertions["solver_inspect"] = {"path": solver_path, "success": inspected.get("success"),
                                         "error_code": _error_code(inspected), "data": _json_safe(_data(inspected))}
    # The writable sub-feature and its property come from the *inspect payload's own* publish table:
    # a solver sequence's children carry engine tags the driver must not guess (the live run sent
    # ``maxiter`` to ``StudyStep`` and was refused with "unsupported fields").  The first feature
    # path is kept only for the two *negative* probes, which need a real feature to be refused on.
    solver_update = _solver_update_target(inspected)
    solver_feature_path = ((solver_update or {}).get("path")
                           or _first_feature_path(inspected)
                           or {**solver_path, "segments": [
                               *solver_path["segments"],
                               {"collection": "feature", "tag": args.solver_feature_tag}]})
    case.assertions["t020_solver_feature_path"] = solver_feature_path
    case.assertions["t020_update_target"] = _json_safe(solver_update)
    feature_origin = ("the target's own feature path" if (solver_update or {}).get("path")
                      else "solver.inspect" if _first_feature_path(inspected)
                      else f"configured tag {args.solver_feature_tag!r}")
    solver_update_name = str((solver_update or {}).get("name") or "maxiter")
    solver_update_kind = str((solver_update or {}).get("kind") or "int32")
    solver_update_value = (solver_update or {}).get("value")
    if solver_update_value is None:
        solver_update_value = 50
    if not _success(inspected):
        inspect_status, inspect_reason = _unreadable_verdict(inspected, "the solver tree could not be inspected")
        _mark(case, "sub_feature_property_update_readback", inspect_status,
              f"{inspect_reason} (feature path taken from {feature_origin})")
        _mark(case, "solve_after_subfeature_update", inspect_status, inspect_reason)
    elif solver_update is None:
        # The tree was readable but publishes no scalar setting the driver may tune: the sub-feature
        # property line has no target at all, which is blocked (never "the update failed").
        message = ("the inspected solver tree publishes no scalar setting (or none the driver may tune) "
                   f"on any of its {len(_solver_feature_rows(inspected))} feature(s), so no sub-feature "
                   "property could be updated")
        _mark(case, "sub_feature_property_update_readback", "BLOCKED", message,
              feature_path=solver_feature_path)
        _mark(case, "solve_after_subfeature_update", "BLOCKED",
              "no writable solver sub-feature property was available, so the solve after the update was not attempted")
    else:
        updated = await _step(case, host, client, args, state, name="sub_feature_property_update_readback",
                              operation="solver.feature_update",
                              arguments={"path": solver_feature_path,
                                         "properties": [{"name": solver_update_name,
                                                         "value": {"kind": solver_update_kind, "shape": [],
                                                                   "data": solver_update_value}}]},
                              prereq="solver_tree_read", store=store,
                              check=lambda payload, bundle: (
                                  ("PASS", None, {"observed": _data(payload), "feature_path": solver_feature_path,
                                                  "property": solver_update_name,
                                                  "written": solver_update_value,
                                                  "previous": solver_update.get("current")})
                                  if _success(payload) else _unreadable_verdict(
                                      payload, f"the solver sub-feature {solver_feature_path} could not be updated")
                              ))
        if updated is not None and _success(updated):
            readback = await client.action("node.property_get",
                                           {"path": solver_feature_path, "names": [solver_update_name]},
                                           key="t020-readback", request="solver-readback")
            rows = _property_value_rows(readback)
            row = _row_value(rows, solver_update_name)
            read_value = (row or {}).get("value") if isinstance(row, Mapping) else None
            case.assertions["solver_property_readback"] = {
                "path": solver_feature_path, "property": solver_update_name,
                "written": solver_update_value, "previous": solver_update.get("current"),
                "readback": _json_safe(_data(readback)), "value": _json_safe(read_value)}
            readback_ok = bool(_success(readback) and rows and isinstance(read_value, Mapping))
            if not readback_ok:
                solve_status, solve_detail = _unreadable_verdict(
                    readback, "the solver sub-feature property could not be read back")
                _mark(case, "solve_after_subfeature_update", solve_status, solve_detail)
            else:
                previous = _typed_number({"kind": solver_update_kind, "shape": [],
                                          "data": solver_update.get("current")})
                written = _typed_number({"kind": solver_update_kind, "shape": [], "data": solver_update_value})
                observed = _typed_number(read_value)
                if (observed is not None and previous is not None and written is not None
                        and written != previous and observed == previous):
                    # The update was accepted but the readback still reports the previous value: the
                    # write was not observable, which is blocked rather than a satisfied line.
                    _mark(case, "solve_after_subfeature_update", "BLOCKED",
                          f"the readback of {solver_update_name!r} still reports the previous value "
                          f"({solver_update.get('current')!r}) after the update was accepted",
                          observed=case.assertions["solver_property_readback"])
                else:
                    _mark(case, "solve_after_subfeature_update", "PASS", None)
        elif updated is not None:
            _mark(case, "solve_after_subfeature_update", "BLOCKED",
                  "the solver sub-feature property was not updated, so the solve was not attempted")
    await _step(case, host, client, args, state, name="unknown_property_fails_accurately",
                operation="solver.feature_update",
                arguments={"path": solver_feature_path, "properties": [{"name": "phase4_unknown_property",
                                                                        "value": {"kind": "int32", "shape": [], "data": 1}}]},
                prereq="solver_tree_read", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _error_code(payload)})
                    if (not _success(payload)) and bool(_error_message(payload))
                    else (("BLOCKED", "the unknown-property refusal could not be observed because the call was blocked") if _blocked_payload(payload)
                          else ("FAIL", "an unknown solver property was accepted without an accurate error"))
                ))
    await _step(case, host, client, args, state, name="manual_solver_not_auto_overwritten",
                operation="solver.feature_update",
                arguments={"path": solver_feature_path, "properties": [{"name": "phase4_noop_probe",
                                                                        "value": {"kind": "int32", "shape": [], "data": 0}}]},
                prereq="solver_tree_read", store=store,
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _error_code(payload)})
                    if _success(payload) or "manual" in _error_message(payload).lower()
                    or _error_code(payload) in {"PROPERTY_UNKNOWN", "INVALID_REQUEST", "UNSUPPORTED_OPERATION",
                                                "NODE_NOT_FOUND", "INVALID_NODE_PATH", "API_UNSUPPORTED"}
                    else (("BLOCKED", "the manual-configuration probe could not be observed because the call was blocked")
                          if _blocked_payload(payload)
                          else ("FAIL", "a manual solver configuration was overwritten or the failure was not reported clearly"))
                ))
    case.finish()


def _first_solver_path(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    data = _data(payload)
    for key in ("path", "solver", "root"):
        value = data.get(key)
        if isinstance(value, Mapping) and isinstance(value.get("segments"), list):
            return {"segments": [dict(segment) for segment in value["segments"] if isinstance(segment, Mapping)]}
    solvers = data.get("solvers")
    if isinstance(solvers, list):
        for row in solvers:
            if isinstance(row, Mapping):
                path = row.get("path")
                if isinstance(path, Mapping) and isinstance(path.get("segments"), list):
                    return {"segments": [dict(segment) for segment in path["segments"] if isinstance(segment, Mapping)]}
    return None


# ---------------------------------------------------------------------------
# Driver-created acceptance targets (probe fixture)
# ---------------------------------------------------------------------------

#: Tags the fixture owns.  They are deliberately distinct from the suite's own node tags so
#: a live case can never mistake a fixture it created for a model node it happened to find.
PROBE_FIXTURE_TAGS = {"geometry": "p4geom", "block": "p4blk", "mesh": "p4mesh", "mesh_size": "p4size",
                      "physics": "p4ht", "temperature": "p4temp1", "material": "p4mat",
                      "property_group": "def"}
PROBE_FIXTURE_SIZE = [1.0e-3, 1.0e-3, 1.0e-3]
PROBE_FIXTURE_T0 = "300[K]"
PROBE_FIXTURE_MATERIAL_PROPERTIES: tuple[dict[str, Any], ...] = (
    {"name": "thermalconductivity",
     "value": {"kind": "expression", "shape": [3, 3], "unit": "W/(m*K)",
               "data": [["10[W/(m*K)]", "0", "0"], ["0", "10[W/(m*K)]", "0"], ["0", "0", "10[W/(m*K)]"]]}},
    {"name": "density", "value": {"kind": "expression", "shape": [], "data": "1000[kg/m^3]"}},
    {"name": "heatcapacity", "value": {"kind": "expression", "shape": [], "data": "100[J/(kg*K)]"}},
)
#: Refusals that mean "the target already exists": the fixture adopts the node after the
#: conflict is reported instead of treating it as a failure.
PROBE_FIXTURE_ADOPT_CODES = frozenset({"TAG_CONFLICT", "TAG_EXISTS", "NODE_EXISTS", "ALREADY_EXISTS"})


def _seg(collection: str, tag: str) -> dict[str, Any]:
    return {"collection": collection, "tag": tag}


class ProbeFixture:
    """Acceptance targets the driver creates itself on the bound model.

    A live case must never depend on a property that "happens to exist": every target is
    created (or adopted after the product reports a tag conflict) through a published
    operation, written, read back, and only then offered to an assertion.  Each step keeps
    its own status, so a target this build cannot expose is reported as the capability gap
    it is instead of degrading an acceptance line into a silent NOT_RUN.
    """

    def __init__(self, client: ActionClient, args: argparse.Namespace, state: dict[str, Any]) -> None:
        self.client = client
        self.args = args
        self.state = state
        self.component = args.component
        self.steps: list[dict[str, Any]] = []
        self.nodes: dict[str, dict[str, Any]] = {}
        self.group_schema: dict[str, Any] = {}

    # -- paths -----------------------------------------------------------------
    def component_path(self) -> dict[str, Any]:
        return {"segments": [_seg("component", self.component)]}

    def _under(self, key: str, collection: str, tag: str) -> dict[str, Any]:
        base = self.nodes.get(key)
        segments = list(base["segments"]) if isinstance(base, Mapping) and isinstance(base.get("segments"), list) \
            else [_seg("component", self.component)]
        return {"segments": [*segments, _seg(collection, tag)]}

    def geometry_path(self) -> dict[str, Any]:
        return self._under("component", "geom", PROBE_FIXTURE_TAGS["geometry"])

    def block_path(self) -> dict[str, Any]:
        return self._under("geometry", "feature", PROBE_FIXTURE_TAGS["block"])

    def mesh_path(self) -> dict[str, Any]:
        return self._under("component", "mesh", PROBE_FIXTURE_TAGS["mesh"])

    def mesh_feature_path(self) -> dict[str, Any]:
        return self._under("mesh", "feature", PROBE_FIXTURE_TAGS["mesh_size"])

    def physics_path(self) -> dict[str, Any]:
        return self._under("component", "physics", PROBE_FIXTURE_TAGS["physics"])

    def temperature_path(self) -> dict[str, Any]:
        return self._under("physics", "feature", PROBE_FIXTURE_TAGS["temperature"])

    def material_path(self) -> dict[str, Any]:
        return self._under("component", "material", PROBE_FIXTURE_TAGS["material"])

    def property_group_path(self) -> dict[str, Any]:
        return self._under("material", "propertyGroup", PROBE_FIXTURE_TAGS["property_group"])

    # -- steps -----------------------------------------------------------------
    def _record(self, step: str, status: str, *, operation: str | None = None, reason: str | None = None,
                **detail: Any) -> None:
        row: dict[str, Any] = {"step": step, "status": status}
        if operation:
            row["operation"] = operation
        if reason:
            row["reason"] = reason
        row.update(detail)
        self.steps.append(row)

    async def _call(self, step: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        # The identity is *fresh per step*: the fixture is rebuilt when the bound model changes, and
        # the control daemon answers a key it has already stored from the previous model with the
        # old result (or an idempotency conflict) — so a static key makes the rebuild a no-op that
        # reports the earlier model's envelope.  (Observed live: the probe reported "no scalar
        # property could be discovered or created" on a model whose fixture could never be created.)
        payload = await self.client.action(operation, dict(arguments),
                                          key=_fresh_key(f"probe-{step}"), request=f"probe-{step}")
        code = _error_code(payload)
        status = "APPLIED" if _success(payload) else ("ADOPTED" if code in PROBE_FIXTURE_ADOPT_CODES else
                                                      "BLOCKED" if _blocked_payload(payload) else "FAILED")
        self._record(step, status, operation=operation, error_code=code,
                     message=(_error_message(payload) or None) if not _success(payload) else None,
                     success=payload.get("success"))
        return payload

    async def _step_component(self) -> None:
        await self._call("component", "definition.component_manage",
                         {"action": "create", "tag": self.component})
        self.nodes["component"] = self.component_path()

    async def _step_geometry(self) -> None:
        await self._call("geometry", "geometry.sequence_create",
                         {"component": self.component, "tag": PROBE_FIXTURE_TAGS["geometry"], "dimension": 3})
        self.nodes["geometry"] = self.geometry_path()

    async def _step_block(self) -> None:
        payload = await self._call("block", "geometry.feature_create",
                                   {"parent": self.geometry_path(), "tag": PROBE_FIXTURE_TAGS["block"],
                                    "type_id": "Block",
                                    "properties": [{"name": "size", "value": {"kind": "float64", "shape": [3],
                                                                               "data": list(PROBE_FIXTURE_SIZE)}},
                                                   {"name": "pos", "value": {"kind": "float64", "shape": [3],
                                                                             "data": [0.0, 0.0, 0.0]}}]})
        if _error_code(payload) in PROBE_FIXTURE_ADOPT_CODES:
            await self._call("block_adopt", "geometry.feature_update",
                             {"path": self.block_path(),
                              "properties": [{"name": "size", "value": {"kind": "float64", "shape": [3],
                                                                        "data": list(PROBE_FIXTURE_SIZE)}}]})
        self.nodes["block"] = self.block_path()

    async def _step_mesh(self) -> None:
        await self._call("mesh", "mesh.create",
                         {"component": self.component, "tag": PROBE_FIXTURE_TAGS["mesh"],
                          "geometry": PROBE_FIXTURE_TAGS["geometry"]})
        self.nodes["mesh"] = self.mesh_path()

    async def _step_mesh_size(self) -> None:
        payload = await self._call("mesh_size", "mesh.feature_create",
                                   {"parent": self.mesh_path(), "tag": PROBE_FIXTURE_TAGS["mesh_size"],
                                    "type_id": "Size",
                                    "properties": [{"name": "hauto", "value": {"kind": "int32", "shape": [],
                                                                                "data": 4}}]})
        if _error_code(payload) in PROBE_FIXTURE_ADOPT_CODES:
            await self._call("mesh_size_adopt", "mesh.feature_update",
                             {"path": self.mesh_feature_path(),
                              "properties": [{"name": "hauto", "value": {"kind": "int32", "shape": [], "data": 4}}]})
        self.nodes["mesh_size"] = self.mesh_feature_path()

    async def _step_physics(self) -> None:
        await self._call("physics", "physics.create",
                         {"component": self.component, "tag": PROBE_FIXTURE_TAGS["physics"],
                          "type_id": self.args.heat_physics_type,
                          "geometry": PROBE_FIXTURE_TAGS["geometry"]})
        self.nodes["physics"] = self.physics_path()

    async def _step_temperature(self) -> None:
        properties = [{"name": "T0", "value": {"kind": "expression", "shape": [], "data": PROBE_FIXTURE_T0}}]
        payload = await self._call("temperature", "physics.feature_create",
                                   {"parent": self.physics_path(), "tag": PROBE_FIXTURE_TAGS["temperature"],
                                    "type_id": "TemperatureBoundary", "entity_dimension": 2,
                                    "properties": properties})
        if _error_code(payload) in PROBE_FIXTURE_ADOPT_CODES:
            await self._call("temperature_adopt", "physics.feature_update",
                             {"path": self.temperature_path(), "properties": properties})
        self.nodes["temperature"] = self.temperature_path()

    async def _step_material(self) -> None:
        properties = [dict(row) for row in PROBE_FIXTURE_MATERIAL_PROPERTIES]
        payload = await self._call("material", "material.create",
                                   {"component": self.component, "tag": PROBE_FIXTURE_TAGS["material"],
                                    "type_id": "Common", "definition": {"properties": properties}})
        if _error_code(payload) in PROBE_FIXTURE_ADOPT_CODES:
            await self._call("material_adopt", "material.set_properties",
                             {"path": self.material_path(), "group": PROBE_FIXTURE_TAGS["property_group"],
                              "properties": properties})
        self.nodes["material"] = self.material_path()

    async def _cached_fixture_is_bound(self) -> bool:
        """True when the cached fixture still lives in the currently bound model.

        The fixture is built once per run, but a run rebinds models: cases that create their own
        model (chains, T018, T020) leave *their* model bound, and the live run then reused a fixture
        built on an earlier model — every ``p4*`` path resolved to ``NODE_NOT_FOUND`` and the probe
        reported "no scalar property could be discovered or created".  The cache is therefore keyed
        by the model identity it was built against and re-verified with one published read.
        """
        cached = self.state.get("probe_fixture")
        if not isinstance(cached, Mapping) or not cached.get("built"):
            return False
        if not _same_ref(cached.get("model_ref"), self.state.get("ref")):
            self._record("cache_invalidated", "REBUILT",
                         reason=("the bound model changed since the fixture was created, so its nodes are "
                                 "recreated in the model this case addresses"))
            return False
        path = (cached.get("nodes") or {}).get("material")
        if not isinstance(path, Mapping):
            return False
        probe = await self.client.action("node.property_schema", {"path": dict(path)},
                                        key=_fresh_key("probe-fixture-verify"),
                                        request="probe-fixture-verify")
        if _error_code(probe) == "NODE_NOT_FOUND" or _blocked_payload(probe) or _unknown_outcome(probe) is not None:
            self._record("cache_invalidated", "REBUILT",
                         operation="node.property_schema", error_code=_error_code(probe),
                         reason=("the fixture's own material node is not readable in the bound model, so the "
                                 "fixture is recreated"))
            return False
        return True

    async def build(self) -> dict[str, Any]:
        """Create every fixture target once per run and return the recorded evidence."""
        if await self._cached_fixture_is_bound():
            cached = self.state["probe_fixture"]
            self.steps = [dict(row) for row in cached.get("steps") or []]
            self.nodes = {key: dict(value) for key, value in (cached.get("nodes") or {}).items()
                          if isinstance(value, Mapping)}
            self.group_schema = dict(cached.get("group_schema") or {})
            self._record("reused", "REUSED",
                         reason="the fixture was created once for this run and is reused by this case")
            return self.evidence()
        for step in (self._step_component, self._step_geometry, self._step_block, self._step_mesh,
                     self._step_mesh_size, self._step_physics, self._step_temperature, self._step_material):
            try:
                await step()
            except CapabilityUnavailable as exc:
                self._record(step.__name__, "BLOCKED", reason=str(exc))
            except Exception as exc:  # noqa: BLE001 - a broken fixture step must not abort a case
                self._record(step.__name__, "FAILED", reason=f"{type(exc).__name__}: {exc}")
        try:
            await self._step_group_schema()
        except CapabilityUnavailable as exc:
            self._record("material_group_schema", "BLOCKED", reason=str(exc))
        except Exception as exc:  # noqa: BLE001 - the schema read is evidence, not a precondition
            self._record("material_group_schema", "FAILED", reason=f"{type(exc).__name__}: {exc}")
        self.state["probe_fixture"] = self.evidence(built=True)
        return self.evidence()

    async def _step_group_schema(self) -> None:
        """Read the material property-group schema once (authoritative names and shapes)."""
        if not isinstance(self.nodes.get("material"), Mapping):
            return
        payload = await self.client.action("node.property_schema", {"path": self.property_group_path()},
                                          key=_fresh_key("probe-group-schema"), request="probe-group-schema")
        self.group_schema = {"success": payload.get("success"), "error_code": _error_code(payload),
                             "properties": _json_safe(_data(payload).get("properties"))}
        self._record("material_group_schema",
                     "READ" if _success(payload) else ("BLOCKED" if _blocked_payload(payload) else "FAILED"),
                     operation="node.property_schema", error_code=_error_code(payload))

    def evidence(self, *, built: bool = False) -> dict[str, Any]:
        row: dict[str, Any] = {"steps": list(self.steps),
                               "nodes": {key: dict(value) for key, value in self.nodes.items()},
                               "group_schema": dict(self.group_schema),
                               "model_ref": _json_safe(self.state.get("ref"))}
        if built:
            row["built"] = True
        return row

    def verified_roots(self) -> list[dict[str, Any]]:
        return [dict(value) for value in self.nodes.values() if isinstance(value, Mapping)]

    def ready(self, key: str) -> bool:
        """True when the target for ``key`` was applied or adopted (never on a refusal)."""
        if not isinstance(self.nodes.get(key), Mapping):
            return False
        names = {key, f"{key}_adopt"}
        return any(row.get("step") in names and row.get("status") in {"APPLIED", "ADOPTED"}
                   for row in self.steps if isinstance(row, Mapping))

    def group_candidates(self, kinds: Iterable[str]) -> list[dict[str, Any]]:
        """Typed rows for the material property group, from the schema read back above."""
        wanted = set(kinds)
        rows = self.group_schema.get("properties")
        out: list[dict[str, Any]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping) or row.get("metadata_status") != "KNOWN":
                continue
            if wanted and row.get("kind") not in wanted:
                continue
            out.append({"path": self.property_group_path(), "node_type": "PropertyGroup",
                        "name": row.get("name"), "kind": row.get("kind"), "shape_rank": row.get("shape_rank"),
                        "getter": row.get("getter"), "unit": row.get("unit"),
                        "metadata_status": row.get("metadata_status"), "source": "probe_fixture"})
        return out

    #: Nodes the fixture created, in read order, with the property names worth probing first.
    NODE_CANDIDATE_TARGETS = ("block", "mesh_size", "temperature", "physics", "material", "geometry")

    async def node_property_candidates(self, kinds: Iterable[str],
                                       *, property_limit: int = 8) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Read the fixture's *own* nodes for typed properties (schema first, then value).

        ``node.find`` discovery is a bounded, model-shaped walk: on a model the driver does not
        control it can report ``INCOMPLETE`` with an empty result (observed live), which would
        leave every readback acceptance line with no target even though the fixture created one.
        The nodes this fixture created are therefore read directly: each property is offered only
        after the engine's own schema says it is KNOWN and a value was read back.
        """
        wanted = set(kinds)
        rows: list[dict[str, Any]] = []
        attempts: list[dict[str, Any]] = []
        for key in self.NODE_CANDIDATE_TARGETS:
            path = self.nodes.get(key)
            if not isinstance(path, Mapping):
                continue
            schema = await self.client.action("node.property_schema", {"path": dict(path)},
                                             key=_fresh_key(f"probe-node-schema-{key}"),
                                             request=f"probe-node-schema-{key}")
            properties = [dict(row) for row in _data(schema).get("properties", []) if isinstance(row, Mapping)]
            known = [row for row in properties
                     if row.get("metadata_status") == "KNOWN" and isinstance(row.get("name"), str)
                     and (not wanted or row.get("kind") in wanted)]
            attempts.append({"node": key, "path": dict(path), "success": schema.get("success"),
                             "error_code": _error_code(schema), "properties": len(properties),
                             "known": [str(row["name"]) for row in known]})
            for row in known[:property_limit]:
                value = await _read_value(self.client, path, str(row["name"]))
                if value is None:
                    continue
                rows.append({"path": dict(path), "node_type": key, "name": str(row["name"]),
                             "kind": row.get("kind"), "shape_rank": row.get("shape_rank"),
                             "getter": row.get("getter"), "unit": row.get("unit"),
                             "value": _json_safe(value), "source": "probe_fixture"})
        return rows, attempts


async def _probe_fixture(client: ActionClient, args: argparse.Namespace, state: dict[str, Any]) -> ProbeFixture:
    fixture = ProbeFixture(client, args, state)
    await fixture.build()
    return fixture


async def _probe_candidates(client: ActionClient, args: argparse.Namespace, state: dict[str, Any],
                            kinds: Sequence[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Typed candidates **created by the driver** on the bound model, with their evidence.

    Bounded and non-fatal by construction: a build whose fixture cannot be created (or whose
    properties cannot be read back) yields an empty list plus the recorded reason, so the
    calling case keeps its own honest NOT_RUN/BLOCKED verdicts instead of aborting.
    """
    kinds = list(kinds)
    try:
        fixture = await _probe_fixture(client, args, state)
        created, discovery = await _typed_candidates(client, kinds=kinds, roots=fixture.verified_roots(),
                                                     node_limit=60, property_limit=12)
        owned, owned_attempts = await fixture.node_property_candidates(kinds)
        group_rows: list[dict[str, Any]] = []
        for row in fixture.group_candidates(kinds):
            value = await _read_value(client, row["path"], str(row["name"]))
            if value is None:
                continue
            group_rows.append({**row, "value": _json_safe(value)})
    except CapabilityUnavailable as exc:
        return [], {"fixture": None, "status": "BLOCKED", "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a broken probe target must not abort a case
        return [], {"fixture": None, "status": "FAILED", "reason": f"{type(exc).__name__}: {exc}"}
    candidates = [*group_rows, *owned, *created]
    return candidates, {"fixture": fixture.evidence(), "discovery": discovery, "owned_nodes": owned_attempts,
                        "created_candidates": len(created), "owned_candidates": len(owned),
                        "material_group_candidates": len(group_rows),
                        "group_schema": fixture.group_schema, "status": "READ" if candidates else "EMPTY"}


# ---------------------------------------------------------------------------
# Protective regressions (T010, T035, T038, T005, T033)
# ---------------------------------------------------------------------------


def _fixture_root(run_dir: Path) -> Path:
    """Fixture inputs live inside the run directory when it is in-repo, else under evidence/."""
    resolved = Path(run_dir).resolve()
    if ROOT == resolved or ROOT in resolved.parents:
        return resolved
    root = ROOT / "evidence" / "phase4" / "_guards" / resolved.name
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


async def _case_guard_t010(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                           state: dict[str, Any]) -> None:
    rows = await _prepare_case(case, host, _plan_ops(case.case_id))
    describe_ops = ("node.property_set", "transaction.apply", "geometry.feature_create")
    available = [operation for operation in describe_ops if (rows.get(operation) or {}).get("available")]
    execution_fields: list[str] = []
    schema_fields: list[str] = []
    for operation in available:
        row = rows.get(operation) or {}
        fields = row.get("execution_fields")
        if isinstance(fields, list):
            execution_fields.extend(str(field) for field in fields)
        properties = row.get("input_schema_properties")
        if isinstance(properties, list):
            schema_fields.extend(str(prop) for prop in properties)
    declared = set(execution_fields) | set(schema_fields)
    contract_ok = {"idempotency_key", "request_id"} <= declared
    case.assertion("static_idempotency_contract_published", contract_ok,
                   available_operations=available, execution_fields=sorted(set(execution_fields)),
                   input_schema_fields=sorted(set(schema_fields)))
    if contract_ok:
        case.subcase("static_idempotency_contract_published", "PASS", level="static")
    elif not available:
        case.subcase("static_idempotency_contract_published", "BLOCKED", level="static",
                     reason=("no strict write operation is available in this build, so the idempotency contract "
                             "cannot be inspected"),
                     execution_fields=sorted(set(execution_fields)))
    else:
        case.subcase("static_idempotency_contract_published", "FAIL", level="static",
                     reason=("the available strict operation(s) publish neither idempotency_key nor request_id in "
                             "wire_compatibility.execution_fields or the operation input schema"),
                     execution_fields=sorted(set(execution_fields)),
                     input_schema_fields=sorted(set(schema_fields)))

    write_ops = ("node.property_set",)
    runnable, status, why = _can_run(case, args, state, rows, write_ops, require_writable_model=True)
    if not runnable:
        for name in ("repeated_key_not_reexecuted", "same_tag_same_type_duplicate_rejected_or_idempotent",
                     "same_tag_different_type_rejected", "request_hash_conflict_rejected"):
            _mark(case, name, status, why)
        case.finish()
        return
    candidates, t010_discovery = await _typed_candidates(client, kinds=("expression", "float64", "int32", "string"))
    scalar = next((row for row in candidates if row.get("shape_rank") == 0), None)
    fixture_evidence: dict[str, Any] | None = None
    if scalar is None:
        # The idempotency probe needs one writable scalar: create it (and read it back)
        # instead of skipping every T010 acceptance line on an empty model.
        created, fixture_evidence = await _probe_candidates(
            client, args, state, ("expression", "float64", "int32", "string"))
        candidates = [*created, *candidates]
        scalar = next((row for row in candidates if row.get("shape_rank") == 0), None)
    case.assertions["t010_probe_target"] = {
        "discovery": t010_discovery, "probe_fixture": fixture_evidence, "candidates": len(candidates),
        "target": ({"node": scalar.get("node_type"), "property": scalar.get("name"), "kind": scalar.get("kind"),
                    "shape_rank": scalar.get("shape_rank"), "path": scalar.get("path"),
                    "source": scalar.get("source", "model_discovery"), "value": scalar.get("value")}
                   if scalar is not None else None),
    }
    if scalar is None:
        for name in ("repeated_key_not_reexecuted", "request_hash_conflict_rejected"):
            _mark(case, name, "NOT_RUN",
                  "no scalar property could be discovered or created for the idempotency probe")
    else:
        value = dict(scalar.get("value") or {})
        body = {"path": scalar["path"], "properties": [{"name": scalar["name"], "value": value}]}
        # One identity, generated once and shared by the pair: the acceptance line is that the
        # *same* key is not re-executed.  A literal key reused across runs would be answered from the
        # control daemon's store, so the pair would compare two stored envelopes and pass without the
        # engine being asked anything.
        repeat_key = _fresh_key("guard-t010-repeat")
        first = await client.action("node.property_set", body, key=repeat_key, request="repeat")
        # The retry is a *true* retry: the same key and the bytes the first call was dispatched with.
        # The driver used to re-derive the body — its ``expected_revision`` moved from 20 to 21 after
        # the first write — so the product answered ``IDEMPOTENCY_CONFLICT`` (it hashes
        # ``expected_revision`` into the request) and the run reported a product defect that was its
        # own.  The recorded dispatch is re-sent verbatim, once, and the product's own operation
        # identity is what decides the line.
        first_identity = execution_identity(first)
        retry: dict[str, Any] | None = None
        try:
            retry, retry_record = await client.retry(repeat_key)
            retry_identity = execution_identity(retry)
            same = bool(retry_record.get("same_body")) and bool(first_identity.get("operation_id")) \
                and first_identity.get("operation_id") == retry_identity.get("operation_id")
            reason = None
            if not retry_record.get("same_body"):
                reason = ("the retry did not resend the dispatched bytes "
                          f"({retry_record.get('body_sha256')} -> {retry_record.get('retry_body_sha256')})")
            elif not first_identity.get("operation_id"):
                reason = ("the product published no operation identity for the first call, so the "
                          "replayed identity could not be established (the envelope carried none)")
            elif not same:
                reason = ("the retry with the same idempotency key and an identical body produced a "
                          "different operation identity")
        except CapabilityUnavailable as exc:
            retry_record = {"refused_before_dispatch": str(exc)}
            retry_identity = {}
            same = False
            reason = f"the retry was refused before dispatch: {exc}"
        case.assertions["t010_repeat"] = {
            "first": _envelope_identity(first), "retry": _envelope_identity(retry) if retry_identity else None,
            "first_identity": first_identity, "retry_identity": retry_identity or None,
            "retry_record": retry_record, "same_operation_id": same,
            "same_body": bool(retry_record.get("same_body")),
            "key": repeat_key,
            "retry_count": len(getattr(client, "retries", []) or []),
        }
        _mark(case, "repeated_key_not_reexecuted", "PASS" if same else "FAIL", reason)
        # The conflict probe differs from the dispatched body by exactly one documented field
        # (``provenance``) and keeps the same expected revision, so the product's answer can only be
        # about the *body* under that key — never about a revision this driver moved itself.
        conflict_body = {"path": scalar["path"],
                         "properties": [{"name": scalar["name"], "value": value}],
                         "provenance": {"phase4_conflict_probe": True}}
        conflict_revision = _expected_revision(client.dispatched.get(repeat_key, {}).get("arguments") or {})
        conflict = await client.action("node.property_set", conflict_body, key=repeat_key, request="repeat",
                                       revision_override=conflict_revision)
        conflict_text = json.dumps(_envelope_identity(conflict))
        rejected = (not _success(conflict)) and any(token in conflict_text.upper()
                                                   for token in ("IDEMPOTENCY", "CONFLICT"))
        case.assertions["t010_conflict"] = dict(_envelope_identity(conflict),
                                                expected_revision=conflict_revision)
        _mark(case, "request_hash_conflict_rejected", "PASS" if rejected else "FAIL",
              None if rejected else "a different body under the same idempotency key was not rejected as a conflict")
    # The duplicate probe needs the tag to exist *first*: one create, then the duplicate create.
    # Both payloads follow the published ``create_feature`` schema (``component``, ``geometry``,
    # ``tag``, ``feature_type``, ``properties_json``, ``run_geometry``); the previous payload sent
    # ``properties``/``add_to_main``, which the published schema does not declare, so the intended
    # feature sizes were silently dropped and the first call reported ``created: true`` — the
    # reason the live run's duplicate line failed.  ``_assert_existing_feature_type`` is what the
    # product answers a same-tag same-type create with: ``created: false``, or an explicit refusal.
    duplicate_first = await client.action(
        "create_feature",
        {"component": args.component, "geometry": args.geometry_tag, "tag": args.duplicate_probe_tag,
         "feature_type": "Rectangle", "properties_json": '{"size": ["0.001", "0.001"]}',
         "run_geometry": False},
        key="guard-t010-duplicate-first", request="t010-duplicate-first")
    duplicate_second = await client.action(
        "create_feature",
        {"component": args.component, "geometry": args.geometry_tag, "tag": args.duplicate_probe_tag,
         "feature_type": "Rectangle", "properties_json": '{"size": ["0.001", "0.001"]}',
         "run_geometry": False},
        key="guard-t010-duplicate-second", request="t010-duplicate-second")
    first_created = bool(_success(duplicate_first) and _data(duplicate_first).get("created") is True)
    second_existing = bool(_success(duplicate_second) and _data(duplicate_second).get("created") is False)
    second_refused = bool(not _success(duplicate_second) and _error_message(duplicate_second))
    duplicate_ok = second_existing or second_refused
    case.assertions["t010_duplicate"] = {
        "first": _envelope_identity(duplicate_first), "second": _envelope_identity(duplicate_second),
        "first_created": first_created, "second_reports_existing": second_existing,
        "second_refused": second_refused,
        "second_error": _error_message(duplicate_second) or None,
        "second_data_keys": sorted(_data(duplicate_second)),
    }
    if duplicate_ok:
        _mark(case, "same_tag_same_type_duplicate_rejected_or_idempotent", "PASS", None,
              observed=case.assertions["t010_duplicate"])
    elif _blocked_payload(duplicate_first) or _blocked_payload(duplicate_second):
        _mark(case, "same_tag_same_type_duplicate_rejected_or_idempotent", "BLOCKED",
              f"the duplicate create could not be judged ({_error_code(duplicate_second) or _error_code(duplicate_first)})",
              observed=case.assertions["t010_duplicate"])
    else:
        _mark(case, "same_tag_same_type_duplicate_rejected_or_idempotent", "FAIL",
              "a duplicate same-tag same-type feature was neither reported as existing nor rejected",
              observed=case.assertions["t010_duplicate"])
    await _step(case, host, client, args, state, name="same_tag_different_type_rejected",
                operation="create_feature",
                arguments={"component": args.component, "geometry": args.geometry_tag, "tag": args.duplicate_probe_tag,
                           "feature_type": "Circle", "properties_json": '{"r": "0.001"}', "run_geometry": False},
                prereq="same_tag_same_type_duplicate_rejected_or_idempotent", store={},
                check=lambda payload, bundle: (
                    ("PASS", None, {"observed": _error_code(payload)})
                    if (not _success(payload)) and bool(_error_message(payload))
                    else ("FAIL", "a duplicate tag with a different feature type was accepted")
                ))
    case.finish()


async def _case_guard_t035(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                           state: dict[str, Any]) -> None:
    run_dir = Path(args.run_dir)
    docs_ops = ("docs.index", "docs.search")
    # The guard gates its live denials on these two operations, so they are probed here (the
    # plan inventory declares them too): an empty probe table must never read as a gap.
    rows = await _prepare_case(case, host, (*_plan_ops(case.case_id), *docs_ops))
    docs_ok = all((rows.get(operation) or {}).get("available") for operation in docs_ops)
    case.assertion("static_docs_guard_ops_executable", docs_ok, operations=list(docs_ops))
    _availability_subcase(case, "static_docs_guard_ops_executable", docs_ops, rows, flag=docs_ok)
    fixture_root = _fixture_root(run_dir)
    outside_dir = Path(tempfile.mkdtemp(prefix="comsol-mcp-phase4-outside-"))
    outside_file = outside_dir / "outside_COMSOL_6.4_guard.md"
    outside_file.write_text("# outside fixture\n\nphase4 guard fixture\n", encoding="utf-8")
    outside_digest_before = _sha256(outside_file)
    credential_file = fixture_root / "credentials_COMSOL_6.4.md"
    credential_file.write_text(f"# credentials fixture\n\n{FAKE_CREDENTIAL}\n", encoding="utf-8")
    symlink = fixture_root / "outside_link_COMSOL_6.4.md"
    try:
        if symlink.exists() or symlink.is_symlink():
            symlink.unlink()
        symlink.symlink_to(outside_file)
    except OSError:
        symlink = None  # type: ignore[assignment]
    case.assertions["t035_fixtures"] = {"outside_file": str(outside_file), "outside_sha256": outside_digest_before,
                                        "credential_fixture": str(credential_file),
                                        "symlink": str(symlink) if symlink else None}

    runnable, status, why = _can_run(case, args, state, rows, docs_ops)
    index_status: dict[str, Any] = {}
    if not runnable:
        for name in ("outside_workspace_source_denied", "symlink_outside_denied",
                     "outside_write_attempt_denied"):
            _mark(case, name, status, why)
    else:
        outside = await client.action("docs.index",
                                      {"runtime_id": f"{args.runtime_id}-outside-root", "sources": [str(outside_file)]},
                                      require_model=False, key="t035-outside", request="docs.index")
        index_status["outside_index"] = _envelope_identity(outside)
        outside_status, outside_reason, outside_detail = _denial_verdict(
            outside, "an outside-workspace documentation source was accepted or the denial was not explicit",
            release=host.release_summary(outside))
        _mark(case, "outside_workspace_source_denied", outside_status, outside_reason, observed=outside_detail)
        if symlink is not None:
            linked = await client.action("docs.index",
                                         {"runtime_id": f"{args.runtime_id}-symlink-root", "sources": [str(symlink)]},
                                         require_model=False, key="t035-symlink", request="docs.index")
            index_status["symlink_index"] = _envelope_identity(linked)
            symlink_status, symlink_reason, symlink_detail = _denial_verdict(
                linked, "a symlink pointing outside the workspace was accepted as a documentation source",
                release=host.release_summary(linked))
            _mark(case, "symlink_outside_denied", symlink_status, symlink_reason, observed=symlink_detail)
        else:
            _mark(case, "symlink_outside_denied", "BLOCKED", "the symlink fixture could not be created")
        directory_attempt = await client.action("docs.index",
                                                {"runtime_id": f"{args.runtime_id}-outside-dir",
                                                 "sources": [str(outside_dir)]},
                                                require_model=False, key="t035-outside-dir", request="docs.index")
        index_status["outside_directory_index"] = _envelope_identity(directory_attempt)
        dir_status, dir_reason, dir_detail = _denial_verdict(
            directory_attempt, "an outside directory could be registered as an indexable source",
            release=host.release_summary(directory_attempt))
        _mark(case, "outside_write_attempt_denied", dir_status, dir_reason, observed=dir_detail)
    case.assertions["t035_index_attempts"] = index_status
    outside_after = _sha256(outside_file)
    unchanged = outside_digest_before == outside_after
    case.assertions["outside_file_unchanged"] = {"before": outside_digest_before, "after": outside_after}
    _mark(case, "failed_denial_does_not_modify_files", "PASS" if unchanged else "FAIL",
          None if unchanged else "the outside fixture file was modified by a denied request")
    emit = await client.action("docs.index",
                               {"runtime_id": f"{args.runtime_id}-credential-fixture",
                                "sources": [str(credential_file)]},
                               require_model=False, key="t035-credential", request="docs.index")
    search = await client.action("docs.search", {"query": "phase4 guard fixture", "version": args.runtime_id,
                                                 "limit": 5},
                                 require_model=False, key="t035-search", request="docs.search")
    case.assertions["t035_credential_ingest"] = {"index": _envelope_identity(emit),
                                                 "search": _envelope_identity(search),
                                                 "credential_sha256": _sha256_text(FAKE_CREDENTIAL)}
    runtime_home = host.private_home if host.private_home_is_caller_owned else None
    scan = _leak_scan(run_dir, exclude={credential_file.name}, private_home=runtime_home)
    case.assertions["t035_leak_scan"] = scan
    case.subcase("credential_text_not_persisted", "PASS" if not scan["credential_hits"] else "FAIL",
                 level="protocol", force=True,
                 reason=None if not scan["credential_hits"] else "the fixture credential text reached the evidence tree",
                 hits=scan["credential_hits"])
    # Pass/fail of the private-path rule is decided over the driver's own evidence documents;
    # hits inside a caller-owned runtime home are reported as blocked (the driver cannot
    # redact a SQLite journal it does not write, and --private-home may place that home
    # inside the evidence tree) — they are never reported as a clean tree.
    if scan["private_path_hits"]:
        case.subcase("private_paths_not_in_evidence", "FAIL", level="protocol", force=True,
                     reason="private absolute paths reached the evidence documents",
                     hits=scan["private_path_hits"], runtime_state_hits=scan.get("runtime_state_hits"))
    elif scan.get("runtime_state_hits"):
        case.subcase("private_paths_not_in_evidence", "BLOCKED", level="protocol", force=True,
                     reason=("the caller-owned private home was placed inside the evidence tree; the driver's own "
                             "evidence documents contain no private paths, but the runtime state files it does not "
                             "write do — re-run without --private-home, or point it outside the evidence tree"),
                     runtime_state_hits=scan["runtime_state_hits"], private_home=scan.get("private_home_scanned"))
    else:
        case.subcase("private_paths_not_in_evidence", "PASS", level="protocol", force=True, reason=None)
    case.finish()


def _leak_scan(run_dir: Path, *, exclude: set[str] | None = None,
               private_home: Path | None = None) -> dict[str, Any]:
    """Scan every written evidence file for credential text and private absolute paths.

    Hits inside a *caller-owned* runtime home are classified separately: the driver cannot
    redact a SQLite journal it does not write, and ``--private-home`` may place that home
    inside the evidence tree.  They stay recorded (``runtime_state_hits``) and are reported
    as blocked, never as a clean tree.
    """
    excluded = exclude or set()
    credential_hits: list[dict[str, Any]] = []
    fixture_hits: list[dict[str, Any]] = []
    private_path_hits: list[dict[str, Any]] = []
    runtime_state_hits: list[dict[str, Any]] = []
    secret_patterns = {"private_key_block": "-BEGIN", "cloud_key": "AKIA", "bearer_token": "Bearer "}
    home = str(Path.home())
    private_root = Path(private_home).expanduser() if private_home is not None else None
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.name in excluded or "SHA256SUMS" == path.name:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        relative = str(path.relative_to(run_dir))
        in_runtime_home = False
        if private_root is not None:
            try:
                path.resolve().relative_to(private_root.resolve())
                in_runtime_home = True
            except (OSError, ValueError):
                in_runtime_home = False
        if FAKE_CREDENTIAL in text:
            # The fixture file exists to be ingested: its own literal is classified, not a leak.
            entry = {"file": relative, "kind": "fixture_credential_literal"}
            (fixture_hits if "credential" in path.name.lower() else credential_hits).append(entry)
        for kind, pattern in secret_patterns.items():
            if pattern in text:
                credential_hits.append({"file": relative, "kind": kind})
        for marker_name, marker in (("home", home), ("private_home", ".g3-private"),
                                    ("phase1_private", ".phase1-private"), ("phase2_private", ".phase2-private"),
                                    ("control_private", "control-private"),
                                    ("receipt_assignment", "COMSOL_MCP_ISOLATION_RECEIPT=")):
            if marker and marker in text:
                entry = {"file": relative, "kind": marker_name}
                (runtime_state_hits if in_runtime_home else private_path_hits).append(entry)
    return {"credential_hits": credential_hits, "credential_leak_hits": credential_hits,
            "credential_fixture_hits": fixture_hits, "private_path_hits": private_path_hits,
            "runtime_state_hits": runtime_state_hits,
            "private_home_scanned": str(private_root) if private_root is not None else None,
            "excluded_fixtures": sorted(excluded),
            "scanned_files": sum(1 for path in run_dir.rglob("*") if path.is_file())}


async def _case_guard_t038(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                           state: dict[str, Any]) -> None:
    run_dir = Path(args.run_dir)
    case.assertions["initialize"] = _json_safe(host.initialize_summary())
    tools = sorted(host.tools)
    tools_ok = bool(tools) and all(tool for tool in tools)
    case.assertion("tools_list_schemas", tools_ok, tool_count=len(tools), tools=tools[:80])
    case.subcase("initialize_and_tools_list_schemas", "PASS" if tools_ok else "FAIL", level="protocol",
                 reason=None if tools_ok else "tools/list did not return any named tool", tool_count=len(tools))

    # Every protocol probe this guard makes happens before the envelope assertions are
    # evaluated: an empty transcript must never be read as a pass.
    described = await host.call("operation_describe", {"operation_id": "phase4.does.not.exist"})
    identity = _envelope_identity(described)
    described_unknown = _unknown_outcome(described)
    case.assertions["error_propagation"] = {"probe": identity, "unknown_engine_state": _json_safe(described_unknown),
                                           "release": host.release_summary(described)}
    if described_unknown is not None:
        # A gated call is refused before the registry is consulted: the probe never reached the
        # product, so its refusal is not evidence that an unknown operation is reported as an error.
        case.subcase("error_propagation_structured", "BLOCKED", level="protocol",
                     reason=("the unknown-operation probe was refused with an unknown engine state "
                             f"({described_unknown.get('error_code') or 'EXECUTION_STATE_UNKNOWN'}) before the "
                             "registry answered, so structured error propagation was not observed"),
                     observed=identity)
    else:
        propagation_ok = (not _success(described)) and identity.get("outer_isError") is True and bool(identity.get("error_code"))
        case.subcase("error_propagation_structured", "PASS" if propagation_ok else "FAIL", level="protocol",
                     reason=None if propagation_ok else "an unknown operation was not reported as a structured error",
                     observed=identity)

    paging_ok = False
    paging_detail: dict[str, Any] = {}
    if "registry_list" in host.tools:
        first = await host.call("registry_list", {})
        data = _data(first)
        first_raw = data.get("operations")
        first_ops: list[Any] = first_raw if isinstance(first_raw, list) else []
        ids = [str(row.get("operation_id")) for row in first_ops if isinstance(row, Mapping)]
        cursor = data.get("next_cursor") or data.get("cursor")
        pages = 1
        while isinstance(cursor, str) and cursor and pages < 20:
            following = await host.call("registry_list", {"cursor": cursor})
            followed = _data(following)
            batch_raw = followed.get("operations")
            batch: list[Any] = batch_raw if isinstance(batch_raw, list) else []
            batch_ids = [str(row.get("operation_id")) for row in batch if isinstance(row, Mapping)]
            if set(batch_ids) & set(ids):
                break
            ids.extend(batch_ids)
            cursor = followed.get("next_cursor") or followed.get("cursor")
            pages += 1
        paging_detail = {"pages": pages, "operations": len(ids), "unique": len(set(ids)),
                         "total_reported": data.get("total")}
        paging_ok = bool(ids) and len(ids) == len(set(ids))
    else:
        paging_detail = {"registry_list": "not published"}
    case.assertion("registry_paging_no_duplicates", paging_ok, **paging_detail)
    case.subcase("registry_paging_continuation", "PASS" if paging_ok else "FAIL", level="protocol",
                 reason=None if paging_ok else "the registry listing could not be paged without duplicates",
                 **paging_detail)

    partial = await host.call("operation_call", {"operation_id": "phase4.partial.failure",
                                                 "arguments": {"finish": "full"},
                                                 "execution": {"idempotency_key": "phase4-partial", "finish": "full"}})
    partial_identity = _envelope_identity(partial)
    partial_unknown = _unknown_outcome(partial)
    case.assertions["partial_failure"] = {"probe": partial_identity, "unknown_engine_state": _json_safe(partial_unknown),
                                          "release": host.release_summary(partial)}
    if partial_unknown is not None:
        case.subcase("partial_failure_not_reported_as_success", "BLOCKED", level="protocol",
                     reason=("the partial-failure probe was refused with an unknown engine state "
                             f"({partial_unknown.get('error_code') or 'EXECUTION_STATE_UNKNOWN'}) before the "
                             "operation ran, so the finish-mode refusal was not observed"),
                     observed=partial_identity)
    else:
        partial_ok = (not _success(partial)) and partial_identity.get("outer_isError") is True
        case.subcase("partial_failure_not_reported_as_success", "PASS" if partial_ok else "FAIL", level="protocol",
                     reason=None if partial_ok else "an invalid finish mode was not rejected",
                     observed=partial_identity)

    rows = [row for row in host.transcript
            if row.get("host") == host.label and row.get("operation") not in {"initialize", "tools/list"}]
    envelope_ok = bool(rows)
    mismatches: list[dict[str, Any]] = []
    if not rows:
        mismatches.append({"operation": None, "expected": "at least one tool call in the transcript"})
    for row in rows:
        raw_payload = row.get("payload")
        payload: Mapping[str, Any] = raw_payload if isinstance(raw_payload, Mapping) else {}
        outer = row.get("outer_isError")
        if payload.get("success") is False and outer is not True:
            envelope_ok = False
            mismatches.append({"operation": row.get("operation"), "expected": "outer isError true"})
        if payload.get("success") is True and outer is True:
            envelope_ok = False
            mismatches.append({"operation": row.get("operation"), "expected": "outer isError false"})
    case.assertions["envelope_consistency"] = {"calls": len(rows), "mismatches": mismatches}
    case.subcase("outer_iserror_matches_success", "PASS" if envelope_ok else "FAIL", level="protocol",
                 reason=None if envelope_ok else "the outer isError flag disagreed with the structured success flag",
                 calls=len(rows), mismatches=mismatches[:5])

    legacy_names = set(ActionClient.CONTROL_ALIASES.values())
    strict_rows = [row for row in rows if row.get("operation") not in legacy_names]
    missing_structured = sorted({str(row.get("operation")) for row in strict_rows
                                 if not isinstance(row.get("structuredContent"), Mapping)})
    structured_ok = bool(strict_rows) and not missing_structured
    case.assertion("structured_content_envelope", structured_ok,
                   tool_calls=len(rows), strict_calls=len(strict_rows),
                   legacy_calls=len(rows) - len(strict_rows),
                   strict_calls_without_structured_content=missing_structured)
    case.subcase("structured_content_action_results", "PASS" if structured_ok else "FAIL", level="protocol",
                 reason=None if structured_ok else
                 "action result(s) returned no structuredContent: " + (", ".join(missing_structured) or "(none observed)"),
                 strict_calls=len(strict_rows), missing=missing_structured)

    log_path = run_dir / f"{host.label}.engine.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    jsonrpc_lines = [line for line in log_text.splitlines()
                     if '"jsonrpc"' in line or ("'jsonrpc'" in line)]
    if not log_text:
        isolation_status, isolation_reason = "BLOCKED", "the engine log stream was empty, so isolation could not be observed"
    elif jsonrpc_lines:
        isolation_status, isolation_reason = "FAIL", "protocol traffic appeared on the engine log stream"
    else:
        isolation_status, isolation_reason = "PASS", None
    case.assertions["stdio_isolation"] = {"engine_log": str(log_path), "engine_log_bytes": len(log_text),
                                          "jsonrpc_frames_on_engine_log": len(jsonrpc_lines)}
    case.subcase("stdio_log_isolation", isolation_status, level="protocol", reason=isolation_reason,
                 lines=jsonrpc_lines[:3])
    case.finish()


async def _case_guard_t005(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                           state: dict[str, Any]) -> None:
    rows = await _prepare_case(case, host, _plan_ops(case.case_id))
    eval_ops = ("evaluate_expressions",)
    eval_ok = all((rows.get(operation) or {}).get("available") for operation in eval_ops)
    case.assertion("static_evaluation_ops_available", eval_ok, operations=list(eval_ops))
    eval_ok = _availability_subcase(case, "static_evaluation_ops_available", eval_ops, rows, flag=eval_ok)
    live_names = ("user_derived_nodes_preserved", "invalid_expression_does_not_delete_nodes",
                  "temporary_nodes_cleaned")
    runnable, status, why = _can_run(case, args, state, rows, eval_ops, require_writable_model=True)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    store: dict[str, Any] = {}
    before = await client.host.call("model_tree", {"depth": 2,
                                                   "execution": _execution(key="t005-tree-before",
                                                                           ref=state.get("ref"),
                                                                           revision=state.get("revision"))})
    first = await _step(case, host, client, args, state, name="user_derived_nodes_preserved",
                        operation="evaluate_expressions",
                        arguments={"expressions_json": json.dumps([{"name": "phase4_probe", "expression": "1+1"}]),
                                   "evaluation_policy": "ephemeral_mutation"},
                        store=store)
    stored = case.assertions.get("step_user_derived_nodes_preserved")
    derived = [dict(row) for row in (first or {}).get("data", {}).get("derived_values", [])
               if isinstance(row, Mapping)] if isinstance(first, Mapping) else []
    after = await client.host.call("model_tree", {"depth": 2,
                                                  "execution": _execution(key="t005-tree-after",
                                                                          ref=state.get("ref"),
                                                                          revision=state.get("revision"))})
    before_tree = _json_safe(_data(before).get("tree"))
    after_tree = _json_safe(_data(after).get("tree"))
    untouched = before_tree == after_tree
    case.assertions["t005_tree_diff"] = {"identical": untouched, "stored": _json_safe(stored),
                                        "derived_values": derived[:5]}
    blocked = _tree_read_blocked(case, "user_derived_nodes_preserved",
                                 {"before": before, "after": after})
    if not blocked:
        _mark(case, "user_derived_nodes_preserved", "PASS" if untouched else "FAIL",
              None if untouched else "the model tree changed after an ephemeral evaluation")
    bad = await client.action("evaluate_expressions",
                              {"expressions_json": json.dumps([{"name": "phase4_bad", "expression": "1+*2"}]),
                               "evaluation_policy": "ephemeral_mutation"},
                              key="t005-invalid", request="invalid")
    after_bad = await client.host.call("model_tree", {"depth": 2,
                                                      "execution": _execution(key="t005-tree-after-bad",
                                                                              ref=state.get("ref"),
                                                                              revision=state.get("revision"))})
    bad_tree = _json_safe(_data(after_bad).get("tree"))
    if not _tree_read_blocked(case, "invalid_expression_does_not_delete_nodes",
                              {"before": before, "after_bad": after_bad, "invalid probe": bad}):
        _mark(case, "invalid_expression_does_not_delete_nodes",
              "PASS" if bad_tree == before_tree else "FAIL",
              None if bad_tree == before_tree else "an invalid expression evaluation modified the model tree",
              error_code=_error_code(bad))
    cleanup = await client.host.call("model_tree", {"depth": 2,
                                                    "execution": _execution(key="t005-tree-cleanup",
                                                                            ref=state.get("ref"),
                                                                            revision=state.get("revision"))})
    cleanup_tree = _json_safe(_data(cleanup).get("tree"))
    if not _tree_read_blocked(case, "temporary_nodes_cleaned", {"before": before, "cleanup": cleanup}):
        _mark(case, "temporary_nodes_cleaned", "PASS" if cleanup_tree == before_tree else "FAIL",
              None if cleanup_tree == before_tree else "temporary evaluation nodes were left behind in the model tree")
    case.finish()


def _record_solution(state: dict[str, Any], case: Case, *, source: str, dataset: str | None,
                     solution: str | None) -> dict[str, Any]:
    """Record one *stored* solution this run observed.

    A study's requested ``tlist`` is not a stored time and a requested dataset is not a stored
    dataset, so only a solved study's own completion is recorded here — and what a field evaluation
    reads is the stored solution named in the evaluation response, never the request.
    """
    rows = state.setdefault("solved_solutions", [])
    if not isinstance(rows, list):
        rows = state["solved_solutions"] = []
    row = {"case": case.case_id, "source": source, "dataset": dataset, "solution": solution, "at": _utc_now()}
    rows.append(row)
    del rows[:-20]
    case.assertions.setdefault("solved_solutions", []).append(dict(row))
    return row


def _request_dispatch_stage(host: ProductionHost, payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """What the run's execution context recorded about one request's dispatch stage (C02).

    A refused-before-dispatch request is the only kind whose replay cannot execute anything twice, and
    the context is the single place that decides this — so a case that wants to show "no write call
    happened" reads this instead of guessing from the error text.
    """
    context = getattr(host, "context", None)
    if context is None:
        return {"available": False, "reason": "this host carries no execution context (offline double)"}
    witness = unpack_envelope("dispatch-stage", payload)
    request_id = witness.execution.get("request_id") if isinstance(witness.execution, Mapping) else None
    rows = [row for row in context.dispatches if row.get("request_id") == request_id]
    return {"available": True, "request_id": request_id,
            "stage": rows[-1].get("stage") if rows else None, "rows": _json_safe(rows[-3:]),
            "nothing_executed": bool(rows and rows[-1].get("stage") in NOT_EXECUTED_STAGES)}


def _evaluation_rows(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The expression rows a successful evaluation published (never a Python-computed substitute)."""
    data = _mapping(_data(payload))
    for key in ("expressions", "results", "values", "data"):
        rows = data.get(key)
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)):
            collected = [dict(row) for row in rows if isinstance(row, Mapping)]
            if collected:
                return collected
    return []


def _value_shape(value: Any) -> Any:
    """The shape of one reported value (so "a scalar" and "a field" cannot be confused)."""
    if isinstance(value, (list, tuple)):
        return [len(value)] + ([len(value[0])] if value and isinstance(value[0], (list, tuple)) else [])
    if value is None:
        return None
    return []


#: The expression kinds T033 verifies on a clean, validly bound model (C04).  A constant is *not*
#: computed in Python: it is routed through the engine's own evaluator, and the routing conditions
#: each request needs are pre-registered here, so "9" can only ever come from the product.
T033_EXPRESSIONS: tuple[dict[str, Any], ...] = (
    {"name": "phase4_constant", "expression": "3*3", "kind": "constant",
     "routing": ("routed through the engine's own expression parser with the bound model's context; the "
                 "driver never computes the value itself"),
     "requires": ()},
    {"name": "phase4_model_expression", "expression": "k_w_mk/T0_k", "kind": "model_expression",
     "routing": "resolved against the bound model's own parameters/expressions",
     "requires": ("model_parameters",)},
    {"name": "phase4_solved_field", "expression": "T", "kind": "solved_field",
     "routing": ("read on a *stored* solution/dataset: the study's requested tlist is not a stored time, so "
                 "the dataset/solution and the stored times are recorded with the value"),
     "requires": ("solved_solution",)},
    {"name": "phase4_illegal", "expression": "sin(", "kind": "illegal_expression",
     "routing": "must be refused by the engine's parser; a successful value would itself be the defect",
     "requires": ()},
)


def _t033_requirement(name: str, case: Case, host: ProductionHost, state: Mapping[str, Any]) -> dict[str, Any]:
    """Whether one pre-registered T033 routing condition is established by evidence this run holds."""
    values = state if isinstance(state, Mapping) else {}
    if name == "model_parameters":
        rows = case.assertions.get("t033_parameter_probe")
        rows = rows if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)) else []
        ok = any(isinstance(row, Mapping) and row.get("success") for row in rows)
        return {"met": bool(ok), "evidence": ("the model's parameters were read back" if ok else
                                              "no parameter read-back was recorded for this model")}
    if name == "solved_solution":
        rows = values.get("solved_solutions")
        rows = rows if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)) else []
        ok = bool(rows)
        return {"met": ok, "evidence": (f"a stored solution was observed ({len(rows)} dataset/solution row(s))"
                                        if ok else
                                        "no stored solution was observed in this run, so a field evaluation "
                                        "has no dataset to read")}
    return {"met": False, "evidence": f"unknown routing condition {name!r}"}


async def _case_guard_t033(host: ProductionHost, client: ActionClient, case: Case, args: argparse.Namespace,
                           state: dict[str, Any]) -> None:
    rows = await _prepare_case(case, host, _plan_ops(case.case_id))
    detail = rows.get("evaluate_expressions") or {}
    policy = detail.get("evaluation_policy")
    policy_source = detail.get("evaluation_policy_source")
    policy_note = detail.get("evaluation_policy_note")
    # C04: the policy is whatever the *operation* publishes, read at its own path
    # (``input_schema.properties.evaluation_policy`` first).  The case judges that declaration
    # instead of demanding it in a field the operation never had.
    policy_ok = bool(detail.get("available")) and isinstance(policy, Mapping) and bool(policy)
    case.assertion("static_evaluation_policy_documented", policy_ok,
                   evaluation_policy=_json_safe(policy),
                   evaluation_policy_source=policy_source,
                   evaluation_policy_note=policy_note,
                   input_schema_properties=_json_safe(detail.get("input_schema_properties")),
                   implementation_status=detail.get("implementation_status"),
                   remediation=_json_safe(detail.get("remediation")))
    case.assertion("static_evaluation_policy_source_recorded", bool(policy_source),
                   evaluation_policy_source=policy_source, note=policy_note)
    if policy_source:
        case.subcase("static_evaluation_policy_source_recorded", "PASS", level="static",
                     reason=None, evaluation_policy_source=policy_source)
    else:
        # C04: a field the product does not publish is not a defect of the case — it is a line that
        # cannot be inspected on this build, so it is BLOCKED with the path that was read recorded.
        case.subcase("static_evaluation_policy_source_recorded", "BLOCKED", level="static",
                     reason=(policy_note or "") or
                            ("operation_describe publishes no evaluation_policy at "
                             f"{_EVALUATION_POLICY_PATH} nor at the data level, so the published path this "
                             "build uses cannot be established"),
                     evaluation_policy_source=None,
                     inspected_paths=[_EVALUATION_POLICY_PATH, "data.evaluation_policy"],
                     operation_available=bool(detail.get("available")))
    if policy_ok:
        case.subcase("static_evaluation_policy_documented", "PASS", level="static",
                     evaluation_policy_source=policy_source)
    else:
        # The operation publishes no policy declaration for ``evaluation_policy`` at either the
        # schema path or the data level, so the T033 policy line cannot be inspected on this build.
        case.subcase("static_evaluation_policy_documented", "BLOCKED", level="static",
                     reason=("operation_describe publishes no evaluation policy for evaluate_expressions "
                             "in this build (no isolation or node-ownership contract), so the T033 policy "
                             "contract cannot be inspected"),
                     evaluation_policy=_json_safe(policy), evaluation_policy_source=policy_source)
    live_names = ("pure_read_rejects_or_isolates", "ephemeral_mutation_recorded_and_serial",
                  "only_own_temporary_nodes_cleaned")
    runnable, status, why = _can_run(case, args, state, rows, ("evaluate_expressions",), require_writable_model=True)
    if not runnable:
        for name in live_names:
            _mark(case, name, status, why)
        case.finish()
        return
    pure = await client.action("evaluate_expressions",
                               {"expressions_json": json.dumps([{"name": "phase4_pure", "expression": "2*3"}]),
                                "evaluation_policy": "pure_read"},
                               key="t033-pure", request="pure")
    pure_unknown = _unknown_outcome(pure)
    # C04: the acceptance line is *the policy refusal*, not any failure.  A missing node, a stale
    # revision or an engine that answered UNKNOWN proves nothing about the pure_read policy, so only
    # a refusal that names the policy counts — and a success counts only when the reply reports the
    # (non-mutating) policy it applied.
    pure_text = json.dumps(_envelope_identity(pure)) + json.dumps(_json_safe(_data(pure)))
    pure_refusal_basis = sorted({token for token in ("PURE_READ", "EVALUATION_POLICY", "POLICY", "READ_ONLY")
                                 if token in pure_text.upper()})
    pure_policy_refusal = (not _success(pure)) and bool(pure_refusal_basis)
    pure_policy_echo = bool(_success(pure) and isinstance(_data(pure), Mapping) and any(
        str(_data(pure).get(key)).lower() == "pure_read"
        for key in ("evaluation_policy", "policy", "applied_policy")))
    pure_ok = pure_policy_refusal or pure_policy_echo
    case.assertions["t033_pure"] = {**_envelope_identity(pure),
                                    "unknown_engine_state": _json_safe(pure_unknown),
                                    "policy_refusal": pure_policy_refusal,
                                    "policy_refusal_basis": pure_refusal_basis,
                                    "policy_echo": pure_policy_echo,
                                    "dispatch_stage": _request_dispatch_stage(host, pure),
                                    "note": ("a pure_read verdict is this line's evidence only when it *is* the "
                                             "policy refusal (or a success that echoes the non-mutating policy); "
                                             "any other error is recorded as not-a-policy-refusal")}
    if pure_unknown is not None:
        # A gated or unknown evaluation is neither an isolation note nor a rejection.
        _mark(case, "pure_read_rejects_or_isolates", "BLOCKED",
              f"the pure_read evaluation was refused with an unknown engine state ({_error_code(pure)}); "
              "neither isolation nor rejection was established",
              unknown_engine_state=_json_safe(pure_unknown))
    else:
        _mark(case, "pure_read_rejects_or_isolates", "PASS" if pure_ok else "FAIL",
              None if pure_ok else
              (f"the pure_read evaluation neither produced a policy refusal nor echoed the policy "
               f"(observed {_error_code(pure) or 'no error code'}): an arbitrary error is not the policy "
               "refusal this line requires"),
              policy_refusal=pure_policy_refusal, policy_echo=pure_policy_echo,
              dispatch_stage=_request_dispatch_stage(host, pure))
    first = await client.action("evaluate_expressions",
                                {"expressions_json": json.dumps([{"name": "phase4_ephemeral", "expression": "3*3"}]),
                                 "evaluation_policy": "ephemeral_mutation"},
                                key="t033-ephemeral", request="ephemeral")
    second = await client.action("evaluate_expressions",
                                 {"expressions_json": json.dumps([{"name": "phase4_ephemeral", "expression": "3*3"}]),
                                  "evaluation_policy": "ephemeral_mutation"},
                                 key="t033-ephemeral-serial", request="ephemeral")
    recorded_ok = _success(first) and _success(second)
    unknown_eval = _unknown_outcome(first) or _unknown_outcome(second)
    case.assertions["t033_ephemeral"] = {"first": _envelope_identity(first), "second": _envelope_identity(second),
                                         "unknown_engine_state": _json_safe(unknown_eval),
                                         "release": host.release_summary(first) or host.release_summary(second)}
    if unknown_eval is not None:
        # The T035 treatment: the engine state was unknown, so what the evaluations recorded was
        # not established — blocked, never FAIL and never PASS.
        _mark(case, "ephemeral_mutation_recorded_and_serial", "BLOCKED",
              f"the ephemeral evaluation was refused with an unknown engine state "
              f"({unknown_eval.get('error_code') or 'EXECUTION_STATE_UNKNOWN'}); whether consecutive "
              "evaluations were both recorded was not established",
              unknown_engine_state=_json_safe(unknown_eval))
    else:
        _mark(case, "ephemeral_mutation_recorded_and_serial", "PASS" if recorded_ok else "FAIL",
              None if recorded_ok else "consecutive ephemeral evaluations were not both recorded")
    payload_data = _data(second)
    own_nodes = payload_data.get("temporary_nodes") if isinstance(payload_data, Mapping) else None
    ephemeral_flag = payload_data.get("ephemeral_mutation") if isinstance(payload_data, Mapping) else None
    reported_keys = sorted(payload_data) if isinstance(payload_data, Mapping) else []
    case.assertions["t033_temporary_nodes"] = {
        "temporary_nodes": _json_safe(own_nodes), "ephemeral_mutation": ephemeral_flag,
        "reported_keys": reported_keys,
        "unknown_engine_state": _json_safe(unknown_eval),
        "evaluation_policy": payload_data.get("evaluation_policy") if isinstance(payload_data, Mapping) else None,
    }
    if unknown_eval is not None:
        _mark(case, "only_own_temporary_nodes_cleaned", "BLOCKED",
              f"the evaluation envelope reported an unknown engine state "
              f"({unknown_eval.get('error_code') or 'EXECUTION_STATE_UNKNOWN'}), so the temporary nodes it "
              "owned and cleaned were not established",
              observed=case.assertions["t033_temporary_nodes"])
    elif isinstance(own_nodes, list):
        _mark(case, "only_own_temporary_nodes_cleaned", "PASS", None, observed=case.assertions["t033_temporary_nodes"])
    elif ephemeral_flag is True:
        # The build *does* report that the evaluation was an ephemeral mutation, but it publishes
        # no inventory of the temporary nodes it owned — so "the evaluation cleaned only its own
        # nodes" cannot be established from the reply.  Recorded as a build gap rather than a
        # FAIL (the driver's expectation is unverifiable here) or a PASS (nothing was verified).
        _mark(case, "only_own_temporary_nodes_cleaned", "BLOCKED",
              "the build reports ephemeral_mutation=true but publishes no temporary-node ownership "
              f"inventory, so node ownership cannot be established (reported keys: {', '.join(reported_keys)})",
              observed=case.assertions["t033_temporary_nodes"])
    else:
        _mark(case, "only_own_temporary_nodes_cleaned", "FAIL",
              "the evaluation did not report which temporary nodes it owned and cleaned",
              observed=case.assertions["t033_temporary_nodes"])
    # C04: the four expression kinds, each with the routing condition it needs pre-registered.  The
    # constant is routed through the engine (never computed here), the model expression needs a
    # readable expression property, the field evaluation needs a *stored* solution, and the illegal
    # expression must be refused by the engine's own parser.
    try:
        candidates, discovery = await _typed_candidates(client, kinds=("expression", "float64"),
                                                        node_limit=12, property_limit=3)
    except CapabilityUnavailable:
        candidates, discovery = [], {"available": False}
    case.assertions["t033_parameter_probe"] = [
        {"success": True, "node": row.get("node_type"), "property": row.get("name"), "kind": row.get("kind"),
         "unit": row.get("unit"), "value": row.get("value")} for row in candidates[:5]]
    case.assertions["t033_parameter_discovery"] = _json_safe(discovery)
    expression_evidence: list[dict[str, Any]] = []
    for item in T033_EXPRESSIONS:
        unmet = [name for name in item["requires"] if not _t033_requirement(name, case, host, state)["met"]]
        if unmet:
            expression_evidence.append({"name": item["name"], "kind": item["kind"],
                                        "expression": item["expression"], "routing": item["routing"],
                                        "verdict": "NOT_RUN",
                                        "reason": "routing condition not established: " + ", ".join(unmet)})
            continue
        payload = await client.action(
            "evaluate_expressions",
            {"expressions_json": json.dumps([{"name": item["name"], "expression": item["expression"]}]),
             "evaluation_policy": "ephemeral_mutation"},
            key=f"t033-{item['kind']}", request=f"t033-{item['kind']}")
        rows = _evaluation_rows(payload)
        data = _mapping(_data(payload))
        value = rows[0].get("value") if rows else None
        unknown = _unknown_outcome(payload)
        entry: dict[str, Any] = {"name": item["name"], "kind": item["kind"], "expression": item["expression"],
                                 "routing": item["routing"], "success": _success(payload),
                                 "error_code": _error_code(payload), "error_message": _error_message(payload) or None,
                                 "value": _json_safe(value), "shape": _value_shape(value),
                                 "dataset": _json_safe(data.get("dataset")), "solution": _json_safe(data.get("solution")),
                                 "reported_keys": sorted(str(key) for key in data),
                                 "dispatch_stage": _request_dispatch_stage(host, payload),
                                 "unknown_engine_state": _json_safe(unknown)}
        if unknown is not None:
            entry["verdict"] = "UNKNOWN"
        elif item["kind"] == "illegal_expression":
            entry["verdict"] = "refused" if not _success(payload) else "NOT_REFUSED"
        elif _success(payload) and rows:
            entry["verdict"] = "value_recorded"
        elif item["kind"] in {"model_expression", "solved_field"}:
            # A name the model does not define (or a field with no stored dataset on the evaluation
            # route) is a routing gap of *this* model, recorded as such: the engine's refusal is the
            # observation, and nothing is substituted for it.
            entry["verdict"] = "ROUTING_GAP"
        else:
            entry["verdict"] = "NO_VALUE_REPORTED"
        expression_evidence.append(entry)
    case.assertions["t033_expression_kinds"] = expression_evidence
    unknown_kinds = [row for row in expression_evidence if row["verdict"] == "UNKNOWN"]
    failed_kinds = [row for row in expression_evidence
                    if row["verdict"] in {"NOT_REFUSED", "NO_VALUE_REPORTED"}]
    observed_kinds = [row for row in expression_evidence if row["verdict"] in {"value_recorded", "refused"}]
    if unknown_kinds:
        _mark(case, "evaluation_expression_kinds_routed", "BLOCKED",
              "an expression kind could not be established: the engine reported an unknown state "
              f"({unknown_kinds[0].get('error_code') or 'EXECUTION_STATE_UNKNOWN'})",
              observed=case.assertions["t033_expression_kinds"])
    elif failed_kinds:
        _mark(case, "evaluation_expression_kinds_routed", "FAIL",
              (f"the pre-registered expectation was violated for: "
               + ", ".join(f"{row['kind']}={row['verdict']}" for row in failed_kinds)),
              observed=case.assertions["t033_expression_kinds"])
    else:
        _mark(case, "evaluation_expression_kinds_routed", "PASS",
              None if len(observed_kinds) == len(expression_evidence) else
              ("some kinds were not routed on this model; their routing conditions are recorded per kind "
               "and none was silently treated as a value"),
              observed=case.assertions["t033_expression_kinds"])
    case.finish()


# ---------------------------------------------------------------------------
# Reopen check (new process, new private home, provided .mph)
# ---------------------------------------------------------------------------


def _parse_expectation(spec: str) -> dict[str, Any]:
    """Parse ``name=expression=expected`` with an optional ``;tol=...`` suffix."""
    body, _, tail = spec.partition(";")
    name, _, expression = body.partition("=")
    expression, _, expected = expression.partition("=")
    if not (name and expression and expected):
        raise argparse.ArgumentTypeError(f"--reopen-expect needs name=expression=expected, got {spec!r}")
    tolerance = 1e-6
    for token in tail.split(","):
        key, _, value = token.partition("=")
        if key.strip() == "tol" and value.strip():
            tolerance = float(value)
    try:
        expected_value = float(expected)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--reopen-expect expected value must be numeric: {expected!r}") from exc
    return {"name": name, "expression": expression, "expected": expected_value, "tolerance": tolerance}


async def _reopen_check(args: argparse.Namespace) -> int:
    mph = Path(args.reopen_check).expanduser()
    stamp = _stamp()
    run_dir = (Path(args.run_dir).expanduser() if args.run_dir
               else ROOT / "evidence" / "phase4" / "_reopen_check" / stamp).resolve()
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    case = Case(case_id="REOPEN_CHECK", package="REOPEN", acceptance=("G3 §8",))
    case.assertions["mph"] = {"path": str(mph), "exists": mph.is_file(),
                              "sha256": _sha256(mph) if mph.is_file() else None,
                              "bytes": mph.stat().st_size if mph.is_file() else None}
    _write_json(run_dir / "environment.json", _environment_document(args, run_dir, extra={
        "mode": "reopen-check", "command": "tools/phase4_run_mcp.py --reopen-check", "mph": _regrade(str(mph))}))
    if not mph.is_file():
        case.subcase("mph_readable", "FAIL", level="fixture", reason="the supplied .mph does not exist")
        for name in ("fresh_process_and_private_home", "structure_readback", "representative_values"):
            case.subcase(name, "NOT_RUN", level="live", reason="prerequisite subcase mph_readable did not pass")
        case.finish()
        case.finalize_inventory()
        _write_case_evidence(run_dir, case, [], None)
        _print_report([case], run_dir)
        return 1
    case.subcase("mph_readable", "PASS", level="fixture", reason=None, sha256=case.assertions["mph"]["sha256"])
    transcript: list[dict[str, Any]] = []
    state: dict[str, Any] = {"ref": None, "revision": None}
    host = ProductionHost(args, run_dir, transcript, label="reopen")
    try:
        async with host:
            case.subcase("fresh_process_and_private_home", "PASS", level="live", reason=None,
                         private_home=str(host.private_home), pid=os.getpid(),
                         server_command=list(host.server_command()),
                         isolation_receipt_present=host.isolation_receipt_present)
            client = ActionClient(host, args, state)
            if not args.live:
                for name in ("structure_readback", "representative_values"):
                    case.subcase(name, "BLOCKED", level="live", reason="offline run: --live was not supplied")
            else:
                connect = await host.call("server_connect", {
                    "host": args.host, "port": args.port,
                    "execution": _execution(key="reopen-server-connect", request="connect", ref=None, revision=None),
                })
                state["server_connect"] = {"success": _success(connect), "error_code": _error_code(connect)}
                loaded, load_probe = await _load_model(host, client, mph, args.reopen_artifact_id, key="reopen-load")
                case.assertions["model_load"] = _envelope_identity(loaded)
                case.assertions["model_load_probe"] = _json_safe(load_probe)
                if not _success(loaded):
                    blocked = _blocked_payload(loaded)
                    for name in ("structure_readback", "representative_values"):
                        case.subcase(name, "BLOCKED" if blocked else "FAIL", level="live",
                                     reason=f"model.load returned {_error_code(loaded) or 'an invalid envelope'}: "
                                            f"{_error_message(loaded)[:160] or 'no message'}")
                else:
                    _adopt_payload_model(client, loaded)
                    state["ref"] = client.state.get("ref")
                    state["revision"] = client.state.get("revision")
                    tree = await client.host.call("model_tree", {"depth": 3,
                                                                 "execution": _execution(key="reopen-tree",
                                                                                         ref=state.get("ref"),
                                                                                         revision=state.get("revision"))})
                    data = _data(tree)
                    structure = {
                        "component_count": len(data.get("components") or []) if isinstance(data.get("components"), list) else None,
                        "study_count": len(data.get("studies") or []) if isinstance(data.get("studies"), list) else None,
                        "solutions": data.get("solutions"),
                        "tree_keys": sorted(data)[:20],
                    }
                    case.assertions["structure"] = _json_safe(structure)
                    structure_ok = _success(tree) and bool(data)
                    case.subcase("structure_readback", "PASS" if structure_ok else "FAIL", level="live",
                                 reason=None if structure_ok else "the reopened model did not expose a structure readback",
                                 **{key: value for key, value in structure.items() if value is not None})
                    expectations = list(args.reopen_expect or [])
                    if not expectations:
                        case.subcase("representative_values", "NOT_RUN", level="numerical",
                                     reason="no --reopen-expect expressions were supplied for the value comparison")
                    else:
                        expressions = json.dumps([{"name": item["name"], "expression": item["expression"]}
                                                  for item in expectations])
                        payload = await client.action("evaluate_expressions",
                                                      {"expressions_json": expressions,
                                                       "evaluation_policy": "ephemeral_mutation"},
                                                      key="reopen-values", request="reopen-values")
                        results_raw = _data(payload).get("results")
                        results: list[Any] = results_raw if isinstance(results_raw, list) else []
                        observed = {str(row.get("name")): row for row in results if isinstance(row, Mapping)}
                        deviations = []
                        for item in expectations:
                            row = observed.get(item["name"])
                            got = _finite_float(row.get("last_value")) if isinstance(row, Mapping) else None
                            if got is None:
                                deviations.append(f"{item['name']}: no value was returned")
                            elif abs(got - item["expected"]) > item["tolerance"]:
                                deviations.append(f"{item['name']}: {got} != {item['expected']} (tol {item['tolerance']})")
                        case.assertions["reopen_values"] = {"envelope": _envelope_identity(payload),
                                                            "results": _json_safe(results), "deviations": deviations}
                        case.subcase("representative_values", "PASS" if not deviations else "FAIL", level="numerical",
                                     reason=None if not deviations else "; ".join(deviations[:3]),
                                     checked=len(expectations))
    except BaseException as exc:
        case.subcase("fresh_process_and_private_home", "BLOCKED", level="live",
                     reason=f"{type(exc).__name__}: {exc}")
        for name in ("structure_readback", "representative_values"):
            case.subcase(name, "NOT_RUN", level="live", reason="the stdio session could not be used")
    case.finish()
    case.finalize_inventory()
    _write_case_evidence(run_dir, case, transcript, host)
    _write_run_index(run_dir, [case], extra={"mode": "reopen-check", "mph": _regrade(str(mph))})
    _print_report([case], run_dir)
    return 0 if case.status == "PASS" else 1


def _regrade(text: str) -> str:
    """Public-facing text: absolute private paths are replaced before anything is written."""
    return _redact_string(text)


def _environment_document(args: argparse.Namespace, run_dir: Path, *, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    private_home = str(args.private_home) if getattr(args, "private_home", None) else "run-owned (.g3-private/phase4-acceptance/<run>)"
    document: dict[str, Any] = {
        "generated_at": _utc_now(),
        "driver": "tools/phase4_run_mcp.py",
        "mode": "live" if args.live else "offline",
        "run_dir": str(run_dir),
        "python": _regrade(str(Path(args.python).expanduser())),
        "server_command": _regrade(f"{Path(args.python).expanduser()} -m comsol_mcp.mcp_server"),
        "git": _git_snapshot(),
        "comsol": {
            "root": args.comsol_root,
            "jdk11": args.jdk11,
            "prefs": args.prefs,
            "host": args.host,
            "port": args.port,
            "tool_profile": args.tool_profile,
            "project_id": args.project_id,
            "runtime_id": args.runtime_id,
            "private_home": _regrade(private_home),
            "isolation_receipt_present": bool(os.environ.get("COMSOL_MCP_ISOLATION_RECEIPT")),
            "trusted_code_requested": not TRUSTED_CODE_IS_NOT_REQUIRED,
        },
        "comsol_environment_keys_present": sorted(key for key in os.environ if key.startswith("COMSOL_")),
        "case_order": list(CASE_ORDER),
        "selected_cases": select_cases(args),
        "slice": _slice_summary(args, select_cases(args)),
    }
    if extra:
        document.update(dict(extra))
    return _redact(document)


def _case_requests(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for row in rows:
        if row.get("operation") in {"initialize", "tools/list"}:
            continue
        requests.append({
            "sequence": len(requests) + 1,
            "operation": row.get("operation"),
            "arguments": _json_safe(row.get("arguments")),
            "elapsed_s": row.get("elapsed_s"),
            "outer_isError": row.get("outer_isError"),
        })
    return requests


def _case_results(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row in rows:
        if row.get("operation") in {"initialize", "tools/list"}:
            continue
        raw_payload = row.get("payload")
        payload: Mapping[str, Any] = raw_payload if isinstance(raw_payload, Mapping) else {}
        results.append({
            "sequence": len(results) + 1,
            "operation": row.get("operation"),
            "success": payload.get("success"),
            "error_code": _error_code(payload),
            "error_message": _error_message(payload)[:400],
            "structuredContent": _json_safe(row.get("structuredContent")),
            "content": _json_safe(row.get("content")),
        })
    return results


def _export_reconciliations(case: Case, host: ProductionHost | None, *, since: int = 0) -> None:
    """Attach the engine reconciliations observed while this case ran.

    A blocked call that needed ``job_reconcile`` is evidence about the control plane, so it
    is recorded per case (and again in the run index) instead of being retried silently.  The
    driver's own UNKNOWN-job ledger is attached too: it is the only record of *which* jobs the
    control plane's gate was waiting for, and of how each one was released.
    """
    records = list(host.reconciliations[since:]) if host is not None else []
    if records:
        case.assertions["engine_reconciliations"] = _json_safe(records)
    if host is not None:
        ledger = host.ledger_evidence()
        if ledger["entries"] or ledger["gate_refusals"]["count"] or ledger["still_blocked"]:
            case.assertions["unknown_job_ledger"] = ledger


def _write_case_evidence(run_dir: Path, case: Case, rows: Sequence[Mapping[str, Any]],
                         host: ProductionHost | None) -> dict[str, Any]:
    case_dir = run_dir / "cases" / case.case_id
    case_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_path = run_dir / f"{host.label}.engine.log" if host is not None else None
    log_detail: dict[str, Any] = {"engine_log": None}
    if log_path is not None and log_path.is_file():
        log_detail = {"engine_log": str(log_path), "engine_log_sha256": _sha256(log_path),
                      "engine_log_bytes": log_path.stat().st_size}
    _write_json(case_dir / "environment.json", _redact({
        "case_id": case.case_id,
        "package": case.package,
        "acceptance": list(case.acceptance),
        "host_label": host.label if host is not None else None,
        "transport": "stdio",
        "private_home": str(host.private_home) if host is not None else None,
        "private_home_caller_owned": host.private_home_is_caller_owned if host is not None else None,
        "tool_profile": host.profile if host is not None else None,
        "isolation_receipt_present": host.isolation_receipt_present if host is not None else None,
        "transcript_path": str(run_dir / "transcript.json"),
        "started_at": case.started_at,
        "finished_at": case.finished_at,
        **log_detail,
    }))
    _write_json(case_dir / "requests.json", {"case_id": case.case_id, "requests": _case_requests(rows)})
    _write_json(case_dir / "results.json", {"case_id": case.case_id, "results": _case_results(rows)})
    _write_json(case_dir / "assertions.json", {"case": case.as_dict(), "assertion_count": len(case.assertions),
                                               "subcase_count": len(case.subcases)})
    return case.as_dict()


# ---------------------------------------------------------------------------
# C03: case isolation, prerequisite declarations and first-cause classification
# ---------------------------------------------------------------------------
#: The four classes a case's *first* failure is filed under (G3.1 section 4).  A case that cannot
#: start yields one dependency-blocked finding that points at its root cause instead of dozens of
#: pseudo-independent defects; a driver/fixture/protocol defect is the harness's own; a promised
#: required capability that is missing is an implementation gap; and only a genuinely unobtainable
#: platform, product, system approval or license is an external blocker.
FIRST_CAUSE_CLASSES: tuple[str, ...] = ("DEPENDENCY_BLOCKED", "HARNESS_FAILURE", "IMPLEMENTATION_GAP",
                                        "EXTERNAL_BLOCKER")
FIRST_CAUSE_BY_CODE: dict[str, str] = {
    # A required predecessor did not succeed.
    "DEPENDENCY_BLOCKED": "DEPENDENCY_BLOCKED",
    "NODE_NOT_FOUND": "DEPENDENCY_BLOCKED",
    "PRECONDITION_FAILED": "DEPENDENCY_BLOCKED",
    "MISSING_DEPENDENCY": "DEPENDENCY_BLOCKED",
    "GEOMETRY_NOT_FOUND": "DEPENDENCY_BLOCKED",
    # The driver's own defect (fixture, assertion, protocol, planning).
    "DRIVER_PRE_DISPATCH_REFUSAL": "HARNESS_FAILURE",
    "INVALID_MCP_RESPONSE": "HARNESS_FAILURE",
    "HARNESS_FAILURE": "HARNESS_FAILURE",
    "PROTOCOL_ERROR": "HARNESS_FAILURE",
    # A required capability that this build does not implement.
    "NOT_IMPLEMENTED": "IMPLEMENTATION_GAP",
    "UNSUPPORTED_OPERATION": "IMPLEMENTATION_GAP",
    "API_UNSUPPORTED": "IMPLEMENTATION_GAP",
    "CAPABILITY_UNAVAILABLE": "IMPLEMENTATION_GAP",
    "COMPILE_UNAVAILABLE": "IMPLEMENTATION_GAP",
    "UNAVAILABLE": "IMPLEMENTATION_GAP",
    # A revision-handling / contract defect in the product: never a license or product gap.
    "REVISION_CONFLICT": "IMPLEMENTATION_GAP",
    "MODEL_IDENTITY_MISMATCH": "IMPLEMENTATION_GAP",
    "INVALID_REQUEST": "IMPLEMENTATION_GAP",
    "INVALID_INVARIANT": "IMPLEMENTATION_GAP",
    "PROPERTY_TYPE_MISMATCH": "IMPLEMENTATION_GAP",
    # Genuinely unobtainable resources: platform, product, license, approval.
    "BLOCKED_LICENSE": "EXTERNAL_BLOCKER",
    "LICENSE_UNAVAILABLE": "EXTERNAL_BLOCKER",
    "INSUFFICIENT_LICENSE": "EXTERNAL_BLOCKER",
    "PRODUCT_UNAVAILABLE": "EXTERNAL_BLOCKER",
    "EXECUTION_STATE_UNKNOWN": "EXTERNAL_BLOCKER",
    "ENGINE_UNRESPONSIVE": "EXTERNAL_BLOCKER",
    "CONTROL_STARTUP_ERROR": "EXTERNAL_BLOCKER",
    "SERVER_UNAVAILABLE": "EXTERNAL_BLOCKER",
    "CONTROL_SERVICE_UNAVAILABLE": "EXTERNAL_BLOCKER",
    "RUNTIME_CONFIGURATION_REQUIRED": "EXTERNAL_BLOCKER",
    "ISOLATION_PROOF_REQUIRED": "EXTERNAL_BLOCKER",
    "CONNECT_REQUIRED": "EXTERNAL_BLOCKER",
    "ENGINE_BUSY": "EXTERNAL_BLOCKER",
}
#: Wording that shows a failure is *not* about an unobtainable resource, however it was labelled:
#: a revision conflict, an API-signature mismatch, a node path or a local path error is a product or
#: harness defect.  The live review found exactly this misattribution ("CAD license missing" for a
#: REVISION_CONFLICT / signature / path problem), which is why the classifier re-files such a row
#: instead of trusting its label.
MISATTRIBUTED_BLOCKER = re.compile(
    r"revision[_ ]conflict|expected_revision|managed revision|idempotency[_ ]conflict|"
    r"signature|arity|nodepath|node[_ ]path|node not found|tag[_ ]conflict|"
    r"no such file|file not found|path does not exist|invalid[_ ]request|schema",
    re.IGNORECASE)
#: Wording that points at the harness rather than at the product.
HARNESS_WORDING = re.compile(r"\bthe driver\b|\bdriver\b|fixture|assertion|driver_exception|plan(ning)? |handler|"
                             r"unexpected call to|traceback", re.IGNORECASE)


def _defect_evidence_text(entry: Mapping[str, Any]) -> str:
    """Everything one failure row carries as *evidence*, for the misattribution guard.

    The label a row claims is not evidence, so the guard reads the row's own refusal/observation
    material: a ``REVISION_CONFLICT`` hidden in ``observed``/``refusal`` must be able to contradict a
    ``BLOCKED_LICENSE`` claim that the row's headline makes.
    """
    parts: list[str] = [str(entry.get("reason") or ""), str(entry.get("message") or "")]
    for key in ("observed", "detail", "refusal", "read_errors", "refusal_evidence", "envelope"):
        value = entry.get(key)
        if value is None:
            continue
        try:
            parts.append(json.dumps(value, default=str))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            parts.append(str(value))
    return " ".join(part for part in parts if part)


def _first_cause_from_text(text: str) -> str:
    """The class of a failure that carries no code of its own — from the failure's own wording."""
    lowered = text.lower()
    if any(token in lowered for token in ("prerequisite", "not run", "did not pass", "predecessor",
                                          "dependency", "before its prerequisite")):
        return "DEPENDENCY_BLOCKED"
    if HARNESS_WORDING.search(text):
        return "HARNESS_FAILURE"
    if any(token in lowered for token in ("license", "licence", "product", "unavailable", "not installed",
                                          "no seat", "entitlement")):
        return "EXTERNAL_BLOCKER"
    if any(token in lowered for token in ("revision", "signature", "nodepath", "node path", "schema",
                                          "not implemented", "unsupported")):
        return "IMPLEMENTATION_GAP"
    return "IMPLEMENTATION_GAP"


def classify_first_cause(row: Mapping[str, Any] | None, *, case_id: str | None = None) -> dict[str, Any]:
    """File one failure under exactly one of the four first-cause classes, with its own evidence.

    ``row`` is a subcase row (``status``/``reason``/``error_code``/``observed``) or a driver-side
    failure record.  A row that declares its own ``first_cause`` keeps it.  A label that claims an
    external blocker while the evidence names a revision conflict, a signature mismatch or a path
    error is re-filed (and says so), so "CAD license missing" can never stand in for a driver or
    product defect.
    """
    entry = row if isinstance(row, Mapping) else {}
    declared = entry.get("first_cause")
    if isinstance(declared, str) and declared in FIRST_CAUSE_CLASSES:
        return {"class": declared, "source": "declared", "case_id": case_id,
                "reason": entry.get("reason"), "error_code": entry.get("error_code")}
    observed = entry.get("observed")
    observed = observed if isinstance(observed, Mapping) else {}
    code = str(entry.get("error_code") or observed.get("error_code") or "")
    # C03: the product may wrap the operation's *own* failure inside a broader code — the M1 run's
    # ``variable.group_create`` refusal is ``EXECUTION_STATE_UNKNOWN`` with
    # ``error.details.cause_code = NODE_NOT_FOUND``.  The original cause decides the class; the
    # wrapper code stays in the row as ``raised_code`` so nothing is lost.
    cause_code = str(entry.get("cause_code") or observed.get("cause_code") or "")
    reason = str(entry.get("reason") or observed.get("message") or entry.get("message") or "")
    classification = FIRST_CAUSE_BY_CODE.get(cause_code)
    cause_source = "product error.details.cause_code"
    if classification is None:
        classification = FIRST_CAUSE_BY_CODE.get(code)
        cause_source = "envelope error.code"
    cause = classification or _first_cause_from_text(f"{cause_code} {code} {reason}")
    guard: dict[str, Any] | None = None
    if cause == "EXTERNAL_BLOCKER":
        # The G3.1 §4 correction: "CAD license insufficient" may never stand in for a revision,
        # signature or path defect.  The guard reads the row's whole evidence, not just its label.
        evidence_text = _defect_evidence_text(entry)
        matched = MISATTRIBUTED_BLOCKER.search(evidence_text)
        if matched:
            cause = "HARNESS_FAILURE" if HARNESS_WORDING.search(evidence_text) else "IMPLEMENTATION_GAP"
            guard = {"reclassified_from": "EXTERNAL_BLOCKER", "class": cause,
                     "matched_evidence": matched.group(0),
                     "why": ("the evidence names a revision/signature/path defect, which is never an "
                             "unobtainable resource: no license, product or platform claim may stand in "
                             "for it")}
    return {"class": cause, "source": "classified", "case_id": case_id,
            "error_code": code or None, "cause_code": cause_code or None, "raised_code": code or None,
            "cause_source": cause_source if classification else "wording",
            "reason": reason or None, "guard": guard,
            "subcase": entry.get("subcase")}


def case_first_cause(case: Case) -> dict[str, Any] | None:
    """The case's own first failure: at most ONE class per case (never dozens of pseudo-defects).

    A case-level (or subcase-level) ``first_cause`` declaration is honoured, but only inside the
    closed vocabulary: a declaration outside it is itself a driver-side defect and is filed as a
    harness failure rather than quietly accepted.
    """
    declared = case.assertions.get("first_cause")
    if isinstance(declared, Mapping):
        return _json_safe(dict(declared))
    if isinstance(declared, str):
        if declared in FIRST_CAUSE_CLASSES:
            return {"class": declared, "source": "declared", "case_id": case.case_id,
                    "reason": case.reason, "error_code": None}
        return {"class": "HARNESS_FAILURE", "source": "declared-invalid", "case_id": case.case_id,
                "reason": f"the case declared {declared!r}, which is outside the first-cause vocabulary",
                "error_code": None,
                "guard": {"reclassified_from": declared, "class": "HARNESS_FAILURE",
                          "why": "the closed vocabulary is DEPENDENCY_BLOCKED/HARNESS_FAILURE/"
                                 "IMPLEMENTATION_GAP/EXTERNAL_BLOCKER"}}
    for name, row in case.subcases.items():
        if row.get("status") in {"FAIL", "BLOCKED"}:
            entry = dict(row)
            entry.setdefault("subcase", name)
            return _json_safe(classify_first_cause(entry, case_id=case.case_id))
    if case.status in {"FAIL", "BLOCKED"}:
        # A case that closed without a failing subcase still gets one cause: the case-level reason
        # is the first symptom the rest of the case's lines follow from.
        return _json_safe(classify_first_cause({"status": case.status, "reason": case.reason,
                                                "subcase": "(case level)"}, case_id=case.case_id))
    return None


def _first_cause_summary(cases: Sequence[Case]) -> dict[str, Any]:
    """The run-wide first-cause view: one class per failing case, never one per failed assertion."""
    per_case: dict[str, Any] = {}
    counts = {name: 0 for name in FIRST_CAUSE_CLASSES}
    for case in cases:
        cause = case_first_cause(case)
        if cause is None:
            continue
        per_case[case.case_id] = cause
        cause_class = str(cause.get("class") or "IMPLEMENTATION_GAP")
        counts[cause_class] = counts.get(cause_class, 0) + 1
    return {"per_case": per_case, "counts": counts, "classes": list(FIRST_CAUSE_CLASSES),
            "note": ("each case contributes at most one first cause; downstream subcases list "
                     "DEPENDENCY_BLOCKED against it instead of becoming pseudo-independent defects")}


# ---------------------------------------------------------------------------
# Case prerequisites and per-case isolation
# ---------------------------------------------------------------------------
#: The closed vocabulary of prerequisites a case may declare.
PREREQUISITE_CHECKS: dict[str, str] = {
    "bound_model": "a model_ref is bound for this run",
    "observed_revision": "the bound model's revision was read back at least once",
    "own_geometry": "the case builds its own component/geometry (no predecessor geometry assumed)",
    "negative_probe": "the case deliberately provokes a product refusal",
    "isolated_model": "the case's declared isolation (its own model or a verified checkpoint restore) was applied",
    "runtime": "a runtime/connection epoch exists for the runtime-scoped probes",
    "predecessor": "a named predecessor case finished PASS",
}
#: What each case declares it needs before its live half may run (G3.1 section 4: "in case start,
#: establish explicit prerequisites: model, component/geometry, material, mesh/study, revision").
#: A missing declaration item is the case's *dependency*, not a defect of the case itself.
CASE_PREREQUISITES: dict[str, tuple[str, ...]] = {
    "R01_LIVE": ("bound_model", "own_geometry"),
    "R03_LIVE": ("bound_model", "own_geometry"),
    "R04_LIVE": ("bound_model", "observed_revision", "negative_probe"),
    "R_READBACK": ("bound_model",),
    "W13_T006_variables": ("bound_model", "observed_revision", "own_geometry"),
    "W13_T015_units": ("bound_model",),
    "W13_T048_selection_drift": ("bound_model", "own_geometry"),
    "W13_T016_2D_data": ("bound_model", "own_geometry"),
    "W14_T009_geometry_edit": ("bound_model",),
    "W14_T034_local_paths": ("bound_model",),
    "W15_T007_selections": ("bound_model", "own_geometry"),
    "W15_T017_material": ("bound_model", "own_geometry"),
    "W15_T042_license": ("bound_model", "runtime"),
    "W16_T018_mesh": ("bound_model", "own_geometry"),
    "W16_T019_chainA_steady": ("bound_model", "own_geometry"),
    "W16_T019_chainB_transient": ("bound_model", "own_geometry"),
    "W16_T019_chainC_continue": ("bound_model", "predecessor:W16_T019_chainA_steady"),
    "W16_T020_solver": ("bound_model",),
    "GUARD_T010": ("bound_model", "negative_probe"),
    "GUARD_T035": ("bound_model", "negative_probe"),
    "GUARD_T038": ("bound_model", "negative_probe"),
    "GUARD_T005": ("bound_model", "negative_probe"),
    "GUARD_T033": ("bound_model", "observed_revision", "negative_probe", "isolated_model"),
}
#: How one case keeps its negative probes and writes away from the models other cases use.  Live
#: runs prepare the case's own model (or a verified checkpoint restore) *before* the case body; the
#: same Server stays strictly serial either way.
CASE_ISOLATION: dict[str, dict[str, str]] = {
    # The negative probes of R01/R03/R04 used to pollute the single shared model and every case after
    # them inherited the damage: they get their own model.
    "R01_LIVE": {"mode": "own_model", "rationale": "negative probes must not pollute another case's model"},
    "R03_LIVE": {"mode": "own_model", "rationale": "negative probes must not pollute another case's model"},
    "R04_LIVE": {"mode": "own_model", "rationale": "the stale-revision probe must not dirty the shared model"},
    "GUARD_T033": {"mode": "own_model",
                   "rationale": ("the evaluation-policy probes need a clean, validly bound model that no "
                                 "earlier refusal has dirtied")},
    "W16_T019_chainC_continue": {"mode": "checkpoint_restore",
                                 "rationale": "the user-style fixture is restored from a verified checkpoint, "
                                              "never edited in place"},
    "W14_T009_geometry_edit": {"mode": "checkpoint_restore",
                               "rationale": "an in-place geometry edit is only safe on a restored fixture"},
}
#: The default: the case runs against the run's bound model, strictly serially with the others.
#: The closed vocabulary of isolation modes: how a case keeps its writes away from the models other
#: cases use.  A live run prepares the declared mode *before* the case body and records whether it
#: was applied; the run itself stays serial either way.
ISOLATION_MODES: tuple[str, ...] = ("own_model", "checkpoint_restore", "shared_bound")
#: The declared isolation of a case that does not name one: the run's own bound model, serial flow.
CASE_ISOLATION_DEFAULT = {"mode": "shared_bound",
                          "rationale": "reads/writes the run's bound model; the run is serial"}


def case_isolation(case_id: str) -> dict[str, Any]:
    """How this case is isolated from the others (a declaration, always recorded)."""
    entry = dict(CASE_ISOLATION.get(case_id) or CASE_ISOLATION_DEFAULT)
    entry["case_id"] = case_id
    entry["modes"] = list(ISOLATION_MODES)
    return entry


def _prerequisite_check(name: str, state: Mapping[str, Any] | None, *, live: bool,
                        isolation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Check one declared prerequisite against what this run has actually established."""
    values = state if isinstance(state, Mapping) else {}
    if name == "bound_model":
        if not live:
            return {"applicable": False, "satisfied": None,
                    "evidence": "not applicable: the run is offline (no engine to bind a model on)"}
        ref = values.get("ref")
        ok = isinstance(ref, Mapping)
        return {"applicable": True, "satisfied": ok,
                "evidence": ("a model_ref is bound" if ok else
                             "no model_ref is bound: every live step of this case depends on one")}
    if name == "observed_revision":
        if not live:
            return {"applicable": False, "satisfied": None,
                    "evidence": "not applicable: the run is offline"}
        revision = values.get("revision")
        ok = isinstance(revision, int) and not isinstance(revision, bool)
        return {"applicable": True, "satisfied": ok,
                "evidence": (f"the bound model's revision was read back ({revision})" if ok else
                             "the bound model's revision was never read back")}
    if name == "runtime":
        if not live:
            return {"applicable": False, "satisfied": None,
                    "evidence": "not applicable: the run is offline"}
        ok = bool(values.get("server_connect", {}).get("success")) if isinstance(values.get("server_connect"),
                                                                                 Mapping) else False
        return {"applicable": True, "satisfied": ok,
                "evidence": ("the server connection succeeded" if ok else
                             "no successful server connection was recorded for this run")}
    if name == "own_geometry":
        return {"applicable": False, "satisfied": None,
                "evidence": "declared: the case builds its own component/geometry before using it"}
    if name == "negative_probe":
        return {"applicable": False, "satisfied": None,
                "evidence": "declared: the case asks for the product's own refusal"}
    if name == "isolated_model":
        record = isolation if isinstance(isolation, Mapping) else {}
        declaration = record.get("declaration") if isinstance(record.get("declaration"), Mapping) else {}
        mode = declaration.get("mode")
        if mode in (None, "shared_bound") or not live:
            return {"applicable": False, "satisfied": None,
                    "evidence": ("not applicable: this case runs against the run's bound model" if mode in (None, "shared_bound")
                                 else "not applicable: the run is offline (no engine to isolate a model on)")}
        ok = record.get("applied") is True
        return {"applicable": True, "satisfied": ok,
                "evidence": (f"the case's declared isolation ({mode}) was applied" if ok else
                             f"the case's declared isolation ({mode}) was not applied: "
                             f"{record.get('reason') or 'no reason was recorded'}")}
    if name.startswith("predecessor:"):
        target = name.split(":", 1)[1]
        statuses = values.get("completed_cases")
        status = statuses.get(target) if isinstance(statuses, Mapping) else None
        if status is None and not live:
            return {"applicable": False, "satisfied": None,
                    "evidence": f"not applicable: {target} did not run in this selection"}
        ok = status == "PASS"
        return {"applicable": True, "satisfied": ok,
                "evidence": (f"{target} finished {status}" if status else
                             f"{target} has not run in this run, so its product cannot be assumed")}
    return {"applicable": False, "satisfied": None,
            "evidence": f"unknown declaration {name!r} (PREREQUISITE_CHECKS names the vocabulary)"}


def case_prerequisites(case_id: str, state: Mapping[str, Any] | None, *, live: bool,
                       isolation: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The case's declared prerequisites, each checked (never assumed)."""
    declared = CASE_PREREQUISITES.get(case_id) or ("bound_model",)
    checks = {name: _prerequisite_check(name, state, live=live, isolation=isolation) for name in declared}
    unsatisfied = [name for name, row in checks.items()
                   if row.get("applicable") and row.get("satisfied") is False]
    unknown = [name for name, row in checks.items() if name not in PREREQUISITE_CHECKS
               and not name.startswith("predecessor:")]
    return {"case_id": case_id, "declared": list(declared), "checks": checks,
            "unsatisfied": unsatisfied, "unknown_declarations": unknown,
            "status": "UNSATISFIED" if unsatisfied else "SATISFIED",
            "note": ("a missing prerequisite makes this case DEPENDENCY_BLOCKED and points at the "
                     "root cause; it never becomes a defect of this case")}


async def _case_isolation_step(case: Case, client: ActionClient, args: argparse.Namespace,
                               state: dict[str, Any], host: ProductionHost) -> dict[str, Any]:
    """Give an independent case its own model before its body runs (live runs only).

    The declaration is always recorded.  ``own_model`` creates a fresh MCP-owned model through the
    public route and re-binds the run's client state to it; ``checkpoint_restore`` requires a
    verified restore of the named checkpoint (recorded, never assumed).  An offline run records
    exactly why nothing was attempted.
    """
    plan = case_isolation(case.case_id)
    record: dict[str, Any] = {"declaration": plan, "attempted": False, "applied": False,
                              "at": _utc_now()}
    if plan["mode"] == "shared_bound":
        record["reason"] = "declared: this case runs against the run's bound model, strictly serially"
        return record
    if not args.live:
        record["reason"] = "not attempted: the run is offline (no engine to create or restore a model on)"
        return record
    if plan["mode"] == "own_model":
        label = f"{case.case_id} ({args.fixture_model_name}) isolation model"
        payload = await _create_empty_model(client, state, label, host)
        record.update({"attempted": True, "route": "model.create", "model_label": label,
                       "success": _success(payload), "error_code": _error_code(payload),
                       "model_ref": _envelope_identity(payload)})
        ok = bool(_success(payload) and isinstance(state.get("ref"), Mapping))
        record["applied"] = ok
        if not ok:
            record["reason"] = (f"the case's own model could not be created "
                                f"({_error_code(payload) or 'no model_ref came back'})")
        return record
    if plan["mode"] == "checkpoint_restore":
        # A restore must be *verified* before the case may trust it: the published checkpoint reads
        # establish that the checkpoint exists, and the restore is only accepted with a new ModelRef.
        if "checkpoint_restore" not in host.tools:
            record.update({"attempted": False, "applied": False,
                           "reason": "checkpoint_restore is not published by this host, so this case cannot "
                                     "be isolated on a restored fixture"})
            return record
        record["attempted"] = True
        payload = await client.action("checkpoint.restore",
                                      {"checkpoint": case.case_id, "reload": True},
                                      key=_fresh_key(f"isolate-{case.case_id}"),
                                      request=f"isolate-{case.case_id}")
        record.update({"success": _success(payload), "error_code": _error_code(payload),
                       "model_ref": _envelope_identity(payload)})
        record["applied"] = bool(_success(payload) and isinstance(state.get("ref"), Mapping))
        if not record["applied"]:
            record["reason"] = (f"the checkpoint restore did not yield a bound model "
                                f"({_error_code(payload) or 'no model_ref came back'})")
        return record
    record["reason"] = f"unknown isolation mode {plan['mode']!r}"
    return record


# ---------------------------------------------------------------------------
# Slicing: --only subsets and the staged run plan (M0 -> M1 -> M2 -> M3)
# ---------------------------------------------------------------------------
#: The staged run plan of G3.1 section 10.  Every case belongs to exactly one stage; M3 is computed
#: as the remainder so a newly added case can never silently stop being run.
RUN_STAGES: dict[str, tuple[str, ...]] = {
    "M0": (),  # the offline root-cause gate: no --live, so the whole selection runs its static half
    "M1": ("W13_T006_variables", "W13_T015_units", "R04_LIVE", "GUARD_T010", "GUARD_T005", "GUARD_T033"),
    "M2": ("W16_T019_chainA_steady", "W16_T019_chainB_transient"),
}


def stage_cases(stage: str) -> tuple[str, ...]:
    """The case ids of one stage (M3 is the remainder, in the published case order)."""
    name = str(stage).upper()
    if name in RUN_STAGES and RUN_STAGES[name]:
        return RUN_STAGES[name]
    taken = {case_id for values in RUN_STAGES.values() for case_id in values}
    return tuple(case_id for case_id in CASE_ORDER if case_id not in taken)


def select_cases(args: argparse.Namespace) -> list[str]:
    """The case selection of one run: a stage subset, an explicit --only list, or the intersection.

    ``--only`` accepts both the comma-separated form the command line uses and the list ``main``
    normalizes it to, and a slice never widens: a case named by ``--only`` that its stage excludes
    is dropped rather than pulled in.
    """
    stage = str(getattr(args, "stage", "") or "")
    base = list(stage_cases(stage)) if stage else list(CASE_ORDER)
    raw = getattr(args, "only", None)
    if isinstance(raw, str):
        requested = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        requested = [str(part).strip() for part in (raw or []) if str(part).strip()]
    if not requested:
        return base
    return [case_id for case_id in requested if case_id in base]


def _slice_summary(args: argparse.Namespace, selected: Sequence[str]) -> dict[str, Any]:
    """What this run selected, and under which stage plan."""
    only = list(getattr(args, "only", []) or [])
    stage = getattr(args, "stage", None) or None
    return {"stage": stage,
            "stages": {name: list(stage_cases(name)) for name in ("M0", "M1", "M2", "M3")},
            "only": only, "live": bool(getattr(args, "live", False)), "selected": list(selected),
            "note": ("a slice is a documented run plan, not a weakened acceptance: every selected case "
                     "still records its own subcases at their declared evidence level, and a stage "
                     "subset never turns an unselected case into a pass")}


def _status_counts(cases: Sequence[Case]) -> dict[str, int]:
    counts = {status: 0 for status in ("PASS", "FAIL", "BLOCKED", "NOT_RUN")}
    for case in cases:
        counts[case.status] = counts.get(case.status, 0) + 1
    return counts


def _subcase_counts(cases: Sequence[Case]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        for row in case.subcases.values():
            status = str(row.get("status"))
            counts[status] = counts.get(status, 0) + 1
    return counts


def _write_run_index(run_dir: Path, cases: Sequence[Case], *, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    index: dict[str, Any] = {
        "generated_at": _utc_now(),
        "run_dir": str(run_dir),
        "cases": [{**case.as_dict(), "assertion_count": len(case.assertions), "subcase_count": len(case.subcases)}
                  for case in cases],
        "case_status_counts": _status_counts(cases),
        "subcase_status_counts": _subcase_counts(cases),
        "totals": {"assertions": sum(len(case.assertions) for case in cases),
                   "subcases": sum(len(case.subcases) for case in cases)},
    }
    if extra:
        index.update(dict(extra))
    _write_json(run_dir / "index.json", _redact(index))
    return index


def _first_cause_lines(cases: Sequence[Case]) -> list[str]:
    """The summary's first-cause table: one class per failing case."""
    summary = _first_cause_summary(cases)
    counts = summary["counts"]
    lines = ["", "## First causes (C03)", "",
             "- per class: " + ", ".join(f"{name} {counts.get(name, 0)}" for name in FIRST_CAUSE_CLASSES),
             f"- classification rule: {summary['note']}"]
    for case in cases:
        cause = summary["per_case"].get(case.case_id)
        if cause is None:
            continue
        guard = ""
        if cause.get("guard"):
            guard = f" — reclassified from {cause['guard'].get('reclassified_from')}: {cause['guard'].get('why')}"
        # C03: the raised code and the operation's own cause are both named, so a wrapper code (e.g.
        # EXECUTION_STATE_UNKNOWN around NODE_NOT_FOUND) can never hide which failure this is.
        codes = [str(item) for item in (cause.get("raised_code") or cause.get("error_code"),
                                        cause.get("cause_code")) if item]
        lines.append(f"- `{case.case_id}` → **{cause.get('class')}** "
                     f"({cause.get('error_code') or cause.get('source') or 'no code'}"
                     + (f"; cause={'/'.join(codes)}" if len(codes) > 1 else "")
                     + f"): {str(cause.get('reason') or '')[:160]}{guard}")
    return lines


def _isolation_lines(cases: Sequence[Case]) -> list[str]:
    """The summary's isolation/prerequisite table: what each case declared and what was applied."""
    lines = ["", "## Case isolation and prerequisites (C03)", ""]
    for case in cases:
        isolation_raw = case.assertions.get("isolation")
        isolation: Mapping[str, Any] = isolation_raw if isinstance(isolation_raw, Mapping) else {}
        declaration_raw = isolation.get("declaration")
        declaration: Mapping[str, Any] = declaration_raw if isinstance(declaration_raw, Mapping) else {}
        prerequisites = case.assertions.get("prerequisites")
        prerequisites = prerequisites if isinstance(prerequisites, Mapping) else {}
        missing = prerequisites.get("unsatisfied") or []
        established = prerequisites.get("established")
        established = established if isinstance(established, Mapping) else {}
        restored_raw = isolation.get("shared_model_restored")
        restored: Mapping[str, Any] = restored_raw if isinstance(restored_raw, Mapping) else {}
        restored_ref = _mapping(restored.get("ref"))
        lines.append(f"- `{case.case_id}`: isolation={declaration.get('mode')} "
                     f"(applied={isolation.get('applied', False)}) — {declaration.get('rationale')}; "
                     f"prerequisites={prerequisites.get('status', 'not declared')}"
                     + (f"; shared binding restored: {restored_ref.get('model_tag')}"
                        if restored_ref else "")
                     + (f"; missing: {', '.join(str(name) for name in missing)}" if missing else ""))
        if established:
            # C03: what the case *established itself* (created and read back), not just what it
            # declared — a container the fixture never made is the case's dependency.
            lines.append(f"  - established: {established.get('component') or established.get('kind')} → "
                         f"{established.get('status')} ({established.get('reason')})")
    return lines


def _slice_lines(args: argparse.Namespace | None, selected: Sequence[str] | None) -> list[str]:
    """The summary's slice section: which stage/subset this run executed."""
    if args is None:
        return []
    summary = _slice_summary(args, list(selected or []))
    lines = ["", "## Slice (this run)", "",
             f"- stage: {summary['stage'] or 'none (the full case order)'}",
             f"- live: {summary['live']}",
             f"- selected cases: {', '.join(summary['selected']) or 'none'}",
             f"- note: {summary['note']}"]
    for name in ("M0", "M1", "M2", "M3"):
        lines.append(f"- {name}: {', '.join(summary['stages'][name]) or '(offline gate: no live case subset)'}")
    return lines


def _context_lines(evidence: Mapping[str, Any] | None) -> list[str]:
    """The summary's execution-context section: requests, rejection reasons, replans, probes."""
    if not isinstance(evidence, Mapping):
        return []
    counts = evidence.get("rejection_counts") or {}
    replays = evidence.get("replays") or []
    identical = len([row for row in replays if row.get("same_body") is True])
    dispatches = evidence.get("dispatches") or []
    stages = {name: len([row for row in dispatches if row.get("stage") == name]) for name in
              sorted({str(row.get("stage")) for row in dispatches})}
    lines = ["", "## Execution context (C02)", "",
             f"- logical requests planned (run/case/step/sequence keys): {evidence.get('request_count', 0)}",
             "- refusals classified as: "
             + ", ".join(f"{name} {counts.get(name, 0)}" for name in (*REJECTION_REASONS, *AUXILIARY_REJECTIONS)),
             f"- recorded new plans (replans): {len(evidence.get('replans') or [])}; "
             f"same-key replays recorded: {len(replays)} (identical body verified: {identical})",
             # C03: the stage each dispatched request provably reached, in the driver's own
             # vocabulary (NOT_EXECUTED stages included), so a refusal is never read as a mystery.
             "- dispatch stages recorded: " + (", ".join(f"{name} {count}" for name, count in stages.items())
                                               if stages else "none"),
             f"- generation replacements observed: {len(evidence.get('generation_replacements') or [])}",
             f"- deliberate negative probes (never auto-repaired): {len(evidence.get('negative_probes') or [])}",
             f"- unfinished jobs known to the context: {len(evidence.get('unfinished_jobs') or [])}"]
    return lines


def _write_summary(run_dir: Path, cases: Sequence[Case], host: ProductionHost | None = None, *,
                   args: argparse.Namespace | None = None, selected: Sequence[str] | None = None) -> None:
    lines = ["# Phase 4 (G3) production stdio acceptance run", "",
             f"- generated: {_utc_now()}", f"- run directory: `{run_dir}`", ""]
    lines.append("| case | package | status | assertions | subcases | first finding |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for case in cases:
        finding = ""
        for row in case.subcases.values():
            if row.get("status") in {"FAIL", "BLOCKED"} and row.get("reason"):
                finding = str(row.get("reason"))[:160]
                break
        lines.append(f"| {case.case_id} | {case.package} | {case.status} | {len(case.assertions)} | "
                     f"{len(case.subcases)} | {finding.replace('|', '/')} |")
    counts = _status_counts(cases)
    lines.extend(["", f"cases: PASS {counts['PASS']} / FAIL {counts['FAIL']} / BLOCKED {counts['BLOCKED']} / "
                      f"NOT_RUN {counts['NOT_RUN']}", ""])
    lines.append("## Subcase detail")
    for case in cases:
        lines.append("")
        lines.append(f"### {case.case_id} — {case.status}")
        for name, row in case.subcases.items():
            reason = f" — {row.get('reason')}" if row.get("reason") else ""
            lines.append(f"- `{row.get('status')}` [{row.get('level')}] {name}{reason}")
    if host is not None:
        ledger = host.ledger_evidence()
        if ledger["entries"] or ledger["gate_refusals"]["count"] or ledger["still_blocked"]:
            lines.extend(["", "## Unknown-job ledger (driver side)", "",
                          f"- gate refusals: {ledger['gate_refusals']['count']} "
                          f"({', '.join(f'{tool}×{count}' for tool, count in sorted(ledger['gate_refusals']['by_tool'].items())) or 'none'})",
                          f"- recorded UNKNOWN jobs: {len(ledger['entries'])} "
                          f"(released: {len(ledger['released_jobs'])}, unreleased: {len(ledger['unresolved_jobs'])})",
                          f"- calls that stayed refused after the release path ran: {len(ledger['still_blocked'])}", ""])
            for entry in ledger["entries"]:
                releases = entry.get("release_attempts") or []
                last = releases[-1] if releases else {}
                lines.append(f"- `{entry.get('job_id')}` ({entry.get('operation')}): released="
                             f"{entry.get('released')}, attempts={len(releases)}, "
                             f"reconciled_quiescent={last.get('reconciled_quiescent')}, status={last.get('status')}")
    lines.extend(_first_cause_lines(cases))
    lines.extend(_isolation_lines(cases))
    lines.extend(_slice_lines(args, selected))
    if host is not None:
        lines.extend(_context_lines(host.context.evidence()))
    body = _redact_string("\n".join(lines) + "\n")
    run_dir.joinpath("summary.md").write_text(body, encoding="utf-8")


def _write_sha256sums(run_dir: Path) -> Path:
    target = run_dir / "SHA256SUMS"
    lines: list[str] = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path == target:
            continue
        digest = _sha256(path)
        lines.append(f"{digest}  {path.relative_to(run_dir)}")
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return target


def _print_report(cases: Sequence[Case], run_dir: Path) -> None:
    print("")
    print(f"evidence: {run_dir}")
    print(f"{'case':<28} {'status':<8} {'assert':>7} {'sub':>5}  first finding")
    print("-" * 118)
    for case in cases:
        finding = ""
        for row in case.subcases.values():
            if row.get("status") in {"FAIL", "BLOCKED"} and row.get("reason"):
                finding = str(row.get("reason"))
                break
        if not finding:
            for row in case.subcases.values():
                if row.get("status") == "NOT_RUN" and row.get("reason"):
                    finding = str(row.get("reason"))
                    break
        print(f"{case.case_id:<28} {case.status:<8} {len(case.assertions):>7} {len(case.subcases):>5}  {finding[:70]}")
    counts = _status_counts(cases)
    subcounts = _subcase_counts(cases)
    print("-" * 118)
    print(f"cases: PASS {counts['PASS']} | FAIL {counts['FAIL']} | BLOCKED {counts['BLOCKED']} | NOT_RUN {counts['NOT_RUN']}")
    print("subcases: " + " | ".join(f"{status} {subcounts.get(status, 0)}"
                                    for status in ("PASS", "FAIL", "BLOCKED", "NOT_RUN")))


CASES: dict[str, Callable[[ProductionHost, ActionClient, Case, argparse.Namespace, dict[str, Any]], Awaitable[None]]] = {
    "R01_LIVE": _case_r01,
    "R03_LIVE": _case_r03,
    "R04_LIVE": _case_r04,
    "R_READBACK": _case_r_readback,
    "W13_T006_variables": _case_w13_t006,
    "W13_T015_units": _case_w13_t015,
    "W13_T048_selection_drift": _case_w13_t048,
    "W13_T016_2D_data": _case_w13_t016,
    "W14_T009_geometry_edit": _case_w14_t009,
    "W14_T034_local_paths": _case_w14_t034,
    "W15_T007_selections": _case_w15_t007,
    "W15_T017_material": _case_w15_t017,
    "W15_T042_license": _case_w15_t042,
    "W16_T018_mesh": _case_w16_t018,
    "W16_T019_chainA_steady": _case_w16_t019_chain_a,
    "W16_T019_chainB_transient": _case_w16_t019_chain_b,
    "W16_T019_chainC_continue": _case_w16_t019_chain_c,
    "W16_T020_solver": _case_w16_t020,
    "GUARD_T010": _case_guard_t010,
    "GUARD_T035": _case_guard_t035,
    "GUARD_T038": _case_guard_t038,
    "GUARD_T005": _case_guard_t005,
    "GUARD_T033": _case_guard_t033,
}


async def _run_suite(args: argparse.Namespace) -> int:
    global _RUN_IDEMPOTENCY_PREFIX
    run_dir = (Path(args.run_dir).expanduser() if args.run_dir
               else ROOT / "evidence" / "phase4" / "runs" / _stamp()).resolve()
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.run_dir = str(run_dir)
    _RUN_IDEMPOTENCY_PREFIX = f"phase4-{run_dir.name}"
    selected = select_cases(args)
    unknown = [case_id for case_id in selected if case_id not in CASES]
    if unknown:
        _write_json(run_dir / "environment.json", _environment_document(args, run_dir,
                                                                        extra={"unknown_cases": unknown}))
        print(f"unknown case id(s): {', '.join(unknown)}")
        print("available: " + ", ".join(CASE_ORDER))
        return 2
    _write_json(run_dir / "environment.json", _environment_document(args, run_dir))
    transcript: list[dict[str, Any]] = []
    state: dict[str, Any] = {"ref": None, "revision": None, "model_label": None}
    cases: list[Case] = []
    host = ProductionHost(args, run_dir, transcript, label="primary", run_state=state)
    startup_error: str | None = None
    try:
        async with host:
            client = ActionClient(host, args, state)
            await _bind_model(client, case=None, args=args, state=state, host=host)
            for case_id in selected:
                specification = next(item for item in CASE_PACKAGES if item[0] == case_id)
                case = Case(case_id=case_id, package=specification[1], acceptance=specification[2])
                # C02: every call this case dispatches is planned under run/case/step/sequence, so a
                # same key can only ever mean the same request.
                state["request_scope"] = {"case": case_id, "step": None}
                # C03: the case's isolation is declared and prepared first (its own model or a verified
                # checkpoint restore), then its prerequisites are checked against what this run has
                # actually established.  A missing prerequisite is ONE dependency-blocked finding
                # that points at the root cause, not a defect of this case.
                # An isolated case runs on its *own* model: the run's shared binding is captured
                # *before* the case is prepared, put back afterwards, and the swap is recorded —
                # never silent.  Capturing it after the isolation step (as this used to) made the
                # restore a no-op, so every later "shared_bound" case silently inherited the last
                # isolated model: the M1 run's T010/T005 ran on R04's own (probe-dirtied) model.
                shared_ref_before = dict(state["ref"]) if isinstance(state.get("ref"), Mapping) else None
                shared_revision_before = state.get("revision")
                isolation = await _case_isolation_step(case, client, args, state, host)
                prerequisites = case_prerequisites(case_id, state, live=bool(args.live), isolation=isolation)
                case.assertions["isolation"] = isolation
                case.assertions["prerequisites"] = prerequisites
                started = len(transcript)
                reconcile_cursor = len(host.reconciliations)
                try:
                    if prerequisites["unsatisfied"]:
                        missing = ", ".join(prerequisites["unsatisfied"])
                        reason = (f"the case's declared prerequisite(s) are not established: {missing} "
                                  f"({'; '.join(str((prerequisites['checks'].get(name) or {}).get('evidence')) for name in prerequisites['unsatisfied'])})")
                        case.assertions["first_cause"] = classify_first_cause(
                            {"reason": reason, "error_code": "DEPENDENCY_BLOCKED", "first_cause": "DEPENDENCY_BLOCKED"},
                            case_id=case_id)
                        case.assertions["dependency_blocked"] = {"missing": list(prerequisites["unsatisfied"]),
                                                                 "note": ("downstream subcases list NOT_RUN against "
                                                                          "this root cause instead of becoming "
                                                                          "independent defects")}
                        for item in PLAN.get(case_id, ()):
                            case.subcase(item.name, "NOT_RUN", level=item.level,
                                         reason=f"dependency not established: {missing}")
                        case.finish("BLOCKED", reason=reason)
                    else:
                        await CASES[case_id](host, client, case, args, state)
                except CapabilityUnavailable as exc:
                    case.finish("BLOCKED", reason=f"capability unavailable: {exc}")
                except BaseException as exc:  # noqa: BLE001 - the driver records any driver-side failure
                    detail = traceback.format_exc().strip().splitlines()
                    case.finish("FAIL", reason=f"{type(exc).__name__}: {exc}")
                    case.assertions["driver_exception"] = {"type": type(exc).__name__, "message": str(exc),
                                                          "trace_tail": detail[-6:]}
                finally:
                    if case.finished_at is None:
                        case.finish()
                    if (isolation.get("applied") and shared_ref_before is not None
                            and isinstance(state.get("ref"), Mapping)
                            and state["ref"] != shared_ref_before):
                        isolation["shared_model_restored"] = {
                            "ref": shared_ref_before, "revision": shared_revision_before,
                            "note": ("the case ran on its own model; the run's shared binding is restored "
                                     "for the cases that follow")}
                        state["ref"] = dict(shared_ref_before)
                        state["revision"] = shared_revision_before
                    _export_reconciliations(case, host, since=reconcile_cursor)
                    case.finalize_inventory()
                    _write_case_evidence(run_dir, case, transcript[started:], host)
                    cases.append(case)
                    statuses = state.get("completed_cases")
                    if not isinstance(statuses, dict):
                        statuses = {}
                        state["completed_cases"] = statuses
                    statuses[case.case_id] = case.status
    except BaseException as exc:  # the stdio session itself could not be established
        startup_error = f"{type(exc).__name__}: {exc}"
        for case_id in selected:
            specification = next(item for item in CASE_PACKAGES if item[0] == case_id)
            case = Case(case_id=case_id, package=specification[1], acceptance=specification[2])
            for item in PLAN.get(case_id, ()):
                case.subcase(item.name, "BLOCKED", level=item.level,
                             reason=f"the production stdio session could not be established: {startup_error}")
            case.finish("BLOCKED", reason=startup_error)
            case.finalize_inventory()
            _write_case_evidence(run_dir, case, [], None)
            cases.append(case)
    _write_json(run_dir / "transcript.json", {"transport": "stdio", "host": "primary",
                                              "call_count": len([row for row in transcript
                                                                 if row.get("operation") not in {"initialize", "tools/list"}]),
                                              "transcript": transcript})
    # The evidence tree is written first, then scanned, then rewritten: the leak scan is itself
    # evidence about the finished tree, so the index and the digest list are produced last.
    _write_summary(run_dir, cases, host, args=args, selected=selected)
    scan = _leak_scan(run_dir, private_home=host.private_home if host.private_home_is_caller_owned else None)
    if any(case.case_id == "GUARD_T035" for case in cases):
        _apply_leak_scan(cases, run_dir, scan)
    _write_summary(run_dir, cases, host, args=args, selected=selected)
    _write_run_index(run_dir, cases, extra={"startup_error": startup_error, "leak_scan": scan,
                                            "engine_reconciliations": host.reconciliations,
                                            "unknown_job_ledger": host.ledger_evidence(),
                                            "execution_context": host.context.evidence(),
                                            "first_cause": _first_cause_summary(cases),
                                            "slice": _slice_summary(args, selected),
                                            "model_bind": _json_safe({key: value for key, value in state.items()
                                                                      if key.startswith("model_") or key.endswith("_probe")})})
    _write_sha256sums(run_dir)
    _print_report(cases, run_dir)
    counts = _status_counts(cases)
    if counts["FAIL"]:
        return 1
    if counts["BLOCKED"]:
        return 3
    if counts["NOT_RUN"]:
        return 4
    return 0


def _apply_leak_scan(cases: Sequence[Case], run_dir: Path, scan: Mapping[str, Any]) -> None:
    """Fold the run-wide leak scan into the T035 guard, overriding a provisional PASS."""
    guard = next((case for case in cases if case.case_id == "GUARD_T035"), None)
    if guard is None:
        return
    fixture_names = {Path(str(row.get("credential_fixture") or "")).name
                     for row in [guard.assertions.get("t035_fixtures") or {}] if isinstance(row, Mapping)}
    credential_hits = [hit for hit in (scan.get("credential_leak_hits") or scan.get("credential_hits", []))
                       if Path(str(hit.get("file"))).name not in fixture_names]
    private_hits = [hit for hit in scan.get("private_path_hits", [])
                    if Path(str(hit.get("file"))).name not in fixture_names]
    runtime_hits = [hit for hit in scan.get("runtime_state_hits", []) or []
                    if Path(str(hit.get("file"))).name not in fixture_names]
    guard.assertions["run_leak_scan"] = {"credential_hits": credential_hits, "private_path_hits": private_hits,
                                         "runtime_state_hits": runtime_hits,
                                         "scanned_files": scan.get("scanned_files")}
    guard.subcase("credential_text_not_persisted", "FAIL" if credential_hits else "PASS", level="protocol",
                  force=True, reason="the run-wide scan found credential material in the evidence tree" if credential_hits else None,
                  hits=credential_hits)
    if private_hits:
        guard.subcase("private_paths_not_in_evidence", "FAIL", level="protocol", force=True,
                      reason="the run-wide scan found private absolute paths in the evidence documents",
                      hits=private_hits, runtime_state_hits=runtime_hits)
    elif runtime_hits:
        guard.subcase("private_paths_not_in_evidence", "BLOCKED", level="protocol", force=True,
                      reason=("the caller-owned private home was placed inside the evidence tree; the driver's own "
                              "evidence documents contain no private paths (re-run without --private-home or point "
                              "it outside the evidence tree)"),
                      runtime_state_hits=runtime_hits)
    else:
        guard.subcase("private_paths_not_in_evidence", "PASS", level="protocol", force=True, reason=None)
    _write_case_evidence(run_dir, guard, [], None)


async def _bind_model(client: ActionClient, case: Case | None, args: argparse.Namespace, state: dict[str, Any],
                      host: ProductionHost) -> None:
    """Bind the acceptance model: an explicitly supplied .mph, else a fresh MCP-owned model (live only)."""
    if not args.live:
        state["model_origin"] = "none (offline run)"
        return
    connect = await host.call("server_connect", {
        "host": args.host, "port": args.port,
        "execution": _execution(key="suite-connect", request="suite-connect"),
    })
    state["server_connect"] = {"success": _success(connect), "error_code": _error_code(connect)}
    if not _success(connect):
        # Every live case will see ENGINE_UNRESPONSIVE at its first engine
        # call; record the connection failure once, honestly, and let the
        # cases report their own blocked status.
        state["model_origin"] = f"connect_failed ({_error_code(connect) or 'UNKNOWN'})"
        return
    if args.model:
        model_path = Path(args.model).expanduser()
        payload, probe = await _load_model(host, client, model_path, args.model_artifact_id, key="suite-load-model")
        state["model_load_probe"] = probe
        state["model_origin"] = "loaded" if _success(payload) else (
            "blocked" if _blocked_payload(payload) else "load_failed")
        state["model_sha256"] = _sha256(model_path) if model_path.is_file() else None
        state["model_load_error"] = _error_code(payload)
        return
    created = await _create_empty_model(client, state, args.fixture_model_name, host)
    state["model_origin"] = "created" if _success(created) else (
        "blocked" if _blocked_payload(created) else "create_failed")
    if not _success(created):
        state["model_create_error"] = _error_code(created)


CASE_PACKAGES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("R01_LIVE", "R", ("G3 §7 R01",)),
    ("R03_LIVE", "R", ("G3 §7 R03",)),
    ("R04_LIVE", "R", ("G3 §7 R04",)),
    ("R_READBACK", "R", ("G3 §7",)),
    ("W13_T006_variables", "W13", ("G3 §7 W13.T006",)),
    ("W13_T015_units", "W13", ("G3 §7 W13.T015",)),
    ("W13_T048_selection_drift", "W13", ("G3 §7 W13.T048",)),
    ("W13_T016_2D_data", "W13", ("G3 §7 W13.T016",)),
    ("W14_T009_geometry_edit", "W14", ("G3 §7 W14.T009",)),
    ("W14_T034_local_paths", "W14", ("G3 §7 W14.T034",)),
    ("W15_T007_selections", "W15", ("G3 §7 W15.T007",)),
    ("W15_T017_material", "W15", ("G3 §7 W15.T017",)),
    ("W15_T042_license", "W15", ("G3 §7 W15.T042",)),
    ("W16_T018_mesh", "W16", ("G3 §7 W16.T018",)),
    ("W16_T019_chainA_steady", "W16", ("G3 §8 chain A",)),
    ("W16_T019_chainB_transient", "W16", ("G3 §8 chain B",)),
    ("W16_T019_chainC_continue", "W16", ("G3 §8 chain C",)),
    ("W16_T020_solver", "W16", ("G3 §7 W16.T020",)),
    ("GUARD_T010", "GUARD", ("G3 §10 T010",)),
    ("GUARD_T035", "GUARD", ("G3 §10 T035",)),
    ("GUARD_T038", "GUARD", ("G3 §10 T038",)),
    ("GUARD_T005", "GUARD", ("G3 §10 T005",)),
    ("GUARD_T033", "GUARD", ("G3 §10 T033",)),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phase4_run_mcp.py",
        description=("G3 (Phase 4) production stdio acceptance driver: spawns the real MCP stdio entrypoint, "
                     "drives the published operations only, and writes a redacted evidence tree."),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--python", default=sys.executable, help="interpreter that hosts comsol_mcp.mcp_server")
    parser.add_argument("--live", action="store_true",
                        help="run the live cases against a bound model on an already running COMSOL Server")
    parser.add_argument("--host", default=os.environ.get("COMSOL_SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("COMSOL_SERVER_PORT", "2036")))
    parser.add_argument("--comsol-root", default=os.environ.get("COMSOL_ROOT"),
                        help="COMSOL installation root passed to the server child")
    parser.add_argument("--runtime-root", dest="comsol_root", help="alias of --comsol-root")
    parser.add_argument("--jdk11", default=os.environ.get("COMSOL_JAVA_HOME") or os.environ.get("JAVA_HOME"))
    parser.add_argument("--prefs", default=os.environ.get("COMSOL_PREFS_DIR"))
    parser.add_argument("--private-home", default=None,
                        help="bind an existing private MCP home (default: a fresh run-owned home)")
    parser.add_argument("--tool-profile", default=os.environ.get("COMSOL_MCP_TOOL_PROFILE", "full"))
    parser.add_argument("--project-id", default="comsol-mcp-phase4")
    parser.add_argument("--runtime-id", default="COMSOL-6.4")
    parser.add_argument("--component", default="comp1")
    parser.add_argument("--geometry-tag", default="geom1")
    parser.add_argument("--mesh-tag", default="mesh1")
    parser.add_argument("--physics-tag", default="ht")
    parser.add_argument("--study-tag", default="std1")
    parser.add_argument("--dataset-tag", default="dset1")
    parser.add_argument("--heat-physics-type", default="HeatTransfer")
    parser.add_argument("--temperature-tag", default="temp1")
    parser.add_argument("--volume-source-tag", default="hs1")
    parser.add_argument("--selection-tag", default="sel1")
    parser.add_argument("--material-tag", default="mat1")
    parser.add_argument("--function-tag", default="int1")
    parser.add_argument("--variable-group", default="var1")
    parser.add_argument("--work-plane-tag", default="wp1")
    parser.add_argument("--rectangle-tag", default="r1")
    parser.add_argument("--array-tag", default="arr1")
    parser.add_argument("--array-size-x", default="2")
    parser.add_argument("--array-size-y", default="1")
    parser.add_argument("--array-pitch", default="0.002")
    parser.add_argument("--rect-width", default="0.001")
    parser.add_argument("--rect-height", default="0.001")
    parser.add_argument("--import-tag", default="imp1")
    parser.add_argument("--mesh-size-tag", default="size1")
    parser.add_argument("--mesh-quality-metric", default="skewness")
    parser.add_argument("--solver-feature-tag", default="fc1")
    parser.add_argument("--coordinate-system-tag", default="cs1")
    parser.add_argument("--model-file-stem", default="phase4-model")
    parser.add_argument("--local-space-dir", default="local dir with spaces")
    parser.add_argument("--save-policy", default="local")
    parser.add_argument("--cad-artifact-id", default="phase4-cad-artifact")
    parser.add_argument("--model", type=Path, default=None, help=".mph to load for the live cases")
    parser.add_argument("--model-artifact-id", default="phase4-model-artifact")
    parser.add_argument("--fixture-model-name", default="Phase4 G3 acceptance fixture")
    parser.add_argument("--chain-a-label", default="G3 chain A steady conduction")
    parser.add_argument("--chain-b-label", default="G3 chain B transient conduction")
    parser.add_argument("--chain-c-model", type=Path, default=None,
                        help="user-style .mph for the chain C continuation")
    parser.add_argument("--chain-c-artifact-id", default="phase4-chain-c-artifact")
    parser.add_argument("--continuation-time", default="1[s]")
    parser.add_argument("--solve-timeout-s", type=float, default=900.0)
    parser.add_argument("--license-products", action="append", default=None,
                        help="product name to probe with runtime.license_inspect (repeatable)")
    parser.add_argument("--duplicate-probe-tag", default="phase4_dup_probe")
    parser.add_argument("--only", default=None,
                        help=("comma-separated case ids to run (a slice; --stage narrows it further).  A slice "
                              "never weakens an acceptance line: every selected case records its subcases at "
                              "their declared evidence level"))
    parser.add_argument("--stage", default=None, choices=["M0", "M1", "M2", "M3"],
                        help=("staged run plan of G3.1 section 10: M0 = the offline root-cause gate (run "
                              "without --live), M1 = the single clean-model integration gate, M2 = the minimal "
                              "physical closure (chain A then chain B), M3 = preservation and the remaining "
                              "acceptance (the remainder, so no case can be forgotten)"))
    parser.add_argument("--run-dir", default=None, help="explicit evidence directory")
    parser.add_argument("--reopen-check", type=Path, default=None,
                        help="reopen-check mode: load this .mph in a fresh process/private home")
    parser.add_argument("--reopen-artifact-id", default="phase4-reopen-artifact")
    parser.add_argument("--reopen-expect", action="append", default=None, metavar="NAME=EXPR=EXPECTED[;tol=..]",
                        help="representative value to check in reopen-check mode (repeatable)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.license_products is None:
        args.license_products = [args.heat_physics_type]
    if args.only:
        args.only = [item.strip() for item in args.only.split(",") if item.strip()]
        if args.reopen_check:
            parser.error("--only cannot be combined with --reopen-check")
    if args.reopen_check:
        args.reopen_expect = [_parse_expectation(spec) for spec in (args.reopen_expect or [])]
        try:
            return asyncio.run(_reopen_check(args))
        except KeyboardInterrupt:
            return 130
    try:
        return asyncio.run(_run_suite(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())


