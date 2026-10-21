{
    'name': 'Product Barcode Generator',
    'version': '19.0.1.0.1',
    'category': 'Inventory',
    'summary': 'Automatically generate product barcodes based on configurable prefix, padding, and suffix',
    'description': '''
        This module provides automatic barcode generation for products.
        
        Features:
        - Automatic barcode generation on product creation
        - Configurable prefix, padding, and suffix
        - Company-specific configuration
        - Barcode uniqueness validation
        - Respects manually entered barcodes
        
        Configuration available under:
        Inventory → Configuration → Settings → Product Barcode Generator
    ''',
    'author': 'Commerce9',
    'website': 'https://commerce9.io',
    'license': 'AGPL-3',
    'depends': [
        'base',
        'product',
        'stock',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence.xml',
        'views/res_config_settings_views.xml',
        'views/brand.xml',
        'views/product_model.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
