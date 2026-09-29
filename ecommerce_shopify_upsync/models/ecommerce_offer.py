# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class ECommerceOffer(models.Model):
    _inherit = 'ecommerce.offer'

    shopify_inventory_item_id = fields.Char(
        string="Shopify Inventory Item ID",
        readonly=True,
        copy=False,
    )
    shopify_inventory_management = fields.Boolean(
        string="Shopify Manages Inventory",
        default=True,
    )
    # Variant Inventory Tracker: user-facing 'shopify'/'none' values matching
    # Shopify's own terminology, computed from (and writing back to)
    # `shopify_inventory_management` so the GraphQL push code — which only knows
    # about the InventoryItem.tracked boolean — doesn't need a second source of
    # truth. Kept in sync automatically; nothing else needs to change when this is added.
    shopify_inventory_tracker = fields.Selection(
        selection=[
            ('shopify', 'Shopify (tracked)'),
            ('none', 'None (not tracked)'),
        ],
        string="Inventory Tracker",
        compute='_compute_shopify_inventory_tracker',
        inverse='_inverse_shopify_inventory_tracker',
        store=True,
        default='shopify',
        help="Shopify's GraphQL API only exposes inventory tracking as a boolean "
             "(InventoryItem.tracked) — there is no separate 'tracker name' field "
             "like the legacy REST API/CSV had. 'Shopify' tracks quantity on "
             "Shopify; 'None' leaves it untracked (always available).",
    )
    shopify_inventory_policy = fields.Selection(
        selection=[
            ('DENY', 'Deny (stop selling when out of stock)'),
            ('CONTINUE', 'Continue (allow selling when out of stock)'),
        ],
        string="Inventory Policy",
        default='DENY',
    )
    shopify_taxable = fields.Boolean(
        string="Taxable",
        default=True,
    )
    shopify_compare_at_price = fields.Float(
        string="Compare At Price",
    )
    shopify_last_pushed = fields.Datetime(
        string="Last Pushed to Shopify",
        readonly=True,
        copy=False,
    )
    shopify_push_status = fields.Selection(
        selection=[
            ('pending', 'Pending'),
            ('pushed', 'Pushed'),
            ('error', 'Error'),
        ],
        string="Push Status",
        default='pending',
        readonly=True,
        copy=False,
    )
    shopify_push_error = fields.Text(
        string="Push Error",
        readonly=True,
        copy=False,
    )
    # Shopify Markets: per-market inclusion + fixed pricing lines for this offer.
    shopify_market_price_ids = fields.One2many(
        comodel_name='shopify.market.price',
        inverse_name='offer_id',
        string="Market Prices",
    )

    @api.depends('shopify_inventory_management')
    def _compute_shopify_inventory_tracker(self):
        for offer in self:
            offer.shopify_inventory_tracker = 'shopify' if offer.shopify_inventory_management else 'none'

    def _inverse_shopify_inventory_tracker(self):
        for offer in self:
            offer.shopify_inventory_management = offer.shopify_inventory_tracker == 'shopify'

    def write(self, vals):
        """Auto-push to Shopify when matched_product_id is set."""
        result = super().write(vals)
        if 'matched_product_id' in vals:
            for offer in self:
                account = offer.ecommerce_account_id
                if (account.channel_code != 'shopify'
                        or account.state != 'connected'
                        or not offer.matched_product_id):
                    continue
                if offer.ec_product_template_identifier:
                    continue
                template = offer.matched_product_id.product_tmpl_id
                all_template_offers = self.search([
                    ('ecommerce_account_id', '=', account.id),
                    ('matched_product_template_id', '=', template.id),
                    ('matched_product_id', '!=', False),
                ])
                try:
                    account._push_product_template_to_shopify(all_template_offers)
                    _logger.info("Auto-pushed product '%s' to Shopify.", template.name)
                except Exception as e:
                    _logger.error("Auto-push failed for '%s': %s", template.name, str(e))
                    offer.write({
                        'shopify_push_status': 'error',
                        'shopify_push_error': str(e),
                    })
        return result
