# Developer guide

For contributors and advanced users who want to change the tools, understand the code layout, or add documentation. If you only want to run studies, see [Quick start](quickstart.md).

## Repository layout

```text
.
├── crm/
│   ├── lunar_excavator_crm.py        rigid CRM production driver
│   ├── lunar_excavator_oneway.py     one-way CRM → FEA replay driver
│   ├── lunar_excavator_crm_fea.py    two-way coupled FEA (diagnostic)
│   ├── crm_soil.py                   soil parameter definitions
│   ├── run_status.py                 shared stage-labelled progress formatter
│   ├── run.sh / run_oneway_fea.sh / run_fea.sh
│   ├── README.md / FEA_COUPLING.md
│   └── chrono_runtime_env.sh        local Chrono runtime helper
├── environment/
│   ├── install assets (environment.yml, requirements.txt, patches, scripts)
│   └── INSTALL.md
├── docs/                              beginner and in-depth documentation (this directory)
├── geometry/                          user-supplied CAD/meshes (gitignored)
├── geometry_checks/mesh_preview/      GUI STEP preview output (gitignored)
├── step_to_chrono_fea.py              STEP → TetGen mesh + CRM surface
├── stl_to_obj.py                      STL → OBJ conversion
├── lunar_excavator_config.py          shared dimensions, paths, and pose helpers
├── detector_size_tool.py              interactive fill-detector sizer
├── study_wizard.py                    CLI wizard: prompts, config, commands, execution
├── study_gui.py                       Tkinter GUI (Imports wizard helpers directly)
├── study_gui_modern.py                optional themed GUI subclass (Pillow rounded ttk)
├── study_gui_defaults.json            saved GUI run defaults (gitignored)
├── study_gui_settings.json            saved GUI appearance settings (gitignored)
├── study_output_preview.py            CSV-only Tk preview widget
├── fea_summary.py                     compact FEA MPa/mm chart tables
├── export_study_vtk.py                study → ParaView VTK/PVD exporter
├── view_study_paraview.py             ParaView/PyVista viewer
├── view_results.py                    four-panel CRM/FEA summary plots
├── view_geometry.py                   STEP vs converted mesh comparison
├── full_model.py / run_full_model.sh  combined diagnostic runner
├── install.sh
└── tests/                             unittest regression suite
```

Generated directories (`chrono-src`, `chrono-build`, `chrono-install`, `chrono-env`, `.mamba`, `.tools`, `Output_*`, `geometry/`, `fea/`) are ignored by git. Do not commit geometry or build artefacts; several tests enforce the no-bundled-geometry policy.

## Entry points and shared code

`study_wizard.py` is the shared orchestration layer. Important functions:

- `build_parser()` — all CLI flags.
- `build_commands()` — builds the mesh, CRM, FEA, and export command list.
- `build_oneway_fea_only_commands()` — FEA replay for an existing CRM load file (used by the GUI button).
- `concurrent_groups()` / `run_concurrent_steps()` — group and launch the optional simultaneous CRM/FEA pair.
- `write_reproducibility_files()` — writes `study_config.json` and `commands.sh`.
- `main()` — prompts, resolves defaults, runs stages, and offers to view results.

`study_gui.py` imports the wizard module as `wiz` and calls the same helpers, so GUI and CLI studies remain equivalent. The GUI owns presentation, settings files, and interactive tools. Its `PARAM_INFO` and `SETTINGS_INFO` dictionaries are the single source of truth for the Info buttons and the generated Docs page.

For concurrent studies, `build_commands()` marks the rigid-CRM and one-way-FEA steps with a shared `concurrent_group`, adds the live-replay flags to the FEA command, and records a cleanup marker path on the CRM step. Both runners (`main()` and the GUI's `_run_thread()`) call `concurrent_groups()` so the pair starts together and later stages stay sequential. The marker path is `crm/crm_running.flag`; the runner writes a live PID before FEA starts and removes the file as soon as CRM is reaped. Skip and stop signal both members of the group. The generated `commands.sh` mirrors this with a CRM subshell that removes the marker on exit, so the script does not depend on the Python orchestrator.

The drivers under `crm/` are standalone argparse programs. They defer Chrono imports so `--help`, `--print-soil`, and geometry checks work without a GPU or a built environment. The CRM launchers set local `PYTHONPATH`/`LD_LIBRARY_PATH`, and `crm/run.sh` and `crm/run_oneway_fea.sh` source `crm/chrono_runtime_env.sh` to pin `LD_PRELOAD` to the locally installed Chrono libraries, including PardisoMKL. The one-way driver's opt-in 3D VTU export snapshots each frame into plain NumPy arrays and hands them to a `spawn`ed writer process through a bounded queue; the Chrono SWIG wrapper holds the GIL during solver steps, so a Python thread would not overlap file I/O.

`environment/build-chrono-source.sh` builds the supported Chrono 11 checkout with only FSI/FSI-SPH, FEA, Python, PardisoMKL, and a trimmed Chrono::Vehicle enabled. `environment/patches/chrono-python-minimal-vehicle.patch` reduces the Python vehicle wrapper to `veh.CRMTerrain` and `CHRONO_VEHICLE_CRM_ONLY=ON` drops unused vehicle sources; vehicle models, robot models, parsers, and the vehicle SCM GPU backend are off. The script uses GCC 13.4, oneMKL, `-O2`, an `nproc - 2` default build-job limit, and a raised compiler stack limit. The serial retry is a fallback for compiler front-end crashes, not a normal second build phase. Keep those settings synchronized with `environment/environment.yml`, `install.sh`, `environment/INSTALL.md`, and `tests/test_installer_dependencies.py`.

The CRM drivers detect a few renamed bindings so they still run against either Chrono 10 or 11: `crm/crm_soil.py` prefers `SoilProperties`/`SetCrmSPH` (Chrono 11) and falls back to `ElasticMaterialProperties`/`SetElasticSPH`; the terrain settle delay prefers `SetFreeFlowDuration` and falls back to `SetActiveDomainDelay`; and the flexible-BCE count prefers `GetNumFleaMeshMarkers` and falls back to `GetNumFlexBodyMarkers`. Chrono 11 also omits some methods rather than renaming them; the stress and shell-density guards are described in [Chrono 11 Python binding compatibility](#chrono-11-python-binding-compatibility). Keep these guards and their regression tests (`tests/test_chrono11_api_compat.py`) in sync if another binding changes.

Several CLI tools re-exec themselves under `chrono-env/bin/python` once (guarded by internal `PYCHRONO_*REEXEC`/`PYCHRONO_*ENV` variables) so system-Python invocations get the local packages. The GUI is the reverse case: when started from `chrono-env`, it re-execs into a system Python with working Tkinter for display quality and records the Chrono interpreter in `PYCHRONO_CHRONO_PYTHON` so simulations still use the project environment.

## Configuration schema

`study_config.json` is the contract between configuration and execution. Top-level groups:

| Group | Example keys |
| --- | --- |
| Identity | `study_name`, `output_dir`, `geometry`, `step_file`, `crm_obj`, `fea_node`, `fea_ele` |
| Geometry | `cad_axis`, `unit_scale`, `mesh_size`, `surface_faces`, `surface_fit_tolerance`, `part_aware_simplify`, `drum_radius`, `drum_half_length`, `inner_radius`, `hub_radius`, `detector_auto_size`, `detector_radius`, `detector_length` |
| Geometry defaults/corrections | `geometry_defaults`, `auto_geometry`, `geometry_corrections` |
| Time and cadence | `duration`, `fea_duration`, `spacing`, `crm_dt`, `fea_dt`, `crm_fps`, `fea_fps`, `loads_fps`, `particle_fps`, `particle_writers` |
| Domain/motion | `bed_height`, `bed_padding`, `active_box`, `settle_time`, `gravity`, `drum_gravity`, `descent_mode`, `descent_force_n`, `descent_force_ramp_s`, `omega`, `reverse_spin`, `spin_axis` |
| Materials | `soil` (density, Young's modulus, Poisson ratio, friction, cohesion, grain diameter), `pla` (modulus, Poisson ratio, density, Rayleigh beta) |
| FEA/export | `solver`, `num_threads`, `load_radius_fraction`, `raw_stress_every`, `save_particles`, `export_vtk`, `fea_export_vtk`, `paraview_*` |
| Execution | `commands` (list of `{name, command}`), `concurrent_crm_fea`, `dry_run`, `open_results`, `open_paraview` |

Keep key names in sync between the wizard's default dictionaries (`SOIL_DEFAULTS`, `PLA_DEFAULTS`, and the scalar defaults in `build_parser()`), the GUI's `shipped_defaults()`/`_v()` variable creation, and `study_gui_defaults.json`. Tests in `tests/test_study_gui_fields.py` cover many of these fields.

When adding a setting, update:

1. the wizard parser and default/validation logic;
2. the command builder that emits the corresponding flag;
3. the GUI variable and the relevant page row (or Defaults group);
4. `docs/cli-reference.md`, `docs/gui-guide.md`, and, if needed, `crm/README.md`;
5. a regression test.

## GUI architecture notes

- `StudyGUI._build_ui()` creates the top-level `ttk.Notebook`. The Output Preview tab is inserted before Docs by `_apply_preview_visibility()` when enabled in Settings.
- Wizard pages are `ScrollFrame` instances added by `_add_page()`; page 6 (Review & Run) is a plain frame with command/log panes. `ScrollFrame._scroll_units()` passes wheel targets straight to `yview_moveto()` instead of deriving a maximum from `winfo_height()`: Tk's canvas viewport is smaller than the reported widget height, so a hand-computed limit left the last rows reachable only with the scrollbar.
- The modern launcher's `ModernStudyGUI._update_step_chips()` highlights **Configure** for wizard pages 1-5, **Simulate** for page 6, and **Review** for the results tabs; `_goto_step()` jumps to page 1 or 6 for the first two chips.
- `_row()` builds a labelled entry/combobox/checkbox plus an **Info** button whose text comes from `PARAM_INFO`.
- Theme and fonts are configured in `_apply_ui_settings()` using named fonts (`StudyHeaderFont`, `StudyDocTitleFont`, `StudyDocHeadingFont`, `StudyDocSubheadingFont`, `StudyDocCodeFont`, `StudyDocBoldFont`, `StudyDocItalicFont`, `StudyDocTableHeaderFont`, `StudyDocTableBoldFont`). `text_widgets` is the list of `tk.Text` widgets recoloured when settings change.
- The Docs tab (`_build_docs_tab()`) is a left `ttk.Treeview` plus a read-only `tk.Text` pane. `_show_doc()` reads the selected Markdown file from disk, except for the generated GUI parameter reference, which is assembled from `PARAM_INFO`/`SETTINGS_INFO`. `_render_doc_markdown()` implements a small Markdown subset with semantic text tags: distinct heading levels, inline bold/italic/code, shaded fenced code boxes, quotes, nested list indentation, and rules. Pipe tables are embedded `ttk.Treeview` widgets (`_render_doc_table()`) with real columns, a styled header, a horizontal scrollbar, and clickable link spans, so alignment does not depend on a monospaced font. `_configure_doc_tags()` keeps colors in sync with light/dark settings. `_doc_follow_link()` switches to registered local pages, scrolls to heading anchors, or opens web links; `_find_doc_text()` highlights matches one at a time. There is no HTML renderer.
- The Output Preview widget lives in `study_output_preview.py` and is driven by CSV files only.

### Adding a documentation page

Documentation pages are plain Markdown files in the repository. To add one to the GUI Docs tab:

1. Create the file, preferably under `docs/`.
2. Add `(title, "docs/your_page.md")` to `DOC_ENTRIES` in `study_gui.py` (near the top, beside `GENERATED_DOC_ID`). Use `None` instead of a path for a page generated from GUI help tables.
3. Link it from `docs/README.md` so the documentation hub stays complete.
4. Restart the GUI so the new entry appears in the tree. **Refresh** then reloads the selected page after later file edits.

Use the supported Markdown subset: headings, fenced code, bullets, numbered lists, quotes, rules, and simple pipe tables that start with a leading pipe character and include a separator row. Tables become embedded `ttk.Treeview` widgets with real columns, a styled header, and a horizontal scrollbar; link labels in cells are clickable. Nested bullets are indented. Inline `code`, **bold**, *italic*, and links are styled. Local links must point to a page registered in `DOC_ENTRIES`; `#anchor` links resolve to headings rendered on that page. Write page names in the prose too so links are useful in plain Markdown viewers.

## Tests and checks

The suite uses the standard library `unittest` module (no pytest needed):

```bash
chrono-env/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Coverage includes GUI fields and settings, STEP/mesh geometry consistency, fill metrics, force descent, gravity controls, FEA summaries and export timing (including the one-way replay's CRM-frame duration limit), the background one-way VTU writer (frame ordering, flush, error propagation, and per-frame stress-array reuse), detector sizing, output preview, four-panel result plots and their blitted shared crosshair, ParaView script generation (including first-frame camera priming, the non-parallel view-up guard, and default display opacity/visibility), installer dependency text, and the installer build-thread cap. `tests/test_fill_metrics.py` covers the driver-side metric logic.

All regression files live under `tests/`; `tests/__init__.py` keeps the repository root and the test directory importable. Run a single file as `chrono-env/bin/python tests/test_name.py` or `chrono-env/bin/python -m unittest tests.test_name`.

GUI tests create a `tk.Tk()` root, withdraw it, and instantiate `StudyGUI` directly. They locate widgets through app attributes (`app.pages[2]`, `app.doc_text`, `app.settings_vars[...]`) or by walking `winfo_children()` and checking label text. Set `PYCHRONO_GUI_NO_REEXEC=1` in tests to avoid the GUI's self-relaunch.

## Running tools during development

Useful quick checks that do not need a GPU:

```bash
./crm/run.sh --help
./crm/run.sh --print-soil
./run_study.sh --help
./run_study.sh --dry-run --yes --geometry step --step-file geometry/MyDrum.step
```

`--dry-run` writes `study_config.json` and `commands.sh`, which is the fastest way to verify that a parameter change produces the command line you expect.

Keep run outputs under `Output_*` (gitignored) and prefer project-local paths over `/tmp` for large files. When you change a default, update its documentation and the corresponding regression test in the same commit.

## Chrono 11 Python binding compatibility

Chrono 11's SWIG wrappers omit a few C++ methods that the drivers previously called directly:

- `ChStressTensorD.GetEquivalentVonMises()` is not wrapped. `environment/patches/chrono-python-von-mises.patch` exposes the native C++ formula as `VonMises()`, and `install.sh` applies that patch alongside the SPH patch. `crm/lunar_excavator_crm_fea.py::von_mises_pa()` calls the patched method (or the original name if a future binding provides it). Principal-stress reconstruction is intentionally not used: `ComputePrincipalStresses()` returns NaNs for hydrostatic/zero tensors and would corrupt exported stress arrays. Both the scalar FEA summary and the per-element VTU stress arrays use this helper, so the one-way production metric is unchanged.
- `ChMaterialShellKirchhoff.SetDensity()` is not wrapped. The diagnostic-only two-way driver guards that call and relies on the explicit contact-skin node masses assigned in `create_contact_skin()`. Production one-way replay does not create a shell material.

`tests/test_chrono11_api_compat.py` covers these shims. When adding a Chrono call, prefer a runtime capability check (`hasattr`) over assuming a method exists in every supported wrapper.

## Documentation conventions

- Beginner docs live in `docs/` and use short sections, commands you can copy, and links to deeper material.
- In-depth docs explain *why* as well as *what* and explicitly state validation status.
- All docs are readable from the GUI Docs tab; keep the `DOC_ENTRIES` list and `docs/README.md` index in sync.
- Physics/metric caveats must stay consistent across `README.md`, `crm/README.md`, `crm/FEA_COUPLING.md`, and `docs/`: SPH markers only, instantaneous eroded-cylinder fill classification, and diagnostic-only two-way FEA.
