from odoo import api, fields, models, tools


class IntercompanyTransferReport(models.Model):
    _name = "intercompany.transfer.report"
    _description = "Inter-Company Transfer Analysis"
    _auto = False
    _order = "transfer_date desc, id desc"

    name = fields.Char(string="Transfer", readonly=True)
    transfer_id = fields.Many2one("intercompany.stock.transfer", string="Transfer Ref", readonly=True)
    transfer_date = fields.Date(string="Transfer Date", readonly=True)
    state = fields.Selection([
        ("draft", "Draft"),
        ("confirmed", "Confirmed"),
        ("in_transit", "In Transit"),
        ("received", "Received"),
        ("cancelled", "Cancelled"),
    ], string="Status", readonly=True)
    user_id = fields.Many2one("res.users", string="Responsible", readonly=True)
    source_company_id = fields.Many2one("res.company", string="Source Company", readonly=True)
    destination_company_id = fields.Many2one("res.company", string="Destination Company", readonly=True)
    source_warehouse_id = fields.Many2one("stock.warehouse", string="Source Warehouse", readonly=True)
    destination_warehouse_id = fields.Many2one("stock.warehouse", string="Destination Warehouse", readonly=True)
    product_id = fields.Many2one("product.product", string="Product", readonly=True)
    product_categ_id = fields.Many2one("product.category", string="Product Category", readonly=True)
    product_uom_id = fields.Many2one("uom.uom", string="UoM", readonly=True)
    quantity = fields.Float(string="Quantity", readonly=True, digits="Product Unit")
    line_count = fields.Integer(string="# Lines", readonly=True)

    @property
    def _table_query(self):
        return """
            SELECT
                l.id AS id,
                t.id AS transfer_id,
                t.name AS name,
                t.transfer_date AS transfer_date,
                t.state AS state,
                t.user_id AS user_id,
                t.source_company_id AS source_company_id,
                t.destination_company_id AS destination_company_id,
                t.source_warehouse_id AS source_warehouse_id,
                t.destination_warehouse_id AS destination_warehouse_id,
                l.product_id AS product_id,
                pt.categ_id AS product_categ_id,
                l.product_uom_id AS product_uom_id,
                l.quantity AS quantity,
                1 AS line_count
            FROM intercompany_stock_transfer_line l
            JOIN intercompany_stock_transfer t ON t.id = l.transfer_id
            JOIN product_product pp ON pp.id = l.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
        """