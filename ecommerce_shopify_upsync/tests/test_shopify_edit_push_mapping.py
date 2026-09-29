# Part of Odoo. See LICENSE file for full copyright and licensing details.
from unittest.mock import MagicMock, call, patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestShopifyEditPushMapping(TransactionCase):

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
            'name': 'Test Shopify Account Edit Push',
            'company_id': self.company.id,
            'ecommerce_channel_id': channel.id,
            'fulfilled_by': 'ecommerce',
            'shopify_store': 'test-edit-store',
            'shopify_access_token': 'test-token',
            'state': 'connected',
        })

        self.cat = self.env['product.category'].create({'name': 'Electronics'})

        self.tmpl = self.env['product.template'].create({
            'name': 'Original Product',
            'categ_id': self.cat.id,
            'default_code': 'SKU-EDIT-01',
            'list_price': 100.0,
        })
        self.product = self.tmpl.product_variant_id
        self.offer = self.env['ecommerce.offer'].create({
            'name': self.product.display_name,
            'sku': self.product.default_code,
            'ecommerce_account_id': self.account.id,
            'matched_product_id': self.product.id,
        })

    def test_create_push_edit_push_edit_push_single_product(self):
        """Create → Push → Edit → Push → Edit → Push.

        Verifies that productCreate is only called on the first push,
        subsequent pushes call productUpdate reusing the same product and handle,
        and only ONE Shopify product exists throughout.
        """
        create_response = {
            'shopify_product_id': '8888',
            'handle': 'original-product',
            'variants': [{
                'shopify_variant_id': '9999',
                'shopify_inventory_item_id': '7777',
                'sku': 'SKU-EDIT-01',
            }],
        }
        update_response = {
            'shopify_product_id': '8888',
            'handle': 'original-product',
        }

        # 1. First Push: Initial Create
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   return_value=create_response) as mock_push, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.find_shopify_product_by_handle_or_sku',
                   return_value=None), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(self.offer)

            # push_product_to_shopify called without shopify_product_id (triggers productCreate)
            self.assertEqual(mock_push.call_count, 1)
            self.assertIsNone(mock_push.call_args[1].get('shopify_product_id'))

            # Check mapping is saved in Odoo
            self.assertEqual(self.offer.ec_product_template_identifier, '8888')
            self.assertEqual(self.offer.ec_product_identifier, '9999')
            self.assertEqual(self.tmpl.shopify_handle, 'original-product')

        # 2. Edit product in Odoo (Title, price)
        self.tmpl.name = "Original Product Edited Once"
        self.tmpl.list_price = 120.0

        # Push 2: Must call productUpdate on 8888, reusing 'original-product' handle
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   return_value=update_response) as mock_push, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_variant_to_shopify') as mock_push_variant, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(self.offer)

            # push_product_to_shopify called with shopify_product_id='8888' (triggers productUpdate)
            self.assertEqual(mock_push.call_count, 1)
            self.assertEqual(mock_push.call_args[1].get('shopify_product_id'), '8888')
            # Verify payload reused existing handle
            product_data = mock_push.call_args[0][1]
            self.assertEqual(product_data['handle'], 'original-product')
            self.assertEqual(product_data['title'], 'Original Product Edited Once')
            # Variant was updated
            self.assertEqual(mock_push_variant.call_count, 1)
            # Offer identifier remains '8888'
            self.assertEqual(self.offer.ec_product_template_identifier, '8888')
            self.assertEqual(self.tmpl.shopify_handle, 'original-product')

        # 3. Edit product in Odoo again
        self.tmpl.name = "Original Product Edited Twice"
        self.tmpl.list_price = 150.0

        # Push 3: Must call productUpdate on 8888 again, still reusing 'original-product' handle
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   return_value=update_response) as mock_push, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_variant_to_shopify') as mock_push_variant, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(self.offer)

            self.assertEqual(mock_push.call_count, 1)
            self.assertEqual(mock_push.call_args[1].get('shopify_product_id'), '8888')
            product_data = mock_push.call_args[0][1]
            self.assertEqual(product_data['handle'], 'original-product')
            self.assertEqual(product_data['title'], 'Original Product Edited Twice')
            self.assertEqual(self.offer.ec_product_template_identifier, '8888')
            self.assertEqual(self.tmpl.shopify_handle, 'original-product')

    def test_restore_mapping_when_odoo_mapping_missing_by_handle(self):
        """If Shopify product exists but Odoo mapping is missing, restore mapping by handle before creating anything."""
        self.tmpl.shopify_handle = 'existing-remote-handle'
        self.offer.ec_product_template_identifier = False
        self.offer.ec_product_identifier = False

        shopify_product_fixture = {
            'id': 'gid://shopify/Product/3333',
            'title': 'Original Product',
            'handle': 'existing-remote-handle',
            'variants': {
                'nodes': [{
                    'id': 'gid://shopify/ProductVariant/4444',
                    'sku': 'SKU-EDIT-01',
                    'inventoryItem': {'id': 'gid://shopify/InventoryItem/5555'},
                }]
            }
        }

        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.find_shopify_product_by_handle_or_sku',
                   return_value=shopify_product_fixture) as mock_find, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   return_value={'shopify_product_id': '3333', 'handle': 'existing-remote-handle'}) as mock_push, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_variant_to_shopify') as mock_push_var, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(self.offer)

            # Verified search was called
            mock_find.assert_called_once()
            # Mapping was restored on Odoo offer
            self.assertEqual(self.offer.ec_product_template_identifier, '3333')
            self.assertEqual(self.offer.ec_product_identifier, '4444')
            self.assertEqual(self.tmpl.shopify_handle, 'existing-remote-handle')

            # push_product_to_shopify called with shopify_product_id='3333' (productUpdate)
            # NOT productCreate!
            self.assertEqual(mock_push.call_count, 1)
            self.assertEqual(mock_push.call_args[1].get('shopify_product_id'), '3333')

    def test_restore_mapping_when_odoo_mapping_missing_by_sku(self):
        """If Shopify product exists but Odoo mapping is missing, restore mapping by SKU before creating anything."""
        self.tmpl.shopify_handle = False
        self.offer.ec_product_template_identifier = False
        self.offer.ec_product_identifier = False

        shopify_product_fixture = {
            'id': 'gid://shopify/Product/7777',
            'title': 'Original Product',
            'handle': 'matched-by-sku-handle',
            'variants': {
                'nodes': [{
                    'id': 'gid://shopify/ProductVariant/8888',
                    'sku': 'SKU-EDIT-01',
                    'inventoryItem': {'id': 'gid://shopify/InventoryItem/9999'},
                }]
            }
        }

        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.find_shopify_product_by_handle_or_sku',
                   return_value=shopify_product_fixture) as mock_find, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   return_value={'shopify_product_id': '7777', 'handle': 'matched-by-sku-handle'}) as mock_push, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_variant_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(self.offer)

            # Mapping restored
            self.assertEqual(self.offer.ec_product_template_identifier, '7777')
            self.assertEqual(self.offer.ec_product_identifier, '8888')
            self.assertEqual(self.tmpl.shopify_handle, 'matched-by-sku-handle')

            # Called productUpdate on 7777
            self.assertEqual(mock_push.call_count, 1)
            self.assertEqual(mock_push.call_args[1].get('shopify_product_id'), '7777')

    def test_existing_product_fetches_remote_handle_if_empty(self):
        """When product is already mapped but template.shopify_handle is empty, fetch and reuse existing handle."""
        self.offer.ec_product_template_identifier = '1234'
        self.offer.ec_product_identifier = '5678'
        self.tmpl.shopify_handle = False

        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_shopify_product_handle',
                   return_value='remote-desk-handle') as mock_get_handle, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   return_value={'shopify_product_id': '1234', 'handle': 'remote-desk-handle'}) as mock_push, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_variant_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._sync_product_collection_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account.ECommerceAccount._push_product_images_to_shopify'):

            self.account._push_product_template_to_shopify(self.offer)

            mock_get_handle.assert_called_once_with(self.account, '1234')
            self.assertEqual(self.tmpl.shopify_handle, 'remote-desk-handle')
            product_data = mock_push.call_args[0][1]
            self.assertEqual(product_data['handle'], 'remote-desk-handle')
