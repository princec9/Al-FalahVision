from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductDiscountRule(models.Model):
    _name = 'product.discount.rule'
    _description = 'Product / Brand Discount Rule'
    _order = 'date_start desc, id desc'

    product_tmpl_id = fields.Many2one('product.template', string='Product', ondelete='cascade', index=True)
    brand_id = fields.Many2one('product.brand', string='Brand', ondelete='cascade', index=True)
    categ_id = fields.Many2one('product.category', string='Product Category',
                               help='Brand rules only. Leave empty to apply to all categories of the brand.')
    min_discount = fields.Float(string='Minimum Discount (%)', digits='Discount')
    max_discount = fields.Float(string='Maximum Discount (%)', digits='Discount', required=True)
    date_start = fields.Date(string='Start Date', required=True, default=fields.Date.context_today)
    date_end = fields.Date(string='End Date')

    @api.constrains('min_discount', 'max_discount', 'date_start', 'date_end')
    def _check_values(self):
        for rule in self:
            if not 0 <= rule.min_discount <= rule.max_discount <= 100:
                raise ValidationError(_('Discounts must satisfy 0 <= Minimum <= Maximum <= 100.'))
            if rule.date_end and rule.date_end < rule.date_start:
                raise ValidationError(_('End Date cannot be before Start Date.'))

    @api.model
    def _get_applicable_rule(self, product, date):
        """Active rule on the given date. Priority: product > brand + category > brand."""
        date = date or fields.Date.context_today(self)
        date_domain = [
            ('date_start', '<=', date),
            '|', ('date_end', '=', False), ('date_end', '>=', date),
        ]
        rule = self.search([('product_tmpl_id', '=', product.product_tmpl_id.id)] + date_domain, limit=1)
        if not rule and product.brand_id:
            brand_domain = [('brand_id', '=', product.brand_id.id)] + date_domain
            rule = self.search(brand_domain + [('categ_id', '=', product.categ_id.id)], limit=1) \
                or self.search(brand_domain + [('categ_id', '=', False)], limit=1)
        return rule