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
├── study_gui_defaults.json            saved GUI run defaults (gitignored)
├── study_gui_settings.json            saved GUI appearance settings (gitignored)
├── study_output_preview.py            CSV-only Tk preview widget
├── fea_summary.py                     compact FEA MPa/mm chart tables
├── export_study_vtk.py                study → ParaView VTK/PVD exporter
├── view_study_paraview.py             ParaView/PyVista viewer
├── view_results.py                    CRM/FEA summary plots
├── view_geometry.py                   STEP vs converted mesh comparison
├── full_model.py / run_full_model.sh  combined diagnostic runner
├── install.sh
└── test_*.py, crm/test_fill_metrics.py  regression suites
```

Generated directories (`chrono-src`, `chrono-build`, `chrono-install`, `chrono-env`, `.mamba`, `.tools`, `Output_*`, `geometry/`, `fea/`) are ignored by git. Do not commit geometry or build artefacts; several tests enforce the no-bundled-geometry policy.

## Entry points and shared code

`study_wizard.py` is the shared orchestration layer. Important functions:

- `build_parser()` — all CLI flags.
- `build_commands()` — builds the mesh, CRM, FEA, and export command list.
- `build_oneway_fea_only_commands()` — FEA replay for an existing CRM load file (used by the GUI button).
- `write_reproducibility_files()` — writes `study_config.json` and `commands.sh`.
- `main()` — prompts, resolves defaults, runs stages, and offers to view results.

`study_gui.py` imports the wizard module as `wiz` and calls the same helpers, so GUI and CLI studies remain equivalent. The GUI owns presentation, settings files, and interactive tools. Its `PARAM_INFO` and `SETTINGS_INFO` dictionaries are the single source of truth for the Info buttons and the generated Docs page.

The drivers under `crm/` are standalone argparse programs. They defer Chrono imports so `--help`, `--print-soil`, and geometry checks work without a GPU or a built environment. The CRM launchers set `PYTHONPATH`/`LD_LIBRARY_PATH` as needed; `crm/run.sh` also sources `crm/chrono_runtime_env.sh` to pin `LD_PRELOAD` to the locally installed Chrono libraries. The one-way launcher sets those paths only for solvers that need them.

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
| Execution | `commands` (list of `{name, command}`), `dry_run`, `open_results`, `open_paraview` |

Keep key names in sync between the wizard's default dictionaries (`SOIL_DEFAULTS`, `PLA_DEFAULTS`, and the scalar defaults in `build_parser()`), the GUI's `shipped_defaults()`/`_v()` variable creation, and `study_gui_defaults.json`. Tests in `test_study_gui_fields.py` cover many of these fields.

When adding a setting, update:

1. the wizard parser and default/validation logic;
2. the command builder that emits the corresponding flag;
3. the GUI variable and the relevant page row (or Defaults group);
4. `docs/cli-reference.md`, `docs/gui-guide.md`, and, if needed, `crm/README.md`;
5. a regression test.

## GUI architecture notes

- `StudyGUI._build_ui()` creates the top-level `ttk.Notebook`. The Output Preview tab is inserted before Docs by `_apply_preview_visibility()` when enabled in Settings.
- Wizard pages are `ScrollFrame` instances added by `_add_page()`; page 6 (Review & Run) is a plain frame with command/log panes.
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
chrono-env/bin/python -m unittest discover -s . -p 'test_*.py'
```

Coverage includes GUI fields and settings, STEP/mesh geometry consistency, fill metrics, force descent, gravity controls, FEA summaries and export timing (including the one-way replay's CRM-frame duration limit), detector sizing, output preview, ParaView script generation (including first-frame camera priming and the non-parallel view-up guard), and installer dependency text. `crm/test_fill_metrics.py` covers the driver-side metric logic.

GUI tests create a `tk.Tk()` root, withdraw it, and instantiate `StudyGUI` directly. They locate widgets through app attributes (`app.pages[2]`, `app.doc_text`, `app.settings_vars[...]`) or by walking `winfo_children()` and checking label text. Set `PYCHRONO_GUI_NO_REEXEC=1` in tests to avoid the GUI's self-relaunch.

> A font-size assertion in `test_study_gui_fields.py` can fail on hosts whose font configuration reports a different actual point size; this is an environment sensitivity, not a simulation regression.

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

## Documentation conventions

- Beginner docs live in `docs/` and use short sections, commands you can copy, and links to deeper material.
- In-depth docs explain *why* as well as *what* and explicitly state validation status.
- All docs are readable from the GUI Docs tab; keep the `DOC_ENTRIES` list and `docs/README.md` index in sync.
- Physics/metric caveats must stay consistent across `README.md`, `crm/README.md`, `crm/FEA_COUPLING.md`, and `docs/`: SPH markers only, instantaneous eroded-cylinder fill classification, and diagnostic-only two-way FEA.
