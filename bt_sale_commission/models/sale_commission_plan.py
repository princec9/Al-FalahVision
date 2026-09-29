# -*- coding: utf-8 -*-
"""
sale.commission.plan (_inherit)
================================

Adds ``commission_python_compute``: a Python formula that acts as a
drop-in replacement for native's own rate-calculation step -- the role
played, in stock ``sale_commission``, by:

    * ``SaleCommissionAchievementReport._rate_to_case()`` together with
      the flat ``rate`` on the Achievements tab
      (``sale.commission.plan.achievement.rate``), and
    * the target-rate interpolation table on the Commissions tab
      (``sale.commission.plan.target.commission``), consumed inside
      ``SaleCommissionReport._query()``.

This field is only ever consulted when ``commission_based_on ==
'sale_order_line'`` (a field provided by ``optical_prescription``, reused
here as-is -- never redefined). For ``'sale_order'`` plans (native's
default) this module changes nothing: no field on this model is read,
so behaviour is byte-identical to stock Odoo.
"""
import logging

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval, test_python_expr

_logger = logging.getLogger(__name__)
# Confirmed against sale_commission/model/commission_plan.py::commission_based_on
# is NOT native; the field itself is defined by optical_prescription
# (models/sale_order.py, class SaleCommissionPlan, _inherit='sale.commission.plan').
LINE_MODE = 'sale_order_line'

# Confirmed against sale_commission/model/commission_plan.py::periodicity.
LINE_ALLOWED_ACHIEVEMENT_TYPES = {'amount_sold', 'qty_sold'}

DEFAULT_FORMULA_HELP = _(
    "Python code evaluated once per row of the Commissions report when "
    "Attribution Level is 'Sale Order Line'. This is a drop-in "
    "replacement for native's own rate-calculation step (the flat rate "
    "on the Achievements tab, or the target-rate table on the "
    "Commissions tab) -- not an input to some separately stored "
    "calculation.\n\n"
    "Must set two local variables:\n"
    "  result       -- a number\n"
    "  result_type  -- 'percentage' or 'amount'\n\n"
    "Final commission = total_sold * (result / 100) if result_type is "
    "'percentage', otherwise result is used directly.\n\n"
    "Available variables: plan, user, total_sold, achieved, "
    "total_sold_qty, target, date_from, date_to, currency, company, "
    "row, env."
)


class SaleCommissionPlan(models.Model):
    _inherit = 'sale.commission.plan'

    def _default_commission_python_compute(self):
        return """# Runs once per report row when Attribution Level is
# "Sale Order Line". You must set two variables:
#
#   result       -> a number
#   result_type  -> 'percentage' or 'amount'
#
# Final commission = total_sold * (result / 100)   if result_type == 'percentage'
#                   = result                        if result_type == 'amount'
#
# Available variables:
#   plan            -> this sale.commission.plan record
#   user            -> res.users this row belongs to
#   total_sold      -> this row's achieved amount (alias of achieved)
#   achieved        -> same value as total_sold
#   total_sold_qty  -> quantity sold, same orders/period/product-category
#                      scope as total_sold
#   target          -> sale.commission.plan.target period this row falls into
#   date_from       -> start date of that period
#   date_to         -> end date of that period
#   currency        -> res.currency of this row
#   company         -> res.company of this row
#   row             -> the underlying sale.commission.report record
#   env             -> the Odoo environment
#
# Example below: flat 5% commission on everything sold.
result = 5
result_type = 'percentage'
"""

    commission_python_compute = fields.Text(
        string="Line Commission Formula",
        default=_default_commission_python_compute,
        help=DEFAULT_FORMULA_HELP,
    )

    @api.constrains('commission_based_on', 'achievement_ids')
    def _check_line_mode_achievement_type(self):
        for plan in self:
            if plan.commission_based_on != LINE_MODE:
                continue
            bad_types = set(plan.achievement_ids.mapped('type')) - LINE_ALLOWED_ACHIEVEMENT_TYPES
            if bad_types:
                raise ValidationError(_(
                    "Commission plans with Attribution Level set to 'Sale "
                    "Order Line' can only use 'Amount Sold' or 'Quantity "
                    "Sold' on the Achievements tab — never 'Amount "
                    "Invoiced' or 'Quantity Invoiced' — since only the "
                    "sold side carries the line's own salesperson "
                    "directly; the invoiced side would need an indirect "
                    "link back to the sale order line that can be "
                    "missing or ambiguous."
                ))

    # ------------------------------------------------------------------
    # Formula validation & evaluation
    # ------------------------------------------------------------------
    @api.constrains('commission_python_compute')
    def _check_commission_python_compute_syntax(self):
        for plan in self:
            if not plan.commission_python_compute:
                continue
            error = test_python_expr(
                expr=plan.commission_python_compute, mode="exec",
            )
            if error:
                raise ValidationError(error)

    def _evaluate_commission_python_compute(self, eval_context):
        """Run ``commission_python_compute`` once against ``eval_context``
        and return ``(result, result_type)``, or ``None`` if there is no
        formula, or it did not produce a usable result.

        Never raises: a broken formula must degrade to "no override for
        this row" rather than break the report open. Every fallback path
        is logged so the reason is visible in the server log instead of
        silently producing a native-looking number.
        """
        self.ensure_one()
        if not self.commission_python_compute:
            return None

        local_ctx = dict(eval_context)
        try:
            returned_ctx = safe_eval(
                self.commission_python_compute,
                local_ctx,
                mode="exec",
            )
        except Exception:
            _logger.exception(
                "bt_sale_commission: formula on plan %r (id=%s) raised an "
                "exception -- falling back to the native value for this row.",
                self.display_name, self.id,
            )
            return None

        # Odoo versions differ on whether safe_eval mutates the dict we
        # passed in place, or hands back a separate namespace as its
        # return value. Prefer the returned namespace when it looks like
        # one; fall back to the dict we passed otherwise.
        ctx = returned_ctx if isinstance(returned_ctx, dict) else local_ctx

        result = ctx.get('result')
        result_type = ctx.get('result_type')
        if result_type not in ('percentage', 'amount'):
            _logger.warning(
                "bt_sale_commission: formula on plan %r (id=%s) left "
                "result_type as %r instead of 'percentage'/'amount' -- "
                "falling back to the native value for this row.",
                self.display_name, self.id, result_type,
            )
            return None
        try:
            result = float(result)
        except (TypeError, ValueError):
            _logger.warning(
                "bt_sale_commission: formula on plan %r (id=%s) set "
                "result=%r, which is not a number -- falling back to the "
                "native value for this row.",
                self.display_name, self.id, result,
            )
            return None
        return result, result_type

    @api.model
    def _compute_commission_amount(self, result, result_type, total_sold):
        """Final amount = total_sold * (result / 100) if percentage,
        else result directly -- per the formula's contract."""
        if result_type == 'percentage':
            return (total_sold or 0.0) * (result / 100.0)
        return result
