{
    'name': 'Sale Discount Rules (Product / Brand)',
    'version': '19.0.1.0.0',
    'category': 'Sales',
    'summary': 'Min/Max discount rules on Product and Brand with Sales Order warning',
    'license': 'LGPL-3',
    'depends': ['sale_management', 'product_barcode'],
    'data': [
        'security/ir.model.access.csv',
        'views/product_discount_rule_views.xml',
    ],
    'installable': True,
}
