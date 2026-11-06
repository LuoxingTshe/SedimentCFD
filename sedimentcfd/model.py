"""Thetis/Firedrake run: 2D shallow water + suspended sediment + Exner bed update.

Firedrake and Thetis are imported lazily so that the rest of the package
(config, server, tests) works without them.
"""
import importlib.util
import json
import math
from pathlib import Path
import subprocess

from .config import WATER_DENSITY, WATER_VISCOSITY, Config

THETIS_COMMIT = '162c605079611dfce69b4d1182181a421116b141'
ROOT = Path(__file__).resolve().parents[1]

# RectangleMesh boundary ids
LEFT, RIGHT, BOTTOM, TOP = 1, 2, 3, 4


def check():
    """Report whether the pinned Firedrake/Thetis environment is importable."""
    modules = {name: importlib.util.find_spec(name) is not None
               for name in ('firedrake', 'petsc4py', 'mpi4py', 'thetis')}
    checkout = ROOT / 'external' / 'thetis'
    commit = None
    if (checkout / '.git').exists():
        result = subprocess.run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True)
        commit = result.stdout.strip() if result.returncode == 0 else None
    return dict(modules=modules, ready=all(modules.values()),
                thetis_commit=commit, expected_commit=THETIS_COMMIT,
                commit_matches=commit == THETIS_COMMIT)


def sediment_q(concentration, depth, sediment_density):
    """Thetis' conservative sediment variable q = H * C / rho_s [m] from C [kg/m³]."""
    return depth * (concentration / sediment_density)


def run(config: Config, output: Path, on_export=None):
    """Spin up the flow, then run coupled flow + sediment + bed.

    `on_export(model)` is called at each of the 41 exports of the coupled run.
    Returns (FlowSolver2d of the coupled run, inventory list of mass budgets).
    """
    config.validate()
    if not check()['ready']:
        raise RuntimeError('Firedrake/Thetis not found. Run `source activate_thetis.sh` '
                           '(or scripts/install_thetis.py first).')
    import thetis
    from firedrake import (And, Constant, Function, FunctionSpace, RectangleMesh, SpatialCoordinate,
                           as_vector, assemble, conditional, dx, le, max_value)

    class SedimentModel(thetis.sediment_model.SedimentModel):
        # This Thetis revision returns int(1) when the correction is off, and
        # CrankNicolson only copies Function/Constant fields, dropping the factor.
        def get_advective_velocity_correction_factor(self):
            if not self.use_advective_velocity_correction:
                return Constant(1.0)
            return super().get_advective_velocity_correction_factor()

    c = config
    output = Path(output)
    thetis.physical_constants['rho0'].assign(WATER_DENSITY)  # Thetis defaults to sea water
    mesh = RectangleMesh(c.nx, c.ny, c.length, c.width)
    bathymetry = Function(FunctionSpace(mesh, 'CG', 1), name='bathymetry_2d').assign(c.depth)

    # Gabions: extra linear drag drag * (resistance - 1) inside each rotated rectangle.
    x, y = SpatialCoordinate(mesh)
    ratio = Constant(1.0)
    for g in c.gabions:
        a = math.radians(g.angle)
        along = (x - g.x) * math.cos(a) + (y - g.y) * math.sin(a)
        across = -(x - g.x) * math.sin(a) + (y - g.y) * math.cos(a)
        inside = And(le(abs(along), g.length / 2), le(abs(across), g.width / 2))
        ratio = max_value(ratio, conditional(inside, g.resistance, 1.0))
    gabion_drag = Function(FunctionSpace(mesh, 'DG', 0), name='gabion_drag')
    gabion_drag.interpolate(c.drag * (ratio - 1))

    inflow = Constant(-c.discharge)  # negative normal flux = into the domain
    walls = {BOTTOM: {'un': Constant(0)}, TOP: {'un': Constant(0)}}

    def solver(folder, duration, sediment):
        model = thetis.solver2d.FlowSolver2d(mesh, bathymetry)
        o = model.options
        o.element_family = 'dg-dg'
        o.polynomial_degree = 1
        o.use_nonlinear_equations = True
        o.linear_drag_coefficient = gabion_drag
        o.nikuradse_bed_roughness = Constant(3 * c.grain_size)
        o.horizontal_viscosity = Constant(c.viscosity)
        o.norm_smoother = Constant(0.01)
        o.simulation_end_time = duration
        o.simulation_export_time = duration / 40
        o.timestep = duration / math.ceil(duration / c.timestep)
        o.output_directory = str(folder)
        o.check_volume_conservation_2d = True
        o.fields_to_export = ['uv_2d', 'elev_2d', 'bathymetry_2d']
        o.fields_to_export_hdf5 = []
        o.set_timestepper_type('CrankNicolson', implicitness_theta=1.0)
        if sediment:
            s = o.sediment_model_options
            s.sediment_model_class = SedimentModel
            s.solve_suspended_sediment = True
            s.solve_exner = True
            s.use_bedload = False
            s.use_sediment_conservative_form = True
            s.use_advective_velocity_correction = False
            s.average_sediment_size = Constant(c.grain_size)
            s.bed_reference_height = Constant(c.reference_height)
            s.morphological_viscosity = Constant(WATER_VISCOSITY)
            s.sediment_density = Constant(c.sediment_density)
            s.porosity = Constant(c.bed_porosity)
            s.horizontal_diffusivity = Constant(c.diffusivity)
            s.morphological_acceleration_factor = Constant(1)
            s.check_sediment_conservation = True
            s.check_sediment_overshoot = True
            # q is only ~1e-5 m: default absolute tolerances let depleted cells go
            # negative, so solve the small sediment system directly and tightly.
            s.sediment_timestepper_options.solver_parameters = {
                'snes_rtol': 1e-9, 'snes_atol': 1e-13, 'ksp_type': 'preonly', 'pc_type': 'lu',
                'pc_factor_mat_solver_type': 'mumps', 'mat_type': 'aij'}
            o.fields_to_export.append('sediment_2d')
        model.bnd_functions['shallow_water'] = {
            LEFT: {'flux': inflow}, RIGHT: {'elev': Constant(0)}, **walls}
        return model

    # 1. Hydrodynamic spin-up from uniform flow.
    spinup = solver(output / 'spinup', c.spinup, sediment=False)
    spinup.assign_initial_conditions(uv=as_vector((c.velocity, 0)), elev=Constant(0))
    spinup.iterate()

    # 2. Coupled run with sediment-laden inflow and initial water.
    model = solver(output / 'morphology', c.duration, sediment=True)
    model.create_equations()
    H = model.depth.get_total_depth(model.fields.elev_2d)
    model.bnd_functions['sediment'] = {
        LEFT: {'flux': inflow, 'value': sediment_q(c.inlet_concentration, H, c.sediment_density)},
        RIGHT: {'elev': Constant(0)}, **walls}
    H0 = bathymetry + spinup.fields.elev_2d
    model.assign_initial_conditions(
        uv=spinup.fields.uv_2d, elev=spinup.fields.elev_2d,
        sediment=sediment_q(c.initial_concentration, H0, c.sediment_density))

    inventory = []
    is_root = mesh.comm.rank == 0

    def export():
        suspended = assemble(c.sediment_density * model.fields.sediment_2d * dx)
        bed = assemble(c.sediment_density * (1 - c.bed_porosity) * (c.depth - bathymetry) * dx)
        inventory.append(dict(time=float(model.simulation_time), suspended_kg=float(suspended),
                              net_bed_change_kg=float(bed)))
        if on_export is not None:
            on_export(model)
        if is_root:
            (output / 'inventory.json').write_text(json.dumps(inventory, indent=2))

    if is_root:
        output.mkdir(parents=True, exist_ok=True)
        (output / 'run_config.json').write_text(json.dumps(
            dict(config=c.to_dict(), thetis=str(Path(thetis.__file__).resolve().parent),
                 environment=check()), indent=2))
    model.iterate(export_func=export)
    return model, inventory
