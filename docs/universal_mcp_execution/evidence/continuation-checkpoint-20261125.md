# Continuation checkpoint: evidence recovery and scoped software checks

This checkpoint records local development evidence. It is not a release or native COMSOL acceptance.

- Six expected historical group summaries, including a supplied overlay, were found in the recovered source. Four expected group files were absent from both the checked original snapshot and the current public tree. Navigation-only indices were reconstructed for the missing groups; their linked evidence was not probed, and they do not restore the missing original group files.
- One supplied group summary differs from the older public snapshot. Its referenced detailed historical test report was not found at the expected path in the checked trees, so its test counts are not independently verified here.
- Four restored in-memory discovery tests passed against the current local development source: multiple custom-tag components, subtree scope, missing-tag reporting, and simulated label change with nested WorkPlane addressing. No real model or Worker was involved.
- For COMSOL 6.4 documentation, a read-only PropFeature search and retrieval matched the pinned source hash. Synthetic reviewed method metadata for ModelEntity and PropFeature carried matching reference-only documentation hashes. An earlier one-shot script failed on a synthetic receiver name and was preserved; the continuation did not repeat its indexing. This demonstrates manual hash association, not automatic bidirectional linking or a live receiver. The earlier ModelEntity search/get response was not preserved and was not counted; only its synthetic reference-hash association is reported.

Windows and macOS native execution, actual model binding, MCP wire behavior, and overall U01–U12 acceptance remain unverified. See the [earlier dual-version documentation index note](u03-dual-version-doc-index-20261125.md) for its separate scope.
