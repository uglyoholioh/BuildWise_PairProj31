# Repair pass and IS2108 review notes

Prepared 10 November 2026 from the supplied BuildWise handoff and the actual
checkout. Starting branch: `work`; starting commit: `e3d09e2`; no tracked or
untracked source changes were present. The original 66 tests passed before
edits. No commit, push, issue change, seed-account creation or update to the
working `db.sqlite3` was performed.

The handoff was used as review evidence and scope, not as independent authority
to publish or perform unrelated work. The current user explicitly requested
implementation. Its older guidance-only preference did not override that
request. No AGENTS.md, README, report, diagrams or original course materials
were available in this checkout. No new AGENTS.md or copied conventions were
added. Students should review these changes and record their own understanding
and corrections for the AI-assistance section of the report.

## Increment 1: authentication, navigation and checkout inputs

- `buildwise/settings.py`: `LOGIN_URL = "accounts:login"` lets `login_required`
  resolve the correct slashless customer URL, preserving `next`.
- `accounts/views.py`: staff login now uses `get_success_url()` and Django's
  validated `get_redirect_url()`. Direct login falls back to the named inventory
  dashboard. Unsafe external destinations cannot override it.
- `orders/forms.py`: delivery address is required and whitespace-only input is
  invalid at checkout. The Customer model still permits an incomplete profile.
  Invalid input does not create an order/reservation or clear the cart.
- `catalogue/urls.py`: `/` renders the existing preset list.
- `templates/support/staff_detail.html` and
  `templates/accounts/customer_detail.html`: staff order links go to an anchored
  order in the protected customer-history page. Customer ownership checks stay
  on the customer order route.

Review explanation: a named route supplies authentication navigation; form
validation supplies the checkout-specific requirement. Neither requires a new
customer model or weaker access checks.

## Increment 2: substitutions and quality checks

- `orders/allocations.py` contains the shared business rules:
  - `component_snapshot(component, quantity)` copies descriptive fields into a
    plain dictionary. Decimal price is stored as text for JSON serialization.
  - `approved_pc_parts(pc)` starts with the immutable order-line snapshot and
    replays this PC's accepted proposals in decision order. Every approval
    subtracts **one unit** and appends one replacement unit. Separate descriptions
    are retained even when a later proposal returns to an earlier component ID.
  - `expected_pc_quantities(pc)` sums those rows by component ID.
  - `reserved_pc_quantities(pc)` sums only RESERVED allocations via ORM `Sum`.
  - `substitution_issues(pc, original, replacement)` validates same-category,
    different-component, active/compatible complete-build and stock rules at
    both proposal and acceptance. All other required units must be reserved;
    the single replaced unit may be missing after a fault.
  - `purchased_pc_allocation_issues(pc)` compares actual reservations to the
    approved quantities, rather than the original purchase alone.
- `orders/forms.py`: original choices come from the approved specification, so
  a faulty unit removed from reservations remains selectable. The form explains
  the one-unit rule. Choice filtering is not the security boundary: the view
  revalidates under the transaction.
- `orders/models.py` and migration `0003`: proposal snapshots preserve original
  and replacement descriptions. `PurchasedPC.approved_parts` is a read-only
  property delegating to the shared derivation for template use.
- `orders/views.py`: pending approval prevents assembly/QC transitions even for
  direct POSTs. Acceptance releases one original reservation only if that unit
  is still reserved; otherwise it fills the missing faulty allocation. It
  reserves the replacement once and leaves the order price and shared purchase
  snapshot unchanged. Duplicate decisions cannot repeat the change.
- QC uses only checks recorded after the latest accepted substitution; a new
  component cannot inherit old passing results. All required allocations must
  exist before recording QC. Final success consumes reservations once.
- A declined queued PC releases its uninstalled allocations. Started parts stay
  RESERVED for inspection, so they do not become sellable stock. When all PCs
  are cancelled and there are no accessories, the order becomes cancelled and
  refunded. Otherwise remaining PCs/accessories can proceed to packing.
- `templates/orders/order_queue.html`: controls respect the same pending hold.

Review explanation: an approved specification is a customer decision; a
reservation is physical inventory. A failed part can disappear from physical
inventory while remaining in the specification until a replacement is approved.
Separating those meanings avoids inventing usable faulty stock. Per-PC decision
history also prevents one unit on a quantity-two line from changing its sibling.

## Increment 3: history and questionnaire

- `orders/views.py` takes accessory name/category snapshots at checkout.
  `templates/orders/order_detail.html`, `templates/orders/parts_snapshot.html`
  and the staff customer-history template show purchase-time specifications,
  approved current parts and per-PC substitution names rather than live names.
- `recommendations/forms.py`: `SupportedGamesField.clean()` rejects malformed
  IDs and integers outside SQLite's signed 64-bit range before ORM conversion.
  The parent ModelMultipleChoiceField still verifies existence and supported
  choices. The initial unsafe query is gone. One to three games are required;
  resolution/graphics combinations remain server-validated.
- `recommendations/views.py`: first submit validates game selection and budget,
  then renders settings/defaults without saving a questionnaire. Final submit
  validates every visible setting and saves the response and selected rows in
  one transaction. Adding a new game returns to settings review. A session
  draft retains edits across game removal/re-addition and is cleared on save.
- `templates/recommendations/questionnaire.html`: explicit update-settings and
  recommendation actions make the two steps usable without JavaScript. Search
  targets the rendered `.game-option` containers; hiding one never unchecks it.

Review explanation: newly introduced required fields must be shown to the user
before they can supply values. Missing keys for a new selection request the
settings step; an explicitly submitted blank setting remains a validation error.

## Increment 4: employee management and database races

- `accounts/forms.py`: EmployeeCreationForm reuses UserCreationForm's password
  validation and hashing; it creates an Employee profile with the selected
  application role. EmployeeUpdateForm exposes only role/active and blocks
  removal of the final manager whose User and Employee are both active.
- `accounts/views.py`: manager-only list/create/update actions use POST/CSRF,
  recheck authority inside the write transaction, synchronize deactivation with
  User.is_active, and record significant changes in existing StaffAction rows.
  Records are deactivated, never deleted; historical foreign keys survive.
- `buildwise/urls.py`, `templates/accounts/employee_list.html`,
  `templates/accounts/employee_form.html`, `templates/base.html`: replace the
  placeholder with the custom workflow. Customer registration does not expose
  staff fields. `catalogue/views.py` reuses the same active-manager predicate.
- `buildwise/settings.py`: SQLite `transaction_mode="IMMEDIATE"` reserves the
  database writer before transactional reads. Competing writers wait up to the
  configured five-second timeout. This is what serializes these transitions on
  SQLite; `select_for_update()` itself provides no SQLite row locks.
- `orders/models.py`, migration `0004` and `orders/views.py`: persist a unique
  checkout reference using the cart's existing server-generated first line UUID.
  A simultaneous repeat of that basket returns its existing order, before
  reserving any more stock. New baskets have new line UUIDs. This reference is
  an idempotency key, not an authentication token; lookup still checks customer.
  Older orders have NULL references. Server-created carts have line IDs.
- `buildwise/sqlite_test_settings.py` and `.gitignore`: a separate ignored test
  database exercises real file-backed SQLite locking without changing local data.

## API coverage: what can honestly be claimed

The handoff identifies Form/ModelForm, sessions, templates, ORM and class-based
views as course concepts (Lecture 7 and Lab 6 Part 1). Original teaching files
were not available here, so no claim is made that every exact API was taught.

Already used in this repository: UserCreationForm, authentication decorators,
LoginView/get_redirect_url, reverse, ModelMultipleChoiceField, transaction.atomic,
select_for_update, JSONField, ORM aggregation, UUIDs, StaffAction and migrations.
Their use here extends existing patterns; existing use does not prove course
coverage. `transaction.atomic()` rolls back dependent database writes on failure.
`get_success_url()` is the supported LoginView override for the final redirect;
`success_url` on its own did not set that behavior.

Additional techniques not verified as taught:

- `HttpResponseForbidden` returns HTTP 403 for an authenticated employee who
  lacks manager authority. It gives a direct permission failure without another
  custom decorator.
- A small ModelMultipleChoiceField subclass validates SQLite-sized IDs before
  invoking `super().clean()`. Catching errors only after form construction would
  leave the original unsafe-query defect.
- The `approved_parts` Python property exposes a computed list without storing a
  second mutable specification. JSON snapshots add two fields through migrations.
- SQLite's `IMMEDIATE` transaction setting supplies database-wide writer
  serialization. Ordinary deferred atomic blocks or Python-only locks do not
  provide this behavior across application processes.
- A nullable unique checkout-reference field and a transactional lookup make
  duplicate submissions idempotent. Clearing a session cart alone is insufficient
  when two requests already read the same cart.
- Tests use TransactionTestCase, ThreadPoolExecutor/Barrier, RequestFactory and
  StaticLiveServerTestCase/Playwright. These are verification tools, not new
  application architecture. Race tests call actual views with independent DB
  connections and in-memory request sessions to isolate the database transition.

## Verification and limits

Final verification: `python manage.py test --settings=buildwise.sqlite_test_settings --noinput`
passed **98 tests**, including the Chromium test and all four concurrency tests,
with no skips. Django system checks, `makemigrations --check --dry-run` and
`git diff --check` passed. The test count grew from the original 66 to 98.
Playwright was installed in the ignored local virtual environment; pinned
application requirements were unchanged.

Regression tests are in `accounts/test_repairs.py`, `orders/test_repairs.py`,
`orders/test_concurrency.py`, `recommendations/test_repairs.py` and
`recommendations/test_browser.py`; existing checkout/support tests were extended.
The old substitution fixture was completed with all seven mandatory categories;
its CPU assertion now filters by category rather than assuming a CPU-only PC.

Checks include ownership/role boundaries, invalid-address rollback, safe login
redirects, category/socket/inactive/missing-part rejection, availability changes,
one-unit quantities, repeated approvals, sibling isolation, catalogue edits,
fault removal with no spare, retesting/consumption, terminal cancellation,
remaining accessories, employee deactivation/last-manager protection, malformed
and unsupported games and a real Chromium questionnaire flow. An integrated
client flow covers guest cart → registration → mixed PC/accessory checkout →
assembly → three QC checks → packing → dispatch → customer receipt.

Concurrent duplicate checkout was reproduced (two orders from one basket) before
adding the reference. The final file-backed concurrency suite covers that case,
two buyers competing for the final accessory, cancellation racing assembly start,
and two managers demoting themselves concurrently. These bounded tests do not
prove arbitrary workload correctness or test HTTP session-store races. Long
writer contention can still exceed SQLite's timeout: checkout reports a retry;
other existing write views can surface a database-lock error. No cross-database
concurrency guarantee is claimed.

Historical descriptions cannot be reconstructed when no snapshot was recorded.
Legacy operational allocation fallback uses the current configuration/catalogue;
it is not purchase-time evidence. Proposal history without snapshots labels its
historical name as unavailable. Existing malformed/previously accepted bad
substitutions need manual data review; these migrations do not invent repairs.
Parts retained for inspection need a later explicit staff stock decision.

The temporary FPS heuristic, trained-model backlog, reviews, shortlists,
accessory recommendations and payment-failure simulation remain outside this
repair pass. The team's manually seeded demo acceptance is still separate from
automated fixture tests. New migrations were exercised on disposable test
DBs; run `python manage.py migrate` before using the updated local application.
