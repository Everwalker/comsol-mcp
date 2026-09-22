# ADR-W18: Graphics Dependency and API/Headless Decoupling

## Status
Accepted

## Context
In early phases of COMSOL integration (notably W03 Desktop automation), visualization and screenshot capabilities relied heavily on desktop graphical user interface (GUI) automation, active display servers (macOS Quartz/Aqua, Linux X11/Wayland, Windows Desktop Manager), and interactive window handles.

This dependency presented significant operational challenges:
1. **Server and Headless Environments**: Automated execution on headless CI workers, cloud compute nodes, and headless daemons (`mphserver`) lacks an interactive display session.
2. **Fragility of Desktop Scraping**: Window capture and OS-level screenshotting are sensitive to screen resolution, window overlap, focus stealing, and display scaling.
3. **Payload Bloat**: Transporting uncompressed or redundantly mirrored base64 image strings within text JSON envelopes causes severe memory bloat and violates transport budgets.

## Decision
For workstream W18 (Real Plotting and Graphics Return), we decouple graphics generation and rendering entirely from desktop GUI automation:

1. **Native Model API Rendering**:
   - Plot groups (`PlotGroup1D`, `PlotGroup2D`, `PlotGroup3D`, `PlotGroupPolar`) and child plot features (`Surface`, `Contour`, `Slice`, `Line`, `Arrow`, etc.) are configured programmatically through the COMSOL Model API (`model.result()`).
   - Image rendering is executed via COMSOL Model export features (`model.result().export().create(<tag>, "Image")`), leveraging COMSOL's built-in offscreen graphics pipeline (software OpenGL / Mesa / JOGL offscreen rasterization) supported in server mode (`mphserver`).
   - Neither interactive windowing nor GUI display servers are required.

2. **Project-Scoped Artifact Management**:
   - Rendered images (PNG/JPEG) are atomically written to the authorized project root under `g2_artifacts/plots/` or configured export paths through `ArtifactStore`.
   - Every exported image artifact is registered in `ArtifactStore`, with pinned SHA-256 digest, byte size, and format identity verified before publication.

3. **Dual MCP Content Delivery**:
   - The MCP transport layer (`comsol_mcp._mcp_gateway`) detects image outputs from `plot.render`, `plot.geometry_render`, and `export.run`.
   - Output is delivered using standard MCP typed `ImageContent(type="image", data=b64, mimeType="image/png")` alongside a compact `TextContent` JSON envelope containing structured metadata.
   - The text payload replaces massive base64 strings with a concise placeholder, eliminating redundant payload duplication while maintaining full client compatibility.

4. **Camera and View Control**:
   - View configurations, projection axes, and camera settings are managed via `plot.view_manage` through `model.view()`, enabling deterministic viewpoint positioning without mouse or keyboard simulation.

## Consequences
- **Positive**:
  - Full headless compatibility: tests and simulations execute consistently on local servers, cloud VMs, and CI runners without Xvfb or virtual framebuffers.
  - Strict determinism: plots are generated directly from solution datasets and numerical definitions rather than screen pixels.
  - Safe, bounded memory: image bytes travel in typed content blocks without ballooning JSON wire sizes.
- **Negative / Trade-offs**:
  - Offscreen rendering requires valid geometry and mesh data loaded in memory.
  - In environments without hardware GPU acceleration, complex 3D rasterization falls back to software rendering, requiring appropriate timeouts on large models.
