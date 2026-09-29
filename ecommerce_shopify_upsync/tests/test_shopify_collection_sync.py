# Part of Odoo. See LICENSE file for full copyright and licensing details.
from unittest.mock import MagicMock, call, patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestShopifyCollectionSync(TransactionCase):

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
            'name': 'Test Shopify Account Collections',
            'company_id': self.company.id,
            'ecommerce_channel_id': channel.id,
            'fulfilled_by': 'ecommerce',
            'shopify_store': 'test-collection-store',
            'shopify_access_token': 'test-token',
            'state': 'connected',
        })

        # Category structure:
        # Category A: ACCESSORIES / GIFT
        self.cat_accessories = self.env['product.category'].create({'name': 'ACCESSORIES'})
        self.cat_gift = self.env['product.category'].create({
            'name': 'GIFT',
            'parent_id': self.cat_accessories.id,
        })
        # Category B: APPAREL
        self.cat_apparel = self.env['product.category'].create({'name': 'APPAREL'})

        # Product 1 in Category GIFT (complete_name = 'ACCESSORIES / GIFT')
        self.tmpl_1 = self.env['product.template'].create({
            'name': 'Gift Item 1',
            'categ_id': self.cat_gift.id,
            'default_code': 'GIFT-01',
            'shopify_product_type': 'Custom Gift Type',
        })
        self.product_1 = self.tmpl_1.product_variant_id
        self.offer_1 = self.env['ecommerce.offer'].create({
            'name': self.product_1.display_name,
            'sku': self.product_1.default_code,
            'ecommerce_account_id': self.account.id,
            'matched_product_id': self.product_1.id,
        })

        # Product 2 in same Category GIFT
        self.tmpl_2 = self.env['product.template'].create({
            'name': 'Gift Item 2',
            'categ_id': self.cat_gift.id,
            'default_code': 'GIFT-02',
        })
        self.product_2 = self.tmpl_2.product_variant_id
        self.offer_2 = self.env['ecommerce.offer'].create({
            'name': self.product_2.display_name,
            'sku': self.product_2.default_code,
            'ecommerce_account_id': self.account.id,
            'matched_product_id': self.product_2.id,
        })

        # Product 3 in Category APPAREL
        self.tmpl_3 = self.env['product.template'].create({
            'name': 'Apparel Item 1',
            'categ_id': self.cat_apparel.id,
            'default_code': 'APP-01',
        })
        self.product_3 = self.tmpl_3.product_variant_id
        self.offer_3 = self.env['ecommerce.offer'].create({
            'name': self.product_3.display_name,
            'sku': self.product_3.default_code,
            'ecommerce_account_id': self.account.id,
            'matched_product_id': self.product_3.id,
        })

        self.mock_push_response = {
            'shopify_product_id': '1001',
            'variants': [{
                'shopify_variant_id': '2001',
                'shopify_inventory_item_id': '3001',
                'sku': 'GIFT-01',
            }],
        }

    def test_product_type_independent_from_category(self):
        """Odoo product category must not populate Shopify productType.

        Product Type is controlled independently by shopify_product_type.
        """
        # With explicit shopify_product_type set
        data_1 = self.account._prepare_shopify_product_data(self.tmpl_1, self.offer_1)
        self.assertEqual(data_1['product_type'], 'Custom Gift Type')
        self.assertNotEqual(data_1['product_type'], self.cat_gift.complete_name)
        self.assertNotEqual(data_1['product_type'], self.cat_gift.name)

        # Without shopify_product_type set, it should remain empty, NOT fallback to category
        data_2 = self.account._prepare_shopify_product_data(self.tmpl_2, self.offer_2)
        self.assertEqual(data_2['product_type'], '')
        self.assertNotEqual(data_2['product_type'], self.cat_gift.complete_name)

    def test_one_product_one_new_category_creates_collection(self):
        """Pushing a product with a new category creates a Shopify collection and assigns the product."""
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   return_value={'shopify_product_id': '1001', 'variants': []}), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.find_shopify_collection',
                   return_value=None) as mock_find, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.create_shopify_collection',
                   return_value={'id': 'gid://shopify/Collection/5001', 'title': 'ACCESSORIES / GIFT'}) as mock_create, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_product_shopify_collections',
                   return_value=[]) as mock_get_prod_cols, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.add_product_to_shopify_collection') as mock_add:

            self.account._push_product_template_to_shopify(self.offer_1)

            # Collection was created on Shopify with the category complete name
            mock_create.assert_called_once_with(
                self.account, title='ACCESSORIES / GIFT', handle='accessories-gift'
            )
            # Collection ID stored on Odoo category
            self.assertEqual(self.cat_gift.shopify_collection_id, 'gid://shopify/Collection/5001')
            # Collection ID recorded on template
            self.assertEqual(self.tmpl_1.shopify_collection_id, 'gid://shopify/Collection/5001')
            # Product was added to the collection
            mock_add.assert_called_once_with(self.account, 'gid://shopify/Collection/5001', '1001')

    def test_existing_shopify_collection_mapping(self):
        """When a matching Shopify collection already exists on Shopify, it is reused rather than created."""
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   return_value={'shopify_product_id': '1001', 'variants': []}), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.find_shopify_collection',
                   return_value={'id': 'gid://shopify/Collection/7777', 'title': 'ACCESSORIES / GIFT', 'handle': 'accessories-gift'}) as mock_find, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.create_shopify_collection') as mock_create, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_product_shopify_collections',
                   return_value=[]) as mock_get_prod_cols, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.add_product_to_shopify_collection') as mock_add:

            self.account._push_product_template_to_shopify(self.offer_1)

            # create_shopify_collection must NOT have been called
            mock_create.assert_not_called()
            # Stored existing ID
            self.assertEqual(self.cat_gift.shopify_collection_id, 'gid://shopify/Collection/7777')
            self.assertEqual(self.tmpl_1.shopify_collection_id, 'gid://shopify/Collection/7777')
            mock_add.assert_called_once_with(self.account, 'gid://shopify/Collection/7777', '1001')

    def test_multiple_products_same_category_reuses_collection_no_duplicates(self):
        """Multiple products in the same category must reuse the same Shopify collection without creating duplicates."""
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   side_effect=[
                       {'shopify_product_id': '1001', 'variants': []},
                       {'shopify_product_id': '1002', 'variants': []},
                   ]), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.find_shopify_collection',
                   return_value=None), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.create_shopify_collection',
                   return_value={'id': 'gid://shopify/Collection/5001', 'title': 'ACCESSORIES / GIFT'}) as mock_create, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_shopify_collection_by_id',
                   return_value={'id': 'gid://shopify/Collection/5001', 'title': 'ACCESSORIES / GIFT', 'product_ids': ['gid://shopify/Product/1001']}), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_product_shopify_collections',
                   return_value=[]), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.add_product_to_shopify_collection') as mock_add:

            # Push Product 1
            self.account._push_product_template_to_shopify(self.offer_1)
            self.assertEqual(mock_create.call_count, 1)
            self.assertEqual(self.cat_gift.shopify_collection_id, 'gid://shopify/Collection/5001')

            # Push Product 2 (same category)
            self.account._push_product_template_to_shopify(self.offer_2)
            # Collection must NOT be created again!
            self.assertEqual(mock_create.call_count, 1)

            # Both products assigned to the same collection
            self.assertEqual(self.tmpl_1.shopify_collection_id, 'gid://shopify/Collection/5001')
            self.assertEqual(self.tmpl_2.shopify_collection_id, 'gid://shopify/Collection/5001')
            self.assertEqual(mock_add.call_count, 2)
            mock_add.assert_has_calls([
                call(self.account, 'gid://shopify/Collection/5001', '1001'),
                call(self.account, 'gid://shopify/Collection/5001', '1002'),
            ])

    def test_multiple_categories(self):
        """Products in different categories are assigned to their respective Shopify collections."""
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   side_effect=[
                       {'shopify_product_id': '1001', 'variants': []},
                       {'shopify_product_id': '1003', 'variants': []},
                   ]), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.find_shopify_collection',
                   return_value=None), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.create_shopify_collection',
                   side_effect=[
                       {'id': 'gid://shopify/Collection/5001', 'title': 'ACCESSORIES / GIFT'},
                       {'id': 'gid://shopify/Collection/5002', 'title': 'APPAREL'},
                   ]) as mock_create, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_product_shopify_collections',
                   return_value=[]), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.add_product_to_shopify_collection') as mock_add:

            # Push Product 1 (GIFT)
            self.account._push_product_template_to_shopify(self.offer_1)
            # Push Product 3 (APPAREL)
            self.account._push_product_template_to_shopify(self.offer_3)

            self.assertEqual(mock_create.call_count, 2)
            self.assertEqual(self.cat_gift.shopify_collection_id, 'gid://shopify/Collection/5001')
            self.assertEqual(self.cat_apparel.shopify_collection_id, 'gid://shopify/Collection/5002')

            self.assertEqual(self.tmpl_1.shopify_collection_id, 'gid://shopify/Collection/5001')
            self.assertEqual(self.tmpl_3.shopify_collection_id, 'gid://shopify/Collection/5002')

            mock_add.assert_has_calls([
                call(self.account, 'gid://shopify/Collection/5001', '1001'),
                call(self.account, 'gid://shopify/Collection/5002', '1003'),
            ])

    def test_repushing_same_product_no_duplicate_assignment(self):
        """Re-pushing a product that is already in the collection must not duplicate assignments or collections."""
        self.cat_gift.shopify_collection_id = 'gid://shopify/Collection/5001'
        self.tmpl_1.shopify_collection_id = 'gid://shopify/Collection/5001'
        self.offer_1.ec_product_template_identifier = '1001'
        self.offer_1.ec_product_identifier = '2001'

        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   return_value={'shopify_product_id': '1001'}), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_variant_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_shopify_collection_by_id',
                   return_value={'id': 'gid://shopify/Collection/5001', 'title': 'ACCESSORIES / GIFT', 'product_ids': ['gid://shopify/Product/1001']}), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.create_shopify_collection') as mock_create, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_product_shopify_collections',
                   return_value=[{'id': 'gid://shopify/Collection/5001', 'title': 'ACCESSORIES / GIFT'}]) as mock_get_prod_cols, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.add_product_to_shopify_collection') as mock_add:

            self.account._push_product_template_to_shopify(self.offer_1)

            # Neither collection creation nor product addition should be invoked
            mock_create.assert_not_called()
            mock_add.assert_not_called()
            self.assertEqual(self.tmpl_1.shopify_collection_id, 'gid://shopify/Collection/5001')

    def test_changing_product_category_updates_collection_membership(self):
        """When a product's Odoo category changes, its Shopify collection membership is updated accordingly."""
        # Initial state: Product 1 was in Category GIFT (Collection 5001)
        self.cat_gift.shopify_collection_id = 'gid://shopify/Collection/5001'
        self.cat_apparel.shopify_collection_id = 'gid://shopify/Collection/5002'
        self.tmpl_1.shopify_collection_id = 'gid://shopify/Collection/5001'
        self.offer_1.ec_product_template_identifier = '1001'
        self.offer_1.ec_product_identifier = '2001'

        # Now change Category from GIFT to APPAREL in Odoo
        self.tmpl_1.categ_id = self.cat_apparel

        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_product_to_shopify',
                   return_value={'shopify_product_id': '1001'}), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.push_variant_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.publish_product_to_shopify'), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_shopify_collection_by_id',
                   return_value={'id': 'gid://shopify/Collection/5002', 'title': 'APPAREL'}), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.get_product_shopify_collections',
                   return_value=[{'id': 'gid://shopify/Collection/5001', 'title': 'ACCESSORIES / GIFT'}]), \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.remove_product_from_shopify_collection') as mock_remove, \
             patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.add_product_to_shopify_collection') as mock_add:

            self.account._push_product_template_to_shopify(self.offer_1)

            # Removed from old collection 5001
            mock_remove.assert_called_once_with(self.account, 'gid://shopify/Collection/5001', '1001')
            # Added to new collection 5002
            mock_add.assert_called_once_with(self.account, 'gid://shopify/Collection/5002', '1001')
            # Template updated to new collection ID
            self.assertEqual(self.tmpl_1.shopify_collection_id, 'gid://shopify/Collection/5002')

    def test_graphql_collection_update_with_selections_to_add(self):
        """Test utils_upsync.add_product_to_shopify_collection GraphQL logic."""
        from odoo.addons.ecommerce_shopify_upsync import utils_upsync

        # 1. Successful collectionUpdate with inclusion.selectionsToAdd
        mock_response_success = {
            'data': {
                'collectionUpdate': {
                    'collection': {'id': 'gid://shopify/Collection/5001'},
                    'userErrors': [],
                }
            }
        }
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api',
                   return_value=mock_response_success) as mock_gql:
            res = utils_upsync.add_product_to_shopify_collection(
                self.account, 'gid://shopify/Collection/5001', '1001'
            )
            self.assertTrue(res)
            self.assertEqual(mock_gql.call_count, 1)
            # Verify collectionUpdate and selectionsToAdd in the query
            call_query = mock_gql.call_args[0][2]
            self.assertIn('collectionUpdate', call_query)
            self.assertIn('selectionsToAdd', call_query)

        # 2. collectionUpdate fails -> fallback to collectionAddProducts
        mock_response_fallback = {
            'data': {
                'collectionAddProducts': {
                    'collection': {'id': 'gid://shopify/Collection/5001'},
                    'userErrors': [],
                }
            }
        }
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api',
                   side_effect=[
                       {'errors': [{'message': "Field 'sourcesToUpdate' doesn't exist"}]},
                       mock_response_fallback,
                   ]) as mock_gql:
            res = utils_upsync.add_product_to_shopify_collection(
                self.account, 'gid://shopify/Collection/5001', '1001'
            )
            self.assertTrue(res)
            self.assertEqual(mock_gql.call_count, 2)
            first_query = mock_gql.call_args_list[0][0][2]
            second_query = mock_gql.call_args_list[1][0][2]
            self.assertIn('collectionUpdate', first_query)
            self.assertIn('collectionAddProducts', second_query)

    def test_graphql_collection_update_with_selections_to_remove(self):
        """Test utils_upsync.remove_product_from_shopify_collection GraphQL logic."""
        from odoo.addons.ecommerce_shopify_upsync import utils_upsync

        # 1. Successful collectionUpdate with inclusion.selectionsToRemove
        mock_response_success = {
            'data': {
                'collectionUpdate': {
                    'collection': {'id': 'gid://shopify/Collection/5001'},
                    'userErrors': [],
                }
            }
        }
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api',
                   return_value=mock_response_success) as mock_gql:
            res = utils_upsync.remove_product_from_shopify_collection(
                self.account, 'gid://shopify/Collection/5001', '1001'
            )
            self.assertTrue(res)
            self.assertEqual(mock_gql.call_count, 1)
            call_query = mock_gql.call_args[0][2]
            self.assertIn('collectionUpdate', call_query)
            self.assertIn('selectionsToRemove', call_query)

        # 2. collectionUpdate fails -> fallback to collectionRemoveProducts
        mock_response_fallback = {
            'data': {
                'collectionRemoveProducts': {
                    'job': {'id': 'gid://shopify/Job/999'},
                    'userErrors': [],
                }
            }
        }
        with patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api',
                   side_effect=[
                       {'errors': [{'message': "Field 'sourcesToUpdate' doesn't exist"}]},
                       mock_response_fallback,
                   ]) as mock_gql:
            res = utils_upsync.remove_product_from_shopify_collection(
                self.account, 'gid://shopify/Collection/5001', '1001'
            )
            self.assertTrue(res)
            self.assertEqual(mock_gql.call_count, 2)
            first_query = mock_gql.call_args_list[0][0][2]
            second_query = mock_gql.call_args_list[1][0][2]
            self.assertIn('collectionUpdate', first_query)
            self.assertIn('collectionRemoveProducts', second_query)
