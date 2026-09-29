# -*- coding: utf-8 -*-
"""
sale.commission.achievement.report (_inherit)
================================================

Attribution override only: makes rows for 'sale_order_line' plans group
by sale_order_line.user_id instead of the Sale Order/Invoice header's
user_id.

This model has no `commission` field, only `achieved` -- native's own
target-progress figure -- and this module never touches it, for any
plan, regardless of Attribution Level. There is nothing on this model
for the Line Commission Formula to write into; the formula only ever
affects the payable `commission` field on sale.commission.report.

This was raised as an explicit, unresolved decision in this module's
original design (see README) precisely because fixing attribution means
touching the same class of internals as
`optical_prescription/report/achievement_report.py` -- a dormant,
already-written patch doing exactly this, deliberately left uninstalled
(`optical_prescription/__init__.py` comments out `from . import report`)
pending stakeholder confirmation. The decision made, on request, is to
reimplement the same mechanism here instead of uncommenting that import,
so `bt_sale_commission` stays self-contained and `optical_prescription`
is not touched at all.

Do not additionally uncomment `optical_prescription`'s own import. Both
overrides patch the exact same native methods on the exact same model;
if both are active, whichever module's class sits later in the merged
MRO silently wins per method, and the two are not guaranteed compatible.
Use this module's version, or that one, never both.

Zero-impact guarantee: every overridden method here short-circuits to
`super()` whenever there is no active, approved plan with
`commission_based_on == 'sale_order_line'`, so installing this module
changes nothing for sites that only use 'sale_order' plans.
"""
from datetime import datetime

from odoo import api, fields, models

LINE_MODE = 'sale_order_line'


class SaleCommissionAchievementReport(models.Model):
    _inherit = 'sale.commission.achievement.report'

    @api.model
    def _has_line_based_plans(self):
        """True if at least one active/approved plan uses line-mode.
        When False every method below is a pure passthrough to super()
        and the emitted SQL is byte-identical to stock Odoo."""
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
        """Publish the plan's commission_based_on into the sale_rules /
        invoices_rules CTEs. Appended right after _rate_to_case(...),
        which emits no trailing comma -- hence the leading comma here.
        Those CTEs are never UNIONed, so no column-count mismatch can
        occur in sibling models (notably sale.commission.report)."""
        select_ = super()._select_rules()
        if not self._has_line_based_plans():
            return select_
        return select_ + (
            ",\n        COALESCE(scp.commission_based_on, 'sale_order')"
            " AS commission_based_on"
        )

    @api.model
    def _get_filtered_orders_cte(self, users=None, teams=None):
        """Identical to base, except the `users` pre-filter also accepts
        orders that merely have a LINE assigned to one of those users --
        otherwise an order headed by A would be dropped before B's line
        on it could ever be considered."""
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
        """Team plans, and installs with no line-based plan at all, are
        untouched. Otherwise `fo` becomes a LATERAL projection whose
        `user_id` is the *effective* salesperson for the rule at hand:
        the line's own salesperson for 'sale_order_line' plans, the
        header's for 'sale_order' plans.

        No header fallback: for 'sale_order_line' plans, a line with an
        empty `sale_order_line.user_id` matches no rule at all -- it
        belongs to no one under this plan, not to the order header. This
        is an explicit product decision, not an oversight."""
        if join_type == 'team' or not self._has_line_based_plans():
            return super()._join_sales(join_type=join_type)

        return f"""
            JOIN filtered_orders fo0
              ON (rules.commission_based_on = '{LINE_MODE}'
                  OR fo0.user_id = rules.user_id)
            JOIN sale_order_line sol
              ON sol.order_id = fo0.id
             AND (rules.commission_based_on != '{LINE_MODE}'
                  OR sol.user_id = rules.user_id)
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
                            THEN sol.user_id
                        ELSE fo0.user_id
                    END AS user_id,
                    fo0.date_order,
                    fo0.partner_id
            ) fo ON true
        """

    @api.model
    def _get_filtered_moves_cte(self, users=None, teams=None):
        """Same relaxation as _get_filtered_orders_cte, on the invoice
        side: also accept moves with an invoice line linked back to a
        Sale Order Line owned by one of `users`."""
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
        """Same principle as _join_sales, on the invoice side. The
        invoice line's salesperson is resolved through the standard
        sale<->invoice link table back to sale_order_line.user_id, via
        LEFT JOIN LATERAL ... LIMIT 1 so an invoice line linked to
        several SO lines is never counted twice.

        No header fallback: for 'sale_order_line' plans, an invoice line
        whose linked Sale Order Line has no salesperson (or has no linked
        Sale Order Line at all) matches no rule -- it belongs to no one,
        not to the invoice header."""
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
                            THEN line_sp.user_id
                        ELSE fm0.invoice_user_id
                    END AS invoice_user_id,
                    fm0.date,
                    fm0.partner_id
            ) fm
              ON (rules.commission_based_on != '{LINE_MODE}'
                  OR line_sp.user_id = rules.user_id)
        """
