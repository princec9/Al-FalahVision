from odoo import fields, models
from dateutil.relativedelta import relativedelta


class AccountMove(models.Model):
    _inherit = 'account.move'

    warranty_id = fields.Many2one('product.warranty', string="Warranty")

    # View warranty form
    def action_view_warranty_from_invoice(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Warranty',
            'res_model': 'product.warranty',
            'view_mode': 'form',
            'res_id': self.warranty_id.id,
            'target': 'current',
        }

    # Get warranty config setting
    def _get_apply_on(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'is_product_warranty.apply_warranty_on', 'product'
        )

    # Check if product has warranty
    def _check_product_under_warranty(self, product):
        apply_on = self._get_apply_on()
        if apply_on == 'product_variant':
            return product.under_warranty
        elif apply_on == 'product_category':
            return product.categ_id.under_warranty
        else:
            return product.product_tmpl_id.under_warranty

    # Calculate warranty period
    def _get_warranty_months(self, product):
        apply_on = self._get_apply_on()
        if apply_on == 'product_variant':
            return product.warranty_period or 12
        elif apply_on == 'product_category':
            return product.categ_id.warranty_period or 12
        else:
            return product.product_tmpl_id.warranty_period or 12

    # Auto-create warranty on invoice confirm
    def action_post(self):
        res = super().action_post()
        for move in self:
            if move.move_type != 'out_invoice':
                continue

            # Update existing warranty state
            warranty = self.env['product.warranty'].search(
                [('invoice_id', '=', move.id)], limit=1
            )
            if warranty:
                warranty.write({'state': 'invoiced'})

            # Check creation trigger setting
            warranty_trigger = self.env['ir.config_parameter'].sudo().get_param(
                'is_product_warranty.create_warranty_from', 'manual'
            )
            if warranty_trigger != 'invoice':
                continue

            # Generate new warranty records
            for line in move.invoice_line_ids:
                product = line.product_id
                if not product or not move._check_product_under_warranty(product):
                    continue

                exists = self.env['product.warranty'].search([
                    ('product_id', '=', product.id),
                    ('partner_id', '=', move.partner_id.id),
                    ('invoice_id', '=', move.id),
                ], limit=1)

                if not exists:
                    warranty_months = move._get_warranty_months(product)
                    start_date = fields.Date.today()
                    end_date = start_date + relativedelta(months=warranty_months)

                    # Create new warranty
                    new_warranty = self.env['product.warranty'].create({
                        'product_id': product.id,
                        'partner_id': move.partner_id.id,
                        'invoice_id': move.id,
                        'start_date': start_date,
                        'warranty_start_date': start_date,
                        'warranty_end_date': end_date,
                        'warranty_period': warranty_months,
                        'state': 'draft',
                    })
                    move.warranty_id = new_warranty.id
        return res
    