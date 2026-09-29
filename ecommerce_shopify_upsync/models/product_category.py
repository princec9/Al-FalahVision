# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ProductCategory(models.Model):
    """Shopify taxonomy mapping shared by products in an Odoo category."""

    _inherit = 'product.category'

    shopify_category_id = fields.Char(
        string='Shopify Product Category',
        readonly=True,
        help='Shopify Standard Product Taxonomy category GID.',
    )
    shopify_collection_id = fields.Char(
        string='Shopify Collection ID',
        readonly=True,
        help='Shopify Collection GID or ID corresponding to this Odoo category. '
             'Reused across product pushes to avoid duplicate collections.',
    )
