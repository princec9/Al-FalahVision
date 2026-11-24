{
    'name': 'BT Stock View - All Companies & Branches',
    'version': '19.0.1.0.4',
    'category': 'Inventory/Reporting',
    'summary': 'Read-only stock overview of all products across all companies and branches, '
               'even for users who are not allowed in those companies.',
    'description': """
BT Stock View
=============
Shows the on-hand stock of every product in every company / branch / warehouse
to users of the "Stock Viewer (All Companies)" group, even if the user is NOT
an allowed company for those branches.

* Display only: users get no access to the underlying stock.quant, products,
  warehouses or companies records.
* Read-only list and pivot report, no create / edit / delete / open record.
    """,
    'author': 'Broadtech',
    'website': 'https://www.broadtech.com',
    'license': 'LGPL-3',
    'depends': ['stock','vendor_in_out'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/bt_stock_view_views.xml',
    ],
    'installable': True,
    'application': False,
}
