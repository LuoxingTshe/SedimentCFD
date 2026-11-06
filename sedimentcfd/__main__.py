import argparse
from dataclasses import asdict
import csv
import json
from pathlib import Path
import numpy as np
from .engine import Config, simulate


def export(result, directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'result.json').write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    template = (Path(__file__).parent / 'web' / 'index.html').read_text(encoding='utf-8')
    payload = json.dumps(result, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c')
    preview = template.replace('<script>', '<script>window.SEDIMENTCFD_RESULT=' + payload + ';</script><script>', 1)
    (directory / 'preview.html').write_text(preview, encoding='utf-8')
    with (directory / 'history.csv').open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=result['history'][0].keys())
        writer.writeheader()
        writer.writerows(result['history'])
    p, v = np.array(result['points']), np.array(result['velocity'])
    last = result['frames'][-1]
    np.savetxt(directory / 'nodes.csv', np.column_stack((p, v, result['head'], last['concentration'], last['bed_mm'])), delimiter=',', header='x_m,y_m,u_m_s,v_m_s,head_m,concentration_kg_m3,bed_mm', comments='')
    np.savez_compressed(directory / 'fields.npz', points=p, triangles=result['triangles'], velocity=v, head=result['head'], concentration=last['concentration'], bed_mm=last['bed_mm'])


def main():
    parser = argparse.ArgumentParser(description='SedimentCFD — minimal planar FEM prototype')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run', help='Run a JSON configuration and export results')
    run.add_argument('--config', type=Path)
    run.add_argument('--output', type=Path, default=Path('outputs/demo'))
    serve = sub.add_parser('serve', help='Open local interactive model')
    serve.add_argument('--port', type=int, default=8765)
    thetis = sub.add_parser('thetis', help='Experimental upstream shallow-water + Exner backend')
    thetis.add_argument('--check', action='store_true', help='Report dependency presence without importing Firedrake')
    thetis.add_argument('--config', type=Path)
    thetis.add_argument('--output', type=Path, default=Path('outputs/thetis'))
    thetis.add_argument('--grain-size', type=float, default=0.00016)
    thetis.add_argument('--spinup', type=float, default=120)
    thetis.add_argument('--dt', type=float, default=0.25)
    thetis.add_argument('--bedload', action='store_true')
    sub.add_parser('config', help='Print default configuration')
    args = parser.parse_args()
    if args.command == 'thetis':
        from .thetis_backend import environment_report, run as run_thetis, ThetisSettings
        if args.check:
            print(json.dumps(environment_report(), indent=2))
            return
        config = Config.from_dict(json.loads(args.config.read_text())) if args.config else Config()
        try:
            run_thetis(config, args.output, ThetisSettings(grain_size=args.grain_size,
                spinup=args.spinup, timestep=args.dt, bedload=args.bedload))
        except (RuntimeError, ValueError) as error:
            parser.exit(2, str(error) + '\n')
    elif args.command == 'config':
        print(json.dumps(asdict(Config()), indent=2))
    elif args.command == 'serve':
        from .server import serve as start
        start(args.port)
    else:
        config = Config.from_dict(json.loads(args.config.read_text())) if args.config else Config()
        result = simulate(config)
        export(result, args.output)
        print(json.dumps(result['summary'], indent=2))
        print(f'Results: {args.output.resolve()}')


if __name__ == '__main__':
    main()
