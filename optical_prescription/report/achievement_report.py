# -*- coding: utf-8 -*-
"""
Commission source: Sale Order vs Sale Order Line
================================================

Business requirement
--------------------
`sale.commission.plan` carries a new selection field `commission_based_on`:

    * 'sale_order'      -> standard Odoo behaviour (DEFAULT).
                           Achievements are attributed to the salesperson on
                           the Sale Order header (sale_order.user_id) /
                           Invoice header (account_move.invoice_user_id).
    * 'sale_order_line' -> achievements are attributed to the salesperson set
                           on each Sale Order Line (sale_order_line.user_id),
                           so several salespersons can earn from one order,
                           each on their own lines only.

Design
------
The base report (`sale.commission.achievement.report`) builds one big SQL view.
Rather than copy-pasting and rewriting `_sale_lines()` / `_invoices_lines()`
(which breaks `sale.commission.report`, an enterprise sibling model that reuses
these very methods and composes its own column layout on top -> "each UNION
query must have the same number of columns"), this override only touches the
three seams the base design already exposes:

    _select_rules()            -> publish the plan's `commission_based_on` into
                                  the `sale_rules` / `invoices_rules` CTEs.
    _join_sales()              -> re-expose `fo` through a LATERAL that swaps
    _join_invoices()              `user_id` / `invoice_user_id` for the
                                  line-level salesperson *only when the rule's
                                  plan says so*.
    _get_filtered_orders_cte() -> relax the header-level pre-filter so orders /
    _get_filtered_moves_cte()     invoices are not dropped before the line-level
                                  salesperson can be considered.

Every downstream method keeps working unmodified against `fo.user_id` /
`fm.invoice_user_id`; the existing `fo.user_id = rules.user_id` predicate inside
`_sale_lines()` then performs the salesperson matching for us, for both modes.

Guarantees
----------
* Zero impact when no approved plan uses 'sale_order_line': every method
  short-circuits to `super()` and the emitted SQL is byte-identical to stock.
* Team plans ('user_type' == 'team') are never affected - they keep aggregating
  the whole order / invoice per team exactly as stock Odoo does.
* Only salesperson *identification* changes. Achievement types (amount_sold,
  qty_sold, amount_invoiced, qty_invoiced), plan structure, targets, forecasts,
  adjustments, currency conversion and every report column are untouched.
* No impact on quotations, the sales workflow, deliveries or invoicing.

Invoice side
------------
`account.move.line` has no native `user_id`. The invoice line's salesperson is
resolved through the standard sale<->invoice link table
`sale_order_line_invoice_rel` back to `sale_order_line.user_id`, via a
LEFT JOIN LATERAL ... LIMIT 1 so an invoice line linked to several SO lines
cannot duplicate its amount.

Fallback
--------
In 'sale_order_line' mode a line with an EMPTY salesperson falls back to the
order / invoice header salesperson (COALESCE). This is deliberate: without it
that line's revenue would silently vanish from every commission plan. Lines that
DO carry a salesperson never fall back, so the header salesperson is never
credited for another person's line - which is what the requirement asks for.
"""

from datetime import datetime

from odoo import api, fields, models

LINE_MODE = 'sale_order_line'


class SaleCommissionAchievementReport(models.Model):
    _inherit = "sale.commission.achievement.report"

    # =========================================================
    # Helpers
    # =========================================================

    @api.model
    def _has_line_based_plans(self):
        """True if at least one active/approved commission plan is configured
        on Sale Order Line. When False, this whole override is a no-op and the
        generated SQL is identical to stock Odoo."""
        plan_model = self.env['sale.commission.plan'].sudo()
        if 'commission_based_on' not in plan_model._fields:
            return False
        return bool(plan_model.search_count([
            ('active', '=', True),
            ('state', '=', 'approved'),
            ('commission_based_on', '=', LINE_MODE),
        ], limit=1))

    @api.model
    def _select_rules(self):
        """Expose the plan's commission source to the join conditions.

        Appended right after `_rate_to_case(...)`, which emits no trailing
        comma - hence the leading comma here. Only adds a column to the
        `sale_rules` / `invoices_rules` CTEs, which are never UNIONed, so no
        column-count mismatch can occur in sibling models.
        """
        select_ = super()._select_rules()
        if not self._has_line_based_plans():
            return select_
        # COALESCE guards plan rows that pre-date the field (NULL -> stock
        # Sale Order behaviour), so an upgrade can never silently reattribute
        # commissions on an existing plan.
        return select_ + (
            ",\n        COALESCE(scp.commission_based_on, 'sale_order')"
            " AS commission_based_on"
        )

    # =========================================================
    # SALES (Sale Order / Sale Order Line)
    # =========================================================

    @api.model
    def _get_filtered_orders_cte(self, users=None, teams=None):
        """Identical to base, except the `users` pre-filter also accepts orders
        that merely have a LINE assigned to one of those users. Without this,
        an order whose header salesperson is John would be dropped before
        Sarah's line on it could ever be seen."""
        if not users or not self._has_line_based_plans():
            return super()._get_filtered_orders_cte(users=users, teams=teams)

        date_from, date_to = self._get_achievement_default_dates()
        today = fields.Date.today().strftime('%Y-%m-%d')
        date_from_condition = (
            f"""AND date_order >= '{datetime.strftime(date_from, "%Y-%m-%d")}'"""
            if date_from else ""
        )
        user_ids = ','.join(str(i) for i in users.ids)
        return f"""
        filtered_orders AS (
            SELECT
                    id,
                    team_id,
                    state,
                    currency_rate,
                    company_id,
                    currency_id,
                    user_id,
                    date_order,
                    write_date,
                    partner_id
              FROM sale_order
             WHERE state = 'sale'
               AND (
                    user_id IN ({user_ids})
                 OR EXISTS (
                        SELECT 1
                          FROM sale_order_line sol_f
                         WHERE sol_f.order_id = sale_order.id
                           AND sol_f.user_id IN ({user_ids})
                    )
               )
               {'AND team_id in (%s)' % ','.join(str(i) for i in teams.ids) if teams else ''}
               {date_from_condition}
               AND date_order <= '{datetime.strftime(date_to, "%Y-%m-%d") if date_to else today}'
        )
        """

    @api.model
    def _join_sales(self, join_type=None):
        """Team plans and installations without any line-based plan: untouched.

        Otherwise `fo` becomes a LATERAL projection of `filtered_orders` whose
        `user_id` column is the *effective* salesperson for the rule at hand:
        the SO line's salesperson for 'sale_order_line' plans, the SO header's
        for 'sale_order' plans. The join predicates keep both branches pruned
        so we never build the full rules x orders cross product.
        """
        if join_type == 'team' or not self._has_line_based_plans():
            return super()._join_sales(join_type=join_type)

        return f"""
            JOIN filtered_orders fo0
              ON (rules.commission_based_on = '{LINE_MODE}'
                  OR fo0.user_id = rules.user_id)
            JOIN sale_order_line sol
              ON sol.order_id = fo0.id
             AND (rules.commission_based_on != '{LINE_MODE}'
                  OR COALESCE(sol.user_id, fo0.user_id) = rules.user_id)
            JOIN LATERAL (
                SELECT
                    fo0.id,
                    fo0.team_id,
                    fo0.state,
                    fo0.currency_rate,
                    fo0.company_id,
                    fo0.currency_id,
                    CASE
                        WHEN rules.commission_based_on = '{LINE_MODE}'
                            THEN COALESCE(sol.user_id, fo0.user_id)
                        ELSE fo0.user_id
                    END AS user_id,
                    fo0.date_order,
                    fo0.write_date,
                    fo0.partner_id
            ) fo ON true
        """

    # =========================================================
    # INVOICES (Invoice lines linked back to Sale Order Lines)
    # =========================================================

    @api.model
    def _get_filtered_moves_cte(self, users=None, teams=None):
        """Identical to base, except the `users` pre-filter also accepts moves
        having an invoice line linked to an SO line assigned to those users."""
        if not users or not self._has_line_based_plans():
            return super()._get_filtered_moves_cte(users=users, teams=teams)

        date_from, date_to = self._get_achievement_default_dates()
        today = fields.Date.today().strftime('%Y-%m-%d')
        date_from_str = date_from and datetime.strftime(date_from, "%Y-%m-%d")
        date_from_condition = f"""AND date >= '{date_from_str}'""" if date_from_str else ""
        user_ids = ','.join(str(i) for i in users.ids)
        return f"""
        filtered_moves AS (
            SELECT
                    id,
                    team_id,
                    move_type,
                    state,
                    invoice_currency_rate,
                    company_id,
                    currency_id,
                    invoice_user_id,
                    date,
                    write_date,
                    partner_id
              FROM account_move
             WHERE move_type IN ('out_invoice', 'out_refund')
               AND state = 'posted'
               AND (
                    invoice_user_id IN ({user_ids})
                 OR EXISTS (
                        SELECT 1
                          FROM account_move_line aml_f
                          JOIN sale_order_line_invoice_rel silr_f
                            ON silr_f.invoice_line_id = aml_f.id
                          JOIN sale_order_line sol_f
                            ON sol_f.id = silr_f.order_line_id
                         WHERE aml_f.move_id = account_move.id
                           AND sol_f.user_id IN ({user_ids})
                    )
               )
             {'AND team_id in (%s)' % ','.join(str(i) for i in teams.ids) if teams else ''}
               {date_from_condition}
               AND date <= '{datetime.strftime(date_to, "%Y-%m-%d") if date_to else today}'
        )
        """

    @api.model
    def _join_invoices(self, join_type=None):
        """Same principle as `_join_sales`, on the invoice side. The invoice
        line's salesperson comes from the linked SO line; LIMIT 1 inside the
        LATERAL guarantees an invoice line linked to several SO lines is never
        counted twice."""
        if join_type == 'team' or not self._has_line_based_plans():
            return super()._join_invoices(join_type=join_type)

        return f"""
            JOIN filtered_moves fm0
              ON (rules.commission_based_on = '{LINE_MODE}'
                  OR fm0.invoice_user_id = rules.user_id)
            JOIN account_move_line aml
              ON aml.move_id = fm0.id
            LEFT JOIN LATERAL (
                SELECT sol_i.user_id
                  FROM sale_order_line_invoice_rel silr
                  JOIN sale_order_line sol_i
                    ON sol_i.id = silr.order_line_id
                 WHERE silr.invoice_line_id = aml.id
                   AND sol_i.user_id IS NOT NULL
                 LIMIT 1
            ) line_sp ON true
            LEFT JOIN product_product pp
              ON aml.product_id = pp.id
            LEFT JOIN product_template pt
              ON pp.product_tmpl_id = pt.id
            JOIN LATERAL (
                SELECT
                    fm0.id,
                    fm0.team_id,
                    fm0.move_type,
                    fm0.state,
                    fm0.invoice_currency_rate,
                    fm0.company_id,
                    fm0.currency_id,
                    CASE
                        WHEN rules.commission_based_on = '{LINE_MODE}'
                            THEN COALESCE(line_sp.user_id, fm0.invoice_user_id)
                        ELSE fm0.invoice_user_id
                    END AS invoice_user_id,
                    fm0.date,
                    fm0.write_date,
                    fm0.partner_id
            ) fm
              ON (rules.commission_based_on != '{LINE_MODE}'
                  OR COALESCE(line_sp.user_id, fm0.invoice_user_id) = rules.user_id)
        """
