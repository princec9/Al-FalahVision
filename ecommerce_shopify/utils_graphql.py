# Part of Odoo. See LICENSE file for full copyright and licensing details.


import logging
import re
from datetime import datetime
 
import pytz
import requests

from odoo.addons.ecommerce_shopify import const
from odoo.addons.odoo_ecommerce.utils import ECommerceApiError

TIMEOUT = 30
LIMIT = 10
MAX_LIMIT = 250  # used for fetch nested objects.

_logger = logging.getLogger(__name__)


# === Graphql Final Request === #
def _call_shopify_graphql_admin_api(headers, url, query):
    """Call shopify graphql api.

    :param dict headers: contains 2 keys
        - X-Shopify-Access-Token (str): access token of shopify account.
        - Content-Type (str): 'application/json'
    :param str url: Graphql API url in
        'https://{shopify_store_name}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json' format.
    :param str query: query for particular object.

    :return: json response of api
    :rtype: dict
    """
    try:
        response = requests.post(
            url=url,
            headers=headers,
            timeout=TIMEOUT,
            json={'query': query},
        )
        response.raise_for_status()
        _logger.info("Successfully executed request on %s", url)
    except (ValueError, requests.exceptions.ConnectionError, requests.exceptions.MissingSchema,
    requests.exceptions.Timeout, requests.exceptions.HTTPError, requests.exceptions.RequestException, requests.exceptions.InvalidURL) as ex:
        _logger.error("Error occurred during execute request on %s, error_description: %s", url, ex)
        raise ECommerceApiError(str(ex))
    dat = response.json()
    _logger.info("Dattttttttttttttttttttttttttttttta")
    _logger.info(dat)
    return  dat


# === Error Handling After Request === #
def _handle_query_error(response, url):
    """Handles errors that occur during a query to Shopify.

    :param dict response: The response received from Shopify, which may contain errors.
    :param str url: The URL from which the response was fetched.

    This method is called after each query is fetched from Shopify.
    If an error occurs while fetching data from Shopify, it is returned a list of errors under `errors` key,
    where each item is dictionary contain `message` key.
    """
    if response.get('errors'):
        error_message = ", ".join([error.get('message') for error in response.get('errors')])
        _logger.error("Error occurred after fetch data from %s, error_description: %s", url, error_message)
        raise ECommerceApiError(error_message)


def _handle_mutation_error(mutation_key, response, url):
    if not isinstance(response, dict):
        raise ECommerceApiError(
            f"Invalid response from Shopify mutation '{mutation_key}': {response}"
        )

    if 'errors' in response and response['errors']:
        error_message = ", ".join(
            e.get('message') for e in response['errors'] if e.get('message')
        )
        raise ECommerceApiError(error_message)

    if 'data' not in response:
        raise ECommerceApiError(
            f"No 'data' key in Shopify mutation response for '{mutation_key}'."
        )

    mutation_res = response['data'].get(mutation_key)

    if mutation_res is None:
        raise ECommerceApiError(
            f"Mutation key '{mutation_key}' missing in Shopify response."
        )

    user_errors = mutation_res.get('userErrors') or []

    if user_errors:
        error_message = ", ".join(
            err.get('message') for err in user_errors if err.get('message')
        )
        raise ECommerceApiError(error_message)

# === Prepare Request Query === #
def _generate_shopify_shop_query():
    shop_query = """
    query GetShop {
        shop {
            name
        }
    }
    """
    return shop_query


def _generate_shopify_locations_query(after):
    """Generate shopify location query

    :param str after: The pagination token used to fetch the next page.
    """
    location_query = """
    query GetLocations {
        locations(first: %s, %squery: \"active:true\") {
            edges {
                node {
                    id
                    name
                }
            }
            pageInfo {
                hasNextPage
                endCursor
            }
        }
    }
    """ % (LIMIT, f'after: \"{after}\", ' if after else '')
    return location_query


def _generate_shopify_products_query(updated_at_min=None, after=None):
    """Generate shopify products query

    :param str updated_at_min: The date after which the updated products should be fetched.

    :param str after: The pagination token used to fetch the next page.
    """
    arguments = [f'first: {LIMIT}']
    if after:
        arguments.append(f'after: "{after}"')
    if updated_at_min:
        # Shopify's search grammar requires timestamps containing colons to be
        # quoted; without the quotes it reports an "Invalid search field"
        # warning and ignores the filter.
        arguments.extend([
            'sortKey: UPDATED_AT',
            f'query: "updated_at:>\'{updated_at_min}\'"',
        ])

    product_query = """
    query GetProducts {
        products(%s) {
            edges {
                node {
                    id
                    title
                    handle
                    vendor
                    productType
                    tags
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
                    totalVariants
                    featuredImage {
                        id
                        url
                    }
                    media (first:%s) {
                        nodes {
                            ... on MediaImage {
                                id
                                image {
                                    url
                                    altText
                                }
                            }
                        }
                    }
                    variants (first:%s) {
                        nodes {
                            id
                            image {
                                url
                            }
                            inventoryItem {
                                id
                                tracked
                                measurement {
                                    weight {
                                        value
                                    }
                                }
                                unitCost {
                                    amount
                                }
                            }
                            price
                            sku
                            title
                            barcode
                        }
                    }
                    metafields (first:%s) {
                        nodes {
                            id
                            namespace
                            key
                            value
                            type
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
            pageInfo {
                hasNextPage
                endCursor
            }
        }
    }
    """ % (', '.join(arguments), MAX_LIMIT, MAX_LIMIT, MAX_LIMIT)
    return product_query


def _generate_shopify_order_by_id_query(order_id):
    """Generate shopify order by id query

    :param str order_id: The order identifier which is used to fetch particular order from shopify
    """
    order_query = """
    query GetOrder {
        order(id: \"%s\") {%s}
    }
    """ % (const.GLOBAL_ORDER_ID + order_id, _shopify_order_common_query())
    return order_query


def _generate_shopify_orders_query(updated_at_min, after):
    """Generate shopify order query

    :param str updated_at_min: The date after which the updated order should be fetched.

    :param str after: The pagination token used to fetch the next page.
    """
    # not needed to query filter status:any for fetch all orders, it's by default fetch all orders like canceled and successfull.
    order_query = """
    query GetOrders {
        orders(first: %s, sortKey: UPDATED_AT, %squery: \"updated_at:>\'%s\'\") {
            edges {
                node {%s}
            }
            pageInfo {
                hasNextPage
                endCursor
            }
        }
    }
    """ % (LIMIT, f'after: \"{after}\", ' if after else '', updated_at_min, _shopify_order_common_query())
    return order_query


def _shopify_order_common_query():
    """Generate common query for order which is used to fetch all orders as well as order by id."""
    order_query = """
    billingAddress {
        address1
        address2
        city
        countryCodeV2
        firstName
        lastName
        phone
        provinceCode
        zip
    }
    cancelledAt
    currencyCode
    createdAt
    customer {
        defaultAddress {
            address1
            address2
            city
            countryCodeV2
            firstName
            lastName
            phone
            provinceCode
            zip
        }
        defaultEmailAddress {
            emailAddress
        }
        id
    }
    customerJourneySummary {
        ready
        firstVisit {
            utmParameters {
                campaign
                medium
                source
            }
        }
        lastVisit {
            utmParameters {
                campaign
                medium
                source
            }
        }
    }
    displayFinancialStatus
    fulfillments(first: %s) {
        createdAt
        fulfillmentLineItems(first: %s) {
            nodes {
                id
                lineItem {
                    id
                }
                quantity
            }
        }
        id
        location {
            id
        }
        status
        trackingInfo(first: %s) {
            company
            number
        }
    }
    id
    lineItems (first:%s) {
        nodes {
            discountAllocations {
                allocatedAmountSet {
                    shopMoney {
                        amount
                    }
                }
            }
            id
            name
            originalUnitPriceSet {
                shopMoney {
                    amount
                }
            }
            quantity
            taxLines (first:%s) {
                priceSet {
                    shopMoney {
                        amount
                    }
                }
                rate
                title
            }
            title
            variant {
                id
                image {
                    url
                }
                inventoryItem {
                    id
                    tracked
                    measurement {
                        weight {
                            value
                        }
                    }
                    unitCost {
                        amount
                    }
                }
                price
                product {
                    id
                    title
                    totalVariants
                    featuredImage {
                        url
                    }
                }
                sku
                title
                barcode
            }
            variantTitle
        }
    }
    name
    shippingAddress {
        address1
        address2
        city
        countryCodeV2
        firstName
        lastName
        phone
        provinceCode
        zip
    }
    shippingLines (first:%s) {
        nodes {
            discountAllocations {
                allocatedAmountSet {
                    shopMoney {
                        amount
                    }
                }
            }
            id
            originalPriceSet {
                shopMoney {
                    amount
                }
            }
            taxLines {
                priceSet {
                    shopMoney {
                        amount
                    }
                }
                rate
                title
            }
            title
        }
    }
    taxesIncluded
    totalPriceSet {
        shopMoney {
            amount
        }
    }
    transactions (first: %s) {
        createdAt
        kind
        gateway
        status
    }
    """ % (MAX_LIMIT, MAX_LIMIT, MAX_LIMIT, MAX_LIMIT, MAX_LIMIT, MAX_LIMIT, MAX_LIMIT)
    return order_query


def _generate_shopify_inventory_item_query(shopify_inventory_item_id):
    """Generate shopify inventory item query

    :param str shopify_inventory_item_id: Shopify unique identifier of inventory item.
    """
    inventoryItems_query = """
    query GetInventoryItem {
        inventoryItem(id: \"%s\") {
            inventoryLevels (first: %s) {
                nodes {
                    location {
                        id
                    }
                    quantities (names: [\"on_hand\"]) {
                        quantity
                    }
                }
            }
        }
    }
    """ % (const.GLOBAL_INVENTORY_ITEM_ID + shopify_inventory_item_id, MAX_LIMIT)
    return inventoryItems_query


def _generate_shopify_fulfillmentCreate_query(picking, headers, request_url):
    """Generate shopify mutation query for push fulfillment.

    :param `stock.pikcing` picking: record set of stock.picking which will be updated.
    :param dict headers: headers, used to make query request for fulfillment order.
    :param str url: Graphql API url, used to make query request for fulfillment order.

    :return fulfillment_query: fulfillment query for push picking.
    :rtype: str.
    """

    # === Handle Fulfillment Order === #
    fulfillment_order_query = _generate_shopify_fulfillment_order_query(picking.ecommerce_order_identifier)
    fulfillment_order_response = _call_shopify_graphql_admin_api(headers, request_url, fulfillment_order_query)
    _handle_query_error(fulfillment_order_response, request_url)
    if not fulfillment_order_response['data']['order']:
        raise ECommerceApiError(f"No order exists on Shopify, related to id {picking.ecommerce_order_identifier}")

    # === Prepare Fulfillment Update Query === #
    mapped_shopify_line_id = {
        move_id.sale_line_id.ecommerce_line_identifier: int(move_id.quantity)
        for move_id in picking.move_ids
        if move_id.sale_line_id and
        move_id.sale_line_id.ecommerce_line_identifier
    }

    fulfillment_orders_data = fulfillment_order_response['data']['order']['fulfillmentOrders']['nodes']
    fulfillment_orders_query = []
    for fulfillment_order_data in fulfillment_orders_data:
        if fulfillment_order_data['status'] in ['CANCELLED', 'CLOSED', 'INCOMPLETE', 'ON_HOLD', 'SCHEDULED']:
            continue
        fulfillment_order_line_items_query = []
        fulfillment_line_items_data = fulfillment_order_data['lineItems']['nodes']
        for fulfillment_line_item_data in fulfillment_line_items_data:
            order_line_item_id = fulfillment_line_item_data['lineItem']['id'].replace(const.GLOBAL_LINE_ITEM_ID, '')
            if order_line_item_id in mapped_shopify_line_id:
                order_line_item_quantity = mapped_shopify_line_id[order_line_item_id]
                fulfillment_order_line_items_query.append('{id: \"%s\" quantity: %s}' % (fulfillment_line_item_data['id'], order_line_item_quantity))
        if fulfillment_order_line_items_query:
            fulfillment_orders_query.append('{fulfillmentOrderId: \"%s\" fulfillmentOrderLineItems: [%s]}' % (fulfillment_order_data['id'], ''.join(fulfillment_order_line_items_query)))

    fulfillment_tracking_info_query = 'trackingInfo: {'
    tracking_company = (
        const.SHOPIFY_CARRIER_NAMES_MAPPING.get(picking.carrier_id.name)
        or const.SHOPIFY_CARRIER_NAMES_MAPPING.get(picking.carrier_id.delivery_type)
        or 'Other'
    )  # If either the delivery_type of the picking's carrier_id or the name of the picking's carrier_id exists on Shopify, then set it as the company; otherwise, set the company as Other.
    fulfillment_tracking_info_query += f'company: \"{tracking_company}\"'
    fulfillment_tracking_info_query += f'number: \"{picking.carrier_tracking_ref}\"'
    if picking.carrier_tracking_url:
        fulfillment_tracking_info_query += f'url: \"{picking.carrier_tracking_url}\"'
    fulfillment_tracking_info_query += '}'

    fulfillment_push_query = """
    mutation createFulfillment {
        fulfillmentCreate (
            fulfillment: {
                lineItemsByFulfillmentOrder: [%s]
                %s
            }
        ) {
            fulfillment {
                id
            }
            userErrors {
                message
            }
        }
    }
    """ % (''.join(fulfillment_orders_query), fulfillment_tracking_info_query)

    return fulfillment_push_query


def _generate_shopify_fulfillment_order_query(order_id):
    """Generate shopify fulfillment order query

    :param str order_id: Shopify unique identifier of order.
    """

    order_query = """
    query GetOrder {
        order(id: \"%s\") {
            fulfillmentOrders (first:%s) {
                nodes {
                    id
                    lineItems (first:%s) {
                        nodes {
                            id
                            lineItem {
                                id
                            }
                        }
                    }
                    status
                }
            }
        }
    }
    """ % (const.GLOBAL_ORDER_ID + order_id, MAX_LIMIT, MAX_LIMIT)
    return order_query


def _generate_shopify_inventorySetQuantities_query(inventory_data):
    """Generate shopify inventory update query

    :param list inventory_data: Inventory data that will be updated on Shopify. Each item includes:
        - offer (ecommerce.offer): The offer whose quantity will be updated.
        - location (ecommerce.location): The location at which the quantity should be updated.
        - quantity (int): The product quantity.
    """
    quantities_data = []
    for inventory_data in inventory_data:
        quantities_data.append("""
        {
            inventoryItemId: \"%s\"
            locationId: \"%s\"
            quantity: %s
        }
        """ % (
            const.GLOBAL_INVENTORY_ITEM_ID + inventory_data['offer'].shopify_inventory_item_id,
            const.GLOBAL_LOCATION_ID + inventory_data['location'].ecommerce_location_identifier,
            int(inventory_data['quantity'])
            )
        )
    inventory_query = """
    mutation InventoryUpdate {
        inventorySetQuantities(
            input: {
                name: \"available\"
                reason: \"correction\"
                ignoreCompareQuantity: true
                quantities: [%s]
            }
        ){
            userErrors {
                message
            }
        }
    }
    """ % ''.join(quantities_data)
    # name = available, which is used to set absolute quantity
    # reason = correction, Used to correct an inventory error or as a general adjustment reason.
    return inventory_query


def _generate_shopify_webhookSubscriptionCreate_query(uri, topic):
    """Generate Shopify webhook subscription create mutation query.

    :param str uri: Public webhook callback URL.
    :param str topic: Shopify webhook topic.
    """
    webhook_query = """
    mutation webhookSubscriptionCreate {
        webhookSubscriptionCreate(
            topic: %s,
            webhookSubscription: {
                uri: \"%s\",
                format: JSON
            }
        ) {
            webhookSubscription {
                id
                topic
                format
                uri
            }
            userErrors {
                field
                message
            }
        }
    }
    """ % (topic, uri)
    _logger.info("webhook_query1111111111111111111111")
    _logger.info(webhook_query)
    return webhook_query

# === Make Query Request === #
def call_shopify_graphql_query(account, endpoint, params={}):
    """Call Shopify GraphQL Admin API for query(fetch data).

    :param account: record of `ecommerce.account`
    :param str endpoint: objects for mutation on shopify.
        valid values:
        - 'shop'  : Fetch shop data for authentication.
        - 'order' : Fetch order by identifier.

    :return response: response from shopify
    :rtype dict:
    """
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    query = ''
    if endpoint == 'shop':
        query = _generate_shopify_shop_query()
    elif endpoint == 'order':
        order_id = params.get('order_id')
        query = _generate_shopify_order_by_id_query(order_id)
    response = _call_shopify_graphql_admin_api(headers, request_url, query)
    _handle_query_error(response, request_url)
    # Ensure the response contains a 'data' key.
    if not isinstance(response, dict) or 'data' not in response:
        raise ECommerceApiError(
            f"Unexpected GraphQL response structure from {endpoint}: {response}"
        )
    res = response['data']
    if endpoint == 'order':
        if not res.get('order'):
            raise ECommerceApiError(f"No order exists on Shopify, related to {order_id}")
        order = _shopify_prepare_order_structure(res['order'])
        return order
    return res


# === Make Pagination Request === #
def call_shopify_graphql_with_pagination(account, endpoint, params={}):
    """Call Shopify GraphQL Admin API with cursor-based pagination.

    :param account: record of `ecommerce.account`
    :param str endpoint: Resource to fetch from Shopify.
        valid values:
        - 'locations' : Fetch locations from shopify.
        - 'products'  : Fetch products from shopify.
        - 'orders'    : Fetch orders from shopify.

    :return response: response from shopify
    :rtype dict:
    """
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    result_data = []
    has_next_page = True
    next_page = ''
    while has_next_page:
        query_params = {'after': next_page}
        query_params.update(params)
        query = globals()[f'_generate_shopify_{endpoint}_query'](**query_params)
        response = _call_shopify_graphql_admin_api(headers, request_url, query)
        _handle_query_error(response, request_url)
        edges = response['data'][endpoint]['edges']
        result_data.extend(globals()[f'_shopify_prepare_{endpoint}_structure'](edges))
        has_next_page = response['data'][endpoint]['pageInfo']['hasNextPage']
        next_page = response['data'][endpoint]['pageInfo']['endCursor']
    return {endpoint: result_data}


# === Make Mutation Request === #
def call_shopify_graphql_mutation(account, endpoint, params={}):
    """Call Shopify GraphQL Admin API for mutation.

    :param account: record of `ecommerce.account`
    :param str endpoint: objects for mutation on shopify.
        valid values:
        - 'fulfillmentCreate'      : For create fulfillment on shopify.
        - 'inventorySetQuantities' : For update inventory on shopify.
        - 'webhookSubscriptionCreate' : For create webhook subscription on shopify.

    :return response: response from shopify
    :rtype dict:
    """
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    request_query = ''
    if endpoint == 'fulfillmentCreate':
        picking = params.get('picking')
        request_query = _generate_shopify_fulfillmentCreate_query(picking, headers, request_url)
    elif endpoint == 'inventorySetQuantities':
        inventory_data = params.get('inventory_data')
        request_query = _generate_shopify_inventorySetQuantities_query(inventory_data)
    elif endpoint == 'webhookSubscriptionCreate':
        uri = params.get('uri')
        topic = params.get('topic')
        request_query = _generate_shopify_webhookSubscriptionCreate_query(uri, topic)
    response = _call_shopify_graphql_admin_api(headers, request_url, request_query)
    _logger.debug('Shopify mutation response for %s: %s', endpoint, response)
    _handle_mutation_error(endpoint, response, request_url)
    # Ensure response structure is as expected
    if not isinstance(response, dict) or 'data' not in response:
        raise ECommerceApiError(
            f"Unexpected GraphQL mutation response structure for {endpoint}: {response}"
        )
    res = {}
    if endpoint == 'fulfillmentCreate':
        res.update({'id': response['data'][endpoint]['fulfillment']['id'].replace(const.GLOBAL_FULFILLMENT_ID, '')})
    elif endpoint == 'webhookSubscriptionCreate':
        # Safely extract webhookSubscription
        webhook_data = response['data'][endpoint].get('webhookSubscription')
        res = webhook_data or {}
    return res


# === Make Inventory Specific Query Request === #
def call_shopify_graphql_fetch_inventory(account):
    """Call Shopify GraphQL Admin API for fetch inventory data.

    :param account: record of `ecommerce.account`

    :return inventory_data: response from shopify.
    :rtype dict:
    """
    request_url = f"https://{account.shopify_store}.myshopify.com/admin/api/{const.SHOPIFY_API_VERSION}/graphql.json"
    headers = {
        'X-Shopify-Access-Token': account.shopify_access_token,
        'Content-Type': 'application/json',
    }
    offers = account.ecommerce_offer_ids.filtered(lambda r: r.shopify_inventory_item_id and r.sync_stock)
    res = []
    for offer in offers:
        inventory_item_query = _generate_shopify_inventory_item_query(offer.shopify_inventory_item_id)
        response = _call_shopify_graphql_admin_api(headers, request_url, inventory_item_query)
        _handle_query_error(response, request_url)
        inventory_levels = ((response.get('data') or {}).get('inventoryItem') or {}).get('inventoryLevels') or {}
        res.extend(_shopify_prepare_inventory_structure(inventory_levels.get('nodes') or [], offer))
    return {'inventory': res}


# === Prepare Response Structure === #
def _shopify_prepare_locations_structure(edges):
    locations_data = []
    for edge in edges:
        node = edge['node']
        locations_data.append({
            'id': node['id'].replace(const.GLOBAL_LOCATION_ID, ''),
            'name': node['name'],
        })
    return locations_data


def _shopify_prepare_product_images_structure(product_node):
    """Prepare the full Shopify media gallery of a product.

    Only `MediaImage` nodes carry an `image { url }`; other media types (video,
    3D model, ...) resolve the inline fragment to an empty dict and are skipped.

    :param dict product_node: The `product` GraphQL node, containing a `media` connection.
    :return: list of dicts with 'shopify_media_id', 'url' and 'alt' keys, in Shopify's display order.
    :rtype: list
    """
    images_data = []
    for media_node in (product_node.get('media') or {}).get('nodes') or []:
        image = media_node.get('image') or {}
        url = image.get('url')
        if not url:
            continue
        images_data.append({
            'shopify_media_id': (media_node.get('id') or '').replace(const.GLOBAL_MEDIA_IMAGE_ID, ''),
            'url': url,
            'alt': image.get('altText') or '',
        })
    return images_data


def _shopify_prepare_products_structure(edges):
    products_data = []
    for edge in edges:
        product_node = edge['node']
        is_single_product = product_node.get('totalVariants') == 1 if product_node.get('totalVariants') else False
        images_data = _shopify_prepare_product_images_structure(product_node)
        # Product-level metafields (product owner), carried on every variant's data
        # so Odoo can mirror them onto the product.template. `metafields_has_next_page`
        # tells the upsync layer to do a full paginated re-fetch instead of trusting
        # this first page.
        metafields_conn = product_node.get('metafields') or {}
        metafields_data = [{
            'id': (node.get('id') or '').replace('gid://shopify/Metafield/', ''),
            'namespace': node.get('namespace'),
            'key': node.get('key'),
            'value': node.get('value'),
            'type': node.get('type'),
            'created_at': node.get('createdAt'),
            'updated_at': node.get('updatedAt'),
        } for node in metafields_conn.get('nodes') or [] if (node.get('id') or '').startswith('gid://shopify/Metafield/')]
        metafields_has_next_page = bool((metafields_conn.get('pageInfo') or {}).get('hasNextPage'))
        cat_node = product_node.get('category') or {}
        cat_gid = cat_node.get('id') if isinstance(cat_node, dict) else (cat_node or '')
        col_nodes = (product_node.get('collections') or {}).get('nodes') or []
        col_gid = col_nodes[0].get('id') if col_nodes else ''
        vendor = product_node.get('vendor') or ''
        product_type = product_node.get('productType') or ''
        tags = product_node.get('tags') or []
        for variant in product_node['variants']['nodes']:
            variant_id = (variant.get('id') or '').replace(const.GLOBAL_PRODUCT_VARIANT_ID, '')
            if not variant_id:
                _logger.warning(
                    "Skipping Shopify variant of product '%s' — it has no variant ID.",
                    product_node.get('title'),
                )
                continue
            sku = variant.get('sku') or variant_id
            if not variant.get('sku'):
                _logger.info(
                    "Shopify variant %s of product '%s' has no SKU; using its variant ID as the Odoo internal reference.",
                    variant_id, product_node.get('title'),
                )
            InventoryItem = variant.get('inventoryItem')
            image_url = ''
            image_shopify_media_id = ''
            if is_single_product:
                featured_image = product_node.get('featuredImage')
                image_url = featured_image.get('url', '') if featured_image else ''
                # Carried through so the main image (product.template.image_1920)
                # can be matched by id, the same way gallery rows already are —
                # without it, the main image can only be reconciled by content
                # hash on its first push, and a hash miss against visually
                # identical-but-byte-different Shopify media creates a permanent
                # duplicate (see ecommerce_shopify_upsync's push-side fix).
                image_shopify_media_id = (
                    (featured_image or {}).get('id') or ''
                ).replace(const.GLOBAL_MEDIA_IMAGE_ID, '')
            else:
                image_url = variant.get('image').get('url', '') if variant.get('image') else ''
            products_data.append({
                'shopify_inventory_item_id': InventoryItem.get('id').replace(const.GLOBAL_INVENTORY_ITEM_ID, ''),
                'sku': sku,
                'name': product_node['title'] if variant['title'] == 'Default Title' else f"{product_node['title']} ({variant['title']})",
                'ec_product_identifier': variant_id,
                'ec_product_template_identifier': product_node['id'].replace(const.GLOBAL_PRODUCT_ID, ''),
                'vendor': vendor,
                'variant_data': {
                    'list_price': float(variant.get('price') or 0.0),
                    'standard_price': float((InventoryItem.get('unitCost') or {}).get('amount') or 0.0),
                    'weight': float(InventoryItem.get('measurement').get('weight').get('value') or 0.0),
                    'image_url': image_url,
                    'image_shopify_media_id': image_shopify_media_id,
                    'images': images_data,
                    'barcode': variant.get('barcode') or '',
                    'is_storable': InventoryItem.get('tracked') or False,
                    'metafields': metafields_data,
                    'metafields_has_next_page': metafields_has_next_page,
                    'handle': product_node.get('handle', ''),
                    'shopify_category_id': cat_gid,
                    'shopify_collection_id': col_gid,
                    'shopify_collections': col_nodes,
                    'vendor': vendor,
                    'product_type': product_type,
                    'tags': tags,
                },
            })
    return products_data


def _shopify_prepare_orders_structure(edges):
    orders_data = []
    for edge in edges:
        node = edge['node']
        orders_data.append(_shopify_prepare_order_structure(node))
    return orders_data


def _shopify_prepare_order_structure(order_node):
    """Prepare shopify order structure from order data returned by shopify."""
    billing_address = order_node.get('billingAddress') or (order_node.get('customer') or {}).get('defaultAddress')
    email = ((order_node.get('customer') or {}).get('defaultEmailAddress') or {}).get('emailAddress')
    shipping_address = order_node.get('shippingAddress')
    transactions = order_node.get('transactions', [])
    sale_transactions = [transaction for transaction in transactions if transaction.get('kind') in ['SALE', 'CAPTURE']]
    payment_provider_codes = [transaction.get('gateway', '') for transaction in sale_transactions]

    order = {
        'billing_address': {
            'city': billing_address.get('city'),
            'country_code': billing_address.get('countryCodeV2'),
            'email': email,
            'name': ((billing_address.get('firstName') or '') + ' ' + (billing_address.get('lastName') or '')).strip(),
            'phone': billing_address.get('phone'),
            'state_code': billing_address.get('provinceCode'),
            'street': billing_address.get('address1'),
            'street2': billing_address.get('address2'),
            'zip': billing_address.get('zip'),
        } if billing_address else {},
        'currency_code': order_node.get('currencyCode'),
        'customer_id': (order_node.get('customer') or {}).get('id', '').replace(const.GLOBAL_CUSTOMER_ID, ''),
        'financial_status': order_node.get('displayFinancialStatus'),
        'date_order': convert_iso_to_utc(order_node.get('createdAt')),
        'fulfillments': _shopify_prepare_fulfillments_structure(order_node.get('fulfillments')),
        'id': order_node.get('id').replace(const.GLOBAL_ORDER_ID, ''),
        'invoice_date': _get_invoice_date_from_transactions(order_node.get('transactions')),
        'order_lines': _shopify_prepare_order_lines_structure(order_node.get('lineItems').get('nodes')),
        'reference': order_node.get('name'),
        'shipping_address': {
            'city': shipping_address.get('city'),
            'country_code': shipping_address.get('countryCodeV2'),
            'email': email,
            'name': ((shipping_address.get('firstName') or '') + ' ' + (shipping_address.get('lastName') or '')).strip(),
            'phone': shipping_address.get('phone'),
            'state_code': shipping_address.get('provinceCode'),
            'street': shipping_address.get('address1'),
            'street2': shipping_address.get('address2'),
            'zip': shipping_address.get('zip'),
        } if shipping_address else {},
        'shipping_lines': _shopify_prepare_shipping_lines_structure(order_node.get('shippingLines').get('nodes')),
        'status': 'canceled' if order_node.get('cancelledAt') else 'confirmed',
        'payment_provider_codes': payment_provider_codes,
        'tax_included': order_node.get('taxesIncluded'),
        'total_amount': float(order_node.get('totalPriceSet').get('shopMoney').get('amount') or 0.0),
        'utm_data': _shopify_prepare_utm_data(order_node.get('customerJourneySummary')),
    }
    return order


def _shopify_prepare_fulfillments_structure(fulfillments_data):
    """Prepare shopify fulfillments structure from fulfillments data returned by shopify."""
    fulfillments = []
    for fulfillment_data in fulfillments_data:
        tracking_company = ((fulfillment_data.get('trackingInfo') and fulfillment_data.get('trackingInfo')[0] or {}).get('company') or '')
        if tracking_company:
            tracking_company = re.sub(r"\s+", "", tracking_company)
            tracking_company = re.sub(r"\(", "_", tracking_company)
            tracking_company = re.sub(r"\)(?!$)", "_", tracking_company)
            tracking_company = re.sub(r"\)$", "", tracking_company).lower()
        fulfillment = {
            'date_done': convert_iso_to_utc(fulfillment_data.get('createdAt')),
            'carrier_id': tracking_company,
            'ecommerce_picking_identifier': fulfillment_data.get('id').replace(const.GLOBAL_FULFILLMENT_ID, ''),
            'location_id': fulfillment_data.get('location').get('id').replace(const.GLOBAL_LOCATION_ID, ''),
            'status': 'canceled' if fulfillment_data.get('status') in ['CANCELLED', 'ERROR', 'FAILURE'] else 'confirmed',
            'tracking_number': (fulfillment_data.get('trackingInfo') and fulfillment_data.get('trackingInfo')[0] or {}).get('number'),
        }
        line_items = []
        for line_item in fulfillment_data.get('fulfillmentLineItems').get('nodes'):
            data = {
                'ecommerce_line_identifier': line_item.get('lineItem').get('id').replace(const.GLOBAL_LINE_ITEM_ID, ''),
                'ecommerce_move_identifier': line_item.get('id').replace(const.GLOBAL_FULFILLMENT_LINE_ITEM_ID, ''),
                'quantity': line_item.get('quantity'),
            }
            line_items.append(data)
        fulfillment['line_items'] = line_items
        fulfillments.append(fulfillment)
    return fulfillments


def _get_invoice_date_from_transactions(transactions):
    """Get invoice date from transactions data returned by shopify."""
    transactions.reverse()  # Reverse the transactions to get the latest transaction with kind 'CAPTURE' or 'SALE'
    for transaction in transactions:
        if transaction.get('kind') in ['CAPTURE', 'SALE'] and transaction.get('status') == 'SUCCESS':
            return convert_iso_to_utc(transaction.get('createdAt'))


def _shopify_prepare_order_lines_structure(line_nodes):
    """Prepare shopify order line structure from line item data returned by shopify."""
    order_lines = []
    for line_node in line_nodes:
        product_variant = line_node.get('variant') or {}
        product = product_variant.get('product') or {}
        variant_id = (product_variant.get('id') or '').replace(const.GLOBAL_PRODUCT_VARIANT_ID, '')
        # `ecommerce.offer.sku` is required in Odoo, while Shopify permits an
        # empty SKU.  The immutable Shopify variant ID is unique and is already
        # carried as the offer's e-commerce identifier, making it a safe
        # fallback internal reference for order-created offers as well.
        sku = product_variant.get('sku') or variant_id
        is_single_product = product.get('totalVariants') == 1 if product.get('totalVariants') else False
        if is_single_product:
            image_url = product.get('featuredImage').get('url', '') if product.get('featuredImage') else ''
        else:
            image_url = product_variant.get('image').get('url', '') if product_variant.get('image') else ''
        inventory_item = product_variant.get('inventoryItem') or {}
        order_line = {
            'description': line_node.get('name'),
            'discount_amount': _calculate_shopify_discount(line_node.get('discountAllocations')),
            'id': line_node.get('id').replace(const.GLOBAL_LINE_ITEM_ID, ''),
            'price_unit': line_node.get('originalUnitPriceSet').get('shopMoney').get('amount'),
            'product_data': {
                'shopify_inventory_item_id': inventory_item.get('id').replace(const.GLOBAL_INVENTORY_ITEM_ID, ''),
                'sku': sku,
                'name': (line_node['title'] + ' (' + line_node.get('variantTitle') + ')') if line_node.get('variantTitle') else line_node['title'],
                'ec_product_identifier': variant_id,
                'ec_product_template_identifier': product.get('id').replace(const.GLOBAL_PRODUCT_ID, ''),
                'variant_data': {
                    'list_price': float(product_variant.get('price') or 0.0),
                    'standard_price': float((inventory_item.get('unitCost') or {}).get('amount') or 0.0),
                    'weight': float(inventory_item.get('measurement').get('weight').get('value') or 0.0),
                    'image_url': image_url,
                    'barcode': product_variant.get('barcode') or '',
                    'is_storable': inventory_item.get('tracked') or False,
                },
            } if product_variant else {},
            'qty_ordered': line_node.get('quantity', 0),
            'tax_amount': _calculate_shopify_tax(line_node.get('taxLines')),
        }
        order_lines.append(order_line)
    return order_lines


def _shopify_prepare_shipping_lines_structure(line_nodes):
    """Prepare shopify shipping line structure from shipping line data returned by shopify."""
    shipping_lines = []
    for line_node in line_nodes:
        shipping_line = {
            'description': line_node.get('title'),
            'discount_amount': _calculate_shopify_discount(line_node.get('discountAllocations')),
            'id': 'shopify_ship_' + str(line_node.get('id').replace(const.GLOBAL_SHIPPING_LINE_ID, '')),
            'price_unit': line_node.get('originalPriceSet').get('shopMoney').get('amount'),
            'shipping_code': line_node.get('title'),
            'tax_amount': _calculate_shopify_tax(line_node.get('taxLines')),
        }
        shipping_lines.append(shipping_line)
    return shipping_lines


def _shopify_prepare_utm_data(journey):
    utm_data = {}
    if journey and journey.get('ready'):
        first_visit = journey.get('firstVisit') or {}
        utm_params = first_visit.get('utmParameters') or {}
        if not utm_params:
            last_visit = journey.get('lastVisit') or {}
            utm_params = last_visit.get('utmParameters') or {}
        for key in ('source', 'medium', 'campaign'):
            val = utm_params.get(key)
            if val:
                utm_data[key] = val
    return utm_data


def _shopify_prepare_inventory_structure(inventory_levels, offer):
    inventory_data = []
    for inventory_level in inventory_levels:
        inventory_vals = {
            'ecommerce_location_identifier': inventory_level.get('location', {}).get('id', '').replace(const.GLOBAL_LOCATION_ID, ''),
            'sku': offer.sku,
        }
        if inventory_level.get('quantities'):
            inventory_vals.update({
                'quantity': inventory_level.get('quantities')[0].get('quantity', 0),
            })
        inventory_data.append(inventory_vals)
    return inventory_data


# === Helper methods === #
def _calculate_shopify_tax(tax_lines):
    total_tax = 0
    for line in tax_lines:
        total_tax += float(line.get('priceSet', {}).get('shopMoney', {}).get('amount', 0))

    return total_tax


def _calculate_shopify_discount(discount_lines):
    total_discount = 0
    for line in discount_lines:
        total_discount += float(line.get('allocatedAmountSet').get('shopMoney').get('amount'))
    return total_discount


def convert_iso_to_utc(iso_date):
    """Converts Shopify ISO 8601 date to Odoo datetime format"""
    try:
        if not iso_date:
            return False
        date_obj = datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
        formatted_date = date_obj.astimezone(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
        return formatted_date
    except Exception:
        return False
