# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ProductAttribute(models.Model):
    _inherit = 'product.attribute'

    # Option Linked To: when both are set, the Shopify option generated from this
    # attribute (see `ecommerce_account._prepare_shopify_product_data`) is linked to
    # a Shopify metaobject-backed metafield definition — e.g. namespace "shopify",
    # key "color-pattern" for Shopify's standard Color swatch picker. Only takes
    # effect when the option is first CREATED on Shopify; see
    # `utils_upsync.sync_product_option_linked_metafields` for why it can't be
    # changed afterward through the API.
    shopify_linked_metafield_namespace = fields.Char(
        string="Shopify Linked Metafield Namespace",
        help="e.g. 'shopify' for Shopify's built-in Color/Size/Material option metaobjects.",
    )
    shopify_linked_metafield_key = fields.Char(
        string="Shopify Linked Metafield Key",
        help="e.g. 'color-pattern' — must match an existing metaobject-backed metafield "
             "definition on the target Shopify store.",
    )
