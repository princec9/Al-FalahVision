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
Achievements tab or the target-rate table on the Commissions tab -- with the
sales people being ``res.partner`` records (flag ``is_salesperson``) rather
than ``res.users``.

What stays exactly as designed
------------------------------
* Attribution Level "Sale Order" (native's default): the order's Order Sales Person
  (``sale.order.salesperson_partner_id``) earns commission on the
  untaxed TOTAL of the order (or, if the plan's Achievements tab restricts by
  product/category, on the matching lines of that order).
* Attribution Level "Sale Order Line": per-line attribution -- achievement
  goes to each line's OWN Line Sales Person (``sale.order.line.salesperson_partner_id``),
  which is independent of (not copied from) the Order Sales Person on the
  header. A line without a Line Sales Person belongs to no one.
* Native user-based plans (no partners on the Sales People tab)
  and native models/queries are not modified.
* The formula contract is unchanged: it sets ``result`` and ``result_type``
  ('percentage' or 'amount'); commission = total_sold * result / 100, or
  ``result``. It is evaluated once per report row (sales person x target
  period), and a broken formula yields 0 (logged) instead of breaking the
  report.
* ``optical_prescription/report/achievement_report.py`` stays uninstalled and
  untouched.

What had to change, and why
---------------------------
Native ``sale.commission.achievement.report`` / ``sale.commission.report``
are built from ``res.users`` (plan users, ``rules.user_id``): a partner with
no login can never be a row there, so extending them with a compute field
cannot work for partners. This module therefore keeps the native models
untouched and adds ONE new model, ``sale.commission.partner.report``, rebuilt
from the confirmed order lines (at most every 30 s, when the report is
opened). That is the one deliberate departure from "no new model".

Manual adjustments, partner-wise
--------------------------------
The native manual adjustment (``sale.commission.achievement``: Add to /
Reduce From) points at res.users lines of the plan, which this module hides.
It gains ``add_partner_id`` / ``reduce_partner_id`` (the plan's Sales People
lines). The net adjustment dated inside a target period is added to that
partner's Achieved amount in Commission by Sales Person (new column
"Adjustments"), so it moves the achieved rate, the Commissions-tab
interpolation and the Commission Formula. The native adjustment screens show
the partner fields and hide the user fields; the same adjustments are also
listed in Sales > Reporting > Adjustments by Sales Person.

Setup: tick "Sales Person" on the partner; add the partner to the plan's
"Sales People" tab with a From / To period (only sales inside it
count; "Other plans" shows overlaps with the partner's other plans) (plan approved, Attribution Level "Sale Order
Line", with target periods); pick the Sales Person (Partner) on the order
and lines. Reports: Sales > Reporting > Commission by Sales Person and
Achievements by Sales Person. The native Commission and Achievements report
menus are re-pointed to them (disable with system parameter
bt_sale_commission.repoint_native_report = 0).
    """,
    "version": "19.0.2.4.1",
    "category": "Sales/CRM",
    "author": "BroadTech IT Solutions Pvt Ltd",
    "license": "LGPL-3",
    "depends": ["optical_prescription", "sale_commission", "account"],
    "data": [
        "security/ir.model.access.csv",
        "views/sale_commission_plan_views.xml",
        "views/sale_order_views.xml",
        "views/account_move_views.xml",
        "views/sale_commission_report_views.xml",
        "views/sale_commission_achievement_views.xml",
        "data/commission_report_data.xml",
    ],
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": False,
    "auto_install": False,
}
