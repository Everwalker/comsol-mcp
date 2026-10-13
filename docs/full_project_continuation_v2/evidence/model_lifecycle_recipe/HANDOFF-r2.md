# Managed model lifecycle and recipe reuse r2 handoff

R2 repairs the three accepted gaps from r1: historical recipe smoke evidence survives closing its source model and changing the live Worker endpoint/epoch; `model.load` accepts project-registered pinned MPH files including restored files without legacy type metadata after safe archive validation; and `model.save(overwrite=true)` atomically replaces only an exact owned registered target, retaining the previous bytes until registration succeeds and restoring them on registration failure.

The exact full source snapshot is [SOURCE_FREEZE_r2.json](SOURCE_FREEZE_r2.json). The r1 base patch remains [MODEL_LIFECYCLE_RECIPE_SOURCE.patch](MODEL_LIFECYCLE_RECIPE_SOURCE.patch), and the exact changes from r1 are [MODEL_LIFECYCLE_RECIPE_R2_DELTA.patch](MODEL_LIFECYCLE_RECIPE_R2_DELTA.patch). The affected 11-module suite is [junit-r2-managed-delivery.xml](junit-r2-managed-delivery.xml): **165 passed, 0 skipped, 0 failures**. The focused module suite passed **20/20**.

The tests exercise the actual ControlDaemon and OperationStore route with a synthetic Worker. That establishes software behavior only. COMSOL native execution, numerical and physical acceptance, and portability are **NOT_RUN/UNVERIFIED**. The production dirty-close context still lacks a real scoped authorization verifier; a nonempty caller reference is not treated as authorization. Registered `model_package` extraction is also not yet a `model.load` capability.

No shared route, backend, registry, execution-service, or Worker file changed in r2; the existing ownership handoff for those files remains with Windows. Next: parent review of this frozen r2 scope, then continue the independently owned report/evidence-bundle/publish and full-data backup/restore units.
