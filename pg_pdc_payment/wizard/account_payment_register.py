from odoo import api, fields, models


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    is_pdc = fields.Boolean(string='Is PDC', compute='_compute_is_pdc')
    pdc_check_number = fields.Char(string='Cheque Number')
    pdc_check_date = fields.Date(string='Cheque Date')

    @api.depends('payment_method_line_id', 'payment_method_line_id.name')
    def _compute_is_pdc(self):
        for wizard in self:
            line = wizard.payment_method_line_id
            wizard.is_pdc = bool(line) and line._is_pdc()

    def _pdc_vals(self):
        """Cheque values to store on the payment (only for the PCD method)."""
        self.ensure_one()
        if not self.is_pdc:
            return {}
        return {
            'pdc_check_number': self.pdc_check_number,
            'pdc_check_date': self.pdc_check_date,
        }

    def _create_payment_vals_from_wizard(self, batch_result):
        vals = super()._create_payment_vals_from_wizard(batch_result)
        vals.update(self._pdc_vals())
        return vals

    def _create_payment_vals_from_batch(self, batch_result):
        vals = super()._create_payment_vals_from_batch(batch_result)
        vals.update(self._pdc_vals())
        return vals
