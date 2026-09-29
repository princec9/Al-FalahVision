# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ShopifySalesChannel(models.Model):
    """A Shopify Publication — either a regular sales channel (Online Store, Point
    of Sale, a marketplace app, ...) or a Market's catalog publication. Populated by
    `ecommerce.account.action_fetch_shopify_sales_channels`, which calls the same
    `publications` query already used by the existing global/web/unpublished publish
    flow (`utils_upsync.fetch_shopify_sales_channels`).

    Selecting a set of these on `ecommerce.account.shopify_publish_channel_ids` and
    setting `shopify_upsync_published_scope` to 'custom' publishes pushed products to
    exactly that set, instead of only the 3 built-in presets.
    """
    _name = 'shopify.sales.channel'
    _description = 'Shopify Sales Channel / Market Catalog Publication'
    _order = 'name'

    name = fields.Char(string="Channel Name", required=True)
    shopify_publication_id = fields.Char(
        string="Shopify Publication ID",
        required=True,
        help="GID of the Shopify Publication, e.g. gid://shopify/Publication/123456789.",
    )
    ecommerce_account_id = fields.Many2one(
        comodel_name='ecommerce.account',
        string="E-commerce Account",
        required=True,
        ondelete='cascade',
    )
    is_publish_target = fields.Boolean(
        string="Publish Here",
        help="When 'Published Scope' is set to 'Custom (Selected Channels)', pushed "
             "products are published to every channel/market checked here.",
    )

    _unique_account_publication = models.Constraint(
        'UNIQUE(ecommerce_account_id, shopify_publication_id)',
        "This publication is already registered for this account.",
    )
