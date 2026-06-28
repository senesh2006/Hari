# product_validator.py
"""Utilities to filter and score Kapruka product results based on strategy constraints.

The validator enforces hard constraints only:
* Avoid terms (e.g. "peanut", "adult")
* Budget ceiling
* Simple health/dietary constraints extracted from the strategy's `constraints`

It returns a list of products that satisfy all hard rules, preserving the original order.
"""

from .validation_rules import is_price_within_budget, is_health_safe, is_category_safe, is_hobby_safe

def validate_products(products: list, strategy: dict, shown_products: list = None, rejected_categories: list = None) -> list:
    """Filter `products` according to the supplied `strategy` and recommendation memory.

    Parameters
    ----------
    products: list of dicts – each dict is a Kapruka product card.
    strategy: dict – the JSON object produced by the strategy LLM; relevant keys:
        - "avoid": list of lower‑cased terms that must not appear in name/description.
        - "budget": numeric budget or the string "open"/None.
        - "constraints": list of health/dietary constraints (e.g. "diabetic", "peanut allergy").
    shown_products: list – products already shown to the user in this session.
    rejected_categories: list – categories rejected by the user.

    Returns
    -------
    list of product dicts that pass all hard constraints and memory filters.
    """
    if not products:
        return []

    avoid = [str(a).lower() for a in (strategy.get("avoid") or []) if str(a).strip()]
    budget = strategy.get("budget")
    constraints = strategy.get("constraints") or []
    shown_products = shown_products or []
    rejected_categories = rejected_categories or []

    shown_set = {str(name).lower().strip() for name in shown_products}

    filtered = []
    for p in products:
        name_desc = f"{p.get('name', '')} {p.get('description', '')}".lower()
        name_lower = str(p.get('name', '')).lower().strip()
        
        # Filter out previously shown products
        if name_lower in shown_set:
            continue
            
        # Filter out rejected categories
        if not is_category_safe(p, rejected_categories):
            continue

        # Filter out hobby-inconsistent keyword matches (e.g. pet food for fishing)
        if not is_hobby_safe(p, strategy):
            continue

        if avoid and any(term in name_desc for term in avoid):
            continue
        if not is_price_within_budget(p, budget):
            continue
        if not is_health_safe(p, constraints):
            continue
        filtered.append(p)
    return filtered
