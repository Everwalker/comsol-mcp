"""Write-point unit-dimension compatibility for physics-feature properties.

Why this module exists
----------------------
``physics.feature_create``/``physics.feature_update`` write the caller's COMSOL
expression text verbatim (this layer never converts units or evaluates an
expression).  A *wrong-dimensional* expression therefore used to be accepted in
silence: live evidence
``evidence/phase4_1/runs/20260920T235502Z-g3_1-m1/cases/W13_T015_units`` shows a
``1e5[W/m^2]`` expression written into the volumetric heat source ``Q0`` of a
``HeatSource`` feature (documented SI unit ``W/m^3``) with ``status: APPLIED``
and no warning at all.

The check implemented here compares the **SI dimension** of the unit the caller
declared in the expression text against the dimension the local COMSOL 6.4
corpus documents for that (feature type, property name) write point.  It is
deliberately narrow and fail-open *only* where no verified statement exists:

* a write point without a verified table row is never checked (an unknown
  feature type or property name cannot produce a refusal);
* an expression whose unit cannot be resolved (no ``[...]`` declaration, a
  symbol outside the verified unit vocabulary, an expression-only text such as
  ``k(T)`` or ``Q0_ref``) is recorded as ``NOT_DECLARED``/``UNRESOLVED`` and is
  **not** refused -- the layer does not guess a dimension for it;
* only a *proven* dimension mismatch is refused, and it is refused **before the
  first write** with the ``UNIT_DIMENSION_MISMATCH`` code, because the write
  contract of this layer is fail-closed: writing a documented-incompatible
  quantity into a physics property would silently corrupt the model semantics.

Every table row cites the local COMSOL 6.4 document (path, PDF page/chunk and
sha256 of the indexed source) that states the property's SI unit, so a reviewer
can re-read the statement instead of trusting this table.

Vocabulary provenance
---------------------
The unit symbols below are the ones the COMSOL documentation uses for these
properties.  A symbol that is not in the table is *unknown*, never assumed to
be dimensionless.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# SI dimension algebra
# ---------------------------------------------------------------------------

#: (mass, length, time, temperature, electric current, amount, luminous intensity)
DIMENSIONLESS: tuple[int, ...] = (0, 0, 0, 0, 0, 0, 0)
_M, _L, _T, _THETA, _I, _N, _J = range(7)


def _dim(**powers: int) -> tuple[int, ...]:
    base = [0] * 7
    for name, exponent in powers.items():
        base[{"m": _M, "l": _L, "t": _T, "theta": _THETA, "i": _I, "n": _N, "j": _J}[name]] = exponent
    return tuple(base)


#: The verified unit vocabulary.  Every entry is a *dimension* only: prefixes are
#: dimensionless factors and are stripped separately, and offset units (``degC``)
#: are listed because their dimension is a temperature even though this layer
#: never converts an offset value.
UNIT_DIMENSIONS: dict[str, tuple[int, ...]] = {
    # dimensionless
    "1": DIMENSIONLESS, "rad": DIMENSIONLESS, "%": DIMENSIONLESS, "deg": DIMENSIONLESS,
    # base units
    "kg": _dim(m=1), "g": _dim(m=1),
    "m": _dim(l=1),
    "s": _dim(t=1), "min": _dim(t=1), "h": _dim(t=1), "hr": _dim(t=1),
    "K": _dim(theta=1), "degC": _dim(theta=1), "°C": _dim(theta=1),
    "A": _dim(i=1), "mol": _dim(n=1), "cd": _dim(j=1),
    # derived units used by the verified write points
    "N": _dim(m=1, l=1, t=-2),
    "Pa": _dim(m=1, l=-1, t=-2),
    "J": _dim(m=1, l=2, t=-2),
    "W": _dim(m=1, l=2, t=-3),
    "Hz": _dim(t=-1),
}

#: SI decimal prefixes as a *dimensionless* factor.  Longest first so ``da``/``m``
#: style ambiguity is resolved deterministically.
_PREFIXES: tuple[str, ...] = (
    "da", "y", "z", "a", "f", "p", "n", "u", "µ", "m", "c", "d", "h", "k", "M", "G", "T", "P", "E",
)


class UnitSyntaxError(ValueError):
    """The unit text is not in the verified unit grammar."""


def _token_dimension(token: str) -> tuple[int, ...]:
    """Dimension of a single unit token; raises when it is not in the vocabulary."""
    clean = token.strip()
    if not clean:
        raise UnitSyntaxError("empty unit token")
    if clean in UNIT_DIMENSIONS and UNIT_DIMENSIONS[clean] is not None:
        return UNIT_DIMENSIONS[clean]
    # ``1``-style dimensionless spellings
    if clean in {"1", "-"}:
        return DIMENSIONLESS
    for prefix in _PREFIXES:
        if not clean.startswith(prefix) or len(clean) == len(prefix):
            continue
        rest = clean[len(prefix):]
        if rest in UNIT_DIMENSIONS and UNIT_DIMENSIONS[rest] is not None:
            # A prefix scales the unit; it never changes its dimension.  This is
            # what makes ``mW/m^2`` (milliwatt per square metre) comparable with
            # ``W/m^2`` while ``mm`` still reads as a length.
            return UNIT_DIMENSIONS[rest]
    raise UnitSyntaxError(f"unit symbol {token!r} is not in the verified vocabulary")


def unit_dimension(text: str) -> tuple[int, ...]:
    """Dimension of a unit expression such as ``W/m^3`` or ``kg/(m*s^2)``.

    Grammar: ``*``, ``.`` or whitespace multiply, ``/`` divides the next factor,
    ``(``/``)`` group, ``^<int>`` and a bare trailing integer are exponents.
    Raises :class:`UnitSyntaxError` for anything outside this grammar, so an
    unparsable unit can never be mistaken for a known dimension.
    """
    if not isinstance(text, str) or not text.strip():
        raise UnitSyntaxError("empty unit text")
    tokens = _tokenize(text)
    dimension, position = _parse_product(tokens, 0, DIMENSIONLESS)
    if position != len(tokens):
        raise UnitSyntaxError(f"unit text {text!r} has trailing tokens at {position}")
    return dimension


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    current = ""
    index = 0
    while index < len(text):
        char = text[index]
        if char in "*·." or char.isspace():
            if current:
                out.append(current)
                current = ""
            out.append("*")
            index += 1
            continue
        if char in "()/^":
            if current:
                out.append(current)
                current = ""
            out.append(char)
            index += 1
            continue
        current += char
        index += 1
    if current:
        out.append(current)
    # Documented renderings such as ``W/m3`` print the exponent as a bare digit
    # (see the COMSOL 6.4 symbol tables).  Split ``m3`` into ``m`` + ``3`` so the
    # exponent branch of the parser sees it; a token that is only digits is left
    # alone (it is not a unit symbol either way).
    split: list[str] = []
    for token in out:
        match = re.fullmatch(r"([A-Za-zµ°%]+)([0-9]+)", token)
        if match is not None:
            split.extend([match.group(1), match.group(2)])
        else:
            split.append(token)
    # collapse repeated multiplication markers
    collapsed: list[str] = []
    for token in split:
        if token == "*" and collapsed and collapsed[-1] == "*":
            continue
        collapsed.append(token)
    return collapsed


def _parse_product(tokens: Sequence[str], position: int, base: tuple[int, ...]) -> tuple[tuple[int, ...], int]:
    dimension = base
    pending_divide = False
    while position < len(tokens):
        token = tokens[position]
        if token == ")":
            break
        if token in {"*", "/"}:
            pending_divide = token == "/"
            position += 1
            continue
        if token == "(":
            inner, position = _parse_product(tokens, position + 1, DIMENSIONLESS)
            if position >= len(tokens) or tokens[position] != ")":
                raise UnitSyntaxError("unbalanced parenthesis in unit text")
            position += 1
        else:
            inner = _token_dimension(token)
            position += 1
        exponent = 1
        if position < len(tokens):
            nxt = tokens[position]
            if nxt == "^":
                if position + 1 >= len(tokens):
                    raise UnitSyntaxError("dangling exponent in unit text")
                try:
                    exponent = int(tokens[position + 1])
                except ValueError as exc:
                    raise UnitSyntaxError(f"non-integer exponent {tokens[position + 1]!r}") from exc
                if exponent == 0:
                    raise UnitSyntaxError("exponent 0 is not a verified unit spelling")
                position += 2
            elif nxt.isdigit():
                # Documented renderings such as ``W/m3`` use a bare exponent.
                exponent = int(nxt)
                position += 1
        if pending_divide:
            pending_divide = False
            inner = tuple(-power for power in inner)
        if exponent != 1:
            inner = tuple(power * exponent for power in inner)
        dimension = tuple(left + right for left, right in zip(dimension, inner))
    return dimension, position


def dimension_label(dimension: Sequence[int]) -> str:
    """Human-readable label of a dimension vector (evidence/debug only)."""
    names = ("kg", "m", "s", "K", "A", "mol", "cd")
    parts = [f"{name}^{power}" if power != 1 else name
             for name, power in zip(names, dimension) if power]
    return "·".join(parts) if parts else "1"


# ---------------------------------------------------------------------------
# expression text -> declared dimension
# ---------------------------------------------------------------------------

def declared_unit_text(expression: str) -> str | None:
    """The ``[...]`` unit declaration of a COMSOL expression, or ``None``.

    More than one declaration is multiplied (``10[cm]*2[1/min]``), which is the
    only composition the documented expression grammar of these properties uses.
    """
    if not isinstance(expression, str) or not expression.strip():
        return None
    groups: list[str] = []
    depth = 0
    current = ""
    for char in expression:
        if char == "[":
            depth += 1
            if depth == 1:
                current = ""
                continue
        elif char == "]":
            depth -= 1
            if depth == 0:
                groups.append(current)
                continue
        if depth:
            current += char
    if not groups:
        return None
    return "*".join(group for group in groups if group.strip()) or None


def expression_dimension(expression: str) -> tuple[int, ...]:
    """Dimension the expression text declares; raises when it cannot be resolved."""
    declared = declared_unit_text(expression)
    if declared is None:
        raise UnitSyntaxError("the expression declares no unit")
    return unit_dimension(declared)


def expression_text_of(value: Any) -> str | None:
    """The scalar expression text of a property value, or ``None``.

    Accepts the published shapes: a plain string, ``{"value": <...>}`` and a
    typed ``{"kind": "expression"|"string", "shape": [], "data": <str>}``.
    Anything else (numbers, arrays, matrices, boolean enumerations) is not an
    expression text and returns ``None``.
    """
    if isinstance(value, str):
        return value
    if not isinstance(value, Mapping):
        return None
    kind = value.get("kind")
    if isinstance(kind, str):
        shape = value.get("shape")
        data = value.get("data")
        if kind in {"expression", "string"} and (shape in (None, []) or shape == ()) and isinstance(data, str):
            return data
        return None
    if "value" in value and len(value) <= 3:
        return expression_text_of(value["value"])
    return None


# ---------------------------------------------------------------------------
# verified write points
# ---------------------------------------------------------------------------

_POWER_PER_VOLUME = _dim(m=1, l=-1, t=-3)
_POWER_PER_AREA = _dim(m=1, l=0, t=-3)

_RELMANUAL = "doc/pdf/COMSOL_Multiphysics/COMSOL_ReferenceManual.pdf"
_HT_GUIDE = "doc/pdf/Heat_Transfer_Module/HeatTransferModuleUsersGuide.pdf"

#: Every write point of this layer is written *verbatim*: the expression text is handed to the
#: engine unchanged and read back unchanged, and this layer only compares the expression's declared
#: dimension with the documented dimension of the write point.  It never multiplies a value by a
#: thickness, an absorptivity or any other implicit factor, and never converts a unit -- so a value
#: whose dimension does not fit the documented write point is refused (a proven mismatch) instead of
#: being re-scaled into it.  T015's acceptance line "不自动乘厚度或吸收率" is exactly this property,
#: and it is published with every unit-check record so the reply cannot be read either way.
VERBATIM_WRITE_POLICY = (
    "the expression text is written unchanged and read back unchanged; this layer compares the declared "
    "dimension with the documented dimension of the write point and never multiplies a value by a "
    "thickness, an absorptivity or any other implicit factor"
)

#: The documented readings of one property are entity-dimension specific; this layer claims only the
#: reading its row cites.  ``HeatSource.Q0`` is the case T015 probes: the Reference Manual's sentence
#: quoted in the row's evidence ("as the heat per unit volume, as a linear heat source, or as a heat
#: rate") documents a *per-unit-volume* reading for a domain, a per-length reading for an edge and a
#: heat-rate reading for a point.  The row below claims the domain reading only, so an area-unit
#: expression on it is a mismatch -- never a silent multiplication by a thickness (which is the factor
#: that would be needed to turn a per-volume value into a surface one).
_DIMENSION_DEPENDENT_NOTE = (
    "this write point claims the documented domain reading only; the same property has other documented "
    "readings for other entity dimensions (linear heat source, heat rate), and none of them is converted "
    "into this one"
)

#: ``{feature type_id: {property name: row}}``.  A row exists only where the
#: local COMSOL 6.4 corpus states the property's SI unit *and* the property name
#: is the one this layer writes.  There is deliberately no row for, for example,
#: the boundary heat source feature: the documented notation there is ``Qb``
#: while the settings label is ``q0``/``Q0`` depending on the module, so the
#: (type, name) pair is not verified and the vocabulary must not guess it.
UNIT_WRITE_POINTS: dict[str, dict[str, dict[str, Any]]] = {
    "HeatSource": {
        "Q0": {
            "dimension": _POWER_PER_VOLUME,
            "si_unit": "W/m^3",
            "meaning": "domain heat source, heat per unit volume",
            "documented_entity": "domain",
            "dimension_note": _DIMENSION_DEPENDENT_NOTE,
            "evidence": (
                "COMSOL 6.4 Reference Manual, PDF p.1344: \"Heat Source ... Specify Q0 as the heat per "
                f"unit volume, as a linear heat source, or as a heat rate\" ({_RELMANUAL}; sha256 "
                "3dacc33243911a98cbcac19c6b83c9b662dd0b7c56b880b3a9e6a2ab602e7106). "
                "COMSOL 6.4 - Heat Source: \"Enter a value for the heat source Q (SI unit: W/m3)\" "
                "(doc/help/wtpwebapps/ROOT/doc/com.comsol.help.cfd/cfd_ug_fluidflow_high_mach.08.30.html; "
                "sha256 8f684ce1845990638b65c7d3be5edf9afb53fd663dee5b3fca1b17a536650c1b). "
                "Heat Transfer Module User's Guide, PDF p.59 symbol table: \"Qs W/m3 Heat source in solid "
                f"phase\", \"Qtot W/m3 Total domain heat source\" ({_HT_GUIDE}; sha256 "
                "e5480ba9e344c864d82038bff320da715c3650989ff6b4d2d9e45aab65f5344b)."
            ),
            "property_name_evidence": (
                "live: evidence/phase4_1/runs/20260920T235502Z-g3_1-m1/cases/W13_T015_units "
                "(physics.feature_create on HeatSource wrote and read back Q0)"
            ),
        }
    },
    "HeatFluxBoundary": {
        "q0": {
            "dimension": _POWER_PER_AREA,
            "si_unit": "W/m^2",
            "meaning": "inward heat flux on the selected boundary",
            "documented_entity": "boundary",
            "dimension_note": (
                "this write point claims the documented boundary reading only: the value is a surface "
                "density applied on the selected boundary exactly as written; no thickness and no "
                "absorptivity factor is introduced, and the volume reading of a domain source is refused "
                "rather than converted"
            ),
            "evidence": (
                "COMSOL 6.4 Reference Manual, PDF p.1368: \"q0 is the inward heat flux (SI unit: W/m2), "
                f"normal to the boundary\" ({_RELMANUAL}; sha256 "
                "3dacc33243911a98cbcac19c6b83c9b662dd0b7c56b880b3a9e6a2ab602e7106). "
                "Heat Transfer Module User's Guide, PDF p.94 repeats the same statement, and its PDF p.59 "
                f"symbol table lists \"qtot W/m2 Total heat flux\" ({_HT_GUIDE}; sha256 "
                "e5480ba9e344c864d82038bff320da715c3650989ff6b4d2d9e45aab65f5344b)."
            ),
            "property_name_evidence": (
                "documented settings label (Reference Manual p.1368 \"It adds q0 to the total flux ... "
                "Enter a value for q0\"); not yet live-verified on this build, so a mismatch is refused "
                "with that limit recorded in the refusal details"
            ),
        }
    },
}

MISMATCH = "MISMATCH"
COMPATIBLE = "COMPATIBLE"
NOT_DECLARED = "NOT_DECLARED"
UNRESOLVED = "UNRESOLVED"
NOT_VERIFIED = "NOT_VERIFIED"


def write_point(type_id: Any, name: Any) -> dict[str, Any] | None:
    """The verified row for a (feature type, property) pair, if any."""
    if not isinstance(type_id, str) or not isinstance(name, str):
        return None
    return (UNIT_WRITE_POINTS.get(type_id) or {}).get(name)


def unit_check(type_id: Any, name: Any, value: Any) -> dict[str, Any] | None:
    """Check one property value against its write point's documented dimension.

    Returns a JSON-safe evidence record (``None`` when the write point is not in
    the verified table), with ``status`` one of :data:`COMPATIBLE`,
    :data:`MISMATCH`, :data:`NOT_DECLARED` or :data:`UNRESOLVED`.  Only
    :data:`MISMATCH` is a refusal; nothing here modifies the value.
    """
    row = write_point(type_id, name)
    if row is None:
        return None
    record: dict[str, Any] = {
        "write_point": f"{type_id}.{name}",
        "property": name,
        "feature_type": type_id,
        "feature_dimension": dimension_label(row["dimension"]),
        "documented_si_unit": row["si_unit"],
        "documented_entity": row.get("documented_entity"),
        "dimension_note": row.get("dimension_note"),
        "evidence": row["evidence"],
        # T015 publishes the implicit-factor answer *with every* record, whatever the verdict is:
        # no thickness and no absorptivity factor is applied to the value, and this layer never
        # converts one documented reading into another.  A reply without this block would leave the
        # acceptance line unanswerable, so it is not optional.
        "verbatim": True,
        "implicit_factors": {
            "applied": False,
            "thickness": False,
            "absorptivity": False,
            "policy": VERBATIM_WRITE_POLICY,
        },
    }
    text = expression_text_of(value)
    if text is None:
        record["status"] = NOT_VERIFIED
        record["observed"] = type(value).__name__
        record["reason"] = ("the value is not a scalar expression text, so no unit declaration can be read "
                            "from it; this write point is left unchecked rather than guessed")
        return record
    record["expression"] = text
    declared = declared_unit_text(text)
    if declared is None:
        record["status"] = NOT_DECLARED
        record["reason"] = ("the expression declares no [unit]; the engine resolves it against the "
                            "property's own unit and this layer never invents one")
        return record
    record["declared_unit"] = declared
    try:
        observed = expression_dimension(text)
    except UnitSyntaxError as exc:
        record["status"] = UNRESOLVED
        record["reason"] = f"the declared unit could not be resolved: {exc}"
        return record
    record["declared_dimension"] = dimension_label(observed)
    if observed == row["dimension"]:
        record["status"] = COMPATIBLE
        return record
    record["status"] = MISMATCH
    record["expected_si_unit"] = row["si_unit"]
    record["reason"] = (f"the expression declares {declared!r} ({dimension_label(observed)}), but this write "
                        f"point is documented as {row['si_unit']} ({dimension_label(row['dimension'])})")
    return record


def write_point_vocabulary() -> dict[str, Any]:
    """The published vocabulary, for evidence/reporting (no engine access)."""
    return {
        "source": "local COMSOL 6.4 documentation corpus (see each row's evidence)",
        "rows": [
            {"feature_type": type_id, "property": name, "si_unit": row["si_unit"],
             "dimension": dimension_label(row["dimension"]), "meaning": row["meaning"],
             "documented_entity": row.get("documented_entity"),
             "dimension_note": row.get("dimension_note"),
             "implicit_factors": {"applied": False, "thickness": False, "absorptivity": False}}
            for type_id, properties in sorted(UNIT_WRITE_POINTS.items())
            for name, row in sorted(properties.items())
        ],
        "policy": ("only a proven dimension mismatch is refused, before the first write; an expression "
                   "without a resolvable [unit] is recorded as NOT_DECLARED/UNRESOLVED and is not refused"),
        "verbatim_policy": VERBATIM_WRITE_POLICY,
        "unverified_examples": [
            {"feature_type": "BoundaryHeatSource", "reason":
             "the documented notation is the variable Qb (SI unit: W/m2) while the settings label is not "
             "verified against this build, so no (type, property) row is published"},
        ],
    }


__all__ = [
    "COMPATIBLE",
    "DIMENSIONLESS",
    "MISMATCH",
    "NOT_DECLARED",
    "NOT_VERIFIED",
    "UNIT_DIMENSIONS",
    "UNIT_WRITE_POINTS",
    "UNRESOLVED",
    "VERBATIM_WRITE_POLICY",
    "UnitSyntaxError",
    "declared_unit_text",
    "dimension_label",
    "expression_dimension",
    "expression_text_of",
    "unit_check",
    "unit_dimension",
    "write_point",
    "write_point_vocabulary",
]
