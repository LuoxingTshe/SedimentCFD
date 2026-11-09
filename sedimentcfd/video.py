"""Render a finished run (preview_data.json) to a PNG sequence, then to MP4 with FFmpeg.

Offline counterpart of web/preview.html: the same colour scales, thin solid gabion outlines and
tracer particles. Fields are linearly interpolated between the saved snapshots, as on the page.
Unlike the page, tracers advance in simulation time in step with the field, so their on-screen
speed agrees with the time label.
"""
import io
import json
import logging
import math
import shutil
import subprocess
import tempfile
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

# Keep in step with the colour scale in web/preview.html (all ramps start from black).
RED, GREEN, ORANGE, BLUE = (255, 44, 44), (170, 235, 172), (255, 140, 0), (40, 120, 255)
FIELDS = {  # key: (title, colour-bar label, unit, colour of the high end)
    'concentration': ('Suspended sediment concentration', 'Concentration', 'kg/m³', RED),
    'bed': ('Bed level change', 'Bed level change', 'mm', ORANGE),
    'speed': ('Depth-averaged flow speed', 'Speed', 'm/s', GREEN),
    'depth': ('Water depth', 'Depth', 'm', GREEN),
}
TRAIL_FADE = 0.07  # per frame, as the page fades its particle canvas at 60 Hz
TRACER = np.array([205, 246, 255], np.float32)
TEXT, MUTED, ACCENT, GRID = (232, 237, 233), (165, 182, 184), (159, 221, 199), (110, 120, 122)

HELVETICA = '/System/Library/Fonts/Helvetica.ttc'  # faces: 0 regular, 1 bold, 2 oblique
FALLBACK = ['/System/Library/Fonts/Supplemental/Arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
REGULAR, BOLD = 0, 1


def _font(size, face=REGULAR):
    """Helvetica (all panel and axis text); Arial / DejaVu Sans where it is missing."""
    from PIL import ImageFont
    size = max(int(size), 8)  # small test renders would otherwise ask for 0 px text
    if Path(HELVETICA).exists():
        return ImageFont.truetype(HELVETICA, size, index=face)
    for p in FALLBACK:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


_MATH_RC = None


def _math_rc():
    """matplotlib rcParams that typeset mathtext in Helvetica.

    matplotlib only reads the first face of a .ttc, so the regular, bold and oblique faces are
    split into separate files first. Glyphs Helvetica lacks fall back to STIX Sans.
    """
    global _MATH_RC
    if _MATH_RC is None:
        _MATH_RC = {'mathtext.fontset': 'dejavusans'}
        if Path(HELVETICA).exists():
            try:
                from fontTools.ttLib import TTCollection
                from matplotlib import font_manager
                logging.getLogger('fontTools').setLevel(logging.ERROR)
                logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)
                folder = Path(tempfile.gettempdir()) / 'sedimentcfd-fonts'
                folder.mkdir(exist_ok=True)
                for i, face in enumerate(TTCollection(HELVETICA).fonts[:3]):
                    path = folder / f'helvetica-{i}.ttf'
                    if not path.exists():
                        face.save(path)
                    font_manager.fontManager.addfont(str(path))
                _MATH_RC = {'font.family': 'Helvetica', 'mathtext.fontset': 'custom',
                            'mathtext.rm': 'Helvetica', 'mathtext.sf': 'Helvetica',
                            'mathtext.it': 'Helvetica:italic', 'mathtext.bf': 'Helvetica:bold',
                            'mathtext.fallback': 'stixsans'}
            except Exception:  # fontTools/matplotlib missing or font unreadable: keep DejaVu
                pass
    return _MATH_RC


def _math(text, px, colour):
    """Text with $...$ mathtext as an RGBA image in `colour` on transparency."""
    import matplotlib
    from matplotlib import mathtext
    from matplotlib.font_manager import FontProperties
    from PIL import Image
    buf = io.BytesIO()
    with matplotlib.rc_context(_math_rc()):
        mathtext.math_to_image(text, buf, prop=FontProperties(size=max(px, 12)), dpi=72, format='png')
    buf.seek(0)
    alpha = Image.eval(Image.open(buf).convert('L'), lambda v: 255 - v)  # black-on-white -> coverage
    img = Image.new('RGBA', alpha.size, tuple(colour) + (255,))
    img.putalpha(alpha)
    return img


def _tick(span):  # same rule as the page's axis ticks
    return next((s for s in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500) if span / s <= 10), span / 5)


def _nice_ticks(lo, hi):
    raw = (hi - lo) / 4
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    ticks = np.arange(math.ceil(lo / step - 1e-9), math.floor(hi / step + 1e-9) + 1) * step
    return [0.0 if abs(t) < 1e-12 * step else float(t) for t in ticks]


class Layout:
    """Plot box (equal x/y scale) on the left, legend/time panel on the right."""

    def __init__(self, cfg, width, height):
        self.width, self.height = width, height
        self.s = s = height / 1440
        L, B = cfg['length'], cfg['width']
        scale = min(0.6 * width / L, (height - 220 * s) / B)
        self.pw, self.ph = round(L * scale), round(B * scale)
        self.left = round(150 * s)
        self.top = round((height - 40 * s - self.ph) / 2)
        self.sx, self.sy = self.pw / L, self.ph / B
        self.panel = self.left + self.pw + round(110 * s)

    def px(self, x, y):
        """Local metres -> pixel coordinates inside the plot box (y down)."""
        return x * self.sx, self.ph - y * self.sy


def triangle_map(points, triangles, lay):
    """Index of the mesh triangle under each plot pixel centre."""
    px, py = lay.px(points[:, 0], points[:, 1])
    idx = np.full((lay.ph, lay.pw), -1, np.int32)
    for i, (a, b, c) in enumerate(triangles):
        ax, ay, bx, by, cx, cy = px[a], py[a], px[b], py[b], px[c], py[c]
        x0, x1 = max(math.floor(min(ax, bx, cx)), 0), min(math.ceil(max(ax, bx, cx)), lay.pw)
        y0, y1 = max(math.floor(min(ay, by, cy)), 0), min(math.ceil(max(ay, by, cy)), lay.ph)
        if x0 >= x1 or y0 >= y1:
            continue
        X, Y = np.meshgrid(np.arange(x0, x1) + .5, np.arange(y0, y1) + .5)
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        u = ((by - cy) * (X - cx) + (cx - bx) * (Y - cy)) / den
        v = ((cy - ay) * (X - cx) + (ax - cx) * (Y - cy)) / den
        idx[y0:y1, x0:x1][(u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9)] = i
    for _ in range(4):  # pixels missed along the outer edge borrow a neighbour
        miss = idx < 0
        if not miss.any():
            break
        for shifted in (np.roll(idx, 1, 0), np.roll(idx, -1, 0), np.roll(idx, 1, 1), np.roll(idx, -1, 1)):
            fill = (idx < 0) & (shifted >= 0)
            idx[fill] = shifted[fill]
    return idx


def value_range(values, key):
    lo, hi = float(values.min()), float(values.max())
    if key == 'bed':  # keep 0 inside the range
        lo, hi = min(lo, -1e-9), max(hi, 1e-9)
    return lo, max(hi, lo + 1e-9)


def colourise(v, key, lo, hi):
    if key == 'bed':  # deposition / erosion scaled separately
        pos = np.clip(v / hi, 0, 1)[:, None] * np.array(ORANGE, np.float32)
        neg = np.clip(v / lo, 0, 1)[:, None] * np.array(BLUE, np.float32)
        rgb = np.where(v[:, None] >= 0, pos, neg)
    else:
        rgb = np.clip((v - lo) / (hi - lo), 0, 1)[:, None] * np.array(FIELDS[key][3], np.float32)
    return np.rint(rgb).astype(np.uint8)


def interp(times, series, t):
    i1 = int(np.clip(np.searchsorted(times, t), 1, len(times) - 1))
    w = (t - times[i1 - 1]) / (times[i1] - times[i1 - 1])
    return series[i1 - 1] * (1 - w) + series[i1] * w


class Tracers:
    """Massless tracers advected by the cell-mean velocity (midpoint RK2), as on the page."""

    def __init__(self, cfg, idx, lay, count, rng):
        self.L, self.B = cfg['length'], cfg['width']
        self.right = cfg.get('inflow') == 'right'
        self.max_age = max(75, 1.2 * self.L / cfg['velocity'])  # long enough to cross the reach
        self.idx, self.lay, self.rng = idx, lay, rng
        self.x = rng.random(count) * self.L
        self.y = self.B * (.001 + .998 * rng.random(count))
        self.age = rng.random(count) * 30
        self.trail = np.zeros(idx.shape, np.float32)

    def locate(self, x, y):
        px, py = self.lay.px(x, y)
        i, j = np.floor(py).astype(int), np.floor(px).astype(int)
        ok = (x >= 0) & (x <= self.L) & (y >= 0) & (y <= self.B)
        ok &= (i >= 0) & (i < self.lay.ph) & (j >= 0) & (j < self.lay.pw)
        out = np.full(x.shape, -1)
        out[ok] = self.idx[i[ok], j[ok]]
        return out

    def respawn(self, mask):
        n = int(mask.sum())
        r = .01 * self.L * self.rng.random(n)  # new tracers enter at the inflow
        self.x[mask] = self.L - r if self.right else r
        self.y[mask] = self.B * (.001 + .998 * self.rng.random(n))
        self.age[mask] = self.rng.random(n) * 30

    def step(self, uv, dt):
        i = self.locate(self.x, self.y)
        alive = i >= 0
        v = np.zeros((len(self.x), 2))
        v[alive] = uv[i[alive]]
        j = self.locate(self.x + .5 * dt * v[:, 0], self.y + .5 * dt * v[:, 1])
        mid = alive & (j >= 0)
        v[mid] = uv[j[mid]]
        x0, y0 = self.x.copy(), self.y.copy()
        self.x += dt * v[:, 0]
        self.y += dt * v[:, 1]
        self.age += dt
        alive &= (self.x >= 0) & (self.x <= self.L) & (self.y >= 0) & (self.y <= self.B) & (self.age < self.max_age)
        self.trail *= 1 - TRAIL_FADE
        self._draw(x0[alive], y0[alive], self.x[alive], self.y[alive])
        self.respawn(~alive)

    def _draw(self, x0, y0, x1, y1):
        if not len(x0):
            return
        ax, ay = self.lay.px(x0, y0)
        bx, by = self.lay.px(x1, y1)
        n = int(min(np.ceil(2 * np.hypot(bx - ax, by - ay).max()), 64)) + 2
        t = np.linspace(0, 1, n)[:, None]
        self._put((ax + (bx - ax) * t).ravel(), (ay + (by - ay) * t).ravel(), .72, 1)
        self._put(bx, by, 1.0, 2)  # bright head, ~1.5 px like the page

    def _put(self, x, y, value, size):
        i0 = np.floor(y - (size - 1) / 2).astype(int)
        j0 = np.floor(x - (size - 1) / 2).astype(int)
        for di in range(size):
            for dj in range(size):
                i, j = i0 + di, j0 + dj
                ok = (i >= 0) & (i < self.lay.ph) & (j >= 0) & (j < self.lay.pw)
                np.maximum.at(self.trail, (i[ok], j[ok]), np.float32(value))


def _fmt(v, ref):
    return '0' if abs(v) < 1e-6 * ref else f'{v:.4g}'


def static_overlay(cfg, lay, key, lo, hi, tracers, n_triangles):
    """Axes, gabions, colour bar, model summary and notes; drawn at 2x and box-filtered for anti-aliasing.

    Vertical offsets in the panel are in 1440p pixels from the top of the plot; `panel_text` in
    render_frames fills the gap left for the time read-out (offsets 150-270).
    """
    from PIL import Image, ImageDraw
    k = 2
    s = lay.s * k
    img = Image.new('RGBA', (lay.width * k, lay.height * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = lambda px, face=REGULAR: _font(round(px * s), face)
    L0, T0 = lay.left * k, lay.top * k
    X = lambda x: L0 + x * lay.sx * k
    Y = lambda y: T0 + (lay.ph - y * lay.sy) * k
    OX, OY = cfg.get('origin_x', 0) or 0, cfg.get('origin_y', 0) or 0
    thin = max(1, round(1 * s))

    # axes in drawing (CAD) coordinates
    d.rectangle([L0, T0, X(cfg['length']), Y(0)], outline=GRID + (255,), width=thin)
    sx, sy = _tick(cfg['length']), _tick(cfg['width'])
    for x in np.arange(math.ceil(OX / sx) * sx, OX + cfg['length'] + 1e-9, sx):
        d.line([X(x - OX), Y(0), X(x - OX), Y(0) + 8 * s], fill=GRID, width=thin)
        d.text((X(x - OX), Y(0) + 14 * s), f'{x:g}', font=f(22), fill=MUTED, anchor='mt')
    for y in np.arange(math.ceil(OY / sy) * sy, OY + cfg['width'] + 1e-9, sy):
        d.line([L0 - 8 * s, Y(y - OY), L0, Y(y - OY)], fill=GRID, width=thin)
        d.text((L0 - 14 * s, Y(y - OY)), f'{y:g}', font=f(22), fill=MUTED, anchor='rm')
    d.text((X(cfg['length'] / 2), Y(0) + 50 * s), 'x (m)', font=f(22), fill=MUTED, anchor='mt')
    d.text((L0 - 14 * s, T0 - 18 * s), 'y (m)', font=f(22), fill=MUTED, anchor='rb')

    # gabions: thin solid white outline
    for g in cfg['gabions']:
        a = math.radians(g['angle'])
        pts = []
        for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)):
            u, v = su * g['length'] / 2, sv * g['width'] / 2
            pts.append((X(g['x'] - OX + u * math.cos(a) - v * math.sin(a)),
                        Y(g['y'] - OY + u * math.sin(a) + v * math.cos(a))))
        d.line(pts, fill=(255, 255, 255, 255), width=max(1, round(1.5 * s)), joint='curve')

    P = lay.panel * k
    PW = (lay.width - lay.panel - round(80 * lay.s)) * k  # panel width
    at = lambda off: T0 + off * s

    def math_line(text, off, px, colour):
        img.alpha_composite(_math(text, round(px * s), colour), (int(P), int(at(off))))

    # heading
    title, bar_label, unit, _ = FIELDS[key]
    d.text((P, at(0)), 'Flow Around Gabions', font=f(46, BOLD), fill=TEXT, anchor='lt')
    d.text((P, at(62)), title, font=f(28), fill=TEXT, anchor='lt')
    d.text((P, at(104)), 'Solver output \u00b7 not calibrated against field data \u00b7 qualitative only',
           font=f(21), fill=ACCENT, anchor='lt')

    # horizontal colour bar
    d.text((P, at(330)), f'{bar_label} ({unit})', font=f(24), fill=TEXT, anchor='ls')
    top, height, cols = at(345), int(30 * s), int(PW)
    vals = lo + (hi - lo) * (np.arange(cols) + .5) / cols
    img.paste(Image.fromarray(np.repeat(colourise(vals, key, lo, hi)[None], height, axis=0)).convert('RGBA'),
              (int(P), int(top)))
    d.rectangle([P, top, P + cols, top + height], outline=GRID + (255,), width=thin)
    ref = max(abs(lo), abs(hi))
    for t in _nice_ticks(lo, hi):
        xx = P + (t - lo) / (hi - lo) * cols
        d.line([xx, top + height, xx, top + height + 8 * s], fill=MUTED, width=thin)
        d.text((xx, top + height + 14 * s), _fmt(t, ref), font=f(20), fill=TEXT,
               anchor='lt' if xx - P < 20 * s else 'rt' if P + cols - xx < 20 * s else 'mt')
    caption = ('Orange deposition \u00b7 black no change \u00b7 blue erosion' if key == 'bed'
               else f'Colour range {_fmt(lo, ref)} to {_fmt(hi, ref)} {unit}: minimum to maximum over the run')
    d.text((P, at(428)), caption, font=f(19), fill=MUTED, anchor='lt')

    # model summary: framework and governing equations, as configured in model.py
    d.line([P, at(472), P + PW, at(472)], fill=GRID, width=thin)
    d.text((P, at(492)), 'Model: Thetis 2D on Firedrake (finite elements)', font=f(25, BOLD), fill=TEXT, anchor='lt')
    d.text((P, at(530)), f'Depth-averaged \u00b7 DG P1\u2013P1 \u00b7 implicit Euler \u00b7 \u0394t = {cfg["timestep"]:g} s',
           font=f(20), fill=MUTED, anchor='lt')
    d.text((P, at(558)), f'Mesh {cfg["nx"]} \u00d7 {cfg["ny"]} cells, {n_triangles:,} triangles \u00b7 '
           f'hydrodynamic spin-up {cfg["spinup"]:g} s', font=f(20), fill=MUTED, anchor='lt')
    eq, label = 27, 21
    d.text((P, at(602)), 'Shallow-water equations', font=f(label), fill=ACCENT, anchor='lt')
    math_line(r'$\partial_t \eta + \nabla\cdot(H\mathbf{u}) = 0$', 634, eq, TEXT)
    math_line(r'$\partial_t \mathbf{u} + \mathbf{u}\cdot\nabla\mathbf{u} + g\nabla\eta = '
              r'\nabla\cdot(\nu_h\nabla\mathbf{u}) - C_D|\mathbf{u}|\mathbf{u}/H - K_g\mathbf{u}$', 676, eq, TEXT)
    math_line(r'$C_D = 2\kappa^2 / \ln^2(11.036\,H/k_s),\quad k_s = 3d_{50}$', 718, eq, TEXT)
    math_line(r'Suspended sediment, conservative form $q = HC/\rho_s$', 774, label, ACCENT)
    math_line(r'$\partial_t q + \nabla\cdot(\mathbf{u}q) = \nabla\cdot(\varepsilon_s\nabla q) + E - D$', 806, eq, TEXT)
    d.text((P, at(864)), 'Exner bed update (no bedload)', font=f(label), fill=ACCENT, anchor='lt')
    math_line(r'$(1-p)\,\partial_t z_b = D - E$', 896, eq, TEXT)
    drags = sorted({round(cfg['drag'] * (g['resistance'] - 1), 4) for g in cfg['gabions']})
    notes = [
        rf'$\eta$ surface elevation, $H$ depth, $\mathbf{{u}}$ velocity, $\nu_h$ = {cfg["viscosity"]:g} m²/s',
        rf'$K_g$ = {" / ".join(f"{v:g}" for v in drags) or "0"} s$^{{-1}}$ inside gabions, 0 elsewhere',
        rf'$E$, $D$: van Rijn erosion, Rouse-profile deposition, $d_{{50}}$ = {cfg["grain_size"] * 1000:g} mm',
        rf'$\varepsilon_s$ = {cfg["diffusivity"]:g} m²/s, $p$ = {cfg["bed_porosity"]:g}, '
        rf'$\rho_s$ = {cfg["sediment_density"]:g} kg/m³',
    ]
    for n, line in enumerate(notes):
        math_line(line, 956 + 30 * n, 19, MUTED)

    # legend notes, bottom-aligned with the plot
    notes = [f"Inflow enters at the {cfg.get('inflow', 'left')} boundary",  # Helvetica has no arrow glyphs
             'Thin white outline: gabion drag zone']
    if tracers:
        notes.append('Dots and trails: tracers carried by the flow, not sediment grains')
    for n, line in enumerate(reversed(notes)):
        d.text((P, Y(0) - n * 32 * s), line, font=f(20), fill=MUTED, anchor='lb')
    return img.resize((lay.width, lay.height), Image.BOX)


def render_frames(run, frames_dir, field='concentration', size=(2560, 1440), fps=60, seconds=20.0,
                  particles=2400, seed=0, log=print):
    """Write frames_dir/frame_00000.png ... spanning the whole run; returns the frame count."""
    from PIL import Image, ImageDraw
    if size[0] % 2 or size[1] % 2:
        raise ValueError('frame width and height must be even for yuv420p')
    data = json.loads((Path(run) / 'preview_data.json').read_text())
    cfg = data['config']
    pts = np.array(data['points'], float) - [cfg.get('origin_x', 0) or 0, cfg.get('origin_y', 0) or 0]
    tris = np.array(data['triangles'])
    snaps = data['frames']
    times = np.array([f['time'] for f in snaps])
    uv = np.array([f['uv'] for f in snaps])
    values = np.hypot(uv[..., 0], uv[..., 1]) if field == 'speed' else np.array([f[field] for f in snaps])
    suspended = np.array([e['suspended_kg'] for e in data['inventory']])
    lo, hi = value_range(values, field)

    lay = Layout(cfg, *size)
    idx = triangle_map(pts, tris, lay)
    lookup = np.where(idx < 0, len(tris), idx)  # extra black row for any uncovered pixel
    tracers = Tracers(cfg, idx, lay, particles, np.random.default_rng(seed)) if particles else None
    overlay = static_overlay(cfg, lay, field, lo, hi, tracers is not None, len(tris))

    n = max(round(seconds * fps), 2)
    t_sim = np.linspace(times[0], times[-1], n)
    dt = t_sim[1] - t_sim[0]
    if tracers:  # build trails before the first frame
        for _ in range(round(1.5 * fps)):
            tracers.step(uv[0], dt)

    s = lay.s
    clock, small = _font(round(60 * s)), _font(round(21 * s))
    P, T0 = lay.panel, lay.top
    bar_w = lay.width - P - round(80 * s)
    frames_dir = Path(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob('frame_*.png'):
        old.unlink()

    pending = deque()
    with ThreadPoolExecutor(max_workers=6) as pool:
        for k, t in enumerate(t_sim):
            if tracers and k:
                tracers.step(interp(times, uv, t), dt)
            rgb = np.vstack([colourise(interp(times, values, t), field, lo, hi), [[0, 0, 0]]])
            plot = rgb[lookup]
            if tracers:
                a = np.minimum(tracers.trail, 1)[..., None]
                plot = (plot * (1 - a) + TRACER * a + .5).astype(np.uint8)
            canvas = np.zeros((lay.height, lay.width, 3), np.uint8)
            canvas[lay.top:lay.top + lay.ph, lay.left:lay.left + lay.pw] = plot
            img = Image.fromarray(canvas).convert('RGBA')
            img.alpha_composite(overlay)
            img = img.convert('RGB')
            d = ImageDraw.Draw(img)
            d.text((P, T0 + 150 * s), f't = {t:5.1f} s', font=clock, fill=TEXT, anchor='lt')
            d.text((P, T0 + 225 * s), f'Coupled run {times[-1]:g} s \u00b7 suspended sediment {interp(times, suspended, t):,.1f} kg',
                   font=small, fill=MUTED, anchor='lt')
            y = T0 + 268 * s
            d.line([P, y, P + bar_w, y], fill=GRID, width=max(1, round(2 * s)))
            d.line([P, y, P + bar_w * (t - times[0]) / (times[-1] - times[0]), y], fill=ACCENT, width=max(1, round(4 * s)))
            pending.append(pool.submit(img.save, frames_dir / f'frame_{k:05d}.png', compress_level=1))
            while len(pending) > 12:
                pending.popleft().result()
            if k % fps == 0 or k == n - 1:
                log(f'frame {k + 1}/{n}  t = {t:.1f} s')
        for p in pending:
            p.result()
    return n


def encode(frames_dir, output, fps=60, crf=16):
    """H.264 / yuv420p / BT.709 MP4 from frames_dir/frame_%05d.png."""
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('FFmpeg not found on PATH (macOS: brew install ffmpeg)')
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-stats',
        '-framerate', str(fps), '-i', str(Path(frames_dir) / 'frame_%05d.png'),
        '-vf', 'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p',
        '-c:v', 'libx264', '-preset', 'slow', '-crf', str(crf), '-profile:v', 'high',
        '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv',
        '-movflags', '+faststart', str(output)], check=True)
    return Path(output)


def make_video(run, field='concentration', size=(2560, 1440), fps=60, seconds=20.0, particles=2400,
               output=None, crf=16, log=print):
    run = Path(run)
    frames_dir = run / f'frames_{field}'
    output = Path(output) if output else run / f'{field}_{size[0]}x{size[1]}_{fps}fps.mp4'
    n = render_frames(run, frames_dir, field, size, fps, seconds, particles, log=log)
    log(f'{n} frames in {frames_dir}; encoding {output}')
    return encode(frames_dir, output, fps, crf)
