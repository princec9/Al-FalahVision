from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import SQL


class BtStockView(models.Model):
    """Read-only SQL view of stock for all companies / branches.

    The view is built directly in SQL, so the multi-company record rules of
    stock.quant, product, warehouse and company are not applied. All the
    displayed values are denormalised (plain chars / floats), so the user never
    needs read access to the related records. The model itself has read-only
    access, restricted to the Stock Viewer group.
    """
    _name = 'bt.stock.view'
    _description = 'Stock Overview (All Companies)'
    _auto = False
    _rec_name = 'product_name'
    _order = 'company_name, warehouse_name, product_name'

    product_name = fields.Char(string='Product', readonly=True)
    default_code = fields.Char(string='Internal Reference', readonly=True)
    barcode = fields.Char(string='Barcode', readonly=True)
    category_name = fields.Char(string='Product Category', readonly=True)
    brand_name = fields.Char(string='Brand', readonly=True)
    tracking_vendor_name = fields.Char(string='Tracking Vendor', readonly=True)
    company_name = fields.Char(string='Company / Branch', readonly=True)
    parent_company_name = fields.Char(string='Parent Company', readonly=True)
    warehouse_name = fields.Char(string='Warehouse', readonly=True)
    location_name = fields.Char(string='Location', readonly=True)
    lot_name = fields.Char(string='Lot/Serial Number', readonly=True)
    uom_name = fields.Char(string='Unit', readonly=True)
    quantity = fields.Float(string='On Hand', readonly=True, digits='Product Unit')
    reserved_quantity = fields.Float(string='Reserved', readonly=True, digits='Product Unit')
    available_quantity = fields.Float(string='Available', readonly=True, digits='Product Unit')
    incoming_qty = fields.Float(
        string='Incoming', readonly=True, digits='Product Unit',
        help="Planned incoming quantity on open stock moves.")
    outgoing_qty = fields.Float(
        string='Outgoing', readonly=True, digits='Product Unit',
        help="Planned outgoing quantity on open stock moves.")
    sales_qty = fields.Float(
        string='Sales Quantity', readonly=True, digits='Product Unit',
        help="Planned quantity on open moves to customer locations.")
    purchase_qty = fields.Float(
        string='Purchase Quantity', readonly=True, digits='Product Unit',
        help="Planned quantity on open moves from supplier locations.")
    sales_return_qty = fields.Float(
        string='Sales Return', readonly=True, digits='Product Unit',
        help="Done quantity returned by customers (Customer location -> internal location).")
    purchase_return_qty = fields.Float(
        string='Purchase Return', readonly=True, digits='Product Unit',
        help="Done quantity returned to vendors (internal location -> Vendor location).")
    return_date = fields.Date(
        string='Return Date', readonly=True,
        help="Date of the return. Empty on the on-hand stock rows.")

    @property
    def _table_query(self):
        lang = self.env.lang or self.env.user.lang or 'en_US'
        return SQL(
            """
            WITH rows AS (
                -- On-hand stock, one row per quant
                SELECT
                    sq.id::bigint AS id,
                    sq.product_id AS product_id,
                    sq.company_id AS company_id,
                    sq.location_id AS location_id,
                    sq.lot_id AS lot_id,
                    sq.quantity::float8 AS quantity,
                    sq.reserved_quantity::float8 AS reserved_quantity,
                    0::float8 AS sales_return_qty,
                    0::float8 AS purchase_return_qty,
                    0::float8 AS incoming_qty,
                    0::float8 AS outgoing_qty,
                    0::float8 AS sales_qty,
                    0::float8 AS purchase_qty,
                    NULL::date AS return_date
                FROM stock_quant sq
                JOIN stock_location l ON l.id = sq.location_id
                WHERE l.usage = 'internal'

                UNION ALL

                -- Completed returns, grouped by day.
                SELECT
                    1000000000 + row_number() OVER (
                        ORDER BY r.product_id, r.company_id, r.location_id, r.return_date
                    ) AS id,
                    r.product_id, r.company_id, r.location_id,
                    NULL::int AS lot_id,
                    0::float8, 0::float8,
                    r.sales_return_qty, r.purchase_return_qty,
                    0::float8, 0::float8,
                    0::float8, 0::float8, r.return_date
                FROM (
                    SELECT
                        sml.product_id AS product_id,
                        sml.company_id AS company_id,
                        CASE WHEN ls.usage = 'internal' THEN sml.location_id
                             ELSE sml.location_dest_id END AS location_id,
                        sml.date::date AS return_date,
                        SUM(CASE WHEN ls.usage = 'customer'
                                 THEN sml.quantity_product_uom ELSE 0 END)::float8 AS sales_return_qty,
                        SUM(CASE WHEN ld.usage = 'supplier'
                                 THEN sml.quantity_product_uom ELSE 0 END)::float8 AS purchase_return_qty
                    FROM stock_move_line sml
                    JOIN stock_location ls ON ls.id = sml.location_id
                    JOIN stock_location ld ON ld.id = sml.location_dest_id
                    WHERE sml.state = 'done'
                      AND ((ls.usage != 'internal' AND ld.usage = 'internal')
                        OR (ls.usage = 'internal' AND ld.usage != 'internal'))
                    GROUP BY 1, 2, 3, 4
                ) r

                UNION ALL

                -- Planned moves, matching Odoo's stock product Incoming and
                -- Outgoing quantities. Sales and Purchase are subsets of them.
                SELECT
                    2000000000 + row_number() OVER (
                        ORDER BY m.product_id, m.company_id, m.location_id
                    ) AS id,
                    m.product_id, m.company_id, m.location_id,
                    NULL::int AS lot_id,
                    0::float8, 0::float8,
                    0::float8, 0::float8,
                    m.incoming_qty, m.outgoing_qty,
                    m.sales_qty, m.purchase_qty, NULL::date AS return_date
                FROM (
                    SELECT
                        sm.product_id,
                        sm.company_id,
                        CASE WHEN ls.usage = 'internal' THEN sm.location_id
                             ELSE sm.location_dest_id END AS location_id,
                        SUM(CASE WHEN ls.usage != 'internal' AND ld.usage = 'internal'
                                 THEN sm.product_qty ELSE 0 END)::float8 AS incoming_qty,
                        SUM(CASE WHEN ls.usage = 'internal' AND ld.usage != 'internal'
                                 THEN sm.product_qty ELSE 0 END)::float8 AS outgoing_qty,
                        SUM(CASE WHEN ls.usage = 'internal' AND ld.usage = 'customer'
                                 THEN sm.product_qty ELSE 0 END)::float8 AS sales_qty,
                        SUM(CASE WHEN ls.usage = 'supplier' AND ld.usage = 'internal'
                                 THEN sm.product_qty ELSE 0 END)::float8 AS purchase_qty
                    FROM stock_move sm
                    JOIN stock_location ls ON ls.id = sm.location_id
                    JOIN stock_location ld ON ld.id = sm.location_dest_id
                    WHERE sm.state IN ('waiting', 'confirmed', 'assigned', 'partially_available')
                      AND ((ls.usage != 'internal' AND ld.usage = 'internal')
                        OR (ls.usage = 'internal' AND ld.usage != 'internal'))
                    GROUP BY 1, 2, 3
                ) m
            )
            SELECT
                rw.id AS id,
                COALESCE(pt.name->>%(lang)s, pt.name->>'en_US') AS product_name,
                pp.default_code AS default_code,
                pp.barcode AS barcode,
                pc.complete_name AS category_name,
                # br.name AS brand_name,
                # tv.name AS tracking_vendor_name,
                rc.name AS company_name,
                prc.name AS parent_company_name,
                sw.name AS warehouse_name,
                sl.complete_name AS location_name,
                lot.name AS lot_name,
                COALESCE(uu.name->>%(lang)s, uu.name->>'en_US') AS uom_name,
                rw.quantity AS quantity,
                rw.reserved_quantity AS reserved_quantity,
                rw.quantity - rw.reserved_quantity AS available_quantity,
                rw.incoming_qty AS incoming_qty,
                rw.outgoing_qty AS outgoing_qty,
                rw.sales_qty AS sales_qty,
                rw.purchase_qty AS purchase_qty,
                rw.sales_return_qty AS sales_return_qty,
                rw.purchase_return_qty AS purchase_return_qty,
                rw.return_date AS return_date
            FROM rows rw
            JOIN stock_location sl ON sl.id = rw.location_id
            JOIN product_product pp ON pp.id = rw.product_id
            JOIN product_template pt ON pt.id = pp.product_tmpl_id
            LEFT JOIN product_category pc ON pc.id = pt.categ_id
            -- Brand and tracking vendor are taken from the product.
            -- If these fields live on product.product, use pp.brand_id /
            -- pp.tracking_vendor_id instead of pt.*.
            -- Adjust the brand table name to your brand model.
            LEFT JOIN product_brand br ON br.id = pt.brand_id
            LEFT JOIN res_partner tv ON tv.id = pt.tracking_vendor_id
            LEFT JOIN uom_uom uu ON uu.id = pt.uom_id
            LEFT JOIN res_company rc ON rc.id = rw.company_id
            LEFT JOIN res_company prc ON prc.id = rc.parent_id
            LEFT JOIN stock_warehouse sw ON sw.id = sl.warehouse_id
            LEFT JOIN stock_lot lot ON lot.id = rw.lot_id
            """,
            lang=lang,
        )

    # Extra safety net on top of the ACL (read only for everybody).
    @api.model_create_multi
    def create(self, vals_list):
        raise UserError(self.env._("This report is read-only."))

    def write(self, vals):
        raise UserError(self.env._("This report is read-only."))

    def unlink(self):
        raise UserError(self.env._("This report is read-only."))