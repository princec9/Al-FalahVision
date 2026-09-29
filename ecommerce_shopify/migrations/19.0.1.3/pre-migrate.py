# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""Pre-migration for ecommerce_shopify 19.0.1.3.

This version adds a `UNIQUE(product_tmpl_id, shopify_media_id)` constraint on
`product.shopify.image` as a DB-level guarantee that a given Shopify image is
never imported twice for the same product — a safety net below the
application-level dedup in `_sync_shopify_product_images`, which already
matches by `shopify_media_id` but relied only on Python-level checks.

Any product still carrying duplicate rows from before that dedup fix (same
product_tmpl_id + shopify_media_id) must be cleaned up here, otherwise adding
the constraint during this module's own schema init would fail outright on
the existing duplicates. Rows with no `shopify_media_id` yet (locally-curated
images never pushed to Shopify — see ecommerce_shopify_upsync) are left alone:
NULL is never considered equal to NULL by a SQL UNIQUE constraint, so any
number of them can coexist safely.

For each duplicate group, the row with the lowest id is kept (arbitrary but
stable) and the rest are dropped.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT to_regclass('public.product_shopify_image')
    """)
    if not cr.fetchone()[0]:
        # Fresh install, or the table doesn't exist yet at this point — nothing to clean up.
        return

    cr.execute("""
        DELETE FROM product_shopify_image dupe
        USING product_shopify_image keeper
        WHERE dupe.id > keeper.id
          AND dupe.product_tmpl_id = keeper.product_tmpl_id
          AND dupe.shopify_media_id = keeper.shopify_media_id
          AND dupe.shopify_media_id IS NOT NULL
          AND dupe.shopify_media_id != ''
    """)
    _logger.info(
        "ecommerce_shopify pre-migrate: removed %s duplicate product.shopify.image "
        "row(s) before adding the UNIQUE(product_tmpl_id, shopify_media_id) constraint.",
        cr.rowcount,
    )
