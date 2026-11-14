BUILDWISE - IS2108 PAIR PROJECT
AY2026/27 Semester 1
Group number: 31 (PP31)

SUBMISSION DETAILS - COMPLETE BEFORE HANDING IN

Group leader: Oliver Koh Qi Yang

Member 1 (James)
Full name (as registered): Tan Chuan-En James
Student number: A0324911L
Email: jamestance@u.nus.edu
Contact number: 97877103

Member 2 (Oliver)
Full name (as registered): Oliver Koh Qi Yang
Student number: A0324864X
Email: oliverkoh@u.nus.edu
Contact number: 98926223

Full demonstration video (YouTube): [TO COMPLETE: final YouTube URL]
Both members must present. Maximum video length: 15 minutes.
Check that the assessor can open the final link.

APPLICATION

BuildWise is a PC storefront and custom staff operations panel built with
Python, Django, SQLite and Bootstrap. The storefront integrates a saved
Random Forest model to estimate FPS and inform build recommendations.

PREREQUISITES

Python 3.12 and the exact dependencies in requirements.txt.
SQLite is included with Python. No separate database server or API key is needed.
The trained joblib model is included; no model training is needed for startup.
Internet access is needed for installing dependencies, Bootstrap CDN resources
and externally hosted product images.

FIRST RUN

Extract the submission and open a terminal in the Django project folder under
source/ that contains manage.py, requirements.txt and this readme.txt.
Use a fresh extraction/database for the demo. Choose ONE command sequence below.

Windows PowerShell:
  py -3.12 -m venv .venv
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  .\.venv\Scripts\python.exe manage.py migrate
  .\.venv\Scripts\python.exe manage.py seed_storefront
  .\.venv\Scripts\python.exe manage.py check
  .\.venv\Scripts\python.exe manage.py runserver

macOS / Linux:
  python3.12 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python manage.py migrate
  .venv/bin/python manage.py seed_storefront
  .venv/bin/python manage.py check
  .venv/bin/python manage.py runserver

The virtual environment is invoked directly; activation is not required.
For subsequent runs, use only the runserver command. Stop with Ctrl+C.
Use a new Python 3.12 environment if an existing .venv uses another Python version.

DEMO LOGINS AND PAGES

Storefront: http://127.0.0.1:8000/
Customer sign-in: http://127.0.0.1:8000/accounts/login
Customer registration: http://127.0.0.1:8000/accounts/register
Gaming questionnaire: http://127.0.0.1:8000/questionnaire
Staff sign-in: http://127.0.0.1:8000/staff/login
Staff inventory: http://127.0.0.1:8000/staff/
Staff order queue: http://127.0.0.1:8000/staff/queue

Manager: alicia
Employees: marcus, priya
Customers: weijie, nurul, daniel, sarah, arjun, meiling
Password for all newly created demo accounts: storefront123

These are fabricated local demo accounts. seed_storefront prints the logins.
Its --password option sets a different password for newly created accounts only.
Existing accounts retain their passwords.

The full demo seed creates accounts, catalogue, supported games, an assembly fee,
stock, published presets and sample orders, reviews and support conversations.
Do not run it against catalogue data you need to preserve: it refreshes the
supplied products and presets. It is not necessary to also run seed_demo_catalogue,
seed_fps_games or manually create the first manager.

SPECIAL NOTES

- Payment and refunds are simulated; no real payment is taken.
- FPS estimates use synthetic training data, not measured hardware benchmarks.
- Employees use the custom staff panel, not Django's default admin panel.
- Local changes persist in db.sqlite3. Product uploads are stored in media/.
- External product images may be unavailable if their source is offline.
- The supplied settings are for local development and assessment.

SUBMISSION CONTENTS

One ZIP: docs/PP31.docx and the Django project folder under source/.
Include this readme.txt, requirements.txt, manage.py, project/app folders,
migrations, templates, static assets, seed data and the saved FPS model.
Exclude .git and virtual-environment folders. If including a demonstration
database with uploaded images, include its matching media/ files too.
Test the extracted package and video link before submitting.
