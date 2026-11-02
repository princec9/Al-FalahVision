# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    is_salesperson = fields.Boolean(
        string="Sales Person",
        help="Tick to make this partner selectable as the sales person on "
             "sale orders and sale order lines. Commission is earned by "
             "these partners.",
    )
    commission_partner_line_ids = fields.One2many(
        'sale.commission.plan.partner', 'partner_id',
        string="Commission Plan Lines")
    commission_plan_ids = fields.Many2many(
        'sale.commission.plan',
        string="Commission Plans",
        compute='_compute_commission_plan_ids',
        help="Plans that list this partner on their Sales People tab.",
    )

    @api.depends('commission_partner_line_ids.plan_id')
    def _compute_commission_plan_ids(self):
        for partner in self:
            partner.commission_plan_ids = partner.commission_partner_line_ids.plan_id
