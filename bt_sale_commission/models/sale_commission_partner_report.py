# -*- coding: utf-8 -*-
"""
sale.commission.partner.report
================================

Commission report keyed on the res.partner sales person (never res.users):
one row per (plan, sales person partner, target period) with the amount
achieved, the target, the achieved rate and the commission calculated by the
plan's Commission Formula.

Rows are built ONLY from posted customer invoices and credit notes
(``account.move`` / ``account.move.line``), never from sale orders:

* Attribution Level "Invoice"      : sales person = ``account.move.salesperson_partner_id``;
  the untaxed amount of the partner's invoices minus credit notes is the
  achieved amount (amount_invoiced); the plan's Commission Formula runs once
  per row and decides the commission (``target_completion`` and
  ``target_commission()`` give access to the Commissions tab table).
* Attribution Level "Invoice Line" : sales person = ``account.move.line.salesperson_partner_id``;
  same formula, once per row; ``lines`` holds the partner's account.move.line
  records so the formula can look at each line.

The table is rebuilt whenever the report is opened from its menu, when an
invoice is posted / changed, and by a scheduled action every 15 minutes.
"""
import logging
import time
from datetime import datetime, time as dtime, timedelta

from odoo import api, fields, models

from .sale_commission_plan_partner import intersect_dates

_logger = logging.getLogger(__name__)

REFRESH_SECONDS = 30
PARAM_LAST = 'bt_sale_commission.last_refresh'
LINE_MODE = 'account_move_line'
INVOICE_MODES = ('account_move', 'account_move_line')
INVOICE_TYPES = ('out_invoice', 'out_refund')


class SaleCommissionPartnerReport(models.Model):
    _name = 'sale.commission.partner.report'
    _description = "Commission by Sales Person (Partner)"
    _order = 'partner_id, date_from desc, plan_id'

    partner_id = fields.Many2one('res.partner', string="Sales Person", index=True)
    plan_id = fields.Many2one('sale.commission.plan', string="Plan", index=True)
    commission_based_on = fields.Selection(
        related='plan_id.commission_based_on', string="Attribution Level",
        store=True)
    target_id = fields.Many2one('sale.commission.plan.target', string="Target Period")
    date_from = fields.Date(string="From")
    date_to = fields.Date(string="To")
    company_id = fields.Many2one('res.company', string="Company")
    currency_id = fields.Many2one('res.currency', string="Currency")
    achieved = fields.Monetary(
        string="Achieved", currency_field='currency_id',
        help="Sales of the period plus the net manual adjustments.")
    adjustment = fields.Monetary(
        string="Adjustments", currency_field='currency_id',
        help="Net manual adjustments (Add to minus Reduce From) dated inside "
             "the period; already included in Achieved.")
    qty = fields.Float(string="Quantity Sold")
    target_amount = fields.Monetary(string="Target", currency_field='currency_id')
    achieved_rate = fields.Float(string="Achieved Rate (%)")
    commission = fields.Monetary(string="Commission", currency_field='currency_id')
    formula_note = fields.Char(string="Formula Note", readonly=True,
                               help="Why the Commission Formula produced no commission.")

    # ------------------------------------------------------------------
    # Rebuild
    # ------------------------------------------------------------------
    @api.model
    def _get_period_move_lines(self, plan, partner, date_from, date_to,
                               by_line, apply_rules=True):
        """Product lines of POSTED customer invoices and credit notes dated
        (invoice date) inside the window.

        * by_line=True  : lines whose own Sales Person (Partner) is `partner`
                          (Attribution Level 'Invoice Line').
        * by_line=False : lines of the moves whose header Sales Person
                          (Partner) is `partner` (Attribution Level 'Invoice').
        Down payment lines are left out (the final invoice carries the whole
        amount), like the sale order based levels."""
        domain = [
            ('move_id.move_type', 'in', INVOICE_TYPES),
            ('move_id.state', '=', 'posted'),
            ('display_type', '=', 'product'),
        ]
        if by_line:
            domain.append(('salesperson_partner_id', '=', partner.id))
        else:
            domain.append(('move_id.salesperson_partner_id', '=', partner.id))
        if plan.company_id:
            domain.append(('company_id', '=', plan.company_id.id))
        if date_from:
            domain.append(('move_id.invoice_date', '>=', date_from))
        if date_to:
            domain.append(('move_id.invoice_date', '<=', date_to))
        lines = self.env['account.move.line'].sudo().search(domain)
        lines = lines.filtered(
            lambda l: not any(sl.is_downpayment for sl in l.sale_line_ids))
        if not apply_rules:
            return lines
        return lines.filtered(plan._line_matches_achievement_rules)

    @api.model
    def _get_invoice_period_figures(self, plan, partner, date_from, date_to):
        """(achieved, qty, count) for the invoice based Attribution Levels.

        achieved = amount invoiced - amount credited by credit notes, untaxed,
        in company currency. A product line of a customer invoice has a
        credit (negative) balance and one of a credit note a debit (positive)
        balance, so ``-balance`` is already signed: + for invoices, - for
        credit notes. Quantities follow the same sign."""
        by_line = plan.commission_based_on == 'account_move_line'
        lines = self._get_period_move_lines(
            plan, partner, date_from, date_to, by_line)
        achieved = -sum(lines.mapped('balance'))
        qty = sum(
            l.quantity * (-1 if l.move_id.move_type == 'out_refund' else 1)
            for l in lines)
        count = len(lines) if by_line else len(lines.move_id)
        return achieved, qty, count

    @api.model
    def _get_period_figures(self, plan, partner, date_from, date_to):
        """(achieved, qty, count) of `partner` under `plan` in the window,
        from posted customer invoices minus credit notes (untaxed, company
        currency). See _get_invoice_period_figures."""
        return self._get_invoice_period_figures(plan, partner, date_from, date_to)

    @api.model
    def _get_period_adjustment(self, plan, partner, date_from, date_to):
        """(net, count) of the manual adjustments (sale.commission.achievement)
        of `partner` under `plan` dated inside the window.

        net = sum of the adjustments that ADD to this partner's line of the
        plan minus those that REDUCE from it. Amounts are converted to company
        currency the same way the native model stores them
        (``achieved / currency_rate``)."""
        Adjustment = self.env['sale.commission.achievement'].sudo()
        domain = []
        if date_from:
            domain.append(('date', '>=', date_from))
        if date_to:
            domain.append(('date', '<=', date_to))

        def _total(field_name):
            records = Adjustment.search(domain + [
                ('%s.plan_id' % field_name, '=', plan.id),
                ('%s.partner_id' % field_name, '=', partner.id),
            ])
            return (sum(r.achieved / (r.currency_rate or 1.0) for r in records),
                    len(records))

        added, added_count = _total('add_partner_id')
        reduced, reduced_count = _total('reduce_partner_id')
        return added - reduced, added_count + reduced_count

    @api.model
    def _rebuild(self):
        self = self.sudo()
        self.env.cr.execute("DELETE FROM sale_commission_partner_report")
        self.env.invalidate_all()
        # Every approved plan that lists sales person partners, whatever its
        # Attribution Level ('Sale Order' = order total, 'Sale Order Line' =
        # per line). Plans without partners are native user plans: untouched.
        plans = self.env['sale.commission.plan'].search([
            ('active', '=', True), ('state', '=', 'approved'),
        ])
        _logger.info(
            "bt_sale_commission: %s approved plan(s): %s",
            len(plans), plans.mapped('name'))
        vals_list = []
        for plan in plans:
            company = plan.company_id or self.env.company
            if not plan.partner_line_ids:
                _logger.info(
                    "bt_sale_commission: plan %r has no partner on its Sales "
                    "People (Partners) tab.", plan.name)
            for pline in plan.partner_line_ids:
                partner = pline.partner_id
                window = pline._get_window()
                if window is None:
                    continue
                for target, period_from, period_to in plan._get_commission_periods():
                    # sales count only inside the partner's own From/To
                    effective = intersect_dates(period_from, period_to, window[0], window[1])
                    if effective is None:
                        continue
                    date_from, date_to = effective
                    achieved, qty, count = self._get_period_figures(
                        plan, partner, date_from, date_to)
                    # manual adjustments (Add to / Reduce From) of this partner
                    adjustment, adjustment_count = self._get_period_adjustment(
                        plan, partner, date_from, date_to)
                    _logger.info(
                        "bt_sale_commission: plan %r (%s), %r, %s..%s -> %s "
                        "order(s)/line(s), %s adjustment(s)", plan.name,
                        plan.commission_based_on, partner.display_name,
                        date_from, date_to, count, adjustment_count)
                    if not count and not adjustment_count:
                        continue
                    achieved += adjustment
                    target_amount = target.amount if target else 0.0
                    vals_list.append({
                        'partner_id': partner.id,
                        'plan_id': plan.id,
                        'target_id': target.id or False,
                        'date_from': date_from,
                        'date_to': date_to,
                        'company_id': company.id,
                        'currency_id': company.currency_id.id,
                        'achieved': achieved,
                        'adjustment': adjustment,
                        'qty': qty,
                        'target_amount': target_amount,
                        'achieved_rate': (achieved / target_amount) if target_amount else 0.0,  # ratio: 1.0 = 100%
                    })
        rows = self.create(vals_list)
        for row in rows:
            row.commission = row._compute_row_commission()
        return len(rows)

    def _row_lines(self):
        """account.move.line records (product lines of posted customer invoices
        / credit notes, Achievements rules applied) that make up this row's
        Achieved: the partner's lines in the row's window."""
        self.ensure_one()
        return self._get_period_move_lines(
            self.plan_id, self.partner_id, self.date_from, self.date_to,
            by_line=self.plan_id.commission_based_on == LINE_MODE)

    def _company_achieved(self):
        """Untaxed invoiced minus credited of EVERY sales person of the company
        in the row's window (same lines rules as Achieved); used for company
        targets. Signed: credit notes reduce it."""
        self.ensure_one()
        plan = self.plan_id
        by_line = plan.commission_based_on == LINE_MODE
        domain = [
            ('move_id.move_type', 'in', INVOICE_TYPES),
            ('move_id.state', '=', 'posted'),
            ('display_type', '=', 'product'),
            ('company_id', '=', self.company_id.id),
            ('salesperson_partner_id' if by_line else 'move_id.salesperson_partner_id',
             '!=', False),
        ]
        if self.date_from:
            domain.append(('move_id.invoice_date', '>=', self.date_from))
        if self.date_to:
            domain.append(('move_id.invoice_date', '<=', self.date_to))
        lines = self.env['account.move.line'].sudo().search(domain)
        lines = lines.filtered(
            lambda l: not any(sl.is_downpayment for sl in l.sale_line_ids)
        ).filtered(plan._line_matches_achievement_rules)
        return -sum(lines.mapped('balance'))

    def _formula_context(self, **extra):
        """Variables of the Commission Formula. The formula runs ONCE per row
        (sales person partner x target period) at both Attribution Levels:
        ``achieved`` is the partner's total of the row."""
        self.ensure_one()
        lines = self._row_lines()
        sign = lambda l: -1.0 if l.move_id.move_type == 'out_refund' else 1.0
        plan = self.plan_id
        ctx = {
            'row': self,
            'plan': plan,
            'partner': self.partner_id,
            'achieved': self.achieved,
            'total_sold': self.achieved,        # alias of achieved
            'amount_invoiced': self.achieved,   # alias of achieved
            'amount_invoice': self.achieved,    # alias of achieved
            'adjustment': self.adjustment,
            'total_sold_qty': self.qty,
            'qty_invoiced': self.qty,
            'qty_invoice': self.qty,
            'target': self.target_id,
            'target_amount': self.target_amount,
            'target_completion': (self.achieved / self.target_amount) if self.target_amount else 0.0,
            'target_commission': self._target_commission_helper(),
            'company_target': plan.company_target or 0.0,
            'company_achieved': self._company_achieved(),
            'lines': lines,
            'moves': lines.move_id,
            # signed untaxed amount / quantity of an invoice line (credit notes < 0)
            'line_amount': lambda l: -l.balance,
            'line_qty': lambda l: l.quantity * sign(l),
            'date_from': self.date_from,
            'date_to': self.date_to,
            'currency': self.currency_id,
            'company': self.company_id,
            'env': self.env,
            'result': None,
            'result_type': None,
        }
        ctx.update(extra)
        return ctx

    def _target_commission_helper(self):
        """Callable given to the formula: target_commission(completion) -> the
        amount of the plan's Commissions tab (target completion -> commission,
        interpolated); 0 when the plan has no such table."""
        plan = self.plan_id

        def target_commission(completion):
            lines = plan._get_target_commission_lines()
            return plan._interpolate_target_commission(lines, completion) if lines else 0.0
        return target_commission

    def _compute_row_commission(self):
        """Commission of the row, from the plan's Python formula (both
        Attribution Levels). Nothing achieved -> no commission. A plan WITHOUT
        any formula uses the Commissions tab (target completion)."""
        self.ensure_one()
        plan = self.plan_id
        if not self.achieved:
            self.formula_note = "Achieved is 0: no commission."
            return 0.0
        if not plan.commission_python_compute:
            lines = plan._get_target_commission_lines()
            if lines and self.target_amount:
                self.formula_note = False
                return plan._interpolate_target_commission(
                    lines, self.achieved / self.target_amount)
        result, result_type, error = plan._run_commission_formula(self._formula_context())
        self.formula_note = error or False
        if error:
            return 0.0
        return plan._compute_commission_amount(result, result_type, self.achieved)

    @api.model
    def _ensure_fresh(self, force=False):
        param = self.env['ir.config_parameter'].sudo()
        now = time.time()
        try:
            last = float(param.get_param(PARAM_LAST) or 0)
        except ValueError:
            last = 0
        if force or now - last > REFRESH_SECONDS:
            param.set_param(PARAM_LAST, str(now))
            count = self._rebuild()
            _logger.info("bt_sale_commission: rebuilt %s commission rows.", count)

    @api.model
    def action_recalculate(self):
        """Rebuild, then open the report (menu entry)."""
        self._ensure_fresh(force=True)
        return self.env['ir.actions.act_window']._for_xml_id(
            'bt_sale_commission.sale_commission_partner_report_action')

    @api.model
    def _cron_rebuild(self):
        self._ensure_fresh(force=True)

    @api.model
    def _diagnose_invoice_plan(self, plan):
        """Why an 'Invoice' / 'Invoice Line' plan may give no rows."""
        out = []
        by_line = plan.commission_based_on == 'account_move_line'
        Line = self.env['account.move.line'].sudo()
        base = [('move_id.move_type', 'in', INVOICE_TYPES),
                ('display_type', '=', 'product')]
        field = 'salesperson_partner_id' if by_line else 'move_id.salesperson_partner_id'
        out.append("%s product line(s) on customer invoices/credit notes, %s of them on "
                   "POSTED moves, %s of those with a %s Sales Person" % (
                       Line.search_count(base),
                       Line.search_count(base + [('move_id.state', '=', 'posted')]),
                       Line.search_count(base + [('move_id.state', '=', 'posted'),
                                                 (field, '!=', False)]),
                       'line' if by_line else 'invoice'))
        for pline in plan.partner_line_ids:
            total = Line.search_count(base + [('move_id.state', '=', 'posted'),
                                              (field, '=', pline.partner_id.id)])
            out.append("%s: %s posted line(s) carry this partner" % (
                pline.partner_id.display_name, total))
        return out

    @api.model
    def action_check_setup(self):
        """Tell the user, in plain words, why the report may be empty."""
        sudo = self.sudo()
        msgs = []
        plans = sudo.env['sale.commission.plan'].search([('active', '=', True)])
        if not plans:
            msgs.append("No commission plan exists.")
        for plan in plans:
            problems = []
            if plan.state != 'approved':
                problems.append("state is '%s' (must be approved)" % plan.state)
            if not plan.partner_line_ids:
                problems.append("no partner on Sales People tab")
            if not problems:
                periods = plan._get_commission_periods()
                for pline in plan.partner_line_ids:
                    window = pline._get_window()
                    count = 0
                    for _target, pf, pt in periods:
                        eff = intersect_dates(pf, pt, *(window or (None, None))) if window else None
                        if eff is not None:
                            count += sudo._get_period_figures(
                                plan, pline.partner_id, eff[0], eff[1])[2]
                            count += sudo._get_period_adjustment(
                                plan, pline.partner_id, eff[0], eff[1])[1]
                    if not count:
                        problems.append(
                            "%s has 0 posted %s (and no adjustment) inside "
                            "the plan's target periods and the partner's From/To"
                            % (pline.partner_id.display_name,
                               'invoice lines' if plan.commission_based_on == LINE_MODE
                               else 'invoices / credit notes'))
            if plan.commission_based_on in INVOICE_MODES and plan.partner_line_ids:
                problems += sudo._diagnose_invoice_plan(plan)
            msgs.append("Plan '%s' [%s]: %s" % (
                plan.name, plan.commission_based_on, '; '.join(problems) or 'OK'))
        inv_lines = sudo.env['account.move.line']
        inv_base = [('move_id.move_type', 'in', INVOICE_TYPES),
                    ('move_id.state', '=', 'posted'),
                    ('display_type', '=', 'product')]
        msgs.append("Posted invoice/credit note lines: %s, of which with an Invoice "
                    "Sales Person: %s, with a Line Sales Person: %s" % (
                        inv_lines.search_count(inv_base),
                        inv_lines.search_count(inv_base + [('move_id.salesperson_partner_id', '!=', False)]),
                        inv_lines.search_count(inv_base + [('salesperson_partner_id', '!=', False)])))
        msgs.append("Partners ticked Sales Person: %s" % sudo.env['res.partner'].search_count(
            [('is_salesperson', '=', True)]))
        msgs.append("Commission rows now: %s" % sudo.search_count([]))
        _logger.info("bt_sale_commission setup check: %s", msgs)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': "Commission setup check",
                'message': "  |  ".join(msgs),
                'type': 'info',
                'sticky': True,
            },
        }
