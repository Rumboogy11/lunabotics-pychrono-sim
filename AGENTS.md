# AGENTS.md — read before working in this repository

This is agent-facing guidance. It is **not** user documentation and is not shown in the GUI Docs tab. For user-facing material, start with `docs/README.md`.

## 1. Read these before changing code or docs

In this order, as needed:

1. `README.md` — supported workflow, installation summary, and top-level caveats.
2. `docs/README.md` — the documentation hub and reading paths.
3. `docs/quickstart.md`, `docs/glossary.md`, `docs/gui-guide.md`, `docs/wizard-guide.md`, `docs/cli-reference.md`, `docs/outputs-and-visualization.md`, `docs/troubleshooting.md`, `docs/how-it-works.md`, `docs/developer-guide.md`.
4. `crm/README.md`, `crm/FEA_COUPLING.md`, `FULL_MODEL.md`, `environment/INSTALL.md` — detailed references that still matter.
5. `tests/test_docs_integration.py` — the contract for docs registration and Docs-tab rendering.

Prefer answering questions from these files and from `--help` / `--dry-run` output instead of guessing or asking the user to choose between implementation details.

## 2. Project invariants — do not contradict these

- There are three front ends for the same pipeline: `study_gui.py`, `study_wizard.py` / `run_study.sh`, and direct drivers under `crm/`.
- The **production design model is rigid Chrono::CRM**. It is used for fill, torque, work, and specific energy.
- **One-way CRM → FEA** is a structural *screening* replay of recorded soil loads on a solid mesh. It has no SPH contact.
- Two-way FEA (`crm/lunar_excavator_crm_fea.py`) and `full_model.py` are **diagnostic only and not engineering-validated**.
- Soil parameters are best estimates, **not calibrated measurements**. Keep them frozen when comparing designs.
- **No CAD or mesh geometry is bundled.** `geometry/` is gitignored; users supply their own STEP or mesh files.
- Excavation metrics count **SPH fluid markers only**. BCE markers are excluded. Older comparisons that included BCE markers are invalid.
- Fill uses the instantaneous `excavated` set: SPH particles inside the detector cylinder eroded by one particle radius in the drum's live pose. A particle loses the flag when it leaves the cylinder. `lifted` is reported separately as a diagnostic (above the initial bed by one SPH radius) but does not gate fill.
- `crm_animation.pvd` and `combined.pvd` contain CRM particles and rigid poses only — **never FEA**.
- FEA scalar summaries are the fast default. A full 3D FEA stress animation is opt-in.
- The viewer **rechecks the actual PVD and frame files**. Do not say it trusts a "validated full PVD". A lone `t=0` frame is ignored; a structurally usable partial multi-frame series can display even if the manifest marks it partial.
- FEA VTU frames live at `fea/vtk/frames/frame_*.vtu` with `fea/vtk/fea.pvd`. CRM particle and excavator frames are regenerated on every export; FEA VTU frames are produced by the FEA driver.
- The GUI defaults ParaView to `QT_QPA_PLATFORM=xcb`; `PYCHRONO_PARAVIEW_QT_PLATFORM` overrides it.
- The GUI prefers a system Python with working Tkinter and records the Chrono-capable interpreter in `PYCHRONO_CHRONO_PYTHON`. Most other launchers prefer `chrono-env/bin/python`.
- Use current terminology: **CRM = Continuous Representation Model**; **BCE = Boundary Condition Enforcing markers**. Avoid stale phrases such as "Continuum Representation of Material" or "Boundary Condition Estimator".

## 3. Architecture — where changes belong

- `study_wizard.py` is the shared orchestration layer. Key functions include `build_parser()`, `build_commands()`, `build_oneway_fea_only_commands()`, `write_reproducibility_files()`, and `main()`.
- `study_gui.py` imports the wizard as `wiz`, so GUI and CLI studies stay equivalent. `PARAM_INFO` and `SETTINGS_INFO` are the source of truth for Info buttons and the generated GUI parameter reference.
- The `crm/` drivers are standalone argparse programs that defer Chrono imports so `--help`, `--print-soil`, and geometry checks work without a built GPU environment.
- `crm/run.sh` pins local Chrono libraries with `LD_PRELOAD` via `crm/chrono_runtime_env.sh`. `crm/run_oneway_fea.sh` adds local `PYTHONPATH` / `LD_LIBRARY_PATH` only when `--solver gmres`, `sparse-lu`, or `sparse-qr` is explicitly requested.
- `study_config.json` is the contract between configuration and execution. Its groups cover identity, geometry, cadence, domain/motion, materials, FEA/export, and execution commands.
- Keep setting names synchronized across:
  - wizard defaults (`SOIL_DEFAULTS`, `PLA_DEFAULTS`, scalar defaults in `build_parser()`);
  - GUI variables (`shipped_defaults()`, `_v()`);
  - `study_gui_defaults.json`;
  - docs and regression tests.
- Workspace-local `study_gui_defaults.json` and `study_gui_settings.json` are gitignored. Do not commit them.
- Generated directories and outputs (`chrono-src`, `chrono-build`, `chrono-install`, `chrono-env`, `.mamba`, `.tools`, `Output_*`, `geometry/`, `fea/`) are gitignored. Do not commit geometry or build artefacts.

### Docs-tab specifics

- Pages are registered in `DOC_ENTRIES` near `GENERATED_DOC_ID` in `study_gui.py`. A `None` path means the page is generated from GUI help tables.
- `_render_doc_markdown()` supports headings, fenced code, bullets, numbered lists, quotes, rules, and simple pipe tables. It styles title/heading/subheading levels differently, renders inline bold/italic/code, puts fenced code in shaded bordered boxes, and indents nested lists and quotes.
- Pipe tables are embedded `ttk.Treeview` widgets with real columns, a styled header, and a horizontal scrollbar for wide tables. They do **not** rely on monospaced padding. Link labels inside table cells are clickable.
- Local Markdown links switch to registered Docs pages and `#anchor` links scroll to headings; web links open in the browser. Write page names in prose too so links are useful in plain Markdown viewers.
- Tables must start with a leading pipe character and include a separator row.
- **Refresh reloads the selected page; it does not add a newly registered tree entry.** Restart the GUI for a new entry to appear.
- Docs **Find** searches only the current page, highlights one match at a time, and wraps at the end.

## 4. Always update documentation with behavior changes

Treat docs as part of the change, not an afterthought. If a change affects what users see, run, configure, or interpret, update the relevant files **in the same work**.

| Change | Update these |
| --- | --- |
| CLI flag, default, environment variable, launcher behavior | `docs/cli-reference.md`; `docs/wizard-guide.md` if exposed by the wizard; `README.md` examples if user-facing; detailed reference (`crm/README.md`, `FULL_MODEL.md`, `environment/INSTALL.md`) where applicable; the tool's `--help` / docstring text. |
| GUI field, button, tab, page, setting, or Info text | `study_gui.py` (`PARAM_INFO` / `SETTINGS_INFO`), `docs/gui-guide.md`, `docs/quickstart.md` for first-run steps, and the GUI summary in `README.md`. |
| Output file, directory, path, exporter, or viewer behavior | `docs/outputs-and-visualization.md`, `docs/troubleshooting.md`, `docs/how-it-works.md` where the pipeline is described, the output/visualization section of `README.md`, and `crm/FEA_COUPLING.md` / exporter / viewer docstrings when FEA is involved. |
| Physics, metric, fill classification, or validation-status change | `docs/how-it-works.md`, `docs/glossary.md`, status notes in `docs/README.md`, top caveats in `README.md`, `crm/README.md`, `crm/FEA_COUPLING.md`, and `FULL_MODEL.md` as applicable. |
| Config schema or default change | `docs/developer-guide.md`, `docs/wizard-guide.md`, `docs/gui-guide.md`, and a regression test. |
| Bug fix or user workaround | `docs/troubleshooting.md` if users may encounter it; `docs/developer-guide.md` if it is contributor-facing. |
| Install/build/tooling change | `environment/INSTALL.md`, `docs/cli-reference.md`, and `docs/developer-guide.md`. |
| Internal re-exec guard or helper variable | `docs/cli-reference.md` if user-visible; otherwise explain it in `docs/developer-guide.md`. |
| Test or repository-layout change | `docs/developer-guide.md`. |
| New documentation page | Create under `docs/`; add to `DOC_ENTRIES` in `study_gui.py`; link from `docs/README.md`; add to `EXPECTED_DOCS` in `tests/test_docs_integration.py`; add to the table in `README.md`; restart the GUI to see the tree entry. |
| Change to these working rules | Edit this `AGENTS.md`. |

After any conceptual change, search for stale wording rather than fixing only the file you had open. Useful searches include:

```bash
grep -RniE 'validated.*PVD|complete PVD|fea/vtk/frame_|messenger|Continuum Representation|Boundary Condition Estimator|every prompt|accept `y`/`n`' --include='*.md' --include='*.py' .
```

Also check old defaults and paths when changing them, for example `0.8` versus `0.95` for load radius fraction and `1` versus `5` for raw stress interval.

## 5. Verification checklist

Run quick checks before declaring work done. Use the project interpreter.

```bash
./run_study.sh --help
./crm/run.sh --help
./crm/run_oneway_fea.sh --help
./run_full_model.sh --help

chrono-env/bin/python -m py_compile <changed-python-files>
chrono-env/bin/python tests/test_docs_integration.py
chrono-env/bin/python -m unittest discover -s tests -p 'test_*.py'
```

For flag/default changes, prefer a dry run over a simulation:

```bash
./run_study.sh --dry-run --yes --geometry step --step-file /path/to/your_drum.step
```

Check relative Markdown links after doc edits. The docs set is expected to remain link-clean. A minimal checker:

```bash
chrono-env/bin/python - <<'PY'
from pathlib import Path
import re
root = Path('.')
pattern = re.compile(r'\[[^\]]+\]\(([^)]+)\)')
excluded = {'chrono-env', 'chrono-install', '.git', 'chrono-build', 'chrono-src'}
broken = []
for path in [p for p in root.rglob('*.md') if not any(x in excluded for x in p.parts)]:
    for target in pattern.findall(path.read_text(encoding='utf-8')):
        target = target.split('#', 1)[0].strip()
        if target and not target.startswith(('http://', 'https://', 'mailto:')):
            if not (path.parent / target).resolve().exists():
                broken.append(f'{path}: {target}')
print(f'broken links: {broken}')
PY
```

Also run `git diff --check` and review `git status --short`.

### Known baseline noise

- `test_font_size_applies_directly_and_scale_controls_density` in `tests/test_study_gui_fields.py` can fail on hosts whose font configuration reports a different actual point size (`AssertionError: 3 != 10`). This is pre-existing and documented in `docs/developer-guide.md`; do not treat it as a simulation regression.
- Tk tests may print teardown warnings such as `can't invoke "event" command: application has been destroyed`. These are noise unless a new failure appears.
- STEP parser warnings in tests come from deliberately minimal fixtures.
- The suite uses standard-library `unittest`; no pytest installation is required.

## 6. Documentation writing style

- Beginner docs: short sections, copyable commands, plain language, links to deeper material.
- In-depth docs: explain *why*, describe evidence, and state validation status explicitly.
- Keep all user docs renderable in the GUI Docs tab and linked from `docs/README.md`.
- Say "structurally usable multi-frame PVD", not "complete validated PVD".
- Name the FEA frame directory explicitly: `fea/vtk/frames/`.
- Do not invent bundled geometry or validated FEA results.
- When editing in-app help text, remember the generated Docs page updates automatically, but the static Markdown guides do not.
- Preserve consistency across `README.md`, `docs/`, `crm/README.md`, `crm/FEA_COUPLING.md`, and `FULL_MODEL.md`.
