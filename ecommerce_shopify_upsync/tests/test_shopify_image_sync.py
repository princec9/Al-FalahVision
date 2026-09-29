# Part of Odoo. See LICENSE file for full copyright and licensing details.
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.ecommerce_shopify import const
from odoo.addons.ecommerce_shopify_upsync import utils_upsync


IMG1_B64 = b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII='
IMG1_BYTES = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\xdac\xfc\xff\x1f\x00\x03\x03\x02\x00\xef\xbf\xa7\xdb\x00\x00\x00\x00IEND\xaeB`\x82'
IMG2_B64 = b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
IMG2_BYTES = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\rIDATx\xdacd\xf8\xcfP\x0f\x00\x03\x86\x01\x80Z4}k\x00\x00\x00\x00IEND\xaeB`\x82'


@tagged('post_install', '-at_install')
class TestShopifyProductImageSync(TransactionCase):

    def setUp(self):
        super().setUp()
        channel = self.env['ecommerce.channel'].search([('code', '=', 'shopify')], limit=1)
        if not channel:
            channel = self.env['ecommerce.channel'].create({
                'name': 'Shopify Test Channel',
                'code': 'shopify',
                'sequence': 40,
            })
        self.account = self.env['ecommerce.account'].create({
            'name': 'Test Shopify Account',
            'company_id': self.env.company.id,
            'ecommerce_channel_id': channel.id,
            'fulfilled_by': 'ecommerce',
            'shopify_store': 'test-store',
            'shopify_access_token': 'test-token-abc123',
        })
        self.template = self.env['product.template'].create({'name': 'Image Sync Product'})
        self.image = self.env['product.shopify.image'].create({
            'product_tmpl_id': self.template.id,
            'name': 'Front',
            'image': IMG1_B64,
        })

    def _media_response(self, media_id='111', url='https://cdn.example/img1.png'):
        return {
            'data': {
                'product': {
                    'media': {
                        'nodes': [{
                            'id': const.GLOBAL_MEDIA_IMAGE_ID + media_id,
                            'mediaContentType': 'IMAGE',
                            'alt': 'Front',
                            'image': {'url': url},
                        }],
                    },
                },
            },
        }

    def _empty_media_response(self):
        return {'data': {'product': {'media': {'nodes': []}}}}

    def _create_media_response(self, media_id='222'):
        return {
            'data': {
                'productCreateMedia': {
                    'media': [{'id': const.GLOBAL_MEDIA_IMAGE_ID + media_id}],
                    'userErrors': [],
                },
            },
        }

    def _is_create_media_call(self, call):
        return 'productCreateMedia' in call.args[2]

    def _is_delete_media_call(self, call):
        return 'productDeleteMedia' in call.args[2]

    def _images(self):
        return [{
            'bytes': IMG1_BYTES,
            'filename': 'front.png',
            'mime_type': 'image/png',
            'offer_image': self.image,
            'alt': 'Front',
            'variant_offer': None,
        }]

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._fetch_url_bytes')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_existing_shopify_image_found_no_create_call(self, mock_api, mock_fetch):
        mock_api.return_value = self._media_response('111')
        mock_fetch.return_value = IMG1_BYTES

        images = utils_upsync.push_product_images_to_shopify(self.account, '999999', self._images())

        self.assertEqual(images[0]['shopify_media_id'], '111')
        self.assertEqual(self.image.shopify_media_id, '111')
        self.assertFalse(any(self._is_create_media_call(c) for c in mock_api.call_args_list))
        self.assertFalse(any(self._is_delete_media_call(c) for c in mock_api.call_args_list))

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._staged_upload_post')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._staged_upload_create')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_new_odoo_image_creates_exactly_once(self, mock_api, mock_stage_create, mock_stage_post):
        mock_api.side_effect = [self._empty_media_response(), self._create_media_response('222')]
        mock_stage_create.return_value = {'url': 'https://upload.example', 'resourceUrl': 'staged://img'}
        mock_stage_post.return_value = 'staged://img'

        images = utils_upsync.push_product_images_to_shopify(self.account, '999999', self._images())

        self.assertEqual(images[0]['shopify_media_id'], '222')
        self.assertEqual(self.image.shopify_media_id, '222')
        self.assertEqual(sum(1 for c in mock_api.call_args_list if self._is_create_media_call(c)), 1)

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._staged_upload_post')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._staged_upload_create')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_push_same_product_twice_second_push_creates_zero_media(self, mock_api, mock_stage_create, mock_stage_post):
        mock_api.side_effect = [
            self._empty_media_response(),
            self._create_media_response('222'),
            self._media_response('222'),
        ]
        mock_stage_create.return_value = {'url': 'https://upload.example', 'resourceUrl': 'staged://img'}
        mock_stage_post.return_value = 'staged://img'

        utils_upsync.push_product_images_to_shopify(self.account, '999999', self._images())
        utils_upsync.push_product_images_to_shopify(self.account, '999999', self._images())

        self.assertEqual(self.image.shopify_media_id, '222')
        self.assertEqual(sum(1 for c in mock_api.call_args_list if self._is_create_media_call(c)), 1)

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._staged_upload_post')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._staged_upload_create')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_push_same_product_three_times_third_push_creates_zero_media(self, mock_api, mock_stage_create, mock_stage_post):
        mock_api.side_effect = [
            self._empty_media_response(),
            self._create_media_response('222'),
            self._media_response('222'),
            self._media_response('222'),
        ]
        mock_stage_create.return_value = {'url': 'https://upload.example', 'resourceUrl': 'staged://img'}
        mock_stage_post.return_value = 'staged://img'

        utils_upsync.push_product_images_to_shopify(self.account, '999999', self._images())
        utils_upsync.push_product_images_to_shopify(self.account, '999999', self._images())
        utils_upsync.push_product_images_to_shopify(self.account, '999999', self._images())

        self.assertEqual(sum(1 for c in mock_api.call_args_list if self._is_create_media_call(c)), 1)

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_odoo_shopify_media_id_exists_reused(self, mock_api):
        self.image.shopify_media_id = '111'
        mock_api.return_value = self._media_response('111')

        images = utils_upsync.push_product_images_to_shopify(self.account, '999999', self._images())

        self.assertEqual(images[0]['shopify_media_id'], '111')
        self.assertFalse(any(self._is_create_media_call(c) for c in mock_api.call_args_list))

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._fetch_url_bytes')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_missing_mapping_existing_shopify_media_is_discovered_and_saved(self, mock_api, mock_fetch):
        mock_api.return_value = self._media_response('111')
        mock_fetch.return_value = IMG1_BYTES

        utils_upsync.push_product_images_to_shopify(self.account, '999999', self._images())

        self.assertEqual(self.image.shopify_media_id, '111')
        self.assertFalse(any(self._is_create_media_call(c) for c in mock_api.call_args_list))

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._fetch_url_bytes')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_image_url_changed_does_not_create_duplicate_when_content_matches(self, mock_api, mock_fetch):
        mock_api.return_value = self._media_response('111', url='https://cdn.example/new-signed-url.png')
        mock_fetch.return_value = IMG1_BYTES

        utils_upsync.push_product_images_to_shopify(self.account, '999999', self._images())

        self.assertEqual(self.image.shopify_media_id, '111')
        self.assertFalse(any(self._is_create_media_call(c) for c in mock_api.call_args_list))

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._fetch_url_bytes')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_image_order_changed_does_not_create_duplicate_media(self, mock_api, mock_fetch):
        image2 = self.env['product.shopify.image'].create({
            'product_tmpl_id': self.template.id,
            'name': 'Back',
            'image': IMG2_B64,
        })
        mock_api.return_value = {
            'data': {
                'product': {
                    'media': {
                        'nodes': [
                            {'id': const.GLOBAL_MEDIA_IMAGE_ID + '222', 'mediaContentType': 'IMAGE', 'alt': 'Back', 'image': {'url': 'https://cdn.example/img2.png'}},
                            {'id': const.GLOBAL_MEDIA_IMAGE_ID + '111', 'mediaContentType': 'IMAGE', 'alt': 'Front', 'image': {'url': 'https://cdn.example/img1.png'}},
                        ],
                    },
                },
            },
        }
        mock_fetch.side_effect = lambda url: IMG2_BYTES if 'img2' in url else IMG1_BYTES
        images = self._images() + [{
            'bytes': IMG2_BYTES,
            'filename': 'back.png',
            'mime_type': 'image/png',
            'offer_image': image2,
            'alt': 'Back',
            'variant_offer': None,
        }]

        utils_upsync.push_product_images_to_shopify(self.account, '999999', list(reversed(images)))

        self.assertEqual(self.image.shopify_media_id, '111')
        self.assertEqual(image2.shopify_media_id, '222')
        self.assertFalse(any(self._is_create_media_call(c) for c in mock_api.call_args_list))

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.link_media_to_variant')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync.reorder_product_media')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._fetch_url_bytes')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_bulk_push_products_uses_idempotent_media_sync(self, mock_api, mock_fetch, mock_reorder, mock_link):
        variant = self.env['product.product'].create({
            'name': 'Image Sync Product Variant',
            'product_tmpl_id': self.template.id,
        })
        offer = self.env['ecommerce.offer'].create({
            'name': 'Offer',
            'sku': 'SKU-IMG',
            'ecommerce_account_id': self.account.id,
            'matched_product_id': variant.id,
            'ec_product_template_identifier': '999999',
            'ec_product_identifier': '888888',
        })
        mock_api.return_value = self._media_response('111')
        mock_fetch.return_value = IMG1_BYTES

        self.account._push_product_images_to_shopify('999999', self.template, offer)

        self.assertEqual(self.image.shopify_media_id, '111')
        self.assertFalse(any(self._is_create_media_call(c) for c in mock_api.call_args_list))
        mock_reorder.assert_called_once()

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._staged_upload_post')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._staged_upload_create')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._fetch_url_bytes')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_add_new_image_creates_only_the_new_one(self, mock_api, mock_fetch, mock_stage_create, mock_stage_post):
        """An existing image (already mapped) stays untouched; only the newly
        added second image results in a create call — no re-upload of image A,
        no duplicate of image B."""
        image2 = self.env['product.shopify.image'].create({
            'product_tmpl_id': self.template.id,
            'name': 'Back',
            'image': IMG2_B64,
        })
        self.image.shopify_media_id = '111'
        mock_api.side_effect = [
            self._media_response('111'),
            self._create_media_response('222'),
        ]
        mock_stage_create.return_value = {'url': 'https://upload.example', 'resourceUrl': 'staged://img'}
        mock_stage_post.return_value = 'staged://img'

        images = self._images() + [{
            'bytes': IMG2_BYTES,
            'filename': 'back.png',
            'mime_type': 'image/png',
            'offer_image': image2,
            'alt': 'Back',
            'variant_offer': None,
        }]

        utils_upsync.push_product_images_to_shopify(self.account, '999999', images)

        self.assertEqual(self.image.shopify_media_id, '111')
        self.assertEqual(image2.shopify_media_id, '222')
        self.assertEqual(sum(1 for c in mock_api.call_args_list if self._is_create_media_call(c)), 1)
        self.assertFalse(any(self._is_delete_media_call(c) for c in mock_api.call_args_list))

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._staged_upload_post')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._staged_upload_create')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._fetch_url_bytes')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_deleted_shopify_media_recreates_only_the_missing_one(
        self, mock_api, mock_fetch, mock_stage_create, mock_stage_post,
    ):
        """A stored Shopify Media ID that Shopify no longer has must not be
        blindly trusted: it's detected (absent from the current media list),
        a replacement is created for that image only, and a different,
        still-valid image (matched by content hash) is neither duplicated nor
        deleted as stale."""
        image2 = self.env['product.shopify.image'].create({
            'product_tmpl_id': self.template.id,
            'name': 'Back',
            'image': IMG2_B64,
        })
        self.image.shopify_media_id = 'DELETED_999'
        mock_api.side_effect = [
            self._media_response('222', url='https://cdn.example/img2.png'),
            self._create_media_response('333'),
        ]
        mock_fetch.return_value = IMG2_BYTES
        mock_stage_create.return_value = {'url': 'https://upload.example', 'resourceUrl': 'staged://img'}
        mock_stage_post.return_value = 'staged://img'

        images = self._images() + [{
            'bytes': IMG2_BYTES,
            'filename': 'back.png',
            'mime_type': 'image/png',
            'offer_image': image2,
            'alt': 'Back',
            'variant_offer': None,
        }]

        utils_upsync.push_product_images_to_shopify(self.account, '999999', images)

        self.assertEqual(self.image.shopify_media_id, '333')
        self.assertEqual(image2.shopify_media_id, '222')
        self.assertEqual(sum(1 for c in mock_api.call_args_list if self._is_create_media_call(c)), 1)
        self.assertFalse(any(self._is_delete_media_call(c) for c in mock_api.call_args_list))

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._staged_upload_post')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._staged_upload_create')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._fetch_url_bytes')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_main_image_tracked_and_not_recreated_on_second_push(
        self, mock_api, mock_fetch, mock_stage_create, mock_stage_post,
    ):
        """The main product image (image_1920) must get its own persistent
        shopify_media_id on product.template, not rely purely on content-hash
        matching on every push. Without that mapping, a one-time hash miss
        against visually-identical-but-byte-different existing Shopify media
        (e.g. the same picture originally imported via a different Shopify CDN
        URL than the gallery row it visually duplicates) creates a permanent
        extra copy that then matches itself forever after — this is the
        Odoo-side root cause behind the reported "every push adds another
        image" behavior."""
        main_image_entry = {
            'bytes': IMG1_BYTES,
            'filename': 'main.png',
            'mime_type': 'image/png',
            'offer_image': self.template,
            'alt': 'Main',
            'variant_offer': None,
        }

        # First push: Shopify's only existing media doesn't byte-match the
        # main image (simulating the featuredImage-vs-media-url mismatch), so
        # no match is found and exactly one new media item is created.
        mock_api.side_effect = [
            self._media_response('111', url='https://cdn.example/other.png'),
            self._create_media_response('222'),
        ]
        mock_fetch.return_value = IMG2_BYTES  # does not hash-match IMG1_BYTES
        mock_stage_create.return_value = {'url': 'https://upload.example', 'resourceUrl': 'staged://img'}
        mock_stage_post.return_value = 'staged://img'

        utils_upsync.push_product_images_to_shopify(self.account, '999999', [dict(main_image_entry)])

        self.assertEqual(self.template.shopify_media_id, '222')
        self.assertEqual(sum(1 for c in mock_api.call_args_list if self._is_create_media_call(c)), 1)

        # Second push of the same unchanged main image: the stored mapping
        # (priority 1) must be used directly — zero create calls, regardless
        # of what content-hash comparison would have found.
        mock_api.reset_mock()
        mock_api.side_effect = [self._media_response('222', url='https://cdn.example/main.png')]

        utils_upsync.push_product_images_to_shopify(self.account, '999999', [dict(main_image_entry)])

        self.assertEqual(self.template.shopify_media_id, '222')

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._fetch_url_bytes')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_two_local_images_same_content_claim_once_not_twice(self, mock_api, mock_fetch):
        """Regression: two DIFFERENT Odoo image entries in the same push call
        (e.g. the main image and a gallery row) that happen to hold
        byte-identical content must both resolve to the SAME Shopify media —
        not have the second one wrongly conclude "no match" (because the
        shared existing media was already claimed by the first) and create a
        duplicate. A direct caller of push_product_images_to_shopify can
        submit such a pair even though the orchestration layer
        (_push_product_images_to_shopify) separately pre-dedupes by hash
        before ever reaching this function — this is defense in depth for it."""
        image2 = self.env['product.shopify.image'].create({
            'product_tmpl_id': self.template.id,
            'name': 'Duplicate of Front',
            'image': IMG1_B64,  # same bytes as self.image
        })
        mock_api.return_value = self._media_response('111')
        mock_fetch.return_value = IMG1_BYTES

        images = self._images() + [{
            'bytes': IMG1_BYTES,
            'filename': 'front-dup.png',
            'mime_type': 'image/png',
            'offer_image': image2,
            'alt': 'Duplicate of Front',
            'variant_offer': None,
        }]

        utils_upsync.push_product_images_to_shopify(self.account, '999999', images)

        self.assertEqual(self.image.shopify_media_id, '111')
        self.assertEqual(image2.shopify_media_id, '111')
        self.assertFalse(any(self._is_create_media_call(c) for c in mock_api.call_args_list))

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._fetch_url_bytes')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_two_local_images_same_stored_id_different_bytes_claim_once(self, mock_api, mock_fetch):
        """Regression: the main image and a gallery row can legitimately both
        carry the SAME stored shopify_media_id (Shopify's `media` connection
        includes the featured image as one of its own nodes, so fetch records
        the identical id on both), while their LOCAL bytes differ slightly —
        e.g. one was decoded from `image_1920` and the other downloaded fresh
        from Shopify's CDN and re-encoded by Odoo's Image field, producing a
        different hash for visually the same picture. The second entry to be
        resolved must still reuse the shared id — it must not be rejected
        just because the first entry already claimed it, which previously
        sent it through hash-matching (no candidate left) straight to a
        needless re-upload."""
        image2 = self.env['product.shopify.image'].create({
            'product_tmpl_id': self.template.id,
            'name': 'Front (re-encoded)',
            'image': IMG2_B64,
        })
        self.image.shopify_media_id = '111'
        image2.shopify_media_id = '111'  # same real Shopify media as self.image
        mock_api.return_value = self._media_response('111')

        images = self._images() + [{
            'bytes': IMG2_BYTES,  # different bytes than self.image's IMG1_BYTES
            'filename': 'front-reencoded.png',
            'mime_type': 'image/png',
            'offer_image': image2,
            'alt': 'Front (re-encoded)',
            'variant_offer': None,
        }]

        utils_upsync.push_product_images_to_shopify(self.account, '999999', images)

        self.assertEqual(self.image.shopify_media_id, '111')
        self.assertEqual(image2.shopify_media_id, '111')
        self.assertFalse(any(self._is_create_media_call(c) for c in mock_api.call_args_list))
        self.assertFalse(any(self._is_delete_media_call(c) for c in mock_api.call_args_list))

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._fetch_url_bytes')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_unclaimed_duplicate_on_shopify_is_deleted_even_if_content_matches(self, mock_api, mock_fetch):
        """Regression: Shopify already has TWO media items depicting the same
        picture (e.g. left over from a bug fixed in an earlier push) while
        Odoo has only ONE image for it. The one Odoo image claims one of
        them; the other is unclaimed and must be deleted as stale — even
        though its *content* still matches something in Odoo (the previous
        behavior kept it forever specifically because of that content match,
        which is exactly what let this kind of leftover duplicate survive
        every subsequent push)."""
        self.image.shopify_media_id = '111'
        mock_api.return_value = {
            'data': {
                'product': {
                    'media': {
                        'nodes': [
                            {'id': const.GLOBAL_MEDIA_IMAGE_ID + '111', 'mediaContentType': 'IMAGE',
                             'alt': 'Front', 'image': {'url': 'https://cdn.example/img1.png'}},
                            {'id': const.GLOBAL_MEDIA_IMAGE_ID + '999', 'mediaContentType': 'IMAGE',
                             'alt': 'Front (dup)', 'image': {'url': 'https://cdn.example/img1-dup.png'}},
                        ],
                    },
                },
            },
        }
        mock_fetch.return_value = IMG1_BYTES  # both existing Shopify items hash-match the Odoo image

        utils_upsync.push_product_images_to_shopify(self.account, '999999', self._images())

        self.assertEqual(self.image.shopify_media_id, '111')
        self.assertFalse(any(self._is_create_media_call(c) for c in mock_api.call_args_list))
        delete_calls = [c for c in mock_api.call_args_list if self._is_delete_media_call(c)]
        self.assertEqual(len(delete_calls), 1)
        self.assertIn(const.GLOBAL_MEDIA_IMAGE_ID + '999', delete_calls[0].args[2])
        self.assertNotIn(const.GLOBAL_MEDIA_IMAGE_ID + '111"', delete_calls[0].args[2])


class TestSyncShopifyMainImageMediaId(TransactionCase):
    """FETCH side: the main/featured image's Shopify Media ID must be
    recorded on product.template.shopify_media_id, the same way a gallery
    row already carries its own (see TestShopifyProductImageSync)."""

    def setUp(self):
        super().setUp()
        channel = self.env['ecommerce.channel'].search([('code', '=', 'shopify')], limit=1)
        if not channel:
            channel = self.env['ecommerce.channel'].create({
                'name': 'Shopify Test Channel', 'code': 'shopify', 'sequence': 40,
            })
        self.account = self.env['ecommerce.account'].create({
            'name': 'Test Shopify Account',
            'company_id': self.env.company.id,
            'ecommerce_channel_id': channel.id,
            'fulfilled_by': 'ecommerce',
            'shopify_store': 'test-store',
            'shopify_access_token': 'test-token-abc123',
        })
        self.template = self.env['product.template'].create({'name': 'Main Image Product'})
        self.variant = self.template.product_variant_id
        self.offer = self.env['ecommerce.offer'].create({
            'name': 'Offer',
            'sku': 'SKU-MAIN',
            'ecommerce_account_id': self.account.id,
            'matched_product_id': self.variant.id,
        })

    def test_main_image_media_id_recorded_on_fetch(self):
        self.account._sync_shopify_main_image_media_id(self.offer, {
            'variant_data': {'image_shopify_media_id': '555'},
        })
        self.assertEqual(self.template.shopify_media_id, '555')

    def test_missing_media_id_leaves_field_untouched(self):
        self.template.shopify_media_id = '111'
        self.account._sync_shopify_main_image_media_id(self.offer, {
            'variant_data': {'image_shopify_media_id': ''},
        })
        self.assertEqual(self.template.shopify_media_id, '111')


class TestImageSyncRoundTrip(TransactionCase):
    """End-to-end: repeated Fetch -> Push -> Fetch -> Push on an unchanged
    product must never accumulate duplicate images, on either Odoo's or
    Shopify's side — this exercises the real orchestration methods
    (_find_or_create_offer for fetch, _push_product_images_to_shopify for
    push), not the lower-level utils in isolation."""

    def setUp(self):
        super().setUp()
        channel = self.env['ecommerce.channel'].search([('code', '=', 'shopify')], limit=1)
        if not channel:
            channel = self.env['ecommerce.channel'].create({
                'name': 'Shopify Test Channel', 'code': 'shopify', 'sequence': 40,
            })
        self.account = self.env['ecommerce.account'].create({
            'name': 'Test Shopify Account',
            'company_id': self.env.company.id,
            'ecommerce_channel_id': channel.id,
            'fulfilled_by': 'ecommerce',
            'shopify_store': 'test-store',
            'shopify_access_token': 'test-token-abc123',
        })
        self.template = self.env['product.template'].create({'name': 'Round Trip Product'})
        self.variant = self.template.product_variant_id
        self.offer = self.env['ecommerce.offer'].create({
            'name': 'Round Trip Product',
            'sku': 'SKU-RT',
            'ecommerce_account_id': self.account.id,
            'matched_product_id': self.variant.id,
            'ec_product_template_identifier': '999999',
            'ec_product_identifier': '888888',
        })

    def _product_data(self):
        return {
            'ec_product_template_identifier': '999999',
            'ec_product_identifier': '888888',
            'variant_data': {
                'image_url': 'https://cdn.example/featured.png',
                'image_shopify_media_id': '111',
                'images': [{
                    'shopify_media_id': '111',
                    'url': 'https://cdn.example/featured.png',
                    'alt': 'Front',
                }],
            },
        }

    def _media_response(self):
        return {
            'data': {
                'product': {
                    'media': {
                        'nodes': [{
                            'id': const.GLOBAL_MEDIA_IMAGE_ID + '111',
                            'mediaContentType': 'IMAGE',
                            'alt': 'Front',
                            'image': {'url': 'https://cdn.example/featured.png'},
                        }],
                    },
                },
            },
        }

    def _is_create_media_call(self, call):
        return 'productCreateMedia' in call.args[2]

    def _is_delete_media_call(self, call):
        return 'productDeleteMedia' in call.args[2]

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._fetch_url_bytes')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    @patch('odoo.addons.ecommerce_shopify.models.ecommerce_account.fetch_image_base64_from_url')
    def test_repeated_fetch_and_push_never_duplicates(self, mock_fetch_image, mock_push_api, mock_push_url_bytes):
        mock_fetch_image.return_value = IMG1_B64

        # FETCH #1 — gallery row created (matched by Shopify Media ID) and the
        # main image's media id recorded on the template.
        self.account._find_or_create_offer(self._product_data())
        self.assertEqual(self.template.shopify_media_id, '111')
        gallery = self.env['product.shopify.image'].search([('product_tmpl_id', '=', self.template.id)])
        self.assertEqual(len(gallery), 1)
        self.assertEqual(gallery.shopify_media_id, '111')

        # PUSH #1 — the id recorded by fetch is matched directly (priority 1):
        # zero uploads, zero deletes.
        self.template.image_1920 = IMG1_B64
        mock_push_api.return_value = self._media_response()
        mock_push_url_bytes.return_value = IMG1_BYTES
        self.account._push_product_images_to_shopify('999999', self.template, self.offer)
        self.assertFalse(any(self._is_create_media_call(c) for c in mock_push_api.call_args_list))
        self.assertFalse(any(self._is_delete_media_call(c) for c in mock_push_api.call_args_list))

        # FETCH #2 — re-syncing the unchanged product updates the existing
        # gallery row in place; it must not create a second one.
        mock_push_api.reset_mock()
        self.account._find_or_create_offer(self._product_data())
        gallery = self.env['product.shopify.image'].search([('product_tmpl_id', '=', self.template.id)])
        self.assertEqual(len(gallery), 1)
        self.assertEqual(self.template.shopify_media_id, '111')

        # PUSH #2 — still fully idempotent.
        self.account._push_product_images_to_shopify('999999', self.template, self.offer)
        self.assertFalse(any(self._is_create_media_call(c) for c in mock_push_api.call_args_list))
        self.assertFalse(any(self._is_delete_media_call(c) for c in mock_push_api.call_args_list))


@tagged('post_install', '-at_install')
class TestShopifyImageDeduplicationAndHashMatching(TransactionCase):

    def setUp(self):
        super().setUp()
        channel = self.env['ecommerce.channel'].search([('code', '=', 'shopify')], limit=1)
        if not channel:
            channel = self.env['ecommerce.channel'].create({
                'name': 'Shopify Test Channel',
                'code': 'shopify',
                'sequence': 40,
            })
        self.account = self.env['ecommerce.account'].create({
            'name': 'Test Account Dedup',
            'company_id': self.env.company.id,
            'ecommerce_channel_id': channel.id,
            'fulfilled_by': 'ecommerce',
            'shopify_store': 'dedup-store',
            'shopify_access_token': 'token-dedup-xyz',
        })
        self.template = self.env['product.template'].create({'name': 'Dedup Test Product'})

    def test_image_utils_images_match(self):
        # Exact match
        self.assertTrue(utils_upsync.images_match(IMG1_BYTES, IMG1_BYTES))
        self.assertTrue(utils_upsync.images_match(IMG1_B64, IMG1_B64))
        # Different images
        self.assertFalse(utils_upsync.images_match(IMG1_BYTES, IMG2_BYTES))
        self.assertFalse(utils_upsync.images_match(IMG1_B64, IMG2_B64))

    def test_odoo_create_prevents_duplicate_gallery_record(self):
        """Creating a gallery image with duplicate content must NOT create a new row in Odoo."""
        img1 = self.env['product.shopify.image'].create({
            'product_tmpl_id': self.template.id,
            'name': 'Front',
            'image': IMG1_B64,
        })
        # Attempt to create duplicate image
        img2 = self.env['product.shopify.image'].create({
            'product_tmpl_id': self.template.id,
            'name': 'Front Duplicate',
            'image': IMG1_B64,
            'shopify_media_id': '999',
        })
        # Verify img2 returned the existing record
        self.assertEqual(img1.id, img2.id)
        # Verify only 1 record exists in the database
        records = self.env['product.shopify.image'].search([('product_tmpl_id', '=', self.template.id)])
        self.assertEqual(len(records), 1)
        # Verify shopify_media_id was updated onto img1
        self.assertEqual(records.shopify_media_id, '999')

    def test_odoo_gallery_deduplication_clean_merge(self):
        """_deduplicate_shopify_images must safely merge duplicate rows and preserve unique ones."""
        # Create records bypassing create dedup via context
        img1 = self.env['product.shopify.image'].with_context(skip_image_dedup=True).create({
            'product_tmpl_id': self.template.id,
            'name': 'Front 1',
            'image': IMG1_B64,
        })
        img2 = self.env['product.shopify.image'].with_context(skip_image_dedup=True).create({
            'product_tmpl_id': self.template.id,
            'name': 'Front 2 Duplicate',
            'image': IMG1_B64,
            'shopify_media_id': '888',
        })
        unique_img = self.env['product.shopify.image'].with_context(skip_image_dedup=True).create({
            'product_tmpl_id': self.template.id,
            'name': 'Different Image',
            'image': IMG2_B64,
            'shopify_media_id': '777',
        })

        all_imgs = self.env['product.shopify.image'].search([('product_tmpl_id', '=', self.template.id)])
        self.assertEqual(len(all_imgs), 3)

        # Run deduplication
        self.template._deduplicate_shopify_images()

        remaining = self.env['product.shopify.image'].search([('product_tmpl_id', '=', self.template.id)])
        self.assertEqual(len(remaining), 2)
        self.assertIn(unique_img, remaining)
        self.assertIn(img1, remaining)
        self.assertNotIn(img2, remaining)
        # Primary record adopted shopify_media_id from duplicate
        self.assertEqual(img1.shopify_media_id, '888')

    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._fetch_url_bytes')
    @patch('odoo.addons.ecommerce_shopify_upsync.utils_upsync._call_shopify_graphql_admin_api')
    def test_push_skips_cross_duplicate_between_main_and_gallery(self, mock_api, mock_fetch):
        """When gallery row duplicates template main image, only 1 image is pushed to Shopify."""
        self.template.image_1920 = IMG1_B64
        self.template.shopify_media_id = '111'
        self.env['product.shopify.image'].with_context(skip_image_dedup=True).create({
            'product_tmpl_id': self.template.id,
            'name': 'Gallery Copy of Main',
            'image': IMG1_B64,
            'shopify_media_id': '111',
        })

        mock_api.return_value = {
            'data': {
                'product': {
                    'media': {
                        'nodes': [{
                            'id': const.GLOBAL_MEDIA_IMAGE_ID + '111',
                            'mediaContentType': 'IMAGE',
                            'alt': 'Front',
                            'image': {'url': 'https://cdn.example/front.png'},
                        }],
                    },
                },
            },
        }

        with self.assertLogs('odoo.addons.ecommerce_shopify_upsync.models.ecommerce_account', level='INFO') as cm:
            self.account._push_product_images_to_shopify('999999', self.template, self.env['ecommerce.offer'])
            log_output = " ".join(cm.output)
            self.assertIn('DUPLICATE DETECTED', log_output)
            self.assertIn('IMAGE SKIPPED', log_output)

        # No new media should be created
        self.assertFalse(any('productCreateMedia' in c.args[2] for c in mock_api.call_args_list))

    @patch('odoo.addons.ecommerce_shopify.models.ecommerce_account.fetch_image_base64_from_url')
    def test_pull_matches_existing_by_content_and_prevents_duplicate(self, mock_fetch_image):
        """Pulling an image from Shopify that has matching content updates existing record without creating duplicate."""
        existing_row = self.env['product.shopify.image'].create({
            'product_tmpl_id': self.template.id,
            'name': 'Local Image',
            'image': IMG1_B64,
        })
        mock_fetch_image.return_value = IMG1_B64

        offer = self.env['ecommerce.offer'].create({
            'name': 'Offer 1',
            'sku': 'SKU-DEDUP-1',
            'ecommerce_account_id': self.account.id,
            'matched_product_id': self.template.product_variant_id.id,
            'ec_product_template_identifier': '999999',
            'ec_product_identifier': '888888',
        })

        product_data = {
            'variant_data': {
                'images': [{
                    'shopify_media_id': '555',
                    'url': 'https://cdn.example/cdn-img1.png',
                    'alt': 'Shopify Label',
                }],
            },
        }

        with self.assertLogs('odoo.addons.ecommerce_shopify.models.ecommerce_account', level='INFO') as cm:
            self.account._sync_shopify_product_images(offer, product_data)
            log_output = " ".join(cm.output)
            self.assertIn('IMAGE FOUND', log_output)
            self.assertIn('IMAGE UPDATED', log_output)
            self.assertIn('DUPLICATE DETECTED', log_output)

        rows = self.env['product.shopify.image'].search([('product_tmpl_id', '=', self.template.id)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.id, existing_row.id)
        self.assertEqual(rows.shopify_media_id, '555')
        self.assertEqual(rows.name, 'Shopify Label')
