# Part of Odoo. See LICENSE file for full copyright and licensing details.

SHOPIFY_API_VERSION = '2026-01'

# Namespace Shopify's own "Google & YouTube" sales channel app uses for Google
# Shopping attributes. Writing metafields under this namespace/key convention is
# what makes them show up as Google Shopping fields for that channel, instead of
# needing a bespoke metafield definition per shop.
GOOGLE_SHOPPING_METAFIELD_NAMESPACE = 'mm-google-shopping'
# (key, Shopify metafield type) — mirrors Shopify's documented Google Shopping
# metafield definitions used by ecommerce_shopify_upsync's
# `product.template.action_add_google_shopping_metafields`.
GOOGLE_SHOPPING_METAFIELDS = [
    ('google_product_category', 'single_line_text_field'),
    ('gender', 'single_line_text_field'),
    ('age_group', 'single_line_text_field'),
    ('mpn', 'single_line_text_field'),
    ('condition', 'single_line_text_field'),
    ('custom_product', 'boolean'),
    ('custom_label_0', 'single_line_text_field'),
    ('custom_label_1', 'single_line_text_field'),
    ('custom_label_2', 'single_line_text_field'),
    ('custom_label_3', 'single_line_text_field'),
    ('custom_label_4', 'single_line_text_field'),
]

# Shopify Product Metafield webhook topics (used alongside ORDERS_CREATE for the
# bidirectional metafield sync — a product create/update webhook makes Odoo
# re-pull the product's metafields from Shopify, and a delete webhook archives
# the stale Odoo rows).
SHOPIFY_WEBHOOK_TOPIC_PRODUCTS_CREATE = 'PRODUCTS_CREATE'
SHOPIFY_WEBHOOK_TOPIC_PRODUCTS_UPDATE = 'PRODUCTS_UPDATE'
SHOPIFY_WEBHOOK_TOPIC_PRODUCTS_DELETE = 'PRODUCTS_DELETE'

# Valid Shopify metafield types (MetafieldDefinition types available via the
# Admin GraphQL API). (value, human-readable label) — the SAME set drives the
# `shopify.product.metafield.type` selection and the `metafieldsSet` push, so the
# stored value is always Shopify's own type key and the label is what the user
# sees in Odoo ("single_line_text_field" shows as "Single Line Text").
SHOPIFY_METAFIELD_TYPES = [
    ('single_line_text_field', 'Single Line Text'),
    ('multi_line_text_field', 'Multi Line Text'),
    ('number_integer', 'Integer'),
    ('number_decimal', 'Decimal'),
    ('boolean', 'Boolean'),
    ('date', 'Date'),
    ('date_time', 'Date and Time'),
    ('json', 'JSON'),
    ('url', 'URL'),
    ('color', 'Color'),
    ('rating', 'Rating'),
    ('money', 'Money'),
    ('dimension', 'Dimension'),
    ('volume', 'Volume'),
    ('weight', 'Weight'),
    ('rich_text_field', 'Rich Text'),
    ('variant_option', 'Variant Option'),
    ('product_reference', 'Product Reference'),
    ('variant_reference', 'Product Variant Reference'),
    ('file_reference', 'File Reference'),
    ('page_reference', 'Page Reference'),
    ('metaobject_reference', 'Metaobject Reference'),
    ('list.single_line_text_field', 'List of Single Line Text'),
    ('list.multi_line_text_field', 'List of Multi Line Text'),
    ('list.number_integer', 'List of Integers'),
    ('list.number_decimal', 'List of Decimals'),
    ('list.boolean', 'List of Booleans'),
    ('list.date', 'List of Dates'),
    ('list.date_time', 'List of Date and Time'),
    ('list.json', 'List of JSON'),
    ('list.url', 'List of URLs'),
    ('list.color', 'List of Colors'),
    ('list.rating', 'List of Ratings'),
    ('list.money', 'List of Money'),
    ('list.dimension', 'List of Dimensions'),
    ('list.volume', 'List of Volumes'),
    ('list.weight', 'List of Weights'),
    ('list.variant_option', 'List of Variant Options'),
    ('list.product_reference', 'List of Product References'),
    ('list.variant_reference', 'List of Variant References'),
    ('list.file_reference', 'List of File References'),
    ('list.page_reference', 'List of Page References'),
    ('list.metaobject_reference', 'List of Metaobject References'),
]

# Legacy Shopify metafield `value_type` strings (pre-dating Shopify's metafield
# definitions/type system). Some metafields — especially ones created a long time
# ago, or through an app that still targets the legacy REST metafields API — are
# still reported by the Admin GraphQL API with one of these old type names instead
# of a modern SHOPIFY_METAFIELD_TYPES key. Map them to the closest modern key: the
# `shopify.product.metafield.type` selection only accepts SHOPIFY_METAFIELD_TYPES
# values, and Shopify's own `metafieldsSet` mutation (used to push) only accepts
# modern type keys too, so a legacy value must be normalized on pull either way.
LEGACY_SHOPIFY_METAFIELD_TYPE_ALIASES = {
    'string': 'single_line_text_field',
    'integer': 'number_integer',
    'json_string': 'json',
}

# Namespaces to never auto-create a Metafield Definition for when pushing a
# metafield value (see ecommerce_shopify_upsync.utils_upsync.
# create_metafield_definition_on_shopify): `global` is Shopify's own reserved
# system namespace (e.g. global.description_tag) and definitions can't be
# created there via the API; GOOGLE_SHOPPING_METAFIELD_NAMESPACE is already
# managed by Shopify's own Google & YouTube channel app, so creating our own
# definition there risks conflicting with — or duplicating — that app's.
METAFIELD_DEFINITION_SKIP_NAMESPACES = {'global', GOOGLE_SHOPPING_METAFIELD_NAMESPACE}

# graphql url used for retrive numaric id from global id of associated object.
GLOBAL_PRODUCT_ID = "gid://shopify/Product/"
GLOBAL_PRODUCT_VARIANT_ID = "gid://shopify/ProductVariant/"
GLOBAL_LOCATION_ID = "gid://shopify/Location/"
GLOBAL_CUSTOMER_ID = "gid://shopify/Customer/"
GLOBAL_FULFILLMENT_ID = "gid://shopify/Fulfillment/"
GLOBAL_FULFILLMENT_LINE_ITEM_ID = "gid://shopify/FulfillmentLineItem/"
GLOBAL_LINE_ITEM_ID = "gid://shopify/LineItem/"
GLOBAL_SHIPPING_LINE_ID = "gid://shopify/ShippingLine/"
GLOBAL_ORDER_ID = "gid://shopify/Order/"
GLOBAL_INVENTORY_ITEM_ID = "gid://shopify/InventoryItem/"
GLOBAL_MEDIA_IMAGE_ID = "gid://shopify/MediaImage/"
GLOBAL_METAFIELD_ID = "gid://shopify/Metafield/"
GLOBAL_METAFIELD_DEFINITION_ID = "gid://shopify/MetafieldDefinition/"

# Mapping of Shopify Carrier Names
SHOPIFY_CARRIER_NAMES_MAPPING = {
    "4px": "4PX",
    "ags": "AGS",
    "amazon": "Amazon",
    "amazonlogisticsuk": "Amazon Logistics UK",
    "anpost": "An Post",
    "anjunlogistics": "Anjun Logistics",
    "apc": "APC",
    "asendiausa": "Asendia USA",
    "australiapost": "Australia Post",
    "bonshaw": "Bonshaw",
    "bpost": "BPost",
    "bpostinternational": "BPost International",
    "canadapost": "Canada Post",
    "canpar": "Canpar",
    "cdllastmile": "CDL Last Mile",
    "chinapost": "China Post",
    "chronopost": "Chronopost",
    "chukou1": "Chukou1",
    "colissimo": "Colissimo",
    "comingle": "Comingle",
    "coordinadora": "Coordinadora",
    "correios": "Correios",
    "correos": "Correos",
    "ctt": "CTT",
    "cttexpress": "CTT Express",
    "cypruspost": "Cyprus Post",
    "delnext": "Delnext",
    "deutschepost": "Deutsche Post",
    "dhlecommerce": "DHL eCommerce",
    "dhlecommerceasia": "DHL eCommerce Asia",
    "dhlexpress": "DHL Express",
    "dpd": "DPD",
    "dpdlocal": "DPD Local",
    "dpduk": "DPD UK",
    "dtdexpress": "DTD Express",
    "dx": "DX",
    "eagle": "Eagle",
    "estes": "Estes",
    "evri": "Evri",
    "fedex": "FedEx",
    "firstgloballogistics": "First Global Logistics",
    "firstline": "First Line",
    "fsc": "FSC",
    "fulfilla": "Fulfilla",
    "gls": "GLS",
    "guangdongweisuyiinformationtechnology_wse": "Guangdong Weisuyi Information Technology (WSE)",
    "heppnerinternationalespeditiongmbh&co.": "Heppner Internationale Spedition GmbH & Co.",
    "icelandpost": "Iceland Post",
    "idex": "IDEX",
    "israelpost": "Israel Post",
    "japanpost_en": "Japan Post (EN)",
    "japanpost_ja": "Japan Post (JA)",
    "lapostecolissimo": "La Poste Colissimo",
    "laposteburkinafaso": "La Poste Burkina Faso",
    "lasership": "Lasership",
    "latviapost": "Latvia Post",
    "lietuvospaštas": "Lietuvos Paštas",
    "logisters": "Logisters",
    "lonestarovernight": "Lone Star Overnight",
    "m3logistics": "M3 Logistics",
    "meteorspace": "Meteor Space",
    "mondialrelay": "Mondial Relay",
    "newzealandpost": "New Zealand Post",
    "ninjavan": "NinjaVan",
    "northrussiasupplychain_shenzhen_co.": "North Russia Supply Chain (Shenzhen) Co.",
    "ontrac": "OnTrac",
    "packeta": "Packeta",
    "pagologistics": "Pago Logistics",
    "pingandatengfeiexpress": "Ping An Da Tengfei Express",
    "pitneybowes": "Pitney Bowes",
    "portalpostnord": "Portal PostNord",
    "posteitaliane": "Poste Italiane",
    "postnl": "PostNL",
    "postnorddk": "PostNord DK",
    "postnordno": "PostNord NO",
    "postnordse": "PostNord SE",
    "purolator": "Purolator",
    "qxpress": "Qxpress",
    "qyunexpress": "Qyun Express",
    "royalmail": "Royal Mail",
    "royalshipments": "Royal Shipments",
    "sagawa_en": "Sagawa (EN)",
    "sagawa_ja": "Sagawa (JA)",
    "sendle": "Sendle",
    "sfexpress": "SF Express",
    "sfcfulfillment": "SFC Fulfillment",
    "shreenandancourier": "SHREE NANDAN COURIER",
    "singaporepost": "Singapore Post",
    "southwestaircargo": "Southwest Air Cargo",
    "startrack": "StarTrack",
    "stepforwardfreight": "Step Forward Freight",
    "swisspost": "Swiss Post",
    "tforcefinalmile": "TForce Final Mile",
    "tinghao": "Tinghao",
    "tnt": "TNT",
    "tollipec": "Toll IPEC",
    "uniteddeliveryservice": "United Delivery Service",
    "ups": "UPS",
    "usps": "USPS",
    "venipak": "Venipak",
    "wepost": "We Post",
    "whistl": "Whistl",
    "wizmo": "Wizmo",
    "wmyc": "WMYC",
    "xpedigo": "Xpedigo",
    "xpologistics": "XPO Logistics",
    "yamato_en": "Yamato (EN)",
    "yamato_ja": "Yamato (JA)",
    "yifanexpress": "YiFan Express",
    "yunexpress": "YunExpress",
}
