"""W16 domain operations: mesh, study and solver.

Frozen publishing contract (the control plane dispatches through this):

    OPERATIONS: dict[str, Callable[[Any, str, dict], dict]]

Each entry is called as ``fn(worker, model_tag, arguments)``.  It returns the
operation's ``data`` dictionary on success and raises
``comsol_mcp._g2_contract.ExecutionContractError`` for a *pre-write* refusal or
validation failure.  It never wraps its own result in a ``{"success": ...}``
envelope: the control plane owns that.

Post-write verification is *data*: a dispatched call whose readback did not
confirm the change returns ``status``/``partial_change``/
``execution_state_unknown`` plus the per-step ``applied``/``failed``/
``not_executed`` lists, exactly like the W13 domain modules.  Nothing is
retried silently and a build/solve whose state cannot be read back is reported
as unverified with the precise worker allow-list entry that is missing.

API provenance (COMSOL 6.4.0.293, installed at /Applications/COMSOL64)
--------------------------------------------------------------------
Every COMSOL method name and every type string used below is verified offline
against one of two local sources:

* ``javap`` of the installed public API jar
  ``/Applications/COMSOL64/Multiphysics/apiplugins/com.comsol.api_1.0.0.jar``
  and of the implementation jar
  ``/Applications/COMSOL64/Multiphysics/plugins/com.comsol.model_1.0.0.jar``.
* The local COMSOL 6.4 documentation corpus
  ``/Users/everwalker/Documents/KnowledgeBases/COMSOL-6.4-KB`` - the per-type
  property tables and the documented call sequences.  The ``comsol_api_mesh.49.*``
  and ``comsol_api_solver.51.*`` help pages carry the exact ``create`` strings.

op -> COMSOL API -> verification source
---------------------------------------
mesh.list        ``component.mesh().tags()`` + per-sequence ``geom()``/
                 ``isAutomatic()``/``current()``/``isComplete()`` (component
                 accessor: javap ``ModelNode.mesh()``).
mesh.create      ``component.mesh().create(<mtag>,<gtag>)`` -> Programming
                 Reference "Adding a Meshing Sequence"
                 (``comsol_api_mesh.49.004``); the physics-/user-controlled
                 switch is ``mesh.automatic(boolean)`` + ``isAutomatic()``
                 (``comsol_api_mesh.49.021`` "Physics-Controlled Meshing").
mesh.inspect     ``mesh(...).feature().tags()``/``getType()``/``status()``/
                 ``message()`` recursion (javap ``MeshFeature`` /
                 ``GeomMeshFeature``; status vocabulary
                 ``comsol_api_mesh.49.010``).
mesh.feature_create ``mesh(<tag>).create(<ftag>,<ftype>)`` and the attribute
                 form ``mesh(<tag>).feature(<ftag>).create(<ftag1>,<ftype>)``
                 (``comsol_api_mesh.49.005``); ``ftype`` is checked against
                 ``MESH_FEATURE_TYPE_IDS``.
mesh.feature_update ``PropFeature.set(property,value)`` through the shared G2
                 property-set discipline (R03 typed readback).
mesh.feature_remove ``mesh(<tag>).feature().remove(<ftag>)``
                 (``comsol_api_mesh.49.011``; javap ``MeshFeatureList``).
mesh.build       ``mesh(<tag>).run()`` / ``mesh(<tag>).run(<ftag>)``
                 ("builds all features up to (and including) the feature
                 <ftag>", ``comsol_api_mesh.49.007``).
mesh.clear       ``mesh(<tag>).clearMesh()`` - "clears the built mesh while
                 keeping all features and settings"
                 (``comsol_api_mesh.49.013``).  The destructive
                 ``feature().clear()`` is deliberately *not* reachable here.
mesh.statistics  ``mesh(<tag>).stat()`` -> ``getNumElem()/getNumVertex()/
                 getTypes()/getMinQuality()/getMeanQuality()/getVolume()/
                 getMaxDimension()/getQualityMeasure()`` and the sequence-level
                 equivalents (javap ``MeshStatistics``; ``comsol_api_mesh.49.026``
                 /``49.027``/``49.029``).
mesh.quality     ``stat().getQualityMeasure()/setQualityMeasure(String)`` +
                 ``getQualityDistr(int)`` with the eight documented measures
                 (javap ``MeshStatistics.setQualityMeasure`` javadoc and Table
                 4-5 of ``comsol_api_mesh.49.026``).
mesh.validate    composition of the verified statistics/status reads above;
                 a criterion this layer cannot evaluate is reported
                 ``NOT_EVALUATED`` instead of being counted as a pass.
study.list       ``model.study().tags()`` + ``study(<tag>).feature().tags()``
                 and the solver association read through
                 ``model.sol(<tag>).study()`` (javap ``StudyList``/``Study``/
                 ``StudyFeatureList``/``SolverSequence.study()``).
study.create     ``model.study().create(<tag>)`` (``comsol_api_solver.51.59``
                 "Studies and Study Steps").
study.inspect    ``Study``/``StudyFeature`` accessors: ``feature().tags()``,
                 ``type()``, ``getSolverSequences``-equivalent association read,
                 ``isGenPlots``/``isStoreSolution`` (javap).
study.remove     ``model.study().remove(<tag>)`` (javap ``StudyList``) with an
                 explicit attached-solver policy driven by
                 ``model.sol(<tag>).study()`` and ``model.sol().remove(<tag>)``.
study.step_create ``model.study(<tag>).create(<ftag>,<type>)`` with the type
                 vocabulary and per-type property tables of
                 ``comsol_api_solver.51.59-83``.
study.step_update ``StudyFeature.set(property,value)`` (property tables of the
                 step's own type).
study.step_remove ``study(<tag>).feature().remove(<ftag>)`` (javap
                 ``StudyFeatureList``).
study.physics_activation ``StudyFeature.setSolveFor(<entityPath>,boolean)`` /
                 ``solveFor(<entityPath>)`` (documented in
                 ``comsol_api_solver.51.59`` and Table 6-87) and the documented
                 ``activateCoupling`` String Map through the R03 keyed-entry
                 path.
study.solver_generate ``study(<tag>).createAutoSequences(<type>)`` with
                 ``<type>`` in ``all|jobs|sol`` (``comsol_api_general.47.60``).
study.run        ``study(<tag>).run()`` (``comsol_api_general.47.60``); the
                 post-run evidence is the study's own
                 ``getLastComputationTime()/Date()/Version()`` (javap).
solver.list      ``model.sol().tags()`` + ``study()``/``isEmpty()``/
                 ``isInitialized()``/``getDefaultSolnum()`` (javap
                 ``SolverSequenceList``/``SolverSequence``).
solver.inspect   recursive ``feature().tags()``/``getType()`` read plus the
                 ``problem()`` node tags (javap ``SolverFeature``).
solver.create    ``model.sol().create(<tag>[,<studytag>])``
                 (``comsol_api_general.47.58``).
solver.feature_create ``sol(<tag>).create(<ftag>,<oper>)`` or the nested
                 ``...feature(<ftag>).create(<f2tag>,<oper>)`` (javap + the
                 per-type pages ``comsol_api_solver.51.17-58``).
solver.feature_update ``SolverFeature.set(property,value)`` (the feature's own
                 documented table, e.g. Table 6-70 for a Stationary feature and
                 Table 6-74 for a Time feature).
solver.feature_remove ``sol(<tag>).feature().remove(<ftag>)`` (javap
                 ``SolverFeatureList``).
solver.run       ``runAll()`` / ``run(<ftag>)`` / ``runFromTo(<a>,<b>)``
                 (``comsol_api_general.47.58``; javap ``SolverSequence``), with
                 the solution-state readbacks ``isEmpty()``/``isInitialized()``/
                 ``getDefaultSolnum()``.

Known, reported gaps (refused before the first write, never guessed)
--------------------------------------------------------------------
* ``mesh.import``/``mesh.export``/``mesh.convergence_study``,
  ``study.initial_solution_set``/``study.sweep_manage``,
  ``solver.solution_inspect``/``solution_clear``/``solution_transfer``/
  ``log_read``/``resource_configure`` are *not* part of the W16 scope and are
  not published by this module.
* The quality measure ``custom`` needs a user quality expression that this
  layer never sets, so it is refused instead of silently evaluating the
  default measure.
* A mesh-quality *worst element location* and a mesh-only DOF count have no
  verified offline read path; both are reported as ``NOT_AVAILABLE`` with the
  reason rather than estimated.
* Several worker allow-list entries are still missing for this domain
  (``clearMesh``/``stat``/``getNumElem``/...).  A read that the worker refuses
  is reported with ``allowlist_entry_required`` and never turned into an empty
  or successful value.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Sequence

from ._g2_contract import ExecutionContractError, NodePath
from ._g2_engine import _call, property_set
from ._g3_common import (
    ENVELOPE_FIELDS,
    apply_local_selection,
    bound_model,
    call_probe,
    child_node,
    definition_properties,
    describe_engine_failure,
    node_type,
    operation_arguments,
    property_definition,
    property_rows,
    quantity,
    require_bool,
    require_int,
    require_mapping,
    require_string,
    resolve_path,
    selection_state,
    split_parent_path,
    tag_list,
    validate_selection_spec,
    validate_tag,
)

# ---------------------------------------------------------------------------
# Verified type vocabularies
# ---------------------------------------------------------------------------

#: ``component.mesh().create(<tag>,<gtag>)`` accepts two arguments; a mesh
#: sequence therefore has arity 2 in the wire ``NodePath`` sense (component +
#: sequence tag) and is *created*, never "typed".  The sequence type argument
#: of ``MeshList.create(String,String,String)`` (``Sequence``/``Geometry``,
#: documented in the MeshList API page) is deliberately not exposed: W16 only
#: creates geometry-conforming meshes through the documented two-argument
#: ``ComponentMeshList.create(String, String)`` form.
MESH_SEQUENCE_CREATE_ARITY = 2

#: ``mesh(<tag>).create(<ftag>,<ftype>)`` type strings, from the COMSOL 6.4
#: Meshing API chapter (``comsol_api_mesh.49.*``): Table 4-1 "Available Mesh
#: Operations" and Table 4-2 "Available Mesh Attributes" of
#: ``comsol_api_mesh.49.002`` plus the per-type command pages 49.052-49.107.
MESH_FEATURE_TYPE_SOURCES: dict[str, str] = {
    "Adapt": "comsol_api_mesh.49.052 (Table 4-1)",
    "AdjacentSelection": "comsol_api_mesh.49.053 (mesh selection type)",
    "Ball": "comsol_api_mesh.49.054 (Table 4-1)",
    "BallSelection": "comsol_api_mesh.49.055",
    "BndLayer": "comsol_api_mesh.49.056 (Table 4-1)",
    "BndLayerProp": "comsol_api_mesh.49.057 (Table 4-2)",
    "Box": "comsol_api_mesh.49.058 (Table 4-1)",
    "BoxSelection": "comsol_api_mesh.49.055",
    "CollapseEntities": "comsol_api_mesh.49.059 (Table 4-1)",
    "ComplementSelection": "comsol_api_mesh.49.106",
    "Convert": "comsol_api_mesh.49.060 (Table 4-1)",
    "Copy": "comsol_api_mesh.49.064 (Table 4-1)",
    "CopyDomain": "comsol_api_mesh.49.063 (Table 4-1)",
    "CopyEdge": "comsol_api_mesh.49.061 (Table 4-1)",
    "CopyFace": "comsol_api_mesh.49.062 (Table 4-1)",
    "CornerProp": "comsol_api_mesh.49.065",
    "CornerRefinement": "comsol_api_mesh.49.066 (Table 4-2)",
    "CreateDomains": "comsol_api_mesh.49.067 (Table 4-1)",
    "CreateEdges": "comsol_api_mesh.49.068 (Table 4-1)",
    "CreateFaces": "comsol_api_mesh.49.069 (Table 4-1)",
    "CreateVertices": "comsol_api_mesh.49.070 (Table 4-1)",
    "Cylinder": "comsol_api_mesh.49.071 (Table 4-1)",
    "CylinderSelection": "comsol_api_mesh.49.055",
    "DeleteEntities": "comsol_api_mesh.49.072 (Table 4-1)",
    "DetectFaces": "comsol_api_mesh.49.073 (Table 4-1)",
    "DifferenceSelection": "comsol_api_mesh.49.106",
    "DiskSelection": "comsol_api_mesh.49.055",
    "Distribution": "comsol_api_mesh.49.074 (Table 4-2)",
    "Edge": "comsol_api_mesh.49.075 (Table 4-1)",
    "EdgeGroup": "comsol_api_mesh.49.076 (Table 4-2)",
    "EdgeMap": "comsol_api_mesh.49.077 (Table 4-2)",
    "ExplicitSelection": "comsol_api_mesh.49.078",
    "FillHoles": "comsol_api_mesh.49.079 (Table 4-1)",
    "FixedMesh": "comsol_api_mesh.49.080",
    "FreeQuad": "comsol_api_mesh.49.081 (Table 4-1)",
    "FreeTet": "comsol_api_mesh.49.082 (Table 4-1)",
    "FreeTri": "comsol_api_mesh.49.083 (Table 4-1)",
    "IdenticalMesh": "comsol_api_mesh.49.084 (Table 4-2)",
    "Import": "comsol_api_mesh.49.085 (Table 4-1)",
    "Imprint": "comsol_api_mesh.49.086",
    "IntersectLine": "comsol_api_mesh.49.087 (Table 4-1)",
    "IntersectPlane": "comsol_api_mesh.49.088 (Table 4-1)",
    "IntersectionSelection": "comsol_api_mesh.49.106",
    "JoinEntities": "comsol_api_mesh.49.089 (Table 4-1)",
    "LogicalExpression": "comsol_api_mesh.49.090 (Table 4-1)",
    "Map": "comsol_api_mesh.49.091 (Table 4-1)",
    "MergeEntities": "comsol_api_mesh.49.092 (Table 4-1)",
    "OnePointMap": "comsol_api_mesh.49.093 (Table 4-2)",
    "Reference": "comsol_api_mesh.49.094 (Table 4-1)",
    "Refine": "comsol_api_mesh.49.095 (Table 4-1)",
    "RemeshDomains": "comsol_api_mesh.49.096",
    "RemeshEdges": "comsol_api_mesh.49.097",
    "RemeshFaces": "comsol_api_mesh.49.098",
    "Scale": "comsol_api_mesh.49.099 (Table 4-2)",
    "Size": "comsol_api_mesh.49.100 (Table 4-2, Table 4-36)",
    "SizeExpression": "comsol_api_mesh.49.101",
    "Sweep": "comsol_api_mesh.49.102 (Table 4-1)",
    "Transform": "comsol_api_mesh.49.103",
    "TwoPointMap": "comsol_api_mesh.49.104 (Table 4-2)",
    "Union": "comsol_api_mesh.49.105 (Table 4-1)",
    "UnionSelection": "comsol_api_mesh.49.106",
    "Vertex": "comsol_api_mesh.49.107 (Table 4-1)",
}

MESH_FEATURE_TYPE_IDS = frozenset(MESH_FEATURE_TYPE_SOURCES)

#: Documented property names, only for the types whose table was transcribed
#: from the corpus.  These tables are reported by ``mesh.inspect`` and used as
#: a *fallback* allow-list when the live node cannot answer ``properties()``;
#: the authoritative allow-list is the node's own engine metadata (see
#: ``_definition_payload``).
MESH_DOCUMENTED_PROPERTIES: dict[str, frozenset[str]] = {
    # Table 4-36 (comsol_api_mesh.49.100).  The properties ending in "active"
    # are not available for the default size feature (tag "size").
    "Size": frozenset({"custom", "hauto", "hcurve", "hcurveactive", "hgrad", "hgradactive",
                       "hmax", "hmaxactive", "hmin", "hminactive", "hnarrow", "hnarrowactive",
                       "table"}),
    # Table 4-59 (comsol_api_mesh.49.082).
    "FreeTet": frozenset({"method", "optlevel", "optcurved", "optlarge", "optsmall",
                          "smoothcontrol", "smoothmaxiter", "smoothmaxdepth",
                          "xscale", "yscale", "zscale"}),
    # comsol_api_mesh.49.083.
    "FreeTri": frozenset({"defectremoval", "method", "narrowreg", "simplifymesh",
                          "simplifytol", "smoothcontrol", "smoothmaxiter", "smoothmaxdepth",
                          "xscale", "yscale", "zscale"}),
    # comsol_api_mesh.49.081.  FreeQuad has no "narrowreg" in the documented table.
    "FreeQuad": frozenset({"defectremoval", "method", "simplifymesh", "simplifytol",
                           "smoothcontrol", "smoothmaxiter", "smoothmaxdepth",
                           "xscale", "yscale", "zscale"}),
    # comsol_api_mesh.49.056.
    "BndLayer": frozenset({"sharpcorners", "layerdec", "method", "smoothtransition",
                           "splitangle", "splitdivangle", "trimmaxangle", "trimminangle",
                           "smoothmaxiter", "smoothmaxdepth"}),
    # comsol_api_mesh.49.074 (the sub-tables by distribution type).
    "Distribution": frozenset({"type", "numelem", "equidistant", "explicit", "reverse"}),
    # comsol_api_mesh.49.102.
    "Sweep": frozenset({"remeshsourceface", "element", "mapinterpmethod", "smoothcontrol",
                        "smoothmaxiter", "smoothmaxdepth", "sourceface", "sweeppath",
                        "targetface", "targetmesh"}),
    # comsol_api_mesh.49.091.
    "Map": frozenset({"adjustedgdistr", "interpmethod", "smoothcontrol", "smoothmaxiter",
                      "smoothmaxdepth"}),
    # comsol_api_mesh.49.066.
    "CornerRefinement": frozenset({"boundary", "corner", "filter", "minangle", "refinement",
                                   "usefilter"}),
    # comsol_api_mesh.49.095.
    "Refine": frozenset({"rmethod", "numrefine", "facerep", "boxcoord"}),
    # comsol_api_mesh.49.060.
    "Convert": frozenset({"splitmethod"}),
}

#: ``stat().setQualityMeasure`` / ``getQualityMeasure`` vocabulary: the six
#: names in the javap javadoc of
#: ``com.comsol.model.MeshStatistics.setQualityMeasure`` plus the two the
#: manual's Table 4-5 additionally documents.  ``custom`` is refused by
#: ``mesh.quality`` because a custom measure needs an expression this layer
#: never sets.
QUALITY_MEASURES: frozenset[str] = frozenset(
    {"volcircum", "maxangle", "condition", "vollength", "growth", "skewness",
     "curvedskewness", "custom"}
)

#: Table 4-4 "Element Types" (comsol_api_mesh.49.026).
MESH_ELEMENT_TYPES: dict[str, int] = {
    "vtx": 0, "edg": 1, "tri": 2, "quad": 2, "tet": 3, "pyr": 3, "prism": 3, "hex": 3,
}

#: ``mesh.feature(<ftag>).status()`` - "built, warning, needs_rebuild, edited,
#: or error" (comsol_api_mesh.49.010 / Programming Reference "Getting Build
#: Status").
MESH_FEATURE_STATUS = ("built", "warning", "needs_rebuild", "edited", "error")

#: ``mesh(<tag>).automatic(boolean)`` (comsol_api_mesh.49.021): a sequence is
#: physics-controlled by default; ``automatic(false)`` switches to the
#: user-controlled state.
MESH_MODE_IDS: dict[str, bool] = {"physics_controlled": True, "user_controlled": False}

#: ``model.study(<tag>).create(<ftag>,<type>)`` type strings, transcribed from
#: the ``Syntax`` block of each COMSOL 6.4 study-step page
#: (``comsol_api_solver.51.59-83``).
STUDY_STEP_TYPE_SOURCES: dict[str, str] = {
    "Stationary": "comsol_api_solver.51.79",
    "Transient": "comsol_api_solver.51.81 (Time Dependent; create string is \"Transient\")",
    "TimeDiscrete": "comsol_api_solver.51.82",
    "Eigenvalue": "comsol_api_solver.51.67",
    "Eigenfrequency": "comsol_api_solver.51.66",
    "StationaryEigenfrequency": "comsol_api_solver.51.68",
    "Frequency": "comsol_api_solver.51.69",
    "Frequencylinearized": "comsol_api_solver.51.69 (Frequency-Domain Perturbation)",
    "Parametric": "comsol_api_solver.51.75",
    "Sensitivity": "comsol_api_solver.51.78",
    "ModelReduction": "comsol_api_solver.51.73",
    "FreqToTimeFFT": "comsol_api_solver.51.70",
    "TimeToFreqFFT": "comsol_api_solver.51.83",
    "FunctionSweep": "comsol_api_solver.51.71",
    "MaterialSweep": "comsol_api_solver.51.72",
    "RayTracing": "comsol_api_solver.51.76",
    "SchrodingerPoisson": "comsol_api_solver.51.77",
    "SurrogateModelTraining": "comsol_api_solver.51.80",
    "Batch": "comsol_api_solver.51.60",
    "BatchSweep": "comsol_api_solver.51.61",
    "ClusterComputing": "comsol_api_solver.51.64",
    "ClusterSweep": "comsol_api_solver.51.65",
    "BidirectionallyCoupledParticleTracing": "comsol_api_solver.51.62",
    "BidirectionallyCoupledRayTracing": "comsol_api_solver.51.63",
}

STUDY_STEP_TYPE_IDS = frozenset(STUDY_STEP_TYPE_SOURCES)

#: Study steps that generate a solver contract (the "defining" steps).  The
#: remaining published ids are study *extension* steps (Table 20-1 of the
#: Reference Manual: "There are some study steps that do not generate equations
#: and can only be used in combination with other study steps").
STUDY_DEFINING_STEPS = frozenset(
    {"Stationary", "Transient", "TimeDiscrete", "Eigenvalue", "Eigenfrequency",
     "Frequency", "Frequencylinearized", "StationaryEigenfrequency", "Sensitivity",
     "RayTracing", "SchrodingerPoisson", "SurrogateModelTraining", "ModelReduction"}
)

#: Documented study-step properties (Tables 6-167..6-174 / 6-175..6-182 /
#: 6-127.. of the solver chapter).  Only the properties common to the steps
#: this layer drives are listed; the authoritative allow-list is again the
#: node's own engine metadata.
STUDY_STEP_PROPERTIES: dict[str, frozenset[str]] = {
    "Stationary": frozenset({"geometricNonlinearity", "stol", "usestol", "plot", "plotgroup",
                             "probefreq", "probes", "probesel", "activate", "activateCoupling",
                             "activaterom", "discretization", "equationform", "reconstructors",
                             "useadvanceddisable", "initmethod", "initstudy", "manualsolnum",
                             "notlistsolnum", "notmanualsolnum", "notsolmethod", "notsolnum",
                             "notstudy", "nott", "outputmap", "outputselectionmap", "solnum",
                             "t", "useinitsol", "usesol"}),
    "Transient": frozenset({"tlist", "usertol", "rtol", "tunit", "plot", "plotgroup",
                            "plotfreq", "probefreq", "probes", "probesel", "activate",
                            "activateCoupling", "activaterom", "discretization", "equationform",
                            "reconstructors", "useadvanceddisable", "initmethod", "initstudy",
                            "manualsolnum", "notlistsolnum", "notmanualsolnum", "notsolmethod",
                            "notsolnum", "notstudy", "nott", "outputmap", "outputselectionmap",
                            "solnum", "t", "useinitsol", "usesol"}),
    "Frequency": frozenset({"loadparameters", "plist", "preusesol", "punit", "stol", "usestol",
                            "plot", "plotgroup", "probes", "probesel", "activate",
                            "activateCoupling", "activaterom", "discretization", "equationform",
                            "reconstructors", "useadvanceddisable", "initmethod", "initstudy",
                            "manualsolnum", "notlistsolnum", "notmanualsolnum", "notsolmethod",
                            "notsolnum", "notstudy", "nott", "outputmap", "outputselectionmap",
                            "solnum", "t", "useinitsol", "usesol"}),
    "Eigenvalue": frozenset({"neigs", "shift", "eigunit", "eigref", "useadvanceddisable",
                             "activate", "activateCoupling", "discretization",
                             "outputmap", "outputselectionmap"}),
    "Eigenfrequency": frozenset({"neigs", "shift", "eigunit", "eigref", "useadvanceddisable",
                                 "activate", "activateCoupling", "discretization",
                                 "outputmap", "outputselectionmap"}),
}

#: ``model.sol(<tag>).create(<ftag>,<oper>)`` type strings.  ``syntax-verified``
#: means the literal appears in the ``Syntax`` block of a
#: ``comsol_api_solver.51.*`` operation page; ``context-verified`` means the id
#: is declared in the shipped help ``contexts.xml`` (``<context id="sol_<Name>">``)
#: for COMSOL 6.4, i.e. it is a documented solver-feature context but its
#: ``create`` literal is not in the operation-page syntax list this layer read.
SOLVER_FEATURE_TYPE_SOURCES: dict[str, str] = {
    "Advanced": "comsol_api_solver.51.17 syntax",
    "Assemble": "comsol_api_solver.51.18 syntax",
    "AutoRemesh": "comsol_api_solver.51.19 syntax",
    "AWE": "comsol_api_solver.51.20 syntax",
    "CombineSolution": "comsol_api_solver.51.21 syntax",
    "CopySolution": "comsol_api_solver.51.22 syntax",
    "Eigenvalue": "comsol_api_solver.51.23 syntax",
    "EigenvalueParam": "comsol_api_solver.51.25 syntax",
    "FFT": "comsol_api_solver.51.26 syntax",
    "For": "comsol_api_solver.51.27 syntax",
    "EndFor": "comsol_api_solver.51.27 syntax",
    "FullyCoupled": "comsol_api_solver.51.28 syntax",
    "HardwareAcceleration": "comsol_api_solver.51.29 syntax",
    "Linear": "comsol_api_solver.51.31 syntax (LinearType family; use Direct/Iterative)",
    "Direct": "comsol_api_solver.51.31 syntax",
    "Iterative": "comsol_api_solver.51.31 syntax",
    "DomainDecomposition": "comsol_api_solver.51.31 syntax",
    "SAI": "comsol_api_solver.51.31 syntax",
    "LowerLimit": "comsol_api_solver.51.32 syntax",
    "LumpedStep": "comsol_api_solver.51.33 syntax",
    "Modal": "comsol_api_solver.51.34 syntax",
    "ModalReduction": "comsol_api_solver.51.35 syntax",
    "ModeFollowing": "comsol_api_solver.51.36 syntax",
    "Optimization": "comsol_api_solver.51.37 syntax",
    "Parametric": "comsol_api_solver.51.38 syntax",
    "PlugFlow": "comsol_api_solver.51.39 (page title PlugFlow)",
    "ProperOrthogonalDecomposition": "comsol_api_solver.51.40 syntax",
    "PreviousSolution": "comsol_api_solver.51.41 syntax",
    "Segregated": "comsol_api_solver.51.42 syntax",
    "SegregatedStep": "comsol_api_solver.51.43 syntax",
    "Sensitivity": "comsol_api_solver.51.44 syntax",
    "StatAcceleration": "comsol_api_solver.51.45 syntax",
    "StateSpace": "comsol_api_solver.51.46 syntax",
    "Stationary": "comsol_api_solver.51.47 syntax",
    "StopCondition": "comsol_api_solver.51.48 syntax",
    "StoreSolution": "comsol_api_solver.51.49 syntax",
    "StudyStep": "comsol_api_solver.51.50 syntax",
    "Time": "comsol_api_solver.51.51 syntax",
    "TimeAdaption": "comsol_api_solver.51.52 syntax",
    "TimeDiscrete": "comsol_api_solver.51.53 syntax",
    "TimeExplicit": "comsol_api_solver.51.54 syntax",
    "TimeParametric": "comsol_api_solver.51.55 syntax",
    "UpperLimit": "comsol_api_solver.51.56 syntax",
    "Variables": "comsol_api_solver.51.57 syntax",
    "Adaption": "comsol_api_solver.51 contexts.xml (sol_Adaption)",
    "AMS": "comsol_api_solver.51 contexts.xml (sol_AMS)",
    "ASAMGKrylovPreconditioner": "comsol_api_solver.51 contexts.xml",
    "AuxiliarySpaceAMG": "comsol_api_solver.51 contexts.xml",
    "AuxiliarySpaceAlgebraicMultigridCoarseSolver": "comsol_api_solver.51 contexts.xml",
    "AuxiliarySpaceAlgebraicMultigridPostSmoother": "comsol_api_solver.51 contexts.xml",
    "AuxiliarySpaceAlgebraicMultigridPreSmoother": "comsol_api_solver.51 contexts.xml",
    "BlockNavierStokes": "comsol_api_solver.51 contexts.xml",
    "CoarseSolver": "comsol_api_solver.51 contexts.xml",
    "ControlField": "comsol_api_solver.51 contexts.xml",
    "ControlState": "comsol_api_solver.51 contexts.xml",
    "DirectPreconditioner": "comsol_api_solver.51 contexts.xml",
    "DomainDecompositionCoarse": "comsol_api_solver.51 contexts.xml",
    "DomainDecompositionCoarseSolver": "comsol_api_solver.51 contexts.xml",
    "DomainDecompositionSchur": "comsol_api_solver.51 contexts.xml",
    "DomainDecompositionSchurSolver": "comsol_api_solver.51 contexts.xml",
    "ErrorEstimation": "comsol_api_solver.51 contexts.xml",
    "Field": "comsol_api_solver.51 contexts.xml",
    "HierarchicalLU": "comsol_api_solver.51 contexts.xml",
    "IncompleteLU": "comsol_api_solver.51 contexts.xml",
    "InputMatrix": "comsol_api_solver.51 contexts.xml",
    "Jacobi": "comsol_api_solver.51 contexts.xml",
    "KrylovPreconditioner": "comsol_api_solver.51 contexts.xml",
    "Multigrid": "comsol_api_solver.51 contexts.xml",
    "PostSmoother": "comsol_api_solver.51 contexts.xml",
    "PreSmoother": "comsol_api_solver.51 contexts.xml",
    "PressureSolver": "comsol_api_solver.51 contexts.xml",
    "SCGS": "comsol_api_solver.51 contexts.xml",
    "SOR": "comsol_api_solver.51 contexts.xml",
    "SORGauge": "comsol_api_solver.51 contexts.xml",
    "SORLine": "comsol_api_solver.51 contexts.xml",
    "SORVector": "comsol_api_solver.51 contexts.xml",
    "SchurDense": "comsol_api_solver.51 contexts.xml",
    "SchurKrylovPreconditioner": "comsol_api_solver.51 contexts.xml",
    "SchurLocal": "comsol_api_solver.51 contexts.xml",
    "SchurSolver": "comsol_api_solver.51 contexts.xml",
    "SchurSourceKrylovPreconditioner": "comsol_api_solver.51 contexts.xml",
    "SchurSourceSolver": "comsol_api_solver.51 contexts.xml",
    "SchurSpLocal": "comsol_api_solver.51 contexts.xml",
    "StationaryAttrib": "comsol_api_solver.51 contexts.xml",
    "State": "comsol_api_solver.51 contexts.xml",
    "TimeAttrib": "comsol_api_solver.51 contexts.xml",
    "Vanka": "comsol_api_solver.51 contexts.xml",
    "VelocitySolver": "comsol_api_solver.51 contexts.xml",
}

SOLVER_FEATURE_TYPE_IDS = frozenset(SOLVER_FEATURE_TYPE_SOURCES)

#: ``study(<tag>).createAutoSequences(<type>)`` argument vocabulary
#: (``comsol_api_general.47.60``): "The argument type is one of all, jobs, or
#: sol".
STUDY_AUTO_SEQUENCE_TYPES = frozenset({"all", "jobs", "sol"})

#: Documented solver-feature property names for the two operation features the
#: W16 acceptance chain drives (Table 6-70 "Stationary Properties" and Table
#: 6-74 "Valid Properties for Time", ``comsol_api_solver.51.47``/``51.51``).
SOLVER_DOCUMENTED_PROPERTIES: dict[str, frozenset[str]] = {
    "Stationary": frozenset({"clist", "cname", "control", "keeplog", "keepnotsolstatic",
                             "linplistsolnum", "linpmanualsolnum", "linpmethod", "linpsol",
                             "linpsolnum", "linpsoluse", "linpsolusesolnum", "linpt",
                             "listsolnum", "lumpedflux", "manualsolnum", "nonlin", "message",
                             "outsollinear", "outsollinearized", "plot", "plotgroup", "probes",
                             "probesel", "reacf", "stol", "storelinpoint", "t"}),
    "Time": frozenset({"tlist", "tout", "rtol", "atol", "atolmethod", "atolglobal",
                       "atolglobalfactor", "atolglobalmethod", "atolglobalvaluemethod", "atoludot",
                       "atoludotactive", "bdforder", "consistent", "complex", "control", "clist",
                       "cname", "initialstep", "maxstepconstraint", "maxstep", "minstep",
                       "keeplog", "plot", "plotgroup", "probes", "probesel", "reacf",
                       "outputtimes", "interp", "storelinpoint", "algebrdaecoupling",
                       "algebrdaescaling", "equilibrate", "estrat", "eventtol", "odesolvertype"}),
}

#: ``StudyFeature.setSolveFor(<entityPath>,boolean)`` / ``solveFor(<entityPath>)``
#: is the documented physics-activation accessor (``comsol_api_general.47.60``:
#: ``step.setSolveFor(<entityPath>,boolean)`` and
#: ``step.solveFor(<entityPath>)``; Table 6-87 lists the ``activate`` String Map
#: for the same decision).  This layer drives the physics map through
#: ``setSolveFor``/``solveFor`` and the coupling map through the R03 keyed-entry
#: path on ``activateCoupling``.
PHYSICS_ACTIVATION_KEYS = ("physics", "coupling")

_SOLVE_FOR_PROPERTY = {"coupling": "activateCoupling"}

#: Readback probes used after a dispatched mutation.  A probe the worker
#: refuses is reported with ``allowlist_entry_required`` - it is never treated
#: as a successful read or as an empty value.
_MESH_SEQUENCE_READBACK = ("current", "isComplete", "isEmpty", "getNumElem", "getTypes",
                           "getSDim", "hasProblems", "problems", "getVolume", "buildTime")
_MESH_FEATURE_READBACK = ("status", "message", "isBuilt", "hasError", "hasWarning",
                          "problems", "errors", "warnings")
_SOLVER_READBACK = ("isEmpty", "isInitialized", "getDefaultSolnum", "hasProblems",
                    "getErrorMessage", "getInformationMessage", "getWarningMessage",
                    "getSequenceType", "getSize")
_STUDY_READBACK = ("getLastComputationTime", "getLastComputationDate", "getLastComputationVersion",
                   "getSolverSequences", "isGenPlots", "isGenConv", "isStoreSolution",
                   "isAttached")

_MESH_SELECTION_KINDS = ("named", "explicit", "all", "inherited")


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _status(applied: Sequence[Any], failed: Sequence[Any], not_executed: Sequence[Any],
            execution_state_unknown: bool) -> dict[str, Any]:
    if execution_state_unknown:
        status = "EXECUTION_STATE_UNKNOWN"
    elif failed:
        status = "PARTIAL_FAILURE" if applied else "FAILED"
    else:
        status = "APPLIED"
    return {
        "ok": not failed,
        "status": status,
        "partial_change": bool(applied) or bool(execution_state_unknown),
        "execution_state_unknown": bool(execution_state_unknown),
        "applied_count": len(applied),
        "failed_count": len(failed),
        "not_executed_count": len(not_executed),
    }


def _property_write(node_path: Mapping[str, Any], worker: Any, model_tag: str,
                    payload: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the G2 property-set discipline and normalise its envelope.

    Mirrors the W13 helper: the shared ``property_set`` owns the typed readback
    rule, this layer only reports which assignments were applied, which failed
    and which never ran.
    """
    if not payload:
        return {"applied": [], "failed": [], "not_executed": [], "execution_state_unknown": False,
                "readback_values": {}, "engine_error": None}
    result = property_set(worker, model_tag, node_path, payload)
    data = result.get("data") if isinstance(result, Mapping) else None
    data = data if isinstance(data, Mapping) else {}
    applied = list(data.get("applied") or [])
    return {
        "applied": applied,
        "failed": list(data.get("failed") or []),
        "not_executed": list(data.get("not_executed") or []),
        "execution_state_unknown": bool(result.get("execution_state_unknown")),
        "readback_values": {row.get("name"): row.get("readback") for row in applied
                            if isinstance(row, Mapping)},
        "engine_error": result.get("error"),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


def _probe_snapshot(node: Any, methods: Sequence[str]) -> dict[str, Any]:
    """Read a bounded set of optional accessors and record every refusal.

    ``values`` holds the reads that succeeded; ``allowlist_entry_required``
    names the worker allow-list entries that are missing for this build, and
    ``errors`` keeps every other probe failure.  An unavailable read is never
    converted into an empty list or a success.
    """
    values: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    missing: list[str] = []
    for method in methods:
        probe = call_probe(node, method)
        if probe["ok"]:
            values[method] = _jsonable(probe["value"])
            continue
        error = probe["error"] or {}
        if error.get("allowlist_entry_required"):
            missing.append(str(error["allowlist_entry_required"]))
        errors[method] = error
    return {
        "values": values,
        "errors": errors,
        "allowlist_entry_required": sorted(set(missing)),
        "readable": bool(values),
    }


def _readback_block(node: Any, methods: Sequence[str]) -> dict[str, Any]:
    snapshot = _probe_snapshot(node, methods)
    return {
        "state": snapshot["values"],
        "errors": snapshot["errors"],
        "allowlist_entry_required": snapshot["allowlist_entry_required"],
        "readable": snapshot["readable"],
    }


def _component_tags(worker: Any, model_tag: str) -> list[str]:
    model = bound_model(worker, model_tag)
    container = _call(model, "component")
    return tag_list(container)


def _require_component(worker: Any, model_tag: str, component: str) -> Any:
    component = validate_tag(component, "component")
    model = bound_model(worker, model_tag)
    container = _call(model, "component")
    if component not in tag_list(container):
        raise ExecutionContractError("NODE_NOT_FOUND", f"component {component!r} does not exist")
    return _call(model, "component", component)


def _require_geometry(worker: Any, model_tag: str, component: str, geometry: str) -> Any:
    geometry = validate_tag(geometry, "geometry")
    comp = _require_component(worker, model_tag, component)
    container = _call(comp, "geom")
    if geometry not in tag_list(container):
        raise ExecutionContractError(
            "NODE_NOT_FOUND", f"geometry {geometry!r} does not exist in component {component!r}"
        )
    return _call(comp, "geom", geometry)


def _mesh_sequence_path(component: str, mesh: str) -> dict[str, Any]:
    return {"segments": [{"collection": "component", "tag": component},
                         {"collection": "mesh", "tag": mesh}]}


def _mesh_sequence_context(worker: Any, model_tag: str, path: Any, *, label: str = "path"
                           ) -> tuple[dict[str, Any], Any, str, str]:
    """Resolve a mesh-sequence NodePath and require the documented shape.

    ``model.component(<ctag>).mesh(<tag>)`` is the only verified accessor chain
    for a meshing sequence (javap ``ModelNode.mesh()`` + the implementation
    overload ``AbstractModelImpl.mesh(String)``).
    """
    if not isinstance(path, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a NodePath object")
    parsed = NodePath.from_wire(path)
    segments = [segment for segment in parsed.segments]
    if len(segments) != 2 or segments[0].collection != "component" or segments[1].collection != "mesh":
        raise ExecutionContractError(
            "INVALID_NODE_PATH",
            f"{label} must be component+mesh (model.component(<ctag>).mesh(<tag>)); "
            f"got {[segment.as_dict() for segment in segments]}",
        )
    component = validate_tag(segments[0].tag, "component")
    mesh = validate_tag(segments[1].tag, "mesh")
    comp = _require_component(worker, model_tag, component)
    container = _call(comp, "mesh")
    if mesh not in tag_list(container):
        raise ExecutionContractError(
            "NODE_NOT_FOUND", f"mesh sequence {mesh!r} does not exist in component {component!r}"
        )
    canonical, node = resolve_path(worker, model_tag, path, label=label)
    return canonical, node, component, mesh


def _feature_context(worker: Any, model_tag: str, path: Any, *, label: str = "path",
                     parent_collections: Sequence[str] = ("component", "mesh", "study", "sol", "feature")
                     ) -> tuple[dict[str, Any], Any, dict[str, Any], str]:
    """Resolve a ``...feature(<tag>)`` NodePath into (path, node, parent, tag)."""
    if not isinstance(path, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a NodePath object")
    parsed = NodePath.from_wire(path)
    if not parsed.segments or parsed.segments[-1].collection != "feature":
        raise ExecutionContractError(
            "INVALID_NODE_PATH", f"{label} must end in a feature segment (collection 'feature')"
        )
    tag = validate_tag(parsed.segments[-1].tag, "tag")
    parent_path, collection, parent_tag = split_parent_path(path, label=label)
    if collection != "feature" or parent_tag != tag:
        raise ExecutionContractError("INVALID_NODE_PATH", f"{label} is not a feature path")
    root = parsed.segments[0].collection
    if root not in parent_collections:
        raise ExecutionContractError(
            "INVALID_NODE_PATH",
            f"{label} must start at one of {list(parent_collections)}; got {root!r}",
        )
    canonical, parent = resolve_path(worker, model_tag, parent_path, label=f"{label}.parent")
    if tag not in tag_list(_call(parent, "feature")):
        raise ExecutionContractError(
            "NODE_NOT_FOUND", f"feature {tag!r} does not exist under {parent_path}"
        )
    canonical_full, node = resolve_path(worker, model_tag, path, label=label)
    return canonical_full, node, canonical, tag


def _study_context(worker: Any, model_tag: str, path: Any, *, label: str = "path"
                   ) -> tuple[dict[str, Any], Any, str]:
    if not isinstance(path, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a NodePath object")
    parsed = NodePath.from_wire(path)
    if len(parsed.segments) != 1 or parsed.segments[0].collection != "study":
        raise ExecutionContractError(
            "INVALID_NODE_PATH", f"{label} must be a single study segment (model.study(<tag>))"
        )
    tag = validate_tag(parsed.segments[0].tag, "study")
    model = bound_model(worker, model_tag)
    if tag not in tag_list(_call(model, "study")):
        raise ExecutionContractError("NODE_NOT_FOUND", f"study {tag!r} does not exist")
    canonical, node = resolve_path(worker, model_tag, path, label=label)
    return canonical, node, tag


def _solver_context(worker: Any, model_tag: str, path: Any, *, label: str = "path"
                    ) -> tuple[dict[str, Any], Any, str]:
    if not isinstance(path, Mapping):
        raise ExecutionContractError("INVALID_REQUEST", f"{label} must be a NodePath object")
    parsed = NodePath.from_wire(path)
    if len(parsed.segments) != 1 or parsed.segments[0].collection != "sol":
        raise ExecutionContractError(
            "INVALID_NODE_PATH", f"{label} must be a single sol segment (model.sol(<tag>))"
        )
    tag = validate_tag(parsed.segments[0].tag, "sol")
    model = bound_model(worker, model_tag)
    if tag not in tag_list(_call(model, "sol")):
        raise ExecutionContractError("NODE_NOT_FOUND", f"solver sequence {tag!r} does not exist")
    canonical, node = resolve_path(worker, model_tag, path, label=label)
    return canonical, node, tag


def _feature_tags(node: Any) -> list[str]:
    return tag_list(_call(node, "feature"))


def _feature_rows(node: Any) -> list[dict[str, Any]]:
    container = _call(node, "feature")
    rows: list[dict[str, Any]] = []
    for tag in tag_list(container):
        child = _call(container, "get", tag)
        rows.append({"tag": tag, "type_id": node_type(child),
                     "label": call_probe(child, "label")["value"] if call_probe(child, "label")["ok"] else None})
    return rows


def _live_property_names(node: Any, *, label: str) -> list[str]:
    """The node's own authoritative property list, or a refusal."""
    probe = call_probe(node, "properties")
    if not probe["ok"]:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"authoritative property metadata is unavailable for {label}: {probe['error']}",
        )
    names = probe["value"]
    if not isinstance(names, (list, tuple)):
        raise ExecutionContractError(
            "API_UNSUPPORTED", f"{label}.properties() did not return a property list"
        )
    return [str(name) for name in names]


def _definition_payload(node: Any, definition: Mapping[str, Any], documented: frozenset[str] | None,
                        *, label: str) -> tuple[list[dict[str, Any]], str]:
    """Turn a wire definition into a validated ``property_set`` payload.

    Property names are checked *before* any write against the live node's own
    ``properties()`` list, because that list is the target build's authoritative
    vocabulary.  Only when the live metadata cannot be read does the documented
    table transcribed from the local COMSOL 6.4 corpus act as the allow-list;
    when neither is available the definition is refused instead of guessed.
    """
    names = [name for name in definition if name not in ENVELOPE_FIELDS]
    if not names:
        return [], "empty"
    try:
        allowed: frozenset[str] | None = frozenset(_live_property_names(node, label=label))
        source = "engine_properties"
    except ExecutionContractError:
        if documented is None:
            raise
        allowed = documented
        source = "documented_table"
    return definition_properties(node, definition, allowed, label=label), source


def _mesh_selection(node: Any, worker: Any, model_tag: str, spec: Any) -> dict[str, Any]:
    """Bind a SelectionSpec to a mesh feature's local selection.

    ``MeshFeature.selection()`` returns a ``MeshSelection`` - a *component-local*
    selection (javap ``MeshFeature.selection()`` -> ``MeshSelection``).  The
    model-component binding that ``apply_local_selection`` performs for
    model-global containers is deliberately skipped here (``component=None``):
    the Programming Reference requires ``model(<mtag>)`` before a local
    selection is assigned on a model-global container such as
    ``model.variable(<vtag>)``, and issuing it on a mesh feature's selection
    would add a call the documented mesh API never asks for.  The refusal is
    still surfaced (never swallowed) when ``selection()`` itself is unavailable.
    """
    resolved = validate_selection_spec(spec, allowed_kinds=_MESH_SELECTION_KINDS)
    probe = call_probe(node, "selection")
    if not probe["ok"] or probe["value"] is None:
        reason = probe["error"] or {"message": "selection() is unavailable"}
        raise ExecutionContractError(
            "API_UNSUPPORTED", f"this mesh feature does not expose selection(): {reason}"
        )
    local = probe["value"]
    applied = apply_local_selection(local, worker, model_tag, None, resolved)
    return {**applied, "selection_node_state": selection_state(local)}


def _completion(*, dispatched: bool, applied: Sequence[Any], failed: Sequence[Any],
                not_executed: Sequence[Any], readback: Mapping[str, Any],
                error: Mapping[str, Any] | None = None, require_readback: bool = True
                ) -> dict[str, Any]:
    """Build the post-write envelope (data, never an exception).

    ``execution_state_unknown`` is true when the call was dispatched but no
    readback could confirm or refute the change.  A refused readback keeps the
    missing worker allow-list entry visible so the gap is actionable instead of
    looking like a successful no-op.
    """
    if error is not None:
        failed = list(failed) + [dict(error)]
        unknown = not readback.get("readable", False)
        status = "PARTIAL_FAILURE" if applied or not unknown else "FAILED"
    elif not dispatched:
        unknown = False
        status = "NOT_EXECUTED"
    elif failed:
        # A step that failed after the dispatch (for example a property the
        # build refuses) must never be reported as APPLIED just because the
        # queue accepted the call.
        unknown = not readback.get("readable", False)
        status = "PARTIAL_FAILURE" if applied else "FAILED"
    elif require_readback and not readback.get("readable", False):
        unknown = True
        status = "DISPATCHED_UNVERIFIED"
    else:
        unknown = False
        status = "APPLIED"
    return {
        "ok": error is None and not failed and status == "APPLIED",
        "status": status,
        "partial_change": bool(applied) or unknown,
        "execution_state_unknown": unknown,
        "applied": list(applied),
        "failed": list(failed),
        "not_executed": list(not_executed),
        "readback": dict(readback),
        "applied_count": len(applied),
        "failed_count": len(failed),
        "not_executed_count": len(not_executed),
        "engine_error": dict(error) if error is not None else None,
    }


def _require_registered_type(type_id: Any, table: Mapping[str, str], label: str,
                             *, allow: frozenset[str] | None = None) -> str:
    value = require_string(type_id, label, max_length=64)
    if value not in table or (allow is not None and value not in allow):
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"{value!r} is not in the verified COMSOL 6.4 {label} vocabulary "
            f"({len(table)} verified ids; see the operation result's type_source for the cited page)",
        )
    return value


def _type_source(type_id: str, table: Mapping[str, str]) -> str | None:
    return table.get(type_id)


# ---------------------------------------------------------------------------
# mesh domain
# ---------------------------------------------------------------------------


def mesh_list(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component",))
    component = args.get("component")
    components = [validate_tag(component, "component")] if component else _component_tags(worker, model_tag)
    rows: list[dict[str, Any]] = []
    for ctag in components:
        comp = _require_component(worker, model_tag, ctag)
        container = _call(comp, "mesh")
        for mtag in tag_list(container):
            seq = _call(container, "get", mtag)
            snapshot = _probe_snapshot(seq, ("geom", "getSDim", "isAutomatic", "current",
                                             "isComplete", "isEmpty", "getNumElem", "getTypes"))
            values = snapshot["values"]
            rows.append({
                "component": ctag,
                "mesh": mtag,
                "path": _mesh_sequence_path(ctag, mtag),
                "label": call_probe(seq, "label")["value"] if call_probe(seq, "label")["ok"] else None,
                "geometry": values.get("geom"),
                "sdim": values.get("getSDim"),
                "physics_controlled": values.get("isAutomatic"),
                "current_feature": values.get("current"),
                "is_complete": values.get("isComplete"),
                "is_empty": values.get("isEmpty"),
                "element_count": values.get("getNumElem"),
                "element_types": values.get("getTypes"),
                "feature_count": len(_feature_tags(seq)),
                "read_errors": snapshot["errors"],
                "allowlist_entry_required": snapshot["allowlist_entry_required"],
            })
    return {
        "component_scope": component,
        "meshes": rows,
        "mesh_count": len(rows),
        "notes": [
            "a mesh sequence is always attached to the component that owns its geometry "
            "(model.component(<ctag>).mesh(<mtag>)); there is no model-global mesh sequence in this layer",
        ],
    }


def mesh_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("component", "tag", "geometry", "mode"),
                               ("component", "tag", "geometry"))
    component = validate_tag(args["component"], "component")
    tag = validate_tag(args["tag"])
    geometry = validate_tag(args["geometry"], "geometry")
    mode = args.get("mode")
    if mode is not None:
        mode = require_string(mode, "mode", max_length=32)
        if mode not in MESH_MODE_IDS:
            raise ExecutionContractError(
                "INVALID_REQUEST", f"mode must be one of {sorted(MESH_MODE_IDS)}"
            )
    comp = _require_component(worker, model_tag, component)
    _require_geometry(worker, model_tag, component, geometry)
    container = _call(comp, "mesh")
    existing = tag_list(container)
    if tag in existing:
        raise ExecutionContractError(
            "TAG_CONFLICT", f"mesh sequence {tag!r} already exists in component {component!r}"
        )
    _call(container, "create", tag, geometry)
    after = tag_list(container)
    if tag not in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"mesh sequence {tag!r} was created but the post-create tag readback does not show it",
        )
    seq = _call(container, "get", tag)
    applied: list[dict[str, Any]] = [{"step": "create", "method": "component.mesh().create",
                                      "requested": {"tag": tag, "geometry": geometry},
                                      "readback": {"tags": after}}]
    failed: list[dict[str, Any]] = []
    readback = _readback_block(seq, ("geom", "isAutomatic", "current", "getSDim"))
    if mode is not None:
        requested = MESH_MODE_IDS[mode]
        probe = call_probe(seq, "isAutomatic")
        if probe["ok"] and bool(probe["value"]) is requested:
            applied.append({"step": "mode", "method": "mesh.automatic",
                            "requested": requested, "readback": bool(probe["value"])})
        else:
            error = None
            try:
                _call(seq, "automatic", requested)
            except ExecutionContractError as exc:
                error = describe_engine_failure(exc, "automatic")
            verify = call_probe(seq, "isAutomatic")
            if error is not None:
                failed.append({"step": "mode", "requested": requested, "error": error})
            elif verify["ok"] and bool(verify["value"]) is requested:
                applied.append({"step": "mode", "method": "mesh.automatic",
                                "requested": requested, "readback": bool(verify["value"])})
            else:
                failed.append({"step": "mode", "requested": requested,
                               "readback": _jsonable(verify["value"]),
                               "error": {"code": "EXECUTION_STATE_UNKNOWN",
                                         "message": "mesh.automatic() readback did not confirm the requested mode"}})
            readback = _readback_block(seq, ("geom", "isAutomatic", "current", "getSDim"))
    result: dict[str, Any] = {
        "path": _mesh_sequence_path(component, tag),
        "component": component,
        "tag": tag,
        "geometry": geometry,
        "mode": mode,
        "mode_semantics": {"physics_controlled": "mesh.automatic(true)",
                           "user_controlled": "mesh.automatic(false)"},
        "tag_readback": after,
        "type_note": ("a meshing sequence has no getType() type string; the create call is the documented "
                      "model.component(<ctag>).mesh().create(<mtag>,<gtag>) -- javap "
                      "com.comsol.model.MeshList.create(java.lang.String tag, java.lang.String gtag) "
                      "(\"Creates a meshing sequence\", params tag + gtag) inherited by "
                      "com.comsol.model.ComponentMeshList"),
    }
    result.update(_completion(dispatched=True, applied=applied, failed=failed, not_executed=[],
                              readback=readback))
    return result


def _mesh_tree(seq: Any, depth: int, *, prefix: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Recursive mesh-feature walk over the documented ``feature()`` accessor."""
    rows: list[dict[str, Any]] = []
    for tag in _feature_tags(seq):
        node = _call(seq, "feature", tag)
        path = {"segments": list(prefix["segments"]) + [{"collection": "feature", "tag": tag}]}
        snapshot = _probe_snapshot(node, _MESH_FEATURE_READBACK)
        values = snapshot["values"]
        row: dict[str, Any] = {
            "path": path,
            "tag": tag,
            "type_id": node_type(node),
            "label": call_probe(node, "label")["value"] if call_probe(node, "label")["ok"] else None,
            "status": values.get("status"),
            "message": values.get("message"),
            "is_built": values.get("isBuilt"),
            "has_error": values.get("hasError"),
            "has_warning": values.get("hasWarning"),
            "problems": values.get("problems"),
            "errors": values.get("errors"),
            "warnings": values.get("warnings"),
            "read_errors": snapshot["errors"],
            "allowlist_entry_required": snapshot["allowlist_entry_required"],
            "children": [],
        }
        if depth > 1:
            child_container = call_probe(node, "feature")
            if child_container["ok"] and child_container["value"] is not None:
                try:
                    row["children"] = _mesh_tree(node, depth - 1, prefix={"segments": path["segments"]})
                except ExecutionContractError as exc:
                    row["children_error"] = describe_engine_failure(exc, "feature")
                    row["children"] = []
        rows.append(row)
    return rows


def mesh_inspect(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "depth"), ("path",))
    depth = args.get("depth")
    depth = 1 if depth is None else require_int(depth, "depth", minimum=1, maximum=4)
    canonical, node = resolve_path(worker, model_tag, args["path"], label="path")
    parsed = NodePath.from_wire(canonical)
    root_collection = parsed.segments[-1].collection if parsed.segments else None
    if root_collection == "mesh":
        _, _, component, mesh_tag = _mesh_sequence_context(worker, model_tag, args["path"])
        features = _mesh_tree(node, depth, prefix=canonical)
        state = _readback_block(node, ("geom", "getSDim", "isAutomatic", "current", "isComplete",
                                       "isEmpty", "getNumElem", "getTypes", "lengthUnit"))
        return {
            "kind": "mesh_sequence",
            "path": canonical,
            "component": component,
            "mesh": mesh_tag,
            "label": call_probe(node, "label")["value"] if call_probe(node, "label")["ok"] else None,
            "geometry": state["state"].get("geom"),
            "sdim": state["state"].get("getSDim"),
            "physics_controlled": state["state"].get("isAutomatic"),
            "current_feature": state["state"].get("current"),
            "is_complete": state["state"].get("isComplete"),
            "is_empty": state["state"].get("isEmpty"),
            "element_count": state["state"].get("getNumElem"),
            "element_types": state["state"].get("getTypes"),
            "feature_count": len(features),
            "features": features,
            "readback": state,
            "documented_properties": {row["type_id"]: sorted(MESH_DOCUMENTED_PROPERTIES[row["type_id"]])
                                      for row in features
                                      if row.get("type_id") in MESH_DOCUMENTED_PROPERTIES},
            "status_values": list(MESH_FEATURE_STATUS),
        }
    if root_collection != "feature":
        raise ExecutionContractError(
            "INVALID_NODE_PATH", "path must be a mesh sequence or a mesh feature"
        )
    canonical, node, parent_path, tag = _feature_context(worker, model_tag, args["path"], label="path")
    type_id = node_type(node)
    snapshot = _probe_snapshot(node, _MESH_FEATURE_READBACK)
    properties = _probe_property_table(node)
    return {
        "kind": "mesh_feature",
        "path": canonical,
        "parent_path": parent_path,
        "tag": tag,
        "type_id": type_id,
        "type_source": _type_source(type_id or "", MESH_FEATURE_TYPE_SOURCES),
        "label": call_probe(node, "label")["value"] if call_probe(node, "label")["ok"] else None,
        "status": snapshot["values"].get("status"),
        "message": snapshot["values"].get("message"),
        "is_built": snapshot["values"].get("isBuilt"),
        "has_error": snapshot["values"].get("hasError"),
        "has_warning": snapshot["values"].get("hasWarning"),
        "problems": snapshot["values"].get("problems"),
        "selection": selection_state(_call(node, "selection")) if getattr(node, "selection", None) else None,
        "properties": properties["values"],
        "property_metadata": properties["metadata"],
        "documented_properties": sorted(MESH_DOCUMENTED_PROPERTIES.get(type_id or "", frozenset())),
        "children": _mesh_tree(node, depth, prefix=canonical) if depth > 1 else [],
        "read_errors": snapshot["errors"],
        "allowlist_entry_required": snapshot["allowlist_entry_required"],
    }


def _probe_property_table(node: Any, limit: int = 64) -> dict[str, Any]:
    """Read a bounded property table with per-name metadata and value errors."""
    probe = call_probe(node, "properties")
    if not probe["ok"]:
        return {"values": None, "metadata": None, "error": probe["error"],
                "allowlist_entry_required": probe["error"].get("allowlist_entry_required")
                if isinstance(probe["error"], Mapping) else None}
    names = [str(name) for name in probe["value"]] if isinstance(probe["value"], (list, tuple)) else []
    names = names[:limit]
    metadata: dict[str, Any] = {}
    for name in names:
        row = property_rows(node, [name]).get(name, {})
        metadata[name] = {"value_type": row.get("value_type"), "kind": row.get("kind"),
                          "shape_rank": row.get("shape_rank"),
                          "allowed_values": _jsonable(row.get("allowed_values")),
                          "metadata_status": row.get("metadata_status")}
    values: dict[str, Any] = {}
    errors: dict[str, Any] = {}
    for name in names:
        probe = call_probe(node, "getString", name)
        if probe["ok"]:
            values[name] = probe["value"]
            continue
        numeric = call_probe(node, "getDouble", name)
        if numeric["ok"]:
            values[name] = numeric["value"]
            continue
        errors[name] = probe["error"]
    return {"values": values, "metadata": metadata, "value_errors": errors,
            "property_count": len(names), "truncated": isinstance(probe["value"], (list, tuple))
            and len(probe["value"]) > limit}


def mesh_feature_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("parent", "tag", "type_id", "properties", "selection"),
                               ("parent", "tag", "type_id", "properties"))
    tag = validate_tag(args["tag"])
    type_id = _require_registered_type(args["type_id"], MESH_FEATURE_TYPE_SOURCES, "type_id")
    definition = property_definition(args["properties"], "properties")
    parent_path, parent = resolve_path(worker, model_tag, args["parent"], label="parent")
    parsed = NodePath.from_wire(parent_path)
    if not parsed.segments or parsed.segments[-1].collection not in {"mesh", "feature"}:
        raise ExecutionContractError(
            "INVALID_NODE_PATH",
            "parent must be a mesh sequence or a mesh feature (an attribute feature is created under "
            "mesh(<tag>).feature(<ftag>).create(...))",
        )
    container = _call(parent, "feature")
    existing = tag_list(container)
    if tag in existing:
        current = node_type(_call(container, "get", tag))
        if current is not None and current != type_id:
            raise ExecutionContractError(
                "TYPE_CONFLICT",
                f"mesh feature {tag!r} already exists with type {current!r} (requested {type_id!r})",
            )
        raise ExecutionContractError("TAG_CONFLICT", f"mesh feature {tag!r} already exists")

    _call(parent, "create", tag, type_id)
    readback_tags = tag_list(container)
    if tag not in readback_tags:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"mesh feature {tag!r} was created but the post-create tag readback does not show it",
        )
    node = _call(container, "get", tag)
    type_readback = node_type(node)
    applied: list[dict[str, Any]] = [{"step": "create", "method": "mesh(...).create(tag, type)",
                                      "requested": {"tag": tag, "type_id": type_id},
                                      "readback": {"tags": readback_tags, "type": type_readback}}]
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False
    if definition:
        try:
            payload, property_source = _definition_payload(node, definition, MESH_DOCUMENTED_PROPERTIES.get(type_id),
                                                           label=f"properties for {type_id}")
        except ExecutionContractError as exc:
            failed.append({"step": "properties", "error": {"code": exc.code, "message": str(exc)},
                           "partial_change": True, "execution_state_unknown": False})
            property_source = None
            payload = []
        if payload:
            path = {"segments": parsed.as_dict()["segments"] + [{"collection": "feature", "tag": tag}]}
            write = _property_write(path, worker, model_tag, payload)
            applied.extend({"step": "property", **row} for row in write["applied"])
            failed.extend({"step": "property", **row} for row in write["failed"])
            not_executed.extend({"step": "property", **row} for row in write["not_executed"])
            execution_state_unknown = execution_state_unknown or write["execution_state_unknown"]
        elif property_source is None:
            property_source = "refused"
    else:
        property_source = "empty"
    selection_result = None
    if args.get("selection") is not None:
        try:
            result = _mesh_selection(node, worker, model_tag, args["selection"])
            applied.append({"step": "selection", **result})
            selection_result = result
        except ExecutionContractError as exc:
            failed.append({"step": "selection",
                           "error": {"code": exc.code, "message": str(exc)},
                           "partial_change": True,
                           "execution_state_unknown": exc.code == "EXECUTION_STATE_UNKNOWN"})
            execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
    readback = _readback_block(node, _MESH_FEATURE_READBACK)
    snapshot_ok = call_probe(node, "properties")["ok"]
    result = {
        "path": {"segments": parsed.as_dict()["segments"] + [{"collection": "feature", "tag": tag}]},
        "parent_path": parent_path,
        "tag": tag,
        "type_id": type_id,
        "type_source": _type_source(type_id, MESH_FEATURE_TYPE_SOURCES),
        "type_readback": type_readback,
        "property_source": property_source,
        "documented_properties": sorted(MESH_DOCUMENTED_PROPERTIES.get(type_id, frozenset())),
        "selection": selection_result,
        "feature_properties_readable": snapshot_ok,
    }
    result.update(_completion(dispatched=True, applied=applied, failed=failed, not_executed=not_executed,
                              readback=readback, require_readback=False))
    if execution_state_unknown:
        result["execution_state_unknown"] = True
        if not failed:
            result["status"] = "EXECUTION_STATE_UNKNOWN"
        result["partial_change"] = True
        result["ok"] = False
    return result


def _mesh_parent_component(parent_path: Mapping[str, Any]) -> str:
    """Return the component tag of a mesh-feature parent path.

    Kept for callers that need the owning component explicitly (for example to
    report it in a result); the mesh-feature *selection* binding deliberately
    does not use it - see :func:`_mesh_selection`.
    """
    for segment in NodePath.from_wire(parent_path).segments:
        if segment.collection == "component":
            return str(segment.tag)
    raise ExecutionContractError("INVALID_NODE_PATH", "parent path has no component segment")


def mesh_feature_update(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "properties"), ("path", "properties"))
    definition = property_definition(args["properties"], "properties")
    canonical, node, parent_path, tag = _feature_context(worker, model_tag, args["path"], label="path")
    type_id = node_type(node)
    payload, property_source = _definition_payload(node, definition, MESH_DOCUMENTED_PROPERTIES.get(type_id or ""),
                                                   label=f"properties for {type_id or 'mesh feature'}")
    if not payload:
        raise ExecutionContractError("INVALID_REQUEST", "properties must contain at least one assignment")
    write = _property_write(canonical, worker, model_tag, payload)
    readback = _readback_block(node, _MESH_FEATURE_READBACK)
    result = {
        "path": canonical,
        "parent_path": parent_path,
        "tag": tag,
        "type_id": type_id,
        "property_source": property_source,
        "applied": [{"step": "property", **row} for row in write["applied"]],
        "failed": [{"step": "property", **row} for row in write["failed"]],
        "not_executed": [{"step": "property", **row} for row in write["not_executed"]],
        "properties": write["readback_values"],
        "rebuild_required": True,
        "notes": [
            "COMSOL does not rebuild automatically after a property edit: the sequence status becomes "
            "needs_rebuild/edited until mesh.build runs again (comsol_api_mesh.49.010)",
        ],
    }
    result.update(_status(result["applied"], result["failed"], result["not_executed"],
                          write["execution_state_unknown"]))
    if not write["execution_state_unknown"] and not result["failed"]:
        result["engine_error"] = write["engine_error"]
    result["readback"] = readback
    result["status_after"] = readback["state"].get("status")
    return result


def mesh_feature_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path",), ("path",))
    canonical, node, parent_path, tag = _feature_context(worker, model_tag, args["path"], label="path")
    type_id = node_type(node)
    _, parent = resolve_path(worker, model_tag, parent_path, label="parent")
    container = _call(parent, "feature")
    before = tag_list(container)
    _call(container, "remove", tag)
    after = tag_list(container)
    readback = _readback_block(parent, ("current", "isComplete", "isEmpty", "getNumElem"))
    if tag in after:
        return {
            "path": canonical,
            "parent_path": parent_path,
            "tag": tag,
            "type_id": type_id,
            **_completion(dispatched=True, applied=[], failed=[], not_executed=[],
                          readback=readback,
                          error={"code": "EXECUTION_STATE_UNKNOWN",
                                 "message": f"mesh feature {tag!r} was removed but the tag readback still shows it"}),
        }
    return {
        "path": canonical,
        "parent_path": parent_path,
        "tag": tag,
        "type_id": type_id,
        "removed": True,
        "tag_readback_before": before,
        "tag_readback_after": after,
        "sibling_count": len(after),
        **_completion(dispatched=True, applied=[{"step": "remove", "method": "mesh(...).feature().remove",
                                                 "tag": tag, "readback": after}],
                      failed=[], not_executed=[], readback=readback),
    }


def mesh_build(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "until_tag"), ("path",))
    canonical, node, component, mesh_tag = _mesh_sequence_context(worker, model_tag, args["path"])
    until = args.get("until_tag")
    feature_tags = _feature_tags(node)
    call_args: tuple[Any, ...] = ()
    if until is not None:
        until = validate_tag(until, "until_tag")
        if until not in feature_tags:
            raise ExecutionContractError(
                "NODE_NOT_FOUND",
                f"until_tag {until!r} does not exist in mesh sequence {mesh_tag!r}; "
                f"available features: {feature_tags}",
            )
        call_args = (until,)
    before = _readback_block(node, _MESH_SEQUENCE_READBACK)
    started = time.monotonic()
    error: dict[str, Any] | None = None
    try:
        _call(node, "run", *call_args)
    except ExecutionContractError as exc:
        error = describe_engine_failure(exc, "run")
    duration_s = round(time.monotonic() - started, 3)
    after = _readback_block(node, _MESH_SEQUENCE_READBACK)
    result = {
        "path": canonical,
        "component": component,
        "mesh": mesh_tag,
        "until_tag": until,
        "built_feature_range": feature_tags[:feature_tags.index(until) + 1] if until in feature_tags else feature_tags,
        "duration_s": duration_s,
        "state_before": before["state"],
        "state_after": after["state"],
        "readback_allowlist_entry_required": after["allowlist_entry_required"],
        "long_task_semantics": {
            "owner": "control_plane",
            "note": "mesh.run() is a synchronous engine call on the serial worker queue; the caller's "
                    "rpc/execution deadlines belong to the control plane and a wait expiry does not prove "
                    "that COMSOL stopped building",
        },
        "notes": [
            "a completed build does not prove mesh quality; inspect the statistics/quality readback "
            "(mesh.statistics, mesh.quality) before treating the mesh as acceptable",
        ],
    }
    result.update(_completion(dispatched=True, applied=[{"step": "run", "method": "mesh.run",
                                                         "args": list(call_args), "duration_s": duration_s}],
                              failed=[], not_executed=[], readback=after, error=error))
    result["state_changed"] = before["state"] != after["state"]
    return result


def mesh_clear(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path",), ("path",))
    canonical, node, component, mesh_tag = _mesh_sequence_context(worker, model_tag, args["path"])
    before = _readback_block(node, _MESH_SEQUENCE_READBACK)
    feature_tags_before = _feature_tags(node)
    error: dict[str, Any] | None = None
    try:
        _call(node, "clearMesh")
    except ExecutionContractError as exc:
        error = describe_engine_failure(exc, "clearMesh")
        if error.get("allowlist_entry_required"):
            raise ExecutionContractError(
                "METHOD_NOT_ALLOWED",
                "the worker allow-list does not expose clearMesh; add it instead of reaching the "
                "destructive mesh(<tag>).feature().clear() form",
            ) from exc
    after = _readback_block(node, _MESH_SEQUENCE_READBACK)
    feature_tags_after = _feature_tags(node)
    return {
        "path": canonical,
        "component": component,
        "mesh": mesh_tag,
        "state_before": before["state"],
        "state_after": after["state"],
        "features_before": feature_tags_before,
        "features_after": feature_tags_after,
        "features_preserved": feature_tags_before == feature_tags_after,
        "destructive_variant_excluded": "mesh(<tag>).feature().clear() (removes every feature) is not reachable "
                                        "from this operation",
        "readback_allowlist_entry_required": after["allowlist_entry_required"],
        **_completion(dispatched=True,
                      applied=[{"step": "clearMesh", "method": "mesh.clearMesh",
                                "requested": "clear built mesh, keep features"}],
                      failed=[], not_executed=[], readback=after, error=error),
    }


def mesh_statistics(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path",), ("path",))
    canonical, node, component, mesh_tag = _mesh_sequence_context(worker, model_tag, args["path"])
    stat_probe = call_probe(node, "stat")
    stat_node = stat_probe["value"] if stat_probe["ok"] and stat_probe["value"] is not None else None
    sequence = _probe_snapshot(node, ("getNumElem", "getNumVertex", "getTypes", "getSDim",
                                      "getMinQuality", "getMeanQuality", "getMaxVolume", "getMinVolume",
                                      "getVolume", "getMaxGrowthRate", "getMeanGrowthRate",
                                      "isComplete", "isEmpty", "hasSecondOrderElements", "buildTime",
                                      "hasProblems", "problems"))
    statistics = None
    if stat_node is not None:
        statistics = _probe_snapshot(stat_node, ("getNumElem", "getNumVertex", "getTypes",
                                                 "getMinQuality", "getMeanQuality", "getVolume",
                                                 "getMaxVolume", "getMinVolume", "getMaxGrowthRate",
                                                 "getMeanGrowthRate", "getMaxDimension", "getQualityMeasure",
                                                 "isComplete", "isEmpty", "getQualityDistr"))
    missing = sorted(set(sequence["allowlist_entry_required"])
                     | set((statistics or {}).get("allowlist_entry_required", []))
                     | ({str(stat_probe["error"].get("allowlist_entry_required"))}
                        if isinstance(stat_probe["error"], Mapping)
                        and stat_probe["error"].get("allowlist_entry_required") else set()))
    coverage = _mesh_coverage(node, sequence)
    return {
        "path": canonical,
        "component": component,
        "mesh": mesh_tag,
        "element_count": sequence["values"].get("getNumElem"),
        "vertex_count": sequence["values"].get("getNumVertex"),
        "element_types": sequence["values"].get("getTypes"),
        "sdim": sequence["values"].get("getSDim"),
        "min_quality": sequence["values"].get("getMinQuality"),
        "mean_quality": sequence["values"].get("getMeanQuality"),
        "mesh_volume": sequence["values"].get("getVolume"),
        "min_volume": sequence["values"].get("getMinVolume"),
        "max_volume": sequence["values"].get("getMaxVolume"),
        "max_growth_rate": sequence["values"].get("getMaxGrowthRate"),
        "mean_growth_rate": sequence["values"].get("getMeanGrowthRate"),
        "is_complete": sequence["values"].get("isComplete"),
        "is_empty": sequence["values"].get("isEmpty"),
        "has_second_order_elements": sequence["values"].get("hasSecondOrderElements"),
        "build_time_ms": sequence["values"].get("buildTime"),
        "problems": sequence["values"].get("problems"),
        "statistics_node_available": stat_node is not None,
        "statistics": statistics["values"] if statistics else None,
        "quality_measure": (statistics or {}).get("values", {}).get("getQualityMeasure"),
        "elements_by_dimension": coverage,
        "dof_estimate": {
            "status": "NOT_AVAILABLE",
            "reason": "a mesh-only read has no verified COMSOL API that reports the assembled degrees of "
                      "freedom; the DOF count belongs to the compiled equations of a study step "
                      "(COMSOL Reference Manual, Compile Equations Statistics)",
        },
        "read_errors": {**sequence["errors"], **((statistics or {}).get("errors") or {})},
        "allowlist_entry_required": missing,
        "quality_definition": "element quality is a dimensionless 0..1 measure; the comparison is only "
                              "meaningful with the measure named by quality_measure",
        "notes": [
            "a successful build is not a quality statement: report the measure, the minimum and the "
            "counts only when they were actually read",
        ],
    }


def _mesh_coverage(node: Any, sequence: Mapping[str, Any]) -> dict[str, Any] | None:
    probe = _probe_snapshot(node, ("getGeomEntities",))
    if not probe["readable"]:
        return None
    values = probe["values"].get("getGeomEntities")
    if not isinstance(values, list):
        return None
    return {"entity_ids": values, "count": len(values)}


def mesh_quality(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "metric", "selection", "bins"), ("path", "metric"))
    metric = require_string(args["metric"], "metric", max_length=32)
    if metric not in QUALITY_MEASURES:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"{metric!r} is not a documented COMSOL 6.4 mesh quality measure "
            f"(Table 4-5): {sorted(QUALITY_MEASURES)}",
        )
    if metric == "custom":
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            "the 'custom' quality measure evaluates a user quality expression that this layer never sets, "
            "so it is refused instead of silently reporting the default measure",
        )
    bins = args.get("bins")
    bins = 10 if bins is None else require_int(bins, "bins", minimum=1, maximum=200)
    selection = args.get("selection")
    if selection is not None:
        validate_selection_spec(selection, label="selection")
    canonical, node, component, mesh_tag = _mesh_sequence_context(worker, model_tag, args["path"])
    stat_probe = call_probe(node, "stat")
    if not stat_probe["ok"] or stat_probe["value"] is None:
        reason = stat_probe["error"] or {"code": "API_UNSUPPORTED", "message": "stat() is unavailable"}
        if isinstance(reason, Mapping) and reason.get("allowlist_entry_required"):
            raise ExecutionContractError(
                "METHOD_NOT_ALLOWED",
                f"the worker allow-list does not expose stat (needed to read the mesh quality measure); "
                f"missing entries: [{reason['allowlist_entry_required']}]",
            )
        raise ExecutionContractError("API_UNSUPPORTED", f"mesh statistics are unavailable: {reason}")
    stat = stat_probe["value"]
    before = _probe_snapshot(stat, ("getQualityMeasure",))
    current = before["values"].get("getQualityMeasure")
    change: dict[str, Any] | None = None
    if current != metric:
        try:
            _call(stat, "setQualityMeasure", metric)
        except ExecutionContractError as exc:
            error = describe_engine_failure(exc, "setQualityMeasure")
            if error.get("allowlist_entry_required"):
                raise ExecutionContractError(
                    "METHOD_NOT_ALLOWED",
                    f"the worker allow-list does not expose setQualityMeasure; missing entries: "
                    f"[{error['allowlist_entry_required']}]",
                ) from exc
            raise
        after = call_probe(stat, "getQualityMeasure")
        if not (after["ok"] and after["value"] == metric):
            raise ExecutionContractError(
                "EXECUTION_STATE_UNKNOWN",
                f"stat().setQualityMeasure({metric!r}) readback did not confirm the requested measure",
            )
        change = {"method": "stat().setQualityMeasure", "requested": metric,
                  "previous": current, "readback": after["value"],
                  "scope": "mesh statistics setting (not a mesh build)"}
    values = _probe_snapshot(stat, ("getQualityMeasure", "getMinQuality", "getMeanQuality",
                                    "getQualityDistr"))
    distrib = values["values"].get("getQualityDistr")
    histogram = None
    if isinstance(distrib, list) and distrib:
        histogram = _quality_histogram(distrib, bins)
    return {
        "path": canonical,
        "component": component,
        "mesh": mesh_tag,
        "metric": metric,
        "metric_source": "comsol_api_solver-independent mesh chapter Table 4-5 / MeshStatistics javadoc",
        "quality_measure_before": current,
        "quality_measure_change": change,
        "quality_measure_readback": values["values"].get("getQualityMeasure"),
        "min_quality": values["values"].get("getMinQuality"),
        "mean_quality": values["values"].get("getMeanQuality"),
        "histogram": histogram,
        "histogram_bins": bins,
        "selection": selection,
        "worst_element_locations": {
            "status": "NOT_AVAILABLE",
            "reason": "no verified offline API reports the coordinates of the worst-quality element; "
                      "getVertex()/getElem() would require the mesh-data allow-list entries and an "
                      "element-quality join that this layer does not implement",
        },
        "read_errors": values["errors"],
        "allowlist_entry_required": values["allowlist_entry_required"],
        "measure_definitions": {
            "skewness": "equiangular skew", "maxangle": "largest angle in the element",
            "volcircum": "volume versus circumradius", "vollength": "volume versus edge length",
            "condition": "element dimension / Frobenius condition number",
            "growth": "local (anisotropic) neighbour growth rate",
            "curvedskewness": "skewness * reldetjacmin",
        },
        "partial_change": bool(change),
        "execution_state_unknown": False,
        "status": "APPLIED",
        "ok": True,
        "not_executed": [],
        "applied": [change] if change else [],
        "failed": [],
    }


def _quality_histogram(values: Sequence[Any], bins: int) -> dict[str, Any]:
    counts = [int(item) for item in values if isinstance(item, int) and not isinstance(item, bool)]
    edges = [round(index / len(counts), 6) for index in range(len(counts) + 1)] if counts else []
    return {
        "counts": counts,
        "edges": edges,
        "convention": "getQualityDistr(size) returns `size` bins: the first entry counts elements with "
                      "quality below 1/size, the last entry counts elements above (size-1)/size "
                      "(comsol_api_mesh.49.028); the engine's own bin count is reported, not the "
                      "requested bins value",
        "requested_bins": bins,
        "engine_bins": len(counts),
        "bin_count_matches_request": len(counts) == bins,
    }


def mesh_validate(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "criteria"), ("path", "criteria"))
    criteria = require_mapping(args["criteria"], "criteria")
    allowed_keys = {"min_quality", "require_complete", "require_element_types", "require_sdim",
                    "require_max_growth_rate", "quality_measure"}
    unknown = sorted(set(criteria) - allowed_keys)
    if unknown:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"criteria has unsupported fields: {unknown}; supported: {sorted(allowed_keys)}",
        )
    if not criteria:
        raise ExecutionContractError("INVALID_REQUEST", "criteria must contain at least one requirement")
    canonical, node, component, mesh_tag = _mesh_sequence_context(worker, model_tag, args["path"])
    state = _readback_block(node, ("getNumElem", "getNumVertex", "getTypes", "getSDim", "isComplete",
                                   "isEmpty", "getMinQuality", "getMeanQuality", "getMaxGrowthRate",
                                   "getGeomEntities"))
    values = state["state"]
    checks: list[dict[str, Any]] = []
    unavailable = state["allowlist_entry_required"]

    def record(name: str, *, expected: Any, actual: Any, ok: bool | None, reason: str | None = None
               ) -> None:
        row: dict[str, Any] = {"check": name, "expected": expected, "actual": _jsonable(actual)}
        if ok is None:
            row["status"] = "NOT_EVALUATED"
            row["reason"] = reason
        else:
            row["status"] = "PASS" if ok else "FAIL"
        checks.append(row)

    if "require_complete" in criteria:
        expected = require_bool(criteria["require_complete"], "criteria.require_complete")
        actual = values.get("isComplete")
        if actual is None:
            record("require_complete", expected=expected, actual=None, ok=None,
                   reason="isComplete() could not be read")
        else:
            record("require_complete", expected=expected, actual=bool(actual), ok=bool(actual) is expected)
    if "require_sdim" in criteria:
        expected = require_int(criteria["require_sdim"], "criteria.require_sdim", minimum=0, maximum=3)
        actual = values.get("getSDim")
        record("require_sdim", expected=expected, actual=actual,
               ok=None if actual is None else int(actual) == expected,
               reason="getSDim() could not be read" if actual is None else None)
    if "require_element_types" in criteria:
        expected_types = [str(item) for item in (criteria["require_element_types"] or [])]
        bad = sorted({item for item in expected_types if item not in MESH_ELEMENT_TYPES})
        if bad:
            raise ExecutionContractError(
                "INVALID_REQUEST", f"criteria.require_element_types has unknown element types: {bad}"
            )
        actual = values.get("getTypes")
        if not isinstance(actual, list):
            record("require_element_types", expected=sorted(expected_types), actual=None, ok=None,
                   reason="getTypes() could not be read")
        else:
            missing = sorted(set(expected_types) - {str(item) for item in actual})
            record("require_element_types", expected=sorted(expected_types), actual=actual,
                   ok=not missing)
            if missing:
                checks[-1]["missing"] = missing
    if "min_quality" in criteria:
        threshold = require_mapping(criteria["min_quality"], "criteria.min_quality")
        unknown_q = sorted(set(threshold) - {"value", "measure"})
        if unknown_q:
            raise ExecutionContractError("INVALID_REQUEST", f"criteria.min_quality has unsupported fields: {unknown_q}")
        raw_value = threshold["value"]
        if not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool):
            raise ExecutionContractError(
                "INVALID_REQUEST", f"criteria.min_quality.value must be a number, got {raw_value!r}"
            )
        value = float(raw_value)
        measure = threshold.get("measure") or criteria.get("quality_measure")
        if measure is not None and measure not in QUALITY_MEASURES:
            raise ExecutionContractError("INVALID_REQUEST", f"unknown quality measure {measure!r}")
        actual = values.get("getMinQuality")
        note = None
        if measure is not None:
            read = _probe_snapshot(node, ("getQualityMeasure",))
            if read["values"].get("getQualityMeasure") not in {None, measure}:
                actual = None
                note = ("the mesh statistics are configured for a different quality measure "
                        f"({read['values'].get('getQualityMeasure')!r}); set it with mesh.quality before "
                        "asserting a threshold")
        record("min_quality", expected=value, actual=actual,
               ok=None if actual is None else float(actual) >= value,
               reason=note or ("getMinQuality() could not be read" if actual is None else None))
    if "require_max_growth_rate" in criteria:
        limit = float(require_mapping({"value": criteria["require_max_growth_rate"]}, "criteria.require_max_growth_rate")["value"])
        actual = values.get("getMaxGrowthRate")
        record("require_max_growth_rate", expected=limit, actual=actual,
               ok=None if actual is None else float(actual) <= limit,
               reason="getMaxGrowthRate() could not be read" if actual is None else None)

    failed = [check for check in checks if check["status"] == "FAIL"]
    unevaluated = [check for check in checks if check["status"] == "NOT_EVALUATED"]
    verdict = "FAIL" if failed else ("INCOMPLETE" if unevaluated else "PASS")
    return {
        "path": canonical,
        "component": component,
        "mesh": mesh_tag,
        "criteria": dict(criteria),
        "checks": checks,
        "verdict": verdict,
        "ok": verdict == "PASS",
        "status": "APPLIED" if verdict == "PASS" else ("FAILED" if verdict == "FAIL" else "EXECUTION_STATE_UNKNOWN"),
        "execution_state_unknown": bool(unevaluated),
        "partial_change": False,
        "applied": [],
        "failed": failed,
        "not_executed": unevaluated,
        "failure_count": len(failed),
        "not_evaluated_count": len(unevaluated),
        "readback_allowlist_entry_required": unavailable,
        "boundary_layer_coverage": {
            "status": "NOT_EVALUATED",
            "reason": "boundary-layer completeness has no verified single-call read path in this layer; "
                      "inspect the BndLayer feature status/message instead",
        },
        "notes": [
            "a criterion this layer cannot read is NOT_EVALUATED and never counts as a pass",
        ],
    }


# ---------------------------------------------------------------------------
# study domain
# ---------------------------------------------------------------------------


def study_list(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("scope",))
    scope = args.get("scope")
    if scope is not None:
        canonical, node, tag = _study_context(worker, model_tag, scope, label="scope")
        studies = [(canonical, node, tag)]
    else:
        model = bound_model(worker, model_tag)
        container = _call(model, "study")
        studies = []
        for tag in tag_list(container):
            studies.append(({"segments": [{"collection": "study", "tag": tag}]},
                            _call(container, "get", tag), tag))
    associations = _solver_associations(worker, model_tag)
    rows: list[dict[str, Any]] = []
    for canonical, node, tag in studies:
        features = _feature_rows(node)
        snapshot = _probe_snapshot(node, ("getLastComputationTime", "getLastComputationDate",
                                          "getLastComputationVersion", "isGenPlots", "isStoreSolution"))
        rows.append({
            "path": canonical,
            "study": tag,
            "label": call_probe(node, "label")["value"] if call_probe(node, "label")["ok"] else None,
            "steps": features,
            "step_count": len(features),
            "physics_activation_scope": [row for row in features if row.get("type_id") in STUDY_DEFINING_STEPS],
            "solver_sequences": associations.get(tag, []),
            "last_computation_time": snapshot["values"].get("getLastComputationTime"),
            "last_computation_date": snapshot["values"].get("getLastComputationDate"),
            "last_computation_version": snapshot["values"].get("getLastComputationVersion"),
            "generates_plots": snapshot["values"].get("isGenPlots"),
            "stores_solution": snapshot["values"].get("isStoreSolution"),
            "read_errors": snapshot["errors"],
            "allowlist_entry_required": snapshot["allowlist_entry_required"],
        })
    return {
        "scope": scope,
        "studies": rows,
        "study_count": len(rows),
        "solver_associations": associations,
        "notes": [
            "the solver association is read with model.sol(<tag>).study(); isAttached() would additionally "
            "distinguish an attached sequence from a merely associated one",
        ],
    }


def _solver_associations(worker: Any, model_tag: str) -> dict[str, list[dict[str, Any]]]:
    model = bound_model(worker, model_tag)
    container = _call(model, "sol")
    out: dict[str, list[dict[str, Any]]] = {}
    for tag in tag_list(container):
        node = _call(container, "get", tag)
        probe = call_probe(node, "study")
        study_tag = probe["value"] if probe["ok"] and isinstance(probe["value"], str) else None
        out.setdefault(study_tag or "", []).append({
            "solver": tag,
            "path": {"segments": [{"collection": "sol", "tag": tag}]},
            "study_readback": study_tag,
            "study_readback_error": None if probe["ok"] else probe["error"],
        })
    return out


def study_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("tag", "label"), ("tag",))
    tag = validate_tag(args["tag"])
    label = args.get("label")
    if label is not None:
        label = require_string(label, "label", max_length=256)
    model = bound_model(worker, model_tag)
    container = _call(model, "study")
    if tag in tag_list(container):
        raise ExecutionContractError("TAG_CONFLICT", f"study {tag!r} already exists")
    _call(container, "create", tag)
    after = tag_list(container)
    if tag not in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN", f"study {tag!r} was created but the post-create tag readback does not show it"
        )
    node = _call(container, "get", tag)
    applied: list[dict[str, Any]] = [{"step": "create", "method": "model.study().create",
                                      "requested": tag, "readback": after}]
    failed: list[dict[str, Any]] = []
    if label is not None:
        try:
            _call(node, "label", label)
        except ExecutionContractError as exc:
            failed.append({"step": "label", "error": describe_engine_failure(exc, "label")})
        else:
            readback = call_probe(node, "label")
            if readback["ok"] and readback["value"] == label:
                applied.append({"step": "label", "requested": label, "readback": readback["value"]})
            else:
                failed.append({"step": "label", "requested": label,
                               "readback": _jsonable(readback["value"]),
                               "error": {"code": "EXECUTION_STATE_UNKNOWN",
                                         "message": "study label readback did not match"}})
    readback = _readback_block(node, ("label", "getLastComputationTime", "getLastComputationVersion"))
    result = {
        "path": {"segments": [{"collection": "study", "tag": tag}]},
        "study": tag,
        "label": label,
        "tag_readback": after,
        "label_readback": readback["state"].get("label"),
        "definition_note": "model.study().create(<tag>) creates an empty study sequence; add a step with "
                           "study.step_create (an empty study has no solver contract)",
    }
    result.update(_completion(dispatched=True, applied=applied, failed=failed, not_executed=[],
                              readback=readback, require_readback=False))
    return result


def study_inspect(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path",), ("path",))
    canonical, node, tag = _study_context(worker, model_tag, args["path"])
    steps = _study_step_rows(node, canonical)
    associations = _solver_associations(worker, model_tag)
    snapshot = _probe_snapshot(node, ("getLastComputationTime", "getLastComputationDate",
                                      "getLastComputationVersion", "isGenPlots", "isGenConv",
                                      "isGenIntermediatePlots", "isStoreSolution", "isPlotUndefVals",
                                      "isStoreCompleteHistory"))
    return {
        "path": canonical,
        "study": tag,
        "label": call_probe(node, "label")["value"] if call_probe(node, "label")["ok"] else None,
        "steps": steps,
        "step_count": len(steps),
        "solver_sequences": associations.get(tag, []),
        "attached_solver_count": len(associations.get(tag, [])),
        "settings": snapshot["values"],
        "settings_read_errors": snapshot["errors"],
        "allowlist_entry_required": snapshot["allowlist_entry_required"],
        "run_readback": {
            "getLastComputationTime": snapshot["values"].get("getLastComputationTime"),
            "getLastComputationDate": snapshot["values"].get("getLastComputationDate"),
            "getLastComputationVersion": snapshot["values"].get("getLastComputationVersion"),
        },
        "notes": [
            "physics activation is read per step with solveFor() and reported inside each step row",
        ],
    }


def _study_step_rows(study: Any, study_path: Mapping[str, Any]) -> list[dict[str, Any]]:
    container = _call(study, "feature")
    rows: list[dict[str, Any]] = []
    for tag in tag_list(container):
        child = _call(container, "get", tag)
        type_probe = call_probe(child, "type")
        type_id = type_probe["value"] if type_probe["ok"] and isinstance(type_probe["value"], str) else node_type(child)
        properties = _probe_property_table(child)
        rows.append({
            "path": {"segments": list(study_path["segments"]) + [{"collection": "feature", "tag": tag}]},
            "tag": tag,
            "type_id": type_id,
            "type_source": STUDY_STEP_TYPE_SOURCES.get(type_id or ""),
            "label": call_probe(child, "label")["value"] if call_probe(child, "label")["ok"] else None,
            "generates_equations": (type_id in STUDY_DEFINING_STEPS) if type_id else None,
            "properties": properties["values"],
            "property_metadata": properties["metadata"],
            "documented_properties": sorted(STUDY_STEP_PROPERTIES.get(type_id or "", frozenset())),
            "physics_activation": _activation_state(child),
            "activation_read_errors": properties.get("value_errors"),
            "allowlist_entry_required": properties.get("allowlist_entry_required"),
        })
    return rows


def _activation_state(step: Any) -> dict[str, Any]:
    """Read the documented physics/variables activation decisions of one step."""
    out: dict[str, Any] = {}
    solve_for = call_probe(step, "solveFor")
    if solve_for["ok"]:
        out["solveFor_probe"] = _jsonable(solve_for["value"])
    entry = call_probe(step, "getEntryKeys", "activate")
    if entry["ok"]:
        keys = [str(item) for item in entry["value"]] if isinstance(entry["value"], (list, tuple)) else []
        values: dict[str, Any] = {}
        for key in keys:
            read = call_probe(step, "getString", "activate", key)
            values[key] = read["value"] if read["ok"] else None
        out["activate"] = values
    out["allowlist_entry_required"] = sorted(
        {str(error.get("allowlist_entry_required")) for error in
         (solve_for["error"], entry["error"]) if isinstance(error, Mapping)
         and error.get("allowlist_entry_required")}
    )
    return out


def study_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "remove_solver"), ("path",))
    remove_solver = args.get("remove_solver")
    remove_solver = False if remove_solver is None else require_bool(remove_solver, "remove_solver")
    canonical, node, tag = _study_context(worker, model_tag, args["path"])
    model = bound_model(worker, model_tag)
    associations = _solver_associations(worker, model_tag).get(tag, [])
    if associations and not remove_solver:
        raise ExecutionContractError(
            "SOLVER_SEQUENCE_EXISTS",
            f"study {tag!r} has {len(associations)} associated solver sequence(s) "
            f"({[row['solver'] for row in associations]}); pass remove_solver=true to remove them explicitly "
            "with the study (the study is never removed while silently leaving an orphan solver)",
        )
    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False
    for row in associations:
        solver_tag = row["solver"]
        sol_container = _call(model, "sol")
        try:
            _call(sol_container, "remove", solver_tag)
        except ExecutionContractError as exc:
            execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
            failed.append({"step": "solver_remove", "solver": solver_tag,
                           "error": describe_engine_failure(exc, "remove")})
            not_executed.append({"step": "study_remove", "study": tag,
                                 "reason": "the study was not removed because its solver removal failed"})
            break
        remaining = tag_list(sol_container)
        if solver_tag in remaining:
            execution_state_unknown = True
            failed.append({"step": "solver_remove", "solver": solver_tag,
                           "error": {"code": "EXECUTION_STATE_UNKNOWN",
                                     "message": "the solver tag is still listed after remove()"}})
            not_executed.append({"step": "study_remove", "study": tag,
                                 "reason": "the study was not removed because its solver removal is unverified"})
            break
        applied.append({"step": "solver_remove", "solver": solver_tag, "readback": remaining})
    else:
        container = _call(model, "study")
        before = tag_list(container)
        try:
            _call(container, "remove", tag)
        except ExecutionContractError as exc:
            execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
            failed.append({"step": "study_remove", "study": tag,
                           "error": describe_engine_failure(exc, "remove")})
        else:
            after = tag_list(container)
            if tag in after:
                execution_state_unknown = True
                failed.append({"step": "study_remove", "study": tag,
                               "error": {"code": "EXECUTION_STATE_UNKNOWN",
                                         "message": "the study tag is still listed after remove()"}})
            else:
                applied.append({"step": "study_remove", "study": tag,
                                "readback_before": before, "readback_after": after})
    result = {
        "path": canonical,
        "study": tag,
        "remove_solver": remove_solver,
        "associated_solvers": [row["solver"] for row in associations],
        "solver_handling": {
            "policy": "association is read with model.sol(<tag>).study(); with remove_solver=false an "
                      "associated sequence is a pre-write refusal, never a silent orphan",
        },
    }
    result.update(_status(applied, failed, not_executed, execution_state_unknown))
    result["applied"] = applied
    result["failed"] = failed
    result["not_executed"] = not_executed
    return result


def study_step_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("study", "tag", "type_id", "properties"),
                               ("study", "tag", "type_id", "properties"))
    tag = validate_tag(args["tag"])
    type_id = _require_registered_type(args["type_id"], STUDY_STEP_TYPE_SOURCES, "type_id")
    definition = property_definition(args["properties"], "properties")
    study_path, study, study_tag = _study_context(worker, model_tag, args["study"], label="study")
    container = _call(study, "feature")
    if tag in tag_list(container):
        current = node_type(_call(container, "get", tag))
        if current is not None and current != type_id:
            raise ExecutionContractError(
                "TYPE_CONFLICT",
                f"study step {tag!r} already exists with type {current!r} (requested {type_id!r})",
            )
        raise ExecutionContractError("TAG_CONFLICT", f"study step {tag!r} already exists")
    _call(study, "create", tag, type_id)
    readback_tags = tag_list(container)
    if tag not in readback_tags:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"study step {tag!r} was created but the post-create tag readback does not show it",
        )
    node = _call(container, "get", tag)
    type_readback = node_type(node)
    path = {"segments": list(study_path["segments"]) + [{"collection": "feature", "tag": tag}]}
    applied: list[dict[str, Any]] = [{"step": "create", "method": "study(...).create(tag, type)",
                                      "requested": {"tag": tag, "type_id": type_id},
                                      "readback": {"tags": readback_tags, "type": type_readback}}]
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False
    property_source: str | None = None
    if definition:
        try:
            payload, property_source = _definition_payload(node, definition, STUDY_STEP_PROPERTIES.get(type_id),
                                                           label=f"properties for {type_id}")
        except ExecutionContractError as exc:
            failed.append({"step": "properties", "error": {"code": exc.code, "message": str(exc)},
                           "partial_change": True, "execution_state_unknown": False})
            property_source = "refused"
            payload = []
        if payload:
            write = _property_write(path, worker, model_tag, payload)
            applied.extend({"step": "property", **row} for row in write["applied"])
            failed.extend({"step": "property", **row} for row in write["failed"])
            not_executed.extend({"step": "property", **row} for row in write["not_executed"])
            execution_state_unknown = execution_state_unknown or write["execution_state_unknown"]
    else:
        property_source = "empty"
    readback = _readback_block(node, ("type", "solveFor", "getString"))
    result = {
        "path": path,
        "study": study_tag,
        "tag": tag,
        "type_id": type_id,
        "type_source": _type_source(type_id, STUDY_STEP_TYPE_SOURCES),
        "type_readback": type_readback,
        "generates_equations": type_id in STUDY_DEFINING_STEPS,
        "property_source": property_source,
        "documented_properties": sorted(STUDY_STEP_PROPERTIES.get(type_id, frozenset())),
        "properties": {name: row.get("readback") for name, row in
                       {row.get("name"): row for row in applied if isinstance(row, Mapping)}.items()},
        "solver_generation_note": "a defining step is turned into a solver contract by "
                                  "study.solver_generate (createAutoSequences) or by study.run",
    }
    result.update(_completion(dispatched=True, applied=applied, failed=failed, not_executed=not_executed,
                              readback=readback, require_readback=False))
    if execution_state_unknown:
        result["execution_state_unknown"] = True
        result["partial_change"] = True
        result["ok"] = False
        if not failed:
            result["status"] = "EXECUTION_STATE_UNKNOWN"
    return result


def study_step_update(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "properties"), ("path", "properties"))
    definition = property_definition(args["properties"], "properties")
    canonical, node, parent_path, tag = _feature_context(worker, model_tag, args["path"], label="path")
    type_probe = call_probe(node, "type")
    type_id = type_probe["value"] if type_probe["ok"] and isinstance(type_probe["value"], str) else node_type(node)
    payload, property_source = _definition_payload(node, definition, STUDY_STEP_PROPERTIES.get(type_id or ""),
                                                   label=f"properties for {type_id or 'study step'}")
    if not payload:
        raise ExecutionContractError("INVALID_REQUEST", "properties must contain at least one assignment")
    write = _property_write(canonical, worker, model_tag, payload)
    result: dict[str, Any] = {
        "path": canonical,
        "parent_path": parent_path,
        "tag": tag,
        "type_id": type_id,
        "property_source": property_source,
        "applied": [{"step": "property", **row} for row in write["applied"]],
        "failed": [{"step": "property", **row} for row in write["failed"]],
        "not_executed": [{"step": "property", **row} for row in write["not_executed"]],
        "properties": write["readback_values"],
        "solver_sequence_interaction": _sequence_interaction(worker, model_tag, parent_path),
    }
    result.update(_status(result["applied"], result["failed"], result["not_executed"],
                          bool(write["execution_state_unknown"])))
    return result


def _sequence_interaction(worker: Any, model_tag: str, step_parent_path: Any) -> dict[str, Any]:
    """Document how an edited study step relates to an attached solver sequence.

    ``createAutoSequences`` (study.solver_generate/study.run) only generates a
    sequence ``if the solver sequence has not been edited``
    (comsol_api_solver.51.3, "model.study() ... createAutoSequences"), so a
    sequence that a user edited by hand is preserved rather than silently
    overwritten.  This block reports the sequences currently attached to the
    study so a caller can see the interaction without guessing.
    """
    study_tag = _study_tag_of(step_parent_path)
    attached = _solver_associations(worker, model_tag).get(study_tag, [])
    return {
        "policy": ("editing a study step does not rewrite an attached solver sequence; a sequence is "
                   "generated or refreshed only by study.solver_generate/study.run, and a sequence "
                   "edited by hand is preserved (createAutoSequences generates only when the solver "
                   "sequence has not been edited)"),
        "study": study_tag,
        "attached_sequences": [row["solver"] for row in attached],
        "attached_sequence_details": attached,
    }


def _study_tag_of(parent_path: Mapping[str, Any]) -> str:
    for segment in NodePath.from_wire(parent_path).segments:
        if segment.collection == "study":
            return str(segment.tag)
    return ""


def study_step_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path",), ("path",))
    canonical, node, parent_path, tag = _feature_context(worker, model_tag, args["path"], label="path")
    type_probe = call_probe(node, "type")
    type_id = type_probe["value"] if type_probe["ok"] else node_type(node)
    study_path, study, study_tag = _study_context(worker, model_tag, parent_path, label="parent_path")
    container = _call(study, "feature")
    before = tag_list(container)
    _call(container, "remove", tag)
    after = tag_list(container)
    if tag in after:
        return {
            "path": canonical,
            "study": study_tag,
            "tag": tag,
            "type_id": type_id,
            **_completion(dispatched=True, applied=[], failed=[], not_executed=[], readback={},
                          error={"code": "EXECUTION_STATE_UNKNOWN",
                                 "message": f"study step {tag!r} was removed but the tag readback still shows it"}),
        }
    return {
        "path": canonical,
        "study": study_tag,
        "tag": tag,
        "type_id": type_id,
        "removed": True,
        "tag_readback_before": before,
        "tag_readback_after": after,
        "remaining_steps": after,
        **_completion(dispatched=True,
                      applied=[{"step": "remove", "method": "study(...).feature().remove",
                                "tag": tag, "readback": after}],
                      failed=[], not_executed=[],
                      readback=_readback_block(study, ("getLastComputationTime",))),
    }


def study_physics_activation(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("step", "activation"), ("step", "activation"))
    activation = require_mapping(args["activation"], "activation")
    unknown = sorted(set(activation) - set(PHYSICS_ACTIVATION_KEYS))
    if unknown:
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"activation has unsupported groups: {unknown}; supported: {list(PHYSICS_ACTIVATION_KEYS)} "
            "(physics -> setSolveFor/solveFor, coupling -> the activateCoupling String Map)",
        )
    if not activation:
        raise ExecutionContractError("INVALID_REQUEST", "activation must contain at least one entry")
    canonical, node, parent_path, tag = _feature_context(worker, model_tag, args["step"], label="step")
    type_probe = call_probe(node, "type")
    type_id = type_probe["value"] if type_probe["ok"] else node_type(node)
    if type_id is not None and type_id not in STUDY_DEFINING_STEPS:
        raise ExecutionContractError(
            "API_UNSUPPORTED",
            f"study step type {type_id!r} does not generate equations, so it has no physics selection "
            f"(the documented common study-step properties apply to equation-generating steps)",
        )
    entries: list[tuple[str, str, bool]] = []
    for group in PHYSICS_ACTIVATION_KEYS:
        mapping = activation.get(group)
        if mapping is None:
            continue
        mapping = require_mapping(mapping, f"activation.{group}")
        if not mapping:
            raise ExecutionContractError("INVALID_REQUEST", f"activation.{group} must not be empty")
        for entity, solve in mapping.items():
            entity = require_string(entity, f"activation.{group} key", max_length=128)
            entries.append((group, entity, require_bool(solve, f"activation.{group}[{entity!r}]")))
    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False
    for index, (group, entity, solve) in enumerate(entries):
        if group == "physics":
            method = "setSolveFor"
            try:
                before = call_probe(node, "solveFor", entity)
                if before["ok"] and bool(before["value"]) is solve:
                    applied.append({"group": group, "entity": entity, "requested": solve,
                                    "readback": bool(before["value"]),
                                    "note": "already in the requested state; no write was needed"})
                    continue
                _call(node, "setSolveFor", entity, solve)
            except ExecutionContractError as exc:
                error = describe_engine_failure(exc, "setSolveFor")
                execution_state_unknown = execution_state_unknown or exc.code == "EXECUTION_STATE_UNKNOWN"
                failed.append({"group": group, "entity": entity, "requested": solve, "error": error})
                not_executed.extend({"group": g, "entity": e, "requested": s} for g, e, s in entries[index + 1:])
                break
            readback = call_probe(node, "solveFor", entity)
            if readback["ok"] and bool(readback["value"]) is solve:
                applied.append({"group": group, "entity": entity, "method": method,
                                "requested": solve, "readback": bool(readback["value"])})
            else:
                execution_state_unknown = True
                failed.append({"group": group, "entity": entity, "requested": solve,
                               "readback": _jsonable(readback["value"]),
                               "error": {"code": "EXECUTION_STATE_UNKNOWN",
                                         "message": f"solveFor({entity!r}) readback did not confirm the request"}})
                not_executed.extend({"group": g, "entity": e, "requested": s} for g, e, s in entries[index + 1:])
                break
        else:
            property_name = _SOLVE_FOR_PROPERTY[group]
            row = property_rows(node, [property_name]).get(property_name, {})
            if row.get("metadata_status") != "KNOWN":
                raise ExecutionContractError(
                    "API_UNSUPPORTED",
                    f"authoritative value metadata is unavailable for the {property_name!r} keyed property, "
                    "so the coupling activation is refused before the write",
                )
            from ._g2_engine import property_entry_set  # local import: the R03 keyed-entry path

            signature = {"boolean": "boolean", "string": "java.lang.String",
                         "expression": "java.lang.String"}.get(str(row.get("kind")))
            value = {"kind": "boolean", "shape": [], "data": solve, "java_signature": signature}
            envelope = property_entry_set(worker, model_tag, canonical, property_name, entity, value)
            data = envelope.get("data") if isinstance(envelope, Mapping) else {}
            if envelope.get("success"):
                applied.append({"group": group, "entity": entity, "method": f"setEntry({property_name})",
                                "requested": solve, "readback": (data or {}).get("readback")})
            else:
                execution_state_unknown = execution_state_unknown or bool(envelope.get("execution_state_unknown"))
                failed.append({"group": group, "entity": entity, "requested": solve,
                               "error": envelope.get("error") or {"code": "VERIFICATION_FAILED",
                                                                  "message": "keyed write was not verified"}})
                not_executed.extend({"group": g, "entity": e, "requested": s} for g, e, s in entries[index + 1:])
                break
    study_tag = _study_tag_of(parent_path)
    return {
        "path": canonical,
        "study": study_tag,
        "step": tag,
        "type_id": type_id,
        "requested_entries": [{"group": group, "entity": entity, "solve": solve} for group, entity, solve in entries],
        "applied": applied,
        "failed": failed,
        "not_executed": not_executed,
        "activation_readback": _activation_state(node),
        "manual_solver_policy": "physics activation is a study-step setting; a manually edited solver "
                                "sequence is not rewritten by this operation",
        **_status(applied, failed, not_executed, execution_state_unknown),
    }


def study_solver_generate(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("study", "replace_existing"), ("study", "replace_existing"))
    replace_existing = require_bool(args["replace_existing"], "replace_existing")
    study_path, study, study_tag = _study_context(worker, model_tag, args["study"], label="study")
    before = _solver_associations(worker, model_tag).get(study_tag, [])
    if before and not replace_existing:
        raise ExecutionContractError(
            "SOLVER_SEQUENCE_EXISTS",
            f"study {study_tag!r} already has associated solver sequence(s) "
            f"({[row['solver'] for row in before]}); pass replace_existing=true to ask COMSOL to "
            "generate the default sequence explicitly",
        )
    steps = _feature_rows(study)
    if not any(row.get("type_id") in STUDY_DEFINING_STEPS for row in steps):
        raise ExecutionContractError(
            "INVALID_REQUEST",
            f"study {study_tag!r} has no equation-generating step; createAutoSequences would generate "
            "nothing useful. Steps: {steps}".format(steps=[row.get("tag") for row in steps]),
        )
    started = time.monotonic()
    error: dict[str, Any] | None = None
    try:
        _call(study, "createAutoSequences", "sol")
    except ExecutionContractError as exc:
        error = describe_engine_failure(exc, "createAutoSequences")
    duration_s = round(time.monotonic() - started, 3)
    after = _solver_associations(worker, model_tag).get(study_tag, [])
    created = [row["solver"] for row in after if row["solver"] not in {item["solver"] for item in before}]
    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    if error is not None:
        failed.append({"step": "createAutoSequences", "error": error})
    elif created:
        applied.append({"step": "createAutoSequences", "method": "study(...).createAutoSequences(\"sol\")",
                        "created": created, "readback": [row["solver"] for row in after]})
    else:
        # COMSOL documents createAutoSequences as generating only when the
        # solver sequence has not been edited; an unchanged association list is
        # therefore a documented no-op, not a failure - but it is not "created".
        failed.append({"step": "createAutoSequences",
                       "error": {"code": "NO_SEQUENCE_CREATED",
                                 "message": "createAutoSequences(\"sol\") returned without associating a new "
                                            "solver sequence; COMSOL only generates one when the sequence "
                                            "has not been edited"}})
    result = {
        "study": study_path,
        "study_tag": study_tag,
        "replace_existing": replace_existing,
        "solver_sequences_before": [row["solver"] for row in before],
        "solver_sequences_after": [row["solver"] for row in after],
        "created": created,
        "duration_s": duration_s,
        "auto_sequence_type": "sol",
        "sequence_type_vocabulary": sorted(STUDY_AUTO_SEQUENCE_TYPES),
        "manual_solver_policy": {
            "policy": "createAutoSequences creates an attached solver sequence with default settings when the "
                      "sequence has not been edited; a user-edited sequence is preserved and this operation "
                      "reports the unchanged association list instead of claiming a rewrite",
            "source": "comsol_api_general.47.60 createAutoSequences; comsol_api_general.47.58 createAutoSequence",
        },
        "steps": [row.get("tag") for row in steps],
    }
    result.update(_completion(dispatched=True, applied=applied, failed=failed, not_executed=[],
                              readback={"state": {"associations_after": after},
                                        "errors": {},
                                        "allowlist_entry_required": sorted(
                                            {str(row["study_readback_error"].get("allowlist_entry_required"))
                                             for row in after
                                             if isinstance(row.get("study_readback_error"), Mapping)
                                             and row["study_readback_error"].get("allowlist_entry_required")}),
                                        "readable": True},
                              error=None, require_readback=True))
    if error is None and not created:
        result["status"] = "NOT_EXECUTED"
        result["ok"] = False
        result["execution_state_unknown"] = False
    return result


def study_run(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("study", "resources", "timeout_s"), ("study",))
    resources = args.get("resources")
    if resources is not None:
        require_mapping(resources, "resources")
    timeout_s = args.get("timeout_s")
    if timeout_s is not None:
        from ._g3_common import require_number

        require_number(timeout_s, "timeout_s")
    study_path, study, study_tag = _study_context(worker, model_tag, args["study"], label="study")
    steps = _feature_rows(study)
    if not steps:
        raise ExecutionContractError(
            "INVALID_REQUEST", f"study {study_tag!r} has no study step; there is nothing to compute"
        )
    before = _probe_snapshot(study, ("getLastComputationTime", "getLastComputationDate",
                                     "getLastComputationVersion"))
    started = time.monotonic()
    error: dict[str, Any] | None = None
    try:
        _call(study, "run")
    except ExecutionContractError as exc:
        error = describe_engine_failure(exc, "run")
    duration_s = round(time.monotonic() - started, 3)
    after = _probe_snapshot(study, ("getLastComputationTime", "getLastComputationDate",
                                    "getLastComputationVersion"))
    associations = _solver_associations(worker, model_tag).get(study_tag, [])
    timestamp_changed = before["values"] != after["values"] and bool(after["values"])
    result = {
        "study": study_path,
        "study_tag": study_tag,
        "steps": [row.get("tag") for row in steps],
        "duration_s": duration_s,
        "requested_resources": resources,
        "requested_timeout_s": timeout_s,
        "computation_before": before["values"],
        "computation_after": after["values"],
        "computation_timestamp_changed": timestamp_changed,
        "solver_sequences": [row["solver"] for row in associations],
        "long_task_semantics": {
            "owner": "control_plane",
            "engine_call": "study(<tag>).run() is executed synchronously on the serial worker queue",
            "caller_deadline": "timeout_s/rpc deadlines limit caller waiting only; an expired wait does not "
                               "prove that COMSOL stopped computing, so the original job must be inspected "
                               "rather than resubmitted",
            "health": "the worker's health/status socket stays responsive during a solve",
        },
        "notes": [
            "study.run() corresponds to Compute in the Desktop and regenerates the solver sequence for an "
            "unedited study",
            "the timestamps are the study's own record of the last computation and are the only verified "
            "post-run evidence this layer can read back",
        ],
    }
    result.update(_completion(
        dispatched=True,
        applied=[{"step": "run", "method": "study.run", "duration_s": duration_s,
                  "timestamp_changed": timestamp_changed}],
        failed=[], not_executed=[],
        readback={"state": after["values"], "errors": after["errors"],
                  "allowlist_entry_required": sorted(set(before["allowlist_entry_required"])
                                                     | set(after["allowlist_entry_required"])),
                  "readable": after["readable"]},
        error=error, require_readback=False))
    if error is None and not timestamp_changed:
        result["ok"] = True
        result["status"] = "DISPATCHED_UNVERIFIED"
        result["execution_state_unknown"] = True
        result["partial_change"] = True
        result["notes"].append(
            "the run call returned normally but the study's last-computation timestamp did not change and "
            "could not confirm that a new computation was recorded"
        )
    return result


# ---------------------------------------------------------------------------
# solver domain
# ---------------------------------------------------------------------------


def solver_list(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("filter",))
    filter_spec = args.get("filter")
    filter_spec = {} if filter_spec is None else require_mapping(filter_spec, "filter")
    unknown = sorted(set(filter_spec) - {"study", "tag"})
    if unknown:
        raise ExecutionContractError(
            "INVALID_REQUEST", f"filter has unsupported fields: {unknown}; supported: ['study', 'tag']"
        )
    study_filter = filter_spec.get("study")
    if study_filter is not None:
        study_filter = require_string(study_filter, "filter.study", max_length=63)
    tag_filter = filter_spec.get("tag")
    if tag_filter is not None:
        tag_filter = require_string(tag_filter, "filter.tag", max_length=63)
    model = bound_model(worker, model_tag)
    container = _call(model, "sol")
    rows: list[dict[str, Any]] = []
    for tag in tag_list(container):
        if tag_filter is not None and tag != tag_filter:
            continue
        node = _call(container, "get", tag)
        study_probe = call_probe(node, "study")
        study_tag = study_probe["value"] if study_probe["ok"] and isinstance(study_probe["value"], str) else None
        if study_filter is not None and study_tag != study_filter:
            continue
        snapshot = _probe_snapshot(node, ("isEmpty", "isInitialized", "getDefaultSolnum",
                                          "getSequenceType", "hasProblems", "hasProblemsOrInformation",
                                          "getPVals", "getPNames", "isAttached"))
        features = _feature_rows(node)
        rows.append({
            "path": {"segments": [{"collection": "sol", "tag": tag}]},
            "solver": tag,
            "label": call_probe(node, "label")["value"] if call_probe(node, "label")["ok"] else None,
            "study": study_tag,
            "study_readback_error": None if study_probe["ok"] else study_probe["error"],
            "is_attached": snapshot["values"].get("isAttached"),
            "is_empty": snapshot["values"].get("isEmpty"),
            "is_initialized": snapshot["values"].get("isInitialized"),
            "default_solnum": snapshot["values"].get("getDefaultSolnum"),
            "sequence_type": snapshot["values"].get("getSequenceType"),
            "has_problems": snapshot["values"].get("hasProblems"),
            "parameter_names": snapshot["values"].get("getPNames"),
            "parameter_values": snapshot["values"].get("getPVals"),
            "features": features,
            "feature_count": len(features),
            "read_errors": snapshot["errors"],
            "allowlist_entry_required": snapshot["allowlist_entry_required"],
        })
    return {
        "filter": filter_spec,
        "solvers": rows,
        "solver_count": len(rows),
        "association_semantics": "model.sol(<tag>).study() returns the associated study tag; attach() is what "
                                 "makes a sequence part of the study sequence",
    }


def _solver_tree(node: Any, depth: int, *, prefix: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tag in _feature_tags(node):
        child = _call(node, "feature", tag)
        path = {"segments": list(prefix["segments"]) + [{"collection": "feature", "tag": tag}]}
        snapshot = _probe_snapshot(child, ("hasError", "hasWarning", "hasInformation", "hasProblem",
                                           "hasProblemOrInformation", "getM", "getN", "getNnz"))
        problems: list[str] = []
        problem_container = call_probe(child, "problem")
        if problem_container["ok"] and problem_container["value"] is not None:
            problems = tag_list(problem_container["value"])
        row: dict[str, Any] = {
            "path": path,
            "tag": tag,
            "type_id": node_type(child),
            "type_source": _type_source(node_type(child) or "", SOLVER_FEATURE_TYPE_SOURCES),
            "label": call_probe(child, "label")["value"] if call_probe(child, "label")["ok"] else None,
            "has_error": snapshot["values"].get("hasError"),
            "has_warning": snapshot["values"].get("hasWarning"),
            "has_information": snapshot["values"].get("hasInformation"),
            "has_problem": snapshot["values"].get("hasProblem"),
            "problems": problems,
            "settings": _probe_property_table(child)["values"],
            "settings_metadata": _probe_property_table(child)["metadata"],
            "read_errors": snapshot["errors"],
            "allowlist_entry_required": snapshot["allowlist_entry_required"],
            "children": [],
        }
        child_container = call_probe(child, "feature")
        if depth > 1 and child_container["ok"] and child_container["value"] is not None:
            try:
                row["children"] = _solver_tree(child, depth - 1, prefix={"segments": path["segments"]})
            except ExecutionContractError as exc:
                row["children_error"] = describe_engine_failure(exc, "feature")
        rows.append(row)
    return rows


def solver_inspect(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "depth"), ("path",))
    depth = args.get("depth")
    depth = 2 if depth is None else require_int(depth, "depth", minimum=1, maximum=4)
    canonical, node = resolve_path(worker, model_tag, args["path"], label="path")
    parsed = NodePath.from_wire(canonical)
    root = parsed.segments[-1].collection if parsed.segments else None
    if root == "sol":
        solver_tag = validate_tag(parsed.segments[0].tag, "sol")
        snapshot = _probe_snapshot(node, ("isEmpty", "isInitialized", "getDefaultSolnum",
                                          "getSequenceType", "hasProblems", "hasProblem",
                                          "getErrorMessage", "getInformationMessage", "getWarningMessage",
                                          "getPVals", "getPNames", "getParamVals", "getParamNames",
                                          "getNStepsBack", "isAttached", "study"))
        return {
            "kind": "solver_sequence",
            "path": canonical,
            "solver": solver_tag,
            "label": call_probe(node, "label")["value"] if call_probe(node, "label")["ok"] else None,
            "study": snapshot["values"].get("study"),
            "is_attached": snapshot["values"].get("isAttached"),
            "is_empty": snapshot["values"].get("isEmpty"),
            "is_initialized": snapshot["values"].get("isInitialized"),
            "default_solnum": snapshot["values"].get("getDefaultSolnum"),
            "sequence_type": snapshot["values"].get("getSequenceType"),
            "has_problems": snapshot["values"].get("hasProblems"),
            "error_message": snapshot["values"].get("getErrorMessage"),
            "information_message": snapshot["values"].get("getInformationMessage"),
            "warning_message": snapshot["values"].get("getWarningMessage"),
            "parameter_names": snapshot["values"].get("getPNames"),
            "parameter_values": snapshot["values"].get("getPVals"),
            "features": _solver_tree(node, depth, prefix=canonical),
            "read_errors": snapshot["errors"],
            "allowlist_entry_required": snapshot["allowlist_entry_required"],
            "type_vocabulary": {
                "verified_count": len(SOLVER_FEATURE_TYPE_SOURCES),
                "syntax_verified": sorted(name for name, source in SOLVER_FEATURE_TYPE_SOURCES.items()
                                          if source.endswith("syntax")),
                "context_verified": sorted(name for name, source in SOLVER_FEATURE_TYPE_SOURCES.items()
                                           if "contexts.xml" in source),
            },
        }
    if root != "feature":
        raise ExecutionContractError(
            "INVALID_NODE_PATH", "path must be a solver sequence (sol) or a solver feature"
        )
    canonical, node, parent_path, tag = _feature_context(worker, model_tag, args["path"], label="path")
    type_id = node_type(node)
    properties = _probe_property_table(node)
    snapshot = _probe_snapshot(node, ("hasError", "hasWarning", "hasInformation", "hasProblem",
                                      "hasProblemOrInformation"))
    problems: list[str] = []
    container = call_probe(node, "problem")
    if container["ok"] and container["value"] is not None:
        problems = tag_list(container["value"])
    return {
        "kind": "solver_feature",
        "path": canonical,
        "parent_path": parent_path,
        "tag": tag,
        "type_id": type_id,
        "type_source": _type_source(type_id or "", SOLVER_FEATURE_TYPE_SOURCES),
        "label": call_probe(node, "label")["value"] if call_probe(node, "label")["ok"] else None,
        "settings": properties["values"],
        "settings_metadata": properties["metadata"],
        "documented_properties": sorted(SOLVER_DOCUMENTED_PROPERTIES.get(type_id or "", frozenset())),
        "problem_nodes": problems,
        "status": {key: snapshot["values"].get(key) for key in
                   ("hasError", "hasWarning", "hasInformation", "hasProblem")},
        "children": _solver_tree(node, depth, prefix=canonical),
        "children_depth": depth,
        "read_errors": {**snapshot["errors"], **(properties.get("value_errors") or {})},
        "allowlist_entry_required": sorted(
            set(snapshot["allowlist_entry_required"]) | set(properties.get("allowlist_entry_required") or [])
        ),
    }


def solver_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("tag", "study"), ("tag", "study"))
    tag = validate_tag(args["tag"])
    study_path, study, study_tag = _study_context(worker, model_tag, args["study"], label="study")
    model = bound_model(worker, model_tag)
    container = _call(model, "sol")
    if tag in tag_list(container):
        raise ExecutionContractError("TAG_CONFLICT", f"solver sequence {tag!r} already exists")
    existing = _solver_associations(worker, model_tag).get(study_tag, [])
    _call(container, "create", tag, study_tag)
    after = tag_list(container)
    if tag not in after:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"solver sequence {tag!r} was created but the post-create tag readback does not show it",
        )
    node = _call(container, "get", tag)
    study_probe = call_probe(node, "study")
    applied: list[dict[str, Any]] = [{"step": "create", "method": "model.sol().create(tag, studytag)",
                                      "requested": {"tag": tag, "study": study_tag},
                                      "readback": {"tags": after, "study": _jsonable(study_probe["value"])}}]
    failed: list[dict[str, Any]] = []
    if not (study_probe["ok"] and study_probe["value"] == study_tag):
        failed.append({"step": "study_association", "requested": study_tag,
                       "readback": _jsonable(study_probe["value"]),
                       "error": {"code": "EXECUTION_STATE_UNKNOWN",
                                 "message": "sol(<tag>).study() readback did not confirm the study association"}})
    readback = _readback_block(node, ("study", "isEmpty", "isInitialized", "getSequenceType"))
    result = {
        "path": {"segments": [{"collection": "sol", "tag": tag}]},
        "solver": tag,
        "study": study_path,
        "study_tag": study_tag,
        "study_association_readback": _jsonable(study_probe["value"]),
        "tag_readback": after,
        "preexisting_study_sequences": [row["solver"] for row in existing],
        "features": _feature_rows(node),
        "create_semantics": "model.sol().create(<tag>,<studytag>) adds a sequence containing one StudyStep "
                            "feature bound to the study step (comsol_api_general.47.58); use attach() to make "
                            "the sequence part of the study sequence",
    }
    result.update(_completion(dispatched=True, applied=applied, failed=failed, not_executed=[],
                              readback=readback, require_readback=False))
    return result


def solver_feature_create(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("parent", "tag", "type_id", "properties"),
                               ("parent", "tag", "type_id"))
    tag = validate_tag(args["tag"])
    type_id = _require_registered_type(args["type_id"], SOLVER_FEATURE_TYPE_SOURCES, "type_id")
    definition = args.get("properties")
    definition = {} if definition is None else property_definition(definition, "properties")
    parent_path, parent = resolve_path(worker, model_tag, args["parent"], label="parent")
    parsed = NodePath.from_wire(parent_path)
    if not parsed.segments or parsed.segments[-1].collection not in {"sol", "feature"}:
        raise ExecutionContractError(
            "INVALID_NODE_PATH",
            "parent must be a solver sequence (sol) or a solver feature "
            "(sol(<tag>).feature(<ftag>).create(<f2tag>,<oper>))",
        )
    container = _call(parent, "feature")
    existing = tag_list(container)
    if tag in existing:
        current = node_type(_call(container, "get", tag))
        if current is not None and current != type_id:
            raise ExecutionContractError(
                "TYPE_CONFLICT",
                f"solver feature {tag!r} already exists with type {current!r} (requested {type_id!r})",
            )
        raise ExecutionContractError("TAG_CONFLICT", f"solver feature {tag!r} already exists")
    _call(parent, "create", tag, type_id)
    readback_tags = tag_list(container)
    if tag not in readback_tags:
        raise ExecutionContractError(
            "EXECUTION_STATE_UNKNOWN",
            f"solver feature {tag!r} was created but the post-create tag readback does not show it",
        )
    node = _call(container, "get", tag)
    type_readback = node_type(node)
    path = {"segments": parsed.as_dict()["segments"] + [{"collection": "feature", "tag": tag}]}
    applied: list[dict[str, Any]] = [{"step": "create", "method": "sol(...).create(tag, oper)",
                                      "requested": {"tag": tag, "type_id": type_id},
                                      "readback": {"tags": readback_tags, "type": type_readback}}]
    failed: list[dict[str, Any]] = []
    not_executed: list[dict[str, Any]] = []
    execution_state_unknown = False
    property_source: str | None = None
    if definition:
        try:
            payload, property_source = _definition_payload(node, definition,
                                                           SOLVER_DOCUMENTED_PROPERTIES.get(type_id),
                                                           label=f"properties for {type_id}")
        except ExecutionContractError as exc:
            failed.append({"step": "properties", "error": {"code": exc.code, "message": str(exc)},
                           "partial_change": True, "execution_state_unknown": False})
            property_source = "refused"
            payload = []
        if payload:
            write = _property_write(path, worker, model_tag, payload)
            applied.extend({"step": "property", **row} for row in write["applied"])
            failed.extend({"step": "property", **row} for row in write["failed"])
            not_executed.extend({"step": "property", **row} for row in write["not_executed"])
            execution_state_unknown = execution_state_unknown or write["execution_state_unknown"]
    else:
        property_source = "empty"
    readback = _readback_block(node, ("hasError", "hasWarning", "hasProblem"))
    result = {
        "path": path,
        "parent_path": parent_path,
        "tag": tag,
        "type_id": type_id,
        "type_source": _type_source(type_id, SOLVER_FEATURE_TYPE_SOURCES),
        "type_readback": type_readback,
        "property_source": property_source,
        "documented_properties": sorted(SOLVER_DOCUMENTED_PROPERTIES.get(type_id, frozenset())),
    }
    result.update(_completion(dispatched=True, applied=applied, failed=failed, not_executed=not_executed,
                              readback=readback, require_readback=False))
    if execution_state_unknown:
        result["execution_state_unknown"] = True
        result["partial_change"] = True
        result["ok"] = False
        if not failed:
            result["status"] = "EXECUTION_STATE_UNKNOWN"
    return result


def solver_feature_update(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "properties"), ("path", "properties"))
    definition = property_definition(args["properties"], "properties")
    canonical, node, parent_path, tag = _feature_context(worker, model_tag, args["path"], label="path")
    type_id = node_type(node)
    payload, property_source = _definition_payload(node, definition,
                                                   SOLVER_DOCUMENTED_PROPERTIES.get(type_id or ""),
                                                   label=f"properties for {type_id or 'solver feature'}")
    if not payload:
        raise ExecutionContractError("INVALID_REQUEST", "properties must contain at least one assignment")
    write = _property_write(canonical, worker, model_tag, payload)
    result: dict[str, Any] = {
        "path": canonical,
        "parent_path": parent_path,
        "tag": tag,
        "type_id": type_id,
        "property_source": property_source,
        "applied": [{"step": "property", **row} for row in write["applied"]],
        "failed": [{"step": "property", **row} for row in write["failed"]],
        "not_executed": [{"step": "property", **row} for row in write["not_executed"]],
        "properties": write["readback_values"],
        "manual_solver_policy": "editing a solver feature writes only the named properties of that feature; "
                                "manual properties such as control/stol/tlist are preserved unless they are "
                                "explicitly assigned",
    }
    result.update(_status(result["applied"], result["failed"], result["not_executed"],
                          bool(write["execution_state_unknown"])))
    result["readback"] = _readback_block(node, ("hasError", "hasWarning", "hasProblem"))
    return result


def solver_feature_remove(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path",), ("path",))
    canonical, node, parent_path, tag = _feature_context(worker, model_tag, args["path"], label="path")
    type_id = node_type(node)
    _, parent = resolve_path(worker, model_tag, parent_path, label="parent")
    container = _call(parent, "feature")
    before = tag_list(container)
    _call(container, "remove", tag)
    after = tag_list(container)
    if tag in after:
        return {
            "path": canonical,
            "parent_path": parent_path,
            "tag": tag,
            "type_id": type_id,
            **_completion(dispatched=True, applied=[], failed=[], not_executed=[], readback={},
                          error={"code": "EXECUTION_STATE_UNKNOWN",
                                 "message": f"solver feature {tag!r} was removed but the tag readback still shows it"}),
        }
    return {
        "path": canonical,
        "parent_path": parent_path,
        "tag": tag,
        "type_id": type_id,
        "removed": True,
        "tag_readback_before": before,
        "tag_readback_after": after,
        **_completion(dispatched=True,
                      applied=[{"step": "remove", "method": "sol(...).feature().remove",
                                "tag": tag, "readback": after}],
                      failed=[], not_executed=[],
                      readback=_readback_block(parent, ("isEmpty", "isInitialized", "getDefaultSolnum"))),
    }


def solver_run(worker: Any, model_tag: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    args = operation_arguments(arguments, ("path", "range", "timeout_s"), ("path",))
    range_spec = args.get("range")
    timeout_s = args.get("timeout_s")
    if timeout_s is not None:
        from ._g3_common import require_number

        require_number(timeout_s, "timeout_s")
    canonical, node, solver_tag = _solver_context(worker, model_tag, args["path"], label="path")
    call_args: tuple[Any, ...]
    method: str
    if range_spec is None:
        method, call_args = "runAll", ()
        range_label: dict[str, Any] = {"mode": "all"}
    else:
        spec = require_mapping(range_spec, "range")
        unknown = sorted(set(spec) - {"feature", "from", "to"})
        if unknown:
            raise ExecutionContractError(
                "INVALID_REQUEST",
                f"range has unsupported fields: {unknown}; supported: ['feature', 'from', 'to']",
            )
        feature = spec.get("feature")
        start = spec.get("from")
        stop = spec.get("to")
        tags = _feature_tags(node)
        if feature is not None and (start is not None or stop is not None):
            raise ExecutionContractError("INVALID_REQUEST", "range.feature cannot be combined with from/to")
        if feature is not None:
            feature = validate_tag(feature, "range.feature")
            if feature not in tags:
                raise ExecutionContractError(
                    "NODE_NOT_FOUND", f"range.feature {feature!r} does not exist; available: {tags}"
                )
            method, call_args = "run", (feature,)
            range_label = {"mode": "up_to", "feature": feature}
        elif start is not None or stop is not None:
            if start is None or stop is None:
                raise ExecutionContractError("INVALID_REQUEST", "range.from and range.to must be given together")
            start = validate_tag(start, "range.from")
            stop = validate_tag(stop, "range.to")
            for name, value in (("range.from", start), ("range.to", stop)):
                if value not in tags:
                    raise ExecutionContractError(
                        "NODE_NOT_FOUND", f"{name} {value!r} does not exist; available: {tags}"
                    )
            if tags.index(start) > tags.index(stop):
                raise ExecutionContractError(
                    "INVALID_REQUEST", f"range.from ({start!r}) is after range.to ({stop!r}) in the sequence"
                )
            method, call_args = "runFromTo", (start, stop)
            range_label = {"mode": "from_to", "from": start, "to": stop}
        else:
            raise ExecutionContractError("INVALID_REQUEST", "range requires feature or from+to")
    before = _readback_block(node, _SOLVER_READBACK)
    started = time.monotonic()
    error: dict[str, Any] | None = None
    try:
        _call(node, method, *call_args)
    except ExecutionContractError as exc:
        error = describe_engine_failure(exc, method)
    duration_s = round(time.monotonic() - started, 3)
    after = _readback_block(node, _SOLVER_READBACK)
    result = {
        "path": canonical,
        "solver": solver_tag,
        "method": method,
        "range": range_label,
        "duration_s": duration_s,
        "requested_timeout_s": timeout_s,
        "state_before": before["state"],
        "state_after": after["state"],
        "readback_allowlist_entry_required": after["allowlist_entry_required"],
        "long_task_semantics": {
            "owner": "control_plane",
            "engine_call": f"sol(<tag>).{method}(...) runs synchronously on the serial worker queue",
            "caller_deadline": "timeout_s limits caller waiting only; an expired wait does not prove that "
                               "COMSOL stopped, so inspect the original job instead of resubmitting",
        },
        "notes": [
            "runAll()/run(feature)/runFromTo(a,b) are the documented solver-sequence run forms; a manual "
            "solver configuration is executed as configured and is never regenerated by this operation",
        ],
    }
    result.update(_completion(dispatched=True,
                              applied=[{"step": method, "args": list(call_args), "duration_s": duration_s}],
                              failed=[], not_executed=[], readback=after, error=error))
    result["state_changed"] = before["state"] != after["state"]
    if error is None and not result["state_changed"]:
        result["status"] = "DISPATCHED_UNVERIFIED"
        result["execution_state_unknown"] = True
        result["partial_change"] = True
        result["ok"] = True
        result["notes"].append(
            "the run call returned normally but the readable solution-state keys did not change; the "
            "solution state is only confirmed by a solution readback (isEmpty/isInitialized/getDefaultSolnum)"
        )
    return result


# ---------------------------------------------------------------------------
# publishing table
# ---------------------------------------------------------------------------

OPERATIONS: dict[str, Callable[[Any, str, dict], dict]] = {
    "mesh.list": mesh_list,
    "mesh.create": mesh_create,
    "mesh.inspect": mesh_inspect,
    "mesh.feature_create": mesh_feature_create,
    "mesh.feature_update": mesh_feature_update,
    "mesh.feature_remove": mesh_feature_remove,
    "mesh.build": mesh_build,
    "mesh.clear": mesh_clear,
    "mesh.statistics": mesh_statistics,
    "mesh.quality": mesh_quality,
    "mesh.validate": mesh_validate,
    "study.list": study_list,
    "study.create": study_create,
    "study.inspect": study_inspect,
    "study.remove": study_remove,
    "study.step_create": study_step_create,
    "study.step_update": study_step_update,
    "study.step_remove": study_step_remove,
    "study.physics_activation": study_physics_activation,
    "study.solver_generate": study_solver_generate,
    "study.run": study_run,
    "solver.list": solver_list,
    "solver.inspect": solver_inspect,
    "solver.create": solver_create,
    "solver.feature_create": solver_feature_create,
    "solver.feature_update": solver_feature_update,
    "solver.feature_remove": solver_feature_remove,
    "solver.run": solver_run,
}

#: Operation id -> the argument names this layer accepts.  The control-plane
#: envelope (``model_ref``/``project_id``/``session_id``/``request_id``/
#: ``idempotency_key``/``expected_revision`` and the ActionResult fields) is not
#: part of the domain contract; these tuples are exactly the
#: ``operation_arguments`` allow-lists used by the implementations above,
#: exported so the schema can be compared with the action catalog instead of
#: trusting prose.
OPERATION_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "mesh.list": ("component",),
    "mesh.create": ("component", "tag", "geometry", "mode"),
    "mesh.inspect": ("path", "depth"),
    "mesh.feature_create": ("parent", "tag", "type_id", "properties", "selection"),
    "mesh.feature_update": ("path", "properties"),
    "mesh.feature_remove": ("path",),
    "mesh.build": ("path", "until_tag"),
    "mesh.clear": ("path",),
    "mesh.statistics": ("path",),
    "mesh.quality": ("path", "metric", "selection", "bins"),
    "mesh.validate": ("path", "criteria"),
    "study.list": ("scope",),
    "study.create": ("tag", "label"),
    "study.inspect": ("path",),
    "study.remove": ("path", "remove_solver"),
    "study.step_create": ("study", "tag", "type_id", "properties"),
    "study.step_update": ("path", "properties"),
    "study.step_remove": ("path",),
    "study.physics_activation": ("step", "activation"),
    "study.solver_generate": ("study", "replace_existing"),
    "study.run": ("study", "resources", "timeout_s"),
    "solver.list": ("filter",),
    "solver.inspect": ("path", "depth"),
    "solver.create": ("tag", "study"),
    "solver.feature_create": ("parent", "tag", "type_id", "properties"),
    "solver.feature_update": ("path", "properties"),
    "solver.feature_remove": ("path",),
    "solver.run": ("path", "range", "timeout_s"),
}

#: Operation id -> arguments that must be present for the call to be meaningful.
OPERATION_REQUIRED: dict[str, tuple[str, ...]] = {
    "mesh.list": (),
    "mesh.create": ("component", "tag", "geometry"),
    "mesh.inspect": ("path",),
    "mesh.feature_create": ("parent", "tag", "type_id", "properties"),
    "mesh.feature_update": ("path", "properties"),
    "mesh.feature_remove": ("path",),
    "mesh.build": ("path",),
    "mesh.clear": ("path",),
    "mesh.statistics": ("path",),
    "mesh.quality": ("path", "metric"),
    "mesh.validate": ("path", "criteria"),
    "study.list": (),
    "study.create": ("tag",),
    "study.inspect": ("path",),
    "study.remove": ("path",),
    "study.step_create": ("study", "tag", "type_id", "properties"),
    "study.step_update": ("path", "properties"),
    "study.step_remove": ("path",),
    "study.physics_activation": ("step", "activation"),
    "study.solver_generate": ("study", "replace_existing"),
    "study.run": ("study",),
    "solver.list": (),
    "solver.inspect": ("path",),
    "solver.create": ("tag", "study"),
    "solver.feature_create": ("parent", "tag", "type_id"),
    "solver.feature_update": ("path", "properties"),
    "solver.feature_remove": ("path",),
    "solver.run": ("path",),
}

__all__ = ["OPERATIONS", "OPERATION_ARGUMENTS", "OPERATION_REQUIRED"]
