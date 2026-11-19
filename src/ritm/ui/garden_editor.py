"""Снимок редактора сада и доступность действий, без Qt/файлов/изменения модели."""

from ritm.domain.garden import CATALOG, apply_command

TERRAIN_TITLES = {"grass": "Трава", "tilled_soil": "Грядка", "water": "Вода"}


def empty_editor(error=""):
    return {
        "ready": False,
        "error": error,
        "revision": 0,
        "identity": "",
        "month": "",
        "items": [],
        "catalog": [],
        "fertilizer": 0,
        "fertilizerMax": "0",
        "plantedCells": 0,
        "matureCells": 0,
        "milestone": False,
    }


def editor_view(state, identity, sprites):
    garden = state.garden
    items = []
    for item in garden.items:
        p = item.product
        dose = p.doses[item.stage - 1] if p.growing and item.stage < 4 else 0
        items.append(
            {
                "id": str(item.id),
                "kind": item.kind,
                "title": p.title,
                "source": sprites[item.sprite_id].path.as_uri(),
                "stage": item.stage,
                "placed": item.placed,
                "x": item.x if item.placed else -1,
                "y": item.y if item.placed else -1,
                "growing": p.growing,
                "dose": dose,
                "canGrow": bool(dose and garden.fertilizer >= dose),
                "growReason": "Растение взрослое"
                if p.growing and not dose
                else "Недостаточно удобрения"
                if dose > garden.fertilizer
                else "",
                "refund": p.price // 2 if p.growing else 0,
                "offsets": [list(cell) for cell in p.cell_offsets],
            }
        )
    return {
        "ready": True,
        "error": "",
        "revision": state.revision,
        "identity": identity,
        "month": garden.month,
        "items": items,
        "fertilizer": garden.fertilizer,
        "fertilizerMax": str(state.balance // 10),
        "plantedCells": garden.planted_cells,
        "matureCells": garden.mature_cells,
        "milestone": garden.milestone,
        "catalog": [
            {
                "kind": p.id,
                "title": p.title,
                "price": p.price,
                "growing": p.growing,
                "source": sprites[f"{p.id}_04" if p.growing else p.id].path.as_uri(),
                "canBuy": state.balance >= p.price,
                "terrain": " / ".join(TERRAIN_TITLES[t] for t in sorted(p.allowed_terrain)),
                "cells": len(p.cell_offsets),
            }
            for p in CATALOG.values()
        ],
    }


def fertilizer_quote(text, maximum):
    if not isinstance(text, str) or not text.isascii() or not text.isdecimal() or len(text) > 12:
        return {"valid": False, "cost": "—", "quantity": 0, "canBuy": False}
    quantity = int(text)
    return {
        "valid": quantity > 0,
        "cost": str(quantity * 10),
        "quantity": quantity,
        "canBuy": 0 < quantity <= int(maximum),
    }


def placement_preview(state, identifier, x, y):
    """Чистая предварительная проверка теми же правилами; не расходует деньги/UUID."""
    from uuid import UUID

    try:
        apply_command(state, {"action": "place", "id": identifier, "x": x, "y": y}, UUID(int=0))
    except ValueError as error:
        return str(error)
    return ""
