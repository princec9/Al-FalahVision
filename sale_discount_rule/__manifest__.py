{
    'name': 'Sale Discount Rules (Product / Brand / Category)',
    'version': '19.0.1.0.1',
    'category': 'Sales',
    'summary': 'Min/Max discount rules on Product, Brand and Category with Sales Order validation',
    'license': 'LGPL-3',
    'depends': ['sale_management', 'product_barcode'],
    'data': [
        'security/ir.model.access.csv',
        'views/product_discount_rule_views.xml',
    ],
    'installable': True,
}
