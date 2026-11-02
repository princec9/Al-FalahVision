# -*- coding: utf-8 -*-
"""
sale.commission.plan (_inherit)
================================

Partner-based commission. The people who earn commission under a plan are
res.partner records flagged "Sales Person" (``res.partner.is_salesperson``),
listed on the plan's "Sales People" tab. res.users is not used.

``commission_python_compute`` is the plan's Python formula: it is evaluated
once per report row (partner x target period) and decides the commission
rate or amount, exactly where native Odoo would apply the flat rate / target
rate table.
"""
import functools
import logging

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval, test_python_expr

_logger = logging.getLogger(__name__)
# commission_based_on is provided by optical_prescription (reused as-is).
LINE_MODE = 'account_move_line'

# native (user-keyed) report model -> our partner-based window action
REPOINT = {
    'sale.commission.report': 'bt_sale_commission.sale_commission_partner_report_action',
    'sale.commission.achievement.report': 'bt_sale_commission.sale_commission_achievement_partner_action',
}

DEFAULT_FORMULA_HELP = _(
    "Python code evaluated once per row of the Commission by Sales Person "
    "report (one row per sales person partner and target period), at both "
    "Attribution Levels. Everything is read from posted customer invoices and "
    "credit notes, untaxed, in company currency: achieved (= total_sold = "
    "amount_invoiced) is the partner's total of the row ('Invoice' = invoices "
    "whose Sales Person is the partner, 'Invoice Line' = lines whose own Sales "
    "Person is the partner), manual adjustments included. No achieved, no "
    "commission.\n\n"
    "Must set two local variables:\n"
    "  result       -- a number\n"
    "  result_type  -- 'percentage' or 'amount'\n\n"
    "Final commission = achieved * (result / 100) if result_type is "
    "'percentage', otherwise result is used directly.\n\n"
    "Available variables: plan, partner, achieved, total_sold, amount_invoiced, "
    "adjustment, total_sold_qty, qty_invoiced, target, target_amount, "
    "target_completion, target_commission(c), company_target, company_achieved "
    "(all sales people of the company in the period), lines (the partner's "
    "account.move.line records of the row), moves, line_amount(l), line_qty(l), "
    "date_from, date_to, currency, company, row, env."
)


class SaleCommissionPlan(models.Model):
    _inherit = 'sale.commission.plan'

    company_target = fields.Float("Company Target")

    # Attribution Level: ONLY the two invoice based levels. The selection of
    # optical_prescription is replaced (no selection_add), sale order levels
    # are no longer offered:
    #   account_move       -> untaxed invoices minus credit notes, per invoice
    #                         Sales Person; commission from the Commissions tab
    #   account_move_line  -> Commission Formula per invoice line, per line
    #                         Sales Person
    commission_based_on = fields.Selection(
        selection=[
            ('account_move', 'Invoice'),
            ('account_move_line', 'Invoice Line'),
        ],
        default='account_move',
    )

    @api.onchange('company_target')
    def _onchange_company_target(self):
        """When the plan's company_target changes, push that value onto
        each personal target line in target_ids."""
        for plan in self:
            for line in plan.target_ids:
                line.amount = plan.company_target

    # ------------------------------------------------------------------
    # Buttons on the plan form (e.g. "Related commissions") build their
    # window action in Python, so re-pointing menus/action records alone does
    # not catch them. Every plan-specific action_* method is wrapped: if it
    # returns a window action on a native user-keyed report, the partner-based
    # report is opened instead (same plan filter).
    # ------------------------------------------------------------------
    def _register_hook(self):
        res = super()._register_hook()
        cls = type(self)
        for name in dir(cls):
            if not name.startswith('action_') or hasattr(models.Model, name):
                continue
            method = getattr(cls, name, None)
            if not callable(method) or getattr(method, '_bt_partner_wrapped', False):
                continue
            wrapper = self._bt_make_action_wrapper(method)
            wrapper._bt_partner_wrapped = True
            setattr(cls, name, wrapper)
        return res

    @staticmethod
    def _bt_make_action_wrapper(method):
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            result = method(self, *args, **kwargs)
            try:
                return self._bt_translate_native_action(result)
            except Exception:
                _logger.exception(
                    "bt_sale_commission: could not translate action of %s; "
                    "returning the native one.", method.__name__)
                return result
        return wrapper

    def _bt_translate_native_action(self, result):
        if (not isinstance(result, dict)
                or result.get('type') != 'ir.actions.act_window'
                or result.get('res_model') not in REPOINT):
            return result
        param = self.env['ir.config_parameter'].sudo()
        if param.get_param('bt_sale_commission.repoint_native_report') in ('0', 'False', 'false'):
            return result
        xmlid = REPOINT[result['res_model']]
        if not self.env.ref(xmlid, raise_if_not_found=False):
            return result
        new = self.env['ir.actions.act_window']._for_xml_id(xmlid)
        if result['res_model'] == 'sale.commission.report':
            self.env['sale.commission.partner.report']._ensure_fresh(force=True)
            # keep only the plan filter of the native domain
            domain = result.get('domain') or []
            if isinstance(domain, (list, tuple)):
                plan_leaves = [
                    leaf for leaf in domain
                    if isinstance(leaf, (list, tuple)) and leaf and leaf[0] == 'plan_id'
                ]
                new['domain'] = plan_leaves or [('plan_id', 'in', self.ids)]
        new['name'] = result.get('name') or new.get('name')
        if result.get('target'):
            new['target'] = result['target']
        return new

    partner_line_ids = fields.One2many(
        'sale.commission.plan.partner', 'plan_id',
        string="Sales People", copy=True,
        help="Partners flagged as Sales Person who earn commission under this "
             "plan, each with the period (From / To) during which sales "
             "count.")
    commission_partner_ids = fields.Many2many(
        'res.partner', string="Commission Partners",
        compute='_compute_commission_partner_ids')

    @api.depends('partner_line_ids.partner_id')
    def _compute_commission_partner_ids(self):
        for plan in self:
            plan.commission_partner_ids = plan.partner_line_ids.partner_id

    @api.model
    def _bt_migrate_partner_rel(self):
        """v2.0.0-2.0.4 stored partners in a plain many2many table; turn each
        pair into a row of the new tab (idempotent)."""
        cr = self.env.cr
        cr.execute("SELECT to_regclass('sale_commission_plan_partner_rel')")
        if not cr.fetchone()[0]:
            return
        cr.execute("SELECT plan_id, partner_id FROM sale_commission_plan_partner_rel")
        Line = self.env['sale.commission.plan.partner'].sudo()
        existing = {(l.plan_id.id, l.partner_id.id) for l in Line.search([])}
        vals = [{'plan_id': p, 'partner_id': q} for p, q in cr.fetchall()
                if (p, q) not in existing]
        if vals:
            Line.create(vals)

    # ------------------------------------------------------------------
    # Helpers used by the commission report
    # ------------------------------------------------------------------
    def _get_plan_dates(self):
        """(date_from, date_to) of the plan itself; False when open-ended
        or when the plan has no such fields."""
        self.ensure_one()
        return (
            self.date_from if 'date_from' in self._fields else False,
            self.date_to if 'date_to' in self._fields else False,
        )

    def _get_commission_periods(self):
        """List of (target, date_from, date_to): one per target period of
        the plan, or a single period spanning the plan's own dates when
        the plan has no targets."""
        self.ensure_one()
        targets = self.target_ids.sorted(lambda t: t.date_from or fields.Date.today())
        if targets:
            return [(t, t.date_from, t.date_to) for t in targets]
        date_from, date_to = self._get_plan_dates()
        return [(self.env['sale.commission.plan.target'], date_from, date_to)]

    def _get_matching_achievement(self, line):
        """First Achievements-tab rule of the plan that applies to the invoice
        / order line (product / category restriction); empty recordset if none
        applies. A plan without rules has nothing to match (see
        _line_matches_achievement_rules)."""
        self.ensure_one()
        for rule in self.achievement_ids:
            if rule.product_id and rule.product_id != line.product_id:
                continue
            if rule.product_categ_id and rule.product_categ_id != line.product_id.categ_id:
                continue
            return rule
        return self.achievement_ids.browse()

    def _line_matches_achievement_rules(self, line):
        """True if the line counts toward at least one Achievements-tab rule
        of the plan; a plan with no rule counts everything."""
        self.ensure_one()
        if not self.achievement_ids:
            return True
        return bool(self._get_matching_achievement(line))

    def _default_commission_python_compute(self):
        return """# Runs once per report row (sales person partner x target period), at both
# Attribution Levels. Everything comes from posted customer invoices / credit
# notes (untaxed, company currency). No achieved -> no commission.
# You must set two variables:
#
#   result       -> a number
#   result_type  -> 'percentage' or 'amount'
#
# Final commission = achieved * (result / 100)   if result_type == 'percentage'
#                  = result                       if result_type == 'amount'
#
# Available variables:
#   plan            -> this sale.commission.plan record
#   partner         -> the sales person (res.partner) this row belongs to
#   achieved        -> the partner's untaxed invoiced minus credited in the period
#                      (adjustments included). Aliases: total_sold, amount_invoiced
#   adjustment      -> net manual adjustments in the period (included in achieved)
#   total_sold_qty  -> quantity invoiced minus credited (alias qty_invoiced)
#   target          -> sale.commission.plan.target period of this row
#   target_amount   -> that period's target amount
#   target_completion -> achieved / target_amount (1.0 = 100%)
#   target_commission(c) -> Commissions-tab table value for completion c
#   company_target  -> plan.company_target
#   company_achieved -> untaxed invoiced minus credited of ALL sales people of
#                      the company in the period
#   lines           -> the partner's account.move.line records of this row
#   moves           -> their invoices / credit notes
#   line_amount(l)  -> untaxed amount of a line (negative on credit notes)
#   line_qty(l)     -> quantity of a line (negative on credit notes)
#   date_from / date_to -> period of this row
#   currency, company, row, env
#
# Example: 10% commission when the partner achieved more than 600, else 0.
if achieved > 600:
    result = 10
else:
    result = 0
result_type = 'percentage'
"""

    commission_python_compute = fields.Text(
        string="Commission Formula",
        default=_default_commission_python_compute,
        help=DEFAULT_FORMULA_HELP,
    )

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

    def _run_commission_formula(self, eval_context):
        """Run the formula. Returns (result, result_type, error): `error` is
        a short text when the formula is missing, raised, or did not set a
        usable `result` / `result_type` (then result is None). Never raises."""
        self.ensure_one()
        if not self.commission_python_compute:
            return None, None, "no formula on the plan"
        # Version-independent way to get the formula's variables back:
        # safe_eval's exec mode returns nothing and (depending on the Odoo
        # version) may run on a copy of the context, so the code is followed
        # by two lines that store `result` / `result_type` in a dict that is
        # shared by reference with the copy.
        out = {}
        local_ctx = dict(eval_context)
        local_ctx['_bt_out'] = out
        code = (self.commission_python_compute.rstrip('\n') + "\n"
                "_bt_out['result'] = result\n"
                "_bt_out['result_type'] = result_type\n")
        try:
            safe_eval(code, local_ctx, mode="exec")
        except Exception as e:
            _logger.exception(
                "bt_sale_commission: formula on plan %r (id=%s) raised.",
                self.display_name, self.id)
            return None, None, "formula error: %s: %s" % (type(e).__name__, e)
        result = out.get('result')
        result_type = out.get('result_type')
        if result_type not in ('percentage', 'amount'):
            return None, None, (
                "formula left result_type as %r (must be 'percentage' or "
                "'amount')" % (result_type,))
        try:
            return float(result), result_type, False
        except (TypeError, ValueError):
            return None, None, "formula set result=%r, not a number" % (result,)

    def _evaluate_commission_python_compute(self, eval_context):
        """(result, result_type) or None -- see _run_commission_formula."""
        result, result_type, error = self._run_commission_formula(eval_context)
        if error:
            _logger.warning("bt_sale_commission: plan %r (id=%s): %s",
                            self.display_name, self.id, error)
            return None
        return result, result_type

    @api.model
    def _compute_commission_amount(self, result, result_type, total_sold):
        """Final amount = total_sold * (result / 100) if percentage,
        else result directly -- per the formula's contract."""
        if result_type == 'percentage':
            return (total_sold or 0.0) * (result / 100.0)
        return result

    # ------------------------------------------------------------------
    # Attribution Level "Sale Order": native target-commission table
    # (sale.commission.plan.target.commission, the plan's Commissions tab)
    # ------------------------------------------------------------------
    def _get_target_commission_lines(self):
        """Rows of the plan's Commissions tab, ordered by target completion.
        The one2many is looked up by its comodel so this does not depend on
        the native field name."""
        self.ensure_one()
        for field in self._fields.values():
            if (field.type == 'one2many'
                    and field.comodel_name == 'sale.commission.plan.target.commission'):
                return self[field.name].sorted(lambda c: (c.target_rate, c.id))
        return self.env['sale.commission.plan.target.commission']

    @api.model
    def _interpolate_target_commission(self, lines, rate):
        """Commission for an achieved completion `rate` (ratio: 1.0 = 100%)
        from the table `lines` (sorted by target_rate): linear interpolation
        between the two surrounding rows; 0 below the first row; the last
        row's amount at/after the last row."""
        lower = upper = None
        for line in lines:
            if line.target_rate <= rate:
                lower = line
            else:
                upper = line
                break
        if lower is None:
            return 0.0
        if upper is None:
            return lower.amount
        span = upper.target_rate - lower.target_rate
        if not span:
            return lower.amount
        return lower.amount + (upper.amount - lower.amount) * (rate - lower.target_rate) / span
