"""Read rectangular gabions from a DXF drawing (units: metres).

Each gabion is a closed rectangle drawn either as four LINE entities or as one
closed LWPOLYLINE. Lines that do not form a rectangle with others (centre lines,
axis marks) are ignored; other closed polylines are rejected.
"""
import math
from pathlib import Path

from .config import Config, Gabion

TOLERANCE = 1e-6  # m, endpoints closer than this are the same vertex


def read_entities(path):
    """Return (lines, polylines) from the ENTITIES section: [((x1, y1), (x2, y2))], [[(x, y), ...]]."""
    raw = Path(path).read_text(errors='replace').splitlines()
    pairs = [(raw[i].strip(), raw[i + 1].strip()) for i in range(0, len(raw) - 1, 2)]
    lines, polylines = [], []
    section, i = None, 0
    while i < len(pairs):
        code, value = pairs[i]
        if code == '2' and i and pairs[i - 1] == ('0', 'SECTION'):
            section = value
        if code != '0' or section != 'ENTITIES' or value not in ('LINE', 'LWPOLYLINE'):
            i += 1
            continue
        kind, body = value, []
        i += 1
        while i < len(pairs) and pairs[i][0] != '0':
            body.append(pairs[i])
            i += 1
        if kind == 'LINE':
            d = dict(body)
            lines.append(((float(d['10']), float(d['20'])), (float(d['11']), float(d['21']))))
        else:
            xs = [float(v) for c, v in body if c == '10']
            ys = [float(v) for c, v in body if c == '20']
            closed = any(c == '70' and int(v) & 1 for c, v in body)
            points = list(zip(xs, ys))
            if closed or (len(points) > 2 and math.dist(points[0], points[-1]) < TOLERANCE):
                polylines.append(points[:-1] if math.dist(points[0], points[-1]) < TOLERANCE else points)
    return lines, polylines


def quadrilaterals(lines):
    """Find every 4-cycle of LINE segments (a-b-d-c), whatever else is attached to its corners."""
    key = lambda p: (round(p[0] / TOLERANCE), round(p[1] / TOLERANCE))
    neighbours, coords = {}, {}
    for a, b in lines:
        ka, kb = key(a), key(b)
        if ka == kb:
            continue
        coords[ka], coords[kb] = a, b
        neighbours.setdefault(ka, set()).add(kb)
        neighbours.setdefault(kb, set()).add(ka)
    found = {}
    for a, around in neighbours.items():
        around = sorted(around)
        for i, b in enumerate(around):
            for c in around[i + 1:]:
                for d in (neighbours[b] & neighbours[c]) - {a}:
                    found.setdefault(frozenset((a, b, c, d)), [coords[k] for k in (a, b, d, c)])
    return list(found.values())


def is_rectangle(points, angle_tolerance=1.0):
    try:
        rectangle_to_gabion(points, angle_tolerance=angle_tolerance)
        return True
    except ValueError:
        return False


def rectangle_to_gabion(points, resistance=10.0, angle_tolerance=1.0):
    """Convert four corner points (in order) to a Gabion; raise if not a rectangle."""
    if len(points) != 4:
        raise ValueError(f'expected a rectangle, got a closed loop with {len(points)} vertices')
    edges = [(points[(k + 1) % 4][0] - points[k][0], points[(k + 1) % 4][1] - points[k][1]) for k in range(4)]
    for k in range(4):
        a, b = edges[k], edges[(k + 1) % 4]
        cos = (a[0] * b[0] + a[1] * b[1]) / (math.hypot(*a) * math.hypot(*b))
        if abs(math.degrees(math.acos(max(-1, min(1, cos)))) - 90) > angle_tolerance:
            raise ValueError(f'closed loop at {points[0]} is not a rectangle')
    long_edge = max(edges[:2], key=lambda e: math.hypot(*e))
    angle = math.degrees(math.atan2(long_edge[1], long_edge[0])) % 180
    return Gabion(x=sum(p[0] for p in points) / 4, y=sum(p[1] for p in points) / 4,
                  length=max(math.hypot(*edges[0]), math.hypot(*edges[1])),
                  width=min(math.hypot(*edges[0]), math.hypot(*edges[1])),
                  angle=angle, resistance=resistance)


def read_gabions(path, resistance=10.0):
    lines, polylines = read_entities(path)
    loops = [q for q in quadrilaterals(lines) if is_rectangle(q)] + polylines  # polylines must be rectangles
    gabions = [rectangle_to_gabion(p, resistance) for p in loops]
    if not gabions:
        raise ValueError(f'no closed rectangles found in {path}')
    return sorted(gabions, key=lambda g: (g.x, g.y))


def config_from_dxf(path, inflow='left', cell=1.0, bank_margin=10.0, upstream=20.0,
                    downstream=30.0, resistance=10.0, **overrides):
    """Build a Config whose rectangular reach encloses the drawing's gabions.

    Margins (m) are added around the gabions' bounding box: `bank_margin` on both banks,
    `upstream` on the inflow side and `downstream` on the outlet side. The domain edges
    are rounded outwards to whole metres. CAD coordinates are kept via origin_x/origin_y.
    """
    gabions = read_gabions(path, resistance)
    xs, ys = [], []
    for g in gabions:
        a = math.radians(g.angle)
        for sx in (-1, 1):
            for sy in (-1, 1):
                u, v = sx * g.length / 2, sy * g.width / 2
                xs.append(g.x + u * math.cos(a) - v * math.sin(a))
                ys.append(g.y + u * math.sin(a) + v * math.cos(a))
    left_margin, right_margin = (upstream, downstream) if inflow == 'left' else (downstream, upstream)
    x0, x1 = math.floor(min(xs) - left_margin), math.ceil(max(xs) + right_margin)
    y0, y1 = math.floor(min(ys) - bank_margin), math.ceil(max(ys) + bank_margin)
    data = Config().to_dict()
    data.update(origin_x=x0, origin_y=y0, length=x1 - x0, width=y1 - y0, inflow=inflow,
                nx=max(2, round((x1 - x0) / cell)), ny=max(2, round((y1 - y0) / cell)),
                gabions=[vars(g) for g in gabions])
    data.update(overrides)
    return Config.from_dict(data)
