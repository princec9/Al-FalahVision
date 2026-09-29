# Part of Odoo. See LICENSE file for full copyright and licensing details.
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestShopifyCompanyFilter(TransactionCase):

    def setUp(self):
        super().setUp()
        self.company_a = self.env.company
        self.company_b = self.env['res.company'].create({'name': 'Company B Test'})

        channel = self.env['ecommerce.channel'].search([('code', '=', 'shopify')], limit=1)
        if not channel:
            channel = self.env['ecommerce.channel'].create({
                'name': 'Shopify Test Channel',
                'code': 'shopify',
                'sequence': 40,
            })

        self.account = self.env['ecommerce.account'].create({
            'name': 'Test Shopify Account Filter',
            'company_id': self.company_a.id,
            'ecommerce_channel_id': channel.id,
            'fulfilled_by': 'ecommerce',
            'shopify_store': 'test-company-store',
            'shopify_access_token': 'test-token',
            'state': 'connected',
        })

        self.tmpl_a = self.env['product.template'].create({
            'name': 'Product A',
            'company_id': self.company_a.id,
            'default_code': 'SKU-A',
        })
        self.product_a = self.tmpl_a.product_variant_id

        self.tmpl_b = self.env['product.template'].create({
            'name': 'Product B',
            'company_id': self.company_b.id,
            'default_code': 'SKU-B',
        })
        self.product_b = self.tmpl_b.product_variant_id

        self.offer_a = self.env['ecommerce.offer'].create({
            'name': self.product_a.display_name,
            'sku': self.product_a.default_code,
            'ecommerce_account_id': self.account.id,
            'matched_product_id': self.product_a.id,
        })
        self.offer_b = self.env['ecommerce.offer'].create({
            'name': self.product_b.display_name,
            'sku': self.product_b.default_code,
            'ecommerce_account_id': self.account.id,
            'matched_product_id': self.product_b.id,
        })

        self.mock_push_response = {
            'shopify_product_id': '111',
            'variants': [{
                'shopify_variant_id': '222',
                'shopify_inventory_item_id': '333',
                'sku': 'SKU-A',
            }],
        }

    def test_push_products_without_company_filter(self):
        """When company_ids is not set, all products are pushed."""
        self.account.company_ids = False
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   return_value=self.mock_push_response) as mock_push:
            self.account.action_push_products()
            self.assertEqual(mock_push.call_count, 2)
            self.assertEqual(self.offer_a.shopify_push_status, 'pushed')
            self.assertEqual(self.offer_b.shopify_push_status, 'pushed')

    def test_push_products_with_company_filter(self):
        """When company_ids is set to Company A, only Company A product is pushed."""
        self.account.company_ids = [(6, 0, [self.company_a.id])]
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   return_value=self.mock_push_response) as mock_push:
            self.account.action_push_products()
            self.assertEqual(mock_push.call_count, 1)
            self.assertEqual(self.offer_a.shopify_push_status, 'pushed')
            self.assertNotEqual(self.offer_b.shopify_push_status, 'pushed')

    def test_push_products_multiple_selected_companies(self):
        """When multiple companies are selected, products of all selected companies are pushed."""
        self.account.company_ids = [(6, 0, [self.company_a.id, self.company_b.id])]
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   return_value=self.mock_push_response) as mock_push:
            self.account.action_push_products()
            self.assertEqual(mock_push.call_count, 2)
            self.assertEqual(self.offer_a.shopify_push_status, 'pushed')
            self.assertEqual(self.offer_b.shopify_push_status, 'pushed')
