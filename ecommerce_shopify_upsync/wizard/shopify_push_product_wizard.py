# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ShopifyPushProductWizard(models.TransientModel):
    _name = 'shopify.push.product.wizard'
    _description = 'Push New Product to Shopify'

    ecommerce_account_id = fields.Many2one(
        comodel_name='ecommerce.account',
        string="Shopify Account",
        required=True,
        domain=[('channel_code', '=', 'shopify'), ('state', '=', 'connected')],
    )
    product_ids = fields.Many2many(
        comodel_name='product.product',
        string="Products",
        required=True,
        domain=[('sale_ok', '=', True)],
        help="Select one or more Odoo products to push to Shopify.",
    )
    push_all_variants = fields.Boolean(
        string="Push All Variants",
        default=True,
        help="If enabled, all variants of each selected product template will be pushed together.",
    )
    is_multi_variant = fields.Boolean(
        compute='_compute_is_multi_variant',
    )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids') or []
        active_id = self.env.context.get('active_id')
        if not active_ids and active_id:
            active_ids = [active_id]

        if 'product_ids' not in fields_list or not active_ids:
            return values

        if active_model == 'product.template':
            templates = self.env['product.template'].browse(active_ids)
            values['product_ids'] = [(6, 0, templates.mapped('product_variant_id').ids)]
        elif active_model == 'product.product':
            values['product_ids'] = [(6, 0, active_ids)]
        return values

    @api.depends('product_ids')
    def _compute_is_multi_variant(self):
        for rec in self:
            rec.is_multi_variant = any(
                product.product_tmpl_id.product_variant_count > 1
                for product in rec.product_ids
            )

    def action_push(self):
        """Create offer(s) and push one or more products to Shopify."""
        self.ensure_one()

        account = self.ecommerce_account_id
        products = self.product_ids

        if account.state != 'connected':
            raise UserError(_("Shopify account is not connected."))
        if not products:
            raise UserError(_("Please select at least one product."))

        selected_templates = products.mapped('product_tmpl_id')
        products_to_push = self.env['product.product']
        for template in selected_templates:
            selected_template_products = products.filtered(lambda p: p.product_tmpl_id == template)
            if self.push_all_variants and template.product_variant_count > 1:
                products_to_push |= template.product_variant_ids
            else:
                products_to_push |= selected_template_products

        missing_sku_products = products_to_push.filtered(lambda p: not p.default_code)
        if missing_sku_products:
            raise UserError(_(
                "SKU is required for all selected products. Missing Internal Reference on: %s"
            ) % ", ".join(missing_sku_products.mapped('display_name')))

        template_offer_map = {}
        total_offers = 0
        pushed_templates = 0
        for template in products_to_push.mapped('product_tmpl_id'):
            template_products = products_to_push.filtered(lambda p: p.product_tmpl_id == template)
            template_offers = self.env['ecommerce.offer']
            for product in template_products:
                existing_offer = account.ecommerce_offer_ids.filtered(
                    lambda o: o.matched_product_id == product or o.sku == product.default_code
                )[:1]
                if existing_offer:
                    template_offers |= existing_offer
                    continue

                offer = self.env['ecommerce.offer'].with_context(tracking_disable=True).create({
                    'sku': product.default_code,
                    'name': product.display_name,
                    'ecommerce_account_id': account.id,
                    'matched_product_id': product.id,
                    'shopify_push_status': 'pending',
                })
                template_offers |= offer

            if not template_offers:
                continue
            template_offer_map[template] = template_offers

        if not template_offer_map:
            raise UserError(_("No offers could be created or reused for the selected products."))

        try:
            for template, offers in template_offer_map.items():
                account._push_product_template_to_shopify(offers)
                total_offers += len(offers)
                pushed_templates += 1
        except Exception as e:
            for offers in template_offer_map.values():
                offers.write({
                    'shopify_push_status': 'error',
                    'shopify_push_error': str(e),
                })
            raise UserError(_("Failed to push product to Shopify: %s") % str(e))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Products Pushed"),
                'message': _(
                    "%s template(s) and %s offer(s) successfully pushed to Shopify."
                ) % (pushed_templates, total_offers),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
        
