"""Small real coupled solve; run only in the installed Firedrake environment."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sedimentcfd import Config, Gabion
from sedimentcfd.thetis_backend import ThetisSettings, run

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'outputs' / 'thetis_smoke')
output = parser.parse_args().output
c = Config(length=8, width=4, nx=16, ny=8, velocity=.3, depth=1,
           duration=1, initial_concentration=.15,
           gabions=[Gabion(x=4, y=2, length=2, width=1, angle=60, resistance=4)])
model = run(c, output, ThetisSettings(spinup=1, timestep=.1))
fields = {k: model.fields[k].dat.data_ro for k in ('uv_2d', 'elev_2d', 'sediment_2d', 'bathymetry_2d')}
for name, values in fields.items():
    if not np.isfinite(values).all():
        raise AssertionError(f'Nonfinite field: {name}')
if fields['sediment_2d'].min() < -1e-10:
    raise AssertionError('Negative suspended sediment')
# Check water depth on a common CG1 space, not mismatched DG and CG arrays.
from firedrake import Function
water_depth = Function(model.fields.bathymetry_2d.function_space()).project(
    model.depth.get_total_depth(model.fields.elev_2d))
if water_depth.dat.data_ro.min() <= 0:
    raise AssertionError('Dry/negative depth in a fully wet smoke case')
summary = dict(status='passed', duration_s=float(model.simulation_time),
               min_depth_m=float(water_depth.dat.data_ro.min()),
               min_sediment_q_m=float(fields['sediment_2d'].min()),
               max_bed_change_m=float(np.max(np.abs(c.depth-fields['bathymetry_2d']))),
               limitation='Execution smoke test only; not physical calibration or mass-budget validation')
(output/'smoke_summary.json').write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
