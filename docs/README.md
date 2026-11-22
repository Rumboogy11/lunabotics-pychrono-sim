# Documentation for the lunar bucket-drum excavator tools

Welcome. This project simulates a lunar bucket-drum excavator digging into a soil bed. It combines a fast production rigid-soil model (using best-estimate, uncalibrated soil parameters) with an optional structural replay, and it gives you three ways to drive the same workflow: a desktop GUI, an interactive terminal wizard, and individual command-line tools.

These pages are written to be read in order if you are new, or jumped into directly if you know what you need.

> All of these documents are also available **inside the application**. Launch `./run_study_gui.sh`, open the **Docs** tab, and pick a page from the list on the left. The built-in reader styles headings, inline code, and quotes, renders tables as real columns, and its links jump between pages. **Find** steps through matches on the page you are viewing, and **Refresh** reloads the selected page after you edit a file or change GUI help text.

## Start here (beginner path)

1. [Quick start: your first study](quickstart.md) — install, prepare geometry, run a study, and find your results. If you read only one page, read this one.
2. [Glossary](glossary.md) — plain-language definitions for CRM, SPH, BCE, FEA, fill ratio, and the rest of the vocabulary.
3. [GUI guide](gui-guide.md) — every tab, page, button, and field in the desktop application, with recommended settings.

## Use the tools (workflow path)

4. [Command-line study wizard](wizard-guide.md) — the interactive terminal alternative to the GUI, including non-interactive and dry-run use.
5. [Command-line reference](cli-reference.md) — a compact catalogue of every launcher and helper script.
6. [Outputs and visualization](outputs-and-visualization.md) — what the study writes, how to read the CSVs, and how to view particles, poses, stress, and charts.
7. [Troubleshooting](troubleshooting.md) — what to check when a run is slow, empty, or fails.

## Understand the project (in-depth path)

8. [How it works](how-it-works.md) — the simulation pipeline, physics, metrics, geometry conversion, and which results are validated.
9. [Developer guide](developer-guide.md) — repository layout, configuration schema, test suite, and how to extend the GUI and docs.

The original detailed documents are still available and are linked from the Docs tab too:

- [Top-level project README](../README.md) — installation summary, quick commands, and key caveats.
- [CRM solver reference](../crm/README.md) — every rigid-CRM option, soil parameters, and output files.
- [One-way and two-way FEA coupling](../crm/FEA_COUPLING.md) — coupling status and the evidence behind it.
- [Combined diagnostic runner](../FULL_MODEL.md) — the experimental rigid + FEA orchestrator.
- [Environment and build options](../environment/INSTALL.md) — CUDA, conda, ParaView, and no-root installs.

## Which path should I use?

| I want to… | Use |
| --- | --- |
| Run a study without learning the command line | [Quick start](quickstart.md) and the [GUI guide](gui-guide.md) |
| Automate studies in a script | [Wizard guide](wizard-guide.md) and [CLI reference](cli-reference.md) |
| Understand what "fill ratio" actually measures | [How it works](how-it-works.md) and the [Glossary](glossary.md) |
| Open or interpret results | [Outputs and visualization](outputs-and-visualization.md) |
| Diagnose a failed or strange run | [Troubleshooting](troubleshooting.md) |
| Change the code or docs | [Developer guide](developer-guide.md) |

## Important status notes

- The **rigid Chrono::CRM** stage is the production/design model. Use it for geometry comparisons of fill, torque, and specific energy.
- The **one-way CRM → FEA replay** is a structural screening tool: it replays recorded soil loads on a solid mesh. It is not two-way fluid–structure interaction.
- The **two-way FEA** and combined `full_model.py` paths are diagnostics only. Their stress and fill results are **not engineering-validated**.
- Soil and excavation metrics count **SPH fluid markers only**. Older comparisons that included boundary (BCE) markers are invalid.
- No CAD or mesh geometry is bundled with the project. You supply your own STEP or mesh files; `geometry/` is ignored by git.
