# -*- coding: utf-8 -*-
{
    'name': 'C9 Prescription Import',
    'version': '19.0.1.0.0',
    'category': 'Sales/Sales',
    'summary': 'Import optical prescriptions from CSV into Odoo',
    'description': """
C9 Prescription Import
======================
Import optical prescriptions from CSV or XLSX files containing 49 fields.
Maps CustomerId to res.partner.c9_customer_id and stores prescriptions with
the full field set. Unmatched rows are imported and flagged rather than
discarded. Supports batch import of large files (200k+ rows).
    """,
    'author': 'C9',
    'website': '',
    'license': 'OPL-1',
    'application': True,
    'installable': True,
    'auto_install': False,
    'depends': [
        'base',
        'sale_management',
        'contacts',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'views/prescription_views.xml',
        'views/prescription_import_views.xml',
        'views/customer_mapping_views.xml',
        'views/res_partner_views.xml',
        'views/actions.xml',
        'views/menu.xml',
    ],
    'assets': {},
}
