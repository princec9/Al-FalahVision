import logging
from odoo import api, fields, models
from ..image_utils import decode_image_bytes, images_match

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    shopify_image_ids = fields.One2many(
        comodel_name='product.shopify.image',
        inverse_name='product_tmpl_id',
        string="Shopify Additional Images",
    )

    shopify_media_id = fields.Char(
        string="Shopify Main Image Media ID",
        readonly=True,
        copy=False,
    )
    shopify_image_url = fields.Char(
        string="Shopify Image URL",
        readonly=True,
        copy=False,
    )

    def _deduplicate_shopify_images(self):
        """Safely detect, merge and remove duplicate image records on this product template.
        - Identifies duplicate rows within template.shopify_image_ids using reliable content comparison.
        - Merges shopify_media_id / shopify_image_url to the primary record so mappings are preserved.
        - Safely unlinks duplicate rows in Odoo without deleting valid unique images.
        """
        for template in self:
            gallery = template.shopify_image_ids
            if len(gallery) <= 1:
                continue

            unique_entries = []
            to_remove = self.env['product.shopify.image'].browse()

            for img in gallery:
                if not img.image:
                    continue
                img_bytes = decode_image_bytes(img.image)
                if not img_bytes:
                    continue

                matched_primary = None
                for primary_rec, primary_bytes in unique_entries:
                    if images_match(img_bytes, primary_bytes):
                        matched_primary = primary_rec
                        break

                if matched_primary:
                    _logger.info(
                        "DUPLICATE DETECTED: Product '%s' gallery image ID %s duplicates primary image ID %s",
                        template.display_name, img.id, matched_primary.id,
                    )
                    update_vals = {}
                    if not matched_primary.shopify_media_id and img.shopify_media_id:
                        update_vals['shopify_media_id'] = img.shopify_media_id
                    if not matched_primary.shopify_image_url and img.shopify_image_url:
                        update_vals['shopify_image_url'] = img.shopify_image_url

                    # Clear shopify_media_id on duplicate first to avoid SQL UNIQUE(product_tmpl_id, shopify_media_id) collision
                    if 'shopify_media_id' in update_vals:
                        img.write({'shopify_media_id': False})
                        img.flush_recordset(['shopify_media_id'])

                    if update_vals:
                        matched_primary.write(update_vals)
                        matched_primary.flush_recordset(list(update_vals.keys()))
                        _logger.info(
                            "IMAGE UPDATED: Transferred %s from duplicate image ID %s to primary image ID %s",
                            update_vals, img.id, matched_primary.id,
                        )
                    _logger.info(
                        "IMAGE SKIPPED: Removing duplicate gallery image record ID %s for product '%s'",
                        img.id, template.display_name,
                    )
                    to_remove |= img
                else:
                    unique_entries.append((img, img_bytes))

            if to_remove:
                to_remove.unlink()


class ProductShopifyImage(models.Model):
    _name = 'product.shopify.image'
    _description = 'Shopify Product Additional Image'
    _order = 'sequence, id'

    sequence = fields.Integer(default=10)
    product_tmpl_id = fields.Many2one(
        comodel_name='product.template',
        string="Product",
        required=True,
        ondelete='cascade',
    )
    name = fields.Char(string="Alt Text")
    image = fields.Image(
        string="Image",
        required=True,
        max_width=1920,
        max_height=1920,
    )
    shopify_media_id = fields.Char(
        string="Shopify Media ID",
        readonly=True,
        copy=False,
    )
    shopify_image_url = fields.Char(
        string="Shopify Image URL",
        readonly=True,
        copy=False,
    )

    _unique_media_per_product = models.Constraint(
        'UNIQUE(product_tmpl_id, shopify_media_id)',
        "This Shopify image has already been imported for this product.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get('skip_image_dedup'):
            return super().create(vals_list)

        created_records = self.browse()
        for vals in vals_list:
            tmpl_id = vals.get('product_tmpl_id')
            raw_img = vals.get('image')
            if not tmpl_id or not raw_img:
                created_records |= super().create([vals])
                continue

            tmpl = self.env['product.template'].browse(tmpl_id)
            existing_gallery = self.search([('product_tmpl_id', '=', tmpl_id)], order='sequence, id')
            matched_image = None

            for existing in existing_gallery | created_records.filtered(lambda r: r.product_tmpl_id.id == tmpl_id):
                if existing.image and images_match(raw_img, existing.image):
                    matched_image = existing
                    break

            if matched_image:
                _logger.info(
                    "DUPLICATE DETECTED: Image content matches existing gallery image (ID %s) for product '%s'",
                    matched_image.id, tmpl.display_name,
                )
                _logger.info(
                    "IMAGE FOUND: Existing image record ID %s found for product '%s'",
                    matched_image.id, tmpl.display_name,
                )
                update_vals = {}
                if vals.get('shopify_media_id') and not matched_image.shopify_media_id:
                    update_vals['shopify_media_id'] = vals['shopify_media_id']
                if vals.get('shopify_image_url') and not matched_image.shopify_image_url:
                    update_vals['shopify_image_url'] = vals['shopify_image_url']
                if vals.get('name') and not matched_image.name:
                    update_vals['name'] = vals['name']
                if update_vals:
                    matched_image.write(update_vals)
                    _logger.info(
                        "IMAGE UPDATED: Updated existing image record ID %s with %s",
                        matched_image.id, update_vals,
                    )
                _logger.info(
                    "IMAGE SKIPPED: Reusing existing gallery image ID %s instead of creating a duplicate row",
                    matched_image.id,
                )
                created_records |= matched_image
                continue

            new_rec = super().create([vals])
            _logger.info(
                "IMAGE CREATED: Created new gallery image ID %s for product '%s'",
                new_rec.id, tmpl.display_name,
            )
            created_records |= new_rec

        return created_records
