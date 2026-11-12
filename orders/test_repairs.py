from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Customer, Employee
from catalogue.models import Component, Accessory
from inventory.models import Reservation, StockAdjustment
from inventory.availability import available_component_quantity
from .allocations import component_snapshot, expected_pc_quantities, purchased_pc_allocation_issues
from .forms import SubstitutionProposalForm
from .models import Order, OrderLine, OrderStatusHistory, PurchasedPC, SubstitutionProposal, QualityCheck


class SubstitutionRepairTests(TestCase):
    def setUp(self):
        self.employee = Employee.objects.create(user=User.objects.create_user('staff'))
        self.customer = Customer.objects.create(user=User.objects.create_user('buyer'), name='Buyer', email='buyer@example.test')
        self.order = Order.objects.create(customer=self.customer, payment_status='paid', total_snapshot=1400,
                                          delivery_name='Buyer', delivery_address='Example road')
        specs = {'cpu': {'socket': 'AM5'}, 'motherboard': {'socket': 'AM5', 'memory_type': 'DDR5', 'form_factor': 'ATX'},
                 'memory': {'memory_type': 'DDR5'}, 'gpu': {}, 'storage': {}, 'power_supply': {}, 'case': {'form_factor': 'ATX'}}
        # Use model choices rather than assumptions about category values.
        categories = [Component.Category.CPU, Component.Category.MOTHERBOARD, Component.Category.MEMORY,
                      Component.Category.GPU, Component.Category.STORAGE, Component.Category.POWER_SUPPLY, Component.Category.CASE]
        self.parts = []
        for category, fields in zip(categories, specs.values()):
            part = self.new_part(category, category, **fields)
            self.parts.append(part)
        self.cpu = self.parts[0]
        self.line = OrderLine.objects.create(order=self.order, kind='build', name='Original build', quantity=2,
                                             unit_price_snapshot=700, custom_snapshot={'components': [component_snapshot(p) for p in self.parts]})
        self.pc = PurchasedPC.objects.create(order_line=self.line, unit_index=1)
        self.sibling = PurchasedPC.objects.create(order_line=self.line, unit_index=2)
        for pc in (self.pc, self.sibling):
            for part in self.parts:
                Reservation.objects.create(component=part, purchased_pc=pc, quantity=1)
        self.replacement = self.new_part(Component.Category.CPU, 'Replacement CPU', socket='AM5')
        self.client.force_login(self.employee.user)

    def new_part(self, category, model, **fields):
        part = Component.objects.create(category=category, brand='Example', model=model, price=100, **fields)
        StockAdjustment.objects.create(item_type='component', component=part, delta=5, reason='Test stock', created_by=self.employee)
        return part

    def propose(self, original=None, replacement=None):
        self.client.force_login(self.employee.user)
        return self.client.post(reverse('orders:propose_substitution', args=[self.pc.pk]), {
            'original_component': (original or self.cpu).pk,
            'replacement_component': (replacement or self.replacement).pk, 'reason': 'Replacement needed',
        })

    def decide(self, decision='accept'):
        self.client.force_login(self.customer.user)
        proposal = self.pc.substitutions.latest('pk')
        return self.client.post(reverse('orders:decide_substitution', args=[proposal.pk]), {'decision': decision})

    def test_rejects_category_socket_inactive_and_incomplete_build(self):
        for replacement in [self.parts[4], self.new_part(Component.Category.CPU, 'Wrong socket', socket='LGA'),
                            self.new_part(Component.Category.CPU, 'Inactive', socket='AM5', active=False)]:
            self.assertEqual(self.propose(replacement=replacement).status_code, 200)
            self.assertFalse(SubstitutionProposal.objects.exists())
        self.line.custom_snapshot['components'].pop()
        self.line.save()
        self.assertEqual(self.propose().status_code, 200)
        self.assertFalse(SubstitutionProposal.objects.exists())

    def test_acceptance_rechecks_active_socket_category_and_stock(self):
        self.propose()
        for field, value in [('active', False), ('socket', 'LGA'), ('category', Component.Category.STORAGE)]:
            old_value = getattr(self.replacement, field)
            setattr(self.replacement, field, value)
            self.replacement.save()
            self.decide()
            self.assertEqual(self.pc.substitutions.get().status, 'pending')
            setattr(self.replacement, field, old_value)
            self.replacement.save()
        StockAdjustment.objects.create(item_type='component', component=self.replacement, delta=-5,
                                       reason='Stock changed', created_by=self.employee)
        self.decide()
        self.assertEqual(self.pc.substitutions.get().status, 'pending')
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'on_hold')

    def test_pending_hold_blocks_start_and_qc_even_if_state_is_changed(self):
        self.propose()
        response = self.client.get(reverse('orders:order_queue'))
        self.assertNotContains(response, reverse('orders:start_order', args=[self.pc.pk]))
        self.client.post(reverse('orders:start_order', args=[self.pc.pk]))
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'on_hold')
        for status, route in [('assembling', 'start_quality_check'), ('quality_checking', 'record_quality_check')]:
            self.pc.status = status
            self.pc.save()
            self.client.post(reverse('orders:' + route, args=[self.pc.pk]), {'checklist_item': 'startup', 'passed': 'yes'})
            self.pc.refresh_from_db()
            self.assertEqual(self.pc.status, status)
        self.assertFalse(QualityCheck.objects.exists())

    def test_successive_approvals_preserve_snapshot_sibling_and_duplicate_safety(self):
        original = self.line.custom_snapshot
        self.propose()
        self.decide()
        self.decide()
        self.assertEqual(expected_pc_quantities(self.pc).get(self.replacement.pk), 1)
        self.assertEqual(purchased_pc_allocation_issues(self.pc), [])
        next_cpu = self.new_part(Component.Category.CPU, 'Third CPU', socket='AM5')
        self.propose(original=self.replacement, replacement=next_cpu)
        self.decide()
        self.assertEqual(expected_pc_quantities(self.pc).get(next_cpu.pk), 1)
        self.assertNotIn(self.cpu.pk, expected_pc_quantities(self.pc))
        self.assertNotIn(next_cpu.pk, expected_pc_quantities(self.sibling))
        self.line.refresh_from_db()
        self.assertEqual(self.line.custom_snapshot, original)
        self.assertEqual(self.line.unit_price_snapshot, Decimal('700'))
        self.client.force_login(self.employee.user)
        self.client.post(reverse('orders:start_order', args=[self.pc.pk]))
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'assembling')

    def test_returning_to_original_component_keeps_new_approval_description(self):
        self.propose()
        self.decide()
        self.cpu.model = "Updated original CPU"
        self.cpu.save()
        self.propose(original=self.replacement, replacement=self.cpu)
        self.decide()
        self.assertEqual(self.pc.approved_parts[-1]["model"], "Updated original CPU")
        self.line.refresh_from_db()
        self.assertNotEqual(self.line.custom_snapshot["components"][0]["model"], "Updated original CPU")
        self.assertEqual(purchased_pc_allocation_issues(self.pc), [])

    def test_quality_results_before_approval_do_not_pass_replacement(self):
        self.client.post(reverse("orders:start_order", args=[self.pc.pk]))
        self.client.post(reverse("orders:start_quality_check", args=[self.pc.pk]))
        for check in ("installed_parts", "startup"):
            self.client.post(reverse("orders:record_quality_check", args=[self.pc.pk]), {"checklist_item": check, "passed": "yes"})
        self.propose()
        self.decide()
        self.client.force_login(self.employee.user)
        self.client.post(reverse("orders:record_quality_check", args=[self.pc.pk]), {"checklist_item": "stability", "passed": "yes"})
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, "quality_checking")
        self.assertFalse(Reservation.objects.filter(purchased_pc=self.pc, status="consumed").exists())

    def test_one_unit_substitution_preserves_remaining_quantity(self):
        self.line.custom_snapshot['components'][0]['quantity'] = 2
        self.line.save()
        Reservation.objects.filter(purchased_pc=self.pc, component=self.cpu).update(quantity=2)
        self.propose()
        self.decide()
        self.assertEqual(expected_pc_quantities(self.pc)[self.cpu.pk], 1)
        self.assertEqual(expected_pc_quantities(self.pc)[self.replacement.pk], 1)
        self.assertEqual(purchased_pc_allocation_issues(self.pc), [])

    def test_fault_no_spare_substitute_retest_and_consume_once(self):
        self.client.post(reverse('orders:start_order', args=[self.pc.pk]))
        self.client.post(reverse('orders:start_quality_check', args=[self.pc.pk]))
        StockAdjustment.objects.create(item_type='component', component=self.cpu, delta=-3,
                                       reason='No unreserved spare', created_by=self.employee)
        self.client.post(reverse('orders:record_quality_check', args=[self.pc.pk]), {
            'checklist_item': 'startup', 'passed': 'no', 'faulty_component': self.cpu.pk, 'note': 'Faulty CPU',
        })
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'on_hold')
        self.assertEqual(available_component_quantity(self.cpu), 0)
        self.assertIn(self.cpu, SubstitutionProposalForm(purchased_pc=self.pc).fields['original_component'].queryset)
        self.assertEqual(self.propose().status_code, 302)
        self.decide()
        self.assertEqual(purchased_pc_allocation_issues(self.pc), [])
        self.client.force_login(self.employee.user)
        for check in ('installed_parts', 'startup', 'stability', 'stability'):
            self.client.post(reverse('orders:record_quality_check', args=[self.pc.pk]), {'checklist_item': check, 'passed': 'yes'})
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, 'quality_passed')
        self.assertEqual(Reservation.objects.filter(purchased_pc=self.pc, status='consumed').count(), 7)
        self.assertEqual(StockAdjustment.objects.filter(component=self.replacement, delta=-1).count(), 1)
        self.assertEqual(StockAdjustment.objects.filter(component=self.cpu, delta=-1).count(), 1)

    def test_decline_all_pcs_refunds_order_and_releases_only_unstarted_parts(self):
        self.propose()
        self.decide('decline')
        self.assertFalse(Reservation.objects.filter(purchased_pc=self.pc, status='reserved').exists())
        self.pc = self.sibling
        self.propose()
        self.decide('decline')
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ('cancelled', 'refunded'))

    def test_accessories_can_pack_after_last_pc_declined(self):
        self.sibling.status = 'cancelled'
        self.sibling.save()
        accessory = Accessory.objects.create(name='Mouse', category='Mouse', price=20)
        line = OrderLine.objects.create(order=self.order, kind='accessory', name='Mouse', quantity=1, unit_price_snapshot=20, accessory=accessory)
        Reservation.objects.create(accessory=accessory, order_line=line, quantity=1)
        self.propose()
        self.decide('decline')
        self.client.force_login(self.employee.user)
        self.client.post(reverse('orders:start_packing', args=[self.order.pk]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'packing')

    def test_history_names_survive_catalogue_edits_and_other_customer_cannot_decide(self):
        self.propose()
        other = User.objects.create_user('other')
        Customer.objects.create(user=other, name='Other', email='other@example.test')
        self.client.force_login(other)
        proposal = self.pc.substitutions.get()
        self.assertEqual(self.client.post(reverse('orders:decide_substitution', args=[proposal.pk]), {'decision': 'accept'}).status_code, 404)
        self.assertEqual(self.client.get(reverse('orders:order_detail', args=[self.order.pk])).status_code, 404)
        self.decide()
        Component.objects.filter(pk__in=[self.cpu.pk, self.replacement.pk]).update(model='Renamed catalogue model')
        response = self.client.get(reverse('orders:order_detail', args=[self.order.pk]))
        self.assertContains(response, 'Replacement CPU')
        self.assertNotContains(response, 'Renamed catalogue model')
        self.client.force_login(self.employee.user)
        response = self.client.get(reverse('accounts:customer_detail', args=[self.customer.pk]))
        self.assertContains(response, 'Replacement CPU')
        self.assertNotContains(response, 'Renamed catalogue model')


    def create_gaming_preferences(self, resolution="1920x1080"):
        from recommendations.models import Game, GameSettingSupport, QuestionnaireResponse, QuestionnaireGameSetting
        game = Game.objects.create(name="Test Game", slug="cyberpunk-2077")
        GameSettingSupport.objects.create(game=game, resolution="1920x1080", graphics_preset="high")
        response = QuestionnaireResponse.objects.create(customer=self.customer, perf_level="balanced", max_spend=1500)
        QuestionnaireGameSetting.objects.create(response=response, game=game, fps_goal=90,
                                                resolution=resolution, graphics_preset="high")
        gpu = self.parts[3]
        gpu.ml_tier = "high"
        gpu.model = "GeForce RTX 4060 8GB"
        gpu.save()
        self.cpu.model = "Ryzen 5 7500F"
        self.cpu.save()
        self.line.custom_snapshot["components"][0]["model"] = self.cpu.model
        self.line.custom_snapshot["components"][3]["model"] = gpu.model
        self.line.custom_snapshot["components"][3]["ml_tier"] = "high"
        self.line.save()
        replacement = self.new_part(Component.Category.GPU, "GeForce RTX 4070 12GB", ml_tier="enthusiast",
                                    specs={"VRAM (GB)": 16, "Idle fan speed": 0})
        return response, replacement

    def test_customer_sees_saved_specifications_prices_and_trained_fps(self):
        questionnaire, replacement = self.create_gaming_preferences()
        self.propose(original=self.parts[3], replacement=replacement)
        proposal = self.pc.substitutions.get()
        self.assertEqual(proposal.spec_differences, "")
        game = proposal.performance_snapshot["games"][0]
        self.assertEqual((game["original_fps"], game["replacement_fps"]), (66, 103))
        self.assertTrue(game["meets_target"])
        self.client.force_login(self.customer.user)
        page = self.client.get(reverse("orders:order_detail", args=[self.order.pk]))
        for text in ("Original component", "Proposed component", "VRAM (GB)", "16", "Idle fan speed",
                     "Changed", "Test Game", "90 FPS", "66 FPS (estimate)", "103 FPS (estimate)",
                     "Random Forest regression", "Confirmed PC price", "$700.00", "$1400.00",
                     "Declining cancels only PC"):
            self.assertContains(page, text)
        self.assertContains(page, "<td>0</td>", html=True)

    def test_performance_history_survives_game_preferences_and_catalogue_changes(self):
        questionnaire, replacement = self.create_gaming_preferences()
        self.propose(original=self.parts[3], replacement=replacement)
        proposal = self.pc.substitutions.get()
        snapshot = proposal.performance_snapshot
        self.decide()
        replacement.ml_tier = "entry"
        replacement.model = "Later name"
        replacement.save()
        setting = questionnaire.game_settings.get()
        setting.fps_goal = 300
        setting.save()
        setting.game.name = "Later game name"
        setting.game.save()
        page = self.client.get(reverse("orders:order_detail", args=[self.order.pk]))
        self.assertContains(page, "Test Game")
        self.assertContains(page, "103 FPS (estimate)")
        self.assertNotContains(page, "Later game name")
        self.assertNotContains(page, "Later name")
        proposal.refresh_from_db()
        self.assertEqual(proposal.performance_snapshot, snapshot)
        self.assertEqual(proposal.status, "accepted")
        self.assertContains(page, "Your decision was recorded")
        self.assertEqual(OrderStatusHistory.objects.filter(note__contains="Customer accepted substitution").count(), 1)

    def test_missing_preferences_and_unsupported_estimates_are_explicit(self):
        self.propose()
        self.client.force_login(self.customer.user)
        page = self.client.get(reverse("orders:order_detail", args=[self.order.pk]))
        self.assertContains(page, "No saved gaming preferences")
        self.decide()
        questionnaire, replacement = self.create_gaming_preferences(resolution="unsupported-resolution")
        self.propose(original=self.parts[3], replacement=replacement)
        proposal = self.pc.substitutions.latest("pk")
        game = proposal.performance_snapshot["games"][0]
        self.assertIsNone(game["original_fps"])
        self.assertIsNone(game["replacement_fps"])
        self.client.force_login(self.customer.user)
        page = self.client.get(reverse("orders:order_detail", args=[self.order.pk]))
        self.assertContains(page, "Unavailable for this build or these settings")

    def test_unknown_gpu_model_does_not_invent_revised_estimate(self):
        questionnaire, replacement = self.create_gaming_preferences()
        replacement.model = "Unknown GPU"
        replacement.save()
        self.propose(original=self.parts[3], replacement=replacement)
        game = self.pc.substitutions.get().performance_snapshot["games"][0]
        self.assertEqual(game["original_fps"], 66)
        self.assertIsNone(game["replacement_fps"])
        self.assertFalse(game["meets_target"])

    def test_changed_offer_requires_new_decision_and_keeps_pending_hold(self):
        self.propose()
        old = self.pc.substitutions.get()
        old.fps_note = "Old staff performance note"
        old.save()
        self.replacement.specs = {"Clock": "Changed specification"}
        self.replacement.save()
        self.decide()
        old.refresh_from_db()
        self.pc.refresh_from_db()
        updated = self.pc.substitutions.get(status="pending")
        self.assertEqual(old.status, "superseded")
        self.assertIsNone(old.decided_at)
        self.assertEqual(self.pc.status, "on_hold")
        self.assertNotEqual(old.pk, updated.pk)
        self.assertEqual(updated.replacement_snapshot["specs"], self.replacement.specs)
        self.assertEqual(updated.fps_note, "")
        self.assertEqual(updated.previous_pc_status, "queued")
        self.assertFalse(Reservation.objects.filter(purchased_pc=self.pc, component=self.replacement).exists())
        self.client.post(reverse("orders:decide_substitution", args=[old.pk]), {"decision": "accept"})
        self.assertEqual(self.pc.substitutions.count(), 2)
        self.client.force_login(self.employee.user)
        self.client.post(reverse("orders:start_order", args=[self.pc.pk]))
        self.pc.refresh_from_db()
        self.assertEqual(self.pc.status, "on_hold")
        self.decide()
        self.decide()
        updated.refresh_from_db()
        self.assertEqual(updated.status, "accepted")
        self.assertEqual(Reservation.objects.filter(purchased_pc=self.pc, component=self.replacement, status="reserved").count(), 1)
        self.assertEqual(expected_pc_quantities(self.sibling)[self.cpu.pk], 1)

    def test_price_change_alone_does_not_change_offer_or_order_total(self):
        self.propose()
        proposal = self.pc.substitutions.get()
        quoted_price = proposal.replacement_snapshot["price"]
        self.replacement.price = 999
        self.replacement.save()
        self.decide()
        proposal.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(proposal.status, "accepted")
        self.assertEqual(proposal.replacement_snapshot["price"], quoted_price)
        self.assertEqual(self.order.total_snapshot, Decimal("1400.00"))
        self.assertEqual(self.pc.substitutions.count(), 1)

    def test_legacy_proposal_stays_readable_and_requires_fresh_review(self):
        self.propose()
        proposal = self.pc.substitutions.get()
        proposal.original_snapshot = {}
        proposal.replacement_snapshot = {}
        proposal.performance_snapshot = {}
        proposal.save()
        self.client.force_login(self.customer.user)
        page = self.client.get(reverse("orders:order_detail", args=[self.order.pk]))
        self.assertContains(page, "historical description unavailable")
        self.assertContains(page, "No performance estimates were recorded")
        self.decide()
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "superseded")
        self.assertEqual(self.pc.substitutions.filter(status="pending").count(), 1)

    def test_cancelled_order_does_not_show_decision_controls(self):
        self.propose()
        self.client.force_login(self.customer.user)
        self.client.post(reverse("orders:order_cancel", args=[self.order.pk]))
        page = self.client.get(reverse("orders:order_detail", args=[self.order.pk]))
        self.assertNotContains(page, "Accept replacement")
        self.assertNotContains(page, "Decline and refund this PC")
        self.decide()
        self.assertFalse(self.pc.substitutions.filter(status="accepted").exists())

    def test_declining_started_pc_preserves_sibling_and_records_refund_once(self):
        self.client.post(reverse("orders:start_order", args=[self.pc.pk]))
        self.propose()
        self.decide("decline")
        proposal = self.pc.substitutions.get()
        decided_at = proposal.decided_at
        self.decide("decline")
        self.pc.refresh_from_db()
        self.sibling.refresh_from_db()
        self.order.refresh_from_db()
        proposal.refresh_from_db()
        self.assertEqual(self.pc.status, "cancelled")
        self.assertEqual(self.pc.refund_amount, Decimal("700.00"))
        self.assertEqual(proposal.decided_at, decided_at)
        self.assertEqual(proposal.decided_by, f"customer:{self.customer.user_id}")
        self.assertEqual(self.sibling.status, "queued")
        self.assertEqual(self.order.status, "in_progress")
        self.assertEqual(Reservation.objects.filter(purchased_pc=self.pc, status="reserved").count(), 7)
        self.client.force_login(self.employee.user)
        self.client.post(reverse("orders:start_order", args=[self.sibling.pk]))
        self.sibling.refresh_from_db()
        self.assertEqual(self.sibling.status, "assembling")

    def test_decision_requires_post_and_csrf_token(self):
        from django.test import Client
        self.propose()
        proposal = self.pc.substitutions.get()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.customer.user)
        url = reverse("orders:decide_substitution", args=[proposal.pk])
        self.assertEqual(client.get(url).status_code, 405)
        self.assertEqual(client.post(url, {"decision": "accept"}).status_code, 403)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, "pending")


class IntegratedPurchaseTests(TestCase):
    def setUp(self):
        from . import tests as checkout_tests
        checkout_tests.CheckoutTests.setUp(self)

    def test_guest_registration_mixed_cart_checkout_and_delivery(self):
        accessory = Accessory.objects.create(name='Original mouse', category='Mouse', price=20)
        StockAdjustment.objects.create(item_type='accessory', accessory=accessory, delta=2,
                                       reason='Test stock', created_by=self.employee)
        self.client.post(reverse('orders:cart_add'), {'kind': 'preset', 'item_id': self.configuration.pk})
        self.client.post(reverse('orders:cart_add'), {'kind': 'accessory', 'item_id': accessory.pk})
        response = self.client.post(reverse('accounts:register'), {
            'username': 'guestbuyer', 'name': 'Guest Buyer', 'email': 'guest@example.test',
            'password1': 'OceanFixture!Pass482', 'password2': 'OceanFixture!Pass482', 'next': '/checkout',
        })
        self.assertRedirects(response, reverse('orders:checkout'))
        self.assertEqual(len(self.client.session['cart']), 2)
        buyer = User.objects.get(username='guestbuyer')
        checkout = self.client.get(reverse('orders:checkout'))
        response = self.client.post(reverse('orders:checkout'), {
            'name': 'Guest Buyer', 'delivery_address': 'Test road',
            'quote': checkout.context['form']['quote'].value(),
        })
        order = Order.objects.get()
        self.assertRedirects(response, reverse('orders:order_detail', args=[order.pk]))
        accessory.name = 'Renamed mouse'
        accessory.category = 'Renamed category'
        accessory.save()
        page = self.client.get(reverse('orders:order_detail', args=[order.pk]))
        self.assertContains(page, 'Original mouse')
        self.assertNotContains(page, 'Renamed')
        pc = PurchasedPC.objects.get()
        self.client.force_login(self.employee.user)
        self.client.post(reverse('orders:start_order', args=[pc.pk]))
        self.client.post(reverse('orders:start_quality_check', args=[pc.pk]))
        for check in ('installed_parts', 'startup', 'stability'):
            self.client.post(reverse('orders:record_quality_check', args=[pc.pk]), {'checklist_item': check, 'passed': 'yes'})
        pc.refresh_from_db()
        self.assertEqual(pc.status, 'quality_passed')
        self.client.post(reverse('orders:start_packing', args=[order.pk]))
        self.client.post(reverse('orders:dispatch_order', args=[order.pk]), {'courier': 'Demo courier'})
        order.refresh_from_db()
        self.assertEqual(order.status, 'dispatched')
        self.client.force_login(buyer)
        self.client.post(reverse('orders:confirm_receipt', args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.status, 'delivered')
        self.assertEqual(Reservation.objects.filter(status='consumed').count(), 8)
