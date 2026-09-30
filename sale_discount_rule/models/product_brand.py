from odoo import fields, models


class ProductBrand(models.Model):
    _inherit = 'product.brand'

    discount_rule_ids = fields.One2many('product.discount.rule', 'brand_id', string='Discount Rules')
