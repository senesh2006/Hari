# validation_rules.py
"""Helper functions for product constraint validation.

These operate on Kapruka product dicts.
"""
import re

def _parse_price(price):
    if price is None:
        return None
    try:
        return float(price)
    except Exception:
        s = re.sub(r"[^0-9.]", "", str(price))
        try:
            return float(s) if s else None
        except Exception:
            return None

def is_price_within_budget(product: dict, budget) -> bool:
    if budget is None or (isinstance(budget, str) and budget.lower() == "open"):
        return True
    price = _parse_price(product.get("price"))
    if price is None:
        return False
    try:
        limit = float(budget)
    except Exception:
        return True
    return price <= limit

def is_health_safe(product: dict, constraints: list) -> bool:
    if not constraints:
        return True
    text = f"{product.get('name', '')} {product.get('description', '')}".lower()
    for term in constraints:
        term = str(term).lower().strip()
        if term and term in text:
            return False
    return True
