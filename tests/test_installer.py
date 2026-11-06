import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('installer', Path(__file__).resolve().parents[1]/'scripts/install_thetis.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class InstallerTests(unittest.TestCase):
    def test_hdf5_uses_petsc_build_and_mpi_compiler(self):
        env = {'PATH': '/project/.venv-firedrake/bin:/opt/homebrew/bin'}
        installer.configure_python_build_env(env, Path('/project/petsc'), 'arch-test', Path('/opt/homebrew'))
        self.assertEqual(env['HDF5_DIR'], '/project/petsc/arch-test')
        self.assertEqual(env['HDF5_MPI'], 'ON')
        self.assertEqual(env['CC'], '/opt/homebrew/bin/mpicc')
        self.assertTrue(env['PATH'].startswith('/project/.venv-firedrake/bin:'))

    def test_stale_hdf5_settings_replaced(self):
        env = {'HDF5_DIR':'/old/serial/hdf5','CC':'clang'}
        installer.configure_python_build_env(env, Path('/new/petsc'), 'arch-a', Path('/opt/homebrew'))
        self.assertEqual(env['HDF5_DIR'], '/new/petsc/arch-a')
        self.assertEqual(env['CC'], '/opt/homebrew/bin/mpicc')
