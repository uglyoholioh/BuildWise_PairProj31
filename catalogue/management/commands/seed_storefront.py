"""Populate BuildWise with a complete, sale-ready storefront.

The command is safe to rerun. Catalogue records and presets are kept in sync
with ``catalogue/data/storefront_catalogue.json``; orders, reviews, support
threads and saved builds are created once and then left untouched so that
historical records stay immutable.
"""
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Customer, Employee
from catalogue.compatibility import check_preset_items
from catalogue.models import (
    Accessory,
    AssemblyFeeSetting,
    Component,
    Configuration,
    ConfigurationItem,
)
from inventory.models import Reservation, StockAdjustment
from orders.allocations import component_snapshot
from orders.models import (
    Order,
    OrderLine,
    OrderStatusHistory,
    PurchasedPC,
    QualityCheck,
    Shipment,
    SubstitutionProposal,
)
from orders.substitutions import substitution_review_snapshot
from recommendations.ml.schema import GAMES, GRAPHICS_PRESETS, RESOLUTIONS
from recommendations.models import (
    Game,
    GameSettingSupport,
    QuestionnaireGameSetting,
    QuestionnaireResponse,
    ShortlistItem,
)
from support.models import Review, SupportMessage, SupportRequest

DEFAULT_PASSWORD = "storefront123"
ASSEMBLY_FEE = Decimal("100.00")
DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "storefront_catalogue.json"

LEGACY_CONFIGURATION_NAMES = {
    "[Demo] BuildWise 5060 Gaming PC": "BuildWise Core 5060",
    "[Demo] BuildWise RX 9070 XT Gaming PC": "BuildWise Core 9070 XT",
}

STAFF = (
    ("alicia", "Alicia", "Tan", "alicia.tan@buildwise.example", Employee.Role.MANAGER),
    ("marcus", "Marcus", "Lim", "marcus.lim@buildwise.example", Employee.Role.EMPLOYEE),
    ("priya", "Priya", "Nair", "priya.nair@buildwise.example", Employee.Role.EMPLOYEE),
)

CUSTOMERS = (
    ("weijie", "Tan Wei Jie", "weijie.tan@example.com",
     "Blk 412 Bedok North Ave 2, #08-134, Singapore 460412"),
    ("nurul", "Nurul Huda Binte Rahman", "nurul.rahman@example.com",
     "21 Jurong West Street 41, #05-02, Singapore 640421"),
    ("daniel", "Daniel Ong", "daniel.ong@example.com",
     "Blk 158 Bukit Batok Street 11, #12-77, Singapore 650158"),
    ("sarah", "Sarah Chua", "sarah.chua@example.com",
     "8 Sin Ming Road, #03-11, Singapore 575629"),
    ("arjun", "Arjun Menon", "arjun.menon@example.com",
     "Blk 302 Anchorvale Link, #09-45, Singapore 540302"),
    ("meiling", "Lee Mei Ling", "meiling.lee@example.com",
     "12 Tanjong Pagar Road, #06-01, Singapore 088444"),
)

QUESTIONNAIRES = {
    # Three recommendation strategies: value, performance and lowest cost.
    "sarah": {
        "days_ago": 17,
        "max_spend": "3300.00",
        "games": (
            ("counter-strike-2", 140, "1920x1080", "high"),
            ("fortnite", 100, "1920x1080", "high"),
        ),
    },
    "weijie": {
        "days_ago": 13,
        "max_spend": "3800.00",
        "games": (
            ("counter-strike-2", 140, "1920x1080", "high"),
            ("fortnite", 100, "1920x1080", "high"),
        ),
    },
    # Shows the affordable-alternatives and above-budget states.
    "arjun": {
        "days_ago": 22,
        "max_spend": "3000.00",
        "games": (("cyberpunk-2077", 80, "2560x1440", "high"),),
    },
    "meiling": {
        "days_ago": 16,
        "max_spend": "3800.00",
        "games": (("counter-strike-2", 200, "2560x1440", "high"),),
    },
}

ACCESSORY = {
    "mouse": "Logitech G203 LIGHTSYNC Gaming Mouse (Black)",
    "combo": "Logitech MK235 Wireless Keyboard and Mouse Combo",
    "headset": "JBL Quantum 120 Over-Ear Gaming Wired Headphones Black",
    "mousepad": "Razer Gigantus V2 Soft Gaming Mousepad (XXL - Wuthering Waves Edition)",
    "keyboard": "Razer Reclusa X Mini Wired Gaming Keyboard",
    "monitor": 'Lenovo LOQ 27-10 27" FHD IPS 200Hz Gaming Monitor',
}

ORDERS = (
    {
        "key": "BW-2401", "customer": "weijie", "state": "delivered", "placed_days": 12,
        "courier": "Ninja Van", "tracking": "NV-SG-84216730", "confirmed_by": "customer",
        "lines": (
            {"kind": "build", "config": "BuildWise Performance 9070 XT"},
            {"kind": "accessory", "name": ACCESSORY["mouse"]},
            {"kind": "accessory", "name": ACCESSORY["headset"]},
            {"kind": "accessory", "name": ACCESSORY["mousepad"]},
        ),
        "review": (5, "The build is quiet and runs Cyberpunk at 1440p on high without stuttering. "
                      "Cable management was clean and the delivery updates were accurate."),
    },
    {
        "key": "BW-2402", "customer": "nurul", "state": "delivered", "placed_days": 18,
        "courier": "Ninja Van", "tracking": "NV-SG-83951442", "confirmed_by": "staff",
        "lines": (
            {"kind": "build", "config": "BuildWise Performance 9070 XT"},
            {"kind": "accessory", "name": ACCESSORY["mouse"]},
            {"kind": "accessory", "name": ACCESSORY["headset"]},
            {"kind": "accessory", "name": ACCESSORY["mousepad"]},
        ),
        "review": (5, "Ordered for my brother's birthday. The PC arrived two days early and the RGB can be "
                      "switched off, which he prefers. Very happy with the finish."),
    },
    {
        "key": "BW-2403", "customer": "daniel", "state": "delivered", "placed_days": 25,
        "courier": "J&T Express", "tracking": "JTSG77210458", "confirmed_by": "customer",
        "lines": (
            {"kind": "build", "config": "BuildWise Performance 9070 XT"},
            {"kind": "accessory", "name": ACCESSORY["mouse"]},
            {"kind": "accessory", "name": ACCESSORY["headset"]},
            {"kind": "accessory", "name": ACCESSORY["mousepad"]},
        ),
    },
    {
        "key": "BW-2404", "customer": "meiling", "state": "delivered", "placed_days": 15,
        "courier": "Ninja Van", "tracking": "NV-SG-85120674", "confirmed_by": "customer",
        "lines": (
            {
                "kind": "build", "config": "BuildWise Creator 5060",
                "accepted_substitution": {
                    "original": "gpu_rtx5060",
                    "replacement": "gpu_rx9070xt",
                    "reason": "The selected RTX 5060 allocation was delayed. We offered the RX 9070 XT "
                              "at no extra cost so the build could stay on schedule.",
                    "spec_differences": "16GB VRAM instead of 8GB, a larger triple-fan card and higher "
                                        "1440p headroom. The 850W power supply already covers the upgrade.",
                    "fps_note": "Estimated 1440p performance improves substantially; the confirmed order "
                                "price is unchanged.",
                },
            },
        ),
        "review": (5, "Originally ordered the 5060 build. BuildWise offered the 9070 XT when the 5060 was "
                      "delayed, and I accepted. The upgrade was fitted before delivery and the price did not "
                      "change. Great service."),
    },
    {
        "key": "BW-2405", "customer": "weijie", "state": "delivered", "placed_days": 30,
        "courier": "J&T Express", "tracking": "JTSG76018823", "confirmed_by": "customer",
        "lines": (
            {"kind": "build", "config": "BuildWise Core 5060"},
            {"kind": "accessory", "name": ACCESSORY["mouse"]},
            {"kind": "accessory", "name": ACCESSORY["combo"]},
            {"kind": "accessory", "name": ACCESSORY["headset"]},
        ),
        "review": (4, "Good value for 1080p. Handles CS2 and Fortnite easily. I added a monitor later and it "
                      "was plug-and-play."),
    },
    {
        "key": "BW-2406", "customer": "sarah", "state": "delivered", "placed_days": 16,
        "courier": "Ninja Van", "tracking": "NV-SG-84009715", "confirmed_by": "customer",
        "lines": (
            {"kind": "build", "config": "BuildWise Core 5060"},
            {"kind": "accessory", "name": ACCESSORY["mouse"]},
            {"kind": "accessory", "name": ACCESSORY["combo"]},
            {"kind": "accessory", "name": ACCESSORY["headset"]},
        ),
        "review": (5, "Exactly what I needed for university and gaming. The staff answered my delivery "
                      "question quickly and the build was well packed."),
    },
    {
        "key": "BW-2407", "customer": "arjun", "state": "delivered", "placed_days": 21,
        "courier": "Ninja Van", "tracking": "NV-SG-84370826", "confirmed_by": "customer",
        "lines": (
            {
                "kind": "build", "config": "BuildWise Core 5060",
                "name": "Arjun's Core 5060", "customised": True,
                "replacements": {"storage": "ssd_kioxia_plus", "case": "case_phanteks_xtv5"},
            },
            {"kind": "accessory", "name": ACCESSORY["mouse"]},
            {"kind": "accessory", "name": ACCESSORY["combo"]},
            {"kind": "accessory", "name": ACCESSORY["headset"]},
        ),
        "review": (4, "The quieter case was worth it. Runs cool and the cable work is neat. The monitor stand "
                      "wobble was a minor issue, but support responded the same day."),
    },
    {
        "key": "BW-2408", "customer": "sarah", "state": "dispatched", "placed_days": 5,
        "courier": "Ninja Van", "tracking": "NV-SG-85620931",
        "lines": ({"kind": "build", "config": "BuildWise Creator 5060"},),
    },
    {
        "key": "BW-2409", "customer": "daniel", "state": "packing", "placed_days": 4,
        "lines": ({"kind": "build", "config": "BuildWise Elite 9070 XT"},),
    },
    {
        "key": "BW-2410", "customer": "weijie", "state": "quality_checking", "placed_days": 2,
        "lines": ({"kind": "build", "config": "BuildWise Core 9070 XT"},),
    },
    {
        "key": "BW-2411", "customer": "nurul", "state": "queued", "placed_days": 1,
        "lines": ({"kind": "build", "config": "BuildWise Creator 5060"},),
    },
    {
        "key": "BW-2412", "customer": "arjun", "state": "on_hold", "placed_days": 3,
        "lines": (
            {
                "kind": "build", "config": "BuildWise Creator 5060",
                "pending_substitution": {
                    "original": "gpu_rtx5060",
                    "replacement": "gpu_rx9070xt",
                    "reason": "The RTX 5060 allocation is delayed. We can fit the RX 9070 XT at no extra "
                              "cost to keep the build on schedule.",
                    "spec_differences": "16GB VRAM instead of 8GB and a higher 1440p performance ceiling. "
                                        "The existing 850W power supply supports the replacement.",
                    "fps_note": "The replacement is estimated to clear the customer's 1440p target.",
                },
            },
        ),
    },
    {
        "key": "BW-2413", "customer": "nurul", "state": "cancelled", "placed_days": 20,
        "lines": ({"kind": "build", "config": "BuildWise Core 5060"},),
    },
    {
        "key": "BW-2414", "customer": "meiling", "state": "delivered", "placed_days": 10,
        "courier": "J&T Express", "tracking": "JTSG77134502", "confirmed_by": "customer",
        "lines": (
            {"kind": "accessory", "name": ACCESSORY["combo"]},
            {"kind": "accessory", "name": ACCESSORY["keyboard"]},
            {"kind": "accessory", "name": ACCESSORY["monitor"]},
        ),
    },
)

SUPPORT_THREADS = (
    {
        "customer": "weijie", "order": "BW-2401", "status": "in_progress", "created_days": 4,
        "messages": (
            ("customer", 4, False, "The rear exhaust fan is ticking under load. It started after the first "
                                   "week. Everything else is running fine."),
            ("employee", 3.9, False, "Thanks for the recording. Please check that no cable is touching the "
                                      "rear fan. If the noise continues, we can send a replacement fan under "
                                      "warranty."),
            ("customer", 3.8, False, "Checked the cables. The ticking is still there when the GPU is under "
                                     "load."),
            ("employee", 3.7, True, "Likely fan bearing. One Corsair 3500X rear fan in stock; offer a courier "
                                    "replacement or a drop-in at the workshop."),
        ),
    },
    {
        "customer": "nurul", "order": "BW-2402", "status": "closed", "created_days": 11,
        "resolution": "Delivery slot confirmed for 2-4 pm on the agreed date. Customer acknowledged.",
        "messages": (
            ("customer", 11, False, "Can I change the delivery to after 2pm? I will be at work in the "
                                    "morning."),
            ("employee", 10, False, "Yes, we have moved your delivery to the 2-4 pm slot. The courier will "
                                    "call before arrival."),
            ("customer", 10, False, "Perfect, thank you."),
        ),
    },
    {
        "customer": "arjun", "order": "BW-2407", "status": "open", "created_days": 3,
        "messages": (
            ("customer", 3, False, "The monitor stand has a slight wobble when I type. The screen itself is "
                                   "fine. Is there a replacement stand or a mount you recommend?"),
            ("employee", 2.9, False, "We can send a replacement stand, or the Northbayou A5-XE mount if you "
                                     "prefer a desk arm. Let us know which you would like."),
        ),
    },
    {
        "customer": "meiling", "order": "BW-2414", "status": "open", "created_days": 3,
        "messages": (
            ("customer", 3, False, "One key on the MK235 keyboard feels mushy. Can I swap it?"),
            ("employee", 2.9, False, "Please bring it to the counter or we can arrange a one-for-one "
                                     "replacement. Keep the box and receipt if possible."),
            ("customer", 2.8, False, "I have the receipt. Can you arrange courier pickup?"),
            ("employee", 2.7, True, "Replacement MK235 reserved from accessory stock; arrange pickup after "
                                    "the customer confirms the address."),
        ),
    },
)


ORDER_TIMES = {
    "BW-2401": (20, 15),
    "BW-2402": (11, 5),
    "BW-2403": (14, 40),
    "BW-2404": (19, 25),
    "BW-2405": (9, 50),
    "BW-2406": (16, 10),
    "BW-2407": (12, 35),
    "BW-2408": (21, 5),
    "BW-2409": (10, 45),
    "BW-2410": (18, 20),
    "BW-2411": (13, 15),
    "BW-2412": (15, 55),
    "BW-2413": (17, 30),
    "BW-2414": (11, 40),
}

SUPPORT_TIMES = {
    "BW-2401": (9, 40),
    "BW-2402": (15, 20),
    "BW-2407": (11, 10),
    "BW-2414": (14, 5),
}


def ago(days=0, hours=0, minutes=0):
    return timezone.now() - timedelta(days=days, hours=hours, minutes=minutes)


def at(days_ago, hour, minute=0):
    """A specific local time on the day ``days_ago`` days before now."""
    return (timezone.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )


def set_created(instance, when):
    type(instance).objects.filter(pk=instance.pk).update(created_at=when)


class Command(BaseCommand):
    help = (
        "Populate a complete BuildWise storefront: catalogue, presets, staff and customer "
        "accounts, stock, orders, reviews, support conversations, questionnaires and shortlists."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help="Password used for newly created staff and customer accounts.",
        )
        parser.add_argument(
            "--skip-orders",
            action="store_true",
            help="Create the catalogue and accounts without orders, reviews or support threads.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        password = options["password"]

        staff = self._ensure_staff(password)
        customers = self._ensure_customers(password)
        components = self._upsert_components(data["components"])
        accessories = self._upsert_accessories(data["accessories"])
        fee = self._ensure_assembly_fee(staff["alicia"])
        self._ensure_games()
        self._receive_stock(data, components, accessories, staff["marcus"])
        configurations = self._upsert_configurations(data["configurations"], components)

        self._ensure_questionnaires(QUESTIONNAIRES, customers, components)
        self._ensure_shortlists(components, configurations, customers)

        order_index = {}
        if not options["skip_orders"]:
            for spec in ORDERS:
                order, pcs = self._create_order(spec, customers, components, accessories, configurations, fee, staff)
                if order is not None:
                    order_index[spec["key"]] = (spec, order, pcs)
            self._ensure_reviews(order_index)
            self._ensure_support(SUPPORT_THREADS, customers, order_index, staff)

        published = Configuration.objects.filter(status=Configuration.Status.PUBLISHED).count()
        draft = Configuration.objects.filter(status=Configuration.Status.DRAFT).count()
        withdrawn = Configuration.objects.filter(status=Configuration.Status.WITHDRAWN).count()
        created_orders = len(order_index)
        total_orders = Order.objects.count()
        self.stdout.write(self.style.SUCCESS("Storefront data is ready."))
        self.stdout.write(
            f"  Catalogue: {len(components)} components, {len(accessories)} accessories\n"
            f"  Presets: {published} published, {draft} draft, {withdrawn} withdrawn\n"
            f"  Accounts: {len(staff)} staff, {len(customers)} customers\n"
            f"  Orders: {total_orders} total"
            + ("" if options["skip_orders"] else f" ({created_orders} created this run)")
        )
        self.stdout.write("")
        self.stdout.write("Sign in at /staff/login with a manager or employee account:")
        self.stdout.write("  alicia  (manager)   marcus  (employee)   priya  (employee)")
        self.stdout.write("Sign in at /accounts/login with a customer account:")
        self.stdout.write("  weijie  nurul  daniel  sarah  arjun  meiling")
        self.stdout.write(f"Password for newly created accounts: {password}")
        self.stdout.write("Existing accounts keep their current passwords.")
        self.stdout.write("Existing orders and reviews are preserved when this command is rerun.")

    # ------------------------------------------------------------------ accounts
    def _ensure_user(self, username, first_name, last_name, email, password):
        user = User.objects.filter(username=username).first()
        if user is None:
            user = User.objects.create_user(
                username=username,
                password=password,
                first_name=first_name,
                last_name=last_name,
                email=email,
            )
            return user, True
        changed = []
        if not user.has_usable_password():
            user.set_password(password)
            changed.append("password")
        for field, value in (("first_name", first_name), ("last_name", last_name), ("email", email)):
            if getattr(user, field) != value:
                setattr(user, field, value)
                changed.append(field)
        if changed:
            user.save(update_fields=changed)
        return user, False

    def _ensure_staff(self, password):
        staff = {}
        for username, first_name, last_name, email, role in STAFF:
            user, _ = self._ensure_user(username, first_name, last_name, email, password)
            employee, _ = Employee.objects.get_or_create(
                user=user, defaults={"role": role, "active": True}
            )
            staff[username] = employee
        return staff

    def _ensure_customers(self, password):
        customers = {}
        for username, name, email, address in CUSTOMERS:
            first_name, _, last_name = name.partition(" ")
            user, _ = self._ensure_user(username, first_name, last_name, email, password)
            customer, _ = Customer.objects.get_or_create(
                user=user,
                defaults={"name": name, "email": email, "delivery_address": address},
            )
            customers[username] = customer
        return customers

    # ----------------------------------------------------------------- catalogue
    def _upsert_components(self, rows):
        components = {}
        for row in rows:
            identity = {
                "category": row["category"],
                "brand": row["brand"],
                "model": row["model"],
            }
            defaults = {key: value for key, value in row.items() if key not in ("key", "stock")}
            defaults["price"] = Decimal(defaults["price"])
            component = Component.objects.filter(**identity).first()
            if component is None:
                component = Component.objects.create(**defaults)
            else:
                for field, value in defaults.items():
                    setattr(component, field, value)
                component.save()
            components[row["key"]] = component
        return components

    def _upsert_accessories(self, rows):
        accessories = {}
        for row in rows:
            defaults = {key: value for key, value in row.items() if key not in ("name", "stock")}
            defaults["price"] = Decimal(defaults["price"])
            accessory = Accessory.objects.filter(name=row["name"]).first()
            if accessory is None:
                accessory = Accessory.objects.create(name=row["name"], **defaults)
            else:
                for field, value in defaults.items():
                    setattr(accessory, field, value)
                accessory.save()
            accessories[row["name"]] = accessory
        return accessories

    def _ensure_assembly_fee(self, manager):
        fee = AssemblyFeeSetting.objects.order_by("-updated_at", "-pk").first()
        if fee is None or (not Order.objects.exists() and fee.fee_amount != ASSEMBLY_FEE):
            fee = AssemblyFeeSetting.objects.create(
                fee_amount=ASSEMBLY_FEE, updated_by=manager
            )
        return AssemblyFeeSetting.objects.get(pk=fee.pk)

    def _ensure_games(self):
        for slug, name in GAMES.items():
            game, _ = Game.objects.update_or_create(slug=slug, defaults={"name": name})
            for resolution in RESOLUTIONS:
                for preset in GRAPHICS_PRESETS:
                    GameSettingSupport.objects.get_or_create(
                        game=game, resolution=resolution, graphics_preset=preset
                    )

    def _receive_stock(self, data, components, accessories, employee):
        for index, row in enumerate(data["components"]):
            self._receive_item(
                "component", components[row["key"]], row.get("stock", 0), employee,
                ago(days=36 - (index % 7), hours=index % 9),
            )
        for index, row in enumerate(data["accessories"]):
            self._receive_item(
                "accessory", accessories[row["name"]], row.get("stock", 0), employee,
                ago(days=30 - (index % 7), hours=index % 9),
            )

    def _receive_item(self, item_type, item, quantity, employee, when):
        if quantity <= 0:
            return
        field = "component" if item_type == "component" else "accessory"
        if StockAdjustment.objects.filter(item_type=item_type, **{field: item}).exists():
            return
        adjustment = StockAdjustment.objects.create(
            item_type=item_type,
            **{field: item},
            delta=quantity,
            reason="Stock received - November allocation",
            created_by=employee,
        )
        set_created(adjustment, when)

    def _rename_legacy_configurations(self):
        for old_name, new_name in LEGACY_CONFIGURATION_NAMES.items():
            legacy = Configuration.objects.filter(name=old_name).first()
            if legacy is None:
                continue
            if Configuration.objects.filter(name=new_name).exists():
                if not OrderLine.objects.filter(configuration=legacy).exists():
                    legacy.delete()
            else:
                legacy.name = new_name
                legacy.save(update_fields=("name",))

    def _upsert_configurations(self, rows, components):
        self._rename_legacy_configurations()
        configurations = {}
        for row in rows:
            if row["status"] not in Configuration.Status.values:
                raise CommandError(f"Unknown preset status for {row['name']}.")
            configuration = Configuration.objects.filter(name=row["name"]).first()
            if configuration is None:
                configuration = Configuration.objects.create(
                    name=row["name"], status=Configuration.Status.DRAFT
                )
            configuration.description = row.get("description", "")
            configuration.image = row.get("image", "")
            configuration.source_url = row.get("source_url", "")
            configuration.save(update_fields=("description", "image", "source_url"))

            configuration.items.all().delete()
            for key in row["components"]:
                component = components.get(key)
                if component is None:
                    raise CommandError(f"Preset {row['name']} refers to unknown component {key}.")
                ConfigurationItem.objects.create(
                    configuration=configuration, component=component, quantity=1
                )
            issues = check_preset_items(configuration.items.select_related("component"))
            if issues:
                raise CommandError(f"Preset {row['name']} is not valid: {' '.join(issues)}")
            configuration.status = row["status"]
            configuration.save(update_fields=("status",))
            configurations[row["name"]] = configuration
        return configurations

    # ------------------------------------------------------- preferences and lists
    def _ensure_questionnaires(self, specs, customers, components):
        for username, spec in specs.items():
            customer = customers[username]
            if customer.questionnaires.exists():
                continue
            response = QuestionnaireResponse.objects.create(
                customer=customer,
                perf_level="balanced",
                max_spend=Decimal(spec["max_spend"]),
            )
            set_created(response, at(spec["days_ago"], 20, 15))
            for slug, fps_goal, resolution, graphics_preset in spec["games"]:
                game = Game.objects.get(slug=slug)
                QuestionnaireGameSetting.objects.create(
                    response=response,
                    game=game,
                    fps_goal=fps_goal,
                    resolution=resolution,
                    graphics_preset=graphics_preset,
                )

    def _snapshot_components(self, configuration, components, replacements=None):
        parts = [
            {"component_id": item.component_id, "quantity": item.quantity}
            for item in configuration.items.all()
        ]
        if replacements:
            categories = {component.pk: component.category for component in components.values()}
            for category, key in replacements.items():
                parts = [
                    part for part in parts
                    if categories[part["component_id"]] != category
                ]
                parts.append({"component_id": components[key].pk, "quantity": 1})
        return {"components": parts}

    def _ensure_shortlists(self, components, configurations, customers):
        lists = (
            ("weijie", "9070 XT performance build", "BuildWise Performance 9070 XT", None, 9),
            ("weijie", "Gen5 storage upgrade", "BuildWise Core 9070 XT", {"storage": "ssd_wd_sn8100"}, 7),
            ("sarah", "Creator 5060 for university", "BuildWise Creator 5060", None, 4),
            ("arjun", "Core 5060 with quieter case", "BuildWise Core 5060",
             {"case": "case_phanteks_xtv5"}, 6),
        )
        for username, name, configuration_name, replacements, days_ago in lists:
            customer = customers[username]
            if customer.shortlist.filter(name=name).exists():
                continue
            configuration = configurations[configuration_name]
            saved = ShortlistItem.objects.create(
                customer=customer,
                configuration=configuration,
                name=name,
                custom_snapshot=self._snapshot_components(configuration, components, replacements),
            )
            set_created(saved, at(days_ago, 19, 30))

    # -------------------------------------------------------------------- orders
    def _prepared_lines(self, spec, components, accessories, configurations, fee):
        prepared = []
        for line in spec["lines"]:
            if line["kind"] == "accessory":
                accessory = accessories[line["name"]]
                quantity = line.get("quantity", 1)
                prepared.append({
                    "line": line, "kind": "accessory", "accessory": accessory,
                    "quantity": quantity, "unit_price": Decimal(accessory.price),
                })
                continue
            configuration = configurations[line["config"]]
            parts = [
                (item.component, item.quantity)
                for item in configuration.items.select_related("component")
            ]
            for category, key in (line.get("replacements") or {}).items():
                parts = [(component, quantity) for component, quantity in parts if component.category != category]
                parts.append((components[key], 1))
            unit_price = sum(
                (component.price * quantity for component, quantity in parts), Decimal("0.00")
            ) + fee.fee_amount
            prepared.append({
                "line": line, "kind": "build", "configuration": configuration,
                "parts": parts, "quantity": line.get("quantity", 1), "unit_price": unit_price,
            })
        return prepared

    def _create_order(self, spec, customers, components, accessories, configurations, fee, staff):
        existing = Order.objects.filter(checkout_reference=spec["key"]).first()
        if existing is not None:
            return None, []

        prepared = self._prepared_lines(spec, components, accessories, configurations, fee)
        total = sum(
            (row["unit_price"] * row["quantity"] for row in prepared), Decimal("0.00")
        )
        customer = customers[spec["customer"]]
        hour, minute = ORDER_TIMES.get(spec["key"], (10, 0))
        placed = at(spec["placed_days"], hour, minute)
        order = Order.objects.create(
            checkout_reference=spec["key"],
            customer=customer,
            payment_status="paid",
            total_snapshot=total,
            delivery_name=customer.name,
            delivery_address=customer.delivery_address,
        )
        Order.objects.filter(pk=order.pk).update(placed_at=placed)
        order.placed_at = placed

        pcs = []
        for row in prepared:
            line_spec = row["line"]
            if row["kind"] == "accessory":
                line = OrderLine.objects.create(
                    order=order,
                    kind="accessory",
                    name=row["accessory"].name,
                    quantity=row["quantity"],
                    unit_price_snapshot=row["unit_price"],
                    accessory=row["accessory"],
                    custom_snapshot={"accessory": {
                        "name": row["accessory"].name,
                        "category": row["accessory"].category,
                    }},
                )
                Reservation.objects.create(
                    accessory=row["accessory"], order_line=line, quantity=row["quantity"]
                )
                continue

            configuration = row["configuration"]
            customised = bool(line_spec.get("customised"))
            snapshot = {
                "components": [
                    component_snapshot(component, quantity)
                    for component, quantity in row["parts"]
                ],
                "source_preset_id": configuration.pk,
                "source_preset_name": configuration.name,
                "customised_from_preset": customised,
            }
            line = OrderLine.objects.create(
                order=order,
                kind="build",
                name=line_spec.get("name") or configuration.name,
                quantity=row["quantity"],
                unit_price_snapshot=row["unit_price"],
                configuration=None if customised else configuration,
                custom_snapshot=snapshot,
            )
            for unit_index in range(1, row["quantity"] + 1):
                pc = PurchasedPC.objects.create(order_line=line, unit_index=unit_index)
                for component, quantity in row["parts"]:
                    Reservation.objects.create(
                        component=component, purchased_pc=pc, quantity=quantity
                    )
                pc.order_spec_line = line_spec
                pcs.append(pc)

        self._apply_order_state(order, pcs, spec, staff["marcus"], components)
        return order, pcs

    def _apply_order_state(self, order, pcs, spec, employee, components):
        placed = order.placed_at
        state = spec["state"]
        # Keep in-progress work in the past even when an order was placed two days ago.
        quality_at = min(placed + timedelta(days=2), timezone.now() - timedelta(hours=2))
        packing_at = placed + timedelta(days=3)
        delivered_at = placed + timedelta(days=6)

        if state == "cancelled":
            for pc in pcs:
                pc.status = "cancelled"
                pc.refund_amount = pc.order_line.unit_price_snapshot
                pc.save(update_fields=("status", "refund_amount"))
                pc.reservations.filter(status=Reservation.Status.RESERVED).update(
                    status=Reservation.Status.RELEASED
                )
            order.status = "cancelled"
            order.payment_status = "refunded"
            order.save(update_fields=("status", "payment_status"))
            self._timeline(order, "placed", placed, "Simulated payment succeeded and stock was reserved.")
            self._timeline(
                order, "cancelled", placed + timedelta(days=1),
                f"Customer cancelled before assembly. Reservations released. "
                f"Simulated refund of ${order.total_snapshot:.2f} recorded.",
                from_status="placed",
            )
            return

        if state == "queued":
            for pc in pcs:
                pc.status = "queued"
                pc.save(update_fields=("status",))
            self._timeline(order, "placed", placed, "Simulated payment succeeded and stock was reserved.")
            return

        self._timeline(order, "placed", placed, "Simulated payment succeeded and stock was reserved.")

        if pcs:
            for pc in pcs:
                pc.assembly_started = True
                pc.status = "assembling"
                pc.save(update_fields=("status", "assembly_started"))
            order.status = "in_progress"
            order.save(update_fields=("status",))
            self._timeline(
                order, "in_progress", placed + timedelta(days=1),
                f"Assembly started for PC #{pcs[0].pk}.",
                from_status="placed", employee=employee,
            )
            for pc in pcs:
                accepted = pc.order_spec_line.get("accepted_substitution")
                if accepted:
                    self._apply_accepted_substitution(
                        pc, accepted, employee,
                        placed + timedelta(days=1, hours=4), components,
                    )
            previous_status = "in_progress"
        else:
            previous_status = "placed"

        if state == "on_hold":
            held_at = placed + timedelta(days=2)
            for pc in pcs:
                proposal_spec = pc.order_spec_line.get("pending_substitution")
                if not proposal_spec:
                    continue
                original = components[proposal_spec["original"]]
                replacement = components[proposal_spec["replacement"]]
                original_snapshot, replacement_snapshot, performance_snapshot = substitution_review_snapshot(
                    pc, original, replacement
                )
                proposal = SubstitutionProposal.objects.create(
                    purchased_pc=pc,
                    original_component=original,
                    replacement_component=replacement,
                    proposed_by=employee,
                    reason=proposal_spec["reason"],
                    spec_differences=proposal_spec["spec_differences"],
                    fps_note=proposal_spec["fps_note"],
                    status="pending",
                    previous_pc_status="assembling",
                    original_snapshot=original_snapshot,
                    replacement_snapshot=replacement_snapshot,
                    performance_snapshot=performance_snapshot,
                )
                SubstitutionProposal.objects.filter(pk=proposal.pk).update(proposed_at=held_at)
                pc.status = "on_hold"
                pc.hold_reason = "Waiting for customer approval of a component substitution."
                pc.save(update_fields=("status", "hold_reason"))
                self._timeline(
                    order, "in_progress", held_at,
                    f"PC #{pc.pk}: replacement proposal #{proposal.pk} sent to the customer.",
                    from_status="in_progress", employee=employee,
                )
            return

        if state == "quality_checking":
            for pc in pcs:
                pc.status = "quality_checking"
                pc.save(update_fields=("status",))
                self._record_quality_passes(pc, employee, quality_at, count=2)
            return

        for pc in pcs:
            pc.status = "quality_passed"
            pc.save(update_fields=("status",))
            self._record_quality_passes(pc, employee, quality_at, count=3)
            self._consume_pc_parts(pc, employee, quality_at + timedelta(hours=2))

        if state == "packing":
            order.status = "packing"
            order.save(update_fields=("status",))
            self._timeline(
                order, "packing", packing_at, "Order packing started.",
                from_status=previous_status, employee=employee,
            )
            return

        if state in ("dispatched", "delivered"):
            if state == "delivered":
                dispatch_time = placed + timedelta(days=4)
                packing_time = packing_at
            else:
                dispatch_time = placed + timedelta(days=3)
                packing_time = dispatch_time - timedelta(hours=8)
            self._timeline(
                order, "packing", packing_time, "Order packing started.",
                from_status=previous_status, employee=employee,
            )
            self._consume_accessories(order, employee, dispatch_time)
            shipment = Shipment.objects.create(
                order=order,
                status="dispatched",
                courier=spec.get("courier", ""),
                tracking_ref=spec.get("tracking", ""),
                dispatched_at=dispatch_time,
            )
            order.status = "dispatched"
            order.save(update_fields=("status",))
            self._timeline(
                order, "dispatched", dispatch_time, "Order dispatched.",
                from_status="packing", employee=employee,
            )
            if state == "delivered":
                shipment.status = "delivered"
                shipment.delivered_at = delivered_at
                if spec.get("confirmed_by") == "staff":
                    shipment.confirmed_by = f"staff:{employee.pk}"
                else:
                    shipment.confirmed_by = f"customer:{order.customer.user_id}"
                shipment.save(update_fields=("status", "delivered_at", "confirmed_by"))
                order.status = "delivered"
                order.save(update_fields=("status",))
                self._timeline(
                    order, "delivered", delivered_at, "Delivery confirmed.",
                    from_status="dispatched",
                    employee=employee if spec.get("confirmed_by") == "staff" else None,
                )
            return

    def _apply_accepted_substitution(self, pc, change, employee, when, components):
        original = components[change["original"]]
        replacement = components[change["replacement"]]
        original_snapshot, replacement_snapshot, performance_snapshot = substitution_review_snapshot(
            pc, original, replacement
        )
        proposal = SubstitutionProposal.objects.create(
            purchased_pc=pc,
            original_component=original,
            replacement_component=replacement,
            proposed_by=employee,
            reason=change["reason"],
            spec_differences=change["spec_differences"],
            fps_note=change["fps_note"],
            status="accepted",
            previous_pc_status="assembling",
            original_snapshot=original_snapshot,
            replacement_snapshot=replacement_snapshot,
            performance_snapshot=performance_snapshot,
            decided_at=when,
            decided_by=f"customer:{pc.order_line.order.customer.user_id}",
        )
        SubstitutionProposal.objects.filter(pk=proposal.pk).update(
            proposed_at=when - timedelta(hours=1)
        )
        reservation = pc.reservations.filter(
            component=original, status=Reservation.Status.RESERVED
        ).order_by("pk").first()
        if reservation is not None:
            if reservation.quantity == 1:
                reservation.status = Reservation.Status.RELEASED
                reservation.save(update_fields=("status",))
            else:
                reservation.quantity -= 1
                reservation.save(update_fields=("quantity",))
        Reservation.objects.create(component=replacement, purchased_pc=pc, quantity=1)
        self._timeline(
            pc.order_line.order, "in_progress", when,
            f"Customer accepted substitution #{proposal.pk} for PC #{pc.pk}; one replacement "
            f"unit reserved and the confirmed price unchanged.",
            from_status="in_progress",
        )

    def _record_quality_passes(self, pc, employee, when, count):
        checklist = ("installed_parts", "startup", "stability")
        for index, item in enumerate(checklist[:count]):
            check = QualityCheck.objects.create(
                purchased_pc=pc,
                employee=employee,
                stage="quality_checking",
                checklist_item=item,
                passed=True,
            )
            set_created(check, when + timedelta(hours=index))

    def _consume_pc_parts(self, pc, employee, when):
        for reservation in pc.reservations.filter(
            status=Reservation.Status.RESERVED, component__isnull=False
        ).select_related("component"):
            adjustment = StockAdjustment.objects.create(
                item_type="component",
                component=reservation.component,
                delta=-reservation.quantity,
                reason=f"PC #{pc.pk} passed quality checks; components installed.",
                created_by=employee,
            )
            set_created(adjustment, when)
            reservation.status = Reservation.Status.CONSUMED
            reservation.save(update_fields=("status",))

    def _consume_accessories(self, order, employee, when):
        for reservation in Reservation.objects.filter(
            order_line__order=order,
            status=Reservation.Status.RESERVED,
            accessory__isnull=False,
        ).select_related("accessory"):
            adjustment = StockAdjustment.objects.create(
                item_type="accessory",
                accessory=reservation.accessory,
                delta=-reservation.quantity,
                reason=f"Accessory order #{order.pk} dispatched.",
                created_by=employee,
            )
            set_created(adjustment, when)
            reservation.status = Reservation.Status.CONSUMED
            reservation.save(update_fields=("status",))

    def _timeline(self, order, to_status, when, note, from_status="", employee=None):
        event = OrderStatusHistory.objects.create(
            order=order,
            from_status=from_status,
            to_status=to_status,
            note=note,
            employee=employee,
        )
        set_created(event, when)

    # ---------------------------------------------------------- accepted changes
    def _ensure_reviews(self, order_index):
        for spec, order, pcs in order_index.values():
            review = spec.get("review")
            if not review or not pcs:
                continue
            rating, text = review
            for pc in pcs:
                if pc.status != "quality_passed" or pc.order_line.order.status != "delivered":
                    continue
                if Review.objects.filter(purchased_pc=pc).exists():
                    continue
                line = pc.order_line
                snapshot = {
                    "name": line.name,
                    "components": pc.approved_parts,
                    "source_preset_name": line.custom_snapshot.get(
                        "source_preset_name", line.name if line.configuration_id else ""
                    ),
                    "customised_from_preset": line.custom_snapshot.get(
                        "customised_from_preset", False
                    ),
                    "has_substitutions": pc.substitutions.filter(status="accepted").exists(),
                }
                saved = Review.objects.create(
                    purchased_pc=pc,
                    customer=order.customer,
                    rating=rating,
                    text=text,
                    build_snapshot=snapshot,
                )
                set_created(saved, order.placed_at + timedelta(days=7, hours=3))

    def _ensure_support(self, threads, customers, order_index, staff):
        for thread in threads:
            customer = customers[thread["customer"]]
            order = Order.objects.filter(checkout_reference=thread["order"]).first()
            if order is None:
                continue
            if SupportRequest.objects.filter(customer=customer, order=order).exists():
                continue
            support_request = SupportRequest.objects.create(
                customer=customer,
                order=order,
                status=thread["status"],
                resolution_note=thread.get("resolution", ""),
            )
            hour, minute = SUPPORT_TIMES.get(thread["order"], (10, 0))
            created = at(thread["created_days"], hour, minute)
            set_created(support_request, created)
            for sender_type, days_ago, staff_only, body in thread["messages"]:
                message = SupportMessage.objects.create(
                    request=support_request,
                    sender_type=sender_type,
                    employee=staff["priya"] if sender_type == "employee" else None,
                    is_staff_only=staff_only,
                    body=body,
                )
                when = created + timedelta(
                    days=max(0, thread["created_days"] - days_ago)
                )
                set_created(message, when)
