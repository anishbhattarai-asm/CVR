"""Conway's Game of Life, small enough to type out in a video."""

from collections import Counter
from typing import Iterable, Set, Tuple

Cell = Tuple[int, int]


def neighbours(cell: Cell) -> Iterable[Cell]:
    x, y = cell
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx or dy:
                yield x + dx, y + dy


def step(alive: Set[Cell]) -> Set[Cell]:
    """One generation: survive on 2 or 3 neighbours, be born on 3."""
    counts = Counter(n for cell in alive for n in neighbours(cell))
    return {cell for cell, n in counts.items()
            if n == 3 or (n == 2 and cell in alive)}


GLIDER = {(1, 0), (2, 1), (0, 2), (1, 2), (2, 2)}

if __name__ == "__main__":
    board = GLIDER
    for generation in range(4):
        print(f"gen {generation}: {sorted(board)}")
        board = step(board)  # the glider drifts one cell every 4 steps
