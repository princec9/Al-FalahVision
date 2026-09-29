# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.ecommerce_shopify import const
from odoo.addons.ecommerce_shopify_upsync import utils_upsync

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # `shopify_image_ids` (product.shopify.image) lives in ecommerce_shopify —
    # shared with the product import so a pushed image and an imported one are
    # the same record, not two disconnected copies.

    # Handle: populated automatically from the mapped Shopify product.
    shopify_handle = fields.Char(
        string="Shopify Handle",
        readonly=True,
        help="Shopify URL handle. Populated automatically from the mapped Shopify product.",
    )
    # Shopify Product Type: kept independent from Odoo product category
    shopify_product_type = fields.Char(
        string="Shopify Product Type",
        help="Custom Shopify Product Type. Independent from the Odoo product category.",
    )
    # Shopify Collection ID: current collection membership in Shopify
    shopify_collection_id = fields.Char(
        string="Shopify Collection ID",
        readonly=True,
        help="Shopify Collection GID where this product is currently assigned.",
    )
    # Shopify Product Category: the Shopify Standard Product Taxonomy category GID,
    # e.g. "gid://shopify/TaxonomyCategory/sg-4-17-2-17". Fetched automatically from Shopify.
    shopify_category_id = fields.Char(
        string="Shopify Product Category",
        readonly=True,
        help="Shopify Standard Product Taxonomy category GID, e.g. "
             "gid://shopify/TaxonomyCategory/sg-4-17-2-17. Fetched automatically from Shopify.",
    )
    shopify_seo_title = fields.Char(string="Shopify SEO Title")
    shopify_seo_description = fields.Text(string="Shopify SEO Description")
    # Gift Card: see `ecommerce_account._prepare_shopify_variant_data` and the
    # module README section on why this is a best-effort heuristic, not a native
    # Shopify "is gift card" flag (the Admin API doesn't expose one on ProductInput).
    is_gift_card = fields.Boolean(
        string="Is Gift Card",
        help="Marks this product as a gift card when pushed to Shopify: sets the "
             "product type to 'Gift Card', adds a 'Gift Card' tag, and forces "
             "variants to not require shipping. Shopify's Admin API has no direct "
             "'is gift card' field on products, so this is the closest supported "
             "approximation — true Shopify gift-card products (with redeemable "
             "codes/balances) must still be created via Shopify Admin.",
    )
    # Variant Tax Code: applies to every variant of this template, mirroring the
    # existing hs_code dual-check pattern (checked at variant level with a
    # template-level fallback) in `_prepare_shopify_variant_data`.
    shopify_tax_code = fields.Char(
        string="Shopify Tax Code",
        help="Tax code passed to Shopify's InventoryItem/ProductVariant `taxCode` "
             "field, used by external tax services (e.g. Avalara, TaxJar) to "
             "classify this product.",
    )
    shopify_metafield_ids = fields.One2many(
        comodel_name='shopify.product.metafield',
        inverse_name='product_tmpl_id',
        string="Shopify Metafields",
    )

    @api.model_create_multi
    def create(self, vals_list):
        templates = super().create(vals_list)
        templates._ensure_metafield_definitions()
        return templates

    def _ensure_metafield_definitions(self):
        """Ensure all centralized metafield definitions exist on these products."""
        definitions = self.env['shopify.metafield.definition'].search([])
        if not definitions:
            return
        Metafield = self.env['shopify.product.metafield'].with_context(active_test=False)
        for template in self:
            existing = Metafield.search([('product_tmpl_id', '=', template.id)])
            existing_ns_keys = {(m.namespace, m.key) for m in existing}
            to_create = []
            for defn in definitions:
                if not defn.namespace or not defn.key:
                    continue
                if (defn.namespace, defn.key) in existing_ns_keys:
                    matching = existing.filtered(
                        lambda m, d=defn: (m.namespace, m.key) == (d.namespace, d.key) and not m.definition_id
                    )
                    if matching:
                        matching.write({'definition_id': defn.id})
                    continue
                to_create.append({
                    'product_tmpl_id': template.id,
                    'definition_id': defn.id,
                    'namespace': defn.namespace,
                    'key': defn.key,
                    'type': defn.type or defn.type_name or 'single_line_text_field',
                    'value': '',
                })
            if to_create:
                Metafield.create(to_create)

    def action_refresh_metafield_definitions(self):
        """Fetch all Shopify product metafield definitions via GraphQL, create/update
        Odoo definitions without duplicates, make them available across all products,
        load individual product values if this product is mapped, and reload the UI.
        """
        self.ensure_one()

        # 1. Discover the connected Shopify account and optional Shopify product mapping
        account = False
        shopify_product_id = False
        offers = self.env['ecommerce.offer'].search([
            ('matched_product_template_id', '=', self.id),
            ('ecommerce_account_id.state', '=', 'connected'),
            ('ecommerce_account_id.channel_code', '=', 'shopify'),
        ])
        if offers:
            mapped_offers = offers.filtered(lambda o: o.ec_product_template_identifier)
            chosen_offer = mapped_offers[:1] if mapped_offers else offers[:1]
            account = chosen_offer.ecommerce_account_id
            shopify_product_id = chosen_offer.ec_product_template_identifier

        if not account:
            account = self.env['ecommerce.account'].search([
                ('channel_code', '=', 'shopify'),
                ('state', '=', 'connected'),
            ], limit=1)

        if not account:
            self._ensure_metafield_definitions()
            raise UserError(_("No connected Shopify account found. Please connect an account first."))

        # 2. Fetch definitions from Shopify and distribute to all products
        synced_definitions, stats = account._sync_shopify_metafield_definitions()
        self._ensure_metafield_definitions()

        # 3. If this product is mapped to Shopify, load its individual values
        pulled_values = 0
        if shopify_product_id:
            try:
                res = account._pull_product_metafields_to_odoo(shopify_product_id, self)
                pulled_values = (res.get('created', 0) + res.get('updated', 0))
            except Exception as e:
                _logger.warning(
                    "Failed to pull product metafield values during definition refresh for %s: %s",
                    self.display_name, str(e),
                )

        found = stats.get('found', 0)
        created = stats.get('created', 0)
        updated = stats.get('updated', 0)

        msg_parts = [
            _("Definitions refreshed: %(found)d found (%(created)d created, %(updated)d updated).",
              found=found, created=created, updated=updated)
        ]
        if shopify_product_id:
            msg_parts.append(
                _("%(pulled)d product value(s) loaded.", pulled=pulled_values)
            )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Refresh Definitions"),
                'message': " ".join(msg_parts),
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    # === PER-PRODUCT METAFIELD SYNC BUTTONS === #

    def _get_shopify_account_and_product_id(self):
        """Return the first (account, shopify_product_id) pair for this template.
        Raises UserError when no connected Shopify account is mapped."""
        self.ensure_one()
        offers = self.env['ecommerce.offer'].search([
            '|',
            ('matched_product_template_id', '=', self.id),
            ('matched_product_id.product_tmpl_id', '=', self.id),
            ('ecommerce_account_id.state', '=', 'connected'),
            ('ecommerce_account_id.channel_code', '=', 'shopify'),
            ('ec_product_template_identifier', '!=', False),
        ])
        if not offers:
            raise UserError(_(
                "This product is not mapped to a connected Shopify account. "
                "Open the E-commerce Offers tab and link it first."
            ))
        # Prefer the first offer whose account has a Shopify product ID
        offer = offers[:1]
        return offer.ecommerce_account_id, offer.ec_product_template_identifier

    def action_pull_shopify_metafields(self):
        """Manual button on the product form: pull metafields from Shopify."""
        self.ensure_one()
        account, shopify_product_id = self._get_shopify_account_and_product_id()
        try:
            result = account._pull_product_metafields_to_odoo(
                shopify_product_id, self, explicit_pull=True
            )
            count = sum(result.get(k, 0) for k in ('created', 'updated', 'archived'))
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'type': 'success',
                    'title': _("Pull Shopify Metafields"),
                    'message': _("%s metafield(s) updated from Shopify." % count) if count else _(
                        "All metafields are already up to date."),
                    'next': {'type': 'ir.actions.client', 'tag': 'reload'},
                },
            }
        except Exception as e:
            message = str(e)
            self.shopify_metafield_ids.filtered('active').write({
                'last_sync_direction': 'SHOPIFY_TO_ODOO',
                'last_sync_status': 'failed',
                'last_sync_error': message,
            })
            _logger.exception("Pull Shopify metafields failed for product %s: %s", self.display_name, message)
            raise UserError(_("Pull failed: %s") % message)

    def action_push_shopify_metafields(self):
        """Manual button on the product form: push Odoo metafields to Shopify."""
        self.ensure_one()
        account, shopify_product_id = self._get_shopify_account_and_product_id()
        try:
            result = account._push_product_metafields_to_shopify(
                shopify_product_id, self,
            )
        except Exception as e:
            message = str(e)
            self.shopify_metafield_ids.filtered('active').write({
                'last_sync_direction': 'ODOO_TO_SHOPIFY',
                'last_sync_status': 'failed',
                'last_sync_error': message,
            })
            _logger.exception("Push Shopify metafields failed for product %s: %s", self.display_name, message)
            raise UserError(_("Push failed: %s") % message)

        pushed = len(result.get('success', {}))
        errors = len(result.get('errors', []))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning' if errors else 'success',
                'title': _("Push Shopify Metafields"),
                'message': (_("%s pushed, %s failed." % (pushed, errors))
                            if errors else _("%s metafield(s) pushed to Shopify." % pushed)),
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def action_sync_shopify_metafields(self):
        """Manual button on the product form: pull then push (bidirectional sync).
        Direction and status reflect the actual operation performed for each metafield."""
        self.ensure_one()
        account, shopify_product_id = self._get_shopify_account_and_product_id()
        try:
            pull_result = account._pull_product_metafields_to_odoo(shopify_product_id, self)
        except Exception as e:
            message = str(e)
            self.shopify_metafield_ids.filtered('active').write({
                'last_sync_direction': 'SHOPIFY_TO_ODOO',
                'last_sync_status': 'failed',
                'last_sync_error': message,
            })
            _logger.exception("Sync Metafields (Pull phase) failed for product %s: %s", self.display_name, message)
            raise UserError(_("Pull phase failed: %s") % message)

        pulled_keys = {
            (mf.get('namespace'), mf.get('key'))
            for mf in pull_result.get('metafields', [])
            if mf.get('namespace') and mf.get('key')
        }

        # Identify lines with local values that were NOT pulled from Shopify
        lines_to_push = self.shopify_metafield_ids.filtered(
            lambda l: l.active and l.value and (l.namespace, l.key) not in pulled_keys
        )

        pushed = 0
        errors = 0
        if lines_to_push:
            try:
                push_res = utils_upsync.push_product_metafields_to_shopify(
                    account, shopify_product_id, lines_to_push,
                )
                success_map = push_res.get('success', {})
                per_line_errors = {(item[0], item[1]): item[2] for item in push_res.get('errors', [])}
                for line in lines_to_push:
                    metafield_id = success_map.get((line.namespace, line.key))
                    error_message = per_line_errors.get((line.namespace, line.key))
                    if (line.namespace, line.key) in success_map:
                        target_id = metafield_id or line.shopify_metafield_id
                        target_id = utils_upsync.format_shopify_metafield_gid(target_id)
                        line.write({
                            'shopify_metafield_id': target_id,
                            'last_sync_date': fields.Datetime.now(),
                            'last_sync_direction': 'ODOO_TO_SHOPIFY',
                            'last_sync_status': 'success',
                            'last_sync_error': False,
                        })
                        pushed += 1
                    else:
                        line.write({
                            'last_sync_direction': 'ODOO_TO_SHOPIFY',
                            'last_sync_status': 'failed',
                            'last_sync_error': error_message or 'Unknown Shopify error',
                        })
                        errors += 1
            except Exception as e:
                errors += len(lines_to_push)
                lines_to_push.write({
                    'last_sync_direction': 'ODOO_TO_SHOPIFY',
                    'last_sync_status': 'failed',
                    'last_sync_error': str(e),
                })
                _logger.exception("Sync Metafields (Push phase) failed for product %s: %s", self.display_name, e)

        pulled_count = len(pulled_keys)
        msg = _("%s metafield(s) pulled from Shopify, %s pushed to Shopify.") % (pulled_count, pushed)
        if errors:
            msg += _(" %s error(s).") % errors

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'warning' if errors else 'success',
                'title': _("Sync Metafields"),
                'message': msg,
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }


    # === Push to Shopify (product data) === #

    def action_push_to_shopify(self):
        """One-click re-push of this product's own variants to every Shopify
        account it is already mapped to — no bulk 'Push Products' button, no
        'Push New Product' wizard. Reuses the exact same
        `ecommerce.account._push_product_template_to_shopify` used by both of those,
        so behavior (create-vs-update, images, publish, metafields, markets...)
        stays identical.

        Also auto-creates an offer for any variant of this template that doesn't
        have one yet on an account it's already connected to (e.g. a color added
        after the first push) — that's the #1 reason a product ends up on Shopify
        missing its Options/Variants: only variants with an offer get included.
        """
        self.ensure_one()
        Offer = self.env['ecommerce.offer']
        existing_offers = Offer.search([
            ('matched_product_template_id', '=', self.id),
            ('ecommerce_account_id.channel_code', '=', 'shopify'),
        ])
        accounts = existing_offers.ecommerce_account_id
        if not accounts:
            raise UserError(_(
                "This product isn't linked to a Shopify account yet. Use the "
                "'Push New Product' wizard once first (from a connected Shopify "
                "account, or Products > Push New Product) — after that, this "
                "button will re-push updates directly."
            ))

        pushed_accounts = 0
        messages = []
        for account in accounts:
            if account.state != 'connected':
                messages.append(_("%s: account is not connected, skipped.") % account.display_name)
                continue

            account_offers = existing_offers.filtered(lambda o, a=account: o.ecommerce_account_id == a)
            # Include every variant of this template, not just the ones that
            # already had an offer — this is what fixes "Shopify shows no
            # variants" for a color/size added after the initial push.
            missing_variants = self.product_variant_ids.filtered(
                lambda p, seen=account_offers.matched_product_id: p not in seen
            )
            missing_sku = missing_variants.filtered(lambda p: not p.default_code)
            if missing_sku:
                messages.append(_("%s: missing Internal Reference on: %s") % (
                    account.display_name, ", ".join(missing_sku.mapped('display_name')),
                ))
                continue
            for product in missing_variants:
                account_offers |= Offer.with_context(tracking_disable=True).create({
                    'sku': product.default_code,
                    'name': product.display_name,
                    'ecommerce_account_id': account.id,
                    'matched_product_id': product.id,
                    'shopify_push_status': 'pending',
                })

            try:
                account._push_product_template_to_shopify(account_offers)
                pushed_accounts += 1
            except Exception as e:
                account_offers.write({'shopify_push_status': 'error', 'shopify_push_error': str(e)})
                messages.append(_("%s: push failed — %s") % (account.display_name, str(e)))

        if pushed_accounts:
            messages.insert(0, _("Pushed to %s Shopify account(s).") % pushed_accounts)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success' if pushed_accounts and len(messages) == 1 else 'warning',
                'title': _("Push to Shopify"),
                'message': "\n".join(messages) or _("Nothing to push."),
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }
