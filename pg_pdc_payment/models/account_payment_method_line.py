from odoo import models


class AccountPaymentMethodLine(models.Model):
    _inherit = 'account.payment.method.line'

    def _is_pdc(self):
        """True when this payment method line is named PDC (or PCD)."""
        self.ensure_one()
        name = (self.name or '').strip().upper()
        return name == 'PDC'