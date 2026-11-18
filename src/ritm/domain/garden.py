"""Правила сада и точная экономика без Qt, часов, файлов и начислений приложения."""

import re
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType
from uuid import UUID, uuid5

WIDTH = HEIGHT = 10
PLANT_CELLS = 80
TREE_LIMIT = 2
TERRAINS = frozenset({"grass", "tilled_soil", "water"})


@dataclass(frozen=True)
class Product:
    id: str
    title: str
    category: str
    price: int
    terrain: str | None
    size: int | None
    doses: tuple[int, ...] = ()

    @property
    def growing(self):
        return self.category in ("plant", "tree")


CATALOG = MappingProxyType(
    {
        p.id: p
        for p in (
            *(
                Product(key, title, "plant", 80, terrain, 1, (4, 8, 16))
                for key, title, terrain in (
                    ("daisy", "Ромашка", "grass"),
                    ("poppy", "Мак", "grass"),
                    ("fern", "Папоротник", "grass"),
                    ("carrot", "Морковь", "tilled_soil"),
                    ("pumpkin", "Тыква", "tilled_soil"),
                    ("water_lily", "Кувшинка", "water"),
                )
            ),
            Product("oak", "Дуб", "tree", 320, "grass", 2, (16, 32, 64)),
            Product("sakura", "Сакура", "tree", 320, "grass", 2, (16, 32, 64)),
            *(
                Product(key, title, "decor", price, None, None)
                for key, title, price in (
                    ("stone", "Камень", 40),
                    ("fence", "Секция забора", 40),
                    ("lantern", "Фонарь", 100),
                    ("bench", "Скамейка", 160),
                )
            ),
        )
    }
)


def natural(value, *, positive=False):
    if type(value) is not int or value < (1 if positive else 0) or value.bit_length() > 256:
        raise ValueError("Нужно неотрицательное целое число допустимого размера.")
    return value


def coins(value):
    if type(value) not in (int, Fraction) or value < 0:
        raise ValueError("Монеты должны быть неотрицательной точной суммой, без float.")
    result = Fraction(value)
    if max(len(str(result.numerator)), len(str(result.denominator))) > 128:
        raise ValueError("Сумма монет слишком велика.")
    return result


def completion_bonus(base_paid, difficulty=None, weight=Decimal("100")) -> Fraction:
    """Полная или взвешенная надбавка; идемпотентность начислений будет в T39c."""
    base = coins(base_paid)
    difficulty = 5 if difficulty is None else difficulty
    if type(difficulty) is not int or not 1 <= difficulty <= 10:
        raise ValueError("Сложность должна быть от 1 до 10.")
    if (
        not isinstance(weight, Decimal)
        or not weight.is_finite()
        or not 0 <= weight <= 100
        or weight != weight.quantize(Decimal("0.01"))
    ):
        raise ValueError("Вес — процент с точностью до сотой.")
    return max(Fraction(difficulty), base * difficulty / 50) * Fraction(weight) / 100


def focus_coins(minutes):
    """Стоимость уже отобранных оплачиваемых пятиминуток, не источник событий."""
    natural(minutes)
    return minutes // 5 * 10


@dataclass(frozen=True)
class GardenItem:
    kind: str
    id: UUID
    stage: int = 1
    x: int | None = None
    y: int | None = None

    def __post_init__(self):
        if (
            not isinstance(self.kind, str)
            or self.kind not in CATALOG
            or not isinstance(self.id, UUID)
        ):
            raise ValueError("Неизвестный предмет или некорректный UUID сада.")
        valid_stages = (1, 2, 3, 4) if self.product.growing else (0,)
        if type(self.stage) is not int or self.stage not in valid_stages:
            raise ValueError("Некорректная стадия предмета сада.")
        if (self.x is None) != (self.y is None):
            raise ValueError("Координаты задаются вместе.")
        if self.x is not None and (type(self.x) is not int or type(self.y) is not int):
            raise ValueError("Координаты должны быть целыми.")

    @property
    def product(self):
        return CATALOG[self.kind]

    @property
    def placed(self):
        return self.x is not None

    @property
    def cells(self):
        if not self.placed:
            return frozenset()
        size = self.product.size
        if size is None:
            raise ValueError("Размер украшения ещё не утверждён. Оно остаётся в инвентаре.")
        return frozenset(
            (x, y) for x in range(self.x, self.x + size) for y in range(self.y, self.y + size)
        )

    @property
    def sprite_id(self):
        return f"{self.kind}_{self.stage:02}" if self.product.growing else self.kind


@dataclass(frozen=True)
class Garden:
    month: str
    terrain: tuple[str, ...] = ("grass",) * 100
    items: tuple[GardenItem, ...] = ()
    fertilizer: int = 0
    milestone: bool = False

    def __post_init__(self):
        if not isinstance(self.month, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", self.month):
            raise ValueError("Месяц сада должен иметь формат ГГГГ-ММ.")
        date(int(self.month[:4]), int(self.month[5:]), 1)
        if (
            type(self.terrain) is not tuple
            or len(self.terrain) != 100
            or any(not isinstance(t, str) or t not in TERRAINS for t in self.terrain)
        ):
            raise ValueError("Поле должно содержать 100 известных клеток грунта.")
        if type(self.items) is not tuple or any(type(i) is not GardenItem for i in self.items):
            raise ValueError("Предметы сада должны быть неизменяемым списком.")
        if len({i.id for i in self.items}) != len(self.items):
            raise ValueError("UUID предметов не должны повторяться.")
        natural(self.fertilizer)
        if type(self.milestone) is not bool:
            raise ValueError("Отметка завершения должна быть логической.")
        occupied = set()
        for item in self.items:
            cells = item.cells
            if any(not (0 <= x < WIDTH and 0 <= y < HEIGHT) for x, y in cells):
                raise ValueError("Предмет выходит за границы поля.")
            if occupied.intersection(cells):
                raise ValueError("Клетка занята другим предметом.")
            if any(self.terrain[y * WIDTH + x] != item.product.terrain for x, y in cells):
                raise ValueError("Растение не совместимо с этим грунтом.")
            occupied.update(cells)
        if sum(i.placed and i.product.category == "tree" for i in self.items) > TREE_LIMIT:
            raise ValueError("На поле допускается не больше двух деревьев.")
        if self.planted_cells > PLANT_CELLS:
            raise ValueError("Растения могут занимать не больше 80 клеток.")

    @property
    def planted_cells(self):
        return sum(len(i.cells) for i in self.items if i.product.growing)

    @property
    def mature_cells(self):
        return sum(len(i.cells) for i in self.items if i.product.growing and i.stage == 4)

    def item(self, identifier):
        value = next((i for i in self.items if i.id == identifier), None)
        if value is None:
            raise ValueError("Предмет больше не принадлежит этому саду.")
        return value


@dataclass(frozen=True)
class GardenState:
    garden: Garden
    balance: Fraction = field(default_factory=Fraction)
    revision: int = 0

    def __post_init__(self):
        if type(self.garden) is not Garden:
            raise ValueError("Некорректный снимок сада.")
        object.__setattr__(self, "balance", coins(self.balance))
        natural(self.revision)


def _changed(garden, **changes):
    result = replace(garden, **changes)
    return replace(result, milestone=True) if result.mature_cells == PLANT_CELLS else result


def _fields(command, required):
    if type(command) is not dict or set(command) != {"action", *required}:
        raise ValueError("Команда сада содержит отсутствующие или лишние поля.")


def apply_command(state: GardenState, command: dict, operation_id: UUID):
    """Вернуть новый снимок и UUID предмета; исходный снимок никогда не меняется."""
    if not isinstance(operation_id, UUID) or not isinstance(command, dict):
        raise ValueError("Некорректная команда сада.")
    garden, balance = state.garden, state.balance
    action = command.get("action")
    identifier = None
    if action == "buy":
        _fields(command, {"kind"})
        kind = command["kind"]
        if not isinstance(kind, str) or kind not in CATALOG:
            raise ValueError("Товар отсутствует в каталоге.")
        product = CATALOG[kind]
        if balance < product.price:
            raise ValueError("Недостаточно монет.")
        identifier = uuid5(operation_id, "garden-item")
        item = GardenItem(kind, identifier, 1 if product.growing else 0)
        garden = _changed(garden, items=(*garden.items, item))
        balance -= product.price
    elif action == "buy_fertilizer":
        _fields(command, {"quantity"})
        quantity = natural(command["quantity"], positive=True)
        if balance < quantity * 10:
            raise ValueError("Недостаточно монет.")
        garden = _changed(garden, fertilizer=garden.fertilizer + quantity)
        balance -= quantity * 10
    elif action == "terrain":
        _fields(command, {"x", "y", "terrain"})
        x, y, terrain = command["x"], command["y"], command["terrain"]
        if type(x) is not int or type(y) is not int or not 0 <= x < WIDTH or not 0 <= y < HEIGHT:
            raise ValueError("Клетка находится за границей поля.")
        if not isinstance(terrain, str) or terrain not in TERRAINS:
            raise ValueError("Неизвестный грунт.")
        if any((x, y) in item.cells for item in garden.items):
            raise ValueError("Грунт меняется только на свободной клетке.")
        tiles = list(garden.terrain)
        tiles[y * WIDTH + x] = terrain
        garden = _changed(garden, terrain=tuple(tiles))
    elif action in ("place", "stash", "grow", "sell"):
        _fields(command, {"id", "x", "y"} if action == "place" else {"id"})
        if not isinstance(command["id"], str):
            raise ValueError("Нужен UUID предмета.")
        identifier = UUID(command["id"])
        item = garden.item(identifier)
        updated = item
        if action == "place":
            if command["x"] is None or command["y"] is None:
                raise ValueError("Для размещения нужны координаты.")
            updated = replace(item, x=command["x"], y=command["y"])
        elif action == "stash":
            updated = replace(item, x=None, y=None)
        elif action == "grow":
            if not item.product.growing or item.stage == 4:
                raise ValueError("Этот предмет больше не растёт.")
            dose = item.product.doses[item.stage - 1]
            if garden.fertilizer < dose:
                raise ValueError("Недостаточно удобрений для следующей стадии.")
            updated = replace(item, stage=item.stage + 1)
            garden = _changed(garden, fertilizer=garden.fertilizer - dose)
        elif action == "sell":
            if not item.product.growing:
                raise ValueError("Сейчас продаются только растения и деревья.")
            balance += item.product.price // 2
        items = tuple(updated if old.id == identifier else old for old in garden.items)
        if action == "sell":
            items = tuple(old for old in garden.items if old.id != identifier)
        garden = _changed(garden, items=items)
    else:
        raise ValueError("Неизвестное действие сада.")
    return GardenState(garden, balance, state.revision + 1), identifier
