import math
from pathlib import Path
import tempfile
import unittest

from sedimentcfd import Config
from sedimentcfd.dxf import config_from_dxf, read_gabions


def rectangle_dxf(rectangles, polyline=False):
    """Minimal DXF text with each rectangle as four LINEs (or one closed LWPOLYLINE) plus a centre line."""
    body = []
    for cx, cy, length, width, angle in rectangles:
        a = math.radians(angle)
        corners = [(cx + u * math.cos(a) - v * math.sin(a), cy + u * math.sin(a) + v * math.cos(a))
                   for u, v in ((-length / 2, -width / 2), (length / 2, -width / 2),
                                (length / 2, width / 2), (-length / 2, width / 2))]
        if polyline:
            body += ['0', 'LWPOLYLINE', '8', '0', '90', '4', '70', '1']
            for x, y in corners:
                body += ['10', repr(x), '20', repr(y)]
        else:
            for k in range(4):
                (x1, y1), (x2, y2) = corners[k], corners[(k + 1) % 4]
                body += ['0', 'LINE', '8', '0', '10', repr(x1), '20', repr(y1), '30', '0',
                         '11', repr(x2), '21', repr(y2), '31', '0']
        (x1, y1), (x2, y2) = corners[0], corners[2]  # a diagonal mark that is not a closed loop
        body += ['0', 'LINE', '8', '0', '10', repr(x1), '20', repr(y1), '11', repr(x2), '21', repr(y2)]
    return '\n'.join(['0', 'SECTION', '2', 'ENTITIES', *body, '0', 'ENDSEC', '0', 'EOF']) + '\n'


class DxfTests(unittest.TestCase):
    def write(self, text):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'cage.dxf'
        path.write_text(text)
        return path

    def test_reads_rotated_rectangles_from_lines_and_polylines(self):
        for polyline in (False, True):
            with self.subTest(polyline=polyline):
                path = self.write(rectangle_dxf([(-30, 50, 36, 1.8, 127.8), (35, 40, 36, 1.8, -83.4)], polyline))
                a, b = read_gabions(path)
                self.assertAlmostEqual(a.x, -30)
                self.assertAlmostEqual(a.y, 50)
                self.assertAlmostEqual(a.length, 36)
                self.assertAlmostEqual(a.width, 1.8)
                self.assertAlmostEqual(a.angle, 127.8)
                self.assertAlmostEqual(b.angle, 96.6)  # same rectangle as -83.4°

    def test_config_encloses_gabions_with_margins_and_keeps_cad_coordinates(self):
        path = self.write(rectangle_dxf([(0, 0, 10, 2, 0)]))
        config = config_from_dxf(path, inflow='right', cell=1, bank_margin=5, upstream=20, downstream=30)
        self.assertEqual((config.origin_x, config.origin_y), (-35, -6))  # downstream margin on the left
        self.assertEqual((config.length, config.width), (60, 12))
        self.assertEqual((config.nx, config.ny, config.inflow), (60, 12, 'right'))
        self.assertEqual(Config.from_dict(config.to_dict()), config)

    def test_rejects_drawings_without_rectangles(self):
        with self.assertRaises(ValueError):
            read_gabions(self.write('0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n'))

    def test_bundled_cage_drawing(self):
        drawing = Path(__file__).resolve().parents[1] / 'Cage.dxf'
        if not drawing.exists():
            self.skipTest('Cage.dxf not present')
        gabions = read_gabions(drawing)
        self.assertEqual(len(gabions), 5)
        for g in gabions:
            self.assertAlmostEqual(g.length, 36, places=3)
            self.assertAlmostEqual(g.width, 1.8, places=3)


if __name__ == '__main__':
    unittest.main()
