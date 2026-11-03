# ArcGIS Pro 补充耕地验收插件 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver a Pro 3.1.5-compatible `.esriAddInX` ribbon add-in backed by a Python toolbox that guides project setup, data inspection, sampling design, QA, and delivery preparation for supplementary cultivated-land acceptance.

**Architecture:** A C# ArcGIS Pro SDK add-in provides the ribbon and opens native geoprocessing dialogs for a bundled Python toolbox. The Python toolbox owns business rules and calls existing utilities only where their data contract matches; it reads established configuration and the acceptance guide instead of duplicating thresholds in UI code. The first package creates traceable, non-overwriting, reviewable outputs and labels unverified or manual-review items honestly.

**Tech Stack:** ArcGIS Pro SDK for .NET 3.1, C#/.NET 6, ArcPy in ArcGIS Pro 3.1.5 Python, Python toolbox (`.pyt`), PowerShell packaging and installation helper.

---

### Task 1: Verify authoritative acceptance rules and map existing code

**Files:**
- Read: `config/standard_profile.json`
- Read: `scripts/bootstrap_project.py`
- Read: `scripts/setup_quality_gdb.py`
- Read: `scripts/ingest_cad_rtk.py`
- Read: `scripts/qa_delivery.py`
- Read: `scripts/delivery_package.py`
- Read: approved source guide and local supplementary cultivated-land acceptance materials, where reachable

**Step 1: Inspect input authority and script contracts**

Read the guide's relevant deliverable and sampling sections, config provenance, and each script's CLI/outputs. Treat `setup_quality_gdb.py` and `delivery_package.py` as partial acceptance-specific reusable code; `bootstrap_project.py`, `ingest_cad_rtk.py`, and `qa_delivery.py` serve a different land-survey cartography profile and must not control acceptance QA. Classify each rule as source-backed, project default, or unresolved. Do not import cadastral land-change thresholds as acceptance rules.

**Step 2: Record integration boundary**

Create an implementation mapping in plugin docs: reusable scripts and exact compatible operations; new acceptance-specific operations; unresolved guide rules that must remain manual review.

**Acceptance:** Sampling requirements cite guide 6.1.1–6.1.4 and 6.2.4.2–6.2.4.3; delivery cites 10.1–10.4 and Appendices C/G/I. Point rules include per-unit 3 minimum, per-parcel 1 minimum, general 15 m spacing with documented small-parcel exception; bulk density is 3 parallel cores within 2 m of texture-profile survey point and 2 m between parallel points. Every automated rule cites version/page or remains UNVERIFIED. No unrelated standard is treated as authoritative.

### Task 2: Configure the Pro 3.1 build toolchain

**Files:**
- Create: `addin/README-build.md`
- Create: `addin/AcceptanceAddin.sln`
- Create: `addin/AcceptanceAddin/AcceptanceAddin.csproj`

**Step 1: Confirm current host and SDK source**

Confirm Pro 3.1.5, .NET 6 target, Visual Studio 2022, Pro-installed `Esri.ProApp.SDK.Desktop.targets`, and official Esri Pro SDK 3.1 source. Preserve existing Pro and Python installations.

**Step 2: Install the matching SDK template/tooling**

The ArcGIS Pro SDK 3.1 VSIX requires a full Visual Studio SKU; this host has Build Tools only, which rejects the VSIX. Pro 3.1 SDK assemblies and a private .NET 6 SDK are available. The installed MSBuild lacks the .NET SDK resolver and Esri targets' `CodeTaskFactory` fails under .NET MSBuild, so compile with `dotnet build` against Pro assemblies and package the standard Add-In ZIP layout with PowerShell. Record exact versions and limitation; do not alter VS/Pro installation.

**Step 3: Create the minimal SDK add-in project**

Create a Pro SDK 3.1 project using references relative to the detected Pro installation. Keep user machine paths out of source; allow a build property/environment variable to override the installation location. Keep a conditional import for Esri targets when the host supports them; the current package script disables that import and verifies the package contents directly.

**Acceptance:** The build resolves all ArcGIS references using Pro 3.1 assemblies and targets `net6.0-windows`/x64. Host-specific packaging deviation and independent registration evidence are recorded. Do not upgrade Pro or silently switch versions.

### Task 3: Build the acceptance Python core and toolbox contract

**Files:**
- Create: `acceptance/__init__.py`
- Create: `acceptance/rules.py`
- Create: `acceptance/project.py`
- Create: `acceptance/sampling.py`
- Create: `acceptance/qa.py`
- Create: `acceptance/delivery.py`
- Create: `toolboxes/SupplementaryCultivatedLandAcceptance.pyt`
- Create: `toolboxes/acceptance_support/`

**Step 1: Define source-backed rule configuration**

Implement configuration loading and provenance fields. Missing or contradictory rules return `UNVERIFIED` or `NEEDS_REVIEW` rather than assumed pass.

**Step 2: Implement project workspace initialization**

Create expected GDB/folder outputs in a new destination, refuse collisions, preserve supplied source data, and write a run manifest with input paths, parameters, timestamp, plugin/core version, and messages.

**Step 3: Implement sampling design as candidate geometry**

Create separate editable design-point feature classes for composite-sample subsites and bulk-density parallel cores. Composite subsites follow per-unit ≥3, every parcel ≥1, grid-uniform distribution, general ≥15 m spacing; small parcels remain a documented human exception and must still be covered with points away from field edges. Bulk-density cores are 3 points per evaluation unit within 2 m of the texture-profile survey location and 2 m apart, using the 100 cm³ corer. Validate geometry and CRS only against project-confirmed inputs. Pollution-risk sampling is left configurable/manual because the guide requires risk-specific expert judgment. Store source clause, design status, exception reason, and stable IDs. Never assert designed points were sampled.

**Step 4: Implement QA and delivery readiness tools**

Produce a machine-readable structural QA report and delivery directory checklist with `PASS`/`FAIL`/`UNVERIFIED`/`NEEDS_REVIEW`. First release checks Appendix G layer presence/CRS and Appendix I folders/files; Appendix C fields/topology, 50-mu area and sampling spatial QA remain manual. Preserve supplied data unchanged. Treat CMA certificates, sample handover records, lab originals, signatures, photos and report contents as manual evidence; never mark these complete without review.

**Step 5: Expose focused Python toolbox tools**

Publish tools for project setup, vector/CAD/RTK intake, sampling-design generation, structural QA and delivery preparation. Field-record import/association remains out of first release because no approved schema was supplied. Use explicit parameters, Chinese labels/help, and friendly geoprocessing errors.

**Acceptance:** Tools are discoverable by ArcGIS Pro 3.1.5 Python and emit traceable outputs; source data remains untouched; unresolved thresholds cannot produce `PASS`.

### Task 4: Build the functional ribbon interface

**Files:**
- Create: `addin/AcceptanceAddin/Config.daml`
- Create: `addin/AcceptanceAddin/Module1.cs`
- Create: `addin/AcceptanceAddin/Commands/ProjectSetupButton.cs`
- Create: `addin/AcceptanceAddin/Commands/SamplingDesignButton.cs`
- Create: `addin/AcceptanceAddin/Commands/QualityCheckButton.cs`
- Create: `addin/AcceptanceAddin/Commands/DeliveryButton.cs`
- Create: `addin/AcceptanceAddin/ToolboxLocator.cs`

**Step 1: Define Chinese ribbon tab and workflow groups**

Create tab “补充耕地验收” with groups “项目”, “采样”, “质检与汇交” and named buttons for each approved workflow.

**Step 2: Resolve bundled toolbox paths safely**

Locate add-in resources from the deployed package, validate existence/version, and display an actionable error if missing. Do not use developer-specific absolute paths.

**Step 3: Open native Pro geoprocessing tools**

Use Pro SDK Geoprocessing API to open matching custom toolbox tools, retaining native messages, progress, history, and cancellation behavior.

**Step 4: Handle missing project and unsupported state**

Disable or explain actions when there is no project/toolbox or an incompatible Pro version; never create a side database silently.

**Acceptance:** Each ribbon command opens its intended toolbox tool in Pro 3.1.5. C# contains no acceptance thresholds or geospatial business calculations.

### Task 5: Package, install, document, and deliver

**Files:**
- Create: `addin/package.ps1`
- Create: `addin/install_addin.ps1`
- Create: `addin/README.md`
- Create: `addin/dist/AcceptanceAddin.esriAddInX`
- Modify: `README.md`

**Step 1: Bundle Python toolbox and resources**

Package toolbox, acceptance core, and source-backed configuration with the Add-In using a stable relative deployment layout supported by Pro 3.1.5. Do not bundle or modify ArcGIS Pro Python.

**Step 2: Build the add-in package**

Build an `.esriAddInX` with the Pro SDK packaging target and verify the package contains the expected DAML, DLL, toolbox, config, and resources.

**Step 3: Add a reversible per-user installation path**

Provide install/update instructions for the current user's ArcGIS Pro add-ins location; preserve any existing installed version and explain restart/enable steps. No machine-wide registry/config edits.

**Step 4: Document tool workflows and result semantics**

Write Chinese setup, usage, rule-source, output-schema, troubleshooting, and manual-review notes. Explain designed points vs actual field samples and limitations of synthetic data.

**Acceptance:** Package exists under `addin/dist`, its contents are read back, installer registers only the per-user Add-In, and README instructions match the actual package.

### Task 6: Verify against Pro 3.1.5 and a disposable synthetic project

**Files:**
- Create: `addin/verification/verification-record.md`
- Create: disposable data only under a new unique temp/sample output directory

**Step 1: Validate toolbox in Pro Python**

Use installed ArcGIS Pro Python to load toolbox metadata and execute non-destructive inspection/project initialization on a disposable synthetic output path.

**Step 2: Build and inspect package**

Build with MSBuild; inspect package contents and metadata; verify there is no absolute developer path in packaged configuration.

**Step 3: Load in ArcGIS Pro 3.1.5**

Install per user, open Pro, verify tab/groups appear, and open project setup/QA tools. If interactive Pro verification is unavailable, state that and keep build/package evidence.

**Step 4: Record observed results and limitations**

Record exact Pro, SDK, Python, build output, toolbox discovery, UI load, generated outputs, QA state counts, and unresolved-rule/manual-review caveats.

**Acceptance:** Python toolbox tools load in Pro Python 3.1.5, disposable project setup/sampling/QA/delivery operations produce readable outputs, and the package is registered to the current user's Add-Ins folder with matching SHA-256. The desktop UI screenshot channel timed out twice, so ribbon visibility remains UNVERIFIED and requires a Pro restart/visual check. Do not run against real source data without a copy. Do not claim unverified rules or UI states pass.

---
