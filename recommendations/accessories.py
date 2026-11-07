from django.db.models import Count

from catalogue.compatibility import REQUIRED_CONFIGURATION_CATEGORIES, check_configuration_components
from catalogue.models import Accessory, Component
from inventory.availability import available_accessory_quantity
from orders.allocations import approved_pc_parts, component_snapshot
from orders.models import OrderLine, PurchasedPC, SubstitutionProposal


# Conservative defaults for this optional purchase-history feature.
MIN_CUSTOMERS = 3
MIN_MATCHING_CATEGORIES = 5
MAX_SUGGESTIONS = 3


def _core_parts(parts):
    categories = {}
    for part in parts:
        category = part.get("category")
        if category in REQUIRED_CONFIGURATION_CATEGORIES and part.get("component_id") and part.get("quantity", 0) > 0:
            categories.setdefault(category, set()).add(part["component_id"])
    return categories


def accessory_suggestions(items):
    """Rank accessories bought alongside similar delivered PCs, without reserving stock."""
    items = list(items)
    if check_configuration_components(item.component for item in items):
        return []
    target = _core_parts(component_snapshot(item.component, item.quantity) for item in items)
    if len(target) != len(REQUIRED_CONFIGURATION_CATEGORIES):
        return []

    pcs = list(PurchasedPC.objects.filter(
        order_line__kind="build", order_line__order__status="delivered",
        order_line__order__payment_status="paid", status="quality_passed", refund_amount=0,
    ).select_related("order_line"))
    # Load accepted changes together so replaying history does not query once per PC.
    proposals_by_pc = {}
    for proposal in SubstitutionProposal.objects.filter(
        purchased_pc_id__in=[pc.pk for pc in pcs], status="accepted",
    ).order_by("decided_at", "pk"):
        proposals_by_pc.setdefault(proposal.purchased_pc_id, []).append(proposal)

    matching_orders = set()
    for pc in pcs:
        proposals = proposals_by_pc.get(pc.pk, [])
        if not pc.order_line.custom_snapshot.get("components") or any(not proposal.replacement_snapshot for proposal in proposals):
            continue  # Never infer an older purchase from today's catalogue contents.
        purchased = _core_parts(approved_pc_parts(pc, proposals))
        if len(purchased) != len(REQUIRED_CONFIGURATION_CATEGORIES):
            continue
        if any(purchased[category] != target[category] for category in (Component.Category.CPU, Component.Category.GPU)):
            continue
        matches = sum(purchased[category] == target[category] for category in REQUIRED_CONFIGURATION_CATEGORIES)
        if matches >= MIN_MATCHING_CATEGORIES:
            matching_orders.add(pc.order_line.order_id)
    if not matching_orders:
        return []

    evidence = list(OrderLine.objects.filter(
        order_id__in=matching_orders, kind="accessory", accessory__active=True, quantity__gt=0,
    ).values("accessory_id").annotate(
        purchase_count=Count("order_id", distinct=True),
        customer_count=Count("order__customer_id", distinct=True),
    ).filter(customer_count__gte=MIN_CUSTOMERS).order_by("-purchase_count", "accessory__price", "accessory_id"))
    accessories = Accessory.objects.in_bulk(row["accessory_id"] for row in evidence)
    suggestions = []
    for row in evidence:
        accessory = accessories[row["accessory_id"]]
        if available_accessory_quantity(accessory) < 1:
            continue
        suggestions.append({"accessory": accessory, "purchase_count": row["purchase_count"]})
        if len(suggestions) == MAX_SUGGESTIONS:
            break
    return suggestions
