# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    ecommerce_line_identifier = fields.Char(
        string="E-commerce order line ID",
        readonly=True, copy=False)

    ecommerce_account_id = fields.Many2one(
        related="order_id.ecommerce_account_id",
        store=True,
    )

    shopify_line_properties = fields.Text(
        string="Custom Properties",
        help="Custom line item properties imported from Shopify (e.g. prescription values).",
        readonly=True, copy=False,
    )

    order_date_order = fields.Datetime(
        related='order_id.date_order', string="Order Date", store=False,
    )

    _unique_ecommerce_account_ecommerce_line_identifier = models.Constraint(
        'UNIQUE(ecommerce_account_id, ecommerce_line_identifier)',
        "E-commerce order line identifier should be unique per ecommerce account.",
    )

    def _prepare_invoice_line(self, **optional_values):
        res = super()._prepare_invoice_line(**optional_values)
        if self.env.context.get('ecommerce_auto_invoice') and not self.display_type:
            from odoo.tools.float_utils import float_is_zero
            precision = self.env['decimal.precision'].precision_get('Product Unit')
            remaining_qty = self.product_uom_qty - self.qty_invoiced
            if not float_is_zero(remaining_qty, precision_digits=precision):
                res['quantity'] = remaining_qty
        return res
