from odoo import fields, models


class MonthlySalesReportEntry(models.Model):
    _name = 'sunray.monthly.sales.report.entry'
    _description = 'Monthly Sales Report Entry'
    _order = 'date desc, id desc'

    date = fields.Date(required=True, default=fields.Date.context_today)
    customer_id = fields.Many2one('res.partner', string='Customer')
    salesperson_id = fields.Many2one('res.users', string='Store Incharge', default=lambda self: self.env.user)
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.company.currency_id, required=True)
    amount = fields.Monetary(currency_field='currency_id', required=True, default=0.0)
