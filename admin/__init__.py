"""Admin dashboard — FastAPI + Jinja2 for pro-athlete data entry, ontology authoring,
harvest control, analytics overview, the Study page, and interactive audit review
(``audit.py`` — round verdicts over private owner footage + the dictionary verdict queue
over public corpus frames, see ``/admin/audit/rounds`` and ``/admin/audit/dictionary``).

Installable as a PWA (manifest + service worker, ``admin/static/{manifest.webmanifest,
sw.js}``, registered from ``templates/base.html``). Network-first for every HTML/JSON
route (nothing here is ever served stale), cache-first only for ``/admin/static/*``, an
offline fallback page for navigations (``/admin/offline``). Chrome installs a PWA over
plain http on localhost, so the existing ``127.0.0.1:8765`` bind is enough — open the
dashboard, then the browser's install icon in the address bar (or menu → "Install
GrapplingArc Admin…")."""
