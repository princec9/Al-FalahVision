# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    salesperson_partner_id = fields.Many2one(
        'res.partner',
        string="Order Sales Person",
        domain="[('is_salesperson', '=', True)]",
        index=True,
        copy=True,
        check_company=False,
        default=lambda self: self._default_salesperson_partner_id(),
        tracking=True,
        help="Partner (flagged Sales Person) who earns the commission on "
             "this order, for plans with Attribution Level \"Sale Order\". "
             "Order lines have their own, independent Line Sales Person.",
    )

    @api.model
    def _default_salesperson_partner_id(self):
        partner = self.env.user.partner_id
        return partner if partner.is_salesperson else self.env['res.partner']

    @api.model
    def _bt_backfill_salesperson_partner(self):
        """Orders created before the partner Sales Person field existed
        would earn nothing. Fill ONLY the empty ones from the old
        salesperson's partner (idempotent: safe on every upgrade). Order
        lines are NOT touched: their Line Sales Person is independent."""
        cr = self.env.cr
        cr.execute("""
            UPDATE sale_order so
               SET salesperson_partner_id = ru.partner_id
              FROM res_users ru
             WHERE ru.id = so.user_id
               AND so.salesperson_partner_id IS NULL
        """)
        self.env.invalidate_all()
