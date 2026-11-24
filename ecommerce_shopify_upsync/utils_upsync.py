# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import hashlib
import logging
import re
import unicodedata

import requests

from odoo.addons.ecommerce_shopify import const
from odoo.addons.ecommerce_shopify.image_utils import (
    decode_image_bytes,
    compute_image_fingerprint,
    fingerprints_match,
    images_match,
)
from odoo.addons.ecommerce_shopify.utils_graphql import _call_shopify_graphql_admin_api
from odoo.addons.odoo_ecommerce.utils import ECommerceApiError

_logger = logging.getLogger(__name__)

TIMEOUT = 30

# Shopify's WeightUnit enum (InventoryItemMeasurementInput.weight.unit).
WEIGHT_UNIT_GRAMS = 'GRAMS'
WEIGHT_UNIT_KILOGRAMS = 'KILOGRAMS'
WEIGHT_UNIT_OUNCES = 'OUNCES'
WEIGHT_UNIT_POUNDS = 'POUNDS'

# Conversion factors to kilograms, used as the pivot unit for any-to-any conversion.
_KG_PER_UNIT = {
    WEIGHT_UNIT_KILOGRAMS: 1.0,
    WEIGHT_UNIT_GRAMS: 0.001,
    WEIGHT_UNIT_POUNDS: 0.45359237,
    WEIGHT_UNIT_OUNCES: 0.028349523125,
}


# === Product Create Mutation === #

def _build_product_options_input(options, options_linked_metafields=None, options_detail=None):
    """Build the `productOptions: [...]` input block for productCreate.

    :param list options: option names, e.g. ['Color', 'Size']
    :param list options_linked_metafields: parallel list of dict|None
    :param list options_detail: list of dicts with 'name', 'values', 'namespace', 'key'
    """
    if not options and not options_detail:
        return ''
    parts = []
    if options_detail:
        for opt in options_detail:
            name = opt.get('name', '')
            val_strs = ['{name: %s}' % _gql_string(v) for v in opt.get('values', []) if v]
            values_clause = f"values: [{', '.join(val_strs)}]" if val_strs else ''
            link_clause = ''
            if opt.get('namespace') and opt.get('key'):
                link_clause = 'linkedMetafield: {namespace: %s, key: %s}' % (
                    _gql_string(opt['namespace']), _gql_string(opt['key']),
                )
            clauses = [f"name: {_gql_string(name)}"]
            if values_clause:
                clauses.append(values_clause)
            if link_clause:
                clauses.append(link_clause)
            parts.append('{%s}' % ' '.join(clauses))
    else:
        linked = options_linked_metafields or [None] * len(options)
        for name, link in zip(options, linked):
            linked_metafield_str = ''
            if link and link.get('namespace') and link.get('key'):
                linked_metafield_str = 'linkedMetafield: {namespace: %s, key: %s}' % (
                    _gql_string(link['namespace']), _gql_string(link['key']),
                )
            parts.append('{name: %s %s}' % (_gql_string(name), linked_metafield_str))
    return f"productOptions: [{', '.join(parts)}]"



def _build_seo_input(product_data):
    """Build the `seo: {...}` input block shared by productCreate/productUpdate.

    Shopify stores `seo.title`/`seo.description` as the `title_tag`/
    `description_tag` metafields under the hood (both single_line_text_field)
    — the same constraint the generic metafields push already has to satisfy
    (see `_sanitize_metafield_value`). `shopify_seo_description` is an Odoo
    Text field, so it can contain a newline a user typed or pasted; sending
    that straight through fails with "Value must be a single line text
    string." on the *whole* productUpdate/productCreate call, not just the
    metafield.
    """
    seo_title = _sanitize_metafield_value(product_data.get('seo_title'), 'single_line_text_field')
    seo_description = _sanitize_metafield_value(product_data.get('seo_description'), 'single_line_text_field')
    if not seo_title and not seo_description:
        return ''
    return 'seo: { title: %s description: %s }' % (
        _gql_string(seo_title), _gql_string(seo_description),
    )


def _generate_product_create_mutation(product_data):
    """Generate productCreate GraphQL mutation (2026-01: no variants in productCreate)."""
    tags = ', '.join(product_data.get('tags', []))
    status = product_data.get('status', 'ACTIVE')
    options_str = _build_product_options_input(
        product_data.get('options', []),
        product_data.get('options_linked_metafields'),
        options_detail=product_data.get('options_detail'),
    )
    # Handle: explicit override (product.shopify_handle) or a slugified title — always
    # sent so Shopify's URL matches what Odoo expects, instead of Shopify's own
    # auto-generated handle which can drift if the title changes later.
    handle_str = f"handle: {_gql_string(product_data['handle'])}" if product_data.get('handle') else ''
    # Shopify Standard Product Taxonomy category, e.g. "gid://shopify/TaxonomyCategory/sg-4-17-2-17".
    category_str = f"category: {_gql_string(product_data['category_id'])}" if product_data.get('category_id') else ''
    seo_str = _build_seo_input(product_data)

    mutation = """
    mutation ProductCreate {
        productCreate(input: {
            title: %(title)s
            descriptionHtml: %(description_html)s
            vendor: %(vendor)s
            productType: %(product_type)s
            tags: %(tags)s
            status: %(status)s
            %(handle)s
            %(category)s
            %(seo)s
            %(options)s
        }) {
            product {
                id
                handle
                category {
                    id
                    name
                }
            }
            userErrors {
                field
                message
            }
        }
    }
    """ % {
        'title': _gql_string(product_data.get('title', '')),
        'description_html': _gql_string(product_data.get('description_html', '')),
        'vendor': _gql_string(product_data.get('vendor', '')),
        'product_type': _gql_string(product_data.get('product_type', '')),
        'tags': _gql_string(tags),
        'status': status,
        'handle': handle_str,
        'category': category_str,
        'seo': seo_str,
        'options': options_str,
    }
    return mutation


def _generate_variants_bulk_create_mutation(shopify_product_id, variants, options=None):
    """Generate productVariantsBulkCreate mutation (2026-01: variants created separately)."""
    variants_input = _build_variants_input(variants, options=options)
    return """
    mutation VariantsBulkCreate {
        productVariantsBulkCreate(
            productId: "%(product_gid)s"
            variants: [%(variants)s]
            strategy: REMOVE_STANDALONE_VARIANT
        ) {
            productVariants {
                id
                inventoryItem {
                    id
                    sku
                }
                selectedOptions {
                    name
                    value
                }
            }
            userErrors {
                field
                message
            }
        }
    }
    """ % {
        'product_gid': const.GLOBAL_PRODUCT_ID + shopify_product_id,
        'variants': variants_input,
    }



def _generate_product_update_mutation(shopify_product_id, product_data):
    """Generate productUpdate GraphQL mutation.

    Note: existing option names/values are intentionally NOT resent here (Shopify's
    productUpdate input does not accept `productOptions` — that's create-only).
    Updating an existing option's linked metafield is handled separately by
    `sync_product_option_linked_metafields` via the dedicated `productOptionUpdate`
    mutation, since it requires the Shopify option ID.
    """
    tags = ', '.join(product_data.get('tags', []))
    status = product_data.get('status', 'ACTIVE')
    category_str = f"category: {_gql_string(product_data['category_id'])}" if product_data.get('category_id') else ''
    seo_str = _build_seo_input(product_data)
    vendor_str = f"vendor: {_gql_string(product_data['vendor'])}" if product_data.get('vendor') else ''

    mutation = """
    mutation ProductUpdate {
        productUpdate(input: {
            id: "%(gid)s"
            title: %(title)s
            descriptionHtml: %(description_html)s
            %(vendor)s
            productType: %(product_type)s
            tags: %(tags)s
            status: %(status)s
            %(category)s
            %(seo)s
        }) {
            product {
                id
                handle
                category {
                    id
                    name
                }
            }
            userErrors {
                field
                message
            }
        }
    }
    """ % {
        'gid': const.GLOBAL_PRODUCT_ID + shopify_product_id,
        'title': _gql_string(product_data.get('title', '')),
        'description_html': _gql_string(product_data.get('description_html', '')),
        'vendor': vendor_str,
        'product_type': _gql_string(product_data.get('product_type', '')),
        'tags': _gql_string(tags),
        'status': status,
        'category': category_str,
        'seo': seo_str,
    }
    return mutation


def _generate_variant_update_mutation(shopify_product_id, shopify_variant_id, variant_data, options=None):
    """Generate productVariantsBulkUpdate GraphQL mutation (Shopify API 2026-01+).

    :param str shopify_product_id: numeric Shopify product ID
    :param str shopify_variant_id: numeric Shopify variant ID
    :param dict variant_data: variant fields to update
    :param list options: optional list of option names
    """
    compare_at = f'compareAtPrice: "{variant_data["compare_at_price"]}"' if variant_data.get('compare_at_price') else ''
    inventory_policy = variant_data.get('inventory_policy', 'DENY')
    taxable = 'true' if variant_data.get('taxable', True) else 'false'

    taxable_line = f'taxable: {taxable}' if not variant_data.get('is_gift_card') else ''
    # Same additions as _build_variants_input (create path): requiresShipping,
    # taxCode, cost, and a dynamic (not hardcoded) weight unit.
    requires_shipping = 'true' if variant_data.get('requires_shipping', True) else 'false'
    tax_code_line = f'taxCode: {_gql_string(variant_data["tax_code"])}' if variant_data.get('tax_code') else ''
    cost_line = f'cost: "{variant_data["cost"]}"' if variant_data.get('cost') else ''

    option_values_line = ''
    var_opts = variant_data.get('variant_option_values')
    if not var_opts and variant_data.get('option_values'):
        if isinstance(variant_data['option_values'][0], dict):
            var_opts = variant_data['option_values']
        elif options:
            var_opts = [
                {'optionName': opt_name, 'name': str(val)}
                for opt_name, val in zip(options, variant_data['option_values']) if val
            ]
    if var_opts:
        opt_strs = [
            '{optionName: %s, name: %s}' % (_gql_string(o.get('optionName', '')), _gql_string(o.get('name', '')))
            for o in var_opts if o.get('name')
        ]
        if opt_strs:
            option_values_line = f"optionValues: [{', '.join(opt_strs)}]"

    mutation = """
    mutation VariantUpdate {
        productVariantsBulkUpdate(
            productId: "%(product_gid)s"
            variants: [{
                id: "%(variant_gid)s"
                price: "%(price)s"
                barcode: %(barcode)s
                %(compare_at)s
                %(taxable_line)s
                inventoryPolicy: %(inventory_policy)s
                %(tax_code)s
                %(option_values_line)s
                inventoryItem: {
                    sku: %(sku)s
                    tracked: %(tracked)s
                    requiresShipping: %(requires_shipping)s
                    %(cost)s
                    %(hsn_line)s
                    measurement: {
                        weight: {
                            value: %(weight)s
                            unit: %(weight_unit)s
                        }
                    }
                }
            }]
        ) {
            productVariants {
                id
                selectedOptions {
                    name
                    value
                }
            }
            userErrors {
                field
                message
            }
        }
    }
    """ % {
        'product_gid': const.GLOBAL_PRODUCT_ID + shopify_product_id,
        'variant_gid': const.GLOBAL_PRODUCT_VARIANT_ID + shopify_variant_id,
        'sku': _gql_string(variant_data.get('sku', '')),
        'barcode': _gql_string(variant_data.get('barcode', '')),
        'price': variant_data.get('price', 0.0),
        'compare_at': compare_at,
        'taxable': taxable,
        'inventory_policy': inventory_policy,
        'taxable_line': taxable_line,
        'requires_shipping': requires_shipping,
        'tax_code': tax_code_line,
        'cost': cost_line,
        'option_values_line': option_values_line,
        'hsn_line': f'harmonizedSystemCode: "{variant_data["hsn_code"]}"' if variant_data.get('hsn_code') else '',
        'tracked': 'true' if variant_data.get('inventory_management') else 'false',
        'weight': variant_data.get('weight', 0.0),
        'weight_unit': variant_data.get('weight_unit') or WEIGHT_UNIT_KILOGRAMS,
    }
    return mutation



def _generate_product_publish_mutation(shopify_product_id, published_scope):
    """Generate publishablePublish or publishableUnpublish mutation."""
    channel_map = {
        'global': ['online-store', 'pos'],
        'web': ['online-store'],
        'unpublished': [],
    }
    channels = channel_map.get(published_scope, ['online-store'])

    if not channels:
        mutation = """
        mutation ProductUnpublish {
            publishableUnpublishToCurrentChannel(id: "%(gid)s") {
                publishable { ... on Product { id } }
                userErrors { field message }
            }
        }
        """ % {'gid': const.GLOBAL_PRODUCT_ID + shopify_product_id}
    else:
        publication_ids = ' '.join([f'{{publicationId: "gid://shopify/Publication/{c}"}}' for c in channels])
        mutation = """
        mutation ProductPublish {
            publishablePublish(
                id: "%(gid)s"
                input: { publicationIds: [%(pub_ids)s] }
            ) {
                publishable { ... on Product { id } }
                userErrors { field message }
            }
        }
        """ % {
            'gid': const.GLOBAL_PRODUCT_ID + shopify_product_id,
            'pub_ids': publication_ids,
        }
    return mutation


def _staged_upload_create(headers, request_url, filename, file_size, mime_type):
    """Step 1: Request a staged upload target from Shopify (POST method for images)."""
    mutation = """
    mutation StagedUploadCreate {
        stagedUploadsCreate(input: [{
            resource: IMAGE
            filename: %(filename)s
            mimeType: %(mime_type)s
            fileSize: "%(file_size)s"
            httpMethod: POST
        }]) {
            stagedTargets {
                url
                resourceUrl
                parameters {
                    name
                    value
                }
            }
            userErrors { field message }
        }
    }
    """ % {
        'filename': _gql_string(filename),
        'mime_type': _gql_string(mime_type),
        'file_size': file_size,
    }
    response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
    errors = response.get('data', {}).get('stagedUploadsCreate', {}).get('userErrors', [])
    if errors:
        raise ECommerceApiError(f"stagedUploadsCreate errors: {errors}")
    targets = response.get('data', {}).get('stagedUploadsCreate', {}).get('stagedTargets', [])
    if not targets:
        raise ECommerceApiError(f"Shopify stagedUploadsCreate returned no targets. Response: {response}")
    _logger.info("Shopify staged upload target: url=%s resourceUrl=%s params=%s",
                 targets[0].get('url'), targets[0].get('resourceUrl'), targets[0].get('parameters'))
    return targets[0]


def _staged_upload_post(target, image_bytes, mime_type, filename):
    """Step 2: POST image as multipart/form-data to the S3 staged upload URL.

    IMPORTANT: 'file' field must be the LAST field in the multipart form.
    """
    import io
    post_url = target['url']
    parameters = target.get('parameters', [])

    # Build ordered multipart fields — 'file' must come last (S3 requirement)
    # Use list of tuples to preserve order
    multipart_fields = []
    for p in parameters:
        multipart_fields.append((p['name'], (None, p['value'])))

    # 'file' must be last
    multipart_fields.append(('file', (filename, io.BytesIO(image_bytes), mime_type)))

    _logger.info("Shopify staged upload POST to %s with %d params + file (%d bytes)",
                 post_url, len(parameters), len(image_bytes))

    response = requests.post(
        post_url,
        files=multipart_fields,
        timeout=TIMEOUT,
    )
    _logger.info("Shopify staged upload POST response: %s %s", response.status_code, response.text[:200])
    if response.status_code not in (200, 201, 204):
        raise ECommerceApiError(
            f"Shopify staged upload POST failed: {response.status_code} {response.text[:500]}"
        )
    return target['resourceUrl']


def _generate_product_create_media_mutation(shopify_product_id, resource_url, alt=None):
    """Step 3 of image upload: attach the uploaded image to the product.

    :param str alt: Image Alt Text — Shopify's `Media.alt` input, sourced from the
        Odoo image's Alt Text field (main image, additional image, or variant image).
    """
    alt_line = f'alt: {_gql_string(alt)}' if alt else ''
    return """
    mutation ProductCreateMedia {
        productCreateMedia(
            productId: "%(gid)s"
            media: [{
                originalSource: %(resource_url)s
                mediaContentType: IMAGE
                %(alt)s
            }]
        ) {
            media {
                ... on MediaImage { id }
            }
            userErrors { field message }
        }
    }
    """ % {
        'gid': const.GLOBAL_PRODUCT_ID + shopify_product_id,
        'resource_url': _gql_string(resource_url),
        'alt': alt_line,
    }


def _generate_product_media_query(shopify_product_id):
    """Fetch existing image media for a product so push can reconcile.

    Returns MediaImage nodes with id, mediaContentType, alt, and image.url
    so each Odoo image can be matched against existing Shopify media.
    Non-image media (video, 3D) are also returned for safe pruning when
    they match no Odoo counterpart.
    """
    return """
    query GetProductMedia {
        product(id: "%(gid)s") {
            media(first: 250) {
                nodes {
                    id
                    mediaContentType
                    ... on MediaImage {
                        alt
                        image {
                            url
                        }
                    }
                }
            }
        }
    }
    """ % {'gid': const.GLOBAL_PRODUCT_ID + shopify_product_id}


def _sha256_bytes(data):
    """Return hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def _fetch_url_bytes(url):
    """Download bytes from a public Shopify CDN URL.

    Used for content-hash comparison when no stored ``shopify_media_id``
    mapping is available.  Raises on network errors so the caller can
    fall back gracefully.
    """
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.content


def _parse_existing_media(response):
    """Normalise the media query response into a flat list of dicts.

    Each dict has keys: gid, bare_id, media_content_type, alt, image_url.
    Non-image nodes may have alt/image_url as empty strings.
    """
    nodes = (
        response.get('data', {})
        .get('product', {})
        .get('media', {})
        .get('nodes', [])
    )
    result = []
    for node in nodes:
        gid = node.get('id', '')
        # Odoo stores the numeric part, while callers/API fixtures may provide
        # either that value or Shopify's full GID.  Always compare by the same
        # canonical numeric identifier.
        bare = _normalise_media_id(gid)
        img = node.get('image') or {}
        result.append({
            'gid': gid,
            'bare_id': bare,
            'media_content_type': (node.get('mediaContentType') or '').upper(),
            'alt': node.get('alt') or img.get('altText') or '',
            'image_url': img.get('url') or '',
        })
    return result


def _normalise_media_id(media_id):
    """Return Shopify's numeric MediaImage id for a bare id or full GID."""
    value = str(media_id or '').strip()
    if not value:
        return ''
    return value.rsplit('/', 1)[-1]


def _generate_product_delete_media_mutation(shopify_product_id, media_ids):
    """Delete existing media from a product."""
    ids_list = ', '.join([f'"{mid}"' for mid in media_ids])
    return """
    mutation ProductDeleteMedia {
        productDeleteMedia(
            productId: "%(gid)s"
            mediaIds: [%(media_ids)s]
        ) {
            deletedMediaIds
            userErrors { field message }
        }
    }
    """ % {
        'gid': const.GLOBAL_PRODUCT_ID + shopify_product_id,
        'media_ids': ids_list,
    }


def _generate_product_reorder_media_mutation(shopify_product_id, media_id_positions):
    """Image Position: explicit reordering of already-uploaded media.

    Shopify's productCreateMedia has no `position` input — media position is set by
    a separate `productReorderMedia` call once every image has a media id, using the
    order captured from `shopify_image_ids.sequence` (see `_push_product_images_to_shopify`).

    :param list media_id_positions: list of (bare media_id, position) tuples, 0-based
        — `media_id` is the id stripped of its GID prefix, matching how
        `push_product_images_to_shopify` stores `shopify_media_id`.
    """
    # MoveInput.newPosition is a String in Shopify's schema (despite being a
    # numeric index), so it's quoted like the id, not passed as a bare integer.
    moves = ', '.join(
        '{id: %s newPosition: %s}' % (
            _gql_string(const.GLOBAL_MEDIA_IMAGE_ID + media_id), _gql_string(str(position)),
        )
        for media_id, position in media_id_positions
    )
    return """
    mutation ProductReorderMedia {
        productReorderMedia(
            id: "%(gid)s"
            moves: [%(moves)s]
        ) {
            job { id }
            userErrors { field message }
        }
    }
    """ % {
        'gid': const.GLOBAL_PRODUCT_ID + shopify_product_id,
        'moves': moves,
    }


def _generate_variant_append_media_mutation(shopify_product_id, shopify_variant_id, media_id):
    """Variant Image: link an already-uploaded product media to a specific variant.

    There is no `variantImage` field on ProductVariantsBulkInput — Shopify links
    media to a variant with the dedicated `productVariantAppendMedia` mutation,
    which is why this runs as a follow-up step after the image itself is uploaded.

    :param str media_id: bare media id (GID prefix stripped), matching how
        `push_product_images_to_shopify` stores `shopify_media_id`.
    """
    return """
    mutation VariantAppendMedia {
        productVariantAppendMedia(
            productId: "%(product_gid)s"
            variantMedia: [{
                variantId: "%(variant_gid)s"
                mediaIds: ["%(media_gid)s"]
            }]
        ) {
            productVariants { id }
            userErrors { field message }
        }
    }
    """ % {
        'product_gid': const.GLOBAL_PRODUCT_ID + shopify_product_id,
        'variant_gid': const.GLOBAL_PRODUCT_VARIANT_ID + shopify_variant_id,
        'media_gid': const.GLOBAL_MEDIA_IMAGE_ID + media_id,
    }


def _generate_inventory_activate_mutation(inventory_item_id, location_id):
    """Variant Fulfillment Service: activate a variant's InventoryItem at a location.

    Shopify's GraphQL API has no per-variant "fulfillment service" string like the
    legacy REST API — a fulfillment service IS a Location (owned by an app such as a
    3PL), and routing a variant to it means activating (creating an InventoryLevel
    for) that InventoryItem at the service's Location. This is additive: it does not
    touch the item's existing activation at the account's default/other locations, so
    the current `_update_inventory` quantity-sync flow keeps working unchanged.
    """
    return """
    mutation InventoryActivate {
        inventoryActivate(
            inventoryItemId: "%(item_gid)s"
            locationId: "%(location_gid)s"
        ) {
            inventoryLevel { id }
            userErrors { field message }
        }
    }
    """ % {
        'item_gid': const.GLOBAL_INVENTORY_ITEM_ID + inventory_item_id,
        'location_gid': const.GLOBAL_LOCATION_ID + location_id,
    }


def _generate_metafields_set_mutation(metafields):
    """Generic Shopify Metafields framework: bulk-set metafields via `metafieldsSet`.

    Used both by the generic `shopify.product.metafield` lines a user adds on a
    product, and by the Google Shopping helper (which pre-fills lines under the
    `mm-google-shopping` namespace — Shopify's own Google & YouTube channel app
    convention — see `product_template.action_add_google_shopping_metafields`).

    CRITICAL (Shopify MetafieldsSetInput identity): Shopify identifies a product
    metafield by the logical combination (ownerId + namespace + key). `metafieldsSet`
    upserts on that combination — if it exists Shopify updates it, otherwise it
    creates it. MetafieldsSetInput does NOT carry a `metafield_id` / `id` identity
    field; the id is returned by Shopify and stored on the Odoo row purely for
    mapping/debugging. We therefore ALWAYS send owner_id + namespace + key + type
    + value, never an id — this makes repeated pushes idempotent by construction.

    :param list metafields: list of dicts, each with keys owner_id (product GID),
        namespace, key, type, value.
    """
    inputs = []
    for m in metafields:
        inputs.append("""{
            ownerId: "%(owner_id)s"
            namespace: %(namespace)s
            key: %(key)s
            type: %(type)s
            value: %(value)s
        }""" % {
            'owner_id': m['owner_id'],
            'namespace': _gql_string(m['namespace']),
            'key': _gql_string(m['key']),
            'type': _gql_string(m['type']),
            'value': _gql_string(m['value']),
        })
    return """
    mutation MetafieldsSet {
        metafieldsSet(metafields: [%(inputs)s]) {
            metafields { id namespace key type value compareDigest }
            userErrors { field message code }
        }
    }
    """ % {'inputs': ', '.join(inputs)}


def _generate_metafields_delete_mutation(metafield_gids):
    """Delete metafields from Shopify via `metafieldsDelete`.

    Only called explicitly (see `delete_metafield_from_shopify`) — never used to
    clear rows that merely became stale during a pull (those are archived in Odoo
    instead).

    :param list metafield_gids: full GIDs of the metafields to delete.
    """
    ids = ', '.join(f'"{gid}"' for gid in metafield_gids)
    return """
    mutation MetafieldsDelete {
        metafieldsDelete(metafields: [%(ids)s]) {
            deletedMetafields {
                id
            }
            userErrors { field message code }
        }
    }
    """ % {'ids': ids}


def _generate_product_metafields_query(shopify_product_id, after=None):
    """Fetch a Shopify product's metafields via the Admin GraphQL Product query.

    This is a PRODUCT metafield query — NOT a blog/`blogs/{blog_id}/metafields`
    REST call. The ownerId is the Product GID (`gid://shopify/Product/<id>`); the
    product numeric ID is converted to the GID at the call site.

    :param str shopify_product_id: numeric Shopify product ID.
    :param str after: optional pagination cursor (endCursor of a previous page).
    """
    after_str = f'after: "{after}"' if after else ''
    return """
    query ProductMetafields {
        product(id: "%(gid)s") {
            id
            title
            metafields(first: 250, %(after)s) {
                nodes {
                    id
                    namespace
                    key
                    value
                    type
                    compareDigest
                    createdAt
                    updatedAt
                }
                pageInfo {
                    hasNextPage
                    endCursor
                }
            }
        }
    }
    """ % {
        'gid': const.GLOBAL_PRODUCT_ID + shopify_product_id,
        'after': after_str,
    }


def _generate_metafield_definitions_query(after=None, owner_type='PRODUCT', namespace=None, key=None, type_=None):
    """Fetch Shopify metafield definitions for a given owner type.

    Supports both `nodes` and `edges` connection structures, with pagination.
    Note: Filtering by namespace/key/type is handled in Python because Shopify's
    metafieldDefinitions schema accepts ownerType, first, after, but not direct
    namespace/key/type arguments.
    """
    arguments = [f'ownerType: {owner_type}', 'first: 250']
    if after:
        arguments.append(f'after: "{after}"')
    return """
    query ProductMetafieldDefinitions {
        metafieldDefinitions(%(arguments)s) {
            nodes {
                id
                name
                namespace
                key
                description
                ownerType
                type {
                    name
                }
            }
            edges {
                cursor
                node {
                    id
                    name
                    namespace
                    key
                    description
                    ownerType
                    type {
                        name
                    }
                }
            }
            pageInfo {
                hasNextPage
                endCursor
            }
        }
    }
    """ % {'arguments': ', '.join(arguments)}


def _generate_price_list_fixed_prices_mutation(price_list_id, variant_gid, price, compare_at_price, currency_code):
    """Shopify Markets: set a variant's fixed price within a market's Price List.

    Shopify Markets pricing/inclusion is not part of productCreate/productUpdate —
    "Price / Country" and "Compare At Price / Country" are set per-market via
    `priceListFixedPricesAdd` against that market's Price List id (configured in
    Shopify Admin under Settings > Markets and stored on
    `shopify.market.price.market_price_list_id`).
    """
    compare_at_line = (
        f'compareAtPrice: {{ amount: "{compare_at_price}", currencyCode: {currency_code} }}'
        if compare_at_price else ''
    )
    return """
    mutation PriceListFixedPricesAdd {
        priceListFixedPricesAdd(
            priceListId: "%(price_list_gid)s"
            prices: [{
                variantId: "%(variant_gid)s"
                price: { amount: "%(price)s", currencyCode: %(currency_code)s }
                %(compare_at)s
            }]
        ) {
            prices { variant { id } }
            userErrors { field message }
        }
    }
    """ % {
        'price_list_gid': price_list_id,
        'variant_gid': variant_gid,
        'price': price,
        'currency_code': currency_code,
        'compare_at': compare_at_line,
    }


def _generate_product_options_query(shopify_product_id):
    """Fetch a product's current options (id, name, linkedMetafield).

    Option Linked To (see `_build_product_options_input`) can only be *set* at
    option-creation time via `productOptions.linkedMetafield` — Shopify's API does
    not expose a mutation to change an existing option's linked metafield afterward
    (the only route is deleting and recreating the option, which is destructive to
    its variants and out of scope for an automated sync). This query is therefore
    used defensively, as a drift check: `sync_product_option_linked_metafields`
    compares Shopify's current linkage against Odoo's `product.attribute` config and
    logs a warning (instead of silently failing, or pretending an unsupported update
    happened) when they've diverged after the option already exists.
    """
    return """
    query GetProductOptions {
        product(id: "%(gid)s") {
            options {
                id
                name
                linkedMetafield { namespace key }
            }
        }
    }
    """ % {'gid': const.GLOBAL_PRODUCT_ID + shopify_product_id}


# === Helper Functions === #

def _gql_string(value):
    """Escape and wrap a value as a GraphQL string."""
    if value is None:
        return '""'
    escaped = str(value).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    return f'"{escaped}"'


def slugify_handle(text):
    """Turn a product title into a Shopify-safe handle (URL slug).

    Shopify handles must be lowercase, ASCII, use hyphens as separators, and contain
    no characters outside [a-z0-9-]. Mirrors what Shopify itself does when it
    auto-generates a handle from the title, so an explicit handle we send stays
    consistent with one Shopify would have generated on its own.
    """
    if not text:
        return ''
    # Normalize accented characters to their closest ASCII equivalent (e.g. "café" -> "cafe").
    normalized = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode('ascii')
    slug = normalized.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    slug = re.sub(r'-{2,}', '-', slug)
    return slug


def get_odoo_weight_unit(env):
    """Return the Shopify WeightUnit that matches Odoo's system-wide weight UoM.

    Odoo only exposes a kg/lb toggle at the system level (ir.config_parameter
    'product.weight_in_lbs') — there's no per-product weight UoM. This reads that
    toggle instead of assuming kilograms.
    """
    weight_in_lbs = env['ir.config_parameter'].sudo().get_param('product.weight_in_lbs')
    return WEIGHT_UNIT_POUNDS if weight_in_lbs == '1' else WEIGHT_UNIT_KILOGRAMS


def convert_weight(value, from_unit, to_unit):
    """Convert a weight value between Shopify WeightUnit values (via a kg pivot)."""
    if not value or from_unit == to_unit:
        return value
    kg_value = value * _KG_PER_UNIT.get(from_unit, 1.0)
    return kg_value / _KG_PER_UNIT.get(to_unit, 1.0)


def get_shopify_variant_weight(env, weight, override_unit='auto'):
    """Resolve the (value, unit) pair to send Shopify for a variant's weight.

    :param float weight: raw `product.product.weight` value, expressed in Odoo's
        current system weight UoM (see `get_odoo_weight_unit`).
    :param str override_unit: 'auto' (default, use Odoo's own unit unchanged) or one
        of GRAMS/KILOGRAMS/OUNCES/POUNDS to force conversion to that unit — e.g. when
        the Shopify store's catalog is standardized on grams or ounces.
    :return: tuple(float value, str shopify_weight_unit)
    """
    source_unit = get_odoo_weight_unit(env)
    target_unit = source_unit if not override_unit or override_unit == 'auto' else override_unit
    return round(convert_weight(weight or 0.0, source_unit, target_unit), 4), target_unit


def _build_options_input(options):
    """Build options input string for product creation."""
    return ', '.join([_gql_string(opt) for opt in options])


def _build_variants_input(variants, options=None):
    """Build variant input list for product creation."""
    parts = []
    for v in variants:
        compare_at = f'compareAtPrice: "{v["compare_at_price"]}"' if v.get('compare_at_price') else ''
        inventory_policy = v.get('inventory_policy', 'DENY')
        # Variant Inventory Tracker: Shopify's GraphQL API only exposes this as the
        # InventoryItem.tracked boolean (the legacy REST "fulfillment_service"-based
        # tracker string doesn't exist here) — 'shopify' -> tracked, 'none' -> untracked.
        tracked = 'true' if v.get('inventory_management') else 'false'
        taxable = 'true' if v.get('taxable', True) else 'false'
        option_values = ''
        var_opts = v.get('variant_option_values')
        if not var_opts and v.get('option_values'):
            if isinstance(v['option_values'][0], dict):
                var_opts = v['option_values']
            elif options:
                var_opts = [
                    {'optionName': opt_name, 'name': str(val)}
                    for opt_name, val in zip(options, v['option_values']) if val
                ]
        if var_opts:
            opt_strs = [
                '{optionName: %s, name: %s}' % (_gql_string(o.get('optionName', '')), _gql_string(o.get('name', '')))
                for o in var_opts if o.get('name')
            ]
            if opt_strs:
                option_values = f"optionValues: [{', '.join(opt_strs)}]"
        elif v.get('option_values') and not options:
            opts = ', '.join([_gql_string(o) for o in v['option_values']])
            option_values = f'options: [{opts}]'


        # Variant Requires Shipping: InventoryItemInput.requiresShipping.
        requires_shipping = 'true' if v.get('requires_shipping', True) else 'false'
        # Variant Tax Code: top-level ProductVariantsBulkInput.taxCode (used by
        # external tax services such as Avalara/TaxJar to classify the item).
        tax_code_line = f'taxCode: {_gql_string(v["tax_code"])}' if v.get('tax_code') else ''
        # Cost per Item: InventoryItemInput.cost, sourced from product.standard_price.
        cost_line = f'cost: "{v["cost"]}"' if v.get('cost') else ''

        parts.append("""
        {
            price: "%(price)s"
            barcode: %(barcode)s
            %(compare_at)s
            taxable: %(taxable)s
            inventoryPolicy: %(inventory_policy)s
            %(tax_code)s
            inventoryItem: {
                sku: %(sku)s
                tracked: %(tracked)s
                requiresShipping: %(requires_shipping)s
                %(cost)s
                %(hsn_line)s
                measurement: {
                    weight: {
                        value: %(weight)s
                        unit: %(weight_unit)s
                    }
                }
            }
            %(option_values)s
        }
        """ % {
            'sku': _gql_string(v.get('sku', '')),
            'barcode': _gql_string(v.get('barcode', '')),
            'price': v.get('price', 0.0),
            'compare_at': compare_at,
            'taxable': taxable,
            'inventory_policy': inventory_policy,
            'tracked': tracked,
            'requires_shipping': requires_shipping,
            'tax_code': tax_code_line,
            'cost': cost_line,
            'weight': v.get('weight', 0.0),
            # Variant Weight Unit: dynamic, resolved by `get_shopify_variant_weight`
            # from Odoo's configured weight UoM — never hardcoded.
            'weight_unit': v.get('weight_unit') or WEIGHT_UNIT_KILOGRAMS,
            'option_values': option_values,
            'hsn_line': f'harmonizedSystemCode: "{v["hsn_code"]}"' if v.get('hsn_code') else '',
        })
    return ', '.join(parts)


# === API Call Functions === #

def push_product_to_shopify(account, product_data, shopify_product_id=None):
    """Push a product to Shopify (create or update).

    2026-01 API: productCreate no longer accepts variants.
    Flow for new products:
      Step 1 — productCreate (product fields only)
      Step 2 — productVariantsBulkCreate (variants)

    :param account: ecommerce.account record
    :param dict product_data: prepared product data
    :param str shopify_product_id: existing Shopify product ID (for update)
    :return: dict with shopify_product_id and variants list
    """
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }

    if shopify_product_id:
        # Update existing product fields
        mutation = _generate_product_update_mutation(shopify_product_id, product_data)
        response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
        _handle_upsync_errors('productUpdate', response)
        product = (response.get('data') or {}).get('productUpdate', {}).get('product') or {}
        cat_info = product.get('category') or {}
        cat_gid = cat_info.get('id') if isinstance(cat_info, dict) else (cat_info or '')
        return {
            'shopify_product_id': shopify_product_id,
            'handle': product.get('handle', ''),
            'category_id': cat_gid,
        }
    else:
        # Step 1 — Create product (no variants)
        mutation = _generate_product_create_mutation(product_data)
        response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
        _handle_upsync_errors('productCreate', response)
        product = (response.get('data') or {}).get('productCreate', {}).get('product') or {}
        shopify_product_id = (product.get('id') or '').replace(const.GLOBAL_PRODUCT_ID, '')
        handle = product.get('handle', '')
        cat_info = product.get('category') or {}
        cat_gid = cat_info.get('id') if isinstance(cat_info, dict) else (cat_info or '')

        if not shopify_product_id:
            raise ECommerceApiError("productCreate returned no product ID.")

        result = {
            'shopify_product_id': shopify_product_id,
            'handle': handle,
            'category_id': cat_gid,
            'variants': [],
        }

        # Step 2 — Create variants
        variants = product_data.get('variants', [])
        if variants:
            result['variants'] = create_variants_on_shopify(
                account, shopify_product_id, variants, options=product_data.get('options')
            )

        return result


def create_variants_on_shopify(account, shopify_product_id, variants, options=None):
    """Create variants for an existing Shopify product and return their IDs.

    This is deliberately shared by the initial-create and re-push paths.  A
    product can gain an Odoo variant after it was first pushed, so an update
    must bulk-create offers that do not yet have a Shopify variant ID rather
    than silently skipping them.
    """
    if not variants:
        return []
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    mutation = _generate_variants_bulk_create_mutation(shopify_product_id, variants, options=options)
    _logger.debug("Bulk creating %d variants for product %s with payload: %s", len(variants), shopify_product_id, variants)
    response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
    _handle_upsync_errors('productVariantsBulkCreate', response)
    variant_nodes = (
        response.get('data', {})
        .get('productVariantsBulkCreate', {})
        .get('productVariants', [])
    )
    if len(variant_nodes) != len(variants):
        _logger.warning(
            "Shopify returned %d variant(s) after creating %d for product %s.",
            len(variant_nodes), len(variants), shopify_product_id,
        )
    result = []
    for index, node in enumerate(variant_nodes):
        if not node:
            continue
        inventory_item = node.get('inventoryItem') or {}
        result.append({
            'shopify_variant_id': (node.get('id') or '').replace(const.GLOBAL_PRODUCT_VARIANT_ID, ''),
            'shopify_inventory_item_id': (inventory_item.get('id') or '').replace(const.GLOBAL_INVENTORY_ITEM_ID, ''),
            # SKU is the stable mapping key.  Fall back to the submitted SKU
            # only for old API responses that omit inventoryItem.sku.
            'sku': inventory_item.get('sku') or (variants[index].get('sku', '') if index < len(variants) else ''),
            'selectedOptions': node.get('selectedOptions') or [],
        })
    return result


def push_variant_to_shopify(account, shopify_product_id, shopify_variant_id, variant_data, options=None):
    """Update a variant on Shopify.

    :param account: ecommerce.account record
    :param str shopify_variant_id: Shopify variant ID
    :param dict variant_data: variant fields to update
    :param list options: optional list of option names
    """
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    mutation = _generate_variant_update_mutation(shopify_product_id, shopify_variant_id, variant_data, options=options)
    response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
    _handle_upsync_errors('productVariantsBulkUpdate', response)



def push_product_images_to_shopify(account, shopify_product_id, images):
    """Push product images to Shopify idempotently.

    Fetches existing media first and reconciles against the Odoo image list
    so that repeated pushes do NOT re-create images that already exist.

    Matching priority per Odoo image:
      0. Another Odoo image already resolved earlier in this same call has
         matching content (e.g. the main image and a gallery row depicting
         the same picture) — reuse its resolved Shopify media id.
      1. Stored ``shopify_media_id`` on the ``offer_image`` record, validated
         against the product's current media list.
      1.5 Persisted image URL match against existing unclaimed Shopify media.
      2. Content-hash / visual fingerprint fallback — download the existing media's image URL,
         compute visual fingerprint and compare with the Odoo image bytes.
      3. No match -> staged-upload and create new media on Shopify.

    Every Odoo image gets exactly one chance to claim a Shopify media id above; any
    Shopify IMAGE media still unclaimed afterward is stale or redundant duplicate
    and is removed at the end to keep the media gallery in sync.

    :param account: ecommerce.account record
    :param str shopify_product_id: numeric Shopify product ID
    :param list images: list of dicts with keys:
        bytes, filename, mime_type, offer_image, alt (optional)
    :return: the same ``images`` list, with 'shopify_media_id' on every item.
    """
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }

    query = _generate_product_media_query(shopify_product_id)
    response = _call_shopify_graphql_admin_api(headers, request_url, query)
    existing = _parse_existing_media(response)
    existing_by_bare = {m['bare_id']: m for m in existing if m['bare_id']}

    claimed_ids = set()
    content_fingerprint_cache = {}

    def _get_existing_fingerprint(m):
        bare = m['bare_id']
        if bare in content_fingerprint_cache:
            return content_fingerprint_cache[bare]
        url = m.get('image_url')
        if not url:
            return None
        try:
            raw = _fetch_url_bytes(url)
            fp = compute_image_fingerprint(raw)
        except Exception as exc:
            _logger.debug("Could not fetch existing media %s for fingerprint: %s", bare, exc)
            return None
        content_fingerprint_cache[bare] = fp
        return fp

    # Step 2 — Match every Odoo image against existing Shopify media.
    resolved_fingerprints = []
    for img in images:
        odoo_fp = compute_image_fingerprint(img['bytes'])
        offer_image = img.get('offer_image')

        # Priority 0 — another Odoo image already resolved earlier in THIS
        # SAME call has matching content (typically the main image and a
        # gallery row depicting the same picture, both imported from Shopify
        # separately during fetch). Reuse its Shopify media id directly.
        matched_mid = None
        for prev_fp, prev_mid in resolved_fingerprints:
            if fingerprints_match(odoo_fp, prev_fp):
                matched_mid = prev_mid
                break

        if matched_mid:
            img['shopify_media_id'] = matched_mid
            if offer_image and getattr(offer_image, 'shopify_media_id', '') != matched_mid:
                offer_image.shopify_media_id = matched_mid
                _logger.info("IMAGE UPDATED: Updated offer_image shopify_media_id=%s", matched_mid)
            _logger.info(
                "DUPLICATE DETECTED: Image %s has duplicate content within this push matching ShopifyMediaId=%s",
                img.get('filename', ''), matched_mid,
            )
            _logger.info(
                "IMAGE FOUND: Reused Shopify media %s for duplicate content Odoo=%s",
                matched_mid, img.get('filename', ''),
            )
            _logger.info(
                "IMAGE SKIPPED: Skipping creation of duplicate image Odoo=%s ShopifyMediaId=%s",
                img.get('filename', ''), matched_mid,
            )
            _logger.info(
                "MEDIA EXISTS (duplicate content within this push) Odoo=%s Shopify=%s Action=SKIP_CREATE",
                img.get('filename', ''), matched_mid,
            )
            continue

        # Priority 1 — stored shopify_media_id mapping
        stored_id = ''
        if offer_image and getattr(offer_image, 'shopify_media_id', ''):
            stored_id = _normalise_media_id(offer_image.shopify_media_id)
        elif img.get('shopify_media_id'):
            stored_id = _normalise_media_id(img['shopify_media_id'])

        if stored_id and stored_id in existing_by_bare:
            img['shopify_media_id'] = stored_id
            claimed_ids.add(stored_id)
            resolved_fingerprints.append((odoo_fp, stored_id))
            _logger.info(
                "IMAGE FOUND: Found existing Shopify media %s via stored mapping for %s",
                stored_id, img.get('filename', ''),
            )
            _logger.info(
                "IMAGE SKIPPED: Skipping creation because media exists on Shopify (ID: %s)",
                stored_id,
            )
            _logger.info(
                "MEDIA EXISTS (stored mapping) Odoo=%s Shopify=%s Action=SKIP_CREATE",
                img.get('filename', ''), stored_id,
            )
            continue

        # Priority 1.5 — URL is a useful fallback when an older Odoo row has no media id.
        source_url = img.get('shopify_image_url') or ''
        if source_url:
            matched_url = next((m for m in existing if m.get('image_url') == source_url and m['bare_id'] not in claimed_ids), None)
            if matched_url:
                mid = matched_url['bare_id']
                img['shopify_media_id'] = mid
                claimed_ids.add(mid)
                resolved_fingerprints.append((odoo_fp, mid))
                if offer_image:
                    offer_image.shopify_media_id = mid
                    _logger.info("IMAGE UPDATED: Saved shopify_media_id=%s on offer_image", mid)
                _logger.info("IMAGE FOUND: Found existing Shopify media %s via URL for %s", mid, img.get('filename', ''))
                _logger.info("IMAGE SKIPPED: Skipping creation because media exists on Shopify (ID: %s)", mid)
                _logger.info("MEDIA EXISTS (URL) Odoo=%s Shopify=%s Action=SKIP_CREATE", img.get('filename', ''), mid)
                continue

        # Priority 2 — content / visual fingerprint match against existing Shopify media
        matched_existing = None
        for m in existing:
            if m['bare_id'] in claimed_ids:
                continue
            if m['media_content_type'] != 'IMAGE':
                continue
            existing_fp = _get_existing_fingerprint(m)
            if existing_fp and fingerprints_match(odoo_fp, existing_fp):
                matched_existing = m
                break

        if matched_existing:
            mid = matched_existing['bare_id']
            img['shopify_media_id'] = mid
            claimed_ids.add(mid)
            resolved_fingerprints.append((odoo_fp, mid))
            if offer_image and getattr(offer_image, 'shopify_media_id', '') != mid:
                offer_image.shopify_media_id = mid
                _logger.info("IMAGE UPDATED: Saved shopify_media_id=%s on offer_image via content match", mid)
            _logger.info(
                "IMAGE FOUND: Found existing Shopify media %s via content match for %s",
                mid, img.get('filename', ''),
            )
            _logger.info(
                "IMAGE SKIPPED: Skipping creation because media exists on Shopify (ID: %s)",
                mid,
            )
            _logger.info(
                "MEDIA EXISTS (content hash) Odoo=%s Shopify=%s Action=SKIP_CREATE",
                img.get('filename', ''), mid,
            )
            continue

        # Priority 3 — no match → create new media on Shopify
        try:
            target = _staged_upload_create(
                headers, request_url,
                img['filename'], len(img['bytes']), img['mime_type'],
            )
            resource_url = _staged_upload_post(
                target, img['bytes'], img['mime_type'], img['filename'],
            )
            mutation = _generate_product_create_media_mutation(
                shopify_product_id, resource_url, alt=img.get('alt'),
            )
            response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
            _handle_upsync_errors('productCreateMedia', response)

            media_nodes = (
                response.get('data', {})
                .get('productCreateMedia', {})
                .get('media', [])
            )
            if media_nodes:
                media_id = (media_nodes[0].get('id') or '').replace('gid://shopify/MediaImage/', '')
                if media_id:
                    img['shopify_media_id'] = media_id
                    claimed_ids.add(media_id)
                    resolved_fingerprints.append((odoo_fp, media_id))
                    if offer_image:
                        offer_image.shopify_media_id = media_id
                        _logger.info("IMAGE UPDATED: Saved new shopify_media_id=%s on offer_image", media_id)

            _logger.info(
                "IMAGE CREATED: Created new Shopify media %s for %s on product %s",
                img.get('shopify_media_id', ''), img.get('filename', ''), shopify_product_id,
            )
            _logger.info(
                "MEDIA CREATED Odoo=%s Shopify=%s Action=CREATE ShopifyMediaId=%s OdooMappingSaved=%s",
                img.get('filename', ''),
                shopify_product_id,
                img.get('shopify_media_id', ''),
                'YES' if offer_image and img.get('shopify_media_id') else 'NO',
            )
        except Exception as e:
            _logger.warning("Failed to upload image '%s': %s", img['filename'], str(e))

    # Step 3 — Stale media removal: delete Shopify IMAGE media that no current
    # Odoo image claimed.
    stale_ids = []
    for m in existing:
        if m['bare_id'] in claimed_ids:
            continue
        if m['media_content_type'] != 'IMAGE':
            continue
        stale_ids.append(m['gid'])
        _logger.info(
            "DUPLICATE DETECTED: Shopify media %s is unclaimed by any Odoo image; marking for deletion",
            m['bare_id'],
        )
        _logger.info(
            "STALE MEDIA Shopify=%s Action=DELETE (unclaimed by any current Odoo image)",
            m['bare_id'],
        )
    if stale_ids:
        try:
            mutation = _generate_product_delete_media_mutation(shopify_product_id, stale_ids)
            response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
            _handle_upsync_errors('productDeleteMedia', response)
            _logger.info("IMAGE SKIPPED: Deleted %d stale/duplicate media item(s) from Shopify product %s", len(stale_ids), shopify_product_id)
        except Exception as e:
            _logger.warning("Failed to delete stale media: %s", str(e))

    return images


def push_product_image_to_shopify(account, shopify_product_id, image_bytes, filename='product.png',
                                  mime_type='image/png'):
    """Push a single product image to Shopify (convenience wrapper)."""
    push_product_images_to_shopify(account, shopify_product_id, [{
        'bytes': image_bytes,
        'filename': filename,
        'mime_type': mime_type,
        'offer_image': None,
    }])
    
    
    
def publish_product_to_shopify(account, shopify_product_id, published_scope):
    """Publish a product to the store's sales channels (e.g. Online Store).

    Shopify's `status: ACTIVE` does NOT add a product to a sales channel — that
    requires publishablePublish against the channel's real (numeric) publication id.

    :param account: ecommerce.account record
    :param str shopify_product_id: numeric Shopify product ID
    :param str published_scope: 'global' | 'web' | 'unpublished'
    """
    target_names = _scope_to_channel_names(published_scope)
    if not target_names:
        _logger.info("Published scope is 'unpublished' — skipping channel publish for product %s.", shopify_product_id)
        return

    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }

    # Step 1 — resolve real publication IDs by channel name
    response = _call_shopify_graphql_admin_api(headers, request_url, _generate_publications_query())
    if response.get('errors'):
        raise ECommerceApiError(
            "Could not read the store's sales channels: %s. The Shopify app's Admin API "
            "access token needs the 'read_publications' and 'write_publications' scopes."
            % response['errors']
        )
    nodes = ((response.get('data') or {}).get('publications') or {}).get('nodes') or []
    wanted = {name.strip().lower() for name in target_names}
    pub_ids = [
        node['id'] for node in nodes
        if any(name.strip().lower() in wanted for name in _publication_names(node))
    ]
    if not pub_ids:
        available = ', '.join(sorted({
            name for node in nodes for name in _publication_names(node)
        })) or '(none)'
        raise ECommerceApiError(
            "No sales channel matching %s was found for published scope '%s'. Channels "
            "visible to this app: %s. If 'Online Store' is missing here, the app is either "
            "missing the 'read_publications' scope or is not installed on that channel."
            % (target_names, published_scope, available)
        )

    # Step 2 — publish to those channels
    pub_input = ' '.join(f'{{publicationId: "{pid}"}}' for pid in pub_ids)
    mutation = """
    mutation ProductPublish {
        publishablePublish(id: "%(gid)s", input: [%(pubs)s]) {
            publishable { ... on Product { id } }
            userErrors { field message }
        }
    }
    """ % {
        'gid': const.GLOBAL_PRODUCT_ID + shopify_product_id,
        'pubs': pub_input,
    }
    response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
    _handle_upsync_errors('publishablePublish', response)
    _logger.info("Published product %s to channels: %s", shopify_product_id, target_names)

    
    
def _scope_to_channel_names(published_scope):
    """Map the module's published_scope to Shopify sales-channel names."""
    return {
        'global': ['Online Store', 'Point of Sale'],
        'web': ['Online Store'],
        'unpublished': [],
    }.get(published_scope, ['Online Store'])
    
    
    
def publish_product_to_shopify(account, shopify_product_id, published_scope):
    """Publish a product to the store's sales channels (e.g. Online Store).

    Shopify's `status: ACTIVE` does NOT add a product to a sales channel — that
    requires publishablePublish against the channel's real (numeric) publication id.

    :param account: ecommerce.account record
    :param str shopify_product_id: numeric Shopify product ID
    :param str published_scope: 'global' | 'web' | 'unpublished'
    """
    target_names = _scope_to_channel_names(published_scope)
    if not target_names:
        _logger.info("Published scope is 'unpublished' — skipping channel publish for product %s.", shopify_product_id)
        return

    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }

    # Step 1 — resolve real publication IDs by channel name
    response = _call_shopify_graphql_admin_api(headers, request_url, _generate_publications_query())
    if response.get('errors'):
        raise ECommerceApiError(
            "Could not read the store's sales channels: %s. The Shopify app's Admin API "
            "access token needs the 'read_publications' and 'write_publications' scopes."
            % response['errors']
        )
    nodes = ((response.get('data') or {}).get('publications') or {}).get('nodes') or []
    wanted = {name.strip().lower() for name in target_names}
    pub_ids = [
        node['id'] for node in nodes
        if any(name.strip().lower() in wanted for name in _publication_names(node))
    ]
    if not pub_ids:
        available = ', '.join(sorted({
            name for node in nodes for name in _publication_names(node)
        })) or '(none)'
        raise ECommerceApiError(
            "No sales channel matching %s was found for published scope '%s'. Channels "
            "visible to this app: %s. If 'Online Store' is missing here, the app is either "
            "missing the 'read_publications' scope or is not installed on that channel."
            % (target_names, published_scope, available)
        )

    # Step 2 — publish to those channels
    pub_input = ' '.join(f'{{publicationId: "{pid}"}}' for pid in pub_ids)
    mutation = """
    mutation ProductPublish {
        publishablePublish(id: "%(gid)s", input: [%(pubs)s]) {
            publishable { ... on Product { id } }
            userErrors { field message }
        }
    }
    """ % {
        'gid': const.GLOBAL_PRODUCT_ID + shopify_product_id,
        'pubs': pub_input,
    }
    response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
    _handle_upsync_errors('publishablePublish', response)
    _logger.info("Published product %s to channels: %s", shopify_product_id, target_names)

def _generate_publications_query():
    """Fetch the store's sales-channel publications (id + name).

    `Publication.name` is deprecated but remains the only field that reliably returns
    "Online Store" / "Point of Sale"; `catalog.title` is null for most channel
    publications. Both are requested so matching still works if either one is empty.
    """
    return """
    query GetPublications {
        publications(first: 50) {
            nodes {
                id
                name
                catalog {
                    title
                }
            }
        }
    }
    """

def _publication_names(node):
    """Return every name a publication node may be known by."""
    names = []
    if node.get('name'):
        names.append(node['name'])
    catalog_title = (node.get('catalog') or {}).get('title')
    if catalog_title:
        names.append(catalog_title)
    return names


def _scope_to_channel_names(published_scope):
    """Map the module's published_scope to Shopify sales-channel names."""
    return {
        'global': ['Online Store', 'Point of Sale'],
        'web': ['Online Store'],
        'unpublished': [],
    }.get(published_scope, ['Online Store'])


def _publish_ids_to_publications(account, shopify_product_id, pub_ids):
    """Shared low-level step: call publishablePublish against explicit publication ids.

    Factored out of the old (accidentally duplicated) `publish_product_to_shopify` so
    both the existing scope-based publish flow and the new multi-channel / Shopify
    Markets flows (feature 17/18) drive the same, single GraphQL call path instead of
    each re-implementing it.
    """
    if not pub_ids:
        return
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    pub_input = ' '.join(f'{{publicationId: "{pid}"}}' for pid in pub_ids)
    mutation = """
    mutation ProductPublish {
        publishablePublish(id: "%(gid)s", input: [%(pubs)s]) {
            publishable { ... on Product { id } }
            userErrors { field message }
        }
    }
    """ % {
        'gid': const.GLOBAL_PRODUCT_ID + shopify_product_id,
        'pubs': pub_input,
    }
    response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
    _handle_upsync_errors('publishablePublish', response)
    _logger.info("Published product %s to publications: %s", shopify_product_id, pub_ids)


def _resolve_publication_ids(account, wanted_names):
    """Resolve human-readable channel/market names to their real Shopify publication ids."""
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    response = _call_shopify_graphql_admin_api(headers, request_url, _generate_publications_query())
    if response.get('errors'):
        raise ECommerceApiError(
            "Could not read the store's sales channels: %s. The Shopify app's Admin API "
            "access token needs the 'read_publications' and 'write_publications' scopes."
            % response['errors']
        )
    nodes = ((response.get('data') or {}).get('publications') or {}).get('nodes') or []
    wanted = {name.strip().lower() for name in wanted_names}
    pub_ids = [
        node['id'] for node in nodes
        if any(name.strip().lower() in wanted for name in _publication_names(node))
    ]
    return pub_ids, nodes


def publish_product_to_shopify(account, shopify_product_id, published_scope):
    """Publish a product to the store's sales channels (e.g. Online Store).

    Shopify's `status: ACTIVE` does NOT add a product to a sales channel — that
    requires publishablePublish against the channel's real (numeric) publication id.

    :param account: ecommerce.account record
    :param str shopify_product_id: numeric Shopify product ID
    :param str published_scope: 'global' | 'web' | 'unpublished'
    """
    target_names = _scope_to_channel_names(published_scope)
    if not target_names:
        _logger.info("Published scope is 'unpublished' — skipping channel publish for product %s.", shopify_product_id)
        return

    pub_ids, nodes = _resolve_publication_ids(account, target_names)
    if not pub_ids:
        available = ', '.join(sorted({
            name for node in nodes for name in _publication_names(node)
        })) or '(none)'
        raise ECommerceApiError(
            "No sales channel matching %s was found for published scope '%s'. Channels "
            "visible to this app: %s. If 'Online Store' is missing here, the app is either "
            "missing the 'read_publications' scope or is not installed on that channel."
            % (target_names, published_scope, available)
        )
    _publish_ids_to_publications(account, shopify_product_id, pub_ids)


def fetch_shopify_sales_channels(account):
    """Publish to Channels: list every publication (sales channel or market catalog)
    the app can see, so the user can pick any of them — not just the 3 hardcoded
    presets. Backs `ecommerce.account.action_fetch_shopify_sales_channels`.

    :return: list of dicts {shopify_publication_id, name}
    """
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    response = _call_shopify_graphql_admin_api(headers, request_url, _generate_publications_query())
    if response.get('errors'):
        raise ECommerceApiError(f"Could not read the store's sales channels: {response['errors']}")
    nodes = ((response.get('data') or {}).get('publications') or {}).get('nodes') or []
    return [
        {'shopify_publication_id': node['id'], 'name': (_publication_names(node) or [node['id']])[0]}
        for node in nodes if node.get('id')
    ]


def publish_product_to_channels(account, shopify_product_id, publication_ids):
    """Publish to Channels (custom scope): publish to an arbitrary, user-selected
    set of publication ids (`ecommerce.account.shopify_publish_channel_ids`),
    instead of the fixed global/web/unpublished presets.
    """
    _publish_ids_to_publications(account, shopify_product_id, publication_ids)


def unpublish_product_from_publication(account, shopify_product_id, publication_id):
    """Remove a product from a single sales channel / market catalog publication."""
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    mutation = """
    mutation ProductUnpublish {
        publishableUnpublish(id: "%(gid)s", input: [{publicationId: "%(pub_id)s"}]) {
            publishable { ... on Product { id } }
            userErrors { field message }
        }
    }
    """ % {
        'gid': const.GLOBAL_PRODUCT_ID + shopify_product_id,
        'pub_id': publication_id,
    }
    response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
    _handle_upsync_errors('publishableUnpublish', response)


def activate_inventory_at_location(account, inventory_item_id, location_id):
    """Variant Fulfillment Service: activate an InventoryItem at a fulfillment
    service's Location (see `_generate_inventory_activate_mutation`)."""
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    mutation = _generate_inventory_activate_mutation(inventory_item_id, location_id)
    response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
    _handle_upsync_errors('inventoryActivate', response)


def reorder_product_media(account, shopify_product_id, media_id_positions):
    """Image Position: apply explicit ordering to a product's uploaded media."""
    if not media_id_positions:
        return
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    mutation = _generate_product_reorder_media_mutation(shopify_product_id, media_id_positions)
    response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
    _handle_upsync_errors('productReorderMedia', response)


def link_media_to_variant(account, shopify_product_id, shopify_variant_id, media_id):
    """Variant Image: attach an already-uploaded media id to a specific variant."""
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    mutation = _generate_variant_append_media_mutation(shopify_product_id, shopify_variant_id, media_id)
    response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
    _handle_upsync_errors('productVariantAppendMedia', response)


# Shopify's metafieldsSet / metafieldsDelete request limit.
METAFIELDS_SET_BATCH_LIMIT = 25


def _log_metafield_request(operation, details=None, response=None):
    """Structured, token-free debug logging around every Shopify metafield API call.

    Explicitly does NOT log the Authorization header / access token, API secrets
    or passwords. Covers the fields requested for metafield sync debugging:
    operation, product ID/GID, namespace, key, type, value, metafield ID,
    GraphQL errors, userErrors and returned metafield IDs.
    """
    details = details or {}
    lines = [
        '=== Shopify Metafield API ===',
        'Request: %s' % operation,
    ]
    for field in ('product_id', 'product_gid', 'metafield_id', 'namespace', 'key', 'type', 'value', 'after', 'owner_type'):
        if field in details and details[field] is not None:
            lines.append('%s: %s' % (field.upper(), details[field]))
    if response is not None:
        lines.append('Response GraphQL errors: %s' % (response.get('errors') or 'none'))
        data = response.get('data') or {}
        for key in data:
            sub = data[key]
            if isinstance(sub, dict):
                lines.append('Response userErrors: %s' % (sub.get('userErrors') or 'none'))
                for node in sub.get('metafields') or []:
                    lines.append('Response metafield ID: %s (ns=%s key=%s)' % (
                        node.get('id'), node.get('namespace'), node.get('key'),
                    ))
    _logger.info('\n'.join(lines))


def fetch_product_metafields_from_shopify(account, shopify_product_id):
    """Pull every metafield of a Shopify product via the Admin GraphQL Product
    query, with full pagination (`pageInfo.hasNextPage` / `endCursor`).

    This is a PRODUCT metafield query — the owner is the Product GID
    (`gid://shopify/Product/<numeric_id>`), never a variant, blog or definition ID.

    :param account: ecommerce.account record (channel_code 'shopify', connected).
    :param str shopify_product_id: numeric Shopify product ID.
    :return: list of dicts — {id (bare numeric metafield ID), gid, namespace, key,
        value, type, created_at, updated_at}.
    :raise ECommerceApiError: on GraphQL errors, a missing product, an invalid
        GID, or an app scope / access problem.
    """
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    owner_gid = const.GLOBAL_PRODUCT_ID + shopify_product_id
    result = []
    has_next_page = True
    cursor = ''
    while has_next_page:
        query = _generate_product_metafields_query(shopify_product_id, after=cursor or None)
        _log_metafield_request('Product.metafields (READ / PULL)', {
            'product_id': shopify_product_id,
            'product_gid': owner_gid,
            'after': cursor or None,
        })
        response = _call_shopify_graphql_admin_api(headers, request_url, query)
        if response.get('errors'):
            messages = '; '.join(e.get('message') for e in response['errors'])
            raise ECommerceApiError(
                f"Shopify Product.metafields query error: {messages}. Check that the "
                "app access token has the read_products scope."
            )
        product = (response.get('data') or {}).get('product')
        if product is None:
            raise ECommerceApiError(
                f"Shopify product {shopify_product_id} was not found (it may have been "
                "deleted, the product ID may be wrong, or the app lacks read scope). "
                f"Raw response: {response}"
            )
        metafields_conn = product.get('metafields') or {}
        for node in metafields_conn.get('nodes') or []:
            gid = node.get('id') or ''
            result.append({
                'id': gid.replace(const.GLOBAL_METAFIELD_ID, ''),
                'gid': gid,
                'namespace': node.get('namespace'),
                'key': node.get('key'),
                'value': node.get('value'),
                'type': node.get('type'),
                'compare_digest': node.get('compareDigest'),
                'created_at': node.get('createdAt'),
                'updated_at': node.get('updatedAt'),
            })
        page_info = metafields_conn.get('pageInfo') or {}
        has_next_page = page_info.get('hasNextPage', False)
        cursor = page_info.get('endCursor') or ''
    _logger.info("Fetched %d metafield(s) for Shopify product %s", len(result), shopify_product_id)
    return result


def fetch_metafield_definitions_from_shopify(account, owner_type='PRODUCT', namespace=None, key=None, type_=None):
    """Pull Shopify metafield definitions via the `metafieldDefinitions` query,
    with full pagination, error handling, and optional filtering by namespace/key/type.

    :param account: ecommerce.account record.
    :param str owner_type: Shopify owner type (default 'PRODUCT').
    :param str namespace: optional namespace filter.
    :param str key: optional key filter.
    :param str type_: optional type filter.
    :return: list of dicts — {shopify_definition_id, name, namespace, key,
        description, owner_type, type} sorted in Shopify's own order.
    :raise ECommerceApiError: on GraphQL errors / missing credentials.
    """
    if not account or not account.shopify_store or not account.shopify_access_token:
        raise ECommerceApiError(
            _("Shopify account configuration missing store name or access token.")
        )

    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    result = []
    has_next_page = True
    cursor = ''
    page_count = 0
    max_pages = 100

    while has_next_page and page_count < max_pages:
        page_count += 1
        query = _generate_metafield_definitions_query(after=cursor or None, owner_type=owner_type)
        _log_metafield_request('metafieldDefinitions (READ)', {
            'owner_type': owner_type,
            'after': cursor or None,
            'page': page_count,
        })
        response = _call_shopify_graphql_admin_api(headers, request_url, query)
        if response.get('errors'):
            messages = '; '.join(e.get('message', str(e)) for e in response['errors'])
            raise ECommerceApiError(
                f"Shopify metafieldDefinitions query error: {messages}. Check that the "
                "Shopify access token includes the required metafield / product read scopes."
            )
        data = response.get('data') or {}
        definitions = data.get('metafieldDefinitions')
        if definitions is None:
            raise ECommerceApiError(
                f"Shopify metafieldDefinitions returned null. Raw response: {response}"
            )

        raw_nodes = definitions.get('nodes')
        if not raw_nodes and definitions.get('edges'):
            raw_nodes = [e.get('node') for e in definitions['edges'] if e.get('node')]

        for node in raw_nodes or []:
            raw_id = str(node.get('id') or '')
            def_id = raw_id.replace(const.GLOBAL_METAFIELD_DEFINITION_ID, '').split('/')[-1]
            raw_type = (node.get('type') or {}).get('name') or ''
            result.append({
                'shopify_definition_id': def_id,
                'name': node.get('name') or '',
                'namespace': node.get('namespace') or '',
                'key': node.get('key') or '',
                'description': node.get('description') or '',
                'owner_type': node.get('ownerType') or owner_type,
                'type': raw_type,
            })

        page_info = definitions.get('pageInfo') or {}
        has_next_page = bool(page_info.get('hasNextPage'))
        cursor = page_info.get('endCursor') or ''
        if not cursor and definitions.get('edges'):
            cursor = definitions['edges'][-1].get('cursor') or ''
        if not cursor:
            has_next_page = False

    # Apply optional client-side filters
    if namespace:
        result = [d for d in result if d.get('namespace') == namespace]
    if key:
        result = [d for d in result if d.get('key') == key]
    if type_:
        result = [d for d in result if d.get('type') == type_]

    _logger.info(
        "Fetched %d metafield definition(s) for ownerType %s (pages=%d, namespace=%s, key=%s, type=%s)",
        len(result), owner_type, page_count, namespace, key, type_,
    )
    return result


def match_metafield_definition(account, namespace, key, type_=None, owner_type='PRODUCT'):
    """Look up the exact metafield definition for a (namespace, key [, type])
    combination on PRODUCT owners — e.g. namespace='custom', key='brand'.

    The lookup filters by ownerType + namespace + key (+ type when known) so the
    definition found cannot be a different-namespace/key/tag one.

    :return: dict from `fetch_metafield_definitions_from_shopify` or None.
    """
    definitions = fetch_metafield_definitions_from_shopify(
        account, owner_type=owner_type, namespace=namespace, key=key, type_=type_,
    )
    for definition in definitions:
        if (definition.get('namespace') == namespace
                and definition.get('key') == key
                and (not type_ or definition.get('type') == type_)
                and definition.get('owner_type') == owner_type):
            return definition
    return None


def _generate_metafield_definition_create_mutation(namespace, key, mf_type, owner_type='PRODUCT', name=None):
    """`metafieldDefinitionCreate` — makes a metafield VALUE (set via
    `metafieldsSet`) actually show up as a field on Shopify's product-edit page
    "Product metafields" card: Shopify only renders a field there when a
    matching definition exists, `metafieldsSet` alone never creates one.
    """
    return """
    mutation CreateMetafieldDefinition {
        metafieldDefinitionCreate(definition: {
            name: %(name)s
            namespace: %(namespace)s
            key: %(key)s
            type: %(type)s
            ownerType: %(owner_type)s
        }) {
            createdDefinition {
                id
                name
                namespace
                key
            }
            userErrors {
                field
                message
                code
            }
        }
    }
    """ % {
        'name': _gql_string(name or key.replace('_', ' ').title()),
        'namespace': _gql_string(namespace),
        'key': _gql_string(key),
        'type': _gql_string(mf_type),
        'owner_type': owner_type,
    }


def normalize_shopify_definition_id(def_id):
    """Normalize Shopify Metafield Definition ID to canonical string without GID prefix."""
    if not def_id:
        return ''
    val = str(def_id).strip()
    if not val:
        return ''
    return val.replace(const.GLOBAL_METAFIELD_DEFINITION_ID, '').split('/')[-1]


def format_shopify_metafield_gid(metafield_id):
    """Format Shopify Metafield ID as canonical GID: gid://shopify/Metafield/..."""
    if not metafield_id:
        return False
    val = str(metafield_id).strip()
    if not val:
        return False
    if val.startswith(const.GLOBAL_METAFIELD_ID):
        return val
    # If it is a bare numeric ID, prefix it with gid://shopify/Metafield/
    bare = val.split('/')[-1]
    if bare.isdigit():
        return f"{const.GLOBAL_METAFIELD_ID}{bare}"
    return val


def normalize_shopify_metafield_id(metafield_id):
    """Normalize Shopify Metafield ID to bare string without GID prefix."""
    if not metafield_id:
        return ''
    val = str(metafield_id).strip()
    if not val:
        return ''
    return val.replace(const.GLOBAL_METAFIELD_ID, '').split('/')[-1]


def create_metafield_definition_on_shopify(
    account, namespace, key, mf_type, name=None, owner_type='PRODUCT', shopify_definition_id=None
):
    """Create or reuse a Shopify Metafield Definition for (namespace, key, owner_type).

    - Checks whether it already exists in Shopify before creating.
    - Uses Shopify Definition ID as primary mapping when available.
    - Uses Namespace + Key + Owner Type as fallback unique identifier.
    - If already exists or already mirrored, reuses it without calling metafieldDefinitionCreate.
    - Treats TAKEN / already exists as existing/successful (reused), not an error.
    - Logs DEFINITION FOUND, DEFINITION REUSED, DEFINITION MAPPED, DEFINITION CREATED.

    :return: dict {'created': bool, 'definition_id': str or None, 'reused': bool, 'error': str or None}
    """
    if not account or not account.shopify_store or not account.shopify_access_token:
        raise ECommerceApiError(
            _("Shopify account configuration missing store name or access token.")
        )

    norm_def_id = normalize_shopify_definition_id(shopify_definition_id)

    # 1. Pre-check: Does the definition already exist on Shopify?
    try:
        existing_defs = fetch_metafield_definitions_from_shopify(account, owner_type=owner_type)
    except Exception as exc:
        _logger.warning("Failed to fetch Shopify definitions during pre-check: %s", exc)
        existing_defs = []

    found = None
    if norm_def_id:
        found = next(
            (d for d in existing_defs if normalize_shopify_definition_id(d.get('shopify_definition_id')) == norm_def_id),
            None,
        )
    if not found:
        found = next(
            (d for d in existing_defs
             if d.get('namespace') == namespace
             and d.get('key') == key
             and (d.get('owner_type') or owner_type) == owner_type),
            None,
        )

    if found:
        def_id = normalize_shopify_definition_id(found.get('shopify_definition_id'))
        _logger.info("DEFINITION FOUND: %s.%s on Shopify (Shopify Definition ID: %s)", namespace, key, def_id)
        _logger.info("DEFINITION REUSED: %s.%s (Shopify Definition ID: %s) already exists on Shopify", namespace, key, def_id)
        return {'created': False, 'definition_id': def_id, 'reused': True, 'error': None}

    # 2. Does not exist on Shopify, call metafieldDefinitionCreate
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    mutation = _generate_metafield_definition_create_mutation(
        namespace, key, mf_type, owner_type=owner_type, name=name,
    )
    response = _call_shopify_graphql_admin_api(headers, request_url, mutation)

    if response.get('errors'):
        messages = '; '.join(e.get('message') for e in response['errors'])
        _logger.warning(
            "Could not create Shopify metafield definition for %s.%s: %s",
            namespace, key, messages,
        )
        return {'created': False, 'definition_id': None, 'reused': False, 'error': messages}

    result = (response.get('data') or {}).get('metafieldDefinitionCreate') or {}
    user_errors = result.get('userErrors') or []
    already_exists = any(
        e.get('code') == 'TAKEN' or 'already exists' in (e.get('message') or '').lower()
        for e in user_errors
    )

    if already_exists:
        _logger.info("DEFINITION DUPLICATE SKIPPED: %s.%s already exists on Shopify", namespace, key)
        matched = match_metafield_definition(account, namespace, key, owner_type=owner_type)
        matched_id = normalize_shopify_definition_id(matched.get('shopify_definition_id')) if matched else None
        _logger.info("DEFINITION REUSED: %s.%s already mirrored on Shopify (Shopify Definition ID: %s)", namespace, key, matched_id or '-')
        return {'created': False, 'definition_id': matched_id, 'reused': True, 'error': None}

    if user_errors:
        message = '; '.join(
            "%s: %s [code=%s]" % (e.get('field'), e.get('message'), e.get('code'))
            for e in user_errors
        )
        _logger.warning(
            "Could not create Shopify metafield definition for %s.%s: %s",
            namespace, key, message,
        )
        return {'created': False, 'definition_id': None, 'reused': False, 'error': message}

    created = result.get('createdDefinition') or {}
    definition_id = normalize_shopify_definition_id(created.get('id'))
    _logger.info(
        "DEFINITION CREATED: %s.%s on Shopify (Shopify Definition ID: %s)",
        namespace, key, definition_id,
    )
    return {'created': True, 'definition_id': definition_id or None, 'reused': False, 'error': None}


def _metafield_error_index(error):
    """Extract the metafield input index a `metafieldsSet` userError refers to.

    Shopify returns userErrors with `field` like ["metafields", 0, "value"]; the
    middle element is the 0-based position in the metafields input array.
    """
    field = error.get('field') or []
    if not isinstance(field, list) or len(field) < 2 or field[0] != 'metafields':
        return None
    try:
        return int(field[1])
    except (TypeError, ValueError):
        return None


def _sanitize_metafield_value(value, mf_type):
    """Normalize a metafield value to satisfy Shopify's own `metafieldsSet`
    constraints for its type, so a value that was perfectly valid to *read*
    doesn't get rejected when pushed back as-is.

    `single_line_text_field` values may not contain a newline — some Shopify
    system metafields (e.g. `global.description_tag`, the SEO meta
    description) are themselves stored with a trailing newline, so pulling a
    value and pushing it back unmodified fails with "Value must be a single
    line text string."
    """
    value = value or ''
    if mf_type == 'single_line_text_field' and value:
        value = value.replace('\r\n', ' ').replace('\n', ' ').replace('\r', ' ').strip()
    return value


def push_product_metafields_to_shopify(account, shopify_product_id, metafield_lines, archived_lines=None):
    """Generic Shopify Metafields framework (and, via it, the Google Shopping
    fields — see `product_template.action_add_google_shopping_metafields`).

    Flow:
      1. Read the product's current metafields from Shopify (Product GraphQL
         query) so each Odoo line is reconciled against what actually exists.
      2. `metafieldsSet` per logical (ownerId, namespace, key): Shopify's
         MetafieldsSetInput has NO id/metafield_id identity field — it upserts
         on ownerId + namespace + key. An existing metafield is therefore
         UPDATED in place (never duplicated); a missing one is CREATED. Repeated
         pushes are idempotent by construction.
      3. The batch is chunked to Shopify's 25-metafield-per-request limit and
         `data.metafieldsSet.userErrors` are inspected per metafield — an HTTP
         200 alone is NOT treated as success.
      4. Pre-push validation detects namespace mismatches (the Odoo row may
         have a different namespace than the Shopify metafield for the same key)
         and BLOCKS the incorrect metafield instead of creating a duplicate
         (e.g. Odoo `customdd.brand` is blocked when Shopify has `custom.brand`).
      5. Any archived lines that correspond to existing Shopify metafields
         are explicitly deleted from Shopify.

    :param list metafield_lines: shopify.product.metafield recordset/list of dicts
        with namespace/key/type/value (the active lines to push).
    :param list archived_lines: shopify.product.metafield recordset/list of dicts
        for rows that are archived (deleted in Odoo).
    :return: dict {
        'success': {(namespace, key): shopify_metafield_id},
        'errors': [(namespace, key, message), ...],
    }
    """
    if not metafield_lines and not archived_lines:
        return {'success': {}, 'errors': []}

    owner_gid = const.GLOBAL_PRODUCT_ID + shopify_product_id

    # ------------------------------------------------------------------ #
    # 1) Fetch ALL existing Shopify metafields for this product           #
    # ------------------------------------------------------------------ #
    shopify_metafields = fetch_product_metafields_from_shopify(account, shopify_product_id)
    existing_by_ns_key = {(m['namespace'], m['key']): m['id'] for m in shopify_metafields}
    # Also build a key-only lookup so we can detect namespace mismatches:
    # the same key exists on Shopify under a DIFFERENT namespace than what Odoo has.
    existing_by_key_only = {m['key']: m for m in shopify_metafields}

    # ------------------------------------------------------------------ #
    # DELETE ARCHIVED METAFIELDS
    # ------------------------------------------------------------------ #
    deleted_map = {}
    if archived_lines:
        to_delete_ids = []
        ns_key_by_id = {}
        for arch in archived_lines:
            if isinstance(arch, dict):
                ns, key = arch.get('namespace'), arch.get('key')
            else:
                ns, key = arch.namespace, arch.key
            if (ns, key) in existing_by_ns_key:
                mf_id = existing_by_ns_key[(ns, key)]
                to_delete_ids.append(mf_id)
                ns_key_by_id[const.GLOBAL_METAFIELD_ID + mf_id] = (ns, key)
                _logger.info("Shopify metafield %s.%s was deleted from Odoo, marking for deletion on Shopify (ID: %s)", ns, key, mf_id)
            else:
                # Already deleted / does not exist on Shopify, mark as success
                deleted_map[(ns, key)] = True
        
        if to_delete_ids:
            try:
                deleted_gids = delete_metafield_from_shopify(account, to_delete_ids)
                if deleted_gids:
                    _logger.info("PRODUCT METAFIELD VALUE DELETED: Successfully deleted metafields from Shopify: %s", deleted_gids)
                    for gid in deleted_gids:
                        if gid in ns_key_by_id:
                            deleted_map[ns_key_by_id[gid]] = True
            except Exception as e:
                _logger.error("Failed to delete stale metafields from Shopify: %s", e)

    if not metafield_lines:
        return {'success': {}, 'errors': [], 'deleted': deleted_map}

    _logger.info(
        "PUSH PRE-FLIGHT — Product %s (gid=%s): fetched %d existing Shopify metafield(s). "
        "Existing (namespace, key) pairs: %s",
        shopify_product_id, owner_gid, len(shopify_metafields),
        ', '.join(f"{ns}.{k}" for ns, k in existing_by_ns_key),
    )
    # ------------------------------------------------------------------ #
    # 2) Build the metafieldsSet inputs with validation                   #
    # ------------------------------------------------------------------ #
    line_inputs = []
    errors = []
    for line in metafield_lines:
        # --- EXPLICIT PRE-PUSH LOG: every single metafield being sent ---
        _logger.info(
            "PUSH METAFIELD INPUT — Product %s: namespace=%r, key=%r, type=%r, value=%r, "
            "odoo_metafield_id=%s, odoo_shopify_metafield_id=%r",
            shopify_product_id, line.namespace, line.key, line.type,
            line.value, line.id, line.shopify_metafield_id,
        )

        sanitized_value = _sanitize_metafield_value(line.value, line.type)
        if not sanitized_value:
            # Shopify's metafieldsSet rejects a blank value outright ("Value
            # can't be blank") whether creating or updating — there is nothing
            # useful to send yet. Common for pre-filled-but-unedited rows (e.g.
            # action_add_google_shopping_metafields' empty placeholders); this
            # is expected, not an error, so it's skipped rather than reported
            # as a push failure.
            _logger.info(
                "PUSH METAFIELD SKIPPED (blank value) — Product %s: namespace=%r, key=%r",
                shopify_product_id, line.namespace, line.key,
            )
            continue

        input_data = {
            'owner_id': owner_gid,
            'namespace': line.namespace,
            'key': line.key,
            'type': line.type,
            'value': sanitized_value,
        }
        existing_id = existing_by_ns_key.get((line.namespace, line.key))

        if existing_id:
            # UPDATE PATH: Shopify metafield found with the SAME namespace + key.
            # metafieldsSet upserts on (ownerId, namespace, key), so we still send
            # owner_id/namespace/key — never an id. The existing Shopify metafield ID
            # is logged and later stored on the Odoo row for mapping/debugging only.
            _logger.info(
                "PUSH METAFIELD UPDATE — Product %s: Odoo %r.%r matches Shopify metafield %s — "
                "upserting (ownerId + namespace + key), no duplicate created. "
                "Payload: %r",
                shopify_product_id, line.namespace, line.key, existing_id,
                {k: v for k, v in input_data.items()},
            )
            line_inputs.append((line, input_data))
        else:
            # CREATE PATH: no (namespace, key) match found. Before creating a new
            # metafield, check for a same-key / different-namespace collision on
            # Shopify. If found, this is a NAMESPACE MISMATCH — the Odoo row and the
            # Shopify metafield are the SAME logical metafield under a different
            # namespace. We BLOCK the create (Fix 7 / Test 3) instead of silently
            # creating a second, incorrect metafield like `customdd.brand`.
            shopify_match_by_key = existing_by_key_only.get(line.key)
            if shopify_match_by_key:
                shopify_ns = shopify_match_by_key['namespace']
                shopify_mf_id = shopify_match_by_key['id']
                mismatch_msg = (
                    "NAMESPACE MISMATCH for key %r on product %s: "
                    "Odoo has namespace=%r but Shopify has namespace=%r "
                    "(Shopify metafield ID: %s, value: %r). "
                    "The push was BLOCKED — no metafield was created under namespace=%r. "
                    "To UPDATE the existing Shopify metafield, fix the Odoo namespace "
                    "from %r to %r in the product's Shopify Metafields tab, then push again. "
                    "The connector never modifies the namespace automatically."
                    % (
                        line.key, shopify_product_id,
                        line.namespace, shopify_ns, shopify_mf_id,
                        shopify_match_by_key.get('value'),
                        line.namespace,
                        line.namespace, shopify_ns,
                    )
                )
                _logger.error("PUSH METAFIELD NAMESPACE MISMATCH — %s", mismatch_msg)
                errors.append((line.namespace, line.key, mismatch_msg))
                # BLOCKED: do NOT add to line_inputs, do NOT send to Shopify.
                continue
            else:
                _logger.info(
                    "PUSH METAFIELD CREATE — Product %s: no existing Shopify metafield for "
                    "namespace=%r key=%r — creating new metafield. Payload: %r",
                    shopify_product_id, line.namespace, line.key,
                    {k: v for k, v in input_data.items()},
                )

            line_inputs.append((line, input_data))

    if errors:
        _logger.warning(
            "PUSH BLOCKED / PARTIAL — product %s: %d metafield(s) going to Shopify, "
            "%d blocked or failed: %s",
            shopify_product_id, len(line_inputs), len(errors),
            '; '.join("%s.%s: %s" % item for item in errors),
        )

    # ------------------------------------------------------------------ #
    # 3) Chunked metafieldsSet, with per-metafield userErrors inspection  #
    # ------------------------------------------------------------------ #
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    result_map = {}

    for start in range(0, len(line_inputs), METAFIELDS_SET_BATCH_LIMIT):
        chunk = line_inputs[start:start + METAFIELDS_SET_BATCH_LIMIT]
        mutation = _generate_metafields_set_mutation([input_data for _, input_data in chunk])

        # Log the FULL mutation for debugging (never log the access token).
        _logger.info(
            "PUSH API CALL — metafieldsSet batch %d–%d of %d for product %s (gid=%s)",
            start + 1, min(start + METAFIELDS_SET_BATCH_LIMIT, len(line_inputs)),
            len(line_inputs), shopify_product_id, owner_gid,
        )
        for _, input_data in chunk:
            _logger.info(
                "PUSH PAYLOAD — %s",
                {k: v for k, v in input_data.items() if k != 'owner_id'},
            )

        response = _call_shopify_graphql_admin_api(headers, request_url, mutation)

        # --- GRAPHQL-LEVEL ERROR (auth, invalid GID, scope, rate-limit) ---
        if response.get('errors'):
            messages = '; '.join(e.get('message') for e in response['errors'])
            for line, _input_data in chunk:
                errors.append((line.namespace, line.key, messages))
            _logger.error(
                "PUSH GRAPHQL ERRORS — product %s: %s", shopify_product_id, messages,
            )
            raise ECommerceApiError(f"Shopify metafieldsSet GraphQL error: {messages}")

        metafields_set = (response.get('data') or {}).get('metafieldsSet') or {}
        chunk_by_ns_key = {(line.namespace, line.key) for line, _ in chunk}

        # --- APPLICATION-LEVEL userErrors (per-metafield) ---
        for user_error in metafields_set.get('userErrors') or []:
            index = _metafield_error_index(user_error)
            if index is not None and index < len(chunk):
                line, _input_data = chunk[index]
                namespace, key = line.namespace, line.key
            else:
                namespace, key = '', ''
            message = "%s: %s%s" % (
                user_error.get('field') or 'metafields',
                user_error.get('message'),
                " [code=%s]" % user_error.get('code') if user_error.get('code') else '',
            )
            errors.append((namespace, key, message))
            _logger.error(
                "PUSH USER ERROR — product %s, namespace=%r, key=%r: %s",
                shopify_product_id, namespace, key, message,
            )

        # --- VALIDATE RETURNED METAFIELDS (namespace/key must match requested) ---
        for node in metafields_set.get('metafields') or []:
            returned_ns = node.get('namespace')
            returned_key = node.get('key')
            metafield_id = (node.get('id') or '').replace(const.GLOBAL_METAFIELD_ID, '')

            if returned_ns is not None and returned_key is not None:
                _logger.info(
                    "PUSH RESPONSE — Product %s: Shopify returned namespace=%r, key=%r, "
                    "metafield_id=%s",
                    shopify_product_id, returned_ns, returned_key, metafield_id,
                )
                result_map[(returned_ns, returned_key)] = metafield_id

                matching_line = next((l for l, _ in chunk if l.namespace == returned_ns and l.key == returned_key), None)
                product_name = matching_line.product_tmpl_id.display_name if matching_line and matching_line.product_tmpl_id else shopify_product_id
                val = matching_line.value if matching_line else ''
                if (returned_ns, returned_key) in existing_by_ns_key:
                    _logger.info(
                        "PRODUCT METAFIELD VALUE UPDATED: Product %s: %s.%s = %r (metafield_id=%s)",
                        product_name, returned_ns, returned_key, val, metafield_id,
                    )
                else:
                    _logger.info(
                        "PRODUCT METAFIELD VALUE CREATED: Product %s: %s.%s = %r (metafield_id=%s)",
                        product_name, returned_ns, returned_key, val, metafield_id,
                    )

                # Sanity check: the (namespace, key) pair IS the upsert identity we
                # sent metafieldsSet, so Shopify echoing back anything else for this
                # batch would be a genuine anomaly.
                if (returned_ns, returned_key) not in chunk_by_ns_key:
                    _logger.warning(
                        "PUSH RESPONSE UNRECOGNIZED — Product %s: Shopify returned "
                        "namespace=%r, key=%r (metafield_id=%s), which was not part "
                        "of this push batch.",
                        shopify_product_id, returned_ns, returned_key, metafield_id,
                    )

    if errors:
        _logger.warning(
            "PUSH SUMMARY — product %s: %d ok, %d issues: %s",
            shopify_product_id, len(result_map), len(errors),
            '; '.join("%s.%s: %s" % item for item in errors),
        )
    else:
        _logger.info(
            "PUSH SUMMARY — product %s: %d metafield(s) pushed/updated successfully.",
            shopify_product_id, len(result_map),
        )
    return {'success': result_map, 'errors': errors, 'deleted': deleted_map}


def delete_metafield_from_shopify(account, metafield_ids):
    """Explicitly delete metafields on Shopify via `metafieldsDelete`.

    Only called for an intentional Odoo-side metafield removal — stale rows found
    during a pull are archived in Odoo instead, never auto-deleted on Shopify.

    :param list metafield_ids: bare numeric Shopify metafield IDs.
    :return: list of deleted metafield GIDs.
    """
    if not metafield_ids:
        return []
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    deleted = []
    for start in range(0, len(metafield_ids), METAFIELDS_SET_BATCH_LIMIT):
        chunk = metafield_ids[start:start + METAFIELDS_SET_BATCH_LIMIT]
        gids = [const.GLOBAL_METAFIELD_ID + mf_id for mf_id in chunk]
        _log_metafield_request('metafieldsDelete', {
            'metafield_ids': ','.join(chunk),
        })
        mutation = _generate_metafields_delete_mutation(gids)
        response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
        if response.get('errors'):
            messages = '; '.join(e.get('message') for e in response['errors'])
            raise ECommerceApiError(f"Shopify metafieldsDelete error: {messages}")
        metafields_delete = (response.get('data') or {}).get('metafieldsDelete') or {}
        for user_error in metafields_delete.get('userErrors') or []:
            _logger.error(
                "Shopify metafieldsDelete error: %s: %s",
                user_error.get('field'), user_error.get('message'),
            )
        deleted.extend(
            node.get('id') for node in metafields_delete.get('deletedMetafields') or []
        )
    return deleted


def push_variant_market_price(account, price_list_id, variant_gid, price, compare_at_price, currency_code):
    """Shopify Markets: push a variant's per-market fixed price (see
    `_generate_price_list_fixed_prices_mutation`)."""
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    mutation = _generate_price_list_fixed_prices_mutation(
        price_list_id, variant_gid, price, compare_at_price, currency_code,
    )
    response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
    _handle_upsync_errors('priceListFixedPricesAdd', response)


def sync_product_option_linked_metafields(account, shopify_product_id, options_config):
    """Option Linked To — drift check for already-existing products.

    linkedMetafield can only be set when an option is first created (see
    `_build_product_options_input`). For a product that already exists on Shopify,
    this reads the option's current linkage and logs a warning if Odoo's
    `product.attribute.shopify_linked_metafield_namespace/key` configuration no
    longer matches, since there is no supported mutation to change it after the
    fact (see `_generate_product_options_query`).

    :param list options_config: [{'name': str, 'namespace': str, 'key': str}, ...]
        for the options that Odoo expects to be linked.
    """
    if not options_config:
        return
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    query = _generate_product_options_query(shopify_product_id)
    response = _call_shopify_graphql_admin_api(headers, request_url, query)
    shopify_options = {
        opt['name']: opt.get('linkedMetafield')
        for opt in ((response.get('data') or {}).get('product') or {}).get('options', [])
    }
    for expected in options_config:
        current = shopify_options.get(expected['name'])
        matches = current and current.get('namespace') == expected['namespace'] and current.get('key') == expected['key']
        if not matches:
            _logger.warning(
                "Shopify product %s option '%s' is not linked to metafield %s.%s in Shopify "
                "(current: %s). Shopify's API does not support relinking an existing option — "
                "this must be fixed manually in Shopify Admin (delete & recreate the option), "
                "or by deleting and recreating the product.",
                shopify_product_id, expected['name'], expected['namespace'], expected['key'], current,
            )


def _generate_publications_query():
    """Fetch the store's sales-channel publications (id + name).

    `Publication.name` is deprecated but remains the only field that reliably returns
    "Online Store" / "Point of Sale"; `catalog.title` is null for most channel
    publications. Both are requested so matching still works if either one is empty.
    """
    return """
    query GetPublications {
        publications(first: 50) {
            nodes {
                id
                name
                catalog {
                    title
                }
            }
        }
    }
    """


def _publication_names(node):
    """Return every name a publication node may be known by."""
    names = []
    if node.get('name'):
        names.append(node['name'])
    catalog_title = (node.get('catalog') or {}).get('title')
    if catalog_title:
        names.append(catalog_title)
    return names


def _handle_upsync_errors(endpoint, response):
    """Handle userErrors from Shopify GraphQL mutation response."""
    if not response:
        raise ECommerceApiError(f"Shopify {endpoint}: empty response received.")
    data = response.get('data') or {}
    endpoint_data = data.get(endpoint)
    if endpoint_data is None:
        # Shopify returned null for this endpoint — product may have been deleted
        errors = response.get('errors')
        if errors:
            raise ECommerceApiError(f"Shopify {endpoint} error: {errors}")
        raise ECommerceApiError(
            f"Shopify {endpoint} returned null — the product may no longer exist on Shopify. "
            f"Full response: {response}"
        )
    user_errors = endpoint_data.get('userErrors', [])
    if user_errors:
        messages = '; '.join([f"{e.get('field')}: {e.get('message')}" for e in user_errors])
        raise ECommerceApiError(f"Shopify {endpoint} error: {messages}")
    if 'errors' in response:
        raise ECommerceApiError(f"Shopify API error: {response['errors']}")


# === Shopify Collection Management === #

GLOBAL_COLLECTION_ID = "gid://shopify/Collection/"


def _format_shopify_gid(resource_type, raw_id):
    """Format an ID or GID to a full Shopify GID.

    e.g. ('Collection', '12345') -> 'gid://shopify/Collection/12345'
         ('Collection', 'gid://shopify/Collection/12345') -> 'gid://shopify/Collection/12345'
    """
    if not raw_id:
        return ''
    raw_str = str(raw_id).strip()
    prefix = f"gid://shopify/{resource_type}/"
    if raw_str.startswith(prefix):
        return raw_str
    bare_id = raw_str.split('/')[-1]
    return f"{prefix}{bare_id}"


def get_shopify_collection_by_id(account, collection_id):
    """Retrieve collection details from Shopify by ID or GID using collection(id: ...)."""
    if not collection_id:
        return None
    collection_gid = _format_shopify_gid('Collection', collection_id)
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    query = """
    query GetCollection {
        collection(id: %(id)s) {
            id
            title
            handle
            products(first: 250) {
                nodes {
                    id
                }
            }
        }
    }
    """ % {'id': _gql_string(collection_gid)}
    try:
        response = _call_shopify_graphql_admin_api(headers, request_url, query)
    except Exception as e:
        _logger.warning("Error fetching Shopify collection %s: %s", collection_gid, e)
        return None
    collection_data = (response.get('data') or {}).get('collection')
    if not collection_data:
        return None
    product_nodes = (collection_data.get('products') or {}).get('nodes') or []
    product_ids = [p['id'] for p in product_nodes if p and p.get('id')]
    return {
        'id': collection_data['id'],
        'title': collection_data.get('title', ''),
        'handle': collection_data.get('handle', ''),
        'product_ids': product_ids,
    }


def find_shopify_collection(account, title=None, handle=None):
    """Find a Shopify collection matching title or handle using collections(first: ..., query: ...)."""
    if not title and not handle:
        return None
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }

    search_terms = []
    if title:
        search_terms.append(f'title:"{title.strip()}"')
    if handle:
        search_terms.append(f'handle:"{handle.strip()}"')

    for search_term in search_terms:
        query = """
        query FindCollections {
            collections(first: 10, query: %(query)s) {
                nodes {
                    id
                    title
                    handle
                }
            }
        }
        """ % {'query': _gql_string(search_term)}
        try:
            response = _call_shopify_graphql_admin_api(headers, request_url, query)
        except Exception as e:
            _logger.warning("Error searching Shopify collections with query '%s': %s", search_term, e)
            continue
        nodes = ((response.get('data') or {}).get('collections') or {}).get('nodes') or []
        for node in nodes:
            node_title = (node.get('title') or '').strip().lower()
            node_handle = (node.get('handle') or '').strip().lower()
            if title and node_title == title.strip().lower():
                return {'id': node['id'], 'title': node.get('title', ''), 'handle': node.get('handle', '')}
            if handle and node_handle == handle.strip().lower():
                return {'id': node['id'], 'title': node.get('title', ''), 'handle': node.get('handle', '')}

    return None


def create_shopify_collection(account, title, handle=None):
    """Create a manual Shopify collection using collectionCreate."""
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }

    handle_str = f"handle: {_gql_string(handle)}" if handle else ""

    mutation = """
    mutation CreateCollection {
        collectionCreate(collection: {
            title: %(title)s
            %(handle)s
        }) {
            collection {
                id
                title
                handle
            }
            userErrors {
                field
                message
            }
        }
    }
    """ % {'title': _gql_string(title), 'handle': handle_str}

    response = _call_shopify_graphql_admin_api(headers, request_url, mutation)

    # Fallback to legacy 'input' argument if 'collection' argument is rejected
    errors = response.get('errors') or []
    if errors and any("doesn't accept argument 'collection'" in str(e) or "Field 'collection'" in str(e) for e in errors):
        legacy_mutation = """
        mutation CreateCollection {
            collectionCreate(input: {
                title: %(title)s
                %(handle)s
            }) {
                collection {
                    id
                    title
                    handle
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """ % {'title': _gql_string(title), 'handle': handle_str}
        response = _call_shopify_graphql_admin_api(headers, request_url, legacy_mutation)

    _handle_upsync_errors('collectionCreate', response)
    collection_data = (response.get('data') or {}).get('collectionCreate', {}).get('collection') or {}
    if not collection_data.get('id'):
        raise ECommerceApiError("Shopify collectionCreate succeeded but returned no collection ID.")
    return {
        'id': collection_data['id'],
        'title': collection_data.get('title', title),
        'handle': collection_data.get('handle', handle or ''),
    }


def get_product_shopify_collections(account, shopify_product_id):
    """Retrieve all collections a product belongs to on Shopify."""
    if not shopify_product_id:
        return []
    product_gid = _format_shopify_gid('Product', shopify_product_id)
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    query = """
    query GetProductCollections {
        product(id: %(id)s) {
            id
            collections(first: 50) {
                nodes {
                    id
                    title
                    handle
                }
            }
        }
    }
    """ % {'id': _gql_string(product_gid)}
    try:
        response = _call_shopify_graphql_admin_api(headers, request_url, query)
    except Exception as e:
        _logger.warning("Error querying collections for Shopify product %s: %s", product_gid, e)
        return []
    product_data = (response.get('data') or {}).get('product') or {}
    nodes = (product_data.get('collections') or {}).get('nodes') or []
    return [{'id': node['id'], 'title': node.get('title', ''), 'handle': node.get('handle', '')}
            for node in nodes if node and node.get('id')]


def add_product_to_shopify_collection(account, collection_id, shopify_product_id):
    """Add a product to a Shopify collection.

    Uses current recommended collectionUpdate with inclusion.selectionsToAdd,
    falling back to collectionAddProducts if needed.
    """
    collection_gid = _format_shopify_gid('Collection', collection_id)
    product_gid = _format_shopify_gid('Product', shopify_product_id)
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }

    mutation_update = """
    mutation CollectionUpdateAddProducts {
        collectionUpdate(collection: {
            id: %(collection_id)s
            sourcesToUpdate: [{
                inclusion: {
                    selectionsToAdd: {
                        productIds: [%(product_id)s]
                    }
                }
            }]
        }) {
            collection {
                id
            }
            userErrors {
                field
                message
            }
        }
    }
    """ % {
        'collection_id': _gql_string(collection_gid),
        'product_id': _gql_string(product_gid),
    }

    use_fallback = False
    try:
        response = _call_shopify_graphql_admin_api(headers, request_url, mutation_update)
        if response.get('errors'):
            use_fallback = True
        else:
            user_errors = (response.get('data') or {}).get('collectionUpdate', {}).get('userErrors', [])
            if user_errors:
                err_msg = ' '.join(e.get('message', '') for e in user_errors)
                if any(w in err_msg.lower() for w in ('already', 'exists', 'duplicate')):
                    return True
                use_fallback = True
            else:
                return True
    except Exception as ex:
        _logger.info("collectionUpdate add failed (%s); falling back to collectionAddProducts", ex)
        use_fallback = True

    if use_fallback:
        mutation_add = """
        mutation CollectionAddProducts {
            collectionAddProducts(
                id: %(collection_id)s
                productIds: [%(product_id)s]
            ) {
                collection {
                    id
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """ % {
            'collection_id': _gql_string(collection_gid),
            'product_id': _gql_string(product_gid),
        }
        response = _call_shopify_graphql_admin_api(headers, request_url, mutation_add)
        user_errors = (response.get('data') or {}).get('collectionAddProducts', {}).get('userErrors', [])
        if user_errors:
            err_msg = ' '.join(e.get('message', '') for e in user_errors)
            if any(w in err_msg.lower() for w in ('already', 'exists', 'duplicate')):
                return True
        _handle_upsync_errors('collectionAddProducts', response)

    return True


def remove_product_from_shopify_collection(account, collection_id, shopify_product_id):
    """Remove a product from a Shopify collection.

    Uses current recommended collectionUpdate with inclusion.selectionsToRemove,
    falling back to collectionRemoveProducts if needed.
    """
    collection_gid = _format_shopify_gid('Collection', collection_id)
    product_gid = _format_shopify_gid('Product', shopify_product_id)
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }

    mutation_update = """
    mutation CollectionUpdateRemoveProducts {
        collectionUpdate(collection: {
            id: %(collection_id)s
            sourcesToUpdate: [{
                inclusion: {
                    selectionsToRemove: {
                        productIds: [%(product_id)s]
                    }
                }
            }]
        }) {
            collection {
                id
            }
            userErrors {
                field
                message
            }
        }
    }
    """ % {
        'collection_id': _gql_string(collection_gid),
        'product_id': _gql_string(product_gid),
    }

    use_fallback = False
    try:
        response = _call_shopify_graphql_admin_api(headers, request_url, mutation_update)
        if response.get('errors'):
            use_fallback = True
        else:
            user_errors = (response.get('data') or {}).get('collectionUpdate', {}).get('userErrors', [])
            if user_errors:
                err_msg = ' '.join(e.get('message', '') for e in user_errors)
                if any(w in err_msg.lower() for w in ('not in collection', 'does not exist', 'not found')):
                    return True
                use_fallback = True
            else:
                return True
    except Exception as ex:
        _logger.info("collectionUpdate remove failed (%s); falling back to collectionRemoveProducts", ex)
        use_fallback = True

    if use_fallback:
        mutation_remove = """
        mutation CollectionRemoveProducts {
            collectionRemoveProducts(
                id: %(collection_id)s
                productIds: [%(product_id)s]
            ) {
                job {
                    id
                }
                userErrors {
                    field
                    message
                }
            }
        }
        """ % {
            'collection_id': _gql_string(collection_gid),
            'product_id': _gql_string(product_gid),
        }
        response = _call_shopify_graphql_admin_api(headers, request_url, mutation_remove)
        user_errors = (response.get('data') or {}).get('collectionRemoveProducts', {}).get('userErrors', [])
        if user_errors:
            err_msg = ' '.join(e.get('message', '') for e in user_errors)
            if any(w in err_msg.lower() for w in ('not in collection', 'does not exist', 'not found')):
                return True
        _handle_upsync_errors('collectionRemoveProducts', response)

    return True


# === Shopify Product Lookup & Handle Helpers === #

def get_shopify_product_details(account, shopify_product_id):
    """Fetch existing Shopify product details (handle, category, collections) by ID."""
    if not shopify_product_id:
        return {}
    product_gid = _format_shopify_gid('Product', shopify_product_id)
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    query = """
    query GetProductDetails {
        product(id: %(id)s) {
            id
            handle
            category {
                id
                name
                fullName
            }
            collections(first: 10) {
                nodes {
                    id
                    title
                    handle
                }
            }
        }
    }
    """ % {'id': _gql_string(product_gid)}
    try:
        response = _call_shopify_graphql_admin_api(headers, request_url, query)
        return (response.get('data') or {}).get('product') or {}
    except Exception as e:
        _logger.warning("Error fetching Shopify product details for %s: %s", product_gid, e)
        return {}


def get_shopify_product_handle(account, shopify_product_id):
    """Fetch existing Shopify product handle by ID."""
    details = get_shopify_product_details(account, shopify_product_id)
    return details.get('handle', '')


def find_shopify_product_by_handle_or_sku(account, template, offers=None):
    """Search Shopify for an existing product by stored handle, slugified handle, variant SKU, or title.

    Used before productCreate to prevent duplicate product creation and duplicate handles.
    """
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }

    search_queries = []

    # 1. Search by handle
    handles = []
    if template.shopify_handle:
        handles.append(template.shopify_handle.strip())
    slugified = slugify_handle(template.name)
    if slugified and slugified not in handles:
        handles.append(slugified)

    for h in handles:
        search_queries.append(('handle', h, f'handle:"{h}"'))

    # 2. Search by SKU / reference
    skus = []
    if offers:
        for o in offers:
            sku = o.sku or (o.matched_product_id.default_code if o.matched_product_id else '')
            if sku and sku not in skus:
                skus.append(sku.strip())
    if hasattr(template, 'product_variant_ids'):
        for p in template.product_variant_ids:
            if p.default_code and p.default_code.strip() not in skus:
                skus.append(p.default_code.strip())

    for sku in skus:
        search_queries.append(('sku', sku, f'sku:"{sku}"'))

    # 3. Search by title
    if template.name:
        search_queries.append(('title', template.name.strip(), f'title:"{template.name.strip()}"'))

    for search_type, target_val, search_term in search_queries:
        query = """
        query SearchProducts {
            products(first: 5, query: %(query)s) {
                nodes {
                    id
                    title
                    handle
                    category {
                        id
                        name
                        fullName
                    }
                    collections(first: 10) {
                        nodes {
                            id
                            title
                            handle
                        }
                    }
                    variants(first: 100) {
                        nodes {
                            id
                            sku
                            barcode
                            selectedOptions {
                                name
                                value
                            }
                            inventoryItem {
                                id
                            }
                        }
                    }
                }
            }
        }
        """ % {'query': _gql_string(search_term)}
        try:
            response = _call_shopify_graphql_admin_api(headers, request_url, query)
        except Exception as e:
            _logger.warning("Error searching Shopify products with query '%s': %s", search_term, e)
            continue

        nodes = ((response.get('data') or {}).get('products') or {}).get('nodes') or []
        for node in nodes:
            if not node or not node.get('id'):
                continue
            if search_type == 'handle':
                if (node.get('handle') or '').strip().lower() == target_val.lower():
                    return node
            elif search_type == 'sku':
                var_nodes = (node.get('variants') or {}).get('nodes') or []
                for v in var_nodes:
                    if (v.get('sku') or '').strip().lower() == target_val.lower():
                        return node
            elif search_type == 'title':
                if (node.get('title') or '').strip().lower() == target_val.lower():
                    return node

    return None


GLOBAL_PRODUCT_OPTION_ID = "gid://shopify/ProductOption/"


def get_shopify_product_options_and_variants(account, shopify_product_id):
    """Fetch product options and variants from Shopify Admin GraphQL API."""
    if not shopify_product_id:
        return {'options': [], 'variants': []}

    product_gid = _format_shopify_gid('Product', shopify_product_id)
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    query = """
    query GetProductOptionsAndVariants {
        product(id: %(id)s) {
            id
            handle
            options {
                id
                name
                position
                values
            }
            variants(first: 250) {
                nodes {
                    id
                    title
                    sku
                    barcode
                    selectedOptions {
                        name
                        value
                    }
                    inventoryItem {
                        id
                    }
                }
            }
        }
    }
    """ % {'id': _gql_string(product_gid)}

    try:
        response = _call_shopify_graphql_admin_api(headers, request_url, query)
        product = (response.get('data') or {}).get('product') or {}
        raw_options = product.get('options') or []
        options = []
        for opt in raw_options:
            if not opt:
                continue
            gid = opt.get('id') or ''
            numeric_id = gid.replace(GLOBAL_PRODUCT_OPTION_ID, '') if GLOBAL_PRODUCT_OPTION_ID in gid else gid.split('/')[-1]
            options.append({
                'id': gid,
                'numeric_id': numeric_id,
                'name': opt.get('name') or '',
                'position': opt.get('position') or 1,
                'values': opt.get('values') or [],
            })

        raw_variants = (product.get('variants') or {}).get('nodes') or []
        variants = []
        for var in raw_variants:
            if not var:
                continue
            gid = var.get('id') or ''
            inv_gid = ((var.get('inventoryItem') or {}).get('id')) or ''
            variants.append({
                'id': gid,
                'numeric_id': gid.replace(const.GLOBAL_PRODUCT_VARIANT_ID, ''),
                'title': var.get('title') or '',
                'sku': var.get('sku') or '',
                'barcode': var.get('barcode') or '',
                'selectedOptions': var.get('selectedOptions') or [],
                'inventory_item_id': inv_gid.replace(const.GLOBAL_INVENTORY_ITEM_ID, ''),
            })

        return {
            'id': product.get('id') or '',
            'handle': product.get('handle') or '',
            'options': options,
            'variants': variants,
        }
    except Exception as e:
        _logger.warning("Error fetching Shopify product options and variants for %s: %s", product_gid, e)
        return {'options': [], 'variants': []}


def delete_variants_from_shopify(account, shopify_product_id, shopify_variant_ids):
    """Delete specific variants from a Shopify product using productVariantsBulkDelete."""
    if not shopify_variant_ids:
        return True

    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    variant_gids = ', '.join([
        _gql_string(_format_shopify_gid('ProductVariant', vid))
        for vid in shopify_variant_ids if vid
    ])
    if not variant_gids:
        return True

    mutation = """
    mutation DeleteProductVariants {
        productVariantsBulkDelete(
            productId: "%(product_gid)s"
            variantsIds: [%(variant_gids)s]
        ) {
            userErrors {
                field
                message
            }
        }
    }
    """ % {
        'product_gid': const.GLOBAL_PRODUCT_ID + str(shopify_product_id),
        'variant_gids': variant_gids,
    }

    try:
        response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
        _handle_upsync_errors('productVariantsBulkDelete', response)
        _logger.info("Deleted %d variant(s) from Shopify product %s", len(shopify_variant_ids), shopify_product_id)
        return True
    except Exception as e:
        _logger.warning("Error deleting variants %s from Shopify product %s: %s", shopify_variant_ids, shopify_product_id, e)
        return False


def sync_product_options_on_shopify(account, shopify_product_id, odoo_options_detail, remote_options=None):
    """Synchronize Odoo product options with Shopify product options on an existing product."""
    if not odoo_options_detail:
        return

    if remote_options is None:
        info = get_shopify_product_options_and_variants(account, shopify_product_id)
        remote_options = info.get('options', [])

    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }

    # Case 1: Only default "Title" option exists on Shopify, but Odoo has real attributes
    if len(remote_options) == 1 and remote_options[0].get('name', '').lower() in ('title', 'default title'):
        create_opts = []
        for opt in odoo_options_detail:
            val_strs = ['{name: %s}' % _gql_string(v) for v in opt.get('values', []) if v]
            values_clause = f"values: [{', '.join(val_strs)}]" if val_strs else 'values: []'
            link_part = ''
            if opt.get('namespace') and opt.get('key'):
                link_part = 'linkedMetafield: {namespace: %s, key: %s}' % (
                    _gql_string(opt['namespace']), _gql_string(opt['key']),
                )
            create_opts.append('{name: %s %s %s}' % (_gql_string(opt['name']), values_clause, link_part))

        if create_opts:
            mutation = """
            mutation CreateProductOptions {
                productOptionsCreate(
                    productId: "%(product_gid)s"
                    options: [%(options)s]
                ) {
                    userErrors { field message }
                }
            }
            """ % {
                'product_gid': const.GLOBAL_PRODUCT_ID + str(shopify_product_id),
                'options': ', '.join(create_opts),
            }
            try:
                response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
                _handle_upsync_errors('productOptionsCreate', response)
            except Exception as e:
                _logger.warning("Error creating options on Shopify product %s: %s", shopify_product_id, e)
        return

    # Case 2: Shopify already has custom options
    remote_by_name = {opt.get('name', '').strip().lower(): opt for opt in remote_options}

    for odoo_opt in odoo_options_detail:
        opt_name = odoo_opt.get('name', '').strip()
        matched_remote = remote_by_name.get(opt_name.lower())

        if matched_remote:
            existing_values = {str(v).strip().lower() for v in matched_remote.get('values', [])}
            values_to_add = [
                v for v in odoo_opt.get('values', [])
                if str(v).strip().lower() not in existing_values
            ]
            if values_to_add:
                add_strs = ['{name: %s}' % _gql_string(v) for v in values_to_add]
                mutation = """
                mutation UpdateOptionValues {
                    productOptionUpdate(
                        productId: "%(product_gid)s"
                        option: {
                            id: "%(option_gid)s"
                            optionValuesToAdd: [%(values)s]
                        }
                    ) {
                        userErrors { field message }
                    }
                }
                """ % {
                    'product_gid': const.GLOBAL_PRODUCT_ID + str(shopify_product_id),
                    'option_gid': matched_remote['id'],
                    'values': ', '.join(add_strs),
                }
                try:
                    response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
                    _handle_upsync_errors('productOptionUpdate', response)
                except Exception as e:
                    _logger.warning("Error adding option values to %s on product %s: %s", opt_name, shopify_product_id, e)
        else:
            val_strs = ['{name: %s}' % _gql_string(v) for v in odoo_opt.get('values', []) if v]
            values_clause = f"values: [{', '.join(val_strs)}]" if val_strs else 'values: []'
            mutation = """
            mutation CreateSingleProductOption {
                productOptionsCreate(
                    productId: "%(product_gid)s"
                    options: [{
                        name: %(name)s
                        %(values)s
                    }]
                ) {
                    userErrors { field message }
                }
            }
            """ % {
                'product_gid': const.GLOBAL_PRODUCT_ID + str(shopify_product_id),
                'name': _gql_string(opt_name),
                'values': values_clause,
            }
            try:
                response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
                _handle_upsync_errors('productOptionsCreate', response)
            except Exception as e:
                _logger.warning("Error creating option %s on Shopify product %s: %s", opt_name, shopify_product_id, e)

    # Check for obsolete options (deleted from Odoo)
    odoo_opt_names = {opt.get('name', '').strip().lower() for opt in odoo_options_detail}
    obsolete_options = [
        opt for opt in remote_options
        if opt.get('name', '').strip().lower() not in odoo_opt_names
        and opt.get('name', '').strip().lower() not in ('title', 'default title')
    ]
    if obsolete_options:
        opt_ids = ', '.join([_gql_string(opt['id']) for opt in obsolete_options if opt.get('id')])
        mutation = """
        mutation DeleteProductOptions {
            productOptionsDelete(
                productId: "%(product_gid)s"
                options: [%(options)s]
            ) {
                userErrors { field message }
            }
        }
        """ % {
            'product_gid': const.GLOBAL_PRODUCT_ID + str(shopify_product_id),
            'options': opt_ids,
        }
        try:
            response = _call_shopify_graphql_admin_api(headers, request_url, mutation)
            _handle_upsync_errors('productOptionsDelete', response)
        except Exception as e:
            _logger.warning("Error deleting obsolete options on product %s: %s", shopify_product_id, e)


def match_and_reconcile_variants(offers, remote_variants, odoo_options=None):
    """Reconcile Odoo offers against Shopify remote variants.

    Returns (offers_to_update, offers_to_create, variants_to_delete)
    - offers_to_update: [(offer, remote_variant_dict), ...]
    - offers_to_create: [offer, ...]
    - variants_to_delete: [remote_variant_dict, ...]
    """
    if not remote_variants:
        offers_to_update = []
        offers_to_create = []
        for offer in offers:
            if offer.ec_product_identifier:
                offers_to_update.append((offer, {
                    'numeric_id': str(offer.ec_product_identifier),
                    'id': const.GLOBAL_PRODUCT_VARIANT_ID + str(offer.ec_product_identifier),
                    'inventory_item_id': str(offer.shopify_inventory_item_id or ''),
                }))
            else:
                offers_to_create.append(offer)
        return offers_to_update, offers_to_create, []

    matched_remote_ids = set()
    offers_to_update = []
    unmatched_offers = []

    remote_by_id = {}
    remote_by_sku = {}
    remote_by_options = {}
    remote_by_barcode = {}

    for rv in remote_variants:
        nid = rv.get('numeric_id')
        if nid:
            remote_by_id[str(nid)] = rv
            remote_by_id[rv.get('id', '')] = rv
        sku = (rv.get('sku') or '').strip().lower()
        if sku:
            remote_by_sku[sku] = rv
        barcode = (rv.get('barcode') or '').strip().lower()
        if barcode:
            remote_by_barcode[barcode] = rv
        sel_opts = rv.get('selectedOptions') or []
        if sel_opts:
            opt_key = '|'.join(sorted(
                f"{(o.get('name') or '').strip().lower()}:{(o.get('value') or '').strip().lower()}"
                for o in sel_opts
            ))
            if opt_key:
                remote_by_options[opt_key] = rv

    for offer in offers:
        matched_rv = None
        # 1. Match by ec_product_identifier
        if offer.ec_product_identifier:
            matched_rv = remote_by_id.get(str(offer.ec_product_identifier))

        # 2. Match by SKU
        if not matched_rv:
            offer_sku = (offer.sku or (offer.matched_product_id.default_code if offer.matched_product_id else '') or '').strip().lower()
            if offer_sku and offer_sku in remote_by_sku:
                candidate = remote_by_sku[offer_sku]
                if candidate.get('numeric_id') not in matched_remote_ids:
                    matched_rv = candidate

        # 3. Match by option values combination
        if not matched_rv and offer.matched_product_id:
            product = offer.matched_product_id
            template = product.product_tmpl_id
            if hasattr(template, 'attribute_line_ids') and template.attribute_line_ids:
                opt_pairs = []
                for al in template.attribute_line_ids:
                    attr_val = False
                    if hasattr(product, 'product_template_attribute_value_ids'):
                        matched = product.product_template_attribute_value_ids.filtered(
                            lambda v, a=al: v.attribute_id == a.attribute_id
                        )
                        attr_val = matched[:1]
                    if attr_val and attr_val.name:
                        opt_pairs.append(f"{al.attribute_id.name.strip().lower()}:{attr_val.name.strip().lower()}")
                if opt_pairs:
                    opt_key = '|'.join(sorted(opt_pairs))
                    candidate = remote_by_options.get(opt_key)
                    if candidate and candidate.get('numeric_id') not in matched_remote_ids:
                        matched_rv = candidate

        # 4. Match by barcode
        if not matched_rv and offer.matched_product_id:
            barcode = (offer.matched_product_id.barcode or '').strip().lower()
            if barcode and barcode in remote_by_barcode:
                candidate = remote_by_barcode[barcode]
                if candidate.get('numeric_id') not in matched_remote_ids:
                    matched_rv = candidate

        # 5. Single variant product fallback
        if not matched_rv and len(offers) == 1 and len(remote_variants) == 1:
            candidate = remote_variants[0]
            if candidate.get('numeric_id') not in matched_remote_ids:
                matched_rv = candidate

        if matched_rv:
            nid = matched_rv.get('numeric_id')
            if nid:
                matched_remote_ids.add(nid)
            offers_to_update.append((offer, matched_rv))
        else:
            unmatched_offers.append(offer)

    variants_to_delete = [
        rv for rv in remote_variants
        if rv.get('numeric_id') and rv.get('numeric_id') not in matched_remote_ids
    ]

    return offers_to_update, unmatched_offers, variants_to_delete

