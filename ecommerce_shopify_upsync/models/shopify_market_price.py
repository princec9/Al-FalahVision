# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ShopifyMarketPrice(models.Model):
    """Shopify Markets: per-market inclusion + fixed pricing for one offer/variant.

    Shopify Markets configuration itself (currencies, countries, domains) is managed
    in Shopify Admin — Odoo does not attempt to mirror that setup. This model only
    holds the two things Odoo can meaningfully push per market:
      - `included`: whether the product should be published to that market's
        catalog (pushed via the same publish/unpublish publication-id mechanism
        used for regular sales channels — see `utils_upsync._publish_ids_to_publications`).
      - `price` / `compare_at_price`: a fixed override price for that market's
        Price List (pushed via `priceListFixedPricesAdd`).
    """
    _name = 'shopify.market.price'
    _description = 'Shopify Market Price'
    _order = 'name'

    offer_id = fields.Many2one(
        comodel_name='ecommerce.offer',
        string="Offer",
        required=True,
        ondelete='cascade',
    )
    name = fields.Char(string="Market", required=True, help="Label only, e.g. 'Canada' or 'European Union'.")
    market_catalog_publication_id = fields.Char(
        string="Market Catalog Publication ID",
        help="GID of the market's catalog Publication in Shopify Admin > Settings > Markets. "
             "Controls the CSV 'Included / Country' behavior (publish/unpublish to this market).",
    )
    market_price_list_id = fields.Char(
        string="Market Price List ID",
        help="GID of the market's Price List in Shopify Admin > Settings > Markets. "
             "Controls the CSV 'Price / Country' and 'Compare At Price / Country' behavior.",
    )
    currency_code = fields.Char(
        string="Currency Code",
        help="ISO currency code of the market's Price List, e.g. CAD, EUR.",
    )
    included = fields.Boolean(string="Included", default=True)
    price = fields.Float(string="Price")
    compare_at_price = fields.Float(string="Compare At Price")

    _unique_offer_market = models.Constraint(
        'UNIQUE(offer_id, name)',
        "This market is already configured for this offer.",
    )
