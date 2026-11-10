# BuildWise QA plan: automated checks and manual review

Run the automated checks first. Use the manual cases below for the human-visible experience and exploratory review; they complement, rather than replace, the logic tests. Do not use production/customer data. Mark each manual case **Pass**, **Fail**, or **Blocked**, and capture the URL, account role, steps, and screenshot for every issue.

## Automated checks — run first

These use isolated Django test databases and do not seed or modify the development catalogue. The project virtualenv now has Playwright and its matching Chromium browser installed. Run from the repository root:

```sh
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python manage.py test --settings=buildwise.sqlite_test_settings --noinput
```

The last command includes file-backed SQLite concurrency tests and six real-browser flows. Its test DB is the reserved `test_buildwise.sqlite3` described in `README.md`; keep real data out of that filename. The local `.venv/bin/chromium` launcher is machine-specific and ignored by Git. On another machine, install Playwright and Chromium, then provide a `chromium` executable on `PATH` as described in `README.md`.

| Automated area | Existing coverage |
|---|---|
| Customer/staff sign-in, roles, employee lifecycle, ownership and redirects | `accounts/tests.py`, `accounts/test_repairs.py` |
| Catalogue forms, compatibility, preset lifecycle/browse, accessory management/browse | `catalogue/tests.py` |
| Stock availability, reservations and adjustments | `inventory/tests.py` |
| Cart, checkout validation/retry, order lifecycle, quality checks, dispatch and substitutions | `orders/tests.py`, `orders/test_repairs.py` |
| Concurrent checkout, cancellation/start and manager changes | `orders/test_concurrency.py` |
| Questionnaire, recommendations, comparisons, shortlist and accessory matching | `recommendations/tests.py`, `recommendations/test_repairs.py`, `recommendations/test_shortlist.py`, `recommendations/test_accessories.py` |
| Support privacy/conversations and reviews | `support/tests.py`, `support/test_reviews.py` |
| Concurrent review submission | `support/test_concurrency.py` |
| Real-browser flows: questionnaire, shortlist, accessory suggestions, checkout retry, substitutions, reviews | `recommendations/test_browser.py`, `orders/test_browser.py`, `support/test_browser.py` |

**Current run.** Django system and migration checks passed. All 166 tests passed in 34 seconds, including the six browser flows and file-backed SQLite concurrency checks.

## Before starting

1. Follow the local setup in `README.md`, migrate the database, and start the server.
2. Create/link a manager as described in `README.md`. Sign in and create a separate employee account. Keep customer, employee, manager, and (if available) inactive-employee accounts distinct. Do not reuse passwords.
3. Load the demo catalogue with `python manage.py seed_demo_catalogue`. In staff pages, review the draft records, set the shared assembly fee, add stock for the demo parts/accessories, and publish at least two complete compatible presets. Keep one draft and one withdrawn preset for visibility checks. Do not publish data you have not checked.
4. Have two customer accounts available (A and B). Customer A should have a complete cartable build; Customer B is used for ownership/privacy checks. You will need to place orders and progress at least one PC order to delivery, one accessory-only order, and one order that can be held for a substitution. This changes the local demo database; note or screenshot initial stock before starting.
5. Use a current desktop browser. Keep a notes file open. For responsive checks, use browser device emulation at 375px and 768px widths.

**Data caveat:** the demo seed intentionally leaves presets as drafts and does not create users, stock, or an assembly fee. Until the setup above is done, empty storefront/recommendation states may be expected rather than defects.

## Manual review — visual and interaction pass

Most underlying validation and state transitions in A–E already have automated assertions above. In these cases, focus on what automation cannot judge well: whether the flow is understandable, feedback is placed clearly, data reads consistently, and the rendered page behaves naturally. Avoid repeatedly staging race conditions or exhaustively retrying every invalid value; those belong to the automated suite.

### A. Entry, navigation, and customer account (about 20 min)

| ID | Steps | Expected result | Status / notes |
|---|---|---|---|
| A01 | Open `/` signed out. Use the navbar links, logo, Shop/Build tools menus, and browser back/forward. | Storefront loads; links have sensible labels and destinations; no dead ends or broken styling. | |
| A02 | Open `/staff/login`, then compare its header/navigation with the customer storefront. | Staff sign-in has staff-appropriate navigation, not customer purchase links. Invalid username/password gives a generic failure and does not reveal which value was wrong. | |
| A03 | Register a fresh customer at `/accounts/register`; try a duplicate email with different letter casing. | Valid registration signs in and returns to the intended action; duplicate email is rejected clearly; customer cannot choose staff privileges. | |
| A04 | Sign out; sign in at `/accounts/login`; change contact/delivery details and password at `/accounts/profile`; sign out and sign back in with the new password. | Session and intended destination are preserved; saved details persist; new password works and old password does not. | |
| A05 | As guest, begin a build/add an item to cart, then choose an action requiring sign-in. Register/sign in and continue. | Current build/cart is retained and the user returns to the requested action without losing selections. | |
| A06 | As guest and customer, visit a protected staff URL such as `/staff/queue`; try employee credentials at customer login and customer credentials at staff login. | Guests are routed to the correct sign-in page; customer cannot enter staff pages; employee is authenticated only through staff sign-in. | |

### B. Storefront, presets, and recommendations (about 35 min)

| ID | Steps | Expected result | Status / notes |
|---|---|---|---|
| B01 | Browse `/` and `/presets` with published, draft, withdrawn, in-stock, and out-of-stock examples. | Published presets display useful name, image (if present), price and availability. Draft/withdrawn presets are hidden. Out-of-stock preset remains viewable but cannot be purchased. | |
| B02 | Apply minimum/maximum price, component context tier, and availability filters separately and together; clear filters. | Each filter narrows results correctly; combined filters compose; empty results have a helpful message; layout remains usable. | |
| B03 | Open a preset detail page. Inspect component list, quantities/specifications, fee, total price, stock, image, and review area. | Details and arithmetic agree with staff records; missing gaming preferences invite questionnaire; unsupported FPS is clearly labeled unavailable, not represented as a promise. | |
| B04 | Open `/questionnaire`; search/select games, keep visible defaults, choose supported per-game settings and budget, then submit. Try more than three games and unsupported settings. | Search and selections are understandable; defaults are visible; per-game controls only offer supported choices; valid inputs save; invalid choices produce field-level feedback without a server error. | |
| B05 | View `/recommendations` for a supported profile. Repeat with a very low budget or a profile with no eligible builds. | Predictions are labeled estimates and compared with targets; qualifying profiles/trade-offs and above-budget extra cost are clear; no-result state explains next steps. | |
| B06 | Add two or three presets/recommendations to comparison; try a fourth and remove one. Compare with and without questionnaire preferences. | At most three builds compare; prices/specs align; predictions use the same targets and are labeled; incomplete/incompatible state is explained; removal works. | |
| B07 | Browse `/accessories`; search by name and filter category. Inspect in-stock and out-of-stock products and images. | Only active accessories appear; search/filter and stock indicators are accurate; out-of-stock item cannot be added; image absence does not break layout. | |
| B08 | Open a preset/build with accessory suggestions where purchase data supports them; then check a cold-start account/build if available. | Relevant in-stock suggestions show current prices and add-to-cart works; suggestions are absent when evidence is insufficient, not filled with arbitrary products. | |

### C. Configure, shortlist, cart, and checkout (about 40 min)

| ID | Steps | Expected result | Status / notes |
|---|---|---|---|
| C01 | Start configure from a preset, from a recommendation, and from blank (`/configure`). Change one part in each case. | Existing choices are prefilled when applicable; categories and selected parts are clear; compatible options are easy to find; changing choices preserves unrelated selections. | |
| C02 | Select a known incompatible CPU/motherboard or RAM/motherboard combination; inspect conflict explanations and quote. | Conflict is identified with the affected parts/reason; incomplete or incompatible configuration cannot be added to cart; no misleading complete price/FPS is shown. | |
| C03 | Finish a valid build, inspect itemised quote, refresh, and navigate away/back. Try add-to-cart. | Quote equals parts × quantities plus one shared assembly fee; session draft persists; valid available configuration can be added once. | |
| C04 | As guest, save a build; sign in and explicitly save it with a name. Save another build, reopen one, compare up to three, remove one. | Guest draft is not silently persisted as a saved shortlist; named saved copies remain distinct; reopen refreshes price/stock/compatibility; remove affects only selected item and current draft remains. | |
| C05 | Add a preset/custom PC and an accessory to cart. Change quantity, try 0 and >20, remove a line, then add it again. | Mixed cart totals are correct; valid quantities update; invalid quantities are rejected clearly; removal and re-add work without duplicate/phantom lines. | |
| C06 | Proceed to checkout while signed out, then sign in. Submit with missing address; correct it and check “simulate payment decline”; retry with decline unchecked. | Sign-in preserves cart; invalid address does not create order; simulated decline explains failure, preserves cart/details, and creates no order/reservation; retry succeeds once. | |
| C07 | On a separate attempt, change an item price or make a cart item unavailable after adding it, then checkout. | Changed price is highlighted and requires explicit review; stale stock/availability is rechecked; failure preserves cart and does not create a partial order or reservation. | |
| C08 | Review successful order detail and its purchased PC/accessory snapshots. Change catalogue values afterward and revisit order. | Confirmed order total and purchased specifications remain as ordered, rather than silently changing with current catalogue data. | |

### D. Order tracking, substitution, support, and reviews (about 45 min)

| ID | Steps | Expected result | Status / notes |
|---|---|---|---|
| D01 | As Customer A, open `/orders`; as Customer B, open the same route and try a guessed Customer A order URL. | Each customer sees only their own orders; guessed IDs do not disclose another customer's order. | |
| D02 | Inspect order detail from paid through assembly/quality check to dispatch and delivery; follow courier/tracking links if present. | Per-PC progress and shared shipment timeline are coherent; dispatch/courier/tracking data are readable; no status skips or contradictory labels. | |
| D03 | Confirm receipt as customer after dispatch, then repeat; have staff confirm a separate delivered order if possible. | Confirmation is unavailable before dispatch; valid confirmation marks the same shipment/order delivered and records actor/time; repeating does not change the original result. | |
| D04 | Cancel a paid order before work starts; then try cancellation after assembly or packing begins. | Eligible cancellation explains refund and releases appropriate reservations; after cutoff it is blocked with a clear next step; status/stock do not become inconsistent. | |
| D05 | Open a PC order as staff and propose an active compatible replacement with a reason; inspect customer view as owner and as another customer. | Affected PC is held pending decision; original/replacement, differences, price and supported estimate information are clear; only order owner can decide. | |
| D06 | Accept a proposal, then test a separate proposal decline. Where feasible, make replacement unavailable between offer and acceptance. | Acceptance rechecks availability/compatibility, leaves price unchanged and updates only that PC; stale offer remains on hold; decline/refund affects only selected PC and preserves siblings/accessories/history. | |
| D07 | As Customer A create a support request linked to an order; reply as staff with a public reply and a staff-only note; close with a resolution. | Conversation is linked correctly; customer sees public messages/status/resolution but never private note; staff actor/time are apparent; closed request cannot receive customer replies. | |
| D08 | Try opening Customer A's support thread while signed in as Customer B; as staff filter requests and navigate from customer/order history. | Cross-customer access is denied; staff filters and links lead to the correct customer/order context. | |
| D09 | Submit a review for a delivered PC, inspect public reviews and related preset page; try duplicate review and review of an undelivered/cancelled PC. | Only purchaser may submit one review per eligible PC; required rating/feedback validation works; public review shows purchased specs and escapes markup; private data is not exposed; related preset/custom-origin label is sensible. | |

### E. Staff catalogue, inventory, and order operations (about 45 min)

| ID | Steps | Expected result | Status / notes |
|---|---|---|---|
| E01 | As employee, visit staff component list; create a component with valid category specifications, edit it, then deactivate it. Try invalid/missing required specs and zero/negative price. | CRUD works; validation identifies missing/invalid fields; deactivated component cannot be newly configured/sold; existing order snapshot remains unchanged. | |
| E02 | Add an explicit incompatibility pair/reason; inspect customer configurator and attempt to publish/checkout affected build; remove the rule. | Same compatibility result/reason is used consistently; affected presets are revalidated; removal restores valid choices where appropriate. | |
| E03 | Create a preset with quantities, description and image; attempt publish while incomplete/incompatible, then complete and publish; withdraw/edit it. | Invalid preset cannot publish; valid one shows customer-side price/availability; withdrawal hides it from browse but keeps relevant history intact. | |
| E04 | As employee attempt changing shared assembly fee; as manager change it. Revisit a prior order and create a new quote. | Only manager can change shared fee; new quote uses current fee; old confirmed order total stays unchanged; negative fee rejected. | |
| E05 | Create/edit/deactivate accessory; try invalid price; verify previous order lines. | Active product appears in browse; inactive item is no longer for sale; invalid price rejected; historical purchase remains readable. | |
| E06 | Use inventory screen to receive stock and make a reduction. Try a reduction exceeding unreserved available quantity; adjust both component and accessory stock. | Physical/reserved/available values reconcile; adjustment reason, actor and time are visible; invalid reduction is blocked; affected preset availability updates. | |
| E07 | As employee inspect `/staff/queue`; start a paid PC and an accessory-only order. Attempt to start an unpaid order if one can be staged safely. | Queue shows payment, delivery, confirmed prices and allocations; paid PC enters assembly; accessory-only goes to packing without assembly; unpaid work cannot start. | |
| E08 | Record required quality checks, one failed check and corrective/retest path if safely staged; then pass all checks. | Checks are per PC with actor/time/result/reason; failed faulty part leaves usable allocation and is replaced/retested where possible; successful completion consumes allocated component stock once. | |
| E09 | Pack/dispatch a completed mixed order and an accessory-only order. Repeat a dispatch/delivery action if UI permits. | Shipment waits until all remaining PCs pass and accessories are packed; accessories consume reserved stock once at dispatch; tracking details persist; duplicate actions do not double-consume. | |
| E10 | Open customer records; search by name, email and numeric customer ID; follow order/support links. | Search finds correct customer; view is read-only and contains useful order/support history; passwords and payment credentials are never shown. | |
| E11 | In staff support queue filter by status/order, reply publicly, add staff-only note, and close with resolution. | Filters work; thread order and authors are clear; private note stays staff-only; closure requires resolution and records actor/time. | |

### F. Manager access, security, accessibility, and presentation (about 30 min)

| ID | Steps | Expected result | Status / notes |
|---|---|---|---|
| F01 | As manager create employee and manager accounts; edit a role; deactivate a non-final manager/employee; try logging in with deactivated account and an already-open session. | New staff can sign in only through staff page; role changes take effect; deactivation blocks existing/new staff access while prior audit attribution remains. | |
| F02 | Try employee access to employee-management URLs; as manager try demoting/deactivating the last active manager. As customer/guest try direct staff URLs. | Only active managers manage employees; the final manager cannot be removed/demoted; other roles are denied without sensitive data or server errors. | |
| F03 | While signed in, sign out and use browser Back, refresh, and direct URL for a previously protected staff/customer page. | Protected data is not accessible from stale page/session; sign-out ends access; expected redirect/permission message appears. | |
| F04 | Keyboard-only pass: Tab/Shift+Tab through navbar, filters, forms, dialogs, cart actions and staff tables; activate controls with Enter/Space. | Focus is visible and follows a logical order; controls work without a mouse; no keyboard trap; form errors can be found. | |
| F05 | At 375px and 768px widths inspect storefront, preset detail, questionnaire, comparison, cart/checkout, order detail and staff tables/forms. | No clipped critical actions, sideways overflow, overlapping text, unreadable tables, or broken image sizing; content order remains sensible. | |
| F06 | Check representative empty, error, success, and long-text states. Inspect image loading, currency/decimal consistency, labels, contrast, and browser console/network errors. | Messages are understandable and near the relevant action; no placeholder/debug text or broken layout; uploaded/hot-linked images have graceful fallback; status and money are consistent. | |

## Time estimate

| Pass | Estimated active time |
|---|---:|
| Automated checks (three commands above) | about 35–60 sec |
| Manual sample data setup | 30–45 min |
| A–B: entry, account, storefront, recommendations | 55 min |
| C: configure through checkout | 40 min |
| D: order lifecycle, substitutions, support, reviews | 45 min |
| E: staff workflows | 45 min |
| F: security, responsive, accessibility, visual sweep | 30 min |
| **Manual pass total (setup + A–F)** | **about 4 hr 5 min–4 hr 20 min** |

Automated runtime is measured from this machine and will vary. Manual timing assumes one tester, a working local server, and no major defects. Allow another 30–60 minutes to reproduce, capture, and retest issues. A genuinely fresh database may add around 30 minutes for the first-manager bootstrap and staff/catalogue setup.

## Notes and defect log

| Case | Pass/fail/block | Browser/viewport | Steps/data used | Expected vs actual | Screenshot/link |
|---|---|---|---|---|---|
| | | | | | |
