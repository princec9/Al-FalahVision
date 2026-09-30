from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = 'product.category'

    discount_rule_ids = fields.One2many(
        'product.discount.rule', 'categ_id', string='Discount Rules',
        domain=[('brand_id', '=', False), ('product_tmpl_id', '=', False)],
    )
