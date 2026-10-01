from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = 'product.category'

    # category-only rules (used when no brand rule applies)
    discount_rule_ids = fields.One2many(
        'product.discount.rule', 'categ_id', string='Discount Rules',
        domain=[('brand_id', '=', False), ('product_tmpl_id', '=', False)],
    )
