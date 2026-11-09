"""Command line: python -m sedimentcfd {run,serve,check,config,dxf,video}"""
import argparse
import json
from pathlib import Path
import sys

from .config import Config

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / 'outputs' / 'preview'


def main(argv=None):
    parser = argparse.ArgumentParser(prog='sedimentcfd', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run', help='run Thetis and write index.html + preview_data.json')
    run.add_argument('--config', type=Path, help='JSON file with any Config fields (default: built-in scenario)')
    run.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    serve = commands.add_parser('serve', help='serve outputs/preview and accept solve requests from the page')
    serve.add_argument('--port', type=int, default=8767)
    commands.add_parser('check', help='report whether Firedrake/Thetis are available')
    commands.add_parser('config', help='print the default config as JSON')
    dxf = commands.add_parser('dxf', help='print a config whose gabions are the rectangles of a DXF drawing (metres)')
    dxf.add_argument('drawing', type=Path)
    dxf.add_argument('--inflow', choices=('left', 'right'), default='left', help='side where the water enters')
    dxf.add_argument('--cell', type=float, default=1.0, help='target mesh cell size, m')
    dxf.add_argument('--bank-margin', type=float, default=10.0, help='m between gabions and each bank')
    dxf.add_argument('--upstream', type=float, default=20.0, help='m between inflow and gabions')
    dxf.add_argument('--downstream', type=float, default=30.0, help='m between gabions and outlet')
    dxf.add_argument('--resistance', type=float, default=10.0, help='gabion drag multiplier')
    video = commands.add_parser('video', help='render a finished run (preview_data.json) to PNG frames + MP4 via FFmpeg')
    video.add_argument('run', type=Path, help='run folder holding preview_data.json, e.g. outputs/cage')
    video.add_argument('--field', choices=('concentration', 'bed', 'speed', 'depth'), default='concentration')
    video.add_argument('--size', default='2560x1440', help='WIDTHxHEIGHT in pixels (default 2K)')
    video.add_argument('--fps', type=int, default=60)
    video.add_argument('--seconds', type=float, default=20.0, help='video length; the whole run is spread over it')
    video.add_argument('--particles', type=int, default=2400, help='tracer count, 0 for none')
    video.add_argument('--crf', type=int, default=16, help='x264 quality (lower = better, larger)')
    video.add_argument('--output', type=Path, help='MP4 path (default: <run>/<field>_<size>_<fps>fps.mp4)')
    args = parser.parse_args(argv)

    if args.command == 'config':
        print(json.dumps(Config().to_dict(), indent=2))
    elif args.command == 'dxf':
        from .dxf import config_from_dxf
        config = config_from_dxf(args.drawing, inflow=args.inflow, cell=args.cell, bank_margin=args.bank_margin,
                                 upstream=args.upstream, downstream=args.downstream, resistance=args.resistance)
        print(json.dumps(config.to_dict(), indent=2))
    elif args.command == 'check':
        from .model import check
        report = check()
        print(json.dumps(report, indent=2))
        return 0 if report['ready'] else 1
    elif args.command == 'serve':
        from .server import serve
        serve(args.port)
    elif args.command == 'video':
        from .video import make_video
        size = tuple(int(v) for v in args.size.lower().split('x'))
        path = make_video(args.run, args.field, size, args.fps, args.seconds, args.particles, args.output, args.crf)
        print('VIDEO_READY', path, flush=True)
    elif args.command == 'run':
        from .preview import build_preview
        config = Config.from_dict(json.loads(args.config.read_text())) if args.config else Config()
        build_preview(config, args.output)
        print('PREVIEW_READY', args.output / 'index.html', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
