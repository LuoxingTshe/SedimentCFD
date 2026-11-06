"""P1 hydraulic-head FEM + conservative monotone edge transport.

This is a Darcy/linear-drag approximation, not Navier–Stokes. The algebraic
hydraulic fluxes, not smoothed plot velocities, drive sediment transport.
"""
from dataclasses import asdict, dataclass, field
import math
import numpy as np
from scipy.sparse import coo_matrix, diags, triu
from scipy.sparse.linalg import spsolve, factorized


@dataclass
class Gabion:
    x: float = 12.0
    y: float = 4.0
    length: float = 6.0
    width: float = 1.2
    angle: float = 65.0  # degrees CCW from x; length direction
    resistance: float = 30.0  # multiplier >= 1, not material porosity


@dataclass
class Config:
    length: float = 40.0
    width: float = 16.0
    nx: int = 80  # cells, not nodes
    ny: int = 32
    depth: float = 1.0
    velocity: float = 0.45  # unobstructed reference velocity under same head drop
    drag: float = 0.03  # s^-1 linear resistance
    diffusion: float = 0.02  # m^2/s
    inlet_concentration: float = 0.15  # kg/m^3
    initial_concentration: float = 0.0
    settling_velocity: float = 0.003  # m/s; prescribed for a single sediment class
    critical_velocity: float = 0.30  # m/s, empirical deposition threshold
    sediment_density: float = 2650.0  # kg/m^3, grain density
    bed_porosity: float = 0.40
    duration: float = 600.0
    dt: float = 5.0
    gabions: list[Gabion] = field(default_factory=lambda: [Gabion(), Gabion(24, 11, 6, 1.2, 115, 30)])

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("配置必须是 JSON 对象")
        data = dict(data)
        if 'gabions' in data:
            if not isinstance(data['gabions'], list):
                raise ValueError("gabions 必须是数组")
            data['gabions'] = [Gabion(**g) for g in data['gabions']]
        result = cls(**data)
        result.validate()
        return result

    def validate(self):
        for key, value in asdict(self).items():
            if key == 'gabions':
                continue
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError(f"{key} 必须是有限数值")
        for name in ('length', 'width', 'depth', 'drag', 'critical_velocity', 'sediment_density', 'dt', 'duration'):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须大于 0")
        for name in ('velocity', 'diffusion', 'inlet_concentration', 'initial_concentration', 'settling_velocity'):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 不能为负")
        if not 0 <= self.bed_porosity < 1:
            raise ValueError("bed_porosity 必须位于 [0,1)")
        if any(type(n) is not int or n < 2 or n > 240 for n in (self.nx, self.ny)):
            raise ValueError("nx、ny 必须是 2–240 的整数")
        if (self.nx + 1) * (self.ny + 1) > 30000 or math.ceil(self.duration / self.dt) > 5000:
            raise ValueError("原型上限为 30000 节点、5000 时间步")
        if len(self.gabions) > 30:
            raise ValueError("最多 30 个石笼")
        for g in self.gabions:
            if not isinstance(g, Gabion):
                raise ValueError("gabions 必须包含 Gabion")
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in asdict(g).values()):
                raise ValueError("石笼参数必须是有限数值")
            if g.length <= 0 or g.width <= 0 or not 1 <= g.resistance <= 1e6:
                raise ValueError("石笼尺寸须为正，阻力倍数须在 1–1000000 之间")
            if not 0 <= g.x <= self.length or not 0 <= g.y <= self.width:
                raise ValueError("石笼中心须在计算区域内")


def mesh(c):
    x, y = np.meshgrid(np.linspace(0, c.length, c.nx + 1), np.linspace(0, c.width, c.ny + 1))
    points = np.column_stack((x.ravel(), y.ravel()))
    j, i = np.meshgrid(np.arange(c.ny), np.arange(c.nx), indexing='ij')
    a = (j * (c.nx + 1) + i).ravel()
    b, d = a + 1, a + c.nx + 1
    # Structured right triangles guarantee nonpositive stiffness off-diagonals.
    triangles = np.vstack((np.column_stack((a, b, d + 1)), np.column_stack((a, d + 1, d))))
    v = points[triangles]
    twice_area = (v[:, 1, 0] - v[:, 0, 0]) * (v[:, 2, 1] - v[:, 0, 1]) - (v[:, 2, 0] - v[:, 0, 0]) * (v[:, 1, 1] - v[:, 0, 1])
    gradients = np.stack((v[:, [1, 2, 0], 1] - v[:, [2, 0, 1], 1], v[:, [2, 0, 1], 0] - v[:, [1, 2, 0], 0]), axis=2) / twice_area[:, None, None]
    areas = twice_area / 2
    lumped = np.bincount(triangles.ravel(), weights=np.repeat(areas / 3, 3), minlength=len(points))
    return points, triangles, areas, gradients, lumped


def stiffness(triangles, areas, gradients, coefficient, n):
    local = np.einsum('tik,tjk->tij', gradients, gradients) * (areas * coefficient)[:, None, None]
    rows = np.repeat(triangles, 3, axis=1).ravel()
    cols = np.tile(triangles, (1, 3)).ravel()
    return coo_matrix((local.ravel(), (rows, cols)), shape=(n, n)).tocsr()


def solve_flow(c):
    c.validate()
    points, triangles, areas, gradients, lumped = mesh(c)
    centers = points[triangles].mean(axis=1)
    resistance = np.ones(len(triangles))
    counts = []
    for g in c.gabions:
        theta = np.deg2rad(g.angle)
        dx, dy = (centers - [g.x, g.y]).T
        along = dx * np.cos(theta) + dy * np.sin(theta)
        across = -dx * np.sin(theta) + dy * np.cos(theta)
        inside = (np.abs(along) <= g.length / 2) & (np.abs(across) <= g.width / 2)
        resistance[inside] = np.maximum(resistance[inside], g.resistance)
        counts.append(int(inside.sum()))
    mobility = 9.81 / (c.drag * resistance)
    K = stiffness(triangles, areas, gradients, c.depth * mobility, len(points))
    left = points[:, 0] == 0
    right = points[:, 0] == c.length
    fixed = left | right
    free = ~fixed
    head = np.zeros(len(points))
    head[left] = c.velocity * c.length * c.drag / 9.81
    head[free] = spsolve(K[free][:, free], -K[free][:, fixed] @ head[fixed])
    element_velocity = -mobility[:, None] * np.einsum('ti,tik->tk', head[triangles], gradients)
    # Projection is for visualization and deposition closure only.
    nodal_velocity = np.column_stack([np.bincount(triangles.ravel(), weights=np.repeat(element_velocity[:, k] * areas / 3, 3), minlength=len(points)) / lumped for k in range(2)])
    # Average |u|^2, rather than |average(u)|^2, to avoid spurious low shear.
    speed_squared = np.bincount(triangles.ravel(), weights=np.repeat(np.sum(element_velocity**2, axis=1) * areas / 3, 3), minlength=len(points)) / lumped
    boundary = np.zeros(len(points))
    boundary[fixed] = (K @ head)[fixed]  # positive supply, negative outlet
    return dict(points=points, triangles=triangles, areas=areas, gradients=gradients, lumped=lumped, K=K, head=head, velocity=nodal_velocity, element_velocity=element_velocity, speed=np.sqrt(speed_squared), boundary=boundary, resistance=resistance, counts=counts, free=free)


def transport_operator(c, flow):
    K = flow['K']
    edges = triu(K, k=1).tocoo()
    # Flux i -> j. Its nodal divergence equals K @ head.
    flux = edges.data * (flow['head'][edges.col] - flow['head'][edges.row])
    upstream = np.where(flux >= 0, edges.row, edges.col)
    downstream = np.where(flux >= 0, edges.col, edges.row)
    magnitude = np.abs(flux)
    A = coo_matrix((np.r_[magnitude, -magnitude], (np.r_[upstream, downstream], np.r_[upstream, upstream])), shape=K.shape).tocsr()
    D = stiffness(flow['triangles'], flow['areas'], flow['gradients'], np.full(len(flow['triangles']), c.depth * c.diffusion), K.shape[0])
    inflow = np.maximum(flow['boundary'], 0)
    outflow = np.maximum(-flow['boundary'], 0)
    deposition = flow['lumped'] * c.settling_velocity * np.maximum(0, 1 - (flow['speed'] / c.critical_velocity)**2)
    return A + D + diags(outflow + deposition), inflow, outflow, deposition


def simulate(config=None):
    c = Config() if config is None else config
    f = solve_flow(c)
    operator, inflow, outflow, deposition = transport_operator(c, f)
    capacity = c.depth * f['lumped']
    concentration = np.full(len(capacity), c.initial_concentration)
    bed_mass = np.zeros(len(capacity))
    source = inflow * c.inlet_concentration
    initial_mass = float(capacity @ concentration)
    mass_in = mass_out = 0.0
    history, frames = [], []
    steps = max(1, math.ceil(c.duration / c.dt))
    dt = c.duration / steps  # exact end time; never exceeds requested dt
    solve = factorized((diags(capacity / dt) + operator).tocsc())
    stride = max(1, math.ceil(steps / 40))
    bulk_density = c.sediment_density * (1 - c.bed_porosity)

    def record(time):
        suspended = float(capacity @ concentration)
        deposited = float(bed_mass.sum())
        history.append(dict(time=time, suspended_kg=suspended, deposited_kg=deposited, input_kg=mass_in, output_kg=mass_out, balance_error_kg=initial_mass + mass_in - mass_out - suspended - deposited))
        frames.append(dict(time=time, concentration=concentration.tolist(), bed_mm=(1000 * bed_mass / (bulk_density * f['lumped'])).tolist()))

    record(0.0)
    for step in range(1, steps + 1):
        concentration = solve(capacity / dt * concentration + source)
        if not np.isfinite(concentration).all() or concentration.min() < -1e-10:
            raise ArithmeticError("输运解不满足有限性/非负性，请检查参数尺度")
        bed_mass += dt * deposition * concentration
        mass_in += dt * float(source.sum())
        mass_out += dt * float(outflow @ concentration)
        if step % stride == 0 or step == steps:
            record(step * dt)
    final = history[-1]
    warnings = ["阻力主导、恒定水深模型：不解析惯性、湍流尾涡、冲刷、推移质或地形反馈。", "阻力倍数、沉降速度及临界流速为待标定参数；默认值仅用于演示。"]
    for i, (g, count) in enumerate(zip(c.gabions, f['counts'])):
        if count == 0:
            warnings.append(f"石笼 {i+1} 未覆盖任何单元，请加密网格或增大宽度。")
        elif g.width < 2 * max(c.length/c.nx, c.width/c.ny):
            warnings.append(f"石笼 {i+1} 横向不足约两格；位置与角度效应可能受网格影响。")
    if max(frames[-1]['bed_mm']) / 1000 > 0.1 * c.depth:
        warnings.append("最大沉积厚度超过水深的 10%，固定地形假设已明显不适用。")
    scale = max(initial_mass + mass_in, 1e-15)
    summary = dict(**final, initial_suspended_kg=initial_mass, relative_mass_error=abs(final['balance_error_kg']) / scale, discharge_m3_s=float(inflow.sum()), max_speed_m_s=float(f['speed'].max()), max_bed_mm=max(frames[-1]['bed_mm']), deposition_fraction=final['deposited_kg'] / scale, nodes=len(capacity), elements=len(f['triangles']), steps=steps, actual_dt=dt, head_drop_m=float(f['head'].max()), water_balance_m3_s=float(f['boundary'].sum()), interior_water_residual=float(np.max(np.abs((f['K'] @ f['head'])[f['free']]))))
    return dict(config=asdict(c), points=f['points'].tolist(), triangles=f['triangles'].tolist(), velocity=f['velocity'].tolist(), speed=f['speed'].tolist(), head=f['head'].tolist(), resistance=f['resistance'].tolist(), frames=frames, history=history, summary=summary, warnings=warnings)
