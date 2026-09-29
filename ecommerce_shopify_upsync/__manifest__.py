# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'Shopify Product Upsync',
    'version': '19.0.2.0.0',
    'category': 'eCommerce',
    'summary': 'Push products from Odoo to Shopify',
    'description': """
        This module extends ecommerce_shopify to allow pushing products
        from Odoo to Shopify including:
        - Product title, description, vendor, type, tags
        - Handle, Standard Product Taxonomy category, SEO title/description
        - Published status and scope, including publish to arbitrary sales channels
        - Variants with SKU, barcode, price, compare-at price, weight (dynamic unit),
          requires-shipping, tax code, cost per item
        - Inventory management/tracker and policy settings
        - Product, additional, and variant images with alt text, explicit position,
          and variant-specific image linking
        - Gift card heuristic mapping
        - Generic Shopify Metafields framework (including Google Shopping fields)
        - Bidirectional product metafield synchronization (pull / push / sync)
          using the correct Shopify Admin GraphQL Product.metafields APIs
        - `metafieldsSet` mutation for upsert — existing metafields are updated
          in place by their Shopify ID (never duplicated)
        - `metafieldDefinitions` read with ownerType=PRODUCT
        - Stale detection: metafields deleted on Shopify are archived in Odoo
        - Account-level and per-product manual Pull / Push / Sync buttons
        - Webhook-driven sync for products/create, products/update, products/delete
        - Per-metafield sync status, direction, error and timestamp fields
        - Full pagination support on read
        - Expanded Shopify metafield type coverage (all standard + list.* types)
        - Shopify Markets: per-market inclusion and fixed pricing
        - Shopify option linked metafields (linked options)
    """,
    'author': 'Custom',
    'depends': ['ecommerce_shopify'],
    'data': [
        'security/ir.model.access.csv',
        'wizard/shopify_push_product_wizard_views.xml',
        'views/product_template_views.xml',
        'views/product_category_views.xml',
        'views/product_attribute_views.xml',
        'views/ecommerce_account_views.xml',
        'views/ecommerce_offer_views.xml',
        'views/shopify_metafield_definition_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
    'demo': [],
}
