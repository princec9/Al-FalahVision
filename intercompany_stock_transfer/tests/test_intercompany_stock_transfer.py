from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import ValidationError


@tagged("post_install", "-at_install")
class TestIntercompanyStockTransfer(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Company = cls.env["res.company"]
        cls.company_1 = cls.env.company
        cls.company_2 = cls.Company.create({"name": "InterCo Test Company 2"})

        cls.warehouse_1 = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.company_1.id)], limit=1
        )
        cls.warehouse_2 = cls.env["stock.warehouse"].create({
            "name": "InterCo Test Warehouse 2",
            "code": "ICT2",
            "company_id": cls.company_2.id,
        })

        cls.product = cls.env["product.product"].create({
            "name": "InterCo Test Product",
            "is_storable": True,
            "tracking": "none",
            "company_id": False,
        })

        cls.transit = cls.env["stock.location"].create({
            "name": "Inter-Company Transit Test",
            "usage": "transit",
            "company_id": False,
        })

        cls.env["ir.config_parameter"].sudo().set_param(
            "intercompany_stock_transfer.transit_location_id", cls.transit.id
        )

        supplier = cls.env.ref("stock.stock_location_suppliers")
        cls.env["stock.move"].with_company(cls.company_1).create({
            "name": "Opening stock",
            "product_id": cls.product.id,
            "product_uom_qty": 100,
            "product_uom": cls.product.uom_id.id,
            "location_id": supplier.id,
            "location_dest_id": cls.warehouse_1.lot_stock_id.id,
            "company_id": cls.company_1.id,
        })._action_confirm()

        opening = cls.env["stock.move"].with_company(cls.company_1).search(
            [
                ("name", "=", "Opening stock"),
                ("company_id", "=", cls.company_1.id),
                ("product_id", "=", cls.product.id),
            ],
            limit=1,
        )
        opening._action_assign()
        opening.picked = True
        opening._action_done()

    def test_transfer_quantity_without_accounting(self):
        Move = self.env["stock.move"]
        AccountMove = self.env["account.move"]

        source_before = self.env["stock.quant"]._get_available_quantity(
            self.product, self.warehouse_1.lot_stock_id
        )
        destination_before = self.env["stock.quant"].with_company(self.company_2)._get_available_quantity(
            self.product, self.warehouse_2.lot_stock_id
        )
        self.assertEqual(source_before, 100)
        self.assertEqual(destination_before, 0)

        account_before = AccountMove.search_count([])

        transfer = self.env["intercompany.stock.transfer"].create({
            "source_company_id": self.company_1.id,
            "destination_company_id": self.company_2.id,
            "source_warehouse_id": self.warehouse_1.id,
            "destination_warehouse_id": self.warehouse_2.id,
            "source_location_id": self.warehouse_1.lot_stock_id.id,
            "destination_location_id": self.warehouse_2.lot_stock_id.id,
            "transit_location_id": self.transit.id,
            "line_ids": [(0, 0, {
                "product_id": self.product.id,
                "product_uom_id": self.product.uom_id.id,
                "quantity": 10,
            })],
        })

        transfer.action_confirm()
        transfer.action_send_to_transit()

        source = self.env["stock.quant"]._get_available_quantity(
            self.product, self.warehouse_1.lot_stock_id
        )
        transit = self.env["stock.quant"]._get_available_quantity(
            self.product, self.transit
        )
        destination = self.env["stock.quant"].with_company(self.company_2)._get_available_quantity(
            self.product, self.warehouse_2.lot_stock_id
        )
        self.assertEqual(source, 90)
        self.assertEqual(transit, 10)
        self.assertEqual(destination, 0)

        source_moves = transfer.source_move_ids
        self.assertTrue(source_moves)
        self.assertTrue(all(m.is_intercompany_transfer for m in source_moves))
        self.assertFalse(any(m.is_in or m.is_out for m in source_moves))

        transfer.action_receive()

        source = self.env["stock.quant"]._get_available_quantity(
            self.product, self.warehouse_1.lot_stock_id
        )
        transit = self.env["stock.quant"]._get_available_quantity(
            self.product, self.transit
        )
        destination = self.env["stock.quant"].with_company(self.company_2)._get_available_quantity(
            self.product, self.warehouse_2.lot_stock_id
        )
        self.assertEqual(source, 90)
        self.assertEqual(transit, 0)
        self.assertEqual(destination, 10)
        self.assertEqual(transfer.state, "received")

        account_after = AccountMove.search_count([])
        self.assertEqual(account_after, account_before)

        if self.env.registry.get("stock.valuation.layer"):
            svl = self.env["stock.valuation.layer"].search([
                ("stock_move_id", "in", (transfer.source_move_ids | transfer.destination_move_ids).ids)
            ])
            self.assertFalse(svl)

    def test_same_company_is_rejected(self):
        transfer = self.env["intercompany.stock.transfer"].create({
            "source_company_id": self.company_1.id,
            "destination_company_id": self.company_1.id,
            "source_warehouse_id": self.warehouse_1.id,
            "destination_warehouse_id": self.warehouse_1.id,
            "source_location_id": self.warehouse_1.lot_stock_id.id,
            "destination_location_id": self.warehouse_1.lot_stock_id.id,
            "transit_location_id": self.transit.id,
            "line_ids": [(0, 0, {
                "product_id": self.product.id,
                "product_uom_id": self.product.uom_id.id,
                "quantity": 1,
            })],
        })
        with self.assertRaises(ValidationError):
            transfer.action_confirm()
