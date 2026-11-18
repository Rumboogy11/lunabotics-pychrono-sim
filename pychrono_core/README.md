# CPU port — pure PyChrono core

`lunar_excavator_pychrono.py` is the small-scale, pure-PyChrono counterpart of
MercuryDPM's `LunarBucketDrumExcavator`. It uses `ChSystemNSC`, `ChBodyEasySphere`
grains, fixed wall boxes, and the shared drum OBJ mesh. It has no DEM-Engine and
no GPU dependency.

## Files

| File | Purpose |
| --- | --- |
| `lunar_excavator_pychrono.py` | simulation driver |
| `run.sh` | launcher (forwards all arguments) |

## Running

```bash
PYTHON=/path/to/pychrono-venv/bin/python ./run.sh --smoke-test
PYTHON=/path/to/pychrono-venv/bin/python ./run.sh --duration 2 --no-output
```

Useful options include `--particle-radius`, `--duration`, `--fps`, `--mesh
{coarse,deme}` (`coarse` is the default; `deme` is the legacy simplification),
`--output-dir`, and `--no-output`. The default parameter set is
the deliberately small `PYCHRONO_DEMO` (4 mm grains, 25 mm bed); it inherits the
selected global X spin and 1.5 mm/s descent. Pass `--particle-radius 0.001` for
Mercury-scale grains, at a much larger CPU cost.

## Visualization caveat

The validated pip wheel (`pychrono==10.0.0rc1`) contains only core, robot, and
postprocess modules. It does **not** include `pychrono.irrlicht` or
`pychrono.vsg3d`, so `--vis` will fail with that wheel. For a live PyChrono
view, install the conda-forge / Project Chrono PyChrono package (which includes
Irrlicht) or build PyChrono from source with `CH_ENABLE_MODULE_IRRLICHT=ON`.
Headless runs and CSV output work in the pip environment. See
`../environment/INSTALL.md`.

## Output

* `spheres_%06d.csv` — header `X,Y,Z,r,family`, same as the DEME port
* `drum_state.csv` — drum position and quaternion per output frame
* `summary.csv` — frame, time, particle count, and drum height
