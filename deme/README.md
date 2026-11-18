# GPU port — DEM-Engine / pyDEME

`lunar_excavator_deme.py` is a grain-resolving validation port of
`LunarBucketDrumExcavator.cpp`. It uses DEME 3.1.0's GPU clump–mesh contact
pipeline and the shared parameters in `../lunar_excavator_config.py`.

> **Role change:** this port is no longer the primary design solver. The project
> goal is now excavator geometry optimization and fill ratio, so the primary
> model should be a calibrated continuum solver such as Chrono::CRM. Use this
> DEME port for short, targeted checks of local discrete mechanisms and for
> calibrating the continuum model, not for large geometry sweeps.

## Files

| File | Purpose |
| --- | --- |
| `lunar_excavator_deme.py` | simulation driver |
| `run.sh` | launcher; sets DEME JIT cache env vars and forwards all args |
| `postprocess.py`, `postprocess.sh` | per-frame bed/drum summary and optional merged particle CSV |
| `view.sh` | default viewer entry point (Matplotlib/Tk) |
| `view_frames.py` | default playback viewer for `spheres_*.csv` frames |
| `view_paraview.sh` | optional ParaView GUI launcher |
| `visualise.py` | optional ParaView pipeline for particles, drum, and walls |

## Running

```bash
# quick validation (810 coarse grains, 0.05 s)
PYTHON=/path/to/deme-venv/bin/python ./run.sh --smoke-test

# production: global X spin, 0.9 mm grains, 60 mm bed, 1.5 mm/s descent
PYTHON=/path/to/deme-venv/bin/python ./run.sh \
    --duration 60 --output-dir Output_DEME_LunarExcavator

# numeric summary (no GUI dependency)
./postprocess.sh --output-dir Output_DEME_LunarExcavator --fps 120

# default interactive viewer: Matplotlib/Tk, reads the CSV frames directly
./view.sh --output-dir Output_DEME_LunarExcavator --stride 8

# optional ParaView GUI, inherited from MercuryDPM's original helper
./view.sh --paraview --output-dir Output_DEME_LunarExcavator
```

> `Output_DEME_LunarExcavator/` now contains a fresh 1 s global-X-spin run with
> 88,440 grains and the full coarse drum geometry. Superseded mixed, Y-spin, and
> simplified-mesh runs were removed. The viewer still skips mismatched particle
> counts and checks the recorded spin axis before replaying `drum_*.vtk`.

Useful options:

| Option | Meaning |
| --- | --- |
| `--particle-radius R` | grain radius in metres (default 0.0009) |
| `--duration T` | simulated seconds (default 60) |
| `--fps F` | output frames per second (default 120; does **not** change `dt`) |
| `--mesh {coarse,medium,deme}` | mesh used for the drum (default: full `coarse`, which keeps the holes; `deme` is the legacy 1200-face simplification) |
| `--device N` | CUDA device index |
| `--settle-time T` | delay before spin/descent |
| `--spin-axis {X,Y,Z}` | override the configured **global** spin axis (default X) |
| `--no-output` | benchmark without writing frames |
| `--smoke-test` | 4 mm grains, 0.05 s, 50 fps |

First-time kernel compilation can take a long time. `run.sh` sets
`DEME_JIT_CACHE_DIR` and `DEME_PERSISTENT_JITIFY_CACHE` under `~/.cache/deme`
so subsequent runs reuse the compiled kernels.

## Output format

* `spheres_%06d.csv` — header `X,Y,Z,r,family`; one row per grain.
* `drum_%06d.vtk` — legacy VTK unstructured grid with the transformed drum mesh.
* `walls_%06d.vtk` — legacy VTK polydata for the five analytical planes.

`postprocess.py` writes `summary.csv` with particle count, bed centroid/extent,
and the drum's minimum z (bottom height). `--merged` additionally writes a single
`spheres_merged.csv` with `frame,time` prepended.

A full 60 s / 120 fps production run writes 7,201 frames. At 0.9 mm grains with
the final X-spin bed, each sphere frame is about 3.45 MB and each coarse drum
VTK frame about 0.83 MB, so expect roughly **25 GB** of sphere CSV plus **6 GB**
of drum VTK (~31 GB total). The coarse mesh costs more than the legacy 1,200-face
mesh: a 1.0 s validation took 631.0 s wall time (versus 210.9 s previously),
implying **10.5 hours** for the full run; it has not been completed at the
selected settings. Use `--no-output` for benchmark runs.

### Viewing frames

`view.sh` defaults to `view_frames.py`, a NumPy + Matplotlib/Tk viewer. It reads
`spheres_*.csv` and, in `auto` mode, uses the actual `drum_*.vtk` pose only when
the recorded spin axis matches the configured axis. Otherwise it warns and uses
the analytic pose, so stale frames cannot make a Y-spin setup look like an
X-spin run. The pose is drawn on the full coarse OBJ with the retaining-wall
box, and the Tk window stays open until you close it. It does not need ParaView
or PyChrono's GUI modules. `view.sh` selects `./deme-env/bin/python`
automatically (then falls back to `/tmp/opencode/deme-venv`); set `PYTHON=...`
to override.

| Viewer option | Meaning |
| --- | --- |
| `--stride N` | plot every Nth grain (default 8; use 1 for all ~88k) |
| `--interval MS` | nominal ms per frame (default 100) |
| `--start N`, `--stop N` | select a frame-index range |
| `--fps F` | output fps when `summary.csv` is unavailable |
| `--point-size S` | grain marker size |
| `--mesh {coarse,medium,deme}` | visualization OBJ (default: full coarse, matching production; `deme` is the legacy 1200-face simplification) |
| `--drum-source {auto,vtk,analytic}` | pose source (default `auto`: use VTK only when its spin axis matches the config; `vtk` forces recorded pose) |
| `--drum-stride N` | plot every Nth drum face (default 1) |
| `--save PATH` | save the selected frame as PNG and exit |
| `--no-drum`, `--no-walls` | hide overlays |
| `--no-repeat` | stop at the last frame |
| `--no-render` | headless scene check, no window |

The viewer's display mesh is independent of the pose-source mesh. It defaults to
the full 7,572-face coarse OBJ, which is also the production simulation mesh, so
the screw holes stay visible. For old runs made with the legacy simplification,
the pose source is detected automatically from the VTK point count; use
`--mesh deme` to display that coarse old mesh exactly. The viewer title shows
the drum centre height and the detected pose-source mesh.

Space pauses/resumes and Left/Right step one frame. `--no-render` is useful for
validating a new output directory without opening a window.

ParaView remains optional and is not part of the DEM solver. `view_paraview.sh`
launches the ParaView GUI and passes the output directory through
`LUNAR_OUTPUT_DIR`; `--no-render` passes `LUNAR_NO_RENDER=1` for headless
pipeline checks. The host's ParaView 6.x / Qt 6 build segfaults after pipeline
setup, so that GUI path is best-effort. `QT_QPA_PLATFORM=xcb` avoids ParaView's
Wayland warning but does not fix that Qt 6 crash on this machine.

## Solver-specific notes

* Families: `0` grains, `2` drum. The five analytical planes are deliberately
  left in DEME's default family; calling `SetFamily()` on a `DEMExternObj`
  plane in DEME 3.1.0 silently disables its contact with grains.
* Plane normals point into the free space containing the grains (opposite to
  MercuryDPM's `InfiniteWall::set()` convention).
* The drum motion is imposed with JIT expressions:
  `SetFamilyPrescribedPosition(2, "0", "0", z_expr, False)` and
  `SetFamilyPrescribedAngVel(2, wx, wy, wz, False)`. The default
  `drum_spin_axis="X"` is converted from a global axis to DEME's drum-local
  components by `global_axis_local()`: global X is local Z, so the solver gets
  `(wx, wy, wz) = (0, 0, omega)`. The X retaining wall uses
  `drum_half_length + bed_margin_x`. Use `--spin-axis Y|Z` for other global axes.
* The Hertz material is derived from MercuryDPM's `(k, c, mu)` as documented in
  `../README.md`. Four substeps per MercuryDPM step were validated at 1 mm;
  `ExcavatorParams.deme_substeps` automatically increases this for smaller
  grains. The selected 0.9 mm setup uses 5 substeps, giving `dt = 8.333e-6 s`.
* The default mesh is the full 7,572-face coarse OBJ, which preserves the screw
  holes. `--mesh medium` uses the 12,956-face OBJ; `--mesh deme` selects the
  optional legacy 1,200-face simplification, which closes some holes and is not
  used for production.

## Troubleshooting

If you see `On average a primitive ... has N contacts ... more than the max
allowance`, check for initial overlaps or a changed `dt`. The script already
enables `SetCDMaxUpdateFreq(1)` and automatic expansion safety; do not raise the
allowance to hide diverged particles. If JIT compilation fails, verify that the
`deme[cuda12]` extra is installed and that `CUDA_HOME` matches the loaded NVRTC
version.
