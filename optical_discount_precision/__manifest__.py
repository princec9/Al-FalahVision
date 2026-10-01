{
    "name": "Optical Discount Precision",
    "version": "19.0.1.1.0",
    "summary": "Display Discount (%) with 2 decimals on Sales Orders, Invoices and Purchase Orders",
    "description": """
Extension of Optical Prescription Management.

* Discount (%) is displayed with 2 decimals on Sales Orders, Invoices,
  Purchase Orders, their PDF reports and the customer portal, while the "Discount"
  Decimal Accuracy (set to 6) keeps the exact value internally, so an
  entered Disc. Amount gives an exact total.

The standard `discount` field and the logic of optical_prescription are
not modified.
""",
    "category": "Sales",
    "license": "LGPL-3",
    "depends": ["optical_prescription", "purchase"],
    "data": [
        "views/sale_order_views.xml",
        "views/account_move_views.xml",
        "views/report_templates.xml",
        "views/purchase_order_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
