{
    'name': 'Vendor In/Out Report',
    'version': '19.0.1.0.0',
    'category': 'Inventory/Reporting',
    'summary': 'Vendor wise Purchased, Adjustments, Sold and Available per product',
    'description': """
          Vendor wise In/Out report
          =========================
          * Set a "Tracking Vendor" on each product (product form or bulk in the Products list).
          * The report shows, per vendor and product: Purchased, Adjustments, Sold and Available.
            - Purchased   = receipts from the vendor minus returns to the vendor
            - Adjustments = inventory adjustments (+/-) and manufacturing
            - Sold        = deliveries to customers minus customer returns
            - Available   = Purchased + Adjustments - Sold
          * Inventory adjustments have no vendor of their own, so they take the product's
            Tracking Vendor.
          * Menu: Inventory > Reporting > Vendor Wise Report (live data, no refresh needed).
          """,
    'depends': ['stock'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/product_views.xml',
        'views/vendor_report_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
