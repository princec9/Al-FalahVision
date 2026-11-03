from odoo import api, fields, models


def _is_pdc_line(method_line):
    """True when the payment method line is the PDC method."""
    if not method_line:
        return False
    code = (method_line.code or '').strip().lower()
    name = (method_line.name or '').strip().upper()
    return code == 'pdc' or name == 'PDC'


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    is_pdc_method = fields.Boolean(
        string='Is PDC Method',
        compute='_compute_is_pdc_method',
    )
    cheque_date = fields.Date(
        string='Cheque Date',
        copy=False,
        tracking=True,
    )

    @api.depends('payment_method_line_id', 'payment_method_line_id.code',
                 'payment_method_line_id.name')
    def _compute_is_pdc_method(self):
        for pay in self:
            pay.is_pdc_method = _is_pdc_line(pay.payment_method_line_id)

    @api.onchange('payment_method_line_id')
    def _onchange_payment_method_line_cheque_date(self):
        for pay in self:
            if not _is_pdc_line(pay.payment_method_line_id):
                pay.cheque_date = False
            elif not pay.cheque_date:
                pay.cheque_date = pay.date

    @api.model_create_multi
    def create(self, vals_list):
        payments = super().create(vals_list)
        payments.filtered(lambda p: p.cheque_date and not p.is_pdc_method).write(
            {'cheque_date': False})
        return payments

    def write(self, vals):
        res = super().write(vals)
        if 'payment_method_line_id' in vals:
            self.filtered(lambda p: p.cheque_date and not p.is_pdc_method).write(
                {'cheque_date': False})
        return res
