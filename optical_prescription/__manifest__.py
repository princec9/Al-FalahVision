{
    "name": "Optical Prescription Management",

    "summary": "Manage eye prescriptions from Sales Orders and Invoices",

    "description": """
Optical Prescription Management
===============================

Advanced optical prescription workflow integrated with Odoo Sales and Accounting.

Main Features
-------------
- Create prescriptions from Sales Orders
- Prescription linked with Invoices
- Prescribed By (Optometrist) selection
- Contact smart button integration
- Automatic prescription transfer to invoices
- Prescription printed in invoice reports

Designed for
------------
- Optical stores
- Eye clinics
- Healthcare eyewear businesses

NOTE:
-----
This module depends on Optical Prescription Base module.
    """,

    "version": "19.0.1.0.8",

    "author": "",
    "website": "",

    "license": "LGPL-3",

    "category": "Sales",

    "application": True,

    "depends": [
        "base",
        "sale_management",
        "account",
        "pos_sale", "purchase_stock",
        "hr",
        "mrp", "point_of_sale",
        'sale',
        'quality_control', 'sale_commission','reimbursement'
    ],

    "data": [
        "security/ir.model.access.csv",
        "wizard/sale_discount_approval_wizard_views.xml",
        "views/sale.xml",
        "views/master_data_views.xml",
        "views/purchase_report.xml",

        "views/res_partner.xml",
        "views/hr_employee.xml",
        "views/mrp_order_views.xml",
        "views/prescription_views.xml",
        "views/fitting_parameters_views.xml",
        "views/product_template_views.xml",
        "views/menu.xml",
        'report/invoice_a5_report.xml',
        'report/invoice_a5_template.xml',
        
    ],
    "assets": {

        "web.assets_backend": [
            "optical_prescription/static/src/js/add_component_button/add_component_button.js",
            "optical_prescription/static/src/js/add_component_button/add_component_button.xml",
            "optical_prescription/static/src/js/fitting_parameters_button/fitting_parameters_button.js",
            "optical_prescription/static/src/js/fitting_parameters_button/fitting_parameters_button.xml",
            "optical_prescription/static/src/js/product_name_suggest/product_name_suggest.js",
            "optical_prescription/static/src/js/product_name_suggest/product_name_suggest.xml",
        ],
        "point_of_sale._assets_pos": [
            "optical_prescription/static/src/js/pos_order_line_discount_amount.js",
            "optical_prescription/static/src/js/pos_orderline_display.js",
            "optical_prescription/static/src/xml/pos_discount_amount.xml",

            "optical_prescription/static/src/js/pos_scan_wizard/pos_store_pay.js",
            "optical_prescription/static/src/js/pos_scan_wizard/pos_scan_wizard.js",
            "optical_prescription/static/src/js/pos_scan_wizard/pos_scan_wizard.xml",
            "optical_prescription/static/src/js/pos_scan_wizard/control_buttons_scan.js",
            "optical_prescription/static/src/js/pos_scan_wizard/control_buttons_scan.xml",

        ],
    },

    "demo": [
        "demo/demo.xml",
    ],

    "installable": True,
    "auto_install": False,
}
