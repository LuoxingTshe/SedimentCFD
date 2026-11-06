"""Experimental adapter for Thetis commit 162c605079611dfce69b4d1182181a421116b141.

Imports the actual upstream solver lazily. No substitute physics when missing.
Runtime integration requires a matching Firedrake main environment.
"""
from dataclasses import asdict, dataclass
import importlib.util
import json
import math
from pathlib import Path
import subprocess

from .engine import Config

UPSTREAM_COMMIT = '162c605079611dfce69b4d1182181a421116b141'


@dataclass
class ThetisSettings:
    grain_size: float = 0.00016  # m, noncohesive sand
    bed_reference_height: float = 0.025  # m
    molecular_viscosity: float = 1e-6  # m²/s, sediment closure
    horizontal_viscosity: float = 0.01  # m²/s, flow mixing; calibrate
    spinup: float = 120.0  # s, NOT an assurance of steady state
    timestep: float = 0.25
    bedload: bool = False

    def validate(self, c):
        c.validate()
        for k, v in asdict(self).items():
            if k == 'bedload':
                if type(v) is not bool:
                    raise ValueError('bedload must be bool')
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                raise ValueError(f'{k} must be finite and positive')
        if c.velocity <= 0:
            raise ValueError('This adapter requires positive inflow velocity')
        if not self.grain_size * 3 < self.bed_reference_height < c.depth:
            raise ValueError('Require 3 * grain_size < bed_reference_height < depth')
        dstar = self.grain_size*(9.81*(c.sediment_density/1000-1)/self.molecular_viscosity**2)**(1/3) if c.sediment_density > 1000 else 0
        if dstar < 1:
            raise ValueError('Upstream sediment closure requires D* >= 1 and density > water density')


def depth_integrated_volume(mass_concentration, depth, density):
    """kg/m³ -> H * solid volume fraction [m]. Accepts UFL expressions."""
    return depth * (mass_concentration / density)


def environment_report():
    modules = {name: importlib.util.find_spec(name) is not None for name in ('firedrake', 'petsc4py', 'mpi4py', 'thetis')}
    checkout = Path(__file__).resolve().parents[1] / 'external' / 'thetis'
    sha = None
    if (checkout / '.git').exists():
        p = subprocess.run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], capture_output=True, text=True)
        if p.returncode == 0:
            sha = p.stdout.strip()
    return dict(modules=modules, dependencies_present=all(modules.values()), checkout_commit=sha,
                expected_commit=UPSTREAM_COMMIT, checkout_matches=sha == UPSTREAM_COMMIT,
                runtime_validated=False)


def run(c: Config, output: Path, settings=None, observer=None):
    s = settings or ThetisSettings()
    s.validate(c)
    env = environment_report()
    if not env['dependencies_present']:
        raise RuntimeError('Thetis backend needs a matching Firedrake/PETSc environment. See THETIS.md; no simulation was run.')
    import thetis as th
    from firedrake import (RectangleMesh, FunctionSpace, Function, Constant, SpatialCoordinate,
                           conditional, le, And, max_value, as_vector, assemble, dx)
    class ConstantVelocitySedimentModel(th.sediment_model.SedimentModel):
        def get_advective_velocity_correction_factor(self):
            # This upstream revision returns int(1) when disabled. CrankNicolson
            # only copies Function/Constant fields, otherwise losing the factor.
            if not self.use_advective_velocity_correction:
                return Constant(1.0)
            return super().get_advective_velocity_correction_factor()

    # Separate sand density from water density; Thetis defaults may be oceanic.
    th.physical_constants['rho0'].assign(1000.0)
    # Boundary ids: 1 left (uniform inflow), 2 right (outlet), 3/4 banks.
    grid = RectangleMesh(c.nx, c.ny, c.length, c.width)
    V = FunctionSpace(grid, 'CG', 1)
    bath = Function(V, name='bathymetry_2d').assign(c.depth)  # positive down
    x, y = SpatialCoordinate(grid)
    ratio = Constant(1.0)
    for g in c.gabions:
        a = math.radians(g.angle)
        along = (x-g.x)*math.cos(a)+(y-g.y)*math.sin(a)
        across = -(x-g.x)*math.sin(a)+(y-g.y)*math.cos(a)
        inside = And(le(abs(along), g.length/2), le(abs(across), g.width/2))
        ratio = max_value(ratio, conditional(inside, g.resistance, 1.0))
    drag = Function(FunctionSpace(grid, 'DG', 0), name='gabion_drag').interpolate(c.drag*(ratio-1))
    # Grain friction is handled by Nikuradse; add only EXCESS gabion drag.
    discharge = c.velocity*c.depth*c.width

    def build(folder, duration, sediment):
        model = th.solver2d.FlowSolver2d(grid, bath)
        o = model.options
        o.element_family = 'dg-dg'
        o.polynomial_degree = 1
        o.use_nonlinear_equations = True
        o.linear_drag_coefficient = drag
        o.nikuradse_bed_roughness = Constant(3*s.grain_size)
        o.horizontal_viscosity = Constant(s.horizontal_viscosity)
        o.norm_smoother = Constant(0.01)
        o.simulation_end_time = duration
        o.simulation_export_time = duration/40
        o.timestep = duration / math.ceil(duration/s.timestep)
        o.output_directory = str(folder)
        o.check_volume_conservation_2d = True
        o.fields_to_export = ['uv_2d', 'elev_2d', 'bathymetry_2d']
        o.fields_to_export_hdf5 = []
        if sediment:
            so = o.sediment_model_options
            so.sediment_model_class = ConstantVelocitySedimentModel
            so.solve_suspended_sediment = True
            so.solve_exner = True
            so.use_bedload = s.bedload
            so.use_angle_correction = s.bedload
            so.use_slope_mag_correction = s.bedload
            so.use_sediment_conservative_form = True
            so.average_sediment_size = Constant(s.grain_size)
            so.bed_reference_height = Constant(s.bed_reference_height)
            so.morphological_viscosity = Constant(s.molecular_viscosity)
            so.sediment_density = Constant(c.sediment_density)
            so.porosity = Constant(c.bed_porosity)
            so.horizontal_diffusivity = Constant(c.diffusion)
            so.morphological_acceleration_factor = Constant(1)
            so.check_sediment_conservation = True
            so.check_sediment_overshoot = True
            so.use_advective_velocity_correction = False
            o.fields_to_export.append('sediment_2d')
        o.set_timestepper_type('CrankNicolson', implicitness_theta=1.0)
        if sediment:
            # q is typically only 1e-5 m; default absolute solver tolerances
            # can accept negative values in depleted cells. Resolve the small
            # sediment system directly and tighten the nonlinear residual.
            so.sediment_timestepper_options.solver_parameters = {
                'snes_rtol': 1e-9, 'snes_atol': 1e-13,
                'ksp_type': 'preonly', 'pc_type': 'lu',
                'pc_factor_mat_solver_type': 'mumps', 'mat_type': 'aij'}
        model.bnd_functions['shallow_water'] = {
            1: {'flux': Constant(-discharge)}, 2: {'elev': Constant(0)},
            3: {'un': Constant(0)}, 4: {'un': Constant(0)}}
        return model

    hydro = build(output/'spinup', s.spinup, False)
    hydro.assign_initial_conditions(uv=as_vector((c.velocity, 0)), elev=Constant(0))
    hydro.iterate()
    model = build(output/'morphology', c.duration, True)
    model.create_equations()
    # Sediment-laden water: inflow and initial water carry the configured mass
    # concentration (converted to q = H*C/rho_s). Concentrations well above the
    # flow's transport capacity need a fine grain to stay in suspension; with
    # 0.2 mm sand everything settles within ~0.1 m of the inlet.
    H = model.depth.get_total_depth(model.fields.elev_2d)
    model.bnd_functions['sediment'] = {
        1: {'flux': Constant(-discharge), 'value': depth_integrated_volume(c.inlet_concentration, H, c.sediment_density)},
        2: {'elev': Constant(0)}, 3: {'un': Constant(0)}, 4: {'un': Constant(0)}}
    H0 = bath + hydro.fields.elev_2d
    model.assign_initial_conditions(uv=hydro.fields.uv_2d, elev=hydro.fields.elev_2d,
        sediment=depth_integrated_volume(c.initial_concentration, H0, c.sediment_density))
    records = []

    def record():
        # Signed bed change: positive deposition, negative erosion.
        suspended = float(assemble(c.sediment_density*model.fields.sediment_2d*dx))
        bed = float(assemble(c.sediment_density*(1-c.bed_porosity)*(c.depth-bath)*dx))
        entry = dict(time=float(model.simulation_time), suspended_kg=suspended, net_bed_change_kg=bed)
        records.append(entry)
        if observer is not None:
            observer(model)
        if grid.comm.rank == 0:
            (output/'inventory.json').write_text(json.dumps(records, indent=2))

    if grid.comm.rank == 0:
        output.mkdir(parents=True, exist_ok=True)
        (output/'run_config.json').write_text(json.dumps(dict(config=asdict(c), settings=asdict(s), environment=env,
            upstream_module=str(Path(th.__file__).resolve()), thetis_version=getattr(th, '__version__', 'unknown'),
            sediment_field='H * solid volume fraction [m]',
            sediment_inflow='fixed concentration (inlet_concentration); initial = initial_concentration',
            boundary_mode='fixed discharge'), indent=2))
    model.iterate(export_func=record)
    return model
