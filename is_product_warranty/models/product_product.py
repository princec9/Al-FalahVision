from odoo import fields, models

class ProductProduct(models.Model):
    _inherit = 'product.product'

    # Warranty fields linked to product template
    under_warranty = fields.Boolean(related='product_tmpl_id.under_warranty', store=True)
    warranty_period = fields.Integer(related='product_tmpl_id.warranty_period', store=True)
    allow_renewal = fields.Boolean(related='product_tmpl_id.allow_renewal', store=True)
    allow_warranty_renewal_times = fields.Integer(related='product_tmpl_id.allow_warranty_renewal_times', store=True)
    warranty_renewal_period = fields.Integer(related='product_tmpl_id.warranty_renewal_period', store=True)
    warranty_renewal_cost = fields.Float(related='product_tmpl_id.warranty_renewal_cost', store=True)

    # Delegated product actions
    def action_update_quantity(self):
        return self.product_tmpl_id.action_update_quantity()

    def action_replenish(self):
        return self.product_tmpl_id.action_replenish()

    def action_print_labels(self):
        return self.product_tmpl_id.action_print_labels()

    def action_apply(self):
        return self.product_tmpl_id.action_apply()

    def action_cancel(self):
        return self.product_tmpl_id.action_cancel()
    