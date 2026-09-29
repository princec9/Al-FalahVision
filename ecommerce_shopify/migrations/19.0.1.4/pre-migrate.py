# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""Pre-migration for ecommerce_shopify 19.0.1.4.

`views/product_template_views.xml` (the "Shopify Gallery" read-only tab, view
`product_template_shopify_gallery_images_form_view`) was removed from this
module's manifest when the pull-side gallery field `shopify_gallery_image_ids`
was replaced by the shared `shopify_image_ids` field on `product.shopify.image`
(see the 19.0.1.2 migration). Removing the file drops the view from the
module's data, but the `ir.ui.view` row itself is only deleted by the
end-of-upgrade orphan-data cleanup, which runs *after* every module has
finished loading.

Until that cleanup runs, the stale view is still resolved as part of
`product.template`'s combined form-view arch — and since it still references
the now-nonexistent `shopify_gallery_image_ids` field, loading *any* other
product.template view (in practice, ecommerce_shopify_upsync's own
`views/product_template_views.xml`, loaded right after this module) fails
view validation with "Field 'shopify_gallery_image_ids' does not exist in
model 'product.template'" and aborts the whole upgrade. Delete it here,
before any other module gets a chance to touch a product.template view.

Safe to run whether or not the row still exists: the WHERE clause matches
zero rows on a fresh install or once already cleaned up.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT res_id FROM ir_model_data
        WHERE module = 'ecommerce_shopify'
          AND model = 'ir.ui.view'
          AND name = 'product_template_shopify_gallery_images_form_view'
    """)
    row = cr.fetchone()
    if not row:
        return

    cr.execute("DELETE FROM ir_ui_view WHERE id = %s", (row[0],))
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE module = 'ecommerce_shopify'
          AND model = 'ir.ui.view'
          AND name = 'product_template_shopify_gallery_images_form_view'
    """)
    _logger.info(
        "ecommerce_shopify pre-migrate: removed orphaned view "
        "product_template_shopify_gallery_images_form_view (id=%s), which "
        "still referenced the removed shopify_gallery_image_ids field.",
        row[0],
    )
