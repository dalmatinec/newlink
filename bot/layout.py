"""Расположение кнопок меню по рядам. Одно нажатие стрелки = один шаг.

⬅️ ➡️: поменяться местами с соседом в ряду.
⬆️: если в ряду есть другие кнопки, кнопка уходит в свой ряд над ним;
     если она уже одна в ряду, то встаёт в конец ряда выше.
⬇️: то же самое вниз (в ряд ниже встаёт первой).
Пример: 2-2-2, последняя кнопка ⬆️ → 2-2-1-1, ещё ⬆️ → 2-3-1.
"""
from typing import Iterable

MAX_PER_ROW = 8  # больше Telegram в один ряд не ставит

Grid = list[list[int]]


def grid_of(items: Iterable) -> Grid:
    """Ряды из кнопок, отсортированных по (row, position)."""
    grid: Grid = []
    current = None
    for item in items:
        if item.row != current:
            grid.append([])
            current = item.row
        grid[-1].append(item.id)
    return grid


def move(grid: Grid, item_id: int, direction: str) -> Grid:
    grid = [list(r) for r in grid if r]
    pos = next(((r, row.index(item_id)) for r, row in enumerate(grid) if item_id in row), None)
    if pos is None:
        return grid
    r, c = pos
    row = grid[r]
    if direction in ("left", "right"):
        j = c - 1 if direction == "left" else c + 1
        if 0 <= j < len(row):
            row[c], row[j] = row[j], row[c]
    elif direction == "up":
        if len(row) > 1:
            row.pop(c)
            grid.insert(r, [item_id])
        elif r > 0 and len(grid[r - 1]) < MAX_PER_ROW:
            grid[r - 1].append(item_id)
            grid.pop(r)
    elif direction == "down":
        if len(row) > 1:
            row.pop(c)
            grid.insert(r + 1, [item_id])
        elif r < len(grid) - 1 and len(grid[r + 1]) < MAX_PER_ROW:
            grid[r + 1].insert(0, item_id)
            grid.pop(r)
    return grid


def rows_of(grid: Grid) -> list[tuple[int, int, int]]:
    """-> (row, position, item_id) для записи в базу."""
    return [(r, p, item_id) for r, row in enumerate(grid) for p, item_id in enumerate(row)]
