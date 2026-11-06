"""Reproduce the README mesh sensitivity table from a source checkout."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sedimentcfd import Config, simulate

print('nx,ny,discharge_m3_s,deposited_kg,max_bed_mm,relative_mass_error')
for nx, ny in [(40, 16), (80, 32), (160, 64)]:
    s = simulate(Config(nx=nx, ny=ny))['summary']
    print(f"{nx},{ny},{s['discharge_m3_s']:.9f},{s['deposited_kg']:.9f},{s['max_bed_mm']:.9f},{s['relative_mass_error']:.3e}")
