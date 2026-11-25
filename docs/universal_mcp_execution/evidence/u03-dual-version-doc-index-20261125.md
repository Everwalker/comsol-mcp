# Scoped COMSOL 6.3 and 6.4 documentation index check

A local, unreleased development wheel was used to index two official help pages in a fresh documentation-only database: the public COMSOL 6.3 ModelUtil API page and a restored COMSOL 6.4 geometry Adjacency help page. This was a software check on macOS ARM, not a release qualification.

- The 6.3 page produced five indexed chunks; a version-filtered search for `setServerBusyHandler` and retrieval of a matching chunk succeeded.
- The 6.4 page produced one indexed chunk; a version-filtered search for `getAdj` and retrieval of that chunk succeeded.
- Each search returned no match when restricted to the other indexed version. A query for unindexed version 6.2 returned `UNAVAILABLE`; this says only that the version was absent from this database. SQLite integrity checking returned `ok`.

The returned pages document the handler argument of `setServerBusyHandler` and the source dimension, target dimension, and optional source entity index for `getAdj`. Actual receiver binding, entity indices, and behavior must still be checked against a target model and runtime.

This check covers two documents only. It does not establish the identity of Windows-installed documentation, an MCP wire interaction, COMSOL/Worker execution, a license, or native acceptance. The [COMSOL 6.3 ModelUtil API page](https://doc.comsol.com/6.3/doc/com.comsol.help.comsol/api/com/comsol/model/util/ModelUtil.html) is publicly available.
