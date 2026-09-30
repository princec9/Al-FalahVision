from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _get_exceeded_discount_rule(self):
        """Return the applicable rule if the line discount exceeds its maximum."""
        self.ensure_one()
        if not self.product_id or not self.discount:
            return False
        # date_order is stored in UTC; convert to the user's timezone before taking the date
        date = self.order_id.date_order and fields.Date.context_today(self, self.order_id.date_order)
        rule = self.env['product.discount.rule']._get_applicable_rule(self.product_id, date)
        if rule and float_compare(self.discount, rule.max_discount, precision_digits=2) > 0:
            return rule
        return False

    def _discount_limit_message(self, rule):
        return _(
            'Discount %(disc).2f%% on "%(product)s" exceeds the maximum allowed %(max).2f%%.',
            disc=self.discount, product=self.product_id.display_name, max=rule.max_discount,
        )

    def _reset_exceeded_discounts(self):
        """Reset over-limit discounts to the allowed maximum and return a warning."""
        messages = []
        for line in self:
            rule = line._get_exceeded_discount_rule()
            if rule:
                messages.append(line._discount_limit_message(rule))
                line.discount = rule.max_discount
        if messages:
            return {'warning': {
                'title': _('Discount Limit Exceeded'),
                'message': '\n'.join(messages) + '\n' + _('The discount has been reset to the maximum allowed.'),
            }}

    @api.onchange('product_id', 'discount')
    def _onchange_discount_limit(self):
        return self._reset_exceeded_discounts()

    @api.constrains('product_id', 'discount')
    def _check_discount_limit(self):
        for line in self:
            rule = line._get_exceeded_discount_rule()
            if rule:
                raise ValidationError(line._discount_limit_message(rule))


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.onchange('date_order')
    def _onchange_date_order_discount_limit(self):
        return self.order_line._reset_exceeded_discounts()

    @api.constrains('date_order')
    def _check_date_order_discount_limit(self):
        self.order_line._check_discount_limit()
