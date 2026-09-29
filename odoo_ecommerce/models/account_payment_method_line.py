# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class AccountPaymentMethodLine(models.Model):
    _inherit = "account.payment.method.line"

    ecommerce_payment_provider_code = fields.Char(
        string="E-commerce Payment Provider Code",
        help="Payment provider code used by E-commerce Platforms.",
        copy=False,
    )
