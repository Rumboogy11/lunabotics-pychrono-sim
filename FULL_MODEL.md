# Combined rigid CRM + FEA diagnostic runner

`./run_full_model.sh` runs a rigid Chrono::CRM stage and a spindle-driven solid-FEA stage
with shared motion/soil settings, then writes a combined report.

**Status/caveats**
- The **rigid CRM stage** is the production/design driver for fill ratio, bulk torque, and specific energy.
- The **FEA stage** is a diagnostic only and is **not engineering-validated** (two-way flex-BCE coupling remains unstable/inconsistent; see `crm/FEA_COUPLING.md`).
- Soil metrics count **SPH fluid markers only**; older comparisons that included BCE markers are invalid.
- By default the orchestrator records FEA failure but still succeeds if rigid CRM succeeded. Use `--strict-fea` to fail on FEA errors.

For a stable structural screening path, use the one-way workflow instead: export rigid CRM loads and replay with `./crm/run_oneway_fea.sh` (see `crm/README.md`).

## Usage

```bash
# Full run (20 s by default)
./run_full_model.sh --output-dir Output_FullModel

# Short end-to-end orchestration check (FEA may still fail/diverge)
./run_full_model.sh --smoke-test --output-dir Output_FullModel_Smoke

# Print commands and write a dry-run report without simulating
./run_full_model.sh --dry-run --output-dir Output_FullModel_DryRun
```

Defaults:
- Rigid CRM: coarse OBJ, 2 mm SPH spacing, spacing-dependent `dt` (4e-5 s at 2 mm)
- FEA: promoted solid mesh, 12 mm SPH spacing, `dt=2.5e-4 s`, 2 BCE layers (diagnostic)

## Outputs

With `--output-dir Output_FullModel`:

```text
Output_FullModel/
├── combined_report.json     settings, stage status, commands, final metrics
├── combined_summary.csv     one row per stage with key metrics
├── crm.log                  rigid CRM stdout/stderr
├── fea.log                  FEA stdout/stderr
├── crm/
│   ├── config.json
│   └── summary.csv
└── fea/
    ├── config.json
    └── summary.csv
```

`combined_summary.csv` includes fill ratio, peak fill, inside/excavated counts, torque/work/specific energy,
and (when available) FEA displacement/von Mises. Missing fields are left blank if a stage did not reach an output frame.

## Options

| Option | Meaning |
| --- | --- |
| `--duration T` | simulated duration for both stages (default 20 s) |
| `--fea-duration T` | separate FEA duration (default: `--duration`) |
| `--crm-spacing D` | rigid CRM SPH spacing (default 0.002 m) |
| `--crm-dt S` | rigid CRM timestep (spacing-dependent default) |
| `--crm-fps F` | rigid CRM output cadence (default 20 Hz) |
| `--fea-spacing D` | FEA SPH spacing (default 0.012 m) |
| `--fea-dt S` | FEA timestep (default 2.5e-4 s) |
| `--fea-fps F` | FEA output cadence (default 5 Hz) |
| `--fea-stress-stride N` | stress evaluation stride (default 100) |
| `--fea-bce-layers N` | FEA flex-BCE layers (default 2; diagnostic) |
| `--fea-threads N` | Chrono/Eigen CPU threads for FEA stage (default 8) |
| `--extra-crm "..."` | extra args for `crm/run.sh` (repeatable) |
| `--extra-fea "..."` | extra args for `crm/run_fea.sh` (repeatable) |
| `--strict-fea` | exit non-zero if FEA fails |
| `--dry-run` | print commands/write report without simulating |
| `--smoke-test` | 0.05 s orchestration check |
