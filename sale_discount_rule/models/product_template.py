from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    discount_rule_ids = fields.One2many('product.discount.rule', 'product_tmpl_id', string='Discount Rules')
