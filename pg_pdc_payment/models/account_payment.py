from odoo import api, fields, models


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    is_pdc = fields.Boolean(
        string='Is PDC',
        compute='_compute_is_pdc',
    )
    pdc_check_number = fields.Char(string='Cheque Number', copy=False, tracking=True)
    pdc_check_date = fields.Date(string='Cheque Date', copy=False, tracking=True)

    @api.depends('payment_method_line_id', 'payment_method_line_id.name')
    def _compute_is_pdc(self):
        for payment in self:
            line = payment.payment_method_line_id
            payment.is_pdc = bool(line) and line._is_pdc()
