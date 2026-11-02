# -*- coding: utf-8 -*-
"""Carry the sales persons of a sale order / order line onto the invoice and
its lines when the invoice is created, so the value is there from the start
(the stored compute on account.move(.line) is only the fallback)."""
from odoo import models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _prepare_invoice(self):
        vals = super()._prepare_invoice()
        vals['salesperson_partner_id'] = self.salesperson_partner_id.id or False
        return vals


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _prepare_invoice_line(self, **optional_values):
        vals = super()._prepare_invoice_line(**optional_values)
        vals['salesperson_partner_id'] = self.salesperson_partner_id.id or False
        return vals
