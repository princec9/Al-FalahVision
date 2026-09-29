from odoo import fields, models

class ProductCategory(models.Model):
    _inherit = 'product.category'

    # Warranty configuration fields
    under_warranty = fields.Boolean("Under Warranty")
    warranty_period = fields.Integer("Warranty Period (Months)", default=12)
    allow_renewal = fields.Boolean("Allow Renewal")
    allow_warranty_renewal_times = fields.Integer("Renewal Times", default=2)
    warranty_renewal_period = fields.Integer("Renewal Period (Months)", default=4)
    warranty_renewal_cost = fields.Float("Renewal Cost", default=2500.0)

    # Sync category changes to child products
    def write(self, vals):
        fields_to_sync = ['under_warranty', 'warranty_period', 'allow_renewal', 
                          'allow_warranty_renewal_times', 'warranty_renewal_period', 
                          'warranty_renewal_cost']
        
        updates = {f: vals[f] for f in fields_to_sync if f in vals}
        res = super().write(vals)
        
        # Update all products in this category and sub-categories
        if updates:
            all_cats = self.env['product.category'].search([('id', 'child_of', self.ids)])
            products = self.env['product.template'].search([('categ_id', 'in', all_cats.ids)])
            products.write(updates)
        return res
    