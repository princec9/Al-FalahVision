# -*- coding: utf-8 -*-
from odoo import fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # Independent of sale.order.salesperson_partner_id: it is NOT computed or
    # copied from the order. Used only by plans whose Attribution Level is
    # "Sale Order Line".
    salesperson_partner_id = fields.Many2one(
        'res.partner',
        string="Line Sales Person",
        domain="[('is_salesperson', '=', True)]",
        index=True,
        copy=True,
        check_company=False,
        help="Partner (flagged Sales Person) who earns the commission on "
             "this line, for plans with Attribution Level \"Sale Order Line\". "
             "It is independent of the Order Sales Person on the order header.",
    )

    salesperson_order_date = fields.Datetime(
        string="Order Date",
        related='order_id.date_order',
        store=True,
        index=True,
    )
