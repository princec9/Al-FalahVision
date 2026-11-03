from odoo import api, fields, models

from ..models.account_payment import _is_pdc_line


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    is_pdc_method = fields.Boolean(compute='_compute_is_pdc_method')
    cheque_date = fields.Date(string='Cheque Date')

    @api.depends('payment_method_line_id', 'payment_method_line_id.code',
                 'payment_method_line_id.name')
    def _compute_is_pdc_method(self):
        for wiz in self:
            wiz.is_pdc_method = _is_pdc_line(wiz.payment_method_line_id)

    @api.onchange('payment_method_line_id')
    def _onchange_payment_method_line_cheque_date(self):
        for wiz in self:
            if not _is_pdc_line(wiz.payment_method_line_id):
                wiz.cheque_date = False
            elif not wiz.cheque_date:
                wiz.cheque_date = wiz.payment_date

    def _create_payment_vals_from_wizard(self, batch_result):
        vals = super()._create_payment_vals_from_wizard(batch_result)
        vals['cheque_date'] = self.cheque_date if self.is_pdc_method else False
        return vals

    def _create_payment_vals_from_batch(self, batch_result):
        vals = super()._create_payment_vals_from_batch(batch_result)
        vals['cheque_date'] = self.cheque_date if self.is_pdc_method else False
        return vals
