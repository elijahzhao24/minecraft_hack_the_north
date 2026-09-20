"""Generate an A3 SVG with the exact default ChArUco geometry (print at 100%)."""
import argparse
from itertools import pairwise
from pathlib import Path

from hmc_backend.calibration.charuco import BoardSpec, build_board


def generate() -> str:
    spec = BoardSpec()
    board, _ = build_board(spec)
    # 280 pixels per square makes each marker 210 px: exactly 30 px per
    # cell for its 5x5 code plus one-cell border on each side.
    pixels_per_square = 280
    raster = board.generateImage((spec.squares_x * pixels_per_square, spec.squares_y * pixels_per_square))
    scale = 40 / pixels_per_square
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="297mm" height="420mm" viewBox="0 0 297 420">',
             '<rect width="297" height="420" fill="white"/>',
             '<text x="8.5" y="6" font-family="sans-serif" font-size="3">ChArUco 7x10 / 40 mm squares / 30 mm markers — A3, print 100%</text>',
             '<g transform="translate(8.5 10)" fill="black" shape-rendering="crispEdges">']
    # Compact vector rectangles for runs repeated across neighboring raster rows.
    previous, start = None, 0
    for y in range(raster.shape[0] + 1):
        if y < raster.shape[0]:
            row = raster[y] == 0
            changes = [x for x in range(1, len(row)) if row[x] != row[x - 1]]
            boundaries = [0] + changes + [len(row)]
            runs = tuple((a, b) for a, b in pairwise(boundaries) if row[a])
        else:
            runs = None
        if runs != previous:
            if previous is not None:
                for a, b in previous:
                    parts.append(f'<rect x="{a * scale:g}" y="{start * scale:g}" width="{(b-a)*scale:g}" height="{(y-start)*scale:g}"/>')
            start, previous = y, runs
    parts += ['</g>', '<path d="M8.5 414h50m-50 -1v2m50 -2v2" stroke="black" stroke-width=".2"/>',
              '<text x="62" y="416" font-family="sans-serif" font-size="3">This line must measure 50 mm. Bottom edge points toward front phone.</text>', '</svg>']
    return '\n'.join(parts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='../docs/calibration-board.svg')
    args = parser.parse_args()
    Path(args.out).write_text(generate())
