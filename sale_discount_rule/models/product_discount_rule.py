from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductDiscountRule(models.Model):
    _name = 'product.discount.rule'
    _description = 'Product / Brand Discount Rule'
    _order = 'date_start desc, id desc'

    product_tmpl_id = fields.Many2one('product.template', string='Product', ondelete='cascade', index=True)
    brand_id = fields.Many2one('product.brand', string='Brand', ondelete='cascade', index=True)
    categ_id = fields.Many2one('product.category', string='Product Category', ondelete='cascade', index=True,
                               help='On a brand rule: limits it to this category (empty = all categories).\n'
                                    'Without brand/product: category rule, used when no brand rule applies.')
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
    def _get_applicable_rule(self, product, date=None):
        date = date or fields.Date.context_today(self)
        tmpl = product.product_tmpl_id
        date_domain = [
            '|', ('date_start', '=', False), ('date_start', '<=', date),
            '|', ('date_end', '=', False), ('date_end', '>=', date),
        ]
        # product's own category plus all parents
        categ_ids = [int(i) for i in product.categ_id.parent_path.split('/')[:-1]]

        # 1) Product rule
        rule = self.search(
            date_domain + [('product_tmpl_id', '=', tmpl.id)],
            order='id desc', limit=1)
        if rule:
            return rule

        # 2) Brand rule (optionally restricted to a category)
        if tmpl.brand_id:
            rules = self.search(date_domain + [
                ('brand_id', '=', tmpl.brand_id.id),
                ('product_tmpl_id', '=', False),
                '|', ('categ_id', '=', False), ('categ_id', 'in', categ_ids),
            ])
            if rules:
                # a category-specific brand rule beats a general brand rule
                return rules.sorted(lambda r: (bool(r.categ_id), r.id), reverse=True)[0]

        # 3) Category rule (no brand, no product), closest category first
        rules = self.search(date_domain + [
            ('brand_id', '=', False),
            ('product_tmpl_id', '=', False),
            ('categ_id', 'in', categ_ids),
        ])
        if rules:
            return rules.sorted(lambda r: len(r.categ_id.parent_path), reverse=True)[0]
        return self.browse()
