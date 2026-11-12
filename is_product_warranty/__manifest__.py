# -*- coding: utf-8 -*-
{
    'name': 'Product Warranty Management',
    'version': '19.0.1.0.1',
    'category': 'Sales',
    'summary': 'Add and manage warranty details for products',
    'description': """
        This module allows you to add warranty information 
        directly to your products.
    """,
    'author': 'InTechual Solutions',
    'license': 'OPL-1',

    # Dependencies
    'depends': ['base','mail','product','account','sale_management','stock','l10n_in','purchase'],

    'data': [
        # Security
        'security/security.xml',
        'security/ir.model.access.csv',

        # Data
        'data/sequence.xml',
        'data/mail_template_data.xml',
        'data/warranty_cron.xml',

        # Views
        'views/warranty_claim_views.xml',
        'views/warranty_master_views.xml',
        'views/warranty_certificate_views.xml',
        'views/warranty_menu_views.xml',
        'views/warranty_renewal_wizard_views.xml',
        'views/product_warranty_view.xml',
        'views/res_users_views.xml',
        'views/res_config_settings_views.xml',
        'views/product_template_views.xml',
        'views/product_category_views.xml',
        'views/warranty_receipt_report.xml',
        'views/account_move_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
    ],

    'installable': True,
    'application': True,

    # Icon
    'images':[
        'static/description/main_screenshot.png'
    ],
    'currency': 'EUR',
    'price': 20,
}
