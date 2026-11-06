#!/usr/bin/env python3
"""Native ARM macOS setup for the checked-out Thetis main branch.
Official procedure: https://www.firedrakeproject.org/firedrake/install.html#developer-install
Run in the user's normal terminal: python3 scripts/install_thetis.py
No sudo, no shell-profile changes, no global pip installs or cache purge.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / '.setup' / 'thetis'
VENV = ROOT / '.venv-firedrake'
FIREDRAKE_COMMIT = '2093d46b499ae31e816b4e651f68e3189f4eeb1b'


def configure_python_build_env(env, petsc, arch, brew_prefix):
    """Match upstream HDF5/MPI settings while preserving our Python 3.13 PATH."""
    env.update(PETSC_DIR=str(petsc), PETSC_ARCH=arch, HDF5_MPI='ON',
               HDF5_DIR=str(petsc / arch), CC=str(brew_prefix / 'bin/mpicc'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume-python', action='store_true',
                        help='Reuse built PETSc/HDF5; skip Homebrew and PETSc compilation')
    parser.add_argument('--resume-verify', action='store_true', help='Reuse installed Firedrake; resume from official checks')
    args = parser.parse_args()
    if args.resume_verify:
        args.resume_python = True
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('This installer targets ARM macOS only.')
    if not (ROOT/'external/thetis/pyproject.toml').is_file():
        raise RuntimeError('Thetis checkout is missing: external/thetis')
    BUILD.mkdir(parents=True, exist_ok=True)
    log_path = BUILD / ('install-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '.log')
    print(f'Log: {log_path}', flush=True)
    env = os.environ.copy()
    env['HOMEBREW_NO_AUTO_UPDATE'] = '1'
    env['PIP_NO_CACHE_DIR'] = '1'
    env['MAKEFLAGS'] = '-j2'  # conservative default for an 8 GB Mac
    env['HWLOC_COMPONENTS'] = '-gl'
    env['OMP_NUM_THREADS'] = '1'
    env['XDG_CACHE_HOME'] = str(BUILD/'cache-compatible')
    env['FIREDRAKE_CACHE_DIR'] = str(BUILD/'cache-compatible')
    env['MPLCONFIGDIR'] = str(BUILD/'cache/matplotlib')
    with log_path.open('w') as log:
        def run(args, cwd=BUILD, capture=False):
            args = [str(a) for a in args]
            heading = '\n$ ' + shlex.join(args) + '\n'
            print(heading, end='', flush=True)
            log.write(heading)
            log.flush()
            process = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            lines = []
            for line in process.stdout:
                log.write(line)
                log.flush()
                if capture:
                    lines.append(line)
                else:
                    print(line, end='', flush=True)
            if process.wait():
                if capture:
                    print(''.join(lines), flush=True)
                raise RuntimeError(f'Command failed ({process.returncode}); see {log_path}')
            return ''.join(lines).strip()

        run(['xcode-select', '-p'])
        brew_prefix = Path(run(['brew', '--prefix'], capture=True))
        env['PATH'] = str(brew_prefix/'bin') + ':' + env.get('PATH', '')
        firedrake = BUILD/'firedrake'
        petsc = BUILD/'petsc'
        for path, url in [(firedrake, 'https://github.com/firedrakeproject/firedrake.git'),
                          (petsc, 'https://gitlab.com/petsc/petsc.git')]:
            if not (path/'.git').is_dir():
                run(['git', 'clone', '--depth', '1', '--branch', 'main', url, path])
            elif path != firedrake and run(['git', '-C', path, 'branch', '--show-current'], capture=True) != 'main':
                raise RuntimeError(f'{path} is not on main; refusing to overwrite existing checkout')
        if run(['git', '-C', firedrake, 'rev-parse', 'HEAD'], capture=True) != FIREDRAKE_COMMIT:
            if run(['git', '-C', firedrake, 'status', '--porcelain'], capture=True):
                raise RuntimeError('Firedrake checkout has local changes; refusing to overwrite')
            run(['git', '-C', firedrake, 'fetch', '--depth', '1', 'origin', FIREDRAKE_COMMIT])
            run(['git', '-C', firedrake, 'checkout', '--detach', FIREDRAKE_COMMIT])
        constraints = BUILD/'constraints.txt'
        constraints.write_text('loopy==2025.2\npetsctools==2026.1\nislpy==2026.1\n')
        env['PIP_CONSTRAINT'] = str(constraints)
        # Use configure from exactly the downloaded Firedrake source revision.
        helper = firedrake/'scripts/firedrake-configure'
        bootstrap = sys.executable
        packages = shlex.split(run([bootstrap, helper, '--show-system-packages'], capture=True))
        # Thetis requires VTK; use Python 3.13 to avoid potential 3.14 VTK gaps.
        packages = [p for p in packages if not p.startswith('python@') and p != 'python']
        packages.append('python@3.13')
        if any(not p or p.startswith('-') for p in packages):
            raise RuntimeError('Unexpected package names from firedrake-configure')
        if not args.resume_python:
            run(['brew', 'install', *packages])
        python = brew_prefix/'opt/python@3.13/bin/python3.13'
        if not python.is_file():
            raise RuntimeError(f'Python 3.13 missing: {python}')
        if not VENV.exists():
            run([python, '-m', 'venv', VENV])
        vpython = VENV/'bin/python'
        if run([vpython, '-c', 'import sys; print("%d.%d" % sys.version_info[:2])'], capture=True) != '3.13':
            raise RuntimeError('Existing .venv-firedrake is not Python 3.13; refusing to replace it')
        env['VIRTUAL_ENV'] = str(VENV)
        env['PATH'] = str(VENV/'bin') + ':' + env['PATH']
        config = shlex.split(run([vpython, helper, '--show-petsc-configure-options'], capture=True))
        arch = next((x.split('=', 1)[1] for x in config if x.startswith('PETSC_ARCH=')), None)
        if not arch or '/' in arch:
            raise RuntimeError('Cannot determine PETSC_ARCH from configure output')
        configure_python_build_env(env, petsc, arch, brew_prefix)
        if not args.resume_python:
            run([vpython, petsc/'configure', *config], cwd=petsc)
            run(['make', '-j2', f'PETSC_DIR={petsc}', f'PETSC_ARCH={arch}', 'all'], cwd=petsc)
            run(['make', f'PETSC_DIR={petsc}', f'PETSC_ARCH={arch}', 'check'], cwd=petsc)
        for required in [petsc/arch/'lib/libhdf5.dylib', petsc/arch/'lib/libpetsc.dylib',
                         petsc/arch/'include/hdf5.h', Path(env['CC'])]:
            if not required.is_file():
                raise RuntimeError(f'Missing build dependency {required}; run without --resume-python')
        run([vpython, '-c', 'import ctypes, os; ctypes.CDLL(os.path.join(os.environ["HDF5_DIR"], "lib/libhdf5.dylib")); print("HDF5 library loads successfully")'])
        if not args.resume_verify:
            run([vpython, '-m', 'pip', 'install', '--upgrade', 'pip'])
            run([vpython, '-m', 'pip', 'install', petsc/'src/binding/petsc4py'])
            run([vpython, '-m', 'pip', 'install', '-r', firedrake/'requirements-build.txt'])
            run([vpython, '-m', 'pip', 'install', '--no-build-isolation', '--no-binary', 'h5py', '-e', str(firedrake)+'[check]'])
        run([vpython, '-m', 'pip', 'check'])
        run([VENV/'bin/firedrake-check', '--mpiexec', 'mpiexec --map-by slot --bind-to none --oversubscribe -n'])
        run([vpython, '-m', 'pip', 'install', '-e', ROOT/'external/thetis', '-e', ROOT])
        run([vpython, '-c', 'import firedrake, petsc4py, mpi4py, thetis; print("All solver imports passed")'], cwd=ROOT)
        run([vpython, '-m', 'sedimentcfd', 'check'], cwd=ROOT)
        run([vpython, '-m', 'unittest', '-v', 'tests.test_thetis'], cwd=ROOT)
        activate = ROOT/'activate_thetis.sh'
        lines = ['# Generated by scripts/install_thetis.py; source this file.']
        for key in ('PETSC_DIR', 'PETSC_ARCH', 'HDF5_MPI', 'HDF5_DIR', 'CC', 'HWLOC_COMPONENTS', 'XDG_CACHE_HOME', 'FIREDRAKE_CACHE_DIR', 'MPLCONFIGDIR', 'OMP_NUM_THREADS'):
            lines.append(f'export {key}={shlex.quote(env[key])}')
        lines.append('source ' + shlex.quote(str(VENV/'bin/activate')))
        activate.write_text('\n'.join(lines)+'\n')
        manifest = {name: run(['git', '-C', path, 'rev-parse', 'HEAD'], capture=True) for name, path in [('firedrake', firedrake), ('petsc', petsc), ('thetis', ROOT/'external/thetis')]}
        manifest['status'] = 'official_firedrake_checks_and_coupled_smoke_passed'
        (BUILD/'installed.json').write_text(json.dumps(manifest, indent=2))
        (BUILD/'pip-freeze.txt').write_text(run([vpython, '-m', 'pip', 'freeze'], capture=True)+'\n')
        print(f'Installation passed. Activate with: source {activate}', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError) as exc:
        print(f'INSTALLATION INCOMPLETE: {exc}', file=sys.stderr)
        sys.exit(1)
