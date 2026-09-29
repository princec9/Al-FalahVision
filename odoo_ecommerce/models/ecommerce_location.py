# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models


class ECommerceLocation(models.Model):
    _name = 'ecommerce.location'
    _description = "E-commerce Location"

    name = fields.Char(string="Name")
    ecommerce_location_identifier = fields.Char(string="E-commerce Location Identifier", readonly=True)
    channel_code = fields.Char(
        related='ecommerce_account_id.channel_code',
    )

    ecommerce_account_id = fields.Many2one(
        comodel_name='ecommerce.account',
        string="E-commerce Account",
        required=True,
        ondelete='restrict',
    )
    matched_location_id = fields.Many2one(
        comodel_name='stock.location',
        string="Stock Location",
        domain=[('usage', '=', 'internal')],
        required=True,
    )

    sync_stock = fields.Boolean(
        string="Stock Synchronization", compute='_compute_sync_stock', store=True, readonly=False,
    )

    _unique_ecommerce_account_ecommerce_location_identifier = models.Constraint(
        'UNIQUE(ecommerce_account_id, ecommerce_location_identifier)',
        "E-commerce location identifier should be unique per ecommerce account.",
    )

    @api.depends('ecommerce_account_id.update_inventory')
    def _compute_sync_stock(self):
        for offer in self:
            offer.sync_stock = offer.ecommerce_account_id.update_inventory
