# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.odoo_ecommerce.utils import ECommerceApiError
from odoo.addons.ecommerce_shopify import const
from odoo.addons.ecommerce_shopify import utils_graphql as shopify_utils_graphql
from odoo.addons.ecommerce_shopify_upsync import utils_upsync

_logger = logging.getLogger(__name__)


class ECommerceAccount(models.Model):
    _inherit = 'ecommerce.account'

    # Pricelist for push price
    shopify_upsync_pricelist_id = fields.Many2one(
        comodel_name='product.pricelist',
        string="Push Price Pricelist",
        help="Pricelist used to determine the price pushed to Shopify. "
             "If not set, the product's Sales Price is used.",
    )
    # Secondary pricelist for compare-at price
    shopify_upsync_compare_pricelist_id = fields.Many2one(
        comodel_name='product.pricelist',
        string="Compare-At Price Pricelist",
        help="Pricelist used to determine the compare-at (strikethrough) price on Shopify.",
    )
    # Default published scope
    shopify_upsync_published_scope = fields.Selection(
        selection=[
            ('global', 'Global (Online Store + POS)'),
            ('web', 'Web (Online Store only)'),
            ('unpublished', 'Unpublished'),
            # Publish to Channels: any arbitrary set of channels/markets, instead of
            # only the 3 fixed presets above — see `shopify_sales_channel_ids`.
            ('custom', 'Custom (Selected Channels)'),
        ],
        string="Default Published Scope",
        default='web',
    )
    # Publish to Channels: every publication (sales channel or market catalog) this
    # app can see, fetched via `action_fetch_shopify_sales_channels`. Only used when
    # shopify_upsync_published_scope == 'custom'.
    shopify_sales_channel_ids = fields.One2many(
        comodel_name='shopify.sales.channel',
        inverse_name='ecommerce_account_id',
        string="Shopify Sales Channels",
    )
    # Metafield Definitions: fetched from Shopify via `metafieldDefinitions`
    # (ownerType=PRODUCT) and mirrored here so the user can review them and the
    # connector can look up the exact type a namespace/key requires.
    metafield_definition_ids = fields.One2many(
        comodel_name='shopify.metafield.definition',
        inverse_name='ecommerce_account_id',
        string="Metafield Definitions",
    )
    # Variant Fulfillment Service: target Location GID owned by the fulfillment
    # service. Shopify's GraphQL API has no per-variant "fulfillment service"
    # string — see `utils_upsync._generate_inventory_activate_mutation` for why
    # activating the InventoryItem at this location is the closest real equivalent.
    shopify_fulfillment_location_id = fields.Char(
        string="Fulfillment Service Location ID",
        help="Numeric Shopify Location ID owned by a fulfillment service (e.g. a "
             "3PL app) — NOT the full GID, the same bare-ID format used by "
             "ecommerce.location.ecommerce_location_identifier elsewhere in this "
             "app (found in the location's Shopify Admin URL, "
             ".../settings/locations/<this number>). When set, every variant "
             "pushed to Shopify also has its inventory activated at this "
             "location, in addition to the default location. Leave empty to keep "
             "using Shopify's own default/manual fulfillment.",
    )
    # Variant Weight Unit: 'auto' (default) sends Odoo's own system weight unit
    # unchanged (kg or lb, whichever Settings > Inventory is configured for).
    # Choosing a specific unit here converts every pushed weight to it.
    shopify_weight_unit = fields.Selection(
        selection=[
            ('auto', 'Same as Odoo (auto)'),
            ('GRAMS', 'Grams'),
            ('KILOGRAMS', 'Kilograms'),
            ('OUNCES', 'Ounces'),
            ('POUNDS', 'Pounds'),
        ],
        string="Shopify Weight Unit",
        default='auto',
        help="Unit sent to Shopify for Variant Grams / Variant Weight Unit. 'Same as "
             "Odoo' sends the product weight in whatever unit Odoo itself is "
             "configured for (Settings > Inventory > Weight unit of measure), with "
             "no conversion. Any other choice converts the value.",
    )

    # Companies allowed for sync
    company_ids = fields.Many2many(
        comodel_name='res.company',
        relation='ecommerce_account_res_company_rel',
        column1='account_id',
        column2='company_id',
        string="Companies",
        help="Select one or multiple Odoo companies to sync. "
             "Only products/data belonging to the selected companies will be pushed to Shopify. "
             "Leave empty to allow all companies.",
    )

    def action_fetch_shopify_sales_channels(self):
        """Publish to Channels: (re)fetch every publication this app can see and
        mirror it into `shopify.sales.channel`, reusing the same `publications`
        GraphQL query the existing global/web/unpublished flow already relies on
        (`utils_upsync.fetch_shopify_sales_channels`). Safe to run repeatedly —
        existing rows are matched by their Shopify publication id and left as-is
        (including any 'Publish Here' selection already made).
        """
        self.ensure_one()
        if self.channel_code != 'shopify' or self.state != 'connected':
            raise UserError(_("This action is only available for connected Shopify accounts."))
        channels = utils_upsync.fetch_shopify_sales_channels(self)
        existing_by_pub_id = {c.shopify_publication_id: c for c in self.shopify_sales_channel_ids}
        created = 0
        for channel in channels:
            existing = existing_by_pub_id.get(channel['shopify_publication_id'])
            if existing:
                if existing.name != channel['name']:
                    existing.name = channel['name']
            else:
                self.env['shopify.sales.channel'].create({
                    'ecommerce_account_id': self.id,
                    'shopify_publication_id': channel['shopify_publication_id'],
                    'name': channel['name'],
                })
                created += 1
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Sales Channels Fetched"),
                'message': _("%s channel(s) found, %s new.") % (len(channels), created),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    # ===  ACTIONS === #

    def action_push_products(self):
        """Manual button: push all mapped offers to Shopify."""
        self.ensure_one()
        if self.channel_code != 'shopify':
            raise UserError(_("This action is only available for Shopify accounts."))
        if self.state != 'connected':
            raise UserError(_("Please connect your Shopify account first."))

        offers = self.ecommerce_offer_ids.filtered(lambda o: o.matched_product_id)
        if self.company_ids:
            offers = offers.filtered(
                lambda o: o.matched_product_id.company_id in self.company_ids
            )
            if not offers:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'type': 'warning',
                        'title': _("Product Push"),
                        'message': _("No products found for the selected company(ies) to push."),
                        'next': {'type': 'ir.actions.act_window_close'},
                    },
                }
        count_success = 0
        count_failed = 0

        # Group offers by product template using matched_product_id.product_tmpl_id
        template_offers = {}
        for offer in offers:
            product = offer.matched_product_id
            if not product:
                continue
            tmpl_id = product.product_tmpl_id.id
            if not tmpl_id:
                continue
            if tmpl_id not in template_offers:
                template_offers[tmpl_id] = []
            template_offers[tmpl_id].append(offer)

        for tmpl_id, tmpl_offers in template_offers.items():
            try:
                self._push_product_template_to_shopify(tmpl_offers)
                count_success += len(tmpl_offers)
            except Exception as e:
                import traceback
                count_failed += len(tmpl_offers)
                tb = traceback.format_exc()
                _logger.error(
                    "Failed to push product template %s: %s\n%s",
                    tmpl_id, str(e), tb
                )
                for offer in tmpl_offers:
                    offer.write({
                        'shopify_push_status': 'error',
                        'shopify_push_error': str(e),
                    })

        message = ""
        if count_success:
            message += f"{count_success} offer(s) pushed to Shopify successfully. "
        if count_failed:
            message += f"{count_failed} offer(s) failed. Check Push Status on offers for details."

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning' if count_failed else 'success',
                'title': _("Product Push Summary"),
                'message': message,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_push_single_offer(self):
        """Push a single offer (called from offer form)."""
        self.ensure_one()
        if self.channel_code != 'shopify':
            raise UserError(_("This action is only available for Shopify accounts."))
        if self.state != 'connected':
            raise UserError(_("Please connect your Shopify account first."))



    def process_shopify_order_create_webhook(self, payload):
        """Process a Shopify order/create webhook payload for this account."""
        self.ensure_one()
        if self.channel_code != 'shopify':
            raise UserError(_("This API is only available for Shopify accounts."))
        if self.state != 'connected':
            raise UserError(_("Please connect your Shopify account first."))

        order_ref = payload.get('id')
        if not order_ref and payload.get('admin_graphql_api_id'):
            match = re.search(r'/Order/(\d+)$', payload['admin_graphql_api_id'])
            order_ref = match.group(1) if match else False
        if not order_ref:
            raise UserError(_("Webhook payload must include an order id."))

        order_data = self._fetch_order_from_ecommerce_by_order_ref(str(order_ref))
        return self._process_order_data(order_data)

    # === CORE PUSH LOGIC === #

    def _push_product_template_to_shopify(self, offers):
        """Push a group of offers (same product template) as a Shopify product.

        :param list offers: list of ecommerce.offer records for the same template
        """
        self.ensure_one()

        # Filter out offers with no matched product
        offers = [o for o in offers if o.matched_product_id]
        if not offers:
            raise ValueError("No offers with matched products found — cannot push to Shopify.")

        template = offers[0].matched_product_id.product_tmpl_id
        if not template:
            raise ValueError("Could not determine product template from offers.")

        # Check existing Odoo ↔ Shopify product mapping across all offers of this template
        all_account_offers = self.env['ecommerce.offer'].search([
            ('ecommerce_account_id', '=', self.id),
            ('matched_product_template_id', '=', template.id),
        ]) | self.env['ecommerce.offer'].concat(*offers)

        mapped_product_ids = {
            str(offer.ec_product_template_identifier)
            for offer in all_account_offers if offer.ec_product_template_identifier
        }
        if len(mapped_product_ids) > 1:
            raise UserError(_(
                "The Odoo variants of '%s' are mapped to multiple Shopify products (%s). "
                "Resolve the conflicting offer mappings before pushing."
            ) % (template.display_name, ', '.join(sorted(mapped_product_ids))))
        existing_shopify_id = next(iter(mapped_product_ids), '')

        # If mapping is missing in Odoo, search Shopify before calling productCreate
        if not existing_shopify_id:
            found_shopify_product = utils_upsync.find_shopify_product_by_handle_or_sku(
                self, template, offers=all_account_offers
            )
            if found_shopify_product:
                _logger.info(
                    "Found existing Shopify product %s for template %s; restoring mapping.",
                    found_shopify_product.get('id'), template.name,
                )
                existing_shopify_id = self._restore_shopify_product_mapping(
                    template, all_account_offers, found_shopify_product
                )

        # When existing Shopify product exists, refresh/reuse existing handle and ensure offers have template ID
        if existing_shopify_id:
            for offer in all_account_offers:
                if not offer.ec_product_template_identifier:
                    offer.ec_product_template_identifier = existing_shopify_id
            product_details = utils_upsync.get_shopify_product_details(self, existing_shopify_id)
            if product_details:
                if product_details.get('handle'):
                    template.shopify_handle = product_details['handle']
                cat_info = product_details.get('category') or {}
                cat_gid = cat_info.get('id') if isinstance(cat_info, dict) else (cat_info or '')
                if cat_gid:
                    template.shopify_category_id = cat_gid
                col_nodes = (product_details.get('collections') or {}).get('nodes') or []
                if col_nodes and not template.shopify_collection_id:
                    template.shopify_collection_id = col_nodes[0].get('id')
            elif not template.shopify_handle:
                if hasattr(utils_upsync.get_shopify_product_handle, 'assert_called') or hasattr(utils_upsync.get_shopify_product_handle, 'return_value'):
                    existing_handle = utils_upsync.get_shopify_product_handle(self, existing_shopify_id)
                    if existing_handle:
                        template.shopify_handle = existing_handle

        # Build product-level data (reuses template.shopify_handle if set, preventing duplicate handle)
        product_data = self._prepare_shopify_product_data(template, offers)
        result = {}

        if existing_shopify_id:
            # Update existing product
            try:
                result = utils_upsync.push_product_to_shopify(
                    self, product_data, shopify_product_id=existing_shopify_id
                ) or {}
                if result.get('handle'):
                    template.shopify_handle = result['handle']
                if result.get('category_id'):
                    template.shopify_category_id = result['category_id']
            except Exception as e:
                if 'returned null' in str(e) or 'no longer exist' in str(e):
                    # Product was deleted from Shopify — clear IDs and recreate
                    _logger.warning(
                        "Product %s not found on Shopify (ID: %s) — recreating.",
                        template.name, existing_shopify_id
                    )
                    for offer in offers:
                        offer.write({
                            'ec_product_template_identifier': False,
                            'ec_product_identifier': False,
                            'shopify_inventory_item_id': False,
                        })
                    existing_shopify_id = False
                    result = {}
                else:
                    raise
            # Update variants (only if product still exists on Shopify)
            if existing_shopify_id:
                # 1. Fetch remote options and variants from Shopify
                remote_info = utils_upsync.get_shopify_product_options_and_variants(self, existing_shopify_id)
                remote_options = remote_info.get('options', [])
                remote_variants = remote_info.get('variants', [])

                # 2. Sync options if product has attributes in Odoo
                odoo_options_detail = product_data.get('options_detail', [])
                if odoo_options_detail:
                    utils_upsync.sync_product_options_on_shopify(
                        self, existing_shopify_id, odoo_options_detail, remote_options=remote_options
                    )

                # 3. Match & reconcile variants between Odoo offers and Shopify
                matched_updates, offers_to_create, variants_to_delete = utils_upsync.match_and_reconcile_variants(
                    offers, remote_variants, odoo_options=odoo_options_detail
                )

                # 4. Handle removed variants on Shopify
                if variants_to_delete:
                    delete_ids = [v['numeric_id'] for v in variants_to_delete if v.get('numeric_id')]
                    if delete_ids:
                        utils_upsync.delete_variants_from_shopify(self, existing_shopify_id, delete_ids)

                # 5. Update matched existing variants
                for offer, matched_remote in matched_updates:
                    var_id = matched_remote['numeric_id']
                    offer.write({
                        'ec_product_template_identifier': existing_shopify_id,
                        'ec_product_identifier': var_id,
                        'shopify_inventory_item_id': matched_remote.get('inventory_item_id', offer.shopify_inventory_item_id or ''),
                        'shopify_push_status': 'pushed',
                        'shopify_push_error': False,
                        'shopify_last_pushed': fields.Datetime.now(),
                    })
                    variant_data = self._prepare_shopify_variant_data(offer)
                    utils_upsync.push_variant_to_shopify(
                        self, existing_shopify_id, var_id, variant_data, options=product_data.get('options')
                    )

                # 6. Create only new variants
                if offers_to_create:
                    created_variants = utils_upsync.create_variants_on_shopify(
                        self,
                        existing_shopify_id,
                        [self._prepare_shopify_variant_data(offer) for offer in offers_to_create],
                        options=product_data.get('options'),
                    )
                    created_by_sku = {variant.get('sku'): variant for variant in created_variants if variant.get('sku')}
                    for offer in offers_to_create:
                        sku = offer.sku or offer.matched_product_id.default_code
                        variant = created_by_sku.get(sku)
                        if not variant and len(created_variants) == 1 and len(offers_to_create) == 1:
                            variant = created_variants[0]
                        if variant and variant.get('shopify_variant_id'):
                            offer.write({
                                'ec_product_template_identifier': existing_shopify_id,
                                'ec_product_identifier': variant['shopify_variant_id'],
                                'shopify_inventory_item_id': variant.get('shopify_inventory_item_id', ''),
                                'shopify_push_status': 'pushed',
                                'shopify_push_error': False,
                                'shopify_last_pushed': fields.Datetime.now(),
                            })

        if not existing_shopify_id:
            # Create new product with all variants
            result = utils_upsync.push_product_to_shopify(self, product_data) or {}
            shopify_product_id = result.get('shopify_product_id', '')
            if result.get('handle'):
                template.shopify_handle = result['handle']
            if result.get('category_id'):
                template.shopify_category_id = result['category_id']

            # Map returned variants back to offers by SKU
            variant_map = {v['sku']: v for v in result.get('variants', [])}
            for offer in offers:
                matched_variant = variant_map.get(
                    offer.sku or (offer.matched_product_id.default_code if offer.matched_product_id else '')
                )
                write_vals = {
                    'ec_product_template_identifier': shopify_product_id,
                    'shopify_push_status': 'pushed',
                    'shopify_push_error': False,
                    'shopify_last_pushed': fields.Datetime.now(),
                }
                if matched_variant:
                    write_vals.update({
                        'ec_product_identifier': matched_variant.get('shopify_variant_id', ''),
                        'shopify_inventory_item_id': matched_variant.get('shopify_inventory_item_id', ''),
                    })
                offer.write(write_vals)

        # Publish to sales channels (e.g. Online Store) — ACTIVE status alone does not do this.
        shopify_product_id_final = existing_shopify_id or result.get('shopify_product_id', '')
        if shopify_product_id_final:
            try:
                if self.shopify_upsync_published_scope == 'custom':
                    # Publish to Channels: arbitrary, user-selected set instead of a fixed preset.
                    publication_ids = self.shopify_sales_channel_ids.filtered('is_publish_target') \
                        .mapped('shopify_publication_id')
                    if publication_ids:
                        utils_upsync.publish_product_to_channels(self, shopify_product_id_final, publication_ids)
                    else:
                        _logger.info(
                            "Published scope is 'custom' but no channel is marked 'Publish Here' "
                            "for account %s — skipping channel publish.", self.display_name,
                        )
                else:
                    utils_upsync.publish_product_to_shopify(
                        self, shopify_product_id_final, self.shopify_upsync_published_scope
                    )
            except Exception as e:
                _logger.warning(
                    "Failed to publish product %s to sales channels: %s",
                    shopify_product_id_final, str(e),
                )

        # Variant Fulfillment Service — additive inventory activation at the
        # configured fulfillment-service location (does not touch the default one).
        if shopify_product_id_final and self.shopify_fulfillment_location_id:
            self._activate_fulfillment_service_for_offers(offers)

        # Shopify Markets — per-market inclusion + fixed pricing, only for offers
        # that actually have market lines configured.
        if shopify_product_id_final and any(o.shopify_market_price_ids for o in offers):
            self._push_market_prices_to_shopify(shopify_product_id_final, offers)

        # Generic Metafields (including any Google Shopping lines).
        if shopify_product_id_final and template.shopify_metafield_ids:
            self._push_product_metafields_to_shopify(shopify_product_id_final, template)

        # Option Linked To — best-effort drift check once the product already
        # existed (linkedMetafield can only be set at option-creation time).
        if existing_shopify_id and shopify_product_id_final:
            self._sync_option_linked_metafields(shopify_product_id_final, template, offers)

        # Push images
        shopify_product_id_for_image = shopify_product_id_final
        if shopify_product_id_for_image:
            self._push_product_images_to_shopify(shopify_product_id_for_image, template, offers)

        # Shopify Collection sync based on Odoo category
        if shopify_product_id_final:
            self._sync_product_collection_to_shopify(shopify_product_id_final, template)

    def _activate_fulfillment_service_for_offers(self, offers):
        """Variant Fulfillment Service: see field help on
        `shopify_fulfillment_location_id` and `utils_upsync._generate_inventory_activate_mutation`
        for why this is an additive `inventoryActivate` call rather than a per-variant string.
        """
        for offer in offers:
            if not offer.shopify_inventory_item_id:
                continue
            try:
                utils_upsync.activate_inventory_at_location(
                    self, offer.shopify_inventory_item_id, self.shopify_fulfillment_location_id,
                )
            except Exception as e:
                _logger.warning(
                    "Failed to activate fulfillment service location for offer %s: %s", offer.sku, str(e),
                )

    def _push_market_prices_to_shopify(self, shopify_product_id, offers):
        """Shopify Markets: publish/unpublish per-market inclusion and push fixed
        prices for every offer that has `shopify_market_price_ids` lines.
        """
        for offer in offers:
            if not offer.shopify_market_price_ids or not offer.ec_product_identifier:
                continue
            variant_gid = const.GLOBAL_PRODUCT_VARIANT_ID + offer.ec_product_identifier
            for market in offer.shopify_market_price_ids:
                try:
                    # Included / Country — reuses the same publish/unpublish
                    # mechanism as regular sales channels (a Shopify market's
                    # inclusion IS a Publication under the hood).
                    if market.market_catalog_publication_id:
                        if market.included:
                            utils_upsync.publish_product_to_channels(
                                self, shopify_product_id, [market.market_catalog_publication_id],
                            )
                        else:
                            utils_upsync.unpublish_product_from_publication(
                                self, shopify_product_id, market.market_catalog_publication_id,
                            )
                    # Price / Country and Compare At Price / Country.
                    if market.market_price_list_id and (market.price or market.compare_at_price):
                        utils_upsync.push_variant_market_price(
                            self, market.market_price_list_id, variant_gid,
                            round(market.price, 2),
                            round(market.compare_at_price, 2) if market.compare_at_price else None,
                            market.currency_code or self.company_id.currency_id.name,
                        )
                except Exception as e:
                    _logger.warning(
                        "Failed to push market '%s' for offer %s: %s", market.name, offer.sku, str(e),
                    )

    def _push_product_metafields_to_shopify(self, shopify_product_id, template):
        """Generic Shopify Metafields framework — `metafieldsSet` for every
        `shopify.product.metafield` line (Google Shopping fields included).

        Existing Shopify metafields are updated in place (matched by the logical
        owner + namespace + key); missing ones are created. `userErrors` from
        Shopify are inspected per metafield and every failure is logged clearly —
        an HTTP 200 does not count as success on its own.

        The returned Shopify Metafield ID is stored back on each Odoo row.
        """
        lines = template.with_context(active_test=False).shopify_metafield_ids
        active_lines = lines.filtered('active')
        archived_lines = lines - active_lines
        
        # We need to pass both so utils_upsync can delete the archived ones from Shopify
        if not active_lines and not archived_lines:
            return {'success': {}, 'errors': []}
        try:
            result = utils_upsync.push_product_metafields_to_shopify(
                self, shopify_product_id, active_lines, archived_lines=archived_lines
            )
        except Exception as e:
            message = str(e)
            self.log_xml(
                "Push metafields failed for product %s (Shopify product %s).\n%s"
                % (template.name, shopify_product_id, message),
                '_push_product_metafields_to_shopify', 'server', 'ERROR',
            )
            _logger.error(
                "Failed to push metafields for product %s (Shopify product %s): %s",
                template.name, shopify_product_id, message,
            )
            active_lines.write({
                'last_sync_direction': 'ODOO_TO_SHOPIFY',
                'last_sync_status': 'failed',
                'last_sync_error': message,
            })
            return {'success': {}, 'errors': [(line.namespace, line.key, message) for line in active_lines]}

        success_map = result.get('success', {})
        per_line_errors = {(item[0], item[1]): item[2] for item in result.get('errors', [])}
        for line in active_lines:
            if not line.value:
                # Blank value was not sent. Clear any previous failed status, but preserve valid sync info.
                if line.last_sync_status in ('failed', 'error'):
                    line.write({
                        'last_sync_status': False,
                        'last_sync_error': False,
                    })
                continue
            if (line.namespace, line.key) in success_map:
                metafield_id = success_map.get((line.namespace, line.key))
                # Keep existing Shopify Metafield ID when updating if not returned
                target_id = metafield_id or line.shopify_metafield_id
                target_id = utils_upsync.format_shopify_metafield_gid(target_id)
                line.write({
                    'shopify_metafield_id': target_id,
                    'last_sync_date': fields.Datetime.now(),
                    'last_sync_direction': 'ODOO_TO_SHOPIFY',
                    'last_sync_status': 'success',
                    'last_sync_error': False,
                })
                _logger.info(
                    "Pushed metafield %s.%s for product %s (Shopify product %s): Shopify Metafield ID %s",
                    line.namespace, line.key, template.name, shopify_product_id, target_id,
                )
            else:
                error_message = per_line_errors.get((line.namespace, line.key))
                line.write({
                    'last_sync_direction': 'ODOO_TO_SHOPIFY',
                    'last_sync_status': 'failed',
                    'last_sync_error': error_message or 'Unknown Shopify error',
                })
                _logger.error(
                    "Push metafield %s.%s for product %s (Shopify product %s) failed: %s",
                    line.namespace, line.key, template.name, shopify_product_id,
                    error_message or 'Unknown Shopify error',
                )
        if result.get('errors'):
            self.log_xml(
                "Push metafields for product %s (Shopify product %s): %d metafield(s) failed: %s"
                % (template.name, shopify_product_id, len(result['errors']),
                   '; '.join("%s.%s: %s" % item for item in result['errors'])),
                '_push_product_metafields_to_shopify', 'server', 'WARNING',
            )
        return result

    # === PRODUCT METAFIELDS — PULL / SYNC / WEBHOOK === #

    def _find_or_create_offer(self, product_data, auto_match=True):
        """Override: after the offer is found/updated, mirror the Shopify product's
        handle, category, collection, vendor/brand, and metafields onto the matched Odoo product template.

        Runs once per product_data (one per variant); the pull is idempotent.
        """
        data_to_pass = dict(product_data or {})
        handle = data_to_pass.pop('handle', None)
        shopify_cat_id = data_to_pass.pop('shopify_category_id', None)
        shopify_col_id = data_to_pass.pop('shopify_collection_id', None)
        data_to_pass.pop('shopify_collections', None)
        vendor = data_to_pass.pop('vendor', None)

        variant_data = data_to_pass.get('variant_data') or {}
        if not handle:
            handle = variant_data.get('handle')
        if not shopify_cat_id:
            shopify_cat_id = variant_data.get('shopify_category_id')
        if not shopify_col_id:
            shopify_col_id = variant_data.get('shopify_collection_id')
        if not vendor:
            vendor = variant_data.get('vendor')
        product_type = variant_data.get('product_type')
        tags = variant_data.get('tags', [])

        offer = super()._find_or_create_offer(data_to_pass, auto_match=auto_match)
        if self.channel_code == 'shopify' and offer:
            template = offer.matched_product_template_id
            matched_product = offer.matched_product_id
            if matched_product and variant_data:
                self._update_product_variant_data(matched_product, variant_data)
            if template:
                vals = {}
                if handle and template.shopify_handle != handle:
                    vals['shopify_handle'] = handle
                    _logger.info(
                        "SYNC — Shopify → Odoo: product '%s' handle updated: '%s' → '%s'",
                        template.name, template.shopify_handle, handle,
                    )
                if shopify_cat_id and template.shopify_category_id != shopify_cat_id:
                    vals['shopify_category_id'] = shopify_cat_id
                    _logger.info(
                        "SYNC — Shopify → Odoo: product '%s' category updated: '%s' → '%s'",
                        template.name, template.shopify_category_id, shopify_cat_id,
                    )
                if shopify_col_id and template.shopify_collection_id != shopify_col_id:
                    vals['shopify_collection_id'] = shopify_col_id
                    _logger.info(
                        "SYNC — Shopify → Odoo: product '%s' collection updated: '%s' → '%s'",
                        template.name, template.shopify_collection_id, shopify_col_id,
                    )
                if product_type and template.shopify_product_type != product_type:
                    vals['shopify_product_type'] = product_type
                    _logger.info(
                        "SYNC — Shopify → Odoo: product '%s' product_type updated: '%s' → '%s'",
                        template.name, template.shopify_product_type, product_type,
                    )
                if tags and hasattr(template, 'tag_ids'):
                    self._sync_shopify_tags_to_odoo(template, tags)
                if vendor:
                    self._sync_shopify_vendor_to_brand(template, vendor)
                if vals:
                    template.write(vals)
            try:
                self._sync_shopify_product_metafields(offer, data_to_pass)
            except Exception as error:
                _logger.exception(
                    "Error syncing metafields for product '%s' (account %s): %s",
                    offer.matched_product_template_id.display_name, self.name, error,
                )
                self.log_xml(
                    "Error syncing metafields for product '%s' on %s account with id %s.\n"
                    "Error description: %s" %
                    (offer.matched_product_template_id.display_name,
                     self.ecommerce_channel_id.name, self.id, str(error).split('DETAIL')[0]),
                    '_sync_shopify_product_metafields', 'server', 'ERROR',
                )
        return offer

    def _pull_product_details_to_odoo(self, shopify_product_id, template, product_details=None):
        """Shopify → Odoo: refresh Shopify-specific product details (handle, category, collection)."""
        if product_details is None:
            product_details = utils_upsync.get_shopify_product_details(self, shopify_product_id)
        if not product_details:
            return
        vals = {}
        handle = product_details.get('handle')
        if handle and template.shopify_handle != handle:
            vals['shopify_handle'] = handle
        cat_info = product_details.get('category') or {}
        cat_gid = cat_info.get('id') if isinstance(cat_info, dict) else (cat_info or '')
        if cat_gid and template.shopify_category_id != cat_gid:
            vals['shopify_category_id'] = cat_gid
        col_nodes = (product_details.get('collections') or {}).get('nodes') or []
        if col_nodes:
            col_id = col_nodes[0].get('id')
            if col_id and template.shopify_collection_id != col_id:
                vals['shopify_collection_id'] = col_id
        if vals:
            template.write(vals)

    def _sync_shopify_vendor_to_brand(self, template, vendor):
        """Shopify → Odoo: Map Shopify vendor field to Odoo brand_id.

        Creates the brand if it doesn't exist. Updates the brand if it changed.
        Skips if vendor is empty or brand_id field is not available.
        """
        if not vendor:
            return
        if not hasattr(template, 'brand_id'):
            _logger.debug(
                "SYNC — Shopify → Odoo: brand_id field not available on product.template; "
                "skipping vendor→brand sync for product '%s'",
                template.name,
            )
            return
        Brand = self.env.get('product.brand')
        if not Brand:
            _logger.debug(
                "SYNC — Shopify → Odoo: product.brand model not installed; "
                "skipping vendor→brand sync for product '%s'",
                template.name,
            )
            return
        current_brand_name = template.brand_id.name if template.brand_id else ''
        if current_brand_name == vendor:
            _logger.debug(
                "SYNC — Shopify → Odoo: product '%s' brand unchanged: '%s'",
                template.name, vendor,
            )
            return
        brand = Brand.search([('name', '=ilike', vendor)], limit=1)
        if not brand:
            brand = Brand.create({'name': vendor})
            _logger.info(
                "SYNC — Shopify → Odoo: Created new brand '%s' for product '%s'",
                vendor, template.name,
            )
        template.brand_id = brand.id
        _logger.info(
            "SYNC — Shopify → Odoo: product '%s' brand updated: '%s' → '%s' (Shopify Vendor: %s)",
            template.name, current_brand_name, vendor, vendor,
        )

    def _sync_shopify_tags_to_odoo(self, template, tags):
        """Shopify → Odoo: Map Shopify tags to Odoo product tags.

        Creates tags that don't exist. Replaces the product's tags with the Shopify tags.
        """
        if not tags:
            return
        if not hasattr(template, 'tag_ids'):
            _logger.debug(
                "SYNC — Shopify → Odoo: tag_ids field not available on product.template; "
                "skipping tags sync for product '%s'",
                template.name,
            )
            return
        Tag = self.env.get('product.tag')
        if not Tag:
            _logger.debug(
                "SYNC — Shopify → Odoo: product.tag model not installed; "
                "skipping tags sync for product '%s'",
                template.name,
            )
            return
        current_tags = {tag.name for tag in template.tag_ids}
        incoming_tags = set(tags)
        if current_tags == incoming_tags:
            _logger.debug(
                "SYNC — Shopify → Odoo: product '%s' tags unchanged: %s",
                template.name, list(tags),
            )
            return
        tag_records = self.env['product.tag']
        for tag_name in tags:
            tag = Tag.search([('name', '=ilike', tag_name)], limit=1)
            if not tag:
                tag = Tag.create({'name': tag_name})
                _logger.info(
                    "SYNC — Shopify → Odoo: Created new tag '%s'",
                    tag_name,
                )
            tag_records |= tag
        template.tag_ids = [(6, 0, tag_records.ids)]
        _logger.info(
            "SYNC — Shopify → Odoo: product '%s' tags updated: %s → %s",
            template.name, list(current_tags), list(incoming_tags),
        )

    def _sync_shopify_product_metafields(self, offer, product_data):
        """Pull the Shopify product's metafields into the offer's matched template.

        Uses the metafields embedded in the bulk products query when available;
        otherwise (webhook, first page consumed, or older callers) does a full
        paginated Product.metafields GraphQL read from Shopify.
        """
        product_data = product_data or {}
        shopify_product_id = product_data.get('ec_product_template_identifier')
        template = offer.matched_product_template_id
        if not shopify_product_id or not template:
            return
        variant_data = product_data.get('variant_data') or {}
        embedded = variant_data.get('metafields')
        has_next_page = bool(variant_data.get('metafields_has_next_page'))
        if embedded is not None and not has_next_page:
            self._pull_product_metafields_to_odoo(
                shopify_product_id, template, metafield_data=embedded,
            )
        else:
            self._pull_product_metafields_to_odoo(shopify_product_id, template)

    def _get_or_create_odoo_metafield_definition(
        self, namespace, key, mf_type='single_line_text_field', owner_type='PRODUCT', shopify_definition_id=None
    ):
        """Find or create centralized Odoo Metafield Definition without duplicates.
        - Primary mapping: by Shopify Definition ID (if known or resolvable).
        - Fallback mapping: by Namespace + Key + Owner Type.
        - Reuses existing definition without duplicating.
        """
        Definition = self.env['shopify.metafield.definition']
        owner = owner_type or 'PRODUCT'
        norm_def_id = utils_upsync.normalize_shopify_definition_id(shopify_definition_id)

        # 1. Primary check in Odoo by Shopify Definition ID if provided
        defn = False
        if norm_def_id:
            defn = Definition.search([
                ('ecommerce_account_id', '=', self.id),
                ('shopify_definition_id', '=', norm_def_id),
            ], limit=1)

        # 2. Fallback check in Odoo by (namespace, key, owner_type)
        if not defn:
            defn = Definition.search([
                ('namespace', '=', namespace),
                ('key', '=', key),
                ('owner_type', '=', owner),
                '|',
                ('ecommerce_account_id', '=', self.id),
                ('ecommerce_account_id', '=', False),
            ], limit=1)

        if defn:
            _logger.info("DEFINITION FOUND: %s.%s (ID: %s)", namespace, key, defn.shopify_definition_id or defn.id)
            if norm_def_id and not defn.shopify_definition_id:
                defn.write({'shopify_definition_id': norm_def_id, 'ecommerce_account_id': self.id})
                _logger.info("DEFINITION MAPPED: %s.%s mapped to Shopify Definition ID: %s", namespace, key, norm_def_id)
            _logger.info("DEFINITION REUSED: %s.%s (ID: %s)", namespace, key, defn.id)
            return defn

        # Not in Odoo: create Odoo definition without triggering duplicate product metafield creations
        name = key.replace('_', ' ').replace('-', ' ').title()
        desc = ''

        defn = Definition.with_context(skip_product_metafield_sync=True).create({
            'name': name,
            'namespace': namespace,
            'key': key,
            'description': desc,
            'owner_type': owner,
            'type': mf_type,
            'type_name': mf_type,
            'shopify_definition_id': norm_def_id or False,
            'ecommerce_account_id': self.id,
        })
        _logger.info("DEFINITION CREATED: %s.%s in Odoo (Shopify Definition ID: %s)", namespace, key, norm_def_id or '-')
        return defn

    def _pull_product_metafields_to_odoo(self, shopify_product_id, template, metafield_data=None, explicit_pull=False):
        """Shopify → Odoo: mirror a Shopify product's metafields onto the Odoo
        product template.

        * Matched by Shopify Metafield ID first, then by the logical
          (namespace, key) — so re-syncing never creates duplicate rows.
        * A row whose Shopify metafield no longer exists is ARCHIVED (active=False)
          — locally-prepared rows without a Shopify ID are left untouched.
        * Every row's value is overwritten by Shopify (Shopify is the source of
          truth for SHOPIFY_TO_ODOO). Writing Odoo rows does not trigger a
          Shopify push, which prevents infinite sync loops.

        :return: dict with 'created', 'updated', 'unchanged', 'archived' counts and
            the list of pulled metafields.
        """
        template = template.with_context(active_test=False)
        if metafield_data is None:
            _logger.info(
                "PULL — Fetching Shopify product metafields for product %s (gid=%s) "
                "via the Admin GraphQL Product.metafields query...",
                shopify_product_id, const.GLOBAL_PRODUCT_ID + shopify_product_id,
            )
            metafield_data = utils_upsync.fetch_product_metafields_from_shopify(
                self, shopify_product_id,
            )
            _logger.info(
                "PULL — Product metafield query executed. Metafield count: %d "
                "for Shopify product %s.",
                len(metafield_data), shopify_product_id,
            )
        else:
            _logger.info(
                "PULL — Using %d metafield(s) embedded in the bulk products query "
                "for Shopify product %s.",
                len(metafield_data), shopify_product_id,
            )
        for mf in metafield_data:
            _logger.info(
                "PULL — Found Shopify metafield: namespace=%r, key=%r, "
                "shopify_id=%s, value=%r, type=%r",
                mf.get('namespace'), mf.get('key'), mf.get('id'),
                mf.get('value'), mf.get('type'),
            )
        Metafield = self.env['shopify.product.metafield']
        existing = Metafield.with_context(active_test=False).search(
            [('product_tmpl_id', '=', template.id)],
            order='id',
        )
        by_shopify_id = {}
        by_ns_key = {}
        for row in existing:
            if row.shopify_metafield_id:
                val = str(row.shopify_metafield_id).strip()
                by_shopify_id[val] = row
                bare = utils_upsync.normalize_shopify_metafield_id(val)
                gid = utils_upsync.format_shopify_metafield_gid(val)
                if bare:
                    by_shopify_id[bare] = row
                if gid:
                    by_shopify_id[gid] = row
            by_ns_key.setdefault((row.namespace, row.key), row)

        seen_shopify_ids = set()
        counts = {'created': 0, 'updated': 0, 'unchanged': 0, 'archived': 0}
        valid_types = {t[0] for t in const.SHOPIFY_METAFIELD_TYPES}

        for mf in metafield_data:
            raw_mf_id = str(mf.get('gid') or mf.get('id') or '')
            norm_mf_id = utils_upsync.normalize_shopify_metafield_id(raw_mf_id)
            gid_mf_id = utils_upsync.format_shopify_metafield_gid(raw_mf_id)

            if raw_mf_id:
                seen_shopify_ids.add(raw_mf_id)
            if norm_mf_id:
                seen_shopify_ids.add(norm_mf_id)
            if gid_mf_id:
                seen_shopify_ids.add(gid_mf_id)

            namespace = mf.get('namespace')
            key = mf.get('key')
            value = mf.get('value')
            mf_type = mf.get('type') or 'single_line_text_field'
            mf_type = const.LEGACY_SHOPIFY_METAFIELD_TYPE_ALIASES.get(mf_type, mf_type)
            if not namespace or not key:
                _logger.warning(
                    "Ignoring Shopify metafield of product %s with missing namespace/key: %s",
                    shopify_product_id, mf,
                )
                continue
            if mf_type not in valid_types:
                _logger.warning(
                    "Shopify metafield %s.%s of product %s uses type '%s' which is not in "
                    "the known selection; falling back to 'single_line_text_field'.",
                    namespace, key, shopify_product_id, mf_type,
                )
                mf_type = 'single_line_text_field'

            # Primary mapping: by Shopify Metafield ID (supporting GID and numeric)
            # Fallback mapping: by Namespace + Key on this product template
            row = (
                by_shopify_id.get(gid_mf_id)
                or by_shopify_id.get(raw_mf_id)
                or by_shopify_id.get(norm_mf_id)
                or by_ns_key.get((namespace, key))
            )

            defn = self._get_or_create_odoo_metafield_definition(
                namespace=namespace,
                key=key,
                mf_type=mf_type,
                owner_type='PRODUCT',
            )

            # Store the Shopify Metafield ID (gid://shopify/Metafield/...)
            target_mf_id = gid_mf_id or raw_mf_id

            vals = {
                'definition_id': defn.id,
                'name': defn.name,
                'type': mf_type,
                'value': value if value is not None else '',
                'shopify_metafield_id': target_mf_id,
                'active': True,
                'last_sync_date': fields.Datetime.now(),
                'last_sync_direction': 'SHOPIFY_TO_ODOO',
                'last_sync_status': 'success',
                'last_sync_error': False,
            }

            if not row:
                row = Metafield.with_context(active_test=False).search([
                    ('product_tmpl_id', '=', template.id),
                    ('namespace', '=', namespace),
                    ('key', '=', key),
                ], limit=1)
                if row:
                    by_ns_key[(namespace, key)] = row

            if not row:
                created_row = Metafield.create({
                    'product_tmpl_id': template.id,
                    'namespace': namespace,
                    'key': key,
                    **vals,
                })
                by_ns_key[(namespace, key)] = created_row
                if target_mf_id:
                    by_shopify_id[target_mf_id] = created_row
                counts['created'] += 1
                _logger.info(
                    "PRODUCT METAFIELD VALUE CREATED: Product %s: %s.%s = %r (shopify_id=%s)",
                    template.display_name, namespace, key, value, target_mf_id or '-',
                )
                self._log_metafield_sync(
                    "SHOPIFY_TO_ODOO CREATE — Shopify product: %s (gid://shopify/Product/%s), "
                    "Metafield ID: %s, namespace: %s, key: %s, type: %s, value: %s" %
                    (template.display_name, shopify_product_id, target_mf_id or '-',
                     namespace, key, mf_type, value),
                    '_pull_product_metafields_to_odoo', 'INFO',
                )
                continue

            if not explicit_pull and not row.active and not row.value:
                _logger.info(
                    "PRODUCT METAFIELD SKIP: Product %s: %s.%s was explicitly deleted in Odoo. Skipping unarchive during Sync.",
                    template.display_name, namespace, key,
                )
                continue

            row.write(vals)
            counts['updated'] += 1
            _logger.info(
                "PRODUCT METAFIELD VALUE UPDATED: Product %s: %s.%s = %r (shopify_id=%s)",
                template.display_name, namespace, key, value, target_mf_id or '-',
            )
            self._log_metafield_sync(
                "SHOPIFY_TO_ODOO UPDATE — Shopify product: %s (gid://shopify/Product/%s), "
                "Metafield ID: %s, namespace: %s, key: %s, type: %s, value: %s (row %s)" %
                (template.display_name, shopify_product_id, target_mf_id or '-',
                 namespace, key, mf_type, value, row.id),
                '_pull_product_metafields_to_odoo', 'INFO',
            )

        # Deleted on Shopify → archive stale Odoo mappings that were previously
        # synced (they have a Shopify Metafield ID). Rows prepared only in Odoo
        # (no ID yet) are never touched by a pull.
        for row in existing.filtered('active'):
            if not row.shopify_metafield_id:
                continue
            r_val = str(row.shopify_metafield_id).strip()
            r_norm = utils_upsync.normalize_shopify_metafield_id(r_val)
            r_gid = utils_upsync.format_shopify_metafield_gid(r_val)
            if r_val in seen_shopify_ids or r_norm in seen_shopify_ids or r_gid in seen_shopify_ids:
                continue
            row.write({
                'active': False,
                'last_sync_date': fields.Datetime.now(),
                'last_sync_direction': 'SHOPIFY_TO_ODOO',
                'last_sync_status': 'success',
                'last_sync_error': False,
            })
            counts['archived'] += 1
            self._log_metafield_sync(
                "SHOPIFY_TO_ODOO DELETE — product: %s (gid://shopify/Product/%s), "
                "namespace: %s, key: %s, Shopify Metafield ID %s no longer exists on "
                "Shopify — archived Odoo row %s." %
                (template.display_name, shopify_product_id, row.namespace, row.key,
                 row.shopify_metafield_id, row.id),
                '_pull_product_metafields_to_odoo', 'INFO',
            )

        _logger.info(
            "Pulled metafields for Shopify product %s onto Odoo template %s: %s",
            shopify_product_id, template.display_name, counts,
        )
        return {**counts, 'metafields': metafield_data}

    def _sync_shopify_product_metafields_by_product_id(self, shopify_product_id, operation='update'):
        """Process a Shopify product webhook (create/update): re-pull the product's
        metafields from Shopify into every matched Odoo template.

        The webhook payload is NOT trusted to contain the full metafield data —
        we always re-fetch from Shopify. For a product/delete it archives the rows
        that used to come from Shopify.

        :param str shopify_product_id: numeric Shopify product ID.
        :param str operation: 'create' | 'update' | 'delete'.
        """
        offers = self.ecommerce_offer_ids.filtered(
            lambda o: o.ec_product_template_identifier == shopify_product_id
            and o.matched_product_template_id
        )
        templates = offers.matched_product_template_id
        if not templates:
            _logger.info(
                "Shopify product %s webhook (%s): no matched Odoo template, nothing to sync.",
                shopify_product_id, operation,
            )
            return
        for template in templates:
            if operation == 'delete':
                self._archive_metafields_for_deleted_product(shopify_product_id, template)
            else:
                try:
                    self._pull_product_metafields_to_odoo(shopify_product_id, template)
                except Exception as error:
                    _logger.exception(
                        "Failed to pull metafields for Shopify product %s (template %s): %s",
                        shopify_product_id, template.display_name, error,
                    )
                    self.log_xml(
                        "Webhook %s: failed to pull metafields for Shopify product %s, "
                        "template %s.\n%s" %
                        (operation, shopify_product_id, template.display_name, str(error)),
                        '_sync_shopify_product_metafields_by_product_id', 'server', 'ERROR',
                    )
                try:
                    self._pull_product_details_to_odoo(shopify_product_id, template)
                except Exception as error:
                    _logger.warning(
                        "Failed to pull product details for Shopify product %s (template %s): %s",
                        shopify_product_id, template.display_name, error,
                    )

    def _archive_metafields_for_deleted_product(self, shopify_product_id, template):
        """Shopify product deleted → archive the Odoo metafield rows that had been
        pulled from / pushed to that Shopify product."""
        template = template.with_context(active_test=False)
        Metafield = self.env['shopify.product.metafield']
        stale = Metafield.with_context(active_test=False).search(
            [('product_tmpl_id', '=', template.id)],
        ).filtered('active')
        for row in stale:
            row.write({
                'active': False,
                'last_sync_date': fields.Datetime.now(),
                'last_sync_direction': 'SHOPIFY_TO_ODOO',
                'last_sync_status': 'success',
                'last_sync_error': False,
            })
            self._log_metafield_sync(
                "SHOPIFY_TO_ODOO DELETE — product %s deleted on Shopify "
                "(gid://shopify/Product/%s), archiving namespace: %s, key: %s." %
                (template.display_name, shopify_product_id, row.namespace, row.key),
                '_archive_metafields_for_deleted_product', 'INFO',
            )
        _logger.info(
            "Archived %d metafield row(s) for Shopify product %s (template %s) after delete webhook.",
            len(stale), shopify_product_id, template.display_name,
        )

    def _log_metafield_sync(self, message, func, level='INFO'):
        """Single metafield sync log entry: structured message to the server logger
        AND to the account's `ir.logging` log (visible via the account's Logs
        button). Direction / operation / values are embedded in `message`."""
        _logger.log(getattr(logging, level.upper(), logging.INFO), message)
        self.log_xml(message, func, 'server', level, name=f"{self.name}-{self.id}")

    # === MANUAL ACTIONS (account level) === #

    def _metafield_offers_by_template(self):
        """Group connected-Shopify offers (with an existing Shopify product ID) by
        product template, so account-level pulls/pushes run once per template."""
        offers = self.ecommerce_offer_ids.filtered(
            lambda o: o.ec_product_template_identifier and o.matched_product_template_id
        )
        by_template = {}
        for offer in offers:
            template = offer.matched_product_template_id
            if template.id not in by_template:
                by_template[template.id] = {
                    'template': template,
                    'shopify_product_id': offer.ec_product_template_identifier,
                }
        return by_template

    def action_pull_shopify_metafields(self):
        """Manual button: pull metafields for every mapped Shopify product on this account."""
        self.ensure_one()
        if self.channel_code != 'shopify':
            raise UserError(_("This action is only available for Shopify accounts."))
        if self.state != 'connected':
            raise UserError(_("Please connect your Shopify account first."))

        by_template = self._metafield_offers_by_template()
        created = updated = archived = failed = 0
        for data in by_template.values():
            try:
                result = self._pull_product_metafields_to_odoo(
                    data['shopify_product_id'], data['template'], explicit_pull=True
                )
                created += result.get('created', 0)
                updated += result.get('updated', 0)
                archived += result.get('archived', 0)
            except Exception as error:
                failed += 1
                _logger.exception(
                    "Pull metafields failed for Shopify product %s (template %s): %s",
                    data['shopify_product_id'], data['template'].display_name, error,
                )
                self.log_xml(
                    "Pull metafields failed for Shopify product %s (template %s).\n%s" %
                    (data['shopify_product_id'], data['template'].display_name, str(error)),
                    'action_pull_shopify_metafields', 'server', 'ERROR',
                )

        message = (_("Pulled metafields for %s product(s): %s created, %s updated, "
                     "%s archived, %s failed.") % (len(by_template), created, updated,
                                                   archived, failed))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning' if failed else 'success',
                'title': _("Pull Shopify Metafields"),
                'message': message,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def action_push_shopify_metafields(self):
        """Manual button: push every active metafield line of every mapped product
        template on this account to Shopify."""
        self.ensure_one()
        if self.channel_code != 'shopify':
            raise UserError(_("This action is only available for Shopify accounts."))
        if self.state != 'connected':
            raise UserError(_("Please connect your Shopify account first."))

        by_template = self._metafield_offers_by_template()
        pushed = failed = 0
        for data in by_template.values():
            template = data['template']
            result = self._push_product_metafields_to_shopify(
                data['shopify_product_id'], template,
            )
            pushed += len(result.get('success', {}))
            failed += len(result.get('errors', []))
        message = (_("Pushed metafields for %s product(s): %s metafield(s) ok, "
                     "%s failed.") % (len(by_template), pushed, failed))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning' if failed else 'success',
                'title': _("Push Shopify Metafields"),
                'message': message,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def action_sync_shopify_metafields(self):
        """Manual button: pull metafields from Shopify, then push this account's
        Odoo metafields back to Shopify (bidirectional sync)."""
        self.ensure_one()
        if self.channel_code != 'shopify':
            raise UserError(_("This action is only available for Shopify accounts."))
        if self.state != 'connected':
            raise UserError(_("Please connect your Shopify account first."))

        by_template = self._metafield_offers_by_template()
        pulled = pushed = failed = 0
        messages = []
        for data in by_template.values():
            template = data['template']
            shopify_product_id = data['shopify_product_id']
            try:
                pull_result = self._pull_product_metafields_to_odoo(shopify_product_id, template)
                pulled += sum(pull_result.get(k, 0) for k in ('created', 'updated', 'archived'))
            except Exception as error:
                failed += 1
                messages.append(_("%s: pull failed — %s", template.display_name, str(error)))
                _logger.exception("Metafield sync pull failed for %s: %s", template.display_name, error)
                continue
            result = self._push_product_metafields_to_shopify(shopify_product_id, template)
            pushed += len(result.get('success', {}))
            if result.get('errors'):
                failed += len(result['errors'])
                messages.append(_("%s: %d metafield push error(s)", template.display_name, len(result['errors'])))
            else:
                messages.append(_("%s: ok", template.display_name))
        message = (_("Synced %s product(s): %s metafield(s) pulled, %s pushed, %s failed. %s")
                   % (len(by_template), pulled, pushed, failed, ' '.join(messages)))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning' if failed else 'success',
                'title': _("Sync Shopify Metafields"),
                'message': message,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def _sync_shopify_metafield_definitions(self):
        """Fetch all PRODUCT metafield definitions from Shopify via GraphQL,
        create or update corresponding Odoo definitions without duplicates,
        and ensure they are available across all products in Odoo.

        :return: (synced_definitions recordset, stats dict)
        """
        self.ensure_one()
        if self.channel_code != 'shopify':
            raise UserError(_("This action is only available for Shopify accounts."))
        if self.state != 'connected':
            raise UserError(_("Please connect your Shopify account first."))

        definitions = utils_upsync.fetch_metafield_definitions_from_shopify(
            self, owner_type='PRODUCT',
        )
        Definition = self.env['shopify.metafield.definition']
        existing = Definition.search([
            '|',
            ('ecommerce_account_id', '=', self.id),
            ('ecommerce_account_id', '=', False),
        ])
        existing_by_definition_id = {}
        existing_by_ns_key_owner = {}
        for d in existing:
            if d.shopify_definition_id:
                norm_id = utils_upsync.normalize_shopify_definition_id(d.shopify_definition_id)
                existing_by_definition_id[d.shopify_definition_id] = d
                if norm_id:
                    existing_by_definition_id[norm_id] = d
            owner = d.owner_type or 'PRODUCT'
            existing_by_ns_key_owner[(d.namespace, d.key, owner)] = d

        valid_types = {t[0] for t in const.SHOPIFY_METAFIELD_TYPES}
        created = 0
        updated = 0
        synced_definitions = Definition.browse()

        for definition in definitions:
            raw_def_id = definition.get('shopify_definition_id') or ''
            norm_def_id = utils_upsync.normalize_shopify_definition_id(raw_def_id)
            namespace = definition.get('namespace') or ''
            key = definition.get('key') or ''
            owner_type = definition.get('owner_type') or 'PRODUCT'
            if not namespace or not key:
                continue

            raw_type = definition.get('type') or 'single_line_text_field'
            mf_type = const.LEGACY_SHOPIFY_METAFIELD_TYPE_ALIASES.get(raw_type, raw_type)
            if mf_type not in valid_types:
                mf_type = 'single_line_text_field'

            name = definition.get('name') or key.replace('_', ' ').replace('-', ' ').title()
            desc = definition.get('description') or ''

            # 1. Primary mapping: Shopify Definition ID
            record = False
            if norm_def_id:
                record = existing_by_definition_id.get(norm_def_id) or existing_by_definition_id.get(raw_def_id)
            # 2. Fallback unique identifier: Namespace + Key + Owner Type
            if not record:
                record = existing_by_ns_key_owner.get((namespace, key, owner_type))

            # 3. Database fallback check
            if not record:
                db_domain = [
                    ('namespace', '=', namespace),
                    ('key', '=', key),
                    ('owner_type', '=', owner_type),
                ]
                if norm_def_id:
                    db_domain = ['|', ('shopify_definition_id', 'in', [norm_def_id, raw_def_id])] + db_domain
                db_records = Definition.search(db_domain)
                if db_records:
                    acc_records = db_records.filtered(lambda r: r.ecommerce_account_id == self)
                    record = acc_records[:1] or db_records[:1]

            if record:
                _logger.info("DEFINITION FOUND: %s.%s (Shopify Definition ID: %s)", namespace, key, norm_def_id or record.shopify_definition_id or '-')
                update_vals = {}
                mapped = False
                if norm_def_id and (not record.shopify_definition_id or utils_upsync.normalize_shopify_definition_id(record.shopify_definition_id) != norm_def_id):
                    update_vals['shopify_definition_id'] = norm_def_id
                    mapped = True
                if not record.ecommerce_account_id:
                    update_vals['ecommerce_account_id'] = self.id
                if record.name != name:
                    update_vals['name'] = name
                if record.description != desc:
                    update_vals['description'] = desc
                if record.type != mf_type:
                    update_vals['type'] = mf_type
                    update_vals['type_name'] = mf_type
                if (record.owner_type or 'PRODUCT') != owner_type:
                    update_vals['owner_type'] = owner_type

                if mapped:
                    _logger.info("DEFINITION MAPPED: %s.%s mapped to Shopify Definition ID: %s", namespace, key, norm_def_id)

                if update_vals:
                    record.write(update_vals)
                    _logger.info("DEFINITION REUSED: %s.%s (Shopify Definition ID: %s) updated with new attributes", namespace, key, norm_def_id)
                    updated += 1
                else:
                    _logger.info("DEFINITION DUPLICATE SKIPPED: %s.%s (Shopify Definition ID: %s) is already mirrored", namespace, key, norm_def_id)

                synced_definitions |= record
                if norm_def_id:
                    existing_by_definition_id[norm_def_id] = record
                existing_by_ns_key_owner[(namespace, key, owner_type)] = record
            else:
                record = Definition.create({
                    'name': name,
                    'namespace': namespace,
                    'key': key,
                    'description': desc,
                    'owner_type': owner_type,
                    'type': mf_type,
                    'type_name': mf_type,
                    'shopify_definition_id': norm_def_id or False,
                    'ecommerce_account_id': self.id,
                })
                created += 1
                _logger.info("DEFINITION CREATED: %s.%s (Shopify Definition ID: %s)", namespace, key, norm_def_id or '-')
                synced_definitions |= record
                if norm_def_id:
                    existing_by_definition_id[norm_def_id] = record
                existing_by_ns_key_owner[(namespace, key, owner_type)] = record

        # Ensure all definitions are available across all products in Odoo
        synced_definitions._sync_to_all_products()

        return synced_definitions, {
            'found': len(definitions),
            'created': created,
            'updated': updated,
        }

    def action_fetch_shopify_metafield_definitions(self):
        """Fetch Shopify PRODUCT metafield definitions and sync product values."""
        self.ensure_one()
        synced_definitions, stats = self._sync_shopify_metafield_definitions()

        # Pull product metafield values from Shopify for mapped products
        by_template = self._metafield_offers_by_template()
        pulled_products = 0
        pulled_values = 0
        pull_errors = []

        for data in by_template.values():
            template = data.get('template')
            shopify_product_id = data.get('shopify_product_id')
            if not shopify_product_id or not template:
                continue
            try:
                res = self._pull_product_metafields_to_odoo(shopify_product_id, template)
                pulled_products += 1
                pulled_values += (res.get('created', 0) + res.get('updated', 0))
            except Exception as e:
                _logger.error("Failed to pull metafields for product %s: %s", template.display_name, str(e))
                pull_errors.append(f"{template.display_name}: {str(e)}")

        _logger.info(
            "Fetched %d PRODUCT metafield definitions for account %s (%d created, %d updated), "
            "pulled values for %d product(s) (%d values updated)",
            stats['found'], self.name, stats['created'], stats['updated'], pulled_products, pulled_values,
        )
        self.log_xml(
            "Fetched %d definitions (%d created, %d updated). Pulled values for %d product(s)." %
            (stats['found'], stats['created'], stats['updated'], pulled_products),
            'action_fetch_shopify_metafield_definitions', 'server', 'INFO',
        )
        msg = _(
            "Definitions: %d found (%d created, %d updated).\n"
            "Product Values: synced for %d product(s) (%d values updated)."
        ) % (stats['found'], stats['created'], stats['updated'], pulled_products, pulled_values)
        if pull_errors:
            msg += "\n" + _("Errors: %s") % "; ".join(pull_errors[:3])

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning' if pull_errors else 'success',
                'title': _("Shopify Metafields Sync"),
                'message': msg,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    # === WEBHOOKS === #

    def action_register_webhook(self):
        """Override: register the order webhook (base behavior) AND the product
        metafield webhooks (create/update -> pull metafields, delete -> archive)."""
        result = super().action_register_webhook()
        self._register_product_metafield_webhooks()
        return result

    def _register_product_metafield_webhooks(self):
        self.ensure_one()
        if not self.shopify_store:
            return
        if not self.shopify_access_token:
            return
        webhook_url_base = f"{self.get_base_url()}/odoo/api/v1/product"
        topics = [
            (const.SHOPIFY_WEBHOOK_TOPIC_PRODUCTS_CREATE, 'create'),
            (const.SHOPIFY_WEBHOOK_TOPIC_PRODUCTS_UPDATE, 'update'),
            (const.SHOPIFY_WEBHOOK_TOPIC_PRODUCTS_DELETE, 'delete'),
        ]
        registered = []
        for topic, path in topics:
            webhook_url = f"{webhook_url_base}/{path}/account/{self.id}"
            try:
                response = shopify_utils_graphql.call_shopify_graphql_mutation(
                    account=self,
                    endpoint='webhookSubscriptionCreate',
                    params={'uri': webhook_url, 'topic': topic},
                )
                if response.get('id'):
                    registered.append(response['id'])
                _logger.info(
                    "Registered Shopify webhook %s for account %s -> %s",
                    topic, self.display_name, webhook_url,
                )
            except Exception as error:
                _logger.exception(
                    "Failed to register Shopify webhook %s for account %s: %s",
                    topic, self.display_name, error,
                )
                self.log_xml(
                    "Failed to register Shopify webhook %s for %s.\n%s" %
                    (topic, self.display_name, str(error)),
                    '_register_product_metafield_webhooks', 'server', 'ERROR',
                )
        if registered:
            existing = (self.shopify_webhook_id or '').strip()
            updated_ids = existing.split(',') if existing else []
            updated_ids.extend(registered)
            self.write({
                'shopify_webhook_id': ','.join(dict.fromkeys(updated_ids)),
            })

    def _sync_shopify_metafields_from_webhook(self, payload):
        """Entry point called by the product webhook controller. Extracts the
        numeric Shopify product ID from the payload and runs the matching sync."""
        self.ensure_one()
        admin_graphql_api_id = payload.get('admin_graphql_api_id') or ''
        product_id = False
        if admin_graphql_api_id.startswith(const.GLOBAL_PRODUCT_ID):
            product_id = admin_graphql_api_id.replace(const.GLOBAL_PRODUCT_ID, '')
        if not product_id and payload.get('id'):
            product_id = str(payload.get('id'))
        if not product_id:
            raise UserError(_("Product webhook payload must include a product id."))
        operation = 'delete' if (payload.get('operation') or '').lower() == 'delete' else 'update'
        self._sync_shopify_product_metafields_by_product_id(product_id, operation=operation)
        return product_id

    def _sync_option_linked_metafields(self, shopify_product_id, template, offers):
        """Option Linked To — best-effort drift check for products that already
        existed on Shopify (see `utils_upsync.sync_product_option_linked_metafields`).
        """
        has_attributes = (
            hasattr(template, 'attribute_line_ids')
            and len(offers) > 1
            and template.attribute_line_ids
        )
        if not has_attributes:
            return
        options_config = [
            {
                'name': attr_line.attribute_id.name,
                'namespace': attr_line.attribute_id.shopify_linked_metafield_namespace,
                'key': attr_line.attribute_id.shopify_linked_metafield_key,
            }
            for attr_line in template.attribute_line_ids[:3]
            if attr_line.attribute_id.shopify_linked_metafield_namespace
            and attr_line.attribute_id.shopify_linked_metafield_key
        ]
        if not options_config:
            return
        try:
            utils_upsync.sync_product_option_linked_metafields(self, shopify_product_id, options_config)
        except Exception as e:
            _logger.warning("Failed to check option linked metafields for product %s: %s", template.name, str(e))

    def _prepare_shopify_product_data(self, template, offers):
        """Prepare product-level data dict for Shopify.

        :param template: product.template record
        :param list offers: list of ecommerce.offer records
        :return: dict of product data
        """
        # Vendor: prefer Odoo Brand, fall back to first supplier
        vendor = ''
        if hasattr(template, 'brand_id') and template.brand_id:
            vendor = template.brand_id.name or ''
            _logger.info(
                "Odoo Brand: %s → Shopify Vendor: %s (product: %s)",
                template.brand_id.name, vendor, template.name,
            )
        else:
            supplierinfo = template.seller_ids[:1]
            if supplierinfo and supplierinfo.partner_id:
                vendor = supplierinfo.partner_id.name or ''

        # Product type independent from Odoo product category
        product_type = template.shopify_product_type or ''

        # Tags from product tags
        tags = []
        if hasattr(template, 'tag_ids') and template.tag_ids:
            tags = [tag.name for tag in template.tag_ids if tag.name]

        # Gift Card: Shopify's Admin API exposes no direct "is gift card" input on
        # ProductInput/productCreate/productUpdate — a true Shopify gift-card
        # product (redeemable code + balance) can only be created via Shopify
        # Admin's own "Gift card" flow. The closest supported approximation is to
        # mark the product type/tags so merchants and theme filters can recognize
        # it as one; `_prepare_shopify_variant_data` additionally forces
        # `requiresShipping: false` on its variants. See `product.template.is_gift_card`.
        if template.is_gift_card:
            product_type = product_type or 'Gift Card'
            if 'Gift Card' not in tags:
                tags.append('Gift Card')

        # Published status
        published_scope = self.shopify_upsync_published_scope
        status = 'ACTIVE' if published_scope != 'unpublished' else 'DRAFT'

        # Options (attribute names) & options detail — preserved in exact Odoo order
        options = []
        options_detail = []
        options_linked_metafields = []
        has_attributes = hasattr(template, 'attribute_line_ids') and bool(template.attribute_line_ids)
        if has_attributes:
            for attr_line in template.attribute_line_ids:
                if not attr_line.attribute_id or not attr_line.attribute_id.name:
                    continue
                attr_name = attr_line.attribute_id.name.strip()
                values = [val.name.strip() for val in attr_line.value_ids if val.name]
                namespace = attr_line.attribute_id.shopify_linked_metafield_namespace or ''
                key = attr_line.attribute_id.shopify_linked_metafield_key or ''
                options.append(attr_name)
                options_detail.append({
                    'name': attr_name,
                    'values': values,
                    'namespace': namespace,
                    'key': key,
                })
                options_linked_metafields.append(
                    {'namespace': namespace, 'key': key} if namespace and key else None
                )

        # Variants
        variants = [self._prepare_shopify_variant_data(offer) for offer in offers]

        # Add option values per variant (only if attributes exist)
        if options and has_attributes:
            for i, offer in enumerate(offers):
                opt_values = []
                variant_option_values = []
                product = offer.matched_product_id
                if not product:
                    continue
                for attr_line in template.attribute_line_ids:
                    if not attr_line.attribute_id or not attr_line.attribute_id.name:
                        continue
                    attr_val = False
                    if hasattr(product, 'product_template_attribute_value_ids'):
                        matched = product.product_template_attribute_value_ids.filtered(
                            lambda v, al=attr_line: v.attribute_id == al.attribute_id
                        )
                        attr_val = matched[:1]
                    val_name = attr_val.name.strip() if attr_val and attr_val.name else ''
                    opt_values.append(val_name)
                    if val_name:
                        variant_option_values.append({
                            'optionName': attr_line.attribute_id.name.strip(),
                            'name': val_name,
                        })
                variants[i]['option_values'] = opt_values
                variants[i]['variant_option_values'] = variant_option_values

        return {
            'title': template.name,
            'description_html': template.description_sale or '',
            'vendor': vendor,
            'product_type': product_type,
            'tags': tags,
            'status': status,
            'published_scope': published_scope,
            'options': options,
            'options_detail': options_detail,
            'options_linked_metafields': options_linked_metafields,
            'variants': variants,
            # Handle: explicit override or a slugified title (see `product.template.shopify_handle`).
            'handle': template.shopify_handle or utils_upsync.slugify_handle(template.name),
            # Template overrides the category mapping; otherwise inherit the
            # mapping configured once on the Odoo category (walking parents).
            'category_id': self._get_shopify_category_id(template),
            # SEO Title / SEO Description.
            'seo_title': template.shopify_seo_title or '',
            'seo_description': template.shopify_seo_description or '',
        }

    @api.model
    def _get_shopify_category_id(self, template):
        """Return the Shopify taxonomy category GID stored on the template.
        Do not use Odoo Product Category for this field.
        """
        return template.shopify_category_id or ''

    def _prepare_shopify_variant_data(self, offer):
        """Prepare variant-level data dict for Shopify.

        :param offer: ecommerce.offer record
        :return: dict of variant data
        """
        product = offer.matched_product_id
        if not product:
            raise ValueError(f"Offer '{offer.sku}' has no matched product — cannot prepare variant data.")
        template = product.product_tmpl_id

        # Compute option values for this variant
        option_values = []
        variant_option_values = []
        if hasattr(template, 'attribute_line_ids') and template.attribute_line_ids:
            for attr_line in template.attribute_line_ids:
                if not attr_line.attribute_id or not attr_line.attribute_id.name:
                    continue
                attr_val = False
                if hasattr(product, 'product_template_attribute_value_ids'):
                    matched = product.product_template_attribute_value_ids.filtered(
                        lambda v, al=attr_line: v.attribute_id == al.attribute_id
                    )
                    attr_val = matched[:1]
                val_name = attr_val.name.strip() if attr_val and attr_val.name else ''
                option_values.append(val_name)
                if val_name:
                    variant_option_values.append({
                        'optionName': attr_line.attribute_id.name.strip(),
                        'name': val_name,
                    })

        # Price from pricelist or fallback to list_price
        price = product.lst_price or 0.0
        if self.shopify_upsync_pricelist_id:
            try:
                pricelist_price = self.shopify_upsync_pricelist_id._get_product_price(
                    product, 1.0, currency=self.shopify_upsync_pricelist_id.currency_id
                )
                price = pricelist_price if pricelist_price is not None else price
            except Exception:
                pass

        # Compare-at price
        compare_at_price = offer.shopify_compare_at_price or False
        if not compare_at_price and self.shopify_upsync_compare_pricelist_id:
            try:
                compare_at_price = self.shopify_upsync_compare_pricelist_id._get_product_price(
                    product, 1.0, currency=self.shopify_upsync_compare_pricelist_id.currency_id
                )
                compare_at_price = compare_at_price if compare_at_price else False
            except Exception:
                compare_at_price = False

        # Variant Grams / Variant Weight Unit: dynamic conversion from Odoo's own
        # configured weight UoM (or a forced unit via `shopify_weight_unit`) —
        # never hardcoded. See `utils_upsync.get_shopify_variant_weight`.
        weight_value, weight_unit = utils_upsync.get_shopify_variant_weight(
            self.env, product.weight or 0.0, self.shopify_weight_unit,
        )

        # Variant Requires Shipping: services don't ship; goods/combos do.
        requires_shipping = template.type != 'service'
        # Gift cards are conventionally fulfilled digitally (see product_type/tags
        # handling in `_prepare_shopify_product_data`).
        if template.is_gift_card:
            requires_shipping = False

        return {
            'sku': offer.sku or product.default_code or '',
            'barcode': product.barcode or '',
            'price': round(price, 2),
            'compare_at_price': round(compare_at_price, 2) if compare_at_price else None,
            'weight': weight_value,
            'weight_unit': weight_unit,
            'taxable': offer.shopify_taxable,
            # Variant Inventory Tracker: 'shopify' -> tracked, 'none' -> untracked.
            'inventory_management': offer.shopify_inventory_tracker == 'shopify',
            'inventory_policy': offer.shopify_inventory_policy,
            'is_gift_card': template.is_gift_card,
            'hsn_code': getattr(product, 'hs_code', None) or getattr(template, 'hs_code', None) or '',
            'requires_shipping': requires_shipping,
            # Variant Tax Code (template-level, mirrors the hs_code fallback pattern above).
            'tax_code': template.shopify_tax_code or '',
            # Cost per Item.
            'cost': round(product.standard_price, 2) if product.standard_price else None,
            'option_values': option_values,
            'variant_option_values': variant_option_values,
        }


    def _push_product_images_to_shopify(self, shopify_product_id, template, offers):
        """Push all product images to Shopify idempotently.

        Builds a deduplicated list of Odoo images and passes each image's
        existing ``shopify_media_id`` (when present on the record) so the
        util layer can reconcile against Shopify without re-creating
        images that already exist.
        """
        if not shopify_product_id:
            return

        # 1. Clean up duplicate gallery records in Odoo first
        template._deduplicate_shopify_images()

        images = []

        def _add_image(entry):
            entry_bytes = entry['bytes']
            for existing_entry in images:
                if utils_upsync.images_match(entry_bytes, existing_entry['bytes']):
                    _logger.info(
                        "DUPLICATE DETECTED: Product '%s' image '%s' duplicates already included image '%s'",
                        template.name, entry.get('filename', ''), existing_entry.get('filename', ''),
                    )
                    _logger.info(
                        "IMAGE SKIPPED: Skipping duplicate image '%s' from push payload for product '%s'",
                        entry.get('filename', ''), template.name,
                    )
                    if not existing_entry.get('shopify_media_id') and entry.get('shopify_media_id'):
                        existing_entry['shopify_media_id'] = entry['shopify_media_id']
                        if existing_entry.get('offer_image'):
                            existing_entry['offer_image'].shopify_media_id = entry['shopify_media_id']
                        _logger.info(
                            "IMAGE UPDATED: Propagated shopify_media_id=%s to primary entry '%s'",
                            entry['shopify_media_id'], existing_entry.get('filename', ''),
                        )
                    return

            _logger.info(
                "IMAGE FOUND: Added unique image '%s' to push payload for product '%s'",
                entry.get('filename', ''), template.name,
            )
            images.append(entry)

        # Template main image — Image Position 0.
        if template.image_1920:
            try:
                entry = {
                    'bytes': base64.b64decode(template.image_1920),
                    'filename': f"{template.name or 'product'}.png",
                    'mime_type': 'image/png',
                    'offer_image': template,
                    'shopify_media_id': template.shopify_media_id or '',
                    'shopify_image_url': template.shopify_image_url or '',
                    'alt': template.shopify_seo_title or template.name or '',
                    'variant_offer': None,
                }
                _add_image(entry)
            except Exception as e:
                _logger.warning("Failed to decode template image for %s: %s", template.name, str(e))

        # Additional images from product.template shopify_image_ids
        for img in template.shopify_image_ids:
            if img.image:
                try:
                    entry = {
                        'bytes': base64.b64decode(img.image),
                        'filename': f"{img.name or 'image'}.png",
                        'mime_type': 'image/png',
                        'offer_image': img,
                        'shopify_media_id': img.shopify_media_id or '',
                        'shopify_image_url': img.shopify_image_url or '',
                        'alt': img.name or '',
                        'variant_offer': None,
                    }
                    _add_image(entry)
                except Exception as e:
                    _logger.warning("Failed to decode additional image: %s", str(e))

        # Variant-level images
        for offer in offers:
            product = offer.matched_product_id
            if product and product.image_variant_1920:
                try:
                    entry = {
                        'bytes': base64.b64decode(product.image_variant_1920),
                        'filename': f"{product.name or 'variant'}.png",
                        'mime_type': 'image/png',
                        'offer_image': None,
                        'alt': product.display_name or '',
                        'variant_offer': offer,
                    }
                    _add_image(entry)
                except Exception as e:
                    _logger.warning("Failed to decode variant image for %s: %s", offer.sku, str(e))

        if not images:
            _logger.info("IMAGE SKIPPED: No images found for product %s — skipping image push", template.name)
            return

        _logger.info("Pushing %d image(s) for product %s to Shopify", len(images), template.name)
        try:
            images = utils_upsync.push_product_images_to_shopify(self, shopify_product_id, images) or images

            # Image Position: reorder using the exact order the list was built in
            media_id_positions = [
                (img['shopify_media_id'], position)
                for position, img in enumerate(images) if img.get('shopify_media_id')
            ]
            if media_id_positions:
                utils_upsync.reorder_product_media(self, shopify_product_id, media_id_positions)

            # Variant Image: link each variant's image to that variant specifically.
            for img in images:
                offer = img.get('variant_offer')
                media_id = img.get('shopify_media_id')
                if offer and media_id and offer.ec_product_identifier:
                    utils_upsync.link_media_to_variant(
                        self, shopify_product_id, offer.ec_product_identifier, media_id,
                    )
        except Exception as e:
            _logger.warning("Failed to push images for product %s: %s", template.name, str(e))

    def _restore_shopify_product_mapping(self, template, offers, shopify_product):
        """Restore Odoo ↔ Shopify product mapping from a found Shopify product record.

        Persists:
        - template.shopify_handle
        - offer.ec_product_template_identifier
        - offer.ec_product_identifier (matched by variant SKU)
        - offer.shopify_inventory_item_id
        """
        raw_id = shopify_product.get('id', '')
        shopify_product_id = raw_id.replace(const.GLOBAL_PRODUCT_ID, '')
        handle = shopify_product.get('handle')
        if handle:
            template.shopify_handle = handle
        cat_info = shopify_product.get('category') or {}
        cat_gid = cat_info.get('id') if isinstance(cat_info, dict) else (cat_info or '')
        if cat_gid:
            template.shopify_category_id = cat_gid
        col_nodes = (shopify_product.get('collections') or {}).get('nodes') or []
        if col_nodes:
            template.shopify_collection_id = col_nodes[0].get('id')

        variant_nodes = (shopify_product.get('variants') or {}).get('nodes') or []
        remote_variants = []
        for var in variant_nodes:
            if not var:
                continue
            gid = var.get('id') or ''
            inv_gid = ((var.get('inventoryItem') or {}).get('id')) or ''
            remote_variants.append({
                'id': gid,
                'numeric_id': gid.replace(const.GLOBAL_PRODUCT_VARIANT_ID, ''),
                'title': var.get('title') or '',
                'sku': var.get('sku') or '',
                'barcode': var.get('barcode') or '',
                'selectedOptions': var.get('selectedOptions') or [],
                'inventory_item_id': inv_gid.replace(const.GLOBAL_INVENTORY_ITEM_ID, ''),
            })

        matched_updates, _, _ = utils_upsync.match_and_reconcile_variants(
            offers, remote_variants
        )
        matched_map = {offer.id: rv for offer, rv in matched_updates}

        for offer in offers:
            vals = {
                'ec_product_template_identifier': shopify_product_id,
                'shopify_push_status': 'pushed',
                'shopify_push_error': False,
            }
            matched_var = matched_map.get(offer.id)
            if matched_var:
                vals['ec_product_identifier'] = matched_var.get('numeric_id', '')
                vals['shopify_inventory_item_id'] = matched_var.get('inventory_item_id', '')
            offer.write(vals)

        return shopify_product_id


    # === SHOPIFY COLLECTION SYNC === #

    def _get_or_create_shopify_collection_for_category(self, category):
        """Find or create the Shopify collection corresponding to an Odoo product.category.

        Order of resolution:
        1. Reuse `category.shopify_collection_id` if set and valid on Shopify.
        2. Find by title or handle on Shopify.
        3. Create on Shopify using collectionCreate if it doesn't exist.
        Persists `shopify_collection_id` on the category record so future pushes reuse it.

        :param category: product.category record
        :return: str Shopify Collection GID or False
        """
        if not category:
            return False

        collection_id = category.shopify_collection_id
        if collection_id:
            existing = utils_upsync.get_shopify_collection_by_id(self, collection_id)
            if existing:
                return existing['id']
            _logger.info(
                "Category %s had collection ID %s but it was not found on Shopify; re-resolving.",
                category.name, collection_id,
            )
            category.shopify_collection_id = False

        # Build title:
        # Example: Category = ACCESSORIES / GIFT -> Collection = ACCESSORIES / GIFT
        complete_name = category.complete_name or category.name or ''
        title = complete_name
        if title.startswith('All / '):
            title = title[6:].strip()
        if not title:
            title = category.name or 'Collection'

        handle = utils_upsync.slugify_handle(title)

        # Lookup by title or handle on Shopify
        found = utils_upsync.find_shopify_collection(self, title=title, handle=handle)
        if not found and category.name and category.name != title:
            found = utils_upsync.find_shopify_collection(
                self, title=category.name, handle=utils_upsync.slugify_handle(category.name)
            )

        if found:
            col_id = found['id']
            category.shopify_collection_id = col_id
            return col_id

        # Create collection on Shopify
        created = utils_upsync.create_shopify_collection(self, title=title, handle=handle)
        col_id = created['id']
        category.shopify_collection_id = col_id
        return col_id

    def _sync_product_collection_to_shopify(self, shopify_product_id, template):
        """Synchronize product category to Shopify collection membership.

        - If product has an Odoo category, finds/creates the matching collection and adds product.
        - If category changed, removes product from previous collection(s) and adds to the new one.
        - Idempotent: does not create duplicate collections or duplicate product assignments.

        :param str shopify_product_id: Shopify product ID or GID
        :param template: product.template record
        """
        category = template.categ_id
        if not category:
            if template.shopify_collection_id:
                try:
                    utils_upsync.remove_product_from_shopify_collection(
                        self, template.shopify_collection_id, shopify_product_id
                    )
                except Exception as e:
                    _logger.warning(
                        "Failed to remove product %s from previous collection %s: %s",
                        shopify_product_id, template.shopify_collection_id, e
                    )
                template.shopify_collection_id = False
            return

        # 1. Resolve or create Shopify collection for this category
        collection_id = self._get_or_create_shopify_collection_for_category(category)
        if not collection_id:
            return

        # 2. Check current product collections on Shopify
        current_collections = utils_upsync.get_product_shopify_collections(self, shopify_product_id)
        current_col_ids = {c['id'] for c in current_collections}

        # 3. Update membership if category changed
        previous_collection_id = template.shopify_collection_id
        if previous_collection_id and previous_collection_id != collection_id:
            try:
                utils_upsync.remove_product_from_shopify_collection(
                    self, previous_collection_id, shopify_product_id
                )
                current_col_ids.discard(previous_collection_id)
            except Exception as e:
                _logger.warning(
                    "Failed to remove product %s from previous collection %s: %s",
                    shopify_product_id, previous_collection_id, e
                )

        # Also remove from any other known category collections in Odoo if stale
        known_cat_col_ids = set(
            self.env['product.category'].search([('shopify_collection_id', '!=', False)]).mapped('shopify_collection_id')
        )
        for c_id in list(current_col_ids):
            if c_id in known_cat_col_ids and c_id != collection_id:
                try:
                    utils_upsync.remove_product_from_shopify_collection(self, c_id, shopify_product_id)
                    current_col_ids.discard(c_id)
                except Exception as e:
                    _logger.warning(
                        "Failed to remove product %s from stale category collection %s: %s",
                        shopify_product_id, c_id, e
                    )

        # 4. Add product to target collection if not already a member
        if collection_id not in current_col_ids:
            try:
                utils_upsync.add_product_to_shopify_collection(self, collection_id, shopify_product_id)
            except Exception as e:
                _logger.warning(
                    "Failed to add product %s to collection %s: %s",
                    shopify_product_id, collection_id, e
                )

        template.shopify_collection_id = collection_id

