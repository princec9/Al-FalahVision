# -*- coding: utf-8 -*-
# b-bt-nai
{
    "name": "BT Sale Commission",
    "summary": "Per-Sale-Order-Line commission rate override for Odoo's native "
                "commission engine, via a Python formula on the Commission Plan.",
        "description": """
BT Sale Commission
===================

Lets a ``sale.commission.plan`` whose Attribution Level
(``commission_based_on``, provided by ``optical_prescription``) is set to
"Sale Order Line" replace native's own rate-calculation step with a Python
formula (``commission_python_compute``), instead of the flat rate on the
Achievements tab or the target-rate table on the Commissions tab.

The formula is evaluated live, per row, directly on top of Odoo's own
``sale.commission.achievement.report`` and ``sale.commission.report``
models (each extended via ``_inherit`` with a single new non-stored
compute field). Nothing is persisted in a custom table: no new model is
introduced anywhere by this module.

When ``commission_based_on == 'sale_order'`` (native's default), every
number this module can influence is left byte-for-byte identical to stock
Odoo.

Per-line attribution
---------------------
A rate override alone cannot make a formula meaningful per Sale Order
Line: native groups achievement rows by the Sale Order/Invoice *header*
salesperson, never by the line's own salesperson. This module therefore
also overrides ``sale.commission.achievement.report``'s join logic (see
``models/sale_commission_achievement_report.py``) so that, for
'sale_order_line' plans only, rows are grouped by
``sale_order_line.user_id`` instead. This mirrors a design already
written -- and deliberately left dormant -- in
``optical_prescription/report/achievement_report.py``; that file remains
uninstalled and untouched, and must stay that way: both implementations
patch the same native methods, and running both at once is unsupported.
    """,
    "version": "19.0.1.0.0",
    "category": "Sales/CRM",
    "author": "BroadTech IT Solutions Pvt Ltd",
    "license": "LGPL-3",
    "depends": ["optical_prescription"],
    "data": [
        "views/sale_commission_plan_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
