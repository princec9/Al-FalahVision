# -*- coding: utf-8 -*-
"""
sale.commission.plan.partner
==============================

Partner twin of native ``sale.commission.plan.user`` (the plan's "Sales
People" tab): same fields, constraints and "Other plans" logic, with
``res.partner`` (flag ``is_salesperson``) in place of ``res.users``.
"""
from odoo import Command, _, api, exceptions, fields, models


def intersect_dates(from_a, to_a, from_b, to_b):
    """Intersection of two date ranges; an empty bound is open-ended.
    Returns (date_from, date_to) or None when the ranges do not overlap."""
    starts = [d for d in (from_a, from_b) if d]
    ends = [d for d in (to_a, to_b) if d]
    date_from = max(starts) if starts else False
    date_to = min(ends) if ends else False
    if date_from and date_to and date_from > date_to:
        return None
    return date_from, date_to


class SaleCommissionPlanPartner(models.Model):
    _name = 'sale.commission.plan.partner'
    _description = 'Commission Plan Sales Person (Partner)'
    _rec_name = 'partner_id'
    _rec_names_search = ['partner_id', 'plan_id']
    _order = 'id'

    plan_id = fields.Many2one(
        'sale.commission.plan', required=True, index=True, ondelete='cascade')
    partner_id = fields.Many2one(
        'res.partner', "Salesperson", required=True, index=True,
        domain="[('is_salesperson', '=', True)]", ondelete='restrict')

    date_from = fields.Date(
        "From", compute='_compute_date_from', store=True, readonly=False)
    date_to = fields.Date("To")
    company_id = fields.Many2one(related='plan_id.company_id', store=True)

    other_plans = fields.Many2many(
        'sale.commission.plan', string="Other plans",
        compute='_compute_other_plans', readonly=False)

    _partner_uniq = models.Constraint(
        'unique (plan_id, partner_id)',
        "The sales person is already present in the plan",
    )

    @api.constrains('date_from', 'date_to')
    def _date_constraint(self):
        for line in self:
            if line.date_to and line.date_from and line.date_to < line.date_from:
                raise exceptions.UserError(_("The assignment start date must be before the end date"))
            if line.date_from and line.plan_id.date_from and line.date_from < line.plan_id.date_from:
                raise exceptions.UserError(_("The sales person's assignment must be within the commission plan period."))
            if line.date_from and line.plan_id.date_to and line.date_from > line.plan_id.date_to:
                raise exceptions.UserError(_("Sales person period cannot start after the plan."))
            if line.date_to and line.plan_id.date_to and line.date_to > line.plan_id.date_to:
                raise exceptions.UserError(_("Sales person period cannot end after the plan."))

    def _compute_display_name(self):
        for record in self:
            record.display_name = _("%(partner)s - %(plan)s",
                                    plan=record.plan_id.name,
                                    partner=record.partner_id.name)

    def _get_window(self):
        """(date_from, date_to) in which this partner earns under the plan:
        the row's own dates limited to the plan's dates; None if empty."""
        self.ensure_one()
        plan_from, plan_to = self.plan_id._get_plan_dates()
        return intersect_dates(self.date_from, self.date_to, plan_from, plan_to)

    @api.depends('partner_id', 'plan_id.date_from', 'plan_id.date_to',
                 'date_from', 'date_to')
    def _compute_other_plans(self):
        lines = self.sudo().search([
            ('partner_id', 'in', self.partner_id.ids),
            ('plan_id.state', 'in', ['draft', 'approved']),
        ])
        for line in self:
            window = line._get_window() if line.plan_id and line.partner_id else None
            other_ids = []
            if window is not None:
                for other in lines:
                    if other.partner_id != line.partner_id:
                        continue
                    if other.plan_id in (line.plan_id, line.plan_id._origin):
                        continue
                    other_window = other._get_window()
                    if other_window is None:
                        continue
                    if intersect_dates(window[0], window[1],
                                       other_window[0], other_window[1]) is None:
                        continue  # no overlap
                    other_ids.append(other.plan_id.id)
            line.other_plans = [Command.set(other_ids)] if other_ids else [Command.clear()]

    @api.depends('plan_id', 'plan_id.date_from')
    def _compute_date_from(self):
        for line in self:
            if not line.plan_id.date_from:
                continue
            line.date_from = line.plan_id.date_from
