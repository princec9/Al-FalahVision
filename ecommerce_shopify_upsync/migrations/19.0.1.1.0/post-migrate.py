# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""Post-migration for ecommerce_shopify_upsync 19.0.1.1.0.

This version adds several new fields/models (see module changelog / __manifest__.py).
Most of them are purely additive with safe defaults and need no data migration:
  - product.template: shopify_handle, shopify_category_id, shopify_seo_title,
    shopify_seo_description, is_gift_card, shopify_tax_code, shopify_metafield_ids
  - product.attribute: shopify_linked_metafield_namespace/key
  - ecommerce.account: shopify_sales_channel_ids, shopify_fulfillment_location_id,
    shopify_weight_unit
  - new models: shopify.product.metafield, shopify.sales.channel, shopify.market.price

The one field that genuinely needs backfilling is
`ecommerce.offer.shopify_inventory_tracker`: it's a stored compute field derived
from the pre-existing `shopify_inventory_management` boolean. Odoo recomputes new
stored compute fields automatically during `-u` for every module upgrade, so this
script is a defensive/explicit backfill (idempotent, safe to re-run) rather than a
strict requirement — it also runs a single UPDATE instead of loading every offer
into the ORM, which matters if there are a lot of them.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install, not an upgrade from an older version — nothing to backfill.
        return

    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'ecommerce_offer' AND column_name = 'shopify_inventory_tracker'
    """)
    if not cr.fetchone():
        # Column not created yet at this point in the upgrade (schema init runs
        # after post-migrate in some upgrade paths) — the ORM's own stored-compute
        # recomputation will populate it correctly regardless.
        _logger.info(
            "ecommerce_shopify_upsync post-migrate: shopify_inventory_tracker column "
            "not present yet, relying on ORM recompute instead of backfilling here."
        )
        return

    cr.execute("""
        UPDATE ecommerce_offer
        SET shopify_inventory_tracker = CASE
            WHEN shopify_inventory_management IS TRUE THEN 'shopify'
            ELSE 'none'
        END
        WHERE shopify_inventory_tracker IS NULL
    """)
    _logger.info(
        "ecommerce_shopify_upsync post-migrate: backfilled shopify_inventory_tracker "
        "on %s offer(s).", cr.rowcount,
    )
