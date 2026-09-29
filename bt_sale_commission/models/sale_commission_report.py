# -*- coding: utf-8 -*-
"""
sale.commission.report (_inherit)
====================================

For 'sale_order_line' plans, the payable `commission` field is
overwritten with the plan's Line Commission Formula result, in place --
no separate column. `achieved` and `achieved_rate` are never touched:
those are native's own sold-progress figures, not a commission amount,
and stay exactly as stock Odoo computes them regardless of Attribution
Level.

Mechanism, two layers:

1. Row level: `commission_python_amount` (compute, never added to any
   view) holds the formula's result. `web_search_read` / `web_read` --
   the methods Odoo's list/form views actually call, not the classic
   `read()` -- patch `commission` onto each already-fetched row for
   line-mode plans only.

2. Group level: list-view group headers/totals and pivot/graph values
   come from a *separate* call (`formatted_read_group` /
   `formatted_read_grouping_sets`) that runs its own SQL `SUM(commission)`
   directly against the query -- it never goes through (1), so a grouped
   "Total commission" would silently keep showing native's un-patched
   sum. Both are overridden below: for any returned group that contains
   at least one line-mode row, the group's own records are re-fetched
   (via the `__extra_domain` Odoo attaches to each group specifically to
   support this) and the sum is recomputed in Python from the same
   per-row values (1) already produces, replacing native's SQL total for
   that group only. Groups with no line-mode rows are left untouched, so
   'sale_order'-only reports pay no extra cost.

Known cost: unlike (1), which patches values already fetched, (2) issues
an extra `search()` per affected group to get that group's own records,
then computes the sum in Python instead of native's single SQL
aggregate. Reports mixing many line-mode plans with very large groups
will be measurably slower than stock Odoo's grouped sum; this was an
explicit, approved trade-off, not an oversight.
"""
import logging

from odoo import api, fields, models
from odoo.fields import Domain

LINE_MODE = 'sale_order_line'
COMMISSION_SUM_SPEC = 'commission:sum'

_logger = logging.getLogger(__name__)


class SaleCommissionReport(models.Model):
    _inherit = 'sale.commission.report'

    # Internal calculation field only -- deliberately never referenced by
    # any view. `commission` itself is what the user sees; this is just
    # where the formula's result is computed before being patched in.
    commission_python_amount = fields.Monetary(
        string="Line Commission Amount (internal)",
        currency_field='currency_id',
        compute='_compute_commission_python_amount',
    )

    @api.depends(
        'plan_id',
        'plan_id.commission_based_on',
        'plan_id.commission_python_compute',
        'achieved', 'commission', 'user_id', 'target_id',
        'date_from', 'date_to', 'currency_id', 'company_id',
    )
    def _compute_commission_python_amount(self):
        for row in self:
            plan = row.plan_id
            if not plan or plan.commission_based_on != LINE_MODE:
                row.commission_python_amount = row.commission
                continue

            evaluated = plan._evaluate_commission_python_compute(
                row._get_commission_python_eval_context()
            )
            if evaluated is None:
                row.commission_python_amount = row.commission
                continue

            result, result_type = evaluated
            row.commission_python_amount = plan._compute_commission_amount(
                result, result_type, row.achieved,
            )

    def _get_commission_total_sold_qty(self):
        """Total ``product_uom_qty`` sold by this row's own salesperson,
        over the same orders/period backing this row's ``achieved``
        figure, restricted to whichever products/categories the plan's
        Achievements-tab rule(s) restrict to -- same population as
        'achieved', expressed as a quantity instead of a rate-weighted
        currency figure.

        Called only from _get_commission_python_eval_context(), which is
        itself only ever called for line-mode rows (see
        _compute_commission_python_amount above) -- no need to re-check
        commission_based_on here.

        Several ``sale.commission.plan.target`` periods can share one
        ``payment_date`` and get folded into a single
        ``sale.commission.report`` row (see native's own
        ``action_achievement_detail()``), so this reuses that exact
        period-widening logic rather than just ``self.target_id``'s own
        range, to look at every period this row actually represents.

        Known cost: one extra search on the achievement report, plus
        one on sale.order.line, per line-mode row -- the same class of
        trade-off already accepted for the group-level commission sum
        above.
        """
        self.ensure_one()
        plan = self.plan_id
        target_ids = plan.target_ids.filtered(
            lambda t: t.payment_date == self.target_id.payment_date
        )
        domain = [('plan_id', '=', plan.id), ('user_id', '=', self.user_id.id)]
        if target_ids:
            date_from = min(target_ids.mapped('date_from'))
            date_to = max(target_ids.mapped('date_to'))
            domain += [('date', '>=', date_from), ('date', '<=', date_to)]

        achievement_rows = self.env['sale.commission.achievement.report'].sudo().search(domain)
        order_ids = achievement_rows.filtered(
            lambda r: r.related_res_model == 'sale.order'
        ).mapped('related_res_id')
        if not order_ids:
            return 0.0

        order_lines = self.env['sale.order.line'].sudo().search([
            ('order_id', 'in', order_ids),
            ('user_id', '=', self.user_id.id),
            ('display_type', '=', False),
            ('is_expense', '=', False),
            ('is_downpayment', '=', False),
        ])
        matching_lines = order_lines.filtered(self._order_line_matches_achievement_rules)
        return sum(matching_lines.mapped('product_uom_qty'))

    def _order_line_matches_achievement_rules(self, line):
        """True if `line` counts toward at least one of the plan's
        Achievements-tab rules -- mirrors native's own per-rule
        ``(rules.product_id IS NULL OR rules.product_id = sol.product_id)
        AND (rules.product_categ_id IS NULL OR rules.product_categ_id =
        pt.categ_id)`` inclusion, OR'd across rules exactly as native
        sums contributions from every matching rule. A fully
        unrestricted rule (no product, no category) alone already
        matches every line."""
        rules = self.plan_id.achievement_ids
        if not rules:
            return True
        for rule in rules:
            if rule.product_id and rule.product_id != line.product_id:
                continue
            if rule.product_categ_id and rule.product_categ_id != line.product_id.categ_id:
                continue
            return True
        return False

    def _get_commission_python_eval_context(self):
        self.ensure_one()
        return {
            'row': self,
            'plan': self.plan_id,
            'user': self.user_id,
            'total_sold': self.achieved,
            'achieved': self.achieved,
            'total_sold_qty': self._get_commission_total_sold_qty(),
            'target': self.target_id,
            'date_from': self.date_from,
            'date_to': self.date_to,
            'currency': self.currency_id,
            'company': self.company_id,
            'env': self.env,
            'result': None,
            'result_type': None,
        }

    def _row_commission(self, rec):
        """The correct 'commission' value for one record: patched for
        line-mode plans, native otherwise. Shared by the row-level and
        group-level overrides so both can never disagree."""
        if rec.plan_id.commission_based_on == LINE_MODE:
            return rec.commission_python_amount
        return rec.commission

    # ==================================================================
    # 1. Row level -- patch native `commission` in place, at read time.
    # ==================================================================

    def _patch_commission_with_formula(self, records, requested_fields):
        if not records or 'commission' not in requested_fields:
            return
        ids = [r['id'] for r in records if r.get('id')]
        by_id = {rec.id: rec for rec in self.browse(ids)}
        for data in records:
            rec = by_id.get(data.get('id'))
            if rec is not None and rec.plan_id.commission_based_on == LINE_MODE:
                data['commission'] = rec.commission_python_amount

    @api.model
    def web_search_read(self, domain, specification, offset=0, limit=None, order=None, count_limit=None):
        result = super().web_search_read(
            domain, specification, offset=offset, limit=limit,
            order=order, count_limit=count_limit,
        )
        self._patch_commission_with_formula(result.get('records', []), specification)
        return result
    web_search_read._readonly = False

    def web_read(self, specification):
        result = super().web_read(specification)
        self._patch_commission_with_formula(result, specification)
        return result

    # ==================================================================
    # 2. Group level -- recompute SUM(commission) in Python for any
    #    group containing at least one line-mode row.
    # ==================================================================

    def _patch_commission_sum_in_groups(self, groups, aggregates, base_domain):
        if COMMISSION_SUM_SPEC not in (aggregates or ()):
            return
        for group in groups:
            try:
                self._patch_one_group_commission_sum(group, base_domain)
            except Exception:
                _logger.exception(
                    "bt_sale_commission: failed to recompute '%s' for a "
                    "group (keys=%s) -- leaving native's SQL total for "
                    "this group as-is.",
                    COMMISSION_SUM_SPEC, list(group.keys()),
                )

    def _patch_one_group_commission_sum(self, group, base_domain):
        extra_domain = group.get('__extra_domain')
        if extra_domain is None:
            _logger.warning(
                "bt_sale_commission: group result had no '__extra_domain' "
                "key -- cannot recompute '%s' for this group; leaving "
                "native's SQL total as-is. Group keys were: %s",
                COMMISSION_SUM_SPEC, list(group.keys()),
            )
            return

        records = self.search(Domain.AND([Domain(base_domain), Domain(extra_domain)]))
        if not records:
            return

        # Only worth recomputing if at least one record in this group
        # belongs to a line-mode plan -- otherwise native's own SQL sum
        # is already exactly right and re-deriving it here is pure
        # overhead for the common case.
        if not any(rec.plan_id.commission_based_on == LINE_MODE for rec in records):
            return

        group[COMMISSION_SUM_SPEC] = sum(self._row_commission(rec) for rec in records)

    @api.model
    def formatted_read_group(self, domain, groupby=(), aggregates=(), having=(), offset=0, limit=None, order=None) -> list[dict]:
        result = super().formatted_read_group(domain, groupby, aggregates, having, offset, limit, order)
        self._patch_commission_sum_in_groups(result, aggregates, domain)
        return result
    formatted_read_group._readonly = False

    @api.model
    def formatted_read_grouping_sets(self, domain, grouping_sets, aggregates=(), *, order=None):
        result = super().formatted_read_grouping_sets(domain, grouping_sets, aggregates, order=order)
        for groups in result:
            self._patch_commission_sum_in_groups(groups, aggregates, domain)
        return result
    formatted_read_grouping_sets._readonly = False