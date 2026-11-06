"""Run a physically parameterized, uncalibrated Thetis scenario and export cell averages."""
from pathlib import Path
import argparse
import json
import sys
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sedimentcfd import Config
from sedimentcfd.thetis_backend import run, ThetisSettings
from firedrake import Function, FunctionSpace, VectorFunctionSpace
ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--request', type=Path)
parser.add_argument('--output', type=Path, default=ROOT/'outputs/physical_preview')
args = parser.parse_args()
request = json.loads(args.request.read_text()) if args.request else {}
config_data = request.get('config', json.loads((ROOT/'examples/physical_preview.json').read_text()))
c = Config.from_dict(config_data)
out = args.output
out.mkdir(parents=True, exist_ok=True)
# 0.063 mm: fine enough for a sediment-laden (1 kg/m³) inflow to stay suspended.
s = ThetisSettings(grain_size=.000063, spinup=60, timestep=.1, horizontal_viscosity=.01)
frames = []
data = dict(config=config_data, frames=frames,
    status='物理参数化场景；尚无实测标定', grain_mm=round(s.grain_size*1000, 4), spinup_s=s.spinup,
    solver='Thetis / Firedrake · P1DG shallow water + conservative sediment + Exner',
    representation='每个三角形的 DG0 单元平均；流向箭头来自真实速度场')
state = {}
def capture(m):
    mesh = m.fields.bathymetry_2d.function_space().mesh()
    if not state:
        if mesh.comm.size != 1: raise RuntimeError('Preview export currently supports serial runs only')
        V = FunctionSpace(mesh,'DG',0)
        state.update(V=V, scalar=Function(V), uv=Function(VectorFunctionSpace(mesh,'DG',0)), ids=V.cell_node_map().values[:,0])
        data['points'] = mesh.coordinates.dat.data_ro.tolist()
        data['triangles'] = mesh.coordinates.cell_node_map().values.tolist()
    ids = state['ids']
    def avg(expr):
        a = state['scalar'].project(expr).dat.data_ro[ids].copy()
        if not np.isfinite(a).all(): raise ValueError('Nonfinite preview field')
        return a.tolist()
    H = m.depth.get_total_depth(m.fields.elev_2d)
    uv = state['uv'].project(m.fields.uv_2d).dat.data_ro[ids].copy()
    if not np.isfinite(uv).all(): raise ValueError('Nonfinite velocity')
    frames.append(dict(time=float(m.simulation_time), raw_q_min=float(m.fields.sediment_2d.dat.data_ro.min()), uv=uv.tolist(),
        concentration=avg(c.sediment_density*m.fields.sediment_2d/H),
        bed=avg(1000*(c.depth-m.fields.bathymetry_2d)), depth=avg(H)))
model = run(c,out,s,observer=capture)
(out/'preview_diagnostics.json').write_text(json.dumps(dict(min_raw_q=min(f['raw_q_min'] for f in frames), min_mean_concentration=min(min(f['concentration']) for f in frames))))
if min(min(f['depth']) for f in frames)<=0: raise ValueError('Nonpositive depth')
data['numerics'] = dict(min_raw_q=min(f['raw_q_min'] for f in frames), min_mean_concentration=min(min(f['concentration']) for f in frames), timestep=s.timestep)
# Gate undershoot against the peak computed concentration.
peak = max(max(f['concentration']) for f in frames)
data['numerics']['max_mean_concentration'] = peak
if data['numerics']['min_mean_concentration'] < -0.001*peak:
    raise ValueError('Concentration undershoot exceeds 0.1% of peak concentration')
sm = model.sediment_model
data['closure'] = {key:float(np.mean(getattr(sm,key).dat.data_ro)) for key in ['dstar','thetacr','taucr','settling_velocity']}
data['inventory'] = json.loads((out/'inventory.json').read_text())
(out/'preview_data.json').write_text(json.dumps(data,allow_nan=False))
template=(ROOT/'sedimentcfd/web/thetis_preview.html').read_text()
(out/'index.html').write_text(template.replace('__RESULT_JSON__',json.dumps(data,allow_nan=False).replace('<','\\u003c')))
print('PREVIEW_READY',out/'index.html',flush=True)
