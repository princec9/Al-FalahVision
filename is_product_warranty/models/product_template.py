from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    under_warranty = fields.Boolean(string="Under Warranty")
    warranty_period = fields.Integer(string="Warranty Period (Months)", default=0)
    create_warranty_from_sale = fields.Boolean(string="Auto Create Warranty")
    allow_renewal = fields.Boolean(string="Allow Renewal")
    allow_warranty_renewal_times = fields.Integer(string="Allow Warranty Renewal Times", default=0)
    warranty_renewal_period = fields.Integer(string="Warranty Renewal Period (Months)", default=0)
    warranty_renewal_cost = fields.Float(string="Warranty Renewal Cost", default=0)


class ProductProduct(models.Model):
    _inherit = 'product.product'

    under_warranty = fields.Boolean(
        related='product_tmpl_id.under_warranty', store=True, readonly=False)
    warranty_period = fields.Integer(
        related='product_tmpl_id.warranty_period', store=True, readonly=False)
    allow_renewal = fields.Boolean(
        related='product_tmpl_id.allow_renewal', store=True, readonly=False)
    allow_warranty_renewal_times = fields.Integer(
        related='product_tmpl_id.allow_warranty_renewal_times', store=True, readonly=False)
    warranty_renewal_period = fields.Integer(
        related='product_tmpl_id.warranty_renewal_period', store=True, readonly=False)
    warranty_renewal_cost = fields.Float(
        related='product_tmpl_id.warranty_renewal_cost', store=True, readonly=False)