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

    @api.depends_context('show_employee_name')
    def _compute_display_name(self):
        super()._compute_display_name()
        if not self.env.context.get('show_employee_name'):
            return
        for partner in self:
            employee = partner.sudo().employee_ids[:1]
            if employee:
                partner.display_name = employee.name

    @api.model
    def _search_display_name(self, operator, value):
        domain = super()._search_display_name(operator, value)
        if self.env.context.get('show_employee_name'):
            employees = self.env['hr.employee'].sudo().search(
                [('name', operator, value)])
            domain = ['|', ('id', 'in', employees.work_contact_id.ids)] + list(domain)
        return domain