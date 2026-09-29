{
    'name': 'Inter-Company Stock Transfer (Transit)',
    'version': '19.0.1.0.1',
    'category': 'Inventory/Inventory',
    'summary': 'Inventory-only inter-company stock transfer through a shared transit location',
    'description': '''
Inter-Company Stock Transfer for Odoo 19.

This module transfers physical stock between companies using stock.move only.
It deliberately does not create sale orders, purchase orders, invoices, bills,
or accounting valuation entries for its own moves.
''',
    'author': 'Custom Development',
    'license': 'LGPL-3',
    'depends': ['stock'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',

        'data/sequence.xml',
        'views/intercompany_stock_transfer_views.xml',
        'views/res_config_settings_views.xml',
        'views/menu_views.xml',
    ],
    'demo': [],
    'installable': True,
    'application': True,
}
