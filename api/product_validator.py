# product_validator.py
"""Utilities to filter and score Kapruka product results based on strategy constraints.

The validator enforces hard constraints only:
* Avoid terms (e.g. "peanut", "adult")
* Budget ceiling
* Simple health/dietary constraints extracted from the strategy's `constraints`

It returns a list of products that satisfy all hard rules, preserving the original order.
"""

from .validation_rules import is_price_within_budget, is_health_safe

def validate_products(products: list, strategy: dict) -> list:
    """Filter `products` according to the supplied `strategy`.

    Parameters
    ----------
    products: list of dicts – each dict is a Kapruka product card.
    strategy: dict – the JSON object produced by the strategy LLM; relevant keys:
        - "avoid": list of lower‑cased terms that must not appear in name/description.
        - "budget": numeric budget or the string "open"/None.
        - "constraints": list of health/dietary constraints (e.g. "diabetic", "peanut allergy").

    Returns
    -------
    list of product dicts that pass all hard constraints.
    """
    if not products:
        return []

    avoid = [str(a).lower() for a in (strategy.get("avoid") or []) if str(a).strip()]
    budget = strategy.get("budget")
    constraints = strategy.get("constraints") or []

    filtered = []
    for p in products:
        name_desc = f"{p.get('name', '')} {p.get('description', '')}".lower()
        if avoid and any(term in name_desc for term in avoid):
            continue
        if not is_price_within_budget(p, budget):
            continue
        if not is_health_safe(p, constraints):
            continue
        filtered.append(p)
    return filtered
