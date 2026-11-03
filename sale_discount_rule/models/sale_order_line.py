from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    # ------------------------------------------------------------------
    # No automatic discount: the pricelist discount is only used as a limit
    # ------------------------------------------------------------------


    def _get_display_price(self):
        """Unit price after the pricelist is applied.

        Standard Odoo may return the price *before* the pricelist discount
        and rely on the `discount` field to show the reduction. Since the
        discount is manual here, price_unit must already include it.
        """
        self.ensure_one()
        return self._get_pricelist_price()
    @api.depends('product_id')
    def _compute_discount(self):
        for line in self:
            line.discount = 0.0

    # ------------------------------------------------------------------
    # Limits: pricelist -> product -> brand -> category
    # ------------------------------------------------------------------
    def _get_pricelist_discount(self):
        """Discount % defined on the pricelist rule matched by this line."""
        self.ensure_one()
        item = self.pricelist_item_id
        if not (self.product_id and self.order_id.pricelist_id and item):
            return 0.0
        if item.compute_price == 'percentage':
            return item.percent_price
        if item.compute_price == 'formula':
            return max(item.price_discount, 0.0)
        return 0.0  # fixed price: no discount percentage

    def _get_discount_limit(self):
        """Return (limit, source) or (False, False) when nothing limits the line."""
        self.ensure_one()
        if not self.product_id or self.display_type:
            return False, False
        pricelist_discount = self._get_pricelist_discount()
        if pricelist_discount > 0:
            return pricelist_discount, _('pricelist')
        # date_order is stored in UTC; convert to the user's timezone first
        date = self.order_id.date_order and fields.Date.context_today(
            self, self.order_id.date_order)
        rule = self.env['product.discount.rule']._get_applicable_rule(self.product_id, date)
        if rule:
            return rule.max_discount, _('discount rule')
        return False, False

    def _get_exceeded_discount_limit(self):
        self.ensure_one()
        if not self.discount:
            return False, False
        limit, source = self._get_discount_limit()
        if source and float_compare(self.discount, limit, precision_digits=2) > 0:
            return limit, source
        return False, False

    def _discount_limit_message(self, limit, source):
        return _(
            'Discount %(disc).2f%% on "%(product)s" exceeds the maximum allowed %(max).2f%% (%(source)s).',
            disc=self.discount, product=self.product_id.display_name,
            max=limit, source=source,
        )

    def _reset_exceeded_discounts(self):
        """Reset over-limit discounts to the allowed maximum and return a warning."""
        messages = []
        for line in self:
            limit, source = line._get_exceeded_discount_limit()
            if source:
                messages.append(line._discount_limit_message(limit, source))
                line.discount = limit
        if messages:
            return {'warning': {
                'title': _('Discount Limit Exceeded'),
                'message': '\n'.join(messages) + '\n' + _(
                    'The discount has been reset to the maximum allowed.'),
            }}

    @api.onchange('product_id', 'discount')
    def _onchange_discount_limit(self):
        return self._reset_exceeded_discounts()

    @api.constrains('product_id', 'discount')
    def _check_discount_limit(self):
        for line in self:
            limit, source = line._get_exceeded_discount_limit()
            if source:
                raise ValidationError(line._discount_limit_message(limit, source))


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    @api.onchange('date_order', 'pricelist_id')
    def _onchange_date_order_discount_limit(self):
        return self.order_line._reset_exceeded_discounts()

    @api.constrains('date_order')
    def _check_date_order_discount_limit(self):
        self.order_line._check_discount_limit()
