# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""Pre-migration for ecommerce_shopify 19.0.1.2.

This version moves the `product.shopify.image` model (and the
`product.template.shopify_image_ids` field pointing to it) down from
`ecommerce_shopify_upsync` into this module, so both the Shopify product
import (this module) and the Shopify product push (`ecommerce_shopify_upsync`)
read and write the same set of records instead of two disconnected ones — the
previous split caused imported images to be wiped on push, and pushed images
to be re-imported as duplicates on the next pull.

The Python model/field/view code already moved; what's left is re-homing the
existing `ir.model.data` rows (module ownership) from `ecommerce_shopify_upsync`
to `ecommerce_shopify` *before* this module's own registry loading reflects the
model. Without this, Odoo would see `ecommerce_shopify_upsync` no longer
declaring a model it used to own and drop the table (and its data) as part of
that module's own upgrade — and if `ecommerce_shopify_upsync` were ever
uninstalled on its own afterwards, it would still cascade-delete a model this
module now depends on.

Safe to run whether or not `ecommerce_shopify_upsync` is installed: the WHERE
clause simply matches zero rows if those `ir.model.data` entries never existed.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        UPDATE ir_model_data
        SET module = 'ecommerce_shopify'
        WHERE module = 'ecommerce_shopify_upsync'
          AND (
              name = 'model_product_shopify_image'
              OR name = 'field_product_template__shopify_image_ids'
              OR name = 'access_product_shopify_image'
              OR name LIKE 'field_product_shopify_image__%'
          )
    """)
    _logger.info(
        "ecommerce_shopify pre-migrate: re-homed %s ir.model.data row(s) for "
        "product.shopify.image from ecommerce_shopify_upsync to ecommerce_shopify.",
        cr.rowcount,
    )
