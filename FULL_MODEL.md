# Combined CRM + FEA production model

`./run_full_model.sh` runs both simulation paths with one shared setup and
writes a single combined report:

1. **Rigid CRM production stage** — full coarse OBJ, 4 mm SPH spacing,
   `dt=5e-4 s`; use this for fill ratio, bulk torque, and specific energy.
2. **Spindle-driven solid FEA diagnostic stage** — promoted tetrahedral mesh,
   12 mm SPH spacing, `dt=2.5e-4 s`, two flex-BCE layers; its outputs are not
   validated structural estimates.

```bash
# Full production run (20 s by default).
./run_full_model.sh --output-dir Output_FullModel

# Short end-to-end check of the orchestration only.  The FEA stage can still
# abort even in this short case; the combined report records the failure.
./run_full_model.sh --smoke-test --output-dir /tmp/opencode/full_model_smoke

# Print both stage commands and write a dry-run report without simulating.
./run_full_model.sh --dry-run
```

The FEA stage is included for diagnostics and is **not engineering-validated**.
Earlier published fill values included BCE markers and must be discarded.
Correct fluid-only rigid CRM checks at 12 mm spacing give fill `0.002755` at
0.2 s and `0.019286` at 0.5 s. Tested FEA cases remain inconsistent: one BCE
layer crashes, two layers retain a small fixed count through 0.2 s but diverge
by 0.5 s, and three layers either fail in interacting runs or show no retention
in a stable gap case. The combined runner defaults to the two-layer case, which
has the longest tested interacting run, and passes `--duration` to both stages.
The FEA stage may still diverge or fail before reaching that duration. The
orchestrator records FEA failure and succeeds if
rigid CRM succeeded unless `--strict-fea` is set. Do not use FEA stress, torque,
or fill as design validation.

## Outputs

With `--output-dir Output_FullModel`:

```text
Output_FullModel/
├── combined_report.json     settings, stage status, commands, final metrics
├── combined_summary.csv     one row per model with key metrics
├── crm.log                  stdout/stderr of the rigid CRM stage
├── fea.log                  stdout/stderr of the FEA stage
├── crm/
│   ├── config.json
│   └── summary.csv
└── fea/
    ├── config.json
    └── summary.csv
```

`combined_summary.csv` includes fill ratio, peak fill, inside/excavated counts,
fluid torque, spindle torque, work, specific energy, maximum nodal displacement,
and maximum von Mises stress. Missing fields are left blank if a stage did not
reach an output frame.

## Options

| Option | Meaning |
| --- | --- |
| `--duration T` | simulated duration for both stages (default 20 s) |
| `--fea-duration T` | separate FEA duration (default: `--duration`) |
| `--crm-spacing D` | rigid CRM SPH spacing (default 0.004 m) |
| `--crm-dt S` | rigid CRM timestep (default 5e-4 s) |
| `--crm-fps F` | rigid CRM output cadence (default 20 Hz) |
| `--fea-spacing D` | FEA SPH spacing (default 0.012 m) |
| `--fea-dt S` | FEA timestep (default 2.5e-4 s) |
| `--fea-fps F` | FEA output cadence (default 5 Hz) |
| `--fea-stress-stride N` | stress evaluation stride (default 100) |
| `--fea-bce-layers N` | FEA flex-BCE layers (default 2; configurable for diagnostics) |
| `--fea-threads N` | Chrono/Eigen CPU threads for the FEA stage (default 8) |
| `--extra-crm "..."` | extra argument for `crm/run.sh`; repeatable |
| `--extra-fea "..."` | extra argument for `crm/run_fea.sh`; repeatable |
| `--strict-fea` | exit non-zero if FEA fails |
| `--dry-run` | print commands and write a report without simulating |
| `--smoke-test` | 0.05 s end-to-end orchestration check |

See [crm/FEA_COUPLING.md](crm/FEA_COUPLING.md) for the FEA limitations and
[crm/README.md](crm/README.md) for the rigid CRM production driver.
