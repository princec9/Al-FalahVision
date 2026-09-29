import logging
from odoo import models, fields, api
from dateutil.relativedelta import relativedelta
from odoo.exceptions import UserError, ValidationError
_logger = logging.getLogger(__name__)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # Line serial number
    serial_number = fields.Char(string="Serial Number")


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_create_warranty_claim(self):
        self.ensure_one()

        # Check whether the Sale Order has a completed delivery
        done_delivery = self.picking_ids.filtered(
            lambda p: p.picking_type_code == 'outgoing'
                      and p.state == 'done'
        )

        if not done_delivery:
            raise UserError(
                _("You cannot create a Warranty Claim until the delivery is completed.")
            )

        return {
            'type': 'ir.actions.act_window',
            'name': _('Create Warranty Claim'),
            'res_model': 'warranty.claim',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_sale_order_id': self.id,
            },
        }

    # Count of warranties
    warranty_count = fields.Integer(compute="_compute_warranty_count", string="Warranties", store=False)

    def _compute_warranty_count(self):
        for order in self:
            order.warranty_count = self.env['product.warranty'].search_count(
                [('sale_order_id', '=', order.id)]
            )

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

    # Auto-create warranty on order confirmation
    def action_confirm(self):
        res = super().action_confirm()

        warranty_trigger = self.env['ir.config_parameter'].sudo().get_param(
            'is_product_warranty.create_warranty_from', 'manual'
        )

        for order in self:
            if warranty_trigger == 'sale_order':
                for line in order.order_line:
                    product = line.product_id
                    if not product:
                        continue

                    if not order._check_product_under_warranty(product):
                        continue

                    exists = self.env['product.warranty'].search([
                        ('sale_order_id', '=', order.id),
                        ('product_id', '=', product.id)
                    ], limit=1)

                    if not exists:
                        warranty_months = order._get_warranty_months(product)
                        start_date = fields.Date.today()
                        end_date = start_date + relativedelta(months=warranty_months)
                        self.env['product.warranty'].create({
                            'product_id': product.id,
                            'sale_order_id': order.id,
                            'partner_id': order.partner_id.id,
                            'serial_number': line.serial_number or 'N/A',
                            'start_date': start_date,
                            'warranty_start_date': start_date,
                            'warranty_end_date': end_date,
                            'warranty_period': warranty_months,
                            'state': 'draft',
                        })
                        _logger.info("Warranty created for: %s", product.name)

            order._warranty_create_purchase_order()

        return res

    # Helper to generate POs
    def _warranty_create_purchase_order(self):
        order = self
        supplier_lines = {}

        for line in order.order_line:
            product = line.product_id
            if not product:
                continue

            seller = product.seller_ids and product.seller_ids[0] or False
            if not seller:
                continue

            supplier = seller.partner_id
            if supplier not in supplier_lines:
                supplier_lines[supplier] = []

            uom = getattr(line, 'product_uom_id', None) or getattr(line, 'product_uom', None)
            supplier_lines[supplier].append({
                'product_id': product.id,
                'name': product.display_name,
                'product_qty': line.product_uom_qty,
                'product_uom_id': uom.id if uom else product.uom_po_id.id,
                'price_unit': seller.price or product.standard_price,
                'date_planned': fields.Datetime.now(),
            })

        for supplier, lines in supplier_lines.items():
            existing_po = self.env['purchase.order'].search([
                ('sale_order_id', '=', order.id),
                ('partner_id', '=', supplier.id),
            ], limit=1)

            if existing_po:
                continue

            po = self.env['purchase.order'].create({
                'partner_id': supplier.id,
                'sale_order_id': order.id,
                'order_line': [(0, 0, l) for l in lines],
            })
            _logger.info("PO created: %s for supplier: %s", po.name, supplier.name)

    # Action to view linked warranties
    def action_view_warranty(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Warranties',
            'res_model': 'product.warranty',
            'domain': [('sale_order_id', '=', self.id)],
            'view_mode': 'list,form',
            'context': {'default_sale_order_id': self.id},
            'target': 'current',
        }
    