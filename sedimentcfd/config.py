"""Scenario parameters (SI units) and their validation."""
from dataclasses import asdict, dataclass, field, fields
import math

GRAVITY = 9.81
WATER_DENSITY = 1000.0
WATER_VISCOSITY = 1e-6  # m²/s, kinematic, ~20 °C; used by the grain-size closures


@dataclass
class Gabion:
    """Rotated rectangle of extra linear drag. Not a solid wall."""
    x: float
    y: float
    length: float
    width: float
    angle: float = 0.0  # degrees counter-clockwise from +x, along `length`
    resistance: float = 10.0  # drag multiplier; extra drag = drag * (resistance - 1)


def _default_gabions():
    return [Gabion(5.0, 2.3, 3.0, 1.5, 60.0, 10.0), Gabion(10.0, 5.7, 3.0, 1.5, 120.0, 10.0)]


@dataclass
class Config:
    # Domain and mesh (rectangle, left inflow, right outlet, impermeable banks)
    length: float = 16.0
    width: float = 8.0
    nx: int = 32  # cells along x
    ny: int = 16  # cells along y
    # Flow
    depth: float = 0.6  # initial water depth
    velocity: float = 0.35  # inflow velocity; discharge = velocity * depth * width
    drag: float = 0.03  # s⁻¹, base linear drag used to scale gabion resistance
    viscosity: float = 0.01  # m²/s, horizontal eddy viscosity (uncalibrated)
    # Sediment (single class, suspended load + Exner bed update)
    grain_size: float = 0.063e-3  # m
    sediment_density: float = 2650.0
    bed_porosity: float = 0.4
    diffusivity: float = 0.01  # m²/s, horizontal sediment diffusivity (uncalibrated)
    reference_height: float = 0.025  # m, near-bed reference height of the closure
    inlet_concentration: float = 1.0  # kg/m³, sediment-laden inflow
    initial_concentration: float = 1.0  # kg/m³
    # Time
    spinup: float = 60.0  # s, hydrodynamics only, before sediment is switched on
    duration: float = 60.0  # s, coupled flow + sediment + bed
    timestep: float = 0.1
    gabions: list[Gabion] = field(default_factory=_default_gabions)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError('config must be a JSON object')
        unknown = set(data) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f'unknown config keys: {sorted(unknown)}')
        data = dict(data)
        if 'gabions' in data:
            if not isinstance(data['gabions'], list):
                raise ValueError('gabions must be a list')
            try:
                data['gabions'] = [Gabion(**g) for g in data['gabions']]
            except TypeError as error:
                raise ValueError(f'invalid gabion: {error}') from None
        config = cls(**data)
        config.validate()
        return config

    def to_dict(self):
        return asdict(self)

    @property
    def discharge(self):
        return self.velocity * self.depth * self.width

    @property
    def dstar(self):
        """Dimensionless grain size used by the upstream sediment closures."""
        relative_density = self.sediment_density / WATER_DENSITY - 1
        return self.grain_size * (GRAVITY * relative_density / WATER_VISCOSITY**2) ** (1 / 3)

    def validate(self):
        for name, value in asdict(self).items():
            if name == 'gabions':
                continue
            if not _is_number(value):
                raise ValueError(f'{name} must be a finite number')
        positive = ('length', 'width', 'depth', 'velocity', 'drag', 'viscosity', 'grain_size',
                    'reference_height', 'spinup', 'duration', 'timestep')
        for name in positive:
            if getattr(self, name) <= 0:
                raise ValueError(f'{name} must be > 0')
        for name in ('diffusivity', 'inlet_concentration', 'initial_concentration'):
            if getattr(self, name) < 0:
                raise ValueError(f'{name} must be >= 0')
        for name in ('nx', 'ny'):
            n = getattr(self, name)
            if type(n) is not int or not 2 <= n <= 240:
                raise ValueError(f'{name} must be an integer in 2..240')
        if not 0 <= self.bed_porosity < 1:
            raise ValueError('bed_porosity must be in [0, 1)')
        if self.sediment_density <= WATER_DENSITY:
            raise ValueError('sediment_density must exceed water density')
        if self.dstar < 1:
            raise ValueError(f'grain_size too fine: D* = {self.dstar:.2f} < 1 (closure needs about >= 0.04 mm)')
        if not 3 * self.grain_size < self.reference_height < self.depth:
            raise ValueError('need 3 * grain_size < reference_height < depth')
        if len(self.gabions) > 30:
            raise ValueError('at most 30 gabions')
        for i, g in enumerate(self.gabions, 1):
            if not isinstance(g, Gabion) or not all(_is_number(v) for v in asdict(g).values()):
                raise ValueError(f'gabion {i}: all values must be finite numbers')
            if g.length <= 0 or g.width <= 0 or not 1 <= g.resistance <= 1e6:
                raise ValueError(f'gabion {i}: size must be > 0 and resistance in 1..1e6')
            if not (0 <= g.x <= self.length and 0 <= g.y <= self.width):
                raise ValueError(f'gabion {i}: centre must lie inside the domain')


def _is_number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
