from odoo import models, fields, api
from dateutil.relativedelta import relativedelta


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    # Warranty relation fields
    sale_order_id = fields.Many2one('sale.order', string="Source Sale Order")
    warranty_ids = fields.One2many('product.warranty', 'purchase_order_id', string="Warranties")
    warranty_count = fields.Integer(compute="_compute_warranty_count", store=False)

    # Count computed field
    @api.depends('warranty_ids')
    def _compute_warranty_count(self):
        for order in self:
            order.warranty_count = self.env['product.warranty'].search_count(
                [('purchase_order_id', '=', order.id)]
            )

    # Action view
    def action_view_warranties(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Product Warranties',
            'res_model': 'product.warranty',
            'view_mode': 'list,form',
            'domain': [('purchase_order_id', '=', self.id)],
            'context': {'default_purchase_order_id': self.id},
            'target': 'current',
        }

    # Configuration helpers
    def _get_apply_on(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'is_product_warranty.apply_warranty_on', 'product'
        )

    def _check_product_under_warranty(self, product):
        apply_on = self._get_apply_on()
        if apply_on == 'product_variant':
            return product.under_warranty
        elif apply_on == 'product_category':
            return product.categ_id.under_warranty
        else:
            return product.product_tmpl_id.under_warranty

    def _get_warranty_months(self, product):
        apply_on = self._get_apply_on()
        if apply_on == 'product_variant':
            return product.warranty_period or 12
        elif apply_on == 'product_category':
            return product.categ_id.warranty_period or 12
        else:
            return product.product_tmpl_id.warranty_period or 12

    # Warranty creation on PO confirmation
    def button_confirm(self):
        res = super().button_confirm()

        warranty_trigger = self.env['ir.config_parameter'].sudo().get_param(
            'is_product_warranty.create_warranty_from', 'manual'
        )

        for order in self:
            for line in order.order_line:
                product = line.product_id
                if not product:
                    continue

                if warranty_trigger == 'po' and order._check_product_under_warranty(product):
                    exists = self.env['product.warranty'].search([
                        ('purchase_order_id', '=', order.id),
                        ('product_id', '=', product.id)
                    ], limit=1)

                    if not exists:
                        warranty_months = order._get_warranty_months(product)
                        start_date = fields.Date.today()
                        end_date = start_date + relativedelta(months=warranty_months)

                        self.env['product.warranty'].create({
                            'name': f"Warranty: {order.name}",
                            'product_id': product.id,
                            'purchase_order_id': order.id,
                            'partner_id': order.partner_id.id,
                            'serial_number': line.serial_number or 'N/A',
                            'start_date': start_date,
                            'warranty_start_date': start_date,
                            'warranty_end_date': end_date,
                            'warranty_period': warranty_months,
                            'state': 'draft',
                        })
        return res


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    # Line specific info
    serial_number = fields.Char(string="Serial Number")
    