"""Run a scenario and write a self-contained animated page (index.html + preview_data.json)."""
import json
from pathlib import Path

from .config import Config
from . import model as thetis_model

TEMPLATE = Path(__file__).with_name('web') / 'preview.html'


def build_preview(config: Config, output: Path):
    """Run Thetis and export DG0 cell averages for every frame. Returns the page data."""
    import numpy as np
    from firedrake import Function, FunctionSpace, VectorFunctionSpace

    c = config
    output = Path(output)
    frames = []
    data = dict(config=c.to_dict(), frames=frames)
    cache = {}

    def capture(m):
        mesh = m.fields.bathymetry_2d.function_space().mesh()
        if not cache:
            if mesh.comm.size != 1:
                raise RuntimeError('preview export supports serial runs only')
            V = FunctionSpace(mesh, 'DG', 0)
            cache.update(scalar=Function(V), vector=Function(VectorFunctionSpace(mesh, 'DG', 0)),
                         cells=V.cell_node_map().values[:, 0])
            data['points'] = mesh.coordinates.dat.data_ro.tolist()
            data['triangles'] = mesh.coordinates.cell_node_map().values.tolist()
        cells = cache['cells']

        def cell_mean(expr, target=cache['scalar']):
            values = target.project(expr).dat.data_ro[cells].copy()
            if not np.isfinite(values).all():
                raise ValueError('non-finite field in preview export')
            return values

        H = m.depth.get_total_depth(m.fields.elev_2d)
        q = m.fields.sediment_2d
        frames.append(dict(
            time=float(m.simulation_time),
            raw_q_min=float(q.dat.data_ro.min()),
            uv=cell_mean(m.fields.uv_2d, cache['vector']).tolist(),
            concentration=cell_mean(c.sediment_density * q / H).tolist(),  # kg/m³
            bed=cell_mean(1000 * (c.depth - m.fields.bathymetry_2d)).tolist(),  # mm, + = deposition
            depth=cell_mean(H).tolist()))

    solver, inventory = thetis_model.run(c, output, on_export=capture)

    min_depth = min(min(f['depth']) for f in frames)
    if min_depth <= 0:
        raise ValueError(f'non-positive water depth ({min_depth:.3g} m)')
    low = min(min(f['concentration']) for f in frames)
    peak = max(max(f['concentration']) for f in frames)
    if low < -0.001 * peak:
        raise ValueError(f'concentration undershoot {low:.3g} kg/m³ exceeds 0.1% of peak {peak:.3g}')

    sm = solver.sediment_model
    data.update(
        numerics=dict(min_raw_q=min(f['raw_q_min'] for f in frames), min_concentration=low,
                      max_concentration=peak, min_depth=min_depth),
        closure={k: float(np.mean(getattr(sm, k).dat.data_ro))
                 for k in ('dstar', 'thetacr', 'taucr', 'settling_velocity')},
        inventory=inventory)
    write_page(data, output)
    return data


FRAME_FIELDS = ('uv', 'concentration', 'bed', 'depth')


def _rounded(value, digits=4):
    if isinstance(value, list):
        return [_rounded(v, digits) for v in value]
    return float(f'{value:.{digits}g}')


def write_page(data, output: Path):
    """Write preview_data.json and index.html; per-cell frame fields keep 4 significant digits."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    frames = [{k: _rounded(v) if k in FRAME_FIELDS else v for k, v in f.items()} for f in data['frames']]
    text = json.dumps({**data, 'frames': frames}, allow_nan=False)
    (output / 'preview_data.json').write_text(text)
    page = TEMPLATE.read_text().replace('__RESULT_JSON__', text.replace('<', '\\u003c'))
    (output / 'index.html').write_text(page)
    return output / 'index.html'
