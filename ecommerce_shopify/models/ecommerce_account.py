# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import hashlib
from datetime import timedelta

from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.ecommerce_shopify import utils_graphql as shopify_utils_graphql
from odoo.addons.odoo_ecommerce.utils import ECommerceApiError, fetch_image_base64_from_url
from ..image_utils import decode_image_bytes, images_match
import logging
_logger = logging.getLogger(__name__)
class ECommerceAccount(models.Model):
    _inherit = 'ecommerce.account'

    shopify_authorization_type = fields.Selection(
        selection=[
            ('oauth', "Oauth"),
            ('self_access', "Self Access"),
        ],
        string="Authorization Type",
        required_if_channel='shopify',
        default='self_access',
        help="Select the authorization type to use for Shopify API access.",
    )
    shopify_store = fields.Char(
        string="Shopify Store",
        help="Shopify store name for Shopify API authentication.",
        copy=False,
    )
    shopify_store_name = fields.Char(
        string="Shopify OAuth Store",
        help="Shopify store name for Shopify API authentication.",
        readonly=True,
        copy=False,
    )
    shopify_client_id = fields.Char(
        string="Shopify Client Id",
        help="Client Id for Shopify API authentication.",
        copy=False,
    )
    shopify_client_secret = fields.Char(
        string="Shopify Client Secret",
        help="Client Secret for Shopify API authentication.",
        copy=False,
    )
    shopify_access_token = fields.Char(
        string="Shopify Access Token",
        help="Access token for Shopify API authentication.",
        copy=False,
    )

    shopify_webhook_id = fields.Char(
        string="Shopify Webhook ID",
        help="Store the Shopify webhook subscription ID for order creation.",
        copy=False,
    )

    def action_connect(self):
        self.ensure_one()
        if self.channel_code == 'shopify' and self.shopify_authorization_type == 'self_access':
            self._authenticate_shopify()
        return super().action_connect()

    def action_disconnect(self):
        self.ensure_one()
        if self.channel_code == 'shopify':
            self.shopify_access_token = False
        return super().action_disconnect()

    def action_copy_shopify_app_url(self):
        self.ensure_one()
        url = f"{self.get_base_url()}/odoo/ecommerce.account/{self.id}/shopify/app"
        return {
            'type': 'ir.actions.client',
            'tag': 'copy_shopify_url',
            'params': {
                'url': url,
            },
        }

    def _authenticate_shopify(self):
        try:
            response = shopify_utils_graphql.call_shopify_graphql_query(
                account=self,
                endpoint='shop',
            )
        except ECommerceApiError as ex:
            raise UserError(self.env._(
                "Shopify account is not authenticated: %s", str(ex),
            ))
        if (response.get('shop') or {}).get('name') != self.shopify_store:
            raise UserError(self.env._(
                "Shopify account is not authenticated: 'No shop information received'",
            ))

    def _get_product_url(self, offer):
        if self.channel_code != 'shopify':
            return super()._get_product_url(offer)
        return f"https://admin.shopify.com/store/{self.shopify_store}/products/{offer.ec_product_template_identifier}/variants/{offer.ec_product_identifier}"

    def _fetch_products_from_ecommerce(self):
        if self.channel_code != 'shopify':
            return super()._fetch_products_from_ecommerce()
        params = {}
        # An account without offers has not imported its Shopify catalog yet.
        # Do a full fetch regardless of the creation-time sync timestamp.
        # Once offers exist, use the timestamp for incremental synchronization.
        if self.ecommerce_offer_ids and self.last_products_sync:
            # Subsequent runs are incremental.  The one-second offset avoids
            # refetching a product that was included in the previous run.
            params['updated_at_min'] = (
                self.last_products_sync + timedelta(seconds=1)
            ).strftime('%Y-%m-%dT%H:%M:%SZ')
        response = shopify_utils_graphql.call_shopify_graphql_with_pagination(
            account=self,
            endpoint='products',
            params=params,
        )
        return response

    def _find_or_create_offer(self, product_data, auto_match=True):
        offer = super()._find_or_create_offer(product_data, auto_match=auto_match)
        # Import the full image gallery on every sync (not just at creation) so a
        # product that gains, loses, or reorders images on Shopify stays in sync.
        if self.channel_code == 'shopify' and offer:
            self._sync_shopify_product_images(offer, product_data)
            self._sync_shopify_main_image_media_id(offer, product_data)
        return offer

    def _sync_shopify_main_image_media_id(self, offer, product_data):
        """Record the Shopify Media ID of the product's featured/main image on
        `product.template.shopify_media_id`, mirroring how gallery rows already
        carry their own id (see `_sync_shopify_product_images`).

        Without this, the main image (`image_1920`) can only be reconciled
        against Shopify's media on its first push by content-hash comparison
        — and a hash miss against visually-identical-but-byte-different
        Shopify media (e.g. the `featuredImage` URL vs. that same picture's
        URL inside the `media` connection) permanently duplicates it, since
        nothing else associates the two afterward.
        """
        media_id = ((product_data or {}).get('variant_data') or {}).get('image_shopify_media_id')
        media_id = str(media_id or '').rsplit('/', 1)[-1]
        image_url = ((product_data or {}).get('variant_data') or {}).get('image_url') or ''
        if not media_id and not image_url:
            return
        default_product = self.env.ref('odoo_ecommerce.ecommerce_default_product', raise_if_not_found=False)
        if default_product and offer.matched_product_id.id == default_product.id:
            return
        product_tmpl = offer.matched_product_template_id
        if product_tmpl:
            values = {}
            if media_id and product_tmpl.shopify_media_id != media_id:
                values['shopify_media_id'] = media_id
            if image_url and product_tmpl.shopify_image_url != image_url:
                values['shopify_image_url'] = image_url
            if values:
                product_tmpl.write(values)

    def _sync_shopify_product_images(self, offer, product_data):
        """Import the Shopify image gallery of `product_data` onto the offer's matched product.

        Images are matched by `shopify_media_id`, URL, and reliable content comparison
        so re-syncing never creates duplicates: a known image is only re-sequenced/re-labelled,
        a new one is downloaded and created, and one no longer present on Shopify is removed.

        :param `ecommerce.offer` offer: The offer whose matched product should receive the images.
        :param dict product_data: The product data fetched from Shopify, containing
            a `variant_data.images` list (see `utils_graphql._shopify_prepare_product_images_structure`).
        """
        images_data = ((product_data or {}).get('variant_data') or {}).get('images')
        if images_data is None:
            return

        default_product = self.env.ref('odoo_ecommerce.ecommerce_default_product', raise_if_not_found=False)
        if default_product and offer.matched_product_id.id == default_product.id:
            return

        product_tmpl = offer.matched_product_template_id
        if not product_tmpl:
            return

        # Pre-deduplicate any existing duplicates in Odoo for this template
        product_tmpl._deduplicate_shopify_images()

        Image = self.env['product.shopify.image']
        existing_by_media_id = {}
        duplicate_rows = Image.browse()
        for image in Image.search([
            ('product_tmpl_id', '=', product_tmpl.id),
            ('shopify_media_id', '!=', False),
        ], order='sequence,id'):
            media_id = str(image.shopify_media_id or '').rsplit('/', 1)[-1]
            if not media_id:
                continue
            if media_id in existing_by_media_id:
                _logger.info("DUPLICATE DETECTED: Found duplicate gallery image row ID %s with media_id=%s", image.id, media_id)
                duplicate_rows |= image
            else:
                existing_by_media_id[media_id] = image
        if duplicate_rows:
            _logger.info("IMAGE SKIPPED: Removing %d duplicate gallery row(s) with identical media_id", len(duplicate_rows))
            duplicate_rows.unlink()

        seen_media_ids = set()
        for sequence, image_data in enumerate(images_data):
            media_id = str(image_data.get('shopify_media_id') or '').rsplit('/', 1)[-1]
            url = image_data.get('url')
            if not url:
                continue
            if media_id:
                seen_media_ids.add(media_id)
            existing_image = existing_by_media_id.get(media_id) if media_id else None

            # Reconcile by persisted CDN URL before creating a new row
            if not existing_image:
                existing_image = Image.search([
                    ('product_tmpl_id', '=', product_tmpl.id),
                    ('shopify_image_url', '=', url),
                ], limit=1)

            # Reliable image content comparison before creating a new row
            incoming_b64 = None
            incoming_bytes = None
            if not existing_image:
                try:
                    incoming_b64 = fetch_image_base64_from_url(url)
                    incoming_bytes = decode_image_bytes(incoming_b64)
                except Exception as exc:
                    _logger.debug("Could not fetch incoming image from %s: %s", url, exc)

                if incoming_bytes:
                    # Check against existing gallery images on this template
                    for candidate in Image.search([('product_tmpl_id', '=', product_tmpl.id)]):
                        if candidate.image and images_match(incoming_bytes, candidate.image):
                            _logger.info("DUPLICATE DETECTED: Shopify image (media_id=%s) content matches existing Odoo image ID %s",
                                         media_id, candidate.id)
                            _logger.info("IMAGE FOUND: Reusing existing Odoo image ID %s for Shopify media %s",
                                         candidate.id, media_id)
                            existing_image = candidate
                            break

                    # Check against template main image (image_1920)
                    if not existing_image and product_tmpl.image_1920:
                        if images_match(incoming_bytes, product_tmpl.image_1920):
                            _logger.info("DUPLICATE DETECTED: Shopify image (media_id=%s) content matches product template main image",
                                         media_id)
                            _logger.info("IMAGE FOUND: Shopify media %s matches template main image for product %s",
                                         media_id, product_tmpl.id)
                            if media_id and product_tmpl.shopify_media_id != media_id:
                                product_tmpl.write({'shopify_media_id': media_id, 'shopify_image_url': url})
                                _logger.info("IMAGE UPDATED: Updated template shopify_media_id=%s", media_id)
                            _logger.info("IMAGE SKIPPED: Skipping creation of gallery record for main image (media_id=%s)", media_id)
                            continue

            if existing_image:
                update_values = {
                    'sequence': sequence,
                    'name': image_data.get('alt') or existing_image.name or '',
                    'shopify_image_url': url,
                }
                if media_id:
                    update_values['shopify_media_id'] = media_id
                existing_image.write(update_values)
                if not media_id and existing_image.shopify_media_id:
                    seen_media_ids.add(str(existing_image.shopify_media_id).rsplit('/', 1)[-1])
                if media_id:
                    existing_by_media_id[media_id] = existing_image
                _logger.info("IMAGE FOUND: Matched existing Odoo image ID %s (media_id=%s)", existing_image.id, media_id or '<url>')
                _logger.info("IMAGE UPDATED: Updated Odoo image ID %s (media_id=%s, url=%s)", existing_image.id, media_id or '<url>', url)
                continue

            try:
                if incoming_b64 is None:
                    incoming_b64 = fetch_image_base64_from_url(url)
                new_img = Image.create({
                    'product_tmpl_id': product_tmpl.id,
                    'sequence': sequence,
                    'name': image_data.get('alt') or '',
                    'shopify_media_id': media_id,
                    'shopify_image_url': url,
                    'image': incoming_b64,
                })
                if media_id:
                    existing_by_media_id[media_id] = new_img
                _logger.info("IMAGE CREATED: Created new Odoo gallery image ID %s for product %s (media_id=%s, url=%s)",
                             new_img.id, product_tmpl.id, media_id or '<url>', url)
            except ECommerceApiError as error:
                self.log_xml(
                    "Error occurred while fetching an additional image for product %s "
                    "on %s account with id %s.\nError description: %s" %
                    (product_tmpl.display_name, self.ecommerce_channel_id.name, self.id, str(error)),
                    '_sync_shopify_product_images',
                    'server',
                )

        stale_images = Image.browse([
            image.id for m_id, image in existing_by_media_id.items()
            if m_id not in seen_media_ids
        ])
        if stale_images:
            _logger.info("IMAGE SKIPPED: Removing %d stale Shopify gallery image(s) for product %s",
                         len(stale_images), product_tmpl.id)
            stale_images.unlink()

    def action_register_webhook(self):
        self.ensure_one()

        if not self.shopify_store:
            raise UserError(_("Shopify store is not configured."))

        if not self.shopify_access_token:
            raise UserError(_("Shopify access token is missing."))

        webhook_url = f"{self.get_base_url()}/odoo/api/v1/order/create/account/{self.id}"
        webhook_topic = "ORDERS_CREATE"

        _logger.info(
            "Registering Shopify webhook for account %s (ID %s) – topic %s, url %s",
            self.display_name,
            self.id,
            webhook_topic,
            webhook_url,
        )

        try:
            response = shopify_utils_graphql.call_shopify_graphql_mutation(
                account=self,
                endpoint='webhookSubscriptionCreate',
                params={
                    'uri': webhook_url,
                    'topic': webhook_topic,
                },
            )
        except ECommerceApiError as e:
            _logger.error("Failed to register Shopify webhook: %s", e)
            raise UserError(_("Failed to register Shopify webhook: %s") % e)

        if response.get('id'):
            self.shopify_webhook_id = response['id']

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Webhook"),
                'message': _("Webhook registered: %s") % (response.get('uri') or webhook_url),
            },
        }

    def _fetch_orders_from_ecommerce(self):
        _logger.info('Fetching orders from Shopify')
        if self.channel_code != 'shopify':
            return super()._fetch_orders_from_ecommerce()
        updated_at_min_date = (self.last_orders_sync + timedelta(seconds=1)).isoformat() + 'Z'
        result = shopify_utils_graphql.call_shopify_graphql_with_pagination(
            account=self,
            endpoint='orders',
            params={'updated_at_min': updated_at_min_date},
        )
        if self.fulfilled_by == 'odoo':
            for order_data in result:
                order_data['fulfillments'] = []
        _logger.info('Fetched %d orders' % len(result))
        return result

    def _fetch_order_from_ecommerce_by_order_ref(self, ecommerce_order_ref):
        _logger.info('Fetching order %s from Shopify' % ecommerce_order_ref)
        if self.channel_code != 'shopify':
            return super()._fetch_order_from_ecommerce_by_order_ref(ecommerce_order_ref)
        response_order = shopify_utils_graphql.call_shopify_graphql_query(
            account=self,
            endpoint='order',
            params={'order_id': ecommerce_order_ref},
        )
        _logger.info('Fetched order %s from Shopify' % ecommerce_order_ref)
        if self.fulfilled_by == 'odoo':
            response_order['fulfillments'] = []
        return response_order

    def _fetch_locations_from_ecommerce(self):
        if self.channel_code != 'shopify':
            return super()._fetch_locations_from_ecommerce()
        response = shopify_utils_graphql.call_shopify_graphql_with_pagination(
            account=self,
            endpoint='locations',
        )
        return response

    def _fetch_inventory_from_ecommerce(self):
        if self.channel_code != 'shopify':
            return super()._fetch_inventory_from_ecommerce()
        response = shopify_utils_graphql.call_shopify_graphql_fetch_inventory(
            account=self,
        )
        return response

    def _update_pickings_to_ecommerce(self, pickings):
        if self.channel_code != 'shopify':
            return super()._update_pickings_to_ecommerce(pickings)
        for picking in pickings:
            try:
                fulfillment = self._update_picking_to_shopify(picking)
            except ECommerceApiError as error:
                self._post_process_after_picking_update_failed(picking, str(error))
            else:
                self._post_process_after_picking_update_success(picking, fulfillment.get('id'))

    def _update_inventory_to_ecommerce(self, inventory_data):
        if self.channel_code != 'shopify':
            return super()._update_inventory_to_ecommerce(inventory_data)
        response = shopify_utils_graphql.call_shopify_graphql_mutation(
            account=self,
            endpoint='inventorySetQuantities',
            params={'inventory_data': inventory_data},
        )
        return response

    def _update_picking_to_shopify(self, picking):
        response = shopify_utils_graphql.call_shopify_graphql_mutation(
            account=self,
            endpoint='fulfillmentCreate',
            params={'picking': picking},
        )
        return response
