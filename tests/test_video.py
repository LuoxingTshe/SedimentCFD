import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    from PIL import Image
except ImportError:  # Pillow is only needed for the video export
    Image = None

from sedimentcfd import Config, video


def fake_run(folder):
    """2 x 1 m reach split into two triangles; concentration rises 0 -> 1 kg/m³ over 10 s."""
    config = Config().to_dict()  # a real run stores the full config
    config.update(length=2.0, width=1.0, origin_x=0, origin_y=0, inflow='left', velocity=0.1, nx=1, ny=1,
                  gabions=[dict(x=1.0, y=0.5, length=0.6, width=0.2, angle=0, resistance=10)])
    frames = [dict(time=t, uv=[[0.1, 0.0]] * 2, concentration=[t / 10] * 2, bed=[0.0, 0.0],
                   depth=[0.5, 0.5]) for t in (0.0, 5.0, 10.0)]
    data = dict(config=config, frames=frames, points=[[0, 0], [2, 0], [2, 1], [0, 1]],
                triangles=[[0, 1, 2], [0, 2, 3]],
                inventory=[dict(time=f['time'], suspended_kg=1.0) for f in frames])
    (folder / 'preview_data.json').write_text(json.dumps(data))


@unittest.skipIf(Image is None, 'Pillow not installed')
class VideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name)
        fake_run(self.run_dir)

    def test_frames_span_run_with_red_concentration(self):
        frames = self.run_dir / 'frames'
        n = video.render_frames(self.run_dir, frames, size=(320, 180), fps=10, seconds=0.5, particles=0, log=lambda m: None)
        self.assertEqual(n, 5)
        self.assertEqual(len(list(frames.glob('frame_*.png'))), 5)
        lay = video.Layout(json.loads((self.run_dir / 'preview_data.json').read_text())['config'], 320, 180)
        probe = (lay.left + lay.pw // 8, lay.top + lay.ph // 2)  # inside the reach, clear of the gabion
        first = Image.open(frames / 'frame_00000.png').convert('RGB')
        last = Image.open(frames / 'frame_00004.png').convert('RGB')
        self.assertEqual(first.size, (320, 180))
        self.assertEqual(first.getpixel(probe), (0, 0, 0))  # lowest concentration -> black
        self.assertEqual(last.getpixel(probe), video.RED)  # highest -> #FF2C2C

    def test_every_pixel_of_the_plot_maps_to_a_triangle(self):
        cfg = json.loads((self.run_dir / 'preview_data.json').read_text())['config']
        lay = video.Layout(cfg, 640, 360)
        idx = video.triangle_map(np.array([[0, 0], [2, 0], [2, 1], [0, 1]], float), [[0, 1, 2], [0, 2, 3]], lay)
        self.assertTrue((idx >= 0).all())

    def test_odd_frame_size_is_rejected(self):
        with self.assertRaises(ValueError):
            video.render_frames(self.run_dir, self.run_dir / 'frames', size=(321, 180), particles=0)

    @unittest.skipIf(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), 'FFmpeg not installed')
    def test_mp4_has_requested_size_and_rate(self):
        out = video.make_video(self.run_dir, size=(320, 180), fps=10, seconds=0.5, particles=50,
                               output=self.run_dir / 'out.mp4', log=lambda m: None)
        probe = json.loads(subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames', '-show_entries',
             'stream=width,height,r_frame_rate,nb_read_frames,pix_fmt', '-of', 'json', str(out)],
            capture_output=True, text=True, check=True).stdout)['streams'][0]
        self.assertEqual((probe['width'], probe['height']), (320, 180))
        self.assertEqual(probe['r_frame_rate'], '10/1')
        self.assertEqual(int(probe['nb_read_frames']), 5)
        self.assertEqual(probe['pix_fmt'], 'yuv420p')


if __name__ == '__main__':
    unittest.main()
