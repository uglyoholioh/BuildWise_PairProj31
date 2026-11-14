from catalogue.compatibility import check_configuration_components
from catalogue.models import Component, ConfigurationItem, specification_fields
from recommendations.models import GameSettingSupport
from recommendations.predictor import predict_fps
from recommendations.ml.schema import MODEL_VERSION, MODEL_METHOD, MODEL_LIMITATIONS

from .allocations import approved_pc_parts, component_snapshot, replace_pc_part, snapshot_items


SPECIFICATION_FIELDS = (
    ("category", "Category"),
    ("brand", "Brand"),
    ("model", "Model"),
    ("socket", "Socket"),
    ("memory_type", "Memory type"),
    ("form_factor", "Form factor"),
    ("ml_tier", "Performance tier"),
    ("requires_cpu_cooler", "Separate CPU cooler required"),
)


def specification_rows(original, replacement):
    """Compare saved descriptions; catalogue edits must not rewrite a proposal."""
    rows = []
    for field, label in SPECIFICATION_FIELDS:
        before = original.get(field, "")
        after = replacement.get(field, "")
        if field == "category":
            categories = dict(Component.Category.choices)
            before = categories.get(before, before)
            after = categories.get(after, after)
        if field == "requires_cpu_cooler":
            if not before and not after:
                continue
            before = "Yes" if before else "No"
            after = "Yes" if after else "No"
        if before or after:
            rows.append({"label": label, "original": before, "replacement": after, "changed": before != after})
    original_specs = specification_fields(original.get("specs"))
    replacement_specs = specification_fields(replacement.get("specs"))
    for label in sorted(original_specs.keys() | replacement_specs.keys()):
        before = original_specs.get(label)
        after = replacement_specs.get(label)
        rows.append({"label": label, "original": before, "replacement": after, "changed": before != after})
    return rows


def replacement_details_changed(proposal, replacement):
    current = component_snapshot(replacement)
    fields = [field for field, label in SPECIFICATION_FIELDS] + ["specs"]
    return any(
        proposal.replacement_snapshot.get(field, False if field == "requires_cpu_cooler" else None) != current[field]
        for field in fields
    )


def substitution_review_snapshot(pc, original, replacement):
    before = approved_pc_parts(pc)
    replacement_snapshot = component_snapshot(replacement)
    original_snapshot, after = replace_pc_part(before, original.pk, replacement_snapshot)

    performance = {"games": [], "method": MODEL_METHOD, "model_version": MODEL_VERSION, "limitations": MODEL_LIMITATIONS}
    customer = pc.order_line.order.customer
    questionnaire = customer.questionnaires.order_by("-created_at", "-pk").first()
    if not questionnaire or not questionnaire.game_settings.exists():
        performance["unavailable_reason"] = "No saved gaming preferences were available when this proposal was made."
        return original_snapshot, replacement_snapshot, performance

    performance["questionnaire_created_at"] = questionnaire.created_at.isoformat()
    original_items = snapshot_items(before)
    replacement_items = snapshot_items(after)
    original_complete = not check_configuration_components((item.component for item in original_items), require_active=False)
    replacement_complete = not check_configuration_components((item.component for item in replacement_items), require_active=False)
    for setting in questionnaire.game_settings.select_related("game").order_by("game__name"):
        supported = GameSettingSupport.objects.filter(
            game=setting.game, resolution=setting.resolution, graphics_preset=setting.graphics_preset,
        ).exists()
        original_fps = None
        replacement_fps = None
        if supported and original_complete:
            original_fps = predict_fps(original_items, setting)
        if supported and replacement_complete:
            replacement_fps = predict_fps(replacement_items, setting)
        performance["games"].append({
            "game": setting.game.name,
            "resolution": setting.resolution,
            "graphics_preset": setting.graphics_preset,
            "fps_goal": setting.fps_goal,
            "original_fps": original_fps,
            "replacement_fps": replacement_fps,
            "meets_target": replacement_fps is not None and replacement_fps >= setting.fps_goal,
        })
    return original_snapshot, replacement_snapshot, performance
