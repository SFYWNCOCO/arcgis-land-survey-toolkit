# AI component consolidation implementation plan

**Goal:** Own AI/MCP source, clients and tests inside `land_survey_toolkit/ai/`, and repair the eight confirmed review findings.

**Architecture:** Keep one native DLL, workbench and installer. Move the existing component without copying its implementation; retain its internal namespace and wire protocol. Historical receipts and generated artifacts remain historical evidence.

**Tech stack:** Python 3.12 / Pro Python 3.9, ArcPy 3.1, C# / .NET 6, stdio MCP.

1. Capture the shared-workspace baseline and migration file hashes. Add regression cases and confirm failures before implementation.
2. Move `ARCGIS/pro_ai_bridge/` to `ai/` with target-absence and workspace-containment checks. Compare every moved file byte-for-byte before editing.
3. Reuse the component build script as toolkit-root `build.py`; update native project/Roslyn/package paths, installer/test paths, toolkit tests, current documentation and the desktop MCP connection. Do not add historical-path fallback branches.
4. Repair sampling completeness/CRS verification, annotation selection, search step validation, incomplete tile handling, demo CRS/API use and export error propagation in their existing files.
5. Run regression cases, both Python test neighborhoods, C# core harness, native compilation and package readback. Exercise demo ArcPy using new synthetic output directories; no business output is overwritten.
6. Have an independent reviewer inspect the final task delta, call sites and validation evidence. Resolve findings and bind approval to final content hashes.

Recovery: use the pre-edit content hashes and migration map under workspace `temp/arcgis-consolidation-20261101/` to check the affected files, then reverse only this task's mapping/patches if necessary. The hash baseline is not a content backup. Check for concurrent changes before recovery; do not reset the shared tree or its index.

No Git commit, installation, service restart or publication is included. Existing live processes are not stopped. A changed MCP launch path takes effect when its client next reconnects/restarts.
