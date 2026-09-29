# Part of Odoo. See LICENSE file for full copyright and licensing details.
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.ecommerce_shopify_upsync import utils_upsync


@tagged('post_install', '-at_install')
class TestShopifyAutoFetchAndReadOnly(TransactionCase):

    def setUp(self):
        super().setUp()
        self.company = self.env.company

        channel = self.env['ecommerce.channel'].search([('code', '=', 'shopify')], limit=1)
        if not channel:
            channel = self.env['ecommerce.channel'].create({
                'name': 'Shopify Test Channel',
                'code': 'shopify',
                'sequence': 40,
            })

        self.account = self.env['ecommerce.account'].create({
            'name': 'Test Shopify Account Auto Fetch',
            'company_id': self.company.id,
            'ecommerce_channel_id': channel.id,
            'fulfilled_by': 'ecommerce',
            'shopify_store': 'test-fetch-store',
            'shopify_access_token': 'test-token',
            'state': 'connected',
        })

        self.cat = self.env['product.category'].create({
            'name': 'Apparel',
        })

        self.tmpl = self.env['product.template'].create({
            'name': 'Graphic T-Shirt',
            'categ_id': self.cat.id,
            'default_code': 'SKU-TSHIRT-01',
            'list_price': 25.0,
        })
        self.product = self.tmpl.product_variant_id
        self.offer = self.env['ecommerce.offer'].create({
            'name': self.product.display_name,
            'sku': self.product.default_code,
            'ecommerce_account_id': self.account.id,
            'matched_product_id': self.product.id,
        })

    def test_fields_are_readonly(self):
        """Verify that Shopify Handle, Product Category, and Collection ID are read-only."""
        self.assertTrue(self.tmpl._fields['shopify_handle'].readonly)
        self.assertTrue(self.tmpl._fields['shopify_category_id'].readonly)
        self.assertTrue(self.tmpl._fields['shopify_collection_id'].readonly)
        self.assertTrue(self.cat._fields['shopify_collection_id'].readonly)

    def test_google_shopping_helper_removed(self):
        """Verify that action_add_google_shopping_metafields has been completely removed."""
        self.assertFalse(hasattr(self.tmpl, 'action_add_google_shopping_metafields'))

    def test_get_shopify_category_id_does_not_use_odoo_category(self):
        """Verify that Shopify Product Category does NOT fall back to Odoo product.category."""
        self.cat.shopify_category_id = 'gid://shopify/TaxonomyCategory/old-odoo-cat'
        self.tmpl.shopify_category_id = False

        # Must return empty string, NOT the Odoo category's value
        result = self.account._get_shopify_category_id(self.tmpl)
        self.assertEqual(result, '')

        # When set on template (auto-fetched), it returns that value
        self.tmpl.shopify_category_id = 'gid://shopify/TaxonomyCategory/sg-4-17-2-17'
        result = self.account._get_shopify_category_id(self.tmpl)
        self.assertEqual(result, 'gid://shopify/TaxonomyCategory/sg-4-17-2-17')

    def test_auto_fetch_data_on_pull_offer(self):
        """When a Shopify product is pulled (via _find_or_create_offer),
        Product ID, Handle, Category GID, and Collection GID are populated automatically."""
        product_data = {
            'shopify_inventory_item_id': 'inv-12345',
            'sku': 'SKU-TSHIRT-01',
            'name': 'Graphic T-Shirt',
            'ec_product_identifier': 'var-999',
            'ec_product_template_identifier': 'prod-888',
            'handle': 'graphic-t-shirt-remote',
            'shopify_category_id': 'gid://shopify/TaxonomyCategory/sg-4-17-2-17',
            'shopify_collection_id': 'gid://shopify/Collection/col-111',
            'variant_data': {},
        }

        with patch.object(self.account.__class__, '_sync_shopify_product_metafields'):
            offer = self.account._find_or_create_offer(product_data, auto_match=True)

        self.assertEqual(offer.ec_product_template_identifier, 'prod-888')
        self.assertEqual(offer.ec_product_identifier, 'var-999')
        self.assertEqual(self.tmpl.shopify_handle, 'graphic-t-shirt-remote')
        self.assertEqual(self.tmpl.shopify_category_id, 'gid://shopify/TaxonomyCategory/sg-4-17-2-17')
        self.assertEqual(self.tmpl.shopify_collection_id, 'gid://shopify/Collection/col-111')

    def test_restore_mapping_populates_handle_category_and_collection(self):
        """When restoring mapping from Shopify, handle, category, and collection are populated."""
        shopify_product = {
            'id': 'gid://shopify/Product/5555',
            'handle': 'restored-shirt-handle',
            'category': {
                'id': 'gid://shopify/TaxonomyCategory/sg-100',
                'name': 'Shirts',
            },
            'collections': {
                'nodes': [{
                    'id': 'gid://shopify/Collection/col-222',
                    'title': 'Summer 2026',
                    'handle': 'summer-2026',
                }],
            },
            'variants': {
                'nodes': [{
                    'id': 'gid://shopify/ProductVariant/6666',
                    'sku': 'SKU-TSHIRT-01',
                    'barcode': '123456789',
                    'selectedOptions': [],
                    'inventoryItem': {'id': 'gid://shopify/InventoryItem/7777'},
                }],
            },
        }

        self.account._restore_shopify_product_mapping(self.tmpl, self.offer, shopify_product)

        self.assertEqual(self.offer.ec_product_template_identifier, '5555')
        self.assertEqual(self.offer.ec_product_identifier, '6666')
        self.assertEqual(self.tmpl.shopify_handle, 'restored-shirt-handle')
        self.assertEqual(self.tmpl.shopify_category_id, 'gid://shopify/TaxonomyCategory/sg-100')
        self.assertEqual(self.tmpl.shopify_collection_id, 'gid://shopify/Collection/col-222')

    def test_product_update_mutation_omits_handle_to_prevent_handle_change(self):
        """_generate_product_update_mutation must NOT include 'handle:' in its input."""
        product_data = {
            'title': 'New T-Shirt Title',
            'handle': 'accidental-new-handle',
            'description_html': '<p>Description</p>',
            'vendor': 'Brand',
            'product_type': 'T-Shirt',
            'tags': ['tag1'],
            'status': 'ACTIVE',
            'category_id': 'gid://shopify/TaxonomyCategory/sg-4-17-2-17',
        }

        mutation = utils_upsync._generate_product_update_mutation('5555', product_data)

        # Handle must not be in mutation input
        self.assertNotIn('handle: "accidental-new-handle"', mutation)
        # Category must be in mutation input
        self.assertIn('category: "gid://shopify/TaxonomyCategory/sg-4-17-2-17"', mutation)
        # Selection returns handle and category
        self.assertIn('handle', mutation)
        self.assertIn('category {', mutation)

    def test_push_existing_product_refreshes_handle_and_category_automatically(self):
        """When an existing mapped product is pushed, remote handle and category are refreshed."""
        self.offer.ec_product_template_identifier = '5555'
        self.offer.ec_product_identifier = '6666'
        self.tmpl.shopify_handle = 'old-handle'
        self.tmpl.shopify_category_id = False

        remote_details = {
            'id': 'gid://shopify/Product/5555',
            'handle': 'actual-shopify-handle',
            'category': {
                'id': 'gid://shopify/TaxonomyCategory/sg-remote-99',
                'name': 'Tops',
            },
            'collections': {
                'nodes': [{'id': 'gid://shopify/Collection/col-333'}],
            },
        }

        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_shopify_product_details',
                   return_value=remote_details) as mock_details, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   return_value={'shopify_product_id': '5555', 'handle': 'actual-shopify-handle',
                                 'category_id': 'gid://shopify/TaxonomyCategory/sg-remote-99'}) as mock_push, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_variant_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(self.offer)

            mock_details.assert_called_once_with(self.account, '5555')
            self.assertEqual(self.tmpl.shopify_handle, 'actual-shopify-handle')
            self.assertEqual(self.tmpl.shopify_category_id, 'gid://shopify/TaxonomyCategory/sg-remote-99')
            self.assertEqual(self.tmpl.shopify_collection_id, 'gid://shopify/Collection/col-333')

    def test_webhook_update_refreshes_product_details(self):
        """A webhook update refreshes handle, category, and collection."""
        self.offer.ec_product_template_identifier = '5555'
        self.offer.ec_product_identifier = '6666'

        remote_details = {
            'id': 'gid://shopify/Product/5555',
            'handle': 'webhook-refreshed-handle',
            'category': {
                'id': 'gid://shopify/TaxonomyCategory/sg-webhook-123',
                'name': 'Apparel',
            },
            'collections': {
                'nodes': [{'id': 'gid://shopify/Collection/col-webhook'}],
            },
        }

        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_shopify_product_details',
                   return_value=remote_details), \
             patch.object(self.account.__class__, '_pull_product_metafields_to_odoo'):

            self.account._sync_shopify_product_metafields_by_product_id('5555', operation='update')

            self.assertEqual(self.tmpl.shopify_handle, 'webhook-refreshed-handle')
            self.assertEqual(self.tmpl.shopify_category_id, 'gid://shopify/TaxonomyCategory/sg-webhook-123')
            self.assertEqual(self.tmpl.shopify_collection_id, 'gid://shopify/Collection/col-webhook')
